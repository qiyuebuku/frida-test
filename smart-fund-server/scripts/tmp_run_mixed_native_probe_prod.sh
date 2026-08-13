#!/usr/bin/env bash
set -euo pipefail

source /home/yuyang/frida-test/smart-fund-server/deployment/.deployment.local.env
SSH=(ssh -i /tmp/deploy_key_smart_fund_113 -p 1113 -o StrictHostKeyChecking=no yuyangruan@119.23.227.187)
SCP=(scp -i /tmp/deploy_key_smart_fund_113 -P 1113 -o StrictHostKeyChecking=no)
LOCAL_DIR=/home/yuyang/frida-test/smart-fund-server/scripts
PROBE_SCRIPT=${THS_PROBE_SCRIPT:-tmp_probe_ranking_hurricane_interference.py}
REMOTE_SCRIPT=/tmp/${PROBE_SCRIPT}

remote_sudo() {
    printf '%s\n' "${REMOTE_SUDO_PASSWORD}" \
        | "${SSH[@]}" "sudo -S -p '' $*"
}

restore() {
    remote_sudo systemctl start smart-fund-scheduler.service smart-fund-worker.service \
        >/dev/null 2>&1 || true
}
trap restore EXIT

"${SCP[@]}" "${LOCAL_DIR}/${PROBE_SCRIPT}" \
    "yuyangruan@119.23.227.187:${REMOTE_SCRIPT}"
remote_sudo systemctl stop smart-fund-scheduler.service smart-fund-worker.service >/dev/null
sleep 3
"${SSH[@]}" \
    "cd /home/yuyangruan/smart-fund/smart-fund-server && PYTHONPATH=/home/yuyangruan/smart-fund/smart-fund-server THS_PROBE_BASE_URL=http://127.0.0.1:49301 THS_PROBE_RANKING_ONLY=${THS_PROBE_RANKING_ONLY:-0} timeout ${THS_PROBE_TIMEOUT_SECONDS:-180} /home/yuyangruan/anaconda3/envs/smart-fund/bin/python ${REMOTE_SCRIPT} ${THS_PROBE_ARGS:-}"
