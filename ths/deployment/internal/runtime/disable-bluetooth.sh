#!/usr/bin/env bash
# Installed runtime implementation; not a public deployment entrypoint.
set -euo pipefail

ADB_BIN="${ADB_BIN:-/home/yuyangruan/android-sdk/platform-tools/adb}"
ADB_SERIAL="${ADB_SERIAL:-emulator-5556}"

for _ in {1..120}; do
    if [[ "$(${ADB_BIN} -s "${ADB_SERIAL}" get-state 2>/dev/null || true)" == "device" ]] \
        && [[ "$(${ADB_BIN} -s "${ADB_SERIAL}" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == "1" ]]; then
        break
    fi
    sleep 2
done

if [[ "$(${ADB_BIN} -s "${ADB_SERIAL}" get-state 2>/dev/null || true)" != "device" ]]; then
    echo "Android device ${ADB_SERIAL} did not become ready" >&2
    exit 1
fi

${ADB_BIN} -s "${ADB_SERIAL}" shell cmd bluetooth_manager disable >/dev/null 2>&1 || true

while read -r user_id; do
    [[ -n "${user_id}" ]] || continue
    ${ADB_BIN} -s "${ADB_SERIAL}" shell settings --user "${user_id}" put global bluetooth_on 0
    ${ADB_BIN} -s "${ADB_SERIAL}" shell pm disable-user --user "${user_id}" \
        com.android.bluetooth >/dev/null
    ${ADB_BIN} -s "${ADB_SERIAL}" shell pm disable-user --user "${user_id}" \
        com.android.bluetoothmidiservice >/dev/null 2>&1 || true
done < <(
    ${ADB_BIN} -s "${ADB_SERIAL}" shell cmd user list \
        | sed -n 's/.*UserInfo{\([0-9][0-9]*\):.*/\1/p'
)
