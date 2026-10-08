#!/usr/bin/env python3
"""The decision tree's derivations, the checker over it, and its branch parts.

The derivations are where a wrong answer is silent: a node pruned that should be
workable, a correction that cuts the node carrying it out, a raise an answer does
not close. So each is pinned on the smallest tree that shows it.

Run:  python3 roadmap-dfs/scripts/test_tree.py
"""
import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check as dfs_check   # noqa: E402
import paths as dfs_paths   # noqa: E402
import state as dfs_state   # noqa: E402
import tree as dfs_tree    # noqa: E402


def task(nodes="", log=""):
    return ("# W1 · sample\n\n## Goal\n\nMake it work.\n\n## Tree\n\n%s\n## Log\n\n%s"
            % (nodes, log))


def node(n, status="open", parent=None, extra=""):
    out = "### W1.%d · step %d\n" % (n, n)
    if parent:
        out += "Parent: W1.%d\n" % parent
    out += "Status: %s\n%s\n" % (status, extra)
    return out


class Derivation(unittest.TestCase):
    def t(self, *nodes, log=""):
        return dfs_tree.parse(task("".join(nodes), log), "W1")

    def test_a_chain_works_its_first_open_node(self):
        t = self.t(node(1, "confirmed"), node(2, parent=1), node(3, parent=2))
        self.assertEqual([n["id"] for n in dfs_tree.frontier(t)], ["W1.2"])
        self.assertEqual(dfs_tree.status_of(t)[0], "open")

    def test_backing_up_prunes_what_was_planned_under_the_refuted_node(self):
        t = self.t(node(1, "confirmed"), node(2, "refuted", 1), node(3, parent=2),
                   node(4, parent=1))
        status, pruned = dfs_tree.effective(t)
        self.assertEqual(pruned, {"W1.3"})
        self.assertEqual([n["id"] for n in dfs_tree.frontier(t)], ["W1.4"])

    def test_every_live_node_confirmed_is_done(self):
        t = self.t(node(1, "confirmed"), node(2, "refuted", 1), node(3, parent=2),
                   node(4, "confirmed", 1))
        self.assertEqual(dfs_tree.status_of(t)[0], "done")

    def test_no_nodes_is_open_and_says_plan_it(self):
        self.assertIn("plan", dfs_tree.status_of(self.t())[1])

    def test_every_path_refuted_is_open_not_done(self):
        t = self.t(node(1, "refuted"))
        self.assertEqual(dfs_tree.status_of(t)[0], "open")

    def test_a_raise_blocks_until_an_answer_names_it(self):
        log = "- 2026-09-25T09:00:00Z · raise · W1.1\n  which?\n"
        t = self.t(node(1), log=log)
        self.assertEqual(dfs_tree.status_of(t)[0], "blocked")
        t = self.t(node(1), log=log + "- 2026-09-25T09:10:00Z · answer · 2026-09-25T09:00:00Z\n  this\n")
        self.assertEqual(dfs_tree.status_of(t)[0], "open")

    def test_a_correction_to_refuted_prunes_below_and_keeps_the_alternatives(self):
        # Siblings are the alternatives: refuting W1.1 is what makes W1.4 the way on.
        t = self.t(node(1, "confirmed"), node(2, "confirmed", 1), node(3, parent=2),
                   node(4, "parked"),
                   log="- 2026-09-25T09:00:00Z · correct · W1.1 · refuted\n  wrong\n")
        status, pruned = dfs_tree.effective(t)
        self.assertEqual(status["W1.1"], "refuted")
        self.assertEqual(pruned, {"W1.2", "W1.3"})
        self.assertIn("corrected W1.1", dfs_tree.status_of(t)[1])

    def test_a_correction_to_confirmed_prunes_the_alternatives_taken_instead(self):
        t = self.t(node(1, "refuted"), node(2, "confirmed"), node(3, parent=2),
                   log="- 2026-09-25T09:00:00Z · correct · W1.1 · confirmed\n  it was right\n")
        status, pruned = dfs_tree.effective(t)
        self.assertEqual(status["W1.1"], "confirmed")
        self.assertEqual(pruned, {"W1.2", "W1.3"})

    def test_confirming_what_already_stands_confirmed_prunes_nothing(self):
        t = self.t(node(1, "confirmed"), node(2, "confirmed", 1), node(3, "parked"),
                   log="- 2026-09-25T09:00:00Z · correct · W1.1 · confirmed\n  yes\n")
        status, pruned = dfs_tree.effective(t)
        self.assertEqual((status["W1.1"], pruned), ("confirmed", set()))
        self.assertTrue(dfs_tree.corrections(t)[0]["keeps"])

    def test_confirming_after_an_earlier_refute_correction_still_prunes(self):
        t = self.t(node(1, "confirmed"), node(2, "confirmed"),
                   log="- 2026-09-25T09:00:00Z · correct · W1.1 · refuted\n  no\n"
                       "- 2026-09-25T10:00:00Z · correct · W1.1 · confirmed\n  yes after all\n")
        self.assertEqual(dfs_tree.effective(t)[1], {"W1.2"})

    def test_keeps_is_the_one_rule_and_judges_by_what_the_node_stands_as_now(self):
        self.assertTrue(dfs_tree.keeps("confirmed", "confirmed"))
        for verdict, was in (("confirmed", "refuted"), ("confirmed", "parked"),
                             ("confirmed", "open"), ("refuted", "confirmed"), ("refuted", "refuted")):
            self.assertFalse(dfs_tree.keeps(verdict, was), (verdict, was))
        # The node says confirmed in the file; the author refuted it, so what it stands as
        # NOW is refuted, and confirming it again is an overrule that prunes, not a note.
        t = self.t(node(1, "confirmed"), node(2, "confirmed", 1),
                   log="- 2026-09-25T09:00:00Z · correct · W1.1 · refuted\n  no\n")
        self.assertEqual(dfs_tree.effective(t)[0]["W1.1"], "refuted")
        self.assertFalse(dfs_tree.keeps("confirmed", dfs_tree.effective(t)[0]["W1.1"]))

    def test_parked_alternatives_and_their_plans_do_not_hold_the_task_open(self):
        t = self.t(node(1, "confirmed"), node(2, "parked"), node(3, parent=2))
        self.assertEqual(dfs_tree.dormant(t), {"W1.2", "W1.3"})
        self.assertEqual(dfs_tree.frontier(t), [])
        self.assertEqual(dfs_tree.status_of(t)[0], "done")
        self.assertIn("W1.3 [dormant]", " ".join(dfs_tree.outline(t)))

    def test_only_parked_is_not_done(self):
        t = self.t(node(1, "refuted"), node(2, "parked"))
        self.assertIn("unpark", dfs_tree.status_of(t)[1])

    def test_a_tree_a_session_completed_waits_for_its_review(self):
        log = ("- 2026-09-25T09:00:00Z · session · work\n"
               "- 2026-09-25T09:10:00Z · end · work · ok\n")
        t = self.t(node(1, "confirmed"), log=log)
        self.assertEqual(dfs_tree.status_of(t), ("open",
                         "the tree is complete; the implementation review is due"))
        t = self.t(node(1, "confirmed"), log=log + "- 2026-09-25T09:20:00Z · review · ok\n")
        self.assertEqual(dfs_tree.status_of(t)[0], "done")
        # A review that said `issues` and added no node still leaves the task open.
        issues = log + "- 2026-09-25T09:20:01Z · review · issues\n  bug\n"
        self.assertIn("review found issues", dfs_tree.status_of(self.t(node(1, "confirmed"), log=issues))[1])
        accepted = issues + "- 2026-09-25T09:40:00Z · accept\n"
        self.assertEqual(dfs_tree.status_of(self.t(node(1, "confirmed"), log=accepted))[0], "done")

    def test_an_accept_takes_a_complete_tree_without_its_review(self):
        # W13, 2026-09-28: the author accepted a complete tree whose review was due,
        # and the task still read review-due, so the runner would have reviewed the
        # code the accept had just taken as it is.
        log = ("- 2026-09-25T09:00:00Z · session · work\n"
               "- 2026-09-25T09:10:00Z · end · work · ok\n"
               "- 2026-09-25T09:20:00Z · accept\n")
        t = self.t(node(1, "confirmed"), log=log)
        self.assertFalse(dfs_tree.review_due(t))
        self.assertEqual(dfs_tree.status_of(t),
                         ("done", "every live node confirmed, and accepted by the author"))
        # Work after the accept is code nobody has seen: the review is due again.
        later = log + ("- 2026-09-25T09:30:00Z · session · work\n"
                       "- 2026-09-25T09:40:00Z · end · work · ok\n")
        t = self.t(node(1, "confirmed"), log=later)
        self.assertTrue(dfs_tree.review_due(t))

    def test_a_review_reopens_the_task_by_adding_an_open_node(self):
        log = ("- 2026-09-25T09:00:00Z · session · work\n"
               "- 2026-09-25T09:10:00Z · end · work · ok\n"
               "- 2026-09-25T09:20:00Z · review · issues\n  W1.2: a bug\n")
        t = self.t(node(1, "confirmed"), node(2, parent=1, extra="Approach: fix the bug\n"), log=log)
        self.assertEqual(dfs_tree.status_of(t), ("open", "current node W1.2 · step 2"))
        self.assertFalse(dfs_tree.open_raises(t))

    def test_an_answer_reopens_the_task_until_a_work_session_acts_on_it(self):
        log = ("- 2026-09-25T09:00:00Z · session · work\n"
               "- 2026-09-25T09:10:00Z · end · work · ok\n"
               "- 2026-09-25T09:11:00Z · raise · W1.1\n  which way?\n"
               "- 2026-09-25T09:12:00Z · answer · 2026-09-25T09:11:00Z\n  a\n")
        t = self.t(node(1, "confirmed"), log=log)
        self.assertEqual(dfs_tree.status_of(t)[1],
                         "the author answered the raise of 2026-09-25T09:11:00Z — act on it first")
        self.assertFalse(dfs_tree.review_due(t))
        # A session that hit the limit has not acted on it: still work, still no review.
        limited = log + ("- 2026-09-25T09:20:00Z · session · work\n"
                         "- 2026-09-25T09:20:05Z · end · work · limit\n")
        t = self.t(node(1, "confirmed"), log=limited)
        self.assertIn("act on it first", dfs_tree.status_of(t)[1])
        self.assertFalse(dfs_tree.review_due(t))
        acted = limited + ("- 2026-09-25T09:30:00Z · session · work\n"
                           "- 2026-09-25T09:40:00Z · end · work · ok\n")
        t = self.t(node(1, "confirmed"), node(2, "confirmed", 1), log=acted)
        self.assertEqual(dfs_tree.status_of(t)[1],
                         "the tree is complete; the implementation review is due")
        accepted = log + "- 2026-09-25T09:13:00Z · accept\n"
        self.assertFalse(dfs_tree.unacted_answers(self.t(node(1, "confirmed"), log=accepted)))

    def test_a_backtrack_withdraws_the_node_and_below_and_is_redone_beside_it(self):
        log = ("- 2026-09-25T09:00:00Z · session · work\n"
               "- 2026-09-25T09:10:00Z · end · work · ok\n"
               "- 2026-09-25T09:20:00Z · backtrack · W1.2\n  evidence is one run\n")
        chain = (node(1, "confirmed"), node(2, "confirmed", 1), node(3, "confirmed", 2))
        t = self.t(*chain, log=log)
        status, pruned = dfs_tree.effective(t)
        self.assertEqual(pruned, {"W1.2", "W1.3"})
        self.assertEqual(status["W1.1"], "confirmed")
        self.assertEqual(dfs_tree.status_of(t),
                         ("open", "the critic backtracked to W1.2 — redo it first"))
        self.assertFalse(dfs_tree.review_due(t))
        self.assertEqual(dfs_tree.sessions_since_author(t), 1)  # a critic is not the author
        redo = node(4, parent=1, extra="Corrects: 2026-09-25T09:20:00Z\n")
        t = self.t(*chain, redo, log=log)
        self.assertEqual([n["id"] for n in dfs_tree.frontier(t)], ["W1.4"])
        self.assertTrue(dfs_tree.corrections(t)[0]["done"])

    def test_the_node_carrying_a_correction_out_is_not_cut_by_it(self):
        log = "- 2026-09-25T09:00:00Z · correct · W1.1 · confirmed\n  go deeper\n"
        t = self.t(node(1, "refuted"), node(2, "confirmed", 1),
                   node(3, parent=1, extra="Corrects: 2026-09-25T09:00:00Z\n"), log=log)
        status, pruned = dfs_tree.effective(t)
        self.assertEqual(pruned, {"W1.2"})
        self.assertEqual([n["id"] for n in dfs_tree.frontier(t)], ["W1.3"])
        self.assertTrue(dfs_tree.corrections(t)[0]["done"])

    def test_sessions_count_from_the_author_and_the_critic(self):
        log = "".join("- 2026-09-25T09:%02d:00Z · session · work\n" % i for i in range(3))
        log += "- 2026-09-25T09:10:00Z · session · critic\n"
        log += "- 2026-09-25T09:11:00Z · session · work\n"
        t = self.t(node(1), log=log)
        self.assertEqual(dfs_tree.sessions_since_author(t), 4)
        self.assertEqual(dfs_tree.sessions_since_critic(t), 1)

    def test_render_round_trips_and_keeps_what_it_does_not_understand(self):
        text = task(node(1, extra="Evidence:\n- for: x\nsomething freeform\n"),
                    "- 2026-09-25T09:00:00Z · raise · W1.1\n  first\nunindented\n")
        t = dfs_tree.parse(text, "W1")
        again = dfs_tree.render(t)
        self.assertIn("something freeform", again)
        self.assertIn("unindented", again)
        self.assertEqual(dfs_tree.render(dfs_tree.parse(again, "W1")), again)


