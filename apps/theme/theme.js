// Head-unit settings: map skin (also recolors Hudiy), driving options, map data, test logs.
import { api, getSettings, saveSettings, loadThemes, applyUiTheme, logEvent, toast } from '../common/hu.js';
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
let S = {};

function page(id) {
  document.querySelectorAll('.page').forEach(p => p.classList.toggle('hidden', p.id !== id));
  document.querySelectorAll('[data-page]').forEach(b => b.classList.toggle('on', b.dataset.page === id));
  if (id === 'maps') maps(); if (id === 'about') about(); if (id === 'display') display(); if (id === 'updates') updates();
}
async function cards() {
  const themes = await loadThemes(); const box = $('cards'); box.innerHTML = '';
  for (const t of themes) {
    const c = document.createElement('div'); c.className = 'tcard' + (t.id === S.theme ? ' on' : '');
    c.innerHTML = `<img src="previews/${t.id}.png" alt=""><div class="tname" style="font-family:'${t.font}'">${esc(t.name)}</div><div class="tsub">${esc(t.inspiredBy)}</div>`;
    c.onclick = async () => { S = await saveSettings({ theme: t.id }); await applyUiTheme(t.id); logEvent('skin_pick', { theme: t.id, from: 'settings' }); cards(); toast(`${t.name} applied`); };
    box.appendChild(c);
  }
}
function switches() {
  const lab = { voice: ['On', 'Off'], nightAuto: ['On', 'Off'], syncHudiyColors: ['On', 'Off'] };
  for (const k of Object.keys(lab)) {
    const b = $(k); b.textContent = S[k] ? lab[k][0] : lab[k][1]; b.classList.toggle('on', !!S[k]);
    b.onclick = async () => { S = await saveSettings({ [k]: !S[k] }); logEvent('setting', { k, v: S[k] }); switches(); };
  }
  const u = $('units'); u.textContent = S.units === 'imperial' ? 'Miles' : 'Kilometers'; u.classList.add('on');
  u.onclick = async () => { S = await saveSettings({ units: S.units === 'imperial' ? 'metric' : 'imperial' }); switches(); };
}
// ---- Display: brightness / auto-dim (hu-dimmer applies it within a quarter second)
let dimTimer = null;
function paintSlider(el) { el.style.setProperty('--pct', `${((el.value - el.min) / (el.max - el.min)) * 100}%`); }
async function display() {
  const st = await api('/api/display/status').catch(() => ({}));
  const d = st.settings || S.display || {};
  const auto = $('dimAuto'); auto.textContent = d.auto ? 'On' : 'Off'; auto.classList.toggle('on', !!d.auto);
  $('manualRow').classList.toggle('hidden', !!d.auto); $('autoRows').classList.toggle('hidden', !d.auto);
  for (const k of ['level', 'day', 'night']) {
    const el = $(k);
    if (document.activeElement !== el) { el.value = d[k]; }
    if (st.min) el.min = st.min;
    $(k + 'Val').textContent = `${el.value}%`; paintSlider(el);
  }
  $('dimStatus').textContent = st.method
    ? `${st.label} · now ${st.applied}% (${st.night ? 'headlights on' : 'headlights off'})${st.min ? ' · lowest ' + st.min + '%' : ''}`
    : 'Dimmer service is starting…';
}
function wireDisplay() {
  $('dimAuto').onclick = async () => { S = await saveSettings({ display: { auto: !(S.display?.auto ?? true) } }); display(); };
  for (const k of ['level', 'day', 'night']) {
    const el = $(k);
    el.oninput = () => {
      $(k + 'Val').textContent = `${el.value}%`; paintSlider(el);
      clearTimeout(dimTimer);
      dimTimer = setTimeout(async () => { S = await saveSettings({ display: { [k]: +el.value } }); logEvent('dim_set', { k, v: +el.value }); }, 120);
    };
  }
}

async function maps() {
  const st = await api('/api/status'); const m = st.maps;
  const yes = (v) => v ? '<span class="accent">installed</span>' : '<span style="color:var(--warn)">missing</span>';
  $('mapInfo').innerHTML = `Region: <b>${esc(m.name || m.region || 'not set')}</b><br>Map display: ${yes(m.basemap)} ${m.basemap_date ? '(' + esc(m.basemap_date) + ')' : ''}<br>Routing: ${yes(m.routing)} · Search: ${yes(m.search)} ${m.built ? '(built ' + esc(m.built.slice(0, 10)) + ')' : ''}`;
  const u = await api('/api/maps/update-status'); $('upd').textContent = u.message || '';
}
// ---- Updates: status written by bin/hu-update.py
async function updates() {
  const u = await api('/api/update/status').catch(() => ({}));
  const when = (t) => t ? new Date(t * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : 'never';
  const last = (u.history || []).filter(h => h.event === 'installed').pop();
  $('updInfo').innerHTML = `Installed version: <b class="accent">${esc(u.version || '?')}</b>${last ? ' (since ' + when(last.time) + ')' : ''}<br>` +
    `Automatic updates: <b>${u.enabled === false ? 'off' : 'on'}</b> · last check: <b>${when(u.last_check)}</b><br>` +
    (u.pending ? `Ready to install: <b class="accent">${esc(u.pending)}</b> (next key-on)` : '');
  $('updMsg').textContent = u.message || '';
  $('updCheck').disabled = ['checking', 'downloading'].includes(u.state);
}
async function about() {
  const r = await api('/api/logs/summary'); $('logInfo').innerHTML = `Logged days: <b>${r.days}</b> · events: <b>${r.events.toLocaleString()}</b> · size: <b>${(r.bytes / 1024).toFixed(0)} KB</b>`;
}
(async () => {
  S = await getSettings(); await applyUiTheme(S.theme);
  document.querySelectorAll('[data-page]').forEach(b => b.onclick = () => page(b.dataset.page));
  $('update').onclick = async () => { const r = await api('/api/maps/update', { json: {} }); toast(r.message || 'Started'); setTimeout(maps, 3000); };
  $('updCheck').onclick = async () => { const r = await api('/api/update/check', { json: {} }); toast(r.message || 'Checking'); setTimeout(updates, 2000); };
  cards(); switches(); wireDisplay(); page(new URLSearchParams(location.search).get('page') || 'skins');
  setInterval(() => { if (!$('maps').classList.contains('hidden')) maps(); }, 5000);
  setInterval(() => { if (!$('display').classList.contains('hidden')) display(); }, 2000);
  setInterval(() => { if (!$('updates').classList.contains('hidden')) updates(); }, 3000);
})();
