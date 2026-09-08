#!/bin/bash
# Build readsync.app, and with --dmg the disk image to install it from.
# The app is a launcher: it carries a copy of the code for the first run and updates it with git
# afterwards, so a new disk image is only needed when this launcher itself changes.
set -euo pipefail
cd "$(dirname "$0")/.."

NAME=readsync
BUNDLE_ID=org.readsync.app
OUT=dist
APP="$OUT/$NAME.app"
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)
PAYLOAD_VERSION=$(git rev-parse --short HEAD 2>/dev/null || echo dev)
REPO=$(git remote get-url origin 2>/dev/null || echo "")

rm -rf "$APP" "$OUT/$NAME.dmg"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# the icon: the same mark the page shows, scaled into an iconset
ICONSET=$(mktemp -d)/icon.iconset
mkdir -p "$ICONSET"
for size in 16 32 64 128 256 512; do
  sips -z $size $size reader/favicon.png --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  sips -z $((size * 2)) $((size * 2)) reader/favicon.png --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/$NAME.icns"

# the launcher itself
swiftc -O -o "$APP/Contents/MacOS/$NAME" \
  app/Readsync.swift app/Payload.swift app/Server.swift app/Menu.swift app/main.swift

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
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
  <key>RSRepository</key><string>$REPO</string>
  <key>RSPayloadVersion</key><string>$PAYLOAD_VERSION</string>
</dict>
</plist>
PLIST

codesign --force --deep --sign - "$APP"  # ad-hoc: enough for a personal Mac, not for distribution
echo "собрано: $APP ($VERSION, код $PAYLOAD_VERSION)"

if [ "${1:-}" = "--dmg" ]; then
  STAGE=$(mktemp -d)
  cp -R "$APP" "$STAGE/"
  ln -s /Applications "$STAGE/Applications"
  hdiutil create -quiet -volname "$NAME" -srcfolder "$STAGE" -ov -format UDZO "$OUT/$NAME.dmg"
  rm -rf "$STAGE"
  echo "собрано: $OUT/$NAME.dmg"
fi
