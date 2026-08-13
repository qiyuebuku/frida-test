#!/usr/bin/env bash
set -euo pipefail

ssh -i /tmp/deploy_key_smart_fund_113 \
  -p 1113 \
  -o StrictHostKeyChecking=no \
  yuyangruan@119.23.227.187 'bash -s' <<'REMOTE'
set -euo pipefail
systemctl is-active smart-fund-worker.service smart-fund-scheduler.service
ADB=/home/yuyangruan/android-sdk/platform-tools/adb
$ADB shell su -c 'find /data/user/0/com.hexin.plat.android/files -type f 2>/dev/null' | head -200
REMOTE
