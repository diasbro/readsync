PYTHON ?= $(shell command -v python3.12 || command -v python3)
PY := .venv/bin/python

.PHONY: setup serve test lint fmt add-book align app dmg ios-sim

setup:
	$(PYTHON) -m venv .venv && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -e ".[dev]"

serve:
	$(PY) serve.py

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check . && $(PY) -m ruff format --check . && for f in reader/*.js; do node --check $$f || exit 1; done

fmt:
	$(PY) -m ruff check --fix . && $(PY) -m ruff format .

fonts:
	$(PY) tools/subset_fonts.py

# make add-book slug=my-book text="https://..." audio="https://..." [narrator="..."]
add-book:
	$(PY) pipeline/add_book.py $(slug) --text "$(text)" --audio "$(audio)" $(if $(narrator),--narrator "$(narrator)",)

# the Mac app: a menu bar launcher that serves the reader; dmg is what you install it from
app:
	app/build.sh

dmg:
	app/build.sh --dmg

# make align slug=my-book   (re-run the precise MMS pass)
align:
	$(PY) pipeline/align.py books/$(slug)

# the iPhone app in the simulator: build, install, launch (needs Xcode, xcodegen and the iOS simulator)
SIM ?= iPhone 17
ios-sim:
	cd ios && xcodegen generate -q && xcodebuild -quiet -project Readsync.xcodeproj -scheme Readsync \
		-destination 'platform=iOS Simulator,name=$(SIM)' -derivedDataPath build CODE_SIGNING_ALLOWED=NO build
	xcrun simctl boot '$(SIM)' 2>/dev/null || true
	xcrun simctl install '$(SIM)' ios/build/Build/Products/Debug-iphonesimulator/Readsync.app
	xcrun simctl launch '$(SIM)' io.github.diasbro.readsync
