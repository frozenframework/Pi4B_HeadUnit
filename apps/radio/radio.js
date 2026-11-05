// AM/FM radio through the RTL-SDR Blog V4. (Hudiy's own FM player adds RDS station names.)
import { api, getSettings, applyUiTheme, followTheme, logEvent, toast } from '../common/hu.js';
import { initI18n, t } from '../common/i18n.js';
const $ = (id) => document.getElementById(id);
const BANDS = { FM: { min: 87900, max: 107900, step: 200, big: 1000, unit: 'MHz' }, AM: { min: 530, max: 1700, step: 10, big: 100, unit: 'kHz' } };
let band = 'FM', freq = { FM: 101100, AM: 740 }, playing = false, presets = { FM: [], AM: [] };

const fmt = (b, f) => b === 'FM' ? (f / 1000).toFixed(1) : String(f);
function render() {
  const B = BANDS[band], f = freq[band];
  $('fnum').textContent = fmt(band, f); $('funit').textContent = B.unit;
  $('needle').style.left = `calc(${((f - B.min) / (B.max - B.min)) * 100}% - 2px)`;
  document.querySelectorAll('[data-band]').forEach(b => b.classList.toggle('on', b.dataset.band === band));
  $('power').textContent = playing ? t('ra.stop') : t('ra.play');
  const grid = $('pgrid'); grid.innerHTML = '';
  for (let i = 0; i < 6; i++) {
    const p = presets[band]?.[i]; const b = document.createElement('button');
    b.textContent = p ? fmt(band, p) : t('ra.preset', { n: i + 1 }); if (!p) b.className = 'empty';
    let t; b.onpointerdown = () => { t = setTimeout(() => { t = null; savePreset(i); }, 900); };
    b.onpointerup = () => { if (t) { clearTimeout(t); if (p) { freq[band] = p; tune(); } } };
    grid.appendChild(b);
  }
}
async function savePreset(i) {
  presets[band] = presets[band] || []; presets[band][i] = freq[band];
  await api('/api/radio/presets', { json: { presets } }); toast(t('ra.saved', { f: fmt(band, freq[band]), n: i + 1 })); logEvent('preset_save', { band }); render();
}
let tuneTimer;
function step(n) {
  const B = BANDS[band]; let f = freq[band] + n;
  if (f > B.max) f = B.min; if (f < B.min) f = B.max; freq[band] = f; render();
  if (playing) { clearTimeout(tuneTimer); tuneTimer = setTimeout(tune, 450); }   // wait until the tapping stops
}
async function tune() {
  const r = await api('/api/radio/tune', { json: { band, freq: freq[band] } });
  if (r.error) { toast(r.message || r.error, 4000); playing = false; } else playing = true;
  render(); status();
}
async function status() {
  const s = await api('/api/radio/status').catch(() => null); if (!s) return;
  playing = s.playing; $('state').textContent = !s.available ? t('ra.no_tuner') : s.playing ? t('ra.playing', { band: s.band }) : t('ra.stopped');
  render();
}
(async () => {
  const st = await getSettings().catch(() => ({})); await applyUiTheme(st.theme); await initI18n(st.language); followTheme();
  if (st.radio) { band = st.radio.band || band; freq.FM = st.radio.FM || freq.FM; freq.AM = st.radio.AM || freq.AM; presets = st.radio.presets || presets; }
  document.querySelectorAll('[data-band]').forEach(b => b.onclick = () => { band = b.dataset.band; render(); if (playing) tune(); });
  $('dn').onclick = () => step(-BANDS[band].step); $('up').onclick = () => step(BANDS[band].step);
  $('dn2').onclick = () => step(-BANDS[band].big); $('up2').onclick = () => step(BANDS[band].big);
  $('power').onclick = async () => { if (playing) { await api('/api/radio/stop', { json: {} }); playing = false; render(); status(); } else tune(); };
  render(); status(); setInterval(status, 5000);
})();
