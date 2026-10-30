// Copies the web libraries and UI fonts into ../apps/lib so the Pi never needs a CDN.
import { cpSync, mkdirSync } from 'node:fs';
import { buildSync } from 'esbuild';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const here = dirname(fileURLToPath(import.meta.url));
const lib = join(here, '..', 'apps', 'lib');
mkdirSync(join(lib, 'fonts'), { recursive: true });
const ml = join(dirname(require.resolve('maplibre-gl/package.json')), 'dist');
for (const f of ['maplibre-gl.mjs', 'maplibre-gl-shared.mjs', 'maplibre-gl-worker.mjs', 'maplibre-gl.css']) cpSync(join(ml, f), join(lib, f));
// pmtiles imports 'fflate' by bare name, which browsers can't resolve: bundle it into one file.
buildSync({ entryPoints: [join(dirname(require.resolve('pmtiles/package.json')), 'dist', 'esm', 'index.js')], bundle: true, format: 'esm', minify: true, outfile: join(lib, 'pmtiles.mjs') });
const fonts = { 'rajdhani': [500, 700], 'barlow-condensed': [500, 700], 'saira-condensed': [500, 700], 'exo-2': [500, 700], 'orbitron': [600, 800] };
for (const [slug, ws] of Object.entries(fonts)) {
  const d = join(dirname(require.resolve(`@fontsource/${slug}/package.json`)), 'files');
  for (const w of ws) cpSync(join(d, `${slug}-latin-${w}-normal.woff2`), join(lib, 'fonts', `${slug}-${w}.woff2`));
}
console.log('libs copied');
