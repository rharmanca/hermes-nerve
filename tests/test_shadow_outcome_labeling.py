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

from hermes_nerve import nervous

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

    def test_ambiguous_turn_writes_no_label(self):
        with tempfile.TemporaryDirectory() as td:
            system = self.make_system(td)
            self._intervene_once(system)
            system.observe_tool_call(tool_call_id="c2", **OK_OTHER_ACTION)
            system.finish_turn(turn_id="t1", session_id="s1", assistant_response="gave up")
            outcomes = [r for r in _rows(self.outcomes) if r.get("record_type") == "outcome"]
            self.assertEqual(outcomes, [])
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
            labeled = [r for r in _rows(self.outcomes) if r.get("record_type") == "outcome"]
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
