// bash render.sh <in.svg> <out.png> (which puts playwright on NODE_PATH and runs this): draw an svg at its own size with headless chromium.
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
const { chromium } = createRequire(import.meta.url)("playwright");
const [src, out] = process.argv.slice(2);
const b = await chromium.launch();
const svg = readFileSync(src, "utf8");
const size = Number(/<svg[^>]* width="(\d+)"/.exec(svg)[1]);          // the picture's own size: 1680 for a background, 1024 for the icon
const p = await b.newPage({ viewport: { width: size, height: size } });
await p.setContent("<body style='margin:0'>" + svg);
await p.screenshot({ path: out, type: "png" });
await b.close();
