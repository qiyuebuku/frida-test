#!/usr/bin/env bash
set -euo pipefail
cd /home/yuyangruan/smart-fund/smart-fund-server
set -a
source /home/yuyangruan/smart-fund/config/smart-fund-server.env
set +a
/home/yuyangruan/anaconda3/envs/smart-fund/bin/python -m src.interfaces.cli init schedules
