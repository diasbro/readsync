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

# the first paired physical iPhone (cable or Wi-Fi); over Wi-Fi it is listed without a connection until one
# is asked for, so it is asked for below rather than looked for here
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
    if hw.get("reality") == "physical" and conn.get("pairingState") == "paired":
        print(hw.get("udid", "")); break
PY
)
rm -f "$list"

# not reachable now (away from this Wi-Fi, off): said once in the log, and a day before the signature runs out
# (7 days after the last install) a notification asks for the phone; tried again in an hour
WARNED="$HOME/Library/Application Support/readsync/ios-warned"
AWAY="$HOME/Library/Application Support/readsync/ios-away"  # there while the phone is out of reach
unreachable() {
  [ -e "$AWAY" ] || { echo "$(date '+%F %T') phone not reachable: install postponed"; touch "$AWAY"; }
  local warned
  warned=$(cat "$WARNED" 2>/dev/null || echo 0)
  if [ "$last" -gt 0 ] && [ $((now - last)) -ge $((6 * 24 * 3600)) ] && [ $((now - warned)) -ge $((12 * 3600)) ]; then
    osascript -e 'display notification "Подпись приложения на телефоне скоро закончится: подключите телефон к той же сети Wi-Fi или кабелем" with title "readsync"' >/dev/null 2>&1 || true
    echo "$now" >"$WARNED"
  fi
  exit 0
}
if [ -z "$device" ]; then unreachable; fi
xcrun devicectl device info details --device "$device" --timeout 60 >/dev/null 2>&1 || unreachable
rm -f "$AWAY"

team=$(security find-certificate -a -c "Apple Development" -p 2>/dev/null | openssl x509 -noout -subject 2>/dev/null \
  | sed -n 's/.*OU *= *\([A-Z0-9]\{10\}\).*/\1/p' | head -1 || true)  # none found: said just below
if [ -z "$team" ]; then echo "$(date '+%F %T') no Apple Development certificate: sign in to Xcode once"; exit 1; fi

echo "$(date '+%F %T') reinstalling on $device"
READSYNC_TEAM=$team xcodegen generate -q
xcodebuild -quiet -project Readsync.xcodeproj -scheme Readsync -configuration Release \
  -destination 'generic/platform=iOS' -derivedDataPath "$BUILD" -allowProvisioningUpdates build
xcrun devicectl device install app --device "$device" "$BUILD/Build/Products/Release-iphoneos/Readsync.app" >/dev/null
rm -rf "$BUILD"  # 100+ MB of build products are not worth keeping for three days
echo "$now" >"$STATE"
echo "$(date '+%F %T') done"
