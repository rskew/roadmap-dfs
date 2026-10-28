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


class ReviewTailTest(unittest.TestCase):
    def task(self, usd, first, issues, ok=0):
        return {"usd": usd, "first_review": first, "review_issues": issues, "review_ok": ok}

    def test_counts_and_shares_of_the_tasks_with_issues_rounds(self):
        tasks = {
            "a": self.task(10, "ok", 0, 1),         # passed first time
            "b": self.task(20, "issues", 1, 1),     # one issues round
            "c": self.task(60, "issues", 3, 1),     # three
            "d": {"usd": 10, "first_review": None, "review_issues": 0, "review_ok": 0},  # no review
        }
        tail = history.review_tail(tasks, 100)
        self.assertEqual((tail["reviewed"], tail["first_issues"]), (3, 2))
        self.assertEqual(tail[1], (2, 0.8))
        self.assertEqual(tail[3], (1, 0.6))


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

    def test_a_refusal_counts_once_under_each_reason_it_lists(self):
        refusal = ("dfs_check: the roadmap broke its own rules.\n\n"
                   "  - .dfs/items/W13.md is ~9,000 tokens, over the 8,000 budget, and this commit\n    grew it.\n"
                   "  - .dfs/items/W13.md line 4 carries a coordinate, 'a.py:3'. x\n"
                   "  - .dfs/items/W13.md line 9 carries a coordinate, 'b.py:7'. x\n"
                   "  - W13.4 names Parent W13.9, which does not exist\n"
                   "  - something else entirely\n")
        self.assertEqual(history.refusal_reasons(refusal),
                         ["over the size budget", "coordinate in a task file", "coordinate in a task file",
                          "Parent does not exist", "other"])
        rec = self.record([
            user("Follow /x/SKILL.md. Work task W13. Everything", "2026-10-01T00:00:00Z"),
            assistant("m1", [bash("t1", "git commit -m 'W13.4: a'")], "2026-10-01T00:01:00Z"),
            user([result("t1", refusal, True)], "2026-10-01T00:02:00Z"),
            # a grep that merely prints the guard's sentence is no refusal
            assistant("m2", [bash("t2", "grep -rn broke scripts")], "2026-10-01T00:03:00Z"),
            user([result("t2", refusal, False)], "2026-10-01T00:04:00Z"),
        ])
        self.assertEqual(rec["refused"], 1)
        self.assertEqual(dict(rec["reasons"]), {"over the size budget": 1, "coordinate in a task file": 1,
                                                 "Parent does not exist": 1, "other": 1})

    def commits(self, *outputs):
        """The `ok` of each commit, one per (command, result) pair."""
        recs = []
        for i, (cmd, text, error) in enumerate(outputs):
            recs += [assistant(f"m{i}", [bash(f"t{i}", cmd)], f"2026-10-01T00:0{i}:00Z"),
                     user([result(f"t{i}", text, error)], f"2026-10-01T00:0{i}:30Z")]
        rec = self.record([user("Follow /x/SKILL.md. Work task W13. Everything", "2026-10-01T00:00:00Z")] + recs)
        return [c["ok"] for c in rec["commits"]]

    def test_a_commit_that_failed_under_a_later_step_that_succeeded_is_not_made(self):
        who = "Author identity unknown\n\n*** Please tell me who you are.\nfatal: unable to auto-detect email address"
        self.assertEqual(self.commits(
            ("git commit -q -m 'W13.1: a' && echo x", who, False),     # no error flag, nothing committed
            ("git commit -m 'W13.1: a'; python3 tree.py log W13 x", who, False),
            ("git commit -q -m 'W13.1: a'", "", False),                # -q prints nothing on success
            ("git commit -m 'W13.1: a'", "[dfs/W13 abc1234] W13.1: a\n 1 file changed", False),
            ("git commit -m 'W13.2: b'", "nothing to commit, working tree clean", False),
            ("git commit -m 'W13.3: c'", "dfs_check: the roadmap broke its own rules.\n- x", True),
            # a quoted `|` in an option (`-c user.name="${a%|*}"`) before `commit`
            ("a=x; git -c user.name=\"${a%|*}\" commit -q -m 'W13.5: e'", "", False),
            # a retry inside one command that worked after the first try failed
            ("git commit -m 'W13.4: d' || git -c user.name=a -c user.email=b commit -m 'W13.4: d'",
             who + "\n[dfs/W13 def5678] W13.4: d", False),
        ), [False, False, True, True, False, False, True, True])

    def test_only_the_commits_that_were_made_count_as_nodes(self):
        recs = [user("Follow /x/SKILL.md. Work task W13. Everything", "2026-10-01T00:00:00Z")]
        for i, (subj, text, error) in enumerate([
                ("W13.1: a", "Author identity unknown\nfatal: unable to auto-detect", False),
                ("W13.1: a", "[dfs/W13 abc1234] W13.1: a", False),
                ("W13.2: b", "dfs_check: the roadmap broke its own rules.", True)]):
            recs += [assistant(f"m{i}", [bash(f"t{i}", f"git commit -m '{subj}'")], f"2026-10-01T00:0{i}:00Z"),
                     user([result(f"t{i}", text, error)], f"2026-10-01T00:0{i}:30Z")]
        s = dict(self.record(recs), usd=1.0)
        (task,) = history.tasks_of([s], {}).values()
        self.assertEqual((task["nodes"], task["refused"]), (1, 1))

    def test_a_work_session_and_its_started_node(self):
        rec = self.record([
            user("Follow /nix/store/x-roadmap-dfs/share/roadmap-dfs/SKILL.md. Work task W13. Everything", "2026-10-01T00:00:00Z"),
            assistant("m1", [bash("t1", "python3 $S/tree.py start W13 W13.5")], "2026-10-01T00:01:00Z"),
        ])
        self.assertEqual((rec["kind"], rec["task"], rec["started"]), ("work", "W13", ["W13.5"]))


class PriceTest(unittest.TestCase):
    def test_a_runner_cost_of_zero_is_kept_not_estimated(self):
        sessions = [{"sid": c, "model": "m", "units": 1000.0} for c in "abcdefg"]
        history.price(sessions, {"a": 0.0, **{c: 2.0 for c in "bcdef"}})
        self.assertEqual((sessions[0]["usd"], sessions[0]["priced"]), (0.0, True))
        self.assertEqual((sessions[6]["usd"], sessions[6]["priced"]), (2.0, False))


if __name__ == "__main__":
    unittest.main()
