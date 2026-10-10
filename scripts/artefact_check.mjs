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
const VALUED = new Set(['--out', '--width', '--height', '--min-font', '--mobile-width']);
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
  console.error('usage: artefact_check.sh <artefact.html> [--out <png>] [--width N] [--height N] [--min-font N] [--mobile-width N]');
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
// The author reads an artefact on a phone first, so every page is rendered a second time
// at a phone's width (360 is the common small Android; a page that fits there fits wider).
const mobileWidth = Number(opt('--mobile-width', 360));
const MOBILE_HEIGHT = 740;
// Smallest height of a thing a thumb has to hit. WCAG's floor is 24px and Apple's guide 44px;
// 32 catches the default-sized button and the bare checkbox without nagging at a link in prose.
const MIN_TARGET = 32;
// A FIXED directory, not TMPDIR: the e2e shell sets TMPDIR to a per-run
// nix-shell path, so the screenshot would land somewhere with a different name
// every time and the agent would have to parse this output to find its own image.
const OUT_DIR = '/tmp/artefact-check';
const out = path.resolve(opt('--out', path.join(OUT_DIR, path.basename(file, '.html') + '.png')));
fs.mkdirSync(path.dirname(out), { recursive: true });

const fails = [];
const browser = await chromium.launch();

// One render of the page at one viewport. `mobile` is a touch screen of `w` px, where a page
// WITHOUT a width=device-width viewport meta is laid out at 980px and shrunk, which is how a
// fixed-width page hides its overflow, so the meta is checked on its own below.
async function pass(w, h, mobile) {
  const tag = mobile ? `[${w}px] ` : '';
  const page = await browser.newPage(
    mobile
      ? { viewport: { width: w, height: h }, hasTouch: true, isMobile: true, deviceScaleFactor: 2 }
      : { viewport: { width: w, height: h } },
  );
  // A blank page is the worst failure available here — it looks like the agent
  // produced nothing — and its two usual causes are a script that threw and a
  // resource that never arrived, so both are watched rather than inferred.
  page.on('console', (m) => {
    if (m.type() === 'error') fails.push(`${tag}console: ${m.text().slice(0, 200)}`);
  });
  page.on('pageerror', (e) => fails.push(`${tag}console: ${String(e).slice(0, 200)}`));
  page.on('requestfailed', (r) =>
    fails.push(`${tag}request: ${r.url().slice(0, 120)} failed (${r.failure()?.errorText || 'unknown'})`),
  );

  await page.goto(pathToFileURL(file).href, { waitUntil: 'load' });
  await page.waitForTimeout(200);

  const report = await page.evaluate(([minFont, mobile, minTarget]) => {
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
    // A phone has no hover and no pointer finer than a fingertip: a control that is too short
    // to hit is as unusable there as clipped text is in a picture.
    if (mobile) {
      const sel = 'button, input:not([type=hidden]), select, textarea, summary, [role=button]';
      for (const el of document.querySelectorAll(sel)) {
        const r = el.getBoundingClientRect();
        if (r.width && r.height && r.height < minTarget) {
          out.fails.push(`target: "${name(el)}" is ${Math.round(r.height)}px high (minimum ${minTarget})`);
        }
      }
    }
    return out;
  }, [minFont, mobile, MIN_TARGET]);

  for (const f of report.fails) fails.push(tag + f);
  // A slider is there to change what the reader sees: a result sentence, a drawing, a size. One
  // that moves only its own number is noise the author has to work out is noise, so each range
  // input is dragged to its minimum and then its maximum and the page, less the slider's own label
  // and <output>, is compared. A redraw may wait for a frame or a timer, so the page settles
  // before each snapshot; and the snapshot takes in the root element's attributes and each
  // element's size and look, since a bar's width can ride on a CSS variable set on <html>.
  if (!mobile) {
    const dead = await page.evaluate(async () => {
      const settle = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 120))));
      const attrs = (e) => [...e.attributes].map((a) => a.name + '=' + a.value).join(';');
      const snap = (id) => {
        const own = new Set();
        if (id) document.querySelectorAll(`label[for="${CSS.escape(id)}"]`).forEach((e) => own.add(e));
        document.querySelectorAll('output').forEach((e) => own.add(e));
        const c = document.body.cloneNode(true);
        c.querySelectorAll('output, script, style').forEach((e) => e.remove());
        if (id) c.querySelectorAll(`label[for="${CSS.escape(id)}"]`).forEach((e) => e.remove());
        const look = [];
        for (const e of document.body.querySelectorAll('*')) {
          if (own.has(e) || [...own].some((o) => e.contains(o))) continue;
          const cs = getComputedStyle(e), r = e.getBoundingClientRect();
          look.push([Math.round(r.width * 10), Math.round(r.height * 10), cs.color, cs.backgroundColor,
            cs.opacity, cs.display, cs.visibility, cs.transform].join(','));
        }
        const canvases = [...document.querySelectorAll('canvas')].map((e) => {
          try { return e.toDataURL(); } catch (_) { return ''; }
        });
        return [attrs(document.documentElement), attrs(document.body), c.innerHTML, look.join('|'), canvases.join('|')].join('\n');
      };
      const set = (el, v) => {
        el.value = v;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
      };
      const dead = [];
      for (const el of document.querySelectorAll('input[type=range]')) {
        const keep = el.value;
        set(el, el.min || 0);
        await settle();
        const lo = snap(el.id);
        set(el, el.max || 100);
        await settle();
        const hi = snap(el.id);
        set(el, keep);
        await settle();
        if (lo === hi) dead.push(el.id || el.getAttribute('aria-label') || 'a slider');
      }
      return dead;
    });
    for (const d of dead) fails.push(`${tag}slider: "${d}" changes nothing on the page but its own number; give it an effect the reader sees or take it out`);
  }
  const meta = await page.evaluate(() => {
    const m = document.querySelector('meta[name=viewport]');
    return m ? m.getAttribute('content') || '' : null;
  });
  if (mobile && (meta === null || !/width\s*=\s*device-width/.test(meta))) {
    fails.push(`${tag}viewport: no <meta name="viewport" content="width=device-width, initial-scale=1">, so a phone lays the page out at 980px and shrinks it`);
  }
  return { page, report };
}

