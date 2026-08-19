#!/usr/bin/env bash
# Fresh-host implementation; called only after missing-state detection.
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${1:-/etc/smart-fund/ths-deployment.env}"
[[ -f "${CONFIG_FILE}" ]] || { echo "missing deployment config: ${CONFIG_FILE}" >&2; exit 1; }
# shellcheck disable=SC1090
source "${CONFIG_FILE}"

: "${THS_COLLECTOR_TEMPLATE:?THS_COLLECTOR_TEMPLATE is required}"
: "${THS_COLLECTOR_TEMPLATE_SHA256:?THS_COLLECTOR_TEMPLATE_SHA256 is required}"
: "${THS_ANDROID_SDK_ARCHIVE:?THS_ANDROID_SDK_ARCHIVE is required}"
: "${THS_ANDROID_SDK_ARCHIVE_SHA256:?THS_ANDROID_SDK_ARCHIVE_SHA256 is required}"
: "${THS_AVD_ARCHIVE:?THS_AVD_ARCHIVE is required}"
: "${THS_AVD_ARCHIVE_SHA256:?THS_AVD_ARCHIVE_SHA256 is required}"
: "${THS_APP_APK:?THS_APP_APK is required}"
: "${THS_APP_APK_SHA256:?THS_APP_APK_SHA256 is required}"
: "${THS_HOOK_APK:?THS_HOOK_APK is required}"
: "${THS_HOOK_APK_SHA256:?THS_HOOK_APK_SHA256 is required}"

verify_artifact() {
    local path="$1" expected="$2" label="$3"
    [[ -f "${path}" ]] || { echo "missing ${label}: ${path}" >&2; exit 1; }
    echo "${expected}  ${path}" | sha256sum -c - >/dev/null \
        || { echo "checksum failed for ${label}" >&2; exit 1; }
}
verify_artifact "${THS_COLLECTOR_TEMPLATE}" "${THS_COLLECTOR_TEMPLATE_SHA256}" collector-template
verify_artifact "${THS_ANDROID_SDK_ARCHIVE}" "${THS_ANDROID_SDK_ARCHIVE_SHA256}" android-sdk
verify_artifact "${THS_AVD_ARCHIVE}" "${THS_AVD_ARCHIVE_SHA256}" android-avd
verify_artifact "${THS_APP_APK}" "${THS_APP_APK_SHA256}" ths-apk
verify_artifact "${THS_HOOK_APK}" "${THS_HOOK_APK_SHA256}" hook-apk

DEPLOY_USER="${THS_DEPLOY_USER:-yuyangruan}"
DEPLOY_HOME="${THS_DEPLOY_HOME:-/home/${DEPLOY_USER}}"
id "${DEPLOY_USER}" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "${DEPLOY_USER}"
install -d -o "${DEPLOY_USER}" -g "${DEPLOY_USER}" \
    "${DEPLOY_HOME}/android-sdk" "${DEPLOY_HOME}/.android/avd"

# Both archives have a stable, path-independent layout: SDK files are rooted
# directly in android-sdk/ and AVD files directly in .android/avd/.
# Let tar detect gzip/zstd from the artifact itself. Production bundles use
# zstd for sparse AVD images, while older bundles may still be gzip archives.
tar -xf "${THS_ANDROID_SDK_ARCHIVE}" -C "${DEPLOY_HOME}/android-sdk"
tar -xf "${THS_AVD_ARCHIVE}" -C "${DEPLOY_HOME}/.android/avd"
chown -R "${DEPLOY_USER}:${DEPLOY_USER}" \
    "${DEPLOY_HOME}/android-sdk" "${DEPLOY_HOME}/.android"

ADB_BIN="${ADB_BIN:-${DEPLOY_HOME}/android-sdk/platform-tools/adb}"
export ADB_BIN THS_ANDROID_SERIAL="${THS_ANDROID_SERIAL:-emulator-5556}"

# Android system images and proprietary APKs are deployment artifacts rather
# than Git content. The script restores them from the checksummed bundle.
for required in "${ADB_BIN}" systemctl curl; do
    command -v "${required}" >/dev/null 2>&1 || [[ -x "${required}" ]] \
        || { echo "missing deployment prerequisite: ${required}" >&2; exit 1; }
