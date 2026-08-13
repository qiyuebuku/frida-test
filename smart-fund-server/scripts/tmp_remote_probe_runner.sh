#!/usr/bin/env bash
set -euo pipefail
cd /home/yuyangruan/smart-fund/smart-fund-server
set -a
source /home/yuyangruan/smart-fund/config/smart-fund-server.env
set +a
timeout 240 /home/yuyangruan/anaconda3/envs/smart-fund/bin/python scripts/tmp_probe_sector_full_table.py > /tmp/probe_result.json 2>&1
cat /tmp/probe_result.json
