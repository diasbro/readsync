#!/bin/bash
# Keep the iPhone app installed: a free Apple ID signs it for 7 days only. Run hourly by launchd
# (`make ios-autoinstall`); every 3 days, when the phone is reachable (cable or the same Wi-Fi), the app
# is built and installed again, which renews the signature. The reading state lives in iCloud and in the
# app's own folder, which a reinstall keeps.
set -euo pipefail
cd "$(dirname "$0")"

STATE="$HOME/Library/Application Support/readsync/ios-install"
LOG="$HOME/Library/Logs/readsync-ios.log"
EVERY=$((3 * 24 * 3600))
BUILD="$HOME/Library/Caches/readsync/ios-build"
BUNDLE=io.github.diasbro.readsync
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

mkdir -p "$(dirname "$STATE")" "$(dirname "$LOG")"
exec >>"$LOG" 2>&1

last=$(cat "$STATE" 2>/dev/null || echo 0)
now=$(date +%s)
if [ "${1:-}" != "--now" ] && [ $((now - last)) -lt $EVERY ]; then exit 0; fi

# the first paired physical iPhone reachable now (cable or Wi-Fi); none: try again in an hour
list=$(mktemp)
xcrun devicectl list devices --json-output "$list" >/dev/null 2>&1 || true
device=$(/usr/bin/python3 - "$list" <<'PY'
import json, sys
try:
    devices = json.load(open(sys.argv[1]))["result"]["devices"]
except Exception:
    devices = []
for d in devices:
    hw, conn = d.get("hardwareProperties", {}), d.get("connectionProperties", {})
    if hw.get("reality") == "physical" and conn.get("pairingState") == "paired" and conn.get("tunnelState") == "connected":
        print(hw.get("udid", "")); break
PY
)
rm -f "$list"
if [ -z "$device" ]; then exit 0; fi

team=$(security find-certificate -a -c "Apple Development" -p 2>/dev/null | openssl x509 -noout -subject 2>/dev/null \
  | sed -n 's/.*OU *= *\([A-Z0-9]\{10\}\).*/\1/p' | head -1)
if [ -z "$team" ]; then echo "$(date '+%F %T') no Apple Development certificate: sign in to Xcode once"; exit 1; fi

echo "$(date '+%F %T') reinstalling on $device"
READSYNC_TEAM=$team xcodegen generate -q
xcodebuild -quiet -project Readsync.xcodeproj -scheme Readsync -configuration Release \
  -destination 'generic/platform=iOS' -derivedDataPath "$BUILD" -allowProvisioningUpdates build
xcrun devicectl device install app --device "$device" "$BUILD/Build/Products/Release-iphoneos/Readsync.app" >/dev/null
rm -rf "$BUILD"  # 100+ MB of build products are not worth keeping for three days
echo "$now" >"$STATE"
echo "$(date '+%F %T') done"
