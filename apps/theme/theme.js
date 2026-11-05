// Head-unit settings: map skin, display, driving, region & language, maps, updates, developer, test logs.
import { api, getSettings, saveSettings, loadThemes, applyUiTheme, followTheme, logEvent, toast, icons } from '../common/hu.js';
import { initI18n, t, LANGS, lang, langTag } from '../common/i18n.js';
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
let S = {};

// "Back to map": opened from the map (embedded) -> close the layer; opened from Hudiy's menu -> Hudiy's
// home screen, which is the map when Start on the map is on (else Hudiy's dashboard).
const EMBEDDED = new URLSearchParams(location.search).has('embedded') && window.parent !== window;
async function exitSettings() {
  if (EMBEDDED) { window.parent.postMessage({ type: 'hu-close-settings' }, location.origin); return; }
  const r = await api('/api/hudiy/action', { json: { action: 'go_home' } }).catch(() => null);
  if (!r?.ok) location.href = '../nav/index.html';               // Hudiy not reachable: at least show the map
}

function page(id) {
  document.querySelectorAll('.page').forEach(p => p.classList.toggle('hidden', p.id !== id));
  document.querySelectorAll('[data-page]').forEach(b => b.classList.toggle('on', b.dataset.page === id));
  ({ maps, about, display, updates, region, dev })[id]?.();
}
// Day and night skins: with the switch on, the cards pick the skin for the selected slot (day or night);
// the bridge changes skins at sunrise and sunset (bin/hu_sun.py), using the GPS position.
let slot = null;
const clock = (x) => x ? new Date(x * 1000).toLocaleTimeString(langTag(), { hour: 'numeric', minute: '2-digit' }) : '–';
async function cards() {
  const themes = await loadThemes(); const box = $('cards'); box.innerHTML = '';
  const dn = !!S.dayNight, name = (id) => themes.find(x => x.id === id)?.name || id;
  const sun = dn ? await api('/api/sun').catch(() => null) : null;
  if (dn && !slot) slot = sun?.night ? 'night' : 'day';
  $('dayNight').textContent = onOff(dn); $('dayNight').classList.toggle('on', dn);
  $('slots').classList.toggle('hidden', !dn);
  $('sunNow').textContent = dn && sun
    ? t('set.daynight_on_sub', { rise: clock(sun.sunrise), set: clock(sun.sunset), now: sun.night ? t('set.night') : t('set.day') })
    : t('set.daynight_off_sub');
  $('slotDay').innerHTML = `<span class="lbl">${esc(t('set.day_skin'))}</span><b>${esc(name(S.dayTheme))}</b>`;
  $('slotNight').innerHTML = `<span class="lbl">${esc(t('set.night_skin'))}</span><b>${esc(name(S.nightTheme))}</b>`;
  $('slotDay').classList.toggle('on', slot === 'day'); $('slotNight').classList.toggle('on', slot === 'night');
  for (const th of themes) {
    const picked = dn ? th.id === (slot === 'night' ? S.nightTheme : S.dayTheme) : th.id === S.theme;
    const c = document.createElement('div'); c.className = 'tcard' + (picked ? ' on' : '');
    const tags = dn ? [th.id === S.dayTheme ? t('set.day') : '', th.id === S.nightTheme ? t('set.night') : ''].filter(Boolean) : [];
    c.innerHTML = `<img src="previews/${th.id}.png" alt=""><div class="tname" style="font-family:'${th.font}'">${esc(th.name)}</div><div class="tsub">${esc(th.inspiredBy)}</div>` +
      (tags.length ? `<div class="ttags">${tags.map(x => `<span>${esc(x)}</span>`).join('')}</div>` : '');
    c.onclick = async () => {
      S = await saveSettings(dn ? { [slot === 'night' ? 'nightTheme' : 'dayTheme']: th.id } : { theme: th.id });
      await applyUiTheme(S.theme); logEvent('skin_pick', { theme: th.id, from: 'settings', slot: dn ? slot : null }); cards();
      toast(t('set.applied', { name: th.name }));
    };
    box.appendChild(c);
  }
}
function wireSkins() {
  $('dayNight').onclick = async () => { S = await saveSettings({ dayNight: !S.dayNight }); slot = null; await applyUiTheme(S.theme); logEvent('setting', { k: 'dayNight', v: S.dayNight }); cards(); };
  $('slotDay').onclick = () => { slot = 'day'; cards(); };
  $('slotNight').onclick = () => { slot = 'night'; cards(); };
}
const onOff = (v) => v ? t('common.on') : t('common.off');
function switches() {
  for (const k of ['startOnMap', 'voice', 'nightAuto', 'syncHudiyColors']) {
    const b = $(k); b.textContent = onOff(S[k]); b.classList.toggle('on', !!S[k]);
    b.onclick = async () => { S = await saveSettings({ [k]: !S[k] }); logEvent('setting', { k, v: S[k] }); switches(); };
  }
  const ps = $('phoneScreen'), onMap = S.phoneScreen !== 'carplay';
  ps.textContent = onMap ? t('set.phone_map') : t('set.phone_carplay'); ps.classList.toggle('on', onMap);
  ps.onclick = async () => { S = await saveSettings({ phoneScreen: onMap ? 'carplay' : 'map' }); logEvent('setting', { k: 'phoneScreen', v: S.phoneScreen }); switches(); };
  const u = $('units'); u.textContent = S.units === 'imperial' ? t('set.miles') : t('set.kilometers'); u.classList.add('on');
  u.onclick = async () => { S = await saveSettings({ units: S.units === 'imperial' ? 'metric' : 'imperial' }); switches(); };
}

