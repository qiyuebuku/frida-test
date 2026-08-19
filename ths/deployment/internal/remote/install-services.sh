#!/usr/bin/env bash
# Production-host implementation; called by ths/deployment/deploy.sh.
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INTERNAL_DIR="$(cd "${SOURCE_DIR}/.." && pwd)"
RUNTIME_SOURCE="${INTERNAL_DIR}/runtime"
SYSTEMD_SOURCE="${INTERNAL_DIR}/systemd"
CONFIG_SOURCE="${INTERNAL_DIR}/config"
TOOLS_SOURCE="${INTERNAL_DIR}/tools"
RUNTIME_DIR="/home/yuyangruan/android-runtime"
ADB_BIN="${ADB_BIN:-/home/yuyangruan/android-sdk/platform-tools/adb}"
MODE="${1:-all}"
[[ "${MODE}" == "all" || "${MODE}" == "--runtime-only" || "${MODE}" == "--prepare" || "${MODE}" == "--start-only" ]] || {
    echo "usage: $0 [--runtime-only|--prepare|--start-only]" >&2
    exit 2
}

if [[ "${MODE}" != "--start-only" ]]; then
install -d -o yuyangruan -g yuyangruan "${RUNTIME_DIR}/bin" "${RUNTIME_DIR}/logs"
install -m 0755 -o yuyangruan -g yuyangruan \
    "${RUNTIME_SOURCE}/collector-bridge.sh" \
    "${RUNTIME_DIR}/bin/ths-collector-bridge.sh"
install -m 0755 -o yuyangruan -g yuyangruan \
    "${RUNTIME_SOURCE}/native-proxy.py" \
    "${RUNTIME_DIR}/bin/ths-native-proxy.py"
install -m 0755 -o yuyangruan -g yuyangruan \
    "${RUNTIME_SOURCE}/app-load-balancer.py" \
    "${RUNTIME_DIR}/bin/ths-app-load-balancer.py"
install -m 0755 -o root -g root \
    "${RUNTIME_SOURCE}/android-watchdog.py" \
    "${RUNTIME_DIR}/bin/ths-android-watchdog.py"
install -m 0755 -o root -g root \
    "${RUNTIME_SOURCE}/android-pool-manager.py" \
    "${RUNTIME_DIR}/bin/ths-android-pool-manager.py"
install -m 0755 -o yuyangruan -g yuyangruan \
    "${RUNTIME_SOURCE}/disable-bluetooth.sh" \
    "${RUNTIME_DIR}/bin/ths-disable-bluetooth.sh"
install -m 0755 -o yuyangruan -g yuyangruan \
    "${RUNTIME_SOURCE}/optimize-android.sh" \
    "${RUNTIME_DIR}/bin/ths-optimize-android.sh"
install -m 0755 -o yuyangruan -g yuyangruan \
    "${RUNTIME_SOURCE}/screen-off.sh" \
    "${RUNTIME_DIR}/bin/ths-screen-off.sh"
install -m 0644 "${SYSTEMD_SOURCE}/android-emulator.service" \
    /etc/systemd/system/ths-android-emulator.service
install -m 0644 "${SYSTEMD_SOURCE}/collector-bridge.service" \
    /etc/systemd/system/ths-collector-bridge.service
install -m 0644 "${SYSTEMD_SOURCE}/collector-bridge@.service" \
    /etc/systemd/system/ths-collector-bridge@.service
install -m 0644 "${SYSTEMD_SOURCE}/trade-bridge.service" \
    /etc/systemd/system/ths-trade-bridge.service
install -m 0644 "${SYSTEMD_SOURCE}/app-load-balancer.service" \
    /etc/systemd/system/ths-app-load-balancer.service
install -m 0644 "${SYSTEMD_SOURCE}/android-watchdog.service" \
    /etc/systemd/system/ths-android-watchdog.service
install -m 0644 "${SYSTEMD_SOURCE}/android-pool-manager.service" \
    /etc/systemd/system/ths-android-pool-manager.service
install -d -m 0755 /etc/smart-fund
for lane in primary futures us-ranking us-etf pool5 pool6 pool7 pool8; do
    install -m 0644 "${CONFIG_SOURCE}/bridge-${lane}.env" \
        "/etc/smart-fund/ths-bridge-${lane}.env"
