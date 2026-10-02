#!/usr/bin/env python3
"""A net under what the screen can read WHILE a chain is still going.

Both halves of this file are about the same report — "codex logs go into run.json
rather than console.log, and don't auto-scroll" — and neither half is about codex
being logged wrongly. Codex logs exactly as it should; what was shaped for Claude
alone is the pair of answers the screen gives about a RUNNING chain:

  · WHICH SESSION IS THIS. Claude is handed an id before it starts, so `meta.json`
    carries the live one from the first second. Codex mints its own and the runner
    read it back only after `wait`, so through the whole of run 2 the file still
    said run 1 — and the live pane, believing it, drew the PREVIOUS session's
    finished transcript under a heading naming it. Nothing empty, nothing red; a
    plausible pane that had stopped moving.

  · WHICH FILE IS MOVING. `-p --output-format json` writes one object at exit, so
    under Claude the console is the only live thing and follow was armed for it by
    name. `exec --json` streams, so under codex the run log is the live one and the
    console holds four lines of the runner's own output.

⚠️ The first is asserted as a TRANSITION, from inside the run that is happening: the
stub agent reads `meta.json` mid-session and writes down what it said, so the test
fails on a runner that records the id correctly but late, which is the whole bug.
A check after the chain ends passes against the unfixed runner.

Run:  python3 roadmap-dfs/scripts/test_live.py
"""
import curses
import importlib.util
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
import paths as dfs_paths            # noqa: E402
import runs as dfs_runs             # noqa: E402
import state as dfs_state            # noqa: E402

TUI_SPEC = importlib.util.spec_from_file_location("dfs_tui", HERE / "tui.py")
TUI = importlib.util.module_from_spec(TUI_SPEC)
TUI_SPEC.loader.exec_module(TUI)


def entry(kind, name, live, run="2"):
    """One row of the run list, as `discover_runs` builds it."""
    return dict(path="/runs/dfs_run_x/" + name, dir="/runs/dfs_run_x", name=name,
                kind=kind, mtime=0, size=1, item="W1", agent="codex", mode="work",
                run=dict(live=live, run=run, state="live" if live else "finished"))


class WhichFileIsMoving(unittest.TestCase):
    """`is_growing` — what follow is armed for, and what the list marks."""

    def test_a_live_chains_console_is_growing(self):
        self.assertTrue(TUI.is_growing(entry("console", "console.log", True)))

    def test_the_run_the_chain_is_in_is_growing(self):
        # The codex case: the stream IS the run log, and it is the file worth
        # sticking to the end of. Falsified against the old condition, which armed
        # follow for `kind == "console"` and nothing else.
        self.assertTrue(TUI.is_growing(entry("result", "run-2.json", True)))

    def test_a_finished_run_beside_a_live_one_is_not(self):
        # A chain on run 2 still has run-1.json next to it. Following the end of a
        # file nothing writes to is a footer saying `● following` about a pane that
        # will never move again.
        self.assertFalse(TUI.is_growing(entry("result", "run-1.json", True)))

    def test_nothing_in_a_finished_chain_is_growing(self):
        self.assertFalse(TUI.is_growing(entry("console", "console.log", False)))
        self.assertFalse(TUI.is_growing(entry("result", "run-2.json", False)))


class WhetherItSticks(unittest.TestCase):
    """`follows` — the other half of the same report, and the half `is_growing` is
    not: which FILE is moving, against whether the READER is at the end of it."""

    def test_a_growing_file_read_from_the_end_sticks(self):
        self.assertTrue(TUI.follows(True, False, 40, 40))

    def test_reading_back_down_to_the_end_starts_it_again(self):
        # The reported failure. `d`, space and PageDown all unfollowed, so the one
        # gesture that means "show me the latest" was the one that stopped the latest
        # arriving, and only `G` could undo it.
        self.assertTrue(TUI.follows(True, False, 41, 40))

    def test_scrolled_up_it_does_not(self):
        self.assertFalse(TUI.follows(True, False, 12, 40))

    def test_already_following_it_stays_so_wherever_the_end_is(self):
        # Following is pinned to the end, so `scroll` is a draw behind a file that
        # grew; it must not be read as the reader having scrolled up.
        self.assertTrue(TUI.follows(True, True, 40, 60))

    def test_a_file_nothing_writes_to_never_sticks(self):
        # ⚠️ Including one we were following when the chain ended: a footer saying
        # `● following` about a pane that will never move again is what `is_growing`
        # exists to prevent, and a latch would have kept it up.
        self.assertFalse(TUI.follows(False, True, 40, 40))
        self.assertFalse(TUI.follows(False, False, 40, 40))


