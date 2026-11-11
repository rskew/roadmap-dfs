#!/usr/bin/env python3
"""Drive the flows the first web tour left out, in headless chromium (W131.37).

    FONTCONFIG_FILE=<conf naming a font dir> python3 docs/ux-web-flows.py OUT_DIR [SCRIPTS_DIR]

It builds a small site from `docs/tui-ux-review/items` (tasks W1-W10; W9.3 has an open raise),
gives the W9.3 raise an answer page (a copy of the answer template, named by an `Answer with:` line), serves it with
`SCRIPTS_DIR/web.py --no-work`, and at 320, 390 and 1280 px wide drives:

  raise-card    the node sheet of W9.3 with its open raise
  answer-page   the Answer dialog with the page in it: pick an option in the frame, press its Send button, see the dialog close
  answer-text   the Answer dialog of W2.1 (no page; a raise the script appends): type an answer, Record it
  add-node      the task's "Add a node" dialog, filled and sent
  reorder       the task list's Reorder mode
  keyboard      Tab through the task page: does each stop show a focus ring; Esc closes a dialog

Every view is drawn (png), measured with the CHECKS of docs/ux-web-playwright.py (sideways scroll, tap targets under
44 px, unlabelled buttons, clipped text, contrast, console errors), and `flows.json` in OUT_DIR holds the lot.
The walk controls and the run log are driven by docs/ux-web-work.py (this script serves with `--no-work`).
"""
import json, os, re, shutil, socket, subprocess, sys, tempfile, time, urllib.request, uuid

here = os.path.dirname(os.path.abspath(__file__))
out = os.path.abspath(sys.argv[1])
scripts = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, "..", "scripts"))
os.makedirs(out, exist_ok=True)
CHECKS = re.search(r'CHECKS = r"""(.*?)"""', open(os.path.join(here, "ux-web-playwright.py")).read(), re.S).group(1)
TEMPLATE = os.path.join(scripts, "..", "templates", "_TEMPLATE_ANSWER.html")
PAGE = str(uuid.uuid4()) + ".html"

from playwright.sync_api import sync_playwright


def build_site():
    site = tempfile.mkdtemp(prefix="w131flow")
    dfs = os.path.join(site, ".dfs")
    shutil.copytree(os.path.join(here, "tui-ux-review", "items"), os.path.join(dfs, "items"))
    os.makedirs(os.path.join(dfs, "artefacts"))
    shutil.copy(os.path.join(here, "..", ".dfs", "ROADMAP.md"), dfs)
    shutil.copy(TEMPLATE, os.path.join(dfs, "artefacts", PAGE))
    ids = sorted(f[:-3] for f in os.listdir(os.path.join(dfs, "items")))
    open(os.path.join(dfs, "order.md"), "w").write("# Order\n\nitems\n\n" + "".join("- %s\n" % i for i in ids))
    w9 = os.path.join(dfs, "items", "W9.md")
    t = open(w9).read()
    t = "".join(l + ("  Answer with: .dfs/artefacts/%s\n" % PAGE if "Options: (a) keep WAL" in l else "") for l in t.splitlines(True))
    open(w9, "w").write(t)
    with open(os.path.join(dfs, "items", "W2.md"), "a") as f:   # a raise with no answer page
        f.write("- 2026-10-07T09:00:00Z · raise · W2.1\n  What I need from you: pick one of the two.\n  Options:\n  (a) Warn on unknown keys.\n  (b) Fail on them.\n  I would take (a).\n")
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "fixture"]):
        subprocess.run(["git"] + cmd, cwd=site, check=True, stdout=subprocess.DEVNULL)
    return site


def serve(site):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = subprocess.Popen([sys.executable, os.path.join(scripts, "web.py"), "--port", str(port), "--no-work"], cwd=site,
                           stdout=subprocess.DEVNULL, stderr=open(os.path.join(out, "server.err"), "a"))
    base = "http://127.0.0.1:%d" % port
    for _ in range(80):
        try:
            urllib.request.urlopen(base + "/api/state", timeout=1).read()
            return srv, base
        except Exception:
            time.sleep(0.25)
    srv.terminate()
    sys.exit("web.py did not start")


