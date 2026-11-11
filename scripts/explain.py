#!/usr/bin/env python3
"""Code explainer: one page that shows a piece of code from six angles.

    explain.py NOTES.json [--out PAGE.html]
    explain.py NOTES.json --drift      list the noted functions whose source changed since the notes were confirmed
    explain.py NOTES.json --confirm    keep a checksum of each noted function, after reading its notes against it

NOTES.json (see docs/explain.md) names the Python files to read and holds the facts a person has
to judge. This script does the part a machine can do truthfully (a call is drawn only when the text fixes its
target; a call on an object of unknown type is left out, not guessed):

  * the top-level view, a call tree read from the files with `ast`;
  * the "errors handled" lens' `try`/`except` and `raise` lines, read from the same tree;
  * a check that every function a note names exists, so a page cannot drift from the code
    without this script failing.

The page is one self-contained HTML file, written for a phone first (docs/artefacts.md). Every shape,
colour and arrow on it comes from the two tables below (PLACES and KINDS), and so does the legend:
a page cannot use a symbol the legend does not explain.
"""
import ast
import hashlib
import html
import itertools
import json
import os
import sys

# The lenses, in reading order: id, tab title, the question it answers, colour.
LENSES = [
    ("data", "Data flow", "Where does a value come from, and where does it end up?", "#1f5fbf"),
    ("state", "State", "What is remembered between calls, and what changes it?", "#7b3fb3"),
    ("resource", "Resources", "What is opened or held, and what lets it go?", "#b85c00"),
    ("failure", "Failure", "When something goes wrong here, who finds out?", "#c0262d"),
    ("queue", "Queue", "What waits, in what order, and who takes it?", "#1c7a3e"),
    ("errors", "Errors handled", "Which errors are caught, and what is done about each?", "#0f7a7a"),
]
LENS = {k: (title, ask, colour) for k, title, ask, colour in LENSES}

# A thing an arrow joins. glyph, what it is, the lens whose colour it takes.
PLACES = {
    "function": ("ƒ", "code in the files read", None),
    "actor": ("◉", "a person or program outside these files", None),
    "store": ("▤", "a file or directory holding data", "data"),
    "state": ("◆", "a value that is remembered and changes", "state"),
    "resource": ("⬢", "something opened that must be let go", "resource"),
    "queue": ("≣", "things waiting, taken in order", "queue"),
}

# An arrow. lens, how the line is drawn, its head, what it means.
KINDS = {
    "moves": ("data", "solid", "▶", "a value travels from one to the other"),
    "sets": ("state", "thick", "▶", "the left one changes the state on the right"),
    "reads": ("state", "dotted", "▶", "the left one looks at the state on the right"),
    "acquires": ("resource", "solid", "●", "the left one takes hold of the resource"),
    "releases": ("resource", "dashed", "○", "the left one lets the resource go"),
    "fails": ("failure", "dashed", "▶", "a failure travels on to the right"),
    "stops": ("failure", "dashed", "■", "a failure ends at the right: nobody further out hears of it"),
    "puts": ("queue", "solid", "▶", "the left one adds to the queue on the right"),
    "takes": ("queue", "dotted", "▶", "the left one removes from the queue on the right"),
    "handles": ("errors", "solid", "▣", "the left one catches an error and does what the label says"),
}
LENS_OF = {k: v[0] for k, v in KINDS.items()}


_ids = itertools.count(1)


class NotesError(Exception):
    pass


# ── reading the code ────────────────────────────────────────────────────────────

FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _add(funcs, name, info):
    """Two functions with one qualified name in two files (the same class name in both) cannot be told
    apart by a note, so the second is an error rather than a silent overwrite of the first. Two in one
    file (a property and its setter, a def under `if`) are one name in one place: the first is kept."""
    if name in funcs and funcs[name]["file"] == info["file"]:
        return
    if name in funcs:
        raise NotesError("%r is defined twice (%s:%d and %s:%d); notes name a function by this one name"
                         % (name, funcs[name]["file"], funcs[name]["line"], info["file"], info["line"]))
    funcs[name] = info


