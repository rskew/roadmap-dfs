#!/usr/bin/env python3
"""replay.py: a task's starting point is rebuilt from git, and a replay is told apart from history."""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import history  # noqa: E402
import replay  # noqa: E402

TASK_FILE = """# W5 · Make the thing

## Summary

Added by the finishing session.

## Goal

Make the thing.

## Background

The author's note.

## Tree

### W5.1 · Do it
Status: confirmed

## Log

- 2026-10-01T00:00:00Z · session · work · x
"""


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd,
                          check=True, capture_output=True, text=True).stdout.strip()


can_exec = replay.hooks_can_run   # the real probe, kept before any test patches replay's


class StartingPointTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        if not can_exec(self.tmp.name):   # /tmp is often noexec: the other tests do not need hooks
            patch = mock.patch.object(replay, "hooks_can_run", return_value=True)
            patch.start()
            self.addCleanup(patch.stop)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "code.py").write_text("v1\n")
        (self.repo / ".dfs" / "items").mkdir(parents=True)
        (self.repo / ".dfs" / "ROADMAP.md").write_text("law\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "before the task")
        self.start = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "code.py").write_text("v2\n")
        (self.repo / ".dfs" / "items" / "W5.md").write_text(TASK_FILE)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "W5.1: do it")
        (self.repo / "code.py").write_text("v3\n")
        git(self.repo, "commit", "-qam", "W5.2: more")

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_author_part_keeps_goal_and_background_only(self):
        text = replay.author_part(TASK_FILE)
        self.assertIn("## Goal\n\nMake the thing.", text)
        self.assertIn("## Background", text)
        for gone in ("Summary", "## Tree", "## Log", "W5.1"):
            self.assertNotIn(gone, text)

    def test_the_starting_point_is_the_commit_before_the_first_node(self):
        sha, text = replay.starting_point(self.repo, "W5")
        self.assertEqual(sha, self.start)
        self.assertTrue(text.startswith("# W5 · Make the thing"))
        self.assertNotIn("Tree", text)

    def test_a_task_with_no_node_commit_is_refused(self):
        with self.assertRaises(SystemExit):
            replay.starting_point(self.repo, "W9")

    def test_prepare_clones_the_old_code_with_the_goal_alone_and_a_clean_tree(self):
        dest = Path(self.tmp.name) / "dfs-replay-W5-a"
        out, tool = replay.prepare(self.repo, "W5", dest, tool=Path(__file__).resolve().parent.parent)
        self.assertEqual((out / "code.py").read_text(), "v1\n")
        self.assertNotIn("## Tree", (out / ".dfs" / "items" / "W5.md").read_text())
        self.assertEqual(git(out, "status", "--porcelain"), "")
        self.assertTrue((out / ".git" / "hooks" / "pre-commit").exists())
        self.assertEqual(git(out, "config", "user.email"), "t@t")   # the checkout's identity
        self.assertEqual(json.loads((out / ".git" / "replay.json").read_text())["from"], self.start)
        with self.assertRaises(SystemExit):   # a replay starts from nothing
            replay.prepare(self.repo, "W5", dest)

    def test_a_dest_where_hooks_cannot_run_is_refused_before_cloning(self):
        dest = Path(self.tmp.name) / "dfs-replay-W5-noexec"
        with mock.patch.object(replay, "hooks_can_run", return_value=False):
            with self.assertRaises(SystemExit) as e:
                replay.prepare(self.repo, "W5", dest)
        self.assertIn("noexec", str(e.exception))
        self.assertFalse(dest.exists())

    def test_the_clone_holds_nothing_of_the_originals_later_work(self):
        git(self.repo, "tag", "later")   # a tag on a commit after the starting point
        out, _ = replay.prepare(self.repo, "W5", Path(self.tmp.name) / "dfs-replay-W5-c",
                                tool=Path(__file__).resolve().parent.parent)
        self.assertEqual(git(out, "remote"), "")
        self.assertEqual(git(out, "tag", "--list"), "")
        self.assertEqual(git(out, "branch", "--all", "--format=%(refname)"), "refs/heads/main")
        later = git(self.repo, "rev-parse", "HEAD")
        self.assertNotEqual(subprocess.run(["git", "cat-file", "-e", later], cwd=out).returncode, 0)

    def test_a_source_whose_branch_is_not_main_leaves_no_branch_holding_the_later_work(self):
        git(self.repo, "branch", "-m", "trunk")
        out, _ = replay.prepare(self.repo, "W5", Path(self.tmp.name) / "dfs-replay-W5-t",
                                tool=Path(__file__).resolve().parent.parent)
        self.assertEqual(git(out, "branch", "--all", "--format=%(refname)"), "refs/heads/main")
        self.assertEqual(git(out, "log", "--all", "--format=%s", "--grep=W5.2"), "")

    def test_the_guard_refuses_a_commit_in_a_prepared_clone(self):
        if not can_exec(self.tmp.name):
            self.skipTest("this temp dir is noexec, so git would skip the hook; set TMPDIR to one that is not")
        out, _ = replay.prepare(self.repo, "W5", Path(self.tmp.name) / "dfs-replay-W5-d",
                                tool=Path(__file__).resolve().parent.parent)
        item = out / ".dfs" / "items" / "W5.md"
        # a Status that is not open | parked | confirmed | refuted is a shape fault the guard refuses
        item.write_text(item.read_text() + "\n## Tree\n\n### W5.1 · Do it\nStatus: done\nApproach: Do the thing in code.py.\n")
        git(out, "add", ".dfs")
        r = subprocess.run(["git", "commit", "-qm", "did it"], cwd=out, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_dest_not_named_as_a_replay_is_refused(self):
        with self.assertRaises(SystemExit):
            replay.prepare(self.repo, "W5", Path(self.tmp.name) / "scratch")

    def test_law_replaces_the_starting_commits_law(self):
        law = Path(self.tmp.name) / "law.md"
        law.write_text("a changed law\n")
        out, _ = replay.prepare(self.repo, "W5", Path(self.tmp.name) / "dfs-replay-W5-b",
                                tool=Path(__file__).resolve().parent.parent, law=law)
        self.assertEqual((out / ".dfs" / "ROADMAP.md").read_text(), "a changed law\n")


class ChangedToolTest(unittest.TestCase):
    """The changed arm: `--tool-ref REV` runs a replay under the tool as REV had it."""
    ROOT = Path(__file__).resolve().parent.parent

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        if not can_exec(self.tmp.name):
            patch = mock.patch.object(replay, "hooks_can_run", return_value=True)
            patch.start()
            self.addCleanup(patch.stop)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / ".dfs" / "items").mkdir(parents=True)
        (self.repo / ".dfs" / "ROADMAP.md").write_text("law\n")
        # The repo is a roadmap-dfs of its own: this checkout's tool files (not the tests or
        # fixtures), as they are at the commit the task starts from.
        files = git(self.ROOT, "ls-files", "SKILL.md", "scripts", "templates").split("\n")
        for rel in files:
            if "fixtures" in rel or Path(rel).name.startswith("test_") or not (self.ROOT / rel).is_file():
                continue
            (self.repo / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(self.ROOT / rel, self.repo / rel)
        (self.repo / "SKILL.md").write_text("# skill\n\nTHE OLD LINE\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "before the task")
        self.old = git(self.repo, "rev-parse", "HEAD")
        (self.repo / ".dfs" / "items" / "W5.md").write_text(TASK_FILE)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "W5.1: do it")
        # a later revision whose SKILL.md differs: the change an arm would carry
        (self.repo / "SKILL.md").write_text("# skill\n\nTHE CHANGED LINE\n")
        git(self.repo, "commit", "-qam", "W5.2: change the skill")
        self.changed = git(self.repo, "rev-parse", "HEAD")

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_tool_is_the_exported_revision_not_the_checkout_and_the_hooks_name_it(self):
        out, tool = replay.prepare(self.repo, "W5", Path(self.tmp.name) / "dfs-replay-W5-x", tool_ref=self.changed)
        self.assertEqual(tool, Path(self.tmp.name) / "dfs-replay-W5-x-tool")
        self.assertIn("THE CHANGED LINE", (tool / "SKILL.md").read_text())
        self.assertNotIn("THE OLD LINE", (tool / "SKILL.md").read_text())
        self.assertEqual(json.loads((out / ".git" / "replay.json").read_text())["tool"], str(tool))
        self.assertTrue((tool / "scripts" / "hook.sh").is_file())
        # init.py ran from the exported tool, so the hook the clone commits under is the REV's guard
        hook = (out / ".git" / "hooks" / "pre-commit").read_text()
        self.assertIn('dfs_tool="${DFS_TOOL:-%s}"' % tool, hook)

    def test_an_earlier_revision_exports_the_earlier_text(self):
        _, tool = replay.prepare(self.repo, "W5", Path(self.tmp.name) / "dfs-replay-W5-y", tool_ref=self.old)
        self.assertIn("THE OLD LINE", (tool / "SKILL.md").read_text())

    def test_without_a_tool_ref_the_arm_is_the_given_tool_and_nothing_is_exported(self):
        out, tool = replay.prepare(self.repo, "W5", Path(self.tmp.name) / "dfs-replay-W5-z", tool=self.ROOT)
        self.assertEqual(tool, self.ROOT)
        self.assertFalse((Path(self.tmp.name) / "dfs-replay-W5-z-tool").exists())

    def test_an_unknown_revision_fails_instead_of_running_the_current_tool(self):
        with self.assertRaises(subprocess.CalledProcessError):
            replay.prepare(self.repo, "W5", Path(self.tmp.name) / "dfs-replay-W5-bad", tool_ref="no-such-rev")

    def test_run_launches_the_chain_from_the_prepared_tool_inside_the_sandbox(self):
        out, tool = replay.prepare(self.repo, "W5", Path(self.tmp.name) / "dfs-replay-W5-r", tool_ref=self.changed)
        seen = {}
        bins = {"proot": "/nix/store/p/bin/proot", "claude": "/nix/store/c/bin/claude",
                "bash": "/nix/store/b/bin/bash", "env": "/nix/store/e/bin/env"}

        class Fake:
            returncode = 0
            stdout = io.StringIO("dfs_run: DONE\n")
            def __init__(self, cmd, **kw):
                seen["cmd"], seen["env"], seen["cwd"] = cmd, kw["env"], kw["cwd"]
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        with mock.patch.object(replay.subprocess, "Popen", Fake), mock.patch.object(replay, "tool_bins", return_value=bins), \
                mock.patch.object(replay, "store_closure", return_value=["/nix/store/c", "/nix/store/p"]), \
                mock.patch.object(replay, "config_dir", return_value=str(Path(self.tmp.name) / "no-config")), \
                redirect_stdout(io.StringIO()):
            replay.run(out, "W5", cap=3, repo=self.repo)
        cmd = seen["cmd"]
        self.assertEqual(cmd[0], "/nix/store/p/bin/proot")
        self.assertNotIn("/nix", [cmd[i + 1] for i, c in enumerate(cmd) if c == "-b"])   # not the whole store
        self.assertIn("/nix/store/c", cmd)
        self.assertEqual(cmd[cmd.index("-r") + 1], str(replay.box_of(out) / "root"))
        self.assertEqual(cmd[-4:], ["bash", str(tool / "scripts" / "run.sh"), "W5", "3"])
        self.assertIn("DFS_TOOL=" + str(tool), cmd)
        self.assertIn("CLAUDE_CMD=/nix/store/c/bin/claude", cmd)
        self.assertEqual(seen["env"]["PROOT_TMP_DIR"], str(replay.box_of(out) / "tmp"))
        self.assertEqual(Path(seen["cwd"]), out)


class SandboxTest(unittest.TestCase):
    """The replay's sessions run in proot: their own clone and a fresh home, not the live checkout."""
    ROOT = Path(__file__).resolve().parent.parent

    def setUp(self):
        base = self.ROOT / ".dfs" / "runs"   # ignored, and inside the checkout: the one mount that executes scripts here
        base.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=base, prefix="sbx-")
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.live = t / "live"                       # stands for the live checkout, outside the replay
        (self.live / ".dfs" / "runs").mkdir(parents=True)
        (self.live / ".dfs" / "runs" / "secret.txt").write_text("the original's evidence\n")
        self.dest = t / "dfs-replay-W5-s"
        self.dest.mkdir()
        (self.dest / "in-the-clone.txt").write_text("mine\n")
        self.tool = t / "tool"                       # a checkout used as the tool: its .dfs must stay out
        for d in ("scripts", ".dfs/items"):
            (self.tool / d).mkdir(parents=True)
        (self.tool / ".dfs" / "items" / "W5.md").write_text("the original's task file\n")
        (self.tool / "scripts" / "run.sh").write_text("# the tool\n")
        (self.tool / "SKILL.md").write_text("skill\n")
        self.cfg = t / "cfg"
        self.cfg.mkdir()
        (self.cfg / ".credentials.json").write_text('{"claudeAiOauth": {}}')
        (self.cfg / "settings.json").write_text("{}")
        (self.cfg / "projects" / "-live-session").mkdir(parents=True)
        (self.cfg / ".claude.json").write_text(json.dumps(
            {"oauthAccount": {"email": "a@b.invalid"}, "userID": "u", "projects": {str(self.live): {}},
             "cachedGrowthBookFeatures": {"x": 1}}))

    def bins(self):
        try:
            proot = replay.nix_bin("proot", "proot", os.environ.get("REPLAY_PROOT") or shutil.which("proot"), self.ROOT)
        except (SystemExit, OSError, subprocess.SubprocessError):
            self.skipTest("no proot here (set REPLAY_PROOT, or have nix and an agent pin)")
        if not can_exec(self.tmp.name):
            self.skipTest("scripts cannot run under this directory")
        return {"proot": proot, "claude": "/nix/store/none/bin/claude", "bash": shutil.which("bash"), "env": shutil.which("env")}

    def test_the_sessions_see_their_clone_and_the_tools_entries_and_nothing_of_the_live_checkout(self):
        bins = self.bins()
        with mock.patch.object(replay, "config_dir", return_value=str(self.cfg)):
            replay.make_box(self.dest)
        script = f"""
echo "[parent]"; ls {self.tmp.name}
echo "[dest]"; ls {self.dest}
echo "[tool]"; ls -A {self.tool}
echo "[live]"; ls {self.live} 2>&1; cat {self.live}/.dfs/runs/secret.txt 2>&1
echo "[home]"; ls -A $HOME; ls -A $HOME/.claude; cat $HOME/.claude.json
echo "[env]"; env
echo "[workspace]"; ls /workspace 2>&1; ls /tmp"""
        env = replay.sandbox_env(self.tool, bins)
        argv = replay.sandbox_argv(self.dest, self.tool, ["sh", "-c", script], bins, env)
        with mock.patch.dict(os.environ, {"DFS_RUNS": str(self.live / ".dfs" / "runs"), "XDG_CONFIG_HOME": "/home/live/.config",
                                          "CLAUDE_CONFIG_DIR": str(self.cfg)}):
            r = subprocess.run(argv, cwd=self.dest, env={"PROOT_TMP_DIR": str(replay.box_of(self.dest) / "tmp"),
                                                         "PATH": os.environ["PATH"]},
                               capture_output=True, text=True, timeout=120)
        out = r.stdout
        self.assertEqual(r.returncode, 0, r.stderr)
        part = lambda name: out.split(f"[{name}]\n")[1].split("\n[")[0]
        self.assertEqual(sorted(part("parent").split()), ["dfs-replay-W5-s", "tool"])   # the live checkout is not there
        self.assertIn("in-the-clone.txt", part("dest"))
        self.assertEqual(sorted(part("tool").split()), ["SKILL.md", "scripts"])         # its .dfs/items is not
        self.assertNotIn("original's evidence", out)
        self.assertNotIn("original's task file", out)
        self.assertIn("No such file", part("live"))
        home = part("home")
        self.assertIn(".credentials.json", home)
        self.assertNotIn("projects", home)                                              # no earlier session's transcript
        self.assertIn("a@b.invalid", home)
        self.assertNotIn("cachedGrowthBookFeatures", home)                               # only the sign-in keys
        env_out = part("env")
        for leaked in ("DFS_RUNS", "XDG_CONFIG_HOME", "CLAUDE_CONFIG_DIR", str(self.live)):
            self.assertNotIn(leaked, env_out)
        self.assertIn("HOME=/home/replay", env_out)
        self.assertIn("DFS_TOOL=" + str(self.tool), env_out)

    def sandbox_out(self, script, bins):
        env = replay.sandbox_env(self.tool, bins)
        argv = replay.sandbox_argv(self.dest, self.tool, ["sh", "-c", script], bins, env,
                                   docs=replay.tool_docs(self.dest, self.tool))
        r = subprocess.run(argv, cwd=self.dest, env={"PROOT_TMP_DIR": str(replay.box_of(self.dest) / "tmp"),
                                                     "PATH": os.environ["PATH"]},
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_the_store_is_not_bound_whole_so_the_flake_sources_with_task_files_are_not_there(self):
        bins = self.bins()
        with mock.patch.object(replay, "config_dir", return_value=str(self.cfg)):
            replay.make_box(self.dest)
        host_sources = sorted(Path("/nix/store").glob("*-source"))[:3]
        if not host_sources:
            self.skipTest("this store holds no *-source directory to hide")
        out = self.sandbox_out("echo '[sources]'; ls -d /nix/store/*-source 2>&1; echo '[count]'; ls /nix/store | wc -l; "
                               "echo '[items]'; find /nix/store -maxdepth 4 -path '*/.dfs/items/*' 2>/dev/null | head -3", bins)
        part = lambda name: out.split(f"[{name}]\n")[1].split("\n[")[0]
        self.assertIn("No such file", part("sources"))
        for source in host_sources:
            self.assertNotIn(source.name, out)
        self.assertEqual(part("items").strip(), "")
        needed = replay.store_closure([*bins.values(), *replay.sandbox_env(self.tool, bins)["PATH"].split(":")])
        self.assertLessEqual(int(part("count")), len(needed))        # the closure and no more
        self.assertLess(len(needed), 1000)                           # a bind each: it stays a short list

    def test_the_tools_docs_in_the_sandbox_hold_every_doc_but_the_findings_of_other_tasks(self):
        """docs/dfs-review-findings.tsv holds the original's first-review defects; a tool bound
        with its docs whole (a live checkout's entries, or a tool in the store) shows them. A doc a
        changed tool adds (its SKILL.md points at it) is there, a `dfs-*.bak` copy is not."""
        bins = self.bins()
        with mock.patch.object(replay, "config_dir", return_value=str(self.cfg)):
            replay.make_box(self.dest)
        (self.tool / "docs").mkdir()
        (self.tool / "docs" / "artefacts.md").write_text("how to write a page\n")
        (self.tool / "docs" / "dfs-review-findings.tsv").write_text("W5\tdefect\tW5.2 added: the original's first-review defect\n")
        (self.tool / "docs" / "dfs-optimization.md").write_text("W5 first review failed\n")
        (self.tool / "docs" / "dfs-review-findings.tsv.bak").write_text("W5 first-review defect, copied\n")
        (self.tool / "docs" / "checklist.md").write_text("a procedure the changed SKILL.md points at\n")
        (self.tool / "docs" / "more").mkdir()
        (self.tool / "docs" / "more" / "steps.md").write_text("nested step\n")
        out = self.sandbox_out(f"echo '[ls]'; ls {self.tool}/docs; echo '[cat]'; cat {self.tool}/docs/artefacts.md "
                               f"{self.tool}/docs/checklist.md {self.tool}/docs/more/steps.md; "
                               f"cat {self.tool}/docs/dfs-review-findings.tsv 2>&1; true", bins)
        part = lambda name: out.split(f"[{name}]\n")[1].split("\n[")[0]
        self.assertEqual(part("ls").split(), ["artefacts.md", "checklist.md", "more"])
        self.assertIn("how to write a page", part("cat"))
        self.assertIn("a procedure the changed SKILL.md points at", part("cat"))
        self.assertIn("nested step", part("cat"))
        self.assertIn("No such file", part("cat"))
        self.assertNotIn("first-review defect", out)
        self.assertEqual(sorted(p.name for p in (replay.box_of(self.dest) / "docs").iterdir()),
                         ["artefacts.md", "checklist.md", "more"])

    def test_a_hidden_doc_that_skill_md_or_a_template_points_at_is_named_for_the_warning(self):
        tool = Path(self.tmp.name) / "changed"
        (tool / "docs").mkdir(parents=True)
        (tool / "templates").mkdir()
        for name in ("dfs-rounds.tsv", "dfs-optimization.md", "checklist.md"):
            (tool / "docs" / name).write_text("x\n")
        (tool / "SKILL.md").write_text("Read `docs/checklist.md`, then `docs/dfs-rounds.tsv`.\n")
        (tool / "templates" / "t.html").write_text("<!-- docs/dfs-optimization.md. -->")
        (tool / "scripts").mkdir()
        (tool / "scripts" / "audit.py").write_text("# writes docs/dfs-review-findings.tsv\n")   # an output, not a use
        self.assertEqual(replay.hidden_docs_named(tool), ["dfs-optimization.md", "dfs-rounds.tsv"])
        self.assertEqual(replay.hidden_docs_named(self.tool), [])

    def test_a_tool_in_the_store_keeps_its_bin_and_libexec_but_gets_the_trimmed_docs(self):
        bins = {"proot": "/nix/store/p/bin/proot", "claude": "/nix/store/c/bin/claude",
                "bash": "/nix/store/b/bin/bash", "env": "/nix/store/e/bin/env"}
        tool = self.tmp.name + "/store-like"
        Path(tool, "docs").mkdir(parents=True)
        Path(tool, "docs", "artefacts.md").write_text("a\n")
        Path(tool, "docs", "dfs-review-findings.tsv").write_text("W5\n")
        box_docs = replay.tool_docs(self.dest, tool)
        argv = replay.sandbox_argv(self.dest, tool, ["true"], bins, {}, store=["/nix/store/t-roadmap"], docs=box_docs)
        binds = [argv[i + 1] for i, a in enumerate(argv) if a == "-b"]
        self.assertIn("/nix/store/t-roadmap", binds)                                   # the package, as before
        self.assertIn(f"{replay.box_of(self.dest) / 'docs'}:{Path(tool).resolve() / 'docs'}", binds)   # docs laid over it, last
        self.assertNotIn(f"{Path(tool).resolve() / 'docs'}:{Path(tool).resolve() / 'docs'}", binds)

    def test_the_box_docs_are_rebuilt_when_the_tools_docs_are_read_only(self):
        # A tool in the nix store has read-only directories; a resumed `run` builds the box docs again.
        tool = Path(self.tmp.name) / "readonly-tool"
        (tool / "docs" / "sub").mkdir(parents=True)
        (tool / "docs" / "artefacts.md").write_text("a\n")
        (tool / "docs" / "sub" / "b.md").write_text("b\n")
        for path in [tool / "docs" / "artefacts.md", tool / "docs" / "sub" / "b.md"]:
            path.chmod(0o444)
        for path in [tool / "docs" / "sub", tool / "docs"]:
            path.chmod(0o555)
        self.addCleanup(lambda: [p.chmod(0o755) for p in [tool / "docs", tool / "docs" / "sub"]])
        first = replay.tool_docs(self.dest, tool)
        second = replay.tool_docs(self.dest, tool)
        self.assertEqual(first, second)
        self.assertEqual((second / "sub" / "b.md").read_text(), "b\n")
        self.assertTrue(os.access(second / "sub", os.W_OK))

    def test_building_the_command_writes_nothing_to_the_box(self):
        bins = {"proot": "/nix/store/p/bin/proot", "claude": "/nix/store/c/bin/claude",
                "bash": "/nix/store/b/bin/bash", "env": "/nix/store/e/bin/env"}
        tool = Path(self.tmp.name) / "tool-with-docs"
        (tool / "docs").mkdir(parents=True)
        (tool / "docs" / "artefacts.md").write_text("a\n")
        replay.sandbox_argv(self.dest, tool, ["true"], bins, {}, store=[])
        self.assertFalse(replay.box_of(self.dest).exists())
        argv = replay.sandbox_argv(self.dest, tool, ["true"], bins, {}, store=[], docs=replay.tool_docs(self.dest, tool))
        self.assertTrue((replay.box_of(self.dest) / "docs" / "artefacts.md").is_file())
        self.assertIn(f"{replay.box_of(self.dest) / 'docs'}:{tool.resolve() / 'docs'}", argv)

    def test_a_tool_in_the_store_is_not_bound_and_the_box_keeps_to_its_own_directories(self):
        bins = {"proot": "/nix/store/p/bin/proot", "claude": "/nix/store/c/bin/claude",
                "bash": "/nix/store/b/bin/bash", "env": "/nix/store/e/bin/env"}
        argv = replay.sandbox_argv(self.dest, "/nix/store/t-roadmap/share/x", ["true"], bins, {"A": "1"})
        binds = [argv[i + 1] for i, a in enumerate(argv) if a == "-b"]
        self.assertIn(f"{self.dest.resolve()}:{self.dest.resolve()}", binds)
        self.assertFalse([b for b in binds if "t-roadmap" in b])
        self.assertEqual([b for b in binds if b.startswith(str(self.dest.parent.resolve()))],
                         [f"{replay.box_of(self.dest) / 'home'}:/home/replay", f"{self.dest.resolve()}:{self.dest.resolve()}"])
        self.assertEqual(argv[-3:], ["-i", "A=1", "true"])

    def test_score_reads_the_transcripts_from_the_boxs_home_when_it_has_them(self):
        self.assertIsNone(replay.replay_config(self.dest))           # no box: the author's config, as before
        proj = replay.box_of(self.dest) / "home" / ".claude" / "projects" / "-x"
        proj.mkdir(parents=True)
        (proj / "s.jsonl").write_text("{}\n")
        cfg = replay.replay_config(self.dest)
        self.assertEqual(cfg, replay.box_of(self.dest) / "home" / ".claude")
        found = history._glob(replay.transcripts(self.dest, cfg))
        self.assertEqual(found, [])                                  # the project dir is not this DEST's
        enc = re.sub(r"[/.]", "-", str(self.dest.resolve()))
        (cfg / "projects" / enc).mkdir()
        (cfg / "projects" / enc / "s.jsonl").write_text("{}\n")
        self.assertEqual([Path(f).parent.name for f in history._glob(replay.transcripts(self.dest, cfg))], [enc])


class AgentTest(unittest.TestCase):
    """A replay runs under the agent the screen is set to, not always claude."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = Path(self.tmp.name)
        self.repo = self.t / "repo"
        (self.repo / ".dfs" / "runs").mkdir(parents=True)

    def agent(self, text=None, live=None, override=None):
        """configured_agent with the checkout's agent file holding `text`, `$DFS_RUNS` at `live`."""
        if text is not None:
            (self.repo / ".dfs" / "runs" / "agent").write_text(text)
        env = {"DFS_RUNS": str(live)} if live else {}
        with mock.patch.dict(os.environ, env):
            if not live:
                os.environ.pop("DFS_RUNS", None)
            return replay.configured_agent(self.repo, override)

    def test_the_agent_is_the_one_the_screen_kept_claude_when_it_kept_none_or_an_unknown_one(self):
        self.assertEqual(self.agent(), "claude")                     # no file
        self.assertEqual(self.agent("kiro\n"), "kiro")
        self.assertEqual(self.agent("codex"), "codex")
        self.assertEqual(self.agent("opencode"), "opencode")
        self.assertEqual(self.agent("gemini"), "claude")             # what run.sh cannot launch

    def test_an_override_wins_and_a_bad_one_is_refused(self):
        self.assertEqual(self.agent("kiro", override="claude"), "claude")
        with self.assertRaises(SystemExit) as e:
            self.agent("kiro", override="gemini")
        self.assertIn("--agent must be one of", str(e.exception))

    def test_dfs_runs_is_read_before_the_checkouts_own_runs(self):
        live = self.t / "other-runs"
        live.mkdir()
        (live / "agent").write_text("codex")
        self.assertEqual(self.agent("kiro", live=live), "codex")
        empty = self.t / "empty-runs"
        empty.mkdir()
        self.assertEqual(self.agent("kiro", live=empty), "kiro")    # no file there: the checkout's

    def launch_cmd(self, agent):
        dest = self.t / "dfs-replay-W5-a"
        (dest / ".git").mkdir(parents=True, exist_ok=True)
        bins = {"proot": "/nix/store/p/bin/proot", agent: f"/nix/store/x/bin/{agent}",
                "bash": "/nix/store/b/bin/bash", "env": "/nix/store/e/bin/env"}
        with mock.patch.object(replay, "tool_bins", return_value=bins) as tb, mock.patch.object(replay, "make_box") as mb:
            cmd, _ = replay.launch(dest, "W5", 3, Path("/nix/store/t/share/x"), 15, self.repo, agent)
        tb.assert_called_once_with(self.repo, agent)
        mb.assert_called_once_with(dest.resolve(), agent)
        return cmd

    def test_each_agent_gets_its_flag_and_its_own_command_variable(self):
        for agent, flag, var in (("kiro", "--kiro", "KIRO_CMD"), ("codex", "--codex", "CODEX_CMD"),
                                 ("opencode", "--opencode", "OPENCODE_CMD")):
            cmd = self.launch_cmd(agent)
            self.assertEqual(cmd[-5:], ["bash", "/nix/store/t/share/x/scripts/run.sh", flag, "W5", "3"], agent)
            self.assertIn(f"{var}=/nix/store/x/bin/{agent}", cmd)
            self.assertFalse([c for c in cmd if c.startswith("CLAUDE_CMD=")], agent)
        cmd = self.launch_cmd("claude")                              # claude is run.sh's default: no flag
        self.assertEqual(cmd[-4:], ["bash", "/nix/store/t/share/x/scripts/run.sh", "W5", "3"])
        self.assertIn("CLAUDE_CMD=/nix/store/x/bin/claude", cmd)

    def test_only_kiro_is_given_the_hosts_loader_and_libraries(self):
        args = lambda agent: replay.sandbox_argv(self.t / "d", "/nix/store/t/x", ["true"], {
            "proot": "/nix/store/p/proot", "bash": "/nix/store/b", "env": "/nix/store/e"}, {}, agent)
        kiro = args("kiro")
        self.assertIn("/usr/lib64:/lib64", kiro)
        self.assertNotIn("/usr/lib64:/lib64", args("claude"))
        binds = [kiro[i + 1] for i, a in enumerate(kiro) if a == "-b"]
        self.assertEqual([b for b in binds if "/home" in b], [f"{replay.box_of(self.t / 'd') / 'home'}:/home/replay"])

    def test_the_kiro_box_has_the_sign_in_and_the_engine_and_none_of_the_authors_work(self):
        import sqlite3
        home = self.t / "home"
        share = home / ".local" / "share" / "kiro-cli"
        share.mkdir(parents=True)
        db = sqlite3.connect(share / "data.sqlite3")
        db.executescript("create table auth_kv (key text primary key, value text);"
                         "create table conversations_v2 (key text, value text);"
                         "create table history (key text, value text);"
                         "insert into auth_kv values ('kirocli:odic:token', 'TOKEN');"
                         "insert into conversations_v2 values ('/live', 'the author''s conversation');"
                         "insert into history values ('/live', 'the author''s history');")
        db.commit()
        db.close()
        (share / "node").write_bytes(b"\x7fELF")
        (share / "kas" / "2.31.1").mkdir(parents=True)
        (share / "kas" / "2.31.1" / "server.js").write_text("server\n")
        (share / "knowledge_bases").mkdir()
        (share / "knowledge_bases" / "mine.txt").write_text("the author's notes\n")
        (home / ".kiro" / "settings").mkdir(parents=True)
        (home / ".kiro" / "settings" / "cli.json").write_text("{}")
        (home / ".kiro" / "sessions" / "abc").mkdir(parents=True)
        (home / ".kiro" / "sessions" / "abc" / "s.json").write_text("the author's session\n")
        dest = self.t / "dfs-replay-W5-k"
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            box = replay.make_box(dest, "kiro")
        out = box / "home" / ".local" / "share" / "kiro-cli"
        got = sqlite3.connect(out / "data.sqlite3")
        self.assertEqual(got.execute("select value from auth_kv").fetchall(), [("TOKEN",)])
        self.assertEqual(got.execute("select count(*) from conversations_v2").fetchone(), (0,))
        self.assertEqual(got.execute("select count(*) from history").fetchone(), (0,))
        got.close()
        self.assertTrue((out / "node").is_file())
        self.assertTrue((out / "kas" / "2.31.1" / "server.js").is_file())
        self.assertTrue((box / "home" / ".kiro" / "settings" / "cli.json").is_file())
        self.assertFalse((out / "knowledge_bases").exists())
        self.assertFalse((box / "home" / ".kiro" / "sessions").exists())
        # the live database is as it was
        live = sqlite3.connect(share / "data.sqlite3")
        self.assertEqual(live.execute("select count(*) from history").fetchone(), (1,))
        live.close()

    def test_prepare_records_the_agent_and_run_launches_under_it(self):
        dest = self.t / "dfs-replay-W5-r"
        (dest / ".git").mkdir(parents=True)
        (dest / ".git" / "replay.json").write_text(json.dumps({"task": "W5", "tool": "/nix/store/t", "agent": "kiro"}))
        with mock.patch.object(replay, "launch", side_effect=SystemExit("stop")) as lc:
            for override, want in ((None, "kiro"), ("codex", "codex")):
                with self.assertRaises(SystemExit):
                    replay.run(dest, "W5", repo=self.repo, agent=override)
                self.assertEqual(lc.call_args.args[-1], want)

    def test_a_replay_under_an_agent_score_cannot_read_is_refused_before_it_spends(self):
        dest = self.t / "dfs-replay-W5-s"
        (dest / ".git").mkdir(parents=True)
        (dest / ".git" / "replay.json").write_text(json.dumps({"task": "W5", "agent": "codex"}))
        with self.assertRaises(SystemExit) as e:
            replay.score(dest, "W5")
        self.assertIn("ran under codex", str(e.exception))
        (self.repo / ".dfs" / "runs" / "agent").write_text("codex")
        with mock.patch.object(replay, "prepare") as pr, self.assertRaises(SystemExit) as e:
            replay.main(["replay", "W5", str(self.t / "dfs-replay-W5-n"), "--repo", str(self.repo)])
        self.assertIn("the agent is codex", str(e.exception))
        pr.assert_not_called()


class ScoreTest(unittest.TestCase):
    def test_a_replay_bounds_one_bash_call(self):
        b = replay.bash_bound(15)
        self.assertEqual(b, {"BASH_DEFAULT_TIMEOUT_MS": "900000", "BASH_MAX_TIMEOUT_MS": "1800000"})
        self.assertEqual(replay.bash_bound(0.5)["BASH_DEFAULT_TIMEOUT_MS"], "30000")

    def test_why_a_chain_stopped_is_its_last_stop_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".git").mkdir()
            self.assertEqual(replay.stopped_on(tmp), "")
            (Path(tmp) / ".git" / "replay.out").write_text(
                "dfs_run: BLOCKED a raise\n...\ndfs_run: DONE after run 6 — every live node\n")
            self.assertEqual(replay.stopped_on(tmp), "DONE")
            (Path(tmp) / ".git" / "replay.out").write_text("dfs_run: it fell over\n")
            self.assertEqual(replay.stopped_on(tmp), "other")

    def test_score_fails_loudly_on_swapped_arguments_a_wrong_task_or_no_transcripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "dfs-replay-W5-a"
            (dest / ".git").mkdir(parents=True)
            with self.assertRaises(SystemExit) as e:   # not a prepared replay
                replay.score(dest, "W5")
            self.assertIn("not a prepared replay", str(e.exception))
            with self.assertRaises(SystemExit) as e:   # swapped: the task where DEST goes
                replay.score("W5", str(dest))
            self.assertIn("not a prepared replay", str(e.exception))
            (dest / ".git" / "replay.json").write_text(json.dumps({"task": "W5"}))
            with self.assertRaises(SystemExit) as e:   # another task's replay
                replay.score(dest, "W9")
            self.assertIn("replays W5, not W9", str(e.exception))
            with tempfile.TemporaryDirectory() as cfg, mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": cfg}):
                with self.assertRaises(SystemExit) as e:   # prepared, but no session of it
                    replay.score(dest, "W5")
                self.assertIn("no session of W5", str(e.exception))

    def test_the_comparison_names_both_columns(self):
        t = {"sessions": {"work": 2, "review": 1}, "usd": 1.5, "nodes": 3, "first_review": "issues",
             "review_issues": 1, "review_ok": 1, "critic_issues": 0, "raises": 0, "refused": 0,
             "after_issues": 0.5}
        buf = io.StringIO()
        with redirect_stdout(buf):
            replay.compare("W5", t, dict(t, stopped="DONE", finished=True))
        out = buf.getvalue()
        self.assertIn("original", out)
        self.assertIn("first review", out)
        self.assertIn("DONE", out)


