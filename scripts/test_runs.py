#!/usr/bin/env python3
"""A net under `--left`, the one answer §0.1 step 2 is not allowed to guess at.

⚠️ What is checked is WHOSE session gets reported, which is the whole of what has
gone wrong here twice. Entry 73 answered "left the working tree unchanged" from a
porcelain listing, six seconds after seventeen write calls; `left_behind` was
written to replace that with the session's own transcript, and then answered with
the READER's run — newest, live, and empty because asking is the first thing a
session does — so the reader's own emptiness came back as the previous session's.
Both are the same failure: a confident sentence about work somebody else did.

The fixture is two run directories on one item, the newer being the caller's, and a
transcript for each carrying a write call the other does not. A regression is
therefore visible as the wrong path in the answer, not merely as a missing one.

Run:  python3 roadmap-dfs/scripts/test_runs.py
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

SPEC = importlib.util.spec_from_file_location("dfs_runs", HERE / "runs.py")
RUNS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNS)


def transcript(path, files):
    """A Claude transcript reduced to what the reader actually reads: one assistant
    turn per write, each naming its file."""
    with open(path, "w") as fh:
        for f in files:
            fh.write(json.dumps({
                "type": "assistant",
                "message": {
                    "usage": {"input_tokens": 10, "output_tokens": 1,
                              "cache_read_input_tokens": 100,
                              "cache_creation_input_tokens": 0},
                    "content": [{"type": "tool_use", "name": "Write",
                                 "input": {"file_path": f, "content": "x"}}],
                },
            }) + "\n")


class LeftBehind(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.runs = root / "runs"
        self.runs.mkdir()
        self.tx = root / "tx"
        self.tx.mkdir()
        # The reader's own run is the NEWER of the two, which is what makes the
        # ordering alone unable to tell them apart.
        self.mk("older", "sid-previous", ["a.md"])
        self.mk("newer", "sid-mine", [])
        RUNS.run_roots = lambda: [str(self.runs)]
        RUNS._dirty_paths = lambda: []
        sc = RUNS._dfs_context()
        sc.transcript_path = lambda sid, agent, root=None: str(self.tx / (sid + ".jsonl"))
        RUNS._dfs_context = lambda: sc
        self.env = {k: os.environ.pop(k, None) for k in
                    ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CODEX_THREAD_ID",
                     "KIRO_SESSION_ID")}

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def mk(self, name, sid, files):
        d = self.runs / ("dfs_run_" + name)
        d.mkdir()
        (d / "meta.json").write_text(json.dumps(
            {"item": "W99", "agent": "claude", "sid": sid, "pid": 0,
             "finished": "2026-01-01T00:00:00+00:00"}))
        transcript(self.tx / (sid + ".jsonl"), files)
        # mtime is what run_dirs sorts on, so it is set rather than inherited.
        os.utime(d / "meta.json", (1, 1 if name == "older" else 2))

    def test_reports_the_previous_session_not_the_reader(self):
        os.environ["CLAUDE_CODE_SESSION_ID"] = "sid-mine"
        info = RUNS.left_behind("W99")
        self.assertEqual(info["run"]["sid"], "sid-previous")
        self.assertEqual(info["calls"], 1)

    def test_with_no_session_id_the_newest_still_answers(self):
        """A person at a shell is not any of these runs, so nothing is excluded."""
        info = RUNS.left_behind("W99")
        self.assertEqual(info["run"]["sid"], "sid-mine")

    def test_a_peer_chain_on_the_same_item_is_an_answer(self):
        """The exclusion is by identity, never by liveness: another chain working
        this item is exactly what a session wants to be told about."""
        os.environ["CLAUDE_CODE_SESSION_ID"] = "sid-unrelated"
        info = RUNS.left_behind("W99")
        self.assertEqual(info["run"]["sid"], "sid-mine")


class CodexWrites(unittest.TestCase):
    """⚠️ A CODEX SESSION'S WRITES, which this reader could not see at all until
    2026-09-21 and therefore answered "nothing written" about every one of them —
    entry 685, written about a 161-response session that had rewritten four files.
    The reader asked for `payload["command"]`, a key no rollout in this tree carries;
    what a rollout carries is `item_completed` items. Both halves are checked here,
    because either one alone still reports nothing: the ITEM shapes, and the
    absolute in-repo path that `git status --porcelain` names relatively."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sc = RUNS._dfs_context()

    def tearDown(self):
        self.tmp.cleanup()

    def rollout(self, records):
        tp = self.root / "rollout.jsonl"
        with open(tp, "w") as fh:
            for rec in records:
                fh.write(json.dumps(rec) + "\n")
        return str(tp)

    @staticmethod
    def item(item):
        return {"type": "event_msg",
                "payload": {"type": "item_completed", "item": item}}

    def test_a_patch_is_a_write_in_both_recorded_shapes(self):
        """The rollout keys `changes` by path; the `--json` stream lists
        `{path, kind}`. A reader that knows one shape reports zero for the other."""
        tp = self.rollout([
            self.item({"type": "FileChange",
                       "changes": {"/repo/docs/a.md": {"type": "update"}}}),
            self.item({"type": "FileChange",
                       "changes": [{"path": "/repo/docs/b.md", "kind": "update"}]}),
        ])
        paths, calls = self.sc.files_written(tp, "codex")
        self.assertEqual(calls, 2)
        self.assertEqual(paths, ["/repo/docs/a.md", "/repo/docs/b.md"])

    def test_a_shell_write_is_read_from_the_argv_list_and_a_read_is_not(self):
        tp = self.rollout([
            self.item({"type": "CommandExecution",
                       "command": ["/bin/bash", "-lc", "sed -i s/a/b/ docs/c.md"]}),
            self.item({"type": "CommandExecution",
                       "command": ["/bin/bash", "-lc", "sed -n '1,40p' docs/c.md"]}),
        ])
        paths, calls = self.sc.files_written(tp, "codex")
        self.assertEqual((calls, paths), (1, ["docs/c.md"]))

    def test_an_absolute_in_repo_path_answers_in_the_root_s_own_terms(self):
        """`still_changed` joins against porcelain, which names paths from the root,
        so an absolute one has to be brought there rather than filed as scratch."""
        runs = self.root / "runs"
        runs.mkdir()
        d = runs / "dfs_run_codex"
        d.mkdir()
        (d / "meta.json").write_text(json.dumps(
            {"item": "W99", "agent": "codex", "sid": "sid-codex", "pid": 0,
             "finished": "2026-01-01T00:00:00+00:00"}))
        tp = self.rollout([
            self.item({"type": "FileChange",
                       "changes": {str(self.root / "docs/a.md"): {"type": "update"},
                                   "/elsewhere/b.md": {"type": "update"}}}),
        ])
        sc = self.sc
        sc.transcript_path = lambda sid, agent, root=None: tp
        old_root, old_repo, old_dirty = RUNS.run_roots, RUNS.REPO, RUNS._dirty_paths
        RUNS.run_roots = lambda: [str(runs)]
        RUNS.REPO = self.root
        RUNS._dirty_paths = lambda: ["docs/a.md"]
        RUNS._dfs_context = lambda: sc
        try:
            info = RUNS.left_behind("W99")
        finally:
            RUNS.run_roots, RUNS.REPO, RUNS._dirty_paths = old_root, old_repo, old_dirty
        self.assertEqual(info["calls"], 1)
        self.assertEqual(info["paths"], ["docs/a.md"])
        self.assertEqual(info["still_changed"], ["docs/a.md"])
        self.assertEqual(info["scratch"], ["/elsewhere/b.md"])


