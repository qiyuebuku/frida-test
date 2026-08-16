#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="/home/yuyangruan/android-runtime"
ADB_BIN="${ADB_BIN:-/home/yuyangruan/android-sdk/platform-tools/adb}"

install -d -o yuyangruan -g yuyangruan "${RUNTIME_DIR}/bin" "${RUNTIME_DIR}/logs"
install -m 0755 -o yuyangruan -g yuyangruan \
    "${SOURCE_DIR}/ths-collector-bridge.sh" \
    "${RUNTIME_DIR}/bin/ths-collector-bridge.sh"
install -m 0755 -o yuyangruan -g yuyangruan \
    "${SOURCE_DIR}/ths-native-proxy.py" \
    "${RUNTIME_DIR}/bin/ths-native-proxy.py"
install -m 0755 -o yuyangruan -g yuyangruan \
    "${SOURCE_DIR}/ths-app-load-balancer.py" \
    "${RUNTIME_DIR}/bin/ths-app-load-balancer.py"
install -m 0755 -o root -g root \
    "${SOURCE_DIR}/ths-android-watchdog.py" \
    "${RUNTIME_DIR}/bin/ths-android-watchdog.py"
install -m 0755 -o root -g root \
    "${SOURCE_DIR}/ths-android-pool-manager.py" \
    "${RUNTIME_DIR}/bin/ths-android-pool-manager.py"
install -m 0755 -o yuyangruan -g yuyangruan \
    "${SOURCE_DIR}/ths-disable-bluetooth.sh" \
    "${RUNTIME_DIR}/bin/ths-disable-bluetooth.sh"
install -m 0755 -o yuyangruan -g yuyangruan \
    "${SOURCE_DIR}/ths-optimize-android.sh" \
    "${RUNTIME_DIR}/bin/ths-optimize-android.sh"
install -m 0644 "${SOURCE_DIR}/ths-android-emulator.service" \
    /etc/systemd/system/ths-android-emulator.service
install -m 0644 "${SOURCE_DIR}/ths-collector-bridge.service" \
    /etc/systemd/system/ths-collector-bridge.service
install -m 0644 "${SOURCE_DIR}/ths-collector-bridge@.service" \
    /etc/systemd/system/ths-collector-bridge@.service
install -m 0644 "${SOURCE_DIR}/ths-app-load-balancer.service" \
    /etc/systemd/system/ths-app-load-balancer.service
install -m 0644 "${SOURCE_DIR}/ths-android-watchdog.service" \
    /etc/systemd/system/ths-android-watchdog.service
install -m 0644 "${SOURCE_DIR}/ths-android-pool-manager.service" \
    /etc/systemd/system/ths-android-pool-manager.service
install -d -m 0755 /etc/smart-fund
for lane in futures us-ranking us-etf pool5 pool6 pool7 pool8; do
    install -m 0644 "${SOURCE_DIR}/ths-bridge-${lane}.env" \
        "/etc/smart-fund/ths-bridge-${lane}.env"
done

# Remove names from the retired topology without removing the current isolated
# futures/US lanes.
systemctl disable --now ths-collector-bridge@realtime.service \
    ths-collector-bridge@ranking.service \
    ths-collector-bridge@sector.service >/dev/null 2>&1 || true
rm -f /etc/smart-fund/ths-bridge-realtime.env \
    /etc/smart-fund/ths-bridge-ranking.env \
    /etc/smart-fund/ths-bridge-sector.env

systemctl daemon-reload
systemctl enable --now ths-android-emulator.service
"${SOURCE_DIR}/install-max-running-users-overlay.sh"
systemctl restart ths-android-emulator.service
for _ in {1..60}; do
    if [[ "$("${ADB_BIN}" -s emulator-5554 get-state 2>/dev/null || true)" == "device" ]] \
        && [[ "$("${ADB_BIN}" -s emulator-5554 shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == "1" ]]; then
        break
    fi
    sleep 2
done
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

# Every bridge temporarily switches the foreground Android user while starting
# its App process. Starting them in parallel races switch-user and leaves a
# random subset alive but without Hook injection. Wait for one lane to expose
# its health endpoint before starting the next lane.
start_bridge_serially ths-collector-bridge.service 49301
start_bridge_serially ths-collector-bridge@futures.service 49311
start_bridge_serially ths-collector-bridge@us-ranking.service 49321
start_bridge_serially ths-collector-bridge@us-etf.service 49331
start_bridge_serially ths-collector-bridge@pool5.service 49341
start_bridge_serially ths-collector-bridge@pool6.service 49361
start_bridge_serially ths-collector-bridge@pool7.service 49371
start_bridge_serially ths-collector-bridge@pool8.service 49381
systemctl enable --now ths-app-load-balancer.service
systemctl enable --now ths-android-watchdog.service