class TranscriptsOfOneReplayTest(unittest.TestCase):
    def test_replay_1_does_not_take_in_replay_10_but_does_take_its_worktrees(self):
        with tempfile.TemporaryDirectory() as cfg, mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": cfg}):
            for name in ("-tmp-dfs-replay-W5-1", "-tmp-dfs-replay-W5-1--dfs-worktrees-W5",
                         "-tmp-dfs-replay-W5-10", "-tmp-dfs-replay-W5-10--dfs-worktrees-W5",
                         "-tmp-dfs-replay-W5-1x"):
                d = Path(cfg) / "projects" / name
                d.mkdir(parents=True)
                (d / "s.jsonl").write_text("{}\n")
            found = {Path(f).parent.name for f in history._glob(replay.transcripts("/tmp/dfs-replay-W5-1"))}
            self.assertEqual(found, {"-tmp-dfs-replay-W5-1", "-tmp-dfs-replay-W5-1--dfs-worktrees-W5"})


class CandidatesTest(unittest.TestCase):
    def test_the_selection_rule_keeps_cheap_tasks_whose_first_review_found_issues(self):
        def task(name, usd, first, work=1, project="-r"):
            return (project, name), {"task": name, "usd": usd, "first_review": first,
                                      "sessions": {"work": work}}
        def start(repo, name):
            if name == "W5":
                raise SystemExit("no node commit")
            return None, None

        tasks = dict([task("W1", 2.0, "issues"), task("W2", 4.0, "issues"), task("W3", 1.0, "ok"),
                      task("W4", 1.5, "issues", project="-other"), task("W5", 0.5, "issues"),
                      task("W6", 1.0, "issues", work=0)])
        with mock.patch.object(replay.history, "load", return_value=(None, tasks, None, None)), \
                mock.patch.object(replay, "starting_point", side_effect=start), \
                mock.patch.object(replay, "transcripts", return_value=[]), \
                mock.patch.object(replay.history, "project_of", return_value="-r"):
            rows = replay.candidates("/r")
        self.assertEqual([t["task"] for t in rows], ["W1"])   # W2 dear, W3 passed, W4 elsewhere, W5 no node commit, W6 no work


