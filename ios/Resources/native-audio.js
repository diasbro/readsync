// The reader's <audio>, played by the app instead: the same properties, methods and events the reader
// uses, backed by AVPlayer, which keeps playing on a locked screen. The app reports each change once
// (an anchor of time, moment and rate); the current time between reports is worked out here, so there
// is no second clock ticking out of step with the reader's own.
(() => {
  "use strict";
  const send = (cmd, extra) => window.webkit.messageHandlers.audio.postMessage(Object.assign({ cmd }, extra || {}));
  const listeners = {};
  const st = { paused: true, duration: NaN, rate: 1, src: "", error: null, t: 0, at: performance.now(), pausedAt: 0 };
  const now = () => (st.paused ? st.t : st.t + ((performance.now() - st.at) / 1000) * st.rate);
  const emit = (type) => (listeners[type] || []).slice().forEach((l) => {
    if (l.once) listeners[type] = listeners[type].filter((x) => x !== l);
    try { l.fn.call(audio, { type, target: audio }); } catch (e) { console.error(e); }
  });
  const audio = {
    addEventListener(type, fn, opts) { (listeners[type] = listeners[type] || []).push({ fn, once: !!(opts && opts.once) }); },
    removeEventListener(type, fn) { listeners[type] = (listeners[type] || []).filter((l) => l.fn !== fn); },
    get currentTime() { return now(); },
    set currentTime(v) { st.t = v; st.at = performance.now(); send("seek", { t: v }); },
    // the saved place put back when the page loads: a seek the reader did not choose, and the app may say so
    restore(t) { st.t = t; st.at = performance.now(); send("seek", { t, chosen: false }); },
    get duration() { return st.duration; },
    get paused() { return st.paused; },
    get error() { return st.error; },
    // when the app's player paused (ms since 1970): a pause on the lock screen reaches the page only at unlock
    get pausedAt() { return st.pausedAt; },
    get playbackRate() { return st.rate; },
    // the time so far was played at the old rate: anchored before the new one counts
    set playbackRate(r) { st.t = now(); st.at = performance.now(); st.rate = r; send("rate", { rate: r }); },  // the app keeps a paused player paused
    get src() { return st.src; },
    set src(v) { st.src = v; send("load", { src: v }); },
    play() { st.t = now(); st.at = performance.now(); st.paused = false; send("play"); return Promise.resolve(); },
    pause() { st.t = now(); st.at = performance.now(); st.paused = true; send("pause"); },
    // the app speaks: {event, t, paused, rate, duration}
    _update(s) {
      const wasPaused = st.paused;
      st.t = s.t; st.at = performance.now();
      if (s.paused != null) st.paused = s.paused;
      if (s.rate) st.rate = s.rate;
      if (s.duration) st.duration = s.duration;
      st.pausedAt = s.pausedAt || 0;
      st.error = s.error ? { message: s.error } : s.event === "loadedmetadata" ? null : st.error;
      if (s.event === "snapshot") {
        // back from a locked screen: a play or pause that happened meanwhile is told now
        if (wasPaused !== st.paused) emit(st.paused ? "pause" : "play");
        emit("timeupdate");  // and a position that moved meanwhile is painted
      } else if (s.event) emit(s.event);
    },
  };
  window.nativeAudio = audio;
})();
