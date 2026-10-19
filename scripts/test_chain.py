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
#   rmeddle    a review that edits code and gives a verdict of ok
#   okbare     a critic ok that names no node (the plain `ok` recipe names W1.1)
#   silent     a critic that changes nothing and gives no verdict
#   noverdict  a critic that raises on W1.1 but gives no verdict
#   limit      die on a 429
#   lower      `work`, then lower the chain's cap to this run, as the TUI's `w` does
#   resolvemain confirm the open node, committing it, after rebasing the branch onto main
#              the way the node asks (keeping the branch's side of a conflict)
#   stash      stash app.txt, as a briefing listing it as not this task's says to
STUB = r"""#!/usr/bin/env bash
set -euo pipefail
n=$(( $(cat "$COUNT_FILE") + 1 )); echo "$n" > "$COUNT_FILE"
prompt="${2:-}"
kind=work; case "$prompt" in *"You are the critic"*) kind=critic ;; *"You are the implementation reviewer"*) kind=review ;; esac
echo "$kind" >> "$KINDS_FILE"
echo "$kind $(printf '%s\n' "$@" | grep -A1 -x -e '--model' | tail -1)" >> "$KINDS_FILE.models"
echo "${RUNDIR:-<unset>}" > "$KINDS_FILE.rundir"
pwd -P > "$KINDS_FILE.cwd"
recipe="$(python3 -c 'import os,sys; r=os.environ["RECIPE"].split(","); i=int(sys.argv[1])-1; print(r[i] if i < len(r) else "nothing")' "$n")"
T="python3 $TREE_PY"
case "$recipe" in
  work)    python3 -c 'import os,sys; p=os.environ["DFS_ITEMS"]+"/W1.md"; t=open(p).read(); open(p,"w").write(t.replace("Evidence:\n","Evidence:\n- for: look %s\n" % sys.argv[1], 1))' "$n" ;;
  code)    echo "change $n" >> app.txt ;;
  confirm) python3 -c 'import os; p=os.environ["DFS_ITEMS"]+"/W1.md"; t=open(p).read(); open(p,"w").write(t.replace("Status: open","Status: confirmed\nDetermination: done.",1))'
           git add -A && git -c user.name=t -c user.email=t@t commit -q --allow-empty -m "W1.1: the step is done" ;;
  raise)   echo "which way?" | $T log W1 raise W1.1 > /dev/null ;;
  ok)      echo "W1.1: re-ran its check; it passes" | $T log W1 critic ok > /dev/null ;;
  okbare)  $T log W1 critic ok < /dev/null > /dev/null ;;
  rok)     $T log W1 review ok < /dev/null > /dev/null ;;
  rissue)  echo "the step's code has a bug" | $T log W1 raise W1.1 > /dev/null
           echo "one issue" | $T log W1 review issues > /dev/null ;;
  fix)     python3 -c 'import os; p=os.environ["DFS_ITEMS"]+"/W1.md"; t=open(p).read(); open(p,"w").write(t.replace("## Log","### W1.2 · fix the bug\nParent: W1.1\nStatus: confirmed\nDetermination: fixed.\n\n## Log",1))'
           echo fixed >> app.txt; git add -A && git -c user.name=t -c user.email=t@t commit -qm "W1.2: fix the bug" ;;
  rnode)   python3 -c 'import os,re; p=os.environ["DFS_ITEMS"]+"/W1.md"; t=open(p).read(); n=len(re.findall(r"^### W1\.",t,re.M))+1; open(p,"w").write(t.replace("## Log","### W1.%d · review finding %d\nStatus: open\nApproach: fix it.\n\n## Log" % (n,n),1))'
           echo "a finding" | $T log W1 review issues > /dev/null ;;
  unblock) rm -f "$BLOCK_FILE" ;&
  fixn)    id="$(python3 -c 'import os,re; p=os.environ["DFS_ITEMS"]+"/W1.md"; t=open(p).read(); m=re.search(r"^### (W1\.\d+) · [^\n]*\nStatus: open",t,re.M); open(p,"w").write(t[:m.start()]+m.group(0).replace("Status: open","Status: confirmed\nDetermination: fixed.")+t[m.end():]); print(m.group(1))')"
           echo "fixed $id" >> app.txt; git add -A && git -c user.name=t -c user.email=t@t commit -qm "$id: fixed" ;;
  resolvemain) git -c user.name=t -c user.email=t@t rebase -q --autostash -X theirs "$MAIN_BRANCH" > /dev/null 2>&1 || exit 3
           id="$(python3 -c 'import os,re; p=os.environ["DFS_ITEMS"]+"/W1.md"; t=open(p).read(); m=re.search(r"^### (W1\.\d+) · [^\n]*\nStatus: open",t,re.M); open(p,"w").write(t[:m.start()]+m.group(0).replace("Status: open","Status: confirmed\nDetermination: rebased.")+t[m.end():]); print(m.group(1))')"
           git add -A && git -c user.name=t -c user.email=t@t commit -qm "$id: the item is up to date" ;;
  rminor)  echo "a comment reads oddly" | $T log W1 review ok > /dev/null ;;
  issue)   echo "the evidence says nothing ran" | $T log W1 raise W1.1 > /dev/null
           echo "one issue" | $T log W1 critic issues > /dev/null ;;
  meddle)  echo "critic was here" >> app.txt; $T log W1 critic ok < /dev/null > /dev/null ;;
  rmeddle) echo "reviewer was here" >> app.txt; $T log W1 review ok < /dev/null > /dev/null ;;
  silent)  : ;;
  noverdict) echo "a note" | $T log W1 raise W1.1 > /dev/null ;;
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

    @property
    def wt(self):
        """The worktree the chain works W1 in, on the branch `dfs/W1`."""
        return self.root / ".dfs" / "worktrees" / "W1"

    def enter(self):
        """Cut W1's worktree as a chain's first run does."""
        return subprocess.run([sys.executable, str(HERE / "branch.py"), "enter", "W1"],
                              cwd=self.root, capture_output=True, text=True, check=True)

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.root, capture_output=True,
                              text=True, check=True).stdout.strip()

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
        # the review `ok` merged the branch: the node's commit is in main's history
        self.assertIn("W1.1: the step is done", self.git("log", "--format=%s"))

    def test_a_chain_works_the_item_on_its_own_branch_and_main_does_not_move(self):
        main = self.git("rev-parse", "HEAD")
        branch = self.git("symbolic-ref", "--short", "HEAD")
        (self.root / "app.txt").write_text("the author's edit\n")
        rc, meta, out = self.run_chain("work,confirm", cap=2)
        self.assertEqual((rc, meta.get("stop")), (0, "cap"), out)
        self.assertIn("W1 is worked on branch dfs/W1", out)
        self.assertEqual(self.git("rev-parse", "HEAD"), main)
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD"), branch)
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD", cwd=self.wt), "dfs/W1")
        self.assertEqual(self.git("log", "--format=%s", "%s..dfs/W1" % main), "W1.1: the step is done")
        self.assertEqual((self.root / "app.txt").read_text(), "the author's edit\n")
        # the chain's own directory stays where the screen reads it
        self.assertTrue(list((self.root / ".dfs" / "runs").glob("*/meta.json")))
        self.assertFalse((self.wt / ".dfs" / "runs").exists())

    def test_a_passing_review_merges_the_branch_and_drops_the_worktree(self):
        (self.root / "app.txt").write_text("the author's edit\n")
        rc, meta, out = self.run_chain("work,confirm,rok")
        self.assertEqual((rc, meta.get("stop")), (0, "done"), out)
        self.assertIn("dfs/W1 merged into", out)
        self.assertEqual(self.git("log", "--merges", "--format=%s"), "W1: merge the item's branch dfs/W1")
        self.assertFalse(self.wt.exists())
        self.assertEqual(self.git("branch", "--list", "dfs/*"), "")
        self.assertEqual(self.git("worktree", "list").count("\n"), 0)
        # the log the runner wrote after the last commit came across with it
        self.assertEqual(dfs_tree.status_of(self.tree())[0], "done")
        self.assertEqual([e["args"][:2] for e in self.log("end")][-1], ["review", "ok"])
        self.assertEqual((self.root / "app.txt").read_text(), "the author's edit\n")

    def test_a_merge_that_conflicts_reopens_the_item_and_the_fix_is_reviewed_and_merged(self):
        main = self.git("symbolic-ref", "--short", "HEAD")
        rc, meta, out = self.run_chain("code,confirm", cap=2)
        (self.root / "app.txt").write_text("app\nmain's line\n")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "main moves")
        self.count.write_text("0")
        rc, meta, out = self.run_chain("rok,resolvemain,rok", cap=3, MAIN_BRANCH=main)
        self.assertEqual((rc, meta.get("stop")), (0, "done"), out)
        self.assertIn("conflicts with %s" % main, out)
        self.assertEqual(self.kinds_run()[-3:], ["review", "work", "review"], out)
        self.assertIn("Rebase dfs/W1 onto %s and resolve the conflicts" % main,
                      (self.root / ".dfs" / "items" / "W1.md").read_text())
        self.assertFalse(self.wt.exists())
        self.assertEqual(self.git("log", "--merges", "--format=%s", main).splitlines(),
                         ["W1: merge the item's branch dfs/W1"])
        self.assertEqual(list((self.root / ".git").glob("worktrees/*/rebase-merge")), [])
        self.assertIn("W1.1: the step is done", self.git("log", "--format=%s", main))
        self.assertFalse((self.root / ".git" / "MERGE_HEAD").exists())

    def test_a_branch_is_rebased_onto_a_default_branch_that_moved_before_it_merges(self):
        main = self.git("symbolic-ref", "--short", "HEAD")
        self.run_chain("work,confirm", cap=2)
        (self.root / "other.txt").write_text("main's own file\n")
        self.git("add", "other.txt")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "main moves")
        moved = self.git("rev-parse", main)
        self.count.write_text("0")
        rc, meta, out = self.run_chain("rok", cap=1, MAIN_BRANCH=main)
        self.assertEqual((rc, meta.get("stop")), (0, "done"), out)
        # the node commit now sits on the commit main moved to, and the merge joins them
        merge = self.git("rev-list", "--merges", "-n1", main)
        self.assertEqual(self.git("rev-parse", merge + "^1"), moved)
        subprocess.run(["git", "merge-base", "--is-ancestor", moved, merge + "^2"], cwd=self.root, check=True)
        self.assertFalse(self.wt.exists())

    def test_a_merge_over_the_authors_uncommitted_edits_is_a_raise_and_leaves_main_alone(self):
        (self.root / "app.txt").write_text("the author's edit\n")
        head = self.git("rev-parse", "HEAD")
        rc, meta, out = self.run_chain("code,confirm,rok")
        self.assertEqual(meta.get("stop"), "blocked", out)
        raised = self.log("raise")
        self.assertEqual(len(raised), 1, out)
        self.assertIn("app.txt", raised[0]["body"])
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual((self.root / "app.txt").read_text(), "the author's edit\n")
        self.assertTrue(self.wt.exists())
        self.assertFalse((self.root / ".git" / "MERGE_HEAD").exists())

    def test_a_staged_edit_that_stops_the_merge_is_named_in_the_raise(self):
        (self.root / "other.txt").write_text("staged\n")
        self.git("add", "other.txt")
        head = self.git("rev-parse", "HEAD")
        rc, meta, out = self.run_chain("code,confirm,rok")
        self.assertEqual(meta.get("stop"), "blocked", out)
        body = self.log("raise")[0]["body"]
        self.assertIn("stop the merge: other.txt", body)
        self.assertNotIn("overlap", body)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)

    def test_a_review_ok_over_a_dirty_worktree_reopens_the_item_and_is_never_done_unmerged(self):
        rc, meta, out = self.run_chain("work,confirm,rmeddle", cap=3)
        self.assertNotEqual(meta.get("stop"), "done", out)
        self.assertIn("Commit or drop the uncommitted changes", (self.wt / ".dfs/items/W1.md").read_text())
        self.assertIn("app.txt", (self.wt / ".dfs/items/W1.md").read_text())
        self.assertTrue(self.wt.exists())
        self.count.write_text("0")
        rc, meta, out = self.run_chain("fixn,rok", cap=2)
        self.assertEqual((rc, meta.get("stop")), (0, "done"), out)
        self.assertFalse(self.wt.exists())
        self.assertEqual(self.git("log", "--merges", "--format=%s"), "W1: merge the item's branch dfs/W1")
        self.assertIn("reviewer was here", (self.root / "app.txt").read_text())

    def test_a_merge_a_hook_refuses_reopens_the_item_with_the_hooks_message(self):
        block = self.aside / "block"
        block.write_text("")
        hook = self.root / ".git" / "hooks" / "pre-merge-commit"
        hook.write_text('#!/bin/sh\n[ -e "%s" ] && { echo "guard says: fix the heading" >&2; exit 1; }\nexit 0\n' % block)
        hook.chmod(0o755)
        if not os.access(hook, os.X_OK):
            self.skipTest("the temp directory is mounted noexec, so git cannot run the hook")
        rc, meta, out = self.run_chain("work,confirm,rok", cap=3, BLOCK_FILE=str(block))
        self.assertNotEqual(meta.get("stop"), "done", out)
        text = (self.wt / ".dfs/items/W1.md").read_text()
        self.assertIn("Fix what refused the merge of dfs/W1", text)
        self.assertIn("guard says: fix the heading", text)
        self.assertNotIn("resolve the conflicts", text)
        self.assertFalse((self.root / ".git" / "MERGE_HEAD").exists())
        self.count.write_text("0")
        rc, meta, out = self.run_chain("unblock,rok", cap=2, BLOCK_FILE=str(block))
        self.assertEqual((rc, meta.get("stop")), (0, "done"), out)
        self.assertFalse(self.wt.exists())

    def test_a_commit_of_the_task_s_files_that_is_refused_reopens_the_item(self):
        self.run_chain("work,confirm", cap=2)
        item = self.wt / ".dfs" / "items" / "W1.md"
        item.write_text(item.read_text() + "- 2026-01-01T00:00:00Z · note\n")
        self.git("config", "commit.gpgsign", "true")
        self.git("config", "gpg.program", "false")
        r = subprocess.run([sys.executable, str(HERE / "branch.py"), "merge", "W1"], cwd=self.root,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("Fix what refuses the commit of W1's files on dfs/W1", item.read_text())
        self.assertTrue(self.wt.exists())

    def test_a_hooks_message_is_cut_at_a_word_without_the_guards_warn_aside(self):
        import branch as dfs_branch
        out = "first line\n(--warn covers only the warnings above)\n" + "word " * 200
        gist = dfs_branch._gist(out, 40)
        self.assertTrue(gist.startswith("first line word word"), gist)
        self.assertTrue(gist.endswith("word …"), gist)
        self.assertNotIn("--warn", gist)
        self.assertEqual(dfs_branch._gist("a\nb"), "a b")

    def test_a_merge_from_a_checkout_off_the_default_branch_is_a_raise_not_a_pass(self):
        self.run_chain("work,confirm", cap=2)
        self.git("checkout", "-q", "-b", "elsewhere")
        r = subprocess.run([sys.executable, str(HERE / "branch.py"), "merge", "W1"], cwd=self.root,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertEqual(len(self.log("raise")), 1)
        self.assertTrue(self.wt.exists())

    def authors_merge_in_progress(self):
        """The author's own `git merge side` on main, conflicted on mine.txt, which they have
        resolved by hand and not yet added."""
        main = self.git("symbolic-ref", "--short", "HEAD")
        self.git("checkout", "-q", "-b", "side")
        (self.root / "mine.txt").write_text("side\n")
        self.git("add", "mine.txt")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "side edits mine")
        self.git("checkout", "-q", main)
        (self.root / "mine.txt").write_text("main\n")
        self.git("add", "mine.txt")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "main edits mine")
        m = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "merge", "side"], cwd=self.root, capture_output=True, text=True)
        self.assertTrue((self.root / ".git" / "MERGE_HEAD").exists(), m.stdout + m.stderr)
        (self.root / "mine.txt").write_text("the author's resolution\n")

    def assert_authors_merge_untouched(self):
        self.assertTrue((self.root / ".git" / "MERGE_HEAD").exists())
        self.assertEqual((self.root / "mine.txt").read_text(), "the author's resolution\n")
        self.assertTrue(self.wt.exists())
        self.assertNotIn("resolve the conflicts", (self.wt / ".dfs/items/W1.md").read_text())

    def test_a_merge_while_the_author_is_mid_merge_is_a_raise_and_leaves_their_merge_alone(self):
        self.run_chain("work,confirm", cap=2)
        self.authors_merge_in_progress()
        r = subprocess.run([sys.executable, str(HERE / "branch.py"), "merge", "W1"], cwd=self.root,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertEqual(len(self.log("raise")), 1)
        self.assertIn("its own merge unfinished", self.log("raise")[0]["body"])
        self.assert_authors_merge_untouched()

    def test_the_authors_accept_while_they_are_mid_merge_is_a_raise_and_leaves_their_merge_alone(self):
        self.run_chain("work,confirm", cap=2)
        self.authors_merge_in_progress()
        subprocess.run([sys.executable, str(HERE / "tree.py"), "log", "W1", "accept"],
                       cwd=self.root, check=True, capture_output=True, stdin=subprocess.DEVNULL)
        self.assertEqual(len(self.log("raise")), 1)
        self.assert_authors_merge_untouched()

    def authors_rebase_in_progress(self):
        """The author's own `git rebase side` of main, stopped on a conflict in mine.txt: HEAD
        is detached for as long as it is unfinished."""
        main = self.git("symbolic-ref", "--short", "HEAD")
        self.git("checkout", "-q", "-b", "up")
        (self.root / "mine.txt").write_text("up\n")
        self.git("add", "mine.txt")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "up edits mine")
        self.git("checkout", "-q", main)
        (self.root / "mine.txt").write_text("main\n")
        self.git("add", "mine.txt")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "main edits mine")
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "rebase", "up"],
                       cwd=self.root, capture_output=True, text=True)
        self.assertTrue(list((self.root / ".git").glob("rebase-*")), "the rebase should be stopped")

    def test_a_merge_while_the_author_is_mid_rebase_is_a_logged_raise_and_the_task_reads_blocked(self):
        self.run_chain("work,confirm", cap=2)
        self.authors_rebase_in_progress()
        r = subprocess.run([sys.executable, str(HERE / "branch.py"), "merge", "W1"], cwd=self.root,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(len(self.log("raise")), 1)
        self.assertIn("rebase unfinished", self.log("raise")[0]["body"])
        self.assertTrue(list((self.root / ".git").glob("rebase-*")))
        self.assertTrue(self.wt.exists())
        self.assertEqual(dfs_tree.status_of(self.tree())[0], "blocked")

    def test_a_merge_from_a_detached_head_is_a_logged_raise_naming_it(self):
        self.run_chain("work,confirm", cap=2)
        self.git("checkout", "-q", "--detach")
        r = subprocess.run([sys.executable, str(HERE / "branch.py"), "merge", "W1"], cwd=self.root,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertEqual(len(self.log("raise")), 1)
        self.assertIn("detached HEAD", self.log("raise")[0]["body"])
        self.assertTrue(self.wt.exists())

    def test_a_merge_with_the_branch_checked_out_in_the_main_checkout_is_a_raise_not_a_pass(self):
        self.run_chain("work,confirm", cap=2)
        shutil.rmtree(self.wt)
        self.git("worktree", "prune")
        main = self.git("symbolic-ref", "--short", "HEAD")
        self.git("checkout", "-q", "dfs/W1")
        r = subprocess.run([sys.executable, str(HERE / "branch.py"), "merge", "W1"], cwd=self.root,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertEqual(len(self.log("raise")), 1)
        self.assertIn("checked out", self.log("raise")[0]["body"])
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD"), "dfs/W1")
        self.assertNotIn("W1.1", self.git("log", "--format=%s", main))

    def test_a_rebase_left_unfinished_in_the_worktree_is_said_so_not_aborted(self):
        main = self.git("symbolic-ref", "--short", "HEAD")
        self.run_chain("work,confirm", cap=2)
        (self.root / "other.txt").write_text("main's own file\n")
        self.git("add", "other.txt")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "main moves")
        # a session's `git rebase -i`, stopped at an `edit` with nothing staged
        started = subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "rebase", "--autostash", "-i", main],
            cwd=self.wt, capture_output=True, text=True,
            env=dict(os.environ, GIT_SEQUENCE_EDITOR="sed -i 's/^pick/edit/'"))
        self.assertTrue(list((self.root / ".git").glob("worktrees/*/rebase-merge")),
                        started.stdout + started.stderr)
        self.assertTrue(list((self.root / ".git").glob("worktrees/*/rebase-merge")))
        r = subprocess.run([sys.executable, str(HERE / "branch.py"), "merge", "W1"], cwd=self.root,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertTrue(list((self.root / ".git").glob("worktrees/*/rebase-merge")))
        self.assertIn("rebase left unfinished", (self.wt / ".dfs/items/W1.md").read_text())
        self.assertNotIn("W1.1", self.git("log", "--format=%s", main))

    def test_an_item_is_worked_in_place_when_the_checkout_is_off_main_and_master(self):
        self.git("checkout", "-q", "-b", "elsewhere")
        rc, meta, out = self.run_chain("work,confirm", cap=2)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.kinds_run(), ["work", "work"], out)
        self.assertFalse(self.wt.exists())
        self.assertEqual(self.git("branch", "--list", "dfs/W1"), "")
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD"), "elsewhere")
        self.assertIn("W1.1: the step is done", self.git("log", "--format=%s"))

    def test_a_task_with_a_worktree_is_not_done_unmerged_when_the_checkout_is_off_main_and_master(self):
        self.run_chain("work", cap=1)
        main = self.git("symbolic-ref", "--short", "HEAD")
        self.git("checkout", "-q", "-b", "elsewhere")
        elsewhere = self.git("rev-parse", "HEAD")
        rc, meta, out = self.run_chain("work,confirm,rok", cap=3)
        self.assertNotEqual(meta.get("stop"), "done", out)
        self.assertEqual(self.git("rev-parse", "HEAD"), elsewhere, out)
        self.assertIn("W1.1: the step is done", self.git("log", "--format=%s", "dfs/W1"))
        self.assertTrue(self.wt.exists())
        self.assertEqual(len(self.log("raise")), 1, out)
        self.assertEqual(dfs_tree.status_of(self.tree())[0], "blocked")
        self.git("checkout", "-q", main)
        r = subprocess.run([sys.executable, str(HERE / "branch.py"), "merge", "W1"], cwd=self.root,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("W1.1: the step is done", self.git("log", "--format=%s"))

    def test_a_chain_in_a_repo_with_no_commit_yet_works_in_place(self):
        shutil.rmtree(self.root / ".git")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        rc, meta, out = self.run_chain("work,confirm", cap=2)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.kinds_run(), ["work", "work"], out)
        self.assertFalse(self.wt.exists())
        self.assertEqual(self.git("log", "--format=%s"), "W1.1: the step is done")

    def test_a_branch_whose_worktree_was_removed_by_hand_is_checked_out_again_and_merged(self):
        self.run_chain("work,confirm", cap=2)
        shutil.rmtree(self.wt)
        r = subprocess.run([sys.executable, str(HERE / "branch.py"), "merge", "W1"], cwd=self.root,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("worktree was missing", r.stderr)
        self.assertIn("W1.1: the step is done", self.git("log", "--format=%s"))
        self.assertEqual(self.git("branch", "--list", "dfs/W1"), "")
        self.assertFalse(self.wt.exists())

    def test_a_worktree_removed_by_hand_is_cut_again_by_enter(self):
        self.run_chain("work,confirm", cap=2)
        shutil.rmtree(self.wt)
        self.enter()
        self.assertTrue((self.wt / ".git").exists())
        self.assertIn("W1.1: the step is done", self.git("log", "--format=%s", cwd=self.wt))

    def test_a_worktree_git_will_not_remove_is_said_so_not_claimed_removed(self):
        self.run_chain("work,confirm", cap=2)
        self.git("worktree", "lock", str(self.wt))
        r = subprocess.run([sys.executable, str(HERE / "branch.py"), "merge", "W1"], cwd=self.root,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("merged, but its worktree could not be removed", r.stderr)
        self.assertIn("W1.1: the step is done", self.git("log", "--format=%s"))
        self.assertTrue(self.wt.exists())

    def test_the_authors_accept_merges_the_branch_too(self):
        rc, meta, out = self.run_chain("work,confirm", cap=2)
        self.assertIn("dfs/W1", self.git("branch", "--list", "dfs/W1"))
        subprocess.run([sys.executable, str(HERE / "tree.py"), "log", "W1", "accept"],
                       cwd=self.root, check=True, capture_output=True, stdin=subprocess.DEVNULL)
        self.assertFalse(self.wt.exists())
        self.assertIn("W1.1: the step is done", self.git("log", "--format=%s"))
        self.assertTrue(self.log("accept"))

    def test_the_authors_accept_from_a_checkout_off_the_default_branch_is_a_raise_not_a_pass(self):
        self.run_chain("work,confirm", cap=2)
        self.git("checkout", "-q", "-b", "elsewhere")
        r = subprocess.run([sys.executable, str(HERE / "tree.py"), "log", "W1", "accept"],
                           cwd=self.root, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.log("raise")), 1)
        self.assertIn("was not merged", self.log("raise")[0]["body"])
        self.assertTrue(self.wt.exists())
        self.assertIn("dfs/W1", self.git("branch", "--list", "dfs/W1"))
        self.assertEqual(dfs_tree.status_of(self.tree())[0], "blocked")

    def test_the_sessions_are_told_their_paths_from_the_worktree(self):
        self.run_chain("work", cap=1)
        self.assertEqual(Path((self.aside / "kinds.cwd").read_text().strip()), self.wt.resolve())

    def test_a_second_chain_reuses_the_branch_and_its_worktree(self):
        self.run_chain("work", cap=1)
        sha = self.git("rev-parse", "dfs/W1")
        rc, meta, out = self.run_chain("code,confirm", cap=2)
        self.assertEqual((rc, meta.get("stop")), (0, "cap"), out)
        self.assertNotIn("existed", out)
        self.assertEqual(self.git("merge-base", sha, "dfs/W1"), sha)
        self.assertEqual(self.git("worktree", "list").count("\n"), 1)

    def test_a_worktree_without_the_task_file_stops_the_chain_with_the_cause(self):
        self.run_chain("work", cap=1)
        (self.wt / ".dfs" / "items" / "W1.md").unlink()
        before = self.count.read_text()
        rc, meta, out = self.run_chain("work", cap=1)
        self.assertEqual((rc, meta.get("stop")), (2, "brief"), out)
        self.assertIn("has no .dfs/items/W1.md, which the main checkout has", meta.get("cause", ""))
        self.assertIn("W1 cannot be briefed: the worktree", out)
        self.assertEqual(self.count.read_text(), before)   # no session started

    def chain_with_briefing(self, mode):
        """Run the chain from a copy of the scripts whose `state.py --brief` first prints a
        notice, then (mode "fail") fails. Returns (rc, meta, output)."""
        copy = self.aside / "scripts-copy"
        shutil.copytree(HERE.parent, copy, ignore=shutil.ignore_patterns("__pycache__", ".git", ".dfs"))
        state = copy / "scripts" / "state.py"
        lines = state.read_text().split("\n")
        lines.insert(1, "import sys as _s\nif '--brief' in _s.argv:\n"
                        "    print('dfs_tree: a notice', file=_s.stderr)\n"
                        "    if %r == 'fail':\n        print('dfs_state: boom', file=_s.stderr); _s.exit(1)" % mode)
        state.write_text("\n".join(lines))
        e = dict(os.environ, CLAUDE_CMD="bash %s" % self.stub, RECIPE="work", COUNT_FILE=str(self.count),
                 KINDS_FILE=str(self.kinds), TREE_PY=str(HERE / "tree.py"), HOME=str(self.aside))
        e.pop("RUNDIR", None)
        p = subprocess.run(["bash", str(copy / "scripts" / "run.sh"), "W1", "1"], cwd=self.root, env=e,
                           capture_output=True, text=True, timeout=300)
        metas = sorted((self.root / ".dfs" / "runs").glob("*/meta.json"), key=lambda f: f.stat().st_mtime)
        return p.returncode, json.loads(metas[-1].read_text()) if metas else {}, p.stdout + p.stderr

    def test_a_briefing_that_fails_is_recorded_with_its_cause_and_its_notice_is_shown(self):
        rc, meta, out = self.chain_with_briefing("fail")
        self.assertEqual((rc, meta.get("stop")), (2, "brief"), out)
        self.assertEqual(meta.get("cause"), "state.py --brief failed: dfs_state: boom")
        self.assertIn("dfs_tree: a notice", out)

    def test_a_notice_the_briefing_prints_on_success_is_shown(self):
        rc, meta, out = self.chain_with_briefing("ok")
        self.assertEqual(rc, 0, out)
        self.assertIn("dfs_tree: a notice", out)

    def test_a_worktree_without_the_task_file_names_the_ignore_rule_that_hides_it(self):
        self.ignore_roadmap(".dfs/items/\n")
        self.enter()
        empty = self.aside / "empty-items"
        empty.mkdir()
        link = self.wt / ".dfs" / "items"
        link.unlink()
        link.symlink_to(empty)   # a link `enter` leaves alone, to a directory without the task file
        rc, meta, out = self.run_chain("work", cap=1)
        self.assertEqual((rc, meta.get("stop")), (2, "brief"), out)
        self.assertIn("and the ignore rule .gitignore:1:.dfs/items/ hides it", meta.get("cause", ""))

    def test_a_task_file_missing_in_its_worktree_is_not_shown_as_an_empty_tree(self):
        self.enter()
        old = dfs_tree.dfs_paths.work_root
        dfs_tree.dfs_paths.work_root = lambda: self.root
        try:
            self.assertEqual(dfs_tree.status_of(dfs_tree.load("W1"))[1], "current node W1.1 · the step")
            (self.wt / ".dfs" / "items" / "W1.md").unlink()
            why = dfs_tree.status_of(dfs_tree.load("W1"))[1]
        finally:
            dfs_tree.dfs_paths.work_root = old
        self.assertIn("its task file is missing in its worktree .dfs/worktrees/W1", why)

    def test_the_task_s_uncommitted_edits_move_to_the_branch_and_main_gets_them_back(self):
        item = self.root / ".dfs" / "items" / "W1.md"
        item.write_text(TASK + "- 2026-01-01T00:00:00Z · note\n")
        (self.root / ".dfs" / "items" / "W1").mkdir()
        (self.root / ".dfs" / "items" / "W1" / "fix.md").write_text("a part\n")
        r = self.enter()
        self.assertEqual(Path(r.stdout.strip()).resolve(), self.wt.resolve())
        self.assertIn("2 uncommitted path(s) of W1 moved", r.stderr)
        self.assertEqual(item.read_text(), TASK)
        self.assertFalse((self.root / ".dfs" / "items" / "W1" / "fix.md").exists())
        self.assertIn("2026-01-01T00:00:00Z · note", (self.wt / ".dfs" / "items" / "W1.md").read_text())
        self.assertEqual((self.wt / ".dfs" / "items" / "W1" / "fix.md").read_text(), "a part\n")
        self.assertEqual(self.git("status", "--porcelain", "--", ".dfs/items"), "")

    def test_an_attachment_named_in_a_task_before_its_first_run_goes_to_the_worktree_and_merges(self):
        name = "0f1e2d3c-4b5a-4968-8776-655443322111.png"
        other = "0f1e2d3c-4b5a-4968-8776-655443322112.png"
        (self.root / ".dfs" / "artefacts").mkdir()
        (self.root / ".dfs" / "artefacts" / name).write_bytes(b"png")
        (self.root / ".dfs" / "artefacts" / other).write_bytes(b"png")
        (self.root / ".dfs" / "items" / "W1.md").write_text(
            TASK.replace("## Goal\n", "## Goal\n\nSee ![x](.dfs/artefacts/%s)\n" % name, 1))
        self.assertIn(name, (self.root / ".dfs" / "items" / "W1.md").read_text())
        self.enter()
        self.assertFalse((self.root / ".dfs" / "artefacts" / name).exists())
        self.assertEqual((self.wt / ".dfs" / "artefacts" / name).read_bytes(), b"png")
        self.assertTrue((self.root / ".dfs" / "artefacts" / other).exists())   # not named: stays
        old = dfs_tree.dfs_paths.work_root
        dfs_tree.dfs_paths.work_root = lambda: self.wt
        try:
            text = "\n".join(f.read_text() for f in dfs_tree.part_files("W1"))
            self.assertEqual(dfs_tree.untracked_artefacts(text), [name])
        finally:
            dfs_tree.dfs_paths.work_root = old

    def test_a_chain_merges_a_task_whose_goal_names_an_attachment_added_before_it_ran(self):
        name = "0f1e2d3c-4b5a-4968-8776-655443322111.png"
        (self.root / ".dfs" / "artefacts").mkdir()
        (self.root / ".dfs" / "artefacts" / name).write_bytes(b"png")
        (self.root / ".dfs" / "items" / "W1.md").write_text(
            TASK.replace("## Goal\n", "## Goal\n\nSee ![x](.dfs/artefacts/%s)\n" % name, 1))
        rc, meta, out = self.run_chain("work,confirm,rok")
        self.assertEqual((rc, meta.get("stop")), (0, "done"), out)
        self.assertEqual(self.git("ls-files", ".dfs/artefacts/" + name), ".dfs/artefacts/" + name)
        self.assertFalse(self.wt.exists())

    def test_a_branch_the_chain_did_not_cut_is_reused_and_main_s_edits_left_alone(self):
        self.git("branch", "dfs/W1")
        item = self.root / ".dfs" / "items" / "W1.md"
        item.write_text(TASK + "- 2026-01-01T00:00:00Z · note\n")
        r = self.enter()
        self.assertIn("existed", r.stderr)
        self.assertIn("· note", item.read_text())

    def test_a_branch_cut_before_the_task_file_was_committed_is_moved_up_to_main(self):
        self.git("branch", "dfs/W1")
        (self.root / ".dfs" / "items" / "W9.md").write_text(TASK.replace("W1", "W9"))
        self.git("add", ".dfs")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "later task")
        r = self.enter()
        self.assertIn("moved up", r.stderr)
        self.assertTrue((self.wt / ".dfs" / "items" / "W9.md").is_file())
        r = self.enter()   # reused worktree, nothing to move
        self.assertNotIn("moved up", r.stderr)

    def test_a_branch_with_commits_of_its_own_is_not_moved(self):
        self.enter()
        (self.wt / "mine.txt").write_text("x\n")
        self.git("add", "mine.txt", cwd=self.wt)
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "mine", cwd=self.wt)
        sha = self.git("rev-parse", "dfs/W1")
        (self.root / "other.txt").write_text("y\n")
        self.git("add", "other.txt")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "main moves")
        self.enter()
        self.assertEqual(self.git("rev-parse", "dfs/W1"), sha)

    def test_an_older_roadmap_that_does_not_ignore_worktrees_does_not_show_them_as_new(self):
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.enter()
        self.assertEqual(self.git("status", "--porcelain"), "")

    def ignore_roadmap(self, rules=".dfs/\n"):
        """Make the repo's roadmap gitignored whole, as a project that keeps its planning out
        of git does: no task file is in any commit."""
        (self.root / ".gitignore").write_text(rules)
        self.git("rm", "-r", "-q", "--cached", "--ignore-unmatch", ".dfs")
        self.git("add", ".gitignore")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "ignore .dfs")

    def test_a_gitignored_roadmap_is_worked_in_a_worktree_that_shares_its_planning_files(self):
        for rules in (".dfs/\n", ".dfs/items/\n.dfs/runs/\n"):
            with self.subTest(rules=rules):
                self.ignore_roadmap(rules)
                r = self.enter()
                self.assertEqual((r.stdout.strip(), self.wt.is_dir()), (str(self.wt), True), r.stderr)
                link = self.wt / ".dfs" / "items"
                self.assertTrue(link.is_symlink())
                self.assertEqual(link.resolve(), (self.root / ".dfs" / "items").resolve())
                self.assertEqual(self.git("status", "--porcelain", cwd=self.wt), "")
                old = dfs_tree.dfs_paths.work_root
                dfs_tree.dfs_paths.work_root = lambda: self.wt
                try:
                    self.assertTrue(dfs_tree.dfs_paths.ignored())
                finally:
                    dfs_tree.dfs_paths.work_root = old
                subprocess.run(["git", "worktree", "remove", "--force", str(self.wt)], cwd=self.root,
                               check=True, capture_output=True)
                self.git("branch", "-D", "dfs/W1")
                self.assertTrue((self.root / ".dfs" / "items" / "W1.md").is_file())   # the links, not the files, went

    def test_a_roadmap_hidden_by_a_blanket_rule_with_some_files_force_added_shares_them_all(self):
        # `.*` hides `.dfs`, and W1.md and order.md were force-added once: the branch carries
        # only those, so the worktree must take the rest, and the current order, from here
        (self.root / ".dfs" / "order.md").write_text("- W1\n")
        (self.root / ".gitignore").write_text(".*\n!.gitignore\n")
        self.git("add", "-f", ".gitignore", ".dfs/order.md")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "ignore dot files")
        (self.root / ".dfs" / "items" / "W2.md").write_text(TASK.replace("W1", "W2"))
        (self.root / ".dfs" / "order.md").write_text("- W1\n- W2\n")
        r = subprocess.run([sys.executable, str(HERE / "branch.py"), "enter", "W2"],
                           cwd=self.root, capture_output=True, text=True)
        wt = self.root / ".dfs" / "worktrees" / "W2"
        self.assertEqual(r.stdout.strip(), str(wt), r.stderr)
        self.assertEqual((wt / ".dfs" / "items" / "W2.md").read_text(), TASK.replace("W1", "W2"))
        self.assertEqual((wt / ".dfs" / "order.md").read_text(), "- W1\n- W2\n")
        self.assertEqual(self.git("status", "--porcelain", cwd=wt), "")
        # the author commits a force-tracked planning file while the item is worked: the
        # rebase at the merge must not stop on the paths the worktree shares
        (self.root / ".dfs" / "items" / "W1.md").write_text(TASK + "\nedited\n")
        self.git("add", "-f", ".dfs/items/W1.md")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "the author's edit")
        (wt / "app.txt").write_text("worked\n")
        self.git("add", "app.txt", cwd=wt)
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "W2.1: the step", cwd=wt)
        r = subprocess.run([sys.executable, str(HERE / "branch.py"), "merge", "W2"], cwd=self.root,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(wt.exists())
        self.assertEqual((self.root / "app.txt").read_text(), "worked\n")
        self.assertTrue((self.root / ".dfs" / "items" / "W2.md").is_file())
        self.assertEqual((self.root / ".dfs" / "order.md").read_text(), "- W1\n- W2\n")
        self.assertIn("edited", (self.root / ".dfs" / "items" / "W1.md").read_text())
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=no"), "M .dfs/order.md")

    def test_entering_a_task_whose_force_tracked_file_has_uncommitted_edits_keeps_the_edits(self):
        # the blanket rule links `items` into the worktree, so the task's own edited file is
        # reached through the link: carrying it over would copy a file onto itself
        (self.root / ".gitignore").write_text(".*\n!.gitignore\n")
        self.git("add", "-f", ".gitignore", ".dfs/items/W1.md")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "ignore dot files")
        task = self.root / ".dfs" / "items" / "W1.md"
        task.write_text(TASK + "\nanswered\n")
        r = subprocess.run([sys.executable, str(HERE / "branch.py"), "enter", "W1"],
                           cwd=self.root, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), str(self.wt))
        self.assertEqual((self.wt / ".dfs" / "items" / "W1.md").read_text(), TASK + "\nanswered\n")
        self.assertEqual(task.read_text(), TASK + "\nanswered\n")

    def test_a_reused_worktree_that_worked_a_force_tracked_task_file_for_real_keeps_the_branchs_version(self):
        # begun before the entry was shared: the worktree holds a real tracked W1.md and the
        # branch committed a step in it, so the main checkout's copy is the stale one
        (self.root / ".gitignore").write_text(".*\n!.gitignore\n")
        self.git("add", "-f", ".gitignore", ".dfs/items/W1.md")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "ignore dot files")
        self.git("worktree", "add", "-q", "-b", "dfs/W1", str(self.wt), "HEAD")
        mine = self.wt / ".dfs" / "items" / "W1.md"
        mine.write_text(TASK + "\nconfirmed in the branch\n")
        self.git("add", "-f", ".dfs/items/W1.md", cwd=self.wt)
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "W1.1: the step", cwd=self.wt)
        mine.write_text(TASK + "\nconfirmed in the branch\nand edited since\n")
        r = self.enter()
        self.assertEqual(mine.read_text(), TASK + "\nconfirmed in the branch\nand edited since\n")
        self.assertEqual((self.root / ".dfs" / "items" / "W1.md").read_text(), mine.read_text())
        self.assertTrue((self.wt / ".dfs" / "items").is_symlink())
        self.assertNotIn("from-worktree", "".join(p.name for p in (self.root / ".dfs" / "items").iterdir()))
        # the merge meets the branch's commit of the file the shared copy now holds
        r = self.merge_by_hand()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.wt.exists())
        self.assertIn("and edited since", (self.root / ".dfs" / "items" / "W1.md").read_text())

    def test_a_reused_worktree_and_the_main_checkout_that_both_changed_a_force_tracked_file_are_left_unshared(self):
        (self.root / ".gitignore").write_text(".*\n!.gitignore\n")
        self.git("add", "-f", ".gitignore", ".dfs/items/W1.md")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "ignore dot files")
        self.git("worktree", "add", "-q", "-b", "dfs/W1", str(self.wt), "HEAD")
        mine = self.wt / ".dfs" / "items" / "W1.md"
        mine.write_text(TASK + "\nin the branch\n")
        self.git("add", "-f", ".dfs/items/W1.md", cwd=self.wt)
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "W1.1: the step", cwd=self.wt)
        (self.root / ".dfs" / "items" / "W1.md").write_text(TASK + "\nhere\n")
        r = self.enter()
        self.assertIn("changed both", r.stderr)
        self.assertEqual(mine.read_text(), TASK + "\nin the branch\n")
        self.assertEqual((self.root / ".dfs" / "items" / "W1.md").read_text(), TASK + "\nhere\n")
        self.assertFalse((self.wt / ".dfs" / "items").is_symlink())

    def force_tracked_worktree_with_a_branch_commit(self, base):
        """The pre-sharing state: a blanket rule, W1.md force-tracked with the text `base`, and a
        worktree that committed `base + "in the branch"` to it for real."""
        (self.root / ".dfs" / "items" / "W1.md").write_bytes(base)
        (self.root / ".gitignore").write_text(".*\n!.gitignore\n")
        self.git("add", "-f", ".gitignore", ".dfs/items/W1.md")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "ignore dot files")
        self.git("worktree", "add", "-q", "-b", "dfs/W1", str(self.wt), "HEAD")
        (self.wt / ".dfs" / "items" / "W1.md").write_bytes(base + b"in the branch\n")
        self.git("add", "-f", ".dfs/items/W1.md", cwd=self.wt)
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "W1.1: the step", cwd=self.wt)

    def test_the_rebase_at_the_merge_does_not_call_the_shared_copy_a_clash_when_main_has_moved(self):
        self.force_tracked_worktree_with_a_branch_commit(TASK.encode())
        self.enter()
        (self.root / ".dfs" / "items" / "W1.md").open("a").write("and edited since\n")   # through the link
        (self.root / "other.txt").write_text("main's own file\n")
        self.git("add", "other.txt")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "main moves")
        r = self.merge_by_hand()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("changed both", r.stderr)
        self.assertFalse(self.wt.exists())
        self.assertIn("and edited since", (self.root / ".dfs" / "items" / "W1.md").read_text())

    def test_a_refused_merge_keeps_the_raise_it_writes_into_the_set_aside_task_file(self):
        # the shared task file is set aside around the merge and written back after it: the
        # raise for the refused merge is written into it, so it must be put back first
        self.force_tracked_worktree_with_a_branch_commit(TASK.encode())
        (self.wt / "app.txt").write_text("worked\n")
        self.git("add", "app.txt", cwd=self.wt)
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "W1.2: app", cwd=self.wt)
        self.enter()
        task = self.root / ".dfs" / "items" / "W1.md"
        with task.open("a") as f:
            f.write("- 2026-10-19T09:00:00Z · session · work · edited since\n")   # through the link
        (self.root / "app.txt").write_text("the author's edit\n")
        r = self.merge_by_hand()
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIn("raised for the author", r.stderr + r.stdout)
        text = task.read_text()
        self.assertIn("edited since", text)
        self.assertIn("raise", text)
        self.assertIn("app.txt", text)

    def test_a_crlf_task_file_the_main_checkout_never_changed_is_not_taken_for_a_clash(self):
        self.force_tracked_worktree_with_a_branch_commit(TASK.replace("\n", "\r\n").encode())
        r = self.enter()
        self.assertNotIn("changed both", r.stderr)
        self.assertTrue((self.wt / ".dfs" / "items").is_symlink())
        self.assertIn(b"in the branch", (self.root / ".dfs" / "items" / "W1.md").read_bytes())

    def test_a_merge_does_not_write_the_authors_edit_over_the_branchs_change_to_a_tracked_roadmap_file(self):
        # `.dfs` is tracked whole: the worktree's `order.md` is its own, so an edit of the
        # author's here was not made on the branch's version, and the merge is theirs to settle
        (self.root / ".dfs" / "order.md").write_text("- W1\n")
        self.git("add", ".dfs/order.md")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "order")
        self.enter()
        (self.wt / ".dfs" / "order.md").write_text("- W1\n- branch\n")
        self.git("add", ".dfs/order.md", cwd=self.wt)
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "W1.1: order", cwd=self.wt)
        (self.root / ".dfs" / "order.md").write_text("- W1\n- author\n")
        head = self.git("rev-parse", "HEAD")
        r = self.merge_by_hand()
        self.assertEqual(self.git("rev-parse", "HEAD"), head, r.stderr)
        self.assertEqual((self.root / ".dfs" / "order.md").read_text(), "- W1\n- author\n")
        self.assertTrue(self.wt.exists())
        self.assertFalse((self.root / ".git" / "MERGE_HEAD").exists())

    def test_a_merge_does_not_write_a_clashing_edit_over_the_branchs_committed_force_tracked_file(self):
        (self.root / ".gitignore").write_text(".*\n!.gitignore\n")
        self.git("add", "-f", ".gitignore", ".dfs/items/W1.md")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "ignore dot files")
        self.git("worktree", "add", "-q", "-b", "dfs/W1", str(self.wt), "HEAD")
        mine = self.wt / ".dfs" / "items" / "W1.md"
        mine.write_text(TASK + "\nin the branch\n")
        self.git("add", "-f", ".dfs/items/W1.md", cwd=self.wt)
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "W1.1: the step", cwd=self.wt)
        (self.root / ".dfs" / "items" / "W1.md").write_text(TASK + "\nhere\n")
        self.enter()   # both changed it: left unshared
        r = self.merge_by_hand()
        self.assertEqual((self.root / ".dfs" / "items" / "W1.md").read_text(), TASK + "\nhere\n", r.stderr)
        self.assertEqual(self.git("show", "dfs/W1:.dfs/items/W1.md"), (TASK + "\nin the branch\n").strip())
        self.assertTrue(self.wt.exists())

    def merge_by_hand(self):
        return subprocess.run([sys.executable, str(HERE / "branch.py"), "merge", "W1"], cwd=self.root,
                              capture_output=True, text=True)

    def commit_in_worktree(self):
        (self.wt / "app.txt").write_text("worked\n")
        self.git("add", "app.txt", cwd=self.wt)
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "W1.1: the step", cwd=self.wt)

    def test_the_first_archive_and_page_written_in_a_gitignored_worktree_survive_the_merge(self):
        # no `.dfs/archive` or `.dfs/artefacts` exists yet when the worktree is cut
        self.ignore_roadmap()
        self.assertFalse((self.root / ".dfs" / "archive").exists())
        self.enter()
        for name in ("items", "archive", "artefacts"):
            self.assertTrue((self.wt / ".dfs" / name).is_symlink(), name)
        (self.wt / ".dfs" / "archive" / "items").mkdir()
        (self.wt / ".dfs" / "archive" / "items" / "W1.md").write_text("**W1.1 Approach**: shelved\n")
        (self.wt / ".dfs" / "artefacts" / "page.html").write_text("<p>page</p>")
        self.commit_in_worktree()
        r = self.merge_by_hand()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("merged into", r.stderr)
        self.assertFalse(self.wt.exists())
        self.assertIn("shelved", (self.root / ".dfs" / "archive" / "items" / "W1.md").read_text())
        self.assertEqual((self.root / ".dfs" / "artefacts" / "page.html").read_text(), "<p>page</p>")

    def test_a_shelved_archive_is_committed_when_only_the_items_directory_is_ignored(self):
        # `.dfs/items/` ignored, `.dfs/archive` tracked: the task file is shared, the archive is the branch's
        self.ignore_roadmap(".dfs/items/\n.dfs/runs/\n")
        self.enter()
        self.assertTrue((self.wt / ".dfs" / "items").is_symlink())
        self.assertFalse((self.wt / ".dfs" / "archive").is_symlink())
        (self.wt / ".dfs" / "archive" / "items").mkdir(parents=True)
        (self.wt / ".dfs" / "archive" / "items" / "W1.md").write_text("**W1.1 Approach**: shelved\n")
        self.commit_in_worktree()
        r = self.merge_by_hand()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("merged into", r.stderr)
        self.assertFalse(self.wt.exists())
        self.assertEqual(self.git("ls-files", ".dfs/archive/items/W1.md"), ".dfs/archive/items/W1.md")
        self.assertIn("shelved", (self.root / ".dfs" / "archive" / "items" / "W1.md").read_text())
        self.assertNotIn("archive", self.git("status", "--porcelain"))

    def test_ignored_planning_files_a_gitignored_worktree_holds_for_real_come_back_before_it_goes(self):
        self.ignore_roadmap()
        self.enter()
        # an entry nothing linked, made in the worktree after it was cut, and a real
        # directory left by a worktree cut before `share_ignored_state` made the shared ones
        (self.wt / ".dfs" / "notes").mkdir()
        (self.wt / ".dfs" / "notes" / "n.md").write_text("a note\n")
        (self.wt / ".dfs" / "archive").unlink(missing_ok=True)
        (self.wt / ".dfs" / "archive" / "items").mkdir(parents=True)
        (self.wt / ".dfs" / "archive" / "items" / "W1.md").write_text("shelved\n")
        self.enter()
        self.assertTrue((self.wt / ".dfs" / "archive").is_symlink())
        self.assertEqual((self.root / ".dfs" / "archive" / "items" / "W1.md").read_text(), "shelved\n")
        self.commit_in_worktree()
        r = self.merge_by_hand()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("merged into", r.stderr)
        self.assertEqual((self.root / ".dfs" / "notes" / "n.md").read_text(), "a note\n")

    def test_an_ignored_file_at_the_top_of_a_gitignored_worktree_comes_back_and_a_clash_is_named(self):
        self.ignore_roadmap()
        (self.root / ".dfs" / "archive" / "items").mkdir(parents=True)
        (self.root / ".dfs" / "archive" / "items" / "W1.md").write_text("main's\n")
        self.enter()
        (self.wt / ".dfs" / "memo.txt").write_text("a memo\n")
        (self.wt / ".dfs" / "archive").unlink()
        (self.wt / ".dfs" / "archive" / "items").mkdir(parents=True)
        (self.wt / ".dfs" / "archive" / "items" / "W1.md").write_text("worktree's\n")
        self.commit_in_worktree()
        r = self.merge_by_hand()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.root / ".dfs" / "memo.txt").read_text(), "a memo\n")
        self.assertEqual((self.root / ".dfs" / "archive" / "items" / "W1.md").read_text(), "main's\n")
        self.assertEqual((self.root / ".dfs" / "archive" / "items" / "W1.md.from-worktree").read_text(),
                         "worktree's\n")
        self.assertIn("W1.md.from-worktree", r.stderr)

    def test_a_chain_on_a_gitignored_roadmap_works_in_the_worktree_and_merges_the_code(self):
        self.ignore_roadmap()
        rc, meta, out = self.run_chain("work,fix,confirm,rok")
        self.assertEqual((rc, meta.get("stop")), (0, "done"), out)
        self.assertEqual(Path((self.kinds.parent / "kinds.cwd").read_text().strip()), self.wt.resolve())
        self.assertFalse(self.wt.exists())
        self.assertEqual(self.git("branch", "--list", "dfs/*"), "")
        self.assertIn("W1: merge the item's branch dfs/W1", self.git("log", "--format=%s", "-3"))
        self.assertIn("fixed", (self.root / "app.txt").read_text())
        text = (self.root / ".dfs" / "items" / "W1.md").read_text()
        self.assertIn("Status: confirmed", text)
        self.assertIn("· review · ok", text)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_a_chain_on_another_branch_stays_where_it_is(self):
        self.git("switch", "-q", "-c", "alt")
        rc, meta, out = self.run_chain("work", cap=1)
        self.assertNotIn("is worked on branch", out)
        self.assertFalse(self.wt.exists())
        self.assertIn("look 1", (self.root / ".dfs" / "items" / "W1.md").read_text())

    def test_a_draft_run_by_hand_is_worked_to_done_like_any_open_task(self):
        # `run.sh W1` is the author taking the draft: the runner has no draft stop.
        subprocess.run([sys.executable, str(HERE / "tree.py"), "log", "W1", "draft"],
                       cwd=self.root, check=True, capture_output=True, stdin=subprocess.DEVNULL)
        self.assertTrue(dfs_tree.is_draft(self.tree()))
        rc, meta, out = self.run_chain("work,confirm,rok")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.kinds_run(), ["work", "work", "review"])
        self.assertEqual(dfs_tree.status_of(self.tree())[0], "done")

    def models_run(self):
        f = Path(str(self.kinds) + ".models")
        return [l.strip() for l in f.read_text().splitlines()]

    def test_opus_plans_and_sonnet_implements(self):
        # W1 has a node, so its work sessions implement; the critic judges the tree.
        self.run_chain("work,work,work,work,work,ok,work", cap=7)
        self.assertEqual(self.models_run(),
                         ["work sonnet"] * 5 + ["critic opus", "work sonnet"])

    def test_the_work_session_that_opens_an_empty_tree_plans_on_opus(self):
        item = self.root / ".dfs" / "items" / "W1.md"
        item.write_text(TASK.split("### W1.1")[0] + "## Log\n")
        subprocess.run("git add -A && git -c user.name=t -c user.email=t@t commit -qm empty",
                       shell=True, cwd=self.root, check=True)
        self.run_chain("nothing", cap=1)
        self.assertEqual(self.models_run(), ["work opus"])

    def test_the_review_plans_on_opus_and_the_models_can_be_set_or_dropped(self):
        self.run_chain("work,confirm,rok", PLAN_MODEL="fable", IMPLEMENT_MODEL="")
        self.assertEqual(self.models_run(), ["work", "work", "review fable"])

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
        self.enter()
        (self.wt / "app.txt").write_text("the author's edit\n")
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
        (self.wt / ".dfs" / "items" / "W2.md").write_text(
            TASK.replace("W1", "W2") + "\n- 2099-01-01T00:00:00Z · session · work\n")
        (self.wt / "app.txt").write_text("W2's half-done edit\n")
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

    def test_a_critic_that_changes_code_is_noted_and_the_chain_carries_on(self):
        # The tree cannot say whether the critic or the author (a chat session) made
        # the change, so it is left for the next session to commit, not a stop.
        rc, meta, out = self.run_chain("work,work,work,work,work,meddle,work", cap=7)
        self.assertEqual((rc, meta.get("stop")), (0, "cap"), out)
        self.assertEqual(self.kinds_run()[-2:], ["critic", "work"], out)
        self.assertIn("changed outside .dfs/", out)

    def test_a_critic_that_changed_nothing_does_not_stop_the_chain(self):
        rc, meta, out = self.run_chain("work,work,work,work,work,silent,work,work", cap=8)
        self.assertEqual(self.kinds_run(), ["work"] * 5 + ["critic", "work", "work"], out)
        self.assertEqual(meta.get("stop"), "cap", out)
        self.assertIn("Carrying on", out)

    def test_a_review_that_is_silent_twice_is_idle_not_failed(self):
        rc, meta, out = self.run_chain("confirm,silent,silent", cap=6)
        self.assertEqual((rc, meta.get("stop")), (1, "idle"), out)
        self.assertEqual(self.kinds_run(), ["work", "review", "review"], out)

    def test_an_ok_that_names_no_node_is_not_accepted_and_carries_on(self):
        rc, meta, out = self.run_chain("work,work,work,work,work,okbare,work,work", cap=8)
        self.assertEqual(self.kinds_run(), ["work"] * 5 + ["critic", "work", "work"], out)
        self.assertEqual(meta.get("stop"), "cap", out)
        self.assertIn("ok was not accepted", out)
        self.assertIn("names no node", out)

    def test_an_ok_refused_twice_with_none_accepted_between_is_idle(self):
        rc, meta, out = self.run_chain(
            "work,work,work,work,work,okbare,work,work,work,work,work,okbare", cap=14)
        self.assertEqual((rc, meta.get("stop")), (1, "idle"), out)
        self.assertIn("refused twice with none accepted between", out)

    def test_a_critic_that_wrote_to_the_task_without_a_verdict_still_fails(self):
        rc, meta, out = self.run_chain("work,work,work,work,work,noverdict", cap=7)
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

    def test_a_review_that_changes_code_is_noted_and_its_verdict_stands(self):
        # As for the critic: the change may be the author's, so it is noted, not a stop.
        # The verdict stands, but a branch with the review's change uncommitted does not
        # merge: the item reopens with a node, and the next session commits it.
        rc, meta, out = self.run_chain("confirm,rmeddle,fixn,rok", cap=4)
        self.assertEqual((rc, meta.get("stop")), (0, "done"), out)
        self.assertIn("changed outside .dfs/", out)
        self.assertIn("left for the next session to commit", (self.root / ".dfs" / "items" / "W1.md").read_text())
        self.assertIn("reviewer was here", (self.root / "app.txt").read_text())
        self.assertEqual(self.git("log", "--merges", "--format=%s"), "W1: merge the item's branch dfs/W1")

    def test_a_review_that_changes_code_and_gives_no_verdict_still_fails_the_chain(self):
        rc, meta, out = self.run_chain("confirm,code", cap=3)
        self.assertEqual((rc, meta.get("stop")), (1, "failed"), out)
        self.assertIn("gave no verdict", out)

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

    def test_review_rounds_that_did_not_settle_never_raise_by_their_count(self):
        # Four rounds in a row found issues; a fifth review runs all the same (the fifth work
        # session makes the critic due, and it runs first), and its ok finishes the task.
        rc, meta, out = self.run_chain("confirm" + ",rnode,fixn" * 4 + ",ok,rok", cap=12)
        self.assertEqual(self.kinds_run(),
                         ["work"] + ["review", "work"] * 4 + ["critic", "review"], out)
        self.assertEqual((rc, meta.get("stop")), (0, "done"), out)
        self.assertEqual(dfs_tree.open_raises(self.tree()), [], out)

    def test_a_review_loop_is_stopped_by_the_session_limit(self):
        rc, meta, out = self.run_chain("confirm" + ",rnode,fixn" * 5, cap=12,
                                       DFS_SESSION_LIMIT="3", DFS_CRITIC_EVERY="50")
        raises = dfs_tree.open_raises(self.tree())
        self.assertEqual((rc, meta.get("stop")), (0, "blocked"), out)
        self.assertEqual(len(raises), 1, out)
        self.assertIn("work sessions", raises[0]["body"])

    def test_a_second_review_that_comes_back_ok_finishes_the_task(self):
        rc, meta, out = self.run_chain("confirm,rnode,fixn,rminor", cap=8)
        self.assertEqual(self.kinds_run(), ["work", "review", "work", "review"], out)
        self.assertEqual((rc, meta.get("stop")), (0, "done"), out)
        self.assertEqual(self.log("review")[-1]["args"], ["ok"])

    def test_the_authors_word_starts_the_review_rounds_over(self):
        self.run_chain("confirm" + ",rnode,fixn" * 4, cap=12)
        t = self.tree()
        self.assertEqual(dfs_tree.review_rounds_unsettled(t), 4)
        old = dfs_tree.dfs_paths.work_root
        dfs_tree.dfs_paths.work_root = lambda: self.root
        try:
            dfs_tree.append_log("W1", "accept")
            self.assertEqual(dfs_tree.review_rounds_unsettled(dfs_tree.load("W1")), 0)
        finally:
            dfs_tree.dfs_paths.work_root = old

    def test_the_review_brief_holds_the_bar_and_names_its_round(self):
        import state as dfs_state

        def brief():
            old = dfs_tree.dfs_paths.work_root
            dfs_tree.dfs_paths.work_root = lambda: self.root
            try:
                return dfs_state.review_brief("W1")
            finally:
                dfs_tree.dfs_paths.work_root = old

        self.run_chain("confirm,rnode,fixn", cap=3)
        b = brief()
        self.assertIn("only if it shows the Goal is not met", b)
        self.assertIn("never a node of its own", b)
        self.assertIn("This is review round 2", b)
        self.assertNotIn("raised to the author", b)
        # Later rounds are named too, and none warns of a raise.
        self.count.write_text("0")
        self.kinds.write_text("")
        self.run_chain("rnode,fixn,rnode,fixn", cap=4)
        b = brief()
        self.assertIn("This is review round 4", b)
        self.assertNotIn("raised to the author", b)

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