class RecordedUnderAnotherPathTest(unittest.TestCase):
    """A copy of a project (optimisation-data/shop-app) was recorded under the path it had before."""
    def lines(self):
        return [
            {"type": "user", "timestamp": "2026-10-01T00:00:00Z", "message": {"content": "You are the implementation reviewer for task W5. x"}},
            {"type": "assistant", "timestamp": "2026-10-01T00:01:00Z", "message": {"id": "m", "model": "m", "usage": {"input_tokens": 1, "output_tokens": 1}, "content": [
                {"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": "tree.py log W5 review issues"}}]}},
        ]

    def test_the_original_is_found_by_the_path_it_was_recorded_under(self):
        with tempfile.TemporaryDirectory() as cfg, mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": cfg}):
            d = Path(cfg) / "projects" / "-tmp-orig-shop"
            d.mkdir(parents=True)
            (d / "s.jsonl").write_text("\n".join(json.dumps(x) for x in self.lines()) + "\n")
            self.assertIsNone(replay.original("/tmp/elsewhere/data/shop", "W5"))
            self.assertEqual(replay.original("/tmp/elsewhere/data/shop", "W5", recorded_as="/tmp/orig-shop")["task"], "W5")

    def test_candidates_read_the_same_path_and_say_when_there_is_nothing_to_read(self):
        tasks = {("-tmp-orig-shop", "W5"): {"task": "W5", "usd": 1.0, "first_review": "issues", "sessions": {"work": 1}}}
        with mock.patch.object(replay.history, "load", return_value=(None, tasks, None, None)) as load, \
                mock.patch.object(replay, "starting_point", return_value=(None, None)):
            rows = replay.candidates("/tmp/elsewhere/data/shop", recorded_as="/tmp/orig-shop")
            self.assertEqual(load.call_args[0][1], {"-tmp-orig-shop": "/tmp/elsewhere/data/shop"})   # prices come from the copy
            self.assertEqual([t["task"] for t in rows], ["W5"])
            self.assertEqual(replay.candidates("/tmp/elsewhere/data/shop"), [])
        with tempfile.TemporaryDirectory() as cfg, mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": cfg}):
            (Path(cfg) / "projects" / "-tmp-orig-shop").mkdir(parents=True)
            (Path(cfg) / "projects" / "-tmp-orig-shop" / "s.jsonl").write_text("{}\n")
            self.assertIn("-tmp-orig-shop", replay.recorded_hint("/tmp/elsewhere/data/shop"))
            self.assertEqual(replay.recorded_hint("/tmp/orig-shop"), "")