done

"${SOURCE_DIR}/install-services.sh" --runtime-only
systemctl daemon-reload
systemctl enable --now ths-android-emulator.service
for _ in {1..60}; do
    [[ "$("${ADB_BIN}" -s "${THS_ANDROID_SERIAL}" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == "1" ]] && break
    sleep 2
done
[[ "$("${ADB_BIN}" -s "${THS_ANDROID_SERIAL}" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == "1" ]] \
    || { echo "Android failed to boot" >&2; exit 1; }
"${ADB_BIN}" -s "${THS_ANDROID_SERIAL}" install --no-streaming -r "${THS_APP_APK}" >/dev/null
"${ADB_BIN}" -s "${THS_ANDROID_SERIAL}" install --no-streaming -r "${THS_HOOK_APK}" >/dev/null
"${SOURCE_DIR}/../tools/install-max-running-users-overlay.sh"
app_version="$("${ADB_BIN}" -s "${THS_ANDROID_SERIAL}" shell dumpsys package com.hexin.plat.android \
    | tr -d '\r' | grep -oE 'versionCode=[0-9]+' | head -n 1 | cut -d= -f2)"
[[ "${app_version}" =~ ^[0-9]+$ ]] || { echo "cannot resolve installed THS version" >&2; exit 1; }
for unit in ths-trade-bridge.service ths-collector-bridge@.service; do
    override_dir="/etc/systemd/system/${unit}.d"
    install -d -m 0755 "${override_dir}"
    cat >"${override_dir}/artifact-build.conf" <<EOF
[Service]
Environment=THS_EXPECTED_APP_VERSION_CODE=${app_version}
Environment=THS_EXPECTED_APP_SHA256=${THS_APP_APK_SHA256}
EOF
done
systemctl daemon-reload
"${SOURCE_DIR}/provision-collectors.sh" "${THS_COLLECTOR_TEMPLATE}"

# LSPosed loads per-user scope at zygote boot. Flush userdata and reboot Android
# inside the guest so newly installed APK files are not lost to a forced QEMU
# shutdown before launching the App processes.
"${ADB_BIN}" -s "${THS_ANDROID_SERIAL}" shell sync
"${ADB_BIN}" -s "${THS_ANDROID_SERIAL}" reboot
for _ in {1..60}; do
    [[ "$("${ADB_BIN}" -s "${THS_ANDROID_SERIAL}" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == "1" ]] && break
    sleep 2
done
[[ "$("${ADB_BIN}" -s "${THS_ANDROID_SERIAL}" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == "1" ]] \
    || { echo "Android failed to reboot after collector provisioning" >&2; exit 1; }
for user_id in 0 10 11 12 13 14 15 16 17; do
    "${ADB_BIN}" -s "${THS_ANDROID_SERIAL}" shell cmd package install-existing \
        --user "${user_id}" com.yuyang.thshook >/dev/null
done
"${SOURCE_DIR}/../tools/configure-lsposed.sh"
"${ADB_BIN}" -s "${THS_ANDROID_SERIAL}" shell sync
"${ADB_BIN}" -s "${THS_ANDROID_SERIAL}" reboot
for _ in {1..60}; do
    [[ "$("${ADB_BIN}" -s "${THS_ANDROID_SERIAL}" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == "1" ]] && break
    sleep 2
done
[[ "$("${ADB_BIN}" -s "${THS_ANDROID_SERIAL}" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == "1" ]] \
    || { echo "Android failed to reboot after Hook user activation" >&2; exit 1; }

# Hook enablement/scope must be installed by the AVD image or an explicitly
# supplied repository script. Never mutate an unknown LSPosed database here.
"${SOURCE_DIR}/install-services.sh" --start-only

trade_status="$(curl -fsS --max-time 5 http://127.0.0.1:49500/stock/trade/runtime/status)"
grep -q '"write_ready":true' <<<"${trade_status}" \
    || { echo "trade runtime is not write-ready after deployment" >&2; exit 1; }
curl -fsS --max-time 5 http://127.0.0.1:49350/lb/status >/dev/null
/home/yuyangruan/android-runtime/bin/ths-screen-off.sh
echo "THS production deployment completed: 1 trade + 8 collectors"
