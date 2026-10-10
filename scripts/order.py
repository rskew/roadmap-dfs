#!/usr/bin/env python3
"""The TREE the author means to work the items in, which their ids cannot carry.

An item id is allocated when work is SCOPED, so the list sorts by the order things
were thought of. That is rarely the order they should be done in, and until this file
existed nothing anywhere recorded the difference: the screen sorted `W1 W2 W3` and the
author held the real sequence in their head.

⚠️ **It is a TREE, and the file's own order is the depth-first preorder.** A child is
work that only makes sense under its parent, so the two facts a walker needs — what to
do next, and what to abandon when something blocks — are both read straight off the
page with no traversal to get wrong. An item that RAISES blocks its whole SUBTREE
(`state.py` derives that), which is what lets a walk step sideways onto the next
branch instead of stopping: the "dfs" in the tool's name.

Indentation is the structure and nothing else is: a node is nested under the nearest
line above it with a smaller indent. Any consistent indent works on the way IN, since
the author edits this file by hand; two spaces per level go out.

⚠️ **A BREAK is a fence across the forest: everything after it depends on the whole
tree before it.** Nesting says "this child only makes sense under that parent", which
is a claim about ONE branch, and there was no way to say the other thing an author
knows — "none of the rest of this is worth starting until all of that is finished".
A break says it, in one line, over whatever shape the tree happens to have: a rule
(`---`, markdown's own thematic break, so it is a fence when the file is read as
markdown too) between two root branches. Items below it are held until every item
above it is `done` — not merely unblocked — and `dfs_state.walk` derives that as
`waiting_on`, kept apart from `blocked_by` for the reason `blocked` and `blocked_by`
are kept apart: "answer this", "answer something above this" and "finish something
before this" are three different instructions.

⚠️ **A break can never sit INSIDE a subtree**, and that is what makes it cheap: it
ends every open branch, so the line after one is always a root, no span can contain
one, and no move can smuggle one into a branch. It is also why a break is only
allowed above a ROOT — cutting a parent from its children with a fence would be two
claims at once, and the tree already has a word for one of them.

⚠️ **This is a PREFERENCE, not a decision, and that is why it is not in §2.** §2 is
the roadmap's index and the obvious home, but everything above §3 moves only in a
declared rewrite (§0.4, §0.8) — so re-ranking work would either trip the roadmap's own
append-only guard or be declared as a rewrite, which is the one edit that costs the
whole log. Nothing in §3 becomes true or false when the author moves a branch. So it
lives in its own committed file, reachable by `tui.py`'s `[`/`]` and `{`/`}` and by
nothing else, and the experiment is dropped by deleting the file: with no file, or with
an item this file never names, the list falls back to id order at the root.

⚠️ **It is the SCREEN's tree, not the SESSION's.** §0.1 step 1 has a session pick an
item from §2, and a session never reads this file — so a shape recorded here reaches
the author choosing what to run and does not reach an agent choosing for itself. That
is the whole of its scope today; making a session honour it means giving §2 an order,
which is a rewrite-tier change and therefore the author's `PROPOSE` to make.

One place defines what the tree IS, for the same reason `state.py` is the one
place "blocked" is defined and `runs.py` the one place "live" is: a screen that
nested by one rule while the derivation walked by another is the disagreement this
repo keeps writing down.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths as dfs_paths            # noqa: E402  (needs this file's own dir on the path)

# A line that is ONLY an id, bullet optional, with its indent kept — the indent IS the
# tree. Deliberately not a search: prose above the list says "W7 is blocked on the
# terminal pairing", and a looser pattern would rank an item because of a sentence
# about it.
ID_LINE = re.compile(r"^(\s*)(?:[-*]\s+)?([A-Za-z]+\d+(?:@%s)?)\s*$" % dfs_paths.TAG_RE)

INDENT = "  "

# A break is a NODE, not a property of the items around it: it moves, is written and
# is counted like one, so nothing has to keep a separate list of fence positions in
# step with the tree. Its name is the rule itself, which no id can ever be (`ID_LINE`
# wants letters then digits), so the two can share one list without a flag.
BREAK = "---"

# ⚠️ Counted only once the list has STARTED. A rule above the prose is decoration —
# and YAML front matter is a rule with nothing above it to gate — so a fence before
# the first id would silently hold the entire roadmap.
BREAK_LINE = re.compile(r"^\s*-{3,}\s*$")


def is_break(name: str) -> bool:
    return name == BREAK

DEFAULT_HEADER = """# Implementation tree

