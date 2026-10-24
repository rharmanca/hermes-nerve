"""Local learning needs labeled outcomes. In shadow mode nobody labels decisions by hand,
so the supervisor derives privacy-safe counterfactual labels from what the agent did next:
the same failure recurring means the "do not just continue" intervention was useful; the
same action later succeeding means it was not. Ambiguous turns produce no label."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_nerve import nervous, tools
from hermes_nerve.privacy import canonical_hash

try:
    from tests.test_nervous import FakeDecisionResult, ScriptedEngine
except ModuleNotFoundError:  # `unittest discover -s tests` imports top-level module names
    from test_nervous import FakeDecisionResult, ScriptedEngine


FAIL = dict(
    tool_name="terminal", args={"command": "python broken.py"}, status="error",
    result="Traceback: ImportError: no module named broken", error_message="ImportError: no module named broken",
    session_id="s1", turn_id="t1",
)
OK_SAME_ACTION = dict(
    tool_name="terminal", args={"command": "python broken.py"}, status="ok",
    result="done", error_message="", session_id="s1", turn_id="t1",
)
OK_OTHER_ACTION = dict(
    tool_name="terminal", args={"command": "ls"}, status="ok",
    result="broken.py", error_message="", session_id="s1", turn_id="t1",
)


def _rows(path):
    if not Path(path).exists():
        return []
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


class ShadowOutcomeLabelingTests(unittest.TestCase):
    def make_system(self, td, mode="shadow", **cfg):
        self.events = str(Path(td) / "n.jsonl")
        self.outcomes = str(Path(td) / "o.jsonl")
        env = patch.dict(os.environ, {"HERMES_NERVE_NERVOUS_EVENTS": self.events, "HERMES_NERVE_OUTCOMES": self.outcomes}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        system = nervous.NervousSystem(engine_factory=ScriptedEngine)
        system.configure(enabled=True, admission_enabled=False, mode=mode, repeated_failure_local_replan_at=10, **cfg)
        self.addCleanup(system.configure, enabled=False)
        system.start_turn(user_message="repair it", session_id="s1", turn_id="t1")
        return system

    def _intervene_once(self, system):
        ScriptedEngine.reset("REPLAN")
        first = system.observe_tool_call(tool_call_id="c1", **FAIL)
        self.assertTrue(first["forwarded"])
        self.assertTrue(system.drain())
        decisions = [r for r in _rows(self.outcomes) if r.get("record_type") == "decision"]
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["jev_decision"], "REPLAN")
        return decisions[0]

    def test_failure_recurrence_labels_intervention_useful(self):
        with tempfile.TemporaryDirectory() as td:
            system = self.make_system(td)
            decision = self._intervene_once(system)
            system.observe_tool_call(tool_call_id="c2", **FAIL)
            outcomes = [r for r in _rows(self.outcomes) if r.get("record_type") == "outcome"]
            self.assertEqual(len(outcomes), 1)
            self.assertIs(outcomes[0]["useful_disagreement"], True)
            self.assertEqual(outcomes[0]["event_id"], decision["event_id"])
            self.assertEqual(outcomes[0]["decision_id"], decision["decision_id"])
            self.assertEqual(outcomes[0]["outcome_source"], "shadow-auto")
            self.assertEqual(outcomes[0]["outcome_reason"], "failure-recurred")
            metrics = system.report()["metrics"]
            self.assertEqual(metrics["shadow_outcomes_labeled"], 1)
            self.assertEqual(metrics["shadow_outcomes_useful"], 1)

    def test_same_action_succeeding_labels_intervention_not_useful(self):
        with tempfile.TemporaryDirectory() as td:
            system = self.make_system(td)
            self._intervene_once(system)
            system.observe_tool_call(tool_call_id="c2", **OK_SAME_ACTION)
            outcomes = [r for r in _rows(self.outcomes) if r.get("record_type") == "outcome"]
            self.assertEqual(len(outcomes), 1)
            self.assertIs(outcomes[0]["useful_disagreement"], False)
            self.assertEqual(outcomes[0]["outcome_reason"], "same-action-succeeded")
            self.assertEqual(system.report()["metrics"]["shadow_outcomes_not_useful"], 1)

    def test_each_decision_is_labeled_at_most_once(self):
        with tempfile.TemporaryDirectory() as td:
            system = self.make_system(td)
            self._intervene_once(system)
            system.observe_tool_call(tool_call_id="c2", **FAIL)
            system.observe_tool_call(tool_call_id="c3", **FAIL)
            system.observe_tool_call(tool_call_id="c4", **OK_SAME_ACTION)
            outcomes = [r for r in _rows(self.outcomes) if r.get("record_type") == "outcome"]
            self.assertEqual(len(outcomes), 1)

    def test_ambiguous_turn_writes_explicit_unresolved_row(self):
        """Retain ambiguity for auditability; null labels stay out of learning samples."""
        with tempfile.TemporaryDirectory() as td:
            system = self.make_system(td)
            self._intervene_once(system)
            system.observe_tool_call(tool_call_id="c2", **OK_OTHER_ACTION)
            system.finish_turn(turn_id="t1", session_id="s1", assistant_response="gave up")
            outcomes = [r for r in _rows(self.outcomes) if r.get("record_type") == "outcome"]
            self.assertEqual(len(outcomes), 1)
            self.assertIsNone(outcomes[0]["useful_disagreement"])
            self.assertEqual(outcomes[0]["outcome_reason"], "unresolved-turn-end")
            self.assertEqual(system.report()["metrics"]["shadow_outcomes_unresolved"], 1)

    def test_continue_decisions_are_not_labeled(self):
        with tempfile.TemporaryDirectory() as td:
            system = self.make_system(td)
            ScriptedEngine.reset("CONTINUE")
            system.observe_tool_call(tool_call_id="c1", **FAIL)
            self.assertTrue(system.drain())
            system.observe_tool_call(tool_call_id="c2", **FAIL)
            system.finish_turn(turn_id="t1", session_id="s1", assistant_response="done")
            outcomes = [r for r in _rows(self.outcomes) if r.get("record_type") == "outcome"]
            self.assertEqual(outcomes, [])
            self.assertNotIn("shadow_outcomes_unresolved", system.report()["metrics"])

    def test_delivered_challenge_is_not_auto_labeled(self):
        """Once the agent can see the challenge its next step is no longer a clean counterfactual."""
        with tempfile.TemporaryDirectory() as td:
            system = self.make_system(td, mode="correct_next")
            self._intervene_once(system)
            system.observe_tool_call(tool_call_id="c2", **FAIL)
            outcomes = [r for r in _rows(self.outcomes) if r.get("record_type") == "outcome"]
            self.assertEqual(outcomes, [])

    def test_labels_never_contain_content(self):
        with tempfile.TemporaryDirectory() as td:
            system = self.make_system(td)
            self._intervene_once(system)
            system.observe_tool_call(tool_call_id="c2", **FAIL)
            outcome = [r for r in _rows(self.outcomes) if r.get("record_type") == "outcome"][0]
            text = json.dumps(outcome)
            for banned in ("broken.py", "ImportError", "Traceback", "repair it"):
                self.assertNotIn(banned, text)
            self.assertEqual(set(outcome) - {"turn_id"}, {
                "record_type", "type", "event_id", "decision_id", "decision_type", "objective_type",
                "jev_decision", "useful_disagreement", "outcome_source", "outcome_reason",
                "failure_fingerprint", "action_fingerprint", "timestamp",
            })

    def test_shadow_intervention_without_failure_fingerprint_is_recorded(self):
        with tempfile.TemporaryDirectory() as td:
            system = self.make_system(td)
            ScriptedEngine.reset("REPLAN")
            observed = system.emit_event({
                "type": "DECISION", "goal": "repair", "choices": ["CONTINUE", "REPLAN"],
                "hermes_decision": "CONTINUE", "materiality": 1.0,
                "session_id": "s1", "turn_id": "t1", "event_id": "c1",
            })
            self.assertTrue(observed["forwarded"])
            self.assertTrue(system.drain())
            decisions = [r for r in _rows(self.outcomes) if r.get("record_type") == "decision"]
            self.assertEqual(len(decisions), 1)
            self.assertEqual(decisions[0]["failure_fingerprint"], "")
            system.finish_turn(turn_id="t1", session_id="s1", assistant_response="done")
            outcomes = [r for r in _rows(self.outcomes) if r.get("record_type") == "outcome"]
            self.assertEqual(len(outcomes), 1)
            self.assertIsNone(outcomes[0]["useful_disagreement"])
            self.assertEqual(outcomes[0]["decision_id"], decisions[0]["decision_id"])

    def test_later_different_failure_labels_intervention_useful(self):
        with tempfile.TemporaryDirectory() as td:
            system = self.make_system(td)
            self._intervene_once(system)
            later_failure = dict(
                FAIL,
                args={"command": "python other.py"},
                result="ValueError: other failure",
                error_message="ValueError: other failure",
            )
            system.observe_tool_call(tool_call_id="c2", **later_failure)
            outcomes = [r for r in _rows(self.outcomes) if r.get("record_type") == "outcome"]
            self.assertEqual(len(outcomes), 1)
            self.assertIs(outcomes[0]["useful_disagreement"], True)
            self.assertEqual(outcomes[0]["outcome_reason"], "later-failure")

    def test_verification_action_labels_intervention_agent_verified(self):
        with tempfile.TemporaryDirectory() as td:
            system = self.make_system(td)
            self._intervene_once(system)
            system.observe_tool_call(
                tool_call_id="c2", tool_name="terminal", args={"command": "pytest -q"}, status="ok",
                result="1 passed", error_message="", session_id="s1", turn_id="t1",
            )
            outcomes = [r for r in _rows(self.outcomes) if r.get("record_type") == "outcome"]
            self.assertEqual(len(outcomes), 1)
            self.assertIs(outcomes[0]["useful_disagreement"], True)
            self.assertEqual(outcomes[0]["outcome_reason"], "agent-verified")

    def test_next_turn_followup_is_content_free_and_session_scoped(self):
        with tempfile.TemporaryDirectory() as td:
            system = self.make_system(td)
            self._intervene_once(system)
            system.finish_turn(turn_id="t1", session_id="s1", assistant_response="done")
            system.start_turn(user_message="continue", session_id="s1", turn_id="t2")
            followups = [r for r in _rows(self.outcomes) if r.get("type") == "NEXT_TURN_FOLLOWUP"]
            self.assertEqual(len(followups), 1)
            self.assertEqual(followups[0]["outcome_reason"], "next-turn-followup")
            self.assertEqual(followups[0]["turn_id"], "t2")
            self.assertEqual(followups[0]["session_id_hash"], canonical_hash("s1"))
            self.assertGreaterEqual(followups[0]["seconds_since_prev_turn"], 0)
            self.assertTrue(followups[0]["prev_turn_had_intervention"])
            system.finish_turn(turn_id="t2", session_id="s1", assistant_response="done")

    def test_turn_complete_includes_nerve_totals(self):
        with tempfile.TemporaryDirectory() as td:
            system = self.make_system(td)
            system.configure(admission_enabled=True)
            ScriptedEngine.reset(FakeDecisionResult("ON", 0.95, {"ON": 0.95}))
            system.start_turn(user_message="verify", session_id="s1", turn_id="t2")
            self.assertTrue(system.drain())
            system.finish_turn(turn_id="t2", session_id="s1", assistant_response="done")
            completions = [r for r in _rows(self.events) if r.get("kind") == "turn_complete" and r.get("turn_id") == "t2"]
            self.assertEqual(len(completions), 1)
            self.assertEqual(completions[0]["nerve_latency_ms_total"], 501.0)
            self.assertEqual(completions[0]["nerve_calls"], 1)

    def test_nerve_feedback_tool_records_user_outcome_with_optional_turn_id(self):
        with tempfile.TemporaryDirectory() as td:
            system = self.make_system(td)
            with patch.object(nervous, "_default", system):
                response = json.loads(tools.nerve_feedback({"verdict": "bad"}, session_id="s1"))
            self.assertTrue(response["ok"])
            self.assertEqual(response["feedback"]["outcome_source"], "user")
            rows = [r for r in _rows(self.outcomes) if r.get("record_type") == "outcome"]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["verdict"], "bad")
            self.assertEqual(rows[0]["turn_id"], "t1")
            self.assertEqual(rows[0]["session_id_hash"], canonical_hash("s1"))

    def test_new_outcome_rows_have_no_content_keys(self):
        banned_keys = {"preview", "content", "prompt", "args", "arguments", "text", "response"}
        with tempfile.TemporaryDirectory() as td:
            system = self.make_system(td)
            self._intervene_once(system)
            system.finish_turn(turn_id="t1", session_id="s1", assistant_response="done")
            system.start_turn(user_message="continue", session_id="s1", turn_id="t2")
            with patch.object(nervous, "_default", system):
                tools.nerve_feedback({"verdict": "good", "turn_id": "t2"}, session_id="s1")
            outcomes = [r for r in _rows(self.outcomes) if r.get("record_type") == "outcome"]
            self.assertGreaterEqual(len(outcomes), 3)
            for row in outcomes:
                self.assertFalse(banned_keys.intersection(row), row)

    def test_learned_probability_feeds_router_after_min_samples(self):
        with tempfile.TemporaryDirectory() as td:
            system = self.make_system(td, local_learning=True, local_learning_min_samples=3)
            for i in range(3):
                system.start_turn(user_message="repair it", session_id="s1", turn_id=f"t{i + 2}")
                ScriptedEngine.reset("REPLAN")
                ev = dict(FAIL, turn_id=f"t{i + 2}")
                self.assertTrue(system.observe_tool_call(tool_call_id=f"a{i}", **ev)["forwarded"])
                self.assertTrue(system.drain())
                system.observe_tool_call(tool_call_id=f"b{i}", **ev)
                system.finish_turn(turn_id=f"t{i + 2}", session_id="s1", assistant_response="x")
            labeled = [r for r in _rows(self.outcomes) if r.get("record_type") == "outcome" and r.get("useful_disagreement") is not None]
            self.assertEqual(len(labeled), 3)
            system.start_turn(user_message="repair it", session_id="s1", turn_id="t9")
            ScriptedEngine.reset("REPLAN")
            system.observe_tool_call(tool_call_id="z1", **dict(FAIL, turn_id="t9"))
            router_rows = [r for r in _rows(self.events) if r.get("kind") == "router" and r.get("turn_id") == "t9"]
            self.assertTrue(router_rows)
            self.assertEqual(router_rows[-1]["historical_samples"], 3)
            self.assertIsNotNone(router_rows[-1]["historical_useful_disagreement_probability"])


if __name__ == "__main__":
    unittest.main()
