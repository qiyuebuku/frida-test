#!/usr/bin/env bash
set -euo pipefail

scp -i /tmp/deploy_key_smart_fund_113 \
  -P 1113 \
  -o StrictHostKeyChecking=no \
  /home/yuyang/frida-test/smart-fund-server/scripts/tmp_remote_register_schedules.sh \
  yuyangruan@119.23.227.187:/tmp/
ssh -i /tmp/deploy_key_smart_fund_113 \
  -p 1113 \
  -o StrictHostKeyChecking=no \
  yuyangruan@119.23.227.187 \
  'bash /tmp/tmp_remote_register_schedules.sh'
