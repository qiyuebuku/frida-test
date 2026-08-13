#!/usr/bin/env bash
set -euo pipefail

source /home/yuyang/frida-test/smart-fund-server/deployment/.deployment.local.env
SSH=(ssh -i /tmp/deploy_key_smart_fund_113 -p 1113 -o StrictHostKeyChecking=no yuyangruan@119.23.227.187)
SCP=(scp -i /tmp/deploy_key_smart_fund_113 -P 1113 -o StrictHostKeyChecking=no)

remote_sudo() {
  printf '%s\n' "${REMOTE_SUDO_PASSWORD}" | "${SSH[@]}" "sudo -S -p '' $*"
}

restore() {
  remote_sudo systemctl start smart-fund-scheduler.service smart-fund-worker.service >/dev/null 2>&1 || true
}
trap restore EXIT

remote_sudo systemctl stop smart-fund-scheduler.service smart-fund-worker.service >/dev/null
"${SCP[@]}" \
  /home/yuyang/frida-test/smart-fund-server/scripts/tmp_remote_capture_native_board_query.sh \
  yuyangruan@119.23.227.187:/tmp/capture-native-board-query.sh >/dev/null
"${SSH[@]}" 'bash /tmp/capture-native-board-query.sh'
