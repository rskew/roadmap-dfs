#!/usr/bin/env python3
"""Draw every artefact page in headless chromium, as the web page serves it, and measure it (W131.38).

    FONTCONFIG_FILE=<conf naming a font dir> python3 docs/ux-artefacts-draw.py OUT_DIR [ARTEFACT_DIR] [SCRIPTS_DIR]

Until W131.38 the artefact audit (docs/ux-artefacts.py) read the HTML and a DOM text dump; no page was seen drawn,
because chromium here drew no text (a missing fontconfig file, not the pages). This script serves a copy of
ARTEFACT_DIR (default: this repository's `.dfs/artefacts`) with `SCRIPTS_DIR/web.py --no-work` on a throw-away site,
so the pages come with the same sandbox and Content-Security-Policy the author's browser gives them, opens each `.html`
at a phone (390x844) and a desktop (1280x800) width, and writes to OUT_DIR:

  <page>-phone.png, <page>-desk.png   the page drawn, whole (full_page)
  draw.tsv                            one row per page and width: the measures below
  draw.json                           the same with the detail (which elements, which text)

Measures per page and width: sideways scroll; text wider than its box; text under 12px; a control under 32px
(the artefact checker's floor) and under 44px (a finger); contrast under 4.5 of the text as drawn; console and page
errors; blocked requests; words visible outside the answer form; how many pixels of the page lie above the form;
whether the form is on screen and its radios and buttons can be reached; whether the page's own buttons change the page
when clicked (innerText outside the form compared before and after, up to three of them); whether a chosen option is
kept on reload (the page's `/artefact-state/` round trip).
"""
import json, os, re, shutil, socket, subprocess, sys, tempfile, time, urllib.request

here = os.path.dirname(os.path.abspath(__file__))
out = os.path.abspath(sys.argv[1])
art = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, "..", ".dfs", "artefacts"))
scripts = os.path.abspath(sys.argv[3] if len(sys.argv) > 3 else os.path.join(here, "..", "scripts"))
os.makedirs(out, exist_ok=True)
CHECKS = re.search(r'CHECKS = r"""(.*?)"""', open(os.path.join(here, "ux-web-playwright.py")).read(), re.S).group(1)

from playwright.sync_api import sync_playwright

EXTRA = r"""
() => {
  const form = document.getElementById('dfs-answer');
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const outside = [...document.querySelectorAll('body *')].filter(el => !(form && form.contains(el)) && !el.closest('script,style,template'));
  const text = (document.body.innerText || '');
  const formText = form ? form.innerText : '';
  const own = text.replace(formText, '').trim();
  const r = form ? form.getBoundingClientRect() : null;
  return {
    formPresent: !!form, formTop: r ? Math.round(r.top + scrollY) : null, formH: r ? Math.round(r.height) : null,
    pageH: document.documentElement.scrollHeight, ownWords: own ? own.split(/\s+/).length : 0,
    radios: form ? form.querySelectorAll('input[type=radio]').length : 0,
    visibleButtons: form ? [...form.querySelectorAll('button')].filter(vis).map(b => b.textContent.trim()) : [],
    ownControls: [...document.querySelectorAll('button, input, select, textarea, summary, [role=button], [tabindex="0"]')]
      .filter(el => !(form && form.contains(el)) && vis(el)).length,
    svg: document.querySelectorAll('svg').length, canvas: document.querySelectorAll('canvas').length,
    title: (document.title || '').slice(0, 80),
    h1: ((document.querySelector('h1') || {}).textContent || '').trim().slice(0, 100),
  };
}
"""

OWN_CLICK = r"""
async () => {
  const form = document.getElementById('dfs-answer');
  const fire = [...document.querySelectorAll('button, input[type=range], input[type=radio], input[type=checkbox], select, summary, [role=button], [tabindex="0"]')]
    .filter(el => !(form && form.contains(el)) && el.getBoundingClientRect().width > 0 && !el.disabled).slice(0, 3);
  const snap = () => { const c = form ? form.innerText : ''; return document.body.innerText.replace(c, '') + '|' + document.body.innerHTML.length + '|' + [...document.querySelectorAll('svg *, [style]')].map(e => e.getAttribute('style') || e.getAttribute('d') || e.getAttribute('fill') || '').join('').length; };
  const res = [];
  for (const el of fire) {
    const before = snap();
    if (el.type === 'range') { el.value = el.max; el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); }
    else el.click();
    await new Promise(r => setTimeout(r, 150));
    res.push({ el: el.tagName.toLowerCase() + ' ' + (el.textContent || el.value || el.type || '').trim().replace(/\s+/g, ' ').slice(0, 30), changed: snap() !== before });
  }
  return res;
}
"""


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def build_site():
    site = tempfile.mkdtemp(prefix="w131art")
    dfs = os.path.join(site, ".dfs")
    shutil.copytree(os.path.join(here, "tui-ux-review", "items"), os.path.join(dfs, "items"))
    shutil.copytree(art, os.path.join(dfs, "artefacts"), ignore=shutil.ignore_patterns("*.png", "*.jpg", "*.json"))
    shutil.copy(os.path.join(here, "..", ".dfs", "ROADMAP.md"), dfs)
    ids = sorted(f[:-3] for f in os.listdir(os.path.join(dfs, "items")))
    open(os.path.join(dfs, "order.md"), "w").write("# Order\n\nitems\n\n" + "".join("- %s\n" % i for i in ids))
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "fixture"]):
        subprocess.run(["git"] + cmd, cwd=site, check=True, stdout=subprocess.DEVNULL)
    return site


