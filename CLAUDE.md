# readsync — instructions for AI coding agents

Local immersion-reading tool: a browser page plays an audiobook and highlights the sentence and
word being spoken. Single user (the repo owner), macOS, Python 3.12, no build step, no framework.

## Layout
- `pipeline/` — one script per stage: `extract_text.py` / `extract_fb2.py` (text → `book.json`),
  `anchors.py` (captions → word anchors), `timing_from_anchors.py` (→ `timing.json`),
  `align.py` (MMS forced alignment, refines `timing.json`), `transcribe.py` (faster-whisper fallback),
  `add_book.py` (orchestrator). Each takes a book directory as its argument.
- `reader/` — static UI: `index.html`, `app.js` (vanilla JS, one IIFE), `style.css`, `fonts/`.
- `serve.py` — stdlib HTTP server with Range support and `/api/books`.
- `books/<slug>/` — per-book data (gitignored except `book.toml`). Never commit text or audio.
- `tests/` — pytest for the pipeline. `docs/` — design notes and ADRs.

## Commands
- Setup: `python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"` (or `make setup`)
- Run: `python3 serve.py` → http://127.0.0.1:8765
- Test/lint: `make test`, `make lint` (ruff + pytest + `node --check reader/app.js`)
- Add a book: `make add-book slug=... text=... audio=...`

## Conventions
- Python: ruff (line length 120), type hints, `from __future__ import annotations`, stdlib first.
- JS: no dependencies, no bundler; keep `app.js` a single IIFE; settings persist in `localStorage`
  under `rs:*` keys; anything stateful goes through `store.get/set`.
- Timing model: `timing.json.words[i] = [block, charStart, charEnd, t0, t1]`, monotonic in `t0`.
  Everything in the reader is derived from it; do not add a second time source.
- Data files are large: never `cat` `book.json`/`timing.json`/`*.json3`; inspect with Python.
- The reader must stay light at runtime (10 Hz sync loop, no per-frame DOM work).
- Heavy CPU work belongs in the pipeline, runs once per book, and defaults to low priority.
- Branch names use dashes, never slashes. Commit messages: imperative subject, why in the body.

## Boundaries
- Do not download or commit copyrighted book text or audio; sources stay in `book.toml`.
- Do not add analytics, network calls at runtime, or accounts. Everything is local.
- Do not add features "because readers usually have them": the owner wants only what helps
  focus training and eye comfort, with a toggle for each.
