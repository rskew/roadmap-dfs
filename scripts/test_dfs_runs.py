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

Run:  python3 roadmap-dfs/scripts/test_dfs_runs.py
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

SPEC = importlib.util.spec_from_file_location("dfs_runs", HERE / "dfs_runs.py")
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
