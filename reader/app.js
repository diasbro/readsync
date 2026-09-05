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

  // ---------------- settings ----------------
  const DEFAULTS = { font: 20, lh: 1.65, width: 42, family: "serif", theme: "light", word: true, dim: false, scroll: "zone", clickWord: false, speed: 1 };
  const settings = Object.assign({}, DEFAULTS, store.get("rs:settings", {}));
  function applySettings() {
    const r = document.documentElement.style;
    r.setProperty("--font-size", settings.font + "px");
    r.setProperty("--lh", settings.lh);
    r.setProperty("--width", settings.width + "rem");
    r.setProperty("--family", settings.family === "sans" ? "var(--sans)" : "var(--serif)");
    document.documentElement.dataset.theme = settings.theme;
    document.body.classList.toggle("word-hl", !!settings.word);
    document.body.classList.toggle("dim", !!settings.dim);
    store.set("rs:settings", settings);
  }
  applySettings();

  // ---------------- library ----------------
  if (!slug) {
    $("#library").hidden = false;
    fetch("/api/books").then((r) => r.json()).then((books) => {
      const list = $("#library-list");
      if (!books.length) { list.innerHTML = '<p class="muted">Нет книг. Добавь папку в books/ с book.toml.</p>'; return; }
      list.innerHTML = books.map((b) => {
        const pos = store.get("rs:pos:" + b.slug, 0), dur = store.get("rs:dur:" + b.slug, 0);
        const pct = dur ? Math.round((pos / dur) * 100) : 0;
        const meta = [b.author, b.narrator ? "читает " + b.narrator : null, b.ready ? (b.timing_source === "mms" ? "точная синхронизация" : "синхронизация по субтитрам") : "не готово"].filter(Boolean).join(" · ");
        return `<a class="card" href="?book=${esc(b.slug)}"><div class="t">${esc(b.title || b.slug)}</div><div class="m">${esc(meta)}</div>
          <div class="bar"><i style="width:${pct}%"></i></div><div class="m">${pct ? "прочитано " + pct + "% · " + fmt(pos) : "не начато"}</div></a>`;
      }).join("");
    });
    return;
  }

  // ---------------- book state ----------------
  const app = $("#app"); app.hidden = false;
  const audio = $("#audio"), textEl = $("#text");
  let book, wB, wT0, wT1, wS, sFirst, sLast, sBlock, chapStartWord = [], chapStartTime = [], duration = 0;
  let curWord = -1, curSent = -1, curBlock = -1, curChap = -1;
  let userScrolled = false, wordEls = [], sentEls = [], blockEls = [];

  async function load() {
    const [meta, bookJ, timingJ] = await Promise.all([
      fetch("/api/books").then((r) => r.json()).then((bs) => bs.find((b) => b.slug === slug)),
      fetch(`/books/${slug}/book.json`).then((r) => r.json()),
      fetch(`/books/${slug}/timing.json`).then((r) => (r.ok ? r.json() : null)),
    ]);
    if (!meta) throw new Error("книга не найдена");
    if (!timingJ) throw new Error("нет timing.json — запусти пайплайн");
    book = bookJ; duration = timingJ.duration;
    document.title = book.title + " — readsync";
    $("#book-title").textContent = book.title;
    audio.src = `/books/${slug}/${meta.audio}`;
    buildIndex(timingJ.words);
    render(timingJ.words);
    buildToc();
    $("#loading").hidden = true;
    const pos = store.get("rs:pos:" + slug, 0);
    audio.addEventListener("loadedmetadata", () => {
      if (isFinite(audio.duration)) { duration = audio.duration; store.set("rs:dur:" + slug, duration); }
      if (pos > 0 && pos < duration - 5) audio.currentTime = pos;
      $("#progress").max = duration;
      drawTicks();
      update(true);
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
    for (let b = 0; b < book.blocks.length; b++) {
      for (let k = 0; k < book.blocks[b].sentences.length; k++) { sentIdx[b].push(sFirst.length); sFirst.push(-1); sLast.push(-1); sBlock.push(b); }
    }
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
    ol.innerHTML = book.chapters.map((c, i) => c.hidden ? "" : `<li class="l${c.level}" data-ch="${i}"><span>${esc(c.title)}</span><span class="tt">${isFinite(chapStartTime[i]) ? fmt(chapStartTime[i]) : ""}</span></li>`).join("");
    ol.addEventListener("click", (e) => {
      const li = e.target.closest("li"); if (!li) return;
      const i = +li.dataset.ch;
      if (isFinite(chapStartTime[i])) seek(chapStartTime[i]);
      closeDrawers();
    });
  }
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
  function update(force) {
    const t = audio.currentTime;
    const i = wordAt(t);
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
  function scrollToCurrent(force) {
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
      scrollTo({ top: y, behavior: "smooth" });
    }
  }

  // 10 Hz sync loop while playing (cheap: one binary search + a few class toggles per tick)
  let tick = 0;
  audio.addEventListener("play", () => { clearInterval(tick); tick = setInterval(() => update(false), 100); $("#btn-play").textContent = "❚❚"; session.start(); });
  audio.addEventListener("pause", () => { clearInterval(tick); update(true); $("#btn-play").textContent = "▶"; session.stop(); savePos(); });
  audio.addEventListener("seeked", () => update(true));
  audio.addEventListener("ratechange", () => update(true));
  audio.addEventListener("error", () => { $("#loading").hidden = false; $("#loading").textContent = "Ошибка аудио: " + (audio.error?.message || audio.error?.code); });
  setInterval(() => { if (!audio.paused) savePos(); }, 5000);
  addEventListener("beforeunload", () => { savePos(); session.stop(); });
  function savePos() { store.set("rs:pos:" + slug, audio.currentTime); }

  // ---------------- controls ----------------
  function seek(t) { audio.currentTime = Math.max(0, Math.min(duration || 1e9, t)); userScrolled = false; $("#return-pill").hidden = true; update(true); scrollToCurrent(true); }
  function toggle() { audio.paused ? audio.play() : audio.pause(); }
  function sentStart(si) { return si >= 0 && sFirst[si] >= 0 ? wT0[sFirst[si]] : null; }
  function prevSentence() {
    // if we are >1.5s into the sentence, restart it; otherwise go to previous
    let si = curSent; const st = sentStart(si);
    if (st != null && audio.currentTime - st > 1.5) return seek(st);
    for (let k = si - 1; k >= 0; k--) if (sFirst[k] >= 0) return seek(wT0[sFirst[k]]);
  }
  function nextSentence() { for (let k = curSent + 1; k < sFirst.length; k++) if (sFirst[k] >= 0) return seek(wT0[sFirst[k]]); }
  function repeatSentence() { const st = sentStart(curSent); if (st != null) { seek(st); if (audio.paused) audio.play(); } }
  function setSpeed(v) { v = Math.min(2, Math.max(0.5, +v)); audio.playbackRate = v; settings.speed = v; $("#speed").value = String(v); store.set("rs:settings", settings); }

  $("#btn-play").onclick = toggle;
  $("#btn-back").onclick = () => seek(audio.currentTime - 10);
  $("#btn-fwd").onclick = () => seek(audio.currentTime + 10);
  $("#btn-prev-sent").onclick = prevSentence;
  $("#btn-next-sent").onclick = nextSentence;
  $("#speed").onchange = (e) => setSpeed(e.target.value);
  $("#btn-focus").onclick = () => { settings.dim = !settings.dim; applySettings(); syncSettingsUI(); };
  let seekingUI = false;
  const prog = $("#progress");
  prog.addEventListener("input", () => { seekingUI = true; $("#time-cur").textContent = fmt(+prog.value); });
  prog.addEventListener("change", () => { seekingUI = false; seek(+prog.value); });

  textEl.addEventListener("click", (e) => {
    const nref = e.target.closest(".nref");
    if (nref) { showNote(nref); e.stopPropagation(); return; }
    const w = e.target.closest(".w"), s = e.target.closest(".s");
    if (settings.clickWord && w) return seek(wT0[+w.dataset.w]);
    if (s) { const st = sentStart(+s.dataset.s); if (st != null) seek(st); }
  });

  // user scroll detection
  const onUserScroll = () => { if (settings.scroll === "off") return; userScrolled = true; $("#return-pill").hidden = false; };
  addEventListener("wheel", onUserScroll, { passive: true });
  addEventListener("touchmove", onUserScroll, { passive: true });
  $("#return-pill").onclick = () => { userScrolled = false; $("#return-pill").hidden = true; scrollToCurrent(true); };

  // keyboard
  addEventListener("keydown", (e) => {
    if (e.target.matches("input, select, textarea")) return;
    const k = e.key;
    if (k === " ") { e.preventDefault(); toggle(); }
    else if (k === "ArrowLeft") { e.preventDefault(); e.shiftKey ? seek(audio.currentTime - 10) : prevSentence(); }
    else if (k === "ArrowRight") { e.preventDefault(); e.shiftKey ? seek(audio.currentTime + 10) : nextSentence(); }
    else if (k === "r") repeatSentence();
    else if (k === "[") setSpeed(audio.playbackRate - 0.1);
    else if (k === "]") setSpeed(audio.playbackRate + 0.1);
    else if (k === "f") { settings.dim = !settings.dim; applySettings(); syncSettingsUI(); }
    else if (k === "t") toggleDrawer("#toc");
    else if (k === "a") { userScrolled = false; $("#return-pill").hidden = true; scrollToCurrent(true); }
    else if (k === "Escape") { closeDrawers(); $("#note-pop").hidden = true; $("#sprint-menu").hidden = true; }
  });
  if ("mediaSession" in navigator) {
    navigator.mediaSession.setActionHandler("play", () => audio.play());
    navigator.mediaSession.setActionHandler("pause", () => audio.pause());
    navigator.mediaSession.setActionHandler("seekbackward", () => seek(audio.currentTime - 10));
    navigator.mediaSession.setActionHandler("seekforward", () => seek(audio.currentTime + 10));
    navigator.mediaSession.setActionHandler("previoustrack", prevSentence);
    navigator.mediaSession.setActionHandler("nexttrack", nextSentence);
  }

  // ---------------- drawers / settings ----------------
  function toggleDrawer(sel) { const el = $(sel); const open = el.hidden; closeDrawers(); if (open) { el.hidden = false; $("#scrim").hidden = false; if (sel === "#settings") renderStats(); } }
  function closeDrawers() { $("#toc").hidden = true; $("#settings").hidden = true; $("#scrim").hidden = true; }
  $("#btn-toc").onclick = () => toggleDrawer("#toc");
  $("#btn-settings").onclick = () => toggleDrawer("#settings");
  $("#scrim").onclick = closeDrawers;
  document.querySelectorAll("[data-close]").forEach((b) => (b.onclick = closeDrawers));
  function syncSettingsUI() {
    $("#set-font").value = settings.font; $("#set-lh").value = settings.lh; $("#set-width").value = settings.width;
    $("#set-family").value = settings.family; $("#set-word").checked = !!settings.word; $("#set-dim").checked = !!settings.dim;
    $("#set-scroll").value = settings.scroll; $("#set-click-word").checked = !!settings.clickWord;
    document.querySelectorAll("#set-theme button").forEach((b) => b.classList.toggle("on", b.dataset.v === settings.theme));
    $("#btn-focus").classList.toggle("on", !!settings.dim);
  }
  const bind = (sel, key, conv = (v) => v) => $(sel).addEventListener("input", (e) => { settings[key] = conv(e.target.type === "checkbox" ? e.target.checked : e.target.value); applySettings(); syncSettingsUI(); });
  bind("#set-font", "font", Number); bind("#set-lh", "lh", Number); bind("#set-width", "width", Number);
  bind("#set-family", "family"); bind("#set-word", "word"); bind("#set-dim", "dim"); bind("#set-scroll", "scroll"); bind("#set-click-word", "clickWord");
  $("#set-theme").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; settings.theme = b.dataset.v; applySettings(); syncSettingsUI(); });
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
  addEventListener("click", (e) => { if (!e.target.closest("#note-pop, .nref")) $("#note-pop").hidden = true; if (!e.target.closest("#sprint-menu, #btn-sprint")) $("#sprint-menu").hidden = true; });

  // ---------------- sessions & stats ----------------
  const session = {
    t0: null, w0: null,
    start() { if (this.t0 != null) return; this.t0 = Date.now(); this.w0 = curWord; },
    stop() {
      if (this.t0 == null) return;
      const sec = (Date.now() - this.t0) / 1000, words = Math.max(0, curWord - this.w0);
      this.t0 = null;
      if (sec < 2) return;
      const st = store.get("rs:stats:" + slug, { days: {} });
      const d = st.days[today()] || { sec: 0, words: 0 };
      d.sec += sec; d.words += words; st.days[today()] = d; store.set("rs:stats:" + slug, st);
      sprint.words += words;
    },
  };
  function renderStats() {
    const st = store.get("rs:stats:" + slug, { days: {} });
    const days = Object.keys(st.days).sort();
    const tot = days.reduce((a, k) => a + st.days[k].sec, 0), totW = days.reduce((a, k) => a + st.days[k].words, 0);
    const td = st.days[today()] || { sec: 0, words: 0 };
    let streak = 0; const dt = new Date();
    for (;;) { const k = dt.toISOString().slice(0, 10); if (st.days[k]?.sec > 60) { streak++; dt.setDate(dt.getDate() - 1); } else break; }
    const pct = duration ? Math.round((audio.currentTime / duration) * 100) : 0;
    $("#stats").innerHTML = `Сегодня: <b>${fmt(td.sec)}</b>, ${Math.round(td.words)} слов<br>Всего: <b>${fmt(tot)}</b>, ${Math.round(totW)} слов<br>Серия: <b>${streak}</b> дн.<br>Прогресс книги: <b>${pct}%</b> · осталось ${fmt((duration - audio.currentTime) / audio.playbackRate)}`;
  }

  // ---------------- sprint timer ----------------
  const sprint = { end: null, timer: 0, minutes: 0, words: 0, sents0: 0, stopAtSentence: false };
  $("#btn-sprint").onclick = (e) => { e.stopPropagation(); const m = $("#sprint-menu"); m.hidden = !m.hidden; $("#sprint-stop").hidden = !sprint.end; };
  $("#sprint-menu").addEventListener("click", (e) => { const b = e.target.closest("button[data-min]"); if (b) startSprint(+b.dataset.min); });
  $("#sprint-stop").onclick = () => { stopSprint(); $("#sprint-menu").hidden = true; };
  $("#sprint-close").onclick = () => { $("#sprint-done").hidden = true; };
  $("#sprint-again").onclick = () => { $("#sprint-done").hidden = true; startSprint(sprint.minutes); if (audio.paused) audio.play(); };
  function startSprint(min) {
    stopSprint(); sprint.minutes = min; sprint.end = Date.now() + min * 60000; sprint.words = 0; sprint.sents0 = curSent; sprint.startWord = curWord;
    $("#sprint-menu").hidden = true; $("#btn-sprint").classList.add("on");
    const badge = $("#sprint-badge"); badge.hidden = false;
    sprint.timer = setInterval(() => {
      const left = sprint.end - Date.now();
      if (left <= 0) { clearInterval(sprint.timer); badge.textContent = "финиш…"; badge.classList.add("ending"); if (audio.paused) finishSprint(); else sprint.stopAtSentence = true; return; }
      badge.textContent = fmt(left / 1000); badge.classList.toggle("ending", left < 60000);
    }, 500);
    if (audio.paused) audio.play();
  }
  function stopSprint() { clearInterval(sprint.timer); sprint.end = null; sprint.stopAtSentence = false; $("#sprint-badge").hidden = true; $("#sprint-badge").classList.remove("ending"); $("#btn-sprint").classList.remove("on"); }
  function finishSprint() {
    session.stop();
    const sents = Math.max(0, curSent - sprint.sents0), words = Math.max(0, curWord - Math.max(0, sprint.startWord ?? curWord));
    stopSprint();
    $("#sprint-summary").innerHTML = `${sprint.minutes} мин фокуса.<br>Прочитано: <b>${sents}</b> предложений, <b>${words}</b> слов.<br>Сделай паузу — потом ещё один.`;
    $("#sprint-done").hidden = false;
  }

  load().catch((e) => { $("#loading").textContent = "Ошибка: " + e.message; console.error(e); });
})();
