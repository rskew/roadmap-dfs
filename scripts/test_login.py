#!/usr/bin/env python3
"""login.py: signing claude in from the page, against a stub of claude's `auth` that says what
the real one says (claude-code 2.1.308, read with stdin a pipe): the link, the prompt for the
code, and "Login failed: Request failed with status code 400" for a code it does not take.

Run:  python3 roadmap-dfs/scripts/test_login.py
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths as dfs_paths   # noqa: E402
import login as dfs_login   # noqa: E402

URL = ("https://claude.com/cai/oauth/authorize?code=true&client_id=c&response_type=code"
       "&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback&state=s")

# `$LOGIN_STUB_DIR/account` is the login claude has stored; `argv` is every command it was given.
STUB = r"""#!/usr/bin/env bash
echo "$*" >> "$LOGIN_STUB_DIR/argv"
if [ "$1 $2" = "auth status" ]; then
  if [ -f "$LOGIN_STUB_DIR/account" ]; then cat "$LOGIN_STUB_DIR/account"
  else echo '{"loggedIn": false, "authMethod": "none"}'; exit 1; fi
  exit 0
fi
if [ "$1 $2" = "auth login" ]; then
  echo "Opening browser to sign in…"
  printf '\033[2mIf the browser didn'"'"'t open, visit: %s\033[0m\n' "URL_HERE"
  printf 'Paste code here if prompted > '
  read -r code
  if [ "$code" = "good#code" ]; then
    echo '{"loggedIn": true, "authMethod": "claude.ai", "email": "a@example.com", "subscriptionType": "max", "orgId": "o"}' > "$LOGIN_STUB_DIR/account"
    echo "Login successful."
    exit 0
  fi
  echo "Login failed: Request failed with status code 400" >&2
  exit 1
fi
exit 2
""".replace("URL_HERE", URL)


def until(f, what, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        v = f()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("timed out waiting for " + what)


class Login(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / ".dfs").mkdir()
        (self.root / ".dfs" / "ROADMAP.md").write_text("# Roadmap\n")
        subprocess.run("git init -q -b master", shell=True, cwd=self.root, check=True)
        stub = self.root / "claude-stub"
        stub.write_text(STUB)
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        self.saved = {k: os.environ.get(k) for k in ("CLAUDE_CMD", "LOGIN_STUB_DIR")}
        os.environ.update(CLAUDE_CMD=str(stub), LOGIN_STUB_DIR=str(self.root))
        self._wr = dfs_paths.work_root
        dfs_paths.work_root = lambda: self.root
        self.login = dfs_login.Login()

    def tearDown(self):
        self.login.cancel()
        dfs_paths.work_root = self._wr
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.root, ignore_errors=True)

    def argv(self):
        return (self.root / "argv").read_text().splitlines()

    def waiting(self):
        self.login.start()
        return until(lambda: self.login.state()["phase"] == "waiting" and self.login.state(), "the link")

    def test_the_first_ask_reads_who_is_signed_in_and_later_ones_do_not(self):
        st = self.login.state()
        self.assertEqual(st, dict(phase="idle", url="", error="", account=dict(loggedIn=False, authMethod="none")))
        self.login.state()
        self.assertEqual(self.argv(), ["auth status --json"], "one read, not one per poll")

    def test_the_link_then_the_code_signs_in_and_says_who(self):
        st = self.waiting()
        self.assertEqual(st["url"], URL, "the link, out of claude's colours")
        self.login.start()
        self.assertEqual(self.login.state()["phase"], "waiting", "a second Sign in keeps the one going")
        self.login.code("  good#code\n")
        st = until(lambda: self.login.state()["phase"] == "done" and self.login.state(), "the sign-in")
        self.assertEqual(st["account"], dict(loggedIn=True, authMethod="claude.ai", email="a@example.com",
                                             subscriptionType="max"))
        self.assertEqual(sorted(self.argv()), ["auth login --claudeai", "auth status --json", "auth status --json"],
                         "one login, and the account read again after it")

    def test_opening_the_dialog_again_reads_who_is_signed_in_now(self):
        answers = iter([dict(loggedIn=False, error="claude auth status: timed out"),
                        dict(loggedIn=True, email="a@example.com"), dict(loggedIn=True, email="b@example.com")])
        login = dfs_login.Login(read=lambda: next(answers))
        self.assertEqual(login.refresh()["error"], "claude auth status: timed out")
        self.assertEqual(login.refresh()["email"], "a@example.com", "a failed first read does not stick")
        self.assertEqual(login.state()["account"]["email"], "a@example.com", "a poll does not read")
        login.phase = "waiting"
        self.assertEqual(login.refresh()["email"], "a@example.com", "no read while a sign-in is going")
        login.phase = "idle"
        self.assertEqual(login.refresh()["email"], "b@example.com", "the login changed outside the page")

    def test_a_read_that_began_first_and_ended_last_does_not_win(self):
        import threading
        go = threading.Event()
        answers = iter([dict(loggedIn=False), dict(loggedIn=True, email="new@example.com")])

        def read():
            a = next(answers)
            if not a["loggedIn"]:
                go.wait(5)          # the first read is still out when the second is stored
            return a
        login = dfs_login.Login(read=read)
        first = threading.Thread(target=login.check)
        first.start()
        until(lambda: login.asked == 1, "the first read")
        login.check()
        go.set()
        first.join()
        self.assertEqual(login.account, dict(loggedIn=True, email="new@example.com"))

    def test_a_code_claude_refuses_shows_its_own_words(self):
        self.waiting()
        self.login.code("made#up")
        st = until(lambda: self.login.state()["phase"] == "failed" and self.login.state(), "the refusal")
        self.assertEqual(st["error"], "Login failed: Request failed with status code 400")

    def test_a_code_needs_a_sign_in_waiting_for_it_and_cancel_ends_one(self):
        with self.assertRaises(ValueError):
            self.login.code("good#code")
        self.waiting()
        with self.assertRaises(ValueError):
            self.login.code("   ")
        proc = self.login.proc
        self.login.cancel()
        self.assertIsNotNone(proc.poll(), "the process is ended")
        self.assertEqual(self.login.state()["phase"], "idle")
        self.assertFalse((self.root / "account").exists())

    def test_the_console_flag_asks_for_api_billing(self):
        self.login.start(console=True)
        until(lambda: self.login.state()["phase"] == "waiting", "the link")
        self.assertIn("auth login --console", self.argv())


if __name__ == "__main__":
    unittest.main()
