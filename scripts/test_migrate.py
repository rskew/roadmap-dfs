#!/usr/bin/env python3
"""Moving an old task (task file, archive, log file) into a history file, losslessly (W203.3).

Run:  python3 roadmap-dfs/scripts/test_migrate.py
"""
import contextlib
import io
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import taskhistory as th    # noqa: E402
import tree as dfs_tree     # noqa: E402

TASK = """# W5 · sample

## Summary

It works.

## Goal

Make it.

## Background

Why it is so.

## Tree

### W5.1 · first
Status: confirmed
Approach: archived.
Hypothesis: archived.
Evidence: archived.
Determination: It held (archived.)

### W5.2 · second
Parent: W5.1
Status: confirmed
Approach: do the second thing
Evidence:
- for: it ran
- against: it jammed once
Determination: Confirmed: done.

### W5.3 · third
Parent: W5.2
Status: started
Approach: do the third thing
  on two lines

## Log

- 2026-01-02T10:00:00Z · session · work · run-2
"""

ARCHIVE = """**W5.1 Approach**: the full first approach
**W5.1 Hypothesis**: the first thing holds. Wrong if it does not.
**W5.1 Determination**: It held, on every case.

**W5.1 Evidence**:
- for: it ran on the bench
- against: it was slow
  at the start

**Log**:
- 2026-01-01T09:00:00Z · session · work · run-1
- 2026-01-01T09:30:00Z · raise · W5.1
  What I need from you: a or b?
"""

LOGFILE = """# W5 · log

- 2026-01-03T08:00:00Z · answer · 2026-01-01T09:30:00Z
  a.
"""


def evidence_blocks(archive):
    """{node id: {item, whitespace folded}} for each `**<id> Evidence**:` block of an archive,
    read on its own (check.archived_evidence also takes a `- ` line after an Approach)."""
    out, node, item = {}, None, None
    for line in archive.splitlines():
        m = re.match(r"^\*\*(%s) Evidence\*\*:\s*$" % dfs_tree.NODE_RE, line)
        if line.startswith(("#", "**")):
            node, item = (m.group(1) if m else None), None
        elif node and line.lstrip().startswith("- "):
            item = [line.strip()[2:]]
            out.setdefault(node, []).append(item)
        elif node and line.strip() and item is not None:
            item.append(line.strip())
    return {n: {" ".join(" ".join(i).split()) for i in items} for n, items in out.items()}


