#!/usr/bin/env bash
set -euo pipefail

source /home/yuyang/frida-test/smart-fund-server/deployment/.deployment.local.env
ssh -i /tmp/deploy_key_smart_fund_113 -p 1113 -o StrictHostKeyChecking=no \
  yuyangruan@119.23.227.187 \
  "/home/yuyangruan/android-sdk/platform-tools/adb exec-out su -c 'cat /data/user/0/com.hexin.plat.android/files/hexinApp/hx_native_pkg/AStockSector/2.0/AStockSector/AStockSector.json'" \
  | python3 /home/yuyang/frida-test/smart-fund-server/scripts/tmp_extract_astocksector_config.py
