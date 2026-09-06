PYTHON ?= $(shell command -v python3.12 || command -v python3)
PY := .venv/bin/python

.PHONY: setup serve test lint fmt add-book align

setup:
	$(PYTHON) -m venv .venv && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -e ".[dev]"

serve:
	$(PY) serve.py

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check . && $(PY) -m ruff format --check . && node --check reader/app.js

fmt:
	$(PY) -m ruff check --fix . && $(PY) -m ruff format .

fonts:
	$(PY) tools/subset_fonts.py

# make add-book slug=my-book text="https://..." audio="https://..." [narrator="..."]
add-book:
	$(PY) pipeline/add_book.py $(slug) --text "$(text)" --audio "$(audio)" $(if $(narrator),--narrator "$(narrator)",)

# make align slug=my-book   (re-run the precise MMS pass)
align:
	$(PY) pipeline/align.py books/$(slug)
