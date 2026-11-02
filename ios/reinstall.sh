#!/bin/bash
# Keep the iPhone app installed: a free Apple ID signs it for 7 days only. Run hourly by launchd
# (`make ios-autoinstall`); when fewer than 4 days of the signature are left and the phone is reachable
# (cable or the same Wi-Fi), the app is built with a newly issued profile and installed again. The reading
# state lives in iCloud and in the app's own folder, which a reinstall keeps.
set -euo pipefail
cd "$(dirname "$0")"

SUPPORT="$HOME/Library/Application Support/readsync"
EXPIRES="$SUPPORT/ios-expires"  # when the installed app's signature ends (epoch seconds)
WARNED="$SUPPORT/ios-warned"    # when the last notification was shown
AWAY="$SUPPORT/ios-away"        # there while the phone is out of reach
LOG="$HOME/Library/Logs/readsync-ios.log"
RENEW_LEFT=$((4 * 24 * 3600))
BUILD="$HOME/Library/Caches/readsync/ios-build"
APP="$BUILD/Build/Products/Release-iphoneos/Readsync.app"
BUNDLE=io.github.diasbro.readsync
PROFILES=("$HOME/Library/Developer/Xcode/UserData/Provisioning Profiles" "$HOME/Library/MobileDevice/Provisioning Profiles")
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

mkdir -p "$SUPPORT" "$(dirname "$LOG")"
exec >>"$LOG" 2>&1

now=$(date +%s)
expires=$(cat "$EXPIRES" 2>/dev/null || echo 0)
if [ "${1:-}" != "--now" ] && [ $((expires - now)) -gt $RENEW_LEFT ]; then exit 0; fi

stamp() { date '+%F %T'; }

# a notification at most every 12 hours; `urgent` only once less than a day of the signature is left
notify() {
  local warned
  warned=$(cat "$WARNED" 2>/dev/null || echo 0)
  [ $((now - warned)) -ge $((12 * 3600)) ] || return 0
  if [ "${2:-}" = urgent ] && [ $((expires - now)) -gt $((24 * 3600)) ]; then return 0; fi
  osascript -e "display notification \"$1\" with title \"readsync\"" >/dev/null 2>&1 || true
  echo "$now" >"$WARNED"
}

fail() {
  echo "$(stamp) $1"
  rm -rf "$BUILD"
  notify "$2" "${3:-}"
  exit 1
}

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

# not reachable now (away from this Wi-Fi, off): said once in the log; tried again in an hour
unreachable() {
  [ -e "$AWAY" ] || { echo "$(stamp) phone not reachable: install postponed"; touch "$AWAY"; }
  notify "Подпись приложения на телефоне скоро закончится: подключите телефон к той же сети Wi-Fi или кабелем" urgent
  exit 0
}
if [ -z "$device" ]; then unreachable; fi
xcrun devicectl device info details --device "$device" --timeout 60 >/dev/null 2>&1 || unreachable
rm -f "$AWAY"

team=$(security find-certificate -a -c "Apple Development" -p 2>/dev/null | openssl x509 -noout -subject 2>/dev/null \
  | sed -n 's/.*OU *= *\([A-Z0-9]\{10\}\).*/\1/p' | head -1 || true)
[ -n "$team" ] || fail "no Apple Development certificate: sign in to Xcode" \
  "Приложение на телефоне не подписать: войдите в Apple ID в Xcode (Settings → Accounts)"

# the profile Xcode keeps for this app is used as long as it is valid, and the app then lives only until it
# ends: it goes, so the build below is signed with a new 7-day profile
for dir in "${PROFILES[@]}"; do
  for f in "$dir"/*.mobileprovision; do
    [ -f "$f" ] || continue
    if security cms -D -i "$f" 2>/dev/null | grep -q "<string>$team\.$BUNDLE</string>"; then rm -f "$f"; fi
  done
done

echo "$(stamp) reinstalling on $device"
rm -rf "$BUILD"  # a build left by a failed run would carry its old profile
READSYNC_TEAM=$team xcodegen generate -q
xcodebuild -quiet -project Readsync.xcodeproj -scheme Readsync -configuration Release \
  -destination 'generic/platform=iOS' -derivedDataPath "$BUILD" -allowProvisioningUpdates build \
  || fail "build or signing failed: is Xcode signed in to the Apple ID? (Settings → Accounts)" \
    "Приложение на телефоне не подписать: войдите в Apple ID в Xcode (Settings → Accounts)"

until_=$(security cms -D -i "$APP/embedded.mobileprovision" 2>/dev/null | /usr/bin/python3 -c '
import plistlib, sys
print(int(plistlib.loads(sys.stdin.buffer.read())["ExpirationDate"].timestamp()))' || echo 0)
[ "$until_" -gt $((now + 6 * 24 * 3600)) ] \
  || fail "the build carries an old profile (ends $(date -r "$until_" '+%F %T' 2>/dev/null)): not installed" \
    "Приложение на телефоне не подписать: войдите в Apple ID в Xcode (Settings → Accounts)"

xcrun devicectl device install app --device "$device" "$APP" >/dev/null \
  || fail "install failed (is the phone locked?): tried again in an hour" \
    "Подпись приложения на телефоне скоро закончится: разблокируйте телефон рядом с компьютером" urgent
rm -rf "$BUILD"  # 100+ MB of build products are not worth keeping for three days
echo "$until_" >"$EXPIRES"
rm -f "$SUPPORT/ios-install"  # the old record of the last install, replaced by the end of the signature
echo "$(stamp) done: signed until $(date -r "$until_" '+%F %T')"
