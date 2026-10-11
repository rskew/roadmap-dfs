// bash render.sh <in.svg> <out.png> (which puts playwright on NODE_PATH and runs this): draw an svg at its own size with headless chromium.
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
const { chromium } = createRequire(import.meta.url)("playwright");
const [src, out] = process.argv.slice(2);
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1680, height: 1680 } });
await p.setContent("<body style='margin:0'>" + readFileSync(src, "utf8"));
await p.screenshot({ path: out, type: "png" });
await b.close();
