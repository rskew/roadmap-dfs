You are checking one step of a work log. Below is the step (a "node"). The current directory holds a copy of the repository exactly as it stood when the node was committed, and `NODE_COMMIT.txt` holds that commit's message and diff. You know nothing of who reviewed the node or what happened to it later, and you must not guess. You have read-only tools (read a file, search, list); you cannot run code or git.

A node says what was done (Approach), may state an assumption (Hypothesis), lists what was seen (Evidence), and ends with a verdict (Determination). A Determination may name a result, such as "47 tests passed", that the node text does not reproduce and that you cannot rerun. Do not mark a node down for a result you cannot rerun. But do read what you can: the files, tests, scripts and numbers the node names, and the diff in NODE_COMMIT.txt.

Decide whether the node's evidence is evidence for its claim. Mark it FLAG only for one of these, and only when you can quote the words of the node and name the file or diff line you read that shows the fault:

- b: the observation is of another object than the claim: another version, definition, population, file, or a stand-in, while the Determination says it is the real thing. (A claim openly scoped to the stand-in is not this.)
- c: the Determination claims more than the observation covers: it names a part (a document edited, a case, a project, a clause) that the observations plainly do not reach, and states that part as seen.
- d: the check could not have come out otherwise: reading the test or script, it passes with or without the change, it asserts the thing it was just made to assert, or the node says the control is missing.
- e: the Determination contradicts its own Approach or Evidence.
- a: the Determination states a result as observed while the node says the check was not run, could not be run, or was only reasoned.
- f: a statement the node makes about the repository (a file, function, count, test, line, commit message) is contradicted by the copy of the repository, or the diff does not do what the Determination says it does.

Read before you decide: open what the node names, but stop when you are satisfied. Use at most about 15 tool calls. Do not mark FLAG for style, length, or because you would have done the work differently.

Mark it UNSHOWN when the Determination states observations that neither the node text nor the repository lets you check, and nothing you read contradicts them.

Mark it PASS when the Determination names an observation of the object itself that could have gone the other way, and what you read in the repository fits it and shows no fault above.

Answer with exactly one line, in this form, and then stop:

VERDICT | PASS, UNSHOWN or FLAG | which of a, b, c, d, e, f (or none) | one sentence: the quoted words and file that show the fault, or what was observed and why it carries the claim

The node follows.

---

