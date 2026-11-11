#!/usr/bin/env python3
"""Post a Confirm and a Refute from every artefact page through the real server, and read the task file back (W131.46).

    FONTCONFIG_FILE=<conf naming a font dir> python3 docs/ux-web-rulings.py OUT_DIR [ARTEFACT_DIR] [SCRIPTS_DIR]

W131.10 and W131.38 said "127 pages can post a ruling" from the presence of a form: the page was read, or drawn, and
no button was pressed. This script presses them. It copies ARTEFACT_DIR (default: this repository's `.dfs/artefacts`)
into a throw-away git site served by `SCRIPTS_DIR/web.py --no-work`, and for each `.html` page makes TWO one-node
tasks whose Hypothesis names that page (so the server's `deciding_node` finds the first, and once that is ruled on,
the second). Then in headless chromium it:

  1. opens `/artefacts/<page>` and waits for the page's own rule box to show (the page asks `/artefact-state/` who waits);
  2. chooses the first option and types a note where the page has them, presses Confirm, reads the page's status line;
  3. reads the first task's file: is there a `correct` entry for that node, `confirmed`, with the note in it?
  4. reloads the page (its node is now ruled on, so the rule box must be hidden), opens it again for the second task,
     presses Refute, and reads the second task's file the same way.

A page with no rule buttons of its own (an explainer, a picture, a raise-only page) is listed as `no buttons` and not a
failure. `rulings.tsv` has one row per page; `rulings.json` the detail. The last lines print the counts per form variant.
It exits 1 when a page that shows the rule box fails to record either ruling.
"""
import collections, json, os, re, shutil, socket, subprocess, sys, tempfile, time, urllib.request

here = os.path.dirname(os.path.abspath(__file__))
out = os.path.abspath(sys.argv[1])
art = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, "..", ".dfs", "artefacts"))
scripts = os.path.abspath(sys.argv[3] if len(sys.argv) > 3 else os.path.join(here, "..", "scripts"))
os.makedirs(out, exist_ok=True)

from playwright.sync_api import sync_playwright

TASK = """# W{n} · fixture for {page8}

## Goal

A fixture task: one node that rests on an assumption whose page is {page8}.

## Tree

### W{n}.1 · Assume
Status: open
Approach: Fixture.
Hypothesis: It holds. Wrong if it does not. .dfs/artefacts/{page}

## Log

"""

# What a page's own rule box looks like. The current template uses #dfs-confirm / #dfs-refute, #dfs-rulebox, #dfs-note,
# #dfs-rule-status; the older hand-written pages (W17 to W46 era and the first API pages) use the short ids.
IDS = [("dfs-", "dfs-rulebox", "dfs-confirm", "dfs-refute", "dfs-note", "dfs-rule-status"),
       ("", "rulebox", "confirm", "refute", "note", "rule-status")]


def variant(text):
    """The form a page carries, from its source alone: which API, which button ids, which note box."""
    if "artefact-state" not in text:
        return "none (no server call)"
    for pre, box, yes, no, note, status in IDS:
        if 'id="%s"' % yes in text and 'id="%s"' % no in text:
            return "%s buttons%s" % ("template dfs-" if pre else "own short-id", " + send" if "dfs-send" in text or 'id="answer"' in text else "")
    return "state only (no rule buttons)"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def build_site(pages):
    site = tempfile.mkdtemp(prefix="w131rule")
    dfs = os.path.join(site, ".dfs")
    os.makedirs(os.path.join(dfs, "items"))
    os.makedirs(os.path.join(dfs, "artefacts"))
    shutil.copy(os.path.join(here, "..", ".dfs", "ROADMAP.md"), dfs)
    ids = []
    for k, page in enumerate(pages):
        shutil.copy(os.path.join(art, page), os.path.join(dfs, "artefacts", page))
        for n in (1001 + 2 * k, 1002 + 2 * k):   # four digits each: the server lists tasks in text order, so W10 < W9
            open(os.path.join(dfs, "items", "W%d.md" % n), "w").write(TASK.format(n=n, page=page, page8=page[:8]))
            ids.append("W%d" % n)
    open(os.path.join(dfs, "order.md"), "w").write("# Order\n\nitems\n\n" + "".join("- %s\n" % i for i in ids))
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "fixture"]):
        subprocess.run(["git"] + cmd, cwd=site, check=True, stdout=subprocess.DEVNULL)
    return site


def logged(site, n, verdict, note):
    """The task file's `correct` entry for W<n>.1: (found, mentions the note)."""
    text = open(os.path.join(site, ".dfs", "items", "W%d.md" % n)).read()
    m = re.search(r"· correct · W%d\.1 · %s\n(.*?)(?=^- |\Z)" % (n, verdict), text, re.S | re.M)
    return bool(m), bool(m and note in m.group(1))


