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
    $("#lib-audio-search").checked = settings.audioSearch !== false;
  }
  onSettingsSynced = syncPrefsUI;
  syncPrefsUI();
  $("#lib-settings").onclick = (e) => { e.stopPropagation(); prefs.hidden = !prefs.hidden; };
  addEventListener("click", (e) => { if (!e.target.closest("#lib-prefs, #lib-settings")) prefs.hidden = true; });
  $("#lib-theme").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; settings.theme = b.dataset.v; applySettings(); persistSettings(); syncPrefsUI(); });
  $("#lib-ui").addEventListener("input", (e) => { settings.ui = e.target.value; applySettings(); persistSettings(); syncPrefsUI(); });
  $("#lib-audio-search").addEventListener("change", (e) => { settings.audioSearch = e.target.checked; applySettings(); persistSettings(); paint(); });
  $("#bookmarklet").href = "javascript:(function(){window.open('" + location.origin + "/?wish='+encodeURIComponent(document.title),'_blank')})()";

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
  // nothing to quote yet: a single line of "words" with the highlight walking along it, no text
  function runIdle() {
    const line = $("#demo-line"), demo = $(".demo");
    clearInterval(demoTimer);
    line.innerHTML = [34, 62, 28, 88, 50, 40, 72, 46].map((w) => `<i style="width:${w}px"></i>`).join("");
    line.classList.remove("link"); line.onclick = null;
    demo.dataset.look = "idle";
    const ws = line.querySelectorAll("i");
    let i = 0; ws[0].classList.add("cur");
    if (!matchMedia("(prefers-reduced-motion: reduce)").matches) {
      demoTimer = setInterval(() => { if (document.hidden) return; ws[i].classList.remove("cur"); i = (i + 1) % ws.length; ws[i].classList.add("cur"); }, 520);
    }
  }
  runIdle();
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
    runIdle();
  }

  // ---- data ----
  // Shelves: a book with more than 10 minutes of reading is "reading now" unless moved by hand; within a
  // shelf the most recent activity (opened, or added) comes first. A title saved without text is a "shell"
  // card that waits in the catalog. Every card opens in place (⚙) for its text and audio.
  let books = [], wishes = [], jobs = {}, open = null, confirmDel = null;  // open: id of the card opened in place; confirmDel: awaiting "delete?"
  const READING_SEC = 600;
  const SOURCE = { "fantasy-worlds": "fantasy-worlds", flibusta: "Flibusta", coollib: "Coollib", "standard-ebooks": "Standard Ebooks",
    gutenberg: "Gutenberg", wikisource: "Wikisource", bia: "Buddhadasa Archives" };
  const SOURCES_LABEL = Object.values(SOURCE).join(", ");
  const LOADABLE = new Set(["fb2", "epub", "pdf", "txt", "html"]);
  const searching = new Map();  // card id -> {busy, t0} | {status}: this session's search state
  const hitsCache = {};  // slug -> {hits, author_hits} from /api/hits, loaded when a book card opens
  const idOf = (x) => x.slug || x.id;
  const isShell = (x) => !x.slug;
  const shelfOf = (b) => (b.state.shelf ? b.state.shelf : b.state.finished ? "library" : b.state.seconds > READING_SEC ? "reading" : "library");
  const REF_SOURCE = { knigavuhe: "knigavuhe", yt: "YouTube", ia: "archive.org" };  // a recording loaded by its ref
  const sourceOf = (url) => { const h = (url || "").split("|")[0].trim(); const ref = /^(knigavuhe|yt|ia):/.exec(h); if (ref) return REF_SOURCE[ref[1]]; for (const k in SOURCE) if (h.includes(k.replace("-", "-"))) return SOURCE[k]; return h && !isUrl(h) ? "файл" : h ? new URL(h).hostname.replace(/^www\./, "") : ""; };
  const kb = (n) => (n == null ? "" : n >= 1000 ? (n / 1024).toFixed(1).replace(".", ",") + " МБ" : n + " КБ");
  const facts = (b) => [b.author, b.translator ? "пер. " + b.translator : null, b.year, b.has_audio ? (b.narrator ? "читает " + b.narrator : "с аудио") : null].filter(Boolean).join(" · ");

  // ---- search result rows: one row per edition, exactly as the catalog describes it ----
  const kindOf = (h) => h.parts?.[0]?.kind || "";  // a row saved by an older version may have no parts
  const hitMeta = (h) => [h.author, h.translator ? "пер. " + h.translator : "", h.year, h.parts_label, kindOf(h) === "html" ? "страница" : kindOf(h), kb(h.size_kb), SOURCE[h.source] || h.source, h.lang && h.lang !== "ru" ? h.lang : ""].filter(Boolean).join(" · ");
  const hitData = (h) => esc(JSON.stringify({ title: h.title, author: h.author, translator: h.translator, year: h.year, narrator: h.narrator, urls: h.parts.map((p) => p.url), audio: h.audio_url }));
  const SHOW = 5;
  const openable = (h) => LOADABLE.has(kindOf(h));  // a row nothing can be done with is not a choice
  function rowHtml(h, current) {
    const now = current === h.parts.map((p) => p.url).join(" | ");
    const right = `<button class="btn sm" data-act="pick" data-hit='${hitData(h)}'${now ? ' title="Взять текст и название с сайта заново"' : ""}>${now ? "Обновить" : "Загрузить"}</button>`;
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
    // rows saved before the reader knew a format stay in hits.json: they are filtered out here too
    const raw = found?.hits || [], author = found?.author_hits, rawByAuthor = author?.hits || [];
    const hits = raw.filter(openable), byAuthor = rawByAuthor.filter(openable);
    if (!hits.length && !byAuthor.length) {
      // the book is on the shelf, just not in a file the pipeline can read: that is worth saying,
      // an empty card reads as «нет такой книги»
      const shut = (found?.unopenable || 0) + (raw.length - hits.length) + (rawByAuthor.length - byAuthor.length);
      return shut ? '<div class="m status warn">нашлось, но только в форматах, которые не открыть</div>' : "";
    }
    return `<div class="cands">${hits.length ? rowsHtml(hits, current) : ""}${byAuthor.length ? `<div class="hd">у автора ${esc(author.name)}</div>${rowsHtml(byAuthor, current)}` : ""}</div>`;
  }

  // ---- recordings: found by title, listened to before loading, loaded by their ref (settings.audioSearch) ----
  // Nothing of a listen reaches the disk: the server streams it through and the browser is told not to cache.
  const AUDIO_SOURCE = { knigavuhe: "knigavuhe", youtube: "YouTube", archive: "archive.org" };
  const AUDIO_LABEL = Object.values(AUDIO_SOURCE).join(", ");
  const OURS_PER_H = 21.6e6;  // what the pipeline keeps: AAC-LC mono 48 kbit/s
  const LISTEN_FROM = 120;  // the first part opens with «Аудиокнига… читает…»: the voice starts past it
  const audioFinding = new Map();  // slug -> {busy, t0, ctrl, query} | {query, hits, note | status}
  const audioHits = new Map();  // ref -> recording, for the buttons of the rows on screen
  let audioConfirm = null;  // ref of the row asking "load?"
  let listening = null;  // {ref, parts, part, from}: the recording in the shared <audio id="listen">
  const audioOn = () => settings.audioSearch !== false && !bridge;  // the phone has no server to search with
  const plural = (n, one, few, many) => { const m = n % 100, k = n % 10; return n + " " + (m > 10 && m < 15 ? many : k === 1 ? one : k > 1 && k < 5 ? few : many); };
  const hm = (s) => { const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60); return h ? `${h} ч${m ? ` ${m} мин` : ""}` : `${m || 1} мин`; };
  const bytes = (n) => (n >= 1e9 ? (n / 1e9).toFixed(1).replace(".", ",") + " ГБ" : Math.max(1, Math.round(n / 1e6)) + " МБ");
  const oursOf = (h) => (h.duration_s ? (h.duration_s / 3600) * OURS_PER_H : 0);
  const sizeOf = (h) => (oursOf(h) ? (h.size_bytes ? `~${bytes(h.size_bytes)} → ` : "") + `~${bytes(oursOf(h))} у нас` : "");
  const audioMeta = (h) => [h.author, h.narrator ? "читает " + h.narrator : "", h.duration_s ? hm(h.duration_s) : "",
    h.parts > 1 ? plural(h.parts, "часть", "части", "частей") : "", sizeOf(h), AUDIO_SOURCE[h.source] || h.source].filter(Boolean).join(" · ");
  function audioRowHtml(h, b) {
    audioHits.set(h.ref, h);
    const r = esc(h.ref), ours = oursOf(h);
    const right = audioConfirm === h.ref
      ? `<div class="acts confirm">${b.has_audio ? "заменить" : "загрузить"}${ours ? " ~" + bytes(ours) : ""}${h.captions ? "" : " и распознать речь"}? <button data-act="audioYes" data-ref="${r}">да</button> <button data-act="audioNo">нет</button></div>`
      : `${icon("listen", "▶", "Послушать")}<button class="btn sm" data-act="audioPick">${b.has_audio ? "Заменить" : "Загрузить"}</button>`;
    return `<div class="cand" data-ref="${r}"><div class="ct">${esc(h.title)}<div class="cm">${esc(audioMeta(h))}</div>
      <div class="cm">${h.captions ? "с субтитрами — синхронизация быстрая" : "без субтитров — распознавание речи, долго"}</div></div>${right}</div>`;
  }
  function audioRowsHtml(hits, b) {
    // one book read by several narrators folds into one line, as editions do
    const groups = [];
    hits.forEach((h) => { const key = norm(h.title) + "|" + norm(h.author); const g = groups.find((x) => x.key === key); if (g) g.rows.push(h); else groups.push({ key, rows: [h] }); });
    const asking = (g) => g.rows.some((h) => h.ref === audioConfirm);
    const one = (g) => (g.rows.length === 1 ? audioRowHtml(g.rows[0], b)
      : `<details class="eds"${asking(g) ? " open" : ""}><summary><span class="ct">${esc(g.rows[0].title)}<div class="cm">${esc([g.rows[0].author, plural(g.rows.length, "озвучка", "озвучки", "озвучек")].filter(Boolean).join(" · "))}</div></span><span class="link-btn">озвучки</span></summary><div class="cands">${g.rows.map((h) => audioRowHtml(h, b)).join("")}</div></details>`);
    const rest = groups.slice(SHOW);
    return `<div class="cands">${groups.slice(0, SHOW).map(one).join("")}${rest.length ? `<details class="more"${rest.some(asking) ? " open" : ""}><summary>ещё ${rest.length}</summary><div class="cands">${rest.map(one).join("")}</div></details>` : ""}</div>`;
  }
  function audioFindHtml(b) {
    const s = audioOn() && audioFinding.get(b.slug);
    if (!s) return "";
    const status = s.busy ? `<div class="m status"><span class="spin"></span>ищу в ${AUDIO_LABEL}… <span class="asecs">${Math.round((Date.now() - s.t0) / 1000)}</span> с<button class="link-btn" data-act="stopAudioFind">отменить</button></div>`
      : s.status ? `<div class="m status warn">${esc(s.status)}</div>` : s.note ? `<div class="m">${esc(s.note)}</div>` : "";
    return `<div class="own afind"><input class="afind-input" value="${esc(s.query)}" spellcheck="false" aria-label="Что искать в ${AUDIO_LABEL}" placeholder="Название, автор, чтец"><button class="btn sm" data-act="findAudioGo">Искать</button></div>
      ${status}${s.hits?.length ? audioRowsHtml(s.hits, b) : ""}`;
  }
  async function findAudio(b, query) {
    query = (query || "").trim();
    if (!query) return;
    audioFinding.get(b.slug)?.ctrl?.abort();  // asked again: the answer to the old words is not wanted
    const t0 = Date.now(), ctrl = new AbortController(), s0 = { busy: true, t0, ctrl, query };
    audioFinding.set(b.slug, s0); audioConfirm = null; paint();
    const ticker = setInterval(() => { const el = document.querySelector(`.card[data-key="${CSS.escape(b.slug)}"] .asecs`); if (el) el.textContent = Math.round((Date.now() - t0) / 1000); }, 1000);
    const killer = setTimeout(() => ctrl.abort(), 60000);  // the server gives all sources 30 s together
    let next;
    try {
      const res = await fetch("/api/audio/search?q=" + q(query), { signal: ctrl.signal }).then((r) => r.json());
      if (res.error) throw new Error(res.error);
      const failed = (res.errors || []).map((e) => AUDIO_SOURCE[e.split(":")[0]] || e.split(":")[0]);
      const failedNote = failed.length ? `${failed.join(", ")} не ответил${failed.length > 1 ? "и" : ""}` : "";
      next = { query, hits: res.hits || [] };
      if (!next.hits.length) next.status = failedNote ? failedNote + ", попробуй ещё раз" : "озвучек не нашлось";
      else if (failedNote) next.note = failedNote;
    } catch (e) {
      next = { query, status: e.name === "AbortError" ? "источники не ответили, попробуй позже" : "поиск не удался: " + e.message };
    } finally { clearInterval(ticker); clearTimeout(killer); }
    if (audioFinding.get(b.slug) !== s0) return;  // called off, or asked again meanwhile
    audioFinding.set(b.slug, next); paint();
  }

  // listening: one shared <audio>, one recording at a time. Stopping detaches the source, which closes the
  // connection: a stopped listen fetches nothing more
  const listenEl = $("#listen");
  const player = () => document.querySelector(".lplay");
  const playerSays = (text) => { const el = player()?.querySelector(".lst"); if (el) el.textContent = text; };
  function stopListen() {
    if (!listening && !listenEl.getAttribute("src")) return;
    listening = null;
    listenEl.pause(); listenEl.removeAttribute("src"); listenEl.load();
    document.querySelectorAll(".lplay").forEach((el) => el.remove());
    document.querySelectorAll(".cand.on").forEach((row) => { row.classList.remove("on"); const btn = row.querySelector('[data-act="listen"]'); if (btn) { btn.textContent = "▶"; btn.title = "Послушать"; } });
  }
  function playPart(part, from) {
    Object.assign(listening, { part, from });
    listenEl.src = `/api/audio/listen?ref=${q(listening.ref)}&part=${part}`;
    listenEl.currentTime = from;  // before any data: where playback starts
    listenEl.play().catch(() => {});
    playerSays("готовлю…");
    const sel = player()?.querySelector(".lpart"); if (sel) sel.value = String(part);
  }
  function startListen(row, h) {
    stopListen();
    listening = { ref: h.ref, parts: h.parts || 1, part: 0, from: LISTEN_FROM };
    row.classList.add("on");
    const btn = row.querySelector('[data-act="listen"]'); btn.textContent = "■"; btn.title = "Остановить";
    row.insertAdjacentHTML("afterend", `<div class="lplay"><button class="ic" data-act="listenToggle" title="Пауза">${iconSvg("pause")}</button>
      <button class="link-btn" data-act="listenSkip" data-d="-30">−30 с</button><button class="link-btn" data-act="listenSkip" data-d="30">+30 с</button>
      <span class="lst"></span><span class="ltime"></span><div class="lbar"><i></i></div></div>`);
    playPart(0, LISTEN_FROM);
    // a search row may not know its parts: the list the proxy streams from says (cached on the server)
    const now = listening;
    fetch("/api/audio/parts?ref=" + q(h.ref)).then((r) => r.json()).then((ps) => {
      if (listening !== now || !Array.isArray(ps)) return;
      now.parts = ps.length;
      if (ps.length < 2) return;
      player()?.querySelector(".lst").insertAdjacentHTML("beforebegin", `<select class="lpart" aria-label="Часть">${ps.map((_, i) => `<option value="${i}"${i === now.part ? " selected" : ""}>часть ${i + 1}</option>`).join("")}</select>`);
    }).catch(() => {});
  }
  // a part shorter than the skipped intro is played from its start
  listenEl.addEventListener("loadedmetadata", () => { if (listening && listening.from && isFinite(listenEl.duration) && listenEl.duration < listening.from + 30) listenEl.currentTime = 0; });
  listenEl.addEventListener("playing", () => playerSays(""));
  listenEl.addEventListener("waiting", () => playerSays("готовлю…"));
  listenEl.addEventListener("error", () => { if (listening) playerSays("не играет: источник не отдаёт звук"); });
  ["play", "pause"].forEach((ev) => listenEl.addEventListener(ev, () => { const b = player()?.querySelector('[data-act="listenToggle"]'); if (b) { b.innerHTML = iconSvg(listenEl.paused ? "play" : "pause"); b.title = listenEl.paused ? "Слушать" : "Пауза"; } }));
  listenEl.addEventListener("timeupdate", () => {
    const p = player(), d = listenEl.duration, t = listenEl.currentTime;
    if (!p || !listening) return;
    p.querySelector(".ltime").textContent = fmt(t) + (isFinite(d) ? " / " + fmt(d) : "");
    p.querySelector(".lbar i").style.width = (isFinite(d) && d ? Math.min(100, (t / d) * 100) : 0) + "%";
  });
  listenEl.addEventListener("ended", () => { if (listening && listening.part + 1 < listening.parts) playPart(listening.part + 1, 0); });

  // ---- the card: collapsed, or opened in place with its sections ----
  let renaming = null;  // id of the card whose title is being edited
  let finding = null;  // id of the card whose search query is being typed
  const queryOf = (x) => (isShell(x) ? x.query : hitsCache[x.slug]?.query) || x.title || "";
  const icon = (act, glyph, title) => `<button class="ic" data-act="${act}" title="${title}">${glyph}</button>`;
  function actsHtml(x) {
    if (confirmDel === idOf(x)) return `<div class="acts confirm">${isShell(x) ? "убрать" : "удалить"}${x.has_audio ? " с аудио" : ""}? <button data-act="delYes">да</button> <button data-act="delNo">нет</button></div>`;
    // while the pipeline runs the only thing to offer is calling it off: the ring turns into a cross
    if (x.building) return `<div class="acts"><button class="ic loading" data-act="stopJob" title="Идёт загрузка, нажми чтобы отменить"><span class="spin"></span><span class="x">✕</span></button></div>`;
    const first = isShell(x) ? icon("find", "⌕", "Найти текст") : !x.ready ? "" : shelfOf(x) === "reading" ? icon("pause", "⏸", "Отложить") : icon("read", "▶", "Читать");
    return `<div class="acts">${first}${icon("gear", "⚙", "Текст и аудио")}${icon("del", "✕", isShell(x) ? "Убрать" : "Удалить")}</div>`;
  }
  function statusHtml(x) {
    const s = searching.get(idOf(x)) || {};
    if (s.busy) return `<div class="m status"><span class="spin"></span>ищу в ${SOURCES_LABEL}… <span class="secs">${Math.round((Date.now() - s.t0) / 1000)}</span> с<button class="link-btn" data-act="stop">отменить</button></div>`;
    if (s.status) return `<div class="m status warn">${s.status}</div>`;
    if (s.note) return `<div class="m">${s.note}</div>`;  // found, but not by the name as typed
    const found = isShell(x) ? x : hitsCache[x.slug];
    if (isShell(x) && x.searched && !found?.hits?.length && !found?.author_hits?.hits?.length) return `<div class="m status warn">не нашлось в ${SOURCES_LABEL}</div>`;
    return "";
  }
  function textSection(x) {
    const found = isShell(x) ? x : hitsCache[x.slug] || {};
    return `<div class="sec"><h5>Текст${x.text_source ? `<span class="now">· ${esc(sourceOf(x.text_source))}</span>` : ""}<button class="link-btn" data-act="find">искать издания</button></h5>
      ${statusHtml(x)}${resultsHtml(found, x.text_source)}
      <div class="own"><input name="text_url" placeholder="Ссылка на текст: страница книги, fb2, epub, pdf, txt"><label class="file">или <u>файл</u><input type="file" name="text_file" accept=".fb2,.zip,.epub,.pdf,.txt,.html,.htm" hidden></label><button class="btn sm" data-act="own">Загрузить</button></div></div>`;
  }
  function audioSection(b) {
    const now = b.has_audio ? [b.narrator ? "читает " + b.narrator : "", sourceOf(b.audio_source), b.timing_source === "mms" ? "точное выравнивание" : "разметка по субтитрам"].filter(Boolean).join(" · ") : "нет";
    const align = b.has_audio && b.timing_source !== "mms" ? `<button class="link-btn" data-act="align">выровнять точно (долго)</button>` : "";
    const find = audioOn() ? `<button class="link-btn" data-act="findAudio">${b.has_audio ? "заменить озвучку" : "искать озвучки"}</button>` : "";
    return `<div class="sec"><h5>Аудио<span class="now">· ${esc(now)}</span>${find}${align}</h5>
      ${audioFindHtml(b)}
      <div class="own"><input name="audio_url" placeholder="${b.has_audio ? "Заменить: " : ""}ссылка на YouTube, части по одной через пробел"><label class="file">или <u>файл</u><input type="file" name="audio_file" accept="audio/*,.m4b,.m4a,.mp3" hidden></label><input name="narrator" class="narr" placeholder="Чтец"><button class="btn sm" data-act="audioGo">Загрузить</button></div>
      ${b.has_audio || audioOn() ? "" : `<div class="m">Голос выбери сам: <a target="_blank" rel="noopener" href="https://www.youtube.com/results?search_query=${q((b.title || "") + " аудиокнига")}">YouTube</a></div>`}</div>`;
  }
  function headHtml(x) {
    if (finding === idOf(x)) {
      return `<div class="head editing"><span class="pen" aria-hidden="true">⌕</span>
        <input class="find-input" value="${esc(queryOf(x))}" spellcheck="false" aria-label="Что искать в ${SOURCES_LABEL}"
          placeholder="Название, автор, что угодно">
        ${icon("findGo", "→", "Искать (↵)")}${icon("close", "✕", "Закрыть (Esc отменяет ввод)")}</div>`;
    }
    if (renaming !== idOf(x)) {
      return `<div class="head"><button class="ic pen" data-act="rename" title="Переименовать">✎</button>
        <div class="t">${esc(x.title || x.slug)}${isShell(x) ? '<span class="tag">без текста</span>' : ""}</div>
        <button class="ic" data-act="close" title="Свернуть (Esc)">✕</button></div>`;
    }
    return `<div class="head editing"><span class="pen" aria-hidden="true">✎</span>
      <input class="rename-input" value="${esc(x.title || x.slug)}" spellcheck="false" aria-label="Название">
      ${icon("renameYes", "✓", "Сохранить (↵)")}${icon("close", "✕", "Закрыть (Esc отменяет ввод)")}</div>`;
  }
  function openHtml(x) {
    return `<div class="card open${isShell(x) ? " shell" : ""}" data-key="${esc(idOf(x))}"><div class="body">
      ${headHtml(x)}
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
    const where = !b.ready ? "" : st.finished ? "прочитано целиком" : b.has_audio ? (pct ? `прочитано ${pct}% · ${fmt(pos)}` : "не начато") : (st.sent ? `прочитано ${pct}%` : "не начато");
    const cover = b.cover ? `<img class="cover" src="/books/${esc(b.slug)}/${esc(b.cover)}" alt="">` : `<div class="cover empty">${esc((b.title || b.slug).slice(0, 1))}</div>`;
    const href = b.ready ? "?book=" + esc(b.slug) : "#";
    return `<div class="card" data-key="${esc(b.slug)}"><a class="cover-link" href="${href}">${cover}</a>
      <div class="body"><a href="${href}" class="tlink"><div class="t">${esc(b.title || b.slug)}</div></a><div class="m${meta ? "" : " empty"}">${esc(meta)}</div>${fail}${frag}
      <div class="bar${pct ? "" : " empty"}"><i style="width:${pct}%"></i></div><div class="m${pct ? "" : " empty"}">${where}</div></div>${actsHtml(b)}</div>`;
  }
  function shellHtml(w) {
    if (open === w.id) return openHtml(w);
    return `<div class="card shell" data-key="${esc(w.id)}"><div class="cover empty" data-act="gear">${esc(w.title.slice(0, 1))}</div>
      <div class="body" data-act="gear" title="Текст и аудио"><div class="t">${esc(w.title)}<span class="tag">без текста</span></div>${w.author ? `<div class="m">${esc(w.author)}</div>` : ""}${statusHtml(w)}</div>${actsHtml(w)}</div>`;
  }

  // ---- finding and adding: the sign by the heading opens one line above the cards it filters ----
  const omni = $("#omni-input"), omniRow = $("#omni"), omniOpen = $("#omni-open");
  let query = "";
  function showOmni(on, focus = true) {
    omniRow.hidden = !on;
    omniOpen.hidden = on;
    if (on) { if (focus) omni.focus(); }
    else { omni.blur(); omni.value = ""; query = ""; paint(); }  // focus must leave with the line, or "/" lands in it
  }
  omniOpen.onclick = () => showOmni(true);
  const matches = (x) => !query || norm(x.title + " " + (x.author || "") + " " + (x.translator || "")).includes(norm(query));
  function addRowHtml(any) {
    if (isUrl(query)) return `<div class="card add" data-act="link"><div class="body"><div class="t"><b>＋</b>Загрузить по ссылке</div><div class="m">↵ · название возьму из книги</div></div></div>`;
    return `<div class="card add" data-act="save"><div class="body"><div class="t"><b>＋</b>Сохранить «${esc(query)}»${any ? " как новую книгу" : ""}</div><div class="m">${any ? "⌘↵" : "↵"} · без текста; найти его или добавить свой можно потом</div></div></div>`;
  }
  // paint() lays out what is already loaded; renderLibrary() fetches first
  function paint() {
    stopListen();  // the rows and the player are drawn anew: a listen never outlives the row it started from
    const draft = keepDraft();
    const byActivity = (a, b) => (b.at || 0) - (a.at || 0) || (a.title || "").localeCompare(b.title || "", "ru");
    const entry = (b) => ({ at: b.state.opened || b.added || 0, title: b.title, html: cardHtml(b) });
    // while a query is typed everything matching sits in one list under the line, so nothing hides above it
    const hits = books.filter(matches);
    const reading = query ? [] : hits.filter((b) => shelfOf(b) === "reading").map(entry).sort(byActivity);
    const rest = [...(query ? hits : hits.filter((b) => shelfOf(b) !== "reading")).map(entry),
      ...wishes.filter(matches).map((w) => ({ at: +w.id.slice(1) || 0, title: w.title, html: shellHtml(w) }))].sort(byActivity);
    const any = reading.length + rest.length > 0;
    $("#lib-title").textContent = query && !any ? "В библиотеке нет" : reading.length ? "Остальные" : "Библиотека";
    $("#reading-section").hidden = !reading.length;
    $("#reading-list").innerHTML = reading.map((x) => x.html).join("");
    $("#library-list").innerHTML = rest.map((x) => x.html).join("") + (query ? addRowHtml(any) : !any ? '<p class="muted small">Пока пусто. Напиши название книги в строке выше, вставь ссылку или перетащи файл.</p>' : "");
    restoreDraft(draft);
  }
  // a repaint (a finished job, a search coming back) must not wipe a name that is being typed.
  // The field is remembered by name: a half-typed title must not reappear in the search line.
  function keepDraft() {
    const all = [...document.querySelectorAll(".rename-input, .find-input, .afind-input")];
    const el = all.find((x) => x === document.activeElement) || all[0];
    if (!el) return null;
    const on = "." + ["rename-input", "find-input", "afind-input"].find((c) => el.classList.contains(c));
    return { on, value: el.value, from: el.selectionStart, to: el.selectionEnd, focused: document.activeElement === el };
  }
  function restoreDraft(d) {
    const el = d && document.querySelector(d.on);
    if (!el) return;
    el.value = d.value;
    if (d.focused) { el.focus(); el.setSelectionRange(d.from, d.to); }
  }
  async function renderLibrary() {
    [books, wishes] = await Promise.all([fetch("/api/books").then((r) => r.json()), api("GET", "/api/wishlist").catch(() => wishes)]);
    paint();
    // nothing to find yet: the line is the only move, but it must not grab the keyboard on its own
    if (!books.length && !wishes.length && omniRow.hidden) showOmni(true, false);
    headerLine(books.filter((b) => shelfOf(b) === "reading"), books);
  }
  omni.addEventListener("input", () => { query = omni.value.trim(); paint(); });
  omni.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { showOmni(false); return; }
    if (e.key !== "Enter" || !query) return;
    e.preventDefault();
    const any = books.some(matches) || wishes.some(matches);
    if (isUrl(query)) addByLink(query);
    else if (!any || e.metaKey || e.ctrlKey) addTitle(query);
  });
  addEventListener("keydown", (e) => {
    if (e.key === "Escape" && listening) { stopListen(); return; }  // the first Esc silences, the next closes
    if (e.key === "Escape") { prefs.hidden = true; if (renaming || finding) { renaming = finding = null; paint(); } else if (open) { open = null; paint(); } }
    if (e.key === "/" && !(e.target instanceof Element && e.target.matches("input, textarea, select"))) { e.preventDefault(); showOmni(true); }
  });
  omni.addEventListener("blur", () => { if (!query) showOmni(false); });  // opened by accident: it closes itself
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
  const saveHits = (slug, found) => { hitsCache[slug] = found; return api("PUT", `/api/hits/${slug}`, { hits: found.hits || [], author_hits: found.author_hits || null, query: found.query || "" }).catch(() => {}); };

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
    if (await startAdd({ text_url: url })) { clearOmni(); renderLibrary(); }  // the card shows up loading, that is the message
  }
  async function addByFile(file) {
    if (await startAdd({ title: file.name.replace(/\.(fb2\.zip|zip|fb2|epub|txt|html?)$/i, ""), text_file: file })) renderLibrary();
  }
  ["dragenter", "dragover"].forEach((ev) => addEventListener(ev, (e) => { if (e.dataTransfer?.types.includes("Files")) { e.preventDefault(); document.body.classList.add("dropping"); } }));
  ["dragleave", "drop"].forEach((ev) => addEventListener(ev, (e) => { if (ev === "drop" || e.relatedTarget == null) document.body.classList.remove("dropping"); }));
  addEventListener("drop", (e) => { if (!e.dataTransfer?.files?.length || e.target.closest(".card.open")) return; e.preventDefault(); [...e.dataTransfer.files].forEach(addByFile); });

  // ---- library search for a card: the result stays with it (wishlist item or hits.json) ----
  function stopSearch(x) {
    const s = searching.get(idOf(x));
    if (!s?.busy) return;
    s.stopped = true;
    s.ctrl.abort();
    searching.delete(idOf(x));
    paint();
  }
  // Ask the catalogs. The field the query was typed in stays open the whole time: a search that
  // found the wrong book is answered by changing a word and asking again, which is the common case,
  // so a second query simply calls the first one off instead of being dropped on the floor.
  async function searchFor(x, query) {
    const id = idOf(x);
    query = (query || queryOf(x)).trim();
    if (!query) return;
    if (searching.get(id)?.busy) stopSearch(x);
    const t0 = Date.now();
    const ctrl = new AbortController();
    const state0 = { busy: true, t0, ctrl };
    searching.set(id, state0); paint();
    const ticker = setInterval(() => { const el = document.querySelector(`.card[data-key="${CSS.escape(id)}"] .secs`); if (el) el.textContent = Math.round((Date.now() - t0) / 1000); }, 1000);
    const killer = setTimeout(() => ctrl.abort(), 150000);  // two rounds of 45 s on the server, plus the reading
    let res = null, state = null;
    try { res = await fetch("/api/search?q=" + q(query), { signal: ctrl.signal }).then((r) => r.json()); if (res.error) throw new Error(res.error); }
    catch (e) { res = null; state = state0.stopped ? null : { status: e.name === "AbortError" ? "библиотеки не ответили, попробуй позже" : "поиск не удался: " + esc(e.message) }; }
    finally { clearInterval(ticker); clearTimeout(killer); }
    if (res && !state0.stopped) {
      // the reader may have asked again while this was in flight: only the live search saves
      const failed = (res.errors || []).map((e) => SOURCE[e.split(":")[0]] || e.split(":")[0]).filter((v, i, a) => a.indexOf(v) === i);
      const failedNote = failed.length ? `${failed.join(", ")} не ответил${failed.length > 1 ? "и" : ""}` : "";
      const any = res.hits.length || res.author?.hits?.length;
      const said = [res.note, failedNote].filter(Boolean).join(" · ");
      // nothing found while a library was down is not "no such book": say what happened instead
      state = any
        ? (said ? (failedNote ? { status: esc(said) } : { note: esc(said) }) : null)
        : { status: esc(failedNote ? `${failedNote}, попробуй ещё раз` : `не нашлось в ${SOURCES_LABEL}`) };
      const found = { hits: res.hits, author_hits: res.author, query };  // the query stays with the card, to search again from
      if (searching.get(id) === state0) {
        if (isShell(x)) wishes = await api("PUT", "/api/wishlist/" + x.id, { ...found, searched: today() }).catch(() => wishes);
        else await saveHits(x.slug, found);
      }
    }
    if (state0.stopped) return;  // called off: the card is already back to how it was
    if (state) searching.set(id, state); else searching.delete(id);
    paint();
  }
  // the rename and search fields belong to the card they were opened on: they close with it, or the
  // next card to open shows a field for a name nobody is editing
  const closeCard = () => { open = renaming = finding = audioConfirm = null; stopListen(); };
  // load a picked edition or an own link/file: a shell becomes the book, a ready book gets its text replaced
  async function loadText(x, fields) {
    // the catalog names the book: a picked edition brings its own title, an own file keeps the card's
    if (isShell(x)) Object.assign(fields, { title: fields.title || x.title, author: fields.author || x.author });
    else Object.assign(fields, { slug: x.slug, replace: "1" });
    const s = await startAdd(fields);
    if (!s) return;
    closeCard();
    if (isShell(x)) { await saveHits(s, { hits: x.hits, author_hits: x.author_hits, query: queryOf(x) }); searching.delete(x.id); await api("DELETE", "/api/wishlist/" + x.id).catch(() => {}); }
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
      const to = open === idOf(x) ? null : idOf(x);
      closeCard();
      open = to;
      if (open && !isShell(x) && !hitsCache[x.slug]) hitsCache[x.slug] = await fetch("/api/hits/" + x.slug).then((r) => r.json()).catch(() => ({}));
      paint();
    },
    close: () => { closeCard(); paint(); },
    rename: (x) => { renaming = idOf(x); paint(); const el = document.querySelector(".card.open .rename-input"); el?.focus(); el?.select(); },
    renameNo: () => { renaming = null; paint(); },
    renameYes: async (x, btn) => {
      const title = btn.closest(".head").querySelector(".rename-input").value.trim();
      const taken = [...books, ...wishes].some((y) => idOf(y) !== idOf(x) && norm(y.title) === norm(title));
      if (taken) { toast(`«${title}» уже в библиотеке`); return; }  // stay in the field, the name is free to fix
      renaming = null;
      if (!title || title === x.title) { paint(); return; }
      // the old search result belongs to the old name: it goes, so the card asks to search again
      const r = isShell(x)
        ? await api("PUT", "/api/wishlist/" + x.id, { title, searched: "", query: "", hits: [], author_hits: null }).catch((err) => ({ error: String(err) }))
        : await api("PUT", "/api/books/" + x.slug, { title }).catch((err) => ({ error: String(err) }));
      if (r.error) { toast("Ошибка: " + r.error); paint(); return; }
      if (isShell(x)) wishes = r;
      // the query that found the old name goes with it; the server clears the saved copy, this
      // drops the one in hand so the field offers the new name at once
      if (hitsCache[x.slug]) hitsCache[x.slug].query = "";
      searching.delete(idOf(x));
      renderLibrary();
    },
    del: (x) => { confirmDel = idOf(x); paint(); },
    delNo: () => { confirmDel = null; paint(); },
    delYes: async (x) => {
      confirmDel = null;
      renaming = finding = null;
      stopSearch(x);  // nothing left to answer: the round in flight must not write to a card that is gone
      if (isShell(x)) { wishes = await api("DELETE", "/api/wishlist/" + x.id); paint(); return; }
      const r = await api("DELETE", "/api/books/" + x.slug).catch((err) => ({ error: String(err) }));
      if (r.error) toast("Ошибка: " + r.error); else { delete jobs[x.slug]; renderLibrary(); }
    },
    // one click both asks and shows what is being asked: the field opens with the query in it and the
    // search starts at once. It then stays open with the answer beside it, so a wrong word is one
    // retype and one ↵ away, however many times it takes.
    find: async (x) => {
      if (open !== idOf(x)) { await ACTIONS.gear(x); }  // a card searched from the shelf opens with its sections
      // the field is already open: the link asks for what stands in it now, not for the title again
      const typed = finding === idOf(x) ? document.querySelector(".card.open .find-input")?.value.trim() : null;
      renaming = null; finding = idOf(x); paint();
      const el = document.querySelector(".card.open .find-input"); el?.focus(); el?.select();
      searchFor(x, typed);
    },
    findNo: () => { finding = null; paint(); },  // Esc leaves the field; stopping the search is «отменить»
    findGo: (x, btn) => {
      const query = btn.closest(".head").querySelector(".find-input").value.trim();
      if (query) searchFor(x, query);
    },
    stop: (x) => { stopSearch(x); },
    stopJob: async (x) => {
      const r = await api("DELETE", "/api/jobs/" + x.slug).catch((err) => ({ error: String(err) }));
      if (r.error === "not found") toast("Этот сервер ещё не умеет отменять загрузку, перезапусти его");
      else if (r.error) toast("Не вышло отменить: " + r.error);
      else delete jobs[x.slug];
      renderLibrary();
    },
    pick: (x, btn) => {
      const h = JSON.parse(btn.dataset.hit);
      loadText(x, { title: h.title, author: h.author, translator: h.translator, year: h.year, narrator: h.narrator, text_url: h.urls.join("\n"), audio_url: isShell(x) ? h.audio || "" : "" });
    },
    own: (x, btn) => {
      const row = btn.closest(".own"), url = row.querySelector("[name=text_url]").value.trim(), file = row.querySelector("[name=text_file]").files[0];
      if (!url && !file) { toast("Нужна ссылка на текст или файл"); return; }
      if (url && !isUrl(url)) { toast("Ссылка должна начинаться с http(s)"); return; }
      loadText(x, { text_url: url, text_file: file });
    },
    audioGo: async (x, btn) => {
      const row = btn.closest(".own"), urls = row.querySelector("[name=audio_url]").value.trim().split(/\s+/).filter(Boolean).join("\n"), file = row.querySelector("[name=audio_file]").files[0];
      if (!urls && !file) { toast("Нужна ссылка на аудио или файл"); return; }
      const ok = await startAdd({ slug: x.slug, audio_url: urls, audio_file: file, narrator: row.querySelector("[name=narrator]").value.trim() });
      if (ok) { closeCard(); renderLibrary(); }
    },
    // the field opens with "<title> <author>" and the search starts at once; asked again, it takes the field
    findAudio: (b) => {
      const typed = document.querySelector(".card.open .afind-input")?.value.trim();
      findAudio(b, typed || [b.title, b.author].filter(Boolean).join(" "));
      const el = document.querySelector(".card.open .afind-input"); el?.focus(); el?.select();
    },
    findAudioGo: (b, btn) => findAudio(b, btn.closest(".afind").querySelector(".afind-input").value),
    stopAudioFind: (b) => {
      const s = audioFinding.get(b.slug);
      if (!s?.busy) return;
      s.ctrl.abort(); audioFinding.set(b.slug, { query: s.query }); paint();
    },
    listen: (b, btn) => {
      const row = btn.closest(".cand"), h = audioHits.get(row.dataset.ref);
      if (!h) return;
      if (listening?.ref === h.ref) stopListen(); else startListen(row, h);  // another recording stops this one
    },
    listenToggle: () => { if (!listening) return; if (listenEl.paused) listenEl.play().catch(() => {}); else listenEl.pause(); },
    listenSkip: (b, btn) => { if (listening) listenEl.currentTime = Math.max(0, listenEl.currentTime + Number(btn.dataset.d)); },
    audioPick: (b, btn) => { audioConfirm = btn.closest(".cand").dataset.ref; paint(); },
    audioNo: () => { audioConfirm = null; paint(); },
    audioYes: async (b, btn) => {
      const h = audioHits.get(btn.dataset.ref);
      audioConfirm = null;
      if (!h) { paint(); return; }
      const ok = await startAdd({ slug: b.slug, audio_ref: h.ref, narrator: h.narrator || "" });
      if (ok) { closeCard(); renderLibrary(); } else paint();
    },
    align: async (x) => {
      const r = await api("POST", "/api/align/" + x.slug).catch((err) => ({ error: String(err) }));
      if (r.error) toast("Ошибка: " + r.error); else { closeCard(); toast("Точное выравнивание запущено, это долго"); pollJobs(); renderLibrary(); }
    },
  };
  $("#library").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const card = btn.closest(".card");
    const x = card && !card.classList.contains("add") ? entryOf(card) : null;
    if (x || card?.classList.contains("add")) ACTIONS[btn.dataset.act]?.(x, btn);
  });
  $("#library").addEventListener("keydown", (e) => {
    if (!(e.target instanceof Element)) return;
    if (e.target.classList.contains("afind-input")) {
      if (e.key === "Enter") { e.preventDefault(); e.target.closest(".afind")?.querySelector('[data-act="findAudioGo"]')?.click(); }
      return;
    }
    const rename = e.target.classList.contains("rename-input"), find = e.target.classList.contains("find-input");
    if (!rename && !find) return;
    const act = rename ? "renameYes" : "findGo";
    if (e.key === "Enter") { e.preventDefault(); e.target.closest(".head")?.querySelector(`[data-act="${act}"]`)?.click(); }
    if (e.key === "Escape") { e.stopPropagation(); if (rename) ACTIONS.renameNo(); else ACTIONS.findNo(); }
  });
  // "или файл": the label shows the chosen name
  $("#library").addEventListener("change", (e) => {
    if (e.target.matches(".lpart")) { if (listening) playPart(Number(e.target.value), 0); return; }
    if (e.target.type !== "file") return;
    const l = e.target.closest("label"), name = e.target.files[0]?.name;
    if (l) l.querySelector("u").textContent = name || "файл";
    l?.classList.toggle("chosen", !!name);
  });

  pollJobs().then(renderLibrary).then(() => {
    const wishParam = new URLSearchParams(location.search).get("wish");
    if (!wishParam) return;
    history.replaceState(null, "", location.pathname);
    addTitle(wishParam.replace(/\s+[-–—|].*$/, "").trim() || wishParam);
  });
})();
