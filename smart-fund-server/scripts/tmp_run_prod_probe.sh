#!/usr/bin/env bash
set -euo pipefail

scp -i /tmp/deploy_key_smart_fund_113 \
  -P 1113 \
  -o StrictHostKeyChecking=no \
  /home/yuyang/frida-test/smart-fund-server/scripts/tmp_probe_sector_full_table.py \
  yuyangruan@119.23.227.187:/home/yuyangruan/smart-fund/smart-fund-server/scripts/
scp -i /tmp/deploy_key_smart_fund_113 \
  -P 1113 \
  -o StrictHostKeyChecking=no \
  /home/yuyang/frida-test/smart-fund-server/scripts/tmp_remote_probe_runner.sh \
  yuyangruan@119.23.227.187:/tmp/

ssh -i /tmp/deploy_key_smart_fund_113 \
  -p 1113 \
  -o StrictHostKeyChecking=no \
yuyangruan@119.23.227.187 'bash /tmp/tmp_remote_probe_runner.sh'
