#!/usr/bin/env python3
"""Do session-written raises lead with the ask, the options, a recommendation and the case against? (W131.11)

    python3 docs/ux-raise-form.py          (run from the repository root)
    python3 docs/ux-raise-form.py --tsv    (one row per raise; docs/ux-raise-form.tsv)

A raise written by a session has a node id after `raise`; the runner's own do not. For each one this
tests, from the text alone: the first sentence is a question or says what is needed from the author;
the options are lettered or numbered; a recommendation is given; the strongest case against it is
given; and, against the author's answer (matched by the raise's timestamp), the answer's length and
whether it is a bare letter or digit. Read through `tree.load`, so archived log entries count.
These are word tests, a first screen only: the TSV is meant to be read beside the raise.
"""
import glob, os, re, statistics as st, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import tree

rows = []
for f in sorted(glob.glob(".dfs/items/W*.md")):
    task = os.path.basename(f)[:-3]
    log = tree.load(task)["log"]
    ans = {e["args"][0]: e for e in log if e["kind"] == "answer" and e["args"]}
    for e in log:
        if e["kind"] != "raise" or not e["args"]:
            continue
        body = e["body"]
        lines = [l for l in body.split("\n") if l.strip()]
        first = re.split(r"(?<=[.?:])\s", " ".join(lines[:2]), maxsplit=1)[0] if lines else ""
        a = ans.get(e["ts"])
        abody = " ".join((a or {}).get("body_lines", [])) if a else None
        rows.append({
            "task": task, "node": e["args"][0], "ts": e["ts"], "words": len(body.split()),
            "first": first[:110],
            "asks_first": bool(re.match(r"(?i)(should|do|does|is|are|can|which|what|how|may|want|choose|pick|decide|please|need|ask|the author)", first.strip())
                               or "?" in first or re.match(r"(?i)(decision|question)\b", first.strip())),
            "lettered": bool(re.search(r"(?m)^\s*(\(?[a-dA-D1-4][).:]|[-*] ?\(?[a-dA-D1-4][).])\s", body)),
            "recommend": bool(re.search(r"(?i)\b(I would|I'd|I recommend|my recommendation|recommend|I suggest|I lean|I take|take \(?[a-d]\)?|default)\b", body)),
            "against": bool(re.search(r"(?i)strongest case|case against|against (it|this|that|taking)|downside|what could go wrong|the risk is|counter", body)),
            "page": "Answer with:" in body,
            "answered": a is not None,
            "answer_words": len(abody.split()) if abody is not None else None,
            "bare": bool(abody is not None and re.fullmatch(r"\W*[A-Za-z0-9]\W*", abody.strip() or "x")),
        })

if "--tsv" in sys.argv:
    cols = ["task", "node", "ts", "words", "asks_first", "lettered", "recommend", "against", "page",
            "answered", "answer_words", "bare", "first"]
    print("\t".join(cols))
    for r in rows:
        print("\t".join(str(int(r[c])) if isinstance(r[c], bool) else str(r[c]) for c in cols))
    sys.exit()

n = len(rows)
p = lambda k, rs=rows: f"{sum(bool(r[k]) for r in rs)}/{len(rs)}"
print(f"session-written raises {n}; words median {st.median(r['words'] for r in rows):.0f}, "
      f"over 200: {sum(r['words'] > 200 for r in rows)}, over 400: {sum(r['words'] > 400 for r in rows)}")
print(f"first sentence asks or states the need {p('asks_first')}; options lettered or numbered {p('lettered')}; "
      f"recommendation {p('recommend')}; case against {p('against')}; names an answer page {p('page')}")
ans = [r for r in rows if r["answered"]]
print(f"answered {len(ans)}; answer words median {st.median(r['answer_words'] for r in ans):.0f}; "
      f"bare letter or digit {sum(r['bare'] for r in ans)}; answers of 3 words or fewer "
      f"{sum(r['answer_words'] <= 3 for r in ans)}")
for lo, hi, lab in ((0, 100, "under 100 words"), (100, 200, "100-199"), (200, 10 ** 6, "200+")):
    g = [r for r in ans if lo <= r["words"] < hi]
    if g:
        print(f"  raise {lab}: n={len(g)}, answers median {st.median(r['answer_words'] for r in g):.0f} words, "
              f"bare {sum(r['bare'] for r in g)}, with a lettered option list {sum(r['lettered'] for r in g)}")
long_bare = [r for r in ans if r["words"] > 200 and r["answer_words"] <= 3]
print(f"raises over 200 words answered in 3 words or fewer: {len(long_bare)}: "
      + ", ".join(f"{r['node']} ({r['words']}w)" for r in long_bare))
miss = [r for r in rows if not r["recommend"]]
print(f"without a recommendation ({len(miss)}): " + ", ".join(r["node"] for r in miss))
miss = [r for r in rows if not r["against"]]
print(f"without a case against ({len(miss)}): " + ", ".join(r["node"] for r in miss))
