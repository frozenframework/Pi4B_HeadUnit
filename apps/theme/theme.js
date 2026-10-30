// Head-unit settings: map skin (also recolors Hudiy), driving options, map data, test logs.
import { api, getSettings, saveSettings, loadThemes, applyUiTheme, logEvent, toast } from '../common/hu.js';
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
let S = {};

function page(id) {
  document.querySelectorAll('.page').forEach(p => p.classList.toggle('hidden', p.id !== id));
  document.querySelectorAll('[data-page]').forEach(b => b.classList.toggle('on', b.dataset.page === id));
  if (id === 'maps') maps(); if (id === 'about') about();
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
async function maps() {
  const st = await api('/api/status'); const m = st.maps;
  const yes = (v) => v ? '<span class="accent">installed</span>' : '<span style="color:var(--warn)">missing</span>';
  $('mapInfo').innerHTML = `Region: <b>${esc(m.name || m.region || 'not set')}</b><br>Map display: ${yes(m.basemap)} ${m.basemap_date ? '(' + esc(m.basemap_date) + ')' : ''}<br>Routing: ${yes(m.routing)} · Search: ${yes(m.search)} ${m.built ? '(built ' + esc(m.built.slice(0, 10)) + ')' : ''}`;
  const u = await api('/api/maps/update-status'); $('upd').textContent = u.message || '';
}
async function about() {
  const r = await api('/api/logs/summary'); $('logInfo').innerHTML = `Logged days: <b>${r.days}</b> · events: <b>${r.events.toLocaleString()}</b> · size: <b>${(r.bytes / 1024).toFixed(0)} KB</b>`;
}
(async () => {
  S = await getSettings(); await applyUiTheme(S.theme);
  document.querySelectorAll('[data-page]').forEach(b => b.onclick = () => page(b.dataset.page));
  $('update').onclick = async () => { const r = await api('/api/maps/update', { json: {} }); toast(r.message || 'Started'); setTimeout(maps, 3000); };
  cards(); switches(); page(new URLSearchParams(location.search).get('page') || 'skins');
  setInterval(() => { if (!$('maps').classList.contains('hidden')) maps(); }, 5000);
})();
