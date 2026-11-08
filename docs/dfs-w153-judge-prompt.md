You are checking one step of a work log. You see only the node below. You know nothing of who reviewed it or what happened to it later, and you must not guess.

A node says what was done (Approach), may state an assumption (Hypothesis), lists what was seen (Evidence), and ends with a verdict (Determination).

Answer two questions about the Determination, and nothing else:

1. Object. What is the Determination's claim about (a file, a function, a number, a behaviour, a version, a person's wish)? Does the node say that this same object was looked at, by name: a command that was run and what it printed, a file that was read, a count that was taken? A measure of a near neighbour (another version, another definition, a part of the whole, a stand-in) is not the object.
2. Difference. Had the claim been false, would what was observed have come out differently? A check that would give the same result whether the claim is true or false (it only ran the code that was written, it only re-read the same text, it passed with and without the change, it never showed the failing case) cannot support the claim.

Mark the node FLAG when any of these holds:
- no (a): the Determination rests on reasoning or on a statement, with no observation of the object (a belief, a reading of what the code "should" do);
- no (b): the observation is of another object, version, definition or population than the claim;
- no (c): the observation covers only part of what the Determination claims, and the rest is stated as if it were seen;
- no (d): the observation would have come out the same had the claim been false;
- no (e): the node states something that its own Approach or Evidence contradicts.

Mark it PASS when the Determination names an observation of the object itself and that observation could have gone the other way. A small node that only says a step was done is PASS when the Approach names the check and the Determination says what the check showed. Do not mark FLAG for style, length, or because you would have done the work differently. Judge what is written; do not use tools and do not look for other files.

Answer with exactly one line, in this form, and then stop:

VERDICT | PASS or FLAG | which of a, b, c, d, e (or none) | one sentence: what was observed, and why it does or does not carry the claim

The node follows.

---
