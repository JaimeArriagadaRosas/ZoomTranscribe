import logging
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess

from scripts.auth import AuthFailure, prepare_auth


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.logger = logging.getLogger(f"auth-test-{id(self)}")
        self.logger.addHandler(logging.NullHandler())
        self.url = "https://unab-cl.zoom.us/rec/play/example"
        self.config = {
            "browser": "opera",
            "browser_priority": ["opera", "chrome"],
            "cookies_file": "private/zoom.cookies.txt",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_anonymous_access_avoids_browser_cookie_database(self):
        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            if command[0] == "tasklist":
                return CompletedProcess(command, 0, "", "")
            return CompletedProcess(command, 0, "Zoom class\n", "")

        auth = prepare_auth(self.root, self.config, self.url, self.logger, runner=runner)
        self.assertEqual(auth.mode, "anonymous")
        self.assertFalse(any("--cookies-from-browser" in command for command in calls))

    def test_existing_cookie_file_is_reused(self):
        path = self.root / "private" / "zoom.cookies.txt"
        path.parent.mkdir(parents=True)
        path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

        def runner(command, **kwargs):
            return CompletedProcess(command, 0, "Zoom class\n", "")

        auth = prepare_auth(self.root, self.config, self.url, self.logger, runner=runner)
        self.assertEqual(auth.mode, "cookies_file")
        self.assertEqual(auth.source, "existing_cookie_file")

    def test_locked_browser_fails_before_batch_with_actionable_message(self):
        def runner(command, **kwargs):
            if command[0] == "tasklist":
                return CompletedProcess(command, 0, '"opera.exe","123","Console","1","10,000 K"\n', "")
            if "--cookies-from-browser" in command:
                return CompletedProcess(command, 1, "", "ERROR: Could not copy Chrome cookie database")
            return CompletedProcess(command, 1, "", "ERROR: Sign in required")

        with self.assertRaises(AuthFailure) as raised:
            prepare_auth(self.root, self.config, self.url, self.logger, runner=runner)
        self.assertIn("Opera", str(raised.exception))
        self.assertIn("bloqueada", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
