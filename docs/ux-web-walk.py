#!/usr/bin/env python3
"""Drive the walk controls, the run log and the Reorder break buttons in headless chromium (W131.45).

    FONTCONFIG_FILE=<conf naming a font dir> [WALK_TMP=<a directory that allows exec>] python3 docs/ux-web-walk.py OUT_DIR [SCRIPTS_DIR]

`docs/ux-web-flows.py` serves with `--no-walk`, which refuses every walk write, so it could not draw them. This script serves
a fixture site (the tasks of `docs/tui-ux-review/items`) WITH the walk on, from a COPY of SCRIPTS_DIR whose `run.sh` is
a stub: it writes the `meta.json` a real chain writes, prints a few console lines for about 25 s, and on SIGINT (what Stop
chain sends) writes its finish and leaves. No agent is started. Everything else, `web.py`, `walk.py`, `runs.py` and the page, is
the real code. At 320, 390 and 1280 px it draws:

  idle-list / idle-open     the task list and the idle walk box (Next, Agent, models, Budget, Chains, Limit, Start walk)
  invalid                   Start walk with Budget 0: the form must refuse it and send nothing
  running / running-open    after Start walk: the state line, the chain row and its Stop chain button
  runs-live / runlog-live   the task page's Runs list and the live log, which must grow while it is open
  chainstop-dialog / after  the Stop chain dialog, then the chain stopped and the walk's note
  reorder                   Reorder mode: the Break and Stop break buttons on every row

Each view is measured with the CHECKS of docs/ux-web-playwright.py (sideways scroll, tap targets under 44 px, unlabelled
buttons, clipped text, contrast, console errors); `walk.json` in OUT_DIR holds the lot, and the rows it prints are the ones
with a finding.
"""
import json, os, re, shutil, socket, subprocess, sys, tempfile, time, urllib.request

here = os.path.dirname(os.path.abspath(__file__))
out = os.path.abspath(sys.argv[1])
scripts = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, "..", "scripts"))
os.makedirs(out, exist_ok=True)
CHECKS = re.search(r'CHECKS = r"""(.*?)"""', open(os.path.join(here, "ux-web-playwright.py")).read(), re.S).group(1)

from playwright.sync_api import sync_playwright

STUB = r"""#!/bin/sh
# A stand-in for run.sh: the chain's own files, a few console lines, a finish on SIGINT or after 25 s. Starts no agent.
ITEM="$1"; CAP="$2"
meta() { python3 - "$RUNDIR/meta.json" "$@" <<'PY'
import json, os, sys
p = sys.argv[1]
try: m = json.load(open(p))
except Exception: m = {}
for kv in sys.argv[2:]:
    k, _, v = kv.partition("="); m[k] = v
json.dump(m, open(p + ".tmp", "w")); os.replace(p + ".tmp", p)
PY
}
finish() { meta finished="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)" rc=130 stop=idle; echo "   interrupted"; exit 0; }
trap finish INT TERM
meta item="$ITEM" agent=claude mode=work cap="${CAP:-5}" run=1 started="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)" pid=$$ kind=work
echo "dfs_run: task $ITEM, cap ${CAP:-5}, agent claude, logs in $RUNDIR"
echo "── run 1/${CAP:-5} · work ──"
i=0
while [ $i -lt 25 ]; do
  i=$((i + 1)); echo "   step $i: reading the task file"; sleep 1
done
meta finished="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)" rc=0 stop=cap
echo "   exit 0"
"""


def build_site():
    site = tempfile.mkdtemp(prefix="w131walk", dir=os.environ.get("WALK_TMP"))   # the stub must be executable: not a noexec /tmp
    dfs = os.path.join(site, ".dfs")
    shutil.copytree(os.path.join(here, "tui-ux-review", "items"), os.path.join(dfs, "items"))
    os.makedirs(os.path.join(dfs, "artefacts"))
    shutil.copy(os.path.join(here, "..", ".dfs", "ROADMAP.md"), dfs)
    ids = sorted((f[:-3] for f in os.listdir(os.path.join(dfs, "items"))), key=lambda s: int(s[1:]))
    open(os.path.join(dfs, "order.md"), "w").write("# Order\n\nitems\n\n" + "".join("- %s\n" % i for i in ids))
    open(os.path.join(site, ".gitignore"), "w").write(".dfs/runs/\n")
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "fixture"]):
        subprocess.run(["git"] + cmd, cwd=site, check=True, stdout=subprocess.DEVNULL)
    tool = site + "-tool"          # paths.py takes the tool root to be the parent of a directory named `scripts`
    os.makedirs(tool)
    for extra in ("templates", "docs"):
        if os.path.isdir(os.path.join(scripts, "..", extra)):
            os.symlink(os.path.abspath(os.path.join(scripts, "..", extra)), os.path.join(tool, extra))
    copy = os.path.join(tool, "scripts")
    shutil.copytree(scripts, copy, ignore=shutil.ignore_patterns("__pycache__", "test_*.py", "fixtures"))
    os.chmod(os.path.join(copy, "run.sh"), 0o755)
    open(os.path.join(copy, "run.sh"), "w").write(STUB)
    return site, os.path.abspath(copy)


