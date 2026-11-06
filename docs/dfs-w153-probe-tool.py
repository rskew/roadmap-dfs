import sys
sys.path.insert(0, sys.argv[1] + "/scripts")
import check
long = " ".join(["word"] * 81)
ok = " ".join(["word"] * 80)
def task(h, d):
    return ("# W1 · t\n\n## Goal\n\ng\n\n## Tree\n\n### W1.1 · a\nStatus: confirmed\nApproach: x\n"
            + ("Hypothesis: %s\n" % h if h else "") + "Determination: %s\n\n## Log\n\n" % d)
read = lambda p: None
plain = "It holds. Wrong if it does not."
for label, h, d in (("page-less hypothesis", plain, "ok"), ("81-word determination", "", long), ("80-word determination", "", ok)):
    t = task(h, d)
    a = check.check_assumptions("W1", t, "", read)
    b = check.check_determination_budget("W1", t, "") if hasattr(check, "check_determination_budget") else "no such check"
    print("%-24s assumptions refused: %-5s budget: %s" % (label, bool(a), (b if isinstance(b, str) else ("refused" if b else "passes"))))
