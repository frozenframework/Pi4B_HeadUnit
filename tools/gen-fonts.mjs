// Builds SDF glyph PBFs (Latin ranges) for each theme font into ../apps/fonts/<Font Weight>/<range>.pbf
// Fonts come from the @fontsource npm packages (SIL Open Font License).
import { buildFonts } from 'maplibre-font-maker-node';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const here = dirname(fileURLToPath(import.meta.url));
const pkgDir = (p) => dirname(require.resolve(`${p}/package.json`));
const fonts = [
  ['Rajdhani', '@fontsource/rajdhani', 'rajdhani'],
  ['Barlow Condensed', '@fontsource/barlow-condensed', 'barlow-condensed'],
  ['Saira Condensed', '@fontsource/saira-condensed', 'saira-condensed'],
  ['Exo 2', '@fontsource/exo-2', 'exo-2'],
];
const stacks = [];
for (const [name, pkg, slug] of fonts) {
  stacks.push({ font: join(pkgDir(pkg), 'files', `${slug}-latin-500-normal.woff2`), fontstack: `${name} Regular`, ranges: 'latin' });
  stacks.push({ font: join(pkgDir(pkg), 'files', `${slug}-latin-700-normal.woff2`), fontstack: `${name} Bold`, ranges: 'latin' });
}
await buildFonts({ output: join(here, '..', 'apps', 'fonts'), fontstacks: stacks });
