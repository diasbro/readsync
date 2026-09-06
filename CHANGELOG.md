# Changelog

## Unreleased

- Library page: wishlist with multi-library text search (fantasy-worlds, Flibusta, Coollib),
  automatic load of exact complete matches, covers, "reading now" shelf, last-opened ordering,
  finished label, attach audio to a text-only book, the sentence you stopped at in the header.
- Import: EPUB, TXT and generic HTML extractors, multi-volume merge, multi-part audio, images.
- Reader: page mode (two-column spread), server-side state and settings, bundled fonts, themes,
  focus tools (sprints with breaks, dimming, hidden chrome), illustrations, favicon and brand mark.
- Server: state/settings/wishlist APIs, keep-alive fix, hardened job start.

## 0.1.0 — 2026-09-05

- Pipeline: fantasy-worlds HTML and FB2 extraction, caption anchoring, interpolated timing,
  MMS forced alignment (ONNX, low priority by default), faster-whisper fallback, `add_book.py`.
- Reader: synced sentence/word highlighting, auto-scroll with a reading zone, focus dimming,
  sprint timer with sentence-boundary pause and rest breaks, stats, five themes, bundled fonts.
- Server with HTTP Range support and a library page.
- Library page with an add-book form (URL or file, audio optional) and job progress; text-only books.
- Page mode: two-column spread without audio, keyboard and click turning, position kept by sentence.
- Server-side state (position, settings, stats, mode) shared across browsers.
- Bundled OFL fonts (Literata, PT Serif, Merriweather, Inter, Golos Text, IBM Plex Sans), text weight,
  sentence-level dimming, rewind to sentence start after a pause, highlight offset, weekly stats.
