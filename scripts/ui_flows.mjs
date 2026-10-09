// The web page's core flows, driven in a real browser: node ui_flows.mjs <flow> <url> <fixture-dir>
// Run by test_ui.py, which builds the fixture, serves it, and judges what the flow wrote to
// disk. A flow throws on the first thing that is wrong, and prints nothing on success.
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";

const require = createRequire(import.meta.url);
const { chromium, devices } = require("playwright");
const [flow, URL_, DIR] = process.argv.slice(2);

function assert(cond, msg) { if (!cond) throw new Error(msg); }
function same(a, b, msg) { assert(JSON.stringify(a) === JSON.stringify(b), `${msg}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`); }

function chromiumPath() {
  if (process.env.DFS_UI_CHROMIUM) return process.env.DFS_UI_CHROMIUM;
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH;
  if (root && fs.existsSync(root)) {
    for (const d of fs.readdirSync(root).filter(d => d.startsWith("chromium-")).sort().reverse()) {
      const p = path.join(root, d, "chrome-linux", "chrome");
      if (fs.existsSync(p)) return p;
    }
  }
  return undefined;
}

const PHONE = { ...devices["iPhone 13"] };
const NARROW = { viewport: { width: 320, height: 640 }, hasTouch: true, isMobile: true };
const DESKTOP = { viewport: { width: 1280, height: 800 } };

// ── what a page is audited for, in either theme ───────────────────────────────────────
const AUDIT = () => {
  const lum = c => { const [r, g, b] = c.map(v => { v /= 255; return v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }); return .2126 * r + .7152 * g + .0722 * b; };
  const parse = s => { const m = s.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[ ,\/]+/).map(Number); return { c: p.slice(0, 3), a: p.length > 3 ? p[3] : 1 }; };
  const bgOf = el => { const stack = []; for (let e = el; e; e = e.parentElement) { const p = parse(getComputedStyle(e).backgroundColor); if (p && p.a > 0) { stack.push(p); if (p.a >= 1) break; } } let base = [255, 255, 255]; for (const p of stack.reverse()) base = base.map((v, i) => Math.round(p.c[i] * p.a + v * (1 - p.a))); return base; };
  const out = { overflow: document.documentElement.scrollWidth > innerWidth + 1, low: [], small: [] };
  const seen = new Set();
  for (const el of document.querySelectorAll("body *")) {
    const cs = getComputedStyle(el); if (cs.visibility === "hidden" || cs.display === "none" || +cs.opacity === 0) continue;
    const r = el.getBoundingClientRect(); if (r.width < 1 || r.height < 1) continue;
    if ([...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) {
      const fg = parse(cs.color), bg = bgOf(el);
      if (fg && !el.closest(":disabled,[disabled]")) {
        const L1 = lum(fg.c), L2 = lum(bg), cr = (Math.max(L1, L2) + .05) / (Math.min(L1, L2) + .05);
        const big = parseFloat(cs.fontSize) >= 24 || (parseFloat(cs.fontSize) >= 18.66 && +cs.fontWeight >= 700);
        const key = el.tagName + el.className + cs.color + bg.join();
        if (cr < (big ? 3 : 4.5) && !seen.has(key)) { seen.add(key); out.low.push(`${el.className || el.tagName} "${el.textContent.trim().slice(0, 24)}" ${cr.toFixed(1)}:1`); }
      }
    }
    if (el.matches("button,a,select,input,textarea,[role=button]") && (r.width < 40 || r.height < 40)
        && !(el.tagName === "A" && cs.display === "inline")) {      // a link in a sentence is as big as its words
      const k = el.tagName + el.className + el.id;
      if (!seen.has(k)) { seen.add(k); out.small.push(`${el.tagName}.${el.className || el.id} ${Math.round(r.width)}x${Math.round(r.height)} "${(el.textContent || "").trim().slice(0, 18)}"`); }
    }
  }
  return out;
};

const PAGES = [];
async function open(browser, opts, scheme = "light") {
  const ctx = await browser.newContext({ ...opts, colorScheme: scheme });
  const page = await ctx.newPage();
  PAGES.push(page);
  const errors = [];
  page.on("pageerror", e => errors.push(String(e)));
  // What an artefact's own page logs is not the web page's: the fixture's tries the API on purpose, from the
  // opaque origin `null`, and the browser reports the refusal against the API's address.
  const theirs = m => /\/artefacts\//.test(m.location().url || "") || /origin 'null'/.test(m.text()) || /\/api\/walk\/start$/.test(m.location().url || "");
  page.on("console", m => { if (m.type() === "error" && !theirs(m)) errors.push(m.text()); });
  await page.goto(URL_);
  await page.waitForSelector(".item");
  return { ctx, page, errors };
}
const bg = page => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
async function task(page, id) { await page.click(`.item[data-id=${id}]`); await page.waitForSelector(".row"); }
// Back to the list from wherever: the bar's Back on a phone, the header's on a wide screen.
async function toList(page) {
  for (let i = 0; i < 3 && !(await page.$(".item")); i++) {
    await page.evaluate(() => document.querySelector("#sheet [data-act=close]")?.click());
    const b = (await page.$("#tabs [data-act=up]")) || (await page.$("#back:not([hidden])"));
    if (b && await b.isVisible()) await b.click();
    await page.waitForTimeout(150);
  }
  await page.waitForSelector(".item");
}
async function dialog(page) { await page.waitForSelector("dialog[open]"); }
async function openWalk(page) {
  if (!(await page.$("#walkbox[open]"))) await page.click("#walkbox summary");
  await page.waitForSelector("#nextsel");
}

