PYTHON ?= $(shell command -v python3.12 || command -v python3)
PY := .venv/bin/python
# the library the server and add_book use: $READSYNC_BOOKS, else the menu-bar app's, else this checkout's books/
APP_BOOKS := $(HOME)/Library/Application Support/readsync/books
BOOKS ?= $(or $(READSYNC_BOOKS),$(shell [ -e "$(APP_BOOKS)" ] && echo "$(APP_BOOKS)" || echo books))

.PHONY: setup serve test lint fmt add-book align compact app dmg ios-sim ios-device ios-autoinstall

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
	$(PY) pipeline/align.py "$(BOOKS)/$(slug)"

# make compact slug=my-book   (re-encode the audio to AAC-LC mono 48 kbit/s once the timing is checked to hold)
compact:
	$(PY) pipeline/compact.py "$(BOOKS)/$(slug)"

# the iPhone app in the simulator: build, install, launch (needs Xcode, xcodegen and the iOS simulator)
SIM ?= iPhone 17
ios-sim:
	cd ios && xcodegen generate -q && xcodebuild -quiet -project Readsync.xcodeproj -scheme Readsync \
		-destination 'platform=iOS Simulator,name=$(SIM)' -derivedDataPath build CODE_SIGNING_ALLOWED=NO build
	xcrun simctl boot '$(SIM)' 2>/dev/null || true
	xcrun simctl install '$(SIM)' ios/build/Build/Products/Debug-iphonesimulator/Readsync.app
	xcrun simctl launch '$(SIM)' io.github.diasbro.readsync

# the connected iPhone, signed with the Apple ID signed in to Xcode (a free one signs for 7 days)
ios-device:
	ios/reinstall.sh --now

# reinstall on the iPhone every 3 days while it is reachable, so a free signature never runs out. It builds
# the menu-bar app's copy of main when there is one, not whatever this working tree holds right now.
AGENT := $(HOME)/Library/LaunchAgents/io.github.diasbro.readsync.ios.plist
ios-autoinstall:
	mkdir -p "$(dir $(AGENT))"
	printf '%s\n' '<?xml version="1.0" encoding="UTF-8"?>' \
	  '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">' \
	  '<plist version="1.0"><dict>' \
	  '<key>Label</key><string>io.github.diasbro.readsync.ios</string>' \
	  '<key>ProgramArguments</key><array><string>/bin/bash</string><string>-c</string>' \
	  '<string>f="$$HOME/Library/Application Support/readsync/src/ios/reinstall.sh"; [ -x "$$f" ] || f="$(CURDIR)/ios/reinstall.sh"; exec "$$f"</string></array>' \
	  '<key>StandardOutPath</key><string>$(HOME)/Library/Logs/readsync-ios.log</string>' \
	  '<key>StandardErrorPath</key><string>$(HOME)/Library/Logs/readsync-ios.log</string>' \
	  '<key>StartInterval</key><integer>3600</integer>' \
	  '<key>RunAtLoad</key><true/>' \
	  '<key>LowPriorityIO</key><true/><key>Nice</key><integer>10</integer>' \
	  '</dict></plist>' > "$(AGENT)"
	launchctl bootout gui/$$(id -u) "$(AGENT)" 2>/dev/null || true
	launchctl bootstrap gui/$$(id -u) "$(AGENT)"
	@echo "каждый час проверка, раз в 3 дня переустановка; журнал: ~/Library/Logs/readsync-ios.log"
