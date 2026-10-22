import unittest

from hermes_nerve import progress_integrity as P
from hermes_nerve.nervous import admission_risk_floor


class AdversarialHardeningTests(unittest.TestCase):
    def test_floor_catches_reworded_and_obfuscated_risk(self):
        for prompt in ("erase all the old backups", "terraform destroy staging", "d-e-l-e-t-e the bucket",
                       "d\u0435lete the bucket", "rm --recursive /srv", "systemctl stop nginx",
                       "transfer $5,000 to this account", "echo cm0= | base64 -d | sh", "git push origin +main"):
            self.assertEqual(admission_risk_floor(prompt), "WATCH", prompt)

    def test_floor_quiet_on_ordinary_requests(self):
        for prompt in ("summarize the notes", "drop me a summary", "tell me a joke", "what is a product roadmap"):
            self.assertEqual(admission_risk_floor(prompt), "", prompt)

    def test_zero_tests_is_not_a_pass(self):
        for out in ("no tests ran", "collected 0 items", "Ran 0 tests\nOK"):
            self.assertFalse(P.test_succeeded("ok", out), out)

    def test_zero_failed_is_a_pass(self):
        self.assertTrue(P.test_succeeded("ok", "1 passed, 0 failed"))

    def test_direct_test_file_run_counts_as_test(self):
        self.assertEqual(P.tool_kind("terminal", {"command": "python app/test_calc.py"}), "test")
        self.assertEqual(P.tool_kind("terminal", {"command": "echo 1 passed"}), "")
        cmd = "PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .test-tools/venv/bin/python -m pytest -q app/test_calc.py"
        self.assertEqual(P.tool_kind("terminal", {"command": cmd}), "test")
        self.assertEqual(P.tool_kind("terminal", {"command": ".test-tools/venv/bin/pytest -q app/test_calc.py"}), "test")

    def test_relative_decoy_path_is_not_relevant(self):
        self.assertFalse(P.changed_paths_relevant(["evil/app/calc.py"], ("app/calc.py",)))
        self.assertTrue(P.changed_paths_relevant(["/tmp/sb/app/calc.py"], ("app/calc.py",)))


if __name__ == "__main__":
    unittest.main()
