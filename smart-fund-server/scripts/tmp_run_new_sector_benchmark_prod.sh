#!/usr/bin/env bash
set -euo pipefail

for file in tmp_benchmark_new_sector_core.py tmp_remote_sector_benchmark_runner.sh; do
  scp -i /tmp/deploy_key_smart_fund_113 \
    -P 1113 \
    -o StrictHostKeyChecking=no \
    "/home/yuyang/frida-test/smart-fund-server/scripts/${file}" \
    "yuyangruan@119.23.227.187:/tmp/${file}"
done
ssh -i /tmp/deploy_key_smart_fund_113 \
  -p 1113 \
  -o StrictHostKeyChecking=no \
  yuyangruan@119.23.227.187 \
  'cp /tmp/tmp_benchmark_new_sector_core.py /home/yuyangruan/smart-fund/smart-fund-server/scripts/ && bash /tmp/tmp_remote_sector_benchmark_runner.sh'
ssh -i /tmp/deploy_key_smart_fund_113 \
  -p 1113 \
  -o StrictHostKeyChecking=no \
  yuyangruan@119.23.227.187 \
  'cat /tmp/new_sector_benchmark.json'
