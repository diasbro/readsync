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
    const toast = (msg) => { const el = document.createElement("div"); el.className = "toast"; el.textContent = msg; document.body.appendChild(el); setTimeout(() => el.remove(), 3000); };
    const q = (x) => encodeURIComponent(x);
    const norm = (x) => (x || "").toLowerCase().replace(/ё/g, "е").replace(/[^\p{L}\p{N}]+/gu, " ").trim();
    const isUrl = (x) => /^https?:\/\//i.test(x);
    // preferences popover: theme and interface font, the two settings that shape every page
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
    $("#lib-ui").addEventListener("input", (e) => { settings.ui = e.target.value; applySettings(); persistSettings(); syncPrefsUI(); });
    $("#bookmarklet").href = "javascript:(function(){window.open('" + location.origin + "/?wish='+encodeURIComponent(document.title),'_blank')})()";
    $("#wish-url").textContent = location.origin + "/?wish=Название";
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

    // ---- one grid for everything: books, books still loading, and titles saved without text ----
    // Shelves: a book with more than 10 minutes of reading is "reading now" unless moved by hand;
    // within a shelf the most recent activity (opened, or added) comes first.
    // A title saved without text is a "shell" card: it waits in the catalog until a text is picked
    // from the library search or attached by hand. A ready book keeps that search result, so another
    // edition replaces the text in place.
    let books = [], wishes = [], jobs = {}, open = null, confirmDel = null;  // open: card with its text panel open; confirmDel: slug awaiting "delete?"
    const READING_SEC = 600;
    const SOURCE = { "fantasy-worlds": "fantasy-worlds", flibusta: "Flibusta", coollib: "Coollib" };
    const SOURCES_LABEL = "fantasy-worlds, Flibusta, Coollib";
    const searching = new Map();  // wish id -> {busy, t0} | {status, warn}: this session's search state
    const hidden = new Set();  // cards whose result list is folded away
    const shelfOf = (b) => (b.state.shelf ? b.state.shelf : b.state.finished ? "library" : b.state.seconds > READING_SEC ? "reading" : "library");
    const setShelf = (slug, shelf) => fetch(`/api/state/${slug}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ shelf, shelfAt: Date.now() }) });
    const wishApi = (method, path, body) => fetch("/api/wishlist" + (path || ""), { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined }).then((r) => r.json());
    const ytLink = (title) => `<a target="_blank" rel="noopener" href="https://www.youtube.com/results?search_query=${q(title + " аудиокнига")}">YouTube</a>`;
    const kb = (n) => (n == null ? "" : n >= 1000 ? (n / 1024).toFixed(1).replace(".", ",") + " МБ" : n + " КБ");
    const bookMeta = (b) => [b.author, b.translator ? "пер. " + b.translator : null, b.year, b.has_audio && b.narrator ? "читает " + b.narrator : null].filter(Boolean).join(" · ");

    // ---- search result rows: one row per edition, exactly as the catalog describes it ----
    const hitMeta = (h) => [h.author, h.translator ? "пер. " + h.translator : "", h.year, h.parts_label, h.parts[0].kind === "html" ? "страница" : h.parts[0].kind, kb(h.size_kb), SOURCE[h.source] || h.source, h.lang && h.lang !== "ru" ? h.lang : ""].filter(Boolean).join(" · ");
    const hitData = (h) => esc(JSON.stringify({ title: h.title, author: h.author, translator: h.translator, year: h.year, narrator: h.narrator, urls: h.parts.map((p) => p.url), audio: h.audio_url }));
    const LOADABLE = new Set(["fb2", "epub", "txt", "html"]);
    const rowHtml = (h) => `<div class="cand"><div class="ct">${esc(h.title)}<div class="cm">${esc(hitMeta(h))}</div></div>${LOADABLE.has(h.parts[0].kind) ? `<button class="btn sm pick" data-hit='${hitData(h)}'>Загрузить</button>` : `<span class="cm">${esc(h.parts[0].kind)}, не открою</span>`}</div>`;
    const SHOW = 5;
    function rowsHtml(rows) {
      // same title and author from one source: several editions fold into one line
      const groups = [];
      rows.forEach((h) => { const key = h.source + "|" + norm(h.title) + "|" + norm(h.author); const g = groups.find((x) => x.key === key); if (g) g.rows.push(h); else groups.push({ key, rows: [h] }); });
      const one = (g) => (g.rows.length === 1 ? rowHtml(g.rows[0])
        : `<details class="eds"><summary><span class="ct">${esc(g.rows[0].title)}<div class="cm">${esc([g.rows[0].author, g.rows.length + " изданий", SOURCE[g.rows[0].source]].filter(Boolean).join(" · "))}</div></span><span class="link-btn">издания</span></summary><div class="cands">${g.rows.map(rowHtml).join("")}</div></details>`);
      const head = groups.slice(0, SHOW).map(one).join("");
      const rest = groups.slice(SHOW);
      return head + (rest.length ? `<details class="more"><summary>ещё ${rest.length}</summary><div class="cands">${rest.map(one).join("")}</div></details>` : "");
    }
    function resultsHtml(hits, author) {
      if (!hits?.length && !author?.hits?.length) return "";
      return `<div class="cands">${hits?.length ? rowsHtml(hits) : ""}${author?.hits?.length ? `<div class="hd">у автора ${esc(author.name)}</div>${rowsHtml(author.hits)}` : ""}</div>`;
    }
    // the text panel: pick an edition from the saved search, or give a link or a file
    function panelHtml(id, hasResults) {
      return `<div class="attach-form text-panel" data-id="${esc(id)}">${hasResults ? "" : ""}
          <input name="text_url" placeholder="Ссылка на текст: страница книги, fb2, epub, txt">
          <label class="file-row">или файл <input type="file" name="text_file" accept=".fb2,.zip,.epub,.txt,.html,.htm"></label>
          <input name="audio_url" placeholder="Аудио: ссылка на YouTube, необязательно">
          <div class="row-btns"><button class="btn sm primary text-go">Загрузить</button><button class="btn sm attach-cancel">Отмена</button></div></div>`;
    }

    function cardHtml(b, shelf) {
        const st = b.state, job = jobs[b.slug], failed = !!(job && !job.running && job.exit !== 0);
        const pos = st.pos || 0, dur = st.duration || store.get("rs:dur:" + b.slug, 0);
        const pct = st.finished ? 100 : b.has_audio ? (dur ? Math.round((pos / dur) * 100) : 0) : (st.sentPct || 0);
        const meta = [bookMeta(b), !b.ready ? (b.building ? "загружается…" : failed ? null : "не загрузилась до конца") : b.building ? "заменяю текст…" : null].filter(Boolean).join(" · ");
        const fail = failed ? `<div class="m status warn">${b.ready ? "не загрузилось" : "не загрузилась"}: ${esc(job.log[job.log.length - 1] || "код " + job.exit)}</div>` : "";
        const frag = b.fragment_note ? `<div class="m status warn">в конце текста «${esc(b.fragment_note)}»</div>` : "";
        const tags = b.ready ? '<span class="tag">текст</span>' + (b.has_audio ? '<span class="tag">аудио</span>' : "") + (st.finished ? '<span class="tag done">прочитано</span>' : "") : "";
        const where = !b.ready ? "" : st.finished ? "прочитано целиком" : b.has_audio ? (pct ? `прочитано ${pct}% · ${fmt(pos)}` : "не начато") : (st.sent ? `прочитано ${pct}%` : "не начато");
        const cover = b.cover ? `<img class="cover" src="/books/${esc(b.slug)}/${esc(b.cover)}" alt="">` : `<div class="cover empty">${esc((b.title || b.slug).slice(0, 1))}</div>`;
        const move = b.ready ? (shelf === "reading" ? `<button class="link-btn shelf-btn" data-slug="${esc(b.slug)}" data-shelf="library">убрать из текущих</button>` : `<button class="link-btn shelf-btn" data-slug="${esc(b.slug)}" data-shelf="reading">в текущие</button>`) : "";
        const del = b.building ? "" : confirmDel === b.slug
          ? `<span class="confirm">удалить книгу${b.has_audio ? " с аудио" : ""}? <button class="link-btn del-book" data-slug="${esc(b.slug)}">да</button> <button class="link-btn del-cancel">нет</button></span>`
          : `<button class="link-btn del-ask" data-slug="${esc(b.slug)}">удалить</button>`;
        const replace = b.ready && !b.building ? `<button class="link-btn text-btn" data-slug="${esc(b.slug)}">заменить текст</button>` : "";
        const side = `<div class="side">${b.ready && !b.has_audio && !b.building ? `<button class="btn sm attach-btn" data-slug="${esc(b.slug)}">＋ аудио</button>` : ""}${replace}${move}${del}</div>`;
        let panel = "";
        if (open === b.slug + ":audio") panel = `<div class="attach-form" data-slug="${esc(b.slug)}">
            <textarea name="audio_url" rows="2" placeholder="Ссылки на аудио: YouTube, части по одной в строке"></textarea>
            <label class="file-row">или файл <input type="file" name="audio_file" accept="audio/*,.m4b,.m4a,.mp3"></label>
            <input name="narrator" placeholder="Чтец (необязательно)">
            <label class="row small muted"><input type="checkbox" name="align"> точное выравнивание сразу (долго)</label>
            <div class="row-btns"><button class="btn sm primary attach-go">Загрузить аудио</button><button class="btn sm attach-cancel">Отмена</button></div></div>`;
        else if (open === b.slug) panel = `<div class="m">Другое издание вместо этого текста. Позиция в книге без аудио начнётся заново.</div>${resultsHtml(b.hitsData?.hits, b.hitsData?.author_hits)}${panelHtml(b.slug)}`;
        return `<div class="card${panel ? " attach" : ""}" data-slug="${esc(b.slug)}">${panel ? "" : `<a class="cover-link" href="${b.ready ? "?book=" + esc(b.slug) : "#"}">${cover}</a>`}
          <div class="body">${panel ? `<div class="t">${esc(b.title || b.slug)}</div>${open === b.slug + ":audio" ? `<div class="m">Аудио для этой книги. Голос выбери сам: ${ytLink(b.title || "")}</div>` : ""}${panel}` :
            `<a href="${b.ready ? "?book=" + esc(b.slug) : "#"}" class="tlink"><div class="t">${esc(b.title || b.slug)}${tags}</div></a><div class="m${meta ? "" : " empty"}">${esc(meta)}</div>${fail}${frag}
          <div class="bar${pct ? "" : " empty"}"><i style="width:${pct}%"></i></div><div class="m${pct ? "" : " empty"}">${where}</div>`}</div>${panel ? "" : side}</div>`;
    }
    function shellHtml(w) {
      const s = searching.get(w.id) || {};
      const hasResults = !!(w.hits?.length || w.author_hits?.hits?.length);
      let status = "";
      if (s.busy) status = `<span class="spin"></span>ищу в ${SOURCES_LABEL}… <span class="sec">${Math.round((Date.now() - s.t0) / 1000)}</span> с`;
      else if (s.status) status = s.status;
      else if (w.searched && !hasResults) status = `не нашлось в ${SOURCES_LABEL}`;
      const warn = !s.busy && !!status;
      const results = hasResults && !hidden.has(w.id) ? resultsHtml(w.hits, w.author_hits) : "";
      const panel = open === w.id ? panelHtml(w.id) : "";
      const links = hasResults ? `<div class="mini"><button class="link-btn fold-btn" data-id="${esc(w.id)}">${hidden.has(w.id) ? "показать варианты" : "скрыть варианты"}</button><button class="link-btn find-btn" data-id="${esc(w.id)}">искать снова</button></div>` : "";
      const side = panel ? "" : `<div class="side"><button class="btn sm text-btn" data-id="${esc(w.id)}">＋ текст</button>${hasResults ? "" : `<button class="link-btn find-btn" data-id="${esc(w.id)}">${w.searched ? "искать снова" : "найти текст"}</button>`}<button class="link-btn del-btn" data-id="${esc(w.id)}">убрать</button></div>`;
      return `<div class="card shell${panel ? " attach" : ""}" data-id="${esc(w.id)}">${panel ? "" : `<div class="cover empty">${esc(w.title.slice(0, 1))}</div>`}
        <div class="body"><div class="t">${esc(w.title)}<span class="tag">без текста</span></div>${w.author ? `<div class="m">${esc(w.author)}</div>` : ""}
        ${panel ? `<div class="m">Текст для этой книги. Аудио: ${ytLink(w.title)}</div>` : ""}<div class="m status${warn ? " warn" : ""}${status ? "" : " empty"}">${status}</div>${results}${links}${panel}</div>${side}</div>`;
    }

    // ---- the one line: filters the library as you type; what is not there can be saved from the same line ----
    const omni = $("#omni-input"), kbd = $("#omni-kbd");
    let query = "";
    const matches = (x) => !query || norm(x.title + " " + (x.author || "") + " " + (x.translator || "")).includes(norm(query));
    function addRowHtml(any) {
      if (isUrl(query)) return `<div class="card add" data-act="link"><div class="body"><div class="t"><b>＋</b>Загрузить по ссылке</div><div class="m">↵ · название возьму из книги</div></div></div>`;
      return `<div class="card add" data-act="save"><div class="body"><div class="t"><b>＋</b>Сохранить «${esc(query)}»${any ? " как новую книгу" : ""}</div><div class="m">${any ? "⌘↵" : "↵"} · без текста; найти его или добавить свой можно потом</div></div></div>`;
    }
    // paint() lays out what is already loaded; renderLibrary() fetches first
    function paint() {
      const byActivity = (a, b) => (b.at || 0) - (a.at || 0) || (a.title || "").localeCompare(b.title || "", "ru");
      const entry = (b, shelf) => ({ at: b.state.opened || b.added || 0, title: b.title, html: cardHtml(b, shelf) });
      const reading = books.filter((b) => shelfOf(b) === "reading" && matches(b)).map((b) => entry(b, "reading")).sort(byActivity);
      const rest = [...books.filter((b) => shelfOf(b) !== "reading" && matches(b)).map((b) => entry(b, "library")),
        ...wishes.filter(matches).map((w) => ({ at: +w.id.slice(1) || 0, title: w.title, html: shellHtml(w) }))].sort(byActivity);
      const any = reading.length + rest.length > 0;
      $("#lib-title").textContent = query ? (any ? `Найдено ${reading.length + rest.length}` : "В библиотеке нет") : reading.length ? "Остальные" : "Библиотека";
      $("#reading-section").hidden = !reading.length;
      $("#reading-list").innerHTML = reading.map((x) => x.html).join("");
      $("#library-list").innerHTML = rest.map((x) => x.html).join("") + (query ? addRowHtml(any) : !any ? '<p class="muted small">Пока пусто. Напиши название книги в строке выше, вставь ссылку или перетащи файл.</p>' : "");
      kbd.textContent = query ? "esc" : "/";
    }
    async function renderLibrary() {
      [books, wishes] = await Promise.all([fetch("/api/books").then((r) => r.json()), wishApi("GET").catch(() => wishes)]);
      paint();
      headerLine(books.filter((b) => shelfOf(b) === "reading"), books);
    }
    omni.addEventListener("input", () => { query = omni.value.trim(); paint(); });
    omni.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { omni.value = ""; query = ""; paint(); omni.blur(); return; }
      if (e.key !== "Enter" || !query) return;
      e.preventDefault();
      const any = books.some(matches) || wishes.some(matches);
      if (isUrl(query)) addByLink(query);
      else if (!any || e.metaKey || e.ctrlKey) addTitle(query);
    });
    addEventListener("keydown", (e) => { if (e.key === "/" && !(e.target instanceof Element && e.target.matches("input, textarea, select"))) { e.preventDefault(); omni.focus(); } });
    const clearOmni = () => { omni.value = ""; query = ""; };

    // ---- jobs: background pipeline runs; a card shows "loading" while its job runs and the error if it fails ----
    let jobsTimer = 0, jobsRunning = false;
    async function pollJobs() {
      jobs = await fetch("/api/jobs").then((r) => r.json()).catch(() => jobs);
      const running = Object.values(jobs).some((j) => j.running);
      clearTimeout(jobsTimer);
      if (running) jobsTimer = setTimeout(pollJobs, 3000);
      else if (jobsRunning) renderLibrary();
      jobsRunning = running;
    }
    let adding = false;  // one /api/add at a time: a double click must not start a second job
    async function startAdd(fields) {
      if (adding) return false;
      adding = true;
      try {
        const fd = new FormData(); Object.entries(fields).forEach(([k, v]) => { if (v != null && v !== "") fd.set(k, v); });
        const r = await fetch("/api/add", { method: "POST", body: fd }).then((x) => x.json()).catch((err) => ({ error: String(err) }));
        if (r.error) { toast("Ошибка: " + r.error); return false; }
        await pollJobs(); return r.slug;
      } finally { adding = false; }
    }
    const saveHits = (slug, w) => fetch(`/api/hits/${slug}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ hits: w.hits || [], author_hits: w.author_hits || null }) }).catch(() => {});

    // ---- adding: a title becomes a shell card; a link or a dropped file loads right away ----
    async function addTitle(title) {
      const have = books.find((b) => norm(b.title) === norm(title));
      if (have) { toast(`«${have.title}» уже в библиотеке`); return; }
      wishes = await wishApi("POST", "", { title });
      clearOmni(); paint();
      const w = wishes.find((x) => norm(x.title) === norm(title));
      toast("Сохранено: " + title);
      document.querySelector(`.card[data-id="${w?.id}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
    async function addByLink(url) {
      if (await startAdd({ text_url: url })) { clearOmni(); toast("Загружаю по ссылке, название возьму из книги"); renderLibrary(); }
    }
    async function addByFile(file) {
      if (await startAdd({ title: file.name.replace(/\.(fb2\.zip|zip|fb2|epub|txt|html?)$/i, ""), text_file: file })) { toast("Загружаю файл: " + file.name); renderLibrary(); }
    }
    ["dragenter", "dragover"].forEach((ev) => addEventListener(ev, (e) => { if (e.dataTransfer?.types.includes("Files")) { e.preventDefault(); document.body.classList.add("dropping"); } }));
    ["dragleave", "drop"].forEach((ev) => addEventListener(ev, (e) => { if (ev === "drop" || e.relatedTarget == null) document.body.classList.remove("dropping"); }));
    addEventListener("drop", (e) => { if (!e.dataTransfer?.files?.length || e.target.closest(".attach-form")) return; e.preventDefault(); [...e.dataTransfer.files].forEach(addByFile); });

    // ---- library search for a saved title: the result stays on the card until a text is loaded ----
    async function searchFor(w) {
      if (searching.get(w.id)?.busy) return;
      const t0 = Date.now();
      searching.set(w.id, { busy: true, t0 }); hidden.delete(w.id); paint();
      const ticker = setInterval(() => { const el = document.querySelector(`.card[data-id="${w.id}"] .sec`); if (el) el.textContent = Math.round((Date.now() - t0) / 1000); }, 1000);
      const ctrl = new AbortController(); const killer = setTimeout(() => ctrl.abort(), 90000);
      let res = null, state = null;
      try { res = await fetch("/api/search?q=" + q(w.title), { signal: ctrl.signal }).then((r) => r.json()); if (res.error) throw new Error(res.error); }
      catch (e) { res = null; state = { status: e.name === "AbortError" ? "библиотеки не ответили за полторы минуты, попробуй позже" : "поиск не удался: " + esc(e.message) }; }
      finally { clearInterval(ticker); clearTimeout(killer); }
      if (res) {
        const failed = (res.errors || []).map((x) => SOURCE[x.split(":")[0]] || x.split(":")[0]).filter((x, i, a) => a.indexOf(x) === i);
        const failedNote = failed.length ? ` · ${failed.join(", ")} не ответил${failed.length > 1 ? "и" : ""}` : "";
        const any = res.hits.length || res.author?.hits?.length;
        state = any ? (failed.length ? { status: esc(failedNote.slice(3)) } : null) : { status: esc(`не нашлось в ${SOURCES_LABEL}${failedNote}`) };
        wishes = await wishApi("PUT", "/" + w.id, { hits: res.hits, author_hits: res.author, searched: today() }).catch(() => wishes);
      }
      if (state) searching.set(w.id, state); else searching.delete(w.id);
      paint();
    }
    // load a picked edition: for a shell it becomes the book; for a ready book it replaces the text
    async function loadPick(ctx, h) {
      const fields = { title: h.title, author: h.author, translator: h.translator, year: h.year, narrator: h.narrator, text_url: h.urls.join("\n"), audio_url: h.audio || "" };
      if (ctx.slug) { fields.slug = ctx.slug; fields.replace = "1"; delete fields.title; delete fields.audio_url; }
      const s = await startAdd(fields);
      if (!s) return false;
      open = null;
      if (ctx.wish) { await saveHits(s, ctx.wish); searching.delete(ctx.wish.id); await wishApi("DELETE", "/" + ctx.wish.id).catch(() => {}); }
      toast(ctx.slug ? "Заменяю текст: " + h.title : "Загружаю: " + h.title);
      renderLibrary(); return true;
    }
    async function loadOwn(ctx, f) {
      const url = f.querySelector("[name=text_url]").value.trim(), file = f.querySelector("[name=text_file]").files[0];
      if (!url && !file) { toast("Нужна ссылка на текст или файл"); return; }
      if (url && !isUrl(url)) { toast("Ссылка должна начинаться с http(s)"); return; }
      const fields = { text_url: url, text_file: file, audio_url: f.querySelector("[name=audio_url]").value.trim() };
      if (ctx.slug) { fields.slug = ctx.slug; fields.replace = "1"; delete fields.audio_url; } else { fields.title = ctx.wish.title; fields.author = ctx.wish.author; }
      const s = await startAdd(fields);
      if (!s) return;
      open = null;
      if (ctx.wish) { await saveHits(s, ctx.wish); searching.delete(ctx.wish.id); await wishApi("DELETE", "/" + ctx.wish.id).catch(() => {}); }
      toast(ctx.slug ? "Заменяю текст" : "Загружаю: " + ctx.wish.title);
      renderLibrary();
    }
    const ctxOf = (card) => (card.dataset.id ? { wish: wishes.find((x) => x.id === card.dataset.id) } : { slug: card.dataset.slug });

    $("#library").addEventListener("click", async (e) => {
      const t = e.target, card = t.closest(".card");
      const add = t.closest(".card.add");
      if (add) { add.dataset.act === "link" ? addByLink(query) : addTitle(query); return; }
      const mv = t.closest(".shelf-btn");
      if (mv) { await setShelf(mv.dataset.slug, mv.dataset.shelf); renderLibrary(); return; }
      if (t.closest(".attach-btn")) { open = t.closest(".attach-btn").dataset.slug + ":audio"; paint(); return; }
      const tb = t.closest(".text-btn");
      if (tb) {
        open = tb.dataset.slug || tb.dataset.id;
        if (tb.dataset.slug) { const b = books.find((x) => x.slug === tb.dataset.slug); if (b && !b.hitsData) b.hitsData = await fetch("/api/hits/" + b.slug).then((r) => r.json()).catch(() => ({})); }
        paint(); return;
      }
      if (t.closest(".attach-cancel")) { open = null; paint(); return; }
      const da = t.closest(".del-ask");
      if (da) { confirmDel = da.dataset.slug; paint(); return; }
      if (t.closest(".del-cancel")) { confirmDel = null; paint(); return; }
      const db = t.closest(".del-book");
      if (db) {
        confirmDel = null;
        const r = await fetch("/api/books/" + db.dataset.slug, { method: "DELETE" }).then((x) => x.json()).catch((err) => ({ error: String(err) }));
        if (r.error) toast("Ошибка: " + r.error); else { delete jobs[db.dataset.slug]; renderLibrary(); }
        return;
      }
      const fb = t.closest(".find-btn");
      if (fb) { const w = wishes.find((x) => x.id === fb.dataset.id); if (w) searchFor(w); return; }
      const fo = t.closest(".fold-btn");
      if (fo) { hidden.has(fo.dataset.id) ? hidden.delete(fo.dataset.id) : hidden.add(fo.dataset.id); paint(); return; }
      const del = t.closest(".del-btn");
      if (del) { searching.delete(del.dataset.id); wishes = await wishApi("DELETE", "/" + del.dataset.id); paint(); return; }
      const pick = t.closest(".pick");
      if (pick && card) { loadPick(ctxOf(card), JSON.parse(pick.dataset.hit)); return; }
      if (t.closest(".text-go") && card) { loadOwn(ctxOf(card), t.closest(".text-panel")); return; }
      if (t.closest(".attach-go")) {
        const f = t.closest(".attach-form"); const urls = f.querySelector("[name=audio_url]").value.trim(), file = f.querySelector("[name=audio_file]").files[0];
        if (!urls && !file) { toast("Нужна ссылка на аудио или файл"); return; }
        const ok = await startAdd({ slug: f.dataset.slug, audio_url: urls, audio_file: file, narrator: f.querySelector("[name=narrator]").value, align: f.querySelector("[name=align]").checked ? "on" : "" });
        if (ok) { open = null; toast("Аудио загружается, книга появится с плеером"); renderLibrary(); }
      }
    });

    pollJobs().then(renderLibrary).then(() => {
      const wishParam = new URLSearchParams(location.search).get("wish");
      if (!wishParam) return;
      history.replaceState(null, "", location.pathname);
      addTitle(wishParam.replace(/\s+[-–—|].*$/, "").trim() || wishParam);
    });
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
