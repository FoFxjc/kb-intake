// Copies the pdf.js data files that are fetched at run time (CMaps, standard fonts, the JPEG 2000 / ICC decoders)
// next to the build, so the page viewer works offline: src/kb_intake/service/static/pdfjs/.
import { cp, mkdir } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const from = resolve(here, '../node_modules/pdfjs-dist');
const to = resolve(here, '../../src/kb_intake/service/static/pdfjs');

await mkdir(to, { recursive: true });
for (const dir of ['cmaps', 'standard_fonts', 'wasm', 'iccs']) {
  await cp(resolve(from, dir), resolve(to, dir), {
    recursive: true,
    // the scripting sandbox (QuickJS) is not used: the viewer never runs PDF JavaScript
    filter: (src) => !/quickjs/.test(src),
  });
}
console.log('copy-pdfjs-assets: static/pdfjs/{cmaps,standard_fonts,wasm,iccs}');
