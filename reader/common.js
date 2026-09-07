/* readsync: helpers and reader settings shared by the library page (library.js) and the reader (app.js).
 Plain JS, no build step; the three files share one global scope. */
"use strict";
const $ = (s) => document.querySelector(s);
const slug = new URLSearchParams(location.search).get("book");
const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
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
  settingsTimer = setTimeout(() => fetch("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ settings, settingsAt: at }) }).catch(() => {}), 400);
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
