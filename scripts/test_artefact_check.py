"""artefact_check.sh fails a slider that moves nothing but its own number."""
import subprocess
import tempfile
import unittest
from pathlib import Path

SH = Path(__file__).resolve().parent / "artefact_check.sh"

PAGE = """<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<p id="r">%s</p>
<label for="k">How many: <output id="o">3</output></label>
<input id="k" type="range" min="1" max="10" value="3" style="min-height:44px">
<script>
  var k = document.getElementById('k');
  k.addEventListener('input', function () {
    document.getElementById('o').textContent = k.value;%s
  });
</script>"""


class ASliderMustChangeWhatTheReaderSees(unittest.TestCase):
    def run_check(self, page):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "page.html"
            f.write_text(page)
            r = subprocess.run(["bash", str(SH), str(f), "--out", str(Path(d) / "page.png")],
                               capture_output=True, text=True, timeout=300)
        out = r.stdout + r.stderr
        if "dfs_artefact_check:" not in out:
            self.skipTest("no browser to render with: " + out[-200:])
        return out

    def test_a_slider_that_changes_only_its_own_readout_is_refused(self):
        out = self.run_check(PAGE % ("The work goes ahead.", ""))
        self.assertIn('slider: "k"', out)

    def test_a_slider_that_changes_the_result_is_accepted(self):
        out = self.run_check(PAGE % (
            "With 3 the work goes ahead.",
            "document.getElementById('r').textContent = 'With ' + k.value + ' the work goes ahead.';"))
        self.assertNotIn("slider:", out)

    def test_a_slider_whose_redraw_waits_for_a_frame_or_timer_is_accepted(self):
        out = self.run_check(PAGE % (
            "With 3 the work goes ahead.",
            "setTimeout(function () { requestAnimationFrame(function () { "
            "document.getElementById('r').textContent = 'With ' + k.value + ' the work goes ahead.'; }); }, 30);"))
        self.assertNotIn("slider:", out)

    def test_a_slider_that_sets_a_variable_on_the_root_element_is_accepted(self):
        page = (PAGE % ("The work goes ahead.",
                        "document.documentElement.style.setProperty('--w', k.value * 10 + '%');")
                ).replace("<p id=\"r\">", "<style>#bar{height:20px;background:#07c;width:var(--w,30%)}</style>"
                          "<div id=\"bar\"></div><p id=\"r\">")
        self.assertNotIn("slider:", self.run_check(page))


if __name__ == "__main__":
    unittest.main()
