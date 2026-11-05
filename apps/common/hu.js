// Shared helpers for the head-unit web apps (served by hu-bridge on 127.0.0.1:8765).
export async function api(path, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.method = init.method || 'POST';
    init.headers = { 'Content-Type': 'application/json' };
    init.body = JSON.stringify(opts.json);
  }
  const r = await fetch(path, init);
  return r.json();
}

export const getSettings = () => api('/api/settings');
export const saveSettings = (patch) => api('/api/settings', { json: patch });

let themesCache = null;
export async function loadThemes() {
  if (!themesCache) themesCache = await (await fetch('/apps/styles/themes.json')).json();
  return themesCache;
}

// Day and night skins: the bridge switches the skin at sunrise / sunset; open screens follow along.
export function followTheme(onChange, every = 20000) {
  setInterval(async () => {
    const s = await api('/api/settings').catch(() => null);
    if (s?.theme && s.theme !== document.documentElement.dataset.theme) { await applyUiTheme(s.theme); onChange?.(s); }
  }, every);
}

export async function applyUiTheme(id) {
  const themes = await loadThemes();
  const t = themes.find(x => x.id === id) || themes[0];
  const s = document.documentElement.style;
  for (const [k, v] of Object.entries(t.ui)) s.setProperty(`--${k}`, v);
  s.setProperty('--font', `"${t.font}"`);
  document.documentElement.dataset.theme = t.id;
  document.documentElement.dataset.dark = t.dark ? '1' : '0';
  return t;
}

// Usage log: batched, so test drives produce data without slowing the UI.
const queue = [];
export function logEvent(event, fields = {}) {
  queue.push({ event, app: document.body?.dataset.app, ...fields });
  if (queue.length > 30) flushLog();
}
export function flushLog() {
  if (!queue.length) return;
  const batch = queue.splice(0, queue.length);
  fetch('/api/log', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(batch), keepalive: true }).catch(() => {});
}
setInterval(flushLog, 5000);
addEventListener('pagehide', flushLog);
addEventListener('error', (e) => logEvent('js_error', { msg: String(e.message).slice(0, 200), src: (e.filename || '').split('/').pop(), line: e.lineno }));
document.addEventListener('pointerdown', (e) => {
  const el = e.target.closest('[data-log]');
  if (el) logEvent('tap', { id: el.dataset.log });
}, { passive: true });

// Hudiy's web view exposes a `hudiy` object; hook it only if present (normal browsers don't have it).
export function onHudiy(name, fn) {
  try { if (window.hudiy && name in window.hudiy) window.hudiy[name] = fn; } catch (e) { /* ignore */ }
}

export function fmtDist(m, units = 'imperial') {
  if (m == null || isNaN(m)) return '';
  if (units === 'imperial') {
    const ft = m * 3.28084;
    if (ft < 1000) return `${Math.round(ft / 50) * 50 || 50} ft`;
    const mi = m / 1609.344;
    return mi < 10 ? `${mi.toFixed(1)} mi` : `${Math.round(mi)} mi`;
  }
  return m < 1000 ? `${Math.round(m / 10) * 10} m` : `${(m / 1000).toFixed(m < 10000 ? 1 : 0)} km`;
}

export function fmtTime(sec) {
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
  return h ? `${h} h ${m} min` : `${m} min`;
}

export function toast(msg, ms = 2500) {
  let el = document.querySelector('.toast');
  if (!el) { el = document.createElement('div'); el.className = 'toast panel'; document.body.appendChild(el); }
  el.textContent = msg; el.classList.remove('hidden');
  clearTimeout(el._t); el._t = setTimeout(() => el.classList.add('hidden'), ms);
}

export const icons = {
  search: '<svg class="icon" viewBox="0 0 24 24"><circle cx="10.5" cy="10.5" r="6.5"/><path d="M20 20l-4.6-4.6"/></svg>',
  center: '<svg class="icon" viewBox="0 0 24 24"><path d="M12 3l7 18-7-4-7 4z"/></svg>',
  star: '<svg class="icon" viewBox="0 0 24 24"><path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/></svg>',
  gear: '<svg class="icon" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3.2"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1"/></svg>',
  close: '<svg class="icon" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  play: '<svg class="icon" viewBox="0 0 24 24"><path d="M7 4l13 8-13 8z" fill="currentColor"/></svg>',
  pause: '<svg class="icon" viewBox="0 0 24 24"><path d="M7 4h3v16H7zM14 4h3v16h-3z" fill="currentColor"/></svg>',
  next: '<svg class="icon" viewBox="0 0 24 24"><path d="M5 5l10 7-10 7zM18 5v14"/></svg>',
  prev: '<svg class="icon" viewBox="0 0 24 24"><path d="M19 5L9 12l10 7zM6 5v14"/></svg>',
  shuffle: '<svg class="icon" viewBox="0 0 24 24"><path d="M3 7h4l10 10h4M3 17h4l3-3M14 10l3-3h4M18 4l3 3-3 3M18 14l3 3-3 3"/></svg>',
  back: '<svg class="icon" viewBox="0 0 24 24"><path d="M15 5l-7 7 7 7"/></svg>',
  stop: '<svg class="icon" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" fill="currentColor"/></svg>',
  flag: '<svg class="icon" viewBox="0 0 24 24"><path d="M5 21V4M5 4h11l-2 4 2 4H5"/></svg>',
};
