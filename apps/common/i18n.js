// Screen text in the head unit's language (Settings > Region & language).
// All phrases live in i18n.json: "en" is the master list; a phrase missing in another language
// falls back to English. Mark HTML with data-i18n="key" (text) or data-i18n-aria="key" (aria-label).
export const LANGS = [
  { code: 'en', name: 'English', tag: 'en-US' },
  { code: 'es', name: 'Español', tag: 'es-US' },
  { code: 'zh', name: '中文（简体）', tag: 'zh-CN' },
  { code: 'tl', name: 'Tagalog', tag: 'fil-PH' },
  { code: 'vi', name: 'Tiếng Việt', tag: 'vi-VN' },
  { code: 'ar', name: 'العربية', tag: 'ar', dir: 'rtl' },
  { code: 'fr', name: 'Français', tag: 'fr-FR' },
  { code: 'ko', name: '한국어', tag: 'ko-KR' },
  { code: 'pt', name: 'Português (Brasil)', tag: 'pt-BR' },
  { code: 'hi', name: 'हिन्दी', tag: 'hi-IN' },
];
let all = null, cur = 'en';

export async function initI18n(code) {
  // always the installed version's phrases, never a copy the web view kept from an older version
  if (!all) all = await fetch('/apps/common/i18n.json', { cache: 'no-store' }).then(r => r.json()).catch(() => ({ en: {} }));
  cur = LANGS.some(l => l.code === code) ? code : 'en';
  const L = LANGS.find(l => l.code === cur);
  document.documentElement.lang = L.tag;
  document.documentElement.dir = L.dir || 'ltr';
  translatePage();
  return cur;
}

export function t(key, vars = {}) {
  let s = all?.[cur]?.[key] ?? all?.en?.[key] ?? key;
  for (const [k, v] of Object.entries(vars)) s = s.split(`{${k}}`).join(v);
  return s;
}
export const has = (key) => !!all?.[cur]?.[key];
export const lang = () => cur;
export const langTag = () => LANGS.find(l => l.code === cur).tag;

// A phrase that can't be found keeps the English text written in the page, never shows its key name.
export function translatePage(root = document) {
  root.querySelectorAll('[data-i18n]').forEach(el => {
    if (el.dataset.i18nDefault === undefined) el.dataset.i18nDefault = el.textContent;
    const s = t(el.dataset.i18n);
    el.textContent = s === el.dataset.i18n && el.dataset.i18nDefault ? el.dataset.i18nDefault : s;
  });
  root.querySelectorAll('[data-i18n-aria]').forEach(el => {
    if (el.dataset.i18nAriaDefault === undefined) el.dataset.i18nAriaDefault = el.getAttribute('aria-label') || '';
    const s = t(el.dataset.i18nAria);
    el.setAttribute('aria-label', s === el.dataset.i18nAria && el.dataset.i18nAriaDefault ? el.dataset.i18nAriaDefault : s);
  });
}
