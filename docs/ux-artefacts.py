#!/usr/bin/env python3
"""Artefact pages: what each one is made of, read from the HTML alone (W131.10).

    python3 docs/ux-artefacts.py          (run from the repository root)
    python3 docs/ux-artefacts.py --tsv    (one row per page; docs/ux-artefacts.tsv)

No browser draws text in this sandbox, so `scripts/artefact_check.sh` cannot judge a page.
This reads every `.dfs/artefacts/*.html` and says, per page: who names it (a Hypothesis, a
raise's `Answer with:` line, or only a Summary/Evidence/other mention), whether it has the
answer form and can post, how it draws (svg/canvas, boxes with `data-node`, a table, bars, or
only text), whether the visible text carries the choice and `Wrong if`, and the mechanical
faults `check.py` and `artefact_check.sh` look for (no viewport, placeholder options, a
fetched URL, a fixed width).

Task files are read through `tree.load`, so text moved to the archive is counted.
"""
import glob, os, re, sys, statistics as st
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import tree

REF = re.compile(r"\.dfs/artefacts/([0-9a-f-]{36}\.html)")
pages = {os.path.basename(p): p for p in sorted(glob.glob(".dfs/artefacts/*.html"))}

# who names each page: {page: set of (task, kind)}
named = {p: set() for p in pages}
for f in sorted(glob.glob(".dfs/items/W*.md")):
    task = os.path.basename(f)[:-3]
    t = tree.load(task)
    for nd in t["nodes"]:
        for field, kind in (("Hypothesis", "hypothesis"), ("Approach", "approach"),
                            ("Determination", "determination"), ("Evidence", "evidence")):
            for m in REF.findall(nd["fields"].get(field) or ""):
                if m in named:
                    named[m].add((task, kind))
    for e in t.get("log", []):
        text = e.get("text", "") if isinstance(e, dict) else str(e)
        kind = e.get("kind", "log") if isinstance(e, dict) else "log"
        ans = re.findall(r"^\s*Answer with:\s*\.dfs/artefacts/([0-9a-f-]{36}\.html)", text, re.M)
        for m in REF.findall(text):
            if m in named:
                named[m].add((task, "raise-answer" if m in ans else "log-" + kind))
    summ = t.get("summary") or ""
    for m in REF.findall(summ if isinstance(summ, str) else ""):
        if m in named:
            named[m].add((task, "summary"))


def visible(html):
    h = re.sub(r"(?s)<!--.*?-->", " ", html)      # comments first: the template's names <script>
    h = re.sub(r"(?is)<(script|style).*?</\1>", " ", h)
    h = re.sub(r"<[^>]+>", " ", h)
    return " ".join(h.split())


def facts(name):
    html = open(pages[name], encoding="utf-8").read()
    vis = visible(html)
    low = html.lower()
    f = {"page": name}
    f["viewport"] = "width=device-width" in low
    f["form"] = "dfs-" in html and "/artefact-state/" in html
    f["controls"] = len(re.findall(r"<(input|button|select|textarea|details)\b", low))
    f["handler"] = ("addeventlistener" in low) or bool(re.search(r"\sonclick\s*=", low))
    f["posts_answer"] = bool(re.search(r"answer", low)) and "/artefact-state/" in html
    f["svg"] = "<svg" in low or "<canvas" in low
    f["boxes"] = ("data-node" in low or 'class="dg"' in low or "class='dg'" in low
                  or bool(re.search(r"class=[\"'](box|node|card|stage)\b", low)))
    f["table"] = "<table" in low
    f["bars"] = (bool(re.search(r"style=\"[^\"]*width:\s*(\d+(\.\d+)?%|'\s*\+)", low))
                 or "--w:" in low or "style.width" in low)
    f["draws"] = f["svg"] or f["boxes"] or f["bars"]
    # the falsifier on the page: the words `Wrong if`, or the stock heading "What would show it wrong",
    # in the markup or in the strings its script draws
    f["wrongif_on_page"] = bool(re.search(r"wrong if|what would show[^<]{0,30}wrong|would show[^.<]{0,30}wrong", low))
    f["words"] = len(vis.split())
    # words outside the shared answer form: the page the author reads before it
    pre = re.sub(r"(?is)<section[^>]*dfs-answer.*?</section>", " ", html)
    pre = re.sub(r"(?is)<div[^>]*id=\"(answerbox|rulebox|parkbox)\".*?</div>", " ", pre)
    pre_vis = visible(pre)
    for stock in ("The decision, in a sentence", "Your page goes here"):
        pre_vis = pre_vis.replace(stock, " ")
    f["content_words"] = len(pre_vis.split())
    f["residue"] = "your page goes here" in low or "the decision, in a sentence" in low
    f["posts"] = "/artefact-state/" in html
    f["placeholder"] = "take the first option" in low
    f["fetches"] = bool(re.search(r"(src|href)=\"https?://", low)) or "fetch(\"http" in low
    f["fixed_width"] = bool(re.search(r"[^-]width:\s*\d{4,}px", low))
    f["named_by"] = sorted(named[name])
    kinds = {k for _, k in named[name]}
    f["role"] = ("hypothesis" if "hypothesis" in kinds else
                 "raise-answer" if "raise-answer" in kinds else
                 "other-named" if kinds else "unnamed")
    f["task_no"] = min((int(t[1:]) for t, _ in named[name]), default=-1)
    return f


