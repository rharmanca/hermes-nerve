"""nudge_only supervision mode and INF-31 external-write progress.

nudge_only keeps correct_next challenge injection (<JEV_NERVOUS_SYSTEM>) but no
Nerve code path may return block / continue / approve to Hermes.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_nerve import gate, nervous
from hermes_nerve.progress_integrity import (
    compile_external_write_patterns,
    external_write_succeeded,
)
from tests.test_nervous import ScriptedEngine


def _env(td):
    return patch.dict(os.environ, {
        "HERMES_NERVE_NERVOUS_EVENTS": str(Path(td) / "nervous.jsonl"),
        "HERMES_NERVE_OUTCOMES": str(Path(td) / "outcomes.jsonl"),
    }, clear=False)


def _system(mode, **extra):
    ScriptedEngine.reset("CONTINUE", "CONTINUE", "CONTINUE")
    system = nervous.NervousSystem(engine_factory=ScriptedEngine)
    system.configure(enabled=True, admission_enabled=False, mode=mode, **extra)
    return system


def _events(td):
    path = Path(td) / "nervous.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class ModeParsingTests(unittest.TestCase):
    def test_nudge_only_is_a_known_mode(self):
        self.assertIn("nudge_only", nervous.SUPERVISION_MODES)
        self.assertEqual(_system("nudge_only")._config.mode, "nudge_only")
        self.assertEqual(_system(" NUDGE_ONLY ")._config.mode, "nudge_only")

    def test_misspelled_nudge_value_fails_safe_to_shadow(self):
        for value in ("nudge-only", "nudge", "nudgeonly", "nudge_onyl", "Nudge Only"):
            with self.subTest(value=value):
                self.assertEqual(_system(value)._config.mode, "shadow")

    def test_unknown_mode_fails_safe_to_shadow_with_warning(self):
        for value in ("bogus", "enforce", "", "block"):
            with self.subTest(value=value):
                with self.assertLogs("hermes_nerve.nervous", level="WARNING") as logs:
                    self.assertEqual(_system(value)._config.mode, "shadow")
                self.assertTrue(any("nerve_unknown_mode" in m for m in logs.output))

    def test_default_mode_is_shadow(self):
        self.assertEqual(nervous.NervousConfig().mode, "shadow")
        system = nervous.NervousSystem(engine_factory=ScriptedEngine)
        self.assertEqual(system._config.mode, "shadow")

    def test_plugin_nervous_mode_defaults_are_shadow(self):
        src = (Path(__file__).resolve().parents[1] / "__init__.py").read_text()
        self.assertNotIn('"nervous_mode", "correct_next"', src)
        self.assertNotIn('"nervous_mode","correct_next"', src)
        self.assertEqual(src.count('home=%s'), 2)


class NeverReturnsDirectiveTests(unittest.TestCase):
    """Whatever the internal rules decide, nudge_only returns None."""

    DIRECTIVES = (
        {"action": "block", "message": "x"},
        {"action": "continue", "message": "x"},
        {"action": "approve", "message": "x", "rule_key": "k"},
    )

    def test_public_seams_return_none_for_every_directive(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td, _env(td):
            for directive in self.DIRECTIVES:
                system = _system("nudge_only")
                with patch.object(system, "_before_tool_call", return_value=dict(directive)), \
                        patch.object(system, "_completion_gate", return_value=dict(directive)):
                    self.assertIsNone(system.before_tool_call(tool_name="terminal", args={}))
                    self.assertIsNone(system.completion_gate(final_response="done"))
            kinds = [e.get("kind") or e.get("type") for e in _events(td)]
            self.assertGreaterEqual(sum(1 for e in _events(td) if "nudge_only_suppressed" in json.dumps(e)), 6)
            self.assertTrue(kinds)

    def test_correct_next_still_returns_directives(self):
        system = _system("correct_next")
        with patch.object(system, "_before_tool_call", return_value={"action": "block"}):
            self.assertEqual(system.before_tool_call(tool_name="terminal", args={})["action"], "block")


class ScenarioTests(unittest.TestCase):
    def _repeat_failure(self, mode, td):
        system = _system(mode, repeated_failure_local_replan_at=3)
        system.start_turn(user_message="repair it", session_id="s1", turn_id="t1")
        event = dict(tool_name="terminal", args={"command": "python broken.py"}, status="error",
                     result="Traceback: connection failed after 501 ms",
                     error_message="connection failed after 501 ms", session_id="s1", turn_id="t1")
        system.observe_tool_call(tool_call_id="c1", **event)
        system.drain()
        system.observe_tool_call(tool_call_id="c2", **event)
        system.observe_tool_call(tool_call_id="c3", **event)
        return system, system.before_tool_call(
            tool_name="terminal", args={"command": "python broken.py"},
            session_id="s1", turn_id="t1", tool_call_id="c4")

    def test_repeated_failure_blocks_in_correct_next_but_only_nudges_in_nudge_only(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td, _env(td):
            _, blocked = self._repeat_failure("correct_next", td)
            self.assertEqual(blocked["action"], "block")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td, _env(td):
            system, result = self._repeat_failure("nudge_only", td)
            self.assertIsNone(result)
            events = json.dumps(_events(td))
            self.assertIn("nudge_only_suppressed", events)
            self.assertIn("control_nudged", events)
            self.assertNotIn("control_enforced", events)

    def test_deterministic_failure_challenge_injected_but_not_enforced(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td, _env(td):
            system = _system("nudge_only")
            system.start_turn(user_message="recover from policy block", session_id="s1", turn_id="t1")
            system.observe_tool_call(
                tool_name="execute_code", args={"code": "print('x')"}, status="error",
                result="BLOCKED: execute_code is not allowed in an unattended session; single-query mode requires approval",
                error_message="", tool_call_id="c1", session_id="s1", turn_id="t1")
            injected = system.inject_challenge("blocked", turn_id="t1")
            self.assertIn("<JEV_NERVOUS_SYSTEM>", injected)
            self.assertIsNone(system.before_tool_call(
                tool_name="execute_code", args={"code": "print('x')"},
                session_id="s1", turn_id="t1", tool_call_id="c2"))

    def test_progress_stall_nudges_and_never_blocks(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td, _env(td):
            system = _system("nudge_only")
            system.start_turn(user_message="Fix src/parser.py and add regression tests",
                              session_id="s1", turn_id="t1")
            injected = []
            for i in range(14):
                self.assertIsNone(system.before_tool_call(
                    tool_name="read_file", args={"path": f"s{i}.py"},
                    session_id="s1", turn_id="t1", tool_call_id=f"p{i}"))
                system.observe_tool_call(tool_name="read_file", args={"path": f"s{i}.py"}, status="ok",
                                         result="c", error_message="", tool_call_id=f"r{i}",
                                         session_id="s1", turn_id="t1")
                # Hermes order: post_tool_call, then transform_tool_result.
                injected.append(system.inject_challenge("r", turn_id="t1") or "")
            self.assertTrue(system.status(turn_id="t1")["progress_challenge_issued"])
            self.assertEqual(sum("<JEV_NERVOUS_SYSTEM>" in x for x in injected), 1)
            for i in range(4):
                self.assertIsNone(system.before_tool_call(
                    tool_name="read_file", args={"path": f"x{i}.py"},
                    session_id="s1", turn_id="t1", tool_call_id=f"x{i}"))
            # Completion integrity would force "continue" in correct_next.
            self.assertIsNone(system.completion_gate(turn_id="t1", coding=True, final_response="done"))
            self.assertIn("progress_completion_nudged", json.dumps(_events(td)))
            # Scorecard/health accounting matches shadow's would-block counter.
            self.assertEqual(system.quality_metrics().get("progress_completion_block_shadowed",
                             system.report(recent_limit=0, include_recent=False, include_outcomes=False)
                             ["metrics"].get("progress_completion_block_shadowed")), 1)

    def test_completion_integrity_continue_in_correct_next(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td, _env(td):
            system = _system("correct_next")
            system.start_turn(user_message="Fix src/parser.py", session_id="s1", turn_id="t1")
            self.assertEqual(system.completion_gate(turn_id="t1", coding=True)["action"], "continue")

    def test_policy_memory_never_blocks_in_nudge_only(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td, _env(td):
            system = _system("nudge_only", policy_memory_enabled=True,
                             policy_memory_path=str(Path(td) / "policy.json"))

            class Hit:
                reason = "r"
                evidence_fingerprint = "f"
                expires_at_epoch = 0

            system.start_turn(user_message="do it", session_id="s1", turn_id="t1")
            fake = type("PM", (), {"blocked": lambda self, name: Hit()})()
            system._policy_memory = fake
            self.assertIsNone(system.before_tool_call(tool_name="execute_code", args={},
                                                      session_id="s1", turn_id="t1"))
            self.assertIn("policy_memory_nudged", json.dumps(_events(td)))


class PreToolChainTests(unittest.TestCase):
    def tearDown(self):
        nervous.configure(mode="shadow")

    def _chain(self):
        def nervous_cb(**kw):
            return {"action": "block", "message": "n"}

        def gate_cb(**kw):
            raise RuntimeError("judge outage")

        records = []
        return gate.instrument_pre_tool_chain(
            (("nervous", nervous_cb), ("action_gate", gate_cb, True)), records.append), records

    def test_chain_returns_nothing_under_nudge_only_even_on_gate_outage(self):
        nervous.configure(mode="nudge_only")
        chain, records = self._chain()
        self.assertIsNone(chain(tool_name="terminal", args={}))
        self.assertTrue(any(r.get("outcome") == "suppressed:nudge_only" for r in records))

    def test_gate_outage_still_asks_for_approval_outside_nudge_only(self):
        nervous.configure(mode="correct_next")
        chain, _ = self._chain()
        self.assertEqual(chain(tool_name="terminal", args={})["action"], "block")

        def gate_cb(**kw):
            raise RuntimeError("judge outage")
        chain2 = gate.instrument_pre_tool_chain((("action_gate", gate_cb, True),), lambda r: None)
        self.assertEqual(chain2(tool_name="terminal", args={})["action"], "approve")

    def test_work_supervision_directives_untouched(self):
        nervous.configure(mode="nudge_only")
        chain = gate.instrument_pre_tool_chain(
            (("work_supervision", lambda **kw: {"action": "block"}),), lambda r: None)
        self.assertEqual(chain(tool_name="terminal", args={})["action"], "block")


class ExternalWriteProgressTests(unittest.TestCase):
    def test_name_matching(self):
        pat = compile_external_write_patterns(None)
        for name in ("mcp__linear__save_issue", "kanban_create", "kanban_comment", "send_message",
                     "mcp__trilium_personal__create_note", "mcp__trilium_personal__set_attribute",
                     "gmail_create_draft", "http_post", "api-put"):
            with self.subTest(name=name):
                self.assertTrue(external_write_succeeded(name, pat))
        for name in ("mcp__linear__get_issue", "mcp__linear__list_issues", "web_search", "read_file",
                     "terminal", "write_file", "patch", "nerve_stats", "memory", "kanban_show",
                     "mcp__trilium_personal__get_note_content", "browser_snapshot", "kanban_heartbeat"):
            with self.subTest(name=name):
                self.assertFalse(external_write_succeeded(name, pat))
        self.assertIsNone(compile_external_write_patterns("off"))
        custom = compile_external_write_patterns(["^custom_writer$"])
        self.assertTrue(external_write_succeeded("custom_writer", custom))
        self.assertFalse(external_write_succeeded("mcp__linear__save_issue", custom))

    def _api_only(self, mode, td, *, write_status="ok"):
        system = _system(mode)
        system.start_turn(user_message="Fix the vendor note and update the tracking issue with the result",
                          session_id="s1", turn_id="t1")
        directives = []
        for i in range(14):
            name = "mcp__trilium_personal__get_note_content" if i % 2 else "mcp__linear__get_issue"
            directives.append(system.before_tool_call(tool_name=name, args={}, session_id="s1",
                                                      turn_id="t1", tool_call_id=f"p{i}"))
            system.observe_tool_call(tool_name=name, args={}, status="ok", result="{}", error_message="",
                                     tool_call_id=f"g{i}", session_id="s1", turn_id="t1")
            if i == 3:
                system.observe_tool_call(tool_name="mcp__trilium_personal__set_note_content", args={},
                                         status=write_status, result="{}",
                                         error_message="" if write_status == "ok" else "boom",
                                         tool_call_id="w1", session_id="s1", turn_id="t1")
        return system, directives

    def test_api_only_honest_task_is_not_flagged_even_in_correct_next(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td, _env(td):
            system, directives = self._api_only("correct_next", td)
            state = system.status(turn_id="t1")
            self.assertTrue(system._resolve_turn(turn_id="t1").change_required)
            self.assertGreaterEqual(state["relevant_mutation_attempts"], 1)
            self.assertFalse(state["progress_challenge_issued"])
            self.assertEqual([d for d in directives if d], [])
            self.assertIsNone(system.completion_gate(turn_id="t1", coding=True, final_response="done"))
            events = json.dumps(_events(td))
            self.assertNotIn("local-progress-stall", events)
            self.assertNotIn("progress_completion_blocked", events)

    def test_failed_external_write_is_not_progress(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td, _env(td):
            system, _ = self._api_only("nudge_only", td, write_status="error")
            self.assertEqual(system.status(turn_id="t1")["relevant_mutation_attempts"], 0)
            self.assertTrue(system.status(turn_id="t1")["progress_challenge_issued"])


if __name__ == "__main__":
    unittest.main()
