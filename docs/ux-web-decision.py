#!/usr/bin/env python3
"""Count the taps and screens the web page asks to decide a task with many assumptions (W131.39).

    FONTCONFIG_FILE=<conf naming a font dir> python3 docs/ux-web-decision.py SITE_DIR OUT_DIR [SCRIPTS_DIR]

SITE_DIR is a git repo with `.dfs/` (a copy of this repository's tasks); it is served with
`SCRIPTS_DIR/web.py --no-walk`. For each task in TASKS, at a phone (390x844) and a desktop (1280x800)
width, it drives the real page and records:

 - the task page: whether each assumption's closed row shows its "Wrong if", whether the notes appear;
 - the node sheet reached by the "Flagged" button (one tap per assumption, starting from the task page):
   whether it shows "Wrong if" and Evidence, its words, and how many screens it scrolls;
 - the Accept dialog: whether it shows "Wrong if", Evidence and the notes, its words and screens;
 - whether an open raise removes the Accept button (a copy of the task with one raise appended).

Output: `decision.json` in OUT_DIR and a short table on stdout.
"""
import json, os, shutil, socket, subprocess, sys, time, urllib.request

site, out = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
scripts = os.path.abspath(sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
os.makedirs(out, exist_ok=True)
TASKS = os.environ.get("TASKS", "W75 W130 W97").split()

from playwright.sync_api import sync_playwright


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def words(t):
    return len(t.split())


def serve():
    port = free_port()
    server = subprocess.Popen([sys.executable, os.path.join(scripts, "web.py"), "--port", str(port), "--no-walk"],
                              cwd=site, stdout=subprocess.DEVNULL, stderr=open(os.path.join(out, "server.err"), "w"))
    base = "http://127.0.0.1:%d" % port
    for _ in range(80):
        try:
            urllib.request.urlopen(base + "/api/state", timeout=1).read()
            return server, base
        except Exception:
            time.sleep(0.25)
    server.terminate()
    sys.exit("web.py did not start")


SHEET = """() => {
  const b = document.querySelector('#sheet .sheet-body'); if (!b) return null;
  const heads = [...b.querySelectorAll('h4')].map(h => h.textContent.trim());
  const text = b.innerText;
  // words under each label (the h4s), so the part a decision needs can be told from the rest
  const under = {}; let cur = '';
  for (const c of b.children) { if (c.tagName === 'H4') { cur = c.textContent.trim(); continue; } under[cur] = (under[cur] || 0) + c.innerText.split(/\\s+/).filter(Boolean).length; }
  return { under, id: (b.querySelector('h3') || {}).textContent, heads, text, words: text.split(/\\s+/).filter(Boolean).length,
           screens: +(b.scrollHeight / b.clientHeight).toFixed(2), clientH: b.clientHeight };
}"""
DIALOG = """() => {
  const d = document.querySelector('#form'); if (!d || !d.open) return null;
  const q = d.querySelector('.quote'), text = d.innerText;
  return { text, words: text.split(/\\s+/).filter(Boolean).length, scrollH: d.scrollHeight, clientH: d.clientHeight,
           quoteScrollH: q ? q.scrollHeight : 0, quoteClientH: q ? q.clientHeight : 0, items: d.querySelectorAll('li').length,
           items_text: [...d.querySelectorAll('li')].map(l => l.innerText).join(' '), vh: innerHeight };
}"""


def drive(pg, base, task, size, info):
    res = {"task": task, "size": size, "assumed": [a["id"] for a in info["assumed"]], "notes": len(info["notes"])}
    pg.goto(base + "/#/t/" + task)
    pg.reload()
    pg.wait_for_selector("[data-act=node]", timeout=30000)
    time.sleep(1.0)
    page = pg.evaluate("() => ({ text: document.body.innerText, h: document.documentElement.scrollHeight, vh: innerHeight })")
    res["task_page_screens"] = round(page["h"] / page["vh"], 1)
    res["notes_on_task_page"] = sum(1 for n in info["notes"] if n["heading"] and n["heading"] in page["text"])
    rows = pg.evaluate("""() => [...document.querySelectorAll('[data-act=node]')].filter(e => e.querySelector('.tag.assumption'))
        .map(e => ({ id: e.dataset.id, text: e.innerText }))""")
    res["flagged_rows"] = len(rows)
    res["closed_rows_with_wrong_if"] = sum(1 for r in rows if "wrong if" in r["text"].lower())
    res["closed_rows_with_evidence"] = sum(1 for r in rows if "evidence" in r["text"].lower())

    # Walk the assumptions with the "Flagged" button: one tap each.
    seen, sheets, taps = [], [], 0
    btn = "[data-act=nextflag]:visible"
    pg.locator(btn).first.click(); taps += 1
    for _ in range(len(info["assumed"]) + 2):
        try:
            pg.wait_for_selector("#sheet .sheet-body h3", timeout=8000)
        except Exception:
            break
        s = pg.evaluate(SHEET)
        if not s:
            break
        sid = s["id"].split()[0]
        if sid in seen:
            break
        seen.append(sid)
        low = s["text"].lower()
        sheets.append({"under": s["under"], "id": sid, "words": s["words"], "screens": s["screens"], "heads": s["heads"],
                       "wrong_if": "wrong if" in low, "evidence": "evidence" in low or "\nfor:" in low or "against:" in low})
        before = s["id"]
        if len(seen) == len(info["assumed"]):
            break
        pg.locator("#sheet [data-act=nextflag]").click(); taps += 1
        try:
            pg.wait_for_function("b => { const h = document.querySelector('#sheet .sheet-body h3'); return h && h.textContent !== b; }", arg=before, timeout=20000)
        except Exception:
            pass
    res["flag_taps"] = taps
    res["sheets"] = sheets
    res["reached_all_assumed"] = sorted(s for s in seen if s in res["assumed"]) == sorted(res["assumed"])
    res["decision_words"] = sum(w for s in sheets for k, w in s["under"].items() if k.startswith(("Hypothesis", "Wrong if", "Evidence")))
    res["sheet_words_total"] = sum(s["words"] for s in sheets)
    res["sheet_screens_total"] = round(sum(s["screens"] for s in sheets), 1)

    # The Accept dialog.
    pg.goto(base + "/#/t/" + task)
    pg.wait_for_selector("[data-act=node]", timeout=30000)
    time.sleep(0.8)
    acc = pg.locator("[data-act=accept]:visible")
    res["accept_button"] = acc.count()
    if acc.count():
        acc.first.click()
        time.sleep(0.6)
        d = pg.evaluate(DIALOG)
        if d:
            low = d["text"].lower()
            res["dialog"] = {"words": d["words"], "items": d["items"], "wrong_if_items": low.count("wrong if"),
                             "evidence": any(k in d["items_text"].lower() for k in ("evidence", "for:", "against:")),
                             "notes_shown": sum(1 for n in info["notes"] if n["heading"] and n["heading"] in d["text"]),
                                                         "note_box_px": d["quoteClientH"], "note_box_text_px": d["quoteScrollH"], "viewport_px": d["vh"]}
        pg.keyboard.press("Escape")
    return res


def main():
    server, base = serve()
    try:
        info = {t: json.load(urllib.request.urlopen("%s/api/task/%s" % (base, t))) for t in TASKS}
    except Exception:
        info = {}
    allres = []
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=shutil.which("chromium"), args=["--no-sandbox"])
            for size, vp in (("phone", {"width": 390, "height": 844}), ("desk", {"width": 1280, "height": 800})):
                ctx = b.new_context(viewport=vp, device_scale_factor=1, has_touch=size == "phone", is_mobile=size == "phone")
                pg = ctx.new_page()
                for t in TASKS:
                    if t not in info:
                        continue
                    allres.append(drive(pg, base, t, size, info[t]))
                ctx.close()
            b.close()
    finally:
        server.terminate()
        json.dump(allres, open(os.path.join(out, "decision.json"), "w"), indent=1)
    for r in allres:
        d = r.get("dialog", {})
        print("%-4s %-5s assumed %d | closed rows with Wrong if %d, evidence %d | Flagged taps %d reach all: %s | sheets %d words %.1f screens"
              " | dialog: %d items, %d words, evidence %s, notes %d of %d (note box %s of %s px) | decision words (Hypothesis, Wrong if, Evidence) %d | notes on task page %d"
              % (r["task"], r["size"], len(r["assumed"]), r["closed_rows_with_wrong_if"], r["closed_rows_with_evidence"], r["flag_taps"],
                 r["reached_all_assumed"], r["sheet_words_total"], r["sheet_screens_total"], d.get("items", 0), d.get("words", 0),
                 d.get("evidence"), d.get("notes_shown", 0), r["notes"], d.get("note_box_px"), d.get("note_box_text_px"), r["decision_words"], r["notes_on_task_page"]))


main()
