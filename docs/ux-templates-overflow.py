#!/usr/bin/env python3
"""Draw every artefact page with and without `overflow-wrap: anywhere` on its body (W131.48).

    FONTCONFIG_FILE=<conf naming a font dir> [WRAP=break-word] python3 docs/ux-templates-overflow.py OUT_DIR [ARTEFACT_DIR]

Both artefact templates now carry `overflow-wrap: anywhere` on `body`. Unlike `break-word`, `anywhere` also lowers the
min-content width of every box, flex item and table cell to one character, so a row that used to widen or wrap
between words may now break an ordinary word in the middle. This script asks that of the 170-odd pages already in
ARTEFACT_DIR (default: this repository's `.dfs/artefacts`), which stand in for "every page copied from the templates":

  for each page and each width (320, 390, 1280 px), load it as it is, load it again with the one rule added to its body,
  and compare, token by token (a token is a run of non-space characters in a visible text node):

    broke    tokens of at most 25 characters that are drawn on two or more lines with the rule and on one without it
             (an ordinary word split in the middle: what the rule must not do)
    healed   the other way round (a long word the rule now wraps)
    sideways the page is wider than the screen (documentElement.scrollWidth > width), without and with
    height   the page's height without and with
    narrower flex, grid and table-cell boxes at least 25 px narrower with the rule, and how many tokens in them broke

and writes OUT_DIR/overflow.tsv (one row per page and width), OUT_DIR/overflow.json (the detail) and prints totals.
Pages load from a file, so the answer form says "opened as a file"; nothing else about a page changes.
Needs playwright and chromium (see docs/ux-web-playwright.py).
"""
import json, os, shutil, sys

here = os.path.dirname(os.path.abspath(__file__))
out = os.path.abspath(sys.argv[1])
art = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, "..", ".dfs", "artefacts"))
os.makedirs(out, exist_ok=True)

from playwright.sync_api import sync_playwright

VALUE = os.environ.get("WRAP", "anywhere")   # WRAP=break-word draws the alternative
RULE = "body { overflow-wrap: %s; }" % VALUE

MEASURE = r"""
() => {
  const vis = n => { const r = n.parentElement.getBoundingClientRect(); const s = getComputedStyle(n.parentElement);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && !n.parentElement.closest('script,style,template,[hidden],svg'); };
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const lines = [], broken = []; let i = 0, n;
  while ((n = w.nextNode())) {
    if (!vis(n)) { continue; }
    const re = /\S+/g; let m;
    while ((m = re.exec(n.nodeValue))) {
      const r = document.createRange(); r.setStart(n, m.index); r.setEnd(n, m.index + m[0].length);
      const tops = [...new Set([...r.getClientRects()].filter(q => q.width > 0).map(q => Math.round(q.top / 4)))];
      lines.push(tops.length);
      if (tops.length > 1) broken.push({ i: lines.length - 1, t: m[0].slice(0, 40), len: m[0].length,
        box: n.parentElement.closest('.box, td, th, li, label, button, p, h1, h2, h3, div').tagName.toLowerCase() });
    }
  }
  const boxes = [...document.querySelectorAll('body *')].filter(el => {
    const p = el.parentElement, d = getComputedStyle(p).display, s = getComputedStyle(el);
    return (/flex|grid/.test(d) || /^t[dh]$/i.test(el.tagName)) && el.getBoundingClientRect().width > 0 && s.display !== 'none' && !el.closest('script,style,svg'); })
    .map(el => Math.round(el.getBoundingClientRect().width));
  return { sw: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight, tokens: lines.length, broken, boxes };
}
"""


def main():
    pages = sorted(f for f in os.listdir(art) if f.endswith(".html"))
    rows, detail = [], {}
    tot = dict(pages=len(pages), cells=0, broke_cells=0, broke_tokens=0, healed_tokens=0, new_sideways=0, fixed_sideways=0)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=shutil.which("chromium") or "/host-nix-bin/chromium", args=["--no-sandbox"])
        for width in (320, 390, 1280):
            ctx = b.new_context(viewport={"width": width, "height": 800}, device_scale_factor=1)
            for name in pages:
                res = []
                for with_rule in (False, True):
                    pg = ctx.new_page()
                    try:
                        pg.goto("file://" + os.path.join(art, name), wait_until="load", timeout=15000)
                        if with_rule:
                            pg.add_style_tag(content=RULE)
                        pg.wait_for_timeout(250)
                        res.append(pg.evaluate(MEASURE))
                    except Exception as e:
                        res.append({"error": str(e)[:100]})
                    finally:
                        pg.close()
                if "error" in res[0] or "error" in res[1]:
                    rows.append((name[:8], width, "error", "", "", "", "", "", ""))
                    continue
                a, c = res
                a_br = {x["i"] for x in a["broken"] if x["len"] <= 25}
                c_br = {x["i"] for x in c["broken"] if x["len"] <= 25}
                comparable = a["tokens"] == c["tokens"]
                broke = sorted(c_br - a_br) if comparable else []
                healed = sorted(a_br - c_br) if comparable else []
                narrower = sum(1 for x, y in zip(a["boxes"], c["boxes"]) if x - y >= 25) if len(a["boxes"]) == len(c["boxes"]) else -1
                side0, side1 = a["sw"] > width, c["sw"] > width
                rows.append((name[:8], width, len(broke), len(healed), "yes" if side0 else "", "yes" if side1 else "", a["h"], c["h"], narrower))
                tot["cells"] += 1
                tot["broke_cells"] += bool(broke)
                tot["broke_tokens"] += len(broke)
                tot["healed_tokens"] += len(healed)
                tot["new_sideways"] += (not side0 and side1)
                tot["fixed_sideways"] += (side0 and not side1)
                if broke or healed or side0 != side1:
                    detail["%s@%d" % (name[:8], width)] = {
                        "broke": [x for x in c["broken"] if x["i"] in set(broke)][:12], "healed": [x for x in a["broken"] if x["i"] in set(healed)][:6],
                        "sideways": [a["sw"], c["sw"]], "tokens": [a["tokens"], c["tokens"]], "comparable": comparable}
            ctx.close()
        b.close()
    with open(os.path.join(out, "overflow.tsv"), "w") as f:
        f.write("page\twidth\tbroke\thealed\tsideways_without\tsideways_with\theight_without\theight_with\tnarrower_boxes\n")
        for r in rows:
            f.write("\t".join(str(x) for x in r) + "\n")
    json.dump(detail, open(os.path.join(out, "overflow.json"), "w"), indent=1)
    print(json.dumps(tot))


main()