The order to work the items in, which is not the order they were scoped in, and the
branches they hang off. The file's order is the depth-first preorder; indentation is
the structure. An item that raises blocks its whole subtree, so a walk steps sideways
onto the next branch rather than stopping.

A rule on its own line is a BREAK: everything after it waits until everything before
it is done, which is the one thing nesting cannot say. A break separates whole
branches — it never cuts one — and an item written after it is behind it, including
one nobody has placed yet.

Read by `<scripts>/tui.py` (`[`/`]` move the selected item among its
siblings, `{`/`}` move it out of and into a branch, `b` puts a break above it or takes
one away) and by `state.py`, which returns the items in this order with their
depth and the segment each is in. An item not named here sorts after every item that
is, at the root, by id. See `order.py` for why this is not §2.

Prose goes above the list; the list is the last thing in the file.

"""


def path() -> Path:
    return dfs_paths.order()


def read_text() -> str:
    p = path()
    return p.read_text() if p.exists() else ""


def nodes_of(text: str):
    """The tree as `[(id, depth)]` in preorder, duplicates dropped, breaks kept.

    Indent COLUMNS become depths through a stack, so the file may be indented with
    two spaces, four, or a tab, and a branch that was hand-edited to a ragged width
    still nests the way it looks. A depth can only ever step down by one, which is
    what makes the list a tree rather than a set of numbers.

    A break comes back as `(BREAK, 0)` in its place, and ⚠️ **it EMPTIES the stack**:
    the line after a fence is a root whatever its indent, so a `---` hand-written in
    the middle of a branch closes that branch rather than fencing off half of it.
    That one line is what guarantees a break is never inside a subtree — which every
    move below relies on without checking.
    """
    seen, out, stack = set(), [], []
    for line in text.splitlines():
        if BREAK_LINE.match(line):
            # Not before the first id (that rule is prose, or front matter), and not
            # twice running — a second fence over the same ground gates nothing the
            # first does not, and would leave an empty segment for the screen to draw.
            if out and not is_break(out[-1][0]):
                out.append((BREAK, 0))
            stack = []
            continue
        m = ID_LINE.match(line)
        if not m:
            continue
        col, name = len(m.group(1).expandtabs(4)), m.group(2)
        while stack and col <= stack[-1]:
            stack.pop()
        depth = len(stack)
        stack.append(col)
        if name in seen:
            continue
        seen.add(name)
        out.append((name, depth))
    return out


def order_of(text: str):
    """The ids named, in preorder. A flat file is a tree of roots, so this is what
    it always was."""
    return [name for name, _ in nodes_of(text) if not is_break(name)]


def ranks(text: str):
    """id -> position in the preorder. An id here that no longer exists ranks
    nothing."""
    return {name: i for i, name in enumerate(order_of(text))}


def sort_key(text: str):
    """A key over item ids: ranked ones first in their rank, the rest by id.

    The unranked go AFTER rather than into their id position, so a newly opened item
    appears at the bottom of the list — where new work that nobody has placed yet
    belongs — instead of surfacing in the middle because its number is low.
    """
    rank = ranks(text)
    end = len(rank)

    def key(name):
        m = re.match(r"^(\D*)(\d+)$", name)
        natural = (m.group(1), int(m.group(2))) if m else (name, 0)
        return (rank.get(name, end), natural)

    return key


# ── moving a node ──────────────────────────────────────────────────────────────
#
# ⚠️ A BREAK IS A SIBLING AT THE ROOT, and it moves nothing extra to keep it so. It
# has depth 0, so `siblings_of` already hands it back among the roots and `[`/`]`
# steps a branch ACROSS a fence one slot at a time — deliberately, since moving work
# to the far side of a break is the second thing an author does with one. The
# exception is `indented`, which must refuse it: nesting under a fence is not a shape.
#
# ⚠️ A NODE MOVES WITH ITS SUBTREE, always. A child is work that only makes sense
# under its parent, so detaching one by moving the parent out from under it would
# silently re-parent work onto whatever happened to be above — the row-versus-identity
# mistake this repo keeps writing down, in a different costume.

def span(nodes, i):
    """`(start, end)` of the subtree rooted at `i`: the node and its descendants."""
    depth = nodes[i][1]
    j = i + 1
    while j < len(nodes) and nodes[j][1] > depth:
        j += 1
    return i, j


def index_of(nodes, item):
    for i, (name, _) in enumerate(nodes):
        if name == item:
            return i
    return None


def parent_of(nodes, i):
    """The index of `i`'s parent, or None at the root."""
    for j in range(i - 1, -1, -1):
        if nodes[j][1] < nodes[i][1]:
            return j
    return None


