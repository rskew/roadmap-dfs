#!/usr/bin/env python3
"""GitHub mode: a finished task held for its PR, opened and watched (README, "GitHub mode").

Run:  python3 <scripts>/test_github.py
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths as dfs_paths  # noqa: E402
import tree as dfs_tree    # noqa: E402
import github as dfs_github  # noqa: E402


class Switch(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self._wr = dfs_paths.work_root
        dfs_paths.work_root = lambda: self.root

    def tearDown(self):
        dfs_paths.work_root = self._wr
        shutil.rmtree(self.root, ignore_errors=True)

    def test_off_until_written_and_off_again_when_removed(self):
        self.assertEqual(dfs_paths.read_github(), "")
        self.assertEqual(dfs_paths.write_github("upstream"), "upstream")
        self.assertEqual(dfs_paths.read_github(), "upstream")
        self.assertEqual(dfs_paths.write_github(None), "")
        self.assertFalse(dfs_paths.github_file().exists())
        self.assertEqual(dfs_paths.read_github(), "")

    def test_an_empty_remote_is_origin(self):
        self.assertEqual(dfs_paths.write_github(""), "origin")
        dfs_paths.github_file().write_text("\n")
        self.assertEqual(dfs_paths.read_github(), "origin")

    def test_a_remote_that_is_not_a_name_is_refused(self):
        for bad in ("-x", "a b", "../x", "--upload-pack=sh"):
            with self.assertRaises(ValueError):
                dfs_paths.write_github(bad)
        self.assertFalse(dfs_paths.github_file().exists())

    def test_the_switch_is_part_of_the_roadmap(self):
        self.assertIn(dfs_paths.github_file(), dfs_paths.roadmap_parts())


TASK = """# W1 · the only thing

## Goal

The only thing.

## Tree

### W1.1 · the step
Status: confirmed
Approach: do it.
Determination: done.

## Log

