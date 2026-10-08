#!/usr/bin/env python3
"""The web page's core flows, in a real browser (ui_flows.mjs): each flow is one test, on a
fresh copy of the example project, served by the real server, and what a flow WRITES is
judged here, on disk, as the terminal would read it.

Needs node with playwright and a chromium. Found, in order: `playwright` on PATH (the
nixpkgs `playwright-test` wrapper, read the way artefact_check.sh does), else node with
NODE_PATH or /opt/node-tools/node_modules naming it. Skipped, saying so, when there is none:
    nix shell nixpkgs#playwright-test -c python3 scripts/test_ui.py

Run:  python3 roadmap-dfs/scripts/test_ui.py
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths as dfs_paths    # noqa: E402
import tree as dfs_tree      # noqa: E402
import web as dfs_web        # noqa: E402
import test_walker           # noqa: E402  (the recording walker the walk controls talk to)

EXAMPLE = HERE.parent / "docs" / "tui-ux-review" / "items"
ARTEFACT = """<!doctype html><html><head><meta charset="utf-8"><title>Where the fsync lands</title></head>
<body><p id="r">script ran</p>
<script>fetch('/api/walk/start',{method:'POST',headers:{'Content-Type':'application/json'},body:'{"budget":1}'})
.then(r=>document.getElementById('r').textContent='reached '+r.status)
.catch(()=>document.getElementById('r').textContent='blocked')</script></body></html>"""


def browser_env():
    """(node, env) able to `import playwright`, or None."""
    pw = shutil.which("playwright")
    if pw:
        text = Path(pw).read_text(errors="replace")
        node = re.search(r'^exec "([^"]*)"', text, re.M)
        node_path = re.search(r"^NODE_PATH='([^']*)'\$NODE_PATH$", text, re.M)
        browsers = re.search(r"PLAYWRIGHT_BROWSERS_PATH=\$\{PLAYWRIGHT_BROWSERS_PATH-'([^']*)'\}", text)
        if node and Path(node.group(1)).exists():
            env = dict(os.environ, NODE_PATH=(node_path.group(1) if node_path else "")
                       + (":" + os.environ["NODE_PATH"] if os.environ.get("NODE_PATH") else ""))
            if browsers and "PLAYWRIGHT_BROWSERS_PATH" not in env:
                env["PLAYWRIGHT_BROWSERS_PATH"] = browsers.group(1)
            return node.group(1), env
    node = shutil.which("node")
    if node:
        for path in [p for p in (os.environ.get("NODE_PATH"), "/opt/node-tools/node_modules") if p]:
            if any((Path(q) / "playwright").is_dir() for q in path.split(":")):
                return node, dict(os.environ, NODE_PATH=path)
    return None


class Flows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = browser_env()
        if cls.node is None:
            raise unittest.SkipTest("no playwright: nix shell nixpkgs#playwright-test -c python3 scripts/test_ui.py")

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        items = self.root / ".dfs" / "items"
        items.mkdir(parents=True)
        for f in EXAMPLE.glob("*.md"):
            shutil.copy(f, items / f.name)
        (self.root / ".dfs" / "ROADMAP.md").write_text("# Roadmap\n")
        (self.root / ".dfs" / "title").write_text("Gateway\n")
        (self.root / ".dfs" / "artefacts").mkdir()
        (self.root / ".dfs" / "artefacts" / "7f3a9c1e-fsync.html").write_text(ARTEFACT)
        w9 = items / "W9.md"
        w9.write_text(w9.read_text().replace("Strongest case against:",
                                             "Picture: .dfs/artefacts/7f3a9c1e-fsync.html. Strongest case against:", 1))
        subprocess.run("git init -q -b main && git add -A && git -c user.name=t -c user.email=t@t commit -qm x",
                       shell=True, cwd=self.root, check=True)
        import test_chat
        stub = self.root / "claude-stub"           # the agent a chat turn runs: a claude that answers
        stub.write_text(test_chat.STUB)
        stub.chmod(0o755)
        self._saved = {k: os.environ.get(k) for k in ("CLAUDE_CMD", "CHAT_STUB_LOG", "HOME", "CHAT_STUB_SAY")}
        (self.root / "home").mkdir()
        os.environ.update(CLAUDE_CMD=str(stub), CHAT_STUB_LOG=str(self.root / "stub.log"), HOME=str(self.root / "home"),
                          CHAT_STUB_SAY="Got it: **W9.2** is in `journal_mode`.")
        dfs_web.CHAT["chats"] = None
        self._wr = dfs_paths.work_root
        dfs_paths.work_root = lambda: self.root
        dfs_paths._TAG_CACHE.clear()
        dfs_web._commit_cache.clear()
        self.fake = test_walker.FakeWalker()
        self.handle = dfs_web.serve("127.0.0.1", 0, walker=self.fake)

    def tearDown(self):
        self.handle.stop()
        dfs_web.WALK.update(enabled=True, walker=None)
        dfs_web.CHAT["chats"] = None
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        dfs_paths.work_root = self._wr
        dfs_paths._TAG_CACHE.clear()
        shutil.rmtree(self.root, ignore_errors=True)

    def flow(self, name):
        node, env = self.node
        r = subprocess.run([node, str(HERE / "ui_flows.mjs"), name, self.handle.url.rstrip("/"), str(self.root)],
                           capture_output=True, text=True, env=env, timeout=240)
        self.assertEqual(r.returncode, 0, (r.stderr or r.stdout).strip()[-1500:])

    def log(self, task):
        return dfs_tree.load(task)["log"]

    def test_the_list_the_filter_and_the_project_name(self):
        self.flow("list")

    def test_a_task_and_its_tree(self):
        self.flow("task_tree")

    def test_the_node_sheet_keeps_prev_and_next_where_they_are(self):
        self.flow("node_sheet")

    def test_answering_a_raise_writes_the_terminals_log_entry(self):
        self.flow("answer")
        answers = [e for e in self.log("W9") if e["kind"] == "answer"]
        self.assertEqual([e["body"].strip() for e in answers], ["Spinning disks. Test on one."])
        self.assertEqual(dfs_tree.open_raises(dfs_tree.load("W9")), [])

    def test_refute_confirm_states_the_outcome_and_writes_the_correction(self):
        self.flow("correct")
        corr = [e for e in self.log("W9") if e["kind"] == "correct"]
        self.assertEqual([(e["args"], e["body"].strip()) for e in corr],
                         [(["W9.2", "refuted"], "WAL is wrong on this disk.")])

    def test_adding_a_node(self):
        self.flow("add")
        titles = [n["title"] for n in dfs_tree.load("W3")["nodes"]]
        self.assertIn("Cover the stampede with a test", titles)

    def test_accepting_a_finished_tree(self):
        self.flow("accept")
        self.assertTrue(dfs_tree.accepted(dfs_tree.load("W4")))

    def test_the_walk_controls(self):
        self.flow("walk")
        self.assertEqual(self.fake.started_chains[0][1], "7")
        self.assertEqual(self.fake.chosen, "codex")

    def test_navigation_is_under_the_thumbs(self):
        self.flow("thumbs")

    def test_several_chats_are_found_again_in_the_chats_section(self):
        self.flow("chats")

    def test_a_new_task_from_the_bottom_of_the_list_and_the_title_is_the_rename(self):
        self.flow("newtask")

    def test_reordering_the_task_list_writes_order_md(self):
        self.flow("reorder")

    def test_a_forms_buttons_clear_the_keyboard(self):
        self.flow("keyboard")

    def test_chat_about_a_task_and_the_project(self):
        self.flow("chat")

    def test_the_theme_switch_flips_shows_the_mode_and_remembers(self):
        self.flow("theme")

    def test_renaming_the_project_writes_dfs_title(self):
        self.flow("rename")
        self.assertEqual((self.root / ".dfs" / "title").read_text(), "Gateway core\n")

    def test_the_project_colour_is_chosen_in_the_dialog_and_writes_dfs_theme(self):
        self.flow("colour")
        self.assertEqual((self.root / ".dfs" / "theme").read_text(), "#aa3355\n")

    def test_artefacts_are_linked_and_sandboxed(self):
        self.flow("artefacts")

    def test_prose_is_drawn_as_markdown_and_stays_inert(self):
        self.flow("richtext")

    def test_a_slow_read_shows_loading_and_clears_it(self):
        self.flow("loading")

    def test_a_first_load_that_fails_shows_the_error_and_a_retry_not_loading(self):
        self.flow("loadfail")

    def test_a_change_elsewhere_reaches_an_open_page(self):
        self.flow("live")

    def test_the_design_system_specimen_passes_what_it_asks_of_the_page(self):
        self.flow("design")

    def test_every_screen_in_both_themes_and_three_widths_is_readable_and_reachable(self):
        self.flow("layout")


if __name__ == "__main__":
    unittest.main(verbosity=2)
