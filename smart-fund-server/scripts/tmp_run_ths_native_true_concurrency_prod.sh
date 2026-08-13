#!/usr/bin/env bash
set -euo pipefail

cd /home/yuyang/frida-test/smart-fund-server
set -a
source deployment/.deployment.local.env
set +a
source <(sed '/^main "$@"$/d' deployment/deploy_113.sh)
setup_ssh_key

restore_services() {
  sudo_cmd "systemctl start smart-fund-worker.service smart-fund-scheduler.service" >/dev/null || true
}
trap restore_services EXIT

sudo_cmd "systemctl stop smart-fund-scheduler.service smart-fund-worker.service"
sleep 3
probe_script="${1:-tmp_probe_ths_native_true_concurrency.py}"
probe_base_url="${2:-http://127.0.0.1:49300}"
scp -i /tmp/deploy_key_smart_fund_113 \
  -P 1113 \
  -o StrictHostKeyChecking=no \
  "scripts/${probe_script}" \
  yuyangruan@119.23.227.187:/tmp/
ssh_cmd "cd /home/yuyangruan/smart-fund/smart-fund-server && set -a && source /home/yuyangruan/smart-fund/config/smart-fund-server.env && set +a && THS_PROBE_BASE_URL=${probe_base_url} timeout 600 /home/yuyangruan/anaconda3/envs/smart-fund/bin/python -u /tmp/${probe_script}"
