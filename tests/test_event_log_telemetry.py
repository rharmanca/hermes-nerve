"""Event-log rows must be self-describing enough to evaluate Nerve without ever opening
Hermes state databases, while carrying no arguments, output, prompts or error text."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_nerve import nervous
from hermes_nerve.router import failure_category, FAILURE_CATEGORIES, safe_tool_name

try:
    from tests.test_nervous import ScriptedEngine
except ModuleNotFoundError:  # `unittest discover -s tests` imports top-level module names
    from test_nervous import ScriptedEngine


SECRETISH = ("/srv/vendor-acme/invoice-2026.pdf", "hr@example-corp.com", "sk-live-abcdef", "Traceback (most recent call last)")


def _rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


class FailureCategoryTests(unittest.TestCase):
    def test_fixed_vocabulary(self):
        cases = [
            ("blocked", "", "", None, "blocked"),
            ("error", "", "Permission denied: /etc/shadow", None, "permission"),
            ("error", "", "blocked: approval required for send", None, "blocked"),
            ("error", "", "ENOENT: no such file or directory", None, "not-found"),
            ("error", "", "error: unrecognized arguments: --frob", None, "invalid-argument"),
            ("error", "", "The read operation timed out", None, "timeout"),
            ("error", "", "connection refused", None, "network"),
            ("error", "", "HTTP 429 too many requests", None, "rate-limit"),
            ("error", "SyntaxError: invalid syntax", "", 1, "syntax"),
            ("error", "", "ModuleNotFoundError: No module named 'x'", 1, "import"),
            ("error", "FAILED tests/test_x.py::test_y - AssertionError", "", 1, "test-failure"),
            ("error", "", "", 2, "nonzero-exit"),
            ("error", "something odd happened", "", None, "other"),
            ("ok", "all good", "", 0, ""),
        ]
        for status, result, error, exit_code, expected in cases:
            with self.subTest(error=error or result):
                self.assertEqual(failure_category(status=status, result=result, error_message=error, exit_code=exit_code), expected)

    def test_category_is_never_free_text(self):
        for text in SECRETISH + ("rm -rf /", "DROP TABLE users"):
            category = failure_category(status="error", result=text, error_message=text, exit_code=1)
            self.assertIn(category, FAILURE_CATEGORIES)
            self.assertNotIn(text, category)

    def test_safe_tool_name(self):
        self.assertEqual(safe_tool_name("terminal"), "terminal")
        self.assertEqual(safe_tool_name("mcp__gws_work__gmail users messages list"), "mcp__gws_work__gmail_users_messages_list")
        self.assertEqual(safe_tool_name(""), "unknown")
        self.assertEqual(safe_tool_name(None), "unknown")
        self.assertEqual(len(safe_tool_name("x" * 500)), 80)
        self.assertEqual(safe_tool_name("Tool/With:Odd\nChars"), "tool_with_odd_chars")


class RouterRowTelemetryTests(unittest.TestCase):
    def make_system(self, td):
        self.events = str(Path(td) / "n.jsonl")
        env = patch.dict(os.environ, {"HERMES_NERVE_NERVOUS_EVENTS": self.events, "HERMES_NERVE_OUTCOMES": str(Path(td) / "o.jsonl")}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        system = nervous.NervousSystem(engine_factory=ScriptedEngine)
        system.configure(enabled=True, admission_enabled=False, mode="shadow")
        system.start_turn(user_message="please fix the invoice export for hr@example-corp.com", session_id="s1", turn_id="t1")
        return system

    def _router_rows(self):
        return [r for r in _rows(self.events) if r.get("kind") == "router"]

    def test_router_row_names_tool_status_and_risk(self):
        with tempfile.TemporaryDirectory() as td:
            system = self.make_system(td)
            system.observe_tool_call(tool_call_id="c1", tool_name="terminal", args={"command": "git status"}, status="ok", result="clean", session_id="s1", turn_id="t1")
            row = self._router_rows()[-1]
            self.assertEqual(row["tool_name"], "terminal")
            self.assertEqual(row["status"], "ok")
            self.assertEqual(row["failure_category"], "")
            self.assertEqual(row["failure_repeat_count"], 0)
            self.assertIsInstance(row["risk"], float)
            self.assertIn("read-only-terminal", row["risk_reasons"])

    def test_router_row_categorizes_failures(self):
        with tempfile.TemporaryDirectory() as td:
            system = self.make_system(td)
            ScriptedEngine.reset("CONTINUE", "CONTINUE")
            ev = dict(tool_name="execute_code", args={"code": "open('/srv/vendor-acme/invoice-2026.pdf')"}, status="error",
                      result="Traceback (most recent call last)\nFileNotFoundError: no such file or directory: /srv/vendor-acme/invoice-2026.pdf",
                      error_message="FileNotFoundError: no such file or directory", session_id="s1", turn_id="t1")
            system.observe_tool_call(tool_call_id="c1", **ev)
            system.observe_tool_call(tool_call_id="c2", **ev)
            rows = self._router_rows()
            self.assertEqual(rows[-1]["tool_name"], "execute_code")
            self.assertEqual(rows[-1]["status"], "error")
            self.assertEqual(rows[-1]["failure_category"], "not-found")
            self.assertEqual(rows[-1]["failure_repeat_count"], 2)
            self.assertTrue(rows[-1]["deterministic_failure"])

    def test_log_rows_never_carry_content(self):
        with tempfile.TemporaryDirectory() as td:
            system = self.make_system(td)
            ScriptedEngine.reset("REPLAN", "REPLAN")
            ev = dict(tool_name="terminal", args={"command": "curl -H 'Authorization: Bearer sk-live-abcdef' https://example-corp.com/hr"}, status="error",
                      result="Traceback (most recent call last)\nPermission denied: /srv/vendor-acme/invoice-2026.pdf", error_message="Permission denied hr@example-corp.com",
                      session_id="s1", turn_id="t1")
            system.observe_tool_call(tool_call_id="c1", **ev)
            self.assertTrue(system.drain())
            system.observe_tool_call(tool_call_id="c2", **ev)
            system.finish_turn(turn_id="t1", session_id="s1", assistant_response="done hr@example-corp.com")
            text = Path(self.events).read_text()
            for banned in SECRETISH + ("curl", "Authorization", "please fix"):
                self.assertNotIn(banned, text)

    def test_assessment_rows_name_tool_and_event_type(self):
        with tempfile.TemporaryDirectory() as td:
            system = self.make_system(td)
            ScriptedEngine.reset("REPLAN")
            system.observe_tool_call(tool_call_id="c1", tool_name="terminal", args={"command": "python x.py"}, status="error", result="boom", error_message="boom", session_id="s1", turn_id="t1")
            self.assertTrue(system.drain())
            rows = [r for r in _rows(self.events) if r.get("kind") in {"assessment_silent", "challenge"}]
            self.assertTrue(rows)
            for row in rows:
                self.assertEqual(row["tool_name"], "terminal")
                self.assertEqual(row["event_type"], "FAILURE")

    def test_turn_complete_carries_aggregates(self):
        with tempfile.TemporaryDirectory() as td:
            system = self.make_system(td)
            ScriptedEngine.reset("CONTINUE", "CONTINUE", "CONTINUE")
            system.observe_tool_call(tool_call_id="c1", tool_name="terminal", args={"command": "ls"}, status="ok", result="x", session_id="s1", turn_id="t1")
            system.observe_tool_call(tool_call_id="c2", tool_name="terminal", args={"command": "python x.py"}, status="error", result="The read operation timed out", error_message="timed out", session_id="s1", turn_id="t1")
            system.observe_tool_call(tool_call_id="c3", tool_name="read_file", args={"path": "/srv/vendor-acme/invoice-2026.pdf"}, status="error", result="", error_message="No such file or directory", session_id="s1", turn_id="t1")
            system.finish_turn(turn_id="t1", session_id="s1", assistant_response="done")
            row = [r for r in _rows(self.events) if r.get("kind") == "turn_complete"][-1]
            self.assertEqual(row["tool_counts"], {"terminal": 2, "read_file": 1})
            self.assertEqual(row["failure_categories"], {"timeout": 1, "not-found": 1})
            self.assertEqual(row["tool_failures"], 2)


if __name__ == "__main__":
    unittest.main()