const desk = await pass(width, height, false);
await desk.page.screenshot({ path: out, fullPage: true });
const report = desk.report;
let mobile = null;
if (mobileWidth) {
  const mob = await pass(mobileWidth, MOBILE_HEIGHT, true);
  await mob.page.screenshot({ path: out.replace(/\.png$/i, '') + '-mobile.png', fullPage: true });
  mobile = mob.report;
}
await browser.close();

console.log(`dfs_artefact_check: ${path.relative(process.env.ARTEFACT_REPO_ROOT || process.cwd(), file)}`);
console.log(`  title: ${report.title || '(none)'}`);
console.log(`  viewport ${width}x${height}, content ${report.w}x${report.h}`);
if (mobile) console.log(`  phone ${mobileWidth}x${MOBILE_HEIGHT}, content ${mobile.w}x${mobile.h}`);
// Deduplicated: one mistake in a shared rule reports once per element otherwise,
// and a hundred identical lines hide the other four failures.
const seen = new Map();
for (const f of fails) seen.set(f, (seen.get(f) || 0) + 1);
for (const [f, n] of seen) console.log(`FAIL ${f}${n > 1 ? ` (x${n})` : ''}`);
console.log(`screenshot: ${out}${mobileWidth ? ` and ${out.replace(/\.png$/i, '')}-mobile.png` : ''}`);
console.log(fails.length ? `${fails.length} failure${fails.length === 1 ? '' : 's'}` : 'clean');
process.exit(fails.length ? 1 : 0);
