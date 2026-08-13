#!/usr/bin/env bash
set -euo pipefail
cd /home/yuyangruan/smart-fund/smart-fund-server
set -a
source /home/yuyangruan/smart-fund/config/smart-fund-server.env
set +a
timeout 300 /home/yuyangruan/anaconda3/envs/smart-fund/bin/python scripts/tmp_benchmark_new_sector_core.py > /tmp/new_sector_benchmark.json 2>&1