DOM = next((a[6:] for a in sys.argv if a.startswith("--dom=")), "/tmp/ux-dom")


def dom_facts(name, f):
    """After the page's script has run: `chromium --headless --dump-dom` of each page into DOM/<name>
    (what a browser builds is text and elements even where the sandbox draws no glyphs). Without the
    dumps these fields are None."""
    path = os.path.join(DOM, name)
    if not os.path.exists(path):
        f["dom_words"] = f["dom_draws"] = f["dom_empty"] = f["dom_flow"] = None
        return
    d = open(path, encoding="utf-8").read()
    d = re.sub(r"(?is)<section[^>]*dfs-answer.*?</section>", " ", d)
    d = re.sub(r"(?is)<div[^>]*id=\"(answerbox|rulebox|parkbox)\".*?</div>", " ", d)
    low = d.lower()
    f["dom_words"] = len(visible(d).split())
    f["dom_draws"] = (low.count("<svg") + low.count("<canvas") + low.count("data-node") + low.count("<table")
                      + len(re.findall(r"class=\"[^\"]*\b(box|dg|bar|node|card|cell)\b", low))
                      + len(re.findall(r"style=\"[^\"]*width:\s*\d+(\.\d+)?%", low)))
    f["dom_flow"] = bool(re.search(r"class=\"[^\"]*\blink\b|&darr;|↓|→|<marker|<path|<line", d)) and f["dom_draws"] > 0
    f["dom_empty"] = f["posts"] and f["dom_words"] < 30 and f["dom_draws"] == 0


rows = [facts(n) for n in pages]
for r in rows:
    dom_facts(r["page"], r)


def pct(a, b):
    return f"{a}/{b}" + (f" ({100 * a / b:.0f}%)" if b else "")


if "--tsv" in sys.argv:
    cols = ["page", "role", "task_no", "form", "controls", "handler", "draws", "svg", "boxes", "table",
            "bars", "wrongif_on_page", "words", "content_words", "posts", "dom_words", "dom_draws", "dom_flow", "dom_empty", "residue", "viewport", "placeholder", "fetches", "fixed_width"]
    print("\t".join(cols))
    for r in rows:
        print("\t".join(str(int(r[c])) if isinstance(r[c], bool) else str(r[c]) for c in cols))
    sys.exit()

print(f"pages {len(rows)} (every .dfs/artefacts/*.html; the two templates are not in this folder)")
for role in ("hypothesis", "raise-answer", "other-named", "unnamed"):
    print(f"  named as {role}: {sum(r['role'] == role for r in rows)}")
print()
bands = (("W0-W59", 0, 60), ("W60-W99", 60, 100), ("W100+", 100, 10 ** 6), ("no task", -1, 0))
print(f"{'':10}{'n':>4}{'form':>8}{'handler':>9}{'draws':>8}{'svg/cv':>8}{'boxes':>7}{'bars':>6}{'table':>7}"
      f"{'WrongIf':>9}{'noview':>8}{'words~':>8}")
