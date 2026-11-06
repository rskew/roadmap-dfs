"""The artefact templates are mobile-first: what a session copies is what the author reads on a phone."""
import re
import sys
import unittest
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


class MobileFirst(unittest.TestCase):
    def pages(self):
        return sorted(TEMPLATES.glob("_TEMPLATE*.html"))

    def test_there_are_templates(self):
        self.assertGreaterEqual(len(self.pages()), 2)

    def test_each_declares_a_device_width_viewport(self):
        for p in self.pages():
            self.assertRegex(p.read_text(), r'<meta name="viewport" content="[^"]*width=device-width',
                             p.name)

    def test_each_widens_with_min_width_and_never_narrows_with_max_width(self):
        for p in self.pages():
            css = p.read_text()
            self.assertNotIn("max-width:", re.sub(r"max-width: *\d+(ch|px)", "", css), p.name)
            self.assertNotRegex(css, r"@media *\(max-width", p.name)

    def test_the_diagram_template_has_no_fixed_pixel_width_box(self):
        css = (TEMPLATES / "_TEMPLATE.html").read_text()
        self.assertNotRegex(css, r"(?<!-)width: *\d{3,}px", "a box wider than a phone")
        self.assertNotIn("position: absolute", css)

    def test_both_templates_wrap_a_word_too_long_for_a_phone_in_prose_only(self):
        """Four W75 pages drew wider than a phone because one test name would not wrap. `anywhere` on the body cured
        that but split 113 ordinary words on 16 of the 175 pages (74 in table cells): the rule is break-word, on prose."""
        for name in ("_TEMPLATE.html", "_TEMPLATE_ANSWER.html"):
            css = (TEMPLATES / name).read_text()
            body = css[css.index("  body {"):]
            self.assertNotIn("overflow-wrap", body[:body.index("}")], name + ": the body rule reaches tables and grids")
            self.assertNotRegex(css, r"(?m)^\s*(body|p|table|td|th|\*)[^{\n]*\{[^}]*overflow-wrap: anywhere", name)
            self.assertRegex(css, r"(?m)^\s*p, li, h1, h2, h3, \.box, \.legend, \.lede \{ overflow-wrap: break-word; \}", name)

    def test_a_flex_item_of_the_templates_can_be_narrower_than_its_longest_word(self):
        """`break-word` cannot narrow a flex item below its longest word, so an option label with a test name widened
        the answer page to 750 px on a phone, and the diagram's `.box` to 987 px at 720 (docs/ux-templates-longword.py)."""
        answer = (TEMPLATES / "_TEMPLATE_ANSWER.html").read_text()
        for sel in (r"\.dfs-answer \.opt", r"\.dfs-answer #dfs-parkbox"):
            rule = re.search(sel + r" \{([^}]*)\}", answer)
            self.assertIsNotNone(rule, sel)
            self.assertIn("overflow-wrap: anywhere", rule.group(1), sel + ": its text is a flex item")
        diagram = (TEMPLATES / "_TEMPLATE.html").read_text()
        self.assertRegex(diagram, r"\.box \{ flex: 1 1 0; min-width: 0;", "the row of boxes from 720 px")

    def test_the_answer_template_has_no_slider_to_leave_pointless_in_a_copy(self):
        page = (TEMPLATES / "_TEMPLATE_ANSWER.html").read_text()
        self.assertNotRegex(page, r"type=[\"']?range", "a stock slider is copied into every page")

    def test_the_answer_template_can_confirm_or_refute_its_node(self):
        page = (TEMPLATES / "_TEMPLATE_ANSWER.html").read_text()
        self.assertIn("/artefact-state/", page)
        for verdict in ("'confirmed'", "'refuted'"):
            self.assertIn("rule(%s)" % verdict, page)
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import check as dfs_check
        import tree as dfs_tree
        self.assertTrue(dfs_check.can_rule(page), "what check.py demands of a Hypothesis's page")
        self.assertTrue(dfs_tree.can_answer(page), "what a raise's answering page must do")
        self.assertTrue(dfs_tree.has_placeholder_option(page), "the template's options are placeholders to replace")
        self.assertFalse(dfs_tree.has_placeholder_option(
            page.replace("Take the first option: what it", "Keep WAL: it").replace("Take the second option: what it", "Drop WAL: it")))
        self.assertIn("j.node", page, "the buttons show only while a node waits on this page")

    def test_the_answer_form_stands_alone_so_the_page_above_it_is_free(self):
        # A session replaces everything between <h1> and the form; what the form's script
        # needs must all be inside the form, or a free page breaks it.
        page = (TEMPLATES / "_TEMPLATE_ANSWER.html").read_text()
        mark = "<!-- ===== The answer form: keep from here to the end as it is"
        self.assertIn(mark, page)
        form = page[page.index(mark):]
        script = form[form.index("<script>"):]
        ids = set(re.findall(r"\$\('([\w-]+)'\)", script))
        self.assertGreater(len(ids), 5)
        for i in ids:
            self.assertRegex(form, r'id="%s"' % re.escape(i), "the form's script needs #%s" % i)
        for i in re.findall(r'\bid="([\w-]+)"', form):
            self.assertTrue(i.startswith("dfs-"), "#%s could clash with an id on the page above" % i)
        self.assertNotRegex(script, r"querySelector(All)?\(['\"][^'\"]*#(?!dfs-)", "a lookup by an unprefixed id")
        self.assertIn('name="dfs-option"', form)
        self.assertIn("dfsAnswer", script, "the page's own controls can pick an option")
        self.assertIn(".dfs-answer {", form, "the form carries its own style")


if __name__ == "__main__":
    unittest.main()
