#!/usr/bin/env python3
"""W153 author-time cells for the accounting rows (E1, E4, E5, E6, E7) as a share of what the author faces,
and the E8 power on the pool of 35. Reads the outputs already in docs/, so nothing is typed in twice.

  python3 docs/dfs-w153-author-unit.py

Why a share. The 60-word and one-raise margins are the spread between two replays of one task, a noise
margin. The accounting rows are not noisy: they are totals over the 238 tasks of the history. Divided by
238 a total that cannot reach 60 words or one raise never will (E1, E4), and divided by the tasks that hold
the cost it cannot fall below one (E5). So each cell is the total as a share of the total the author faces
in the same history, against 15%, the quality tier's own relative margin (1.5 of 10 clauses).
"""
import importlib.util, re
from pathlib import Path

D = Path(__file__).resolve().parent
rd = lambda n: (D / n).read_text(encoding="utf-8")
num = lambda s: int(s.replace(",", ""))

# what the author faces (d595c44a, three projects)
mine = rd("dfs-w153-mine.txt").split("== roadmap-dfs as at d595c44a")[1]
raises = num(re.search(r"TOTAL: .*?'raise': (\d+)", mine).group(1))
det_words = num(re.search(r"words read in all Determinations: (\d+)", rd("dfs-w153-e6.txt")).group(1))
e1 = rd("dfs-w153-e1.txt").split("== today's files")[0]
hyp_words = sum(num(m) for m in re.findall(r"author words: Hypothesis text (\d+)", e1))
wrongif_all, wrongif_saved = re.search(r"author words \(Wrong-if halves\): today (\d+); .*? \(saves (\d+) = ", e1).groups()
pages = re.search(r"E1 pages saved: (\d+) of (\d+) hypotheses have one, \d+ of those sit on a node with a raise and would stay, so (\d+) pages", e1).groups()
rulings = re.search(r"rulings: (\d+), refuted \d+; .*?with no page (\d+)", e1).groups()
cut80 = num(re.search(r"^\s+80\s+\d+\s+[\d.]+%\s+(\d+)", rd("dfs-w153-e6.txt"), re.M).group(1))
cut60 = num(re.search(r"^\s+60\s+\d+\s+[\d.]+%\s+(\d+)", rd("dfs-w153-e6.txt"), re.M).group(1))
e7 = rd("dfs-w153-e7.txt")
ok_words = num(re.search(r"words in the lines \(read in `ok` under E7\): total (\d+)", e7).group(1))
det7 = num(re.search(r"words in the Determination \(read today, saved under E7\): total (\d+)", e7).group(1))
words = det_words + hyp_words
rulings_all, rulings_nopage = int(rulings[0]), int(rulings[1])

# the raises the current tool can still raise. The review-count and ten-session checkpoints were removed
# (2e0429b8, 20 Oct 2026; e02dfe4e, 15 Oct), the scope-drift checkpoint was not; docs/dfs-w153-e4.tsv has the 36
# loop checkpoints by kind, and every one was answered. The 26 tooling raises are counted as still possible: they
# are the raises of the tool as it was in Sep and Oct, and which of them the current tool still raises is not known.
kinds = [l.split("\t")[4] for l in rd("dfs-w153-e4.tsv").splitlines()[1:] if re.match(r"\d+\t", l)]
removed = sum(k in ("REVIEW", "SESSIONS") for k in kinds)
assert len(kinds) == 36 and removed == 35, (len(kinds), removed)
raises_now = raises - removed
tot = [l for l in mine.splitlines() if l.startswith("TOTAL:")][0]
answers, corrections = (int(re.search(rf"'{k}': (\d+)", tot).group(1)) for k in ("answer", "correct"))
interv, interv_now = answers + corrections, (answers - removed) + corrections  # author answers + corrections at nodes
print(f"the author faces, as at d595c44a over 238 tasks: {raises} raises answered, of which {removed} were loop checkpoints "
      f"(21 review-count + 14 ten-session)\nwhose code no longer exists, so {raises_now} raises the current tool can still raise; "
      f"{interv} author interventions (answers {answers} + corrections {corrections}), {interv_now} now;\n"
      f"{words} words read ({det_words} in Determinations, {hyp_words} in Hypothesis text; Summaries not counted).\n")

MS = (0.10, 0.15, 0.20)
RULE = lambda sh, m: "gain->adopt" if sh >= m else "inconclusive"   # saving; quality 'not measured' counts as not worse
RULEC = lambda sh, m: "worse->inconclusive" if sh >= m else "inconclusive"  # a cost
print(f"{'row (saving k over a denominator)':<34}{'k':>7}  {'share of d595c44a':>18}  {'share of what is still possible':>32}")
rows = [  # name, k, then, now, is_cost
    ("E5 A: fix needed to finish", 17, raises, raises_now, False),
    ("E5 B: commit refused", 8, raises, raises_now, False),
    ("E5 A, no new permission", 8, raises, raises_now, False),
    ("E4: raises removed", 0, raises, raises_now, False),
]
for name, k, then, now, cost in rows:
    print(f"{name:<34}{k:>7}  {k:>5}/{then:<4}{k/then:>7.1%}  {k:>12}/{now:<4}{k/now:>7.1%}")
    for label, den in (("124-based", then), ("still-possible", now)):
        print(f"    rule on {label:<15}" + "  ".join(f"at {m:.0%}: {RULE(k/den, m):<13}" for m in MS))
print()
for name, k, den, cost in (
        ("E1 `assumed` words", int(wrongif_saved), words, False), ("E6 words, budget 80", cut80, words, False),
        ("E6 words, budget 60", cut60, words, False), ("E7 words read in `ok` (a cost)", ok_words - det7, words, True)):
    print(f"{name:<34}{k:>7}  {k:>5}/{den:<7}{k/den:>7.1%}   (words are not changed by the removed checkpoints: one denominator)")
    print(f"    rule" + " " * 20 + "  ".join(f"at {m:.0%}: {(RULEC if cost else RULE)(k/den, m):<19}" for m in MS))
print(f"    caps: E1 words {int(wrongif_all)/words:.1%} (all Wrong-if words), E7 {ok_words/words:.1%}; E6 has none (any word may go)")
print()
print(f"E1 pages: {rulings[0]} rulings (author corrections on a hypothesis) in the history, {int(rulings[0]) - int(rulings[1])} on a node with a page: "
      f"{(int(rulings[0]) - int(rulings[1]))/interv:.1%} of {interv} author interventions, {(int(rulings[0]) - int(rulings[1]))/interv_now:.1%} of {interv_now} now.")
print("    Not a saving: the author still rules without a page, and a page opened with no ruling leaves no trace, so this is a floor on pages used")
print("    and the files cannot give a share of author time; the cell is 'not measurable from the files', not 'inside'.")
print(f"E1 pages not made: {pages[2]} of {pages[1]} hypotheses.\n")

# E8 power on 35 targets (the pool after the two unmerged trees): rerun of dfs-w153-e8-test.py's power()
s = importlib.util.spec_from_file_location("e8test", D / "dfs-w153-e8-test.py")
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
print()
for n in (32, 35):
    print(f"E8 power on {n} targets: " + "; ".join(f"{pi:.0%} v {pb:.0%}: {m.power(n, pi, pb):.0%}" for pi, pb in ((.6, .3), (.5, .25), (.8, .4))))
for pi, pb in ((.6, .3), (.5, .25)):
    print(f"E8 targets for 80% power at {pi:.0%} v {pb:.0%}: {next(k for k in range(5, 400) if m.power(k, pi, pb) >= .8)}")
