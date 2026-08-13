#!/usr/bin/env bash
set -euo pipefail

ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-/home/yuyangruan/android-sdk}"
ADB="${ANDROID_SDK_ROOT}/platform-tools/adb"
SERIAL="${THS_ANDROID_SERIAL:-emulator-5554}"
PACKAGE="com.hexin.plat.android"
EXPECTED_APP_VERSION_CODE="${THS_EXPECTED_APP_VERSION_CODE:-5235}"
EXPECTED_APP_SHA256="${THS_EXPECTED_APP_SHA256:-03f8d87002b307728d372ddb1978a43732814183073cbc63f0a95bf6a7293eb3}"
ANDROID_USER_ID="${THS_ANDROID_USER_ID:-0}"
ACTIVATE_FOREGROUND_USER="${THS_ACTIVATE_FOREGROUND_USER:-0}"
RETURN_FOREGROUND_USER="${THS_RETURN_FOREGROUND_USER:-}"
STARTUP_DEPENDENCIES="${THS_STARTUP_DEPENDENCIES:-}"
HOST_PORT="${THS_HOST_PORT:-49300}"
DEVICE_PORT="${THS_DEVICE_PORT:-18900}"
PROXY_PORT="${THS_PROXY_PORT:-49301}"
PROXY_SCRIPT="${THS_PROXY_SCRIPT:-/home/yuyangruan/android-runtime/bin/ths-native-proxy.py}"
PROXY_AUTOMATIC_RECOVERY="${THS_PROXY_AUTOMATIC_RECOVERY:-0}"
PROXY_GATEWAY_MANAGED="${THS_PROXY_GATEWAY_MANAGED:-1}"
STARTUP_TIMEOUT_SECONDS="${THS_STARTUP_TIMEOUT_SECONDS:-300}"
HEALTH_INTERVAL_SECONDS="${THS_HEALTH_INTERVAL_SECONDS:-15}"
MAX_HEALTH_FAILURES="${THS_MAX_HEALTH_FAILURES:-3}"
MARKET_TAB_X="${THS_MARKET_TAB_X:-245}"
MARKET_TAB_Y="${THS_MARKET_TAB_Y:-2090}"
OPEN_MARKET_PAGE="${THS_OPEN_MARKET_PAGE:-1}"

log() {
    printf '%s %s\n' "$(date --iso-8601=seconds)" "$*"
}

adb_device() {
    "${ADB}" -s "${SERIAL}" "$@"
}