const FLOWS = {
  // The list, its filter, the project's name, and nothing in the console.
  async list(b) {
    const { page, errors } = await open(b, PHONE);
    assert((await page.$$(".item")).length === 9, "nine tasks in the fixture");
    const name = fs.readFileSync(path.join(DIR, ".dfs", "title"), "utf8").trim();
    same(await page.title(), name, "the tab is named after the project");
    assert((await page.textContent("#title")).startsWith(name), "so is the header");
    // A done task is one line: its goal is cut, not wrapped, and no why sits under it.
    for (const row of await page.$$(".item.done")) {
      same(await row.$eval(".goal", g => getComputedStyle(g).whiteSpace), "nowrap", "a done task's goal is one line");
      assert(!(await row.$(".why")), "a done task has no why line");
    }
    await page.click("[data-act=only][data-v='1']");
    const ids = await page.$$eval(".item", a => a.map(x => x.dataset.id));
    same(ids, ["W2", "W4", "W9"], "needs you: the ones that wait on the author");
    same(errors, [], "no page errors");
  },

  // A task: its tree, folds, and the blocked and tinted rows.
  async task_tree(b) {
    const { page, errors } = await open(b, PHONE);
    assert((await page.getAttribute(".item[data-id=W9]", "class")).includes("blocks"), "a blocked task is tinted");
    assert((await page.getAttribute(".item[data-id=W1]", "class")).includes("assumes"), "one with an assumption is too");
    await task(page, "W9");
    assert((await page.$$(".row")).length >= 8, "the tree is there");
    assert(await page.$(".row.hasraise"), "a raise marks its row");
    const before = (await page.$$(".row")).length;
    await page.click(".row .fold:not(.none) >> nth=0");
    await page.waitForFunction(n => document.querySelectorAll(".row").length < n, before, { timeout: 4000 })
      .catch(() => { throw new Error("folding hides what is under it"); });
    same(errors, [], "no page errors");
  },

  // Each screen's page scroll is kept: Back to the list, and a reload, land where the reader was.
  async scroll_memory(b) {
    const { page, errors } = await open(b, { viewport: { width: 360, height: 260 }, hasTouch: true });
    const y = () => page.evaluate(() => Math.round(window.scrollY));
    const settle = () => page.waitForTimeout(400);
    const room = () => page.evaluate(() => document.documentElement.scrollHeight - innerHeight);
    assert(await room() > 150, "the list is taller than this window, or there is nothing to scroll");
    await page.evaluate(() => window.scrollTo(0, 120)); await settle();
    same(await y(), 120, "the list scrolled");
    await page.evaluate(() => document.querySelector(".item[data-id=W9]").click());
    await page.waitForSelector(".row"); await settle();
    same(await y(), 0, "a screen never seen opens at the top");
    assert(await room() > 60, "the tree is taller than this window");
    await page.evaluate(() => window.scrollTo(0, 60)); await settle();
    same(await y(), 60, "the tree scrolled");
    await page.goBack(); await page.waitForSelector(".item"); await settle();
    same(await y(), 120, "Back to the list lands where it was left");
    await page.reload(); await page.waitForSelector(".item"); await settle();
    same(await y(), 120, "a reload keeps the list's place");
    await page.evaluate(() => document.querySelector(".item[data-id=W9]").click());
    await page.waitForSelector(".row"); await settle();
    same(await y(), 60, "and the tree's, on the way back in");
    same(errors, [], "no page errors");
  },

  // The chat log and the node sheet are scroll areas of their own, and each is remembered too.
  async scroll_memory_areas(b) {
    const { page, errors } = await open(b, { viewport: { width: 360, height: 260 }, hasTouch: true });
    // the chat's answers are the tool's own and need an agent: a long conversation is handed to the page instead
    const state = { messages: Array.from({ length: 40 }, (_, i) => ({ role: i % 2 ? "agent" : "you", text: `line ${i} of a long talk` })), busy: false, busy_for: 0, live: false, elsewhere: false, note: "" };
    await page.route("**/api/chat/**", r => r.fulfill({ contentType: "application/json", body: JSON.stringify(state) }));
    const settle = () => page.waitForTimeout(400);
    const top = sel => page.$eval(sel, e => Math.round(e.scrollTop));
    await task(page, "W9");
    await page.evaluate(() => document.querySelector(".row.hasraise .main").click());
    await page.waitForSelector("#sheet.open .sheet-body");
    const room = await page.$eval(".sheet-body", e => e.scrollHeight - e.clientHeight);
    assert(room > 30, `the sheet's body is taller than its window (${room})`);
    await page.$eval(".sheet-body", e => { e.scrollTop = 30; }); await settle();
    await page.click("#sheet [data-act=next]"); await settle();
    same(await top(".sheet-body"), 0, "a node not yet read opens at its top");
    await page.click("#sheet [data-act=prev]"); await settle();
    same(await top(".sheet-body"), 30, "stepping back to a read node lands where it was left");
    await page.click("#sheet [data-act=close]"); await page.waitForSelector("#sheet:not(.open)", { state: "attached" });
    await page.click("#tabs [data-act=chat]"); await page.waitForSelector("#chat[open]");
    await page.waitForFunction(() => document.querySelectorAll("#chat-log .msg").length === 40);
    const log = () => page.$eval("#chat-log", e => ({ at: Math.round(e.scrollTop), end: e.scrollHeight - e.clientHeight }));
    const l0 = await log();
    assert(l0.end > 200 && Math.abs(l0.at - l0.end) < 3, "a chat opens at its end");
    await page.$eval("#chat-log", e => { e.scrollTop = 150; }); await settle();
    await page.click("[data-act=chatclose]"); await settle();
    await page.click("#tabs [data-act=chat]"); await page.waitForSelector("#chat[open]");
    await page.waitForFunction(() => document.querySelectorAll("#chat-log .msg").length === 40); await settle();
    same((await log()).at, 150, "a chat reopens where it was scrolled to");
    await page.$eval("#chat-log", e => { e.scrollTop = e.scrollHeight; }); await settle();
    await page.click("[data-act=chatclose]"); await settle();
    await page.click("#tabs [data-act=chat]"); await page.waitForSelector("#chat[open]");
    await page.waitForFunction(() => document.querySelectorAll("#chat-log .msg").length === 40); await settle();
    const l1 = await log();
    assert(Math.abs(l1.at - l1.end) < 3, "one left at its end opens at its end");
    same(errors, [], "no page errors");
  },

  // The node sheet: prev and next move, and stay exactly where they are.
  async node_sheet(b) {
    const { page, errors } = await open(b, PHONE);
    await task(page, "W1");
    await page.click(".row .main >> nth=0");
    await page.waitForSelector("#sheet.open");
    const box = async sel => JSON.stringify(await page.$eval(sel, e => { const r = e.getBoundingClientRect(); return [r.x, r.y, r.width, r.height].map(Math.round); }));
    const prev0 = await box("#sheet [data-act=prev]"), next0 = await box("#sheet [data-act=next]");
    for (let i = 0; i < 4; i++) {
      await page.click("#sheet [data-act=next]"); await page.waitForTimeout(120);
      same([await box("#sheet [data-act=prev]"), await box("#sheet [data-act=next]")], [prev0, next0], `step ${i}: Prev/Next stay put`);
    }
    await page.click("#sheet [data-act=close]");
    await page.waitForFunction(() => !document.querySelector("#sheet.open"), null, { timeout: 4000 })
      .catch(() => { throw new Error("close closes it"); });
    same(errors, [], "no page errors");
  },

  // Answer a raise: the log gets the same entry the terminal writes.
  async answer(b) {
    const { page, errors } = await open(b, PHONE);
    await task(page, "W9");
    await page.click(".row.hasraise .main");
    await page.waitForSelector("#sheet.open");
    await page.click("#sheet [data-act=answer]"); await dialog(page);
    await page.fill("dialog textarea", "Spinning disks. Test on one.");
    await page.click("dialog button[value=ok]");
    await page.waitForFunction(() => !document.querySelector(".row.hasraise"));
    same(errors, [], "no page errors");
  },

  // The page a raise names ("Answer with:") records the answer itself: it is a sandboxed frame, yet its POST to
  // /artefact-state reaches the server and the form closes when the page says so. Nothing else it sends does.
  async answer_page(b) {
    const { page, errors } = await open(b, PHONE);
    await task(page, "W9");
    await page.click(".row.hasraise .main");
    await page.waitForSelector("#sheet.open");
    await page.click("#sheet [data-act=answer]"); await dialog(page);
    const frame = page.frames().find(f => f.url().includes("/artefacts/c0ffee-choose.html"));
    assert(frame, "the named page opens beside the form");
    same(await frame.evaluate(() => fetch("/api/walk/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
      .then(r => "reached " + r.status, () => "blocked")), "blocked", "the page still cannot reach the API");
    if (process.env.DFS_SHOT) await page.screenshot({ path: process.env.DFS_SHOT });   // for a node's screenshot
    await page.evaluate(() => postMessage({ dfs: "answered" }, "*"));
    await page.waitForTimeout(200);
    assert(await page.locator("dialog[open] #answerpage").count() === 1, "a message from another window does not close the form");
    assert(await page.locator(".row.hasraise").count() > 0, "the raise is still open");
    await page.frameLocator("dialog #answerpage").locator("#send").click();
    await page.waitForFunction(() => !document.querySelector("dialog[open]"), null, { timeout: 5000 });
    await page.waitForFunction(() => !document.querySelector(".row.hasraise"), null, { timeout: 5000 });
    same(errors, [], "no page errors");
  },

  // Refute/confirm: the form says what each choice does, then writes the correction.
  async correct(b) {
    const { page, errors } = await open(b, PHONE);
    await task(page, "W9");
    await page.click("[data-id='W9.2'].main");
    await page.waitForSelector("#sheet.open");
    await page.click("#sheet [data-act=correct]"); await dialog(page);
    const both = await page.textContent("#verdicthelp");
    assert(/Refuted:/.test(both) && /Confirmed:/.test(both), "both outcomes are stated before a choice");
    await page.click("dialog label:has(input[value=refuted])");
    assert(/^Refuted:/.test(await page.textContent("#verdicthelp")), "then only the chosen one");
    await page.fill("dialog textarea", "WAL is wrong on this disk.");
    await page.click("dialog button[value=ok]");
    await page.waitForFunction(() => /overruled by you/.test(document.body.textContent));
    same(errors, [], "no page errors");
  },

  // A keyboard leaves a short screen: the action buttons of a form are still on it, unscrolled.
  async keyboard(b) {
    const { page, errors } = await open(b, PHONE);
    await page.setViewportSize({ width: 390, height: 330 });        // what is left above the keys
    await task(page, "W9");
    await page.click("[data-id='W9.2'].main"); await page.waitForSelector("#sheet.open");
    await page.click("#sheet [data-act=correct]"); await dialog(page);
    await page.click("dialog label:has(input[value=refuted])");
    for (const sel of ["dialog button[value=ok]", "dialog button[value=cancel]"]) {
      const r = await page.$eval(sel, e => { const b = e.getBoundingClientRect(); return { top: b.top, bottom: b.bottom }; });
      assert(r.top >= 0 && r.bottom <= 330, `${sel} is in view above the keys (${Math.round(r.top)}..${Math.round(r.bottom)} of 330)`);
    }
    await page.fill("dialog textarea", "WAL is wrong on this disk.");
    await page.click("dialog button[value=ok]");
    await page.waitForFunction(() => /overruled by you/.test(document.body.textContent));
    same(errors, [], "no page errors");
  },

  // Add a node under another.
  async add(b) {
    const { page, errors } = await open(b, PHONE);
    await task(page, "W3");
    await page.click("[data-act=add]"); await dialog(page);
    await page.fill("#t", "Cover the stampede with a test");
    await page.click("dialog button[value=ok]");
    await page.waitForFunction(() => /Cover the stampede with a test/.test(document.body.textContent));
    same(errors, [], "no page errors");
  },

  // Accept a finished tree.
  async accept(b) {
    const { page, errors } = await open(b, PHONE);
    await task(page, "W4");
    await page.click("[data-act=accept]"); await dialog(page);
    await page.click("dialog button[value=ok]");
    await page.waitForFunction(() => !document.querySelector("[data-act=accept]"));
    same(errors, [], "no page errors");
  },

  // The walk, through the controls the page offers (a recording walker stands behind them).
  async walk(b) {
    const { page, errors } = await open(b, PHONE);
    await openWalk(page);
    await page.selectOption("#nextsel", "W2");
    await page.selectOption("#agentsel", "codex");
    await page.click("[data-act=walkstart]"); await dialog(page);
    assert(/permission prompts/i.test(await page.textContent("dialog")), "starting says what it does");
    await page.fill("#bud", "7");
    await page.click("dialog button[value=ok]");
    await page.waitForFunction(() => /Walking/.test(document.querySelector("#walkbox")?.textContent || ""));
    assert(/7/.test(await page.textContent("#walkbox")), "the budget shows");
    await page.click("[data-act=walkstop]");
    await page.waitForFunction(() => /Walk is off/.test(document.querySelector("#walkbox")?.textContent || ""));
    same(errors, [], "no page errors");
  },

  // The thumbs: what you press to move is in the bottom of the screen, Back always at the right, under the right thumb, what moves you on the left.
  async thumbs(b) {
    for (const [name, opts] of [["phone", PHONE], ["narrow", NARROW]]) {
      const { ctx, page, errors } = await open(b, opts);
      const H = () => page.evaluate(() => innerHeight), W = () => page.evaluate(() => innerWidth);
      const box = async sel => page.$eval(sel, e => { const r = e.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height, b: r.bottom }; });
      const low = async (sel, what) => {
        const r = await box(sel), h = await H();
        assert(r.y > h * 0.7, `${name}: ${what} is in the lower third (y ${Math.round(r.y)} of ${h})`);
        assert(r.h >= 44, `${name}: ${what} is tap-sized (${Math.round(r.h)}px)`);
        assert(r.w >= 44 && r.x >= 0 && r.x + r.w <= await W(), `${name}: ${what} is at least 44px wide and inside the viewport (x ${Math.round(r.x)}, ${Math.round(r.w)}px wide)`);
        return r;
      };
      // the list: All, Needs you, Reorder and Chats in the bar, no filter strip above the rows and no Assumptions tab
      await low("#tabs [data-act=only][data-v='0']", "All"); await low("#tabs [data-act=only][data-v='1']", "Needs you"); await low("#tabs [data-act=reorder]", "Reorder"); await low("#tabs [data-tab=chats]", "Chats tab");
      assert(!(await page.$(".filters")) && !(await page.$("[data-tab=assumptions]")), `${name}: the filters live in the bar and the Assumptions tab is gone`);
      assert(await page.$eval("#back", e => getComputedStyle(e).display === "none"), `${name}: no Back up in the header`);
      // a task: Chat and Flagged on the left, Back at the right
      await task(page, "W9");
      const back = await low("#tabs [data-act=up]", "Back");
      const chat = await low("#tabs [data-act=chat]", "Chat"), flagged = await low("#tabs [data-act=nextflag]", "Flagged");
      assert(chat.x < flagged.x && flagged.x < back.x, `${name}: Chat, then Flagged, then Back, left to right`);
      assert(back.x + back.w > (await W()) * 0.7, `${name}: Back is under the right thumb`);
      // a node: its bar replaces it, Prev and Next on the left, Back at the right
      await page.click("#tabs [data-act=nextflag]"); await page.waitForSelector("#sheet.open");
      const nb = await low("#sheet [data-act=close]", "node Back"), np = await low("#sheet [data-act=prev]", "Prev");
      const nn = await low("#sheet [data-act=next]", "Next"), nf = await low("#sheet [data-act=nextflag]", "node Flagged");
      assert(np.x < nn.x && nn.x < nf.x && nf.x < nb.x, `${name}: Prev, Next, Flagged, Back, left to right`);
      assert(np.x < (await W()) / 3, `${name}: Prev is under the left thumb`);
      assert(nb.x + nb.w > (await W()) * 0.7, `${name}: Back is under the right thumb`);
      const where = async () => (await page.textContent("#sheet .where")).trim();
      const first = await where();
      await page.click("#sheet [data-act=next]"); await page.waitForFunction(w => document.querySelector("#sheet .where").textContent.trim() !== w, first);
      await page.click("#sheet [data-act=prev]"); await page.waitForFunction(w => document.querySelector("#sheet .where").textContent.trim() === w, first);
      // back, back: the node, then the task
      await page.click("#sheet [data-act=close]");
      await page.waitForFunction(() => !document.querySelector("#sheet.open") && document.querySelector("#tabs [data-act=up]"));
      await page.click("#tabs [data-act=up]"); await page.waitForSelector(".item");
      assert(await page.$("#tabs [data-act=only][data-v='0'][aria-current=page]"), `${name}: the bar is back, All current`);
      // the chat: Back at the bottom
      await page.click("#tabs [data-tab=chats]"); await page.click("[data-act=chat][data-scope=project]"); await page.waitForSelector("#chat[open]");
      await low("#chat [data-act=chatclose]", "chat Back");
      await page.click("#chat [data-act=chatclose]"); await page.waitForFunction(() => !document.querySelector("#chat[open]"));
      same(errors, [], `${name}: no page errors`);
      await ctx.close();
    }
    // a wide screen keeps its Back in the header and its tabs at the top
    const { ctx, page, errors } = await open(b, DESKTOP);
    await task(page, "W9");
    assert(await page.$eval("#back", e => getComputedStyle(e).display !== "none"), "desktop: Back is in the header");
    assert((await page.$eval("#tabs", e => e.getBoundingClientRect().y)) < 60, "desktop: the tabs are at the top");
    await ctx.close();
  },

  // Chat: a basic, read-only conversation about a task, then about the project.
  async chat(b) {
    const { page, errors } = await open(b, PHONE);
    await task(page, "W9");
    await page.click("#tabs [data-act=chat]");
    await page.waitForSelector("#chat[open]");
    // a new chat opens with the summary, unprompted, before anything is said
    await page.waitForFunction(() => document.querySelectorAll("#chat-log .msg.agent").length >= 1, null, { timeout: 20000 });
    assert(!(await page.$("#chat-log .msg.you")), "nobody has spoken: the opening is the tool's, not yours");
    await page.fill("#chat-input", "Where is the journal mode decided?");
    await page.click("#chat-send");
    await page.waitForFunction(() => document.querySelectorAll("#chat-log .msg.agent").length >= 2, null, { timeout: 20000 });
    assert(await page.$("#chat-log .msg.you"), "what you said is kept");
    assert(await page.$("#chat-log .msg.agent b"), "bold is bold");
    assert(await page.$("#chat-log .msg.agent code"), "code is code");
    assert(!(await page.$eval("#chat-send", e => e.disabled)), "and you can ask again");
    await page.fill("#chat-input", "and then?");
    await page.keyboard.press("Control+Enter");
await page.waitForFunction(() => document.querySelectorAll("#chat-log .msg.agent").length >= 3, null, { timeout: 20000 });
    await page.reload(); await page.waitForSelector(".row");          // the route is still W9
    await page.click("#tabs [data-act=chat]"); await page.waitForSelector("#chat[open]");
    await page.waitForFunction(() => document.querySelectorAll("#chat-log .msg").length === 5, null, { timeout: 5000 });
    await page.click("[data-act=chatclear]");
    await page.waitForFunction(() => { const m = document.querySelectorAll("#chat-log .msg"); return m.length === 1 && m[0].classList.contains("agent") && !document.querySelector("#chat-log .msg.you"); }, null, { timeout: 20000 });    // and a new one opens with the summary again
    await page.click("[data-act=chatclose]");
    await toList(page);
    await page.click("#tabs [data-tab=chats]"); await page.click("[data-act=chat][data-scope=project]"); await page.waitForSelector("#chat[open]");
    assert(/Chat · /.test(await page.textContent("#chat-title")), "the project has its own");
    same(errors, [], "no page errors");
  },

  // Chats: start several, leave each, find them in the Chats section, pick one up again.
  async chats(b) {
    const { page, errors } = await open(b, PHONE);
    await task(page, "W9");
    await page.click("#tabs [data-act=chat]"); await page.waitForSelector("#chat[open]");
    await page.fill("#chat-input", "first about W9");
    await page.click("#chat-send");
    await page.waitForFunction(() => document.querySelectorAll("#chat-log .msg.agent").length >= 2, null, { timeout: 20000 });
    await page.click("#chat [data-act=chatlist]");                       // leave it, to the list of chats
    await page.waitForFunction(() => !document.querySelector("#chat[open]") && document.querySelector("#tabs [data-tab=chats][aria-current=page]"));
    await page.waitForSelector(".chatrow");
    // a second, about the project, begun from here
    await page.click("[data-act=chat][data-scope=project]"); await page.waitForSelector("#chat[open]");
    await page.fill("#chat-input", "and about the project");
    await page.click("#chat-send");
    await page.waitForFunction(() => document.querySelectorAll("#chat-log .msg.agent").length >= 2, null, { timeout: 20000 });
    await page.click("#chat [data-act=chatclose]");
    await page.waitForFunction(() => document.querySelectorAll(".chatrow").length === 2, null, { timeout: 8000 });
    const rows = await page.$$eval(".chatrow", e => e.map(x => x.dataset.scope));
    same(rows.sort(), ["W9", "project"], "both conversations are listed");
    // the first is still there, where it was left
    await page.click(".chatrow[data-scope=W9]"); await page.waitForSelector("#chat[open]");
    await page.waitForFunction(() => /first about W9/.test(document.querySelector("#chat-log")?.textContent || ""), null, { timeout: 5000 });
    assert(!(await page.$eval("#chat-send", e => e.disabled)), "and can be gone on with");
    const nav = await page.$$eval("#chat .chat-nav button", e => e.map(x => x.dataset.act));
    same(nav, ["chatclear", "chatlist", "chatclose"], "the chat's bar: New, Chats, Back at the right");
    await page.click("#chat [data-act=chatclose]");
    same(errors, [], "no page errors");
  },

  // Light and dark: the switch flips the page, shows the mode you are in, and remembers.
  async theme(b) {
    const { page, errors } = await open(b, PHONE, "light");
    const light = await bg(page);
    assert((await page.$$eval("#theme circle", e => e.length)) === 1, "a sun in light mode");
    await page.click("#theme");
    const dark = await bg(page);
    assert(dark !== light, "the page changed");
    assert(/^M20/.test(await page.$eval("#theme path", e => e.getAttribute("d"))), "a moon in dark mode");
    await page.reload(); await page.waitForSelector(".item");
    same(await bg(page), dark, "and it was remembered");
    await page.click("#theme");
    same(await bg(page), light, "and switches back");
    same(errors, [], "no page errors");
  },

  // The project's name is editable in the page and lives in .dfs/title.
  async rename(b) {
    const { page, errors } = await open(b, PHONE);
    await page.click("[data-act=rename]"); await dialog(page);
    await page.fill("#pn", "Gateway core");
    await page.click("dialog button[value=ok]");
    await page.waitForFunction(() => document.title === "Gateway core");
    assert((await page.textContent("#title")).startsWith("Gateway core"), "the header follows");
    same(errors, [], "no page errors");
  },

  // The project's colour is chosen in the same dialog and lives in .dfs/theme: the bar's
  // stripe and the browser's theme-color follow it at once.
  async colour(b) {
    const { page, errors } = await open(b, PHONE);
    await page.click("[data-act=rename]"); await dialog(page);
    assert(await page.isChecked("#pa"), "it follows the name until one is chosen");
    await page.fill("#pc", "#aa3355");
    assert(!(await page.isChecked("#pa")), "choosing a colour stops following the name");
    await page.click("dialog button[value=ok]");
    await page.waitForFunction(() => document.querySelector('meta[name=theme-color]').content === "#aa3355");
    const stripe = await page.evaluate(() => getComputedStyle(document.querySelector("#bar")).borderTopColor);
    same(stripe, "rgb(170, 51, 85)", "the bar wears it");
    same(errors, [], "no page errors");
  },

  // The page's background is chosen in the same dialog, one for light mode and one for dark, and
  // lives in .dfs/background apart from the colour: the paper follows the mode the switch picks,
  // the bar's stripe stays the project's colour, and clearing both returns the design's paper.
  async background(b) {
    const { page, errors } = await open(b, PHONE);
    const stripe = () => page.evaluate(() => document.documentElement.style.getPropertyValue("--project"));
    await page.evaluate(() => { document.documentElement.dataset.theme = "light"; localStorage.setItem("theme", "light"); });
    const line = await stripe(), paper = await bg(page);
    await page.click("[data-act=rename]"); await dialog(page);
    assert(await page.isChecked("#pd-light") && await page.isChecked("#pd-dark"), "each mode is the design's paper until one is chosen");
    await page.fill("#pb-light", "#fff2cc"); await page.fill("#pb-dark", "#10243a");
    assert(!(await page.isChecked("#pd-light")) && !(await page.isChecked("#pd-dark")), "choosing a background stops using the paper");
    await page.click("dialog button[value=ok]");
    await page.waitForFunction(() => getComputedStyle(document.body).backgroundColor === "rgb(255, 242, 204)");
    same(await stripe(), line, "the project's colour stays what it was");
    assert(await page.evaluate(() => getComputedStyle(document.querySelector("#bar")).borderTopWidth !== "0px"), "and the bar keeps its stripe");
    assert(await page.isVisible("#theme"), "the light/dark switch stays");
    await page.waitForSelector("dialog[open]", { state: "hidden" });
    await page.click("#theme");
    await page.waitForFunction(() => getComputedStyle(document.body).backgroundColor === "rgb(16, 36, 58)");
    const ink = await page.evaluate(() => getComputedStyle(document.body).color);
    assert(ink !== "rgb(26, 29, 33)", "the text is light on the dark background, got " + ink);
    if (process.env.DFS_SHOT) await page.screenshot({ path: process.env.DFS_SHOT });
    await page.reload(); await page.waitForSelector(".item");
    same(await bg(page), "rgb(16, 36, 58)", "it survives a reload without a flash of the paper");
    await page.click("#theme");
    await page.waitForFunction(() => getComputedStyle(document.body).backgroundColor === "rgb(255, 242, 204)");
    await page.click("[data-act=rename]"); await dialog(page);
    await page.check("#pd-light"); await page.check("#pd-dark");
    await page.click("dialog button[value=ok]");
    await page.waitForFunction(p => getComputedStyle(document.body).backgroundColor === p, paper);
    same(errors, [], "no page errors");
  },

  // Reorder: the list's Reorder switch gives each row tap-sized moves, a move writes order.md and
  // redraws the list, a move that cannot be made says why, and switching it off restores the rows.
  async reorder(b) {
    const { page, errors } = await open(b, PHONE);
    const ids = () => page.$$eval(".item .iid", es => es.map(e => e.textContent.trim()));
    const before = await ids();
    assert(before.length >= 3 && !(await page.$(".moves")), "the list has no moves until Reorder is on");
    await page.click("[data-act=reorder]");
    await page.waitForSelector(".moves");
    same(await page.$$eval(".moves .btn", es => es.filter(e => e.getBoundingClientRect().height < 44).length), 0, "every move is tap-sized");
    assert(!(await page.$("button.item")), "a row is not a button while the moves are on it");
    await page.click(`[data-act=move][data-move=down][data-id=${before[0]}]`);
    await page.waitForFunction(first => document.querySelector(".item .iid").textContent.trim() !== first, before[0], { timeout: 8000 });
    assert(fs.existsSync(path.join(DIR, ".dfs", "order.md")), "the move is written to order.md");
    const after = await ids();
    assert(after.indexOf(before[0]) > 0, "the moved task is no longer first");
    await page.click(`[data-act=move][data-move=up][data-id=${after[0]}]`);
    await page.waitForSelector("#toast.err");
    assert((await page.textContent("#toast")).includes("first"), "a refused move says why");
    await page.click("[data-act=reorder]");
    assert(await page.$("button.item") && !(await page.$(".moves")), "switching Reorder off restores the rows");
    same(errors, [], "no page errors");
  },

  // All in the bar leaves Reorder: pressed in reorder mode, and from the Chats screen after Reorder was on.
  async allleavesreorder(b) {
    const { page, errors } = await open(b, PHONE);
    const all = "#tabs [data-act=only][data-v='0'][aria-current=page]";
    await page.click("[data-act=reorder]");
    await page.waitForSelector(".moves");
    await page.click("#tabs [data-act=only][data-v='0']");
    await page.waitForSelector("button.item");
    assert(!(await page.$(".moves")), "All takes the list out of Reorder");
    assert(await page.$(all), "All is current after it");
    await page.click("[data-act=reorder]");
    await page.waitForSelector(".moves");
    await page.click("#tabs [data-tab=chats]");
    await page.waitForFunction(() => !document.querySelector(".moves"));
    await page.click("#tabs [data-act=only][data-v='0']");
    await page.waitForSelector("button.item");
    assert(!(await page.$(".moves")), "All from Chats shows the list out of Reorder");
    assert(await page.$(all), "All is current after coming from Chats");
    same(errors, [], "no page errors");
  },

  // All, Needs you and Reorder pressed from the Chats screen take you back to the list.
  async barfromchats(b) {
    const { page, errors } = await open(b, PHONE);
    for (const sel of ["#tabs [data-act=only][data-v='0']", "#tabs [data-act=only][data-v='1']", "#tabs [data-act=reorder]"]) {
      await page.click("#tabs [data-tab=chats]");
      await page.waitForFunction(() => !document.querySelector(".item"));
      await page.click(sel);
      await page.waitForFunction(() => location.hash === "" || location.hash === "#/");
      assert(await page.$("#tabs [data-act=only]"), `${sel} from Chats leaves the list bar showing`);
    }
    same(errors, [], "no page errors");
  },

  // A new task from the floating button, and the title renamed by clicking it.
  async newtask(b) {
    const { page, errors } = await open(b, PHONE);
    const btn = await page.$eval("[data-act=newtask]", e => { const r = e.getBoundingClientRect(), t = document.querySelector("#tabs").getBoundingClientRect(); return { h: r.height, fixed: getComputedStyle(e).position === "fixed", right: innerWidth - r.right, aboveTabs: r.bottom <= t.top, low: r.top > innerHeight / 2 }; });
    assert(btn.h >= 44 && btn.fixed && btn.right < 40 && btn.aboveTabs && btn.low, "the button is tap-sized and floats at the bottom right, above the tab bar");
    assert(!(await page.$("[data-act=chat][data-scope=project]")), "the task list has no project chat button");
    await page.click("[data-act=newtask]"); await dialog(page);
    await page.fill("#nn", "Cache the lookups");
    await page.fill("#ng", "Hits above 90%.");
    await page.fill("#nt", "profile it\nadd the cache");
    await page.click("dialog button[value=ok]");
    await page.waitForFunction(() => [...document.querySelectorAll(".item .goal")].some(e => /Cache the lookups/.test(e.textContent)), null, { timeout: 8000 });
    assert(!(await page.$(".row")), "creating the task leaves the list open, not the new task");
    // the title is the control: click it to rename
    await page.click("#title .rename"); await dialog(page);
    assert((await page.inputValue("#pn")).length > 0, "the form has the name");
    await page.keyboard.press("Escape");
    assert(!(await page.$("[data-act=rename]:not(#title .rename)")), "there is no second rename button");
    same(errors, [], "no page errors");
  },

  // An artefact named in a raise is linked and served sandboxed: its script cannot reach the API.
  async artefacts(b, ctx0) {
    const { ctx, page, errors } = await open(b, PHONE);
    await task(page, "W9");
    assert((await page.textContent(".arts")).includes("Where the fsync lands"), "the task lists it");
    await page.click(".row.hasraise .main");
    await page.waitForSelector("#sheet.open");
    assert((await page.$$("#sheet a.art")).length >= 1, "the raise links it inline");
    const [popup] = await Promise.all([ctx.waitForEvent("page"), page.click("#sheet a.art >> nth=0")]);
    await popup.waitForLoadState(); await popup.waitForTimeout(400);
    same(await popup.title(), "Where the fsync lands", "it opens");
    same(await popup.textContent("#r"), "blocked", "its script could not reach the page's API");
    same(errors, [], "no page errors");
  },

  // Prose is markdown: `code`, **bold**, links, lists and fences are drawn in a section and a node's
  // card, and what the file holds can never become an element of the page.
  async richtext(b) {
    const f = path.join(DIR, ".dfs", "items", "W9.md");
    let t = fs.readFileSync(f, "utf8");
    t = t.replace("## Background\n\n", "## Background\n\nRead `journal_mode` and **both** [docs](https://example.com/x?a=1&b=2) <script>window.hacked=1</script>\n- first `item`\n- second\n```sh\nsqlite3 a.db '.mode'\n```\n");
    t = t.replace("Create a sessions table", "Create a `sessions` table, **not** a file per session,");
    t = t.replace("Choose the SQLite journal mode", "Choose the `SQLite` journal mode");
    fs.writeFileSync(f, t);
    const { page, errors } = await open(b, PHONE);
    await task(page, "W9");
    same(await page.locator(".line code", { hasText: "SQLite" }).first().textContent(), "SQLite", "a node title draws inline code, markers gone");
    const bg = page.locator("details.sec", { hasText: "Background" });
    if (!(await bg.getAttribute("open"))?.toString().length && (await bg.evaluate(e => !e.open))) await bg.locator("summary").click();
    same(await bg.locator(".body code").first().textContent(), "journal_mode", "inline code");
    same(await bg.locator(".body b").first().textContent(), "both", "bold");
    same(await bg.locator(".body a[href^='https://example.com']").getAttribute("href"), "https://example.com/x?a=1&b=2", "link");
    same(await bg.locator(".body ul.md li").count(), 2, "list items");
    same(await bg.locator(".body pre").textContent(), "sqlite3 a.db '.mode'", "fenced block");
    assert((await bg.textContent()).includes("<script>window.hacked=1</script>"), "markup in the text is shown, not run");
    assert(!(await page.evaluate(() => window.hacked || document.querySelector("#view script"))), "and never becomes an element");
    await page.click(".line:has-text('journal mode')");
    await page.waitForSelector("#sheet.open, #sheet .sheet-body");
    same(await page.locator("#sheet .sheet-body code").first().textContent(), "sessions", "the card draws inline code");
    same(await page.locator("#sheet .sheet-body b").first().textContent(), "not", "and bold");
    same(errors, [], "no page errors");
  },

  // A slow read shows "loading" and the stripe after a beat and clears them when it lands; a fast one shows nothing.
  async loading(b) {
    const ctx = await b.newContext({ ...PHONE });
    const page = await ctx.newPage(); PAGES.push(page);
    const errors = [];
    page.on("pageerror", e => errors.push(String(e)));
    let release;
    const held = new Promise(r => { release = r; });
    await page.route("**/api/state", async route => { await held; await route.continue(); });
    await page.goto(URL_);
    assert(/Loading/.test(await page.textContent("#view")), "the empty page says Loading");
    await page.waitForFunction(() => !document.querySelector("#loading").hidden, null, { timeout: 3000 });
    same(await page.getAttribute("#view", "aria-busy"), "true", "the view is busy");
    await page.waitForFunction(() => getComputedStyle(document.querySelector("#stripe")).opacity === "1", null, { timeout: 3000 });
    release();
    await page.waitForSelector(".item");
    await page.waitForFunction(() => document.querySelector("#loading").hidden, null, { timeout: 3000 });
    same(await page.getAttribute("#view", "aria-busy"), "false", "the view is no longer busy");
    assert(await page.evaluate(() => !document.documentElement.dataset.loading), "the stripe is gone");
    same(errors, [], "no page errors");
    await ctx.close();
  },

  // A first load that fails stops saying "Loading…": the page shows the error and a Retry that draws once the server answers.
  async loadfail(b) {
    const ctx = await b.newContext({ ...PHONE });
    const page = await ctx.newPage(); PAGES.push(page);
    let fail = true;
    await page.route("**/api/state", async route => {
      if (fail) await route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ error: "the roadmap is unreadable" }) });
      else await route.continue();
    });
    await page.goto(URL_);
    await page.waitForSelector("#view [data-act=retry]", { timeout: 3000 });
    const text = await page.textContent("#view");
    assert(!/Loading/.test(text), "the page no longer says Loading");
    assert(text.includes("the roadmap is unreadable"), "it shows the error");
    fail = false;
    await page.click("[data-act=retry]");
    await page.waitForSelector(".item", { timeout: 3000 });
    await ctx.close();
  },

  // A change made elsewhere (a chain, the terminal) reaches an open page by itself.
  async live(b) {
    const { page, errors } = await open(b, PHONE);
    assert(!(await page.getAttribute(".item[data-id=W3]", "class")).includes("blocks"), "W3 is not blocked");
    fs.appendFileSync(path.join(DIR, ".dfs", "items", "W3.md"),
      "\n- 2026-10-01T09:00:00Z · raise · W3.1\n  Which cache backend do we target?\n");
    await page.waitForFunction(() => document.querySelector(".item[data-id=W3]")?.className.includes("blocks"), null, { timeout: 8000 });
    same(errors, [], "no page errors");
  },

  // The design system's own specimen: it must pass what it asks of the page, in both themes.
  async design(b) {
    for (const [name, opts] of [["phone", PHONE], ["narrow", NARROW], ["desktop", DESKTOP]]) {
      for (const scheme of ["light", "dark"]) {
        const ctx = await b.newContext({ ...opts, colorScheme: scheme });
        const page = await ctx.newPage(); PAGES.push(page);
        const errors = [];
        page.on("pageerror", e => errors.push(String(e)));
        page.on("console", m => { if (m.type() === "error") errors.push(m.text()); });
        await page.goto(URL_ + "/design"); await page.waitForSelector("#colours .sw");
        const a = await page.evaluate(AUDIT);
        assert(!a.overflow, `${name}/${scheme}: /design scrolls sideways`);
        assert(!a.low.length, `${name}/${scheme}: /design low contrast: ${a.low.join("; ")}`);
        assert(!a.small.length, `${name}/${scheme}: /design small targets: ${a.small.join("; ")}`);
        const measured = await page.$$eval("#colours span, #colours .pair", n => n.map(e => e.textContent).filter(t => /:1/.test(t)));
        assert(measured.length >= 8, "the specimen measures the colours it names");
        for (const t of measured) { const r = parseFloat(t.match(/([\d.]+):1/)[1]); assert(r >= 4.5, `${name}/${scheme}: ${t} is under 4.5 to 1`); }
        await page.click("#d-dialog"); await page.waitForSelector("dialog[open]"); await page.keyboard.press("Escape");
        same(errors, [], `${name}/${scheme}: no page errors`);
        await ctx.close();
      }
    }
  },

  // Every screen, both themes, three widths: nothing overflows, text can be read, targets can be hit.
  async layout(b) {
    for (const [name, opts] of [["phone", PHONE], ["narrow", NARROW], ["desktop", DESKTOP]]) {
      for (const scheme of ["light", "dark"]) {
        const { ctx, page, errors } = await open(b, opts, scheme);
        const check = async what => {
          await page.waitForTimeout(150);
          const a = await page.evaluate(AUDIT);
          assert(!a.overflow, `${name}/${scheme}/${what}: scrolls sideways`);
          assert(!a.low.length, `${name}/${scheme}/${what}: low contrast: ${a.low.join("; ")}`);
          assert(!a.small.length, `${name}/${scheme}/${what}: small targets: ${a.small.join("; ")}`);
        };
        await check("list");
        await page.click("[data-act=only][data-v='1']"); await check("needs you");
        await page.click("[data-act=only][data-v='0']");
        await task(page, "W9"); await check("task");
        await page.click(".row.hasraise .main"); await page.waitForSelector("#sheet.open"); await check("node");
        await page.click("#sheet [data-act=correct]"); await dialog(page); await check("correct form");
        await page.keyboard.press("Escape");
        await page.evaluate(() => document.querySelector("#sheet [data-act=close]")?.click());
        await toList(page);
        await page.click("#tabs [data-tab=chats]"); await check("chats");
        await page.click("#tabs [data-act=only][data-v='0']"); await page.waitForSelector(".item");
        assert(!(await page.$("[data-act=chat][data-scope=project]")), "the task list has no project chat button");
        await page.click("#tabs [data-tab=chats]"); await page.click("[data-act=chat][data-scope=project]"); await page.waitForSelector("#chat[open]"); await check("chat");
        same(errors, [], `${name}/${scheme}: no page errors`);
        await ctx.close();
      }
    }
  },
};

const fn = FLOWS[flow];
if (!fn) { console.error("no flow " + flow + "; have " + Object.keys(FLOWS).join(", ")); process.exit(2); }
const browser = await chromium.launch({ executablePath: chromiumPath(), args: ["--no-sandbox"] });
try {
  await fn(browser);
} catch (e) {
  console.error(`${flow}: ${e.message.split("\n")[0]}`);
  if (process.env.DFS_UI_SHOTS && PAGES.length) {     // what the page looked like when it went wrong
    try { await PAGES[PAGES.length - 1].screenshot({ path: path.join(process.env.DFS_UI_SHOTS, flow + "-failed.png") }); } catch (_) {}
  }
  process.exitCode = 1;
} finally {
  await browser.close();
}