class InARepo(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / ".dfs" / "items").mkdir(parents=True)
        (self.root / ".dfs" / "ROADMAP.md").write_text("# Roadmap\n")
        self._wr = dfs_paths.work_root
        dfs_paths.work_root = lambda: self.root
        subprocess.run("git init -q -b master", shell=True, cwd=self.root, check=True)

    def tearDown(self):
        dfs_paths.work_root = self._wr
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, text):
        (self.root / ".dfs" / "items" / "W1.md").write_text(text)

    def commit(self):
        subprocess.run("git add -A && git -c user.name=t -c user.email=t@t commit -qm x",
                       shell=True, cwd=self.root, check=True)


class NewTask(InARepo):
    def test_the_next_id_is_one_past_the_highest_and_the_words_go_in_unchanged(self):
        self.write(task())
        self.assertEqual(dfs_tree.next_task_id(), "W2")
        got = dfs_tree.create_task("  Cache   the lookups ", "Done is: hits above 90%.\nBecause the p99 hurts.", ["- [ ] profile it", "", "add the cache"])
        self.assertEqual(got, "W2")
        t = dfs_tree.load("W2")
        self.assertIn("Done is: hits above 90%.\nBecause the p99 hurts.", (self.root / ".dfs" / "items" / "W2.md").read_text())
        nodes = dfs_tree.by_id(t)
        self.assertEqual([(n["id"], n["parent"], n["status"]) for n in nodes.values()],
                         [("W2.1", None, "open"), ("W2.2", "W2.1", "open")])
        self.assertEqual(dfs_tree.next_task_id(), "W3")

    def test_a_task_with_no_todos_and_no_goal_is_still_a_task(self):
        got = dfs_tree.create_task("Just a name")
        self.assertIn("Just a name", (self.root / ".dfs" / "items" / (got + ".md")).read_text())
        self.assertEqual(dfs_tree.by_id(dfs_tree.load(got)), {})

    def test_what_is_not_a_task_is_refused(self):
        self.write(task())
        for bad in (dict(name=""), dict(name="x", task="W1"), dict(name="x", task="nope")):
            with self.assertRaises(ValueError):
                dfs_tree.create_task(bad["name"], task=bad.get("task"))


