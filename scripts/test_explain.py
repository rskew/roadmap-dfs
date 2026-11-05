#!/usr/bin/env python3
"""The code explainer (`explain.py`): the call tree is read from the code, a note that names a
function the code lacks is refused, and every arrow kind and box kind has a legend entry.

Run:  python3 roadmap-dfs/scripts/test_explain.py
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import explain  # noqa: E402

ROOT = os.path.dirname(HERE)
NOTES = os.path.join(ROOT, "docs", "explain")

CODE = '''
def a():
    b()
    b()

def b():
    c()

def c():
    a()

class K:
    def run(self):
        self.step()
    def step(self):
        try:
            a()
        except OSError:
            pass
        raise ValueError("x")
'''


def write(dirname, name, text):
    path = os.path.join(dirname, name)
    with open(path, "w") as fh:
        fh.write(text)
    return path


class ReadCode(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.funcs = explain.read_code([write(self.dir, "m.py", CODE)])

    def test_calls_are_resolved_once_each_in_order(self):
        self.assertEqual(self.funcs["a"]["calls"], ["b"])
        self.assertEqual(self.funcs["K.run"]["calls"], ["K.step"])
        self.assertEqual(self.funcs["K.step"]["calls"], ["a"])

    def test_errors_are_read_from_try_and_raise(self):
        got = [(w, d) for _, w, d in self.funcs["K.step"]["errors"]]
        self.assertEqual(got, [("catches OSError", "ignores it"), ("raises ValueError", "")])

    def test_a_cycle_is_drawn_once_and_marked(self):
        text = explain.tree_html(self.funcs, {"entries": ["a"]})
        self.assertEqual(text.count("calls itself again"), 1)


SAME_NAMES = '''
import threading

class Host:
    def get(self, key):
        return key
    def start(self):
        pass
    def use(self, table, q):
        table.get(1)
        self.table.get(2)
        q.get()
    def spawn(self):
        threading.Thread(target=self.use).start()
    def make(self):
        Host().start()

class Base:
    def ping(self):
        self.pong()
    def run(self):
        self.pong()

class Mixin(Base):
    def pong(self):
        pass
'''


class WrongOwners(unittest.TestCase):
    """A call on something whose type the text does not give is not a call to a same-named method."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.funcs = explain.read_code([write(self.dir, "m.py", SAME_NAMES)])

    def test_dict_queue_and_thread_calls_are_not_the_methods_of_the_same_name(self):
        self.assertEqual(self.funcs["Host.use"]["calls"], [])
        self.assertEqual(self.funcs["Host.spawn"]["calls"], [])

    def test_a_call_on_a_fresh_instance_or_on_self_is_kept(self):
        self.assertEqual(self.funcs["Host.make"]["calls"], ["Host.start"])

    def test_a_base_class_method_reaches_what_its_subclass_defines(self):
        self.assertEqual(self.funcs["Base.ping"]["calls"], ["Mixin.pong"])


SHADOWED = '''
def go():
    pass

def by_parameter(go):
    go()

def by_assignment():
    go = len
    go("x")

def plain():
    go()
'''


class Shadowed(unittest.TestCase):
    def test_a_name_the_function_binds_is_not_the_module_function(self):
        d = tempfile.mkdtemp()
        funcs = explain.read_code([write(d, "m.py", SHADOWED)])
        self.assertEqual(funcs["by_parameter"]["calls"], [])
        self.assertEqual(funcs["by_assignment"]["calls"], [])
        self.assertEqual(funcs["plain"]["calls"], ["go"])


class Duplicates(unittest.TestCase):
    def test_one_class_name_in_two_files_is_refused_and_async_is_read(self):
        d = tempfile.mkdtemp()
        one = write(d, "one.py", "class K:\n    async def go(self):\n        self.go()\n")
        two = write(d, "two.py", "class K:\n    def go(self):\n        pass\n")
        self.assertIn("K.go", explain.read_code([one]))
        with self.assertRaises(explain.NotesError):
            explain.read_code([one, two])