class Screen:
    """Enough of a curses window to draw into and read back: `addstr` is the only
    thing the panes reach the terminal through (`UI.put`)."""

    def __init__(self, h=24, w=80):
        self.h, self.w, self.rows = h, w, {}

    def getmaxyx(self):
        return (self.h, self.w)

    def erase(self):
        self.rows = {}

    def addstr(self, y, x, text, attr=0):
        self.rows[y] = self.rows.get(y, "") + text

    def move(self, y, x):
        self.cursor = (y, x)

    def refresh(self):
        pass


class TheLogPaneKeepsUp(unittest.TestCase):
    """⚠️ The real `draw`, over a console that is really growing — because the bug was
    never in the decision, it was in WHERE it was made: a latch set by the keypress
    that opened the pane, and dropped by any scroll at all. A test on the policy alone
    would have passed against it.

    What is asserted is what a person sees: the NEWEST line is on the screen.
    """

    def setUp(self):
        curses.color_pair = lambda n: 0       # no initscr here; attrs are opaque
        self.live = True
        self._pid_is_chain = dfs_runs.pid_is_chain
        dfs_runs.pid_is_chain = lambda pid: self.live
        self._work_root, self._tmp = dfs_paths.work_root, dfs_runs.TMP
        self.root = Path(tempfile.mkdtemp())
        (self.root / ".dfs" / "items").mkdir(parents=True)
        dfs_paths.work_root = lambda: self.root
        dfs_paths.roadmap().write_text(ROADMAP)
        (dfs_paths.items() / "W1.md").write_text(ITEM)
        runroot = Path(tempfile.mkdtemp())
        dfs_runs.TMP = str(runroot)
        self.dir = runroot / "dfs_run_x"
        self.dir.mkdir()
        (self.dir / "meta.json").write_text(json.dumps(dict(
            item="W1", cap="5", agent="claude", mode="work", pid=os.getpid(),
            run="1", sid="", started="2026-09-25T10:00:00+00:00")))
        self.console = self.dir / "console.log"
        self.console.write_text("$ run.sh W1 5\n\n")
        self.ui = self.a_screen()

    def tearDown(self):
        dfs_runs.pid_is_chain = self._pid_is_chain
        dfs_paths.work_root, dfs_runs.TMP = self._work_root, self._tmp
        shutil.rmtree(self.root, ignore_errors=True)

    def a_screen(self):
        ui = object.__new__(TUI.UI)
        ui.scr = Screen()
        ui.data = dfs_state.full()
        ui.dirty = (0, 0)
        ui.pane, ui.open_run = "runs", None
        ui.focus = "list"
        ui.sel = ui.run_sel = ui.art_sel = ui.scroll = ui.list_scroll = 0
        ui.follow, ui.msg, ui.body_h, ui.agent = False, "", 1, "claude"
        ui._sel_row = None
        ui.walk_on, ui.walk_dir, ui.walk_note, ui.walk_next = False, None, "", None
        ui.walk_until = ui.walk_budget = ui.walk_spent = 0
        ui.walk_started = 0
        ui.walk_content = set()
        ui.sig = ()
        self.rescan(ui)
        return ui

    def rescan(self, ui=None):
        """What `reload` does to the run list, which is where liveness is read."""
        ui = ui or self.ui
        ui.chains = dfs_runs.run_dirs()
        ui.runs = TUI.discover_runs()
        if ui.open_run:
            ui.open_run = next(r for r in ui.runs
                               if r["path"] == ui.open_run["path"])

    def open_it(self):
        self.ui.run_sel = next(i for i, r in enumerate(self.ui.runs)
                               if r["kind"] == "console")
        self.ui.act(curses.KEY_ENTER if False else 10)      # Enter
        self.ui.draw()

    def grow(self, a, b):
        with self.console.open("a") as fh:
            for i in range(a, b):
                fh.write("line %d\n" % i)
        self.ui.draw()

    def newest(self):
        """The last of the log's own lines the screen is showing, or None."""
        body = [self.ui.scr.rows.get(y, "").strip()
                for y in range(4, self.ui.scr.h - 2)]
        shown = [l for l in body if l.startswith("line ")]
        return shown[-1] if shown else None

    def test_a_wide_screen_shows_the_live_chain_and_the_activity_beside_the_tree(self):
        # The column is what makes the walk watchable without leaving the tree.
        ui = self.ui
        ui.pane = "item"
        ui.tree_item, ui.tree_sel, ui.tree_folded, ui.tree_open = None, 0, set(), set()
        ui._tree_cache = {}
        ui.events = [(0.0, "run", "$ run.sh W1 5")]
        ui.scr = Screen(h=30, w=170)
        ui.draw()
        text = "\n".join(ui.scr.rows.values())
        self.assertIn("live · W1", text)
        self.assertIn("$ run.sh W1 5", text, "the console's tail")
        self.assertIn("activity", text)
        ui.scr = Screen(h=30, w=100)
        ui.draw()
        self.assertNotIn("live · W1", "\n".join(ui.scr.rows.values()),
                         "below SIDE_AT the main column keeps every cell")

    def test_a_pane_opened_on_a_live_chain_keeps_up(self):
        self.open_it()
        self.grow(1, 41)
        self.assertEqual(self.newest(), "line 40")
        self.grow(41, 61)
        self.assertEqual(self.newest(), "line 60")

    def test_scrolling_up_stops_it_and_reading_back_down_starts_it_again(self):
        self.open_it()
        self.grow(1, 61)
        self.ui.act(ord("k"))
        self.ui.draw()
        self.assertFalse(self.ui.follow, "scrolling up is reading, not watching")
        self.grow(61, 81)
        # Not dragged to the end: the window stays where the reader left it. (It is
        # not pinned to one LINE either — what the last row shows shifts as the lines
        # below the pane's own trailing notes arrive.)
        self.assertNotEqual(self.newest(), "line 80")
        for _ in range(40):                  # d, half a screen at a time, to the end
            self.ui.act(ord("d"))
        self.ui.draw()
        self.grow(81, 101)
        self.assertEqual(self.newest(), "line 100")

    def test_it_starts_following_on_the_tick_liveness_appears(self):
        # ⚠️ A run directory whose `meta.json` is not written yet has no pid and no
        # liveness to read (`dfs_runs.run_dirs`), and the measured gap is about 110ms
        # — so the old latch, decided once on Enter, was false for a chain that then
        # ran for an hour.
        self.live = False
        self.rescan()
        self.open_it()
        self.assertFalse(self.ui.follow)
        self.live = True
        self.rescan()
        self.ui.draw()
        self.assertTrue(self.ui.follow)
        self.grow(1, 41)
        self.assertEqual(self.newest(), "line 40")


