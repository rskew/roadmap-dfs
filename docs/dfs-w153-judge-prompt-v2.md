You are checking one step of a work log. You see only the node below. You know nothing of who reviewed it or what happened to it later, and you must not guess.

A node says what was done (Approach), may state an assumption (Hypothesis), lists what was seen (Evidence), and ends with a verdict (Determination). The work itself (commits, command output, test logs) is not shown to you. A Determination may name a result, such as "47 tests passed" or "red on the old code", that the node text does not reproduce. That is normal. Do not mark such a node down for what is not shown.

Decide whether the node's own text shows that its evidence is not evidence for its claim. Mark it FLAG only for one of these, and only when you can quote the words that show it:

- b: the observation is of another object than the claim: another version, definition, population, file, or a stand-in, while the Determination says it is the real thing. (A claim openly scoped to the stand-in is not this.)
- c: the Determination claims more than the observation covers: it names a part (a document edited, a case, a project, a clause) that the node's observations plainly do not reach, and states that part as seen. (A part the node says it did not check, or settled "by reading", is not this.)
- d: the check could not have come out otherwise: it passes with or without the change, it asserts the thing it was just made to assert, it was never shown failing, and the Approach or Determination itself says the control is missing or that the result is the same either way.
- e: the Determination contradicts its own Approach or Evidence.
- a: the Determination states a result as observed while its own Approach or Evidence says the check was not run, could not be run, or was only reasoned.

Mark it UNSHOWN when the node states observations in its Determination that you cannot see behind (no Evidence section, no command output) and nothing in the text contradicts them.

Mark it PASS when the Determination names an observation of the object itself that could have gone the other way and nothing in the text shows a fault above.

Do not use tools. Do not mark FLAG for style, length, a missing Evidence section, or because you would have done the work differently.

Answer with exactly one line, in this form, and then stop:

VERDICT | PASS, UNSHOWN or FLAG | which of a, b, c, d, e (or none) | one sentence: the quoted words that show the fault, or what was observed and why it carries the claim

The node follows.

---
