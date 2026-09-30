# readsync

Local reading tool: a browser page plays an audiobook and highlights the sentence and word being
spoken; books without audio are read as pages. Personal, single-user; macOS; Python 3.12+; no build
step, no framework.

## Layout
- `pipeline/` — one script per stage, `add_book.py` runs them; each takes a book directory. Timing
  comes from caption anchors first and is optionally refined by MMS alignment in short windows (a
  whole book does not fit in one alignment pass).
- `serve.py` routing only; `library.py` books, state, jobs; `sources/` one module per catalog, the
  priority list in `sources/__init__.py`.
- `reader/` — static UI: `common.js` shared, `library.js` library page, `app.js` reader.
- `app/` — Mac menu-bar launcher. It keeps the code in `~/Library/Application Support/readsync/src`
  and updates it with git; `READSYNC_BOOKS` and `READSYNC_PYTHON` tell the server and the pipeline
  where books and Python are.
- `books/` — per-book data and reading state, never tracked.

## Commands
`make setup`, `make serve` (http://127.0.0.1:8765), `make test`, `make lint`, `make dmg`.

## Conventions
- Python: ruff (line length 120), type hints, `from __future__ import annotations`, stdlib first.
- JS: no dependencies; each page is an IIFE over the globals of `common.js`; library actions go
  through the `data-act` table.
- Reading state and settings live on the server; `localStorage` is only a cache.
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
- No network at runtime beyond the book search and the downloads the user asked for.
- Only features that help reading and focus, each behind a toggle.