def press(pg, ids, verdict, note):
    """Choose the first option, type the note, press the verdict's button; the page's status line afterwards."""
    pre, box, yes, no, notebox, status = ids
    pg.evaluate("""([note, notebox]) => {
      const r = document.querySelector('input[type=radio]'); if (r) r.click();
      const t = document.getElementById(notebox); if (t) { t.value = note; t.dispatchEvent(new Event('input', { bubbles: true })); }
    }""", [note, notebox])
    pg.click("#" + (yes if verdict == "confirmed" else no))
    for _ in range(40):
        s = pg.evaluate("(id) => (document.getElementById(id) || {}).textContent || ''", status).strip()
        if s:
            return s
        time.sleep(0.1)
    return ""


def main():
    pages = sorted(f for f in os.listdir(art) if f.endswith(".html"))
    only = os.environ.get("ONLY")
    if only:
        pages = [p for p in pages if p[:8] in only.split(",")]
    site = build_site(pages)
    port = free_port()
    server = subprocess.Popen([sys.executable, os.path.join(scripts, "web.py"), "--port", str(port), "--no-work"], cwd=site,
                              stdout=subprocess.DEVNULL, stderr=open(os.path.join(out, "server.err"), "w"))
    base = "http://127.0.0.1:%d" % port
    for _ in range(80):
        try:
            urllib.request.urlopen(base + "/api/state", timeout=1).read()
            break
        except Exception:
            time.sleep(0.25)
    else:
        server.terminate()
        sys.exit("web.py did not start")
    rows = []
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=shutil.which("chromium"), args=["--no-sandbox"])
            ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1, has_touch=True, is_mobile=True)
            for k, name in enumerate(pages):
                text = open(os.path.join(art, name)).read()
                row = dict(page=name, variant=variant(text))
                ids = next((i for i in IDS if 'id="%s"' % i[2] in text and 'id="%s"' % i[3] in text), None)
                if not ids:
                    row["result"] = "no buttons"
                    rows.append(row)
                    continue
                pg = ctx.new_page()
                errors = []
                pg.on("pageerror", lambda e: errors.append(str(e)[:120]))
                pg.on("console", lambda m: errors.append(m.text[:120]) if m.type == "error" else None)
                try:
                    for verdict, n in (("confirmed", 1001 + 2 * k), ("refuted", 1002 + 2 * k)):
                        note = "note-%d-%s" % (n, verdict)
                        pg.goto(base + "/artefacts/" + name, wait_until="load", timeout=15000)
                        try:
                            pg.wait_for_function("(id) => { const e = document.getElementById(id); return e && !e.hidden; }", arg=ids[1], timeout=4000)
                        except Exception:
                            row[verdict] = "rule box never shown"
                            break
                        said = press(pg, ids, verdict, note)
                        found, noted = logged(site, n, verdict, note)
                        row[verdict] = "ok" if found and said.startswith("Recorded") else "FAIL: status %r, entry %s" % (said[:60], found)
                        row[verdict + "_note"] = noted
                        row[verdict + "_status"] = said[:60]
                    if row.get("confirmed") == "ok" and row.get("refuted") == "ok":
                        pg.goto(base + "/artefacts/" + name, wait_until="load")
                        time.sleep(0.8)
                        row["box_after"] = "hidden" if pg.evaluate("(id) => { const e = document.getElementById(id); return !e || e.hidden; }", ids[1]) else "STILL SHOWN"
                except Exception as e:
                    row["error"] = str(e)[:160]
                row["errors"] = errors[:3]
                row["result"] = ("posts both" if row.get("confirmed") == "ok" and row.get("refuted") == "ok"
                                 else "never offered" if "rule box never shown" in (row.get("confirmed", ""), row.get("refuted", ""))
                                 else "FAILS")
                rows.append(row)
                pg.close()
            b.close()
    finally:
        server.terminate()
        if os.environ.get("KEEP"): print("site kept:", site)
        else: shutil.rmtree(site, ignore_errors=True)
    json.dump(rows, open(os.path.join(out, "rulings.json"), "w"), indent=1)
    cols = ["page", "variant", "result", "confirmed", "confirmed_note", "refuted", "refuted_note", "box_after", "errors", "error"]
    with open(os.path.join(out, "rulings.tsv"), "w") as f:
        f.write("\t".join(cols) + "\n")
        for r in rows:
            f.write("\t".join(str(r.get(c, "")) if not isinstance(r.get(c), list) else "; ".join(r[c]) for c in cols) + "\n")
    by = collections.defaultdict(collections.Counter)
    for r in rows:
        by[r["variant"]][r["result"]] += 1
    print("%d pages" % len(rows))
    for v, c in sorted(by.items()):
        print("  %-34s %s" % (v, ", ".join("%d %s" % (n, k) for k, n in sorted(c.items()))))
    bad = [r for r in rows if r["result"] == "FAILS"]
    for r in bad:
        print("FAIL", r["page"], r.get("confirmed"), r.get("refuted"), r.get("error", ""))
    sys.exit(1 if bad else 0)


main()
