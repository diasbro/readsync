# readsync

Local reading tool: a browser page plays an audiobook and highlights the sentence and word being
spoken; books without audio are read as pages. Personal, single-user; macOS; Python 3.12+; no build
step, no framework.

## Layout
- `pipeline/` — one script per stage, `add_book.py` runs them; each takes a book directory. Timing
  comes from caption anchors first and is optionally refined by MMS alignment in short windows (a
  whole book does not fit in one alignment pass).
- `serve.py` routing only; `library.py` books, jobs; `state.py` reading state; `sources/` one module
  per catalog, the priority list in `sources/__init__.py`.
- Reading state is per device: each device writes only `books/<slug>/state/<device>.json` and reads
  the merge (newest value per key, statistics summed, a sentence from another edition dropped).
  `tests/state_vectors.json` is the contract the iPhone app's copy of the merge follows too.
- `book.toml` carries `id` (fixed), `edition` (new with new text or audio) and `files` (sizes), stamped
  last by `pipeline/manifest.py`: a copy synced file by file is whole when the sizes match. Every
  stage ends with `pipeline/tidy.py`, so downloads and derived audio never outlive their use.
- `reader/` — static UI: `common.js` shared, `library.js` library page, `app.js` reader.
- `app/` — Mac menu-bar launcher. It keeps the code in `~/Library/Application Support/readsync/src`
  and updates it with git; `READSYNC_BOOKS` and `READSYNC_PYTHON` tell the server and the pipeline
  where books and Python are. Its menu can move the library to iCloud Drive/readsync/books.
- `ios/` — the iPhone app (`project.yml` for XcodeGen, `make ios-sim`). It shows the bundled `reader/`
  in a web view and plays audio natively (`ios/Resources/native-audio.js` is the `<audio>` stand-in);
  books are read from its own copy of the shared library. It never searches or adds books.
- `books/` — per-book data and reading state, never tracked.

## Commands
`make setup`, `make serve` (http://127.0.0.1:8765), `make test`, `make lint`, `make dmg`, `make ios-sim`.

## Conventions
- Python: ruff (line length 120), type hints, `from __future__ import annotations`, stdlib first.
- JS: no dependencies; each page is an IIFE over the globals of `common.js`; library actions go
  through the `data-act` table.
- Reading state and settings live on the server (in the iPhone app: in Swift); `localStorage` is
  only a cache. The phone keeps its own reader settings.
- `timing.json` is the only time source: `words[i] = [block, charStart, charEnd, t0, t1]`,
  monotonic in `t0`.
- Position: audio keeps `pos` (seconds), page mode keeps `sent` (sentence index). Page numbers are
  derived from the sentence, never the reverse, and spreads are measured in fractional pixels.
- Page mode is CSS multi-column with `scrollLeft` steps, no per-page DOM. Audio-mode behaviour
  (dimming, highlights, autoscroll, hide-UI) never applies to it.
- The reader stays light at runtime; heavy work belongs in the pipeline.
- `book.json` and `timing.json` are large: inspect them with Python, never print them whole.
- Branch names use dashes; commits follow Conventional Commits.

## Boundaries
- Never commit book text, audio or reading state.
- No network at runtime beyond the book search and the downloads the user asked for. iCloud is
  file sync done by the system, not a call the app makes.
- Only features that help reading and focus, each behind a toggle.
