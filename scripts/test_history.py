#!/usr/bin/env python3
"""history.py's reading of one transcript: what the session was for, did and spent."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import history  # noqa: E402


def user(content, ts):
    return {"type": "user", "timestamp": ts, "message": {"content": content}}


def assistant(mid, blocks, ts, read=50_000, write=1_000, out=200):
    return {"type": "assistant", "timestamp": ts, "message": {
        "id": mid, "model": "claude-opus-5-5", "content": blocks,
        "usage": {"input_tokens": 2, "cache_read_input_tokens": read,
                  "cache_creation_input_tokens": write, "output_tokens": out}}}


def bash(tid, cmd):
    return {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": cmd}}


def result(tid, text, error=False):
    return {"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": error}


class SessionTest(unittest.TestCase):
    def record(self, records):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "-workspace-shop-app--dfs-worktrees-W13"
            d.mkdir()
            f = d / "abc.jsonl"
            f.write_text("".join(json.dumps(r) + "\n" for r in records))
            return history.session(str(f))

    def test_a_review_with_a_refused_then_accepted_commit(self):
        rec = self.record([
            user("You are the implementation reviewer for task W13. Everything below", "2026-10-01T00:00:00Z"),
            # One response split over two records with the same id is counted once.
            assistant("m1", [bash("t1", "git commit -m 'W13.4: fix the drag'")], "2026-10-01T00:01:00Z"),
            assistant("m1", [], "2026-10-01T00:01:00Z"),
            user([result("t1", "dfs_check: the roadmap broke its own rules.\n- too big", True)], "2026-10-01T00:02:00Z"),
            assistant("m2", [bash("t2", "git commit -q -F - <<'EOF'\nW13.4: fix the drag\n\nbody\nEOF")],
                      "2026-10-01T00:03:00Z", read=60_000),
            user([result("t2", "[dfs/W13 abc] W13.4")], "2026-10-01T00:04:00Z"),
            assistant("m3", [bash("t3", "python3 $S/tree.py log W13 review issues <<'EOF'\nx\nEOF")],
                      "2026-10-01T00:05:00Z"),
        ])
        self.assertEqual((rec["project"], rec["kind"], rec["task"]), ("-workspace-shop-app", "review", "W13"))
        self.assertEqual(rec["responses"], 3)
        self.assertEqual(rec["peak"], 61_000)
        self.assertEqual(rec["refused"], 1)
        self.assertEqual([c["subject"] for c in rec["commits"]], ["W13.4: fix the drag"] * 2)
        self.assertTrue(rec["commits"][-1]["ok"])
        self.assertEqual(rec["verdicts"], [("review", "issues")])
        self.assertAlmostEqual(rec["units"], 3 * 2 + 160_000 * 0.1 + 3_000 * 2 + 600 * 5)

    def test_a_work_session_and_its_started_node(self):
        rec = self.record([
            user("Follow /nix/store/x-roadmap-dfs/share/roadmap-dfs/SKILL.md. Work task W13. Everything", "2026-10-01T00:00:00Z"),
            assistant("m1", [bash("t1", "python3 $S/tree.py start W13 W13.5")], "2026-10-01T00:01:00Z"),
        ])
        self.assertEqual((rec["kind"], rec["task"], rec["started"]), ("work", "W13", ["W13.5"]))


if __name__ == "__main__":
    unittest.main()
