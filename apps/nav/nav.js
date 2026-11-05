// Themed offline navigation for the head unit.
// Map: MapLibre + Protomaps PMTiles. Routing/search: hu-bridge (Valhalla + SQLite). GPS: gpsd via SSE.
import * as maplibregl from '../lib/maplibre-gl.mjs';
import { Protocol } from '../lib/pmtiles.mjs';
import { api, getSettings, saveSettings, loadThemes, applyUiTheme, logEvent, fmtDist, fmtTime, toast, icons, onHudiy } from '../common/hu.js';
import { initI18n, t, has, langTag } from '../common/i18n.js';

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const SIM = params.has('sim');            // drive along the route by itself (bench testing)
const PREVIEW = params.has('preview');    // static render for skin previews
if (params.has('clean')) document.documentElement.classList.add('clean');
if (PREVIEW || params.has('clean') || params.has('norail')) document.documentElement.classList.add('norail');   // skin previews: map only

const state = {
  settings: null, theme: null, map: null, pos: null, heading: 0, speed: 0, follow: true,
  route: null, dest: null, progress: 0, offCount: 0, lastReroute: 0, spoken: new Set(), favorites: [],
  units: 'imperial', simTimer: null,
};

maplibregl.setWorkerUrl(new URL('../lib/maplibre-gl-worker.mjs', import.meta.url).href);
const protocol = new Protocol();
maplibregl.addProtocol('pmtiles', protocol.tile);

// ------------------------------------------------------------------ geometry helpers
const R = 6371008.8, rad = Math.PI / 180;
function hav(a, b) {
  const dLat = (b[1] - a[1]) * rad, dLon = (b[0] - a[0]) * rad;
  const s = Math.sin(dLat / 2) ** 2 + Math.cos(a[1] * rad) * Math.cos(b[1] * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(s));
}
function bearing(a, b) {
  const y = Math.sin((b[0] - a[0]) * rad) * Math.cos(b[1] * rad);
  const x = Math.cos(a[1] * rad) * Math.sin(b[1] * rad) - Math.sin(a[1] * rad) * Math.cos(b[1] * rad) * Math.cos((b[0] - a[0]) * rad);
  return (Math.atan2(y, x) / rad + 360) % 360;
}
// Nearest point on segment a-b to p, in a local flat projection (fine at street scale).
function projectOnSeg(p, a, b) {
  const kx = Math.cos(p[1] * rad) * R * rad, ky = R * rad;
  const ax = (a[0] - p[0]) * kx, ay = (a[1] - p[1]) * ky, bx = (b[0] - p[0]) * kx, by = (b[1] - p[1]) * ky;
  const dx = bx - ax, dy = by - ay, L2 = dx * dx + dy * dy;
  let t = L2 ? -(ax * dx + ay * dy) / L2 : 0; t = Math.max(0, Math.min(1, t));
  const x = ax + t * dx, y = ay + t * dy;
  return { t, d: Math.hypot(x, y) };
}
function cumulative(shape) {
  const c = [0];
  for (let i = 1; i < shape.length; i++) c.push(c[i - 1] + hav(shape[i - 1], shape[i]));
  return c;
}
function pointAlong(shape, cum, dist) {
  let i = cum.findIndex(c => c >= dist);
  if (i <= 0) return { p: shape[0], i: 0 };
  const f = (dist - cum[i - 1]) / ((cum[i] - cum[i - 1]) || 1);
  const a = shape[i - 1], b = shape[i];
  return { p: [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f], i: i - 1 };
}

// ------------------------------------------------------------------ maneuver arrows (Valhalla maneuver types)
const TURN = { 8: 0, 22: 0, 17: 0, 9: 45, 23: 30, 18: 40, 20: 45, 37: 30, 10: 90, 11: 135, 12: 180, 13: -180,
  14: -135, 15: -90, 16: -45, 24: -30, 19: -40, 21: -45, 38: -30, 25: 0, 7: 0, 1: 0, 2: 0, 3: 0 };
