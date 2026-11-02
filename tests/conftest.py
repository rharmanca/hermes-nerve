"""Shared test isolation.

Every test runs with gate telemetry and preference storage redirected into a
per-test temp directory. Without this, tests that drive the gate directly
(e.g. test_core.GateTests, which predates the env-var overrides) appended
gate events to the LIVE ledger at ~/.hermes/nerve/gate-events.jsonl.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


# Host-runtime isolation (rob fork). When the suite runs inside a live Hermes
# worker, two things leak in and flip ~46 assertions without any code defect:
#   1. TMPDIR points inside the user's home directory or a Hermes home (e.g.
#      ~/scratch or ~/.hermes/profiles/<p>/cache/scratch), so tmp fixtures are
#      classified as home/profile paths instead of plain temp paths.
#   2. Kanban/delegation identity variables (HERMES_KANBAN_*, HERMES_DELEGATED_
#      CHILD_CONTEXT) make Nerve apply worker completion-authority rules.
# Tests build their own homes and identities, so we neutralise both up front.
_HOST_RUNTIME_ENV_PREFIXES = ("HERMES_KANBAN_",)
_HOST_RUNTIME_ENV_NAMES = ("HERMES_DELEGATED_CHILD_CONTEXT",)


def _inside_hermes_tree(path: str) -> bool:
    """True when *path* lives under the user's home or any .hermes tree."""
    home = Path.home()
    for candidate in {Path(path).absolute(), Path(path).resolve()}:
        if ".hermes" in candidate.parts:
            return True
        if candidate == home or home in candidate.parents:
            return True
        if candidate == home.resolve() or home.resolve() in candidate.parents:
            return True
    return False


def pytest_configure(config):
    import os
    import tempfile

    current = tempfile.gettempdir()
    if _inside_hermes_tree(current):
        # Keep the default short: Chrome unix-socket paths are length-limited.
        base = os.environ.get("NERVE_TEST_TMPDIR") or "/tmp"
        if _inside_hermes_tree(base):
            raise pytest.UsageError("NERVE_TEST_TMPDIR must be outside the home directory and any .hermes tree")
        neutral = tempfile.mkdtemp(prefix="nerve-tests-", dir=base)
        os.environ["TMPDIR"] = neutral
        tempfile.tempdir = neutral
        config._nerve_neutral_tmp = neutral


def pytest_unconfigure(config):
    import shutil

    neutral = getattr(config, "_nerve_neutral_tmp", None)
    if neutral:
        shutil.rmtree(neutral, ignore_errors=True)


@pytest.fixture(autouse=True)
def _isolate_host_runtime_env(monkeypatch):
    import os

    for name in list(os.environ):
        if name.startswith(_HOST_RUNTIME_ENV_PREFIXES) or name in _HOST_RUNTIME_ENV_NAMES:
            monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture(autouse=True)
def _isolate_gate_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_NERVE_GATE_EVENTS", str(tmp_path / "gate-events.jsonl"))
    monkeypatch.setenv("HERMES_NERVE_PREFERENCES", str(tmp_path / "preferences.json"))
    # Never let a test fall back to the caller's real Hermes home.
    home = tmp_path / "hermes-home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_NERVE_NERVOUS_EVENTS", str(tmp_path / "nervous-events.jsonl"))
    monkeypatch.setenv("HERMES_NERVE_OUTCOMES", str(tmp_path / "outcomes.jsonl"))
    monkeypatch.setenv("HERMES_NERVE_WARNING_OUTCOMES", str(tmp_path / "warning-outcomes.jsonl"))
    yield


@pytest.fixture(autouse=True)
def _nerve_p0_isolation():
    """P0 (0.4.1): isolate the advisory circuit breaker across tests.

    A simulated provider stall opens the breaker; without this fixture the
    open state leaks into unrelated tests within the same pytest process.
    """
    try:
        from hermes_nerve import advisory
        advisory.reset_provider_state()
    except Exception:
        pass
    yield
    try:
        advisory.reset_provider_state()
        advisory.reset_for_tests()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _isolate_client_breaker():
    """Judge-outage fast-fail breaker is process-global; reset it per test."""
    from hermes_nerve import client

    client.reset_breaker()
    yield
    client.reset_breaker()
