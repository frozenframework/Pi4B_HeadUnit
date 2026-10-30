// Generates one MapLibre style per theme in ../apps/styles/<id>.json
// from the Protomaps basemap layer set + the palettes in themes.json.
// Run on any computer with Node 18+:  cd tools && npm install && node gen-styles.mjs
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { layers, namedFlavor } from '@protomaps/basemaps';

const here = dirname(fileURLToPath(import.meta.url));
const { themes } = JSON.parse(readFileSync(join(here, 'themes.json'), 'utf8'));
const BASE = 'http://127.0.0.1:8765';
const outDir = join(here, '..', 'apps', 'styles');
mkdirSync(outDir, { recursive: true });

// Make road lines thicker than a desktop basemap: easier to read on a 7.9" screen at arm's length.
const ROAD_SCALE = 1.45;

function scaleInterp(expr, k) {
  if (!Array.isArray(expr) || expr[0] !== 'interpolate') return expr;
  const out = expr.slice(0, 3);
  for (let i = 3; i < expr.length; i += 2) {
    out.push(expr[i], typeof expr[i + 1] === 'number' ? +(expr[i + 1] * k).toFixed(3) : expr[i + 1]);
  }
  return out;
}

function flavorFor(t) {
  const m = t.map;
  const f = { ...namedFlavor(t.dark ? 'dark' : 'light') };
  Object.assign(f, {
    background: m.background, earth: m.earth, water: m.water,
    park_a: m.park, park_b: m.park, wood_a: m.wood, wood_b: m.wood, scrub_a: m.wood, scrub_b: m.wood,
    hospital: m.urban, industrial: m.industrial, school: m.urban, pedestrian: m.pedestrian, glacier: m.earth,
    sand: m.sand, beach: m.sand, aerodrome: m.urban, runway: m.runway, zoo: m.park, military: m.industrial, pier: m.urban,
    buildings: m.buildings,
    tunnel_other_casing: m.minor_casing, tunnel_minor_casing: m.minor_casing, tunnel_link_casing: m.minor_casing,
    tunnel_major_casing: m.major_casing, tunnel_highway_casing: m.highway_casing,
    tunnel_other: m.minor, tunnel_minor: m.minor, tunnel_link: m.link, tunnel_major: m.major, tunnel_highway: m.highway,
    minor_service_casing: m.minor_casing, minor_casing: m.minor_casing, link_casing: m.major_casing,
    major_casing_late: m.major_casing, highway_casing_late: m.highway_casing,
    other: m.minor, minor_service: m.minor, minor_a: m.minor, minor_b: m.minor, link: m.link,
    major_casing_early: m.major_casing, major: m.major, highway_casing_early: m.highway_casing, highway: m.highway,
    railway: m.rail, boundaries: m.boundaries,
    bridges_other_casing: m.minor_casing, bridges_minor_casing: m.minor_casing, bridges_link_casing: m.major_casing,
    bridges_major_casing: m.major_casing, bridges_highway_casing: m.highway_casing,
    bridges_other: m.minor, bridges_minor: m.minor, bridges_link: m.link, bridges_major: m.major, bridges_highway: m.highway,
    roads_label_minor: m.label_minor, roads_label_minor_halo: m.label_halo,
    roads_label_major: m.label, roads_label_major_halo: m.label_halo,
    ocean_label: m.water_label, subplace_label: m.label_minor, subplace_label_halo: m.label_halo,
    city_label: m.city_label, city_label_halo: m.label_halo, state_label: m.label_minor, state_label_halo: m.label_halo,
    country_label: m.label_minor, address_label: m.label_minor, address_label_halo: m.label_halo,
    regular: `${t.font} Regular`, bold: `${t.font} Bold`, italic: `${t.font} Regular`,
  });
  if (f.landcover) {
    f.landcover = { barren: m.sand, farmland: m.park, forest: m.wood, glacier: m.earth, grassland: m.park, scrub: m.wood, urban_area: m.urban };
  }
  return f;
}

for (const t of themes) {
  let L = layers('protomaps', flavorFor(t), { lang: 'en' });
  for (const l of L) {
    if (l.type === 'line' && l.id.startsWith('roads_') && l.paint) {
      if (l.paint['line-width']) l.paint['line-width'] = scaleInterp(l.paint['line-width'], ROAD_SCALE);
      if (l.paint['line-gap-width']) l.paint['line-gap-width'] = scaleInterp(l.paint['line-gap-width'], ROAD_SCALE);
    }
    if (l.layout && l.layout['icon-image']) {
      if (l.id === 'roads_shields') l.layout['icon-image'] = 'shield';
      else if (l.id === 'pois') l.layout['icon-image'] = 'poi';
      else if (l.id === 'places_locality') l.layout['icon-image'] = ['step', ['zoom'], 'townspot', 8, ''];
    }
    if (l.id === 'roads_shields' && l.paint) { l.paint['text-color'] = t.map.label_halo; }
    if (l.id === 'roads_oneway' && l.paint) { l.paint['icon-opacity'] = 0.6; }
    if (l.id === 'pois') { l.minzoom = 16.5; }  // keep the driving view uncluttered
  }
  // Glow layers: a wide, blurred copy drawn under the road it belongs to.
  for (const [kind, g] of Object.entries(t.glow || {})) {
    const srcId = kind === 'highway' ? 'roads_highway' : 'roads_major';
    const idx = L.findIndex(l => l.id === srcId);
    if (idx < 0) continue;
    const src = L[idx];
    const glow = {
      id: `hu_glow_${kind}`, type: 'line', source: 'protomaps', 'source-layer': 'roads', filter: src.filter, minzoom: 5,
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': g.color, 'line-width': scaleInterp(src.paint['line-width'], g.width), 'line-blur': g.blur, 'line-opacity': g.opacity },
    };
    // Put glows below all casings so the crisp road sits on top of its halo.
    const firstCasing = L.findIndex(l => l.id === 'roads_minor_service_casing');
    L.splice(firstCasing >= 0 ? firstCasing : idx, 0, glow);
  }
  const style = {
    version: 8,
    name: t.name,
    metadata: { 'hu:theme': { id: t.id, name: t.name, dark: t.dark, ui: t.ui, route: t.route, vehicle: t.vehicle, font: t.font, inspiredBy: t.inspiredBy } },
    glyphs: `${BASE}/apps/fonts/{fontstack}/{range}.pbf`,
    sprite: `${BASE}/apps/sprites/${t.id}/sprite`,
    sources: {
      protomaps: {
        type: 'vector',
        url: `pmtiles://${BASE}/maps/basemap.pmtiles`,
        attribution: '<a href="https://openstreetmap.org/copyright">© OpenStreetMap contributors</a> · Protomaps',
      },
    },
    layers: L,
  };
  writeFileSync(join(outDir, `${t.id}.json`), JSON.stringify(style));
  console.log(`styles/${t.id}.json  ${L.length} layers`);
}
writeFileSync(join(outDir, 'themes.json'), JSON.stringify(themes.map(t => ({ id: t.id, name: t.name, dark: t.dark, ui: t.ui, route: t.route, vehicle: t.vehicle, font: t.font, inspiredBy: t.inspiredBy, hudiyColor: t.hudiyColor })), null, 1));