class Notes(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.funcs = explain.read_code([write(self.dir, "m.py", CODE)])

    def notes(self, **edge):
        e = {"kind": "moves", "from": "a", "to": "b", "label": "x", "at": "a"}
        e.update(edge)
        return {"title": "t", "files": ["m.py"], "edges": [e]}

    def test_good_notes_pass(self):
        explain.check_notes(self.notes(), self.funcs)

    def test_a_missing_function_is_refused(self):
        for bad in ({"at": "gone"}, {"from": "gone"}, {"to": "gone"}):
            with self.assertRaises(explain.NotesError, msg=str(bad)):
                explain.check_notes(self.notes(**bad), self.funcs)

    def test_an_unknown_kind_or_empty_label_is_refused(self):
        for bad in ({"kind": "teleports"}, {"label": ""}):
            with self.assertRaises(explain.NotesError, msg=str(bad)):
                explain.check_notes(self.notes(**bad), self.funcs)


class Page(unittest.TestCase):
    def test_every_kind_is_in_the_legend(self):
        text = explain.legend_html()
        for kind in explain.KINDS:
            self.assertIn(">%s<" % kind, text)
        for kind, (glyph, _, _) in explain.PLACES.items():
            self.assertIn(glyph + "</i>" + kind, text)

    def test_every_kind_belongs_to_a_lens(self):
        lenses = {k for k, *_ in explain.LENSES}
        self.assertLessEqual({v[0] for v in explain.KINDS.values()}, lenses)

    def test_the_shipped_notes_render_against_the_current_code(self):
        found = [f for f in os.listdir(NOTES) if f.endswith(".json")]
        self.assertTrue(found)
        for name in found:
            text = explain.build(os.path.join(NOTES, name), ROOT)
            self.assertIn("<h1>", text)
            for lens, *_ in explain.LENSES:
                self.assertIn('id="lens-%s"' % lens, text)
            with open(os.path.join(NOTES, name)) as fh:
                notes = json.load(fh)
            for e in notes["edges"]:
                self.assertIn(explain.esc(e["label"]), text, "%s: edge %r is not drawn" % (name, e["label"]))

    def test_every_function_a_shipped_note_names_is_in_the_tree_with_its_dots(self):
        for name in sorted(f for f in os.listdir(NOTES) if f.endswith(".json")):
            path = os.path.join(NOTES, name)
            with open(path) as fh:
                notes = json.load(fh)
            funcs = explain.read_code([os.path.join(ROOT, f) for f in notes["files"]])
            text = explain.build(path, ROOT)
            named = explain.named_in_notes(notes, funcs)
            self.assertTrue(named, name)
            for q in named:
                self.assertIn("<b>ƒ %s</b>" % explain.esc(q), text, "%s: %s is not in the tree" % (name, q))
            for q, lenses in explain.lenses_at(notes).items():
                span = text.split('data-node="t-%s"><b>' % explain.esc(q), 1)[1].split("</span>", 1)[0]
                for l in lenses:
                    self.assertIn('data-lens="%s"' % l, span, "%s: %s has no %s dot" % (name, q, l))


class NotedButUnreached(unittest.TestCase):
    def test_a_noted_function_no_entry_reaches_is_shown_after_the_entries(self):
        d = tempfile.mkdtemp()
        funcs = explain.read_code([write(d, "m.py", "def main():\n    pass\n\ndef alone():\n    pass\n")])
        notes = {"entries": ["main"], "edges": [{"kind": "fails", "from": "alone", "to": "main", "label": "x", "at": "alone"}]}
        text = explain.tree_html(funcs, notes)
        self.assertLess(text.index("<b>ƒ main</b>"), text.index("<b>ƒ alone</b>"))
        self.assertIn('data-lens="failure"', text.split("<b>ƒ alone</b>")[1])


class NoRoots(unittest.TestCase):
    def test_a_module_where_every_function_is_called_still_gets_a_tree(self):
        d = tempfile.mkdtemp()
        funcs = explain.read_code([write(d, "m.py", "def a():\n    b()\n\ndef b():\n    a()\n")])
        text = explain.tree_html(funcs, {})
        self.assertIn("<b>ƒ a</b>", text)
        self.assertIn("<b>ƒ b</b>", text)

    def test_a_files_path_that_is_not_there_says_to_run_from_the_repository_root(self):
        d = tempfile.mkdtemp()
        notes = write(d, "n.json", '{"title": "t", "files": ["scripts/nope.py"]}')
        with self.assertRaises(explain.NotesError) as ctx:
            explain.build(notes, root=d)
        self.assertIn("repository root", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
