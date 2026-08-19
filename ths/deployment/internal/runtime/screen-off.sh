#!/usr/bin/env bash
# Installed runtime implementation; not a public deployment entrypoint.
set -euo pipefail

ADB_BIN="${ADB_BIN:-/home/yuyangruan/android-sdk/platform-tools/adb}"
ADB_SERIAL="${ADB_SERIAL:-emulator-5556}"

adb_device() {
    timeout --signal=TERM --kill-after=5 30 \
        "${ADB_BIN}" -s "${ADB_SERIAL}" "$@"
}

# Production must finish on the trading owner without rendering the emulator.
# Do not issue a redundant switch-user 0: Android may run background-user
# cleanup even when user 0 is already current, killing Hexin because it holds
# RECORD_AUDIO permission and destroying the freshly initialized trade session.
current_user="$(adb_device shell am get-current-user 2>/dev/null | tr -d '\r')"
if [[ "${current_user}" != "0" ]]; then
    adb_device shell am switch-user 0 >/dev/null
    for _ in {1..30}; do
        [[ "$(adb_device shell am get-current-user 2>/dev/null | tr -d '\r')" == "0" ]] && break
        sleep 1
    done
fi
# KEYCODE_SLEEP is idempotent (unlike POWER, which would wake an off display).
adb_device shell input keyevent KEYCODE_SLEEP >/dev/null

for _ in {1..15}; do
    power_state="$(adb_device shell dumpsys power 2>/dev/null | tr -d '\r')"
    if grep -Eq 'Display Power: state=OFF|mWakefulness=Asleep' <<<"${power_state}"; then
        echo "Android production display is off on ${ADB_SERIAL} (user 0)"
        exit 0
    fi
    sleep 1
done

echo "Android display did not enter the off state on ${ADB_SERIAL}" >&2
exit 1