function manIcon(m) {
  if (!m) return '';
  if ([4, 5, 6].includes(m.type)) return icons.flag.replace('class="icon"', '');
  if ([26, 27].includes(m.type)) return '<svg viewBox="0 0 64 64"><circle cx="32" cy="34" r="12"/><path d="M32 22V6M24 14l8-8 8 8"/></svg>';
  const a = TURN[m.type] ?? 0;
  if (Math.abs(a) === 180) return `<svg viewBox="0 0 64 64"><path d="M40 58V26a12 12 0 0 0-24 0v8" transform="${a > 0 ? 'scale(-1,1) translate(-64,0)' : ''}"/><path d="M8 26l8 10 8-10" transform="${a > 0 ? 'scale(-1,1) translate(-64,0)' : ''}"/></svg>`;
  // stem from the bottom, then bend by angle a
  const r = a * rad, len = 22, cx = 32, cy = 30;
  const ex = cx + Math.sin(r) * len, ey = cy - Math.cos(r) * len;
  const hx1 = ex + Math.sin(r - 2.5) * 10, hy1 = ey - Math.cos(r - 2.5) * 10, hx2 = ex + Math.sin(r + 2.5) * 10, hy2 = ey - Math.cos(r + 2.5) * 10;
  return `<svg viewBox="0 0 64 64"><path d="M32 60V${cy} L${ex.toFixed(1)} ${ey.toFixed(1)}"/><path d="M${hx1.toFixed(1)} ${hy1.toFixed(1)}L${ex.toFixed(1)} ${ey.toFixed(1)}L${hx2.toFixed(1)} ${hy2.toFixed(1)}"/></svg>`;
}

// Turn text for languages the router can't write (Chinese, Tagalog): built from the maneuver type.
const MAN_KEY = { 1: 'depart', 2: 'depart', 3: 'depart', 4: 'arrive', 5: 'arrive', 6: 'arrive', 7: 'continue', 8: 'continue',
  9: 'slight_right', 10: 'right', 11: 'sharp_right', 12: 'uturn', 13: 'uturn', 14: 'sharp_left', 15: 'left', 16: 'slight_left',
  17: 'ramp', 18: 'ramp', 19: 'ramp', 20: 'exit_right', 21: 'exit_left', 22: 'keep_straight', 23: 'keep_right', 24: 'keep_left',
  25: 'merge', 37: 'merge', 38: 'merge', 26: 'roundabout', 27: 'roundabout_exit', 28: 'ferry', 29: 'continue' };
function phrase(m) {
  if (!m) return '';
  if (!has('m.continue')) return m.instruction || '';          // the router already wrote it in this language
  const k = MAN_KEY[m.type] || 'continue', st = m.streets?.[0];
  return t('m.' + k) + (st && k !== 'arrive' ? t('m.onto', { street: st }) : '');
}
const spoken = (m) => has('m.continue') ? phrase(m) : (m?.verbal || m?.instruction || '');
function sayDist(m) {
  return fmtDist(m, state.units).replace(/ mi$/, ' ' + t('unit.miles')).replace(/ ft$/, ' ' + t('unit.feet'))
    .replace(/ km$/, ' ' + t('unit.kilometers')).replace(/ m$/, ' ' + t('unit.meters'));
}

// ------------------------------------------------------------------ map + theme
function vehicleEl(t) {
  const el = document.createElement('div'); el.className = 'hu-vehicle';
  el.innerHTML = `<svg viewBox="0 0 54 54"><defs><filter id="vg" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="2.5"/></filter></defs>
    <path d="M27 6 L44 46 L27 37 L10 46 Z" fill="${t.vehicle.fill}" opacity=".55" filter="url(#vg)"/>
    <path d="M27 6 L44 46 L27 37 L10 46 Z" fill="${t.vehicle.fill}" stroke="${t.vehicle.stroke}" stroke-width="2.5" stroke-linejoin="round"/></svg>`;
  return el;
}
function destEl(t) {
  const el = document.createElement('div'); el.className = 'hu-dest';
  el.innerHTML = `<svg viewBox="0 0 34 34"><circle cx="17" cy="17" r="11" fill="${t.route.color}" stroke="${t.ui.bg}" stroke-width="4"/><circle cx="17" cy="17" r="4" fill="${t.ui.bg}"/></svg>`;
  return el;
}

