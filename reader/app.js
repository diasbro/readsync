/* readsync reader: text + audio with synced highlighting. Plain JS, no deps. */
(() => {
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
  const pages = { on: false, spreadW: 0, total: 1, cur: 0, sent: 0 };  // page-mode state (declared early: applySettings reads it)

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
    if (pages.on) requestAnimationFrame(() => { pagesLayout(); goToSentence(pages.sent, false); });
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

  // ---------------- library ----------------
  if (!slug) {
    $("#library").hidden = false;
    fetchSettings().then(adoptSettings);
    // view preferences: the two settings that shape every page (theme, interface font)
    const prefs = $("#lib-prefs");
    function syncPrefsUI() {
      document.querySelectorAll("#lib-theme button").forEach((b) => b.classList.toggle("on", b.dataset.v === settings.theme));
      $("#lib-ui").value = settings.ui;
    }
    onSettingsSynced = syncPrefsUI;
    syncPrefsUI();
    $("#lib-settings").onclick = (e) => { e.stopPropagation(); prefs.hidden = !prefs.hidden; };
    addEventListener("click", (e) => { if (!e.target.closest("#lib-prefs, #lib-settings")) prefs.hidden = true; });
    addEventListener("keydown", (e) => { if (e.key === "Escape") prefs.hidden = true; });
    $("#lib-theme").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; settings.theme = b.dataset.v; applySettings(); persistSettings(); syncPrefsUI(); });
    // find in the library: filters the cards as you type; "/" opens it, Esc clears and closes
    const search = $("#lib-search");
    function applyFilter() {
      const q = norm(search.value);
      document.querySelectorAll(".card").forEach((c) => c.classList.toggle("hit-off", !!q && !norm(c.querySelector(".t")?.textContent + " " + c.querySelector(".m")?.textContent).includes(q)));
      document.querySelectorAll(".lib-section").forEach((s) => { const grid = s.querySelector(".lib-grid"); if (grid) s.classList.toggle("empty", !!q && !grid.querySelector(".card:not(.hit-off)")); });
    }
    function openSearch() { search.hidden = false; search.focus(); }
    function closeSearch() { search.value = ""; search.hidden = true; applyFilter(); }
    $("#lib-find").onclick = () => (search.hidden ? openSearch() : closeSearch());
    search.addEventListener("input", applyFilter);
    search.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSearch(); });
    addEventListener("keydown", (e) => { if (e.key === "/" && !e.target.matches("input, textarea, select")) { e.preventDefault(); openSearch(); } });
    $("#lib-ui").addEventListener("input", (e) => { settings.ui = e.target.value; applySettings(); persistSettings(); syncPrefsUI(); });
    // header: the sentence you stopped at in the current book, with the word highlight walking
    // along it; a click opens the book right there (the reader always starts paused)
    // three looks: "audio" walks the word highlight along the line; "pages" is a still line with a
    // page marker (the first sentence of the spread you are on); "random" is a quote from a finished book
    let demoTimer = 0;
    function runDemo(text, meta, href, look) {
      const line = $("#demo-line"), demo = $(".demo");
      const words = text.split(/\s+/).filter(Boolean);
      line.innerHTML = words.map((w) => `<span class="w">${esc(w)}</span>`).join(" ") + (meta ? ` <span class="src">· ${esc(meta)}</span>` : "");
      line.classList.toggle("link", !!href);
      line.onclick = href ? () => { location.href = href; } : null;
      demo.dataset.look = look || "plain";
      clearInterval(demoTimer);
      const ws = line.querySelectorAll(".w");
      if (look === "audio") {
        let i = 0; ws[0]?.classList.add("cur");
        if (!matchMedia("(prefers-reduced-motion: reduce)").matches && ws.length > 1) {
          demoTimer = setInterval(() => { if (document.hidden) return; ws[i].classList.remove("cur"); i = (i + 1) % ws.length; ws[i].classList.add("cur"); }, 520);
        }
      }
    }
    runDemo("Одна книга. Один голос. Одно внимание.", "", "", "plain");
    async function headerLine(reading, all) {
      const byOpened = (a, b) => (b.state.opened || 0) - (a.state.opened || 0);
      const current = reading.slice().sort(byOpened)[0];
      if (current) {
        const w = await fetch(`/api/where/${current.slug}`).then((r) => r.json()).catch(() => null);
        if (w && w.text) { runDemo(w.text, current.title + (w.mode === "pages" && w.chapter ? " · " + w.chapter : ""), "?book=" + current.slug, w.mode === "audio" ? "audio" : "pages"); return; }
      }
      const done = all.filter((b) => b.ready && b.state.finished);
      if (done.length) {
        const b = done[Math.floor(Math.random() * done.length)];
        const w = await fetch(`/api/where/${b.slug}?random=1`).then((r) => r.json()).catch(() => null);
        if (w && w.text) { runDemo(w.text, "из «" + b.title + "»", "?book=" + b.slug, "random"); return; }
      }
    }
    const toast = (msg) => { const el = document.createElement("div"); el.className = "toast"; el.textContent = msg; document.body.appendChild(el); setTimeout(() => el.remove(), 3000); };
    const q = (x) => encodeURIComponent(x);
    const norm = (x) => (x || "").toLowerCase().replace(/ё/g, "е").replace(/[^\p{L}\p{N}]+/gu, " ").trim();
    const SOURCE = { "fantasy-worlds": "fantasy-worlds", flibusta: "Flibusta", coollib: "Coollib" };

    // ---- library cards; text-only books can get audio attached right here ----
    // Shelves: a book with more than 10 minutes of reading is "reading now" unless moved by hand;
    // within a shelf the most recently opened book comes first, never-opened ones by title.
    let books = [], open = null;
    const READING_SEC = 600;
    // finished books leave "reading now" on their own; a manual move (re-reading) always wins
    const shelfOf = (b) => (b.state.shelf ? b.state.shelf : b.state.finished ? "library" : b.state.seconds > READING_SEC ? "reading" : "library");
    const setShelf = (slug, shelf) => fetch(`/api/state/${slug}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ shelf, shelfAt: Date.now() }) });
    function cardHtml(b, shelf) {
        const st = b.state;
        const pos = st.pos || 0, dur = st.duration || store.get("rs:dur:" + b.slug, 0);
        const pct = st.finished ? 100 : b.has_audio ? (dur ? Math.round((pos / dur) * 100) : 0) : (st.sentPct || 0);
        const meta = [b.author, b.has_audio && b.narrator ? "читает " + b.narrator : null, !b.ready ? (b.building ? "загружается…" : "не готово") : null].filter(Boolean).join(" · ");
        const tags = b.ready ? '<span class="tag">текст</span>' + (b.has_audio ? '<span class="tag">аудио</span>' : "") + (st.finished ? '<span class="tag done">прочитано</span>' : "") : "";
        const where = !b.ready ? "" : st.finished ? "прочитано целиком" : b.has_audio ? (pct ? `прочитано ${pct}% · ${fmt(pos)}` : "не начато") : (st.sent ? `прочитано ${pct}%` : "не начато");
        const cover = b.cover ? `<img class="cover" src="/books/${esc(b.slug)}/${esc(b.cover)}" alt="">` : `<div class="cover empty">${esc((b.title || b.slug).slice(0, 1))}</div>`;
        const move = b.ready ? (shelf === "reading" ? `<button class="link-btn shelf-btn" data-slug="${esc(b.slug)}" data-shelf="library">убрать из текущих</button>` : `<button class="link-btn shelf-btn" data-slug="${esc(b.slug)}" data-shelf="reading">в текущие</button>`) : "";
        const side = `<div class="side">${b.ready && !b.has_audio && !b.building ? `<button class="btn sm attach-btn" data-slug="${esc(b.slug)}">＋ аудио</button>` : ""}${move}</div>`;
        const form = open === b.slug ? `<div class="attach-form" data-slug="${esc(b.slug)}">
            <textarea name="audio_url" rows="2" placeholder="Ссылки на аудио: YouTube, части по одной в строке"></textarea>
            <input name="narrator" placeholder="Чтец (необязательно)">
            <label class="row small muted"><input type="checkbox" name="align"> точное выравнивание сразу (долго)</label>
            <div class="row-btns"><button class="btn sm primary attach-go">Загрузить аудио</button><button class="btn sm attach-cancel">Отмена</button></div></div>` : "";
        return `<div class="card${form ? " attach" : ""}" data-slug="${esc(b.slug)}">${form ? "" : `<a class="cover-link" href="${b.ready ? "?book=" + esc(b.slug) : "#"}">${cover}</a>`}
          <div class="body">${form ? `<div class="t">${esc(b.title || b.slug)}</div><div class="m">Аудио для этой книги. Найди голос сам: <a target="_blank" rel="noopener" href="https://www.youtube.com/results?search_query=${q((b.title || "") + " аудиокнига")}">YouTube</a></div>${form}` :
            `<a href="${b.ready ? "?book=" + esc(b.slug) : "#"}" class="tlink"><div class="t">${esc(b.title || b.slug)}${tags}</div></a><div class="m">${esc(meta)}</div>
          <div class="bar${pct ? "" : " empty"}"><i style="width:${pct}%"></i></div><div class="m${pct ? "" : " empty"}">${where}</div>`}</div>${form ? "" : side}</div>`;
    }
    async function renderLibrary() {
      books = await fetch("/api/books").then((r) => r.json());
      const list = $("#library-list");
      if (!books.length) { list.innerHTML = '<p class="muted small">Пока нет книг. Сохрани название ниже.</p>'; $("#reading-section").hidden = true; return; }
      const byOpened = (a, b) => (b.state.opened || 0) - (a.state.opened || 0) || (a.title || "").localeCompare(b.title || "", "ru");
      const reading = books.filter((b) => shelfOf(b) === "reading").sort(byOpened);
      const rest = books.filter((b) => shelfOf(b) !== "reading").sort(byOpened);
      $("#lib-title").textContent = reading.length ? "Остальные" : "Библиотека";
      headerLine(reading, books);
      $("#reading-section").hidden = !reading.length;
      $("#reading-list").innerHTML = reading.map((b) => cardHtml(b, "reading")).join("");
      list.innerHTML = rest.map((b) => cardHtml(b, "library")).join("");
      applyFilter();
    }
    $("#library").addEventListener("click", async (e) => {
      const mv = e.target.closest(".shelf-btn");
      if (mv) { await setShelf(mv.dataset.slug, mv.dataset.shelf); renderLibrary(); return; }
      const btn = e.target.closest(".attach-btn");
      if (btn) { open = btn.dataset.slug; renderLibrary(); return; }
      if (e.target.closest(".attach-cancel")) { open = null; renderLibrary(); return; }
      if (e.target.closest(".attach-go")) {
        const f = e.target.closest(".attach-form"); const urls = f.querySelector("[name=audio_url]").value.trim();
        if (!urls) { toast("Нужна хотя бы одна ссылка на аудио"); return; }
        const ok = await startAdd({ slug: f.dataset.slug, audio_url: urls, narrator: f.querySelector("[name=narrator]").value, align: f.querySelector("[name=align]").checked ? "on" : "" });
        if (ok) { open = null; toast("Аудио загружается, книга появится с плеером"); renderLibrary(); }
      }
    });
    renderLibrary();

    // ---- jobs: background pipeline runs started from this page ----
    let jobsTimer = 0;
    async function pollJobs() {
      const jobs = await fetch("/api/jobs").then((r) => r.json()).catch(() => ({}));
      const running = Object.values(jobs).some((j) => j.running);
      $("#jobs").innerHTML = Object.entries(jobs).filter(([, j]) => j.running || j.exit !== 0).map(([sl, j]) => `<div class="job"><b>${esc(sl)}</b>: ${j.running ? "идёт загрузка…" : "ошибка (код " + j.exit + "), см. books/" + esc(sl) + "/add.log"}<pre>${esc(j.log.slice(-3).join("\n"))}</pre></div>`).join("");
      clearTimeout(jobsTimer);
      if (running) jobsTimer = setTimeout(pollJobs, 3000); else if (Object.keys(jobs).length) renderLibrary();
    }
    pollJobs();
    let adding = false;  // one /api/add at a time: a double click must not start a second job
    async function startAdd(fields) {
      if (adding) return false;
      adding = true;
      try {
        const fd = new FormData(); Object.entries(fields).forEach(([k, v]) => { if (v != null) fd.set(k, v); });
        const r = await fetch("/api/add", { method: "POST", body: fd }).then((x) => x.json()).catch((err) => ({ error: String(err) }));
        if (r.error) { toast("Ошибка: " + r.error); return false; }
        pollJobs(); return r.slug;
      } finally { adding = false; }
    }
    $("#add-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target); const msg = $("#add-msg");
      if (!fd.get("text_url") && !(fd.get("text_file") && fd.get("text_file").size)) { msg.textContent = "Нужен текст: ссылка или файл."; return; }
      msg.textContent = "Отправляю…";
      const r = await fetch("/api/add", { method: "POST", body: fd }).then((x) => x.json()).catch((err) => ({ error: String(err) }));
      if (r.error) { msg.textContent = "Ошибка: " + r.error; return; }
      msg.textContent = `Загрузка «${fd.get("title")}» запущена.`; e.target.reset(); pollJobs();
    });

    // ---- wishlist: capture a title now; the text is searched across libraries, audio you pick yourself ----
    const wishApi = (method, path, body) => fetch("/api/wishlist" + (path || ""), { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined }).then((r) => r.json());
    const searching = new Map();  // wish id -> {status, hits, busy}
    $("#wish-auto").checked = store.get("rs:wishAuto", true);
    $("#wish-auto").addEventListener("change", (e) => store.set("rs:wishAuto", e.target.checked));

    const hitMeta = (h) => [h.author, h.year, h.parts.length > 1 ? `${h.parts.length} тома` : "", h.parts[0].kind === "html" ? "страница" : h.parts[0].kind, SOURCE[h.source] || h.source, h.lang && h.lang !== "ru" ? h.lang : ""].filter(Boolean).join(" · ");
    const loadBtn = (h, cls) => `<button class="btn sm primary pick ${cls || ""}" data-hit='${esc(JSON.stringify({ title: h.title, author: h.author, narrator: h.narrator, urls: h.parts.map((p) => p.url), audio: h.audio_url }))}' ${h.readable ? "" : "disabled"}>Загрузить</button>`;
    function renderWishlist(items) {
      const el = $("#wish-list");
      if (!items.length) { el.innerHTML = '<p class="muted small">Список пуст. Наткнулся на книгу — сохрани название, остальное потом.</p>'; return; }
      el.innerHTML = items.map((w) => {
        const s = searching.get(w.id) || {};
        const best = s.hits && s.hits[0];
        const found = best ? `<div class="found"><div class="ft">${esc(best.title)}<div class="fm">${esc(hitMeta(best))}</div></div>${loadBtn(best)}</div>` : "";
        const others = s.hits && s.hits.length > 1 ? `<details class="more"><summary>ещё варианты (${s.hits.length - 1})</summary><div class="cands">${s.hits.slice(1).map((h) => `<div class="cand"><div class="ct">${esc(h.title)}<div class="cm">${esc(hitMeta(h))}</div></div>${loadBtn(h)}</div>`).join("")}</div></details>` : "";
        const status = s.status ? `<div class="status ${s.ok ? "ok" : ""}${s.err ? " err" : ""}">${s.html ? s.status : esc(s.status)}</div>` : "";
        return `<div class="wish ${s.busy ? "busy" : ""}" data-id="${esc(w.id)}">
        <div class="wt"><span>${esc(w.title)}${w.author ? ' <span class="muted">· ' + esc(w.author) + "</span>" : ""}</span><small>${esc(w.added || "")}</small></div>
        ${status}${found}${others}
        <div class="links">
          <a href="#" class="fw">${s.hits ? "искать снова" : "найти текст"}</a>
          <a target="_blank" rel="noopener" href="https://www.youtube.com/results?search_query=${q(w.title + " аудиокнига")}">аудио на YouTube</a>
        </div>
        <details class="more"><summary>своя ссылка или файл</summary>
          <div class="fields">
            <input name="author" placeholder="Автор" value="${esc(w.author || "")}">
            <input name="note" placeholder="Заметка" value="${esc(w.note || "")}">
            <input name="text_url" placeholder="Ссылка на текст: страница, fb2, epub, txt" value="${esc(w.text_url || "")}">
            <input name="audio_url" placeholder="Ссылка на аудио (YouTube), необязательно" value="${esc(w.audio_url || "")}">
          </div>
          <div class="actions"><button class="btn sm primary load" ${w.text_url ? "" : "disabled"}>Загрузить по ссылке</button></div>
        </details>
        <div class="actions"><button class="del" title="Удалить из списка">✕ убрать</button></div></div>`;
      }).join("");
    }
    let wishItems = [];
    const refresh = (items) => { wishItems = items; renderWishlist(items); };
    const loadWishlist = () => wishApi("GET").then(refresh).catch(() => {});

    const SOURCES_LABEL = "fantasy-worlds, Flibusta, Coollib";
    async function searchFor(w, auto) {
      if (searching.get(w.id)?.busy) return;  // a search for this title is already running
      const t0 = Date.now();
      const tickStatus = () => { const el = document.querySelector(`.wish[data-id="${w.id}"] .status`); if (el) el.innerHTML = `<span class="spin"></span>ищу в ${SOURCES_LABEL}… ${Math.round((Date.now() - t0) / 1000)} с`; };
      searching.set(w.id, { status: "ищу…", busy: true, html: true }); renderWishlist(wishItems); tickStatus();
      const ticker = setInterval(tickStatus, 1000);
      const ctrl = new AbortController(); const killer = setTimeout(() => ctrl.abort(), 60000);
      let res;
      try { res = await fetch("/api/search?q=" + q(w.title), { signal: ctrl.signal }).then((r) => r.json()); if (res.error) throw new Error(res.error); }
      catch (e) {
        clearInterval(ticker); clearTimeout(killer);
        searching.set(w.id, { status: e.name === "AbortError" ? "Поиск не ответил за минуту. Попробуй ещё раз." : "Поиск не удался: " + e.message, err: true }); renderWishlist(wishItems); return;
      }
      clearInterval(ticker); clearTimeout(killer);
      const hits = res.hits.filter((h) => h.readable);
      const want = norm(w.title);
      const strict = hits.filter((h) => h.complete && (norm(h.title) === want || norm(h.author + " " + h.title) === want));
      if (auto && strict.length) {
        const h = strict[0];
        if (await loadHit(w, h)) { toast(`Нашлось на ${SOURCE[h.source] || h.source}, загружаю: ${h.title}`); return; }
      }
      const failed = (res.errors || []).map((x) => SOURCE[x.split(":")[0]] || x.split(":")[0]);
      const failedNote = failed.length ? ` ${failed.join(", ")} не ответил${failed.length > 1 ? "и" : ""}.` : "";
      searching.set(w.id, hits.length
        ? { hits, status: (strict.length ? "Нашлось, есть похожие, выбери:" : "Точного совпадения нет, ближайшее:") + failedNote, ok: true }
        : { status: `Не нашлось в ${SOURCES_LABEL}.${failedNote} Вставь свою ссылку или файл.`, err: !failed.length ? false : true });
      renderWishlist(wishItems);
    }
    async function loadHit(w, h) {
      const s = await startAdd({ title: h.title, author: h.author || w.author, narrator: h.narrator, text_url: h.parts.map((p) => p.url).join("\n"), audio_url: h.audio_url || "" });
      if (!s) return false;
      searching.delete(w.id); refresh(await wishApi("DELETE", "/" + w.id)); return true;
    }

    async function addByLink(url) {
      const s = await startAdd({ text_url: url });
      if (s) { toast("Загружаю по ссылке, название возьму из книги"); }
    }
    async function addByFile(file) {
      const s = await startAdd({ title: file.name.replace(/\.(fb2\.zip|zip|fb2|epub|txt|html?)$/i, ""), text_file: file });
      if (s) toast("Загружаю файл: " + file.name);
    }
    ["dragenter", "dragover"].forEach((ev) => addEventListener(ev, (e) => { if (e.dataTransfer?.types.includes("Files")) { e.preventDefault(); document.body.classList.add("dropping"); } }));
    ["dragleave", "drop"].forEach((ev) => addEventListener(ev, (e) => { if (ev === "drop" || e.relatedTarget == null) document.body.classList.remove("dropping"); }));
    addEventListener("drop", (e) => { if (!e.dataTransfer?.files?.length) return; e.preventDefault(); [...e.dataTransfer.files].forEach(addByFile); });
    $("#wish-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const title = e.target.title.value.trim(); if (!title) return;
      if (/^https?:\/\//i.test(title)) { e.target.reset(); addByLink(title); return; }
      const items = await wishApi("POST", "", { title }); e.target.reset(); refresh(items); toast("Сохранено: " + title);
      const w = items.find((x) => norm(x.title) === norm(title));
      if (w && $("#wish-auto").checked) searchFor(w, true);
    });
    $("#wish-list").addEventListener("change", async (e) => {
      const card = e.target.closest(".wish"); if (!card || !e.target.name) return;
      const items = await wishApi("PUT", "/" + card.dataset.id, { [e.target.name]: e.target.value }); wishItems = items;
    });
    $("#wish-list").addEventListener("click", async (e) => {
      const card = e.target.closest(".wish"); if (!card) return;
      const w = wishItems.find((x) => x.id === card.dataset.id); if (!w) return;
      if (e.target.closest(".del")) { searching.delete(w.id); refresh(await wishApi("DELETE", "/" + w.id)); return; }
      if (e.target.closest(".fw")) { e.preventDefault(); searchFor(w, false); return; }
      const pick = e.target.closest(".pick");
      if (pick) {
        const h = JSON.parse(pick.dataset.hit);
        if (await loadHit(w, { title: h.title, author: h.author, narrator: h.narrator, parts: h.urls.map((u) => ({ url: u })), audio_url: h.audio, source: "" })) toast("Загружаю: " + h.title);
        return;
      }
      if (e.target.closest(".load")) {
        const fresh = (await wishApi("GET")).find((x) => x.id === w.id) || w;
        if (!fresh.text_url) return;
        if (await loadHit(w, { title: fresh.title, author: fresh.author, narrator: "", parts: [{ url: fresh.text_url }], audio_url: fresh.audio_url, source: "" })) toast("Загружаю: " + fresh.title);
      }
    });
    $("#bookmarklet").href = "javascript:(function(){window.open('" + location.origin + "/?wish='+encodeURIComponent(document.title),'_blank')})()";
    $("#wish-url").textContent = location.origin + "/?wish=Название";
    const wishParam = new URLSearchParams(location.search).get("wish");
    if (wishParam) {
      history.replaceState(null, "", location.pathname);
      const title = wishParam.replace(/\s+[-–—|].*$/, "").trim() || wishParam;
      wishApi("POST", "", { title }).then((items) => { refresh(items); toast("Сохранено: " + title); const w = items.find((x) => norm(x.title) === norm(title)); if (w && $("#wish-auto").checked) searchFor(w, true); });
    } else loadWishlist();
    return;
  }

  // ---------------- book state ----------------
  const app = $("#app"); app.hidden = false;
  const audio = $("#audio"), textEl = $("#text");
  let book, wB, wT0, wT1, wS, sFirst, sLast, sBlock, sWordsCum = [], chapStartWord = [], chapStartTime = [], duration = 0, hasAudio = false;
  let curWord = -1, curSent = -1, curBlock = -1, curChap = -1;
  let userScrolled = false, wordEls = [], sentEls = [], blockEls = [];
  // shared state lives on the server (books/<slug>/state.json) so every browser sees the same
  // position, settings and stats; localStorage is only a cache. Last writer wins by timestamp.
  let remote = {}, remoteSettings = {};
  const loadRemote = () => Promise.all([
    fetch(`/api/state/${slug}`).then((r) => r.json()).then((s) => (remote = s || {})).catch(() => (remote = {})),
    fetchSettings().then((s) => (remoteSettings = s || {})),
  ]);
  const putState = (patch, keepalive) => fetch(`/api/state/${slug}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch), keepalive: !!keepalive }).catch(() => {});

  async function load() {
    const [meta, bookJ, timingJ] = await Promise.all([
      fetch("/api/books").then((r) => r.json()).then((bs) => bs.find((b) => b.slug === slug)),
      fetch(`/books/${slug}/book.json`).then((r) => r.json()),
      fetch(`/books/${slug}/timing.json`).then((r) => (r.ok ? r.json() : null)),
      loadRemote(),
    ]);
    // settings are global on the server; older per-book copies migrate the first time they are seen
    if (!remoteSettings.settings && remote.settings) {  // per-book copy from an older version: promote it to global
      remoteSettings = { settings: remote.settings, settingsAt: remote.settingsAt || 1 };
      fetch("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(remoteSettings) }).catch(() => {});
    }
    onSettingsSynced = syncSettingsUI;
    adoptSettings(remoteSettings);
    const localStats = store.get("rs:stats:" + slug, null);
    if (localStats) putState({ stats: localStats }).then((r) => r && r.json()).then((s) => { if (s && s.stats) store.set("rs:stats:" + slug, s.stats); }).catch(() => {});
    else if (remote.stats) store.set("rs:stats:" + slug, remote.stats);
    if (!meta) throw new Error("книга не найдена");
    hasAudio = !!(timingJ && meta.audio);
    book = bookJ; duration = hasAudio ? timingJ.duration : 0;
    { const now = Date.now(); putState({ opened: now, openedAt: now }); }  // library sorts by last opened
    document.title = book.title + " — readsync";
    $("#book-title").textContent = book.title;
    const words = hasAudio ? timingJ.words : [];
    buildIndex(words);
    render(words);
    buildToc();
    $("#loading").hidden = true;
    // reading mode: audio (scrolling text follows the narrator) or pages (two-column spread, no audio)
    const remoteMode = (remote.modeAt || 0) > store.get("rs:modeAt:" + slug, 0) ? remote.mode : store.get("rs:mode:" + slug, null);
    const remoteSent = (remote.sentAt || 0) > store.get("rs:sentAt:" + slug, 0) ? remote.sent : store.get("rs:sent:" + slug, 0);
    pages.sent = Math.max(0, Math.min(sFirst.length - 1, remoteSent || 0));
    if (!hasAudio) { $("#btn-mode").hidden = true; $(".player").hidden = true; document.body.classList.add("pages"); }
    if (!hasAudio || remoteMode === "pages") document.fonts.ready.then(() => enterPages(pages.sent, false));
    if (!hasAudio) return;
    audio.src = `/books/${slug}/${meta.audio}`;
    const remoteWins = (remote.posAt || 0) > store.get("rs:posAt:" + slug, 0) && typeof remote.pos === "number";
    const pos = remoteWins ? remote.pos : store.get("rs:pos:" + slug, 0);
    if (remoteWins) { store.set("rs:pos:" + slug, remote.pos); store.set("rs:posAt:" + slug, remote.posAt); }
    audio.addEventListener("loadedmetadata", () => {
      if (isFinite(audio.duration)) { duration = audio.duration; store.set("rs:dur:" + slug, duration); }
      if (pos > 0 && pos < duration - 5) audio.currentTime = pos;
      $("#progress").max = duration;
      drawTicks();
      update(true); scrollToCurrent(true, true); settle();
    }, { once: true });
    audio.playbackRate = settings.speed; $("#speed").value = String(settings.speed);
    update(true);
  }

  function buildIndex(words) {
    const n = words.length;
    wB = new Int32Array(n); wT0 = new Float64Array(n); wT1 = new Float64Array(n); wS = new Int32Array(n);
    sFirst = []; sLast = []; sBlock = [];
    // sentences: global index per (block, sentence)
    const sentIdx = book.blocks.map(() => []);
    let cum = 0;
    for (let b = 0; b < book.blocks.length; b++) {
      for (const [a, e] of book.blocks[b].sentences) {
        sentIdx[b].push(sFirst.length); sFirst.push(-1); sLast.push(-1); sBlock.push(b);
        sWordsCum.push(cum); cum += (book.blocks[b].text.slice(a, e).match(/[\p{L}\p{N}]+/gu) || []).length;
      }
    }
    sWordsCum.push(cum);
    for (let i = 0; i < n; i++) {
      const [b, cs, , t0, t1] = words[i];
      wB[i] = b; wT0[i] = t0; wT1[i] = t1;
      const sents = book.blocks[b].sentences;
      let k = sents.findIndex(([a, e]) => cs >= a && cs < e);
      if (k < 0) { k = 0; for (let j = 0; j < sents.length; j++) if (sents[j][0] <= cs) k = j; }
      const si = sentIdx[b][k]; wS[i] = si;
      if (sFirst[si] < 0) sFirst[si] = i;
      sLast[si] = i;
    }
    book.chapters.forEach((c, ci) => {
      let w = -1;
      for (let i = 0; i < n; i++) if (wB[i] >= c.first_block) { w = i; break; }
      chapStartWord.push(w); chapStartTime.push(w >= 0 ? wT0[w] : Infinity);
      c.hidden = !c.title || c.title === "* * *";
    });
  }

  function render(words) {
    // group word indices by block
    const perBlock = new Map();
    for (let i = 0; i < words.length; i++) { const b = words[i][0]; if (!perBlock.has(b)) perBlock.set(b, []); perBlock.get(b).push(i); }
    const out = [];
    let sGlobal = 0, chapPtr = 0;
    book.blocks.forEach((blk, bi) => {
      while (chapPtr < book.chapters.length && book.chapters[chapPtr].first_block === bi) {
        out.push(`<div class="chap-anchor" id="ch${chapPtr}"></div>`); chapPtr++;
      }
      const ws = perBlock.get(bi) || [];
      const text = blk.text;
      const emIn = (a, b) => blk.em.some(([x, y]) => a < y && b > x);
      const notes = blk.notes || [];
      let html = "", pos = 0, wi = 0;
      const emit = (from, to) => {
        // plain text with note markers
        let p = from;
        for (const nt of notes) {
          if (nt.pos >= p && nt.pos <= to && nt.pos >= from) {
            html += esc(text.slice(p, nt.pos)) + `<sup class="nref" data-n="${esc(nt.id)}">${esc(nt.id.replace(/\D/g, "") || "*")}</sup>`;
            p = nt.pos;
          }
        }
        html += esc(text.slice(p, to));
      };
      blk.sentences.forEach(([a, e]) => {
        emit(pos, a); pos = a;
        html += `<span class="s" data-s="${sGlobal}">`;
        while (wi < ws.length && words[ws[wi]][1] < e) {
          const w = words[ws[wi]], cs = w[1], ce = w[2];
          emit(pos, cs);
          html += `<span class="w${emIn(cs, ce) ? " em" : ""}" data-w="${ws[wi]}">${esc(text.slice(cs, ce))}</span>`;
          pos = ce; wi++;
        }
        emit(pos, e); pos = e;
        html += `</span>`; sGlobal++;
      });
      emit(pos, text.length);
      const lvl = blk.kind === "title" ? " lvl" + (book.chapters[blk.chapter]?.level || 2) : "";
      const nextBlk = book.blocks[bi + 1];
      const stanzaEnd = blk.kind === "verse" && (!nextBlk || nextBlk.stanza !== blk.stanza) ? " stanza-end" : "";
      for (const im of blk.images || []) {
        const src = typeof im === "string" ? im : im.src, dims = im.w && im.h ? ` width="${im.w}" height="${im.h}"` : "";
        out.push(`<figure class="fig"><img src="/books/${slug}/${esc(src)}"${dims} alt="" loading="lazy"></figure>`);
      }
      out.push(`<p class="blk k-${blk.kind}${lvl}${stanzaEnd}" data-b="${bi}">${html}</p>`);
    });
    textEl.innerHTML = out.join("");
    wordEls = new Array(wT0.length); sentEls = new Array(sFirst.length); blockEls = new Array(book.blocks.length);
    textEl.querySelectorAll(".w").forEach((el) => (wordEls[+el.dataset.w] = el));
    textEl.querySelectorAll(".s").forEach((el) => (sentEls[+el.dataset.s] = el));
    textEl.querySelectorAll(".blk").forEach((el) => (blockEls[+el.dataset.b] = el));
  }

  function buildToc() {
    const ol = $("#toc-list");
    ol.innerHTML = '<li class="lib" data-lib="1"><span>← Библиотека</span></li>' + book.chapters.map((c, i) => c.hidden ? "" : `<li class="l${c.level}" data-ch="${i}"><span>${esc(c.title)}</span><span class="tt">${isFinite(chapStartTime[i]) ? fmt(chapStartTime[i]) : ""}</span></li>`).join("");
    ol.addEventListener("click", (e) => {
      const li = e.target.closest("li"); if (!li) return;
      if (li.dataset.lib) { location.href = "/"; return; }
      const i = +li.dataset.ch;
      if (pages.on) goToSentence(firstSentOfChapter(i));
      else if (isFinite(chapStartTime[i])) seek(chapStartTime[i]);
      closeDrawers();
    });
  }
  function firstSentOfChapter(ci) { const fb = book.chapters[ci].first_block; for (let s = 0; s < sBlock.length; s++) if (sBlock[s] >= fb) return s; return 0; }
  function chapterOfSent(si) { const b = sBlock[si] ?? 0; let c = 0; book.chapters.forEach((ch, i) => { if (ch.first_block <= b && !ch.hidden) c = i; }); return c; }
  function drawTicks() {
    const el = $("#chapter-ticks");
    el.innerHTML = book.chapters.map((c, i) => !c.hidden && isFinite(chapStartTime[i]) && chapStartTime[i] > 0
      ? `<i class="l${c.level}" style="left:${(chapStartTime[i] / duration) * 100}%" title="${esc(c.title)}"></i>` : "").join("");
  }

  // ---------------- sync ----------------
  function wordAt(t) {
    let lo = 0, hi = wT0.length - 1;
    if (t < wT0[0]) return 0;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (wT0[mid] <= t) lo = mid; else hi = mid - 1; }
    return lo;
  }
  let lastCounted = -1;
  function update(force) {
    if (!hasAudio || pages.on) return;
    const t = audio.currentTime + settings.offset;
    const i = wordAt(t);
    if (!audio.paused && lastCounted >= 0 && i > lastCounted && i - lastCounted < 40) session.words += i - lastCounted;
    lastCounted = i;
    if (i !== curWord || force) {
      if (curWord >= 0 && wordEls[curWord]) wordEls[curWord].classList.remove("cur");
      curWord = i; wordEls[i]?.classList.add("cur");
      const si = wS[i];
      if (si !== curSent) {
        const prevSent = curSent;
        if (curSent >= 0) sentEls[curSent]?.classList.remove("cur");
        curSent = si; sentEls[si]?.classList.add("cur");
        onSentenceChange(prevSent);
      }
      const bi = wB[i];
      if (bi !== curBlock) {
        if (curBlock >= 0) blockEls[curBlock]?.classList.remove("cur");
        curBlock = bi; blockEls[bi]?.classList.add("cur");
      }
      const ci = chapterAt(t);
      if (ci !== curChap) {
        curChap = ci;
        $("#chapter-title").textContent = book.chapters[ci]?.title || "";
        if ("mediaSession" in navigator && "MediaMetadata" in window) {
          navigator.mediaSession.metadata = new MediaMetadata({ title: book.chapters[ci]?.title || book.title, artist: book.author, album: book.title });
        }
        document.querySelectorAll("#toc-list li").forEach((li) => { const k = +li.dataset.ch; li.classList.toggle("cur", k === ci); li.classList.toggle("done", k < ci); });
      }
    }
    const sec = Math.floor(t);
    if (sec !== lastSec || force) {
      lastSec = sec;
      if (!seekingUI) $("#progress").value = t;
      $("#time-cur").textContent = fmt(t);
      const chEnd = chapStartTime.slice(curChap + 1).find((x) => isFinite(x)) ?? duration;
      const rate = audio.playbackRate || 1;
      $("#time-left").textContent = "−" + fmt((chEnd - t) / rate) + " · " + fmt((duration - t) / rate);
    }
  }
  let lastSec = -1;
  function chapterAt(t) { let c = 0; for (let i = 0; i < chapStartTime.length; i++) if (chapStartTime[i] <= t && !book.chapters[i].hidden) c = i; return c; }

  function onSentenceChange(prevSent) {
    if (sprint.stopAtSentence && prevSent >= 0) { sprint.stopAtSentence = false; audio.pause(); finishSprint(); return; }
    scrollToCurrent(false);
  }
  let settling = true;  // until the reader plays or seeks, every scroll is instant (initial positioning)
  function scrollToCurrent(force, instant) {
    const el = sentEls[curSent]; if (!el) return;
    if (settings.scroll === "off" && !force) return;
    if (userScrolled && !force) return;
    const top = parseInt(getComputedStyle(document.documentElement).getPropertyValue("--topbar-h")) || 52;
    const bottom = parseInt(getComputedStyle(document.documentElement).getPropertyValue("--player-h")) || 108;
    const vh = innerHeight - top - bottom;
    const r = el.getBoundingClientRect();
    const target = 0.38;
    const inZone = r.top > top + vh * 0.18 && r.bottom < top + vh * 0.62;
    if (settings.scroll === "line" || !inZone || force) {
      const y = scrollY + r.top - top - vh * target;
      const far = Math.abs(y - scrollY) > vh * 1.5;  // long jumps are instant, short follows are smooth
      scrollTo({ top: y, behavior: instant || settling || far ? "auto" : "smooth" });
    }
  }
  function settle() {
    // fonts and images arrive after the first layout and move the text; re-anchor until the user takes over
    const again = () => { if (settling && !userScrolled && !pages.on) scrollToCurrent(true, true); };
    document.fonts.ready.then(again);
    addEventListener("load", again, { once: true });
    [400, 1200, 2500].forEach((ms) => setTimeout(again, ms));
  }

  // 10 Hz sync loop while playing (cheap: one binary search + a few class toggles per tick)
  let tick = 0;
  audio.addEventListener("play", () => { settling = false; clearInterval(tick); tick = setInterval(() => update(false), 100); $("#btn-play").textContent = "❚❚"; session.start(); document.body.classList.add("playing"); armIdle(); armHidePlayer(); });
  audio.addEventListener("pause", () => { clearInterval(tick); update(true); $("#btn-play").textContent = "▶"; session.stop(); savePos(); document.body.classList.remove("playing", "idle"); showPlayer(); pausedAt = Date.now(); });
  // distraction-free chrome: the top bar fades after 4 s without pointer/keyboard activity while
  // playing; the player bar hides 1.5 s after play starts (scrolling does not bring it back) and
  // returns on pause or when the pointer reaches the bottom edge
  let idleTimer = 0, hideTimer = 0;
  function armIdle() {
    clearTimeout(idleTimer);
    document.body.classList.remove("idle");
    if (settings.hideUi) idleTimer = setTimeout(() => { if (!audio.paused && $("#toc").hidden && $("#settings").hidden) document.body.classList.add("idle"); }, 4000);
  }
  ["mousemove", "mousedown", "keydown", "touchstart"].forEach((ev) => addEventListener(ev, armIdle, { passive: true }));
  function armHidePlayer() {
    clearTimeout(hideTimer);
    if (settings.hideUi && !pages.on) hideTimer = setTimeout(() => { if (!audio.paused) document.body.classList.add("hide-player"); }, 1500);
  }
  function showPlayer() { clearTimeout(hideTimer); document.body.classList.remove("hide-player"); $(".player").classList.remove("peek"); }
  addEventListener("mousemove", (e) => {
    if (!document.body.classList.contains("hide-player")) return;
    const player = $(".player"), peek = player.classList.contains("peek");
    const zone = peek ? innerHeight - player.offsetHeight - 8 : innerHeight - 20;  // offsetHeight: the bar may still be sliding in
    player.classList.toggle("peek", e.clientY >= zone);
  }, { passive: true });
  // focus aid: pause when the reader leaves the tab or window
  const onLeave = () => { if (settings.pauseHidden && !audio.paused) audio.pause(); };
  document.addEventListener("visibilitychange", () => { if (document.hidden) { onLeave(); if (pages.on) session.stop(); } else if (pages.on) session.start(); });
  audio.addEventListener("seeked", () => update(true));
  audio.addEventListener("ratechange", () => update(true));
  audio.addEventListener("error", () => { $("#loading").hidden = false; $("#loading").textContent = "Ошибка аудио: " + (audio.error?.message || audio.error?.code); });
  setInterval(() => { if (!audio.paused) savePos(); }, 5000);
  addEventListener("beforeunload", () => { savePos(true); session.stop(); });
  // a tab only publishes its position after it played or seeked, so a stale background tab
  // closed later cannot clobber progress made in another browser
  let posDirty = false;
  audio.addEventListener("playing", () => { posDirty = true; });
  function savePos(keepalive) {
    if (!posDirty) return;
    const at = Date.now();
    store.set("rs:pos:" + slug, audio.currentTime); store.set("rs:posAt:" + slug, at);
    putState({ pos: audio.currentTime, posAt: at }, keepalive);
  }

  // ---------------- page mode ----------------
  function pagesLayout() {
    const gap = parseFloat(getComputedStyle(textEl).columnGap) || 0;
    pages.spreadW = textEl.clientWidth + gap;
    pages.total = Math.max(1, Math.ceil((textEl.scrollWidth - 2) / pages.spreadW));
  }
  function flowX(el) { const r = el.getClientRects()[0] || el.getBoundingClientRect(); return r.left - textEl.getBoundingClientRect().left + textEl.scrollLeft; }
  function sentAtSpread(n) {
    const x0 = n * pages.spreadW - 1;
    let lo = 0, hi = sentEls.length - 1, ans = hi;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (flowX(sentEls[mid]) >= x0) { ans = mid; hi = mid - 1; } else lo = mid + 1; }
    return ans;
  }
  let sentTimer = 0;
  function goSpread(n, save = true) {
    n = Math.max(0, Math.min(pages.total - 1, n));
    const prevSent = pages.sent;
    pages.cur = n; textEl.scrollLeft = n * pages.spreadW; pages.sent = sentAtSpread(n);
    if (save && pages.sent > prevSent && pages.sent - prevSent < 400) session.words += sWordsCum[pages.sent] - sWordsCum[prevSent];
    $("#pg-info").textContent = `${pages.cur + 1} / ${pages.total} · ${book.chapters[chapterOfSent(pages.sent)]?.title || ""}`;
    $("#chapter-title").textContent = book.chapters[chapterOfSent(pages.sent)]?.title || "";
    if (save) {
      const at = Date.now(); store.set("rs:sent:" + slug, pages.sent); store.set("rs:sentAt:" + slug, at);
      clearTimeout(sentTimer); sentTimer = setTimeout(() => putState({ sent: pages.sent, sentAt: at, sentPct: Math.round((pages.sent / sFirst.length) * 100) }), 300);
    }
  }
  function goToSentence(si, save = true) { const el = sentEls[si]; goSpread(el ? Math.floor((flowX(el) + 1) / pages.spreadW) : 0, save); }
  function saveMode(m) { const at = Date.now(); store.set("rs:mode:" + slug, m); store.set("rs:modeAt:" + slug, at); putState({ mode: m, modeAt: at }); }
  function enterPages(si, save = true) {
    if (hasAudio && !audio.paused) audio.pause();
    pages.on = true; document.body.classList.add("pages"); $("#pager").hidden = false; $("#btn-mode").textContent = "🎧"; $("#btn-mode").title = "Вернуться к аудио (m)";
    closeDrawers(); pagesLayout(); goToSentence(si ?? pages.sent, false);
    if (save) saveMode("pages");
    session.start();
  }
  function exitPages() {
    pages.on = false; document.body.classList.remove("pages"); $("#pager").hidden = true; $("#btn-mode").textContent = "📖"; $("#btn-mode").title = "Режим книги без аудио (m)";
    session.stop(); saveMode("audio");
    const st = sentStart(pages.sent);
    if (st != null) seek(st); else update(true);
  }
  function toggleMode() { if (!hasAudio) return; pages.on ? exitPages() : enterPages(curSent >= 0 ? curSent : pages.sent); }
  $("#btn-mode").onclick = toggleMode;
  $("#pg-prev").onclick = () => goSpread(pages.cur - 1);
  $("#pg-next").onclick = () => goSpread(pages.cur + 1);
  addEventListener("resize", () => { if (pages.on) { pagesLayout(); goToSentence(pages.sent, false); } });
  document.fonts.addEventListener("loadingdone", () => { if (pages.on) { pagesLayout(); goToSentence(pages.sent, false); } });

  // ---------------- controls ----------------
  let pausedAt = 0;
  function play() {
    if (settings.rewind && pausedAt && Date.now() - pausedAt > 8000) {
      const st = sentStart(curSent);
      if (st != null && audio.currentTime - st > 1.5) { audio.currentTime = st; update(true); }
    }
    return audio.play();
  }
  function seek(t) { settling = false; audio.currentTime = Math.max(0, Math.min(duration || 1e9, t)); posDirty = true; userScrolled = false; $("#return-pill").hidden = true; update(true); scrollToCurrent(true); }
  // coming back to a paused tab: adopt a newer position/settings written by another browser
  document.addEventListener("visibilitychange", () => {
    if (document.hidden || !audio.paused) return;
    loadRemote().then(() => {
      if ((remote.posAt || 0) > store.get("rs:posAt:" + slug, 0) && typeof remote.pos === "number") {
        audio.currentTime = remote.pos; posDirty = false; store.set("rs:pos:" + slug, remote.pos); store.set("rs:posAt:" + slug, remote.posAt);
        update(true); scrollToCurrent(true);
      }
      adoptSettings(remoteSettings);
    });
  });
  function toggle() { audio.paused ? play() : audio.pause(); }
  function sentStart(si) { return si >= 0 && sFirst[si] >= 0 ? wT0[sFirst[si]] : null; }
  function prevSentence() {
    // if we are >1.5s into the sentence, restart it; otherwise go to previous
    let si = curSent; const st = sentStart(si);
    if (st != null && audio.currentTime - st > 1.5) return seek(st);
    for (let k = si - 1; k >= 0; k--) if (sFirst[k] >= 0) return seek(wT0[sFirst[k]]);
  }
  function nextSentence() { for (let k = curSent + 1; k < sFirst.length; k++) if (sFirst[k] >= 0) return seek(wT0[sFirst[k]]); }
  function repeatSentence() { const st = sentStart(curSent); if (st != null) { seek(st); if (audio.paused) audio.play(); } }
  function toggleDim() { settings.dimMode = settings.dimMode === "off" ? (settings.lastDim || "para") : "off"; if (settings.dimMode !== "off") settings.lastDim = settings.dimMode; applySettings(); syncSettingsUI(); persistSettings(); }
  function setSpeed(v) { v = Math.min(2, Math.max(0.5, +v)); audio.playbackRate = v; settings.speed = v; $("#speed").value = String(v); store.set("rs:settings", settings); persistSettings(); }

  $("#btn-play").onclick = toggle;
  $("#btn-back").onclick = () => seek(audio.currentTime - 10);
  $("#btn-fwd").onclick = () => seek(audio.currentTime + 10);
  $("#btn-prev-sent").onclick = prevSentence;
  $("#btn-next-sent").onclick = nextSentence;
  $("#speed").onchange = (e) => setSpeed(e.target.value);
  $("#btn-focus").onclick = toggleDim;
  let seekingUI = false;
  const prog = $("#progress");
  prog.addEventListener("input", () => { seekingUI = true; $("#time-cur").textContent = fmt(+prog.value); });
  prog.addEventListener("change", () => { seekingUI = false; seek(+prog.value); });

  textEl.addEventListener("click", (e) => {
    const nref = e.target.closest(".nref");
    if (nref) { showNote(nref); e.stopPropagation(); return; }
    if (pages.on) {
      if (getSelection().toString()) return;
      const r = textEl.getBoundingClientRect(), x = (e.clientX - r.left) / r.width;
      if (x < 0.3) goSpread(pages.cur - 1); else if (x > 0.7) goSpread(pages.cur + 1);
      return;
    }
    const w = e.target.closest(".w"), s = e.target.closest(".s");
    if (settings.clickWord && w) return seek(wT0[+w.dataset.w]);
    if (s) { const st = sentStart(+s.dataset.s); if (st != null) seek(st); }
  });

  // user scroll detection
  const onUserScroll = () => { if (settings.scroll === "off" || pages.on) return; userScrolled = true; $("#return-pill").hidden = false; };
  addEventListener("wheel", onUserScroll, { passive: true });
  addEventListener("touchmove", onUserScroll, { passive: true });
  $("#return-pill").onclick = () => { userScrolled = false; $("#return-pill").hidden = true; scrollToCurrent(true); };

  // keyboard
  addEventListener("keydown", (e) => {
    if (e.target && e.target.matches && e.target.matches("input, select, textarea")) return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;  // leave browser/system shortcuts alone
    const k = e.key === "Spacebar" || e.code === "Space" ? " " : e.key;
    if (k === "m") { toggleMode(); return; }
    if (pages.on) {
      if (k === "ArrowRight" || k === "PageDown" || (k === " " && !e.shiftKey)) { e.preventDefault(); goSpread(pages.cur + 1); }
      else if (k === "ArrowLeft" || k === "PageUp" || (k === " " && e.shiftKey)) { e.preventDefault(); goSpread(pages.cur - 1); }
      else if (k === "Home") goSpread(0);
      else if (k === "End") goSpread(pages.total - 1);
      else if (k === "t") toggleDrawer("#toc");
      else if (k === "Escape") { closeDrawers(); $("#note-pop").hidden = true; $("#sprint-menu").hidden = true; }
      return;
    }
    if (k === " ") { e.preventDefault(); toggle(); }
    else if (k === "ArrowLeft") { e.preventDefault(); e.shiftKey ? seek(audio.currentTime - 10) : prevSentence(); }
    else if (k === "ArrowRight") { e.preventDefault(); e.shiftKey ? seek(audio.currentTime + 10) : nextSentence(); }
    else if (k === "r") repeatSentence();
    else if (k === "[") setSpeed(audio.playbackRate - 0.1);
    else if (k === "]") setSpeed(audio.playbackRate + 0.1);
    else if (k === "f") toggleDim();
    else if (k === "t") toggleDrawer("#toc");
    else if (k === "a") { userScrolled = false; $("#return-pill").hidden = true; scrollToCurrent(true); }
    else if (k === "Escape") { closeDrawers(); $("#note-pop").hidden = true; $("#sprint-menu").hidden = true; }
  });
  if ("mediaSession" in navigator) {
    navigator.mediaSession.setActionHandler("play", () => play());
    navigator.mediaSession.setActionHandler("pause", () => audio.pause());
    navigator.mediaSession.setActionHandler("seekbackward", () => seek(audio.currentTime - 10));
    navigator.mediaSession.setActionHandler("seekforward", () => seek(audio.currentTime + 10));
    navigator.mediaSession.setActionHandler("previoustrack", prevSentence);
    navigator.mediaSession.setActionHandler("nexttrack", nextSentence);
  }

  // ---------------- drawers / settings ----------------
  function toggleDrawer(sel) {
    const el = $(sel); const open = el.hidden; closeDrawers();
    if (open) {
      el.hidden = false; $("#scrim").hidden = false;
      if (sel === "#settings") { renderStats(); loadRemote().then(() => { if (remote.stats) { store.set("rs:stats:" + slug, remote.stats); renderStats(); } }); }
    }
  }
  function closeDrawers() { $("#toc").hidden = true; $("#settings").hidden = true; $("#scrim").hidden = true; }
  $("#btn-toc").onclick = () => toggleDrawer("#toc");
  $("#btn-settings").onclick = () => toggleDrawer("#settings");
  $("#scrim").onclick = closeDrawers;
  document.querySelectorAll("[data-close]").forEach((b) => (b.onclick = closeDrawers));
  function syncSettingsUI() {
    $("#set-font").value = settings.font; $("#set-lh").value = settings.lh; $("#set-width").value = settings.width;
    $("#set-family").value = settings.family; $("#set-sent").checked = !!settings.sent; $("#set-word").checked = !!settings.word; $("#set-dim").value = settings.dimMode;
    $("#set-ui").value = settings.ui; $("#set-weight").value = settings.weight; $("#set-rewind").checked = !!settings.rewind;
    $("#set-offset").value = Math.round(settings.offset * 1000); $("#offset-out").textContent = (settings.offset > 0 ? "+" : "") + Math.round(settings.offset * 1000) + " мс";
    $("#set-scroll").value = settings.scroll; $("#set-click-word").checked = !!settings.clickWord;
    $("#set-word-style").value = settings.wordStyle; $("#set-hide-ui").checked = !!settings.hideUi; $("#set-pause-hidden").checked = !!settings.pauseHidden;
    document.querySelectorAll("#set-theme button").forEach((b) => b.classList.toggle("on", b.dataset.v === settings.theme));
    $("#btn-focus").classList.toggle("on", settings.dimMode !== "off");
  }
  const bind = (sel, key, conv = (v) => v) => $(sel).addEventListener("input", (e) => { settings[key] = conv(e.target.type === "checkbox" ? e.target.checked : e.target.value); applySettings(); syncSettingsUI(); persistSettings(); });
  bind("#set-font", "font", Number); bind("#set-lh", "lh", Number); bind("#set-width", "width", Number);
  bind("#set-family", "family"); bind("#set-sent", "sent"); bind("#set-word", "word"); bind("#set-dim", "dimMode"); bind("#set-scroll", "scroll"); bind("#set-click-word", "clickWord");
  bind("#set-ui", "ui"); bind("#set-weight", "weight", Number); bind("#set-rewind", "rewind"); bind("#set-offset", "offset", (v) => Number(v) / 1000);
  $("#set-dim").addEventListener("input", () => { if (settings.dimMode !== "off") settings.lastDim = settings.dimMode; });
  $("#set-offset").addEventListener("input", () => update(true));
  bind("#set-word-style", "wordStyle"); bind("#set-hide-ui", "hideUi"); bind("#set-pause-hidden", "pauseHidden");
  $("#set-theme").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; settings.theme = b.dataset.v; applySettings(); syncSettingsUI(); persistSettings(); });
  syncSettingsUI();

  // ---------------- notes ----------------
  function showNote(el) {
    const pop = $("#note-pop"); const txt = book.notes[el.dataset.n];
    if (!txt) return;
    pop.textContent = txt; pop.hidden = false;
    const r = el.getBoundingClientRect();
    pop.style.left = Math.max(8, Math.min(innerWidth - 348, r.left - 100)) + "px";
    pop.style.top = (r.bottom + 8) + "px";
  }
  addEventListener("click", (e) => { if (!e.target.closest("#note-pop, .nref")) $("#note-pop").hidden = true; if (!e.target.closest("#sprint-menu, #btn-sprint, #pg-sprint")) $("#sprint-menu").hidden = true; });

  // ---------------- sessions & stats ----------------
  const session = {
    t0: null, words: 0,
    start() { if (this.t0 != null) return; this.t0 = Date.now(); this.words = 0; lastCounted = curWord; },
    stop() {
      if (this.t0 == null) return;
      const sec = (Date.now() - this.t0) / 1000, words = Math.round(this.words);
      this.t0 = null; this.words = 0;
      if (sec < 2) return;
      const st = store.get("rs:stats:" + slug, { days: {} });
      const d = st.days[today()] || { sec: 0, words: 0 };
      d.sec += sec; d.words += words; st.days[today()] = d; store.set("rs:stats:" + slug, st);
      sprint.words += words;
      fetch(`/api/state/${slug}/session`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ day: today(), sec, words }), keepalive: true })
        .then((r) => r.json()).then((s) => { if (s && s.stats) store.set("rs:stats:" + slug, s.stats); }).catch(() => {});
    },
  };
  function renderStats() {
    const st = store.get("rs:stats:" + slug, { days: {} });
    const days = Object.keys(st.days).sort();
    const tot = days.reduce((a, k) => a + st.days[k].sec, 0), totW = days.reduce((a, k) => a + st.days[k].words, 0);
    const td = st.days[today()] || { sec: 0, words: 0 };
    let streak = 0; const dt = new Date();
    for (;;) { const k = dt.toISOString().slice(0, 10); if (st.days[k]?.sec > 60) { streak++; dt.setDate(dt.getDate() - 1); } else break; }
    const pct = hasAudio && duration ? Math.round((audio.currentTime / duration) * 100) : Math.round((pages.sent / Math.max(1, sFirst.length)) * 100);
    const week = []; const d2 = new Date();
    for (let i = 6; i >= 0; i--) { const x = new Date(d2); x.setDate(d2.getDate() - i); const k = x.toISOString().slice(0, 10); week.push({ k, sec: st.days[k]?.sec || 0, wd: ["вс", "пн", "вт", "ср", "чт", "пт", "сб"][x.getDay()] }); }
    const max = Math.max(60, ...week.map((w) => w.sec));
    const bars = `<div class="bars">${week.map((w) => `<div class="${w.k === today() ? "today" : ""}" style="height:${Math.max(4, (w.sec / max) * 100)}%" title="${fmt(w.sec)}"></div>`).join("")}</div>
      <div class="bars-labels">${week.map((w) => `<span>${w.wd}</span>`).join("")}</div>`;
    $("#stats").innerHTML = `Сегодня: <b>${fmt(td.sec)}</b>, ${Math.round(td.words)} слов<br>Всего: <b>${fmt(tot)}</b>, ${Math.round(totW)} слов<br>Серия: <b>${streak}</b> дн.<br>Прогресс книги: <b>${pct}%</b>${hasAudio ? " · осталось " + fmt((duration - audio.currentTime) / audio.playbackRate) : ""}${bars}`;
  }

  // ---------------- sprint timer ----------------
  const sprint = { end: null, timer: 0, minutes: 0, words: 0, sents0: 0, stopAtSentence: false };
  $("#btn-sprint").onclick = $("#pg-sprint").onclick = (e) => { e.stopPropagation(); const m = $("#sprint-menu"); m.hidden = !m.hidden; $("#sprint-stop").hidden = !sprint.end; };
  $("#sprint-menu").addEventListener("click", (e) => { const b = e.target.closest("button[data-min]"); if (b) startSprint(+b.dataset.min); });
  $("#sprint-stop").onclick = () => { stopSprint(); $("#sprint-menu").hidden = true; };
  $("#sprint-close").onclick = () => { $("#sprint-done").hidden = true; };
  $("#sprint-break").onclick = () => { $("#sprint-done").hidden = true; startBreak(5); };
  $("#break-close").onclick = () => { $("#break-done").hidden = true; };
  $("#break-sprint").onclick = () => { $("#break-done").hidden = true; startSprint(sprint.minutes || 25); };
  $("#sprint-again").onclick = () => { $("#sprint-done").hidden = true; startSprint(sprint.minutes); };
  // rest break between sprints: audio stays paused, badge counts down, soft chime at the end
  function startBreak(min) {
    stopSprint(); if (!audio.paused) audio.pause();
    const end = Date.now() + min * 60000, badge = $("#sprint-badge"); badge.hidden = false; badge.classList.remove("ending");
    $("#btn-sprint").classList.add("on"); $("#pg-sprint").classList.add("on");
    sprint.timer = setInterval(() => {
      const left = end - Date.now();
      if (left <= 0) { stopSprint(); chime(); $("#break-done").hidden = false; return; }
      badge.textContent = "перерыв " + fmt(left / 1000);
    }, 500);
  }
  function chime() {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      [523.25, 659.25, 783.99].forEach((f, i) => {
        const o = ctx.createOscillator(), g = ctx.createGain();
        o.type = "sine"; o.frequency.value = f; o.connect(g); g.connect(ctx.destination);
        const t0 = ctx.currentTime + i * 0.18;
        g.gain.setValueAtTime(0, t0); g.gain.linearRampToValueAtTime(0.15, t0 + 0.02); g.gain.exponentialRampToValueAtTime(0.001, t0 + 0.6);
        o.start(t0); o.stop(t0 + 0.65);
      });
    } catch {}
  }
  window.readsync = { startSprint, startBreak, seek, setSpeed };
  function startSprint(min) {
    stopSprint(); sprint.minutes = min; sprint.end = Date.now() + min * 60000; sprint.words = 0; sprint.sents0 = pages.on ? pages.sent : curSent; sprint.startWord = curWord;
    $("#sprint-menu").hidden = true; $("#btn-sprint").classList.add("on"); $("#pg-sprint").classList.add("on");
    const badge = $("#sprint-badge"); badge.hidden = false;
    sprint.timer = setInterval(() => {
      const left = sprint.end - Date.now();
      if (left <= 0) { clearInterval(sprint.timer); badge.textContent = "финиш…"; badge.classList.add("ending"); if (audio.paused || pages.on) finishSprint(); else sprint.stopAtSentence = true; return; }
      badge.textContent = fmt(left / 1000); badge.classList.toggle("ending", left < 60000);
    }, 500);
    if (audio.paused && !pages.on && hasAudio) play();
  }
  function stopSprint() { clearInterval(sprint.timer); sprint.end = null; sprint.stopAtSentence = false; $("#sprint-badge").hidden = true; $("#sprint-badge").classList.remove("ending"); $("#btn-sprint").classList.remove("on"); $("#pg-sprint").classList.remove("on"); }
  function finishSprint() {
    session.stop();
    const sents = Math.max(0, (pages.on ? pages.sent : curSent) - sprint.sents0), words = pages.on ? Math.round(sprint.words) : Math.max(0, curWord - Math.max(0, sprint.startWord ?? curWord));
    stopSprint();
    $("#sprint-summary").innerHTML = `${sprint.minutes} мин фокуса.<br>Прочитано: <b>${sents}</b> предложений, <b>${words}</b> слов.<br>Сделай паузу — потом ещё один.`;
    $("#sprint-done").hidden = false;
  }

  load().catch((e) => { $("#loading").textContent = "Ошибка: " + e.message; console.error(e); });
})();
