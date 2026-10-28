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
                mock.patch.object(replay, "config_dir", return_value=str(Path(self.tmp.name) / "no-config")), \
                redirect_stdout(io.StringIO()):
            replay.run(out, "W5", cap=3, repo=self.repo)
        cmd = seen["cmd"]
        self.assertEqual(cmd[0], "/nix/store/p/bin/proot")
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
        (dest / ".git" / "replay.json").write_text(json.dumps({"task": "W5", "agent": "kiro"}))
        with self.assertRaises(SystemExit) as e:
            replay.score(dest, "W5")
        self.assertIn("ran under kiro", str(e.exception))
        (self.repo / ".dfs" / "runs" / "agent").write_text("kiro")
        with mock.patch.object(replay, "prepare") as pr, self.assertRaises(SystemExit) as e:
            replay.main(["replay", "W5", str(self.t / "dfs-replay-W5-n"), "--repo", str(self.repo)])
        self.assertIn("the agent is kiro", str(e.exception))
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
