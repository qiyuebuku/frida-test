#!/usr/bin/env bash
# Existing collector data must not be overwritten without explicit authorization.
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=collector-template-common.sh
source "${SOURCE_DIR}/../tools/collector-template-common.sh"

TEMPLATE="${1:-${THS_COLLECTOR_TEMPLATE:-}}"
[[ -n "${TEMPLATE}" && -f "${TEMPLATE}" ]] || die "collector template artifact is required"
assert_device_ready

tmp_dir="$(mktemp -d)"
device_dir="/data/local/tmp/ths-collector-provision-$$"
cleanup() {
    adb_shell su -c "rm -rf ${device_dir}" >/dev/null 2>&1 || true
    rm -rf -- "${tmp_dir}"
}
trap cleanup EXIT
tar -xzf "${TEMPLATE}" -C "${tmp_dir}"
[[ -f "${tmp_dir}/manifest.env" && -f "${tmp_dir}/collector-ce.tar" && -f "${tmp_dir}/collector-de.tar" ]] \
    || die "invalid collector template layout"
# shellcheck disable=SC1091
source "${tmp_dir}/manifest.env"
[[ "${THS_TEMPLATE_FORMAT:-}" == "1" ]] || die "unsupported collector template format"
[[ "${THS_PACKAGE:-}" == "com.hexin.plat.android" ]] || die "unexpected package in template"
[[ "$(sha256_file "${tmp_dir}/collector-ce.tar")" == "${THS_CE_SHA256}" ]] || die "CE checksum mismatch"
[[ "$(sha256_file "${tmp_dir}/collector-de.tar")" == "${THS_DE_SHA256}" ]] || die "DE checksum mismatch"

mapfile -t existing_users < <(adb_shell pm list users | sed -n 's/.*UserInfo{\([0-9][0-9]*\):.*/\1/p')
users_to_provision=()
for user_id in {10..17}; do
    if ! printf '%s\n' "${existing_users[@]}" | grep -qx "${user_id}"; then
        created="$(adb_shell pm create-user "ths-collector-${user_id}" | sed -n 's/.* user id \([0-9][0-9]*\).*/\1/p' | tr -d '\r')"
        [[ "${created}" == "${user_id}" ]] \
            || die "fresh AVD user allocation is not deterministic: expected ${user_id}, got ${created:-unknown}"
        existing_users+=("${user_id}")
        users_to_provision+=("${user_id}")
    fi
    adb_shell cmd package install-existing --user "${user_id}" "${THS_PACKAGE}" >/dev/null
    if ! printf '%s\n' "${users_to_provision[@]}" | grep -qx "${user_id}"; then
        if adb_shell su -c "find /data/user/${user_id}/${THS_PACKAGE} -mindepth 1 -print -quit" \
            | grep -q .; then
            echo "preserving existing collector data for user=${user_id}"
        else
            users_to_provision+=("${user_id}")
        fi
    fi
done

app_id="$(package_app_id)"
# This rooted production image labels /data/local/tmp as shell_data_file and
# rejects chmod even for Magisk uid 0.  mkdir's 0755 directory is sufficient:
# artifacts are short-lived, checksummed before use, and removed by the trap.
adb_shell su -c "mkdir -p ${device_dir}"
"${ADB_BIN}" -s "${THS_ANDROID_SERIAL}" push "${tmp_dir}/collector-ce.tar" "${device_dir}/ce.tar" >/dev/null
"${ADB_BIN}" -s "${THS_ANDROID_SERIAL}" push "${tmp_dir}/collector-de.tar" "${device_dir}/de.tar" >/dev/null

for user_id in "${users_to_provision[@]}"; do
    app_uid=$((user_id * 100000 + app_id))
    ce="/data/user/${user_id}/${THS_PACKAGE}"
    de="/data/user_de/${user_id}/${THS_PACKAGE}"
    adb_shell am force-stop --user "${user_id}" "${THS_PACKAGE}" >/dev/null
    adb_shell su -c "mkdir -p '${ce}' '${de}'; find '${ce}' -mindepth 1 -delete; find '${de}' -mindepth 1 -delete; tar -xf '${device_dir}/ce.tar' -C '${ce}'; tar -xf '${device_dir}/de.tar' -C '${de}'; chown -R '${app_uid}:${app_uid}' '${ce}' '${de}'; restorecon -RF '${ce}' '${de}' >/dev/null"
    echo "provisioned collector user=${user_id} uid=${app_uid}"
done
if ((${#users_to_provision[@]} == 0)); then
    echo "all collector users already contain app data; nothing was overwritten"
fi
adb_shell su -c sync