def read_code(paths):
    """{qualified name: {file, line, calls (names), errors}} for every function, async function and
    method of a top-level class. Classes nested in classes or functions are not read; their calls
    count for the function that holds them."""
    funcs = {}
    bases = {}
    for path in paths:
        with open(path) as fh:
            tree = ast.parse(fh.read(), path)
        for node in tree.body:
            if isinstance(node, FUNCS):
                _add(funcs, node.name, _info(path, node, None))
            elif isinstance(node, ast.ClassDef):
                bases[node.name] = [b.id if isinstance(b, ast.Name) else b.attr
                                    for b in node.bases if isinstance(b, (ast.Name, ast.Attribute))]
                for sub in node.body:
                    if isinstance(sub, FUNCS):
                        _add(funcs, "%s.%s" % (node.name, sub.name), _info(path, sub, node.name))
    for q, f in funcs.items():
        f["calls"] = _resolve(f.pop("raw"), f["cls"], funcs, bases, q)
    return funcs


def _canon(node, out):
    """Write the tree out by node class and field name, skipping empty fields.

    `ast.dump` is not used: the fields a node has change between Python versions (3.12 added
    `type_params` to every def), and a checksum that moves with the interpreter would flag every noted
    function as changed. An empty field is left out so a field a newer Python adds, empty, changes nothing."""
    if isinstance(node, ast.AST):
        out.append(type(node).__name__ + "(")
        for name, value in ast.iter_fields(node):
            if value is None or value == []:
                continue
            out.append(name + "=")
            _canon(value, out)
        out.append(")")
    elif isinstance(node, list):
        out.append("[")
        for x in node:
            _canon(x, out)
            out.append(",")
        out.append("]")
    else:
        out.append(repr(node))


def checksum(node):
    """A short hash of a function's code that ignores comments, spacing and the Python version."""
    out = []
    _canon(node, out)
    return hashlib.sha1("".join(out).encode()).hexdigest()[:12]


def _info(path, node, cls):
    raw = []
    errors = []
    # A name the function binds itself (a parameter, an assignment, a loop or `with` target, a nested def)
    # is not the module function of that name, so a call on it is left out.
    a = node.args
    bound = {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs + [a.vararg, a.kwarg] if x}
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            bound.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n is not node:
            bound.add(n.name)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            bound.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            bound.update((x.asname or x.name).split(".")[0] for x in n.names)
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                if f.id not in bound:
                    raw.append(("name", f.id))
            elif isinstance(f, ast.Attribute):
                owner = f.value.id if isinstance(f.value, ast.Name) else None
                if isinstance(f.value, ast.Call) and isinstance(f.value.func, ast.Name):
                    owner = ("new", f.value.func.id)  # Class(...).name(): the owner is a fresh Class
                raw.append(("attr", f.attr, owner))
        elif isinstance(n, ast.Try):
            for h in n.handlers:
                kind = ast.unparse(h.type) if h.type else "anything"
                errors.append((h.lineno, "catches " + kind, _does(h.body)))
        elif isinstance(n, ast.Raise) and n.exc is not None:
            errors.append((n.lineno, "raises " + ast.unparse(n.exc).split("(")[0], ""))
    return {"file": path, "line": node.lineno, "cls": cls, "raw": raw, "errors": sorted(errors),
            "sum": checksum(node)}


def _does(body):
    """What an `except` body does, in a word, from its first statement."""
    first = body[0]
    if isinstance(first, ast.Pass):
        return "ignores it"
    if isinstance(first, ast.Continue):
        return "skips this one"
    if isinstance(first, ast.Raise):
        return "raises again"
    if isinstance(first, ast.Return):
        return "returns " + (ast.unparse(first.value) if first.value is not None else "nothing")
    return "runs " + ast.unparse(first).split("\n")[0][:48]


def _ancestors(cls, bases):
    """cls, then its bases, then their bases, nearest first, only those in the files read."""
    out = [cls]
    for b in bases.get(cls, []):
        if b in bases:
            out += [x for x in _ancestors(b, bases) if x not in out]
    return out


