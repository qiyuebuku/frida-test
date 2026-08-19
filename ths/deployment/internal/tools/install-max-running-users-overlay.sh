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
