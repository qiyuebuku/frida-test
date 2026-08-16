#!/usr/bin/env bash
set -euo pipefail

ADB="${ANDROID_SDK_ROOT:-/home/yuyangruan/android-sdk}/platform-tools/adb"
SERIAL="${THS_ANDROID_SERIAL:-emulator-5554}"
USERS=(0 10 11 12 13 14 15 16)
DISABLED_PACKAGES=(
    com.google.android.gms
    com.google.android.gsf
    com.google.android.googlequicksearchbox
    com.google.android.inputmethod.latin
    com.google.android.apps.messaging
)

adb_device() {
    timeout --signal=TERM --kill-after=5 60 "${ADB}" -s "${SERIAL}" "$@"
}

deadline=$((SECONDS + 300))
while (( SECONDS < deadline )); do
    if [[ "$(adb_device shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == "1" ]]; then
        break
    fi
    sleep 2
done
if (( SECONDS >= deadline )); then
    echo "Android did not finish booting before optimization" >&2
    exit 1
fi

for user_id in "${USERS[@]}"; do
    adb_device shell am start-user -w "${user_id}" >/dev/null 2>&1 || true
    for package in "${DISABLED_PACKAGES[@]}"; do
        adb_device shell pm disable-user --user "${user_id}" "${package}" \
            >/dev/null 2>&1 || true
    done
    adb_device shell settings --user "${user_id}" put system \
        screen_off_timeout 15000 >/dev/null 2>&1 || true
    adb_device shell cmd location set-location-enabled false \
        --user "${user_id}" >/dev/null 2>&1 || true
done

adb_device shell settings put global window_animation_scale 0
adb_device shell settings put global transition_animation_scale 0
adb_device shell settings put global animator_duration_scale 0
adb_device shell settings put global wifi_scan_always_enabled 0
adb_device shell settings put global ble_scan_always_enabled 0

# Keep Hook warnings and errors while avoiding thousands of logcat writes
# during normal realtime traffic. Debugging can restore verbosity with:
# adb shell setprop log.tag.THSHook V
adb_device shell setprop log.tag.THSHook W
adb_device logcat -G 1M >/dev/null 2>&1 || true
# The emulator boot property disables future starts. Stop an already-running
# daemon as a second line of defence after upgrades or restored snapshots.
adb_device shell su -c "stop iorapd" >/dev/null 2>&1 || true
