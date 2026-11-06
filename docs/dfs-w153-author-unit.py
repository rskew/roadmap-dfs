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

M = 0.15
print(f"the author faces, as at d595c44a over 238 tasks: {raises} raises; {words} words read "
      f"({det_words} in Determinations, {hyp_words} in Hypothesis text; Summaries not counted); margin {M:.0%}\n")
print(f"{'row':<28}{'saving (-) or cost (+)':<44}{'share':<9}{'ceiling':<10}{'cell'}")


def row(name, what, k, total, ceiling):
    share = k / total
    cell = "inside (observed)" if abs(share) < M and abs(ceiling) >= M else \
           "inside (by construction)" if abs(ceiling) < M else "over (observed)"
    print(f"{name:<28}{what:<44}{share:+.1%}".ljust(81) + f"{ceiling:<10.1%}{cell}")


row("E5 raises, A", "17 of 124 raises removed", -17, raises, 26 / raises)
row("E5 raises, B", "8 of 124 raises removed", -8, raises, 26 / raises)
row("E5 A, no new permission", "8 of 124 (the 9 that need one stay)", -8, raises, 26 / raises)
row("E4 raises", "0 of 124 removed; all 36 stay", 0, raises, 0 / raises)
row("E1 pages (answers)", f"at most {rulings_all - rulings_nopage} of {rulings_all} rulings had a page", -(rulings_all - rulings_nopage), raises, rulings_all / raises)
row("E1 `assumed` words", f"{wrongif_saved} of {wrongif_all} Wrong-if words", -int(wrongif_saved), words, int(wrongif_all) / words)
row("E6 words, budget 80", f"{cut80} words cut", -cut80, words, 1.0)  # ceiling: every word
row("E6 words, budget 60", f"{cut60} words cut", -cut60, words, 1.0)
row("E7 words", f"{ok_words - det7} more words read in `ok`", ok_words - det7, words, ok_words / words)
print(f"\nE1 pages not made: {pages[2]} of {pages[1]} hypotheses (the files do not show a page opened)")

# E8 power on 35 targets (the pool after the two unmerged trees): rerun of dfs-w153-e8-test.py's power()
s = importlib.util.spec_from_file_location("e8test", D / "dfs-w153-e8-test.py")
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
print()
for n in (32, 35):
    print(f"E8 power on {n} targets: " + "; ".join(f"{pi:.0%} v {pb:.0%}: {m.power(n, pi, pb):.0%}" for pi, pb in ((.6, .3), (.5, .25), (.8, .4))))
for pi, pb in ((.6, .3), (.5, .25)):
    print(f"E8 targets for 80% power at {pi:.0%} v {pb:.0%}: {next(k for k in range(5, 400) if m.power(k, pi, pb) >= .8)}")
