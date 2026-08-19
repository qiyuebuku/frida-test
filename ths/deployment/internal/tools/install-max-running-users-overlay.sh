#!/usr/bin/env bash
# Android overlay install implementation.
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-/home/yuyangruan/android-sdk}"
ADB="${ANDROID_SDK_ROOT}/platform-tools/adb"
SERIAL="${THS_ANDROID_SERIAL:-emulator-5556}"
BUILD_TOOLS_VERSION="${ANDROID_BUILD_TOOLS_VERSION:-35.0.0}"
APKSIGNER="${ANDROID_SDK_ROOT}/build-tools/${BUILD_TOOLS_VERSION}/apksigner"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TEMP_DIR}"' EXIT

"${SOURCE_DIR}/build-max-running-users-overlay.sh" "${TEMP_DIR}/overlay-unsigned.apk"
keytool -genkeypair -keystore "${TEMP_DIR}/overlay.keystore" \
    -storepass android -keypass android -alias androiddebugkey \
    -dname "CN=THS Collector Runtime,O=Smart Fund,C=CN" \
    -keyalg RSA -keysize 2048 -validity 10000 >/dev/null 2>&1
"${APKSIGNER}" sign --ks "${TEMP_DIR}/overlay.keystore" \
    --ks-pass pass:android --key-pass pass:android \
    --out "${TEMP_DIR}/THSMaxRunningUsersOverlay.apk" \
    "${TEMP_DIR}/overlay-unsigned.apk"

mkdir -p "${TEMP_DIR}/module/system/product/overlay"
cp "${TEMP_DIR}/THSMaxRunningUsersOverlay.apk" \
    "${TEMP_DIR}/module/system/product/overlay/THSMaxRunningUsersOverlay.apk"
cat >"${TEMP_DIR}/module/module.prop" <<'EOF'
id=ths_multiuser_overlay
name=THS Multi-user Runtime Overlay
version=2.0
versionCode=2
author=smart-fund
description=Allow ten concurrent Android users for isolated THS collectors
EOF
(cd "${TEMP_DIR}/module" && zip -qr "${TEMP_DIR}/module.zip" .)

"${ADB}" -s "${SERIAL}" push "${TEMP_DIR}/module.zip" \
    /data/local/tmp/ths_multiuser_overlay.zip >/dev/null
"${ADB}" -s "${SERIAL}" shell su -c \
    'magisk --install-module /data/local/tmp/ths_multiuser_overlay.zip'

# Updating an already-mounted Magisk module through /data/adb/modules can hit
# the module's active bind mount instead of its backing file.  Root adbd gives
# us Magisk's data mirror, which is the stable backing path used on the next
# boot.  Re-copy the two generated files there so upgrades (for example 4 ->
# 10 users) cannot silently retain the previous overlay.
"${ADB}" -s "${SERIAL}" root >/dev/null
"${ADB}" -s "${SERIAL}" wait-for-device
MAGISK_MODULE_ROOT="/debug_ramdisk/.magisk/mirror/data/adb/modules/ths_multiuser_overlay"
if "${ADB}" -s "${SERIAL}" shell test -d "${MAGISK_MODULE_ROOT}"; then
    "${ADB}" -s "${SERIAL}" push "${TEMP_DIR}/THSMaxRunningUsersOverlay.apk" \
        "${MAGISK_MODULE_ROOT}/system/product/overlay/THSMaxRunningUsersOverlay.apk" \
        >/dev/null
    "${ADB}" -s "${SERIAL}" push "${TEMP_DIR}/module/module.prop" \
        "${MAGISK_MODULE_ROOT}/module.prop" >/dev/null
    "${ADB}" -s "${SERIAL}" shell chmod 0644 \
        "${MAGISK_MODULE_ROOT}/module.prop" \
        "${MAGISK_MODULE_ROOT}/system/product/overlay/THSMaxRunningUsersOverlay.apk"
fi
