// bash render.sh-style wrapper puts playwright on NODE_PATH; node render.mjs <in.svg> <out.png>: draw an svg at its own size with headless chromium.
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
const { chromium } = createRequire(import.meta.url)("playwright");
const [src, out] = process.argv.slice(2);
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1920, height: 1200 } });
await p.setContent("<body style='margin:0'>" + readFileSync(src, "utf8"));
await p.screenshot({ path: out });
await b.close();
