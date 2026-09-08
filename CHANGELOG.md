# Changelog

## Unreleased

- Library: the sign by the «Библиотека» heading (or `/`) opens one line that finds and adds
  (title, link or file); titles without text wait in the catalog;
  a card is renamed by the pencil next to its name; a search that no title matches as a phrase is
  retried by the words of the query and ranked by them, and an author's surname lists their books;
  «искать издания» turns the card's title into the query field, a picked edition names the card
  the way its catalog does and ↻ loads it again; cards carry three icons (read, text and audio, delete) and open in place with editions from
  fantasy-worlds, Flibusta and Coollib (translator, year, size, author lookup), own link or file,
  audio and precise alignment; shelves, covers, header line.
- Code: `library.py` and a `sources/` package (one module per catalog) behind `serve.py`;
  `reader/common.js`, `library.js`, `app.js`.
- Import: EPUB, PDF, TXT, HTML, multi-volume text, multi-part audio, illustrations.
- Mac app: a menu bar launcher (`make dmg`) that carries both architectures and its own Python,
  optional start at login, and updates by git pull instead of a new disk image.
- Reader: page mode, server-side state and settings, fonts and themes, focus tools.

## 0.1.0 — 2026-09-05

- Pipeline: text extraction, caption anchoring, MMS forced alignment, whisper fallback.
- Reader with synced highlighting, auto-scroll, sprint timer, stats. Server with Range support.
