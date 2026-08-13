#!/usr/bin/env bash
set -euo pipefail
ssh -i /tmp/deploy_key_smart_fund_113 \
  -p 1113 \
  -o StrictHostKeyChecking=no \
  yuyangruan@119.23.227.187 \
  'wc -c /home/yuyangruan/smart-fund/smart-fund-server/scripts/tmp_probe_sector_full_table.py /tmp/probe_result.json 2>/dev/null; cat /tmp/probe_result.json 2>/dev/null'
