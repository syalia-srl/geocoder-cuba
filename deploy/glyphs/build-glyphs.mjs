// Render a TTF/OTF into MapLibre SDF glyph ranges (PBF), one file per range.
// Invoked by bin/build-glyphs; fontnik is installed into a scratch dir there.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { createRequire } from "node:module";

const fontnik = createRequire(import.meta.url)("fontnik");

// Latin + accents + punctuation is all Cuban place names need. Ranges MapLibre
// asks for outside this set 404 and the label is dropped, so keep the tail.
const RANGES = [
  [0, 255],     // Basic Latin + Latin-1 Supplement (ñ á é í ó ú ü)
  [256, 511],   // Latin Extended-A
  [8192, 8447], // General Punctuation (– ’ “ ”)
];

const [fontPath, outDir] = process.argv.slice(2);
if (!fontPath || !outDir) {
  console.error("usage: build-glyphs.mjs <font.ttf> <out-dir>");
  process.exit(2);
}

const font = readFileSync(fontPath);
mkdirSync(outDir, { recursive: true });

for (const [start, end] of RANGES) {
  const pbf = await new Promise((res, rej) =>
    fontnik.range({ font, start, end }, (err, buf) => (err ? rej(err) : res(buf))),
  );
  const out = join(outDir, `${start}-${end}.pbf`);
  writeFileSync(out, pbf);
  console.log(`${out} (${pbf.length} bytes)`);
}
