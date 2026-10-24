from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCORECARD = ROOT / "scripts" / "scorecard.py"


def test_weekly_scorecard_stub_is_repeatable_and_content_free(tmp_path: Path) -> None:
    output = tmp_path / "scorecard.json"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    completed = subprocess.run(
        [sys.executable, str(SCORECARD), "--stub", "--json-out", str(output)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert completed.returncode == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema"] == "hermes-nerve-weekly-scorecard/v1"
    assert payload["mode"] == "stub"
    assert payload["scenario_count"] == 20
    assert payload["counts"] == {
        "correct_continue": 7,
        "correct_intervene": 11,
        "false_intervene": 0,
        "missed_risk": 0,
        "judge_errors": 2,
    }
    assert payload["judge_latency_ms"] == {"samples": 13, "median": 1.0, "max": 1.0}
    assert sum(payload["counts"].values()) == payload["scenario_count"]
    assert "pytest || true" not in completed.stdout
    assert "uvx" not in completed.stdout
    assert "poetry" not in completed.stdout
