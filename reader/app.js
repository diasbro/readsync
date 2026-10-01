/* readsync reader: text + audio with synced highlighting. Shared helpers and settings live in common.js. */
(() => {
  "use strict";
  if (!slug) return;
  const pages = { on: false, spreadW: 0, total: 1, cur: 0, sent: 0, capped: false };  // page-mode state
  const FLOW_CAP = 16777216;  // the widest column flow a browser lays out, in CSS pixels, as measured
  // `pages.on` means the columns are built and a page can be turned; `hasAudio` means there is a
  // narrator to play. Both are false while the book loads, and every control asks one of them, so
  // nothing answers a key or a click before there is something to answer it with.
  // Only a change to the text's metrics moves the columns. A theme or a highlight toggle does not, and
  // laying out a long book again costs seconds, so those skip it.
  let metricsKey = "";
  onApplied = () => {
    const key = [settings.font, settings.lh, settings.width, settings.family, settings.weight].join("|");
    if (key === metricsKey) return;
    metricsKey = key;
    scheduleRelayout();
  };
  // ---------------- book state ----------------
  const app = $("#app"); app.hidden = false;
  // In the iPhone app the narrator plays natively (background, lock screen, headphones): the same
  // interface as <audio>, but the app owns the position, the sessions and the lock-screen controls.
  const native = !!window.nativeAudio;
  if (native) document.documentElement.classList.add("in-app");  // the page sits inside the iPhone app
  const audio = window.nativeAudio || $("#audio"), textEl = $("#text");
  const pgCur = $("#pg-cur"), pgTotal = $("#pg-total"), pgRead = $("#pg-read");
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
  const putState = (patch, keepalive) => send("PUT", `/api/state/${slug}`, patch, keepalive).catch(() => {});

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
      send("PUT", "/api/settings", remoteSettings).catch(() => {});
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
    // in the app the player keeps the position, not this page's cache, so the saved state always wins
    const remoteWins = typeof remote.pos === "number" && (native || (remote.posAt || 0) > store.get("rs:posAt:" + slug, 0));
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
    // a picture decodes after the text is laid out and pushes every page along: measure again
    textEl.querySelectorAll("img").forEach((im) => im.addEventListener("load", scheduleRelayout, { once: true }));
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
  // Chapters cut the bar into parts, but only while the parts stay wider than a fingertip: dozens of cuts
  // on a narrow bar turn it into a dotted line. Sections go first, then the chapters themselves.
  function drawTicks() {
    const el = $("#chapter-ticks");
    const cuts = book.chapters.map((c, i) => ({ c, at: chapStartTime[i] / duration }))
      .filter(({ c, at }) => !c.hidden && at > 0 && at < 1);
    const room = el.clientWidth / 14;
    let shown = cuts.length <= room ? cuts : cuts.filter(({ c }) => c.level <= 1);
    if (shown.length > room) shown = [];
    el.innerHTML = shown.map(({ c, at }) => `<i class="l${c.level}" style="left:${at * 100}%" title="${esc(c.title)}"></i>`).join("");
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
        if ("mediaSession" in navigator && "MediaMetadata" in window && !native) {
          navigator.mediaSession.metadata = new MediaMetadata({ title: book.chapters[ci]?.title || book.title, artist: book.author, album: book.title });
        }
        document.querySelectorAll("#toc-list li").forEach((li) => { const k = +li.dataset.ch; li.classList.toggle("cur", k === ci); li.classList.toggle("done", k < ci); });
      }
    }
    const sec = Math.floor(t);
    if (sec !== lastSec || force) {
      lastSec = sec;
      if (!seekingUI) { $("#progress").value = t; paintProgress(); }
      $("#time-cur").textContent = fmt(t);
      const chEnd = chapStartTime.slice(curChap + 1).find((x) => isFinite(x)) ?? duration;
      const rate = audio.playbackRate || 1;
      $("#time-chap").textContent = "глава −" + fmt((chEnd - t) / rate);
      $("#time-left").textContent = "−" + fmt((duration - t) / rate);
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
  audio.addEventListener("play", () => { settling = false; clearInterval(tick); tick = setInterval(() => update(false), 100); setIcon($("#btn-play"), "pause"); if (!native) session.start(); document.body.classList.add("playing"); armIdle(); armHidePlayer(); });
  audio.addEventListener("pause", () => { clearInterval(tick); update(true); setIcon($("#btn-play"), "play"); if (!native) session.stop(); savePos(); document.body.classList.remove("playing", "idle"); showPlayer(); pausedAt = Date.now(); });
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
  function armHidePlayer(ms = 1500) {
    clearTimeout(hideTimer);
    if (settings.hideUi && !pages.on) hideTimer = setTimeout(() => { if (!audio.paused) document.body.classList.add("hide-player"); }, ms);
  }
  // a finger has no hover: the bars put away come back with a tap, stay while they are used, and go again
  const touchUI = matchMedia("(hover: none)").matches;
  if (touchUI) $(".player").addEventListener("touchstart", () => armHidePlayer(4000), { passive: true });
  function showPlayer() { clearTimeout(hideTimer); document.body.classList.remove("hide-player"); $(".player").classList.remove("peek"); }
  addEventListener("mousemove", (e) => {
    if (!document.body.classList.contains("hide-player")) return;
    const player = $(".player"), peek = player.classList.contains("peek");
    const zone = peek ? innerHeight - player.offsetHeight - 8 : innerHeight - 20;  // offsetHeight: the bar may still be sliding in
    player.classList.toggle("peek", e.clientY >= zone);
  }, { passive: true });
  // focus aid: pause when the reader leaves the tab or window
  // the app keeps playing on a locked screen: leaving the page is the phone being locked, not the reader leaving
  const onLeave = () => { if (!native && settings.pauseHidden && !audio.paused) audio.pause(); };
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
    if (!posDirty || native) return;  // in the app the player saves its own position, even while the screen is locked
    const at = Date.now();
    store.set("rs:pos:" + slug, audio.currentTime); store.set("rs:posAt:" + slug, at);
    putState({ pos: audio.currentTime, posAt: at }, keepalive);
  }

  // ---------------- page mode ----------------
  // A page number is not a place in a book: it changes with the window, the font and the column count.
  // The place is always the sentence, and every relayout puts that same sentence back on the screen.
  // The content box, to the fraction of a pixel. `clientWidth` is rounded to whole pixels, and over
  // hundreds of spreads that rounding walks the text sideways out of the column.
  function contentWidth() {
    const cs = getComputedStyle(textEl);
    const off = parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight)
      + parseFloat(cs.borderLeftWidth) + parseFloat(cs.borderRightWidth);
    return textEl.getBoundingClientRect().width - off;
  }
  function pagesLayout() {
    const gap = parseFloat(getComputedStyle(textEl).columnGap) || 0;
    pages.spreadW = Math.max(1, contentWidth() + gap);
    // the flow is spreads of this pitch with no gap after the last one. Round up: a half-filled last
    // spread is still a page, and losing it would put the end of the book out of reach.
    pages.total = Math.max(1, Math.ceil((textEl.scrollWidth + gap) / pages.spreadW - 0.02));
    // A very long book in a narrow column at a large font runs past what the browser will lay out,
    // and everything after that point is piled onto the last page. Nothing here can undo it, but the
    // reader must not be left thinking the book simply ends where it stops.
    pages.capped = textEl.scrollWidth >= FLOW_CAP - pages.spreadW;
    flowCache.clear();
  }
  // where a sentence sits in the flow, measured from the start of the book. It does not change when
  // the columns are scrolled, only when they are laid out again, so one measurement per layout holds.
  const flowCache = new Map();
  function flowX(si) {
    const known = flowCache.get(si);
    if (known !== undefined) return known;
    const el = sentEls[si];
    if (!el) return 0;
    // the first rect, which is where the sentence starts. Not the leftmost: a sentence that wraps
    // has a rect at the column's left edge on its second line, and taking that would put sentences
    // out of order inside a column, which the binary search below relies on.
    const box = textEl.getBoundingClientRect();
    const r = el.getClientRects()[0] || el.getBoundingClientRect();
    const x = r.left - box.left + textEl.scrollLeft;
    flowCache.set(si, x);
    return x;
  }
  // the spread a sentence starts on. Floor and no fudge: the reader would rather see a line they have
  // already read at the top of the page than lose the one they stopped at off the left edge.
  const spreadOfSent = (si) => Math.max(0, Math.floor(flowX(si) / pages.spreadW));
  function sentAtSpread(n) {
    const x0 = n * pages.spreadW;  // the same boundary spreadOfSent uses, so the two are inverse
    let lo = 0, hi = sentEls.length - 1, ans = hi;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (flowX(mid) >= x0) { ans = mid; hi = mid - 1; } else lo = mid + 1; }
    return ans;
  }
  let sentTimer = 0;
  // Show spread `n`. `anchor` is the sentence the reader is on when the caller already knows it: a
  // relayout must keep the sentence it started from, or every resize event nudges the place a little.
  function goSpread(n, save = true, anchor = null, step = false) {
    n = Math.max(0, Math.min(pages.total - 1, n));
    const prevSent = pages.sent;
    pages.cur = n; textEl.scrollLeft = n * pages.spreadW;
    pages.sent = anchor != null ? Math.max(0, Math.min(sFirst.length - 1, anchor)) : sentAtSpread(n);
    // words read, not words skipped past: a jump to a page or a chapter is not reading
    if (save && step && pages.sent > prevSent && pages.sent - prevSent < 400) session.words += sWordsCum[pages.sent] - sWordsCum[prevSent];
    paintPager();
    $("#chapter-title").textContent = book.chapters[chapterOfSent(pages.sent)]?.title || "";
    if (save) {
      const at = Date.now(); store.set("rs:sent:" + slug, pages.sent); store.set("rs:sentAt:" + slug, at);
      clearTimeout(sentTimer); sentTimer = setTimeout(() => putState({ sent: pages.sent, sentAt: at, sentPct: Math.round((pages.sent / sFirst.length) * 100) }), 300);
    }
  }
  function goToSentence(si, save = true) {
    if (!sentEls[si]) return goSpread(0, save);  // no such sentence: the page shown and the place saved must agree
    goSpread(spreadOfSent(si), save, si);
  }
  // Turn a page. The columns are rebuilt the moment the window changes but measured only after the
  // reader's hand stops, so the numbers are checked first: a turn in that gap would use last
  // window's pitch and land between columns.
  function turn(delta) {
    if (!pages.on) return;
    const gap = parseFloat(getComputedStyle(textEl).columnGap) || 0;
    if (Math.abs(contentWidth() + gap - pages.spreadW) > 0.5) { clearTimeout(relayoutTimer); relayout(); }
    goSpread(pages.cur + delta, true, null, true);
  }
  // The columns were rebuilt (window resized, font or width changed, a webfont arrived): the reader
  // keeps their sentence and only the page number under it changes.
  function relayout() {
    if (!pages.on) return;
    pagesLayout();
    goToSentence(pages.sent, false);
  }
  let relayoutTimer = 0;
  // Laying the columns out again means laying out the whole book: on a long one that is seconds of
  // work, so a drag of the window edge waits for the reader's hand to stop instead of paying it per
  // pixel. A timer and not requestAnimationFrame, so a window resized while this tab sits in the
  // background is still caught up when the reader comes back to it.
  function scheduleRelayout() {
    if (!pages.on) return;
    clearTimeout(relayoutTimer);
    relayoutTimer = setTimeout(relayout, 150);
  }
  function saveMode(m) { const at = Date.now(); store.set("rs:mode:" + slug, m); store.set("rs:modeAt:" + slug, at); putState({ mode: m, modeAt: at }); }
  function enterPages(si, save = true) {
    if (hasAudio && !audio.paused) audio.pause();
    if (native) window.webkit.messageHandlers.audio.postMessage({ cmd: "pages", on: true });  // the lock screen must not start the narrator under a page
    pages.on = true; document.body.classList.add("pages"); $("#pager").hidden = false; setIcon($("#btn-mode"), "audio"); $("#btn-mode").title = "Вернуться к аудио (m)";
    closeDrawers(); pagesLayout(); goToSentence(si ?? pages.sent, false);
    if (save) saveMode("pages");
    session.start();
  }
  function exitPages() {
    if (native) window.webkit.messageHandlers.audio.postMessage({ cmd: "pages", on: false });
    pages.on = false; document.body.classList.remove("pages"); $("#pager").hidden = true; setIcon($("#btn-mode"), "book"); $("#btn-mode").title = "Режим книги без аудио (m)";
    session.stop(); saveMode("audio"); drawTicks();  // the bar had no width while the pages covered it
    const st = sentStart(pages.sent);
    if (st != null) seek(st); else update(true);
  }
  function toggleMode() { if (!hasAudio) return; pages.on ? exitPages() : enterPages(curSent >= 0 ? curSent : pages.sent); }
  $("#btn-mode").onclick = toggleMode;
  $("#pg-prev").onclick = () => turn(-1);
  $("#pg-next").onclick = () => turn(1);
  addEventListener("resize", scheduleRelayout);
  addEventListener("resize", () => { if (hasAudio && duration && !pages.on) drawTicks(); });
  document.fonts.addEventListener("loadingdone", scheduleRelayout);

  // ---- the page number: readable, and a field to jump from ----
  let pgTyping = false;  // while the reader is typing, a page turn must not overwrite what they wrote
  function paintPager() {
    pgTotal.textContent = pages.total;
    pgCur.style.width = String(pages.total).length + 2 + "ch";
    if (!pgTyping) pgCur.value = pages.cur + 1;
    const pct = Math.round((pages.sent / Math.max(1, sFirst.length)) * 100);
    pgRead.innerHTML = pages.capped ? "книга не помещается целиком" : `<span class="pg-word">прочитано </span>${pct}%`;
    pgRead.classList.toggle("warn", !!pages.capped);
    pgRead.title = pages.capped
      ? "В такой колонке браузер не размещает всю книгу, и её конец собран на последней странице. Сделай окно шире или шрифт мельче."
      : "";
  }
  // Take the typed page, or put the real one back when it is empty, out of range or not a number.
  function commitPage() {
    pgTyping = false;
    const n = parseInt(pgCur.value.replace(/\D+/g, ""), 10);
    if (n >= 1 && n <= pages.total && n - 1 !== pages.cur) goSpread(n - 1);
    pgCur.value = pages.cur + 1;
  }
  pgCur.addEventListener("focus", () => { pgTyping = true; pgCur.select(); });
  pgCur.addEventListener("input", () => { pgTyping = true; });  // typed into, however the focus got there
  pgCur.addEventListener("blur", commitPage);
  pgCur.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); commitPage(); pgCur.blur(); }
    else if (e.key === "Escape") { e.preventDefault(); pgTyping = false; pgCur.value = pages.cur + 1; pgCur.blur(); }
    else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      // the field stays in hand: it keeps the focus, so it writes the new page itself
      e.preventDefault();
      turn(e.key === "ArrowUp" ? -1 : 1);
      pgCur.value = pages.cur + 1; pgCur.select();
    }
  });

  // ---------------- controls ----------------
  let pausedAt = 0;
  function play() {
    if (settings.rewind && pausedAt && Date.now() - pausedAt > 8000) {
      const st = sentStart(curSent);
      if (st != null && audio.currentTime - st > 1.5) { audio.currentTime = st; update(true); }
    }
    // a refused play() is silent otherwise: the button looks dead and nothing says why
    return audio.play().catch((e) => {
      $("#loading").hidden = false;
      $("#loading").textContent = "Не запускается: " + e.message + ". Обнови страницу.";
    });
  }
  function seek(t) { settling = false; audio.currentTime = Math.max(0, Math.min(duration || 1e9, t)); posDirty = true; userScrolled = false; $("#return-pill").hidden = true; update(true); scrollToCurrent(true); }
  // coming back to a paused tab: adopt a newer position/settings written by another browser
  document.addEventListener("visibilitychange", () => {
    if (document.hidden || !audio.paused || native) return;  // in the app the player catches up itself
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
  // the played part of the bar, drawn by the stylesheet from this one number (once a second at most)
  function paintProgress() { prog.style.setProperty("--p", ((+prog.value / (+prog.max || 1)) * 100).toFixed(2) + "%"); }
  prog.addEventListener("input", () => { seekingUI = true; $("#time-cur").textContent = fmt(+prog.value); paintProgress(); });
  prog.addEventListener("change", () => { seekingUI = false; seek(+prog.value); });

  const turnZone = (e) => { const r = textEl.getBoundingClientRect(); return (e.clientX - r.left) / r.width; };
  textEl.addEventListener("click", (e) => {
    const nref = e.target.closest(".nref");
    if (nref) { showNote(nref); e.stopPropagation(); return; }
    if (pages.on) {
      if (getSelection().toString() || Date.now() - swiped < 400) return;
      const x = turnZone(e);
      if (x < 0.3) turn(-1); else if (x > 0.7) turn(1);
      return;
    }
    if (!hasAudio) return;  // a book without audio is read in page mode, and its columns are not up yet
    if (touchUI && (document.body.classList.contains("hide-player") || document.body.classList.contains("idle"))) {
      showPlayer(); armIdle(); armHidePlayer(4000);  // this tap only brings the bars back; the next one may seek
      return;
    }
    const w = e.target.closest(".w"), s = e.target.closest(".s");
    if (settings.clickWord && w) return seek(wT0[+w.dataset.w]);
    if (s) { const st = sentStart(+s.dataset.s); if (st != null) seek(st); }
  });

  // which third the pointer is over, so the cursor can say which way a click turns. Kept to a class
  // change on the way in and out of a third: this fires on every pointer move.
  let zone = "";
  textEl.addEventListener("mousemove", (e) => {
    const now = !pages.on || getSelection()?.type === "Range" ? "" : turnZone(e) < 0.3 ? "turn-prev" : turnZone(e) > 0.7 ? "turn-next" : "";
    if (now === zone) return;
    if (zone) textEl.classList.remove(zone);
    if (now) textEl.classList.add(now);
    zone = now;
  }, { passive: true });

  // A finger turns pages the way a book's pages turn: a horizontal swipe, either way. Only a swipe
  // that is clearly sideways counts, so a tap or a slip of the thumb does nothing.
  let swipe = null;
  textEl.addEventListener("touchstart", (e) => {
    swipe = pages.on && e.touches.length === 1 ? { x: e.touches[0].clientX, y: e.touches[0].clientY, at: Date.now() } : null;
  }, { passive: true });
  textEl.addEventListener("touchend", (e) => {
    if (!swipe || !pages.on) return;
    const t = e.changedTouches[0], dx = t.clientX - swipe.x, dy = t.clientY - swipe.y;
    const quick = Date.now() - swipe.at < 600;
    swipe = null;
    if (quick && Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy) * 1.5) {
      turn(dx < 0 ? 1 : -1);
      swiped = Date.now();  // the click that follows a swipe is not a tap on a third
    }
  }, { passive: true });
  let swiped = 0;

  // user scroll detection
  const onUserScroll = () => { if (settings.scroll === "off" || pages.on) return; userScrolled = true; $("#return-pill").hidden = false; };
  addEventListener("touchmove", onUserScroll, { passive: true });
  addEventListener("wheel", onUserScroll, { passive: true });  // audio mode: the browser scrolls, we only notice
  // The columns do not scroll, so in page mode a trackpad flick would do nothing at all. A wheel with
  // notches turns a page per notch; a trackpad sends a stream of small deltas, so those are added up
  // and the tail of the gesture's momentum is ignored.
  let wheelAt = 0, wheelSum = 0, wheelSpent = false;
  addEventListener("wheel", (e) => {
    if (!pages.on) return;
    if (e.ctrlKey || e.metaKey) return;  // zooming the page, not turning it
    if (e.target instanceof Element && e.target.closest(".drawer, .popup, .modal, .pager")) return;
    e.preventDefault();
    const gap = Date.now() - wheelAt;
    wheelAt = Date.now();
    if (gap > 220) { wheelSum = 0; wheelSpent = false; }  // a new gesture, not the last one dying out
    const d = Math.abs(e.deltaY) >= Math.abs(e.deltaX) ? e.deltaY : e.deltaX;
    if (e.deltaMode !== 0) { turn(d > 0 ? 1 : -1); return; }  // whole lines or pages: one notch, one page
    if (wheelSpent) return;
    wheelSum += d;
    if (Math.abs(wheelSum) < 30) return;
    turn(wheelSum > 0 ? 1 : -1);
    wheelSpent = true;
    wheelAt = Date.now();  // turning the page took time of its own: that is not a pause in the gesture
  }, { passive: false });
  $("#return-pill").onclick = () => { userScrolled = false; $("#return-pill").hidden = true; scrollToCurrent(true); };

  // keyboard
  addEventListener("keydown", (e) => {
    if (e.target && e.target.matches && e.target.matches("input, select, textarea")) return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;  // leave browser/system shortcuts alone
    const k = e.key === "Spacebar" || e.code === "Space" ? " " : e.key;
    if (k === "m") { toggleMode(); return; }
    if (!pages.on && !hasAudio) return;  // nothing is up yet: the book is still being laid out
    if (pages.on) {
      if (k === "ArrowRight" || k === "PageDown" || (k === " " && !e.shiftKey)) { e.preventDefault(); turn(1); }
      else if (k === "ArrowLeft" || k === "PageUp" || (k === " " && e.shiftKey)) { e.preventDefault(); turn(-1); }
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
  if ("mediaSession" in navigator && !native) {  // the app runs the lock screen itself
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
      send("POST", `/api/state/${slug}/session`, { day: today(), sec, words }, true)
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
    if (native) { bridge.postMessage({ method: "CHIME" }); return; }  // a Web Audio context would take over the app's audio session
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