# ── the runner, driven against a stub that behaves like codex ──────────────────

ROADMAP = """# Roadmap

## §0 How work is done

Each task is a decision tree.
"""

ITEM = """# W1 · the only thing

## Goal

The only thing.

## Tree

### W1.1 · the step
Status: open
Hypothesis: it can be done.
Evidence:

## Log
"""

# A stub standing in for `codex exec --json`: a JSONL stream whose FIRST line names
# the thread, exactly as codex's does, and which then takes long enough to be looked
# at. While it is running it reads `meta.json` — the one thing the screen has to go
# on — and writes down what it said, which is the observation the test is about.
STUB = r"""#!/usr/bin/env bash
set -euo pipefail
n=$(cat "$COUNT_FILE")
n=$((n + 1))
echo "$n" > "$COUNT_FILE"
tid="01a0b000-0000-7000-8000-00000000000$n"
echo "{\"type\":\"thread.started\",\"thread_id\":\"$tid\"}"
echo '{"type":"turn.started"}'
sleep 3
python3 - "$n" <<'PY' || true
import glob, json, os, sys
n = sys.argv[1]
seen = ""
for m in glob.glob(os.path.join(os.environ["DFS_RUN_DIR"], "*", "meta.json")):
    try:
        meta = json.load(open(m))
    except Exception:
        continue
    if str(meta.get("run") or "") == n:
        seen = str(meta.get("sid") or "")
open(os.path.join(os.environ["OBSERVED_DIR"], "observed-" + n), "w").write(seen)
PY
# Run 1 adds evidence to the node; run 2 confirms it, which completes the tree.
python3 - "$n" <<'PY'
import os, sys
from pathlib import Path
p = Path(os.environ["DFS_ITEMS"]) / "W1.md"
t = p.read_text()
t = t.replace("Evidence:\n", "Evidence:\n- for: run %s looked\n" % sys.argv[1])
if int(sys.argv[1]) >= 2:
    t = t.replace("Status: open", "Status: confirmed\nDetermination: done.")
p.write_text(t)
PY
echo '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":2}}'
"""


