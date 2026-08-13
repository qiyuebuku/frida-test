#!/usr/bin/env bash
set -euo pipefail

source /home/yuyang/frida-test/smart-fund-server/deployment/.deployment.local.env
SSH=(ssh -i /tmp/deploy_key_smart_fund_113 -p 1113 -o StrictHostKeyChecking=no yuyangruan@119.23.227.187)
SCP=(scp -i /tmp/deploy_key_smart_fund_113 -P 1113 -o StrictHostKeyChecking=no)

remote_sudo() {
    printf '%s\n' "${REMOTE_SUDO_PASSWORD}" | "${SSH[@]}" "sudo -S -p '' $*"
}

restore() {
    remote_sudo systemctl start smart-fund-scheduler.service smart-fund-worker.service \
        smart-fund-ths-realtime-stream.service >/dev/null 2>&1 || true
}
trap restore EXIT

"${SCP[@]}" scripts/tmp_probe_stock_ranking_direct.py \
    yuyangruan@119.23.227.187:/tmp/tmp_probe_stock_ranking_direct.py
remote_sudo systemctl stop smart-fund-scheduler.service smart-fund-worker.service \
    smart-fund-ths-realtime-stream.service >/dev/null
"${SSH[@]}" 'for attempt in $(seq 1 20); do \
        curl -fsS --max-time 2 http://127.0.0.1:49301/health >/dev/null && break; \
        sleep 1; \
    done; \
    sleep 2; \
    cd /home/yuyangruan/smart-fund/smart-fund-server; \
    PYTHONPATH=. THS_PROBE_BASE_URL=http://127.0.0.1:49301 \
      timeout 180 /home/yuyangruan/anaconda3/envs/smart-fund/bin/python \
      /tmp/tmp_probe_stock_ranking_direct.py'