class AppendLog(InARepo):
    def test_timestamps_are_unique_and_later_than_everything(self):
        self.write(task(node(1), "- 2099-01-01T00:00:00Z · session · work\n"))
        a = dfs_tree.append_log("W1", "raise", ["W1.1"], "q")
        b = dfs_tree.append_log("W1", "critic", ["ok"])
        self.assertEqual(a, "2099-01-01T00:00:01Z")
        self.assertEqual(b, "2099-01-01T00:00:02Z")

    def test_an_unknown_kind_is_refused(self):
        self.write(task())
        with self.assertRaises(ValueError):
            dfs_tree.append_log("W1", "assume")


class Check(InARepo):
    def problems(self, text):
        return dfs_check.check_shape("W1", text)

    def test_a_backtrack_names_a_node_and_a_node_may_carry_it_out(self):
        good = task(node(1, "confirmed") + node(2, parent=None, extra="Corrects: 2026-09-25T09:00:00Z\n"),
                    "- 2026-09-25T09:00:00Z · backtrack · W1.1\n  thin\n")
        self.assertEqual(self.problems(good), [])
        bad = task(node(1, "confirmed"), "- 2026-09-25T09:00:00Z · backtrack · W1.9\n  x\n")
        self.assertIn("the backtrack at 2026-09-25T09:00:00Z names no node",
                      " | ".join(self.problems(bad)))

    def test_a_clean_tree_has_no_problems(self):
        self.assertEqual(self.problems(task(node(1, "confirmed"), )), [])

    def test_shape_problems_are_named(self):
        bad = task(node(1) + node(1) + node(3, "maybe", parent=9),
                   "- 2026-09-25T09:00:00Z · answer · 2026-01-01T00:00:00Z\n  x\n"
                   "- nonsense line\n")
        found = " | ".join(self.problems(bad))
        for want in ("defined twice", "Status 'maybe'", "Parent W1.9", "names no raise",
                     "not `- <ts>"):
            self.assertIn(want, found)

    def assumed(self, text, head=None, pages=None):
        pages = pages or {}
        read = lambda path: pages.get(path.name)   # noqa: E731
        return " | ".join(dfs_check.check_assumptions("W1", text, head, read))

    PAGE = "<button>decide</button><script>b.addEventListener('click', f)</script>"

    def test_a_hypothesis_must_name_an_interactive_artefact(self):
        def hyp(ref):
            return task(node(1, extra="Hypothesis: It holds. Wrong if it breaks. %s\n" % ref))
        art = ".dfs/artefacts/a1.html"
        self.assertIn("names no artefact", self.assumed(hyp("")))
        self.assertIn("a1.html does not exist", self.assumed(hyp(art)))
        self.assertIn("not interactive", self.assumed(hyp(art), pages={"a1.html": "<p>hi</p>"}))
        for page in ("<script>x()</script><p>no control</p>",
                     "<button>a button nothing listens to</button><script>x()</script>",
                     "<script>b.addEventListener('click', f)</script><p>no control</p>"):
            self.assertIn("not interactive", self.assumed(hyp(art), pages={"a1.html": page}),
                          page)
        self.assertEqual(self.assumed(hyp(art), pages={"a1.html": self.PAGE}), "")
        for page in ("<details>x</details><script>d.addEventListener('toggle', f)</script>",
                     "<button onclick=\"go()\">x</button>",
                     "<script>var b = document.createElement('button');"
                     "b.addEventListener('click', f)</script>"):
            self.assertEqual(self.assumed(hyp(art), pages={"a1.html": page}), "", page)
        self.assertIn("names no artefact", self.assumed(hyp(".dfs/artefacts/../x.html")))

    def test_a_node_with_no_hypothesis_needs_no_artefact(self):
        self.assertEqual(self.assumed(task(node(1, extra="Ask: which one?\n"))), "")

    def test_only_a_new_or_changed_hypothesis_is_judged(self):
        old = task(node(1, "confirmed", extra="Hypothesis: It holds. Wrong if not.\n"))
        self.assertEqual(self.assumed(old, head=old), "")
        again = old.replace("Wrong if not.", "Wrong if it breaks.")
        self.assertIn("W1.1 states a Hypothesis", self.assumed(again, head=old))
        self.assertIn("W1.1 states a Hypothesis", self.assumed(old))   # new in this commit

    def test_a_parked_or_refuted_hypothesis_is_moot_until_it_is_open_again(self):
        parked = task(node(1, "parked", extra="Hypothesis: It holds. Wrong if not.\n"))
        self.assertEqual(self.assumed(parked), "")
        self.assertEqual(self.assumed(task(node(1, "refuted", extra=(
            "Hypothesis: It holds. Wrong if not.\n")))), "")
        reopened = parked.replace("Status: parked", "Status: open")
        self.assertIn("W1.1 states a Hypothesis", self.assumed(reopened, head=parked))

    def test_a_determined_node_is_history(self):
        head = task(node(1, "confirmed", extra="Evidence:\n- for: it ran\nDetermination: yes\n"))
        edited = head.replace("Determination: yes", "Determination: no")
        self.assertIn("changed its Determination",
                      " ".join(dfs_check.check_history("W1", edited, head)))
        dropped = head.replace("- for: it ran\n", "")
        self.assertIn("evidence", " ".join(dfs_check.check_history("W1", dropped, head)))
        grown = head.replace("- for: it ran\n", "- for: it ran\n- for: again\n")
        self.assertEqual(dfs_check.check_history("W1", grown, head), [])

    def test_a_determined_nodes_evidence_may_move_to_the_archive_verbatim(self):
        head = task(node(1, "confirmed", extra="Evidence:\n- for: it ran\n  twice\n"
                                                "Determination: yes\n"))
        moved = head.replace("- for: it ran\n  twice\n", "")
        self.assertEqual(dfs_check.check_history(
            "W1", moved, head, "## W1.1\n\n- for: it ran\ntwice\n"), [])
        for arc in ("", "- for: it ran thrice\n",
                    # a substring of another item, or of prose, is not the line
                    "## W1.1\n\n- for: it ran on nothing, in fact\n",
                    "## W1.1\n\nnobody said for: it ran twice at all\n",
                    # the whole line, but under another node
                    "## W1.2\n\n- for: it ran\ntwice\n"):
            self.assertIn("does not hold it verbatim",
                          " ".join(dfs_check.check_history("W1", moved, head, arc)))
        edited = moved.replace("Determination: yes", "Determination: no")
        self.assertIn("changed its Determination", " ".join(dfs_check.check_history(
            "W1", edited, head, "- for: it ran twice\n")))

    def test_a_determined_nodes_plan_may_move_to_the_archive_verbatim(self):
        head = task(node(1, "confirmed", extra="Approach: cut it\n  thin\nHypothesis: it holds\n"
                                                "Determination: yes\n"))
        moved = head.replace("Approach: cut it\n  thin\n", "Approach: archived.\n")
        self.assertEqual(dfs_check.check_history(
            "W1", moved, head, "**W1.1 Approach**: cut it thin\n"), [])
        # Once HEAD holds the stub too, an unchanged file is no edit: the log cutoff's
        # merge of the archive must not write the words back into the file's nodes.
        self.assertEqual(dfs_check.check_history(
            "W1", moved, moved, "**W1.1 Approach**: cut it thin\n"), [])
        for arc in ("", "**W1.2 Approach**: cut it thin\n", "**W1.1 Hypothesis**: cut it thin\n",
                    # the latest line is the one `load` puts back
                    "**W1.1 Approach**: cut it thin\n\n**W1.1 Approach**: cut it thick\n"):
            self.assertIn("changed its Approach",
                          " ".join(dfs_check.check_history("W1", moved, head, arc)))
        # Any wording other than the stub is an edit, archived or not.
        summed = head.replace("Approach: cut it\n  thin\n", "Approach: cut.\n")
        self.assertIn("changed its Approach", " ".join(dfs_check.check_history(
            "W1", summed, head, "**W1.1 Approach**: cut it thin\n")))
        rewritten = head.replace("Hypothesis: it holds", "Hypothesis: it held")
        self.assertIn("changed its Hypothesis",
                      " ".join(dfs_check.check_history("W1", rewritten, head)))
        # An open node's plan is still the session's to change.
        live = task(node(1, extra="Approach: a\n"))
        self.assertEqual(dfs_check.check_history("W1", live.replace("Approach: a", "Approach: b"),
                                                 live), [])

    def test_plan_is_read_as_the_approach_it_was_renamed_to(self):
        # Trees written before the rename keep `Plan:` in their determined nodes.
        head = task(node(1, "confirmed", extra="Plan: cut it\n  thin\nDetermination: yes\n"))
        t = dfs_tree.parse(head, "W1")
        self.assertEqual(dfs_tree.by_id(t)["W1.1"]["fields"]["Approach"], "cut it thin")
        self.assertNotIn("Plan", dfs_tree.by_id(t)["W1.1"]["fields"])
        moved = head.replace("Plan: cut it\n  thin\n", "Approach: archived.\n")
        for arc in ("**W1.1 Approach**: cut it thin\n", "**W1.1 Plan**: cut it thin\n"):
            self.assertEqual(dfs_check.check_history("W1", moved, head, arc), [])
            self.assertEqual(dfs_tree.by_id(dfs_tree.with_archive(
                dfs_tree.parse(moved, "W1"), arc))["W1.1"]["fields"]["Approach"],
                "cut it thin")

    def test_the_archive_only_grows(self):
        head = "## W1.1\n\n- for: it ran\n- for: it ran\n"
        self.assertEqual(dfs_check.check_archive("W1", head + "- for: more\n", head), [])
        for now in (head.replace("it ran\n", "it ran twice\n", 1),
                    "## W1.1\n\n- for: it ran\n"):
            self.assertIn("only grows", " ".join(dfs_check.check_archive("W1", now, head)))

    def test_the_archive_keeps_its_order(self):
        head = "**W1.1 Evidence**:\n- for: a\n\n**W1.2 Evidence**:\n- for: b\n"
        swapped = "**W1.2 Evidence**:\n- for: a\n\n**W1.1 Evidence**:\n- for: b\n"
        self.assertIn("only grows", " ".join(dfs_check.check_archive("W1", swapped, head)))
        wedged = head.replace("- for: a\n", "**W1.3 Evidence**:\n- for: a\n")
        self.assertIn("only grows", " ".join(dfs_check.check_archive("W1", wedged, head)))
        self.assertEqual(dfs_check.check_archive("W1", head + "\n**W1.3 Approach**: c\n", head), [])

    def run_check(self, *args):
        argv, out = sys.argv, io.StringIO()
        sys.argv = ["check.py", *args]
        try:
            with contextlib.redirect_stdout(out):
                return dfs_check.main(), out.getvalue()
        finally:
            sys.argv = argv

    def refuted_node_commit(self):
        """(sha) of `W1.1: try`, which refutes W1.1 and changes code.txt a -> b."""
        git = lambda c: subprocess.run("git -c user.name=t -c user.email=t@t " + c,
                                       shell=True, cwd=self.root, check=True,
                                       capture_output=True, text=True).stdout
        (self.root / "code.txt").write_text("a\n")
        self.write(task(node(1)))
        git("add -A && git -c user.name=t -c user.email=t@t commit -qm base")
        (self.root / "code.txt").write_text("b\n")
        self.write(task(node(1, "refuted", extra="Evidence:\n- against: b\n"
                                                   "Determination: no\n")))
        git("add -A && git -c user.name=t -c user.email=t@t commit -qm 'W1.1: try'")
        return git("rev-parse HEAD").strip(), git

    def test_the_printed_revert_takes_the_code_out_and_leaves_the_node(self):
        sha, git = self.refuted_node_commit()
        self.write(task(node(1, "refuted", extra="Evidence:\n- against: b\n"
                                                   "Determination: no\n") + node(2)))
        r = subprocess.run(dfs_tree.revert_recipe(sha), shell=True, cwd=self.root,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.root / "code.txt").read_text(), "a\n")
        self.assertIn("### W1.2", (self.root / ".dfs/items/W1.md").read_text())
        git("add .dfs/items/W1.md")
        code, out = self.run_check("--staged")
        self.assertEqual(code, 0, out)
        t = dfs_tree.parse((self.root / ".dfs/items/W1.md").read_text(), "W1")
        self.assertEqual([s for _, _, s in dfs_tree.unreverted_pruned(t)], ["W1.1: try"])
        git("commit -qm 'W1.2: back' -m 'This reverts commit %s.'" % sha)
        self.assertEqual(dfs_tree.unreverted_pruned(t), [])

    def test_git_revert_of_a_node_commit_is_what_the_guard_refuses(self):
        # the control: the instruction the briefing gave before, same commit
        sha, git = self.refuted_node_commit()
        git("revert --no-commit %s" % sha)
        self.assertEqual((self.root / "code.txt").read_text(), "a\n")
        code, out = self.run_check("--staged")
        self.assertEqual(code, 1, out)
        self.assertIn("W1.1", out)
        git("revert --abort")
        # and with the new node written first, git will not start at all
        self.write(task(node(1, "refuted", extra="Evidence:\n- against: b\n"
                                                   "Determination: no\n") + node(2)))
        r = subprocess.run("git revert --no-commit %s" % sha,
                           shell=True, cwd=self.root, capture_output=True, text=True)
        self.assertIn("would be overwritten", r.stderr)

    def test_a_commit_a_determination_says_stays_is_not_listed(self):
        sha, git = self.refuted_node_commit()
        t = dfs_tree.parse(task(node(1, "refuted", extra="Determination: no\n")
                                + node(2, "confirmed", extra="Determination: %s stays.\n"
                                       % sha[:7])), "W1")
        self.assertEqual(dfs_tree.unreverted_pruned(t), [])
        t["nodes"][1]["fields"]["Determination"] = "yes"
        self.assertEqual(len(dfs_tree.unreverted_pruned(t)), 1)

    def test_a_move_is_judged_on_what_is_staged(self):
        arc = self.root / ".dfs" / "archive" / "items" / "W1.md"
        arc.parent.mkdir(parents=True)
        arc.write_text("# W1\n")
        self.write(task(node(1, "confirmed", extra="Evidence:\n- for: it ran\n"
                                                     "Determination: yes\n")))
        self.commit()
        self.write(task(node(1, "confirmed", extra="Evidence: archived.\n"
                                                     "Determination: yes\n")))
        arc.write_text("# W1\n\n**W1.1 Evidence**:\n- for: it ran\n")
        git = lambda c: subprocess.run(c, shell=True, cwd=self.root, check=True)
        git("git add .dfs/items/W1.md")
        self.assertEqual(self.run_check()[0], 0)
        code, out = self.run_check("--staged")
        self.assertEqual(code, 1, out)
        self.assertIn("does not hold it verbatim", out)
        git("git add .dfs/archive/items/W1.md")
        self.assertEqual(self.run_check("--staged")[0], 0)
        self.commit()
        arc.write_text("# W1\n\n**W1.1 Evidence**:\n- for: it ran twice\n")
        self.write(task(node(1, "confirmed", extra="Evidence: archived.\n"
                                                     "Determination: yes\n")))
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertIn("only grows", out)

    def test_a_task_file_and_its_archive_are_never_deleted(self):
        arc = self.root / ".dfs" / "archive" / "items" / "W1.md"
        arc.parent.mkdir(parents=True)
        arc.write_text("# W1\n\n**W1.1 Evidence**:\n- for: it ran\n")
        self.write(task(node(1, "confirmed", extra="Evidence: archived.\n"
                                                     "Determination: yes\n")))
        self.commit()
        git = lambda c: subprocess.run(c, shell=True, cwd=self.root, check=True,
                                       capture_output=True)
        git("git rm -q .dfs/items/W1.md .dfs/archive/items/W1.md")
        for flag in ((), ("--staged",)):
            code, out = self.run_check(*flag)
            self.assertEqual(code, 1, (flag, out))
            self.assertIn("archive/items/W1.md: the archive's line 1 at HEAD", out)
            self.assertIn("items/W1.md was deleted", out)
        git("git reset -q HEAD -- .dfs && git checkout -q -- .dfs")
        git("git mv .dfs/items/W1.md .dfs/items/W2.md")
        code, out = self.run_check("--staged")
        self.assertNotIn("was deleted", out)

    LOG = ("- 2026-09-25T09:00:00Z · session · work\n"
           "- 2026-09-25T09:01:00Z · session · critic\n"
           "- 2026-09-25T09:02:00Z · session · work\n"
           "- 2026-09-25T09:03:00Z · raise · W1.1\n  which?\n\n  **this** or that\n"
           "- 2026-09-25T09:04:00Z · answer · 2026-09-25T09:03:00Z\n  this\n"
           "- 2026-09-25T09:05:00Z · session · work\n")

    def counts(self):
        t = dfs_tree.load("W1")
        return (dfs_tree.sessions_since_author(t), dfs_tree.sessions_since_critic(t),
                [e["ts"] for e in dfs_tree.open_raises(t)],
                [e["ts"] for e in dfs_tree.unacted_answers(t)],
                dfs_state.progress(t), [e["text"] for e in t["log"]])

    def test_compact_moves_the_logs_past_and_leaves_every_count_as_it_was(self):
        self.write(task(node(1), self.LOG))
        self.commit()
        before = self.counts()
        self.assertEqual(dfs_tree.compact("W1"), 4)
        self.assertEqual(self.counts(), before)
        kept = dfs_tree.parse((self.root / ".dfs/items/W1.md").read_text(), "W1")["log"]
        self.assertEqual([e["kind"] for e in kept], ["answer", "session"])
        subprocess.run("git add .dfs", shell=True, cwd=self.root, check=True)
        code, out = self.run_check("--staged")
        self.assertEqual(code, 0, out)
        self.commit()
        self.assertEqual(dfs_tree.compact("W1"), 0)

    def test_shelve_moves_determined_plans_and_every_reader_sees_them_as_before(self):
        self.write(task(node(1, "confirmed", extra="Approach: cut it\n  thin\nHypothesis: it holds\n"
                                                   "Evidence:\n- for: it ran\nDetermination: yes\n")
                        + node(2, "open", parent=1, extra="Approach: go on\nHypothesis: more\n"),
                        self.LOG))
        self.commit()
        before = (self.counts(), dfs_state.content(dfs_tree.load("W1")))
        self.assertEqual(dfs_tree.shelve("W1"), 2)
        text = (self.root / ".dfs/items/W1.md").read_text()
        self.assertIn("Approach: archived.\nHypothesis: archived.\n", text)
        self.assertNotIn("thin", text)
        self.assertIn("Approach: go on", text, "an open node is not history")
        t = dfs_tree.load("W1")
        self.assertEqual(dfs_tree.by_id(t)["W1.1"]["fields"]["Approach"], "cut it thin")
        self.assertEqual(dfs_tree.by_id(t)["W1.1"]["from_archive"], {"Approach", "Hypothesis"})
        self.assertEqual((self.counts(), dfs_state.content(t)), before)
        subprocess.run("git add .dfs", shell=True, cwd=self.root, check=True)
        code, out = self.run_check("--staged")
        self.assertEqual(code, 0, out)
        self.commit()
        self.assertEqual(dfs_tree.shelve("W1"), 0, "a second shelve moves nothing")
        with self.assertRaises(ValueError):
            dfs_tree.shelve("W1", ("W1.2",))

    def test_an_added_node_is_open_numbered_next_and_passes_the_check(self):
        self.write(task(node(1), self.LOG))
        self.commit()
        nid = dfs_tree.add_node("W1", "  try   the other way ", "W1.1", "swap the\nparser")
        self.assertEqual(nid, "W1.2")
        top = dfs_tree.add_node("W1", "something apart")
        self.assertEqual(top, "W1.3")
        nodes = dfs_tree.by_id(dfs_tree.load("W1"))
        self.assertEqual(nodes["W1.2"]["title"], "try the other way")
        self.assertEqual((nodes["W1.2"]["status"], nodes["W1.2"]["parent"]), ("open", "W1.1"))
        self.assertEqual(nodes["W1.2"]["fields"]["Approach"], "swap the parser")
        self.assertIsNone(nodes["W1.3"]["parent"])
        subprocess.run("git add .dfs", shell=True, cwd=self.root, check=True)
        code, out = self.run_check("--staged")
        self.assertEqual(code, 0, out)
        with self.assertRaises(ValueError):
            dfs_tree.add_node("W1", "orphan", "W1.9")
        with self.assertRaises(ValueError):
            dfs_tree.add_node("W1", "   ")

    def test_an_edited_task_changes_its_name_and_goal_and_nothing_else(self):
        self.write(task(node(1, "confirmed"), self.LOG))
        self.commit()
        before = dfs_tree.load("W1")
        dfs_tree.edit_task("W1", "  A  new   name ", "Line one.\n\nLine two.\n")
        t = dfs_tree.load("W1")
        self.assertEqual((t["name"], t["goal"]), ("A new name", "Line one.\n\nLine two."))
        self.assertEqual(t["title"], "# W1 \u00b7 A new name")
        self.assertEqual([n["raw"] for n in t["nodes"]], [n["raw"] for n in before["nodes"]])
        self.assertEqual([e["text"] for e in t["log"]], [e["text"] for e in before["log"]])
        subprocess.run("git add .dfs", shell=True, cwd=self.root, check=True)
        code, out = self.run_check("--staged")
        self.assertEqual(code, 0, out)
        with self.assertRaises(ValueError):
            dfs_tree.edit_task("W1", "  ", "goal")
        with self.assertRaises(ValueError):
            dfs_tree.edit_task("W1", "name", " \n")
        with self.assertRaises(FileNotFoundError):
            dfs_tree.edit_task("W9", "name", "goal")

    def test_a_goal_with_a_section_heading_line_is_refused_and_the_file_is_untouched(self):
        self.write(task(node(1, "confirmed"), self.LOG))
        path = dfs_tree.part_path("W1")
        before = path.read_bytes()
        for goal in ("Why\n\n## Why not\nbecause", "x\n## Log\ny", "## Tree"):
            with self.assertRaises(ValueError):
                dfs_tree.edit_task("W1", "name", goal)
            self.assertEqual(path.read_bytes(), before)
        dfs_tree.edit_task("W1", "name", "A ## in the middle\n### Sub\n## Why?")
        t = dfs_tree.load("W1")
        self.assertEqual(t["goal"], "A ## in the middle\n### Sub\n## Why?")
        self.assertEqual(t["order"], ["Goal", "Tree", "Log"])

    def test_a_task_with_no_goal_section_gets_one_first_when_edited(self):
        self.write(task(node(1), self.LOG).replace("## Goal\n\nMake it work.\n\n", ""))
        self.assertNotIn("Goal", dfs_tree.load("W1")["order"])
        dfs_tree.edit_task("W1", "name", "Now there is one.")
        t = dfs_tree.load("W1")
        self.assertEqual((t["goal"], t["order"]), ("Now there is one.", ["Goal", "Tree", "Log"]))

    def test_an_approach_with_heading_like_lines_stays_one_line_in_its_node(self):
        self.write(task(node(1), self.LOG))
        dfs_tree.edit_node("W1", "W1.1", "t", "first\n## Log\n### W1.9 \u00b7 not a node\nlast")
        t = dfs_tree.load("W1")
        self.assertEqual(t["order"], ["Goal", "Tree", "Log"])
        self.assertEqual([n["id"] for n in t["nodes"]], ["W1.1"])
        self.assertEqual(t["nodes"][0]["fields"]["Approach"], "first ## Log ### W1.9 \u00b7 not a node last")

    def test_an_edited_node_changes_its_title_and_approach_only_while_undetermined(self):
        self.write(task(node(1, "confirmed", extra="Approach: done.\nDetermination: Confirmed: ok.\n")
                        + node(2, parent=1, extra="Approach: old way,\n  wrapped on.\nHypothesis: it holds. Wrong if not.\n")
                        + node(3, "parked", parent=1) + node(4, parent=3, extra="Approach: archived.\n"), self.LOG))
        self.commit()
        dfs_tree.edit_node("W1", "W1.2", "  new   title ", "the\nnew way")
        dfs_tree.edit_node("W1", "W1.3", "now with one", "added approach")
        t = dfs_tree.by_id(dfs_tree.load("W1"))
        self.assertEqual((t["W1.2"]["title"], t["W1.2"]["fields"]["Approach"]), ("new title", "the new way"))
        self.assertEqual(t["W1.2"]["fields"]["Hypothesis"], "it holds. Wrong if not.")
        self.assertEqual((t["W1.2"]["status"], t["W1.2"]["parent"]), ("open", "W1.1"))
        self.assertEqual(t["W1.3"]["fields"]["Approach"], "added approach")
        self.assertEqual(t["W1.3"]["raw"][1:4], ["Parent: W1.1", "Status: parked", "Approach: added approach"])
        dfs_tree.edit_node("W1", "W1.3", "now with one", "")
        self.assertNotIn("Approach", dfs_tree.by_id(dfs_tree.load("W1"))["W1.3"]["fields"])
        self.assertEqual(dfs_tree.by_id(dfs_tree.load("W1"))["W1.1"]["raw"], t["W1.1"]["raw"])
        subprocess.run("git add .dfs", shell=True, cwd=self.root, check=True)
        code, out = self.run_check("--staged")
        self.assertEqual(code, 0, out)
        for nid, title in (("W1.1", "x"), ("W1.4", "x"), ("W1.9", "x"), ("W1.2", " ")):
            with self.assertRaises(ValueError, msg=nid):
                dfs_tree.edit_node("W1", nid, title, "")

    def test_the_authors_words_stay_with_the_node(self):
        log = (self.LOG + "- 2026-09-25T09:10:00Z · raise · W1.1\n  Which way?\n"
               "- 2026-09-25T09:11:00Z · answer · 2026-09-25T09:10:00Z\n  The second.\n"
               "- 2026-09-25T09:12:00Z · correct · W1.1 · refuted\n  It leaks.\n")
        self.write(task(node(1, "confirmed", extra="Hypothesis: it holds. Wrong if it leaks.\n"
                                                   "Determination: Confirmed: fine.\n"), log))
        t = dfs_tree.load("W1")
        st = dfs_tree.story(t, "W1.1")
        self.assertEqual([(r["body"].strip(), a["body"].strip()) for r, a in st["answered"]],
                         [("which?\n\n**this** or that", "this"), ("Which way?", "The second.")])
        self.assertEqual(st["overruled"]["body"].strip(), "It leaks.")
        self.assertEqual(dfs_tree.assumed(t), [], "refuted by the author, so moot")
        self.assertEqual(dfs_tree.split_falsifier("It holds. Wrong if it leaks."),
                         ("It holds.", "it leaks."))

    def test_notes_are_the_remarks_that_came_with_a_verdict(self):
        log = self.LOG + "- 2026-09-25T09:13:00Z · review · ok\n  `_locks` grows.\n- 2026-09-25T09:14:00Z · critic · ok\n"
        self.write(task(node(1), log))
        self.assertEqual(dfs_tree.notes(dfs_tree.load("W1")),
                         [("review", "2026-09-25T09:13:00Z", "ok", "`_locks` grows.")])

    def test_a_node_that_asks_is_assumed_too(self):
        self.write(task(node(1, extra="Ask: Should it warn or fail?\n"), self.LOG))
        self.assertEqual(dfs_tree.assumed(dfs_tree.load("W1")), ["W1.1"])

    def test_a_node_the_author_has_ruled_on_is_no_longer_flagged(self):
        """Confirmed or refuted by the author's correction, the assumption is answered."""
        hyp = "Hypothesis: it holds. Wrong if it leaks.\n"
        for verdict in ("confirmed", "refuted"):
            self.write(task(node(1, "confirmed", extra=hyp) + node(2, "confirmed", extra=hyp),
                            self.LOG + "- 2026-09-25T09:12:00Z · correct · W1.1 · %s\n  Seen.\n" % verdict))
            self.assertEqual(dfs_tree.assumed(dfs_tree.load("W1")), ["W1.2"], verdict)

    def test_a_node_that_points_instead_of_saying_gets_a_note_not_a_refusal(self):
        text = task(node(1, "open", extra="Approach: Fix what W1.0 found\n")
                    + node(2, "open", extra="Approach: In src/cache.py get_or_set, take a "
                                            "per-key lock, re-check the cache inside it.\n")
                    + node(3, "open"), self.LOG)
        notes = dfs_check.standalone_notes("W1", text)
        self.assertEqual(len(notes), 1)
        self.assertIn("W1.1 Approach", notes[0])
        self.assertEqual(dfs_check.standalone_notes("W1", text, head=text), [],
                         "only nodes that are new in this change")
        self.write(text)
        self.commit()
        code, out = self.run_check()
        self.assertEqual(code, 0, out)

    def test_a_verdict_given_across_a_compact_is_still_counted(self):
        old = "- 2026-09-25T09:01:30Z · critic · ok\n"
        log = self.LOG.replace("- 2026-09-25T09:02:00Z", old + "- 2026-09-25T09:02:00Z")
        self.write(task(node(1), log))
        self.commit()
        before = dfs_state.verdicts("W1", "critic")
        self.write(task(node(1), log + "- 2026-09-25T09:06:00Z · critic · issues\n  W1.2\n"))
        self.assertEqual(dfs_tree.compact("W1"), 5)
        items = (self.root / ".dfs/items/W1.md").read_text()
        self.assertNotIn(old, items)
        self.assertEqual((before, dfs_state.verdicts("W1", "critic")), (1, 2))
        self.assertEqual(dfs_state.verdicts("W1", "review"), 0)

    def test_only_the_past_moves_and_only_whole_under_log(self):
        head = task(node(1), self.LOG)
        entry = "- 2026-09-25T09:02:00Z · session · work\n"
        gone = head.replace(entry, "")
        self.assertEqual(dfs_check.check_history("W1", gone, head, "**Log**:\n" + entry), [])
        for arc in ("", entry, "**W1.1 Evidence**:\n" + entry,
                    "**Log**:\n- 2026-09-25T09:02:00Z · session · work · x\n"):
            self.assertIn("log only grows",
                          " ".join(dfs_check.check_history("W1", gone, head, arc)), arc)
        late = "- 2026-09-25T09:05:00Z · session · work\n"
        self.assertIn("log only grows", " ".join(dfs_check.check_history(
            "W1", head.replace(late, ""), head, "**Log**:\n" + late)))

    def test_an_answer_may_name_a_raise_the_archive_holds(self):
        raise_ = "- 2026-09-25T09:03:00Z · raise · W1.1\n  which?\n\n  **this** or that\n"
        now = task(node(1), self.LOG.replace(raise_, ""))
        self.assertIn("names no raise", " ".join(dfs_check.check_shape("W1", now)))
        self.assertEqual(dfs_check.check_shape("W1", now, "# W1\n\n**Log**:\n" + raise_), [])
        t = dfs_tree.with_archive(dfs_tree.parse(now, "W1"), "**Log**:\n" + raise_)
        self.assertEqual(dfs_tree.open_raises(t), [])

    def test_an_open_node_may_change_and_the_log_only_grows(self):
        head = task(node(1), "- 2026-09-25T09:00:00Z · session · work\n")
        now = head.replace("Status: open", "Status: confirmed")
        self.assertEqual(dfs_check.check_history("W1", now, head), [])
        gone = head.replace("- 2026-09-25T09:00:00Z · session · work\n", "")
        self.assertIn("log only grows", " ".join(dfs_check.check_history("W1", gone, head)))


