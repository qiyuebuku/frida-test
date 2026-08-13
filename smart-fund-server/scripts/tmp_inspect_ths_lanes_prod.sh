#!/usr/bin/env bash
set -euo pipefail

SSH=(ssh -i /tmp/deploy_key_smart_fund_113 -p 1113 -o StrictHostKeyChecking=no yuyangruan@119.23.227.187)
"${SSH[@]}" 'bash -s' <<'REMOTE'
set -euo pipefail
ADB=/home/yuyangruan/android-sdk/platform-tools/adb
"${ADB}" -s emulator-5554 shell pm list users </dev/null || true
echo '---PROCS---'
"${ADB}" -s emulator-5554 shell 'ps -A | grep com.hexin.plat.android || true' </dev/null
echo '---FORWARDS---'
"${ADB}" -s emulator-5554 forward --list </dev/null
echo '---SERVICES---'
systemctl list-units 'ths-collector-bridge*' --no-pager --no-legend || true
echo '---CAPACITY---'
"${ADB}" -s emulator-5554 shell 'getprop fw.max_users; getprop fw.show_multiuserui; free -m; df -h /data' </dev/null || true
"${ADB}" -s emulator-5554 shell 'dumpsys user | head -80; getprop ro.fw.mu.headless_system_user; getprop fw.max_running_users' </dev/null || true
REMOTE