def serve(site, copy):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, DFS_RUN_DIR=os.path.join(site, ".dfs", "runs"))
    srv = subprocess.Popen([sys.executable, os.path.join(copy, "web.py"), "--port", str(port)], cwd=site, env=env,
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
            if os.environ.get("SIZES") and size not in os.environ["SIZES"].split(","):
                continue
            site, copy = build_site()
            srv, base = serve(site, copy)
            try:
                ctx = b.new_context(viewport=vp, device_scale_factor=1, has_touch=size != "w1280", is_mobile=size != "w1280")
                pg = ctx.new_page()
                errors, posts = [], []
                pg.on("pageerror", lambda e: errors.append(str(e)))
                pg.on("console", lambda m: errors.append("console." + m.type + ": " + m.text) if m.type in ("error", "warning") else None)
                pg.on("request", lambda r: posts.append(r.url.split("/api/")[-1]) if r.method == "POST" else None)

                def see(name, extra=None):
                    time.sleep(0.8)
                    pg.screenshot(path=os.path.join(out, "%s-%s.png" % (name, size)))
                    res = pg.evaluate(CHECKS)
                    res.update(view=name, size=size, errors=list(errors), extra=extra or {})
                    res["outside_where"] = pg.evaluate("""() => [...document.querySelectorAll('body *')].filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && (r.right > innerWidth + 1 || r.left < -1); })
                      .slice(0, 4).map(e => e.tagName.toLowerCase() + ' in ' + (e.parentElement.id || e.parentElement.className) + ' / ' + (e.closest('[id]') || {}).id + ' "' + e.textContent.trim().slice(0, 40) + '"')""")
                    findings.append(res)
                    errors.clear()

                def api(path):
                    return json.load(urllib.request.urlopen(base + path))

                def rect(sel):
                    return pg.evaluate("""s => { const e = document.querySelector(s); if (!e) return null; const r = e.getBoundingClientRect(), bar = document.querySelector('header, .bar, #bar');
                      const br = bar ? bar.getBoundingClientRect() : null;
                      return { top: Math.round(r.top), bottom: Math.round(r.bottom), w: Math.round(r.width), h: Math.round(r.height), vh: innerHeight,
                        inView: r.top >= 0 && r.bottom <= innerHeight, underBar: !!br && r.top < br.bottom && r.bottom > br.top }; }""", sel)

                # idle list, walk box shut then open
                pg.goto(base + "/#/"); time.sleep(1.0)
                see("idle-list", {"walkbox": bool(pg.query_selector("#walkbox")), "summary": pg.inner_text("#walkbox > summary") if pg.query_selector("#walkbox") else None})
                pg.click("#walkbox > summary"); time.sleep(0.5)
                fields = pg.evaluate("[...document.querySelectorAll('#walkbox label, #walkbox select, #walkbox input, #walkbox button')].map(e => e.tagName.toLowerCase() + ':' + ((e.textContent || e.id || '').trim().slice(0, 30)))")
                see("idle-open", {"fields": fields, "start": rect("[data-act=walkstart]")})
                pg.locator("[data-act=walkstart]").scroll_into_view_if_needed(); time.sleep(0.3)
                see("idle-open-start-in-view", {"start": rect("[data-act=walkstart]"), "box": rect("#walkbox")})
                # an invalid budget must send nothing
                pg.fill("#bud", "0"); posts.clear()
                pg.click("[data-act=walkstart]"); time.sleep(0.6)
                see("invalid", {"posts": list(posts), "validity": pg.evaluate("document.querySelector('#bud').validationMessage"), "on": api("/api/walk")["on"]})
                # start with a budget of one session
                pg.fill("#bud", "1"); pg.fill("#par", "1"); posts.clear()
                pg.click("[data-act=walkstart]")
                live = None
                for _ in range(40):
                    time.sleep(0.5)
                    w = api("/api/walk")
                    if [c for c in w.get("chains", []) if c.get("live")]:
                        live = w; break
                item = next((c["item"] for c in (live or {}).get("chains", []) if c.get("live")), None)
                see("running", {"posts": list(posts), "chain_item": item, "summary": pg.inner_text("#walkbox > summary"), "walk": {k: (live or {}).get(k) for k in ("on", "used", "budget", "note")}, "events": [e["text"] for e in api("/api/walk")["events"][:4]]})
                pg.click("#walkbox > summary"); time.sleep(0.5)
                see("running-open", {"line2": pg.inner_text("#walkbox .line2"), "chain_row": pg.inner_text("#walkbox .chain") if pg.query_selector("#walkbox .chain") else None,
                                     "stopchain": rect("[data-act=chainstop]"), "stopwalk": rect("[data-act=walkstop]")})
                # the Activity list inside the box: opened, with its long runner line
                if pg.query_selector("#walkbox .ev"):
                    pg.click("#walkbox details > summary"); time.sleep(0.4)
                    see("activity-open", {"summary": rect("#walkbox details > summary"), "li_overflow": pg.evaluate("[...document.querySelectorAll('#walkbox .ev li')].map(l => [Math.round(l.getBoundingClientRect().right), l.scrollWidth > l.clientWidth + 1 || l.getBoundingClientRect().right > innerWidth + 1])"),
                                          "box_scroll_w": pg.evaluate("[document.querySelector('#walkbox').scrollWidth, document.querySelector('#walkbox').clientWidth]")})
                    pg.click("#walkbox details > summary"); time.sleep(0.2)
                # the task page and the live log
                if item:
                    pg.goto(base + "/#/t/" + item); time.sleep(1.2)
                    see("runs-live", {"runs_open": pg.evaluate("document.querySelector('#runs') && document.querySelector('#runs').open"), "rows": pg.evaluate("[...document.querySelectorAll('.runrow')].map(e => e.innerText.replace(/\\s+/g, ' '))")})
                    pg.click(".runrow"); time.sleep(1.5)
                    n1 = len(pg.inner_text("#runlog").splitlines()); time.sleep(3.5)
                    n2 = len(pg.inner_text("#runlog").splitlines())
                    see("runlog-live", {"head": pg.inner_text("#runhead"), "lines_then_later": [n1, n2], "msg_form_visible": pg.evaluate("!document.querySelector('#runmsg-form').hidden"),
                                        "log_h": rect("#runlog"), "at_end": pg.evaluate("(e => e.scrollHeight - e.scrollTop - e.clientHeight < 40)(document.querySelector('#runlog'))")})
                # stop the chain from the walk box
                pg.goto(base + "/#/"); time.sleep(1.0)
                if not pg.evaluate("document.querySelector('#walkbox').open"):
                    pg.click("#walkbox > summary"); time.sleep(0.4)
                if pg.query_selector("[data-act=chainstop]"):
                    pg.locator("[data-act=chainstop]").first.click(); time.sleep(0.6)
                    see("chainstop-dialog", {"dialog_open": pg.evaluate("document.querySelector('#form').open"), "heading": pg.inner_text("#form h3")})
                    posts.clear()
                    pg.click("#form button[value=ok]"); time.sleep(2.5)
                    w = api("/api/walk")
                    see("chainstop-after", {"posts": list(posts), "on": w["on"], "note": w.get("note"), "live": [c["item"] for c in w.get("chains", []) if c.get("live")],
                                            "summary": pg.inner_text("#walkbox > summary")})
                # a walk that is still on is stopped from its own button
                if api("/api/walk")["on"]:
                    if not pg.evaluate("document.querySelector('#walkbox').open"):
                        pg.click("#walkbox > summary"); time.sleep(0.4)
                    pg.click("[data-act=walkstop]"); time.sleep(1.2)
                    see("walkstop-after", {"on": api("/api/walk")["on"]})
                # Reorder: the break buttons
                pg.goto(base + "/#/"); time.sleep(1.0)
                pg.click("[data-act=reorder]"); time.sleep(0.6)
                brk = pg.evaluate("""() => [...document.querySelectorAll('[data-act=move]')].filter(e => ['break', 'stop'].includes(e.dataset.move || e.dataset.dir || e.value || ''))
                  .map(e => { const r = e.getBoundingClientRect(); return { label: e.textContent.trim(), w: Math.round(r.width), h: Math.round(r.height) }; })""")
                allmoves = pg.evaluate("""() => [...document.querySelectorAll('[data-act=move]')].map(e => { const r = e.getBoundingClientRect(); return { label: (e.textContent || e.getAttribute('aria-label') || '').trim(), w: Math.round(r.width), h: Math.round(r.height), data: JSON.stringify(e.dataset) }; })""")
                see("reorder", {"move_buttons": len(allmoves), "break_buttons": brk[:4], "sizes": sorted({(m["label"], m["w"], m["h"]) for m in allmoves})})
                ctx.close()
            finally:
                srv.terminate()
                subprocess.run(["pkill", "-f", site + "-tool"], check=False)     # a stub chain still sleeping
                shutil.rmtree(site, ignore_errors=True)
                shutil.rmtree(site + "-tool", ignore_errors=True)
        b.close()
    json.dump(findings, open(os.path.join(out, "walk.json"), "w"), indent=1)
    for f in findings:
        bad = {k: f[k] for k in ("small", "unlabelled", "outside", "clipped", "lowContrast", "errors") if f[k]}
        print(f["size"], f["view"], "scroll" if f["scrollW"][0] > f["scrollW"][1] else "", json.dumps(f["extra"])[:420], json.dumps(bad)[:500])


main()
