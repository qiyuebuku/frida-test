#!/usr/bin/env bash
# Release engineering tool; not a production deployment entrypoint.
set -euo pipefail

OUTPUT="${1:?usage: $0 OUTPUT_BUNDLE}"
: "${THS_ANDROID_SDK_ARCHIVE:?required}"
: "${THS_AVD_ARCHIVE:?required}"
: "${THS_APP_APK:?required}"
: "${THS_HOOK_APK:?required}"
: "${THS_COLLECTOR_TEMPLATE:?required}"

tmp_dir="$(mktemp -d)"
cleanup() { rm -rf -- "${tmp_dir}"; }
trap cleanup EXIT

install -m 0640 "${THS_ANDROID_SDK_ARCHIVE}" "${tmp_dir}/android-sdk.tar.gz"
install -m 0640 "${THS_AVD_ARCHIVE}" "${tmp_dir}/ths-futures-avd.tar.gz"
install -m 0640 "${THS_APP_APK}" "${tmp_dir}/ths.apk"
install -m 0640 "${THS_HOOK_APK}" "${tmp_dir}/ths-hook.apk"
install -m 0600 "${THS_COLLECTOR_TEMPLATE}" "${tmp_dir}/ths-collector-template.tar.gz"

(cd "${tmp_dir}" && sha256sum \
    android-sdk.tar.gz \
    ths-futures-avd.tar.gz \
    ths.apk \
    ths-hook.apk \
    ths-collector-template.tar.gz >MANIFEST.sha256)
mkdir -p "$(dirname "${OUTPUT}")"
tar -czf "${OUTPUT}" -C "${tmp_dir}" \
    MANIFEST.sha256 android-sdk.tar.gz ths-futures-avd.tar.gz \
    ths.apk ths-hook.apk ths-collector-template.tar.gz
chmod 0640 "${OUTPUT}"
sha256sum "${OUTPUT}"
