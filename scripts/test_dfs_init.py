#!/usr/bin/env python3
"""dfs_init.py: what it writes into a new repo, what it leaves alone, and where it
refuses.

Run:  python3 <scripts>/test_dfs_init.py
"""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dfs_check  # noqa: E402
import dfs_init   # noqa: E402
import dfs_paths  # noqa: E402


class Init(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self._wr = dfs_paths.work_root
        dfs_paths.work_root = lambda: self.root
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.root, check=True)

    def tearDown(self):
        dfs_paths.work_root = self._wr
        shutil.rmtree(self.root, ignore_errors=True)

    def run_init(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = dfs_init.main(list(args))
        return rc, out.getvalue() + err.getvalue()

    def test_a_new_repo_gets_a_roadmap_and_both_hooks(self):
        rc, out = self.run_init("--playwright-shell", ".#e2e")
        self.assertEqual(rc, 0, out)
        self.assertTrue(dfs_paths.has_roadmap())
        self.assertIn("## §0 How work is done", dfs_paths.roadmap().read_text())
        self.assertTrue(dfs_paths.items().is_dir())
        self.assertIn("runs/", (dfs_paths.state() / ".gitignore").read_text().split())
        self.assertEqual(dfs_paths.playwright_shell().read_text(), ".#e2e\n")
        for hook in dfs_init.HOOKS:
            text = (self.root / ".git" / "hooks" / hook).read_text()
            self.assertIn("dfs_hook.sh\" %s" % hook, text)
            self.assertTrue((self.root / ".git" / "hooks" / hook).stat().st_mode & 0o100)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(dfs_check.main(), 0)

    def test_a_second_run_keeps_everything(self):
        self.run_init()
        dfs_paths.roadmap().write_text("# Mine\n")
        rc, out = self.run_init()
        self.assertEqual(rc, 0, out)
        self.assertEqual(dfs_paths.roadmap().read_text(), "# Mine\n")
        self.assertNotIn("  wrote ", out)
        self.assertEqual((dfs_paths.state() / ".gitignore").read_text().count("runs/"), 1)

    def test_the_projects_own_hook_is_left_and_the_lines_are_printed(self):
        hook = self.root / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\nmake lint\n")
        rc, out = self.run_init()
        self.assertEqual(rc, 0, out)
        self.assertEqual(hook.read_text(), "#!/bin/sh\nmake lint\n")
        self.assertIn("was not edited", out)
        self.assertIn("dfs_hook.sh\" pre-commit", out)

    def test_core_hooks_path_is_where_the_hooks_go(self):
        subprocess.run(["git", "config", "core.hooksPath", "githooks"], cwd=self.root, check=True)
        self.run_init()
        self.assertTrue((self.root / "githooks" / "pre-merge-commit").exists())

    def test_it_refuses_below_the_repo_root_and_outside_git(self):
        (self.root / "sub").mkdir()
        dfs_paths.work_root = lambda: self.root / "sub"
        rc, out = self.run_init()
        self.assertEqual(rc, 2)
        self.assertIn("repo's root", out)
        plain = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, plain, True)
        dfs_paths.work_root = lambda: plain
        rc, out = self.run_init()
        self.assertEqual(rc, 2)
        self.assertIn("not in a git repo", out)

    def from_the_store(self):
        """As `nix run <flake>#dfs-init` runs it: the tool in the store, and nothing on
        PATH but sh and git, so no dfs-* command. The bin directory is beside this
        file, not in the temp directory, which is regularly on a noexec mount (this
        container's /tmp is) where a stub on PATH cannot run."""
        self.bin = Path(tempfile.mkdtemp(prefix=".test-bin-", dir=HERE))
        self.addCleanup(shutil.rmtree, self.bin, True)
        for tool in ("sh", "git"):
            (self.bin / tool).symlink_to(shutil.which(tool))
        tool, path = dfs_paths.TOOL_ROOT, os.environ["PATH"]
        dfs_paths.TOOL_ROOT = Path("/nix/store/0000-roadmap-dfs/share/roadmap-dfs")
        os.environ["PATH"] = str(self.bin)
        self.addCleanup(setattr, dfs_paths, "TOOL_ROOT", tool)
        self.addCleanup(os.environ.__setitem__, "PATH", path)

    def test_from_the_flake_the_next_steps_name_the_flake(self):
        self.from_the_store()
        rc, out = self.run_init()
        self.assertEqual(rc, 0, out)
        self.assertIn("nix run %s#dfs-run -- --open" % dfs_paths.FLAKE, out)
        self.assertIn("Or the screen:    nix run %s#dfs-tui\n" % dfs_paths.FLAKE, out)
        self.assertIn("    nix run %s#dfs-init" % dfs_paths.FLAKE, dfs_paths.missing_message())

    def test_from_the_flake_the_hook_runs_the_guard_from_the_flake(self):
        """No DFS_TOOL and no dfs-hook on PATH: a commit touching .dfs runs the guard
        with `nix run`, and exits with it; one that does not never calls nix."""
        self.from_the_store()
        self.run_init()
        (self.bin / "nix").write_text('#!/bin/sh\necho "$@" > "%s/nix-args"\nexit 1\n' % self.root)
        (self.bin / "nix").chmod(0o755)
        hook = self.root / ".git" / "hooks" / "pre-commit"
        env = {"PATH": str(self.bin), "HOME": str(self.root)}
        run = lambda: subprocess.run(["sh", str(hook)], cwd=self.root, env=env,
                                     capture_output=True, text=True)
        (self.root / "code.txt").write_text("x\n")
        subprocess.run(["git", "add", "code.txt"], cwd=self.root, check=True)
        self.assertEqual(run().returncode, 0)
        self.assertFalse((self.root / "nix-args").exists())
        subprocess.run(["git", "add", ".dfs"], cwd=self.root, check=True)
        self.assertEqual(run().returncode, 1)
        self.assertEqual((self.root / "nix-args").read_text(),
                         "run %s#dfs-hook -- pre-commit\n" % dfs_paths.FLAKE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
