#!/usr/bin/env bash
set -euo pipefail

source /home/yuyang/frida-test/smart-fund-server/deployment/.deployment.local.env
SSH=(ssh -i /tmp/deploy_key_smart_fund_113 -p 1113 -o StrictHostKeyChecking=no yuyangruan@119.23.227.187)
SCP=(scp -i /tmp/deploy_key_smart_fund_113 -P 1113 -o StrictHostKeyChecking=no)
LOCAL_SCRIPT=/home/yuyang/frida-test/smart-fund-server/scripts/benchmark_ths_collection_capacity.py
REMOTE_SCRIPT=/tmp/benchmark_ths_collection_capacity.py

if (( $# )); then
    printf -v BENCHMARK_ARGS ' %q' "$@"
else
    BENCHMARK_ARGS=' --row-count 50 --timeout 90 --probe-timeout 75 --deadline 600 --output /tmp/ths-full-capacity.json --summary-only'
fi

remote_sudo() {
    printf '%s\n' "${REMOTE_SUDO_PASSWORD}" \
        | "${SSH[@]}" "sudo -S -p '' $*"
}

restore() {
    remote_sudo systemctl start smart-fund-scheduler.service smart-fund-worker.service \
        smart-fund-ths-realtime-stream.service \
        >/dev/null 2>&1 || true
}
trap restore EXIT

STOP_SERVICES=(smart-fund-scheduler.service smart-fund-worker.service)
if [[ "${THS_KEEP_REALTIME:-0}" != "1" ]]; then
    STOP_SERVICES+=(smart-fund-ths-realtime-stream.service)
fi

"${SCP[@]}" "${LOCAL_SCRIPT}" "yuyangruan@119.23.227.187:${REMOTE_SCRIPT}"
remote_sudo systemctl stop "${STOP_SERVICES[*]}" >/dev/null
"${SSH[@]}" 'if [[ "${THS_FORCE_RESTART_APP:-0}" == "1" ]]; then \
        /home/yuyangruan/android-sdk/platform-tools/adb shell am force-stop \
          com.hexin.plat.android; \
        /home/yuyangruan/android-sdk/platform-tools/adb shell am start \
          -n com.hexin.plat.android/.Hexin >/dev/null; \
    elif ! curl -fsS --max-time 2 http://127.0.0.1:49301/health >/dev/null; then \
        /home/yuyangruan/android-sdk/platform-tools/adb shell am start \
          -n com.hexin.plat.android/.Hexin >/dev/null; \
    fi; \
    for attempt in $(seq 1 60); do \
        curl -fsS --max-time 2 http://127.0.0.1:49301/health >/dev/null && exit 0; \
        sleep 1; \
    done; \
    exit 1'
sleep 2
"${SSH[@]}" "cd /home/yuyangruan/smart-fund/smart-fund-server && \
    PYTHONPATH=/home/yuyangruan/smart-fund/smart-fund-server \
    THS_PROBE_BASE_URL=http://127.0.0.1:49301 \
    timeout 650 /home/yuyangruan/anaconda3/envs/smart-fund/bin/python \
    ${REMOTE_SCRIPT}${BENCHMARK_ARGS}"