class RecordedAsReachesEveryCommandTest(unittest.TestCase):
    """`--recorded-as` is passed on by every command that compares with the original, and refused by those that do not."""
    def main(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            return replay.main(list(argv))

    def test_score_and_replay_look_the_original_up_under_the_recorded_path(self):
        with mock.patch.object(replay, "original", return_value=None) as original, \
                mock.patch.object(replay, "score", return_value=None), \
                mock.patch.object(replay, "compare"):
            self.main("score", "/tmp/dfs-replay-W5-a", "W5", "--repo", "/tmp/data/shop", "--recorded-as", "/tmp/orig-shop")
            self.assertEqual(original.call_args, mock.call("/tmp/data/shop", "W5", "/tmp/orig-shop"))
        with mock.patch.object(replay, "original", return_value=None) as original, \
                mock.patch.object(replay, "configured_agent", return_value="claude"), \
                mock.patch.object(replay, "prepare", return_value=("/tmp/dfs-replay-W5-a", "/tool")), \
                mock.patch.object(replay, "run"), mock.patch.object(replay, "score", return_value=None), \
                mock.patch.object(replay, "compare"):
            self.main("replay", "W5", "/tmp/dfs-replay-W5-a", "--repo", "/tmp/data/shop", "--recorded-as", "/tmp/orig-shop")
            self.assertEqual(original.call_args, mock.call("/tmp/data/shop", "W5", "/tmp/orig-shop"))

    def test_the_replay_cap_comes_from_the_original_found_under_that_path(self):
        before = {"sessions": {"work": 5, "review": 2}}
        found = lambda repo, task, recorded_as=None: before if recorded_as == "/tmp/orig-shop" else None
        with mock.patch.object(replay, "original", side_effect=found), \
                mock.patch.object(replay, "configured_agent", return_value="claude"), \
                mock.patch.object(replay, "prepare", return_value=("/tmp/dfs-replay-W5-a", "/tool")), \
                mock.patch.object(replay, "run") as run, mock.patch.object(replay, "score", return_value=None), \
                mock.patch.object(replay, "compare"):
            self.main("replay", "W5", "/tmp/dfs-replay-W5-a", "--repo", "/tmp/data/shop", "--recorded-as", "/tmp/orig-shop")
            self.assertEqual(run.call_args[0][2], 14)   # 2 * 7 sessions, not the 6 of no original

    def test_prepare_and_run_refuse_it_by_name(self):
        for argv in (("prepare", "W5", "/tmp/dfs-replay-W5-a"), ("run", "/tmp/dfs-replay-W5-a", "W5")):
            with self.assertRaises(SystemExit) as e:
                self.main(*argv, "--recorded-as", "/tmp/orig-shop")
            self.assertIn("--recorded-as", str(e.exception))


class OriginalIsTheRepoOwnTest(unittest.TestCase):
    def test_a_task_of_the_same_name_in_another_project_is_not_the_original(self):
        lines = [
            {"type": "user", "timestamp": "2026-10-01T00:00:00Z", "message": {"content": "You are the implementation reviewer for task W5. x"}},
            {"type": "assistant", "timestamp": "2026-10-01T00:01:00Z", "message": {"id": "m", "model": "m", "usage": {"input_tokens": 1, "output_tokens": 1}, "content": [
                {"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": "tree.py log W5 review issues"}}]}},
        ]
        with tempfile.TemporaryDirectory() as cfg, mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": cfg}):
            for name in ("-tmp-mine", "-tmp-other"):
                d = Path(cfg) / "projects" / name
                d.mkdir(parents=True)
                (d / "s.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines))
            # transcripts() is patched wide, as it was before it was narrowed, to show that
            # original() itself refuses the other project's task.
            wide = [os.path.join(cfg, "projects", "*", "*.jsonl")]
            with mock.patch.object(replay, "transcripts", return_value=wide):
                self.assertIsNotNone(replay.original("/tmp/mine", "W5"))
                self.assertIsNone(replay.original("/tmp/nobody", "W5"))


class ReachOutsideDestTest(unittest.TestCase):
    """A replay kept inside the live checkout can read the original's runs, items and
    codex logs; the score counts the calls that did, and the results that showed the answer."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp = Path(self.tmp.name)
        self.live = tmp / "live"
        self.dest = self.live / ".dfs" / "runs" / "replays" / "dfs-replay-W5-a"
        self.home = tmp / "home"
        (self.live / ".dfs" / "items").mkdir(parents=True)
        self.dest.mkdir(parents=True)
        self.home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(self.home), "CLAUDE_CONFIG_DIR": str(self.home / ".claude")})
        env.start()
        self.addCleanup(env.stop)

    def transcript(self, calls, results=()):
        path = Path(self.tmp.name) / "s.jsonl"
        rows = [{"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}]}} for i, (name, inp) in enumerate(calls)]
        rows += [{"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t0", "content": r}]}} for r in results]
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        return [str(path)]

    def reached(self, calls, tool=None):
        return [(n, t) for _, n, t, _ in replay.reached(self.dest, self.live, self.transcript(calls), tool)]

    def test_calls_inside_dest_are_not_reach_and_calls_outside_it_are(self):
        d, live = str(self.dest), str(self.live)
        got = self.reached([
            ("Bash", {"command": f"cd {d} && git log"}),                                   # its own clone
            ("Read", {"file_path": f"{d}/.dfs/worktrees/W5/scripts/x.py"}),                  # its own worktree
            ("Bash", {"command": f"ls {d}-tool/templates"}),                                # its exported tool
            ("Bash", {"command": f"grep -r limit {live}/.dfs/runs"}),                       # the live runs
            ("Read", {"file_path": f"{live}/.dfs/items/W5.md"}),                            # the original's task file
            ("Glob", {"pattern": f"{live}/.dfs/worktrees/*/scripts/*.py"}),                 # live worktrees
            ("Bash", {"command": f"ls {d[:-1]}b/.dfs"}),                                    # a sibling replay is live too
            ("Bash", {"command": "grep -rl limit ~/.codex/sessions"}),                      # codex logs, written with ~
            ("Bash", {"command": f"grep -rl limit {self.home}/.codex"}),
            ("Bash", {"command": "ls $HOME/.claude/projects"}),                              # earlier sessions' transcripts
            ("Bash", {"command": "git status"}),
        ])
        self.assertEqual([n for n, _ in got],
                         ["Bash", "Read", "Glob", "Bash", "Bash", "Bash", "Bash"])

    def test_the_tool_the_replay_runs_under_is_not_reach_even_inside_the_live_checkout(self):
        tool = self.live / "scripts"
        calls = [("Bash", {"command": f"cat {tool}/limit.py"})]
        self.assertEqual(len(self.reached(calls)), 1)
        self.assertEqual(len(self.reached(calls, tool=self.live)), 0)

    def test_a_tool_that_is_the_live_checkout_still_counts_a_read_of_its_dfs_dir(self):
        live = str(self.live)
        calls = [("Bash", {"command": f"cat {live}/scripts/limit.py {live}/SKILL.md"}),   # the tool itself
                 ("Bash", {"command": f"ls {live}/.dfs/runs"}),                            # the original's runs
                 ("Read", {"file_path": f"{live}/.dfs/items/W5.md"}),                      # the original's task
                 ("Glob", {"pattern": f"{live}/.dfs/worktrees/*/scripts/*.py"}),           # live worktrees
                 ("Bash", {"command": f"cat {live}/scripts-old/x"})]                       # not the tool's scripts
        got = self.reached(calls, tool=self.live)
        self.assertEqual([n for n, _ in got], ["Bash", "Read", "Glob", "Bash"])

    def test_a_read_of_the_tools_findings_in_a_live_checkout_tool_counts_and_its_other_docs_do_not(self):
        live = str(self.live)
        calls = [("Bash", {"command": f"cat {live}/docs/artefacts.md"}),                  # the tool's own use of docs
                 ("Bash", {"command": f"cat {live}/docs/checklist.md {live}/docs/more/steps.md"}),   # a doc a changed tool adds
                 ("Bash", {"command": f"cat {live}/docs/dfs-review-findings.tsv"}),       # every task's findings
                 ("Bash", {"command": f"cat {live}/docs/dfs-review-findings.tsv.bak"}),   # a copy of them
                 ("Bash", {"command": f"ls {live}/docs"})]
        got = self.reached(calls, tool=self.live)
        self.assertEqual([t.split("/")[-1] for _, t in got], ["dfs-review-findings.tsv", "dfs-review-findings.tsv.bak", "docs"])

    def test_a_call_the_sandbox_answered_with_nothing_is_told_from_one_that_got_output(self):
        live = str(self.live)
        uses = [("Bash", {"command": f"grep -rl limit {live}/.dfs/runs | head"}),
                ("Bash", {"command": f"ls {live}/.dfs"}),
                ("Read", {"file_path": f"{live}/.dfs/items/W5.md"}),
                ("Bash", {"command": f"cat {live}/.dfs/items/W5.md"}),
                ("Bash", {"command": f"ls {live}/.dfs/worktrees; ls {self.dest}"}),
                ("Bash", {"command": f"ls {live}/.dfs/archive"})]    # no result recorded at all
        answers = [("t0", "(Bash completed with no output)", False), ("t1", "ls: cannot access '/x': No such file or directory", False),
                   ("t2", "File does not exist.", True), ("t3", "Exit code 1\ncat: /x: No such file or directory", False),
                   ("t4", "W5\nW6", False)]
        rows = [{"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": f"t{i}", "name": n, "input": inp}]}} for i, (n, inp) in enumerate(uses)]
        rows += [{"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": i, "content": [{"type": "text", "text": c}] if i == "t3" else c,
             "is_error": err}]}} for i, c, err in answers]
        path = Path(self.tmp.name) / "r.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        got = replay.reached(self.dest, self.live, [str(path)])
        self.assertEqual([nothing for *_, nothing in got], [True, True, True, True, False, False])   # output, and unknown, are not "nothing"

    def test_an_error_result_is_read_by_its_text_so_a_read_then_a_failing_command_is_output(self):
        def nothing(text, is_error=True):
            return replay.answered_nothing({"content": text, "is_error": is_error})
        # `cat live/file; which codex` exits 127 and still printed the file: output, not nothing
        self.assertFalse(nothing("Exit code 127\nthe original's Determination\n/usr/bin/which: no codex in (/nix)"))
        self.assertFalse(nothing("Exit code 1\nworktrees\n/workspace/x/.dfs/runs"))
        self.assertFalse(nothing("<tool_use_error>Permission denied: /live/x</tool_use_error>"))
        # an error that says only that the path is absent, or says nothing, still is
        self.assertTrue(nothing("Exit code 2\nls: cannot access '/live': No such file or directory"))
        self.assertTrue(nothing("<tool_use_error>File does not exist.</tool_use_error>"))
        self.assertTrue(nothing("Exit code 1"))
        self.assertTrue(nothing("", is_error=True))
        self.assertTrue(nothing("(Bash completed with no output)", is_error=False))

    def test_a_call_that_looks_through_the_nix_store_for_flake_sources_is_counted(self):
        store_calls = ["ls /nix/store | grep codex", "grep -rl 'try again at' /nix/store/*", "find /nix/store -name W55.md",
                       "cat /nix/store/abc123-source/.dfs/items/W55.md", "ls -d /nix/store/*-source"]
        fine = ["bash /nix/store/abc123-roadmap-dfs-1/share/roadmap-dfs/scripts/run.sh W5",
                "/nix/store/abc-git-2/bin/git status", "ls /nix/store-notes.txt"]
        rows = [{"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": c}}]}}
            for i, c in enumerate(store_calls + fine)]
        path = Path(self.tmp.name) / "store.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        got = [text for _, _, text, _ in replay.reached(self.dest, self.live, [str(path)])]
        self.assertEqual(got, store_calls)

    def test_a_call_that_fetches_the_replayed_repos_public_remote_is_counted(self):
        git(self.live, "init", "-q")
        git(self.live, "remote", "add", "origin", "git@github.com:rskew/roadmap-dfs")
        counted = ["git clone https://github.com/rskew/roadmap-dfs /tmp/x",
                   "git ls-remote git@github.com:rskew/roadmap-dfs",
                   "curl -s https://raw.githubusercontent.com/rskew/roadmap-dfs/main/scripts/limit.py",
                   "git fetch https://github.com/rskew/roadmap-dfs.git main",
                   "gh api repos/rskew/roadmap-dfs/contents/.dfs/items/W5.md",
                   "gh repo clone rskew/roadmap-dfs"]
        fine = ["git clone https://github.com/rskew/other-repo /tmp/y", "git clone https://github.com/someone/roadmap-dfs /tmp/z",
                "curl https://raw.githubusercontent.com/openai/codex/main/error.rs", "git clone https://github.com/rskew/roadmap-dfs-notes /tmp/n",
                "git push origin HEAD", "git remote get-url origin"]
        rows = [{"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": c}}]}} for i, c in enumerate(counted + fine)]
        rows.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "w", "name": "WebFetch", "input": {"url": "https://github.com/rskew/roadmap-dfs/blob/main/.dfs/items/W5.md"}}]}})
        path = Path(self.tmp.name) / "net.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        got = [text for _, _, text, _ in replay.reached(self.dest, self.live, [str(path)])]
        self.assertEqual(got, counted + ["https://github.com/rskew/roadmap-dfs/blob/main/.dfs/items/W5.md"])

    def test_a_repo_with_no_origin_has_no_remote_to_fetch(self):
        git(self.live, "init", "-q")
        self.assertEqual(replay.remote_paths(self.live), [])
        self.assertEqual(replay.remote_paths(Path(self.tmp.name) / "not-a-repo"), [])

    def test_a_result_that_prints_a_review_finding_of_the_original_is_the_answer_seen(self):
        (self.live / ".dfs" / "items" / "W5.md").write_text(
            "# W5\n\n## Tree\n\n### W5.1 \u00b7 Fix the parser\nStatus: confirmed\n\n## Log\n\n"
            "- 2026-10-10T03:28:33Z \u00b7 review \u00b7 issues\n"
            "  W5.2 added: a limit takes an earlier \"resets \u2026\" anywhere in the stream (limit.py classify)\n"
            "  W5.3 added: minor findings (reset read at hold time, whole-stream looseness)\n"
            "- 2026-10-10T03:28:40Z \u00b7 end \u00b7 review \u00b7 ok \u00b7 peak 41605\n")
        git(self.dest, "init", "-q")
        (self.dest / "f").write_text("x")
        git(self.dest, "add", "-A")
        git(self.dest, "commit", "-q", "-m", "W5.1: x")
        for raw in (True, False):   # a transcript writes the ellipsis raw or escaped
            path = Path(self.tmp.name) / f"r{raw}.jsonl"
            row = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t0", "content":
                   "W5\tdefect\tW5.2 added: a limit takes an earlier \"resets \u2026\" anywhere in the stream (limit.py classify)\n"}]}}
            path.write_text(json.dumps(row, ensure_ascii=raw) + "\n")
            seen = replay.answer_seen(self.dest, self.live, "W5", [str(path)])
            self.assertEqual(list(seen), ['W5.2 added: a limit takes an earlier "resets \u2026" anywhere in the stream (limit.py classify)'[:60]])

    def test_a_result_that_prints_the_originals_conclusion_is_the_answer_seen(self):
        (self.live / ".dfs" / "items" / "W5.md").write_text(
            "# W5\n\n## Tree\n\n### W5.1 \u00b7 Fix the parser\nDetermination: the parser now reads a quoted pipe before the verb.\n"
            "\n### W5.2 \u00b7 Own idea\nDetermination: both sessions wrote this very sentence themselves.\n")
        git(self.dest, "init", "-q")
        (self.dest / ".dfs" / "items").mkdir(parents=True)
        (self.dest / ".dfs" / "items" / "W5.md").write_text("### W5.2 \u00b7 Own idea\nDetermination: both sessions wrote this very sentence themselves.\n")
        git(self.dest, "add", "-A")
        git(self.dest, "commit", "-q", "-m", "W5.2: own")
        files = self.transcript([("Bash", {"command": "x"})], [
            "== run-1.json\nDetermination: the parser now reads a quoted pipe before the verb. more",
            "W5.2 \u00b7 Own idea ... Determination: both sessions wrote this very sentence themselves."])
        seen = replay.answer_seen(self.dest, self.live, "W5", files)
        self.assertEqual(list(seen), ["the parser now reads a quoted pipe before the verb."[:60]])   # not the one it wrote itself
        self.assertEqual(replay.answer_seen(self.dest, self.live, "W5", self.transcript([("Bash", {"command": "x"})], ["nothing"])), {})


def acp(update):
    """One line of a kiro run log: a session update."""
    return json.dumps({"type": "sessionUpdate", "data": {"sessionId": "sess_1", "update": update}}) + "\n"


def acp_call(tid, kind, title, inp, text="", exit_code=0, status="completed"):
    """The lines kiro writes for one tool call: the call, then its update with the output."""
    return (acp({"sessionUpdate": "tool_call", "toolCallId": tid, "title": title, "kind": kind}) +
            acp({"sessionUpdate": "tool_call_update", "toolCallId": tid, "status": status, "rawInput": inp,
                 "content": [{"type": "content", "content": {"type": "text", "text": text}}],
                 "rawOutput": {"exitCode": exit_code}} if kind == "execute" else
                {"sessionUpdate": "tool_call_update", "toolCallId": tid, "status": status, "rawInput": inp,
                 "content": [{"type": "content", "content": {"type": "text", "text": text}}]}))


def acp_summary(credits, requests=3):
    return acp({"sessionUpdate": "session_info_update", "_meta": {"kiro": {
        "kind": "turn_completion", "requestIds": [f"r{i}" for i in range(requests)],
        "promptTurnSummaries": [{"unit": "credit", "usage": credits, "usedTools": []}]}}})


def acp_context(pct):
    return acp({"sessionUpdate": "session_info_update", "_meta": {"kiro": {"contextUsage": {"usagePercentage": pct}}}})


def kiro_run_dir(root, name="dfs_run_a", task="W5", runs=(), console=True):
    """A chain's run directory as kiro leaves it: console.log headers, meta.json and a run-N.json
    per session (`runs` is [(kind, text of the log)]). The CLI's own `[INFO]` lines sit between events."""
    d = Path(root) / ".dfs" / "runs" / name
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"item": task, "agent": "kiro"}))
    if console:
        (d / "console.log").write_text("".join(f"\n── run {i}/20 · {kind} (current node {task}.1 · x) ──\n   exit 0\n"
                                              for i, (kind, _) in enumerate(runs, 1)))
    for i, (_, text) in enumerate(runs, 1):
        (d / f"run-{i}.json").write_text('{"type":"runStarted","data":{}}\n[INFO] kas.server.starting {}\n' + text +
                                         '{"type":"runFinished","data":{"status":"success"}}\n')
    return d


