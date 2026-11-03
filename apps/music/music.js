// iPod Nano / USB music browser. The library is read by MPD from song tags,
// so the iPod's scrambled file names (F00/ABCD.m4a) never show up here.
import { api, getSettings, applyUiTheme, logEvent, icons, toast } from '../common/hu.js';
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

async function poll() {
  const s = await api('/api/music/status').catch(() => null);
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

(async () => {
  const st = await getSettings().catch(() => ({})); await applyUiTheme(st.theme); await initI18n(st.language);
  $('prev').innerHTML = icons.prev; $('next').innerHTML = icons.next; $('toggle').innerHTML = icons.play; $('shuffle').innerHTML = icons.shuffle; $('back').innerHTML = icons.back;
  $('prev').onclick = () => ctl('prev'); $('next').onclick = () => ctl('next'); $('toggle').onclick = () => ctl('toggle'); $('shuffle').onclick = () => ctl('shuffle');
  $('back').onclick = () => { if (stack.length > 1) { stack.pop(); show(stack.at(-1)); } };
  document.querySelectorAll('[data-tab]').forEach(b => b.onclick = () => reset(b.dataset.tab));
  $('bar').onclick = async (e) => { const s = await api('/api/music/status'); const d = s.duration || 0; if (d) api('/api/music/control', { json: { action: 'seek', pos: d * e.offsetX / e.currentTarget.clientWidth } }); };
  reset('artists'); poll(); setInterval(poll, 1000);
})();
