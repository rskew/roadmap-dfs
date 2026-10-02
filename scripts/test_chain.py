#!/usr/bin/env python3
"""The runner's loop, driven end to end against a stub agent, in a real git repo.

What it pins is the part no session decides: that the runner records each session in
the task's Log, runs a critic after every CRITIC_EVERY work sessions, raises after
SESSION_LIMIT without the author, stops when a session moved nothing, clears a dirty
tree that is not this task's, and fails a critic that touched the code.

Run:  python3 roadmap-dfs/scripts/test_chain.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNNER = HERE / "run.sh"
sys.path.insert(0, str(HERE))
import tree as dfs_tree  # noqa: E402

ROADMAP = "# Roadmap\n\n## §0 How work is done\n\nEach task is a decision tree.\n"

TASK = """# W1 · the only thing

## Goal

The only thing.

## Tree

### W1.1 · the step
Status: open
Hypothesis: it can be done.
Evidence:

## Log
"""

# One recipe word per invocation, critic or not, in order:
#   work       add one evidence line to W1.1 (the repo moves)
#   code       change a tracked file outside .dfs, leave it uncommitted
#   confirm    confirm W1.1 and commit it as `W1.1: ...` (the task is done)
#   nothing    change nothing
#   raise      raise on W1.1 through tree.py
#   ok         a critic verdict of ok
#   rok        a review verdict of ok
#   rissue     a review raise plus an issues verdict
#   fix        add and confirm W1.2 with a commit
#   rnode      a review that adds an open node and gives an issues verdict
#   fixn       confirm the open node and commit it under its id
#   rminor     a review verdict of ok carrying a minor finding
#   issue      a critic raise plus an issues verdict
#   meddle     a critic that edits code and gives a verdict
#   silent     a critic that gives no verdict
#   limit      die on a 429
#   lower      `work`, then lower the chain's cap to this run, as the TUI's `w` does
#   stash      stash app.txt, as a briefing listing it as not this task's says to
STUB = r"""#!/usr/bin/env bash
set -euo pipefail
n=$(( $(cat "$COUNT_FILE") + 1 )); echo "$n" > "$COUNT_FILE"
prompt="${2:-}"
kind=work; case "$prompt" in *"You are the critic"*) kind=critic ;; *"You are the implementation reviewer"*) kind=review ;; esac
echo "$kind" >> "$KINDS_FILE"
echo "${RUNDIR:-<unset>}" > "$KINDS_FILE.rundir"
recipe="$(python3 -c 'import os,sys; r=os.environ["RECIPE"].split(","); i=int(sys.argv[1])-1; print(r[i] if i < len(r) else "nothing")' "$n")"
T="python3 $TREE_PY"
case "$recipe" in
  work)    python3 -c 'import os,sys; p=os.environ["DFS_ITEMS"]+"/W1.md"; t=open(p).read(); open(p,"w").write(t.replace("Evidence:\n","Evidence:\n- for: look %s\n" % sys.argv[1], 1))' "$n" ;;
  code)    echo "change $n" >> app.txt ;;
  confirm) python3 -c 'import os; p=os.environ["DFS_ITEMS"]+"/W1.md"; t=open(p).read(); open(p,"w").write(t.replace("Status: open","Status: confirmed\nDetermination: done.",1))'
           git add -A && git -c user.name=t -c user.email=t@t commit -qm "W1.1: the step is done" ;;
  raise)   echo "which way?" | $T log W1 raise W1.1 > /dev/null ;;
  ok)      $T log W1 critic ok < /dev/null > /dev/null ;;
  rok)     $T log W1 review ok < /dev/null > /dev/null ;;
  rissue)  echo "the step's code has a bug" | $T log W1 raise W1.1 > /dev/null
           echo "one issue" | $T log W1 review issues > /dev/null ;;
  fix)     python3 -c 'import os; p=os.environ["DFS_ITEMS"]+"/W1.md"; t=open(p).read(); open(p,"w").write(t.replace("## Log","### W1.2 · fix the bug\nParent: W1.1\nStatus: confirmed\nDetermination: fixed.\n\n## Log",1))'
           echo fixed >> app.txt; git add -A && git -c user.name=t -c user.email=t@t commit -qm "W1.2: fix the bug" ;;
  rnode)   python3 -c 'import os,re; p=os.environ["DFS_ITEMS"]+"/W1.md"; t=open(p).read(); n=len(re.findall(r"^### W1\.",t,re.M))+1; open(p,"w").write(t.replace("## Log","### W1.%d · review finding %d\nStatus: open\nApproach: fix it.\n\n## Log" % (n,n),1))'
           echo "a finding" | $T log W1 review issues > /dev/null ;;
  fixn)    id="$(python3 -c 'import os,re; p=os.environ["DFS_ITEMS"]+"/W1.md"; t=open(p).read(); m=re.search(r"^### (W1\.\d+) · [^\n]*\nStatus: open",t,re.M); open(p,"w").write(t[:m.start()]+m.group(0).replace("Status: open","Status: confirmed\nDetermination: fixed.")+t[m.end():]); print(m.group(1))')"
           echo "fixed $id" >> app.txt; git add -A && git -c user.name=t -c user.email=t@t commit -qm "$id: fixed" ;;
  rminor)  echo "a comment reads oddly" | $T log W1 review ok > /dev/null ;;
  issue)   echo "the evidence says nothing ran" | $T log W1 raise W1.1 > /dev/null
           echo "one issue" | $T log W1 critic issues > /dev/null ;;
  meddle)  echo "critic was here" >> app.txt; $T log W1 critic ok < /dev/null > /dev/null ;;
  silent)  : ;;
  lower)   python3 -c 'import os,sys; p=os.environ["DFS_ITEMS"]+"/W1.md"; t=open(p).read(); open(p,"w").write(t.replace("Evidence:\n","Evidence:\n- for: look %s\n" % sys.argv[1], 1))' "$n"
           echo "$n" > "$LOWER_FILE" ;;
  stash)   case "$prompt" in *"NOT this task's"*"app.txt"*) ;; *) echo "not told to clear app.txt" >&2; exit 3 ;; esac
           git stash push -q --include-untracked -m "dfs: set aside" -- app.txt ;;
  limit)   echo '{"type":"result","subtype":"error","is_error":true,"api_error_status":429,"result":"You have hit your session limit · resets 8pm (UTC)"}'; exit 1 ;;
