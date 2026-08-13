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
ssh_cmd "for attempt in \$(seq 1 60); do curl -fsS --max-time 2 http://127.0.0.1:49301/health >/dev/null && exit 0; sleep 2; done; exit 1"

scp -i /tmp/deploy_key_smart_fund_113 \
  -P 1113 \
  -o StrictHostKeyChecking=no \
  scripts/tmp_benchmark_new_sector_core.py \
  yuyangruan@119.23.227.187:/tmp/tmp_benchmark_new_sector_core.py

ssh_cmd "cd /home/yuyangruan/smart-fund/smart-fund-server && set -a && source /home/yuyangruan/smart-fund/config/smart-fund-server.env && set +a && PYTHONPATH=/home/yuyangruan/smart-fund/smart-fund-server timeout 600 /home/yuyangruan/anaconda3/envs/smart-fund/bin/python -u /tmp/tmp_benchmark_new_sector_core.py"