def siblings_of(nodes, i):
    """Every index sharing `i`'s parent and depth, in order."""
    par = parent_of(nodes, i)
    lo = 0 if par is None else par + 1
    hi = len(nodes) if par is None else span(nodes, par)[1]
    return [j for j in range(lo, hi) if nodes[j][1] == nodes[i][1]]


def _replant(nodes, i, at, shift):
    """Cut `i`'s subtree out, re-depth it by `shift`, and paste it back at `at`."""
    s, e = span(nodes, i)
    cut = [(n, d + shift) for n, d in nodes[s:e]]
    rest = nodes[:s] + nodes[e:]
    at = at if at <= s else at - (e - s)
    return rest[:at] + cut + rest[at:]


def tree_of(items):
    """The tree as a screen shows it, in the shape `moved` and the rest work on.

    ⚠️ THE FENCES GO BACK IN HERE. The derivation hands back items alone, each
    carrying the SEGMENT it is in, and `write_order` writes the whole tree — so a
    list built without the breaks would delete every fence in the file the first time
    anything moved. The segment numbers are what they are put back by, including a gap
    left by a segment whose items have all been closed and deleted, which is why the
    inner loop is a `while` and not an `if`.
    """
    out, seg = [], 0
    for i in items:
        while seg < i.get("segment", 0):
            out.append((BREAK, 0))
            seg += 1
        out.append((i["id"], i["depth"]))
    return out


def moved(nodes, item, step):
    """`nodes` with `item` moved one place among ITS SIBLINGS, or None if it cannot.

    Up and down stay inside the branch on purpose: moving through the flattened list
    instead would change an item's parent as a side effect of a keypress that says
    nothing about parents, and the author would have re-parented work without
    deciding to. `{`/`}` is where a parent changes, and it is the only place.
    """
    i = index_of(nodes, item)
    if i is None:
        return None
    sibs = siblings_of(nodes, i)
    at = sibs.index(i)
    if not (0 <= at + step < len(sibs)):
        return None
    other = sibs[at + step]
    if step < 0:
        return _replant(nodes, i, other, 0)
    return _replant(nodes, i, span(nodes, other)[1], 0)


def placed(nodes, item, target, after):
    """`item` moved to just after (or before) its sibling `target`, or None if `target` is
    not one of its siblings, is the item itself, or either is not named.

    What a drag does: the same reach as `moved` (among the siblings, subtree and all),
    but to any slot at once. The parent never changes here; `indented` and `outdented` do that.
    """
    i, t = index_of(nodes, item), index_of(nodes, target)
    if i is None or t is None or i == t or t not in siblings_of(nodes, i):
        return None
    return _replant(nodes, i, span(nodes, t)[1] if after else t, 0)


def removed(nodes, item):
    """The tree without `item`, or None if it is not named. Its children stay where they
    were and take its place a level up: they are work in their own right, and deleting
    a parent is not deleting them (unlike a MOVE, which carries the subtree)."""
    i = index_of(nodes, item)
    if i is None:
        return None
    s, e = span(nodes, i)
    return nodes[:s] + [(n, d - 1) for n, d in nodes[s + 1:e]] + nodes[e:]