class KiroStreamTest(unittest.TestCase):
    """A kiro session's transcript is the chain's run log: it is read into the record claude's
    transcripts make, with credits where claude has dollars."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        work = (acp_context(4.5) + acp_call("c0", "read", "Read File", {"path": "/x/a.py"}, "text") +
                acp_call("c1", "read", "Read File", {"path": "/x/a.py"}, "text") +
                acp_call("c2", "execute", "Run Command", {"command": 'git commit -m "W5.1: did it"'},
                         "[main abc1234] W5.1: did it\n\nExit Code: 0") +
                acp_call("c3", "execute", "Run Command", {"command": 'git commit -m "W5.2: refused"'},
                         "dfs_check: the roadmap broke its own rules\n  - W5.2 carries a coordinate\n\nExit Code: 1",
                         exit_code=1, status="failed") +
                acp_call("c4", "execute", "Run Command", {"command": 'git commit -m "W5.3: nothing"'},
                         "nothing to commit, working tree clean\n\nExit Code: 0") +
                acp_call("c5", "execute", "Run Command", {"command": "tree.py start W5 W5.4"}, "ok\n\nExit Code: 0") +
                acp({"sessionUpdate": "tool_call", "toolCallId": "sys", "title": "Fetching your cloud config"}) +
                acp_context(9.25) + acp_summary(12.5) + acp_summary(12.5))   # the same turn reported twice
        review = (acp_call("r0", "execute", "Run Command", {"command": "python3 tree.py log W5 review issues"}, "Exit Code: 0") +
                  acp_context(3))   # cut off: no summary
        self.d = kiro_run_dir(self.tmp.name, runs=[("work", work), ("review", review)])

    def test_a_run_log_is_a_session_with_its_kind_task_credits_and_context(self):
        w = history.stream_session(str(self.d / "run-1.json"))
        self.assertEqual((w["kind"], w["task"], w["unit"]), ("work", "W5", "credits"))
        self.assertEqual(w["credits"], 12.5)   # a turn reported twice is one turn
        self.assertEqual((w["usd"], w["peak"], w["responses"]), (0.0, 9.25, 3))
        self.assertEqual((w["reads"], w["rereads"]), (2, 1))
        self.assertEqual(w["started"], ["W5.4"])
        self.assertEqual(sum(w["tools"].values()), 6)   # the CLI's own call is not the session's
        r = history.stream_session(str(self.d / "run-2.json"))
        self.assertEqual((r["kind"], r["credits"], r["verdicts"]), ("review", None, [("review", "issues")]))

    def test_a_commit_counts_only_when_it_was_made(self):
        w = history.stream_session(str(self.d / "run-1.json"))
        self.assertEqual([(c["subject"], c["ok"]) for c in w["commits"]],
                         [("W5.1: did it", True), ("W5.2: refused", False), ("W5.3: nothing", False)])
        self.assertEqual((w["refused"], w["errors"], dict(w["reasons"])), (1, 1, {"coordinate in a task file": 1}))

    def test_a_task_sums_credits_apart_from_dollars_and_counts_the_session_with_none(self):
        sessions, tasks = history.load_streams(str(self.d / "run-*.json"))
        t = tasks[("kiro", "W5")]
        self.assertEqual((t["unit"], t["credits"], t["usd"], t["unpriced"]), ("credits", 12.5, 0.0, 1))
        self.assertEqual((t["nodes"], t["first_review"], t["sessions"]), (1, "issues", {"work": 1, "review": 1}))

    def test_a_log_with_no_console_header_is_another_kind_not_a_guess(self):
        (self.d / "console.log").write_text("")
        self.assertEqual(history.stream_session(str(self.d / "run-1.json"))["kind"], "other")

    def test_the_task_files_log_names_the_kind_where_there_is_no_console_log(self):
        (self.d / "console.log").unlink()
        kinds = history.session_kinds("- 2026-10-28T15:33:36Z \u00b7 session \u00b7 review \u00b7 dfs_run_a/run-1\n"
                                      "- 2026-10-28T15:36:42Z \u00b7 end \u00b7 review \u00b7 ok\n")
        self.assertEqual(kinds, {"dfs_run_a/run-1": "review"})
        self.assertEqual(history.stream_session(str(self.d / "run-1.json"), kinds)["kind"], "review")
        self.assertEqual(history.stream_session(str(self.d / "run-2.json"), kinds)["kind"], "other")


class KiroScoreTest(unittest.TestCase):
    """`replay.py score` on a replay run under kiro: the same table from the run logs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp = Path(self.tmp.name)
        self.live = tmp / "live"
        (self.live / ".dfs" / "items").mkdir(parents=True)
        (self.live / ".dfs" / "items" / "W5.md").write_text(
            "# W5\n\n## Tree\n\n### W5.1 \u00b7 Fix the parser\nDetermination: the parser now reads a quoted pipe before the verb.\n")
        self.dest = tmp / "dfs-replay-W5-k"
        (self.dest).mkdir()
        git(self.dest, "init", "-q", "-b", "main")
        (self.dest / ".git" / "replay.json").write_text(json.dumps({"task": "W5", "agent": "kiro", "tool": str(tmp / "tool")}))
        # a replay's run directory has no console.log: the task file's log says what each session was
        (self.dest / ".dfs" / "items").mkdir(parents=True)
        (self.dest / ".dfs" / "items" / "W5.md").write_text(
            "# W5\n\n## Log\n\n- 2026-10-28T15:33:36Z \u00b7 session \u00b7 work \u00b7 dfs_run_a/run-1\n")
        git(self.dest, "add", "-f", ".dfs/items")
        git(self.dest, "commit", "-q", "-m", "W5.1: own")
        git(self.dest, "commit", "-q", "--allow-empty", "-m", "W5: merge dfs/W5")
        live = str(self.live)
        text = (acp_call("a", "execute", "Run Command", {"command": f"ls {live}/.dfs/runs"}, "Output:\n\nExit Code: 0") +
                acp_call("b", "execute", "Run Command", {"command": f"cat {live}/.dfs/items/W5.md"},
                         "Output:\nDetermination: the parser now reads a quoted pipe before the verb.\n\nExit Code: 0") +
                acp_call("c", "execute", "Run Command", {"command": f"grep -r x {live}/.dfs; which codex"},
                         "Output:\n" + live + "/.dfs/items/W5.md:1: x\n\nExit Code: 127", exit_code=127, status="failed") +
                acp_call("d", "read", "Read File", {"path": f"{self.dest}/a.py"}, "inside") +
                acp_call("e", "execute", "Run Command", {"command": 'git commit -m "W5.1: own"'}, "[main a1b2c3d] W5.1: own\n\nExit Code: 0") +
                acp_summary(7.0))
        kiro_run_dir(self.dest, runs=[("work", text)], console=False)

    def score(self):
        return replay.score(self.dest, "W5", self.live)

    def test_the_table_is_read_from_the_run_logs_with_credits_where_the_original_has_dollars(self):
        t = self.score()
        self.assertEqual((t["unit"], t["credits"], t["nodes"], t["finished"]), ("credits", 7.0, 1, True))
        self.assertEqual(t["sessions"], {"work": 1})   # the kind is from the task file's log, the run dir has no console.log
        buf = io.StringIO()
        with redirect_stdout(buf):
            replay.compare("W5", dict(t, unit="usd", usd=1.3, after_issues=0.0), t)
        rows = {l[:46].strip(): l[46:].split() for l in buf.getvalue().splitlines() if len(l) > 46}
        self.assertEqual(rows["dollars"], ["1.30", "n/a"])   # a credit is not put in the dollar row
        self.assertEqual(rows["credits (kiro)"], ["-", "7.0"])

    def test_the_breach_rows_read_the_run_logs_calls(self):
        t = self.score()
        self.assertEqual((t["blocked"], t["outside"]), (1, 2))   # a: nothing; b and c (a failing command after a read): output
        self.assertEqual(list(t["answer"]), ["the parser now reads a quoted pipe before the verb."[:60]])

    def test_kiros_own_wrapper_lines_are_not_output(self):
        self.assertTrue(replay.nothing_text("Output:\n\nExit Code: 0"))
        self.assertTrue(replay.nothing_text("Output:\nls: cannot access '/x': No such file or directory\n\nExit Code: 2"))
        self.assertFalse(replay.nothing_text("Output:\nworktrees\n\nExit Code: 127"))

    def test_a_replay_with_no_run_log_fails_loudly(self):
        shutil.rmtree(self.dest / ".dfs")
        with self.assertRaises(SystemExit) as e:
            self.score()
        self.assertIn("no session of W5", str(e.exception))


class HistoryKeepsReplaysApartTest(unittest.TestCase):
    def test_default_mining_skips_replay_transcripts_and_notes_the_first_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("-w-proj", "-tmp-dfs-replay-W5-a"):
                d = Path(tmp) / name
                d.mkdir()
                lines = [
                    {"type": "user", "timestamp": "2026-10-01T00:00:00Z", "message": {"content": "You are the implementation reviewer for task W5. x"}},
                    {"type": "assistant", "timestamp": "2026-10-01T00:01:00Z", "message": {"id": "m", "model": "m", "usage": {"input_tokens": 1, "output_tokens": 1}, "content": [
                        {"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": "tree.py log W5 review issues"}}]}},
                ]
                (d / "s.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines))
            _, tasks, _, _ = history.load(os.path.join(tmp, "*", "*.jsonl"), {})
            self.assertEqual([p for p, _ in tasks], ["-w-proj"])
            self.assertEqual(tasks[("-w-proj", "W5")]["first_review"], "issues")
            _, tasks, _, _ = history.load(os.path.join(tmp, "*", "*.jsonl"), {}, replays=True)
            self.assertEqual(len(tasks), 2)


if __name__ == "__main__":
    unittest.main()
