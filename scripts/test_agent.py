#!/usr/bin/env python3
"""What `agent.py` says when nix cannot fetch nixpkgs.

A walk started from the page runs `run.sh`, which asks `agent.py --rev` for the pinned
revision; when nix failed, the only trace was the LAST line of its stderr, which for a
GitHub failure is the end of the response body (`404: Not Found)`) and names neither the
url nor the revision. The 404 below is nix 2.24.10's real output for a wrong revision,
captured with `nix flake metadata github:nixos/nixpkgs/<a revision that does not exist>`.

Run:  python3 test_agent.py   (from this directory), or python3 -m unittest test_agent
"""
import contextlib
import importlib.util
import io
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("dfs_agent", HERE / "agent.py")
AGENT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AGENT)
REV = "5156dc3a037c40890ff64f84d435fc2f97c6f68b"

NOT_FOUND = """unpacking 'github:nixos/nixpkgs/%(r)s' into the Git cache...
error:
       … while fetching the input 'github:nixos/nixpkgs/%(r)s'

       error: Failed to open archive (Source threw exception: error: unable to download 'https://github.com/nixos/nixpkgs/archive/%(r)s.tar.gz': HTTP error 404

              response body:

              404: Not Found)
""" % {"r": REV}

RATE_LIMIT = """error: unable to download 'https://api.github.com/repos/NixOS/nixpkgs/commits/HEAD': HTTP error 403

       response body:

       {"message":"API rate limit exceeded for 203.0.113.9.","documentation_url":"https://docs.github.com"}
"""


def run_agent(stderr, arg="--rev"):
    """agent.main() with no pin and a `nix` that fails printing `stderr`: (rc, its stderr).
    The fake is a script run in place of the `nix` command, so `--rev` goes through the
    real streamed nix() call, which is where the cause was lost. (Run with `sh`, since
    a temp directory may be mounted noexec.)"""
    real_popen = subprocess.Popen
    err = io.StringIO()
    with tempfile.TemporaryDirectory() as t:
        script = Path(t, "nix")
        script.write_text("cat >&2 <<'EOF'\n%sEOF\nexit 1\n" % stderr)

        def popen(args, *a, **kw):
            return real_popen(["/bin/sh", str(script), *args[1:]] if args[0] == "nix" else args, *a, **kw)

        with mock.patch.object(subprocess, "Popen", popen), \
                mock.patch.object(AGENT, "pin_path", lambda: Path(t, "agent-nixpkgs.rev")), \
                mock.patch.object(sys, "argv", ["agent.py", arg]), \
                contextlib.redirect_stderr(err):
            rc = AGENT.main()
    return rc, err.getvalue()


class FailedFetch(unittest.TestCase):
    def line(self, stderr, arg="--rev"):
        rc, err = run_agent(stderr, arg)
        self.assertEqual(rc, 1, err)
        return next(ln for ln in err.splitlines() if ln.startswith("dfs_agent:"))

    def test_a_404_names_the_url_with_its_whole_revision(self):
        line = self.line(NOT_FOUND)
        self.assertIn("unable to download", line)
        self.assertIn(REV, line)
        self.assertIn("HTTP error 404", line)

    def test_a_rate_limit_shows_the_error_and_not_only_its_json_body(self):
        line = self.line(RATE_LIMIT)
        self.assertTrue(line.startswith("dfs_agent: --rev failed: error: unable to download"), line)
        self.assertIn("HTTP error 403", line)
        self.assertIn("API rate limit exceeded", line)

    def test_the_update_path_says_the_same(self):
        self.assertIn("HTTP error 403", self.line(RATE_LIMIT, "--update"))

    def test_nix_output_still_reaches_the_watcher(self):
        _, err = run_agent(NOT_FOUND)
        self.assertIn("response body:", err.split("dfs_agent:")[0])

    def test_a_body_ending_in_a_paren_keeps_all_but_nixs_own(self):
        self.assertIn("(x))", self.line("error: unable to download 'u': HTTP error 500\n\n"
                                        "       response body:\n\n       oops (x)))\n"))

    def test_stderr_with_no_error_clause_keeps_its_last_line(self):
        self.assertIn("something odd", self.line("a\nsomething odd\n"))

    def test_a_trailing_warning_does_not_replace_the_fetch_failure(self):
        line = self.line(RATE_LIMIT + "warning: retrying after an error: later\n")
        self.assertIn("HTTP error 403", line)

    def test_a_timeout_does_not_wait_on_a_child_holding_the_pipes(self):
        # nix killed, but something it started still has stderr open: the readers
        # never see EOF, and an unbounded join would block far past the timeout.
        real_popen = subprocess.Popen
        with tempfile.TemporaryDirectory() as t:
            script = Path(t, "nix")
            script.write_text("sleep 4 &\nsleep 4\n")

            def popen(args, *a, **kw):
                return real_popen(["/bin/sh", str(script)], *a, **kw)

            began = time.time()
            with mock.patch.object(subprocess, "Popen", popen), \
                    mock.patch.object(AGENT, "NIX_JOIN", 0.2), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(subprocess.TimeoutExpired):
                    AGENT.nix("x", timeout=0.3, stream=True)
            self.assertLess(time.time() - began, 2)


if __name__ == "__main__":
    unittest.main()