class CloseLost(unittest.TestCase):
    """A chain killed together with the screen that watched it has nobody to write its died
    line (W118 on 2026-10-23): the next screen to start closes its record and says so."""

    def setUp(self):
        import shutil
        import subprocess
        import paths as dfs_paths
        import runs as dfs_runs
        import tui as TUI
        self.shutil, self.paths, self.runs, self.TUI = shutil, dfs_paths, dfs_runs, TUI
        self.root = Path(tempfile.mkdtemp())
        (self.root / ".dfs" / "items").mkdir(parents=True)
        (self.root / ".dfs" / "ROADMAP.md").write_text("# Roadmap\n")
        (self.root / ".dfs" / "items" / "W2.md").write_text(
            "# W2 · two\n\n## Goal\n\ntwo\n\n## Tree\n\n## Log\n")
        for cmd in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t",
                                                   "commit", "-qm", "init"]):
            subprocess.run(["git"] + cmd, cwd=self.root, check=True)
        self.saved = (dfs_paths.work_root, TUI.ROOT, TUI.STATE, os.environ.get("DFS_RUN_DIR"))
        dfs_paths.work_root = lambda: self.root
        TUI.ROOT, TUI.STATE = self.root, self.root / ".dfs"
        self.rundir = self.root / "runs"
        self.rundir.mkdir()
        os.environ["DFS_RUN_DIR"] = str(self.rundir)
        gone = subprocess.Popen(["true"])
        gone.wait()
        self.live = subprocess.Popen(["sh", "-c", "sleep 60; :", "run.sh"])   # `; :` keeps sh from exec-ing sleep
        self.metas = dict(
            dead=dict(item="W2", pid=str(gone.pid), run="5", started="2026-10-23T11:50:00+00:00"),
            live=dict(item="W2", pid=str(self.live.pid), run="1"),
            done=dict(item="W2", pid=str(gone.pid), run="2", finished="2026-10-22T10:00:00+00:00",
                      rc="0", stop="cap"))
        for name, meta in self.metas.items():
            (self.rundir / ("dfs_run_" + name)).mkdir()
            (self.rundir / ("dfs_run_" + name) / "meta.json").write_text(json.dumps(meta))

    def tearDown(self):
        self.live.kill()
        self.live.wait()
        wr, root, state, run_dir = self.saved
        self.paths.work_root, self.TUI.ROOT, self.TUI.STATE = wr, root, state
        if run_dir is None:
            os.environ.pop("DFS_RUN_DIR", None)
        else:
            os.environ["DFS_RUN_DIR"] = run_dir
        self.shutil.rmtree(self.root, ignore_errors=True)

    def meta(self, name):
        return json.loads((self.rundir / ("dfs_run_" + name) / "meta.json").read_text())

    def test_the_screen_closes_a_dead_chain_s_open_record_once_and_says_so(self):
        ui = self.TUI.UI(None)
        dead = self.meta("dead")
        self.assertEqual(dead["stop"], "lost")
        self.assertTrue(dead["finished"])
        self.assertIn("its supervisor was lost: pid %s was gone" % self.metas["dead"]["pid"],
                      dead["cause"])
        self.assertEqual(self.meta("live"), self.metas["live"])
        self.assertEqual(self.meta("done"), self.metas["done"])
        died = [text for _, kind, text in ui.events if kind == "died"]
        self.assertEqual(died, ["W2's chain died with its supervisor (dfs_run_dead, run 5): its "
                                "pid %s was gone and it never finished" % self.metas["dead"]["pid"]])
        self.assertEqual({os.path.basename(r["dir"]): r["state"] for r in ui.chains},
                         {"dfs_run_dead": "stopped", "dfs_run_live": "live",
                          "dfs_run_done": "finished"})
        self.TUI.UI(None)
        log = (self.rundir / "activity.log").read_text()
        self.assertEqual(log.count("\tdied\t"), 1, log)
        self.assertEqual(self.meta("dead"), dead)

    def test_the_standalone_page_closes_a_dead_chain_s_record_as_its_walk_starts(self):
        import walk as dfs_walk
        import web as WEB
        saved = WEB.WALK["walker"]
        WEB.WALK["walker"] = walker = dfs_walk.HeadlessWalk()
        try:
            WEB._the_walker().close_lost_runs()
        finally:
            WEB.WALK["walker"] = saved
        self.assertEqual(self.meta("dead")["stop"], "lost")
        self.assertEqual(self.meta("live"), self.metas["live"])
        died = [text for _, kind, text in walker.events if kind == "died"]
        self.assertEqual(len(died), 1, died)
        self.assertIn("W2's chain died with its supervisor (dfs_run_dead, run 5)", died[0])
        self.assertEqual((self.rundir / "activity.log").read_text().count("\tdied\t"), 1)

    def test_a_closed_record_is_stamped_as_run_sh_stamps_one(self):
        self.runs.close_lost()
        self.assertRegex(self.meta("dead")["finished"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")

    def test_a_record_from_another_pid_namespace_is_left_open(self):
        """Its pid means nothing in this namespace's /proc, and with no lock file to ask the chain may be running."""
        here = self.runs.pid_namespace()
        for name, ns in (("dead", "pid:[4026539999]"), ("live", "pid:[4026539999]")):
            self.metas[name]["pidns"] = ns
            (self.rundir / ("dfs_run_" + name) / "meta.json").write_text(json.dumps(self.metas[name]))
        self.assertEqual(self.runs.close_lost(), [])
        self.assertEqual(self.meta("dead"), self.metas["dead"])
        self.metas["dead"]["pidns"] = here
        (self.rundir / "dfs_run_dead" / "meta.json").write_text(json.dumps(self.metas["dead"]))
        self.assertEqual([os.path.basename(r["dir"]) for r in self.runs.close_lost()], ["dfs_run_dead"])
        self.assertEqual(self.meta("dead")["stop"], "lost")

    def test_a_chain_of_a_namespace_that_is_gone_is_closed_by_its_free_lock(self):
        """A restarted container is a new pid namespace, and the screen that comes back in it
        cannot read the old chains by pid: what it can read is that nothing holds their lock."""
        import fcntl
        for name in ("dead", "live"):
            self.metas[name]["pidns"] = "pid:[4026539999]"
            (self.rundir / ("dfs_run_" + name) / "meta.json").write_text(json.dumps(self.metas[name]))
            (self.rundir / ("dfs_run_" + name) / "lock").write_text("")
        with open(self.rundir / "dfs_run_live" / "lock", "rb") as held:
            fcntl.flock(held, fcntl.LOCK_EX)         # the chain that is still running there
            closed = self.runs.close_lost()
        self.assertEqual([os.path.basename(r["dir"]) for r in closed], ["dfs_run_dead"])
        self.assertEqual(self.meta("dead")["stop"], "lost")
        self.assertEqual(self.meta("live"), self.metas["live"])

    def test_the_walk_reads_a_lost_chain_as_a_stop_with_its_reason(self):
        import walk as dfs_walk
        self.assertEqual(dfs_walk.walk_decide("lost", True, "W2"),
                         ("stop", "the chain died with its supervisor and never finished"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
