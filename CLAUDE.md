# readsync — instructions for AI coding agents

Local immersion-reading tool: a browser page plays an audiobook and highlights the sentence and
word being spoken. Personal, single-user app; macOS first; Python 3.11+; no build step, no framework.

## Layout
- `pipeline/` — one script per stage: `extract_text.py` (fantasy-worlds and generic HTML),
  `extract_fb2.py`, `extract_epub.py`, `extract_txt.py` (text → `book.json`), `merge_books.py`
  (volumes → one book), `anchors.py` (captions → word anchors), `timing_from_anchors.py`
  (→ `timing.json`), `align.py` (MMS forced alignment, refines `timing.json`), `transcribe.py`
  (faster-whisper fallback), `add_book.py` (orchestrator: several `--text` = volumes, several
  `--audio` = parts, audio-only on an existing slug attaches audio). Each takes a book directory.
- `reader/` — static UI: `index.html`, `app.js` (vanilla JS, one IIFE), `style.css`, `fonts/`.
- `serve.py` — stdlib HTTP server with Range support: `/api/books`, `/api/state/<slug>` (per-book
  reading state, last-writer-wins by `<key>At` timestamps), `/api/settings` (global reader
  settings), `/api/wishlist`, `/api/search` (fantasy-worlds JSON + Flibusta/Coollib OPDS, the only
  runtime network calls besides the pipeline downloads), `/api/where/<slug>`, `/api/add`
  (multipart, launches `add_book.py` as a background job), `/api/jobs`.
- `books/` — all per-book data and reading state; nothing under it is tracked by git.
- `tests/` — pytest for the pipeline. `docs/` — design notes and ADRs.

## Commands
- Setup: `make setup` (venv + `pip install -e ".[dev]"`; `pyproject.toml` is the only dependency list)
- Run: `make serve` (`.venv/bin/python serve.py`) → http://127.0.0.1:8765
- Test/lint: `make test`, `make lint` (ruff + pytest + `node --check reader/app.js`)
- Add a book: `make add-book slug=... text=... audio=...`

## Conventions
- Python: ruff (line length 120), type hints, `from __future__ import annotations`, stdlib first.
- JS: no dependencies, no bundler; keep `app.js` a single IIFE. Reading state and settings live
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
- Branch names use dashes, never slashes. Commit messages: imperative subject, why in the body.

## Boundaries
- Never commit book text, audio or reading state; sources are recorded in each `book.toml`.
- No analytics, no accounts, no network calls at runtime beyond the book search and the
  pipeline downloads the user asked for. Everything else is local.
- Do not add features "because readers usually have them": the owner wants only what helps
  focus training and eye comfort, with a toggle for each.