class WhichSessionIsThis(unittest.TestCase):
    """The id a codex chain records while it is IN the session, not after it."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / ".dfs" / "items").mkdir(parents=True)
        (self.root / ".dfs" / "ROADMAP.md").write_text(ROADMAP)
        (self.root / ".dfs" / "items" / "W1.md").write_text(ITEM)
        self.stub = self.root / "stub-codex"
        self.stub.write_text(STUB)
        self.stub.chmod(0o755)
        self.count = self.root / "count"
        self.count.write_text("0")
        self.observed = self.root / "observed"
        self.observed.mkdir()
        # `bash <path>` rather than the executable it is: a temp directory is
        # regularly on a noexec mount (this container's /tmp is), where the runner's
        # own `command -v` check rejects the stub and the chain exits 2 before
        # serving a run — the same trap test_deaths.py documents.
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        (self.root / ".gitignore").write_text("runs/\ncount\nobserved/\nstub-codex\n.dfs/runs/\n")
        subprocess.run("git add -A && git -c user.name=t -c user.email=t@t commit -qm init",
                       shell=True, cwd=self.root, check=True)
        env = dict(os.environ, CODEX_CMD="bash %s" % self.stub,
                   COUNT_FILE=str(self.count),
                   DFS_RUN_DIR=str(self.root / "runs"),
                   OBSERVED_DIR=str(self.observed), HOME=str(self.root))
        # A live chain's directory must never be this test's: see `export -n` in
        # run.sh, which this is the second half of.
        env.pop("RUNDIR", None)
        self.proc = subprocess.run(
            # Capped at the two work runs: confirming the node completes the tree,
            # and a third run would be the implementation review, which is not what
            # this test watches.
            [str(RUNNER), "--codex", "W1", "2"], cwd=self.root, env=env,
            capture_output=True, text=True, timeout=300)
        metas = sorted((self.root / "runs").glob("*/meta.json"),
                       key=lambda f: f.stat().st_mtime)
        self.meta = json.loads(metas[-1].read_text()) if metas else {}

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def observation(self, n):
        p = self.observed / ("observed-%d" % n)
        self.assertTrue(p.exists(), "run %d never read meta.json:\n%s"
                        % (n, self.proc.stdout))
        return p.read_text().strip()

    def test_the_chain_served_both_runs(self):
        self.assertEqual(int(self.count.read_text()), 2, self.proc.stdout)
        self.assertEqual(self.meta.get("stop"), "cap", self.proc.stdout)
        self.assertIn("W1.1 [confirmed]", self.proc.stdout)

    def test_a_running_session_has_named_itself(self):
        # ⚠️ Read from INSIDE run 1, which is the point: the id was correct after
        # `wait` all along, and a pane watching the run is what it was wrong for.
        self.assertEqual(self.observation(1), "01a0b000-0000-7000-8000-000000000001",
                         self.proc.stdout)

    def test_the_previous_runs_id_is_not_still_standing(self):
        # The failure this replaced: `meta.json` holds ONE sid, so run 1's left
        # there through run 2 pointed the live pane at a finished transcript.
        # Falsified against the unfixed runner, which observed run 1's id here.
        self.assertEqual(self.observation(2), "01a0b000-0000-7000-8000-000000000002",
                         self.proc.stdout)

    def test_the_id_is_still_right_once_the_run_ends(self):
        self.assertEqual(self.meta.get("sid"),
                         "01a0b000-0000-7000-8000-000000000002", self.proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