class Migrate(unittest.TestCase):
    def migrated(self, task=TASK, archive=ARCHIVE, log=LOGFILE):
        return th.migrate("W5", task, archive, log)

    def nodes(self, r):
        return {n["id"]: n for n in th.parse(r["history"], "W5")["nodes"]}

    def test_every_node_gets_a_section_with_its_stubs_put_back(self):
        r = self.migrated()
        n = self.nodes(r)
        self.assertEqual(list(n), ["W5.1", "W5.2", "W5.3"])
        f = n["W5.1"]["fields"]
        self.assertEqual(f["Approach"], "the full first approach")
        self.assertEqual(f["Hypothesis"], "the first thing holds. Wrong if it does not.")
        self.assertEqual(f["Determination"], "It held, on every case.")
        self.assertEqual(n["W5.1"]["evidence"], ["for: it ran on the bench", "against: it was slow at the start"])
        self.assertNotIn("archived.", r["history"])

    def test_a_node_keeps_its_own_evidence_and_gains_the_archived_without_a_copy(self):
        held, arc = TASK, ARCHIVE + "\n**W5.2 Evidence**:\n- for: it ran\n- for: an old line\n"
        n = self.nodes(self.migrated(held, arc))
        self.assertEqual(sorted(n["W5.2"]["evidence"]), ["against: it jammed once", "for: an old line", "for: it ran"])

    def test_a_started_node_is_written_open_and_a_wrapped_field_stays_whole(self):
        n = self.nodes(self.migrated())
        self.assertEqual(n["W5.3"]["status"], "open")
        self.assertEqual(n["W5.3"]["fields"]["Approach"], "do the third thing on two lines")
        self.assertEqual(n["W5.3"]["parent"], "W5.2")

    def test_the_three_logs_are_merged_by_time_each_entry_once(self):
        r = self.migrated(log=LOGFILE + "\n- 2026-01-02T10:00:00Z · session · work · run-2\n")
        self.assertEqual([e["ts"] for e in r["log"]], ["2026-01-01T09:00:00Z", "2026-01-01T09:30:00Z",
                                                       "2026-01-02T10:00:00Z", "2026-01-03T08:00:00Z"])
        self.assertIn("  What I need from you: a or b?", r["log_text"])
        self.assertEqual(len(dfs_tree.parse_log(r["log_text"])), 4)

    def test_other_sections_and_archive_text_without_a_node_go_before_the_first_section(self):
        arc = ARCHIVE + "\n**Background** (moved):\n\nA note.\n- a point\n\n**W5.9 Approach**: a deleted node's\n"
        r = self.migrated(archive=arc)
        pre = th.parse(r["history"], "W5")["pre"]
        for want in ("## Background", "Why it is so.", "A note.", "- a point", "**W5.9 Approach**: a deleted node's"):
            self.assertIn(want, pre)
        self.assertEqual(r["missing"], [])

    def test_nothing_is_missing_from_a_whole_migration(self):
        self.assertEqual(self.migrated()["missing"], [])

    def test_a_line_deleted_from_the_history_or_the_log_is_found_missing(self):
        r = self.migrated()
        for drop, source in (("Approach: do the second thing", "task file"),
                             ("- against: it was slow", "archive"),
                             ("Determination: Confirmed: done.", "task file")):
            history = r["history"].replace(drop + "\n", "")
            self.assertNotEqual(history, r["history"], drop)
            got = th.lost("W5", TASK, ARCHIVE, LOGFILE, history, r["log_text"])
            self.assertEqual([(s, l) for s, l in got][0][0], source, drop)
        log = r["log_text"].replace("  a.\n", "")
        self.assertEqual(th.lost("W5", TASK, ARCHIVE, LOGFILE, r["history"], log), [("log file", "a.")])
        self.assertEqual(th.lost("W5", TASK, ARCHIVE, LOGFILE, r["history"], r["log_text"]), [])

    def test_a_task_file_line_that_belongs_to_no_section_is_not_dropped_silently(self):
        r = self.migrated(TASK.replace("# W5 · sample\n", "# W5 · sample\n\nstray words\n"))
        self.assertEqual(r["missing"], [("task file", "stray words")])


