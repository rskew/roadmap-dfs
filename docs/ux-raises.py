#!/usr/bin/env python3
"""Raises and the author's answers, counted from the task logs (W131.1).

    python3 docs/ux-raises.py [ITEMS_DIR]

For every `raise` log entry: its length, whether it names an answer page, the author's
`answer` (matched by the raise's timestamp), the answer's length and the hours between them.
Also the same for `accept` and `correct`. Includes the archive's moved log entries. Reads only; edits nothing.
"""
import glob, os, re, statistics as st, sys
from datetime import datetime

d = sys.argv[1] if len(sys.argv) > 1 else ".dfs/items"
ent = re.compile(r"^- (\d{4}-\d\d-\d\dT[\d:]+Z) · (\w+)(?: · ([^\n]*))?$")
ts = lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
raises, answers, corrects = {}, {}, []
arch = os.path.join(d, "..", "archive", "items")
files = sorted(glob.glob(os.path.join(d, "W*.md")) + glob.glob(os.path.join(arch, "W*.md")))
for f in files:
    t = os.path.basename(f)[:-3]
    cur = None
    text = open(f, encoding="utf-8").read()
    # the live file's log follows `## Log`; the archive keeps older entries under `**Log**:`
    text = text.split("## Log")[-1] if "## Log" in text else text
    for line in text.split("\n"):
        m = ent.match(line)
        if m:
            cur = [m[1], m[2], m[3] or "", []]
            if m[2] == "raise": raises[(t, m[1])] = cur
            elif m[2] == "answer": answers[(t, (m[3] or "").split(" ")[0])] = cur
            elif m[2] == "correct": corrects.append((t, cur))
        elif cur is not None and line.startswith("  "):
            cur[3].append(line.strip())
        else:
            cur = None
rows = []
for (t, r), e in raises.items():
    body = " ".join(e[3]); a = answers.get((t, r))
    abody = " ".join(a[3]) if a else None
    hrs = (ts(a[0]) - ts(r)).total_seconds() / 3600 if a else None
    rows.append(dict(at=r, t=t, node=e[2], rl=len(body.split()), page="artefacts/" in body,
                     al=len(abody.split()) if abody is not None else None, hrs=hrs, ans=abody))
med = lambda xs: st.median(xs) if xs else float("nan")
print(f"raises {len(rows)}; answered {sum(r['al'] is not None for r in rows)}; "
      f"with an answer page {sum(r['page'] for r in rows)}")
print(f"raise words: median {med([r['rl'] for r in rows]):.0f}, max {max(r['rl'] for r in rows)}")
ans = [r for r in rows if r['al'] is not None]
print(f"answer words: median {med([r['al'] for r in ans]):.0f}; one-word answers {sum(r['al'] <= 2 for r in ans)}; "
      f">20 words {sum(r['al'] > 20 for r in ans)}")
print(f"hours to answer: median {med([r['hrs'] for r in ans]):.1f}, p90 {sorted(r['hrs'] for r in ans)[int(.9*len(ans))]:.1f}")
for lab, sub in (("with page", [r for r in ans if r['page']]), ("without page", [r for r in ans if not r['page']])):
    print(f"  {lab}: n={len(sub)} median answer words {med([r['al'] for r in sub]):.0f}, median hours {med([r['hrs'] for r in sub]):.1f}")
print(f"corrections {len(corrects)}; median words {med([len(' '.join(c[3]).split()) for _, c in corrects]):.0f}")
# a raise with no node is written by the runner (review-count stop, merge blocked by the author's edits);
# one with a node is written by a session
for lab, sub in (("runner", [r for r in rows if not r['node']]), ("session", [r for r in rows if r['node']])):
    a = [r for r in sub if r['al'] is not None]
    print(f"{lab}-written raises: {len(sub)}; median raise words {med([r['rl'] for r in sub]):.0f}; "
          f"median answer words {med([r['al'] for r in a]):.0f}; one-or-two-word answers {sum(r['al'] <= 2 for r in a)}; "
          f"median hours {med([r['hrs'] for r in a]):.1f}; with a page {sum(r['page'] for r in sub)}")
for lab, sub in (("runner", [r for r in rows if not r['node']]), ("session", [r for r in rows if r['node']])):
    print(f"{lab}-written raises dated {min(r['at'] for r in sub)[:10]} to {max(r['at'] for r in sub)[:10]}")
print("\ntask node raise-words page answer-words hours answer")
for r in sorted(rows, key=lambda r: (int(r['t'][1:]), r['hrs'] or 0)):
    print(r['t'], r['node'], r['rl'], int(r['page']), r['al'], f"{r['hrs']:.1f}" if r['hrs'] is not None else "-", (r['ans'] or "UNANSWERED")[:60].replace("\t", " "))