esac
echo '{"type":"result","subtype":"success","is_error":false,"stop_reason":"end_turn","total_cost_usd":0.01,"num_turns":3}'
"""


class Chain(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / ".dfs" / "items").mkdir(parents=True)
        (self.root / ".dfs" / "ROADMAP.md").write_text(ROADMAP)
        (self.root / ".dfs" / "items" / "W1.md").write_text(TASK)
        (self.root / "app.txt").write_text("app\n")
        (self.root / ".gitignore").write_text(".dfs/runs/\n")
        self.aside = Path(tempfile.mkdtemp())
        self.stub = self.aside / "stub-agent"
        self.stub.write_text(STUB)
        self.count = self.aside / "count"
        self.count.write_text("0")
        self.kinds = self.aside / "kinds"
        self.kinds.write_text("")
        subprocess.run("git init -q && git add -A && git -c user.name=t -c user.email=t@t "
                       "commit -qm init", shell=True, cwd=self.root, check=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.aside, ignore_errors=True)

    def run_chain(self, recipe, cap=6, **env):
        e = dict(os.environ, CLAUDE_CMD="bash %s" % self.stub, RECIPE=recipe,
                 COUNT_FILE=str(self.count), KINDS_FILE=str(self.kinds),
                 TREE_PY=str(HERE / "tree.py"), HOME=str(self.aside), **env)
        # Run from inside a chain, the environment names THAT chain's directory; this
        # chain must make its own (see `export -n RUNDIR` in run.sh).
        e.pop("RUNDIR", None) if "RUNDIR" not in env else None
        p = subprocess.run([str(RUNNER), "W1", str(cap)], cwd=self.root, env=e,
                           capture_output=True, text=True, timeout=300)
        metas = sorted((self.root / ".dfs" / "runs").glob("*/meta.json"),
                       key=lambda f: f.stat().st_mtime)
        meta = json.loads(metas[-1].read_text()) if metas else {}
        return p.returncode, meta, p.stdout + p.stderr

    def tree(self):
        old = dfs_tree.dfs_paths.work_root
        dfs_tree.dfs_paths.work_root = lambda: self.root
        try:
            return dfs_tree.load("W1")
        finally:
            dfs_tree.dfs_paths.work_root = old

    def kinds_run(self):
        return self.kinds.read_text().split()

    def log(self, kind):
        return [e for e in self.tree()["log"] if e["kind"] == kind]

    def test_a_node_confirmed_and_committed_finishes_the_task(self):
        rc, meta, out = self.run_chain("work,confirm,rok")
        self.assertEqual((rc, meta.get("stop")), (0, "done"), out)
        self.assertEqual([e["args"][:2] for e in self.log("end")],
                         [["work", "ok"], ["work", "ok"], ["review", "ok"]], out)
        self.assertEqual(self.kinds_run(), ["work", "work", "review"])
        subj = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=self.root,
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(subj, "W1.1: the step is done")

    def test_a_session_that_moved_nothing_stops_the_chain(self):
        rc, meta, out = self.run_chain("work,nothing,work")
        self.assertEqual((rc, meta.get("stop")), (1, "idle"), out)
        self.assertEqual(self.log("end")[-1]["args"][:2], ["work", "idle"])

    def test_uncommitted_code_counts_as_moving_and_is_the_next_sessions(self):
        # A session that stops mid-node leaves its work in the tree; the next one on
        # the same task is allowed to start over it.
        rc, meta, out = self.run_chain("code", cap=1)
        self.assertEqual((rc, meta.get("stop")), (0, "cap"), out)
        rc, meta, out = self.run_chain("code,confirm,rok", cap=3)
        self.assertEqual((rc, meta.get("stop")), (0, "done"), out)

    def test_a_dirty_tree_that_is_not_this_tasks_is_the_first_sessions_to_clear(self):
        # It used to refuse to start, which stopped the walk until the author came.
        (self.root / "app.txt").write_text("the author's edit\n")
        rc, meta, out = self.run_chain("stash,confirm,rok")
        self.assertEqual((rc, meta.get("stop")), (0, "done"), out)
        stashes = subprocess.run(["git", "stash", "list"], cwd=self.root,
                                 capture_output=True, text=True).stdout
        self.assertIn("dfs: set aside", stashes)

    def test_foreign_dirt_goes_to_a_work_session_before_a_due_critic(self):
        # A critic may not change code, and after it the dirt would read as W1's.
        rc, meta, out = self.run_chain("work,work,work,work,work", cap=5)
        self.assertEqual(meta.get("stop"), "cap", out)
        # The last session anywhere was another task's, so the edit is not W1's. Its
        # ts is written out: W1's are bumped past the clock to stay unique.
        (self.root / ".dfs" / "items" / "W2.md").write_text(
            TASK.replace("W1", "W2") + "\n- 2099-01-01T00:00:00Z · session · work\n")
        (self.root / "app.txt").write_text("W2's half-done edit\n")
        rc, meta, out = self.run_chain("work,work,work,work,work,stash,ok", cap=2)
        self.assertEqual(self.kinds_run(), ["work"] * 6 + ["critic"], out)

    def test_the_chain_s_directory_is_not_handed_to_its_agent(self):
        # ⚠️ 2026-09-27: a session working on this tooling ran a test that started its
        # own chain, which inherited RUNDIR and wrote its meta INTO the live chain's
        # directory. The walk then read the live chain as ended and started another.
        named = self.aside / "named"
        rc, _, out = self.run_chain("work", cap=1, RUNDIR=str(named))
        self.assertTrue((named / "meta.json").exists(), "the runner ignored RUNDIR:\n" + out)
        self.assertEqual((self.aside / "kinds.rundir").read_text().strip(), "<unset>",
                         "the agent inherited the chain's directory")

    def test_a_raise_blocks(self):
        rc, meta, out = self.run_chain("raise")
        self.assertEqual((rc, meta.get("stop")), (0, "blocked"), out)

    def test_the_critic_runs_after_every_fifth_work_session(self):
        rc, meta, out = self.run_chain("work,work,work,work,work,ok,work", cap=7)
        self.assertEqual(self.kinds_run(),
                         ["work"] * 5 + ["critic"] + ["work"], out)
        self.assertEqual(meta.get("stop"), "cap", out)
        self.assertEqual(self.log("critic")[0]["args"], ["ok"])

    def test_a_critic_issue_is_a_raise_and_blocks(self):
        rc, meta, out = self.run_chain("work,work,work,work,work,issue,work", cap=7)
        self.assertEqual((rc, meta.get("stop")), (0, "blocked"), out)
        self.assertEqual(self.kinds_run()[-1], "critic")

    def test_a_critic_that_changes_code_fails_the_chain(self):
        rc, meta, out = self.run_chain("work,work,work,work,work,meddle", cap=7)
        self.assertEqual((rc, meta.get("stop")), (1, "failed"), out)
        self.assertIn("changed the working tree", out)

    def test_a_critic_with_no_verdict_fails_the_chain(self):
        rc, meta, out = self.run_chain("work,work,work,work,work,silent", cap=7)
        self.assertEqual((rc, meta.get("stop")), (1, "failed"), out)
        self.assertIn("no verdict", out)

    def test_ten_sessions_without_the_author_raise_for_review(self):
        recipe = ",".join(["work"] * 5 + ["ok"] + ["work"] * 5 + ["ok"] + ["work"])
        rc, meta, out = self.run_chain(recipe, cap=20)
        self.assertEqual((rc, meta.get("stop")), (0, "blocked"), out)
        self.assertEqual(self.kinds_run().count("work"), 10, out)
        raises = dfs_tree.open_raises(self.tree())
        self.assertEqual(len(raises), 1)
        self.assertIn("10 work sessions", raises[0]["body"])

    def test_an_answer_resets_the_session_count(self):
        recipe = ",".join(["work"] * 5 + ["ok"] + ["work"] * 5 + ["ok"])
        self.run_chain(recipe, cap=20)
        t = self.tree()
        ts = dfs_tree.open_raises(t)[0]["ts"]
        old = dfs_tree.dfs_paths.work_root
        dfs_tree.dfs_paths.work_root = lambda: self.root
        try:
            dfs_tree.append_log("W1", "answer", [ts], "carry on")
        finally:
            dfs_tree.dfs_paths.work_root = old
        self.count.write_text("0")
        self.kinds.write_text("")
        rc, meta, out = self.run_chain("work,confirm,rok", cap=4)
        self.assertEqual(meta.get("stop"), "done", out)

    def test_a_complete_tree_is_not_done_until_its_implementation_is_reviewed(self):
        rc, meta, out = self.run_chain("confirm", cap=1)
        self.assertEqual((rc, meta.get("stop")), (0, "cap"), out)
        self.assertIn("implementation review is due", out)

    def test_a_review_that_changes_code_fails_the_chain(self):
        # The reviewer judges the implementation; like the critic, it may not touch it.
        rc, meta, out = self.run_chain("confirm,meddle", cap=3)
        self.assertEqual((rc, meta.get("stop")), (1, "failed"), out)
        self.assertIn("the review changed the working tree", out)

    def test_review_issues_reopen_the_task_once_answered_and_the_fix_is_reviewed(self):
        rc, meta, out = self.run_chain("confirm,rissue", cap=3)
        self.assertEqual((rc, meta.get("stop")), (0, "blocked"), out)
        ts = dfs_tree.open_raises(self.tree())[0]["ts"]
        old = dfs_tree.dfs_paths.work_root
        dfs_tree.dfs_paths.work_root = lambda: self.root
        try:
            dfs_tree.append_log("W1", "answer", [ts], "fix it")
        finally:
            dfs_tree.dfs_paths.work_root = old
        self.count.write_text("0")
        self.kinds.write_text("")
        rc, meta, out = self.run_chain("fix,rok", cap=3)
        self.assertEqual(self.kinds_run(), ["work", "review"], out)
        self.assertEqual((rc, meta.get("stop")), (0, "done"), out)

    def test_two_review_rounds_that_did_not_settle_raise_instead_of_a_third(self):
        rc, meta, out = self.run_chain("confirm,rnode,fixn,rnode,fixn,rok", cap=8)
        self.assertEqual(self.kinds_run(), ["work", "review", "work", "review", "work"], out)
        self.assertEqual((rc, meta.get("stop")), (0, "blocked"), out)
        raises = dfs_tree.open_raises(self.tree())
        self.assertEqual(len(raises), 1, out)
        self.assertIn("2 implementation reviews", raises[0]["body"])

    def test_a_second_review_that_comes_back_ok_finishes_the_task(self):
        rc, meta, out = self.run_chain("confirm,rnode,fixn,rminor", cap=8)
        self.assertEqual(self.kinds_run(), ["work", "review", "work", "review"], out)
        self.assertEqual((rc, meta.get("stop")), (0, "done"), out)
        self.assertEqual(self.log("review")[-1]["args"], ["ok"])

    def test_the_authors_answer_starts_the_review_rounds_over(self):
        self.run_chain("confirm,rnode,fixn,rnode,fixn", cap=8)
        t = self.tree()
        self.assertEqual(dfs_tree.review_rounds_unsettled(t), 2)
        old = dfs_tree.dfs_paths.work_root
        dfs_tree.dfs_paths.work_root = lambda: self.root
        try:
            dfs_tree.append_log("W1", "answer", [dfs_tree.open_raises(t)[0]["ts"]], "go on")
            self.assertEqual(dfs_tree.review_rounds_unsettled(dfs_tree.load("W1")), 0)
        finally:
            dfs_tree.dfs_paths.work_root = old

    def test_the_review_brief_holds_the_bar_and_names_its_round(self):
        import state as dfs_state
        self.run_chain("confirm,rnode,fixn", cap=3)
        old = dfs_tree.dfs_paths.work_root
        dfs_tree.dfs_paths.work_root = lambda: self.root
        try:
            brief = dfs_state.review_brief("W1")
        finally:
            dfs_tree.dfs_paths.work_root = old
        self.assertIn("only if it shows the Goal is not met", brief)
        self.assertIn("never a node of its own", brief)
        self.assertIn("This is review round 2", brief)
        self.assertIn("raised to the author instead", brief)

    def test_a_usage_limit_is_recorded_and_stops(self):
        rc, meta, out = self.run_chain("work,limit")
        self.assertEqual((rc, meta.get("stop")), (1, "limit"), out)
        self.assertEqual(meta.get("resets"), "resets 8pm (UTC)")
        self.assertEqual(self.log("end")[-1]["args"][:2], ["work", "limit"])

    def lowered(self, recipe, cap, before=None):
        rundir = self.root / ".dfs" / "runs" / "dfs_run_lowered"
        rundir.mkdir(parents=True)
        if before is not None:
            (rundir / "cap").write_text("%d\n" % before)
        return self.run_chain(recipe, cap=cap, RUNDIR=str(rundir),
                              LOWER_FILE=str(rundir / "cap"))

    def test_a_cap_lowered_mid_chain_ends_it_after_the_run_in_flight(self):
        # The walk turned off by hand: the session in flight finishes, none follows.
        rc, meta, out = self.lowered("work,lower,work,work", cap=6)
        self.assertEqual((rc, meta.get("stop"), meta.get("cap")), (0, "cap", "2"), out)
        self.assertEqual(self.kinds_run(), ["work", "work"], out)

    def test_a_cap_lowered_before_the_first_run_starts_nothing(self):
        rc, meta, out = self.lowered("work", cap=6, before=0)
        self.assertEqual((rc, meta.get("stop")), (0, "cap"), out)
        self.assertEqual(self.kinds_run(), [], out)

    def test_the_file_can_only_lower_the_cap(self):
        rc, meta, out = self.lowered("work,work,work", cap=2, before=9)
        self.assertEqual((rc, meta.get("stop"), meta.get("cap")), (0, "cap", "2"), out)
        self.assertEqual(self.kinds_run(), ["work", "work"], out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