def _method(cls, name, funcs, bases):
    """The analysed method `name` that `self.name()` reaches from inside `cls`: cls's own or a base's,
    else the one a subclass in the files read supplies (a mixin calling what its subclass defines)."""
    for c in _ancestors(cls, bases):
        if "%s.%s" % (c, name) in funcs:
            return "%s.%s" % (c, name)
    subs = [c for c in bases if cls in _ancestors(c, bases) and "%s.%s" % (c, name) in funcs]
    return "%s.%s" % (subs[0], name) if len(subs) == 1 else None


def _resolve(raw, cls, funcs, bases, me):
    """The qualified names of the analysed functions a function calls, in first-call order.

    A call counts only when its target is certain from the text: `name()` for a function in the
    files read, `self.name()` / `cls.name()` for a method of the class (see `_method`), and
    `Class.name()` and `Class(...).name()` for a class in the files read. Any other `x.name()` (a dict's `.get`, a thread's
    `.start`, a call on something returned) is left out, since the text does not say what `x` is.
    """
    out = []
    for call in raw:
        target = None
        if call[0] == "name" and call[1] in funcs:
            target = call[1]
        elif call[0] == "attr":
            _, name, owner = call
            if owner in ("self", "cls") and cls:
                target = _method(cls, name, funcs, bases)
            elif owner in bases and "%s.%s" % (owner, name) in funcs:
                target = "%s.%s" % (owner, name)
            elif isinstance(owner, tuple) and owner[1] in bases and "%s.%s" % (owner[1], name) in funcs:
                target = "%s.%s" % (owner[1], name)
        if target and target != me and target not in out:
            out.append(target)
    return out


def check_notes(notes, funcs):
    """Every function and place a note names must exist."""
    places = notes.get("places", {})
    for pid, p in places.items():
        if p.get("kind") not in PLACES:
            raise NotesError("place %r has kind %r; the kinds are %s" % (pid, p.get("kind"), ", ".join(PLACES)))
    def known(ref, what):
        if ref in places:
            return
        if ref in funcs:
            return
        raise NotesError("%s names %r, which is neither a function in the files read nor a place" % (what, ref))
    for i, e in enumerate(notes.get("edges", [])):
        what = "edge %d (%s)" % (i + 1, e.get("label", ""))
        if e.get("kind") not in KINDS:
            raise NotesError("%s has kind %r; the kinds are %s" % (what, e.get("kind"), ", ".join(KINDS)))
        known(e.get("from"), what)
        known(e.get("to"), what)
        if e.get("at") is not None and e["at"] not in funcs:
            raise NotesError("%s is at %r, which is not a function in the files read" % (what, e["at"]))
        if not e.get("label"):
            raise NotesError("%s has no label" % what)
    for i, e in enumerate(notes.get("handled", [])):
        if e.get("at") not in funcs:
            raise NotesError("handled note %d is at %r, which is not a function in the files read" % (i + 1, e.get("at")))
    for r in notes.get("entries", []):
        if r not in funcs:
            raise NotesError("entry %r is not a function in the files read" % r)


# ── drawing ─────────────────────────────────────────────────────────────────────

def esc(s):
    return html.escape(str(s), quote=True)


def lenses_at(notes):
    """{function: [lens ids with a note there]}."""
    out = {}
    def add(fn, lens):
        if fn and lens not in out.setdefault(fn, []):
            out[fn].append(lens)
    for e in notes.get("edges", []):
        add(e.get("at"), LENS_OF[e["kind"]])
    for e in notes.get("handled", []):
        add(e["at"], "errors")
    return out


def named_in_notes(notes, funcs):
    """The functions the notes mark, in first-named order: an edge's `at`, `from` and `to` (when they are
    functions and not places), and a handled note's `at`."""
    out = []
    def add(q):
        if q in funcs and q not in out:
            out.append(q)
    for e in notes.get("edges", []):
        for k in ("at", "from", "to"):
            add(e.get(k))
    for h in notes.get("handled", []):
        add(h.get("at"))
    return out


def sums_path(notes_path):
    """Where the checksums of the noted functions are kept: beside the notes, `inbox.json` → `inbox.sums`."""
    return os.path.splitext(notes_path)[0] + ".sums"


