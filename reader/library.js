/* readsync library page: one line finds and adds; cards open in place for text and audio. */
(() => {
  "use strict";
  if (slug) return;
  $("#library").hidden = false;
  fetchSettings().then(adoptSettings);
  // one toast at a time, in a live region: a new message replaces the last instead of stacking on it
  const toastEl = Object.assign(document.createElement("div"), { className: "toast", hidden: true });
  toastEl.setAttribute("role", "status");
  document.body.appendChild(toastEl);
  let toastTimer = 0;
  const toast = (msg) => { toastEl.textContent = msg; toastEl.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { toastEl.hidden = true; }, 3000); };
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
  // the view is a setting too: another browser's choice arriving lays the shelf out again
  onSettingsSynced = () => { syncPrefsUI(); if (loaded) paint(); };
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
    line.classList.toggle("go", !!href);
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
    line.classList.remove("go"); line.onclick = null;
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
    const done = all.filter((b) => b.ready && statusOf(b) === "done");
    if (done.length) {
      const b = done[Math.floor(Math.random() * done.length)];
      const w = await fetch(`/api/where/${b.slug}?random=1`).then((r) => r.json()).catch(() => null);
      if (w && w.text) { runDemo(w.text, "из «" + b.title + "»", "?book=" + b.slug, "random"); return; }
    }
    runIdle();
  }

  // ---- data ----
  // Shelves follow the book's status, which the server derives (state.status): «reading» is «Читаю сейчас»,
  // «done» goes to the folded «Прочитанные», newest first; «paused» and none share the rest. Within a shelf
  // the most recent activity (opened, or added) comes first. A title saved without text is a "shell" card
  // that waits in the catalog. Every card opens in place (⚙) for its text, audio and status.
  // open: id of the card opened in place; confirmDel / confirmStop: the card asking "delete?" / "call the load off?"
  let books = [], wishes = [], jobs = {}, open = null, confirmDel = null, confirmStop = null, loaded = false;
  const SOURCE = { "fantasy-worlds": "Fantasy Worlds", flibusta: "Flibusta", coollib: "Coollib", "standard-ebooks": "Standard Ebooks",
    gutenberg: "Gutenberg", wikisource: "Wikisource", bia: "Buddhadasa Archives" };
  const SOURCES_LABEL = Object.values(SOURCE).join(", ");
  const SOURCES_N = Object.keys(SOURCE).length;
  const LOADABLE = new Set(["fb2", "epub", "pdf", "txt", "html"]);
  const searching = new Map();  // card id -> {busy, t0} | {status}: this session's search state
  const hitsCache = {};  // slug -> {hits, author_hits} from /api/hits, loaded when a book card opens
  // a book comes with its reading state; a saved title has none (its `slug`, once set, names the book it loads into)
  const isShell = (x) => !x.state;
  const idOf = (x) => (isShell(x) ? x.id : x.slug);
  const nameOf = (b) => b.title || (b.building ? "Книга по ссылке" : b.slug);  // a link names its book only once loaded
  const statusOf = (b) => b.state?.status || "none";
  let showDone = store.get("rs:showDone", false);  // «Прочитанные» unfolded: this browser's convenience, not state
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
  const sizeOf = (h) => (oursOf(h) ? `≈${bytes(oursOf(h))} на диске` : "");
  const clean = (t) => (t || "").replace(/\p{Extended_Pictographic}/gu, "").replace(/\s+/g, " ").trim();  // 📖🎧 from video titles
  const narratorOf = (h) => clean(h.narrator);
  const audioMeta = (h) => [h.author, narratorOf(h) ? "читает " + narratorOf(h) : "", h.duration_s ? hm(h.duration_s) : "",
    h.parts > 1 ? plural(h.parts, "часть", "части", "частей") : "", sizeOf(h), AUDIO_SOURCE[h.source] || h.source].filter(Boolean).join(" · ");
  // Is it this book? A catalog answers any words with something: a row whose title shares too few words with
  // the book's title (two, or its only one) is another book, and a recording far shorter than the book (or than the other recordings
  // of it) is a fragment. Neither is hidden, both are said, and another book goes under «ещё».
  const STOP = new Set(["аудиокнига", "книга", "читает", "часть", "глава", "том", "the", "and"]);
  const stem = (w) => (w.length > 5 ? w.slice(0, 5) : w);  // Russian endings: «чиновника» and «чиновник» are one word
  const sig = (t) => new Set(norm(t).split(" ").filter((w) => (w.length > 2 || /\d/.test(w)) && !STOP.has(w)).map(stem));
  function judge(hits, b) {
    const want = sig(b.title);
    const need = Math.min(2, want.size);  // one shared word of a longer title is a coincidence («Чья-то смерть»)
    const related = (h) => [...sig(h.title)].filter((w) => want.has(w)).length >= need;
    const mine = hits.filter(related), lens = mine.map((h) => h.duration_s).filter(Boolean).sort((x, y) => x - y);
    const expect = (b.has_audio && b.state.duration) || lens[Math.floor(lens.length / 2)] || 0;
    hits.forEach((h) => { h.other = !related(h); h.fragment = !h.other && !!expect && !!h.duration_s && h.duration_s < expect * 0.35; });
    return { mine: [...mine.filter((h) => !h.fragment), ...mine.filter((h) => h.fragment)], other: hits.filter((h) => h.other) };
  }
  function audioRowHtml(h, b) {
    audioHits.set(h.ref, h);
    const r = esc(h.ref), ours = oursOf(h), now = b.audio_source === h.ref;
    const right = audioConfirm === h.ref
      ? `<div class="acts confirm">${b.has_audio ? "заменить" : "загрузить"}${ours ? " ≈" + bytes(ours) : ""}${h.captions ? "" : " и распознать речь"}? <button data-act="audioYes" data-ref="${r}">да</button> <button data-act="audioNo">нет</button></div>`
      : `${icon("listen", "▶", "Послушать")}${now ? '<span class="now">сейчас</span>' : `<button class="btn sm" data-act="audioPick">${b.has_audio ? "Заменить" : "Загрузить"}</button>`}`;
    const tags = (h.other ? '<span class="tag">другая книга?</span>' : "") + (h.fragment ? '<span class="tag" title="Намного короче книги">фрагмент?</span>' : "");
    return `<div class="cand${now ? " cur" : ""}" data-ref="${r}"><div class="ct">${esc(clean(h.title))}${tags}<div class="cm">${esc(audioMeta(h))}</div>
      ${h.captions ? "" : '<div class="cm">без субтитров: разметка распознаванием речи, долго</div>'}</div>${right}</div>`;
  }
  function audioRowsHtml(hits, b) {
    // one book read by several narrators folds into one line, as editions do
    const fold = (list) => {
      const groups = [];
      list.forEach((h) => { const key = norm(h.title) + "|" + norm(h.author); const g = groups.find((x) => x.key === key); if (g) g.rows.push(h); else groups.push({ key, rows: [h] }); });
      return groups;
    };
    const asking = (g) => g.rows.some((h) => h.ref === audioConfirm || h.ref === listening?.ref);
    const one = (g) => (g.rows.length === 1 ? audioRowHtml(g.rows[0], b)
      : `<details class="eds"${asking(g) ? " open" : ""}><summary><span class="ct">${esc(g.rows[0].title)}<div class="cm">${esc([g.rows[0].author, plural(g.rows.length, "озвучка", "озвучки", "озвучек")].filter(Boolean).join(" · "))}</div></span><span class="link-btn">озвучки</span></summary><div class="cands">${g.rows.map((h) => audioRowHtml(h, b)).join("")}</div></details>`);
    const { mine, other } = judge(hits, b);
    const groups = fold(mine), rest = [...groups.slice(SHOW), ...fold(other)];
    const head = groups.slice(0, SHOW);
    const label = groups.length > SHOW ? `ещё ${rest.length}` : `другие книги? ${rest.length}`;
    return `<div class="cands">${head.length ? head.map(one).join("") : '<div class="m">ничего похожего на эту книгу</div>'}${rest.length ? `<details class="more"${rest.some(asking) ? " open" : ""}><summary>${label}</summary><div class="cands">${rest.map(one).join("")}</div></details>` : ""}</div>`;
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
  // Collapsed, a card offers what is done daily: ▶ opens the book, ⚙ opens the card. Everything else —
  // the shelf, the text and audio, deleting — is in the card opened in place, each said in words.
  let renaming = null;  // id of the card whose title is being edited
  let finding = null;  // id of the card whose search query is being typed
  const queryOf = (x) => (isShell(x) ? x.query : hitsCache[x.slug]?.query) || x.title || "";
  const icon = (act, glyph, title) => `<button class="ic" data-act="${act}" title="${title}" aria-label="${title}">${glyph}</button>`;
  const jobOf = (b) => jobs[b.slug];
  const failedJob = (b) => { const j = jobOf(b); return j && !j.running && j.exit !== 0 ? j : null; };
  const shellOf = (b) => wishes.find((w) => w.slug === b.slug);  // the title this book is loading for
  const openableCount = (found) => (found?.hits || []).filter(openable).length + (found?.author_hits?.hits || []).filter(openable).length;
  function actsHtml(x) {
    if (x.building) {
      if (confirmStop === idOf(x)) return `<div class="acts confirm">отменить загрузку? <button data-act="stopYes">да, отменить</button> <button data-act="stopNo">нет</button></div>`;
      return `<div class="acts"><span class="spin" aria-hidden="true"></span><button class="link-btn" data-act="stopJob">отменить</button></div>`;
    }
    if (isShell(x)) return `<div class="acts"><button class="link-btn" data-act="find">найти текст</button>${icon("gear", "⚙", "Издания, своя ссылка или файл")}</div>`;
    const read = x.ready ? `<a class="ic" href="?book=${esc(x.slug)}" tabindex="-1" title="Читать" aria-label="Читать">▶</a>` : "";
    return `<div class="acts">${read}${icon("gear", "⚙", "Текст, аудио, полка")}</div>`;
  }
  function statusHtml(x) {
    const s = searching.get(idOf(x)) || {};
    if (s.busy) return `<div class="m status"><span class="spin"></span><span title="${SOURCES_LABEL}">ищу в ${SOURCES_N} каталогах…</span> <span class="secs">${Math.round((Date.now() - s.t0) / 1000)}</span> с<button class="link-btn" data-act="stop">отменить</button></div>`;
    if (s.status) return `<div class="m status warn">${s.status}</div>`;
    if (s.note) return `<div class="m">${s.note}</div>`;  // found, but not by the name as typed
    const found = isShell(x) ? x : hitsCache[x.slug];
    if (isShell(x) && x.searched && !openableCount(found) && !found?.unopenable) return `<div class="m status warn">не нашлось ни в одном из ${SOURCES_N} каталогов</div>`;
    return "";
  }
  // collapsed, a shell says what came of its search in a word; which catalog was down is for the open card
  function briefHtml(w) {
    const s = searching.get(w.id) || {}, n = openableCount(w);
    if (s.busy || !n) return statusHtml(w);
    return `<div class="m">нашлось ${plural(n, "издание", "издания", "изданий")} · открой и выбери</div>`;
  }
  function failHtml(b) {
    const j = failedJob(b);
    if (!j) return "";
    const other = b.ready ? (hitsCache[b.slug]?.hits?.length || b.has_hits) : shellOf(b) || b.has_hits;
    return `<div class="m status warn"><span>не загрузилось: ${esc(j.log[j.log.length - 1] || "код " + j.exit)}</span>${other ? '<button class="link-btn" data-act="otherEdition">другое издание</button>' : ""}${b.ready ? '<button class="link-btn" data-act="dismiss">скрыть</button>' : ""}</div>`;
  }
  // `short`: the line under a cover, where a word and a number fit
  function progressOf(b, short = false) {
    const st = b.state, pos = st.pos || 0, dur = st.duration || store.get("rs:dur:" + b.slug, 0), status = statusOf(b);
    // read as pages, an audiobook counts its page (the phone does the same)
    const pct = Math.round(Number(b.has_audio && st.mode !== "pages" ? (dur ? (pos / dur) * 100 : 0) : st.sentPct)) || 0;
    if (!b.ready) return { pct: 0, where: "" };
    // a status said in words shows even at 0%
    if (status === "done") return { pct: 100, done: true, said: true, where: st.finishedOn && !short ? "прочитана " + dayName(st.finishedOn) : "прочитана" };
    if (status === "paused") return { pct, said: true, where: `отложена · ${pct}%` };
    if (st.rereading) return { pct, said: true, where: `перечитываю · ${pct}%` };
    if (short) return { pct, where: pct ? `${pct}%` : "" };
    return { pct, where: b.has_audio ? (pct ? `прочитано ${pct}% · ${fmt(pos)}` : "не начато") : (st.sent ? `прочитано ${pct}%` : "не начато") };
  }
  function textSection(x) {
    const found = isShell(x) ? x : hitsCache[x.slug] || {};
    const swap = !isShell(x) && x.ready;  // a book with text gets it replaced: the words say so
    return `<div class="sec"><h5>Текст${x.text_source ? `<span class="now">· ${esc(sourceOf(x.text_source))}</span>` : ""}<button class="link-btn" data-act="find">искать издания</button></h5>
      ${statusHtml(x)}${resultsHtml(found, x.text_source)}
      <div class="own"><input name="text_url" placeholder="${swap ? "Заменить текст: ссылка" : "Своя ссылка"} на страницу книги, fb2, epub, pdf, txt"><label class="file">или <u>файл</u><input type="file" name="text_file" accept=".fb2,.zip,.epub,.pdf,.txt,.html,.htm" hidden></label><button class="btn sm" data-act="own">${swap ? "Заменить" : "Добавить"}</button></div></div>`;
  }
  function audioSection(b) {
    const now = b.has_audio ? [narratorOf(b) ? "читает " + narratorOf(b) : "", sourceOf(b.audio_source), b.timing_source === "mms" ? "точное выравнивание" : "разметка по субтитрам"].filter(Boolean).join(" · ") : "нет";
    const align = b.has_audio && b.timing_source !== "mms" ? `<button class="link-btn" data-act="align">выровнять точно (долго)</button>` : "";
    const find = audioOn() ? `<button class="link-btn" data-act="findAudio">${b.has_audio ? "заменить озвучку" : "искать озвучки"}</button>` : "";
    return `<div class="sec"><h5>Аудио<span class="now">· ${esc(now)}</span>${find}${align}</h5>
      ${audioFindHtml(b)}
      <div class="own"><input name="audio_url" placeholder="${b.has_audio ? "Заменить: " : ""}ссылка на YouTube, части по одной через пробел"><label class="file">или <u>файл</u><input type="file" name="audio_file" accept="audio/*,.m4b,.m4a,.mp3" hidden></label><input name="narrator" class="narr" placeholder="Чтец"><button class="btn sm" data-act="audioGo">${b.has_audio ? "Заменить" : "Добавить"}</button></div>
      ${b.has_audio || audioOn() ? "" : `<div class="m">Голос выбери сам: <a target="_blank" rel="noopener" href="https://www.youtube.com/results?search_query=${q((b.title || "") + " аудиокнига")}">YouTube</a></div>`}</div>`;
  }
  function headHtml(x) {
    const fold = icon("close", "⌃", "Свернуть (Esc)");
    if (finding === idOf(x)) {
      return `<div class="head editing"><span class="pen" aria-hidden="true">⌕</span>
        <input class="find-input" value="${esc(queryOf(x))}" spellcheck="false" aria-label="Что искать в ${SOURCES_LABEL}"
          placeholder="Название, автор, что угодно">
        ${icon("findGo", "→", "Искать (↵)")}${fold}</div>`;
    }
    if (renaming !== idOf(x)) {
      return `<div class="head"><button class="ic pen" data-act="rename" title="Переименовать" aria-label="Переименовать">✎</button>
        <div class="t">${esc(isShell(x) ? x.title : nameOf(x))}${isShell(x) ? '<span class="tag">без текста</span>' : ""}</div>${fold}</div>`;
    }
    return `<div class="head editing"><span class="pen" aria-hidden="true">✎</span>
      <input class="rename-input" value="${esc(x.title || "")}" spellcheck="false" aria-label="Название">
      ${icon("renameYes", "✓", "Сохранить (↵)")}${fold}</div>`;
  }
  // a ready book opened in place still reads at one click: «Читать», where it stands, and its status as
  // three words, the current one marked (none for a book without one); a read book can be read again
  const STATUSES = [["reading", "Читаю"], ["paused", "Отложена"], ["done", "Прочитана"]];
  function leadHtml(b) {
    if (isShell(b) || !b.ready) return "";
    const now = statusOf(b);
    const seg = STATUSES.map(([v, label]) => `<button type="button" role="radio" aria-checked="${now === v}" data-act="status" data-v="${v}">${label}</button>`).join('<span aria-hidden="true">·</span>');
    return `<div class="lead"><a class="btn sm primary" href="?book=${esc(b.slug)}">Читать</a><span class="m">${esc(progressOf(b).where)}</span>
      <span class="stat-seg" role="radiogroup" aria-label="Статус книги">${seg}</span>${now === "done" ? '<button class="link-btn" data-act="reread">перечитать</button>' : ""}</div>`;
  }
  function footHtml(x) {
    if (confirmDel === idOf(x)) {
      const what = isShell(x) ? "убрать название из библиотеки?" : `удалить книгу${x.has_audio ? " вместе с аудио" : ""} и место, где остановился?`;
      return `<div class="foot confirm">${what} <button class="danger" data-act="delYes">${isShell(x) ? "убрать" : "удалить"}</button> <button data-act="delNo">отмена</button></div>`;
    }
    return `<div class="foot"><button class="link-btn" data-act="del">${isShell(x) ? "убрать из библиотеки" : "удалить книгу"}</button></div>`;
  }
  // `panel`: opened from a cover, the card spans the grid's width under the cover's row
  function openHtml(x, panel = false) {
    return `<div class="card open${isShell(x) ? " shell" : ""}${panel ? " panel" : ""}" data-key="${esc(idOf(x))}"><div class="body">
      ${headHtml(x)}
      ${facts(x) ? `<div class="m">${esc(facts(x))}</div>` : ""}${leadHtml(x)}${isShell(x) ? "" : failHtml(x)}
      ${x.building ? "" : textSection(x)}${isShell(x) || !x.ready || x.building ? "" : audioSection(x)}${x.building ? "" : footHtml(x)}</div></div>`;
  }
  function cardHtml(b) {
    if (open === b.slug && !b.building) return openHtml(b);
    const job = jobOf(b), failed = !!failedJob(b);
    const { pct, where, done, said } = progressOf(b);
    const stage = job?.running && job.stage ? " · " + job.stage : "";
    const meta = [facts(b), !b.ready ? (b.building ? "загружается" + (stage || "…") : failed ? null : "не загрузилась до конца") : b.building ? "заменяю" + (stage || "…") : null].filter(Boolean).join(" · ");
    const frag = b.fragment_note ? `<div class="m status warn">в конце текста «${esc(b.fragment_note)}»</div>` : "";
    const cover = b.cover ? `<img class="cover" src="/books/${esc(b.slug)}/${esc(b.cover)}" alt="">` : `<div class="cover empty">${esc(nameOf(b).slice(0, 1))}</div>`;
    // one stop for the keyboard per card: the title; the cover is the same link for the pointer only
    const title = `<div class="t">${esc(nameOf(b))}</div>`;
    const link = b.ready ? `<a class="cover-link" href="?book=${esc(b.slug)}" tabindex="-1" aria-hidden="true">${cover}</a>` : `<div class="cover-link">${cover}</div>`;
    return `<div class="card" data-key="${esc(b.slug)}">${link}
      <div class="body">${b.ready ? `<a href="?book=${esc(b.slug)}" class="tlink">${title}</a>` : title}<div class="m${meta ? "" : " empty"}">${esc(meta)}</div>${failHtml(b)}${frag}
      <div class="bar${pct ? "" : " empty"}${done ? " done" : ""}"><i style="width:${pct}%"></i></div><div class="m${pct || said ? "" : " empty"}">${esc(where)}</div></div>${actsHtml(b)}</div>`;
  }
  // ---- covers: the same books as tiles, a cover, the title in two lines and one line of state ----
  // The title is the tile's one stop for the keyboard (a book not loaded yet: its ⚙). A tile opened stays in
  // its place, marked, and its card follows it across the whole grid; the card keeps the book's data-key
  // (focus, closing and the search line find the book by it), the tile names it with data-for.
  const plate = (title, foot, cls = "") => `<div class="cover plate${cls}"><span class="pt">${esc(title)}</span><span class="pa">${esc(foot)}</span></div>`;
  function tileHtml(b) {
    const opened = open === b.slug && !b.building, job = jobOf(b), fail = failedJob(b);
    const loading = b.building;
    const cover = b.cover ? `<img class="cover" src="/books/${esc(b.slug)}/${esc(b.cover)}" alt="" loading="lazy">` : plate(nameOf(b), b.author || "");
    const art = `${cover}${loading ? '<span class="spin" aria-hidden="true"></span>' : ""}`;
    const link = b.ready ? `<a class="cover-link" href="?book=${esc(b.slug)}" tabindex="-1" aria-hidden="true">${art}</a>` : `<div class="cover-link">${art}</div>`;
    const tip = esc([nameOf(b), b.author].filter(Boolean).join(" — "));
    const title = b.ready ? `<a class="tlink" href="?book=${esc(b.slug)}" title="${tip}"><span class="t">${esc(nameOf(b))}</span></a>` : `<span class="t" title="${tip}">${esc(nameOf(b))}</span>`;
    const { where, said } = progressOf(b, true);
    const audioMark = b.has_audio ? `<span class="au" title="С аудио">${iconSvg("audio")}</span>` : "";
    let line, act = icon("gear", iconSvg("gear"), "Текст, аудио, статус");
    if (loading) {
      const stage = job?.running && job.stage ? " · " + job.stage : "";
      line = confirmStop === b.slug ? `<span class="m ask">отменить? <button data-act="stopYes">да</button> · <button data-act="stopNo">нет</button></span>`
        : `<span class="m"><span class="spin"></span>${b.building && b.ready ? "заменяю" : "загружается"}${esc(stage)}</span>`;
      act = confirmStop === b.slug ? "" : icon("stopJob", iconSvg("close"), "Отменить загрузку");
    } else if (fail) line = `<span class="m warn" title="${esc(fail.log[fail.log.length - 1] || "код " + fail.exit)}">не загрузилось</span>`;
    else if (!b.ready) line = '<span class="m warn">не загрузилась до конца</span>';
    else if (b.fragment_note) line = `<span class="m warn" title="в конце текста «${esc(b.fragment_note)}»">фрагмент</span>`;
    else line = `<span class="m" title="${esc(where)}">${said ? "" : audioMark}${esc(where)}</span>`;
    const tile = `<div class="card tile${opened ? " on" : ""}${loading ? " loading" : ""}" ${opened ? "data-for" : "data-key"}="${esc(b.slug)}">${link}${title}<div class="tl">${line}${act}</div></div>`;
    return opened ? tile + openHtml(b, true) : tile;
  }
  function shellTileHtml(w) {
    const opened = open === w.id, s = searching.get(w.id) || {}, n = openableCount(w);
    const said = s.busy ? '<span class="spin"></span>ищу…' : n ? esc(plural(n, "издание", "издания", "изданий")) : s.failed ? "ошибка" : w.unopenable ? "не открыть" : w.searched ? "не нашлось" : "";
    const tile = `<div class="card tile shell${opened ? " on" : ""}" ${opened ? "data-for" : "data-key"}="${esc(w.id)}"><div class="cover-link" data-act="gear">${plate(w.title, "без текста", " empty")}</div>
      <span class="t" data-act="gear" title="${esc(w.title)}">${esc(w.title)}</span><div class="tl"><span class="m">${said}</span>${icon("gear", iconSvg("gear"), "Издания, своя ссылка или файл")}</div></div>`;
    return opened ? tile + openHtml(w, true) : tile;
  }
  function shellHtml(w) {
    if (open === w.id) return openHtml(w);
    return `<div class="card shell" data-key="${esc(w.id)}"><div class="cover empty" data-act="gear">${esc(w.title.slice(0, 1))}</div>
      <div class="body" data-act="gear" title="Издания, своя ссылка или файл"><div class="t">${esc(w.title)}<span class="tag">без текста</span></div>${w.author ? `<div class="m">${esc(w.author)}</div>` : ""}${briefHtml(w)}</div>${actsHtml(w)}</div>`;
  }

  // ---- finding and adding: one line above the shelves, always there. It filters the library as it is
  // typed; ↵ opens the book picked with ↑/↓ (the first by default), or, when the library has no such book,
  // saves the title and asks the catalogs for it at once ----
  const omni = $("#omni-input");
  let query = "", sel = 0;
  const matches = (x) => !query || norm((x.title || "") + " " + (x.author || "") + " " + (x.translator || "")).includes(norm(query));
  const clearOmni = () => { omni.value = ""; query = ""; sel = 0; };
  function addRowHtml(any) {
    const row = (act, title, hint) => `<div class="card add" role="button" tabindex="0" data-act="${act}"><div class="body"><div class="t"><b>＋</b>${title}</div><div class="m">${hint}</div></div></div>`;
    if (isUrl(query)) return row("link", "Загрузить книгу по ссылке", "↵ · название возьму из книги");
    return row("save", `Найти «${esc(query)}» в каталогах`, `${any ? "⌘↵" : "↵ · в библиотеке такой нет"} · появится карточка «без текста» с найденными изданиями`);
  }
  const EMPTY_HTML = `<div class="lib-empty"><p class="le-t">Пока нет книг</p>
    <p class="muted small">Напиши название — найду в каталогах.<br>Или перетащи файл: fb2, epub, pdf, txt.</p>
    <button type="button" class="btn" data-act="pickFile">Выбрать файл</button></div>`;
  // shells whose title is loading into a book are that book's card until the load ends
  const shellsShown = () => wishes.filter((w) => !(w.slug && books.some((b) => b.slug === w.slug)));
  // paint() lays out what is already loaded; renderLibrary() fetches first
  function paint() {
    // the player rides along to the redrawn row: a repaint elsewhere must not cut a listen short
    const pl = player(); pl?.remove();
    const draft = keepDraft(), focus = keepFocus(), unfolded = keepUnfolded();
    const byActivity = (a, b) => (b.at || 0) - (a.at || 0) || (a.title || "").localeCompare(b.title || "", "ru");
    // the covers are a way to see the rest of the library: «Читаю сейчас» and «Прочитанные» stay cards
    const covers = settings.libView === "covers";
    const entry = (b, tile = false) => ({ at: b.state.opened || b.added || 0, title: nameOf(b), html: tile ? tileHtml(b) : cardHtml(b) });
    // while a query is typed everything matching sits in one list under the line, so nothing hides above it
    const hits = books.filter(matches);
    const reading = query ? [] : hits.filter((b) => statusOf(b) === "reading").map((b) => entry(b)).sort(byActivity);
    const doneBooks = query ? [] : hits.filter((b) => statusOf(b) === "done")
      .sort((a, b) => (b.state.finishedOn || "").localeCompare(a.state.finishedOn || "") || nameOf(a).localeCompare(nameOf(b), "ru"));
    const rest = [...(query ? hits : hits.filter((b) => !["reading", "done"].includes(statusOf(b)))).map((b) => entry(b, covers)),
      ...shellsShown().filter(matches).map((w) => ({ at: +w.id.slice(1) || 0, title: w.title, html: covers ? shellTileHtml(w) : shellHtml(w) }))].sort(byActivity);
    const any = reading.length + rest.length + doneBooks.length > 0;
    $("#lib-title").textContent = reading.length ? "Остальные" : "Библиотека";
    $("#reading-section").hidden = !reading.length;
    $("#reading-list").innerHTML = reading.map((x) => x.html).join("");
    // everything is read or being read: no empty heading for the rest
    $("#library-section").hidden = !query && !rest.length && any;
    $("#lib-view").hidden = !rest.length;
    document.querySelectorAll("#lib-view button").forEach((v) => v.setAttribute("aria-pressed", String(v.dataset.v === (covers ? "covers" : "list"))));
    $("#library-list").classList.toggle("covers", covers);
    // an empty library: no heading, one block that says the two ways in and offers the file picker
    $("#library-section").classList.toggle("empty", !query && !any);
    $("#library-list").innerHTML = rest.map((x) => x.html).join("") + (query ? addRowHtml(any) : !any ? EMPTY_HTML : "");
    const year = String(new Date().getFullYear()), thisYear = doneBooks.filter((b) => (b.state.finishedOn || "").startsWith(year)).length;
    $("#done-section").hidden = !doneBooks.length;
    $("#done-n").textContent = doneBooks.length;
    $("#done-year").textContent = thisYear ? `в ${year} — ${thisYear}` : "";
    $("#done-fold").setAttribute("aria-expanded", String(showDone));
    $("#done-list").hidden = !showDone;
    $("#done-list").innerHTML = showDone ? doneBooks.map((b) => cardHtml(b)).join("") : "";
    markSel();
    restoreUnfolded(unfolded);
    if (listening) {
      const row = document.querySelector(`.cand[data-ref="${CSS.escape(listening.ref)}"]`);
      if (row && pl) { row.classList.add("on"); const btn = row.querySelector('[data-act="listen"]'); if (btn) { btn.textContent = "■"; btn.title = "Остановить"; } row.after(pl); }
      else stopListen();
    }
    if (!restoreDraft(draft)) restoreFocus(focus);
  }
  const matchCards = () => (query ? [...document.querySelectorAll("#library-list .card:not(.add):not(.panel)")] : []);
  function markSel() {
    const cards = matchCards();
    sel = Math.max(0, Math.min(sel, cards.length - 1));
    cards.forEach((c, i) => c.classList.toggle("sel", i === sel));
  }
  // a repaint (a finished job, a search coming back) must not wipe what is typed or chosen in the open card:
  // every field is found again by its name in the same card, so nothing moves to another card or to the search line
  const fieldOf = (el) => (el.name ? `[name="${el.name}"]` : "." + el.classList[0]);
  function keepDraft() {
    return [...document.querySelectorAll(".card.open[data-key] input")].map((el) => ({ key: el.closest(".card").dataset.key, on: fieldOf(el),
      value: el.value, files: el.type === "file" ? el.files : null, from: el.selectionStart, to: el.selectionEnd, focused: document.activeElement === el }));
  }
  function restoreDraft(all) {
    let focused = false;
    for (const d of all) {
      const el = document.querySelector(`.card.open[data-key="${CSS.escape(d.key)}"] ${d.on}`);
      if (!el) continue;
      if (!d.files) el.value = d.value;
      else if (d.files.length) { el.files = d.files; el.dispatchEvent(new Event("change", { bubbles: true })); }  // the label names it again
      if (d.focused) { el.focus(); if (!d.files) el.setSelectionRange(d.from, d.to); focused = true; }
    }
    return focused;
  }
  // editions unfolded to compare stay unfolded: a <details> is known by its card, section and summary
  const foldOf = (el) => [el.closest(".card").dataset.key, el.closest(".sec")?.querySelector("h5")?.firstChild?.textContent, el.querySelector("summary")?.textContent].join("|");
  const keepUnfolded = () => [...document.querySelectorAll(".card.open[data-key] details[open]")].map(foldOf);
  const restoreUnfolded = (all) => document.querySelectorAll(".card.open[data-key] details:not([open])").forEach((el) => { if (all.includes(foldOf(el))) el.open = true; });
  // a repaint rebuilds the cards: the keyboard stays where it was — the same button of the same card, the
  // «no» of a question that just appeared, or the card itself when the button is gone
  function keepFocus() {
    const a = document.activeElement, card = a?.closest?.(".card");
    return card && !card.classList.contains("add") ? { key: card.dataset.key || card.dataset.for, tile: card.classList.contains("tile"), act: a.dataset.act, v: a.dataset.v, ref: a.closest(".cand")?.dataset.ref } : null;
  }
  function restoreFocus(f) {
    const k = f && CSS.escape(f.key);
    const card = f && ((f.tile && document.querySelector(`.card.tile[data-for="${k}"], .card.tile[data-key="${k}"]`)) || document.querySelector(`.card[data-key="${k}"]`));
    if (!card) return;
    const el = card.querySelector('[data-act="delNo"], [data-act="stopNo"], [data-act="audioNo"]')
      || (f.ref && card.querySelector(`.cand[data-ref="${CSS.escape(f.ref)}"] [data-act="${f.act}"]`))
      || (f.v && card.querySelector(`[data-act="${f.act}"][data-v="${CSS.escape(f.v)}"]`))
      || (f.act && card.querySelector(`[data-act="${{ delNo: "del", delYes: "del", stopNo: "stopJob" }[f.act] || f.act}"]`))
      || ['button[data-act="gear"]', ".tlink", "[data-act]"].map((s) => card.querySelector(s)).find(Boolean);
    el?.focus({ preventScroll: true });
  }
  async function renderLibrary() {
    [books, wishes] = await Promise.all([fetch("/api/books").then((r) => r.json()), api("GET", "/api/wishlist").catch(() => wishes)]);
    loaded = true;
    paint();
    // nothing to find yet: the line is the only move, so it takes the keyboard
    if (!books.length && !wishes.length && document.activeElement === document.body) omni.focus();
    headerLine(books.filter((b) => statusOf(b) === "reading"), books);
  }
  // the book picked in the line: a ready one opens, any other opens its card
  function openPicked(card) {
    const x = card && entryOf(card);
    if (!x) return;
    if (!isShell(x) && x.ready) { location.href = "?book=" + x.slug; return; }
    clearOmni();
    (open === idOf(x) ? Promise.resolve(paint()) : ACTIONS.gear(x)).then(() => document.querySelector(`.card[data-key="${CSS.escape(idOf(x))}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" }));
  }
  omni.addEventListener("input", () => { query = omni.value.trim(); sel = 0; paint(); });
  omni.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.stopPropagation(); const had = !!query; clearOmni(); omni.blur(); if (had) paint(); return; }
    if ((e.key === "ArrowDown" || e.key === "ArrowUp") && query) { e.preventDefault(); sel += e.key === "ArrowDown" ? 1 : -1; markSel(); matchCards()[sel]?.scrollIntoView({ block: "nearest" }); return; }
    if (e.key !== "Enter" || !query) return;
    e.preventDefault();
    const cards = matchCards();
    if (isUrl(query)) addByLink(query);
    else if (!cards.length || e.metaKey || e.ctrlKey) addTitle(query);
    else openPicked(cards[sel]);
  });
  addEventListener("keydown", (e) => {
    if (e.key === "Escape" && listening) { stopListen(); return; }  // the first Esc silences, the next closes
    if (e.key === "Escape") {
      prefs.hidden = true;
      const no = document.querySelector(".ask .no");
      if (no) { no.click(); return; }  // a question is answered «no» before anything closes
      if (confirmDel || confirmStop || audioConfirm) { confirmDel = confirmStop = audioConfirm = null; paint(); return; }
      if (renaming || finding) { renaming = finding = null; paint(); } else if (open) { const was = open; closeCard(); paint(); document.querySelector(`.card[data-key="${CSS.escape(was)}"] button[data-act="gear"]`)?.focus(); }
    }
    if (e.key === "/" && !(e.target instanceof Element && e.target.matches("input, textarea, select"))) { e.preventDefault(); omni.focus(); }
  });

  // ---- jobs: background pipeline runs; a card shows "loading" while its job runs and the error if it fails ----
  let jobsTimer = 0;
  async function pollJobs() {
    const was = jobs;
    jobs = await fetch("/api/jobs").then((r) => r.json()).catch(() => jobs);
    const running = Object.values(jobs).some((j) => j.running);
    clearTimeout(jobsTimer);
    if (running) jobsTimer = setTimeout(pollJobs, 3000);
    // a book's loading, ready and failed come with the books: a job that ended (or began) fetches them again,
    // one still running only moves its stage
    const keys = Object.keys({ ...was, ...jobs });
    if (loaded && keys.some((k) => !!was[k]?.running !== !!jobs[k]?.running)) renderLibrary();
    else if (loaded && keys.some((k) => was[k]?.stage !== jobs[k]?.stage)) paint();
  }
  let adding = false;  // one /api/add at a time: a double click must not start a second job
  const stopping = new Set();  // slugs whose load is being called off
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
  const saveHits = (slug, found) => { hitsCache[slug] = found; return api("PUT", `/api/hits/${slug}`, { hits: found.hits || [], author_hits: found.author_hits || null, query: found.query || "", unopenable: found.unopenable || 0 }).catch(() => {}); };

  // ---- adding: a title becomes a shell card; a link or a dropped file loads right away ----
  // A title typed in the line: saved as a card «без текста» that opens and asks the catalogs at once, so
  // ↵ is the whole way from a name to its editions. The bookmarklet only saves (`search` false).
  async function addTitle(title, search = true) {
    const have = books.find((b) => norm(b.title) === norm(title));
    if (have) { toast(`«${have.title}» уже в библиотеке`); clearOmni(); paint(); return; }
    let w = wishes.find((x) => norm(x.title) === norm(title));
    if (w) toast(`«${w.title}» уже сохранена`);
    else { wishes = await api("POST", "/api/wishlist", { title }); w = wishes.find((x) => norm(x.title) === norm(title)); if (!search) toast("Сохранено: " + title); }
    clearOmni();
    if (!w) { paint(); return; }
    if (!search) paint();
    else if (w.searched && openableCount(w)) { if (open === w.id) paint(); else await ACTIONS.gear(w); }
    else await ACTIONS.find(w);
    document.querySelector(`.card[data-key="${CSS.escape(w.id)}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
  async function addByLink(url) {
    if (await startAdd({ text_url: url })) { clearOmni(); renderLibrary(); }  // the card shows up loading, that is the message
  }
  const TEXT_FILE = /\.(fb2\.zip|zip|fb2|epub|pdf|txt|html?)$/i;
  const isAudio = (f) => f.type.startsWith("audio/") || /\.(m4b|m4a|mp3)$/i.test(f.name);  // an .m4b may come without a type
  async function addByFile(file) {
    if (await startAdd({ title: file.name.replace(TEXT_FILE, ""), text_file: file })) renderLibrary();
  }
  // only what the pipeline reads becomes a book; the rest is named, not loaded
  async function addFiles(files) {
    const skip = files.filter((f) => !TEXT_FILE.test(f.name));
    if (skip.length) toast("Не открыть: " + skip.map((f) => f.name).join(", "));
    for (const f of files) if (TEXT_FILE.test(f.name)) await addByFile(f);
  }
  ["dragenter", "dragover"].forEach((ev) => addEventListener(ev, (e) => { if (e.dataTransfer?.types.includes("Files")) { e.preventDefault(); document.body.classList.add("dropping"); } }));
  ["dragleave", "drop"].forEach((ev) => addEventListener(ev, (e) => { if (ev === "drop" || e.relatedTarget == null) document.body.classList.remove("dropping"); }));
  // a file dropped on an open card fills its own «или файл» (text or audio by its type); anywhere else every
  // file becomes a book, one after another (one /api/add at a time)
  addEventListener("drop", async (e) => {
    if (!e.dataTransfer?.files?.length) return;
    e.preventDefault();
    const files = [...e.dataTransfer.files], card = e.target instanceof Element && e.target.closest(".card.open");
    if (card) {
      const f = files[0], audio = isAudio(f);
      const input = card.querySelector(audio ? "[name=audio_file]" : "[name=text_file]");
      if (!audio && !TEXT_FILE.test(f.name)) { toast("Не открыть: " + f.name); return; }
      if (!input) { toast("Сначала нужен текст"); return; }  // a card without text has no audio yet
      const dt = new DataTransfer(); dt.items.add(f); input.files = dt.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
      if (files.length > 1) toast("Взят один файл: " + f.name);
      return;
    }
    addFiles(files);
  });

  // ---- library search for a card: the result stays with it (wishlist item or hits.json) ----
  function stopSearch(x) {
    const s = searching.get(idOf(x));
    if (!s?.busy) return;
    s.stopped = true;
    s.ctrl.abort();
    // the server keeps retrying the mirrors otherwise: tell it to stop as well
    api("POST", "/api/search/cancel", { id: s.sid }).catch(() => {});
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
    const ctrl = new AbortController(), sid = Math.random().toString(36).slice(2);
    const state0 = { busy: true, t0, ctrl, sid };
    searching.set(id, state0); paint();
    const ticker = setInterval(() => { const el = document.querySelector(`.card[data-key="${CSS.escape(id)}"] .secs`); if (el) el.textContent = Math.round((Date.now() - t0) / 1000); }, 1000);
    // two rounds of up to 60 s on the server (a mirror gets several tries), plus the reading
    const killer = setTimeout(() => { ctrl.abort(); api("POST", "/api/search/cancel", { id: sid }).catch(() => {}); }, 140000);
    let res = null, state = null;
    try { res = await fetch(`/api/search?q=${q(query)}&id=${sid}`, { signal: ctrl.signal }).then((r) => r.json()); if (res.error) throw new Error(res.error); }
    catch (e) { res = null; state = state0.stopped ? null : { status: e.name === "AbortError" ? "библиотеки не ответили, попробуй позже" : "поиск не удался: " + esc(e.message), failed: true }; }
    finally { clearInterval(ticker); clearTimeout(killer); }
    if (res && !state0.stopped) {
      // the reader may have asked again while this was in flight: only the live search saves
      const failed = (res.errors || []).map((e) => SOURCE[e.split(":")[0]] || e.split(":")[0]).filter((v, i, a) => a.indexOf(v) === i);
      const failedNote = failed.length ? `${failed.join(", ")} не ответил${failed.length > 1 ? "и" : ""}` : "";
      const any = res.hits.length || res.author?.hits?.length;
      const said = [res.note, failedNote].filter(Boolean).join(" · ");
      // nothing found while a library was down is not "no such book": say what happened instead
      const retry = '<button class="link-btn" data-act="find">повторить</button>';
      const shut = res.unopenable || 0;
      state = any
        ? (said ? (failedNote ? { status: esc(said) + retry } : { note: esc(said) }) : null)
        : shut ? null  // the rows say «нашлось, но только в форматах, которые не открыть»
        : failed.length ? { status: esc(`не нашлось в ${SOURCES_N - failed.length} из ${SOURCES_N} каталогов; ${failedNote}`) + retry, failed: true }
        : { status: `не нашлось ни в одном из ${SOURCES_N} каталогов` };
      const found = { hits: res.hits, author_hits: res.author, query, unopenable: shut };  // the query stays with the card, to search again from
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
  const closeCard = () => { open = renaming = finding = audioConfirm = confirmDel = null; stopListen(); };
  // load a picked edition or an own link/file: a shell becomes the book, a ready book gets its text replaced
  async function loadText(x, fields) {
    // the catalog names the book: a picked edition brings its own title, an own file keeps the card's
    if (isShell(x)) Object.assign(fields, { title: fields.title || x.title, author: fields.author || x.author });
    else Object.assign(fields, { slug: x.slug, replace: "1" });
    const s = await startAdd(fields);
    if (!s) return;
    closeCard();
    // the title stays, tied to the book it loads into: the server lets it go once the load succeeds, and
    // a load called off or failed shows it again with its editions
    if (isShell(x)) { await saveHits(s, { hits: x.hits, author_hits: x.author_hits, query: queryOf(x), unopenable: x.unopenable }); stopSearch(x); searching.delete(x.id); await api("PUT", "/api/wishlist/" + x.id, { slug: s }).catch(() => {}); }
    renderLibrary();
  }
  // a card names its book by data-key; a cover whose card is open, by data-for
  const entryOf = (card) => { const k = card.dataset.key || card.dataset.for; return books.find((b) => b.slug === k) || wishes.find((w) => w.id === k); };

  // ---- actions: every button carries data-act; the card it sits in gives the book ----
  const ACTIONS = {
    link: () => addByLink(query),
    pickFile: () => $("#lib-file").click(),  // the files chosen load as dropped ones do (the change handler below)
    save: () => addTitle(query),
    // The status is said in words in the open card; ▶ is only ever «open the book». «Прочитана» adds today to
    // the days the book was read; leaving it for «Читаю» or «Отложена» takes the last day off again (the mark
    // was a mistake). The whole list is written, as the merge takes it whole.
    status: async (x, btn) => {
      const to = btn.dataset.v, from = statusOf(x);
      if (to === from) return;
      const at = Date.now(), days = [...(x.state.finished || [])].sort();
      const patch = { shelf: to, shelfAt: at };
      if (to === "done" && !days.includes(today())) Object.assign(patch, { finished: [...days, today()], finishedAt: at });
      if (from === "done" && days.length) Object.assign(patch, { finished: days.slice(0, -1), finishedAt: at });
      const r = await api("PUT", `/api/state/${x.slug}`, patch).catch((err) => ({ error: String(err) }));
      if (r.error) toast("Не сохранилось: " + r.error);
      renderLibrary();
    },
    // read again: back to «Читаю» from the beginning; the days it was read stay
    reread: async (x) => {
      const at = Date.now();
      const r = await api("PUT", `/api/state/${x.slug}`, { shelf: "reading", shelfAt: at, pos: 0, posAt: at, sent: 0, sentAt: at, sentPct: 0 }).catch((err) => ({ error: String(err) }));
      if (r.error) toast("Не сохранилось: " + r.error);
      renderLibrary();
    },
    view: (x, btn) => { settings.libView = btn.dataset.v; persistSettings(); paint(); },
    fold: () => { showDone = !showDone; store.set("rs:showDone", showDone); paint(); },
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
      // a control character pasted in would break book.toml
      const title = btn.closest(".head").querySelector(".rename-input").value.replace(/[\x00-\x1f\x7f]/g, " ").replace(/\s+/g, " ").trim();
      if (!title || title === x.title) { renaming = null; paint(); return; }
      // the book's own shell (the title it is loading for) is not another book
      const taken = [...books, ...wishes].some((y) => idOf(y) !== idOf(x) && !(y.slug && y.slug === x.slug) && norm(y.title) === norm(title));
      if (taken) { toast(`«${title}» уже в библиотеке`); return; }  // stay in the field, the name is free to fix
      renaming = null;
      // the old search result belongs to the old name: it goes, so the card asks to search again
      const r = isShell(x)
        ? await api("PUT", "/api/wishlist/" + x.id, { title, searched: "", query: "", hits: [], author_hits: null }).catch((err) => ({ error: String(err) }))
        : await api("PUT", "/api/books/" + x.slug, { title }).catch((err) => ({ error: String(err) }));
      if (r.error) { toast("Ошибка: " + r.error); paint(); return; }
      if (isShell(x)) wishes = r;
      // the query that found the old name goes with it; the server clears the saved copy, this
      // drops the one in hand so the field offers the new name at once
      if (hitsCache[x.slug]) hitsCache[x.slug].query = "";
      stopSearch(x); searching.delete(idOf(x));
      renderLibrary();
    },
    del: (x) => { confirmDel = idOf(x); paint(); },
    delNo: () => { confirmDel = null; paint(); },
    delYes: async (x) => {
      confirmDel = null;
      renaming = finding = null;
      stopSearch(x);  // nothing left to answer: the round in flight must not write to a card that is gone
      paint();  // the question goes before the answer comes: a second click has nothing to press
      if (isShell(x)) { wishes = await api("DELETE", "/api/wishlist/" + x.id); paint(); return; }
      const r = await api("DELETE", "/api/books/" + x.slug).catch((err) => ({ error: String(err) }));
      if (r.error) toast("Ошибка: " + r.error);
      else { closeCard(); delete jobs[x.slug]; delete hitsCache[x.slug]; audioFinding.delete(x.slug); renderLibrary(); }  // a book added again under its slug starts afresh
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
    // calling a load off is asked first: an alignment or a long recording may be hours in
    stopJob: (x) => { confirmStop = x.slug; paint(); },
    stopNo: () => { confirmStop = null; paint(); },
    // stopping may take the server seconds: the question goes at once and a second «да» sends nothing
    stopYes: async (x) => {
      confirmStop = null; paint();
      if (stopping.has(x.slug)) return;
      stopping.add(x.slug);
      const r = await api("DELETE", "/api/jobs/" + x.slug).catch((err) => ({ error: String(err) }));
      stopping.delete(x.slug);
      if (r.error) toast("Не вышло отменить: " + r.error);
      else { delete jobs[x.slug]; toast("Загрузка отменена"); }
      renderLibrary();
    },
    // a failure is on record until it is dismissed (the server keeps the job's log across restarts)
    dismiss: async (x) => { await api("DELETE", "/api/jobs/" + x.slug).catch(() => {}); delete jobs[x.slug]; renderLibrary(); },
    // the edition did not open: back to the list of editions. A new book goes, so its title shows again
    // «без текста» with what was found; a ready book keeps its text and opens its card on the list
    otherEdition: async (x) => {
      const shell = shellOf(x);
      if (!x.ready) { await api("DELETE", "/api/books/" + x.slug).catch(() => {}); delete jobs[x.slug]; }
      else await api("DELETE", "/api/jobs/" + x.slug).catch(() => {});
      await renderLibrary();
      const to = !x.ready && shell ? wishes.find((w) => w.id === shell.id) : books.find((b) => b.slug === x.slug);
      if (to) { open = null; await ACTIONS.gear(to); }
    },
    pick: (x, btn) => {
      const h = JSON.parse(btn.dataset.hit);
      const run = () => loadText(x, { title: h.title, author: h.author, translator: h.translator, year: h.year, narrator: h.narrator, text_url: h.urls.join("\n"), audio_url: isShell(x) ? h.audio || "" : "" });
      if (!isShell(x) && x.ready) askInPlace(btn, "текст заменится, место в режиме страниц начнётся сначала —", "заменить", run); else run();
    },
    own: (x, btn) => {
      const row = btn.closest(".own"), url = row.querySelector("[name=text_url]").value.trim(), file = row.querySelector("[name=text_file]").files[0];
      if (!url && !file) { toast("Нужна ссылка на текст или файл"); return; }
      if (url && file) { toast("Либо ссылка, либо файл"); return; }  // both would load as two parts of one book
      if (url && !isUrl(url)) { toast("Ссылка должна начинаться с http(s)"); return; }
      const run = () => loadText(x, { text_url: url, text_file: file });
      if (!isShell(x) && x.ready) askInPlace(btn, "текст заменится, место в режиме страниц начнётся сначала —", "заменить", run); else run();
    },
    audioGo: async (x, btn) => {
      const row = btn.closest(".own"), urls = row.querySelector("[name=audio_url]").value.trim().split(/\s+/).filter(Boolean).join("\n"), file = row.querySelector("[name=audio_file]").files[0];
      if (!urls && !file) { toast("Нужна ссылка на аудио или файл"); return; }
      if (urls && file) { toast("Либо ссылка, либо файл"); return; }
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
  // a question asked where the button was, without a repaint: what was typed or chosen in the card stays
  function askInPlace(btn, question, yes, run) {
    const ask = document.createElement("span");
    ask.className = "ask";
    ask.innerHTML = `${esc(question)} <button type="button" class="yes">${esc(yes)}</button> <button type="button" class="no">отмена</button>`;
    btn.replaceWith(ask);
    ask.querySelector(".yes").onclick = (e) => { e.stopPropagation(); ask.replaceWith(btn); run(); };
    ask.querySelector(".no").onclick = (e) => { e.stopPropagation(); ask.replaceWith(btn); btn.focus(); };
    ask.querySelector(".no").focus();
  }
  $("#library").addEventListener("click", (e) => {
    // the listening bar seeks where it is clicked
    const bar = e.target.closest(".lbar");
    if (bar && listening && isFinite(listenEl.duration)) { const r = bar.getBoundingClientRect(); listenEl.currentTime = ((e.clientX - r.left) / r.width) * listenEl.duration; return; }
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const card = btn.closest(".card");
    if (!card) { ACTIONS[btn.dataset.act]?.(null, btn); return; }  // the page's own: the view, the fold
    const x = card && !card.classList.contains("add") ? entryOf(card) : null;
    if (x || card?.classList.contains("add")) ACTIONS[btn.dataset.act]?.(x, btn);
  });
  $("#library").addEventListener("keydown", (e) => {
    if (!(e.target instanceof Element)) return;
    // the three status words: ← → move between them, ↵ or space picks
    if (e.target.matches(".stat-seg [role=radio]") && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
      e.preventDefault();
      const all = [...e.target.parentElement.querySelectorAll("[role=radio]")], i = all.indexOf(e.target);
      all[(i + (e.key === "ArrowRight" ? 1 : all.length - 1)) % all.length].focus();
      return;
    }
    if (e.target.matches(".card.add") && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); e.target.click(); return; }
    if (e.target.matches(".afind-input, .own input")) {
      if (e.key === "Enter" && e.target.matches(".afind-input")) { e.preventDefault(); e.target.closest(".afind")?.querySelector('[data-act="findAudioGo"]')?.click(); }
      if (e.key === "Escape") { e.stopPropagation(); e.target.blur(); }
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
    if (e.target.id === "lib-file") { const files = [...e.target.files]; e.target.value = ""; addFiles(files); return; }
    if (e.target.type !== "file") return;
    const l = e.target.closest("label"), name = e.target.files[0]?.name;
    if (l) l.querySelector("u").textContent = name || "файл";
    l?.classList.toggle("chosen", !!name);
  });

  pollJobs().then(renderLibrary).then(() => {
    const wishParam = new URLSearchParams(location.search).get("wish");
    if (!wishParam) return;
    history.replaceState(null, "", location.pathname);
    addTitle(wishParam.replace(/\s+[-–—|].*$/, "").trim() || wishParam, false);
  });
})();
