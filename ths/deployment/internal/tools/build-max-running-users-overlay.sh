#!/usr/bin/env bash
# Android overlay build implementation.
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-/home/yuyangruan/android-sdk}"
BUILD_TOOLS_VERSION="${ANDROID_BUILD_TOOLS_VERSION:-35.0.0}"
AAPT2="${ANDROID_SDK_ROOT}/build-tools/${BUILD_TOOLS_VERSION}/aapt2"
ANDROID_JAR="${ANDROID_SDK_ROOT}/platforms/android-30/android.jar"
OVERLAY_DIR="${SOURCE_DIR}/../assets/max-running-users-overlay"
OUTPUT_APK="${1:-${SOURCE_DIR}/THSMaxRunningUsersOverlay.apk}"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TEMP_DIR}"' EXIT

"${AAPT2}" compile --dir "${OVERLAY_DIR}/res" -o "${TEMP_DIR}/resources.zip"
"${AAPT2}" link \
    -I "${ANDROID_JAR}" \
    --manifest "${OVERLAY_DIR}/AndroidManifest.xml" \
    --auto-add-overlay \
    -o "${OUTPUT_APK}" \
    "${TEMP_DIR}/resources.zip"