def read_sums(notes_path):
    try:
        with open(sums_path(notes_path)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def drifted(notes, funcs, sums):
    """The functions the notes mark whose source (comments and spacing aside) is not what it was when the
    notes were last confirmed: `changed`, or `unconfirmed` when no checksum was ever kept for it. A changed
    function may have made a note's sentence false; this says where to read, not what is wrong."""
    return [(q, "changed" if q in sums else "unconfirmed") for q in named_in_notes(notes, funcs)
            if sums.get(q) != funcs[q]["sum"]]


def tree_html(funcs, notes):
    called = {c for f in funcs.values() for c in f["calls"]}
    roots = notes.get("entries") or [q for q in funcs if q not in called]
    if not roots:
        # Every function is called by another (mutual recursion): start from the first function of each file.
        first = {}
        for q, f in sorted(funcs.items(), key=lambda kv: kv[1]["line"]):
            first.setdefault(f["file"], q)
        roots = list(first.values())
    marks = lenses_at(notes)
    seen = set()

    def node(q, path):
        f = funcs[q]
        dots = "".join('<a class="dot" style="background:%s" href="#lens-%s" data-lens="%s" title="%s">%s</a>'
                       % (LENS[l][2], l, l, esc(LENS[l][0]), esc(LENS[l][0][0])) for l in marks.get(q, []))
        if q in seen or q in path:
            why = "calls itself again" if q in path else "shown above"
            return '<li><span class="fn again" data-node="t-%s" data-lenses="">↺ %s <small>%s</small></span></li>' % (esc(q), esc(q), why)
        seen.add(q)
        kids = "".join(node(c, path + [q]) for c in f["calls"])
        return ('<li><span class="fn" data-node="t-%s" data-lenses="%s"><b>ƒ %s</b> <small>%s:%d</small> %s</span>%s</li>'
                % (esc(q), " ".join(marks.get(q, [])), esc(q), esc(os.path.basename(f["file"])), f["line"], dots,
                   "<ul>%s</ul>" % kids if kids else ""))
    main = "".join(node(r, []) for r in roots)
    # A function a note marks that no entry reaches (a call the tree does not draw, such as one on an
    # object of unknown type) still gets a place here, so each note has its function and dots in the tree.
    extra = "".join(node(q, []) for q in named_in_notes(notes, funcs) if q not in seen)
    if not extra:
        return '<ul class="tree">%s</ul>' % main
    return ('<ul class="tree">%s</ul>\n<p class="ask">Named in the notes below, but not called from the entries '
            'by any call the code fixes:</p>\n<ul class="tree extra">%s</ul>' % (main, extra))


def chip(ref, notes, funcs):
    p = notes.get("places", {}).get(ref)
    if p:
        glyph, _, lens = PLACES[p["kind"]]
        colour = LENS[lens][2] if lens else "#444"
        return ('<span class="chip" style="border-color:%s" data-node="c-%s-%d"><i style="color:%s">%s</i>%s%s</span>'
                % (colour, esc(ref), next(_ids), colour, glyph, esc(p.get("label", ref)),
                   "<small>%s</small>" % esc(p["note"]) if p.get("note") else ""))
    return '<span class="chip" style="border-color:#444" data-node="c-%s-%d"><i>ƒ</i>%s</span>' % (esc(ref), next(_ids), esc(ref))


def edge_row(e, notes, funcs, n):
    lens, line, head, _ = KINDS[e["kind"]]
    colour = LENS[lens][2]
    at = '<span class="at">in <b>%s</b></span>' % esc(e["at"]) if e.get("at") else '<span class="at">outside these files</span>'
    return ('<div class="row" data-lens-row="%s"><div class="line">%s<span class="arrow %s" style="--c:%s"><em>%s</em><u>%s</u></span>%s</div>%s</div>'
            % (lens, chip(e["from"], notes, funcs), line, colour, esc(e["label"]), head,
               chip(e["to"], notes, funcs), at))


def lens_html(lens, notes, funcs):
    title, ask, colour = LENS[lens]
    rows = [edge_row(e, notes, funcs, i) for i, e in enumerate(notes.get("edges", [])) if LENS_OF[e["kind"]] == lens]
    extra = ""
    if lens == "errors":
        rows += ['<div class="row note"><div class="line"><span class="chip" style="border-color:%s"><i>ƒ</i>%s</span>'
                 '<span class="arrow solid" style="--c:%s"><em>%s</em><u>▣</u></span><span class="chip"><i style="color:%s">▣</i>%s</span></div>'
                 '<span class="at">in <b>%s</b></span></div>'
                 % (colour, esc(h["at"]), colour, esc(h.get("how", "")), colour, esc(h["error"]), esc(h["at"]))
                 for h in notes.get("handled", [])]
        found = []
        for q, f in funcs.items():
            for line, what, does in f["errors"]:
                found.append('<li><b>ƒ %s</b> <small>%s:%d</small> %s%s</li>'
                             % (esc(q), esc(os.path.basename(f["file"])), line, esc(what), " → " + esc(does) if does else ""))
        extra = ('<details class="found"><summary>Found in the code by reading it (%d)</summary><ul>%s</ul></details>'
                 % (len(found), "".join(found)))
    body = "".join(rows) or '<p class="none">Nothing noted for this lens.</p>'
    return ('<section class="lens" id="lens-%s" data-lens="%s" style="--c:%s"><h2>%s</h2><p class="ask">%s</p>%s%s</section>'
            % (lens, lens, colour, esc(title), esc(ask), body, extra))


def legend_html():
    places = "".join('<li><span class="chip" style="border-color:%s"><i style="color:%s">%s</i>%s</span> %s</li>'
                     % (LENS[l][2] if l else "#444", LENS[l][2] if l else "#444", g, k, esc(what))
                     for k, (g, what, l) in PLACES.items())
    arrows = "".join('<li><span class="arrow %s" style="--c:%s"><em>%s</em><u>%s</u></span> %s</li>'
                     % (line, LENS[l][2], k, head, esc(what)) for k, (l, line, head, what) in KINDS.items())
    return ('<details class="legend" open><summary>How to read these pictures</summary>'
            '<p>Colour is the lens. A box is a thing, an arrow is something that happens to it. '
            'Each box has a symbol in front, each arrow a head at the end, so nothing depends on colour alone.</p>'
            '<h3>Boxes</h3><ul>%s</ul><h3>Arrows</h3><ul>%s</ul>'
            '<p><b>in f</b> at the end of a row names the function whose code does it. The tree above lists the same functions.</p></details>'
            % (places, arrows))


CSS = """
body{margin:0;padding:16px;background:#fbfaf7;color:#1a1a1a;font:16px/1.5 -apple-system,"Segoe UI",system-ui,sans-serif}
h1{font-size:20px;margin:0 0 4px}h2{font-size:18px;margin:0 0 2px}h3{font-size:15px;margin:12px 0 4px}
.lede,.ask{max-width:62ch;color:#444;margin:0 0 12px}
small{color:#666;font-size:12px}
.tabs{display:flex;gap:6px;overflow-x:auto;margin:12px 0 8px;padding:6px 0 4px;position:sticky;top:0;z-index:2;background:#fbfaf7}
.treehelp{margin:0 0 10px;max-width:62ch;color:#222;min-height:1.5em}
.fn.dim{opacity:.4}.fn.lit{border-width:3px;padding:3px 7px;border-color:var(--lc);box-shadow:0 0 0 3px var(--lc)}
.tabs button{flex:0 0 auto;min-height:44px;padding:0 12px;border:1.5px solid var(--c);border-radius:6px;background:#fff;color:#1a1a1a;font:inherit}
.tabs button[aria-pressed=true]{background:var(--c);color:#fff}
.tree,.tree ul{list-style:none;margin:0;padding-left:16px;border-left:2px solid #d8d4c8}
.tree{border-left:0;padding-left:0}.tree li{margin:6px 0}
.fn{display:inline-block;padding:4px 8px;border:1.5px solid #333;border-radius:6px;background:#fff;font-size:15px}
.fn.again{border-style:dashed;color:#666;background:transparent}
.dot{display:inline-block;min-width:20px;height:20px;line-height:20px;text-align:center;border-radius:10px;color:#fff;font-size:12px;text-decoration:none;margin-left:4px}
.lens{margin:0 0 24px;padding:12px;border-left:6px solid var(--c);background:#fff}
.row{margin:0 0 14px;padding-bottom:10px;border-bottom:1px solid #eee}
.line{display:flex;flex-direction:column;align-items:stretch;gap:2px}
.at{display:block}
.chip{display:block;box-sizing:border-box;padding:6px 10px;border:1.5px solid #333;border-radius:6px;background:#fff;font-size:15px}
.chip i{font-style:normal;margin-right:6px}.chip small{display:block}
.arrow{display:flex;flex-direction:column;align-items:center;color:var(--c);font-size:14px;padding:2px 0}
.arrow em{font-style:normal;color:#222;text-align:center}
.arrow u{text-decoration:none;font-size:20px;line-height:1.1}
.arrow::before{content:"";height:14px;border-left:2px solid var(--c)}
.arrow.thick::before{border-left-width:6px}.arrow.dashed::before{border-left-style:dashed}.arrow.dotted::before{border-left-style:dotted}
.arrow u{transform:rotate(90deg)}
.arrow u{display:block;min-width:24px;height:24px;line-height:24px;text-align:center}
.at{font-size:13px;color:#555}
details{margin:12px 0;max-width:70ch}summary{min-height:44px;display:flex;align-items:center;cursor:pointer;font-weight:600}
.legend ul,.found ul{list-style:none;padding:0}.legend li,.found li{margin:8px 0}
.legend .arrow{display:inline-flex;flex-direction:row;align-items:center;gap:6px;vertical-align:middle}.legend .arrow::before{order:1;display:inline-block;width:40px;height:0;border:0;border-top:2px solid var(--c)}.legend .arrow.thick::before{border-top-width:6px}.legend .arrow.dashed::before{border-top-style:dashed}.legend .arrow.dotted::before{border-top-style:dotted}.legend .arrow u{order:2;transform:none}
.none{color:#666}
@media(min-width:720px){body{padding:28px 32px}
.line{flex-direction:row;align-items:center}
.row .chip{flex:0 1 240px}
.arrow{position:relative;flex:1 1 200px;min-width:140px;max-width:460px;padding:0 8px 4px;margin:0 6px;border-bottom:2px solid var(--c)}
.arrow::before{display:none}
.arrow u{position:absolute;right:-8px;bottom:-14px;transform:none}
.arrow.thick{border-bottom-width:6px}.arrow.dashed{border-bottom-style:dashed}.arrow.dotted{border-bottom-style:dotted}
.at{margin-top:4px}
.legend .arrow{position:static;flex:none;min-width:0;max-width:none;margin:0;padding:0;border:0}
.legend .arrow::before{display:inline-block}.legend .arrow u{position:static}}
"""

JS = """
(function(){var secs=[].slice.call(document.querySelectorAll('section.lens')),bs=[].slice.call(document.querySelectorAll('.tabs button')),
fns=[].slice.call(document.querySelectorAll('.tree .fn')),help=document.getElementById('treehelp'),
none=help?help.textContent:'';
if(!secs.length)return;
function show(id){secs.forEach(function(s){s.hidden=id!=='all'&&s.dataset.lens!==id});
var btn=null;bs.forEach(function(b){var on=b.dataset.lens===id;b.setAttribute('aria-pressed',on);if(on)btn=b});
var n=0,all=0;
fns.forEach(function(f){var has=id!=='all'&&f.dataset.lenses.split(' ').indexOf(id)>=0;
f.classList.toggle('lit',has);f.classList.toggle('dim',id!=='all'&&!has);
if(id!=='all'&&!f.classList.contains('again')){all++;if(has)n++}});
if(help)help.textContent=id==='all'?none:btn.textContent+': '+n+' of '+all+' functions in the tree have something to say here. The others are faded.';
if(btn)fns.forEach(function(f){f.style.setProperty('--lc',btn.style.getPropertyValue('--c'))})}
bs.forEach(function(b){b.onclick=function(){show(b.dataset.lens)}});
[].forEach.call(document.querySelectorAll('a.dot'),function(a){a.onclick=function(){show(a.dataset.lens)}});
show('all')})();
"""


def page(notes, funcs):
    tabs = "".join('<button type="button" data-lens="%s" style="--c:%s" aria-pressed="false">%s</button>' % (k, c, esc(t))
                   for k, t, _, c in LENSES)
    tabs = '<button type="button" data-lens="all" style="--c:#333" aria-pressed="true">All</button>' + tabs
    return ('<!doctype html>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            '<title>Explainer: %s</title>\n<style>%s</style>\n'
            '<h1>%s</h1>\n<p class="lede">%s</p>\n'
            '<h2>The code at a glance</h2>\n<p class="ask">Who calls whom in %s. The coloured dots mark the functions each lens below has something to say about; tap one to see it. Every such function is in the tree.</p>\n'
            '<div class="tabs" role="group" aria-label="Lenses">%s</div>\n'
            '<p class="treehelp" id="treehelp" aria-live="polite">Tap a lens to light the functions it has something to say about and fade the rest.</p>\n'
            '%s\n%s\n%s\n<script>%s</script>\n'
            % (esc(notes["title"]), CSS, esc(notes["title"]), esc(notes.get("about", "")),
               esc(", ".join(notes["files"])), tabs, tree_html(funcs, notes), legend_html(),
               "".join(lens_html(k, notes, funcs) for k, *_ in LENSES), JS))


def build(notes_path, root="."):
    with open(notes_path) as fh:
        notes = json.load(fh)
    paths = [os.path.join(root, f) for f in notes["files"]]
    for path in paths:
        if not os.path.isfile(path):
            raise NotesError("cannot read %s: the paths in `files` are relative to the current directory, "
                             "so run this from the repository root" % path)
    funcs = read_code(paths)
    check_notes(notes, funcs)
    return page(notes, funcs)


def load(notes_path, root="."):
    """The notes, the functions they were read against and the checksums kept for them."""
    with open(notes_path) as fh:
        notes = json.load(fh)
    funcs = read_code([os.path.join(root, f) for f in notes["files"]])
    check_notes(notes, funcs)
    return notes, funcs, read_sums(notes_path)


def confirm(notes_path, root="."):
    """Keep a checksum of every function the notes mark: the notes have just been read against this code."""
    notes, funcs, _ = load(notes_path, root)
    with open(sums_path(notes_path), "w") as fh:
        json.dump({q: funcs[q]["sum"] for q in named_in_notes(notes, funcs)}, fh, indent=1, sort_keys=True)
        fh.write("\n")


def main(argv):
    args = argv[1:]
    mode = None
    for flag in ("--drift", "--confirm"):
        if flag in args:
            mode = flag
            args.remove(flag)
    if mode:
        if len(args) != 1:
            print("usage: explain.py NOTES.json --drift | --confirm", file=sys.stderr)
            return 2
        try:
            if mode == "--confirm":
                confirm(args[0])
                return 0
            notes, funcs, sums = load(args[0])
        except (NotesError, OSError, ValueError, KeyError) as exc:
            print("explain.py: %s" % exc, file=sys.stderr)
            return 1
        moved = drifted(notes, funcs, sums)
        for q, why in moved:
            print("%s (%s:%d): %s since the notes were confirmed" % (q, funcs[q]["file"], funcs[q]["line"], why))
        return 1 if moved else 0
    out = None
    if "--out" in args:
        i = args.index("--out")
        out = args[i + 1] if i + 1 < len(args) else None
        args = args[:i] + args[i + 2:]
    if len(args) != 1 or ("--out" in argv and out is None):
        print("usage: explain.py NOTES.json [--out PAGE.html | --drift | --confirm] (run from the repository root: `files` are relative to it)", file=sys.stderr)
        return 2
    try:
        text = build(args[0])
        moved = drifted(*load(args[0])[:2], read_sums(args[0]))
    except (NotesError, OSError, ValueError, KeyError) as exc:
        print("explain.py: %s" % exc, file=sys.stderr)
        return 1
    if moved:
        print("explain.py: %d noted function(s) changed since the notes were confirmed (`--drift` lists them): %s"
              % (len(moved), ", ".join(q for q, _ in moved)), file=sys.stderr)
    if out:
        with open(out, "w") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