for label, lo, hi in bands + (("all", -10, 10 ** 6),):
    r = [x for x in rows if lo <= x["task_no"] < hi]
    if not r:
        continue
    print(f"{label:10}{len(r):>4}{sum(x['form'] for x in r):>8}{sum(x['handler'] for x in r):>9}"
          f"{sum(x['draws'] for x in r):>8}{sum(x['svg'] for x in r):>8}{sum(x['boxes'] for x in r):>7}"
          f"{sum(x['bars'] for x in r):>6}{sum(x['table'] for x in r):>7}"
          f"{sum(x['wrongif_on_page'] for x in r):>9}{sum(not x['viewport'] for x in r):>8}"
          f"{st.median(x['words'] for x in r):>8.0f}")
print()
for role in ("hypothesis", "raise-answer", "other-named", "unnamed"):
    r = [x for x in rows if x["role"] == role]
    if not r:
        continue
    print(f"{role}: n={len(r)} form {pct(sum(x['form'] for x in r), len(r))}; "
          f"draws {pct(sum(x['draws'] for x in r), len(r))}; "
          f"text only (no svg/boxes/bars) {pct(sum(not x['draws'] for x in r), len(r))}; "
          f"Wrong if on page {pct(sum(x['wrongif_on_page'] for x in r), len(r))}")
hyp = [x for x in rows if x["role"] == "hypothesis"]
print()
print(f"hypothesis pages with no `Wrong if` text: {sum(not x['wrongif_on_page'] for x in hyp)}; "
      f"with no controls beyond the form: {sum(x['controls'] < 6 for x in hyp)}")
noform = [x for x in rows if not x["form"]]
print(f"pages without the answer form {len(noform)}: named as "
      + ", ".join(f"{k} {sum(x['role'] == k for x in noform)}" for k in ("hypothesis", "raise-answer", "other-named", "unnamed")))
print(f"posts to /artefact-state/ {sum(x['posts'] for x in rows)}; of those with the shared form "
      f"{sum(x['form'] for x in rows)}, with their own earlier form {sum(x['posts'] and not x['form'] for x in rows)}")
hp = [x for x in rows if x["role"] == "hypothesis" and not x["posts"]]
print(f"Hypothesis pages that cannot post a ruling (no /artefact-state/): {len(hp)}, tasks "
      + ", ".join("W%d" % x["task_no"] for x in hp))
print(f"template residue ('Your page goes here' or the stock heading): {sum(x['residue'] for x in rows)}")
if rows[0]["dom_words"] is None:
    print(f"(no browser dumps in {DOM}: page text read from the HTML, not from what the script builds)")
else:
    em = [x for x in rows if x["dom_empty"]]
    pre = [x for x in rows if not x["content_words"] >= 30 and x["posts"]]
    print(f"in the browser's DOM, after the script ran: median words outside the form "
          f"{st.median(x['dom_words'] for x in rows):.0f}; pages that post but have under 30 words "
          f"outside the form and no box, svg, bar or table: {len(em)} "
          f"(from the HTML alone {len(pre)}: script-drawn pages are missed there)")
    for x in em:
        print("   empty:", x["page"], x["role"], "W%d" % x["task_no"], "dom words", x["dom_words"])
    print()
    print("Hypothesis pages by era, from the DOM: (a) no box/svg/bar/table at all, (b) boxes or bars but no "
          "arrow or line between them, (c) boxes with arrows or an svg; and the count with no falsifier text on the page")
    for label, lo, hi in bands[:3]:
        h = [x for x in rows if x["role"] == "hypothesis" and lo <= x["task_no"] < hi]
        a = sum(x["dom_draws"] == 0 for x in h)
        b = sum(x["dom_draws"] > 0 and not x["dom_flow"] for x in h)
        c = sum(bool(x["dom_flow"]) for x in h)
        print(f"  {label:8} n={len(h):3}  (a) {a:3}  (b) {b:3}  (c) {c:3}  no falsifier text {sum(not x['wrongif_on_page'] for x in h):3}")
    nodr = [x for x in rows if x["dom_draws"] == 0]
    print(f"DOM has no box, svg, bar, table or canvas: {len(nodr)} pages ({sum(x['role'] == 'hypothesis' for x in nodr)} named by a Hypothesis); "
          f"HTML-only 'draws' false: {sum(not x['draws'] for x in rows)}")
print("mechanical faults: placeholder options %d; no viewport %d; fetched URL %d; fixed width >=1000px %d"
      % (sum(x["placeholder"] for x in rows), sum(not x["viewport"] for x in rows),
         sum(x["fetches"] for x in rows), sum(x["fixed_width"] for x in rows)))
