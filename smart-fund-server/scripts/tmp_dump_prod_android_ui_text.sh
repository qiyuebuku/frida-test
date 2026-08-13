#!/usr/bin/env bash
set -euo pipefail

ssh -i /tmp/deploy_key_smart_fund_113 -p 1113 -o StrictHostKeyChecking=no \
  yuyangruan@119.23.227.187 \
  '/home/yuyangruan/android-sdk/platform-tools/adb shell uiautomator dump /sdcard/window.xml >/dev/null && /home/yuyangruan/android-sdk/platform-tools/adb shell cat /sdcard/window.xml' \
  | grep -oE 'text="[^"]*"' \
  | sed 's/^text="//; s/"$//' \
  | grep -v '^$'
