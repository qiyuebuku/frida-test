#!/usr/bin/env bash
# Immutable artifact installer used by fresh-host provisioning.
set -euo pipefail

BUNDLE="${1:?usage: $0 BUNDLE BUNDLE_SHA256 [RELEASE_ROOT]}"
BUNDLE_SHA256="${2:?bundle SHA-256 is required}"
RELEASE_ROOT="${3:-/opt/smart-fund-artifacts/releases}"
[[ -f "${BUNDLE}" ]] || { echo "missing bundle: ${BUNDLE}" >&2; exit 1; }
echo "${BUNDLE_SHA256}  ${BUNDLE}" | sha256sum -c - >/dev/null

release_dir="${RELEASE_ROOT}/${BUNDLE_SHA256}"
staging_dir="${release_dir}.staging.$$"
cleanup() { rm -rf -- "${staging_dir}"; }
trap cleanup EXIT
install -d -m 0750 "${RELEASE_ROOT}" "${staging_dir}"
tar -xzf "${BUNDLE}" -C "${staging_dir}"

for name in android-sdk.tar.gz ths-futures-avd.tar.gz ths.apk ths-hook.apk ths-collector-template.tar.gz MANIFEST.sha256; do
    [[ -f "${staging_dir}/${name}" ]] || { echo "bundle is missing ${name}" >&2; exit 1; }
done
(cd "${staging_dir}" && sha256sum -c MANIFEST.sha256)

if [[ ! -d "${release_dir}" ]]; then
    mv "${staging_dir}" "${release_dir}"
fi
trap - EXIT
rm -rf -- "${staging_dir}"
ln -sfn "${release_dir}" "${RELEASE_ROOT}/current"

checksum() { awk -v file="$1" '$2 == file || $2 == "*" file {print $1}' "${release_dir}/MANIFEST.sha256"; }
install -d -m 0755 /etc/smart-fund
cat >/etc/smart-fund/ths-deployment.env <<EOF
THS_ANDROID_SDK_ARCHIVE=${release_dir}/android-sdk.tar.gz
THS_ANDROID_SDK_ARCHIVE_SHA256=$(checksum android-sdk.tar.gz)
THS_AVD_ARCHIVE=${release_dir}/ths-futures-avd.tar.gz
THS_AVD_ARCHIVE_SHA256=$(checksum ths-futures-avd.tar.gz)
THS_APP_APK=${release_dir}/ths.apk
THS_APP_APK_SHA256=$(checksum ths.apk)
THS_HOOK_APK=${release_dir}/ths-hook.apk
THS_HOOK_APK_SHA256=$(checksum ths-hook.apk)
THS_COLLECTOR_TEMPLATE=${release_dir}/ths-collector-template.tar.gz
THS_COLLECTOR_TEMPLATE_SHA256=$(checksum ths-collector-template.tar.gz)
THS_DEPLOY_USER=yuyangruan
THS_DEPLOY_HOME=/home/yuyangruan
THS_ANDROID_SERIAL=emulator-5556
EOF
chmod 0600 /etc/smart-fund/ths-deployment.env
echo "deployment bundle installed: ${release_dir}"
