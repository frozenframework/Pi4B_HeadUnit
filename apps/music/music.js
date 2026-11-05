// iPod Nano / USB music browser, plus what CarPlay / Android Auto is playing. The library is read by MPD from song tags,
// so the iPod's scrambled file names (F00/ABCD.m4a) never show up here.
import { api, getSettings, applyUiTheme, followTheme, logEvent, icons, toast } from '../common/hu.js';
import { initI18n, t } from '../common/i18n.js';
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
let stack = [];

async function show(view) {
  const list = $('list'); list.innerHTML = `<div class="item muted">${esc(t('common.loading'))}</div>`;
  document.querySelectorAll('[data-tab]').forEach(b => b.classList.toggle('on', b.dataset.tab === view.tab));
  $('back').classList.toggle('hidden', stack.length < 2);
  $('crumb').textContent = [view.artist, view.album].filter(Boolean).join(' › ');
  let res;
  if (view.tab === 'artists') res = await api('/api/music/artists');
  else if (view.tab === 'albums') res = await api('/api/music/albums' + (view.artist ? `?artist=${encodeURIComponent(view.artist)}` : ''));
  else res = await api('/api/music/tracks?' + new URLSearchParams(Object.fromEntries(Object.entries({ artist: view.artist, album: view.album }).filter(([, v]) => v))));
  list.innerHTML = '';
  if (res.error) { list.innerHTML = `<div class="item muted">${esc(t('mu.service_down', { err: res.error }))}</div>`; return; }
  if (!res.length) { list.innerHTML = `<div class="item muted">${esc(t('mu.no_music'))}</div>`; return; }
  if (view.tab === 'tracks') {
    const all = document.createElement('div'); all.className = 'item playall'; all.textContent = '▶  ' + t('mu.play_all', { n: res.length });
    all.onclick = () => play(res, 0); list.appendChild(all);
    res.forEach((t, i) => {
      const el = document.createElement('div'); el.className = 'item';
      el.innerHTML = `<span class="n">${esc(t.track?.split('/')[0] || i + 1)}</span><div class="t"><div>${esc(t.title)}</div><div class="sub">${esc(t.artist)}${t.album && !view.album ? ' · ' + esc(t.album) : ''}</div></div><span class="muted">${t.time ? mmss(t.time) : ''}</span>`;
      el.onclick = () => play(res, i); list.appendChild(el);
    });
  } else {
    for (const name of res) {
      const el = document.createElement('div'); el.className = 'item'; el.innerHTML = `<div class="t">${esc(name)}</div><span class="muted">›</span>`;
      el.onclick = () => push(view.tab === 'artists' ? { tab: 'albums', artist: name } : { tab: 'tracks', artist: view.artist, album: name });
      list.appendChild(el);
    }
  }
}
function push(v) { stack.push(v); show(v); }
function reset(tab) { stack = [{ tab }]; show(stack[0]); }
async function play(tracks, start) {
  logEvent('music_play', { n: tracks.length });
  const r = await api('/api/music/control', { json: { action: 'play_files', files: tracks.map(t => t.file), start } });
  if (r.error) toast(t('mu.cant_play', { err: r.message || r.error })); else poll();
}
const ctl = (action) => api('/api/music/control', { json: { action } }).then(poll);

