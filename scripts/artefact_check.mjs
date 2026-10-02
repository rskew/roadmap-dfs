// Render one roadmap artefact headless and check it is READABLE, then screenshot
// it so the agent that wrote it can look at what it actually made.
//
// The problem this exists for: an agent writing a laid-out diagram never sees the
// render, so overlapping boxes and clipped labels fail SILENTLY and it produces a
// confident-looking picture the author then has to decode. That costs more than the
// prose the diagram replaced, which is the whole benefit gone. So the layout gets a
// machine answer first (below) and an image second — read the image once it is clean
// rather than on every iteration, since reading it costs context.
//
// Run through artefact_check.sh, which supplies node and playwright.
import { createRequire } from 'node:module';
// NODE_PATH is CJS-only resolution, and the wrapper is what supplies it, so the
// import has to go through require rather than a bare ESM specifier.
const { chromium } = createRequire(import.meta.url)('playwright');
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import fs from 'node:fs';

const args = process.argv.slice(2);
const VALUED = new Set(['--out', '--width', '--height', '--min-font']);
const opt = (name, dflt) => {
  const i = args.indexOf(name);
  return i < 0 ? dflt : args[i + 1];
};
const positional = [];
for (let i = 0; i < args.length; i++) {
  if (VALUED.has(args[i])) i++;
  else if (!args[i].startsWith('--')) positional.push(args[i]);
}
const target = positional[0];
if (!target) {
  console.error('usage: artefact_check.sh <artefact.html> [--out <png>] [--width N] [--height N] [--min-font N]');
  process.exit(2);
}
const file = path.resolve(target);
if (!fs.existsSync(file)) {
  console.error(`dfs_artefact_check: no such file, ${file}`);
  process.exit(2);
}
const width = Number(opt('--width', 1200));
const height = Number(opt('--height', 900));
const minFont = Number(opt('--min-font', 12));
// A FIXED directory, not TMPDIR: the e2e shell sets TMPDIR to a per-run
// nix-shell path, so the screenshot would land somewhere with a different name
// every time and the agent would have to parse this output to find its own image.
const OUT_DIR = '/tmp/artefact-check';
const out = path.resolve(opt('--out', path.join(OUT_DIR, path.basename(file, '.html') + '.png')));
fs.mkdirSync(path.dirname(out), { recursive: true });

const fails = [];
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width, height } });

// A blank page is the worst failure available here — it looks like the agent
// produced nothing — and its two usual causes are a script that threw and a
// resource that never arrived, so both are watched rather than inferred.
page.on('console', (m) => {
  if (m.type() === 'error') fails.push(`console: ${m.text().slice(0, 200)}`);
});
page.on('pageerror', (e) => fails.push(`console: ${String(e).slice(0, 200)}`));
page.on('requestfailed', (r) =>
  fails.push(`request: ${r.url().slice(0, 120)} failed (${r.failure()?.errorText || 'unknown'})`),
);

await page.goto(pathToFileURL(file).href, { waitUntil: 'load' });
await page.waitForTimeout(200);

const report = await page.evaluate((minFont) => {
  const out = { fails: [], title: document.title || '', w: 0, h: 0 };
  const doc = document.documentElement;
  out.w = doc.scrollWidth;
  out.h = doc.scrollHeight;

  const name = (el) => {
    const n = el.getAttribute('data-node') || el.id || (el.textContent || '').trim().replace(/\s+/g, ' ');
    return n ? n.slice(0, 32) : el.tagName.toLowerCase();
  };

  // Rendered nothing at all.
  const text = (document.body.innerText || '').trim();
  if (!text && !document.querySelector('svg, canvas, img')) out.fails.push('blank: the page rendered no text and no graphic');

  // Boxes that sit on top of each other. Nesting is legitimate (a label inside a
  // box), so only siblings-by-containment are compared.
  const nodes = [...document.querySelectorAll('[data-node]')];
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i], b = nodes[j];
      if (a.contains(b) || b.contains(a)) continue;
      const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
      const ox = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
      const oy = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
      if (ox > 2 && oy > 2) {
        out.fails.push(`overlap: "${name(a)}" and "${name(b)}" overlap by ${Math.round(ox)}x${Math.round(oy)} px`);
      }
    }
  }

  // Text that does not fit what holds it: clipped when the box hides it, spilling
  // when it does not. Both are unreadable in a still picture.
  for (const el of document.querySelectorAll('*')) {
    const cs = getComputedStyle(el);
    if (cs.display === 'inline' || cs.display === 'none' || cs.visibility === 'hidden') continue;
    const own = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!own) continue;
    const overX = el.scrollWidth - el.clientWidth, overY = el.scrollHeight - el.clientHeight;
    if (el.clientWidth && (overX > 1 || overY > 1)) {
      const hidden = /hidden|clip|auto|scroll/.test(cs.overflow + cs.overflowX + cs.overflowY);
      out.fails.push(
        `${hidden ? 'clipped' : 'spills'}: "${name(el)}" text does not fit (by ${Math.max(overX, overY)}px)`,
      );
    }
    const fs = parseFloat(cs.fontSize);
    if (fs && fs < minFont) out.fails.push(`font: ${fs}px text in "${name(el)}" (minimum ${minFont})`);
  }

  // A diagram wider than the window is read by scrolling, which defeats it.
  if (doc.scrollWidth > doc.clientWidth + 1) {
    out.fails.push(`page: content is ${doc.scrollWidth}px wide against a ${doc.clientWidth}px viewport`);
  }
  return out;
}, minFont);

fails.push(...report.fails);
await page.screenshot({ path: out, fullPage: true });
await browser.close();

console.log(`dfs_artefact_check: ${path.relative(process.env.ARTEFACT_REPO_ROOT || process.cwd(), file)}`);
console.log(`  title: ${report.title || '(none)'}`);
console.log(`  viewport ${width}x${height}, content ${report.w}x${report.h}`);
// Deduplicated: one mistake in a shared rule reports once per element otherwise,
// and a hundred identical lines hide the other four failures.
const seen = new Map();
for (const f of fails) seen.set(f, (seen.get(f) || 0) + 1);
for (const [f, n] of seen) console.log(`FAIL ${f}${n > 1 ? ` (x${n})` : ''}`);
console.log(`screenshot: ${out}`);
console.log(fails.length ? `${fails.length} failure${fails.length === 1 ? '' : 's'}` : 'clean');
process.exit(fails.length ? 1 : 0);
