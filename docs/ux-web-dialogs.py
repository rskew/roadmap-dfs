#!/usr/bin/env python3
"""Does each web dialog open at its heading? Drawn in headless chromium (W131.43).

    FONTCONFIG_FILE=<conf naming a font dir> python3 docs/ux-web-dialogs.py OUT_DIR [SCRIPTS_DIR]

`form()` in `scripts/web/index.html` shows every dialog with `showModal()` and then focuses its first box with
`preventScroll`. Measured: chromium still scrolls the dialog's `<form>` (the box that scrolls; the dialog itself
does not) to bring the focused box into view about 100 to 200 ms later, so a dialog tall for its window opened with
its `<h3>` out of sight. For each dialog at 320, 390 and 1280 px (and 1280 x 400, a short window) this opens it through
its real button and prints the largest `scrollTop` of the dialog or anything in it 0.8 s later, whether the `<h3>`
lies inside the dialog's box, and the dialog's scroll height against its height.
It then wheels over the dialog's heading: a dialog that can scroll must move (`after_wheel`), so the fix does not pin it.
It exits 1 when any dialog opens with its heading out of view or will not scroll. It uses the fixture site of `docs/ux-web-flows.py`
(its `build_site` and `serve`), so that file's requirements apply (playwright, chromium).
"""
import json, os, re, shutil, sys, time

here = os.path.dirname(os.path.abspath(__file__))
out = os.path.abspath(sys.argv[1])
os.makedirs(out, exist_ok=True)
src = open(os.path.join(here, "ux-web-flows.py")).read()
src = src[:src.rindex("\nmain()")]                      # its helpers, without running its tour
sys.argv = [sys.argv[0], out] + sys.argv[2:]
exec(compile(src, "ux-web-flows.py", "exec"))

PROBE = """() => {
  const d = document.querySelector('#form'), h = d.querySelector('h3'), dr = d.getBoundingClientRect(), hr = h ? h.getBoundingClientRect() : null;
  const a = document.activeElement;
  const scrolled = [d, ...d.querySelectorAll('*')].filter(e => e.scrollTop > 0).reduce((m, e) => Math.max(m, Math.round(e.scrollTop)), 0);   // the form is the box that scrolls, not the dialog
  return { open: d.open, scrollTop: scrolled, scrollH: d.scrollHeight, h: Math.round(dr.height),
           heading: h ? h.textContent.slice(0, 40) : null,
           headingInView: !!hr && hr.top >= dr.top - 1 && hr.bottom <= dr.bottom + 1,
           focus: a ? a.tagName.toLowerCase() + (a.id ? '#' + a.id : '') : null };
}"""

VIEWS = (("w320", {"width": 320, "height": 640}), ("w390", {"width": 390, "height": 844}),
         ("w1280", {"width": 1280, "height": 800}), ("w1280x400", {"width": 1280, "height": 400}))


def main():
    rows = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=shutil.which("chromium"), args=["--no-sandbox"])
        for size, vp in VIEWS:
            site = build_site()
            srv, base = serve(site)
            try:
                ctx = b.new_context(viewport=vp, device_scale_factor=1, has_touch=size != "w1280", is_mobile=size.startswith("w3"))
                pg = ctx.new_page()

                def see(name, how):
                    time.sleep(0.8)
                    r = pg.evaluate(PROBE)
                    r.update(dialog=name, size=size, how=how)
                    pg.screenshot(path=os.path.join(out, "%s-%s.png" % (name, size)))
                    r["canScroll"] = pg.evaluate("(() => { const f = document.querySelector('#form form'); return f.scrollHeight > f.clientHeight + 1; })()")
                    hb = pg.locator("#form h3").bounding_box(); pg.mouse.move(hb["x"] + 8, hb["y"] + hb["height"] / 2); pg.mouse.wheel(0, 150); time.sleep(0.4)   # the reader scrolls: it must not be put back
                    r["afterWheel"] = pg.evaluate("Math.round(document.querySelector('#form form').scrollTop)")
                    rows.append(r)
                    pg.keyboard.press("Escape"); time.sleep(0.3)

                pg.goto(base + "/#/t/W9/W9.3"); time.sleep(0.8)
                pg.click("[data-act=answer]"); see("answer-with-page", "button")
                pg.goto(base + "/#/t/W2/W2.1"); time.sleep(0.8)
                pg.click("[data-act=answer]"); see("answer-no-page", "button")
                pg.goto(base + "/#/t/W3"); time.sleep(0.8)
                pg.click("[data-act=add][data-parent='']"); see("add-node", "button")
                pg.goto(base + "/#/t/W9/W9.2"); time.sleep(0.8)
                if pg.query_selector("[data-act=correct]"):
                    pg.click("[data-act=correct]"); see("refute-confirm", "button")
                for t in sorted(f[:-3] for f in os.listdir(os.path.join(site, ".dfs", "items"))):
                    pg.goto(base + "/#/t/" + t); time.sleep(0.6)
                    if pg.query_selector("[data-act=accept]"):
                        pg.click("[data-act=accept]"); see("accept-" + t, "button")
                        break
                else:
                    rows.append(dict(dialog="accept", size=size, how="no fixture task is acceptable"))
                ctx.close()
            finally:
                srv.terminate()
                shutil.rmtree(site, ignore_errors=True)
        b.close()
    json.dump(rows, open(os.path.join(out, "dialogs.json"), "w"), indent=1)
    bad = 0
    for r in rows:
        if "open" not in r:
            print(r["size"], r["dialog"], r["how"]); continue
        flag = "" if r["headingInView"] and r["open"] else "  <-- heading out of view"
        if r["canScroll"] and not r["afterWheel"]:
            flag += "  <-- the reader's scroll was undone"
        bad += bool(flag)
        print("%-10s %-18s open=%s scrollTop=%-4s scrollH=%-5s h=%-4s heading_in_view=%-5s after_wheel=%-4s focus=%s%s" % (
            r["size"], r["dialog"], r["open"], r["scrollTop"], r["scrollH"], r["h"], r["headingInView"], r["afterWheel"], r["focus"], flag))
    sys.exit(1 if bad else 0)


main()
