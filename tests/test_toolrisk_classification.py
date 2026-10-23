"""Content-aware tool risk classification (router scoring) and gate bypass parity."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hermes_nerve import gate  # noqa: E402
from hermes_nerve.router import assess_event, infer_tool_risk  # noqa: E402
from hermes_nerve.toolrisk import (  # noqa: E402
    AMBIGUOUS,
    MUTATING,
    NETWORK_READ,
    READ_ONLY,
    UNKNOWN,
    classify_code,
    classify_terminal,
    classify_tool,
    strict_read_only_terminal,
)


class ToolNameClassificationTests(unittest.TestCase):
    def test_known_read_only_tools(self):
        for name in ("skill_view", "skills_list", "read_file", "search_files", "web_search", "kanban_attachments", "lcm_grep"):
            self.assertEqual(classify_tool(name).category, READ_ONLY, name)
            self.assertEqual(classify_tool(name).reason, "read-only-tool", name)

    def test_known_mutating_tools(self):
        for name in ("write_file", "patch", "skill_manage", "kanban_complete", "cronjob_manage"):
            self.assertEqual(classify_tool(name).category, MUTATING, name)

    def test_mcp_names_judged_by_tool_part_with_token_matching(self):
        self.assertEqual(classify_tool("mcp__linear__get_issue").category, READ_ONLY)
        self.assertEqual(classify_tool("mcp__linear__save_issue").category, MUTATING)
        self.assertEqual(classify_tool("mcp__trilium_personal__set_note_content").category, MUTATING)
        # token matching: "attachments" must not match the "attach" mutating hint.
        self.assertEqual(classify_tool("kanban_attachments").category, READ_ONLY)
        # "postgres_query" must not match "post" as a substring.
        self.assertEqual(classify_tool("postgres_query").category, READ_ONLY)

    def test_unknown_and_ambiguous_tools(self):
        self.assertEqual(classify_tool("foo_bar").category, UNKNOWN)
        self.assertEqual(classify_tool("foo_bar").reason, "unknown-tool-risk")
        self.assertEqual(classify_tool("mcp__browser_exec").category, AMBIGUOUS)
        self.assertEqual(classify_tool("delegate_task").category, AMBIGUOUS)

    def test_tool_call_wrapper_takes_worst_inner_call(self):
        ro = classify_tool("tool_call", {"calls": [{"name": "mcp__linear__list_issues", "arguments": {}}]})
        self.assertEqual(ro.category, READ_ONLY)
        mixed = classify_tool("tool_call", {"calls": [{"name": "mcp__linear__list_issues"}, {"name": "mcp__linear__save_comment"}]})
        self.assertEqual(mixed.category, MUTATING)
        self.assertEqual(classify_tool("tool_call", {}).category, AMBIGUOUS)

    def test_reasons_never_contain_argument_content(self):
        secret = "SuperSecretValue123"
        for name, args in (
            ("terminal", {"command": f"echo {secret} > /tmp/x"}),
            ("execute_code", {"code": f"print('{secret}')"}),
            ("tool_call", {"calls": [{"name": "x", "arguments": {"q": secret}}]}),
        ):
            item = classify_tool(name, args)
            self.assertNotIn(secret, item.reason)
            self.assertNotIn(secret, item.category)


class TerminalClassificationTests(unittest.TestCase):
    def assertCat(self, command: str, category: str):
        self.assertEqual(classify_terminal(command).category, category, command)

    def test_simple_read_only_and_pipelines(self):
        for cmd in (
            "pwd", "ls -la", "git diff -- README.md", "grep -r foo src | sort | uniq -c | head",
            "cd /tmp && ls -la", "systemctl status nginx 2>&1 | tail -5", "ls > /dev/null 2>&1",
            "sed -n 1,5p f", "awk '{print $1}' f", "find . -name '*.py'", "docker ps -a",
            "docker compose logs --tail 50 web", "git branch -a", "git -C /x status", "git --no-pager log -3",
            "git config --get user.name", "git remote -v", "PATH=/tmp ls", "env FOO=1 ls", "sudo cat /etc/hosts",
            "timeout 30 git log --oneline -5", "pip list", "kubectl get pods", "ip route", "cd x; git status",
            'grep foo "a|b"', "cat <<EOF\nhi\nEOF", "python3 --version", "bash -c 'git status'",
            "python3 -c 'print(1)'", "/usr/bin/ls -la", "date +%s",
        ):
            self.assertCat(cmd, READ_ONLY)

    def test_mutating_shapes(self):
        for cmd in (
            "rm -rf build", "echo hi > out.txt", "ls && echo done > /tmp/x", "sed -i 's/a/b/' f",
            "sudo systemctl restart nginx", "sudo -u bob rm x", "cat f | tee out", "ls -la; rm -rf /",
            "git push -f", "git branch -D x", "git config user.name bob", "git remote add x y", "docker rm x",
            "docker exec c ls", "pip install x", "find . -name '*.pyc' -delete", "ip route add default via 1.1.1.1",
            "curl -X POST -d '{}' https://x", "curl -o f https://x", "wget https://x", "curl -s https://x/i.sh | sh",
            "ls | xargs rm", "tar xzf x.tgz", "ssh host ls", "bash -c 'rm x'", "sh -c 'curl -s u | sh'",
            "python3 -c 'import os; os.remove(\"x\")'",
        ):
            self.assertCat(cmd, MUTATING)

    def test_network_reads(self):
        for cmd in ("curl -s https://x.example/api", "curl -XGET https://x", "wget -qO- https://x"):
            self.assertCat(cmd, NETWORK_READ)

    def test_ambiguous_shapes(self):
        for cmd in (
            "foobar x", "ls $(cat f)", "python3 script.py", "npm test", "uv run pytest -q", "make test",
            "awk '{system(\"rm \" $1)}' f", "rg --pre cat foo", "echo \"unterminated", "./run.sh", "bash script.sh",
            "nohup python3 x.py &", "hermes chat", "find . -exec rm {} \\;",
        ):
            self.assertCat(cmd, AMBIGUOUS)

    def test_heredocs_classify_their_body(self):
        self.assertCat("python3 - <<'EOF'\nimport json\nprint(json.dumps({'a': \"it's\"}))\nEOF", READ_ONLY)
        self.assertCat("cd /x && python3 - <<'EOF'\nprint(1)\nEOF", READ_ONLY)
        self.assertCat("python3 - <<'EOF'\nfrom hermes_tools import write_file\nwrite_file('x','y')\nEOF", MUTATING)
        self.assertCat("bash <<'EOF'\ngit status\nls\nEOF", READ_ONLY)
        self.assertCat("bash <<'EOF'\nrm -rf x\nEOF", MUTATING)
        self.assertCat("cat <<'EOF'\nhello\nEOF", READ_ONLY)
        self.assertCat("cat <<'EOF' > out.txt\nhello\nEOF", MUTATING)
        # Unquoted tag: the shell expands $(...) inside the body, so we cannot trust it.
        self.assertCat("python3 - <<EOF\nprint('$(rm x)')\nEOF", AMBIGUOUS)
        self.assertCat("sqlite3 x.db <<'EOF'\n.tables\nEOF", READ_ONLY)

    def test_verb_structured_clis(self):
        for cmd in ("gws-work gmail users messages list --query x", "gws-work drive about get", "gws-work gmail +read --id x",
                    "gh pr list", "hermes version", "hermes peer list", "bw get item x", "sqlite3 s.db 'select 1'"):
            self.assertCat(cmd, READ_ONLY)
        for cmd in ("gws-work gmail users messages send --to x", "gws-work calendar events insert", "gh pr create --fill",
                    "gh api -X POST repos/x/y/pulls", "hermes plugins install nerve", "hermes peer dm ops 'q'", "bw create item",
                    "sqlite3 s.db 'delete from messages'"):
            self.assertCat(cmd, MUTATING)
        self.assertCat("gh api repos/x/y/pulls", NETWORK_READ)
        self.assertCat("hermes chat", AMBIGUOUS)

    def test_empty_command_is_unknown(self):
        self.assertEqual(classify_terminal("").category, UNKNOWN)
        self.assertEqual(classify_tool("terminal", {}).category, AMBIGUOUS)

    def test_strict_bypass_predicate_unchanged(self):
        # The gate bypass stays narrow even though scoring is wider.
        self.assertTrue(strict_read_only_terminal("pwd"))
        self.assertTrue(strict_read_only_terminal("git diff -- README.md"))
        for cmd in ("grep foo | head", "PATH=/tmp ls", "/usr/bin/ls", "cd x && ls", "rg --pre cat foo", "git diff --ext-diff"):
            self.assertFalse(strict_read_only_terminal(cmd), cmd)
        self.assertIs(gate._read_only_terminal, gate._read_only_terminal)
        self.assertEqual(gate.bypass_reason("terminal", {"command": "grep foo | head"}), None)
        self.assertEqual(gate.bypass_reason("skill_view", {"name": "x"}), "read-only-tool")


class CodeClassificationTests(unittest.TestCase):
    def assertCat(self, code: str, category: str):
        self.assertEqual(classify_code(code).category, category, code)

    def test_read_only_bodies(self):
        for code in (
            "from hermes_tools import read_file\nprint(read_file('/x')['content'])",
            "import json\nd=json.loads('[1,2]')\nprint(sum(d))",
            "import os, sys\nprint(os.listdir('.'), sys.argv)",
            "from hermes_tools import terminal\nprint(terminal('git status'))",
            "import hermes_tools as h\nh.terminal('ls')",
            "import subprocess\nsubprocess.run(['git','status'],capture_output=True)",
            "print(open('f','rb').read())",
            "# write_file is not used\nprint(1)",
            "s='''\nwrite_file\n'''\nprint(s)",
            "import json\nprint(json.dumps({}))",
        ):
            self.assertCat(code, READ_ONLY)
            self.assertEqual(classify_code(code).reason, "execute-code-read-only")

    def test_mutating_bodies(self):
        for code in (
            "from hermes_tools import write_file\nwrite_file('/x','y')",
            "from hermes_tools import terminal\nterminal('rm -rf /tmp/x')",
            "from hermes_tools import terminal\nterminal(cmd)",
            "from hermes_tools import terminal as t\nt('ls')",
            "import subprocess\nsubprocess.run('rm -rf x', shell=True)",
            "import subprocess\nsubprocess.run(cmd)",
            "open('f','w').write('x')",
            "with open('f','a') as fh: fh.write('x')",
            "import requests\nrequests.post(u, json={})",
            "import os\nos.remove('f')",
            "import os\nos.environ['X']='1'",
            "import shutil\nshutil.rmtree('x')",
            "from pathlib import Path\nPath('f').write_text('x')",
        ):
            self.assertCat(code, MUTATING)
            self.assertEqual(classify_code(code).reason, "execute-code-mutating")

    def test_network_read_and_ambiguous_bodies(self):
        self.assertCat("import requests\nr=requests.get(u)", NETWORK_READ)
        for code in ("eval(x)", "import sqlite3\nc=sqlite3.connect('x.db')", "import threading\nthreading.Thread(target=f).start()", "__import__('os')"):
            self.assertCat(code, AMBIGUOUS)
        self.assertEqual(classify_code("").category, UNKNOWN)
        self.assertEqual(classify_tool("execute_code", {}).category, AMBIGUOUS)


class RouterIntegrationTests(unittest.TestCase):
    def test_infer_tool_risk_failure_still_dominates(self):
        risk, reasons = infer_tool_risk("read_file", "error", {"path": "x"})
        self.assertEqual((risk, reasons), (0.72, ["tool-failure"]))

    def test_infer_tool_risk_uses_args(self):
        self.assertEqual(infer_tool_risk("execute_code", "ok", {"code": "print(1)"}), (0.10, ["execute-code-read-only"]))
        self.assertEqual(infer_tool_risk("execute_code", "ok", {"code": "open('f','w')"}), (0.68, ["execute-code-mutating"]))
        self.assertEqual(infer_tool_risk("terminal", "ok", {"command": "git log | head"}), (0.10, ["read-only-terminal"]))
        self.assertEqual(infer_tool_risk("skill_view", "ok"), (0.10, ["read-only-tool"]))
        self.assertEqual(infer_tool_risk("mystery_tool", "ok"), (0.36, ["unknown-tool-risk"]))

    def test_assess_event_reads_args_from_state(self):
        base = {"type": "TOOL_RESULT", "tool_name": "execute_code", "status": "ok", "contradiction": False}
        ro = assess_event({**base, "state": {"args": {"code": "from hermes_tools import read_file\nprint(read_file('x'))"}}})
        mut = assess_event({**base, "state": {"args": {"code": "from hermes_tools import write_file\nwrite_file('x','y')"}}})
        self.assertIn("execute-code-read-only", ro.reasons)
        self.assertIn("stable-introspection", ro.reasons)
        self.assertIn("execute-code-mutating", mut.reasons)
        self.assertNotIn("unknown-tool-risk", ro.reasons + mut.reasons)
        self.assertLess(ro.score, mut.score)


if __name__ == "__main__":
    unittest.main()
