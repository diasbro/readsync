#!/bin/bash
# Build readsync.app, and with --dmg the disk image to install it from.
# The app carries everything it needs to run on any Mac: both processor architectures and its own
# Python with the importers. The code it serves is a copy for the first run; after that git updates
# it in place, so a new disk image is only needed when this launcher itself changes.
set -euo pipefail
cd "$(dirname "$0")/.."

NAME=readsync
BUNDLE_ID=org.readsync.app
OUT=dist
APP="$OUT/$NAME.app"
BUILD=build
CACHE="$BUILD/cache"
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)
PAYLOAD_VERSION=$(git rev-parse --short HEAD 2>/dev/null || echo dev)
# edits not yet committed get a name of their own: an app that already holds the clean commit would skip them
DIRTY=""
if ! git diff --quiet HEAD -- 2>/dev/null; then
  DIRTY=1
  PAYLOAD_VERSION="$PAYLOAD_VERSION-dirty-$(git diff HEAD -- | shasum | cut -c1-7)"
fi
REPO=$(git remote get-url origin 2>/dev/null || echo "")
# the app updates itself from this address on Macs that have no GitHub key: always https, never ssh
REPO=$(echo "$REPO" | sed -E 's#^git@github\.com:#https://github.com/#')
PY_TAG=20260901          # python-build-standalone release
PY_VERSION=3.13.15
PY_SHORT=3.13
MACOS_MIN=13.0

rm -rf "$APP" "$OUT/$NAME.dmg"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$CACHE" "$BUILD/bin"

# the icon: the same mark the page shows, scaled into an iconset
ICONSET="$BUILD/icon.iconset"
rm -rf "$ICONSET"; mkdir -p "$ICONSET"
for size in 16 32 64 128 256 512; do
  sips -z $size $size reader/favicon.png --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  sips -z $((size * 2)) $((size * 2)) reader/favicon.png --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/$NAME.icns"

# the launcher, for both processors in one binary
SOURCES=(app/Readsync.swift app/Payload.swift app/Server.swift app/Browser.swift app/Cloud.swift app/Menu.swift app/main.swift)
for target in arm64 x86_64; do
  swiftc -O -target "$target-apple-macos$MACOS_MIN" -o "$BUILD/bin/$NAME-$target" "${SOURCES[@]}"
done
lipo -create -output "$APP/Contents/MacOS/$NAME" "$BUILD/bin/$NAME-arm64" "$BUILD/bin/$NAME-x86_64"

# a Python of its own, one per processor, with the packages the importers need. Nothing to install
# on the Mac it lands on, and nothing of the machine's own Python is touched.
# pip runs on the Python of the processor this build runs on, and installs for both
HOST=$(uname -m)
HOST_PY="$PWD/$APP/Contents/Resources/python/$HOST/bin/python3"
fetch_python() {  # <bundle arch> <build arch> <pip platform>
  local arch=$1 build_arch=$2 platform=$3
  local file="cpython-$PY_VERSION+$PY_TAG-$build_arch-apple-darwin-install_only_stripped.tar.gz"
  local url="https://github.com/astral-sh/python-build-standalone/releases/download/$PY_TAG/$file"
  # downloaded beside its place and renamed when whole: a cut-short download is never taken for the file
  if [ ! -f "$CACHE/$file" ]; then
    curl -fsSL -o "$CACHE/$file.part" "$url"
    mv "$CACHE/$file.part" "$CACHE/$file"
  fi
  local dest="$APP/Contents/Resources/python/$arch"
  mkdir -p "$dest"
  tar -xzf "$CACHE/$file" -C "$dest" --strip-components=1
  local lib="$dest/lib/python$PY_SHORT"
  rm -rf "$lib/idlelib" "$lib/tkinter" "$lib/turtledemo" "$lib/test" "$lib/ensurepip" \
    "$dest/include" "$dest/share"
  "$HOST_PY" -m pip install \
    --quiet --disable-pip-version-check --no-cache-dir \
    --platform "$platform" --only-binary=:all: --python-version "$PY_SHORT" --implementation cp \
    --target "$lib/site-packages" beautifulsoup4 lxml pypdf
  find "$dest" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
}
if [ "$HOST" = arm64 ]; then  # this processor's first: its pip installs for both
  fetch_python arm64 aarch64 macosx_11_0_arm64
  fetch_python x86_64 x86_64 macosx_10_13_x86_64
else
  fetch_python x86_64 x86_64 macosx_10_13_x86_64
  fetch_python arm64 aarch64 macosx_11_0_arm64
fi
# the host's pip warms its own caches while installing for the other side: drop them again
find "$APP/Contents/Resources/python" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true

# the code it serves on the first run, as it is in the working tree: the files git tracks, so no scratch
# file rides along. Every top-level module goes (the server imports them), the tests and tools do not.
PAYLOAD="$APP/Contents/Resources/payload"
mkdir -p "$PAYLOAD"
git ls-files -- ':(glob)*.py' sources pipeline reader | tar -cf - -T - | tar -x -C "$PAYLOAD"
# the server must start from the payload alone: a module left out fails here, not on a fresh install
CHECK_BOOKS=$(mktemp -d)
(cd "$PAYLOAD" && PYTHONDONTWRITEBYTECODE=1 READSYNC_BOOKS="$CHECK_BOOKS" "$HOST_PY" -c "import serve") \
  || { rm -rf "$CHECK_BOOKS"; echo "в коде для приложения чего-то не хватает: serve не импортируется" >&2; exit 1; }
rm -rf "$CHECK_BOOKS"
# updates always come from origin/main (Payload.update): code main does not hold is replaced by it then
if [ -n "$DIRTY" ] || ! git merge-base --is-ancestor HEAD origin/main 2>/dev/null; then
  echo "внимание: код не из origin/main ($(git rev-parse --abbrev-ref HEAD)${DIRTY:+, с правками}), первое обновление заменит его на main"
fi

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$NAME</string>
  <key>CFBundleDisplayName</key><string>readsync</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleVersion</key><string>$PAYLOAD_VERSION</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleExecutable</key><string>$NAME</string>
  <key>CFBundleIconFile</key><string>$NAME</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>$MACOS_MIN</string>
  <key>LSUIElement</key><true/>
  <key>RSRepository</key><string>$REPO</string>
  <key>RSPayloadVersion</key><string>$PAYLOAD_VERSION</string>
  <key>RSPython</key><string>$PY_SHORT</string>
  <key>NSAppleEventsUsageDescription</key><string>Чтобы открывать библиотеку в уже открытой вкладке, а не в новой.</string>
</dict>
</plist>
PLIST

# nothing compiled at build time is sealed in: Python writes its caches outside the bundle (PYTHONPYCACHEPREFIX)
find "$APP" -name "__pycache__" -type d -prune -exec rm -rf {} +
codesign --force --deep --sign - "$APP"  # ad-hoc: an identity for the system, not a developer's name
echo "собрано: $APP ($VERSION, код $PAYLOAD_VERSION, $(du -sh "$APP" | cut -f1))"

if [ "${1:-}" = "--dmg" ]; then
  STAGE=$(mktemp -d)
  cp -R "$APP" "$STAGE/"
  ln -s /Applications "$STAGE/Applications"
  hdiutil create -quiet -volname "$NAME" -srcfolder "$STAGE" -ov -format UDZO "$OUT/$NAME.dmg"
  rm -rf "$STAGE"
  echo "собрано: $OUT/$NAME.dmg ($(du -sh "$OUT/$NAME.dmg" | cut -f1))"
fi