function addRouteLayers() {
  const map = state.map, t = state.theme;
  if (map.getSource('hu-route')) return;
  map.addSource('hu-route', { type: 'geojson', data: routeGeo() });
  const w = (k) => ['interpolate', ['exponential', 1.5], ['zoom'], 8, 3 * k, 13, 6 * k, 17, 16 * k];
  const before = map.getLayer('roads_labels_minor') ? 'roads_labels_minor' : undefined;
  map.addLayer({ id: 'hu-route-glow', type: 'line', source: 'hu-route', layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': t.route.glow, 'line-width': w(2.6), 'line-blur': 8, 'line-opacity': 0.55 } }, before);
  map.addLayer({ id: 'hu-route-casing', type: 'line', source: 'hu-route', layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': t.route.casing, 'line-width': w(1.45) } }, before);
  map.addLayer({ id: 'hu-route-line', type: 'line', source: 'hu-route', layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': t.route.color, 'line-width': w(1) } }, before);
}
function routeGeo() {
  if (!state.route) return { type: 'FeatureCollection', features: [] };
  const rest = state.route.shape.slice(Math.max(0, state.progress));
  return { type: 'Feature', geometry: { type: 'LineString', coordinates: rest.length > 1 ? rest : state.route.shape }, properties: {} };
}
function refreshRoute() { state.map?.getSource('hu-route')?.setData(routeGeo()); }

async function setTheme(id, persist = true) {
  const themes = await loadThemes();
  state.theme = themes.find(t => t.id === id) || themes[0];
  await applyUiTheme(state.theme.id);
  if (state.map) {
    state.map.setStyle(`/apps/styles/${state.theme.id}.json`, { diff: false });
    state.map.once('style.load', () => { addRouteLayers(); refreshRoute(); });
    resetMarkers();
  }
  if (persist) { state.settings = await saveSettings({ theme: state.theme.id }); }
  applyNight();
}

function resetMarkers() {
  const pos = state.pos || state.map.getCenter().toArray();
  state.vehicle?.remove();
  state.vehicle = new maplibregl.Marker({ element: vehicleEl(state.theme), rotationAlignment: 'map', pitchAlignment: 'map' })
    .setLngLat(pos).setRotation(state.heading).addTo(state.map);
  if (state.dest) { state.destMarker?.remove(); state.destMarker = new maplibregl.Marker({ element: destEl(state.theme) }).setLngLat(state.dest.lnglat).addTo(state.map); }
}

function applyNight() {
  const night = state.settings?.nightAuto && state.night && !state.theme?.dark;
  $('nightveil').classList.toggle('hidden', !night);
}

// ------------------------------------------------------------------ GPS
function startGps() {
  if (SIM || PREVIEW) return;
  const es = new EventSource('/api/gps/stream');
  es.onmessage = (e) => {
    const f = JSON.parse(e.data);
    if (!(f.mode >= 2) || f.lat == null) { $('speed').textContent = '--'; return; }
    onFix([f.lon, f.lat], f.track, f.speed || 0);
  };
}

function onFix(p, track, speed) {
  const prev = state.pos;
  state.pos = p; state.speed = speed;
  if (track != null && speed > 1.5) state.heading = track;
  else if (prev && hav(prev, p) > 4) state.heading = bearing(prev, p);
  state.vehicle?.setLngLat(p).setRotation(state.heading);
  const mph = state.units === 'imperial' ? speed * 2.23694 : speed * 3.6;
  $('speed').textContent = Math.round(mph);
  if (state.route) guide();
  if (state.follow) camera();
}

function camera() {
  if (currentApp !== 'map') return;                // covered by Music / Radio / Settings: no map animation work
  const nav = !!state.route;
  const z = nav ? (state.speed > 25 ? 15.2 : state.speed > 13 ? 15.8 : 16.4) : Math.max(state.map.getZoom(), 14.5);
  state.map.easeTo({ center: state.pos, bearing: state.speed > 2 || nav ? state.heading : state.map.getBearing(),
    pitch: nav ? 55 : 0, zoom: z, padding: nav ? { left: 340, top: 60, bottom: 0, right: 80 } : { left: 0, top: 0, bottom: 0, right: 0 },
    duration: SIM ? 900 : 950, easing: (x) => x });
}

// ------------------------------------------------------------------ routing + guidance
async function routeTo(dest, reason = 'new') {
  const from = state.pos || state.map.getCenter().toArray();
  const t0 = performance.now();
  const r = await api('/api/route', { json: { from, to: dest.lnglat, heading: state.speed > 3 ? state.heading : null } });
  if (r.error) { toast(r.message || t('nav.no_route')); logEvent('route_fail', { reason, err: r.error }); return false; }
  state.route = { ...r, cum: cumulative(r.shape) };
  state.dest = dest; state.progress = 0; state.offCount = 0; state.spoken.clear();
  refreshRoute(); resetMarkers();
  document.body.classList.add('navigating'); $('guide').classList.remove('hidden'); $('destSheet').classList.add('hidden');
  logEvent('route_ok', { reason, ms: Math.round(performance.now() - t0), miles: +(r.summary.length).toFixed(1) });
  if (reason === 'new') {
    const b = r.shape.reduce((bb, c) => bb.extend(c), new maplibregl.LngLatBounds(r.shape[0], r.shape[0]));
    state.map.fitBounds(b, { padding: { left: 360, right: 110, top: 40, bottom: 40 }, duration: 800, pitch: 0, bearing: 0 });
    state.follow = false; setTimeout(() => { state.follow = true; if (state.pos) camera(); }, 3500);
    { const first = spoken(r.maneuvers[0]); say(first || t('nav.route_started'), 'route_started', first ? { then: first } : {}); }
  }
  guide();
  if (SIM) startSim();
  return true;
}

function endRoute(arrived = false) {
  logEvent('route_end', { arrived });
  state.route = null; state.dest = null; state.destMarker?.remove(); state.destMarker = null;
  refreshRoute(); clearInterval(state.simTimer);
  document.body.classList.remove('navigating'); $('guide').classList.add('hidden');
  if (arrived) { say(t('nav.arrived'), 'arrived'); toast(t('nav.arrived')); }
  if (state.pos) camera();
}

function guide() {
  const rt = state.route; if (!rt) return;
  const p = state.pos || rt.shape[0];
  const lo = Math.max(0, state.progress - 5), hi = Math.min(rt.shape.length - 1, state.progress + 80);
  let best = { d: Infinity, i: state.progress, t: 0 };
  for (let i = lo; i < hi; i++) {
    const pr = projectOnSeg(p, rt.shape[i], rt.shape[i + 1]);
    if (pr.d < best.d) best = { d: pr.d, i, t: pr.t };
  }
  state.progress = best.i;
  const along = rt.cum[best.i] + best.t * (rt.cum[best.i + 1] - rt.cum[best.i] || 0);
  const total = rt.cum[rt.cum.length - 1];
  const remaining = Math.max(0, total - along);
  // off-route check (needs 3 bad fixes in a row so a GPS jump doesn't trigger it)
  if (best.d > 45 && state.speed > 2) state.offCount++; else state.offCount = 0;
  if (state.offCount >= 3 && Date.now() - state.lastReroute > 12000) {
    state.lastReroute = Date.now(); state.offCount = 0;
    toast(t('nav.rerouting')); say(t('nav.rerouting'), 'rerouting'); routeTo(state.dest, 'reroute'); return;
  }
  if (remaining < 25) { endRoute(true); return; }
  const idx = rt.maneuvers.findIndex((m, k) => k > 0 && rt.cum[m.begin] > along + 3);
  const next = rt.maneuvers[idx] || rt.maneuvers[rt.maneuvers.length - 1];
  const then = rt.maneuvers[idx + 1];
  const toNext = Math.max(0, rt.cum[next.begin] - along);
  $('manIcon').innerHTML = manIcon(next);
  $('manDist').textContent = fmtDist(toNext, state.units);
  $('manText').textContent = phrase(next);
  $('thenRow').textContent = then && toNext < 600 ? t('nav.then', { text: phrase(then) }) : (next.streets[0] ? t('nav.on_street', { street: next.streets[0] }) : '');
  const secs = rt.summary.time * (remaining / (total || 1));
  $('etaTime').textContent = new Date(Date.now() + secs * 1000).toLocaleTimeString(langTag(), { hour: 'numeric', minute: '2-digit' });
  $('etaLeft').textContent = fmtTime(secs);
  $('etaDist').textContent = fmtDist(remaining, state.units);
  // voice prompts: about half a mile out (highway) or 1000 ft (city), then right before the turn
  const far = state.speed > 20 ? 800 : 300;
  const key = `${idx}`;
  if (toNext < far && toNext > 120 && !state.spoken.has(key + 'f')) { state.spoken.add(key + 'f'); say(t('nav.in_dist', { dist: sayDist(toNext), text: spoken(next) }), 'in_dist', { dist: sayDist(toNext), text: spoken(next) }); }
  if (toNext <= 60 + state.speed * 3 && !state.spoken.has(key + 'n')) { state.spoken.add(key + 'n'); say(spoken(next), 'turn'); }
  if (best.i % 5 === 0) refreshRoute();
}

// key lets the chosen voice pack use its own line (Settings > Driving > Voice); text is the plain wording
function say(text, key = '', vars = {}) { if (state.settings?.voice) api('/api/say', { json: { text, key, vars } }).catch(() => {}); }

// Bench-test driver: moves the car along the route at ~35 mph.
function startSim() {
  clearInterval(state.simTimer);
  const rt = state.route; let d = 0; const v = +(params.get('sim') || 15.6) || 15.6;
  state.simTimer = setInterval(() => {
    if (!state.route) return clearInterval(state.simTimer);
    d += v;
    const { p, i } = pointAlong(rt.shape, rt.cum, d);
    const h = bearing(rt.shape[i], rt.shape[Math.min(i + 1, rt.shape.length - 1)]);
    onFix(p, h, v);
  }, 1000);
}

// ------------------------------------------------------------------ destination by long-press
function setupLongPress() {
  const map = state.map; let timer = null, startPt = null;
  map.on('touchstart', (e) => { if (e.originalEvent.touches.length !== 1) return; startPt = e.point; timer = setTimeout(() => pick(e.lngLat), 650); });
  map.on('touchmove', (e) => { if (startPt && Math.hypot(e.point.x - startPt.x, e.point.y - startPt.y) > 12) clearTimeout(timer); });
  map.on('touchend', () => clearTimeout(timer));
  map.on('contextmenu', (e) => pick(e.lngLat));
  map.on('dragstart', () => { state.follow = false; });
}
function pick(lngLat) {
  const feats = state.map.queryRenderedFeatures(state.map.project(lngLat), { layers: ['pois', 'roads_labels_major', 'roads_labels_minor'].filter(l => state.map.getLayer(l)) });
  const name = feats.find(f => f.properties.name)?.properties.name || t('nav.dropped_pin');
  const dist = state.pos ? t('nav.away', { dist: fmtDist(hav(state.pos, lngLat.toArray()), state.units) }) : '';
  showDest({ name, sub: dist, lnglat: lngLat.toArray() });
  logEvent('long_press');
}
function showDest(d) {
  state.pending = d;
  $('destName').textContent = d.name; $('destSub').textContent = d.sub || '';
  $('destSheet').classList.remove('hidden');
}

// ------------------------------------------------------------------ search with on-screen keyboard
function buildKeyboard() {
  const rows = ['1234567890', 'QWERTYUIOP', 'ASDFGHJKL', 'ZXCVBNM'];
  const kbd = $('kbd'); kbd.innerHTML = '';
  rows.forEach((r, ri) => {
    const row = document.createElement('div'); row.className = 'krow';
    for (const ch of r) row.appendChild(key(ch, () => typeKey(ch)));
    if (ri === 2) row.appendChild(key('⌫', () => typeKey('back'), 'wide'));
    if (ri === 3) { row.appendChild(key(t('nav.space'), () => typeKey(' '), 'wide')); row.appendChild(key(t('nav.clear'), () => typeKey('clear'), 'wide')); }
    kbd.appendChild(row);
  });
  function key(label, fn, cls = '') { const b = document.createElement('button'); b.textContent = label; b.className = cls; b.onpointerdown = (e) => { e.preventDefault(); fn(); }; return b; }
}
let query = '', searchTimer = null;
function typeKey(k) {
  if (k === 'back') query = query.slice(0, -1); else if (k === 'clear') query = ''; else query += k.toLowerCase();
  $('searchText').textContent = query;
  clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 280);
}
async function runSearch() {
  const list = $('results'); list.innerHTML = '';
  if (query.trim().length < 2) return;
  const near = state.pos || state.map.getCenter().toArray();
  const res = await api(`/api/search?q=${encodeURIComponent(query)}&near=${near.join(',')}`);
  if (!res.length) { list.innerHTML = `<div class="item muted">${esc(t('nav.no_matches'))}</div>`; return; }
  for (const r of res) {
    const el = document.createElement('div'); el.className = 'item';
    const dist = fmtDist(hav(near, [r.lon, r.lat]), state.units);
    el.innerHTML = `<div><div>${esc(r.name)}</div><div class="sub">${esc([r.kind, r.detail].filter(Boolean).join(' · '))} · ${dist}</div></div>`;
    el.onclick = () => { closeSheets(); state.map.flyTo({ center: [r.lon, r.lat], zoom: 15.5, pitch: 0 }); state.follow = false; showDest({ name: r.name, sub: t('nav.away', { dist }), lnglat: [r.lon, r.lat] }); logEvent('search_pick'); };
    list.appendChild(el);
  }
}
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// ------------------------------------------------------------------ favorites
async function openFavorites() {
  state.favorites = await api('/api/favorites');
  const list = $('favList'); list.innerHTML = '';
  if (!state.favorites.length) list.innerHTML = `<div class="item muted">${esc(t('nav.fav_empty'))}</div>`;
  state.favorites.forEach((f, i) => {
    const el = document.createElement('div'); el.className = 'item';
    el.innerHTML = `<div style="flex:1"><div>${esc(f.name)}</div><div class="sub">${state.pos ? fmtDist(hav(state.pos, f.lnglat), state.units) : ''}</div></div><button class="ghost" data-i="${i}">${esc(t('common.remove'))}</button>`;
    el.onclick = (e) => {
      if (e.target.dataset.i) { state.favorites.splice(i, 1); api('/api/favorites', { json: state.favorites }); openFavorites(); return; }
      closeSheets(); routeTo({ name: f.name, lnglat: f.lnglat });
    };
    list.appendChild(el);
  });
  $('favPanel').classList.remove('hidden');
}
async function saveFavorite(d) {
  const favs = await api('/api/favorites');
  favs.unshift({ name: d.name, lnglat: d.lnglat });
  await api('/api/favorites', { json: favs }); toast(t('nav.saved_fav'));
}

// ------------------------------------------------------------------ skin picker
async function openThemes() {
  const themes = await loadThemes(); const box = $('themeCards'); box.innerHTML = '';
  for (const t of themes) {
    const c = document.createElement('div'); c.className = 'tcard' + (t.id === state.theme.id ? ' on' : '');
    c.innerHTML = `<img src="../theme/previews/${t.id}.png" alt=""><div class="tname" style="font-family:'${t.font}'">${esc(t.name)}</div><div class="tsub">${esc(t.inspiredBy)}</div>`;
    c.onclick = async () => { logEvent('skin_pick', { theme: t.id }); await setTheme(t.id); closeSheets(); };
    box.appendChild(c);
  }
  $('themePanel').classList.remove('hidden');
}
function closeSheets() { document.querySelectorAll('.sheet').forEach(s => s.classList.add('hidden')); }

// ------------------------------------------------------------------ apps from the map screen (left rail)
// iPod & USB, AM/FM Radio and Head Unit Settings open in a layer over the map; the map (and any route)
// keeps running underneath. Music and radio stay loaded once opened (playback runs on the head unit
// either way); Settings starts fresh each time and its Back to map button closes it.
const APPS = { music: '../music/index.html', radio: '../radio/index.html', settings: '../theme/index.html?embedded=1' };
const RAIL_ICONS = {
  map: '<svg class="icon" viewBox="0 0 24 24"><path d="M3 6.5l6-2.5 6 2.5 6-2.5v13.5l-6 2.5-6-2.5-6 2.5z"/><path d="M9 4v13.5M15 6.5V20"/></svg>',
  music: '<svg class="icon" viewBox="0 0 24 24"><path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/></svg>',
  radio: '<svg class="icon" viewBox="0 0 24 24"><rect x="3" y="8" width="18" height="12" rx="2"/><path d="M7 8l10-5"/><circle cx="15.5" cy="14" r="3"/><path d="M6.5 12h4M6.5 15h4M6.5 18h4"/></svg>',
  settings: icons.gear,
};
let currentApp = 'map', settingsLang = null;
function railOn(id) { document.querySelectorAll('[data-app-btn]').forEach(b => b.classList.toggle('on', b.dataset.appBtn === id)); }
function openApp(id) {
  if (id === 'map') { showMap(); return; }
  closeSheets();
  const layer = $('appLayer');
  let f = layer.querySelector(`iframe[data-app="${id}"]`);
  if (id === 'settings' && f) { f.remove(); f = null; }               // Settings: fresh every time
  if (!f) {
    f = document.createElement('iframe'); f.dataset.app = id; f.title = id; f.src = APPS[id];
    layer.appendChild(f);
  }
  if (id === 'settings') settingsLang = state.settings?.language;
  layer.querySelectorAll('iframe').forEach(x => { x.hidden = x !== f; });
  layer.classList.remove('hidden'); currentApp = id; railOn(id);
  logEvent('app_open', { app: id, from: 'map' });
}
async function showMap() {
  const wasSettings = currentApp === 'settings';
  $('appLayer').classList.add('hidden'); currentApp = 'map'; railOn('map');
  const sf = $('appLayer').querySelector('iframe[data-app="settings"]'); if (sf) sf.remove();
  state.map?.resize();
  if (state.pos && state.follow) camera();          // catch up with where the car went meanwhile
  if (!wasSettings) return;
  const s = await getSettings().catch(() => null); if (!s) return;     // pick up what changed in Settings
  if (s.language !== settingsLang) { location.reload(); return; }      // new language: redraw everything
  state.settings = s; state.units = s.units || 'imperial';
  $('speedUnit').textContent = state.units === 'imperial' ? 'mph' : 'km/h';
  if (s.theme && s.theme !== state.theme?.id) await setTheme(s.theme, false); else applyNight();
  if (state.route) guide();
}
function setupRail() {
  document.querySelectorAll('[data-app-btn]').forEach(b => {
    b.querySelector('.ico').innerHTML = RAIL_ICONS[b.dataset.appBtn];
    b.onclick = () => openApp(b.dataset.appBtn);
  });
}
const openSettings = () => openApp('settings');
const PALETTE_ICON = '<svg class="icon" viewBox="0 0 24 24"><path d="M12 3a9 9 0 1 0 0 18c1.2 0 1.8-.8 1.8-1.7 0-1.2-1-1.5-1-2.6 0-.9.7-1.5 1.7-1.5H17a4 4 0 0 0 4-4C21 6.6 17 3 12 3z"/><circle cx="7.5" cy="11" r="1.2"/><circle cx="10" cy="7" r="1.2"/><circle cx="14.5" cy="7" r="1.2"/></svg>';   // map skin (the gear is Settings)
window.addEventListener('message', (e) => { if (e.origin === location.origin && e.data?.type === 'hu-close-settings') showMap(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && currentApp !== 'map') showMap(); });

// ------------------------------------------------------------------ boot
// ------------------------------------------------------------------ from the phone (CarPlay / Android Auto via Hudiy)
// Its next turn while Apple Maps / Google Maps / Waze guides on the phone, now playing, calls; the phone
// button brings CarPlay to the front. The phone speaks its own directions, so nothing is spoken here.
const PHONE_TURN = { turn: 10, slight_turn: 9, sharp_turn: 11, u_turn: 12, straight: 8, name_change: 8, depart: 1,
  destination: 4, roundabout_enter: 26, roundabout: 26, roundabout_exit: 27, merge: 25, on_ramp: 17, off_ramp: 20, fork: 23 };
const LEFT_OF = { 10: 15, 9: 16, 11: 14, 12: 13, 17: 19, 20: 21, 23: 24 };
function phoneArrow(n) {
  let type = PHONE_TURN[n.type] ?? 8;
  if (n.side === 'left' && LEFT_OF[type]) type = LEFT_OF[type];
  return manIcon({ type });
}
const PHONE_ICON = '<svg class="icon" viewBox="0 0 24 24"><rect x="7" y="2.5" width="10" height="19" rx="2.2"/><path d="M10.5 18.5h3"/></svg>';
const phoneSeen = { navV: -1, coverV: -1, src: null };
async function phoneTick() {
  const p = await api('/api/phone').catch(() => null);
  if (!p || p.error) return;
  const srcName = p.source === 'android_auto' ? 'Android Auto' : 'CarPlay';
  // phone button
  const btn = $('phoneBtn'); btn.classList.toggle('hidden', !p.connected);
  if (phoneSeen.src !== srcName) { btn.setAttribute('aria-label', srcName); phoneSeen.src = srcName; }
  // next turn
  const n = p.nav || {}, showTurn = n.active && (n.text || n.distance);
  $('phoneTurn').classList.toggle('hidden', !showTurn);
  if (showTurn) {
    if (n.v !== phoneSeen.navV) {
      phoneSeen.navV = n.v;
      $('ptIcon').innerHTML = n.has_icon ? `<img src="/api/phone/icon?v=${n.v}" alt="">` : phoneArrow(n);
      $('ptText').textContent = n.text || '';
      $('ptSrc').textContent = (n.source === 'android_auto' ? 'Android Auto' : 'CarPlay');
      logEvent('phone_turn', { type: n.type, src: n.source });
    }
    $('ptDist').textContent = n.distance || '';
  }
  // call
  const c = p.call || {};
  $('callChip').classList.toggle('hidden', !c.state);
  const CALL = { incoming: t('nav.call_incoming', { name: c.name || c.number || '' }), ringing: t('nav.call_ringing', { name: c.name || c.number || '' }),
    active: t('nav.call_active', { name: c.name || c.number || '' }) };
  if (c.state) $('callChip').textContent = CALL[c.state] || '';
  // now playing from the phone
  const m = p.media || {}, showMusic = (m.source === 'carplay' || m.source === 'android_auto') && (m.title || m.artist);
  $('nowPlaying').classList.toggle('hidden', !showMusic); document.body.classList.toggle('np', !!showMusic);
  if (showMusic) {
    $('npTitle').textContent = m.title || ''; $('npArtist').textContent = [m.artist, m.album].filter(Boolean).join(' · ');
    if (m.v !== phoneSeen.coverV) {
      phoneSeen.coverV = m.v; const img = $('npCover');
      if (m.has_cover) { img.src = `/api/phone/cover?v=${m.v}`; img.hidden = false; } else img.hidden = true;
    }
    $('npPlay').innerHTML = m.playing ? icons.pause : icons.play;
  }
}
function setupPhone() {
  $('phoneBtn').innerHTML = PHONE_ICON; $('npPrev').innerHTML = icons.prev; $('npNext').innerHTML = icons.next; $('npPlay').innerHTML = icons.play;
  $('phoneBtn').onclick = () => api('/api/phone/show', { json: {} }).catch(() => {});
  $('nowPlaying').querySelector('.npText').onclick = () => api('/api/phone/show', { json: {} }).catch(() => {});
  $('npPlay').onclick = () => api('/api/phone/toggle', { json: {} }).then(phoneTick).catch(() => {});
  $('npPrev').onclick = () => api('/api/phone/previous', { json: {} }).catch(() => {});
  $('npNext').onclick = () => api('/api/phone/next', { json: {} }).catch(() => {});
  if (!PREVIEW) { phoneTick(); setInterval(() => { if (currentApp === 'map') phoneTick(); }, 1000); }
}

async function main() {
  state.settings = await getSettings().catch(() => ({ theme: 'neon-overdrive', units: 'imperial', voice: false }));
  state.units = state.settings.units || 'imperial';
  await initI18n(state.settings.language);
  $('speedUnit').textContent = state.units === 'imperial' ? 'mph' : 'km/h';
  const themes = await loadThemes();
  const startTheme = params.get('theme') || state.settings.theme;
  state.theme = themes.find(t => t.id === startTheme) || themes[0];
  await applyUiTheme(state.theme.id);
  const center = params.get('center')?.split(',').map(Number) || state.settings.lastPos || [-99.0, 31.0];
  const t0 = performance.now();
  state.map = new maplibregl.Map({
    container: 'map', style: `/apps/styles/${state.theme.id}.json`, center, zoom: +(params.get('zoom') || (state.settings.lastPos ? 15 : 5.5)),
    bearing: +(params.get('bearing') || 0), pitch: +(params.get('pitch') || 0), maxPitch: 60, attributionControl: { compact: true },
    fadeDuration: 0, pixelRatio: 1, renderWorldCopies: false, canvasContextAttributes: { antialias: false, preserveDrawingBuffer: PREVIEW },
  });
  window.huMap = state.map;
  state.map.on('load', async () => {
    logEvent('map_load', { ms: Math.round(performance.now() - t0), theme: state.theme.id });
    addRouteLayers(); resetMarkers();
    const st = await api('/api/status').catch(() => null);
    if (st && !st.maps.basemap) $('nomaps').classList.remove('hidden');
    state.night = !!st?.night; applyNight();
    if (params.get('route')) {
      const [a, b] = params.get('route').split(';').map(s => s.split(',').map(Number));
      state.pos = a; await routeTo({ name: 'Preview', lnglat: b }, PREVIEW ? 'preview' : 'new');
      if (PREVIEW) { state.follow = false; const k = +(params.get('progress') || 0.3); const { p, i } = pointAlong(state.route.shape, state.route.cum, state.route.cum.at(-1) * k);
        state.pos = p; state.heading = bearing(state.route.shape[i], state.route.shape[i + 1]); state.progress = i; guide(); refreshRoute(); resetMarkers();
        state.map.jumpTo({ center: p, bearing: state.heading, pitch: 55, zoom: +(params.get('zoom') || 16), padding: { left: 340, top: 60, right: 80, bottom: 0 } }); }
    }
    state.map.once('idle', () => { document.body.dataset.ready = '1'; if (!PREVIEW && !SIM) say('', 'greet'); });
  });
  state.map.on('error', (e) => logEvent('map_error', { msg: String(e.error?.message || e.error).slice(0, 160) }));
  // frame-rate sampling, so test drives show whether the Pi keeps up with a given skin
  let frames = 0; state.map.on('render', () => frames++);
  setInterval(() => { if (frames) logEvent('fps', { fps: Math.round(frames / 30), theme: state.theme.id, nav: !!state.route }); frames = 0; }, 30000);
  setInterval(() => { if (state.pos && !SIM) saveSettings({ lastPos: state.pos }).catch(() => {}); }, 120000);
  setInterval(async () => {
    const st = await api('/api/status').catch(() => null); if (!st) return;
    state.night = !!st.night; applyNight();
    if (st.theme && st.theme !== state.theme?.id && !PREVIEW && !params.get('theme')) setTheme(st.theme, false);   // day/night skins: sunrise/sunset
  }, 10000);

  $('searchBtn').innerHTML = icons.search; $('favBtn').innerHTML = icons.star; $('centerBtn').innerHTML = icons.center; $('themeBtn').innerHTML = PALETTE_ICON;
  $('destFav').innerHTML = icons.star; document.querySelectorAll('.closeBtn').forEach(b => { b.innerHTML = icons.close; b.onclick = closeSheets; });
  $('searchBtn').onclick = () => { buildKeyboard(); $('searchPanel').classList.remove('hidden'); };
  $('favBtn').onclick = openFavorites;
  $('themeBtn').onclick = openThemes;
  $('moreSettings').onclick = openSettings;
  $('centerBtn').onclick = () => { state.follow = true; if (state.pos) camera(); else toast(t('nav.waiting_gps')); };
  $('endBtn').onclick = () => endRoute(false);
  $('destCancel').onclick = () => $('destSheet').classList.add('hidden');
  $('destGo').onclick = () => routeTo(state.pending);
  $('destFav').onclick = () => saveFavorite(state.pending);
  setupLongPress();
  setupRail();
  setupPhone();
  startGps();
  onHudiy('onActivatedChanged', () => { if (window.hudiy?.activated) state.map?.resize(); });
}
main();
