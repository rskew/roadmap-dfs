#!/usr/bin/env python3
"""Pages where an element with the `hidden` attribute is drawn anyway (W131.38).

    FONTCONFIG_FILE=<conf> python3 docs/ux-artefacts-hidden.py [ARTEFACT_DIR]

A page's own CSS such as `.st { display: flex }` beats the browser's `[hidden] { display: none }`, so a panel a button
should show or hide is drawn all the time and the button does nothing. The HTML and a text dump both read as "the
button swaps two panels"; only the drawn page shows both. Prints the pages (answer form excluded) with such an element
drawn at load, from the file with no server, at 390 px.
"""
import glob, os, shutil, sys
from playwright.sync_api import sync_playwright
art = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".dfs", "artefacts"))
JS = """() => [...document.querySelectorAll('[hidden]')].filter(e => !e.closest('#dfs-answer') && !(e.parentElement && e.parentElement.closest('[hidden]'))
  && getComputedStyle(e).display !== 'none' && e.getBoundingClientRect().height > 0).map(e => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (e.className ? '.' + e.className : '')).slice(0, 4)"""
bad = []
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=shutil.which("chromium"), args=["--no-sandbox"])
    pg = b.new_context(viewport={"width": 390, "height": 844}).new_page()
    pages = sorted(glob.glob(os.path.join(art, "*.html")))
    for f in pages:
        pg.goto("file://" + f)
        pg.wait_for_timeout(150)
        n = pg.evaluate(JS)
        if n:
            bad.append((os.path.basename(f)[:8], n))
    b.close()
print("%d of %d pages draw an element that has the hidden attribute" % (len(bad), len(pages)))
for k, n in bad:
    print(k, n)
