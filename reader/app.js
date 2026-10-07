/* readsync reader: text + audio with synced highlighting. Shared helpers and settings live in common.js. */
(() => {
  "use strict";
  if (!slug) return;
  // page-mode state. `into`: spreads past the one `sent` starts on, when no sentence starts on the spread shown
  const pages = { on: false, spreadW: 0, total: 1, cur: 0, sent: 0, into: 0, capped: false };
  const FLOW_CAP = 16777216;  // the widest column flow a browser lays out, in CSS pixels, as measured
  // `pages.on` means the columns are built and a page can be turned; `hasAudio` means there is a
  // narrator to play. Both are false while the book loads, and every control asks one of them, so
  // nothing answers a key or a click before there is something to answer it with.
  // Only a change to the text's metrics moves the columns. A theme or a highlight toggle does not, and
  // laying out a long book again costs seconds, so those skip it.
  // full-screen pages give the columns another box, so the switch counts as a metric
  const metrics = () => [settings.font, settings.lh, settings.width, settings.family, settings.weight, byDevice("immersive")].join("|");
  let metricsKey = metrics();  // common.js applied the cached settings already: those are the metrics laid out
  onApplied = () => {
    const key = metrics();
    if (key === metricsKey) return;
    metricsKey = key;
    // the scrolling text reflows as well: the spoken sentence is put back where the reader looks
    if (pages.on) scheduleRelayout(); else if (hasAudio && !userScrolled) scrollToCurrent(true, true);
  };
  // ---------------- book state ----------------
  const app = $("#app"); app.hidden = false;
  // In the iPhone app the narrator plays natively (background, lock screen, headphones): the same
  // interface as <audio>, but the app owns the position, the sessions and the lock-screen controls.
  const native = !!window.nativeAudio;
  if (native) document.documentElement.classList.add("in-app");  // the page sits inside the iPhone app
  // The app hides the status bar, so the band beside the camera island is free: the top bar moves up into
  // it, its buttons in the two ears, and the text starts right under the island. Portrait only: turned on
  // its side the phone has no inset at the top.
  const insetProbe = document.body.appendChild(Object.assign(document.createElement("div"), { style: "position:fixed;top:0;height:env(safe-area-inset-top,0px);visibility:hidden;pointer-events:none" }));
  const fitEars = () => document.documentElement.classList.toggle("ears", native && insetProbe.offsetHeight >= 24);
  // the web view learns its insets after the first paint: the bars move then, so the scrolling text is put
  // back under the new top bar (pages are laid out again by the text box's own observer below)
  // The ear right of the cutout holds the bar's last buttons. The page is told only how deep the cutout is,
  // not how wide: a notch leaves a shallower inset than an island and is far wider, so the ear beside it is
  // taken as narrower. When the buttons are wider than the ear, the focus button leaves the bar: «Приглушать»
  // in the settings is the same switch. Measured again whenever the bar or the window changes.
  function fitEarButtons() {
    const root = document.documentElement, btn = $("#btn-focus-top");
    root.classList.remove("ears-tight");
    if (!root.classList.contains("ears") || !btn.getClientRects().length) return;
    const cutout = insetProbe.offsetHeight >= 54 ? 140 : 210;
    root.classList.toggle("ears-tight", innerWidth - btn.getBoundingClientRect().left > (innerWidth - cutout) / 2);
  }
  fitEars(); fitEarButtons();
  new ResizeObserver(() => { fitEars(); fitEarButtons(); reanchor(); }).observe(insetProbe);
  addEventListener("resize", fitEarButtons);
  // the right ear says where the reader is, or how long the sprint has left while one runs
  let earR = "", sprintLeft = "";
  const paintEars = () => { $("#ears-info .ear-r").textContent = sprintLeft || earR; };
  const earsInfo = (left, right) => { $("#ears-info .ear-l").textContent = left; earR = right; paintEars(); };
  // a tap in the band by the island, with the bars away, brings them back
  $("#ears-info").addEventListener("click", () => { if (pages.on) setBare(false); else toggleBars(); });
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
    bookMeta = meta;
    book = bookJ; duration = hasAudio ? timingJ.duration : 0;
    { const now = Date.now(); putState({ opened: now, openedAt: now }); }  // library sorts by last opened
    document.title = book.title + " — readsync";
    $("#book-title").textContent = book.title;
    const words = hasAudio ? timingJ.words : [];
    utf16Offsets(words);
    buildIndex(words);
    render(words);
    buildToc();
    $("#loading").hidden = true;
    // reading mode: audio (scrolling text follows the narrator) or pages (two-column spread, no audio)
    const remoteMode = (remote.modeAt || 0) > store.get("rs:modeAt:" + slug, 0) ? remote.mode : store.get("rs:mode:" + slug, null);
    // a cached sentence counts in the numbering of the text it was saved with: another edition's is dropped, as the merge drops it
    const cacheOk = store.get("rs:sentEd:" + slug, null) === (meta.edition || "");
    // and the saved one only when it counts in the text this page loaded (a new edition may have landed between the requests)
    const remoteOk = remote.sentEdition === (meta.edition || "");
    const remoteNewer = remoteOk && (!cacheOk || (remote.sentAt || 0) > store.get("rs:sentAt:" + slug, 0));
    const remoteSent = remoteNewer ? remote.sent : cacheOk ? store.get("rs:sent:" + slug, 0) : 0;
    pages.sent = Math.max(0, Math.min(sFirst.length - 1, remoteSent || 0));
    if (!hasAudio) { $("#btn-mode").hidden = true; $(".player").hidden = true; document.body.classList.add("pages"); }
    if (!hasAudio || remoteMode === "pages") document.fonts.ready.then(() => enterPages(pages.sent, false));
    if (!hasAudio) return;
    audio.src = `/books/${slug}/${meta.audio}`;
    // in the app the player keeps the position, not this page's cache, so the saved state always wins
    const remoteWins = typeof remote.pos === "number" && (native || (remote.posAt || 0) > store.get("rs:posAt:" + slug, 0));
    const pos = remoteWins ? remote.pos : native ? 0 : store.get("rs:pos:" + slug, 0);
    if (remoteWins) { store.set("rs:pos:" + slug, remote.pos); store.set("rs:posAt:" + slug, remote.posAt); }
    audio.addEventListener("loadedmetadata", () => {
      if (isFinite(audio.duration)) { duration = audio.duration; store.set("rs:dur:" + slug, duration); }
      if (pos > 0 && pos < duration - 5) {
        if (!native) audio.currentTime = pos;
        // WebKit reloads a page whose process was killed while the narrator played on: it is already in place
        else if (audio.paused && Math.abs(audio.currentTime - pos) > 1) audio.restore(pos);
      }
      $("#progress").max = duration;
      drawTicks();
      update(true); scrollToCurrent(true, true); settle();
    }, { once: true });
    audio.playbackRate = settings.speed; $("#speed").value = String(settings.speed);
    if (!speeds().includes(settings.speed)) setSpeed(speeds().reduce((a, x) => (Math.abs(x - settings.speed) < Math.abs(a - settings.speed) ? x : a)));
    update(true);
  }

  // Offsets into a block's text (sentences, marks, notes, pics, table cells, timing words) and into a rich note's
  // text are taken here as UTF-16 code units, what `slice` counts. A book marked `"offsets": "utf16"` (the phone's
  // import) has them so; any other book comes from the Python pipeline, which counts code points, and its texts
  // with a character beyond the BMP (an emoji) are converted once, here. An offset past such a text's code-point
  // length cannot be a code point: that text is UTF-16 already (an import from before the mark) and is left as it is.
  function utf16Offsets(words) {
    if (book.offsets === "utf16") return;
    // the converter of one text's offsets, after its marks and pictures are converted; null when it needs none
    const convert = (o, more) => {
      if (!/[\u{10000}-\u{10FFFF}]/u.test(o.text)) return null;
      const at = [0];
      for (const c of o.text) at.push(at[at.length - 1] + c.length);
      const n = at.length - 1, ends = [...more, ...MARKS.flatMap((k) => (o[k] || []).flat()), ...(o.pics || []).map((x) => x.pos)];
      if (ends.some((x) => x > n)) return null;
      const u = (x) => at[Math.min(Math.max(x, 0), n)];
      for (const k of MARKS) if (o[k] || k === "em") o[k] = (o[k] || []).map(([a, e]) => [u(a), u(e)]);
      for (const pc of o.pics || []) pc.pos = u(pc.pos);
      return u;
    };
    const maps = book.blocks.map((blk) => {
      const cells = (blk.rows || []).flat();
      const u = convert(blk, [...blk.sentences.flat(), ...(blk.notes || []).map((x) => x.pos), ...cells.flatMap((c) => [c[0], c[1]])]);
      if (!u) return null;
      blk.sentences = blk.sentences.map(([a, e]) => [u(a), u(e)]);
      for (const nt of blk.notes || []) nt.pos = u(nt.pos);
      if (blk.rows) blk.rows = blk.rows.map((row) => row.map(([a, e, ...h]) => [u(a), u(e), ...h]));
      return u;
    });
    for (const w of words) { const u = maps[w[0]]; if (u) { w[1] = u(w[1]); w[2] = u(w[2]); } }
    for (const nt of Object.values(book.notes || {})) {
      if (!nt || typeof nt !== "object") continue;
      const u = convert(nt, (nt.kinds || []).flatMap((k) => [k[0], k[1]]));
      if (u && nt.kinds) nt.kinds = nt.kinds.map(([a, e, kind]) => [u(a), u(e), kind]);
    }
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
        sWordsCum.push(cum); cum += (book.blocks[b].text.slice(a, e).match(/[\p{L}\p{N}][\p{L}\p{N}\p{M}]*/gu) || []).length;  // a combining mark (и + ̆) stays in its word
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

  // Inline marks, each a list of ranges in its text. A stretch of text between two edges of any of them is drawn
  // once, with the classes of every mark over it, so marks that overlap still nest inside sentences and words.
  const MARKS = ["em", "strong", "sup", "sub"];
  const lines = (s) => esc(s).replace(/\n/g, "<br>");  // `\n` in a text is an explicit line break
  // the marks of a block or a rich note: `cls` names the marks over a stretch no edge cuts, `cut` says whether
  // an edge falls inside one, `run` draws a stretch
  function marksOf(o) {
    const ks = MARKS.filter((k) => o[k] && o[k].length);
    const cuts = [...new Set(ks.flatMap((k) => o[k].flat()))].sort((a, b) => a - b);
    const cls = (a, b) => ks.filter((k) => o[k].some(([x, y]) => a < y && b > x)).join(" ");
    const cut = (a, b) => cuts.some((c) => c > a && c < b);
    const piece = (a, b) => { const c = cls(a, b), h = lines(o.text.slice(a, b)); return c ? `<span class="${c}">${h}</span>` : h; };
    const run = (a, b) => {
      if (a >= b) return "";
      if (!ks.length) return lines(o.text.slice(a, b));
      let h = "";
      for (const c of cuts) { if (c >= b) break; if (c > a) { h += piece(a, c); a = c; } }
      return h + piece(a, b);
    };
    return { cls, cut, run };
  }
  const picHtml = (pc) => `<img class="pic" src="/books/${slug}/${esc(pc.src)}" alt="">`;
  // a picture in the line is a glyph at text height unless it is drawn larger than one
  const sizePic = (im) => im.classList.toggle("big", im.naturalHeight > 64);

  function render(words) {
    // group word indices by block
    const perBlock = new Map();
    for (let i = 0; i < words.length; i++) { const b = words[i][0]; if (!perBlock.has(b)) perBlock.set(b, []); perBlock.get(b).push(i); }
    // a note's marker is the book's own when it had one, else the note's number in the order notes first appear
    const noteNo = new Map();
    for (const b of book.blocks) for (const nt of [...(b.notes || [])].sort((x, y) => x.pos - y.pos)) if (!noteNo.has(nt.id)) noteNo.set(nt.id, noteNo.size + 1);
    const out = [];
    let sGlobal = 0, chapPtr = 0;
    book.blocks.forEach((blk, bi) => {
      while (chapPtr < book.chapters.length && book.chapters[chapPtr].first_block === bi) {
        out.push(`<div class="chap-anchor" id="ch${chapPtr}"></div>`); chapPtr++;
      }
      const ws = perBlock.get(bi) || [];
      const text = blk.text, mk = marksOf(blk), sents = blk.sentences, s0 = sGlobal;
      sGlobal += sents.length;
      // in order, each once: a note or a picture on the boundary of two runs goes with the first, one inside a word after it
      const notes = [...(blk.notes || []), ...(blk.pics || [])].sort((x, y) => x.pos - y.pos);
      let wi = 0, ni = 0, sk = 0;
      const drawn = new Uint8Array(sents.length);
      const emit = (from, to) => {
        // text with note markers and the pictures set in the line (a glyph drawn as an image)
        let h = "", p = from;
        for (; ni < notes.length && notes[ni].pos <= to; ni++) {
          const nt = notes[ni], at = Math.max(p, nt.pos);
          h += mk.run(p, at) + (nt.src ? picHtml(nt) : `<sup class="nref" data-n="${esc(nt.id)}" tabindex="0" role="button">${esc(nt.m || noteNo.get(nt.id))}</sup>`);
          p = at;
        }
        return h + mk.run(p, to);
      };
      // text[from, to) with its sentences and words. A table cell holds a piece of its row's sentence: a sentence
      // is cut to the range, and each piece is a span of that sentence
      const range = (from, to) => {
        let h = "", pos = from;
        while (sk < sents.length && sents[sk][0] < from && sents[sk][1] <= from) sk++;
        for (let k = sk; k < sents.length && sents[k][0] <= to; k++) {
          const a = Math.max(sents[k][0], from), e = Math.min(sents[k][1], to);
          if (a > e || (a === e && (drawn[k] || sents[k][0] !== sents[k][1]))) continue;
          drawn[k] = 1;
          h += emit(pos, a) + `<span class="s" data-s="${s0 + k}">`; pos = a;
          while (wi < ws.length && words[ws[wi]][1] < e) {
            const w = words[ws[wi]], cs = w[1], ce = w[2];
            h += emit(pos, cs);
            // a word under one set of marks carries them itself; one a mark starts or ends inside holds the pieces
            const whole = !mk.cut(cs, ce), c = whole ? mk.cls(cs, ce) : "";
            h += `<span class="w${c ? " " + c : ""}" data-w="${ws[wi]}">${whole ? lines(text.slice(cs, ce)) : mk.run(cs, ce)}</span>`;
            pos = ce; wi++;
          }
          h += emit(pos, e) + "</span>"; pos = e;
        }
        return h + emit(pos, to);
      };
      const lvl = blk.kind === "title" ? " lvl" + (book.chapters[blk.chapter]?.level || 2) : "";
      const nextBlk = book.blocks[bi + 1];
      const stanzaEnd = blk.kind === "verse" && (!nextBlk || nextBlk.stanza !== blk.stanza) ? " stanza-end" : "";
      // a picture of its own goes before its block, or after it when it closes a chapter (`after`)
      const fig = (im) => {
        const src = typeof im === "string" ? im : im.src, dims = im.w && im.h ? ` width="${im.w}" height="${im.h}"` : "";
        return `<figure class="fig"><img src="/books/${slug}/${esc(src)}"${dims} alt="" loading="lazy"></figure>`;
      };
      for (const im of blk.images || []) if (!im.after) out.push(fig(im));
      // a table: a cell per range of its row, a header cell marked by a third element; wider than the column it
      // scrolls inside its own box
      if (blk.kind === "table" && Array.isArray(blk.rows)) {
        const rows = blk.rows.map((row) => `<tr>${row.map(([a, e, th]) => (th ? `<th>${range(a, e)}</th>` : `<td>${range(a, e)}</td>`)).join("")}</tr>`);
        out.push(`<div class="blk k-table" data-b="${bi}"><table>${rows.join("")}</table></div>`);
      } else {
        out.push(`<p class="blk k-${blk.kind}${lvl}${stanzaEnd}" data-b="${bi}"${blockStyle(blk.st)}>${range(0, text.length)}</p>`);
      }
      for (const im of blk.images || []) if (im.after) out.push(fig(im));
    });
    textEl.innerHTML = out.join("");
    wordEls = new Array(wT0.length); sentEls = new Array(sFirst.length); blockEls = new Array(book.blocks.length);
    textEl.querySelectorAll(".w").forEach((el) => (wordEls[+el.dataset.w] = el));
    textEl.querySelectorAll(".s").forEach((el) => (sentEls[+el.dataset.s] ??= el));  // a sentence across table cells: its first piece
    textEl.querySelectorAll(".blk").forEach((el) => (blockEls[+el.dataset.b] = el));
    textEl.querySelectorAll("img.pic").forEach((im) => (im.complete ? sizePic(im) : im.addEventListener("load", () => sizePic(im), { once: true })));
    // a picture decodes after the text is laid out and pushes every page along: measure again
    textEl.querySelectorAll("img").forEach((im) => im.addEventListener("load", scheduleRelayout, { once: true }));
  }

  // The book's own look (book.json's `st`, made by pipeline/extract_style.py): its alignment wins over
  // «По ширине», its indent and margin over the kind's. Only the box changes, never the text inside it, so
  // sentences, words and pages are found as before. A margin keeps to a share of a narrow column.
  const ALIGN = { l: "left", r: "right", c: "center", j: "justify" };
  function blockStyle(st) {
    if (!st) return "";
    const em = (v, hi) => Math.min(hi, Math.max(0, Number(v) || 0));
    let css = "";
    if (ALIGN[st.a]) css += `text-align:${ALIGN[st.a]};`;
    if (st.i != null) css += `text-indent:${em(st.i, 3)}em;`;
    if (st.m) css += `margin-left:min(${em(st.m, 10)}em,40%);`;
    if (st.g) css += `padding-top:calc(${em(st.g, 3)} * var(--lh) * 1em);`;  // blank lines, on top of the kind's margin
    return css ? ` style="${css}"` : "";
  }

  function buildToc() {
    const ol = $("#toc-list");
    ol.innerHTML = '<li class="lib" data-lib="1" tabindex="0" role="button"><span>← Библиотека</span></li>' + book.chapters.map((c, i) => c.hidden ? "" : `<li class="l${Math.min(4, Math.max(1, +c.level || 1))}" data-ch="${i}" tabindex="0" role="button"><span>${esc(c.title)}</span><span class="tt">${isFinite(chapStartTime[i]) ? fmt(chapStartTime[i]) : ""}</span></li>`).join("");
    ol.addEventListener("click", (e) => {
      const li = e.target.closest("li"); if (!li) return;
      if (li.dataset.lib) { flushSent(true); session.stop(); location.href = "/"; return; }
      const i = +li.dataset.ch;
      if (pages.on) { goToSentence(firstSentOfChapter(i)); setBare(true); }
      else if (isFinite(chapStartTime[i])) seek(chapStartTime[i]);
      closeDrawers();
    });
    // from the keyboard too: ↵ or space on an entry is its click
    ol.addEventListener("keydown", (e) => {
      const li = e.target.closest("li");
      if (!li || (e.key !== "Enter" && e.key !== " ")) return;
      e.preventDefault(); e.stopPropagation(); li.click();
    });
  }
  function firstSentOfChapter(ci) { const fb = book.chapters[ci].first_block; for (let s = 0; s < sBlock.length; s++) if (sBlock[s] >= fb) return s; return 0; }
  // -1: before the first chapter that shows (a hidden one, front matter without a title), which names nothing
  function chapterOfSent(si) { const b = sBlock[si] ?? 0; let c = -1; book.chapters.forEach((ch, i) => { if (ch.first_block <= b && !ch.hidden) c = i; }); return c; }
  // Chapters cut the bar into parts, but only while the parts stay wider than a fingertip: dozens of cuts
  // on a narrow bar turn it into a dotted line. Sections go first, then the chapters themselves.
  function drawTicks() {
    const el = $("#chapter-ticks");
    const cuts = book.chapters.map((c, i) => ({ c, at: chapStartTime[i] / duration }))
      .filter(({ c, at }) => !c.hidden && at > 0 && at < 1);
    const room = el.clientWidth / 24;
    let shown = cuts.length <= room ? cuts : cuts.filter(({ c }) => c.level <= 1);
    if (shown.length > room) shown = [];
    el.innerHTML = shown.map(({ c, at }) => `<i class="l${+c.level || 1}" style="left:${at * 100}%" title="${esc(c.title)}"></i>`).join("");
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
    if (!native && !audio.paused) heard(audio.currentTime);
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
        markToc(ci);
      }
    }
    const sec = Math.floor(t);
    if (sec !== lastSec || force) {
      lastSec = sec;
      if (!seekingUI) { $("#progress").value = t; paintProgress(); }
      $("#time-cur").textContent = fmt(t);
      const chEnd = chapStartTime.find((x, i) => i > curChap && !book.chapters[i].hidden && isFinite(x)) ?? duration;
      const rate = audio.playbackRate || 1;
      $("#time-chap").textContent = "глава −" + fmt((chEnd - t) / rate);
      $("#time-chap").hidden = chEnd >= duration;  // the last chapter ends with the book: one time is enough
      earsInfo($("#chapter-title").textContent, chEnd >= duration ? "−" + fmt((duration - t) / rate) : $("#time-chap").textContent);
      $("#time-left").textContent = "−" + fmt((duration - t) / rate);
    }
  }
  let lastSec = -1;
  function markToc(ci) { document.querySelectorAll("#toc-list li").forEach((li) => { const k = +li.dataset.ch; li.classList.toggle("cur", k === ci); li.classList.toggle("done", k < ci); }); }
  function chapterAt(t) { let c = -1; for (let i = 0; i < chapStartTime.length; i++) if (chapStartTime[i] <= t && !book.chapters[i].hidden) c = i; return c; }

  function onSentenceChange(prevSent) {
    if (sprint.stopAtSentence && prevSent >= 0) { sprint.stopAtSentence = false; audio.pause(); finishSprint(); return; }
    scrollToCurrent(false);
  }
  let settling = true;  // until the reader plays or seeks, every scroll is instant (initial positioning)
  function scrollToCurrent(force, instant) {
    const el = sentEls[curSent]; if (!el) return;
    if (settings.scroll === "off" && !force) return;
    if (userScrolled && !force) return;
    // the bars as laid out, safe areas and the card's float included. The card is measured by its layout,
    // not its box on screen: hidden, it slides down, and the reading zone must not move with it
    const player = $(".player");
    const top = $(".topbar").getBoundingClientRect().height;
    const bottom = player.offsetHeight + (parseFloat(getComputedStyle(player).bottom) || 0);
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
    document.fonts.ready.then(reanchor);
    addEventListener("load", reanchor, { once: true });
    [400, 1200, 2500].forEach((ms) => setTimeout(reanchor, ms));
  }
  function reanchor() { if (settling && !userScrolled && !pages.on) scrollToCurrent(true, true); }

  // 10 Hz sync loop while playing (cheap: one binary search + a few class toggles per tick)
  let tick = 0;
  audio.addEventListener("play", () => { settling = false; clearInterval(tick); tick = setInterval(() => update(false), 100); setIcon($("#btn-play"), "pause"); if (!native) session.start(); document.body.classList.add("playing"); armIdle(); armHidePlayer(); });
  audio.addEventListener("pause", () => { clearInterval(tick); update(true); setIcon($("#btn-play"), "play"); if (!native && !pages.on) session.stop(); savePos(); document.body.classList.remove("playing", "idle"); showPlayer();
    pausedAt = audio.pausedAt || Date.now();  // the app says when: a pause on the lock screen is told only at unlock
    if (sprint.stopAtSentence) { sprint.stopAtSentence = false; finishSprint(); }  // the sprint ran out mid-sentence: no sentence end is coming
  });
  // distraction-free chrome while the narrator plays. With a mouse the top bar fades after 4 s without
  // pointer or keyboard activity, and the player hides 1.5 s after play starts (scrolling does not bring
  // it back) and returns on pause or when the pointer reaches the bottom edge. A finger has no idle
  // pointer and no edge to reach: both bars slide away together after 3 s, come back with a tap on the
  // text or on pause, and stay while a finger is on them.
  let idleTimer = 0, hideTimer = 0;
  function armIdle() {
    if (touchUI) return;  // a tap fires mouse events too; on a touch screen the bars go with the player
    clearTimeout(idleTimer);
    document.body.classList.remove("idle");
    if (byDevice("hideUi")) idleTimer = setTimeout(() => { if (!audio.paused && $("#toc").hidden && $("#settings").hidden) document.body.classList.add("idle"); }, 4000);
  }
  ["mousemove", "mousedown", "keydown"].forEach((ev) => addEventListener(ev, armIdle, { passive: true }));
  function armHidePlayer(ms = touchUI ? 3000 : 1500) {
    clearTimeout(hideTimer);
    if (byDevice("hideUi") && !pages.on) hideTimer = setTimeout(() => {
      if (audio.paused) return;
      // a hand still on a bar (a held button, the bar dragged, the speed list open) or a drawer open: wait
      const busy = chromeTouch || $(".player").matches(":active") || document.activeElement === $("#speed")
        || !$("#sprint-menu").hidden || (touchUI && !($("#toc").hidden && $("#settings").hidden));
      if (busy) return armHidePlayer(ms);
      document.body.classList.add("hide-player");
      if (touchUI) document.body.classList.add("idle");
    }, ms);
  }
  let chromeTouch = false;  // a touch that began on a bar and has not ended yet
  document.querySelectorAll(".player, .topbar").forEach((bar) => {
    bar.addEventListener("touchstart", () => { chromeTouch = true; clearTimeout(hideTimer); }, { passive: true });
    ["touchend", "touchcancel"].forEach((ev) => bar.addEventListener(ev, () => {
      chromeTouch = false;
      if (!audio.paused && !document.body.classList.contains("hide-player")) armHidePlayer(4000);
    }, { passive: true }));
  });
  function showPlayer() {
    clearTimeout(hideTimer); document.body.classList.remove("hide-player"); $(".player").classList.remove("peek");
    if (touchUI) document.body.classList.remove("idle");
  }
  addEventListener("mousemove", (e) => {
    if (!document.body.classList.contains("hide-player")) return;
    const player = $(".player"), peek = player.classList.contains("peek");
    const zone = peek ? innerHeight - player.offsetHeight - 8 : innerHeight - 20;  // offsetHeight: the bar may still be sliding in
    player.classList.toggle("peek", e.clientY >= zone);
  }, { passive: true });
  // focus aid: pause when the reader leaves the tab or window
  // the app keeps playing on a locked screen: leaving the page is the phone being locked, not the reader leaving
  const onLeave = () => { if (!native && settings.pauseHidden && !audio.paused) audio.pause(); };
  document.addEventListener("visibilitychange", () => { if (document.hidden) { onLeave(); if (pages.on) session.stop(); } else pageActive(); });
  audio.addEventListener("seeked", () => { heardAt = -1; update(true); });
  // played to the end of the file: read to the end, whatever came before. In the app the player says so itself
  audio.addEventListener("ended", () => { if (!native) markDone(); });
  if (native) audio.addEventListener("finished", () => loadRemote().then(() => showDone(today())));
  // a paused narrator that moved anyway (the app's lock screen): repaint. While playing the tick does it
  audio.addEventListener("timeupdate", () => { if (audio.paused) update(true); });
  audio.addEventListener("ratechange", () => update(true));
  audio.addEventListener("error", () => { $("#loading").hidden = false; $("#loading").textContent = "Ошибка аудио: " + (audio.error?.message || audio.error?.code); });
  setInterval(() => { if (!audio.paused) savePos(); }, 5000);
  addEventListener("beforeunload", () => { savePos(true); flushSent(true); session.stop(); });
  // the links back to the library: in the app the page may be put away without unloading, so the last
  // page turned and the reading session are saved on the click itself
  document.querySelectorAll(".back-lib, #book-title").forEach((a) => a.addEventListener("click", () => { flushSent(true); session.stop(); }));
  // a tab only publishes its position after it played or seeked, so a stale background tab
  // closed later cannot clobber progress made in another browser
  let posDirty = false;
  audio.addEventListener("playing", () => { posDirty = true; });
  function savePos(keepalive) {
    if (!posDirty || native) return;  // in the app the player saves its own position, even while the screen is locked
    const at = Date.now();
    store.set("rs:pos:" + slug, audio.currentTime); store.set("rs:posAt:" + slug, at);
    putState({ pos: audio.currentTime, posAt: at }, keepalive);
    if (audio.paused) posDirty = false;  // saved where it stopped: only playing or a seek makes it the latest again
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
  // A table scrolls sideways inside its box, and a box that scrolls is never split between columns: one taller
  // than a column would be cut off at its foot. Such a table is laid out to the column's width instead and
  // flows on through the columns. Decided again at every layout, as the font and the window change it.
  function splitTables() {
    const tables = textEl.querySelectorAll(".k-table");
    if (!tables.length) return;
    tables.forEach((t) => t.classList.remove("split"));
    const cs = getComputedStyle(textEl), colH = textEl.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
    [...tables].filter((t) => t.offsetHeight > colH).forEach((t) => t.classList.add("split"));
  }
  function pagesLayout() {
    splitTables();
    textEl.classList.remove("pad-col");
    const gap = parseFloat(getComputedStyle(textEl).columnGap) || 0;
    pages.spreadW = Math.max(1, contentWidth() + gap);
    // the flow is spreads of this pitch with no gap after the last one. Round up: a half-filled last
    // spread is still a page, and losing it would put the end of the book out of reach.
    pages.total = Math.max(1, Math.ceil((textEl.scrollWidth + gap) / pages.spreadW - 0.02));
    // An odd count of columns ends the flow half a spread short, and the browser stops the scroll there: the
    // last spread would open on the column before it. An empty column after the text lets it reach the end.
    if (textEl.scrollWidth - textEl.clientWidth < (pages.total - 1) * pages.spreadW - 1) textEl.classList.add("pad-col");
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
    // no sentence starts on spread n (one longer than a page): the one running into it is the place, not the next page's
    if (ans > 0 && spreadOfSent(ans) > n) ans--;
    return ans;
  }
  // how far the book is read: the last spread shown is the end, however many sentences start before it
  const pagesPct = () => (pages.cur >= pages.total - 1 ? 100 : Math.round((pages.sent / Math.max(1, sFirst.length)) * 100));
  let sentTimer = 0, sentPatch = null;
  // Show spread `n`. `anchor` is the sentence the reader is on when the caller already knows it: a
  // relayout must keep the sentence it started from, or every resize event nudges the place a little.
  function goSpread(n, save = true, anchor = null, step = false) {
    n = Math.max(0, Math.min(pages.total - 1, n));
    const prevSent = pages.sent, prevCur = pages.cur;
    pages.cur = n; textEl.scrollLeft = n * pages.spreadW;
    pages.sent = anchor != null ? Math.max(0, Math.min(sFirst.length - 1, anchor)) : sentAtSpread(n);
    pages.into = anchor != null ? 0 : Math.max(0, n - spreadOfSent(pages.sent));
    if (save && step) pageActive();
    // words read, not words skipped past: a jump to a page or a chapter is not reading
    if (save && step && pages.sent > prevSent && pages.sent - prevSent < 400) session.words += sWordsCum[pages.sent] - sWordsCum[prevSent];
    $("#chapter-title").textContent = book.chapters[chapterOfSent(pages.sent)]?.title || "";  // before the pager: it writes the ears
    paintPager();
    if (save && step && n > prevCur) turnedOnto(prevCur, n);
    if (save) {
      const at = Date.now(); store.set("rs:sent:" + slug, pages.sent); store.set("rs:sentAt:" + slug, at); store.set("rs:sentEd:" + slug, bookMeta.edition || "");
      // the edition this page's text came with: a tab left open across a text replacement does not move the new one
      sentPatch = { sent: pages.sent, sentAt: at, sentPct: pagesPct(), sentEdition: bookMeta.edition || "" };
      clearTimeout(sentTimer); sentTimer = setTimeout(flushSent, 300);
    }
  }
  // the page turned last, saved now instead of after the pause (the page is being left)
  // page mode has no narrator to say the reader is still there: ten minutes without a turn end the session,
  // and the next turn starts another
  let pageIdle = 0;
  function pageActive() {
    clearTimeout(pageIdle);
    if (!pages.on || document.hidden) return;
    session.start();
    pageIdle = setTimeout(() => { if (pages.on) session.stop(); }, 600000);
  }
  function flushSent(keepalive) { clearTimeout(sentTimer); if (sentPatch) putState(sentPatch, keepalive); sentPatch = null; }
  // `into`: a page inside a sentence longer than a page stays that many pages into it, though never past
  // the page the next sentence starts on
  function goToSentence(si, save = true, into = 0) {
    if (!sentEls[si]) return goSpread(0, save);  // no such sentence: the page shown and the place saved must agree
    const at = spreadOfSent(si);
    goSpread(into > 0 ? Math.min(at + into, sentEls[si + 1] ? spreadOfSent(si + 1) : pages.total - 1) : at, save, si);
    pages.into = Math.max(0, pages.cur - at);
  }
  // Turn a page. The columns are rebuilt the moment the window changes but measured only after the
  // reader's hand stops, so the numbers are checked first: a turn in that gap would use last
  // window's pitch and land between columns.
  // A turn from the page itself (a tap, a swipe, a key, the wheel) puts the bars away; a turn from the
  // pager's own arrows leaves them where the hand is.
  function turn(delta, fromPager = false) {
    if (!pages.on) return;
    const gap = parseFloat(getComputedStyle(textEl).columnGap) || 0;
    if (relayoutTimer || Math.abs(contentWidth() + gap - pages.spreadW) > 0.5) { clearTimeout(relayoutTimer); relayout(); }
    goSpread(pages.cur + delta, true, null, true);
    if (delta > 0 && pages.total === 1) markDone();  // a book on one spread: turning on from it is reading to its end
    if (!fromPager) setBare(true);
  }
  // Full-screen pages: the text box is the whole screen whether the bars are up or not (they float over
  // it), so showing them never lays the book out again. `bare` only means anything with `immersive` on.
  // Any change from the reader ends the bars' first showing.
  let bareTimer = 0;
  function setBare(on) { clearTimeout(bareTimer); document.body.classList.toggle("bare", on); }
  // Opened in full-screen pages, the bars show for a moment first, so the reader sees where they are
  // and how to get out; an open drawer or a page number being typed keeps them up.
  function peekBars(ms = 2500) {
    setBare(false);
    if (!byDevice("immersive")) return;
    bareTimer = setTimeout(function hide() {
      const busy = !($("#toc").hidden && $("#settings").hidden && $("#sprint-menu").hidden) || pgTyping;
      if (busy) bareTimer = setTimeout(hide, ms); else setBare(true);
    }, ms);
  }
  // The columns were rebuilt (window resized, font or width changed, a webfont arrived): the reader
  // keeps their sentence and only the page number under it changes.
  function relayout() {
    relayoutTimer = 0;
    if (!pages.on) return;
    const into = pages.into;
    pagesLayout();
    goToSentence(pages.sent, false, into);
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
    if (sprint.stopAtSentence) { sprint.stopAtSentence = false; finishSprint(); }  // the sprint ran out mid-sentence: it ends with the listening
    // a sprint goes on across the switch: the words listened to so far are kept, the pages count theirs from here
    if (sprint.end) sprint.base += Math.max(0, curWord - Math.max(0, sprint.startWord ?? curWord));
    session.stop();  // the listening session ends here; the pages keep their own
    if (sprint.end) sprint.words = 0;
    if (native) window.webkit.messageHandlers.audio.postMessage({ cmd: "pages", on: true });  // the lock screen must not start the narrator under a page
    pages.on = true; document.body.classList.add("pages"); $("#pager").hidden = false; setIcon($("#btn-mode"), "audio"); $("#btn-mode").title = "Вернуться к аудио (m)";
    closeDrawers(); peekBars(); pagesLayout(); goToSentence(si ?? pages.sent, save);  // the narrator's sentence is the place now
    if (save) saveMode("pages");
    pageActive();
  }
  function exitPages() {
    if (native) window.webkit.messageHandlers.audio.postMessage({ cmd: "pages", on: false });
    pages.on = false; document.body.classList.remove("pages"); setBare(false); $("#pager").hidden = true; setIcon($("#btn-mode"), "book"); $("#btn-mode").title = "Режим книги (m)";
    clearTimeout(pageIdle); session.stop(); saveMode("audio"); drawTicks();  // the bar had no width while the pages covered it
    fitEarButtons();  // the focus button shows again, in the ear it was measured out of while hidden
    if (sprint.end) { sprint.base += Math.round(sprint.words); sprint.words = 0; }
    // the narrator goes to the page's sentence, or to the next one it reads when that one is not narrated (a
    // title, a scene break), or to the last one before it at the end of the book
    let k = pages.sent;
    while (k < sFirst.length && sFirst[k] < 0) k++;
    if (k >= sFirst.length) { k = pages.sent; while (k >= 0 && sFirst[k] < 0) k--; }
    curChap = -2;  // the chapter line is the narrator's again, whatever the pages wrote there
    const st = sentStart(k);
    if (st != null) seek(st); else update(true);
    if (sprint.end) sprint.startWord = curWord;
  }
  function toggleMode() { if (!hasAudio) return; pages.on ? exitPages() : enterPages(curSent >= 0 ? curSent : pages.sent); }
  $("#btn-mode").onclick = toggleMode;
  $("#pg-prev").onclick = () => turn(-1, true);
  $("#pg-next").onclick = () => turn(1, true);
  addEventListener("resize", scheduleRelayout);
  // the page's box changes without the window too: the insets arrive after the first paint (the top of
  // the page moves down by the island), the ears take the bar. Taller or shorter columns hold other text
  new ResizeObserver(scheduleRelayout).observe(textEl);
  // a paused narrator does not move the text, so a window resized or a phone turned would leave its sentence
  // wherever the reflow put it: it is brought back into view, unless the reader scrolled away from it
  addEventListener("resize", () => {
    if (!hasAudio || !duration || pages.on) return;
    drawTicks();
    if (audio.paused && !userScrolled) scrollToCurrent(false, true);
  });
  document.fonts.addEventListener("loadingdone", scheduleRelayout);

  // ---- the page number: readable, and a field to jump from ----
  let pgTyping = false;  // while the reader is typing, a page turn must not overwrite what they wrote
  function paintPager() {
    pgTotal.textContent = pages.total;
    // with the top bar in the island's ears there is no room for the chapter up there: it reads at the foot
    $("#pg-foot").textContent = `${pages.cur + 1} / ${pages.total}`;
    earsInfo($("#chapter-title").textContent, `${pages.cur + 1} / ${pages.total}`);
    pgCur.style.width = String(pages.total).length + 2 + "ch";
    if (!pgTyping) pgCur.value = pages.cur + 1;
    $("#pg-prev").disabled = pages.cur <= 0; $("#pg-next").disabled = pages.cur >= pages.total - 1;  // the ends say so
    const pct = pagesPct();
    pgRead.innerHTML = pages.capped ? "книга не помещается целиком" : pct ? `<span class="pg-word">прочитано </span>${pct}%` : "";
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
  // a phone's keyboard covers the foot of the screen: while a number is typed the pager rides above it
  const vv = window.visualViewport;
  function liftPager() {
    const lift = vv ? document.documentElement.clientHeight - vv.height - vv.offsetTop : 0;
    $("#pager").style.bottom = pgTyping && lift > 40 ? lift + 8 + "px" : "";
  }
  vv?.addEventListener("resize", liftPager); vv?.addEventListener("scroll", liftPager);
  pgCur.addEventListener("focus", () => { pgTyping = true; pgCur.select(); liftPager(); });
  pgCur.addEventListener("input", () => { pgTyping = true; });  // typed into, however the focus got there
  pgCur.addEventListener("blur", () => { commitPage(); liftPager(); });
  pgCur.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); commitPage(); pgCur.blur(); }
    else if (e.key === "Escape") { e.preventDefault(); pgTyping = false; pgCur.value = pages.cur + 1; pgCur.blur(); }
    else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      // the field stays in hand: it keeps the focus, so it writes the new page itself
      e.preventDefault();
      turn(e.key === "ArrowUp" ? -1 : 1, true);
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
  function seek(t) { heardAt = -1; settling = false; audio.currentTime = Math.max(0, Math.min(duration || 1e9, t)); posDirty = true; userScrolled = false; $("#return-pill").hidden = true; update(true); scrollToCurrent(true); }
  // coming back to a paused tab: adopt a newer position/settings written by another browser
  // In the app the player catches the narrator up itself: only the page turned elsewhere is the page's to adopt
  document.addEventListener("visibilitychange", () => {
    if (document.hidden || !audio.paused) return;
    loadRemote().then(() => {
      if (!native && (remote.posAt || 0) > store.get("rs:posAt:" + slug, 0) && typeof remote.pos === "number") {
        audio.currentTime = remote.pos; posDirty = false; store.set("rs:pos:" + slug, remote.pos); store.set("rs:posAt:" + slug, remote.posAt);
        update(true); scrollToCurrent(true);
      }
      // and the page another device turned to since, when that is the newer place in the text this page shows
      if (pages.on && typeof remote.sent === "number" && remote.sentEdition === (bookMeta.edition || "") && (remote.sentAt || 0) > store.get("rs:sentAt:" + slug, 0)) {
        store.set("rs:sent:" + slug, remote.sent); store.set("rs:sentAt:" + slug, remote.sentAt); store.set("rs:sentEd:" + slug, bookMeta.edition || "");
        goToSentence(Math.max(0, Math.min(sFirst.length - 1, remote.sent)), false);
      }
      if (!native) adoptSettings(remoteSettings);
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
  function toggleDim() { settings.dimMode = settings.dimMode === "off" ? (settings.lastDim || "para") : "off"; if (settings.dimMode !== "off") settings.lastDim = settings.dimMode; applySettings(); syncSettingsUI(); saveSettings("dimMode", "lastDim"); }
  // the keys step through the speeds the list offers, so the list always shows the one playing
  const speeds = () => [...$("#speed").options].map((o) => +o.value);
  function stepSpeed(d) {
    const cur = audio.playbackRate, vs = speeds();
    const v = d > 0 ? vs.find((x) => x > cur + 0.001) : vs.reverse().find((x) => x < cur - 0.001);
    if (v != null) setSpeed(v);
  }
  function setSpeed(v) { v = Math.round(Math.min(2, Math.max(0.5, +v)) * 100) / 100; audio.playbackRate = v; settings.speed = v; $("#speed").value = String(v); store.set("rs:settings", settings); saveSettings("speed"); }

  $("#btn-play").onclick = toggle;
  $("#btn-back").onclick = () => seek(audio.currentTime - 10);
  $("#btn-fwd").onclick = () => seek(audio.currentTime + 10);
  $("#btn-prev-sent").onclick = prevSentence;
  $("#btn-next-sent").onclick = nextSentence;
  $("#speed").onchange = (e) => setSpeed(e.target.value);
  $("#btn-focus").onclick = toggleDim; $("#btn-focus-top").onclick = toggleDim;
  let seekingUI = false;
  const prog = $("#progress");
  // the played part of the bar, drawn by the stylesheet from this one number (once a second at most)
  function paintProgress() { prog.parentElement.style.setProperty("--p", ((+prog.value / (+prog.max || 1)) * 100).toFixed(2) + "%"); }
  prog.addEventListener("input", () => { seekingUI = true; $("#time-cur").textContent = fmt(+prog.value); paintProgress(); });
  prog.addEventListener("change", () => { seekingUI = false; seek(+prog.value); });

  const turnZone = (e) => { const r = textEl.getBoundingClientRect(); return (e.clientX - r.left) / r.width; };
  // the outer thirds turn a page; with full-screen pages the middle one shows or puts away the bars
  function tapPage(e) {
    if (getSelection().toString() || Date.now() - swiped < 400) return;
    const x = turnZone(e);
    if (x < 0.3) turn(-1); else if (x > 0.7) turn(1);
    else if (byDevice("immersive")) setBare(!document.body.classList.contains("bare"));
  }
  // the margins round the columns are part of the page: a thumb at the screen's edge still turns it
  addEventListener("click", (e) => { if (pages.on && [document.documentElement, document.body, app].includes(e.target) && !closePopups()) tapPage(e); });
  // an open footnote or sprint menu: a tap on the page closes it and does nothing else
  function closePopups() {
    const open = !$("#note-pop").hidden || !$("#sprint-menu").hidden;
    $("#note-pop").hidden = true; $("#sprint-menu").hidden = true;
    return open;
  }
  // a note's marker opens from the keyboard as from a click: ↵ or space, not the narrator's space
  textEl.addEventListener("keydown", (e) => {
    const nref = e.target instanceof Element && e.target.closest(".nref");
    if (!nref || (e.key !== "Enter" && e.key !== " ")) return;
    e.preventDefault(); e.stopPropagation(); showNote(nref);
  });
  textEl.addEventListener("click", (e) => {
    const nref = e.target.closest(".nref");
    if (nref) { showNote(nref); e.stopPropagation(); return; }
    if (closePopups()) return;
    if (pages.on) { tapPage(e); return; }
    if (!hasAudio) return;  // a book without audio is read in page mode, and its columns are not up yet
    // A finger taps the text to see the bars or put them away, and again to keep them: a tap must not move
    // the narrator. Seeking takes a double tap; the single tap waits that long to know which it is.
    if (touchUI) {
      const twice = tapTimer && Math.hypot(e.clientX - tapAt.x, e.clientY - tapAt.y) < 40;
      clearTimeout(tapTimer); tapTimer = 0;
      if (twice) return seekToText(e.target);
      tapAt = { x: e.clientX, y: e.clientY };
      tapTimer = setTimeout(() => { tapTimer = 0; toggleBars(); }, 300);
      return;
    }
    if (getSelection().toString()) return;  // text selected with a drag: the click that ends it is not a seek
    seekToText(e.target);
  });
  let tapTimer = 0, tapAt = { x: 0, y: 0 };
  function seekToText(target) {
    const w = target.closest(".w"), s = target.closest(".s");
    if (settings.clickWord && w) return seek(wT0[+w.dataset.w]);
    if (s) { const st = sentStart(+s.dataset.s); if (st != null) seek(st); }
  }
  // the bars that slide away while the narrator plays: back for a while, or away now
  function toggleBars() {
    if (document.body.classList.contains("hide-player") || document.body.classList.contains("idle")) {
      showPlayer(); armIdle(); armHidePlayer(4000);
    } else if (byDevice("hideUi") && !audio.paused) {
      clearTimeout(hideTimer); document.body.classList.add("hide-player", "idle");
    }
  }

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
  // The margins round the columns count too; a swipe that starts on a bar or a drawer is that control's.
  let swipe = null;
  // a table wider than its column scrolls sideways under the finger or the trackpad instead of turning the page
  const wideTable = (el) => { const t = el.closest(".k-table"); return !!t && t.scrollWidth > t.clientWidth + 1; };
  addEventListener("touchstart", (e) => {
    const onControl = e.target instanceof Element && (e.target.closest(".topbar, .pager, .drawer, .popup, .modal, #scrim") || wideTable(e.target));
    swipe = pages.on && !onControl && e.touches.length === 1 ? { x: e.touches[0].clientX, y: e.touches[0].clientY, at: Date.now() } : null;
  }, { passive: true });
  addEventListener("touchend", (e) => {
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
  // only the text counts: scrolling a drawer or dragging the bar does not take the page from the narrator
  const onUserScroll = (e) => {
    if (settings.scroll === "off" || pages.on) return;
    if (e.target instanceof Element && e.target.closest(".drawer, .popup, .modal, .player, .topbar, #scrim")) return;
    userScrolled = true; $("#return-pill").hidden = false;
  };
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
    if (Math.abs(e.deltaX) > Math.abs(e.deltaY) && e.target instanceof Element && wideTable(e.target)) return;
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
  const KEYS = { KeyM: "m", KeyT: "t", KeyR: "r", KeyF: "f", KeyA: "a", BracketLeft: "[", BracketRight: "]" };
  addEventListener("keydown", (e) => {
    if (e.target && e.target.matches && e.target.matches("input, select, textarea")) return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;  // leave browser/system shortcuts alone
    // a letter by its place on the keyboard when the layout gives another alphabet's (ь for m on a Russian one)
    const latin = e.key.length !== 1 || e.key <= "~";
    const k = e.key === "Spacebar" || e.code === "Space" ? " " : latin ? e.key : KEYS[e.code] || e.key;
    if (k === "m") { toggleMode(); return; }
    if (!pages.on && !hasAudio) return;  // nothing is up yet: the book is still being laid out
    if (pages.on) {
      if (k === "ArrowRight" || k === "PageDown" || (k === " " && !e.shiftKey)) { e.preventDefault(); turn(1); }
      else if (k === "ArrowLeft" || k === "PageUp" || (k === " " && e.shiftKey)) { e.preventDefault(); turn(-1); }
      else if (k === "Home") { goSpread(0); setBare(true); }
      else if (k === "End") { goSpread(pages.total - 1); setBare(true); }
      else if (k === "t") toggleDrawer("#toc");
      else if (k === "Escape") { closeDrawers(); closePopups(); closeModals(); }
      return;
    }
    if (k === " ") { e.preventDefault(); toggle(); }
    else if (k === "ArrowLeft") { e.preventDefault(); e.shiftKey ? seek(audio.currentTime - 10) : prevSentence(); }
    else if (k === "ArrowRight") { e.preventDefault(); e.shiftKey ? seek(audio.currentTime + 10) : nextSentence(); }
    else if (k === "r") repeatSentence();
    else if (k === "[") stepSpeed(-1);
    else if (k === "]") stepSpeed(1);
    else if (k === "f") toggleDim();
    else if (k === "t") toggleDrawer("#toc");
    else if (k === "a") { userScrolled = false; $("#return-pill").hidden = true; scrollToCurrent(true); }
    else if (k === "Escape") { closeDrawers(); closePopups(); closeModals(); }
    else if (["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End"].includes(k)) onUserScroll(e);  // the browser scrolls, as with the wheel
  });
  // a drag of the window's scrollbar, which no wheel or touch reports
  addEventListener("mousedown", (e) => { if (e.target === document.documentElement && e.clientX >= document.documentElement.clientWidth) onUserScroll(e); });
  if ("mediaSession" in navigator && !native) {  // the app runs the lock screen itself
    // under a page the narrator is not the media keys' to start or move, as in the app
    const media = (fn) => () => { if (!pages.on) fn(); };
    navigator.mediaSession.setActionHandler("play", media(play));
    navigator.mediaSession.setActionHandler("pause", () => audio.pause());
    navigator.mediaSession.setActionHandler("seekbackward", media(() => seek(audio.currentTime - 10)));
    navigator.mediaSession.setActionHandler("seekforward", media(() => seek(audio.currentTime + 10)));
    navigator.mediaSession.setActionHandler("previoustrack", media(prevSentence));
    navigator.mediaSession.setActionHandler("nexttrack", media(nextSentence));
  }

  // ---------------- drawers / settings ----------------
  function toggleDrawer(sel) {
    const el = $(sel); const open = el.hidden; closeDrawers();
    if (open) {
      el.hidden = false; $("#scrim").hidden = false;
      // the contents open where the reader is: the current chapter marked, in the middle of the list
      if (sel === "#toc") {
        const ci = pages.on || !hasAudio ? chapterOfSent(pages.sent) : curChap;
        markToc(ci);
        const li = $(`#toc-list li[data-ch="${ci}"]`);
        if (li) el.scrollTop = li.offsetTop - (el.clientHeight - li.offsetHeight) / 2;
      }
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
    $("#set-lock-text").checked = !!settings.lockText; $("#set-justify").checked = settings.justify !== false;
    $("#set-word-style").value = settings.wordStyle; $("#set-hide-ui").checked = byDevice("hideUi"); $("#set-immersive").checked = byDevice("immersive"); $("#set-pause-hidden").checked = !!settings.pauseHidden;
    document.querySelectorAll("#set-theme button").forEach((b) => b.classList.toggle("on", b.dataset.v === settings.theme));
    $("#btn-focus").classList.toggle("on", settings.dimMode !== "off"); $("#btn-focus-top").classList.toggle("on", settings.dimMode !== "off");
    if (hasAudio && audio.playbackRate !== settings.speed) audio.playbackRate = settings.speed;  // a speed taken from another device plays
    $("#speed").value = String(settings.speed);
  }
  // Settings go to the server as one object. This page may have sat open for hours, so a change made here first
  // takes whatever another device saved since this page last heard, and only the keys changed here win over it.
  let changedKeys = null;  // changed while the server's copy is being fetched
  function saveSettings(...keys) {
    const known = store.get("rs:settingsAt", 0);  // the copy this page had before the change
    persistSettings();
    if (changedKeys) { keys.forEach((k) => changedKeys.add(k)); return; }
    changedKeys = new Set(keys);
    fetchSettings().then((r) => {
      const mine = {}; changedKeys.forEach((k) => (mine[k] = settings[k])); changedKeys = null;
      // a stamp newer than the one this change replaced, and not this page's own write
      const at = (r && r.settingsAt) || 0;
      if (!r || !r.settings || at <= known || at === store.get("rs:settingsAt", 0)) return;
      Object.assign(settings, migrateSettings(r.settings), mine); applySettings(); syncSettingsUI(); persistSettings();
    });
  }
  const bind = (sel, key, conv = (v) => v) => $(sel).addEventListener("input", (e) => { settings[key] = conv(e.target.type === "checkbox" ? e.target.checked : e.target.value); applySettings(); syncSettingsUI(); saveSettings(key); });
  bind("#set-font", "font", Number); bind("#set-lh", "lh", Number); bind("#set-width", "width", Number);
  // before the bind below, so the copy it caches and sends carries the dim mode to come back to
  $("#set-dim").addEventListener("input", (e) => { if (e.target.value !== "off") settings.lastDim = e.target.value; });
  bind("#set-family", "family"); bind("#set-sent", "sent"); bind("#set-word", "word"); bind("#set-dim", "dimMode"); bind("#set-scroll", "scroll"); bind("#set-click-word", "clickWord");
  bind("#set-ui", "ui"); bind("#set-weight", "weight", Number); bind("#set-rewind", "rewind"); bind("#set-offset", "offset", (v) => Number(v) / 1000);
  $("#set-offset").addEventListener("input", () => update(true));
  bind("#set-justify", "justify");
  // the sentence being spoken on the lock screen: the app's narrator only, a browser has no lock screen
  bind("#set-lock-text", "lockText"); $("#set-lock-text").closest("label").hidden = !native;
  bind("#set-word-style", "wordStyle"); bind("#set-hide-ui", "hideUi"); bind("#set-pause-hidden", "pauseHidden"); bind("#set-immersive", "immersive");
  // switched off while playing: the bars come back now, not at the next pause
  $("#set-hide-ui").addEventListener("input", () => { if (!byDevice("hideUi")) { showPlayer(); document.body.classList.remove("idle"); } });
  $("#set-theme").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; settings.theme = b.dataset.v; applySettings(); syncSettingsUI(); saveSettings("theme"); });
  syncSettingsUI();

  // ---------------- notes ----------------
  // A note is a string, or an object with marks, pictures and paragraphs: `\n\n` parts paragraphs, `\n` breaks
  // a line (verse), and `kinds` gives the ranges that are not plain paragraphs.
  const NOTE_KIND = { verse: "verse", cite: "cite", subtitle: "subtitle", "text-author": "author", author: "author", epigraph: "cite", date: "author" };
  function noteHtml(nt) {
    const o = typeof nt === "string" ? { text: nt } : nt, text = o.text || "", mk = marksOf(o), kinds = o.kinds || [];
    const pics = [...(o.pics || [])].sort((x, y) => x.pos - y.pos);
    // a paragraph's lines go together while their kind stays the same, whether `kinds` ranges cover lines or paragraphs
    const kindAt = (x) => kinds.find(([s, t]) => x >= s && x < t)?.[2];
    const parts = [];
    for (let a = 0, e; a <= text.length; a = e + 2) {
      e = text.indexOf("\n\n", a); if (e < 0) e = text.length;
      let from = a, kind = kindAt(a);
      for (let l = text.indexOf("\n", a) + 1; l > 0 && l < e; l = text.indexOf("\n", l) + 1) {
        const k = kindAt(l);
        if (k !== kind) { parts.push([from, l, kind]); from = l; kind = k; }
      }
      parts.push([from, e, kind]);
    }
    let h = "", pi = 0;
    for (let [a, e, kind] of parts) {
      while (a < e && text[a] === "\n") a++;
      while (e > a && text[e - 1] === "\n") e--;
      let body = "", p = a;
      for (; pi < pics.length && pics[pi].pos <= e; pi++) { const at = Math.max(p, pics[pi].pos); body += mk.run(p, at) + picHtml(pics[pi]); p = at; }
      body += mk.run(p, e);
      if (!body) continue;
      const k = NOTE_KIND[kind];
      h += `<p${k ? ` class="k-${k}"` : ""}>${body}</p>`;
    }
    return h;
  }
  function showNote(el) {
    const pop = $("#note-pop"), nt = book.notes[el.dataset.n];
    const html = nt ? noteHtml(nt) : "";
    if (!html) return;
    pop.innerHTML = html; pop.hidden = false; pop.scrollTop = 0;
    const place = () => {
      const r = el.getBoundingClientRect();
      pop.style.left = Math.max(8, Math.min(innerWidth - 348, r.left - 100)) + "px";
      // under the marker, or over it when the screen ends first; a long note scrolls inside (style.css caps it)
      const h = pop.offsetHeight;
      pop.style.top = Math.max(8, r.bottom + 8 + h > innerHeight - 8 ? r.top - 8 - h : r.bottom + 8) + "px";
    };
    place();
    // a picture arrives after the note is placed and makes it taller
    pop.querySelectorAll("img.pic").forEach((im) => (im.complete ? sizePic(im) : im.addEventListener("load", () => { sizePic(im); if (!pop.hidden) place(); }, { once: true })));
  }
  addEventListener("click", (e) => { if (!e.target.closest("#note-pop, .nref")) $("#note-pop").hidden = true; if (!e.target.closest("#sprint-menu, #btn-sprint, #pg-sprint")) $("#sprint-menu").hidden = true; });

  // ---------------- finished: reading to the end of the main text marks the book read ----------------
  // The end is where the back matter (bibliography, indexes, «Об авторе») begins: its sentence and the last
  // word before it, stamped by the pipeline (text_end, audio_end; 30 s of slack for the timing). A copy
  // stamped before that ends at the last spread and the last minute. Only reading crosses it, a page turned
  // forward or the narrator playing on, never a jump; and the very end of the book is a second line, for a
  // reader who said «not yet» at the first. In the app the narrator is the player's: it marks the book itself
  // and tells the page («finished»). A book already read is never marked again.
  let bookMeta = {};
  const isDone = () => (remote.shelf ? remote.shelf === "done" : bookMeta.state?.status === "done");
  function markDone() {
    if (isDone()) return;  // always marked at the end: a mistake is undone right there or by the status
    const day = today(), was = Array.isArray(remote.finished) ? remote.finished : [];
    const finished = was.includes(day) ? was : [...was, day], at = Date.now();
    Object.assign(remote, { shelf: "done", shelfAt: at, finished, finishedAt: at });
    putState({ shelf: "done", shelfAt: at, finished, finishedAt: at });
    showDone(was.includes(day) ? null : day);
  }
  // the quiet line: no sound, no dialog, the narrator plays on; «отменить» puts the book back to «Читаю»
  let doneTimer = 0, doneDay = null;
  function showDone(day) {
    doneDay = day; $("#done-pill").hidden = false;
    clearTimeout(doneTimer); doneTimer = setTimeout(() => { $("#done-pill").hidden = true; }, 10000);
  }
  $("#done-undo").onclick = () => {
    clearTimeout(doneTimer); $("#done-pill").hidden = true;
    const at = Date.now(), finished = (Array.isArray(remote.finished) ? remote.finished : []).filter((d) => d !== doneDay);
    Object.assign(remote, { shelf: "reading", shelfAt: at, finished, finishedAt: at });
    putState({ shelf: "reading", shelfAt: at, finished, finishedAt: at });
  };
  // a page turned forward from before a line onto or past it
  function turnedOnto(from, to) {
    const last = pages.total - 1, te = Number(bookMeta.text_end) || 0;
    const end = te > 0 && te <= sentEls.length ? Math.min(last, spreadOfSent(te - 1)) : last;
    if ((from < end && to >= end) || (from < last && to >= last)) markDone();
  }
  // the narrator played across the line: two ticks less than 5 s apart, so a seek over it does not count
  let heardAt = -1;
  function heard(t) {
    const ae = Number(bookMeta.audio_end) || 0, end = ae ? ae - 30 : duration - 60;
    if (heardAt >= 0 && t > heardAt && t - heardAt < 5 && heardAt < end && t >= end) markDone();
    heardAt = t;
  }
  // «Прочитана 7 октября», or how many times and the last
  function doneLine() {
    const days = (Array.isArray(remote.finished) ? remote.finished : []).filter((d) => typeof d === "string").sort();
    const n = days.length, times = n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? "раза" : "раз";
    if (n > 1) return `Прочитана ${n} ${times}, последний — ${dayName(days[n - 1])}<br>`;
    if (n === 1) return `Прочитана ${dayName(days[0])}<br>`;
    if (!isDone()) return "";
    return `Прочитана${bookMeta.state?.finishedOn ? " " + dayName(bookMeta.state.finishedOn) : ""}<br>`;
  }

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
  // «1 240 слов», «3 дня»; time read as minutes and hours, «1:43» would read as either
  const plural = (n, one, few, many) => {
    n = Math.round(n); const m = n % 100, k = n % 10;
    return n.toLocaleString("ru") + " " + (m > 10 && m < 15 ? many : k === 1 ? one : k > 1 && k < 5 ? few : many);
  };
  const mins = (sec) => { const m = Math.round(sec / 60); return m >= 60 ? `${Math.floor(m / 60)} ч ${m % 60} мин` : `${m} мин`; };
  function renderStats() {
    const st = store.get("rs:stats:" + slug, { days: {} });
    const days = Object.keys(st.days).sort();
    const tot = days.reduce((a, k) => a + st.days[k].sec, 0), totW = days.reduce((a, k) => a + st.days[k].words, 0);
    const td = st.days[today()] || { sec: 0, words: 0 };
    // days are UTC dates (today(), and the app's the same): counted and named in UTC, so a bar's weekday is its date's.
    // A day not read yet does not break the streak: it runs to yesterday until today has its minute
    let streak = 0; const dt = new Date();
    if (!(td.sec > 60)) dt.setUTCDate(dt.getUTCDate() - 1);
    for (;;) { const k = dt.toISOString().slice(0, 10); if (st.days[k]?.sec > 60) { streak++; dt.setUTCDate(dt.getUTCDate() - 1); } else break; }
    const listening = hasAudio && duration && !pages.on;
    const pct = listening ? Math.round((audio.currentTime / duration) * 100) : pagesPct();
    const week = []; const d2 = new Date();
    for (let i = 6; i >= 0; i--) { const x = new Date(d2); x.setUTCDate(d2.getUTCDate() - i); const k = x.toISOString().slice(0, 10); week.push({ k, sec: st.days[k]?.sec || 0, wd: ["вс", "пн", "вт", "ср", "чт", "пт", "сб"][x.getUTCDay()] }); }
    const max = Math.max(60, ...week.map((w) => w.sec));
    const bars = `<div class="bars">${week.map((w) => `<div class="${w.k === today() ? "today" : ""}${w.sec ? "" : " zero"}" style="height:${Math.max(4, (w.sec / max) * 100)}%" title="${mins(w.sec)}"></div>`).join("")}</div>
      <div class="bars-labels">${week.map((w) => `<span>${w.wd}</span>`).join("")}</div>`;
    $("#stats").innerHTML = `${doneLine()}Сегодня: <b>${mins(td.sec)}</b>, ${plural(td.words, "слово", "слова", "слов")}<br>Всего: <b>${mins(tot)}</b>, ${plural(totW, "слово", "слова", "слов")}<br>`
      + `${streak ? `Серия: <b>${plural(streak, "день", "дня", "дней")}</b><br>` : ""}`
      + `Прогресс книги: <b>${pct}%</b>${listening ? " · осталось " + fmt((duration - audio.currentTime) / audio.playbackRate) : ""}${bars}`;
  }

  // ---------------- sprint timer ----------------
  // `base`: words from the sprint's stretches in the other mode; `breakEnd`: a rest break running
  const sprint = { end: null, timer: 0, minutes: 0, words: 0, base: 0, breakEnd: null, stopAtSentence: false };
  // the time left: in the top bar's badge; beside the camera island there is no room in the bar for it, so
  // there it is the sprint button's own label and, with the bars away, the right ear's
  function countdown(text, short = text, ending = false) {
    const badge = $("#sprint-badge"); badge.hidden = false; badge.textContent = text; badge.classList.toggle("ending", ending);
    ["#btn-sprint", "#pg-sprint"].forEach((b) => { $(b).classList.add("on"); $(b).dataset.left = short; $(b).classList.toggle("ending", ending); });
    sprintLeft = text; paintEars();
  }
  $("#btn-sprint").onclick = $("#pg-sprint").onclick = (e) => {
    e.stopPropagation(); const m = $("#sprint-menu"); m.hidden = !m.hidden; $("#sprint-stop").hidden = !sprint.end && !sprint.breakEnd;
    $("#sprint-stop").textContent = sprint.breakEnd ? "Закончить перерыв" : "Остановить спринт";
    m.style.right = Math.max(8, innerWidth - e.currentTarget.getBoundingClientRect().right) + "px";  // under its own button
  };
  $("#sprint-menu").addEventListener("click", (e) => { const b = e.target.closest("button[data-min]"); if (b) startSprint(+b.dataset.min); });
  $("#sprint-stop").onclick = () => { stopSprint(); $("#sprint-menu").hidden = true; };
  $("#sprint-close").onclick = () => { $("#sprint-done").hidden = true; };
  $("#sprint-break").onclick = () => { $("#sprint-done").hidden = true; startBreak(5); };
  $("#break-close").onclick = () => { $("#break-done").hidden = true; };
  $("#break-sprint").onclick = () => { $("#break-done").hidden = true; startSprint(sprint.minutes || 25); };
  $("#sprint-again").onclick = () => { $("#sprint-done").hidden = true; startSprint(sprint.minutes); };
  // the dialogs close as the others do: Esc, or a tap beside the box
  function closeModals() { $("#sprint-done").hidden = true; $("#break-done").hidden = true; }
  document.querySelectorAll(".modal").forEach((m) => m.addEventListener("click", (e) => { if (e.target === m) closeModals(); }));
  // rest break between sprints: audio stays paused, badge counts down, soft chime at the end
  function startBreak(min) {
    stopSprint(); if (!audio.paused) audio.pause();
    const end = sprint.breakEnd = Date.now() + min * 60000;
    const tick = () => {
      const left = end - Date.now();
      if (left <= 0) { stopSprint(); chime(); $("#break-done").hidden = false; return; }
      countdown("перерыв " + fmt(left / 1000), fmt(left / 1000));
    };
    sprint.timer = setInterval(tick, 500); tick();
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
    stopSprint(); if (pages.on) session.stop();  // a sprint in pages counts the words read from its start
    sprint.minutes = min; sprint.end = Date.now() + min * 60000; sprint.words = 0; sprint.base = 0; sprint.startWord = curWord;
    $("#sprint-menu").hidden = true;
    const tick = () => {
      const left = sprint.end - Date.now();
      if (left <= 0) { clearInterval(sprint.timer); countdown("финиш…", "…", true); if (audio.paused || pages.on) finishSprint(); else sprint.stopAtSentence = true; return; }
      countdown(fmt(left / 1000), undefined, left < 60000);
    };
    sprint.timer = setInterval(tick, 500); tick();
    if (pages.on && !document.hidden) session.start();
    if (audio.paused && !pages.on && hasAudio) play();
  }
  function stopSprint() {
    clearInterval(sprint.timer); sprint.end = null; sprint.breakEnd = null; sprint.stopAtSentence = false;
    $("#sprint-badge").hidden = true; $("#sprint-badge").classList.remove("ending");
    ["#btn-sprint", "#pg-sprint"].forEach((b) => { $(b).classList.remove("on", "ending"); delete $(b).dataset.left; });
    sprintLeft = ""; paintEars();
  }
  function finishSprint() {
    session.stop();
    const words = sprint.base + (pages.on ? Math.round(sprint.words) : Math.max(0, curWord - Math.max(0, sprint.startWord ?? curWord)));
    stopSprint();
    if (pages.on && !document.hidden) session.start();  // reading goes on after the sprint, and so does its time
    $("#sprint-summary").textContent = `${sprint.minutes} мин · ${plural(words, "слово", "слова", "слов")}`;
    $("#sprint-done").hidden = false;
  }

  load().catch((e) => { $("#loading").textContent = "Ошибка: " + e.message; console.error(e); });
})();
