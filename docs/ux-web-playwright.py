#!/usr/bin/env python3
"""Drive the real web page in headless chromium with playwright and draw it (W131.34).

    FONTCONFIG_FILE=<conf naming a font dir> python3 docs/ux-web-playwright.py SITE_DIR OUT_DIR [SCRIPTS_DIR]

SITE_DIR is a git repo with a `.dfs/` (items, artefacts, order.md, ROADMAP.md); the script serves
it with `SCRIPTS_DIR/web.py` (default: this repository's `scripts/`) and writes screenshots and
`findings.json` to OUT_DIR. The chromium of earlier nodes "drew no text" only because fontconfig
had no config file: with FONTCONFIG_FILE pointing at one that names a font directory the page is drawn.

A tour is a list of (name, setup) pairs below; each is drawn at a phone (390x844) and a desktop
(1280x800) size, and a set of checks (sideways scroll, text clipped by its box, tap targets under
44 px, unlabelled buttons, contrast of the text colours as drawn) is run on every view.
"""
import json, os, shutil, socket, subprocess, sys, time, urllib.request

site, out = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
scripts = os.path.abspath(sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
os.makedirs(out, exist_ok=True)

from playwright.sync_api import sync_playwright

CHECKS = r"""
() => {
  const vw = innerWidth, res = {};
  res.scrollW = [document.documentElement.scrollWidth, vw];
  const vis = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && !el.closest('[hidden]') && !el.closest('dialog:not([open])'); };
  const name = el => (el.id ? '#' + el.id : '') + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).join('.') : '') || el.tagName.toLowerCase();
  const label = el => (el.getAttribute('aria-label') || el.textContent || el.title || '').trim().replace(/\s+/g, ' ').slice(0, 40);
  res.small = [...document.querySelectorAll('button, a[href], [role=button], input, select, summary, textarea')].filter(vis)
    .map(el => { const r = el.getBoundingClientRect(); return { el: el.tagName.toLowerCase() + ' ' + name(el), label: label(el), w: Math.round(r.width), h: Math.round(r.height) }; })
    .filter(x => x.h < 44 || x.w < 44);
  res.unlabelled = [...document.querySelectorAll('button, a[href], input, select, textarea')].filter(vis)
    .filter(el => !label(el) && !(el.labels && el.labels.length) && !el.getAttribute('placeholder')).map(el => el.tagName.toLowerCase() + ' ' + name(el));
  res.outside = [...document.querySelectorAll('body *')].filter(vis).filter(el => { const r = el.getBoundingClientRect(); return r.right > vw + 1 || r.left < -1; })
    .slice(0, 8).map(el => name(el) + ' ' + Math.round(el.getBoundingClientRect().left) + '..' + Math.round(el.getBoundingClientRect().right));
  res.clipped = [...document.querySelectorAll('body *')].filter(vis).filter(el => { const s = getComputedStyle(el);
    return el.children.length === 0 && el.textContent.trim() && (s.overflow !== 'visible') && (el.scrollWidth > el.clientWidth + 1 && s.textOverflow !== 'ellipsis' || el.scrollHeight > el.clientHeight + 1 && s.overflowY !== 'auto' && s.overflowY !== 'scroll'); })
    .slice(0, 8).map(el => name(el) + ' ' + el.scrollWidth + '>' + el.clientWidth + ' / ' + el.scrollHeight + '>' + el.clientHeight + ' "' + label(el) + '"');
  res.ellipsis = [...document.querySelectorAll('body *')].filter(vis).filter(el => getComputedStyle(el).textOverflow === 'ellipsis' && el.scrollWidth > el.clientWidth + 1)
    .slice(0, 8).map(el => name(el) + ' "' + label(el) + '"');
  const lum = c => { const [r, g, b] = c.map(v => { v /= 255; return v <= .03928 ? v / 12.92 : Math.pow((v + .055) / 1.055, 2.4); }); return .2126 * r + .7152 * g + .0722 * b; };
  const parse = s => { const m = s.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[ ,\/]+/).map(Number); return { c: p.slice(0, 3), a: p.length > 3 ? p[3] : 1 }; };
  const bgOf = el => { for (let e = el; e; e = e.parentElement) { const b = parse(getComputedStyle(e).backgroundColor); if (b && b.a > .5) return b.c; } return [255, 255, 255]; };
  const low = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!vis(el)) continue;
    const own = [...el.childNodes].filter(n => n.nodeType === 3 && n.textContent.trim()).length;
    if (!own) continue;
    const s = getComputedStyle(el), fg = parse(s.color); if (!fg) continue;
    const bg = bgOf(el), L1 = lum(fg.c), L2 = lum(bg), ratio = (Math.max(L1, L2) + .05) / (Math.min(L1, L2) + .05);
    const big = parseFloat(s.fontSize) >= 24 || (parseFloat(s.fontSize) >= 18.66 && +s.fontWeight >= 700);
    if (ratio < (big ? 3 : 4.5) * (fg.a < 1 ? 1 : 1)) low.push({ el: name(el), ratio: +ratio.toFixed(2), size: s.fontSize, text: label(el) });
  }
  res.lowContrast = low.slice(0, 10);
  res.tiny = [...document.querySelectorAll('body *')].filter(vis).filter(el => [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()) && parseFloat(getComputedStyle(el).fontSize) < 12)
    .slice(0, 6).map(el => name(el) + ' ' + getComputedStyle(el).fontSize + ' "' + label(el) + '"');
  res.focusable = document.activeElement ? name(document.activeElement) : null;
  return res;
}
"""


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    port = free_port()
    server = subprocess.Popen([sys.executable, os.path.join(scripts, "web.py"), "--port", str(port)] + ([] if os.environ.get("WALK") else ["--no-walk"]),
                              cwd=site, stdout=subprocess.DEVNULL, stderr=open(os.path.join(out, "server.err"), "w"))
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
    tour = json.load(open(os.environ["TOUR"])) if os.environ.get("TOUR") else []
    findings = []
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=shutil.which("chromium"), args=["--no-sandbox"])
            for size_name, vp in (("phone", {"width": 390, "height": 844}), ("desk", {"width": 1280, "height": 800})):
                ctx = b.new_context(viewport=vp, device_scale_factor=1, has_touch=size_name == "phone", is_mobile=size_name == "phone")
                pg = ctx.new_page()
                errors = []
                pg.on("pageerror", lambda e: errors.append(str(e)))
                pg.on("console", lambda m: errors.append("console." + m.type + ": " + m.text) if m.type in ("error", "warning") else None)
                for step in tour:
                    name = step["name"]
                    if "goto" in step:
                        pg.goto(base + "/#/" + step["goto"])
                        pg.reload() if step.get("reload") else None
                    for js in step.get("js", []):
                        pg.evaluate(js)
                    for sel in step.get("click", []):
                        pg.click(sel, timeout=5000)
                    time.sleep(step.get("wait", 1.0))
                    pg.screenshot(path=os.path.join(out, "%s-%s.png" % (name, size_name)), full_page=step.get("full", False))
                    res = pg.evaluate(CHECKS)
                    res.update(view=name, size=size_name, errors=list(errors))
                    findings.append(res)
                    errors.clear()
                ctx.close()
            b.close()
    finally:
        server.terminate()
        json.dump(findings, open(os.path.join(out, "findings.json"), "w"), indent=1)


main()