class OnDisk(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.root = Path(tempfile.mkdtemp())
        (self.root / ".dfs" / "items").mkdir(parents=True)
        (self.root / ".dfs" / "archive" / "items").mkdir(parents=True)
        os.chdir(self.root)
        self.addCleanup(os.chdir, self.cwd)
        (self.root / ".dfs/items/W5.md").write_text(TASK)
        (self.root / ".dfs/archive/items/W5.md").write_text(ARCHIVE)
        (self.root / ".dfs/items/W5.log.md").write_text(LOGFILE)

    def test_migrate_writes_the_history_and_the_merged_log_and_leaves_the_old_files(self):
        before = {p: (self.root / p).read_text() for p in (".dfs/items/W5.md", ".dfs/archive/items/W5.md")}
        th.migrate_task("W5", write=True)
        hist = (self.root / ".dfs/items/W5.history.md").read_text()
        self.assertTrue(hist.startswith("# W5 · sample · history\n"))
        self.assertEqual(len(dfs_tree.parse_log((self.root / ".dfs/items/W5.log.md").read_text())), 4)
        self.assertEqual(before, {p: (self.root / p).read_text() for p in before})
        with self.assertRaises(ValueError):
            th.migrate_task("W5", write=True)        # a history already there is not overwritten

    def test_a_migration_that_would_lose_a_line_writes_nothing(self):
        (self.root / ".dfs/items/W5.md").write_text(TASK.replace("# W5 · sample\n", "# W5 · sample\n\nstray\n"))
        with self.assertRaises(ValueError):
            th.migrate_task("W5", write=True)
        self.assertFalse((self.root / ".dfs/items/W5.history.md").exists())
        self.assertEqual((self.root / ".dfs/items/W5.log.md").read_text(), LOGFILE)

    def test_the_command_migrates_and_says_so(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(dfs_tree.main(["tree.py", "migrate", "W5"]), 0)
        self.assertIn("W5", out.getvalue())
        self.assertTrue((self.root / ".dfs/items/W5.history.md").exists())
        self.assertEqual(dfs_tree.main(["tree.py", "migrate", "W5"]), 2)

    def test_the_command_leaves_the_checker_content(self):
        th.migrate_task("W5", write=True)
        self.assertEqual(dfs_tree.part_tags("W5"), [""])


REPO = HERE.parent
REAL = REPO / ".dfs" / "items"


@unittest.skipUnless(REAL.is_dir(), "no roadmap beside the scripts")
class EveryTaskInTheRepo(unittest.TestCase):
    """Every task this repo has moves with nothing lost, and reads the same afterwards."""

    def tasks(self):
        for p in sorted(REAL.glob("W*.md")):
            if dfs_tree.part_of(p.name) == (p.stem, ""):
                yield p.stem

    def read(self, p):
        return p.read_text() if p.exists() else ""

    def test_no_task_loses_a_line_and_every_node_reads_as_before(self):
        n = 0
        for task in self.tasks():
            text = self.read(REAL / ("%s.md" % task))
            arc = self.read(REPO / ".dfs/archive/items" / ("%s.md" % task))
            log = self.read(REAL / ("%s.log.md" % task))
            r = th.migrate(task, text, arc, log)
            self.assertEqual(r["missing"][:2], [], task)
            old = dfs_tree.with_log_file(dfs_tree.with_archive(dfs_tree.parse(text, task), arc), log)
            new = th.parse(r["history"], task)["nodes"]
            self.assertEqual([x["id"] for x in new], [x["id"] for x in old["nodes"]], task)
            held = evidence_blocks(arc)
            for a, b in zip(old["nodes"], new):
                self.assertEqual(b["parent"], a["parent"], a["id"])
                self.assertEqual(b["status"], "open" if a["status"] == "started" else a["status"], a["id"])
                for k in ("Approach", "Hypothesis", "Determination"):
                    x, y = " ".join(a["fields"].get(k, "").split()), " ".join(b["fields"].get(k, "").split())
                    self.assertTrue(y.startswith(x), "%s %s: %r vs %r" % (a["id"], k, x[:60], y[:60]))
                have = {" ".join(e.split()) for e in b["evidence"]}
                want = {" ".join(e.split()) for e in a["evidence"]} | held.get(a["id"], set())
                self.assertLessEqual(want, have, a["id"])
            self.assertEqual([e["text"].rstrip() for e in dfs_tree.parse_log(r["log_text"])],
                             [e["text"].rstrip() for e in old["log"]], task)
            n += 1
        self.assertGreater(n, 100)


class LoadsTheNewFormat(unittest.TestCase):
    """`dfs_tree.load` reads a task that has a history file, and still reads one that has not (W203.4)."""

    def setUp(self):
        self.cwd = os.getcwd()
        self.root = Path(tempfile.mkdtemp())
        (self.root / ".dfs" / "items").mkdir(parents=True)
        os.chdir(self.root)
        self.addCleanup(os.chdir, self.cwd)

    def put(self, name, text):
        (self.root / ".dfs/items" / name).write_text(text)

    def test_the_fixture_task_loads_from_its_history_and_marks_started_from_the_status_file(self):
        fx = HERE / "fixtures" / "history"
        for n in ("W9.md", "W9.history.md", "W9.log.md"):
            self.put(n, (fx / n).read_text())
        t = dfs_tree.load("W9")
        self.assertEqual([x["id"] for x in t["nodes"]], ["W9.%d" % i for i in range(1, 7)])
        by = {x["id"]: x for x in t["nodes"]}
        self.assertEqual(by["W9.4"]["status"], "started")          # the mark lives in the status file
        self.assertEqual(by["W9.5"]["status"], "parked")
        self.assertTrue(by["W9.1"]["evidence"])                    # a node outside the live tree has its evidence
        self.assertEqual(by["W9.2"]["supersedes"], ["W9.1"])
        self.assertEqual(t["goal"], "Make a small battery board that charges and boots.")
        self.assertEqual([e["kind"] for e in t["log"]], ["session", "end", "raise", "answer", "raise"])

    def test_a_task_with_no_history_file_still_reads_the_old_way(self):
        self.put("W5.md", TASK)
        self.put("W5.log.md", LOGFILE)
        t = dfs_tree.load("W5")
        self.assertNotIn("new_format", t)
        self.assertEqual(t["nodes"][0]["fields"]["Approach"], "archived.")     # no archive: the stub stays

    def test_a_status_file_node_is_not_read_once_a_history_exists(self):
        self.put("W5.md", TASK)
        th.migrate_task("W5", write=True)
        self.put("W5.md", TASK.replace("do the second thing", "a line changed in the status file"))
        self.assertEqual(dfs_tree.load("W5")["nodes"][1]["fields"]["Approach"], "do the second thing")

    def fixture(self):
        fx = HERE / "fixtures" / "history"
        for n in ("W9.md", "W9.history.md", "W9.log.md"):
            self.put(n, (fx / n).read_text())
        return dfs_tree.load("W9")

    def test_a_superseded_node_is_derived_from_a_confirmed_one_and_reads_in_order_from_any_end(self):
        t = self.fixture()
        status, pruned = dfs_tree.effective(t)
        self.assertEqual(dfs_tree.superseded_by(t, status), {"W9.1": ["W9.2"], "W9.2": ["W9.3"]})
        self.assertEqual(status["W9.1"], "confirmed")          # derived, never stored: effective keeps the stored one
        for end in ("W9.1", "W9.2", "W9.3"):
            self.assertEqual(dfs_tree.supersession_chain(t, end, status), ["W9.1", "W9.2", "W9.3"], end)
        out = "\n".join(dfs_tree.outline(t))
        self.assertIn("W9.1 [superseded]", out)
        self.assertIn("W9.3 [confirmed]", out)

    def test_a_refuted_node_stays_refuted_and_a_replaced_open_or_parked_one_leaves_the_live_tree(self):
        t = self.fixture()
        for nd in t["nodes"]:
            if nd["id"] == "W9.3":
                nd["supersedes"] = ["W9.2", "W9.6", "W9.5"]
        status, pruned = dfs_tree.effective(t)
        sup = dfs_tree.superseded_by(t, status)
        self.assertNotIn("W9.6", sup)                          # refuted stays refuted
        self.assertIn("W9.5", sup)
        self.assertIn("W9.5", pruned)                          # a parked node replaced is no longer live
        self.assertNotIn("W9.2", pruned)                       # a confirmed one is history, not cut

    def test_a_node_that_supersedes_is_refuted_and_the_older_one_reads_as_before(self):
        t = self.fixture()
        for nd in t["nodes"]:
            if nd["id"] == "W9.3":
                nd["status"] = "refuted"
        self.assertEqual(dfs_tree.superseded_by(t, dfs_tree.effective(t)[0]), {"W9.1": ["W9.2"]})

    def test_show_prints_the_node_its_children_the_chain_and_the_log_that_names_it(self):
        self.fixture()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(dfs_tree.main(["tree.py", "show", "W9", "W9.3"]), 0)
        text = out.getvalue()
        self.assertIn("### W9.3 · charger, third try", text)
        self.assertEqual(text.count("Status:"), 1)             # folded: one Status line, not the two written
        self.assertIn("  W9.4 [open] pick the boost part", text)
        self.assertLess(text.index("W9.1 [superseded]"), text.index("W9.2 [superseded]"))
        self.assertLess(text.index("W9.2 [superseded]"), text.index("W9.3 [confirmed]"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            dfs_tree.main(["tree.py", "show", "W9", "W9.4"])
        self.assertIn("raise · W9.4", out.getvalue())
        self.assertIn("answer · 2026-11-21T08:50:00Z", out.getvalue())     # an answer names its raise's node
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            dfs_tree.main(["tree.py", "show", "W9", "W9.3", "--raw"])
        self.assertEqual(out.getvalue().count("Status:"), 2)   # as written

    def test_a_log_entry_for_w9_4_is_not_found_for_w9_41(self):
        t = self.fixture()
        e = next(e for e in t["log"] if e["kind"] == "raise" and "W9.4" in e["head"])
        self.assertTrue(dfs_tree.names_node(e, "W9.4", t))
        self.assertFalse(dfs_tree.names_node(e, "W9.41", t))
        self.assertFalse(dfs_tree.names_node(dict(e, text="raise · W9.45"), "W9.4", t))

    def test_tag_lists_the_nodes_carrying_it_oldest_first_with_their_status_now(self):
        self.fixture()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(dfs_tree.main(["tree.py", "tag", "W9", "power"]), 0)
        text = out.getvalue()
        self.assertEqual([text.index("### W9.%d" % i) for i in (1, 2, 3, 4)],
                         sorted(text.index("### W9.%d" % i) for i in (1, 2, 3, 4)))
        self.assertNotIn("W9.5", text)
        self.assertIn("Status now: superseded", text)


@unittest.skipUnless(REAL.is_dir(), "no roadmap beside the scripts")
class EveryTaskLoadsTheSameWhenMigrated(unittest.TestCase):
    def test_status_frontier_sessions_and_raises_do_not_change(self):
        cwd, root = os.getcwd(), Path(tempfile.mkdtemp())
        (root / ".dfs/items").mkdir(parents=True)
        (root / ".dfs/archive/items").mkdir(parents=True)
        os.chdir(root)
        self.addCleanup(os.chdir, cwd)
        n = 0
        for p in sorted(REAL.glob("W*.md")):
            task = p.stem
            if dfs_tree.part_of(p.name) != (task, ""):
                continue
            for src, dst in ((p, root / ".dfs/items" / p.name),
                             (REAL / ("%s.log.md" % task), root / ".dfs/items" / ("%s.log.md" % task)),
                             (REPO / ".dfs/archive/items" / p.name, root / ".dfs/archive/items" / p.name)):
                if src.exists():
                    dst.write_text(src.read_text())
            old = dfs_tree.load(task)
            th.migrate_task(task, write=True)
            new = dfs_tree.load(task)
            self.assertTrue(new.get("new_format"), task)
            self.assertEqual(new["goal"], old["goal"], task)
            self.assertEqual([x["id"] for x in new["nodes"]], [x["id"] for x in old["nodes"]], task)
            self.assertEqual(dfs_tree.effective(new)[0], dfs_tree.effective(old)[0], task)
            self.assertEqual([x["id"] for x in dfs_tree.frontier(new)], [x["id"] for x in dfs_tree.frontier(old)], task)
            self.assertEqual(dfs_tree.status_of(new), dfs_tree.status_of(old), task)
            self.assertEqual(dfs_tree.sessions_since_author(new), dfs_tree.sessions_since_author(old), task)
            self.assertEqual([e["ts"] for e in dfs_tree.open_raises(new)],
                             [e["ts"] for e in dfs_tree.open_raises(old)], task)
            self.assertEqual([e["text"].rstrip() for e in new["log"]], [e["text"].rstrip() for e in old["log"]], task)
            self.assertTrue(all(x["evidence"] or not old_x["evidence"]
                                for x, old_x in zip(new["nodes"], old["nodes"])), task)
            n += 1
        self.assertGreater(n, 100)


if __name__ == "__main__":
    unittest.main()
