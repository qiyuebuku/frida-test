#!/usr/bin/env bash
# Shared implementation for collector template maintenance.
set -euo pipefail

THS_PACKAGE="${THS_PACKAGE:-com.hexin.plat.android}"
THS_ANDROID_SERIAL="${THS_ANDROID_SERIAL:-emulator-5556}"
ADB_BIN="${ADB_BIN:-adb}"

die() {
    echo "ERROR: $*" >&2
    exit 1
}

adb_shell() {
    "${ADB_BIN}" -s "${THS_ANDROID_SERIAL}" shell "$@"
}

assert_device_ready() {
    [[ "$("${ADB_BIN}" -s "${THS_ANDROID_SERIAL}" get-state 2>/dev/null || true)" == "device" ]] \
        || die "Android device is not ready: ${THS_ANDROID_SERIAL}"
    [[ "$(adb_shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == "1" ]] \
        || die "Android has not completed boot"
}

ensure_adb_root() {
    "${ADB_BIN}" -s "${THS_ANDROID_SERIAL}" root >/dev/null
    for _ in {1..30}; do
        if adb_shell id 2>/dev/null | grep -q 'uid=0'; then
            local caps
            caps="$(adb_shell sh -c "grep '^CapEff:' /proc/self/status" | tr -d '\r')"
            [[ "${caps}" != 'CapEff:'$'\t''0000000000000000' ]] \
                || die "root adbd has no effective capabilities"
            return 0
        fi
        sleep 1
    done
    die "root adbd is required for app-data provisioning"
}

package_app_id() {
    local app_id
    app_id="$(adb_shell dumpsys package "${THS_PACKAGE}" \
        | sed -n 's/^[[:space:]]*userId=\([0-9][0-9]*\).*/\1/p' \
        | head -n 1 | tr -d '\r')"
    [[ "${app_id}" =~ ^[0-9]+$ ]] || die "cannot resolve appId for ${THS_PACKAGE}"
    printf '%s\n' "${app_id}"
}

sha256_file() {
    sha256sum "$1" | awk '{print $1}'
}