done
fi

[[ "${MODE}" != "--runtime-only" ]] || exit 0

# Remove names from the retired topology without removing the current isolated
# futures/US lanes.
systemctl disable --now ths-collector-bridge@realtime.service \
    ths-collector-bridge@ranking.service \
    ths-collector-bridge@sector.service >/dev/null 2>&1 || true
systemctl disable --now ths-collector-bridge.service \
    ths-android-pool-manager.service >/dev/null 2>&1 || true
rm -f /etc/smart-fund/ths-bridge-realtime.env \
    /etc/smart-fund/ths-bridge-ranking.env \
    /etc/smart-fund/ths-bridge-sector.env

systemctl daemon-reload
systemctl enable --now ths-android-emulator.service
if [[ "${MODE}" != "--start-only" ]]; then
    "${TOOLS_SOURCE}/install-max-running-users-overlay.sh"
    # The first migration may still have emulator-5556 running outside systemd.
    # Stop the old managed AVD, terminate any unmanaged 5556 instance, then let
    # the updated unit become the sole owner of the production AVD.
    systemctl stop ths-android-emulator.service
    "${ADB_BIN}" -s emulator-5556 emu kill >/dev/null 2>&1 || true
    for _ in {1..30}; do
        [[ "$("${ADB_BIN}" -s emulator-5556 get-state 2>/dev/null || true)" != "device" ]] && break
        sleep 1
    done
    systemctl start ths-android-emulator.service
fi
for _ in {1..60}; do
    if [[ "$("${ADB_BIN}" -s emulator-5556 get-state 2>/dev/null || true)" == "device" ]] \
        && [[ "$("${ADB_BIN}" -s emulator-5556 shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == "1" ]]; then
        break
    fi
    sleep 2
done
[[ "${MODE}" != "--prepare" ]] || exit 0
start_bridge_serially() {
    local unit="$1"
    local health_port="$2"
    systemctl enable --now "${unit}"
    for _ in {1..90}; do
        if curl -fsS --max-time 2 "http://127.0.0.1:${health_port}/health" >/dev/null; then
            return 0
        fi
        sleep 2
    done
    echo "THS bridge did not become healthy: ${unit}" >&2
    return 1
}

ensure_trade_runtime() {
    local payload
    systemctl enable --now ths-trade-bridge.service
    for _ in {1..90}; do
        if curl -fsS --max-time 5 "http://127.0.0.1:49500/health" >/dev/null; then
            payload="$(curl -fsS --max-time 120 -X POST \
                -H 'Content-Type: application/json' -d '{}' \
                http://127.0.0.1:49500/stock/trade/runtime/ensure || true)"
            if grep -q '"write_ready":true' <<<"${payload}"; then
                return 0
            fi
        fi
        sleep 2
    done
    echo "THS trade runtime did not become write-ready" >&2
    return 1
}

# Every bridge temporarily switches the foreground Android user while starting
# its App process. Starting them in parallel races switch-user and leaves a
# random subset alive but without Hook injection. Wait for one lane to expose
# its health endpoint before starting the next lane.
ensure_trade_runtime
start_bridge_serially ths-collector-bridge@primary.service 49301
start_bridge_serially ths-collector-bridge@futures.service 49311
start_bridge_serially ths-collector-bridge@us-ranking.service 49321
start_bridge_serially ths-collector-bridge@us-etf.service 49331
start_bridge_serially ths-collector-bridge@pool5.service 49341
start_bridge_serially ths-collector-bridge@pool6.service 49361
start_bridge_serially ths-collector-bridge@pool7.service 49371
start_bridge_serially ths-collector-bridge@pool8.service 49381
systemctl enable --now ths-app-load-balancer.service
systemctl enable --now ths-android-watchdog.service

# Collector cold starts currently switch Android's foreground user and may kill
# user 0 because THS holds audio-record permission. Re-establish the trade
# process/channel after every collector lane is initialized; this is idempotent.
ensure_trade_runtime

# This must be the final deployment action: collector startup temporarily wakes
# and switches Android users.  Leave production on the owner user with the
# display asleep to avoid unnecessary emulator rendering and host GPU/CPU use.
"${RUNTIME_DIR}/bin/ths-screen-off.sh"
