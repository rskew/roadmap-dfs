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
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
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

CHOOSE = """<!doctype html><html><head><meta charset="utf-8"><title>Which disk</title></head>
<body><button id="send">Send as my answer</button><p id="r"></p><script>
document.getElementById('send').addEventListener('click', function () {
  fetch('/artefact-state/c0ffee-choose.html', {method: 'POST', body: JSON.stringify(
    {state: {picked: 'spinning'}, answer: 'Spinning disks. Test on one.'})})
  .then(function (r) { document.getElementById('r').textContent = 'status ' + r.status;
                       if (r.ok) parent.postMessage({dfs: 'answered'}, '*'); });
});</script></body></html>"""


RULE = """<!doctype html><html><head><meta charset="utf-8"><title>Is WAL safe</title></head>
<body><button id="refute">Refute</button><script>
document.getElementById('refute').addEventListener('click', function () {
  fetch('/artefact-state/c0ffee-rule.html', {method: 'POST', body: JSON.stringify(
    {verdict: 'refuted', answer: 'WAL loses commits we cannot lose.'})})
  .then(function (r) { if (r.ok) parent.postMessage({dfs: 'answered'}, '*'); });
});</script></body></html>"""


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
        self.wait_for_requests()
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

    def wait_for_requests(self, timeout=10):
        """The server's handler threads are daemons, which `stop()` does not join: a request the
        browser had in flight when its flow ended still runs, and would read (and write the
        title of) the real checkout once `work_root` is put back."""
        end = time.time() + timeout
        for t in threading.enumerate():
            if "process_request_thread" in t.name:
                t.join(max(0, end - time.time()))

    def flow(self, name):
        node, env = self.node
        r = subprocess.run([node, str(HERE / "ui_flows.mjs"), name, self.handle.url.rstrip("/"), str(self.root)],
                           capture_output=True, text=True, env=env, timeout=240)
        self.assertEqual(r.returncode, 0, (r.stderr or r.stdout).strip()[-1500:])

    def log(self, task):
        return dfs_tree.load(task)["log"]

    def test_the_claude_account_is_signed_in_from_the_project_dialog(self):
        import stat
        import test_login
        stub = self.root / "login-stub"
        stub.write_text(test_login.STUB)
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        saved = {k: os.environ.get(k) for k in ("CLAUDE_CMD", "LOGIN_STUB_DIR")}
        os.environ.update(CLAUDE_CMD=str(stub), LOGIN_STUB_DIR=str(self.root))
        old, dfs_web.LOGIN = dfs_web.LOGIN, dfs_web.dfs_login.Login()
        try:
            self.flow("account")
        finally:
            dfs_web.LOGIN.cancel()
            dfs_web.LOGIN = old
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_the_list_the_filter_and_the_project_name(self):
        self.flow("list")

    def test_going_back_from_a_task_draws_the_list_once_and_stays(self):
        self.flow("backflash")

    def test_going_back_to_the_list_draws_it_without_waiting_on_its_reads(self):
        self.flow("backinstant")

    def test_a_task_and_its_tree(self):
        self.flow("task_tree")

    def test_each_screen_keeps_its_scroll_on_back_and_reload(self):
        self.flow("scroll_memory")

    def test_nothing_on_the_task_list_stops_the_browsers_own_pull_to_refresh(self):
        self.flow("native_pull")

    def test_the_node_sheet_and_the_chat_log_keep_their_scroll(self):
        self.flow("scroll_memory_areas")

    def test_the_chats_top_button_goes_to_the_top_of_the_message_being_read(self):
        self.flow("chat_top")

    def test_a_chat_names_the_agent_it_runs_under_in_its_header_and_in_the_list(self):
        self.flow("chat_agent")

    def test_the_chat_follows_a_reader_near_its_end_or_just_after_sending_and_no_one_else(self):
        self.flow("chat_follow")

    def test_the_node_sheet_keeps_prev_and_next_where_they_are(self):
        self.flow("node_sheet")

    def test_the_page_a_raise_names_records_the_answer_and_keeps_its_state(self):
        (self.root / ".dfs" / "artefacts" / "c0ffee-choose.html").write_text(CHOOSE)
        w9 = self.root / ".dfs" / "items" / "W9.md"
        text = w9.read_text()
        tail = "WAL checkpoints may stall readers anyway."
        self.assertIn(tail, text)
        w9.write_text(text.replace(tail, tail + "\n  Answer with: .dfs/artefacts/c0ffee-choose.html", 1))
        self.flow("answer_page")
        answers = [e for e in self.log("W9") if e["kind"] == "answer"]
        self.assertEqual([e["body"].strip() for e in answers], ["Spinning disks. Test on one."])
        self.assertEqual(json.loads((self.root / ".dfs" / "artefact-state" / "c0ffee-choose.html.json").read_text()),
                         {"picked": "spinning"})

    def test_the_answer_templates_form_answers_a_raise_with_the_option_and_the_note(self):
        self.answer_template_raises()

    def test_the_answer_form_still_answers_under_a_page_that_uses_its_ids(self):
        # A free page draws its own answer and confirm flow, and may well call its parts what
        # the form once did: none of those ids may take the form's lookups.
        self.answer_template_raises(above='<div class="box" id="answer"></div><p id="note">x</p>'
                                         '<span id="confirm"></span><div id="answerbox"></div><i id="rulebox"></i>')

    def answer_template_raises(self, above=""):
        page = (HERE.parent / "templates" / "_TEMPLATE_ANSWER.html").read_text()
        marker = "<!-- ===== The answer form"
        self.assertIn(marker, page)
        (self.root / ".dfs" / "artefacts" / "c0ffee-ans.html").write_text(page.replace(marker, above + marker, 1))
        w9 = self.root / ".dfs" / "items" / "W9.md"
        text = w9.read_text()
        tail = "WAL checkpoints may stall readers anyway."
        self.assertIn(tail, text)
        w9.write_text(text.replace(tail, tail + "\n  Answer with: .dfs/artefacts/c0ffee-ans.html", 1))
        self.flow("template_answer")
        answers = [e for e in self.log("W9") if e["kind"] == "answer"]
        self.assertEqual([e["body"].strip() for e in answers],
                         ["Take the second option: what it commits the work to.\n\nTest on one."])
        state = json.loads((self.root / ".dfs" / "artefact-state" / "c0ffee-ans.html.json").read_text())
        self.assertEqual(state["option"], "Take the second option: what it commits the work to.")

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

    def test_a_picture_attached_to_a_correction_is_kept_and_drawn(self):
        self.flow("attach")
        corr = [e for e in self.log("W9") if e["kind"] == "correct"]
        self.assertEqual(len(corr), 1)
        ref = r"!\[%s\]\(\.dfs/artefacts/([0-9a-f-]+\.png)\)"
        m = re.fullmatch(r"Look at this: " + ref % r"the bar\.png" + " " + ref % r"pasted-[\d-]+\.png" + " " + ref % r"pasted-[\d-]+\.png"
                         + r" And \[my notes\]\(\.dfs/artefacts/7f3a9c1e-fsync\.html\)\.", corr[0]["body"].strip())
        self.assertTrue(m, corr[0]["body"])   # the file input, a paste and a drop each put a reference in
        for name in m.groups():
            self.assertTrue((self.root / ".dfs" / "artefacts" / name).read_bytes().startswith(b"\x89PNG"))

    def test_confirming_over_a_sibling_that_holds_work_asks_to_park_it(self):
        dfs_tree.start_node("W9", "W9.10")
        self.flow("park")
        corr = [e for e in self.log("W9") if e["kind"] == "correct"]
        self.assertEqual([(e["args"], e["body"].strip()) for e in corr],
                         [(["W9.9", "confirmed"], "Spinning disks are fine.")])

    def test_the_assumption_template_holds_confirm_until_the_siblings_are_parked(self):
        shutil.copy(HERE.parent / "templates" / "_TEMPLATE_ANSWER.html", self.root / ".dfs" / "artefacts" / "c0ffee-tpl.html")
        w9 = self.root / ".dfs" / "items" / "W9.md"
        text = w9.read_text()
        head = "### W9.9 \u00b7 Check the spinning-disk case\nParent: W9.8\nStatus: open\n"
        self.assertIn(head, text)
        w9.write_text(text.replace(head, head + "Hypothesis: .dfs/artefacts/c0ffee-tpl.html Disks are fine.\n", 1))
        dfs_tree.start_node("W9", "W9.10")
        self.flow("park_page")
        corr = [e for e in self.log("W9") if e["kind"] == "correct"]
        self.assertEqual([e["args"] for e in corr], [["W9.9", "confirmed"]])

    def test_the_page_a_hypothesis_names_rules_on_its_node(self):
        (self.root / ".dfs" / "artefacts" / "c0ffee-rule.html").write_text(RULE)
        w9 = self.root / ".dfs" / "items" / "W9.md"
        text = w9.read_text()
        head = "Hypothesis: WAL with synchronous=NORMAL"
        self.assertIn(head, text)
        w9.write_text(text.replace(head, "Hypothesis: .dfs/artefacts/c0ffee-rule.html WAL with synchronous=NORMAL", 1))
        self.flow("rule_page")
        corr = [e for e in self.log("W9") if e["kind"] == "correct"]
        self.assertEqual([(e["args"], e["body"].strip()) for e in corr],
                         [(["W9.2", "refuted"], "WAL loses commits we cannot lose.")])

    def test_adding_a_node(self):
        self.flow("add")
        titles = [n["title"] for n in dfs_tree.load("W3")["nodes"]]
        self.assertIn("Cover the stampede with a test", titles)

    def test_deleting_a_node_and_a_task(self):
        self.flow("delete")
        self.assertFalse(dfs_tree.exists("W3"))
        self.assertTrue((self.root / ".dfs" / "archive" / "deleted" / "items" / "W3.md").is_file())

    def test_archiving_a_finished_task_that_is_on_origin(self):
        sh = lambda c, cwd=self.root: subprocess.run(c, shell=True, cwd=cwd, check=True, capture_output=True)
        dfs_tree.append_log("W4", "accept")
        sh("git add -A && git -c user.name=t -c user.email=t@t commit -qm 'W4: accepted'")
        remote = Path(tempfile.mkdtemp())
        try:
            sh("git init -q --bare -b main", remote)
            sh("git remote add origin %s && git push -q -u origin main" % remote)
            dfs_tree._unpushed_seen.clear()
            self.flow("archive")
        finally:
            shutil.rmtree(remote, ignore_errors=True)
        self.assertFalse(dfs_tree.exists("W4"))
        self.assertTrue((self.root / ".dfs" / "archive" / "archived" / "items" / "W4.md").is_file())

    def test_accepting_a_finished_tree(self):
        self.flow("accept")
        self.assertTrue(dfs_tree.accepted(dfs_tree.load("W4")))

    def test_the_walk_controls(self):
        self.flow("walk")
        # The box's Budget 7 and Chains 2 both reach the walk, and the budget is split between the two chains. The flow
        # starts it three times, by Enter in each number field and by the button, none of them asking first.
        self.assertEqual(sorted(cap for _, cap in self.fake.started_chains), ["3", "3", "3", "4", "4", "4"])
        self.assertEqual(self.fake.chosen, "codex")

    def test_the_walk_controls_stay_under_the_bar_as_the_list_scrolls(self):
        self.flow("walkpin")

    def test_navigation_is_under_the_thumbs(self):
        self.flow("thumbs")

    def test_a_new_chat_starts_with_its_opening_prompt_and_no_text_of_the_pages_own(self):
        self.flow("chatempty")

    def test_stop_is_shown_while_the_agent_answers_and_asks_the_server_to_stop_it(self):
        self.flow("chatstop")

    def test_several_chats_are_found_again_in_the_chats_section(self):
        self.flow("chats")

    def test_a_new_task_from_the_bottom_of_the_list_and_the_title_is_the_rename(self):
        self.flow("newtask")

    def test_a_task_is_made_a_draft_in_the_form_and_toggled_on_its_screen(self):
        self.flow("draft")

    def test_a_long_name_wraps_in_its_field_and_enter_saves_it_on_one_line(self):
        self.flow("namefield")
        self.assertEqual(open(self.root / ".dfs" / "items" / "W3.md").readline().strip(), "# W3 · Stop cache stampedes")

    def test_reordering_the_task_list_writes_order_md(self):
        self.flow("reorder")

    def test_a_dropped_row_is_drawn_in_place_while_it_saves(self):
        self.flow("reorderpending")

    def test_a_nudge_across_a_break_is_drawn_where_the_server_puts_it(self):
        self.flow("reorderbreak")

    def test_a_drag_lands_either_side_of_a_break(self):
        self.flow("reorderbreakdrag")

    def test_reorder_keeps_the_list_where_it_was_scrolled(self):
        self.flow("reorderkeepsscroll")

    def test_all_in_the_bar_leaves_reorder(self):
        self.flow("allleavesreorder")

    def test_a_task_says_if_a_chain_is_running_on_it_and_if_it_is_next(self):
        self.flow("walktags")

    def test_the_lists_bar_buttons_work_from_chats(self):
        self.flow("barfromchats")

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

    def test_a_background_per_mode_is_chosen_in_the_dialog_and_writes_dfs_background(self):
        self.flow("background")
        self.assertFalse((self.root / ".dfs" / "background").exists())
        self.assertFalse((self.root / ".dfs" / "theme").exists())

    def test_a_background_picture_is_chosen_in_the_dialog_and_writes_dfs_background_image(self):
        self.flow("picture")
        self.assertFalse((self.root / ".dfs" / "background-image").exists(), "removed again at the end")
        self.assertFalse((self.root / ".dfs" / "background").exists())

    def test_artefacts_are_linked_and_sandboxed(self):
        self.flow("artefacts")

    def test_prose_is_drawn_as_markdown_and_stays_inert(self):
        self.flow("richtext")

    def test_a_slow_read_shows_loading_and_clears_it(self):
        self.flow("loading")

    def test_a_first_load_that_fails_shows_the_error_and_a_retry_not_loading(self):
        self.flow("loadfail")

    def test_a_tasks_runs_open_their_log_and_a_live_log_grows(self):
        self.with_runs("run_log")

    def test_a_live_runs_log_takes_a_message_for_its_session_and_lists_it(self):
        import runs as dfs_runs
        self.with_runs("run_message")
        live = next(r for r in dfs_runs.run_dirs() if r["item"] == "W9" and not r["finished"])
        got = dfs_runs.messages(live["dir"])
        self.assertEqual([(m["text"], m["state"]) for m in got], [("Use the other table", "waiting")],
                         "the box left one message in the live chain's inbox, and none in the finished one's")
        self.assertEqual([m for r in dfs_runs.run_dirs() if r["finished"] for m in dfs_runs.messages(r["dir"])], [])

    def with_runs(self, flow):
        import runs as dfs_runs
        runs = self.root / ".dfs" / "runs"
        for name, finished, console in (
                ("dfs_run_done", "2026-10-09T03:54:17+00:00", "$ run.sh W9 5\n── run 1/5 · work ──\n   exit 0 · 17 turns\n"),
                ("dfs_run_live", "", "$ run.sh W9 5\n\ndfs_run: task W9, cap 5, agent claude\n\n── run 2/5 · work (the author answered) ──\n")):
            (runs / name).mkdir(parents=True)
            (runs / name / "console.log").write_text(console)
            (runs / name / "meta.json").write_text(json.dumps(dict(
                item="W9", agent="claude", mode="work", cap="5", run="2" if finished == "" else "1", pid="4000000",
                started="2026-10-09T03:47:39+00:00", finished=finished, turns="17" if finished else "")))
        real = dfs_runs.pid_is_chain
        dfs_runs.pid_is_chain = lambda pid: True
        try:
            self.flow(flow)
        finally:
            dfs_runs.pid_is_chain = real

    def test_a_codex_runs_session_opens_beside_its_console_and_grows(self):
        import runs as dfs_runs
        d = self.root / ".dfs" / "runs" / "dfs_run_codex"
        d.mkdir(parents=True)
        (d / "console.log").write_text("dfs_run: task W9, cap 5, agent codex\n\n── run 1/5 · work ──\n")
        (d / "meta.json").write_text(json.dumps(dict(
            item="W9", agent="codex", mode="work", cap="5", run="1", pid="4000000",
            started="2026-10-09T03:47:39+00:00", finished="")))
        cmd = "/bin/bash -lc \"sed -n '1,40p' .dfs/items/W9.md\""
        (d / "run-1.json").write_text("".join(json.dumps(e) + "\n" for e in (
            {"type": "thread.started", "thread_id": "01a14374-0252-7903-b819-bc05e4439f47"},
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "Reading the task file first."}},
            {"type": "item.completed", "item": {"id": "item_1", "type": "command_execution", "command": cmd,
                                                "aggregated_output": "# W9 · Something\n", "exit_code": 0, "status": "completed"}})))
        real = dfs_runs.pid_is_chain
        dfs_runs.pid_is_chain = lambda pid: True
        try:
            self.flow("run_session")
        finally:
            dfs_runs.pid_is_chain = real

    def test_a_child_is_drawn_right_of_its_parent_and_siblings_share_a_column(self):
        self.flow("indent")

    def test_every_live_node_shows_whether_it_is_complete_started_or_not_begun(self):
        dfs_tree.start_node("W9", "W9.9")
        # The author confirmed W9.11 without any session building it: no tick, and it says so.
        w9 = self.root / ".dfs" / "items" / "W9.md"
        w9.write_text(w9.read_text().rstrip("\n") + "\n- 2026-10-09T05:00:00Z · correct · W9.11 · confirmed\n  Fine as it is.\n")
        self.flow("progress")

    def test_a_change_elsewhere_reaches_an_open_page(self):
        self.flow("live")

    def test_the_design_system_specimen_passes_what_it_asks_of_the_page(self):
        self.flow("design")

    def test_a_big_screen_gets_a_rail_scaled_type_and_a_pointer_and_keys(self):
        self.flow("wide")

    def test_scrolling_selects_the_control_in_the_middle_and_leaves_room_to_centre_the_ends(self):
        self.flow("mid")

    def tag_a_task(self):
        """A task begun on a branch, `W1@serve-real-records`, beside the main branch's W1."""
        items = self.root / ".dfs" / "items"
        (items / "W1@serve-real-records.md").write_text(
            "# W1@serve-real-records · Serve the real records\n\n## Goal\n\nServe the records from the store, not the fixture.\n\n"
            "## Tree\n\n### W1@serve-real-records.1 · Read from the store\nStatus: open\n"
            "Approach: Point the handler at the store.\n\n## Log\n")
        dfs_paths._TAG_CACHE.clear()

    def test_a_branch_tagged_id_fits_its_row_at_every_width(self):
        self.tag_a_task()
        self.flow("tagged")

    def test_every_screen_in_both_themes_and_five_widths_is_readable_and_reachable(self):
        self.flow("layout")


if __name__ == "__main__":
    unittest.main(verbosity=2)