- 2026-10-27T01:00:00Z · pr · https://github.com/o/r/pull/7
"""

VIEW = {"comments": [{"id": "IC_1", "author": {"login": "ana"}, "body": "Please rename  x.",
                      "url": "https://github.com/o/r/pull/7#issuecomment-1"}],
        "reviews": [{"id": "PRR_2", "author": {"login": "bo"}, "body": "", "state": "APPROVED"},
                    {"id": "PRR_3", "author": {"login": "bo"}, "body": "Needs a test.",
                     "state": "CHANGES_REQUESTED"}]}
LINES = [{"id": 44, "user": {"login": "cy"}, "body": "Off by one here.", "path": "app.py",
          "line": 12, "html_url": "https://github.com/o/r/pull/7#discussion_r44"}]


class Watch(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / ".dfs" / "items").mkdir(parents=True)
        (self.root / ".dfs" / "items" / "W1.md").write_text(TASK)
        self._wr = dfs_paths.work_root
        dfs_paths.work_root = lambda: self.root
        dfs_paths.write_github("")
        self.calls = self.root / "calls"
        (self.root / "view.json").write_text(json.dumps(VIEW))
        (self.root / "lines.json").write_text(json.dumps(LINES))
        fake = self.root / "gh"
        fake.write_text("#!/bin/sh\necho \"$*\" >> %s\ncase \"$1\" in pr) cat %s;; api) cat %s;; esac\n"
                        % (self.calls, self.root / "view.json", self.root / "lines.json"))
        fake.chmod(0o755)
        self._gh = os.environ.get("DFS_GH")
        os.environ["DFS_GH"] = str(fake)

    def tearDown(self):
        dfs_paths.work_root = self._wr
        if self._gh is None:
            os.environ.pop("DFS_GH", None)
        else:
            os.environ["DFS_GH"] = self._gh
        shutil.rmtree(self.root, ignore_errors=True)

    def test_each_comment_with_a_body_becomes_one_open_node_once(self):
        self.assertEqual(dfs_github.watched(), ["W1"])
        self.assertEqual(dfs_github.watch("W1"), ["W1.2", "W1.3", "W1.4"])
        t = dfs_tree.load("W1")
        nodes = {nd["id"]: nd for nd in t["nodes"]}
        self.assertEqual([nodes[i]["status"] for i in ("W1.2", "W1.3", "W1.4")], ["open"] * 3)
        self.assertIn("@ana", nodes["W1.2"]["title"])
        self.assertIn("Please rename x.", nodes["W1.2"]["fields"]["Approach"])
        self.assertIn("PR review from @bo", nodes["W1.3"]["title"])
        self.assertIn("at app.py:12", nodes["W1.4"]["fields"]["Approach"])
        self.assertIn("(github line 44)", nodes["W1.4"]["fields"]["Approach"])
        self.assertEqual(dfs_tree.status_of(t)[0], "open")
        self.assertIn("pr view https://github.com/o/r/pull/7", self.calls.read_text())
        self.assertIn("api repos/o/r/pulls/7/comments", self.calls.read_text())
        # the same comments again, and one new: only the new one is added
        LINES.append({"id": 45, "user": {"login": "cy"}, "body": "And here.", "path": "app.py",
                      "line": 20})
        (self.root / "lines.json").write_text(json.dumps(LINES))
        try:
            self.assertEqual(dfs_github.watch("W1"), ["W1.5"])
        finally:
            LINES.pop()
        self.assertEqual(dfs_github.watch("W1"), [])

    def test_a_comment_whose_node_the_author_deleted_is_not_added_again(self):
        self.assertEqual(dfs_github.watch("W1"), ["W1.2", "W1.3", "W1.4"])
        self.assertEqual(dfs_tree.delete_node("W1", "W1.2"), ["W1.2"])
        self.assertEqual(dfs_github.watch("W1"), [])
        self.assertNotIn("W1.2", {nd["id"] for nd in dfs_tree.load("W1")["nodes"]})
        # a comment that is new still becomes a node
        LINES.append({"id": 45, "user": {"login": "cy"}, "body": "And here.", "path": "app.py",
                      "line": 20})
        (self.root / "lines.json").write_text(json.dumps(LINES))
        try:
            self.assertEqual(len(dfs_github.watch("W1")), 1)
        finally:
            LINES.pop()
        self.assertEqual(dfs_github.watch("W1"), [])

    def test_line_comments_on_every_page_become_nodes(self):
        # `gh api --paginate` prints one array per page, back to back
        page2 = [{"id": 90 + i, "user": {"login": "cy"}, "body": "More %d." % i, "path": "app.py",
                  "line": i + 1} for i in range(2)]
        (self.root / "lines.json").write_text(json.dumps(LINES) + json.dumps(page2) + "\n")
        added = dfs_github.watch("W1")
        self.assertEqual(len(added), 5)
        self.assertIn("--paginate", self.calls.read_text())
        approaches = " ".join(nd["fields"].get("Approach", "") for nd in dfs_tree.load("W1")["nodes"])
        self.assertIn("(github line 91)", approaches)
        self.assertEqual(dfs_github.watch("W1"), [])
        self.assertEqual(dfs_github._paged('[{"a": 1}]\n[[{"a": 2}], {"a": 3}]{"a": 4}'),
                         [{"a": 1}, {"a": 2}, {"a": 3}, {"a": 4}])
        self.assertEqual(dfs_github._paged(""), [])

    def test_the_open_pr_mark_applies_only_to_a_held_task_with_no_pull_request(self):
        t = dfs_tree.load("W1")
        self.assertEqual(dfs_tree.pr_url(t), "https://github.com/o/r/pull/7")
        self.assertFalse(dfs_tree.pr_ready(t))
        (self.root / ".dfs" / "items" / "W1.md").write_text(TASK.split("- 2026")[0])
        t = dfs_tree.load("W1")
        real = dfs_paths.worktree
        dfs_paths.worktree = lambda task: self.root
        try:
            self.assertTrue(dfs_tree.pr_ready(t))
            dfs_paths.write_github(None)
            self.assertFalse(dfs_tree.pr_ready(t))
        finally:
            dfs_paths.worktree = real
        self.assertFalse(dfs_tree.pr_ready(t))      # no worktree held: nothing to push
        merged = TASK + "- 2026-10-27T02:00:00Z · pr · https://github.com/o/r/pull/7 · merged\n"
        (self.root / ".dfs" / "items" / "W1.md").write_text(merged)
        self.assertTrue(dfs_tree.pr_merged(dfs_tree.load("W1")))

    def test_a_pull_request_closed_unmerged_is_not_called_open(self):
        closed = TASK.split("- 2026")[0] + "- 2026-10-27T02:00:00Z · pr · https://github.com/o/r/pull/7 · closed\n"
        (self.root / ".dfs" / "items" / "W1.md").write_text(closed)
        real = dfs_paths.worktree
        dfs_paths.worktree = lambda task: self.root
        try:
            why = dfs_tree.status_of(dfs_tree.load("W1"))[1]
        finally:
            dfs_paths.worktree = real
        self.assertIn("closed unmerged", why)
        self.assertNotIn("is open", why)

    def test_nothing_is_watched_with_github_mode_off_or_no_pull_request(self):
        dfs_paths.write_github(None)
        self.assertEqual(dfs_github.watched(), [])
        dfs_paths.write_github("")
        (self.root / ".dfs" / "items" / "W1.md").write_text(TASK.split("- 2026")[0])
        self.assertEqual(dfs_github.watched(), [])
        self.assertEqual(dfs_github.watch("W1"), [])
        self.assertFalse(self.calls.exists())


class NetworkCalls(unittest.TestCase):
    def env_of(self, **env):
        """The environment `_git_net` hands git, read back through a git alias running `env`."""
        import branch as dfs_branch
        keep = {k: os.environ.pop(k, None) for k in ("GIT_SSH_COMMAND", "GIT_TERMINAL_PROMPT")}
        os.environ.update(env)
        try:
            r = dfs_branch._git_net("-c", "alias.show-env=!env",
                                    "show-env", cwd=tempfile.gettempdir())
        finally:
            for k in keep:
                os.environ.pop(k, None)
            os.environ.update({k: v for k, v in keep.items() if v is not None})
        return dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)

    def test_a_call_to_the_remote_never_waits_on_a_prompt(self):
        got = self.env_of()
        self.assertEqual(got["GIT_TERMINAL_PROMPT"], "0")
        self.assertIn("-o BatchMode=yes", got["GIT_SSH_COMMAND"])

    def test_the_authors_ssh_command_is_kept(self):
        got = self.env_of(GIT_SSH_COMMAND="ssh -F mine")
        self.assertEqual(got["GIT_SSH_COMMAND"], "ssh -F mine -o BatchMode=yes")


if __name__ == "__main__":
    unittest.main()