def main():
    site = build_site()
    port = free_port()
    server = subprocess.Popen([sys.executable, os.path.join(scripts, "web.py"), "--port", str(port), "--no-work"], cwd=site,
                              stdout=subprocess.DEVNULL, stderr=open(os.path.join(out, "server.err"), "w"))
    base = "http://127.0.0.1:%d" % port
    for _ in range(60):
        try:
            urllib.request.urlopen(base + "/api/state", timeout=1).read()
            break
        except Exception:
            time.sleep(0.25)
    else:
        server.terminate()
        sys.exit("web.py did not start")
    pages = sorted(f for f in os.listdir(os.path.join(site, ".dfs", "artefacts")) if f.endswith(".html"))
    only = os.environ.get("ONLY")
    if only:
        pages = [p for p in pages if p[:8] in only.split(",")]
    rows = []
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=shutil.which("chromium"), args=["--no-sandbox"])
            for size, vp in (("phone", {"width": 390, "height": 844}), ("desk", {"width": 1280, "height": 800})):
                ctx = b.new_context(viewport=vp, device_scale_factor=1, has_touch=size == "phone", is_mobile=size == "phone")
                for name in pages:
                    pg = ctx.new_page()
                    errors, failed = [], []
                    pg.on("pageerror", lambda e: errors.append(str(e)[:120]))
                    pg.on("console", lambda m: errors.append(m.type + ": " + m.text[:120]) if m.type == "error" else None)
                    pg.on("requestfailed", lambda r: failed.append(r.url[-60:]))
                    try:
                        pg.goto(base + "/artefacts/" + name, wait_until="load", timeout=15000)
                        time.sleep(0.5)
                        png = os.path.join(out, "%s-%s.png" % (name[:-5], size))
                        pg.screenshot(path=png, full_page=True)
                        res = pg.evaluate(CHECKS)
                        res.update(pg.evaluate(EXTRA))
                        res["ownClicks"] = pg.evaluate(OWN_CLICK)
                                        # the state round trip: choose the first option, wait out the 300 ms debounce, reload, is it kept?
                        kept = None
                        if res["radios"]:
                            pg.evaluate("document.querySelector('#dfs-answer input[type=radio]').click()")
                            time.sleep(0.8)
                            pg.reload(wait_until="load")
                            time.sleep(0.5)
                            kept = pg.evaluate("!!document.querySelector('#dfs-answer input[type=radio]:checked')")
                        res["kept"] = kept
                    except Exception as e:
                        res = dict(error=str(e)[:200])
                    res.update(page=name, size=size, errors=errors[:5], failed=failed[:5])
                    rows.append(res)
                    pg.close()
                ctx.close()
            b.close()
    finally:
        server.terminate()
        shutil.rmtree(site, ignore_errors=True)
    json.dump(rows, open(os.path.join(out, "draw.json"), "w"), indent=1)
    cols = ["page", "size", "scrollW", "outside", "clipped", "tiny", "small32", "small44", "unlabelled", "lowContrast", "errors", "failed",
            "ownWords", "formTop", "pageH", "formH", "radios", "ownControls", "svg", "canvas", "ownClicks", "kept", "error"]
    with open(os.path.join(out, "draw.tsv"), "w") as f:
        f.write("\t".join(cols) + "\n")
        for r in rows:
            sw = r.get("scrollW")
            row = dict(r, scrollW=("%d>%d" % tuple(sw) if sw and sw[0] > sw[1] else "") if sw else "",
                       outside=len(r.get("outside") or []), clipped=len(r.get("clipped") or []), tiny=len(r.get("tiny") or []),
                       small32=len([s for s in r.get("small") or [] if s["h"] < 32 or s["w"] < 32]),
                       small44=len(r.get("small") or []), unlabelled=len(r.get("unlabelled") or []),
                       lowContrast=len(r.get("lowContrast") or []), errors=len(r.get("errors") or []), failed=len(r.get("failed") or []),
                       ownClicks="%d/%d" % (sum(c["changed"] for c in r.get("ownClicks") or []), len(r.get("ownClicks") or [])))
            f.write("\t".join(str(row.get(c, "")) for c in cols) + "\n")


main()
