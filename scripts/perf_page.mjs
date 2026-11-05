// Time the web page's client in headless chromium: the list and a task view loading, and
// what a push (a file change the server tells the page about) costs the open page.
//
//   perf_page.mjs <url> <touch-file> [--task W130] [--other <touch-file>] [--cpu 4] [--runs 3]
//
// <url> is a running `web.py` (serve a COPY of a roadmap, since the push step rewrites a
// file's mtime). <touch-file> is a task file of the open task, <other> one of another task.
// Run it through the playwright wrapper, as artefact_check.sh does (node and NODE_PATH are
// read out of the wrapper). Prints one JSON object of milliseconds and counts.
import { createRequire } from 'node:module';
const { chromium } = createRequire(import.meta.url)('playwright');
import fs from 'node:fs';

const args = process.argv.slice(2);
const opt = (n, d) => { const i = args.indexOf(n); return i < 0 ? d : args[i + 1]; };
const pos = args.filter((a, i) => !a.startsWith('--') && !(i > 0 && args[i - 1].startsWith('--')));
const [url, ownFile] = pos;
const TASK = opt('--task', 'W130');
const OTHER = opt('--other', '');
const CPU = Number(opt('--cpu', '1'));
const RUNS = Number(opt('--runs', '3'));
if (!url || !ownFile) { console.error('usage: perf_page.mjs <url> <touch-file> [--task W130] [--other <file>] [--cpu 4] [--runs 3]'); process.exit(2); }

// In the page before its own script: record long tasks, and wrap the draw and render
// functions (classic-script function declarations are window properties, and the page's
// own calls go through them) with a start and end time. A draw is timed to the next
// frame, so style and layout are in it.
const INIT = () => {
  const T = window.__t = { long: [], draws: [], renders: [], first: {}, api: [] };
  try { new PerformanceObserver(l => l.getEntries().forEach(e => T.long.push([Math.round(e.startTime), Math.round(e.duration)]))).observe({ type: 'longtask', buffered: true }); } catch (e) {}
  const wrap = (name, into) => {
    const f = window[name];
    window[name] = function () {
      const t0 = performance.now(); const r = f.apply(this, arguments);
      const t1 = performance.now(); let h = document.body.offsetHeight; void h;      // forces layout
      const t2 = performance.now(); into.push({ name, at: Math.round(t0), js: +(t1 - t0).toFixed(1), layout: +(t2 - t1).toFixed(1) });
      return r;
    };
  };
  window.addEventListener('DOMContentLoaded', () => {
    T.first.dcl = Math.round(performance.now());
    wrap('drawTasks', T.draws); wrap('drawTask', T.draws);
    const f = window.render;
    window.render = async function () { const t0 = performance.now(); try { return await f.apply(this, arguments); } finally { T.renders.push({ at: Math.round(t0), ms: +(performance.now() - t0).toFixed(1) }); } };
    new MutationObserver(() => {
      for (const [k, sel] of [['item', '#view .item'], ['row', '#view .row']])
        if (!(k in T.first) && document.querySelector(sel)) T.first[k] = Math.round(performance.now());
    }).observe(document.documentElement, { childList: true, subtree: true });
  });
};

const median = a => { const s = [...a].sort((x, y) => x - y); return s[Math.floor(s.length / 2)]; };

async function once(browser, which) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  if (CPU > 1) await (await ctx.newCDPSession(page)).send('Emulation.setCPUThrottlingRate', { rate: CPU });
  await page.addInitScript(INIT);
  const reqs = [];
  page.on('request', r => { const u = new URL(r.url()); if (u.pathname.startsWith('/api/') && u.pathname !== '/api/events') reqs.push({ at: Date.now(), path: u.pathname }); });
  const hash = which === 'task' ? `#/t/${TASK}` : '#/';
  await page.goto(url + '/' + hash, { waitUntil: 'load' });
  await page.waitForSelector(which === 'task' ? '#view .row' : '#view .item', { timeout: 30000 });
  await page.waitForTimeout(600);
  const load = await page.evaluate(() => JSON.parse(JSON.stringify(window.__t)));
  const calls = await page.evaluate(() => performance.getEntriesByType('resource').filter(e => e.name.includes('/api/') && !e.name.includes('/events')).map(e => [new URL(e.name).pathname.replace(/\/api\/task\/.*/, '/api/task/<id>'), Math.round(e.startTime), Math.round(e.responseStart - e.requestStart), Math.round(e.responseEnd - e.responseStart), e.encodedBodySize]));
  const nav = await page.evaluate(() => { const n = performance.getEntriesByType('navigation')[0]; return { responseEnd: Math.round(n.responseEnd), load: Math.round(n.loadEventEnd) }; });
  const out = { which, first: load.first, nav, calls, draws: load.draws, longAtLoad: load.long.reduce((a, [, d]) => a + d, 0), longTasksAtLoad: load.long.length, domNodes: await page.evaluate(() => document.getElementsByTagName('*').length) };
  // A push. Wait out the server's one-second watch and the page's 120 ms debounce.
  if (which === 'task') {
    for (const [label, file] of [['own', ownFile], ['other', OTHER]]) {
      if (!file) continue;
      await page.evaluate(() => { window.__t.draws.length = 0; window.__t.renders.length = 0; window.__t.long.length = 0; performance.clearResourceTimings(); });
      const n0 = reqs.length, t0 = Date.now();
      const now = new Date(); fs.utimesSync(file, now, now);
      await page.waitForTimeout(3000);
      const t = await page.evaluate(() => JSON.parse(JSON.stringify(window.__t)));
      out['push_' + label] = {
        requests: reqs.slice(n0).map(r => r.path.replace(/\/api\/task\/.*/, '/api/task/<id>')),
        renders: t.renders.map(r => r.ms), draws: t.draws.map(d => d.name + ' ' + (d.js + d.layout).toFixed(1)),
        calls: await page.evaluate(() => performance.getEntriesByType('resource').filter(e => e.name.includes('/api/') && !e.name.includes('/events')).map(e => [new URL(e.name).pathname.replace(/\/api\/task\/.*/, '/api/task/<id>'), Math.round(e.startTime), Math.round(e.responseStart - e.requestStart)])),
        longTaskMs: t.long.reduce((a, [, d]) => a + d, 0), longTasks: t.long.length,
      };
    }
  }
  await ctx.close();
  return out;
}

const browser = await chromium.launch();
const res = { url, cpu: CPU, runs: RUNS, list: [], task: [] };
for (let i = 0; i < RUNS; i++) { res.list.push(await once(browser, 'list')); res.task.push(await once(browser, 'task')); }
await browser.close();
const sum = (rs, f) => median(rs.map(f));
res.summary = {
  list_first_draw_ms: sum(res.list, r => r.first.item), list_drawTasks_ms: sum(res.list, r => r.draws.filter(d => d.name === 'drawTasks').reduce((a, d) => a + d.js + d.layout, 0)),
  list_dom_nodes: res.list[0].domNodes, list_long_ms: sum(res.list, r => r.longAtLoad),
  task_first_draw_ms: sum(res.task, r => r.first.row), task_drawTask_ms: sum(res.task, r => r.draws.filter(d => d.name === 'drawTask').reduce((a, d) => a + d.js + d.layout, 0)),
  task_dom_nodes: res.task[0].domNodes, task_long_ms: sum(res.task, r => r.longAtLoad),
};
console.log(JSON.stringify(res, null, 1));
