# readsync — instructions for AI coding agents

Local immersion-reading tool: a browser page plays an audiobook and highlights the sentence and
word being spoken. Personal, single-user app; macOS first; Python 3.11+; no build step, no framework.

## Layout
- `pipeline/` — one script per stage: `extract_text.py` (fantasy-worlds and generic HTML),
  `extract_fb2.py`, `extract_epub.py`, `extract_txt.py`, `extract_pdf.py` (text → `book.json`;
  the PDF one drops running heads and page numbers, joins hyphenated words and reuses the
  plain-text block builder), `merge_books.py`
  (volumes → one book), `anchors.py` (captions → word anchors), `timing_from_anchors.py`
  (→ `timing.json`), `align.py` (MMS forced alignment, refines `timing.json`), `transcribe.py`
  (faster-whisper fallback), `add_book.py` (orchestrator: several `--text` = volumes, several
  `--audio` = parts, audio-only on an existing slug attaches audio). Each takes a book directory.
- `reader/` — static UI, no build step: `index.html`, `common.js` (helpers + reader settings shared
  by both pages), `library.js` (library page: one line finds and adds, cards open in place),
  `app.js` (the reader), `style.css`, `fonts/`.
- `serve.py` — stdlib HTTP server with Range support; routing only. `library.py` — books on disk,
  reading state, settings, saved titles, background jobs. `sources/` — one module per text source
  (`fantasy_worlds.py`, `flibusta.py`, `coollib.py` on top of `opds.py`); `SOURCES` in
  `sources/__init__.py` is the priority list, a new catalog is a new module plus one entry.
  API: `/api/books`, `/api/state/<slug>` (per-book reading state, last-writer-wins by `<key>At`
  timestamps), `/api/settings` (global reader settings), `/api/wishlist` (titles saved without text,
  with their last search result), `/api/search` (all sources, editions as the catalogs describe
  them plus the books of an author the query names; the catalogs match a phrase inside a title, so
  a query no title contains is retried without its first or last word and by its longest words,
  rows are ranked by how much of the query their title carries, mirrored copies and formats the
  pipeline cannot open are dropped, and a query is answered from a 15-minute cache; the only
  runtime network calls besides the pipeline downloads),
  `/api/hits/<slug>` (the search result a book was picked from, with the query it came from), `/api/where/<slug>`, `/api/add`
  (multipart, launches `add_book.py` as a background job; `replace=1` swaps the text of an
  existing book), `/api/align/<slug>`, `/api/jobs`, `PUT /api/books/<slug>` (rename: only the
  title line of `book.toml` changes), `DELETE /api/books/<slug>` (the page
  confirms first).
- `books/` — all per-book data and reading state; nothing under it is tracked by git.
- `tests/` — pytest for the pipeline. `docs/` — design notes and ADRs.

## Commands
- Setup: `make setup` (venv + `pip install -e ".[dev]"`; `pyproject.toml` is the only dependency list)
- Run: `make serve` (`.venv/bin/python serve.py`) → http://127.0.0.1:8765
- Test/lint: `make test`, `make lint` (ruff + pytest + `node --check reader/app.js`)
- Add a book: `make add-book slug=... text=... audio=...`

## Conventions
- Python: ruff (line length 120), type hints, `from __future__ import annotations`, stdlib first.
- JS: no dependencies, no bundler; one file per page, each an IIFE over the globals of
  `common.js`. Cards, panels and actions in `library.js` are small functions and a `data-act`
  table: add a section or an action without touching the rest. Reading state and settings live
  on the server (`/api/state`, `/api/settings`); `localStorage` (`rs:*` keys via `store.get/set`)
  is only a cache and must never be the sole copy of anything.
- Timing model: `timing.json.words[i] = [block, charStart, charEnd, t0, t1]`, monotonic in `t0`.
  Everything in the reader is derived from it; do not add a second time source.
- Position model: audio books keep `pos` (seconds); page mode and text-only books keep `sent`
  (global sentence index). Switching modes converts through the sentence, never through pixels.
- Mode-specific behaviour (dimming, highlights, autoscroll, hide-UI, pause-on-leave) belongs to
  audio mode only; page mode and text-only books must never inherit it.
- Page mode is CSS multi-column with `column-fill: auto` and horizontal `scrollLeft` steps; keep
  it that way (no per-page DOM splitting).
- Data files are large: never `cat` `book.json`/`timing.json`/`*.json3`; inspect with Python.
- The reader must stay light at runtime (10 Hz sync loop, no per-frame DOM work).
- Heavy CPU work belongs in the pipeline, runs once per book, and defaults to low priority.
- Branch names use dashes, never slashes. Commit messages follow Conventional Commits
  (`feat:`, `fix:`, `docs:`, `chore:`, optional scope), subject only unless the why is not obvious.

## Boundaries
- Never commit book text, audio or reading state; sources are recorded in each `book.toml`.
- No analytics, no accounts, no network calls at runtime beyond the book search and the
  pipeline downloads the user asked for. Everything else is local.
- Do not add features "because readers usually have them": only what helps reading and focus,
  each behind a toggle.