// ---- Display: brightness / auto-dim (hu-dimmer applies it within a quarter second)
let dimTimer = null;
function paintSlider(el) { el.style.setProperty('--pct', `${((el.value - el.min) / (el.max - el.min)) * 100}%`); }
async function display() {
  const st = await api('/api/display/status').catch(() => ({}));
  const d = st.settings || S.display || {};
  const auto = $('dimAuto'); auto.textContent = onOff(d.auto); auto.classList.toggle('on', !!d.auto);
  $('manualRow').classList.toggle('hidden', !!d.auto); $('autoRows').classList.toggle('hidden', !d.auto);
  for (const k of ['level', 'day', 'night']) {
    const el = $(k);
    if (document.activeElement !== el) { el.value = d[k]; }
    if (st.min) el.min = st.min;
    $(k + 'Val').textContent = `${el.value}%`; paintSlider(el);
  }
  $('dimStatus').textContent = st.method
    ? [t('set.dim_status', { label: st.label, pct: st.applied }), st.night ? t('set.lights_on') : t('set.lights_off'), st.min ? t('set.lowest', { pct: st.min }) : ''].filter(Boolean).join(' · ')
    : t('set.dim_starting');
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

// ---- Region & language
let SYS = null;
let regionNames = null;   // country names in the screen language (falls back to the system's English list)
const countryName = (c) => { try { return (regionNames ||= new Intl.DisplayNames([langTag()], { type: 'region' })).of(c.code) || c.name; } catch { return c.name; } };
const tzLabel = (z) => z.split('/').slice(1).join(' / ').replace(/_/g, ' ') || z;
const tzTime = (z) => { try { return new Date().toLocaleTimeString(langTag(), { timeZone: z, hour: 'numeric', minute: '2-digit' }); } catch { return ''; } };
async function region() {
  SYS = await api('/api/system').catch(() => null) || SYS || {};
  $('langBtn').textContent = LANGS.find(l => l.code === lang())?.name || 'English';
  const c = (SYS.countries || []).find(x => x.code === SYS.country);
  $('countryBtn').textContent = c ? countryName(c) : (SYS.country || 'US');
  $('tzBtn').textContent = SYS.timezone ? tzLabel(SYS.timezone) : '—';
  $('tzNow').textContent = SYS.timezone ? t('set.local_time', { time: tzTime(SYS.timezone) }) : '';
}
function pick(title, items, current, onPick) {
  $('pickTitle').textContent = title;
  const list = $('pickList'); list.innerHTML = '';
  for (const it of items) {
    const el = document.createElement('div'); el.className = 'item' + (it.value === current ? ' on' : '');
    el.innerHTML = `<div>${esc(it.label)}</div>${it.sub ? `<div class="sub">${esc(it.sub)}</div>` : ''}`;
    el.onclick = async () => { $('picker').classList.add('hidden'); await onPick(it.value); };
    list.appendChild(el);
  }
  $('picker').classList.remove('hidden');
  list.querySelector('.on')?.scrollIntoView({ block: 'center' });
}
async function setSystem(body) {
  const r = await api('/api/system', { json: body }).catch(e => ({ ok: false, error: String(e) }));
  if (!r.ok) { toast(t('set.save_failed', { err: r.error || '?' }), 4000); return false; }
  return r;
}
function wireRegion() {
  $('langBtn').onclick = () => pick(t('set.language'), LANGS.map(l => ({ value: l.code, label: l.name })), lang(), async (code) => {
    const r = await setSystem({ language: code }); if (!r) return;
    S = await getSettings(); await initI18n(code); regionNames = null; switches(); region(); toast(t('set.saved'));
    $('regionNote').classList.remove('hidden');
  });
  $('countryBtn').onclick = () => pick(t('set.country'), (SYS?.countries || []).map(c => ({ value: c.code, label: countryName(c), sub: c.code })).sort((a, b) => (b.value === 'US') - (a.value === 'US') || a.label.localeCompare(b.label, langTag())), SYS?.country, async (code) => {
    if (await setSystem({ country: code })) { toast(t('set.saved')); region(); }
  });
  $('tzBtn').onclick = () => pick(t('set.timezone'), (SYS?.timezones || []).map(z => ({ value: z, label: tzLabel(z), sub: tzTime(z) })), SYS?.timezone, async (z) => {
    if (await setSystem({ timezone: z })) { toast(t('set.saved')); region(); }
  });
  $('pickClose').innerHTML = icons.close; $('pickClose').onclick = () => $('picker').classList.add('hidden');
}

// ---- Developer
async function dev() {
  SYS = await api('/api/system').catch(() => null) || SYS || {};
  const b = $('sshBtn'); b.textContent = onOff(SYS.ssh); b.classList.toggle('on', !!SYS.ssh);
  $('pwWarn').classList.toggle('hidden', !SYS.default_password);
  $('devInfo').innerHTML = [
    `${esc(t('set.user'))}: <b>${esc(SYS.user || '?')}</b> · ${esc(t('set.hostname'))}: <b>${esc(SYS.hostname || '?')}</b>`,
    `${esc(t('set.ip'))}: <b>${esc((SYS.addresses || []).join(', ') || t('set.no_network'))}</b>`,
    `${esc(t('set.version'))}: <b>${esc(SYS.version || '?')}</b>`,
  ].join('<br>');
}
function wireDev() {
  $('termBtn').onclick = async () => { const r = await api('/api/dev/terminal', { json: {} }); if (!r.ok) toast(r.error || '?', 4000); };
  $('oskBtn').onclick = async () => { const r = await api('/api/dev/keyboard', { json: {} }); if (!r.ok) toast(t('set.osk_failed'), 4000); };
  $('sshBtn').onclick = async () => { if (await setSystem({ ssh: !SYS?.ssh })) dev(); };
}

async function maps() {
  const st = await api('/api/status'); const m = st.maps;
  const yes = (v) => v ? `<span class="accent">${esc(t('set.installed'))}</span>` : `<span style="color:var(--warn)">${esc(t('set.missing'))}</span>`;
  $('mapInfo').innerHTML = `${esc(t('set.map_region'))}: <b>${esc(m.name || m.region || t('set.not_set'))}</b><br>${esc(t('set.map_display'))}: ${yes(m.basemap)} ${m.basemap_date ? '(' + esc(m.basemap_date) + ')' : ''}<br>${esc(t('set.routing'))}: ${yes(m.routing)} · ${esc(t('set.search'))}: ${yes(m.search)} ${m.built ? '(' + esc(t('set.built', { date: m.built.slice(0, 10) })) + ')' : ''}`;
  mapProgress();
}
// ---- Map download progress bar (status from bin/hu-update-maps.sh + hu-maps-progress.py)
const ACTIVE = ['preparing', 'downloading', 'retrying', 'checking'];
function bytes(n) {
  const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0; while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return n.toLocaleString(langTag(), { maximumFractionDigits: i >= 3 ? 1 : 0 }) + ' ' + u[i];
}
function timeLeft(s) {
  if (s < 60) return t('set.map_lt1');
  const m = Math.round(s / 60);
  return m >= 60 ? t('set.map_hm', { h: Math.floor(m / 60), m: m % 60 }) : t('set.map_m', { m });
}
async function mapProgress() {
  const u = await api('/api/maps/update-status').catch(() => null); if (!u) return;
  const box = $('mapProg'), bar = $('mapBar'), pctOf = (p) => (p ?? 0).toLocaleString(langTag(), { maximumFractionDigits: 0 }) + '%';
  const active = ACTIVE.includes(u.phase), running = !!u.running;
  box.classList.toggle('hidden', !active);
  $('update').disabled = active && running;
  $('upd').textContent = '';
  if (!active) {                                   // finished, failed, or never run
    $('upd').textContent = u.phase === 'done' ? t('set.map_done') : (u.message || '');
    return;
  }
  const stopped = !running, pct = u.percent;
  box.classList.toggle('stopped', stopped);
  box.classList.toggle('busy', running && (u.phase === 'preparing' || (u.phase === 'retrying' && pct == null)));
  bar.style.width = (u.phase === 'checking' ? 100 : (pct ?? 0)) + '%';
  $('mapPct').textContent = u.phase === 'checking' ? '100%' : (pct != null ? pctOf(pct) : '');
  let line = '';
  if (stopped) line = t('set.map_stopped', { pct: pctOf(pct) });
  else if (u.phase === 'preparing') line = t('set.map_preparing');
  else if (u.phase === 'retrying') line = t('set.map_retrying');
  else if (u.phase === 'checking') line = t('set.map_checking');
  else line = u.eta != null ? t('set.map_left', { time: timeLeft(u.eta) }) : t('set.map_calc');
  $('mapEta').textContent = line;
  const det = [];
  if (u.done != null && u.total) det.push(t('set.map_of', { done: bytes(u.done), total: bytes(u.total) }));
  if (u.rate && running && u.phase === 'downloading') det.push(bytes(u.rate) + '/s');
  $('mapDet').textContent = det.join(' · ');
}
async function about() {
  const r = await api('/api/logs/summary');
  $('logInfo').innerHTML = `${esc(t('set.logged_days'))}: <b>${r.days}</b> · ${esc(t('set.events'))}: <b>${r.events.toLocaleString(langTag())}</b> · ${esc(t('set.size'))}: <b>${(r.bytes / 1024).toFixed(0)} KB</b>`;
}
// ---- Updates: status written by bin/hu-update.py
async function updates() {
  const u = await api('/api/update/status').catch(() => ({}));
  const when = (x) => x ? new Date(x * 1000).toLocaleString(langTag(), { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : t('set.never');
  const last = (u.history || []).filter(h => h.event === 'installed').pop();
  $('updInfo').innerHTML = `${esc(t('set.installed_version'))}: <b class="accent">${esc(u.version || '?')}</b>${last ? ' (' + esc(t('set.since', { date: when(last.time) })) + ')' : ''}<br>` +
    `${esc(t('set.auto_updates'))}: <b>${esc(onOff(u.enabled !== false))}</b> · ${esc(t('set.last_check'))}: <b>${esc(when(u.last_check))}</b><br>` +
    (u.pending ? `${esc(t('set.ready_install'))}: <b class="accent">${esc(u.pending)}</b> (${esc(t('set.next_key_on'))})` : '');
  $('updMsg').textContent = u.message || '';
  $('updCheck').disabled = ['checking', 'downloading', 'installing'].includes(u.state);
  // go back to an earlier version still kept on the head unit
  const older = u.older || [], box = $('goBackBtns');
  $('goBack').classList.toggle('hidden', !older.length);
  if (box.dataset.list !== older.join(',')) {
    box.dataset.list = older.join(','); box.innerHTML = '';
    for (const v of older.slice(0, 2)) {
      const b = document.createElement('button'); b.className = 'ghost'; b.textContent = t('set.go_back_to', { v });
      b.onclick = () => askGoBack(v, u.version); box.appendChild(b);
    }
  }
}
function askGoBack(v, cur) {
  $('goBackQ').textContent = t('set.go_back_ask', { v, cur });
  $('goBackYes').textContent = t('set.go_back_to', { v });
  $('goBackAsk').classList.remove('hidden');
  $('goBackNo').onclick = () => $('goBackAsk').classList.add('hidden');
  $('goBackYes').onclick = async () => {
    $('goBackYes').disabled = true;
    const r = await api('/api/update/goback', { json: { version: v } }).catch(() => ({ ok: false, message: '?' }));
    toast(r.ok ? t('set.going_back', { v }) : (r.message || '?'), 6000);
    $('goBackAsk').classList.add('hidden'); $('goBackYes').disabled = false; updates();
  };
}

(async () => {
  S = await getSettings(); await applyUiTheme(S.theme); await initI18n(S.language);
  document.querySelectorAll('[data-page]').forEach(b => b.onclick = () => page(b.dataset.page));
  $('update').onclick = async () => { const r = await api('/api/maps/update', { json: {} }); toast(r.message || ''); setTimeout(mapProgress, 1500); };
  $('updCheck').onclick = async () => { const r = await api('/api/update/check', { json: {} }); toast(r.message || ''); setTimeout(updates, 2000); };
  $('exitBtn').onclick = exitSettings;
  if (!EMBEDDED && S.startOnMap === false) $('exitBtn').textContent = t('set.exit');
  wireSkins(); cards(); switches(); wireDisplay(); followTheme((s) => { S = s; cards(); }); wireRegion(); wireDev(); page(new URLSearchParams(location.search).get('page') || 'skins');
  setInterval(() => { if (!$('maps').classList.contains('hidden')) mapProgress(); }, 2000);
  setInterval(() => { if (!$('maps').classList.contains('hidden')) maps(); }, 15000);
  setInterval(() => { if (!$('display').classList.contains('hidden')) display(); }, 2000);
  setInterval(() => { if (!$('updates').classList.contains('hidden')) updates(); }, 3000);
})();
