"""The artefact templates are mobile-first: what a session copies is what the author reads on a phone."""
import re
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


if __name__ == "__main__":
    unittest.main()