def main():
    findings = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=shutil.which("chromium"), args=["--no-sandbox"])
        for size, vp in (("w320", {"width": 320, "height": 640}), ("w390", {"width": 390, "height": 844}), ("w1280", {"width": 1280, "height": 800})):
            site = build_site()
            srv, base = serve(site)
            try:
                ctx = b.new_context(viewport=vp, device_scale_factor=1, has_touch=size != "w1280", is_mobile=size != "w1280")
                pg = ctx.new_page()
                errors = []
                pg.on("pageerror", lambda e: errors.append(str(e)))
                pg.on("console", lambda m: errors.append("console." + m.type + ": " + m.text) if m.type in ("error", "warning") else None)

                def see(name, extra=None):
                    time.sleep(0.8)
                    pg.screenshot(path=os.path.join(out, "%s-%s.png" % (name, size)))
                    res = pg.evaluate(CHECKS)
                    res.update(view=name, size=size, errors=list(errors), extra=extra or {})
                    findings.append(res)
                    errors.clear()

                def api(path):
                    return json.load(urllib.request.urlopen(base + path))

                def raises(task):
                    return [x for r in api("/api/task/" + task)["rows"] for x in ([r] if r.get("kind") == "raise" else r.get("raises", []))]

                # the raise card
                pg.goto(base + "/#/t/W9/W9.3"); see("raise-card", {"open_raises": len(raises("W9"))})
                # the answer, with a page
                pg.click("[data-act=answer]"); time.sleep(1)
                see("answer-page-open", {"frame": bool(pg.query_selector("#answerpage")), "frame_h": pg.evaluate("Math.round(document.querySelector('#answerpage').getBoundingClientRect().height)"), "want_h": round(vp["height"] * 0.4)})
                fr = pg.frame_locator("#answerpage")
                fr.locator("input[name=dfs-option]").first.check()
                fr.locator("#dfs-send").scroll_into_view_if_needed()
                see("answer-page-chosen")
                fr.locator("#dfs-send").click(); time.sleep(1.2)
                see("answer-page-sent", {"raises_left": len(raises("W9")), "dialog_open": pg.evaluate("document.querySelector('#form').open")})
                # the answer, in words
                pg.goto(base + "/#/t/W2/W2.1"); time.sleep(0.8)
                pg.click("[data-act=answer]"); time.sleep(0.6)
                pg.fill("#b", "Take (a), keep the keys optional.")
                see("answer-text-open")
                pg.click("button[value=ok]"); time.sleep(1.2)
                see("answer-text-sent", {"raises_left": len(raises("W2")), "dialog_open": pg.evaluate("document.querySelector('#form').open")})
                # add a node
                pg.goto(base + "/#/t/W3"); time.sleep(0.8)
                pg.click("[data-act=add][data-parent='']"); time.sleep(0.5)
                pg.fill("#t", "Check the cache key"); see("add-node-open")
                n0 = len(api("/api/task/W3")["rows"])
                pg.click("button[value=ok]"); time.sleep(1.2)
                see("add-node-sent", {"rows_before": n0, "rows_after": len(api("/api/task/W3")["rows"])})
                # reorder
                pg.goto(base + "/#/"); time.sleep(0.8)
                if pg.query_selector("[data-act=reorder]"):
                    pg.click("[data-act=reorder]"); time.sleep(0.5)
                see("reorder", {"moves": len(pg.query_selector_all("[data-act=move]"))})
                # keyboard: Tab through the task page, then Esc closes a dialog
                pg.goto(base + "/#/t/W2"); time.sleep(0.8); pg.mouse.click(1, 1)
                stops = []
                for _ in range(14):
                    pg.keyboard.press("Tab")
                    stops.append(pg.evaluate("""() => { const e = document.activeElement, s = getComputedStyle(e), r = e.getBoundingClientRect();
                      return { el: e.tagName.toLowerCase() + (e.dataset && e.dataset.act ? '[' + e.dataset.act + ']' : ''), label: (e.getAttribute('aria-label') || e.textContent || '').trim().slice(0, 24),
                        ring: s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0 || s.boxShadow !== 'none', onscreen: r.bottom > 0 && r.top < innerHeight }; }"""))
                see("keyboard-tab", {"stops": stops, "no_ring": [s for s in stops if not s["ring"]], "offscreen": [s for s in stops if not s["onscreen"]]})
                pg.click("[data-act=add][data-parent='']"); time.sleep(0.5)
                focus_in = pg.evaluate("document.activeElement.closest('dialog') !== null")
                pg.keyboard.press("Escape"); time.sleep(0.5)
                see("keyboard-esc", {"focus_in_dialog": focus_in, "dialog_open_after_esc": pg.evaluate("document.querySelector('#form').open")})
                ctx.close()
            finally:
                srv.terminate()
                shutil.rmtree(site, ignore_errors=True)
        b.close()
    json.dump(findings, open(os.path.join(out, "flows.json"), "w"), indent=1)
    for f in findings:
        bad = {k: f[k] for k in ("small", "unlabelled", "outside", "clipped", "lowContrast", "errors") if f[k]}
        print(f["size"], f["view"], "scroll" if f["scrollW"][0] > f["scrollW"][1] else "", json.dumps(f["extra"])[:200], json.dumps(bad)[:400])


main()
