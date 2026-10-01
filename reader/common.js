/* readsync: helpers and reader settings shared by the library page (library.js) and the reader (app.js).
 Plain JS, no build step; the three files share one global scope. */
"use strict";
const $ = (s) => document.querySelector(s);
// Line icons in the page's own colour. Characters like ▶ ⏮ ⏱ ⚙ turn into colour emoji on an iPhone.
const ICONS = {
  play: '<path d="M8 5.2v13.6L19 12z" fill="currentColor" stroke="none"/>',
  pause: '<path d="M7.5 5h3v14h-3zM13.5 5h3v14h-3z" fill="currentColor" stroke="none"/>',
  prev: '<path d="M6.5 5.5v13M18 6.5 9.5 12l8.5 5.5z"/>',
  next: '<path d="M17.5 5.5v13M6 6.5l8.5 5.5L6 17.5z"/>',
  focus: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2.6" fill="currentColor"/>',
  timer: '<circle cx="12" cy="13.5" r="7"/><path d="M12 10v3.5l2.3 1.6M10 3h4"/>',
  menu: '<path d="M4.5 7h15M4.5 12h15M4.5 17h9"/>',
  gear: '<path d="M4.5 7.5h8M17.5 7.5h2M4.5 16.5h2M11.5 16.5h8"/><circle cx="15" cy="7.5" r="2.3"/><circle cx="9" cy="16.5" r="2.3"/>',
  book: '<path d="M12 6.8C10 5.3 7.2 4.8 4.5 5.2v12.6c2.7-.4 5.5.1 7.5 1.6 2-1.5 4.8-2 7.5-1.6V5.2c-2.7-.4-5.5.1-7.5 1.6zM12 6.8v12.6"/>',
  audio: '<path d="M4.5 15v-2.5a7.5 7.5 0 0 1 15 0V15"/><rect x="3.8" y="13.8" width="3.8" height="5.7" rx="1.4"/><rect x="16.4" y="13.8" width="3.8" height="5.7" rx="1.4"/>',
  close: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
  back: '<path d="M14.5 5.5 8 12l6.5 6.5"/>',
};
const iconSvg = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ""}</svg>`;
function setIcon(el, name) { if (el) { el.innerHTML = iconSvg(name); el.dataset.icon = name; } }
document.querySelectorAll("[data-icon]").forEach((el) => setIcon(el, el.dataset.icon));
const slug = new URLSearchParams(location.search).get("book");
// also the apostrophe: catalog titles carry them and some attributes here are single-quoted
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (t) => {
  t = Math.max(0, Math.round(t || 0));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(s).padStart(2, "0");
};
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
};
const today = () => new Date().toISOString().slice(0, 10);
// Inside the iPhone app there is no server: the reader's writes go to Swift over a message bridge,
// which answers with the same JSON the server would. Reads still go through fetch (a URL scheme).
const bridge = window.webkit?.messageHandlers?.readsync;
function send(method, path, body, keepalive) {
  if (bridge) return bridge.postMessage({ method, path, body }).then((json) => ({ ok: true, json: () => Promise.resolve(json) }));
  return fetch(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), keepalive: !!keepalive });
}

// ---------------- settings ----------------
const DEFAULTS = { font: 20, lh: 1.65, width: 42, family: "literata", ui: "inter", weight: 400, theme: "auto", sent: true, word: true, wordStyle: "bg",
  dimMode: "off", offset: 0, scroll: "zone", clickWord: false, speed: 1, hideUi: true, pauseHidden: true, rewind: true };
const FAMILIES = {
  literata: '"Literata", "Iowan Old Style", Georgia, serif', ptserif: '"PT Serif", Georgia, serif', merriweather: '"Merriweather", Georgia, serif',
  iowan: '"Iowan Old Style", "Palatino Linotype", Georgia, serif', charter: '"Charter", "Iowan Old Style", Georgia, serif', georgia: 'Georgia, "Times New Roman", serif',
  inter: '"Inter", var(--sans)', golos: '"Golos Text", var(--sans)', plex: '"IBM Plex Sans", var(--sans)', system: "var(--sans)",
};
const darkMedia = matchMedia("(prefers-color-scheme: dark)");
darkMedia.addEventListener("change", () => applySettings());
const settings = Object.assign({}, DEFAULTS, store.get("rs:settings", {}));
// migrate settings from earlier versions
if (settings.family === "serif") settings.family = "iowan";
if (settings.family === "sans") settings.family = "inter";
if (settings.dim === true) settings.dimMode = "para";
delete settings.dim;
let onApplied = () => {};  // the reader re-lays out its pages after a settings change
function applySettings() {
  const r = document.documentElement.style;
  r.setProperty("--font-size", settings.font + "px");
  r.setProperty("--lh", settings.lh);
  r.setProperty("--width", settings.width + "rem");
  r.setProperty("--family", FAMILIES[settings.family] || FAMILIES.literata);
  r.setProperty("--ui", FAMILIES[settings.ui] || FAMILIES.inter);
  r.setProperty("--weight", settings.weight);
  document.documentElement.dataset.theme = settings.theme === "auto" ? (darkMedia.matches ? "dark" : "light") : settings.theme;
  document.body.classList.toggle("sent-hl", !!settings.sent);
  document.body.classList.toggle("word-hl", !!settings.word);
  document.body.classList.toggle("word-underline", settings.wordStyle === "underline");
  onApplied();
  document.body.classList.toggle("dim-para", settings.dimMode === "para");
  document.body.classList.toggle("dim-sent", settings.dimMode === "sent");
  store.set("rs:settings", settings);
}
applySettings();
// Settings are global (books/settings.json); localStorage only caches them. Both pages use the
// same two functions: pull the newer copy from the server, push local changes after a short delay.
let settingsTimer = 0, onSettingsSynced = () => {};
function persistSettings() {
  const at = Date.now(); store.set("rs:settingsAt", at);
  clearTimeout(settingsTimer);
  settingsTimer = setTimeout(() => send("PUT", "/api/settings", { settings, settingsAt: at }).catch(() => {}), 400);
}
function adoptSettings(remoteSettings) {
  if (remoteSettings && remoteSettings.settings && (remoteSettings.settingsAt || 0) > store.get("rs:settingsAt", 0)) {
    Object.assign(settings, remoteSettings.settings); store.set("rs:settingsAt", remoteSettings.settingsAt); applySettings(); onSettingsSynced();
    return true;
  }
  if (store.get("rs:settingsAt", 0) > ((remoteSettings && remoteSettings.settingsAt) || 0)) persistSettings();
  return false;
}
const fetchSettings = () => fetch("/api/settings").then((r) => r.json()).catch(() => ({}));
