# Implementation tree

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

- W1
- W2
- W3
- W4
- W5
- W6
- W7
- W8
- W9
- W10
- W11
- W12
- W13
- W14
- W15
- W16
- W17
- W18
- W19
- W20
- W21
- W22
- W23
- W24
- W25
- W26
- W27
