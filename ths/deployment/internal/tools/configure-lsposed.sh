#!/usr/bin/env bash
set -euo pipefail

ADB_BIN="${ADB_BIN:-/home/yuyangruan/android-sdk/platform-tools/adb}"
SERIAL="${THS_ANDROID_SERIAL:-emulator-5556}"
HOOK_PACKAGE="com.yuyang.thshook"
TARGET_PACKAGE="com.hexin.plat.android"
DB="/data/adb/lspd/config/modules_config.db"

adb_device() {
    "${ADB_BIN}" -s "${SERIAL}" "$@"
}

apk_path="$(adb_device shell pm path --user 0 "${HOOK_PACKAGE}" | tr -d '\r' | sed -n 's/^package://p')"
[[ "${apk_path}" == /data/app/*/base.apk ]] || {
    echo "cannot resolve persistent Hook APK path: ${apk_path:-missing}" >&2
    exit 1
}

sql_file="$(mktemp)"
trap 'rm -f "${sql_file}"' EXIT
cat >"${sql_file}" <<EOF
BEGIN IMMEDIATE;
INSERT INTO modules(module_pkg_name, apk_path, enabled)
VALUES('${HOOK_PACKAGE}', '${apk_path}', 1)
ON CONFLICT(module_pkg_name) DO UPDATE SET apk_path=excluded.apk_path, enabled=1;
DELETE FROM scope WHERE mid=(SELECT mid FROM modules WHERE module_pkg_name='${HOOK_PACKAGE}');
EOF
for user_id in 0 10 11 12 13 14 15 16 17; do
    printf "INSERT INTO scope(mid, app_pkg_name, user_id) SELECT mid, '%s', %s FROM modules WHERE module_pkg_name='%s';\n" \
        "${TARGET_PACKAGE}" "${user_id}" "${HOOK_PACKAGE}" >>"${sql_file}"
done
printf 'COMMIT;\n' >>"${sql_file}"

adb_device shell su -c "cp -f ${DB} ${DB}.pre-smart-fund-deploy"
adb_device shell su -c "sqlite3 ${DB}" <"${sql_file}"

dump="$(adb_device shell su -c "sqlite3 '${DB}' .dump")"
grep -Fq "'${HOOK_PACKAGE}','${apk_path}',1);" <<<"${dump}" \
    || { echo "LSPosed Hook module was not enabled" >&2; exit 1; }
scope_count="$(grep -F ",'${TARGET_PACKAGE}'," <<<"${dump}" | wc -l | tr -d ' ')"
[[ "${scope_count}" == "9" ]] || {
    echo "LSPosed scope verification failed: expected 9, got ${scope_count}" >&2
    exit 1
}
echo "LSPosed Hook enabled for ${TARGET_PACKAGE} across 9 Android users"
