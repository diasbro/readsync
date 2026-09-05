PY := .venv/bin/python

.PHONY: setup serve test lint fmt add-book align

setup:
	python3.12 -m venv .venv && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -e ".[dev]"

serve:
	python3 serve.py

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check . && $(PY) -m ruff format --check . && node --check reader/app.js

fmt:
	$(PY) -m ruff check --fix . && $(PY) -m ruff format .

# make add-book slug=my-book text="https://..." audio="https://..." [narrator="..."]
add-book:
	$(PY) pipeline/add_book.py $(slug) --text "$(text)" --audio "$(audio)" $(if $(narrator),--narrator "$(narrator)",)

# make align slug=my-book   (re-run the precise MMS pass)
align:
	$(PY) pipeline/align.py books/$(slug)
