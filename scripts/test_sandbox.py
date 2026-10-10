#!/usr/bin/env python3
"""sandbox.sh daemon mode: what it hands the container engine, with a stub for an engine.

Run:  python3 <scripts>/test_sandbox.py
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Sources the script, stubs the lookups that need a real host (nix, its daemon, certificates)
# and the engine, and calls main. The stub engine is a function, since a temp directory may
# not allow executables; it logs one line per call, its arguments joined by spaces, and fails
# a call that matches $ENGINE_FAIL, and makes the sidecar's socket.
DRIVER = r'''
source "$SANDBOX"
docker() {
  echo "${*//$'\n'/ }" >> "$ENGINE_LOG"
  [[ -z "${ENGINE_FAIL:-}" || "$*" != *"$ENGINE_FAIL"* ]] || return 1
  # The sidecar's nix daemon would make its socket in the directory mounted as /proxy.
  if [[ -z "${ENGINE_NO_SOCKET:-}" && "$*" =~ -v\ ([^ ]+):/proxy ]]; then
    python3 -c 'import socket,sys; socket.socket(socket.AF_UNIX).bind(sys.argv[1])' "${BASH_REMATCH[1]}/socket"
  fi
}
sleep() { :; }
require_prereqs() { mkdir -p "${CONTAINER_STATE_DIR}/nix-cache"; }
host_nix_bin_dir() { echo /host/nix/bin; }
host_ca_bundle() { echo /etc/ca; }
host_locale_archive() { echo /etc/locale; }
main "$@"
'''


class Daemon(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "app"
        self.repo.mkdir()
        self.log = self.tmp / "engine.log"
        self.log.touch()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sandbox(self, *args, config="", fail="", extra=None):
        (self.repo / ".container.env").write_text(config)
        self.log.write_text("")
        # The caller's own CONTAINER_* settings would win over the repo's file.
        env = {k: v for k, v in os.environ.items() if not k.startswith("CONTAINER_")}
        env.update(SANDBOX=str(HERE / "sandbox.sh"), ENGINE_LOG=str(self.log),
                   CONTAINER_REPO=str(self.repo), CONTAINER_ENGINE="docker", ENGINE_FAIL=fail, **(extra or {}))
        p = subprocess.run(["bash", "-c", DRIVER, "sandbox", *args], env=env, text=True,
                           capture_output=True, stdin=subprocess.DEVNULL)
        return p, self.log.read_text().splitlines()

    def test_the_script_parses(self):
        subprocess.run(["bash", "-n", str(HERE / "sandbox.sh")], check=True)

    def test_daemon_runs_detached_with_a_restart_policy_and_no_rm(self):
        p, calls = self.sandbox("daemon", config="CONTAINER_APP_CMD=./serve --port 80\nCONTAINER_PORTS=8080\n")
        self.assertEqual(p.returncode, 0, p.stderr)
        runs = [c for c in calls if c.startswith("run ")]
        self.assertEqual(len(runs), 2, calls)  # the sidecar, then the container
        for run in runs:
            self.assertIn("-d", run.split())
            self.assertIn("--restart unless-stopped", run)
            self.assertNotIn("--rm", run.split())
        main = runs[1]
        self.assertNotIn("-it", main.split())
        self.assertIn("-p 8080:8080", main)
        self.assertTrue(main.endswith("-- bash -lc exec ./serve --port 80"), main)

    def test_the_sidecar_survives_the_script_and_a_reboot(self):
        p, calls = self.sandbox("daemon", config="CONTAINER_APP_CMD=true\n")
        sidecar, main = [c for c in calls if c.startswith("run ")]
        # A directory that is not wiped on reboot, mounted whole so a restarted sidecar's new
        # socket is the one the container sees.
        state = self.repo / ".container-state" / "nix-proxy"
        self.assertTrue(state.is_dir())
        self.assertIn(f"-v {state}:/proxy", sidecar)
        self.assertIn(f"-v {state}:/nix/var/nix/daemon-socket ", main + " ")
        # No trap removing the sidecar when the script exits.
        self.assertFalse([c for c in calls if c.startswith("rm") and c.endswith("-nix") and calls.index(c) > calls.index(main)], calls)

    def test_daemon_replaces_an_earlier_one(self):
        p, calls = self.sandbox("daemon", config="CONTAINER_APP_CMD=true\n")
        self.assertIn("rm -f app-dev", calls)
        self.assertIn("rm -f app-dev-nix", calls)
        self.assertLess(calls.index("rm -f app-dev"), [i for i, c in enumerate(calls) if c.startswith("run ") and "--name app-dev " in c][0])

    def test_daemon_takes_a_command_over_the_app_command(self):
        p, calls = self.sandbox("daemon", "dfs-web", "--port", "9", config="CONTAINER_APP_CMD=true\n")
        self.assertTrue(calls[-1].endswith("-- dfs-web --port 9"), calls[-1])

    def test_daemon_without_a_command_or_app_command_refuses(self):
        p, calls = self.sandbox("daemon")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("needs a command", p.stderr)
        self.assertEqual([c for c in calls if c.startswith("run ")], [])

    def test_a_failed_start_removes_what_it_started(self):
        p, calls = self.sandbox("daemon", config="CONTAINER_APP_CMD=true\n", fail="--name app-dev ")
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(calls[-1], "rm -f app-dev app-dev-nix")

    def test_a_sidecar_that_never_makes_its_socket_is_removed(self):
        p, calls = self.sandbox("daemon", config="CONTAINER_APP_CMD=true\n", extra={"ENGINE_NO_SOCKET": "1"})
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("did not come up", p.stderr)
        self.assertEqual(calls[-1], "rm -f app-dev app-dev-nix")

    def test_a_bad_ollama_socket_removes_the_sidecar_already_started(self):
        p, calls = self.sandbox("daemon", config="CONTAINER_APP_CMD=true\n",
                                extra={"OLLAMA_SOCKET": str(self.tmp / "no-such.sock")})
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("is not a unix socket", p.stderr)
        self.assertEqual(len([c for c in calls if c.startswith("run ")]), 1, calls)  # only the sidecar
        self.assertEqual(calls[-1], "rm -f app-dev app-dev-nix")

    def test_stop_removes_both_containers(self):
        p, calls = self.sandbox("stop")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(calls, ["rm -f app-dev app-dev-nix"])

    def test_the_ordinary_modes_stay_throwaway(self):
        p, calls = self.sandbox("run", "true")
        runs = [c for c in calls if c.startswith("run ")]
        self.assertEqual(len(runs), 2, calls)
        for run in runs:
            self.assertIn("--rm", run.split())
            self.assertNotIn("--restart", run)


if __name__ == "__main__":
    unittest.main()