class Tags(unittest.TestCase):
    def test_a_branch_name_sanitises_to_a_tag(self):
        for name, tag in (("fix-sync", "fix-sync"), ("rowan/Fix Sync!", "rowan-fix-sync"),
                          ("feature//x__y", "feature-x-y"), ("2026-plan", "b-2026-plan"),
                          ("---", "b"), ("a" * 50, "a" * 32)):
            self.assertEqual(dfs_paths.sanitise(name), tag, name)
            self.assertRegex(dfs_paths.sanitise(name), r"^%s$" % dfs_paths.TAG_RE)

    def test_a_tagged_id_splits_one_way(self):
        self.assertEqual(dfs_tree.split_id("W13.4"), ("W13", 4, ""))
        self.assertEqual(dfs_tree.split_id("W13.4@fix-sync"), ("W13", 4, "fix-sync"))
        self.assertEqual(dfs_tree.split_id("W30@fx.2@fx"), ("W30@fx", 2, "fx"))
        self.assertEqual(dfs_tree.split_id("W30@fx.2"), ("W30@fx", 2, ""))
        self.assertIsNone(dfs_tree.split_id("W13.4@Fix"))

    def test_a_part_is_named_by_its_path(self):
        self.assertEqual(dfs_tree.part_of("W13.md"), ("W13", ""))
        self.assertEqual(dfs_tree.part_of("W13/fx.md"), ("W13", "fx"))
        self.assertEqual(dfs_tree.part_of("W30@fx/fx.md"), ("W30@fx", "fx"))
        self.assertIsNone(dfs_tree.part_of("_TEMPLATE.md"))
        self.assertIsNone(dfs_tree.part_of("W13/Fx.md"))

    def test_a_tagged_node_parses_with_its_tag(self):
        t = dfs_tree.parse("# W1\n\n## Tree\n\n### W1.2@fx · step\nParent: W1.1\n"
                           "Status: open\n\n## Log\n", "W1")
        self.assertEqual([(nd["id"], nd["n"], nd["tag"]) for nd in t["nodes"]],
                         [("W1.2@fx", 2, "fx")])