def indented(nodes, item):
    """`item` becomes the LAST CHILD of the sibling above it, or None if it has none.

    The sibling above, rather than any node above: an item's new parent has to be
    something it was already beside, or an indent would reach into a branch the
    author was not looking at. A BREAK above it is the same refusal for a stronger
    reason — the branch above is on the other side of a fence, and reaching under it
    would move this work across one as a side effect of a key that says nothing about
    fences.
    """
    i = index_of(nodes, item)
    if i is None:
        return None
    sibs = siblings_of(nodes, i)
    at = sibs.index(i)
    if at == 0:
        return None
    above = sibs[at - 1]
    if is_break(nodes[above][0]):
        return None
    return _replant(nodes, i, span(nodes, above)[1], 1)


def outdented(nodes, item):
    """`item` becomes the NEXT SIBLING of its parent, or None if it is already a root.

    Its own following siblings stay where they are. Some outliners adopt them as
    children of the node being promoted; that is a second edit nobody asked for, and
    it moves work the cursor was not on.
    """
    i = index_of(nodes, item)
    if i is None:
        return None
    par = parent_of(nodes, i)
    if par is None:
        return None
    return _replant(nodes, i, span(nodes, par)[1], -1)


def break_toggled(nodes, item):
    """`nodes` with a fence above `item`, or the fence already there taken away.

    One key for both directions because a fence has only two states and the screen
    shows which one it is in — an `insert` that needed a separate `delete` would want
    the author to select a row that is not selectable.

    Two refusals, and they are different things to do next. A NESTED item cannot have
    one: a break separates whole branches and cutting a parent from its children with
    one would be two claims at once (`{` first, then this). The FIRST item cannot
    either: a fence means "everything above is finished", and above the first item
    there is nothing, so it would gate nothing while looking as though it gated
    everything.
    """
    i = index_of(nodes, item)
    if i is None or nodes[i][1] != 0:
        return None
    if i and is_break(nodes[i - 1][0]):
        return nodes[:i - 1] + nodes[i:]
    if i == 0:
        return None
    return nodes[:i] + [(BREAK, 0)] + nodes[i:]


def has_break_above(nodes, item) -> bool:
    """Whether `item` is the first thing after a fence — what the toggle will undo."""
    i = index_of(nodes, item)
    return i is not None and i > 0 and is_break(nodes[i - 1][0])


def header_of(text: str) -> str:
    """Everything the author wrote above the list, kept across a rewrite of it."""
    if not text.strip():
        return DEFAULT_HEADER
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if ID_LINE.match(line):
            head = "".join(lines[:i])
            return head if head.endswith("\n\n") or not head else head + "\n"
    return text if text.endswith("\n\n") else text.rstrip("\n") + "\n\n"


def write_order(nodes) -> None:
    """Write the whole tree, keeping the prose above it.

    The WHOLE tree, not a patch: a file naming three of eighteen items ranks those
    three and leaves the rest at the root in id order, so a single move on an
    unplaced item would otherwise write a position the next move reads back
    differently. Writing every node the screen is showing makes what you see and
    what is on disk the same tree.
    """
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(header_of(read_text()) + render(nodes))


def render(nodes) -> str:
    """The list as it goes to disk: two spaces a level, and a fence on its own line.

    The blank lines around a rule are not decoration. `---` directly under a list
    item is a thematic break to some markdown parsers and the underline of a setext
    heading to others, and a file whose fences render as headings is one nobody will
    trust the shape of.
    """
    # ⚠️ A fence with nothing after it is not a fence, and is not written back. It
    # gates nothing (there is nothing on its far side), the screen draws no rule for
    # it, and — since a break is toggled from the row BELOW it — one left at the end
    # is one the author has no row to press `b` on. Dropping it is what keeps what
    # you see and what is on disk the same tree.
    nodes = list(nodes)
    while nodes and is_break(nodes[-1][0]):
        nodes.pop()
    out = []
    for name, depth in nodes:
        if is_break(name):
            if out and out[-1]:
                out.append("")
            out.extend([BREAK, ""])
        else:
            out.append("%s- %s" % (INDENT * depth, name))
    while out and not out[-1]:
        out.pop()
    return "".join(line + "\n" for line in out)
