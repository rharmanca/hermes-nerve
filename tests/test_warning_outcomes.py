"""Content-free right/wrong tracking for warnings/nudges (v0.4.15-rob.3)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_nerve import warning_outcomes as wo
from tests.test_nudge_only import _system

SECRET_MARKERS = (
    "repair it", "broken.py", "Traceback", "connection failed", "src/parser.py",
    "regression tests", "SECRET-PROMPT", "rm -rf", "password", "print('x')", "s0.py",
)


def _rows(path):
    if not Path(path).exists():
        return []
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def _assert_content_free(tc, path):
    raw = Path(path).read_text() if Path(path).exists() else ""
    for marker in SECRET_MARKERS:
        tc.assertNotIn(marker, raw)
    for row in _rows(path):
        tc.assertLessEqual(set(row), set(wo.ALLOWED_FIELDS), row)
        tc.assertIn(row["outcome"], wo.OUTCOME_LABELS)
        for key, value in row.items():
            if isinstance(value, str):
                tc.assertRegex(value, r"^[A-Za-z0-9_.:\-]{1,96}$", (key, value))


def _env(td):
    return patch.dict(os.environ, {
        "HERMES_NERVE_NERVOUS_EVENTS": str(Path(td) / "nervous.jsonl"),
        "HERMES_NERVE_OUTCOMES": str(Path(td) / "outcomes.jsonl"),
        "HERMES_NERVE_WARNING_OUTCOMES": str(Path(td) / "warning-outcomes.jsonl"),
    }, clear=False)


class SanitizeTests(unittest.TestCase):
    def test_only_allowlisted_fields_survive(self):
        row = wo.sanitize({
            "outcome": "followed", "turn_id": "t1", "decision_id": "d-1",
            "message": "SECRET-PROMPT text", "args": {"command": "rm -rf /"},
            "reason": "password leaked", "content": "x", "prompt": "y",
            "confidence": 0.7, "rule": "REPLAN",
        })
        self.assertEqual(set(row), {"schema", "ts", "turn_id", "decision_id", "confidence", "rule", "outcome"})

    def test_free_text_in_id_fields_is_dropped_not_truncated(self):
        row = wo.sanitize({"outcome": "nudged", "rule": "the requested verification/test has not passed",
                           "turn_id": "a b c", "decision_type": "x" * 200})
        for key in ("rule", "turn_id", "decision_type"):
            self.assertNotIn(key, row)

    def test_unknown_outcome_and_bad_confidence_rejected(self):
        self.assertIsNone(wo.sanitize({"outcome": "SECRET-PROMPT"}))
        self.assertNotIn("confidence", wo.sanitize({"outcome": "issued", "confidence": "nan"}))
        self.assertNotIn("confidence", wo.sanitize({"outcome": "issued", "confidence": 3}))

    def test_observe_maps_labels(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "w.jsonl"
            tr = wo.WarningOutcomeTracker(path)
            tr.observe({"record_type": "decision", "decision_id": "d1", "decision_type": "FAILURE",
                        "jev_confidence": 0.8, "reason": "SECRET-PROMPT"})
            tr.observe({"record_type": "control_lifecycle", "decision_id": "d1", "stage": "created",
                        "challenge_disposition": "active", "control": "REPLAN"})
            tr.observe({"record_type": "control_lifecycle", "decision_id": "d1", "stage": "next_action",
                        "challenge_disposition": "followed"})
            tr.observe({"record_type": "control_lifecycle", "decision_id": "d1", "stage": "next_action",
                        "challenge_disposition": "overridden"})
            tr.observe({"record_type": "control_lifecycle", "decision_id": "d1", "stage": "expired",
                        "challenge_disposition": "turn-ended"})
            tr.observe({"record_type": "outcome", "decision_id": "d1", "useful_disagreement": True,
                        "outcome_reason": "SECRET-PROMPT"})
            tr.observe({"record_type": "outcome", "decision_id": "d1", "useful_disagreement": False})
            rows = _rows(path)
            self.assertEqual([r["outcome"] for r in rows],
                             ["issued", "followed", "ignored", "expired", "proven_right", "proven_wrong"])
            self.assertEqual(rows[0]["decision_type"], "FAILURE")
            self.assertEqual(rows[0]["confidence"], 0.8)
            self.assertEqual(wo.counts(path)["by_outcome"]["followed"], 1)
            _assert_content_free(self, path)

    def test_write_failure_never_raises(self):
        tr = wo.WarningOutcomeTracker(Path("/proc/forbidden/w.jsonl"))
        self.assertIsNone(tr.record(outcome="issued"))


class IsolationTests(unittest.TestCase):
    def test_default_path_follows_hermes_home(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"HERMES_HOME": td}):
            os.environ.pop("HERMES_NERVE_WARNING_OUTCOMES", None)
            self.assertEqual(wo.warning_outcome_path(), Path(td) / "nerve" / "warning-outcomes.jsonl")

    def test_suite_never_points_at_real_home(self):
        real = Path.home() / ".hermes"
        self.assertNotIn(str(real), str(wo.warning_outcome_path()))
        self.assertNotIn(str(real), os.environ.get("HERMES_HOME", ""))


class ScenarioTrackingTests(unittest.TestCase):
    def test_nudge_only_scenarios_write_content_free_rows(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td, _env(td):
            path = Path(td) / "warning-outcomes.jsonl"
            system = _system("nudge_only", repeated_failure_local_replan_at=3)
            system.start_turn(user_message="repair it SECRET-PROMPT", session_id="s1", turn_id="t1")
            event = dict(tool_name="terminal", args={"command": "python broken.py"}, status="error",
                         result="Traceback: connection failed after 501 ms",
                         error_message="connection failed after 501 ms", session_id="s1", turn_id="t1")
            for i in range(3):
                system.observe_tool_call(tool_call_id=f"c{i}", **event)
                system.drain()
            self.assertIsNone(system.before_tool_call(tool_name="terminal", args={"command": "python broken.py"},
                                                      session_id="s1", turn_id="t1", tool_call_id="c9"))
            system.start_turn(user_message="Fix src/parser.py and add regression tests",
                              session_id="s2", turn_id="t2")
            self.assertIsNone(system.completion_gate(turn_id="t2", coding=True, final_response="done"))
            rows = _rows(path)
            labels = {r["outcome"] for r in rows}
            self.assertIn("nudged", labels)
            self.assertTrue(any(r.get("rule", "").startswith("completion_") for r in rows))
            _assert_content_free(self, path)

    def test_correct_next_mode_writes_no_completion_nudge(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td, _env(td):
            path = Path(td) / "warning-outcomes.jsonl"
            system = _system("correct_next")
            system.start_turn(user_message="Fix src/parser.py", session_id="s1", turn_id="t1")
            self.assertEqual(system.completion_gate(turn_id="t1", coding=True)["action"], "continue")
            self.assertFalse(any(r.get("decision_type") == "completion_integrity" for r in _rows(path)))


if __name__ == "__main__":
    unittest.main()