class Branches(InARepo):
    """Two branches on one task: each writes its own part, and git merges them."""

    def git(self, cmd):
        return subprocess.run("git -c user.name=t -c user.email=t@t " + cmd, shell=True,
                              cwd=self.root, capture_output=True, text=True)

    def setUp(self):
        super().setUp()
        dfs_paths._TAG_CACHE.clear()
        self.addCleanup(dfs_paths._TAG_CACHE.clear)
        self.write(task(node(1, "confirmed") + node(2, parent=1),
                        "- 2026-09-25T09:00:00Z · session · work\n"))
        self.commit()

    def switch(self, cmd):
        r = self.git(cmd)
        self.assertEqual(r.returncode, 0, r.stderr)
        dfs_paths._TAG_CACHE.clear()

    def fx_part(self):
        return self.root / ".dfs" / "items" / "W1" / "fx.md"

    def add_fx_node(self):
        p = dfs_tree.ensure_part("W1")
        t = dfs_tree.parse(p.read_text(), "W1")
        t["nodes"].append(dfs_tree.parse(
            "## Tree\n\n### W1.1@fx · the other way\nParent: W1.1\nStatus: open\n", "W1")
            ["nodes"][0])
        p.write_text(dfs_tree.render(t))

    def check(self, *args):
        argv, out = sys.argv, io.StringIO()
        sys.argv = ["check.py", *args]
        try:
            with contextlib.redirect_stdout(out):
                return dfs_check.main(), out.getvalue()
        finally:
            sys.argv = argv

    def test_the_default_branch_has_no_tag_and_a_branch_has_its_own(self):
        self.assertEqual(dfs_paths.branch_tag(), "")
        self.switch("checkout -qb Fx")
        self.assertEqual(dfs_paths.branch_tag(), "fx")
        self.assertEqual(dfs_tree.path_for("W1"), self.fx_part())

    def test_a_detached_head_and_two_branches_with_one_tag_have_no_tag(self):
        self.switch("checkout -q --detach")
        with self.assertRaises(dfs_paths.NoBranch):
            dfs_paths.branch_tag()
        self.switch("checkout -q master")
        self.git("branch fix/sync")
        self.switch("checkout -qb fix-sync")
        with self.assertRaises(dfs_paths.NoBranch):
            dfs_paths.branch_tag()

    def test_a_branch_writes_its_own_part_and_works_its_own_node_first(self):
        self.switch("checkout -qb fx")
        self.assertEqual(dfs_tree.next_node_id(dfs_tree.load("W1")), "W1.1@fx")
        self.add_fx_node()
        dfs_tree.append_log("W1", "session", ["work"])
        self.assertIn("· session · work", self.fx_part().read_text())
        self.assertNotIn("W1.1@fx", (self.root / ".dfs/items/W1.md").read_text())
        t = dfs_tree.load("W1")
        self.assertEqual(dfs_tree.frontier(t)[0]["id"], "W1.1@fx")
        self.assertEqual(dfs_tree.frontier(dfs_tree.load("W1", here=""))[0]["id"], "W1.2")
        self.assertEqual(len(t["log"]), 2)
        self.assertEqual(dfs_tree.next_node_id(t), "W1.2@fx")
        self.assertEqual(dfs_state.task_ids(), ["W1"])

    def test_two_branches_on_one_task_merge_with_plain_git(self):
        self.switch("checkout -qb fx")
        self.add_fx_node()
        dfs_tree.append_log("W1", "session", ["work"])
        self.commit()
        self.switch("checkout -q master")
        t = dfs_tree.parse((self.root / ".dfs/items/W1.md").read_text(), "W1")
        t["nodes"].append(dfs_tree.parse("## Tree\n\n### W1.3 · a third way\nParent: W1.1\n"
                                         "Status: open\n", "W1")["nodes"][0])
        (self.root / ".dfs/items/W1.md").write_text(dfs_tree.render(t))
        dfs_tree.append_log("W1", "session", ["work"])
        self.commit()
        r = self.git("merge -q --no-edit fx")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        t = dfs_tree.load("W1")
        self.assertEqual(sorted(nd["id"] for nd in t["nodes"]),
                         ["W1.1", "W1.1@fx", "W1.2", "W1.3"])
        self.assertEqual(len([e for e in t["log"] if e["kind"] == "session"]), 3)
        rc, out = self.check()
        self.assertEqual(rc, 0, out)

    def test_a_branch_may_not_add_to_another_part(self):
        self.switch("checkout -qb fx")
        self.write(task(node(1, "confirmed") + node(2, parent=1) + node(3, parent=1),
                        "- 2026-09-25T09:00:00Z · session · work\n"))
        rc, out = self.check()
        self.assertEqual(rc, 1, out)
        self.assertIn("this branch writes only its own", out)
        self.assertIn("W1.3", out)

    def test_a_branch_may_determine_a_node_in_another_part(self):
        self.switch("checkout -qb fx")
        self.write(task(node(1, "confirmed") + node(2, "refuted", parent=1),
                        "- 2026-09-25T09:00:00Z · session · work\n"))
        rc, out = self.check()
        self.assertEqual(rc, 0, out)

    def test_a_node_in_the_wrong_part_is_refused(self):
        self.switch("checkout -qb fx")
        self.fx_part().parent.mkdir()
        self.fx_part().write_text("# W1\n\n## Tree\n\n### W1.3 · untagged\nParent: W1.1\n"
                                  "Status: open\n\n## Log\n")
        bad = " ".join(dfs_check.check_shape(
            "W1", self.fx_part().read_text(), "", "fx",
            dfs_tree.load("W1")))
        self.assertIn("W1.3 is in the part of tag fx", bad)

    def test_a_task_opened_on_a_branch_lives_in_that_branchs_part(self):
        self.switch("checkout -qb fx")
        p = dfs_tree.part_path("W2@fx", "fx")
        p.parent.mkdir(parents=True)
        p.write_text("# W2@fx · on a branch\n\n## Goal\n\nit\n\n## Tree\n\n"
                     "### W2@fx.1@fx · first\nStatus: open\n\n## Log\n")
        self.assertEqual(dfs_state.task_ids(), ["W1", "W2@fx"])
        t = dfs_tree.load("W2@fx")
        self.assertEqual((t["goal"], t["nodes"][0]["id"]), ("it", "W2@fx.1@fx"))
        rc, out = self.check()
        self.assertEqual(rc, 0, out)
        self.commit()
        self.switch("checkout -q master")
        self.assertFalse(dfs_tree.exists("W2@fx"))

    def hooked(self):
        """This repo with the tool's guard as its pre-merge-commit hook, linked rather
        than copied: /tmp may be noexec, and git skips a hook it cannot run."""
        hooks = self.root.parent / (self.root.name + "-hooks")
        hooks.mkdir()
        self.addCleanup(shutil.rmtree, hooks, True)
        (hooks / "pre-merge-commit").symlink_to(HERE / "hook.sh")
        self.git("config core.hooksPath %s" % hooks)

    def test_the_pre_merge_commit_hook_judges_a_merge_without_the_writer_rule(self):
        self.hooked()
        self.switch("checkout -qb fx")
        self.add_fx_node()
        self.commit()
        self.switch("checkout -q master")
        dfs_tree.append_log("W1", "session", ["work"])
        self.commit()
        r = self.git("merge --no-edit fx")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.git("rev-parse -q --verify HEAD^2").returncode, 0)

    def test_the_pre_merge_commit_hook_refuses_a_merge_that_removes_a_node(self):
        self.hooked()
        self.switch("checkout -qb fx")
        self.write(task(node(1, "confirmed"), "- 2026-09-25T09:00:00Z · session · work\n"))
        self.commit()   # no pre-commit hook here, so only the merge can catch it
        self.switch("checkout -q master")
        (self.root / "f").write_text("x\n")
        self.commit()
        r = self.git("merge --no-edit fx")
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("node W1.2 was removed", r.stdout + r.stderr)
        self.assertNotEqual(self.git("rev-parse -q --verify HEAD^2").returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
