"""Provider failures must be visible in privacy-safe form: breaker state in report(),
stable error categories in event logs and per-category metrics — never raw text."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import hermes_nerve.client as client
from hermes_nerve import nervous
from hermes_nerve.client import JevConnectionError, JevError

try:
    from tests.test_nervous import FakeDecisionResult, ScriptedEngine
except ModuleNotFoundError:  # `unittest discover -s tests` imports top-level module names
    from test_nervous import FakeDecisionResult, ScriptedEngine


def _records(td):
    out = []
    for line in (Path(td) / "n.jsonl").read_text().splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


class ProviderFailureVisibilityTests(unittest.TestCase):
    def setUp(self):
        client.reset_breaker()
        self.addCleanup(client.reset_breaker)

    def make_system(self, td, **cfg):
        system = nervous.NervousSystem(engine_factory=ScriptedEngine)
        system.configure(enabled=True, admission_enabled=False, mode="shadow", **cfg)
        env = patch.dict(os.environ, {"HERMES_NERVE_NERVOUS_EVENTS": str(Path(td) / "n.jsonl")}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        return system

    def _failing_assessment(self, td, exc):
        system = self.make_system(td)
        ScriptedEngine.reset(exc)
        system.start_turn(user_message="repair it", session_id="s1", turn_id="t1")
        event = dict(
            tool_name="terminal", args={"command": "python broken.py"}, status="error",
            result="Traceback: boom", error_message="boom", session_id="s1", turn_id="t1",
        )
        first = system.observe_tool_call(tool_call_id="c1", **event)
        self.assertTrue(first["forwarded"])
        self.assertTrue(system.drain())
        return system

    def test_report_exposes_breaker_state_without_secrets(self):
        with tempfile.TemporaryDirectory() as td:
            system = self.make_system(td)
            breaker = system.report()["provider_breaker"]
            self.assertEqual(set(breaker), {"open", "failures", "open_for_s", "threshold", "cooldown_seconds"})
            self.assertFalse(breaker["open"])
            self.assertEqual(breaker["threshold"], client.BREAKER_THRESHOLD)
            self.assertEqual(breaker["cooldown_seconds"], client.BREAKER_COOLDOWN)

    def test_assessment_error_records_category_and_metric(self):
        with tempfile.TemporaryDirectory() as td:
            system = self._failing_assessment(td, JevConnectionError("Jev API connection failed: The read operation timed out"))
            errors = [r for r in _records(td) if r["kind"] == "assessment_error"]
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0]["error_category"], "timeout")
            metrics = system.report()["metrics"]
            self.assertEqual(metrics["provider_errors"], 1)
            self.assertEqual(metrics["provider_errors_timeout"], 1)

    def test_circuit_open_is_its_own_category(self):
        with tempfile.TemporaryDirectory() as td:
            system = self._failing_assessment(td, JevConnectionError("Jev provider circuit open after 2 consecutive connection failures; retrying in 42s"))
            errors = [r for r in _records(td) if r["kind"] == "assessment_error"]
            self.assertEqual(errors[0]["error_category"], "circuit-open")
            self.assertEqual(system.report()["metrics"]["provider_errors_circuit-open"], 1)

    def test_http_error_category_does_not_include_response_body(self):
        with tempfile.TemporaryDirectory() as td:
            system = self._failing_assessment(td, JevError("Jev API returned HTTP 402: insufficient credits for user@example.com"))
            errors = [r for r in _records(td) if r["kind"] == "assessment_error"]
            self.assertEqual(errors[0]["error_category"], "http-402")
            self.assertNotIn("@", errors[0]["error_category"])
            self.assertEqual(system.report()["metrics"]["provider_errors_http-402"], 1)

    def test_admission_floor_fallback_records_category(self):
        with tempfile.TemporaryDirectory() as td:
            system = self.make_system(td)
            ScriptedEngine.reset(JevConnectionError("Jev API connection failed: <urlopen error timed out>"))
            system.start_turn(user_message="Run the production deploy", session_id="s1", turn_id="t1")
            system._turns["t1"].admission = "PENDING"
            with self.assertRaises(JevConnectionError):
                system._do_admission({"turn_id": "t1", "user_message": "Run the production deploy"})
            fallback = [r for r in _records(td) if r["kind"] == "admission_floor_fallback"]
            self.assertEqual(len(fallback), 1)
            self.assertEqual(fallback[0]["error_category"], "timeout")
            self.assertEqual(fallback[0]["admission"], "WATCH")


if __name__ == "__main__":
    unittest.main()