// Which player the left panel shows: the iPod / USB player, or what the phone plays through CarPlay /
// Android Auto (Hudiy reports title, artist, album, cover and position; the buttons go to the phone).
let srcChoice = null;           // null = automatic: the phone while it plays and the iPod / USB doesn't
let showing = 'usb', phoneName = 'CarPlay', coverV = -1;
const secs = (l) => (l || '').split(':').reduce((a, x) => a * 60 + (+x || 0), 0);
async function poll() {
  const [s, p] = await Promise.all([api('/api/music/status').catch(() => null), api('/api/phone').catch(() => null)]);
  const pm = p?.media || {}, phoneHas = !!(p && !p.error && (pm.source === 'carplay' || pm.source === 'android_auto') && (pm.title || pm.artist));
  phoneName = pm.source === 'android_auto' ? 'Android Auto' : 'CarPlay';
  // the CarPlay tab is there whenever the head unit can hear Hudiy; it fills in while the phone plays
  const linked = !!(p && !p.error);
  $('srcPhone').textContent = phoneName; $('srcTabs').classList.toggle('hidden', !linked);
  const usbPlaying = s && !s.error && s.state === 'play';
  showing = linked && (srcChoice === 'phone' || (srcChoice === null && phoneHas && (pm.playing || !usbPlaying))) ? 'phone' : 'usb';
  document.querySelectorAll('[data-src]').forEach(b => b.classList.toggle('on', b.dataset.src === showing));
  document.body.classList.toggle('phone', showing === 'phone');
  $('openPhone').classList.toggle('hidden', showing !== 'phone');
  if (showing === 'phone') {
    $('openPhone').textContent = t('mu.open_phone', { name: phoneName });
    $('src').textContent = phoneHas ? t('mu.from_phone', { name: phoneName }) : t('mu.phone_idle', { name: phoneName });
    $('title').textContent = (phoneHas && pm.title) || t('mu.nothing');
    $('artist').textContent = [pm.artist, pm.album].filter(Boolean).join(' — ');
    $('toggle').innerHTML = pm.playing ? icons.pause : icons.play;
    if (!phoneHas) { $('cover').hidden = true; coverV = -1; $('artist').textContent = ''; $('fill').style.width = '0'; $('el').textContent = ''; $('du').textContent = ''; return; }
    if (pm.v !== coverV) { coverV = pm.v; const c = $('cover'); if (pm.has_cover) { c.src = `/api/phone/cover?v=${pm.v}`; c.hidden = false; } else c.hidden = true; }
    const el = secs(pm.position), du = secs(pm.duration);
    $('fill').style.width = du ? `${Math.min(100, (el / du) * 100)}%` : '0';
    $('el').textContent = pm.position || '0:00'; $('du').textContent = pm.duration || '';
    return;
  }
  $('cover').hidden = true; coverV = -1;
  if (!s || s.error) { $('src').textContent = t('mu.offline'); return; }
  const ipod = s.sources?.find(x => x.ipod);
  const scan = s.updating ? ' · ' + t('mu.scanning') : '';
  $('src').textContent = ipod ? t('mu.ipod', { name: ipod.name }) + scan : s.sources?.length ? t('mu.usb') + scan : t('mu.no_source');
  $('title').textContent = s.song?.title || t('mu.nothing');
  $('artist').textContent = [s.song?.artist, s.song?.album].filter(Boolean).join(' — ');
  $('toggle').innerHTML = s.state === 'play' ? icons.pause : icons.play;
  $('shuffle').classList.toggle('on', s.random);
  const d = s.duration || s.song?.time || 0;
  $('fill').style.width = d ? `${Math.min(100, (s.elapsed / d) * 100)}%` : '0';
  $('el').textContent = mmss(s.elapsed || 0); $('du').textContent = mmss(d);
}
// buttons go to whichever player is showing
const phoneKey = (k) => api('/api/phone/' + k, { json: {} }).then(poll);
const press = (usbAction, phoneAction) => () => (showing === 'phone' ? phoneKey(phoneAction) : ctl(usbAction));

(async () => {
  const st = await getSettings().catch(() => ({})); await applyUiTheme(st.theme); await initI18n(st.language); followTheme();
  $('prev').innerHTML = icons.prev; $('next').innerHTML = icons.next; $('toggle').innerHTML = icons.play; $('shuffle').innerHTML = icons.shuffle; $('back').innerHTML = icons.back;
  $('prev').onclick = press('prev', 'previous'); $('next').onclick = press('next', 'next'); $('toggle').onclick = press('toggle', 'toggle');
  $('shuffle').onclick = () => ctl('shuffle');
  document.querySelectorAll('[data-src]').forEach(b => b.onclick = () => { srcChoice = b.dataset.src; logEvent('music_source', { src: srcChoice }); poll(); });
  $('openPhone').onclick = () => api('/api/phone/show', { json: {} }).catch(() => {});
  $('back').onclick = () => { if (stack.length > 1) { stack.pop(); show(stack.at(-1)); } };
  document.querySelectorAll('[data-tab]').forEach(b => b.onclick = () => reset(b.dataset.tab));
  $('bar').onclick = async (e) => { if (showing === 'phone') return; const s = await api('/api/music/status'); const d = s.duration || 0; if (d) api('/api/music/control', { json: { action: 'seek', pos: d * e.offsetX / e.currentTarget.clientWidth } }); };
  reset('artists'); poll(); setInterval(poll, 1000);
})();