wait_for_android() {
    local deadline=$((SECONDS + STARTUP_TIMEOUT_SECONDS))
    while (( SECONDS < deadline )); do
        if [[ "$(adb_device get-state 2>/dev/null || true)" == "device" ]] \
            && [[ "$(adb_device shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == "1" ]]; then
            log "Android boot completed on ${SERIAL}"
            return 0
        fi
        sleep 2
    done
    log "Android boot timed out after ${STARTUP_TIMEOUT_SECONDS}s"
    return 1
}

verify_ths_app_build() {
    local package_dump actual_version apk_path actual_sha256
    package_dump="$(adb_device shell dumpsys package "${PACKAGE}" 2>/dev/null | tr -d '\r')"
    actual_version="$(grep -oE 'versionCode=[0-9]+' <<<"${package_dump}" | head -1 | cut -d= -f2)"
    apk_path="$(adb_device shell pm path "${PACKAGE}" 2>/dev/null \
        | tr -d '\r' | sed -n 's/^package://p' | head -1)"
    if [[ -z "${actual_version}" || -z "${apk_path}" ]]; then
        log "THS App is not installed or package metadata is unavailable"
        return 1
    fi
    actual_sha256="$(adb_device shell sha256sum "${apk_path}" 2>/dev/null \
        | tr -d '\r' | awk '{print $1}')"
    if [[ "${actual_version}" != "${EXPECTED_APP_VERSION_CODE}" \
        || "${actual_sha256}" != "${EXPECTED_APP_SHA256}" ]]; then
        log "THS App build mismatch: versionCode=${actual_version} sha256=${actual_sha256}; expected versionCode=${EXPECTED_APP_VERSION_CODE} sha256=${EXPECTED_APP_SHA256}"
        return 1
    fi
    log "THS App build verified: versionCode=${actual_version} sha256=${actual_sha256}"
}

ensure_android_user() {
    local allow_foreground_switch="${1:-1}"
    if [[ "${ANDROID_USER_ID}" != "0" ]]; then
        adb_device shell am start-user -w "${ANDROID_USER_ID}" >/dev/null
    fi
    if [[ "${ACTIVATE_FOREGROUND_USER}" != "1" || "${allow_foreground_switch}" != "1" ]]; then
        return 0
    fi

    log "Switching foreground Android user to ${ANDROID_USER_ID}"
    adb_device shell am switch-user "${ANDROID_USER_ID}" >/dev/null
    local deadline=$((SECONDS + 30))
    while (( SECONDS < deadline )); do
        if [[ "$(adb_device shell am get-current-user 2>/dev/null | tr -d '\r')" == "${ANDROID_USER_ID}" ]]; then
            adb_device shell wm dismiss-keyguard >/dev/null 2>&1 || true
            adb_device shell input keyevent 82 >/dev/null 2>&1 || true
            return 0
        fi
        sleep 1
    done
    log "Android user ${ANDROID_USER_ID} did not become foreground"
    return 1
}

configure_background_execution() {
    # Multi-user collector lanes are intentionally long-lived.  These settings
    # prevent Doze/app-standby from treating a background THS user as idle; the
    # health loop below remains responsible for recovering an actually dead
    # process.
    adb_device shell cmd deviceidle whitelist "+${PACKAGE}" >/dev/null 2>&1 || true
    adb_device shell cmd appops set --user "${ANDROID_USER_ID}" \
        "${PACKAGE}" RUN_IN_BACKGROUND allow >/dev/null 2>&1 || true
    adb_device shell cmd appops set --user "${ANDROID_USER_ID}" \
        "${PACKAGE}" RUN_ANY_IN_BACKGROUND allow >/dev/null 2>&1 || true
    adb_device shell am set-inactive --user "${ANDROID_USER_ID}" \
        "${PACKAGE}" false >/dev/null 2>&1 || true
}

restore_foreground_user() {
    [[ -n "${RETURN_FOREGROUND_USER}" ]] || return 0
    log "Returning foreground Android user to ${RETURN_FOREGROUND_USER}"
    adb_device shell am switch-user "${RETURN_FOREGROUND_USER}" >/dev/null
    local deadline=$((SECONDS + 30))
    while (( SECONDS < deadline )); do
        if [[ "$(adb_device shell am get-current-user 2>/dev/null | tr -d '\r')" == "${RETURN_FOREGROUND_USER}" ]]; then
            return 0
        fi
        sleep 1
    done
    log "Android user ${RETURN_FOREGROUND_USER} did not become foreground"
    return 1
}

wait_for_startup_dependencies() {
    [[ -n "${STARTUP_DEPENDENCIES}" ]] || return 0
    local url deadline
    IFS=',' read -r -a urls <<<"${STARTUP_DEPENDENCIES}"
    for url in "${urls[@]}"; do
        deadline=$((SECONDS + STARTUP_TIMEOUT_SECONDS))
        log "Waiting for bridge dependency ${url}"
        while (( SECONDS < deadline )); do
            if curl --fail --silent --max-time 5 "${url}" >/dev/null; then
                break
            fi
            sleep 2
        done
        if (( SECONDS >= deadline )); then
            log "Bridge dependency timed out: ${url}"
            return 1
        fi
    done
}

ensure_forward() {
    adb_device forward "tcp:${HOST_PORT}" "tcp:${DEVICE_PORT}" >/dev/null
}

hook_is_healthy() {
    local payload
    payload="$(curl --fail --silent --show-error --max-time 5 \
        "http://127.0.0.1:${HOST_PORT}/health")" || return 1
    grep -q "\"android_user_id\":${ANDROID_USER_ID}" <<<"${payload}"
}

launch_ths() {
    # Android exposes one current-user slot. Serialize the rare cold-start or
    # recovery switch so independent lane supervisors cannot overwrite each
    # other's foreground user while they bootstrap their Hook endpoint.
    exec 9>/tmp/ths-android-user-switch.lock
    flock -x 9
    ensure_android_user 1
    log "Launching ${PACKAGE} Android user ${ANDROID_USER_ID}"
    adb_device shell am force-stop --user "${ANDROID_USER_ID}" \
        "${PACKAGE}" >/dev/null 2>&1 || true
    adb_device shell am start --user "${ANDROID_USER_ID}" \
        -n "${PACKAGE}/.Hexin" >/dev/null

    local deadline=$((SECONDS + 90))
    while (( SECONDS < deadline )); do
        ensure_forward || true
        if hook_is_healthy; then
            log "THS hook endpoint is healthy"
            restore_foreground_user
            flock -u 9
            return 0
        fi
        sleep 2
    done
    log "THS hook endpoint did not become healthy"
    restore_foreground_user || true
    flock -u 9
    return 1
}

open_market_page() {
    [[ "${OPEN_MARKET_PAGE}" == "1" ]] || return 0
    log "Opening THS market page for WebView JSBridge user ${ANDROID_USER_ID}"
    adb_device shell am start --user "${ANDROID_USER_ID}" \
        -n "${PACKAGE}/.Hexin" >/dev/null 2>&1 || true
    sleep 8
    adb_device shell input tap "${MARKET_TAB_X}" "${MARKET_TAB_Y}"
    sleep 12
}

start_native_proxy() {
    log "Starting native recovery proxy on 127.0.0.1:${PROXY_PORT}"
    # A historical/manual ADB forward may occupy the recovery proxy port.
    # The formal topology only forwards HOST_PORT; PROXY_PORT belongs to Python.
    adb_device forward --remove "tcp:${PROXY_PORT}" >/dev/null 2>&1 || true
    local proxy_args=(
        "${PROXY_SCRIPT}"
        --listen-port "${PROXY_PORT}" \
        --upstream-port "${HOST_PORT}" \
        --device-port "${DEVICE_PORT}" \
        --adb "${ADB}" \
        --serial "${SERIAL}" \
        --package "${PACKAGE}" \
        --android-user-id "${ANDROID_USER_ID}"
    )
    if [[ "${ACTIVATE_FOREGROUND_USER}" == "1" ]]; then
        proxy_args+=(--activate-foreground-user)
    fi
    if [[ -n "${RETURN_FOREGROUND_USER}" ]]; then
        proxy_args+=(--return-foreground-user "${RETURN_FOREGROUND_USER}")
    fi
    if [[ "${PROXY_AUTOMATIC_RECOVERY}" != "1" ]]; then
        proxy_args+=(--disable-automatic-recovery)
    fi
    if [[ "${PROXY_GATEWAY_MANAGED}" == "1" ]]; then
        proxy_args+=(--gateway-managed)
    fi
    python3 "${proxy_args[@]}" &
    PROXY_PID=$!
    trap 'kill "${PROXY_PID}" >/dev/null 2>&1 || true' EXIT TERM INT
}

wait_for_native_proxy() {
    local deadline=$((SECONDS + 30))
    while (( SECONDS < deadline )); do
        if ! kill -0 "${PROXY_PID}" >/dev/null 2>&1; then
            log "Native recovery proxy exited during startup"
            return 1
        fi
        if curl --fail --silent --max-time 3 \
            "http://127.0.0.1:${PROXY_PORT}/health" >/dev/null; then
            log "Native recovery proxy is ready"
            return 0
        fi
        sleep 1
    done
    log "Native recovery proxy did not become ready"
    return 1
}

main() {
    "${ADB}" start-server >/dev/null
    wait_for_android
    verify_ths_app_build
    wait_for_startup_dependencies
    ensure_android_user 0
    configure_background_execution

    adb_device shell settings put global stay_on_while_plugged_in 3 >/dev/null 2>&1 || true
    adb_device shell dumpsys deviceidle disable >/dev/null 2>&1 || true
    ensure_forward

    if ! hook_is_healthy; then
        launch_ths
    else
        log "THS hook endpoint is already healthy"
    fi
    open_market_page
    start_native_proxy
    wait_for_native_proxy

    local failures=0
    while true; do
        if ! kill -0 "${PROXY_PID}" >/dev/null 2>&1; then
            log "Native recovery proxy exited unexpectedly"
            return 1
        fi
        ensure_forward || true
        if hook_is_healthy; then
            failures=0
        else
            failures=$((failures + 1))
            log "Health check failed (${failures}/${MAX_HEALTH_FAILURES})"
            if (( failures >= MAX_HEALTH_FAILURES )); then
                launch_ths
                open_market_page
                failures=0
            fi
        fi
        sleep "${HEALTH_INTERVAL_SECONDS}"
    done
}

main "$@"
