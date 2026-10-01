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
fetch_python() {  # <bundle arch> <build arch> <pip platform>
  local arch=$1 build_arch=$2 platform=$3
  local file="cpython-$PY_VERSION+$PY_TAG-$build_arch-apple-darwin-install_only_stripped.tar.gz"
  local url="https://github.com/astral-sh/python-build-standalone/releases/download/$PY_TAG/$file"
  [ -f "$CACHE/$file" ] || curl -fsSL -o "$CACHE/$file" "$url"
  local dest="$APP/Contents/Resources/python/$arch"
  mkdir -p "$dest"
  tar -xzf "$CACHE/$file" -C "$dest" --strip-components=1
  local lib="$dest/lib/python$PY_SHORT"
  rm -rf "$lib/idlelib" "$lib/tkinter" "$lib/turtledemo" "$lib/test" "$lib/ensurepip" \
    "$dest/include" "$dest/share"
  "$APP/Contents/Resources/python/arm64/bin/python3" -m pip install \
    --quiet --disable-pip-version-check --no-cache-dir \
    --platform "$platform" --only-binary=:all: --python-version "$PY_SHORT" --implementation cp \
    --target "$lib/site-packages" beautifulsoup4 lxml pypdf
  find "$dest" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
}
fetch_python arm64 aarch64 macosx_11_0_arm64      # first: its pip installs for both
fetch_python x86_64 x86_64 macosx_10_13_x86_64
# the arm64 pip warms its own caches while installing for the other side: drop them again
find "$APP/Contents/Resources/python" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true

# the code it serves on the first run, as it is in the working tree; git decides what belongs to the
# project, so no scratch file rides along
mkdir -p "$APP/Contents/Resources/payload"
git ls-files -co --exclude-standard -- serve.py library.py sources pipeline reader \
  | tar -cf - -T - | tar -x -C "$APP/Contents/Resources/payload"

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
