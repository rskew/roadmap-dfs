#!/usr/bin/env python3
"""The critic's `ok` has to be one the record supports: it names live nodes, and the
session that gave it read or ran something besides logging it.

Run:  python3 roadmap-dfs/scripts/test_critic.py
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import context as dfs_context   # noqa: E402
import paths as dfs_paths       # noqa: E402
import state as dfs_state       # noqa: E402

TASK = """# W1 · sample

## Goal

Make it work.

## Tree

### W1.1 · the step
Status: confirmed
Determination: done.

### W1.2 · refuted idea
Status: refuted
Determination: no.

### W1.3 · under the refuted one
Parent: W1.2
Status: open

## Log

- 2026-09-25T09:00:00Z · session · critic · run-1
%s"""


def line(blocks):
    return json.dumps({"type": "assistant", "message": {"content": blocks}}) + "\n"


def use(i, name, **inp):
    return {"type": "tool_use", "id": "t%d" % i, "name": name, "input": inp}


class CriticOk(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / ".dfs" / "items").mkdir(parents=True)
        (self.root / ".dfs" / "ROADMAP.md").write_text("# Roadmap\n")
        self._wr = dfs_paths.work_root
        dfs_paths.work_root = lambda: self.root
        self.addCleanup(lambda: (setattr(dfs_paths, "work_root", self._wr),
                                 shutil.rmtree(self.root, ignore_errors=True)))

    def log(self, verdict, body=""):
        entry = "- 2026-09-25T09:05:00Z · critic · %s\n%s" % (
            verdict, "".join("  %s\n" % l for l in body.splitlines()))
        (self.root / ".dfs" / "items" / "W1.md").write_text(TASK % entry)

    def test_an_ok_that_names_no_node_is_not_believed(self):
        self.log("ok")
        self.assertIn("names no node", dfs_state.critic_problem("W1"))
        self.log("ok", "looks fine to me")
        self.assertIn("names no node", dfs_state.critic_problem("W1"))

    def test_naming_a_pruned_or_unknown_node_does_not_count(self):
        self.log("ok", "W1.3: sits under a refuted node\nW1.9: not in the tree")
        self.assertIn("names no node", dfs_state.critic_problem("W1"))

    def test_an_ok_naming_a_live_node_is_believed_and_issues_is_not_judged(self):
        self.log("ok", "W1.1: re-ran its check; it passes")
        self.assertEqual(dfs_state.critic_problem("W1"), "")
        self.log("issues", "")
        self.assertEqual(dfs_state.critic_problem("W1"), "")

    def test_an_ok_from_a_session_that_looked_at_nothing_is_not_believed(self):
        self.log("ok", "W1.1: re-ran its check; it passes")
        t = self.root / "t.jsonl"
        verdict = use(1, "Bash", command="python3 scripts/tree.py log W1 critic ok <<EOF\nW1.1: ok\nEOF")
        t.write_text(line([verdict]))
        self.assertEqual(dfs_context.tool_uses(str(t), "claude"), 0)
        self.assertIn("read nothing", dfs_state.critic_problem("W1", str(t), "claude"))
        t.write_text(line([use(2, "Read", file_path="src/a.py")]) + line([verdict]))
        self.assertEqual(dfs_context.tool_uses(str(t), "claude"), 1)
        self.assertEqual(dfs_state.critic_problem("W1", str(t), "claude"), "")

    def test_the_harness_calls_and_bookkeeping_tools_are_not_a_look(self):
        # Found by running a real critic as a subagent: its transcript ends with a
        # `SubagentHandback` call the harness adds, which counted as a check, so a critic
        # that did nothing but log an ok (naming nodes it never opened) passed.
        self.log("ok", "W1.1: re-ran its check; it passes")
        t = self.root / "t.jsonl"
        verdict = use(1, "Bash", command="python3 scripts/tree.py log W1 critic ok <<EOF\nW1.1: ok\nEOF")
        t.write_text(line([verdict]) + line([use(2, "SubagentHandback", summary="done")])
                     + line([use(3, "TodoWrite", todos=[])]) + line([use(4, "Edit", file_path="a")]))
        self.assertEqual(dfs_context.tool_uses(str(t), "claude"), 0)
        self.assertIn("read nothing", dfs_state.critic_problem("W1", str(t), "claude"))
        t.write_text(line([use(5, "mcp__github__get_file_contents", path="a")]) + line([verdict]))
        self.assertEqual(dfs_context.tool_uses(str(t), "claude"), 1)

    def test_a_provider_whose_transcript_has_not_been_checked_is_judged_on_its_body_alone(self):
        self.log("ok", "W1.1: re-ran its check; it passes")
        t = self.root / "t.jsonl"
        t.write_text("{}\n")
        for provider in ("codex", "kiro", "opencode"):
            self.assertEqual(dfs_state.critic_problem("W1", str(t), provider), "", provider)
        self.assertIn("read nothing", dfs_state.critic_problem("W1", str(t), "claude"))

    def test_a_transcript_that_cannot_be_read_is_unknown_not_zero(self):
        self.log("ok", "W1.1: re-ran its check; it passes")
        gone = str(self.root / "missing.jsonl")
        self.assertEqual(dfs_context.tool_uses(gone, "claude"), -1)
        self.assertEqual(dfs_state.critic_problem("W1", gone, "claude"), "")

    def test_one_call_is_counted_once_however_many_lines_carry_it(self):
        t = self.root / "t.jsonl"
        call = use(1, "Grep", pattern="x")
        t.write_text(line([call]) + line([call]) + line([{"type": "text", "text": "hi"}]))
        self.assertEqual(dfs_context.tool_uses(str(t), "claude"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
