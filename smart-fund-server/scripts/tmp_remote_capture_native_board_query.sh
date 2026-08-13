#!/usr/bin/env bash
set -euo pipefail

ADB=/home/yuyangruan/android-sdk/platform-tools/adb
${ADB} shell input tap 180 380
sleep 3
${ADB} shell input tap 540 380
sleep 6
curl -fsS -X POST http://127.0.0.1:49301/native/indicator-capture/reset >/dev/null
${ADB} shell input swipe 540 1800 540 500 700
sleep 12
curl -fsS http://127.0.0.1:49301/native/indicator-capture
echo
