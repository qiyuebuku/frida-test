#!/usr/bin/env bash
# Controlled maintenance tool; never export from trade user 0.
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=collector-template-common.sh
source "${SOURCE_DIR}/collector-template-common.sh"

SOURCE_USER_ID="${THS_TEMPLATE_SOURCE_USER_ID:-12}"
OUTPUT="${1:-${SOURCE_DIR}/artifacts/ths-collector-template.tar.gz}"

[[ "${SOURCE_USER_ID}" =~ ^[0-9]+$ ]] || die "invalid source user id"
(( SOURCE_USER_ID >= 10 )) || die "collector template must never be exported from trade user 0"
assert_device_ready

adb_shell pm list packages --user "${SOURCE_USER_ID}" "${THS_PACKAGE}" \
    | tr -d '\r' | grep -qx "package:${THS_PACKAGE}" \
    || die "${THS_PACKAGE} is not installed for user ${SOURCE_USER_ID}"

# A collector golden source must be anonymous and must not contain any Hook
# trading role, password or seeded-account state. Refuse export instead of
# attempting to guess whether such data is safe.
for relative in \
    files/thshook_pwd.json \
    files/thshook_trade_seed.json \
    files/thshook_trade_role.json \
    shared_prefs/sp_weituo_login.xml \
    shared_prefs/sp_wt_expected_login_account.xml; do
    if adb_shell su -c "test -e /data/user/${SOURCE_USER_ID}/${THS_PACKAGE}/${relative}"; then
        die "collector source contains forbidden trading state: ${relative}"
    fi
done

tmp_dir="$(mktemp -d)"
device_prefix="/data/local/tmp/ths-collector-template-${SOURCE_USER_ID}-$$"
cleanup() {
    adb_shell su -c "rm -f ${device_prefix}-ce.tar ${device_prefix}-de.tar" >/dev/null 2>&1 || true
    rm -rf -- "${tmp_dir}"
}
trap cleanup EXIT

adb_shell am force-stop --user "${SOURCE_USER_ID}" "${THS_PACKAGE}" >/dev/null
adb_shell su -c "tar -cf ${device_prefix}-ce.tar -C /data/user/${SOURCE_USER_ID}/${THS_PACKAGE} ."
adb_shell su -c "tar -cf ${device_prefix}-de.tar -C /data/user_de/${SOURCE_USER_ID}/${THS_PACKAGE} ."
"${ADB_BIN}" -s "${THS_ANDROID_SERIAL}" pull "${device_prefix}-ce.tar" "${tmp_dir}/ce.tar" >/dev/null
"${ADB_BIN}" -s "${THS_ANDROID_SERIAL}" pull "${device_prefix}-de.tar" "${tmp_dir}/de.tar" >/dev/null

mkdir -p "${tmp_dir}/ce" "${tmp_dir}/de"
tar -xf "${tmp_dir}/ce.tar" -C "${tmp_dir}/ce"
tar -xf "${tmp_dir}/de.tar" -C "${tmp_dir}/de"

# Runtime caches, WebView cookies, databases, account/order artifacts and Hook
# secrets are not initialization state and must not enter the golden artifact.
find "${tmp_dir}/ce" "${tmp_dir}/de" -depth \
    \( -type d \( -name cache -o -name code_cache -o -name app_webview -o -name no_backup -o -name databases \) \
       -o -type f \( -iname '*thshook*' -o -iname '*weituo*' -o -iname '*authorization*' \
          -o -iname '*deal_push*' -o -iname '*cookie*' -o -iname '*account*' \
          -o -iname '*username*' -o -iname '*user_sid*' \) \) -exec rm -rf -- {} +

if find "${tmp_dir}/ce" "${tmp_dir}/de" -type f \
    \( -iname '*thshook*' -o -iname '*weituo*' -o -iname '*authorization*' \
       -o -iname '*cookie*' -o -iname '*account*' \) -print -quit | grep -q .; then
    die "sanitization validation failed"
fi

tar -cf "${tmp_dir}/collector-ce.tar" -C "${tmp_dir}/ce" .
tar -cf "${tmp_dir}/collector-de.tar" -C "${tmp_dir}/de" .
cat >"${tmp_dir}/manifest.env" <<EOF
THS_TEMPLATE_FORMAT=1
THS_PACKAGE=${THS_PACKAGE}
THS_SOURCE_USER_ID=${SOURCE_USER_ID}
THS_CE_SHA256=$(sha256_file "${tmp_dir}/collector-ce.tar")
THS_DE_SHA256=$(sha256_file "${tmp_dir}/collector-de.tar")
EOF

mkdir -p "$(dirname "${OUTPUT}")"
tar -czf "${OUTPUT}" -C "${tmp_dir}" manifest.env collector-ce.tar collector-de.tar
chmod 0600 "${OUTPUT}"
echo "collector template created: ${OUTPUT} ($(sha256_file "${OUTPUT}"))"
