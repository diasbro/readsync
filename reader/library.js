/* readsync library page: one line finds and adds; cards open in place for text and audio. */
(() => {
  "use strict";
  if (slug) return;
  $("#library").hidden = false;
  fetchSettings().then(adoptSettings);
  const toast = (msg) => { const el = document.createElement("div"); el.className = "toast"; el.textContent = msg; document.body.appendChild(el); setTimeout(() => el.remove(), 3000); };
  const q = (x) => encodeURIComponent(x);
  const norm = (x) => (x || "").toLowerCase().replace(/ё/g, "е").replace(/[^\p{L}\p{N}]+/gu, " ").trim();
  const isUrl = (x) => /^https?:\/\//i.test(x);
  const api = (method, path, body) => fetch(path, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined }).then((r) => r.json());

  // ---- preferences popover: theme and interface font, the two settings that shape every page ----
  const prefs = $("#lib-prefs");
  function syncPrefsUI() {
    document.querySelectorAll("#lib-theme button").forEach((b) => b.classList.toggle("on", b.dataset.v === settings.theme));
    $("#lib-ui").value = settings.ui;
  }
  onSettingsSynced = syncPrefsUI;
  syncPrefsUI();
  $("#lib-settings").onclick = (e) => { e.stopPropagation(); prefs.hidden = !prefs.hidden; };
  addEventListener("click", (e) => { if (!e.target.closest("#lib-prefs, #lib-settings")) prefs.hidden = true; });
  $("#lib-theme").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; settings.theme = b.dataset.v; applySettings(); persistSettings(); syncPrefsUI(); });
  $("#lib-ui").addEventListener("input", (e) => { settings.ui = e.target.value; applySettings(); persistSettings(); syncPrefsUI(); });
  $("#bookmarklet").href = "javascript:(function(){window.open('" + location.origin + "/?wish='+encodeURIComponent(document.title),'_blank')})()";
  $("#wish-url").textContent = location.origin + "/?wish=Название";

  // ---- header line: the sentence you stopped at in the current book, the word highlight walking along it ----
  // looks: "audio" walks the highlight; "pages" is a still line with a page marker; "random" quotes a finished book
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

  // ---- data ----
  // Shelves: a book with more than 10 minutes of reading is "reading now" unless moved by hand; within a
  // shelf the most recent activity (opened, or added) comes first. A title saved without text is a "shell"
  // card that waits in the catalog. Every card opens in place (⚙) for its text and audio.
  let books = [], wishes = [], jobs = {}, open = null, confirmDel = null;  // open: id of the card opened in place; confirmDel: awaiting "delete?"
  const READING_SEC = 600;
  const SOURCE = { "fantasy-worlds": "fantasy-worlds", flibusta: "Flibusta", coollib: "Coollib" };
  const SOURCES_LABEL = Object.values(SOURCE).join(", ");
  const LOADABLE = new Set(["fb2", "epub", "txt", "html"]);
  const searching = new Map();  // card id -> {busy, t0} | {status}: this session's search state
  const hitsCache = {};  // slug -> {hits, author_hits} from /api/hits, loaded when a book card opens
  const idOf = (x) => x.slug || x.id;
  const isShell = (x) => !x.slug;
  const shelfOf = (b) => (b.state.shelf ? b.state.shelf : b.state.finished ? "library" : b.state.seconds > READING_SEC ? "reading" : "library");
  const sourceOf = (url) => { const h = (url || "").split("|")[0].trim(); for (const k in SOURCE) if (h.includes(k.replace("-", "-"))) return SOURCE[k]; return h && !isUrl(h) ? "файл" : h ? new URL(h).hostname.replace(/^www\./, "") : ""; };
  const kb = (n) => (n == null ? "" : n >= 1000 ? (n / 1024).toFixed(1).replace(".", ",") + " МБ" : n + " КБ");
  const facts = (b) => [b.author, b.translator ? "пер. " + b.translator : null, b.year, b.has_audio && b.narrator ? "читает " + b.narrator : null].filter(Boolean).join(" · ");

  // ---- search result rows: one row per edition, exactly as the catalog describes it ----
  const hitMeta = (h) => [h.author, h.translator ? "пер. " + h.translator : "", h.year, h.parts_label, h.parts[0].kind === "html" ? "страница" : h.parts[0].kind, kb(h.size_kb), SOURCE[h.source] || h.source, h.lang && h.lang !== "ru" ? h.lang : ""].filter(Boolean).join(" · ");
  const hitData = (h) => esc(JSON.stringify({ title: h.title, author: h.author, translator: h.translator, year: h.year, narrator: h.narrator, urls: h.parts.map((p) => p.url), audio: h.audio_url }));
  const SHOW = 5;
  function rowHtml(h, current) {
    const now = current === h.parts.map((p) => p.url).join(" | ");
    const right = now ? '<span class="now">сейчас в книге</span>' : LOADABLE.has(h.parts[0].kind) ? `<button class="btn sm" data-act="pick" data-hit='${hitData(h)}'>Загрузить</button>` : `<span class="cm">${esc(h.parts[0].kind)}, не открою</span>`;
    return `<div class="cand${now ? " cur" : ""}"><div class="ct">${esc(h.title)}<div class="cm">${esc(hitMeta(h))}</div></div>${right}</div>`;
  }
  function rowsHtml(rows, current) {
    // same title and author from one source: several editions fold into one line
    const groups = [];
    rows.forEach((h) => { const key = h.source + "|" + norm(h.title) + "|" + norm(h.author); const g = groups.find((x) => x.key === key); if (g) g.rows.push(h); else groups.push({ key, rows: [h] }); });
    const one = (g) => (g.rows.length === 1 ? rowHtml(g.rows[0], current)
      : `<details class="eds"${g.rows.some((h) => current === h.parts.map((p) => p.url).join(" | ")) ? " open" : ""}><summary><span class="ct">${esc(g.rows[0].title)}<div class="cm">${esc([g.rows[0].author, g.rows.length + " изданий", SOURCE[g.rows[0].source]].filter(Boolean).join(" · "))}</div></span><span class="link-btn">издания</span></summary><div class="cands">${g.rows.map((h) => rowHtml(h, current)).join("")}</div></details>`);
    const head = groups.slice(0, SHOW).map(one).join("");
    const rest = groups.slice(SHOW);
    return head + (rest.length ? `<details class="more"><summary>ещё ${rest.length}</summary><div class="cands">${rest.map(one).join("")}</div></details>` : "");
  }
  function resultsHtml(found, current) {
    const hits = found?.hits, author = found?.author_hits;
    if (!hits?.length && !author?.hits?.length) return "";
    return `<div class="cands">${hits?.length ? rowsHtml(hits, current) : ""}${author?.hits?.length ? `<div class="hd">у автора ${esc(author.name)}</div>${rowsHtml(author.hits, current)}` : ""}</div>`;
  }

  // ---- the card: collapsed, or opened in place with its sections ----
  const icon = (act, glyph, title) => `<button class="ic" data-act="${act}" title="${title}">${glyph}</button>`;
  function actsHtml(x) {
    if (confirmDel === idOf(x)) return `<div class="acts confirm">${isShell(x) ? "убрать" : "удалить"}${x.has_audio ? " с аудио" : ""}? <button data-act="delYes">да</button> <button data-act="delNo">нет</button></div>`;
    const first = isShell(x) ? icon("find", "⌕", "Найти текст") : !x.ready ? "" : shelfOf(x) === "reading" ? icon("pause", "⏸", "Отложить") : icon("read", "▶", "Читать");
    return `<div class="acts">${first}${x.building ? "" : icon("gear", "⚙", "Текст и аудио")}${x.building ? "" : icon("del", "✕", isShell(x) ? "Убрать" : "Удалить")}</div>`;
  }
  function statusHtml(x) {
    const s = searching.get(idOf(x)) || {};
    if (s.busy) return `<div class="m status"><span class="spin"></span>ищу в ${SOURCES_LABEL}… <span class="sec">${Math.round((Date.now() - s.t0) / 1000)}</span> с</div>`;
    if (s.status) return `<div class="m status warn">${s.status}</div>`;
    const found = isShell(x) ? x : hitsCache[x.slug];
    if (isShell(x) && x.searched && !found?.hits?.length && !found?.author_hits?.hits?.length) return `<div class="m status warn">не нашлось в ${SOURCES_LABEL}</div>`;
    return "";
  }
  function textSection(x) {
    const found = isShell(x) ? x : hitsCache[x.slug] || {};
    const any = !!(found.hits?.length || found.author_hits?.hits?.length);
    return `<div class="sec"><h5>Текст${x.text_source ? `<span class="now">· ${esc(sourceOf(x.text_source))}</span>` : ""}<button class="link-btn" data-act="find">${any || x.searched ? "искать издания снова" : "искать издания"}</button></h5>
      ${statusHtml(x)}${resultsHtml(found, x.text_source)}
      <div class="own"><input name="text_url" placeholder="Своя ссылка на текст: страница книги, fb2, epub, txt"><label class="file">или файл<input type="file" name="text_file" accept=".fb2,.zip,.epub,.txt,.html,.htm" hidden></label><button class="btn sm" data-act="own">Загрузить</button></div></div>`;
  }
  function audioSection(b) {
    const now = b.has_audio ? [b.narrator ? "читает " + b.narrator : "", sourceOf(b.audio_source), b.timing_source === "mms" ? "точное выравнивание" : "разметка по субтитрам"].filter(Boolean).join(" · ") : "нет";
    const align = b.has_audio && b.timing_source !== "mms" ? `<button class="link-btn" data-act="align">выровнять точно (долго)</button>` : "";
    return `<div class="sec"><h5>Аудио<span class="now">· ${esc(now)}</span>${align}</h5>
      <div class="own"><input name="audio_url" placeholder="${b.has_audio ? "Заменить: " : ""}ссылка на YouTube, части по одной через пробел"><label class="file">или файл<input type="file" name="audio_file" accept="audio/*,.m4b,.m4a,.mp3" hidden></label><input name="narrator" class="narr" placeholder="Чтец"><button class="btn sm" data-act="audioGo">Загрузить</button></div>
      ${b.has_audio ? "" : `<div class="m">Голос выбери сам: <a target="_blank" rel="noopener" href="https://www.youtube.com/results?search_query=${q((b.title || "") + " аудиокнига")}">YouTube</a></div>`}</div>`;
  }
  function openHtml(x) {
    return `<div class="card open${isShell(x) ? " shell" : ""}" data-key="${esc(idOf(x))}"><div class="body">
      <div class="head"><div class="t">${esc(x.title || x.slug)}${isShell(x) ? '<span class="tag">без текста</span>' : ""}</div><button class="ic" data-act="close" title="Свернуть (Esc)">✕</button></div>
      ${facts(x) ? `<div class="m">${esc(facts(x))}</div>` : ""}
      ${textSection(x)}${isShell(x) ? "" : audioSection(x)}</div></div>`;
  }
  function cardHtml(b) {
    if (open === b.slug) return openHtml(b);
    const st = b.state, job = jobs[b.slug], failed = !!(job && !job.running && job.exit !== 0);
    const pos = st.pos || 0, dur = st.duration || store.get("rs:dur:" + b.slug, 0);
    const pct = st.finished ? 100 : b.has_audio ? (dur ? Math.round((pos / dur) * 100) : 0) : (st.sentPct || 0);
    const meta = [facts(b), !b.ready ? (b.building ? "загружается…" : failed ? null : "не загрузилась до конца") : b.building ? "заменяю…" : null].filter(Boolean).join(" · ");
    const fail = failed ? `<div class="m status warn">не загрузилось: ${esc(job.log[job.log.length - 1] || "код " + job.exit)}</div>` : "";
    const frag = b.fragment_note ? `<div class="m status warn">в конце текста «${esc(b.fragment_note)}»</div>` : "";
    const tags = b.ready ? '<span class="tag">текст</span>' + (b.has_audio ? '<span class="tag">аудио</span>' : "") + (st.finished ? '<span class="tag done">прочитано</span>' : "") : "";
    const where = !b.ready ? "" : st.finished ? "прочитано целиком" : b.has_audio ? (pct ? `прочитано ${pct}% · ${fmt(pos)}` : "не начато") : (st.sent ? `прочитано ${pct}%` : "не начато");
    const cover = b.cover ? `<img class="cover" src="/books/${esc(b.slug)}/${esc(b.cover)}" alt="">` : `<div class="cover empty">${esc((b.title || b.slug).slice(0, 1))}</div>`;
    const href = b.ready ? "?book=" + esc(b.slug) : "#";
    return `<div class="card" data-key="${esc(b.slug)}"><a class="cover-link" href="${href}">${cover}</a>
      <div class="body"><a href="${href}" class="tlink"><div class="t">${esc(b.title || b.slug)}${tags}</div></a><div class="m${meta ? "" : " empty"}">${esc(meta)}</div>${fail}${frag}
      <div class="bar${pct ? "" : " empty"}"><i style="width:${pct}%"></i></div><div class="m${pct ? "" : " empty"}">${where}</div></div>${actsHtml(b)}</div>`;
  }
  function shellHtml(w) {
    if (open === w.id) return openHtml(w);
    return `<div class="card shell" data-key="${esc(w.id)}"><div class="cover empty">${esc(w.title.slice(0, 1))}</div>
      <div class="body"><div class="t">${esc(w.title)}<span class="tag">без текста</span></div>${w.author ? `<div class="m">${esc(w.author)}</div>` : ""}${statusHtml(w)}</div>${actsHtml(w)}</div>`;
  }

  // ---- the one line: filters the library as you type; what is not there can be saved from the same line ----
  const omni = $("#omni-input");
  let query = "";
  const matches = (x) => !query || norm(x.title + " " + (x.author || "") + " " + (x.translator || "")).includes(norm(query));
  function addRowHtml(any) {
    if (isUrl(query)) return `<div class="card add" data-act="link"><div class="body"><div class="t"><b>＋</b>Загрузить по ссылке</div><div class="m">↵ · название возьму из книги</div></div></div>`;
    return `<div class="card add" data-act="save"><div class="body"><div class="t"><b>＋</b>Сохранить «${esc(query)}»${any ? " как новую книгу" : ""}</div><div class="m">${any ? "⌘↵" : "↵"} · без текста; найти его или добавить свой можно потом</div></div></div>`;
  }
  // paint() lays out what is already loaded; renderLibrary() fetches first
  function paint() {
    const byActivity = (a, b) => (b.at || 0) - (a.at || 0) || (a.title || "").localeCompare(b.title || "", "ru");
    const entry = (b) => ({ at: b.state.opened || b.added || 0, title: b.title, html: cardHtml(b) });
    const reading = books.filter((b) => shelfOf(b) === "reading" && matches(b)).map(entry).sort(byActivity);
    const rest = [...books.filter((b) => shelfOf(b) !== "reading" && matches(b)).map(entry),
      ...wishes.filter(matches).map((w) => ({ at: +w.id.slice(1) || 0, title: w.title, html: shellHtml(w) }))].sort(byActivity);
    const any = reading.length + rest.length > 0;
    $("#lib-title").textContent = query ? (any ? `Найдено ${reading.length + rest.length}` : "В библиотеке нет") : reading.length ? "Остальные" : "Библиотека";
    $("#reading-section").hidden = !reading.length;
    $("#reading-list").innerHTML = reading.map((x) => x.html).join("");
    $("#library-list").innerHTML = rest.map((x) => x.html).join("") + (query ? addRowHtml(any) : !any ? '<p class="muted small">Пока пусто. Напиши название книги в строке выше, вставь ссылку или перетащи файл.</p>' : "");
  }
  async function renderLibrary() {
    [books, wishes] = await Promise.all([fetch("/api/books").then((r) => r.json()), api("GET", "/api/wishlist").catch(() => wishes)]);
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
  addEventListener("keydown", (e) => {
    if (e.key === "Escape") { prefs.hidden = true; if (open) { open = null; paint(); } }
    if (e.key === "/" && !(e.target instanceof Element && e.target.matches("input, textarea, select"))) { e.preventDefault(); omni.focus(); }
  });
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
  const saveHits = (slug, found) => { hitsCache[slug] = found; return api("PUT", `/api/hits/${slug}`, { hits: found.hits || [], author_hits: found.author_hits || null }).catch(() => {}); };

  // ---- adding: a title becomes a shell card; a link or a dropped file loads right away ----
  async function addTitle(title) {
    const have = books.find((b) => norm(b.title) === norm(title));
    if (have) { toast(`«${have.title}» уже в библиотеке`); return; }
    wishes = await api("POST", "/api/wishlist", { title });
    clearOmni(); paint();
    const w = wishes.find((x) => norm(x.title) === norm(title));
    toast("Сохранено: " + title);
    document.querySelector(`.card[data-key="${w?.id}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
  async function addByLink(url) {
    if (await startAdd({ text_url: url })) { clearOmni(); toast("Загружаю по ссылке, название возьму из книги"); renderLibrary(); }
  }
  async function addByFile(file) {
    if (await startAdd({ title: file.name.replace(/\.(fb2\.zip|zip|fb2|epub|txt|html?)$/i, ""), text_file: file })) { toast("Загружаю файл: " + file.name); renderLibrary(); }
  }
  ["dragenter", "dragover"].forEach((ev) => addEventListener(ev, (e) => { if (e.dataTransfer?.types.includes("Files")) { e.preventDefault(); document.body.classList.add("dropping"); } }));
  ["dragleave", "drop"].forEach((ev) => addEventListener(ev, (e) => { if (ev === "drop" || e.relatedTarget == null) document.body.classList.remove("dropping"); }));
  addEventListener("drop", (e) => { if (!e.dataTransfer?.files?.length || e.target.closest(".card.open")) return; e.preventDefault(); [...e.dataTransfer.files].forEach(addByFile); });

  // ---- library search for a card: the result stays with it (wishlist item or hits.json) ----
  async function searchFor(x) {
    const id = idOf(x);
    if (searching.get(id)?.busy) return;
    const t0 = Date.now();
    searching.set(id, { busy: true, t0 }); paint();
    const ticker = setInterval(() => { const el = document.querySelector(`.card[data-key="${id}"] .sec .sec, .card[data-key="${id}"] span.sec`); if (el) el.textContent = Math.round((Date.now() - t0) / 1000); }, 1000);
    const ctrl = new AbortController(); const killer = setTimeout(() => ctrl.abort(), 90000);
    let res = null, state = null;
    try { res = await fetch("/api/search?q=" + q(x.title), { signal: ctrl.signal }).then((r) => r.json()); if (res.error) throw new Error(res.error); }
    catch (e) { res = null; state = { status: e.name === "AbortError" ? "библиотеки не ответили за полторы минуты, попробуй позже" : "поиск не удался: " + esc(e.message) }; }
    finally { clearInterval(ticker); clearTimeout(killer); }
    if (res) {
      const failed = (res.errors || []).map((e) => SOURCE[e.split(":")[0]] || e.split(":")[0]).filter((v, i, a) => a.indexOf(v) === i);
      const failedNote = failed.length ? `${failed.join(", ")} не ответил${failed.length > 1 ? "и" : ""}` : "";
      const any = res.hits.length || res.author?.hits?.length;
      state = any ? (failed.length ? { status: esc(failedNote) } : null) : { status: esc(`не нашлось в ${SOURCES_LABEL}` + (failedNote ? " · " + failedNote : "")) };
      const found = { hits: res.hits, author_hits: res.author };
      if (isShell(x)) wishes = await api("PUT", "/api/wishlist/" + x.id, { ...found, searched: today() }).catch(() => wishes);
      else await saveHits(x.slug, found);
    }
    if (state) searching.set(id, state); else searching.delete(id);
    paint();
  }
  // load a picked edition or an own link/file: a shell becomes the book, a ready book gets its text replaced
  async function loadText(x, fields, label) {
    if (isShell(x)) Object.assign(fields, { title: x.title, author: fields.author || x.author });
    else Object.assign(fields, { slug: x.slug, replace: "1" });
    const s = await startAdd(fields);
    if (!s) return;
    open = null;
    if (isShell(x)) { await saveHits(s, { hits: x.hits, author_hits: x.author_hits }); searching.delete(x.id); await api("DELETE", "/api/wishlist/" + x.id).catch(() => {}); }
    toast((isShell(x) ? "Загружаю: " : "Заменяю текст: ") + label);
    renderLibrary();
  }
  const entryOf = (card) => books.find((b) => b.slug === card.dataset.key) || wishes.find((w) => w.id === card.dataset.key);

  // ---- actions: every button carries data-act; the card it sits in gives the book ----
  const ACTIONS = {
    link: () => addByLink(query),
    save: () => addTitle(query),
    read: async (x) => { await api("PUT", `/api/state/${x.slug}`, { shelf: "reading", shelfAt: Date.now() }); renderLibrary(); },
    pause: async (x) => { await api("PUT", `/api/state/${x.slug}`, { shelf: "library", shelfAt: Date.now() }); renderLibrary(); },
    gear: async (x) => {
      open = open === idOf(x) ? null : idOf(x);
      if (open && !isShell(x) && !hitsCache[x.slug]) hitsCache[x.slug] = await fetch("/api/hits/" + x.slug).then((r) => r.json()).catch(() => ({}));
      paint();
    },
    close: () => { open = null; paint(); },
    del: (x) => { confirmDel = idOf(x); paint(); },
    delNo: () => { confirmDel = null; paint(); },
    delYes: async (x) => {
      confirmDel = null;
      if (isShell(x)) { searching.delete(x.id); wishes = await api("DELETE", "/api/wishlist/" + x.id); paint(); return; }
      const r = await api("DELETE", "/api/books/" + x.slug).catch((err) => ({ error: String(err) }));
      if (r.error) toast("Ошибка: " + r.error); else { delete jobs[x.slug]; renderLibrary(); }
    },
    find: (x) => { if (open !== idOf(x)) { open = idOf(x); paint(); } searchFor(x); },
    pick: (x, btn) => { const h = JSON.parse(btn.dataset.hit); loadText(x, { author: h.author, translator: h.translator, year: h.year, narrator: h.narrator, text_url: h.urls.join("\n"), audio_url: isShell(x) ? h.audio || "" : "" }, h.title); },
    own: (x, btn) => {
      const row = btn.closest(".own"), url = row.querySelector("[name=text_url]").value.trim(), file = row.querySelector("[name=text_file]").files[0];
      if (!url && !file) { toast("Нужна ссылка на текст или файл"); return; }
      if (url && !isUrl(url)) { toast("Ссылка должна начинаться с http(s)"); return; }
      loadText(x, { text_url: url, text_file: file }, file ? file.name : x.title);
    },
    audioGo: async (x, btn) => {
      const row = btn.closest(".own"), urls = row.querySelector("[name=audio_url]").value.trim().split(/\s+/).filter(Boolean).join("\n"), file = row.querySelector("[name=audio_file]").files[0];
      if (!urls && !file) { toast("Нужна ссылка на аудио или файл"); return; }
      const ok = await startAdd({ slug: x.slug, audio_url: urls, audio_file: file, narrator: row.querySelector("[name=narrator]").value.trim() });
      if (ok) { open = null; toast("Аудио загружается, книга появится с плеером"); renderLibrary(); }
    },
    align: async (x) => {
      const r = await api("POST", "/api/align/" + x.slug).catch((err) => ({ error: String(err) }));
      if (r.error) toast("Ошибка: " + r.error); else { open = null; toast("Точное выравнивание запущено, это долго"); pollJobs(); renderLibrary(); }
    },
  };
  $("#library").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const card = btn.closest(".card");
    const x = card && !card.classList.contains("add") ? entryOf(card) : null;
    if (x || card?.classList.contains("add")) ACTIONS[btn.dataset.act]?.(x, btn);
  });
  // "или файл": the label shows the chosen name
  $("#library").addEventListener("change", (e) => { if (e.target.type === "file") { const l = e.target.closest("label"); if (l) l.firstChild.textContent = e.target.files[0] ? e.target.files[0].name : "или файл"; } });

  pollJobs().then(renderLibrary).then(() => {
    const wishParam = new URLSearchParams(location.search).get("wish");
    if (!wishParam) return;
    history.replaceState(null, "", location.pathname);
    addTitle(wishParam.replace(/\s+[-–—|].*$/, "").trim() || wishParam);
  });
})();
