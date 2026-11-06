#!/usr/bin/env python3
"""Does a long unbroken word widen a page copied from the artefact templates? (W131.51)

    FONTCONFIG_FILE=<conf naming a font dir> python3 docs/ux-templates-longword.py [TEMPLATES_DIR]

For each of `_TEMPLATE_ANSWER.html` (word in the first option label, in the second, and in the park-box text) and
`_TEMPLATE.html` (word in a `.box` span, a `<p>` and a `.legend`), put an 85-character word where a session would put a
test name or path, load the page at 320, 390, 720 and 1280 px, and print documentElement.scrollWidth against the
viewport width. `ok` means the page is no wider than the screen. Needs playwright and chromium
(see docs/ux-web-playwright.py).
"""
import os, shutil, sys

here = os.path.dirname(os.path.abspath(__file__))
tdir = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "..", "templates"))
WORD = "test_" + "a_very_long_unbroken_test_name_" * 2 + "ends_here"   # 85 characters, no space

from playwright.sync_api import sync_playwright

CASES = [
    ("_TEMPLATE_ANSWER.html", "option 1", ("> The first option", "> " + WORD)),
    ("_TEMPLATE_ANSWER.html", "option 2", ("> The second option", "> " + WORD)),
    ("_TEMPLATE_ANSWER.html", "park box", ('<span id="dfs-parks"></span>', '<span id="dfs-parks">' + WORD + '</span>')),
    ("_TEMPLATE_ANSWER.html", "paragraph", ("Your page goes here,", WORD + " Your page goes here,")),
    ("_TEMPLATE.html", "box span", ("issues reads and writes", WORD)),
    ("_TEMPLATE.html", "box b", ("<b>worker</b>", "<b>" + WORD + "</b>")),
    ("_TEMPLATE.html", "legend", ("Say here what", WORD + " Say here what")),
]


def main():
    bad = 0
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=shutil.which("chromium") or "/host-nix-bin/chromium", args=["--no-sandbox"])
        for name, label, (old, new) in CASES:
            src = open(os.path.join(tdir, name)).read()
            assert old in src, (name, old)
            html = src.replace(old, new, 1)
            if label == "park box":
                # shown, as when a node waits on the page and confirming would prune siblings
                html = html.replace('<div id="dfs-rulebox" hidden>', '<div id="dfs-rulebox">').replace('<label id="dfs-parkbox" hidden>', '<label id="dfs-parkbox">')
            row = []
            for width in (320, 390, 720, 1280):
                ctx = b.new_context(viewport={"width": width, "height": 800}, device_scale_factor=1, is_mobile=width < 720)
                pg = ctx.new_page()
                pg.set_content(html)
                pg.wait_for_timeout(150)
                sw, cw = pg.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
                row.append("%d:%s%s" % (width, "ok" if sw <= cw else "WIDE", "" if sw <= cw else "(%d>%d)" % (sw, cw)))
                bad += sw > cw
                ctx.close()
            print("%-22s %-10s %s" % (name, label, "  ".join(row)))
        b.close()
    print("wide cells:", bad)


main()
