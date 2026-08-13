#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST=yuyangruan@119.23.227.187
SSH=(ssh -i /tmp/deploy_key_smart_fund_113 -p 1113 -o StrictHostKeyChecking=no)
SCP=(scp -i /tmp/deploy_key_smart_fund_113 -P 1113 -o StrictHostKeyChecking=no)
APK=/home/yuyang/frida-test/ths/app/build/outputs/apk/debug/app-debug.apk

"${SCP[@]}" "$APK" "$REMOTE_HOST:/tmp/ths-hook-unsigned.apk"
"${SSH[@]}" "$REMOTE_HOST" 'bash -s' <<'REMOTE'
set -euo pipefail
ADB=/home/yuyangruan/android-sdk/platform-tools/adb
APK_SIGNER=/home/yuyangruan/android-sdk/build-tools/34.0.0/apksigner
KEYSTORE=/home/yuyangruan/.android/debug.keystore
rm -f /tmp/ths-hook-production.apk
$APK_SIGNER sign \
  --ks "$KEYSTORE" \
  --ks-key-alias androiddebugkey \
  --ks-pass pass:android \
  --key-pass pass:android \
  --out /tmp/ths-hook-production.apk \
  /tmp/ths-hook-unsigned.apk
$APK_SIGNER verify --print-certs /tmp/ths-hook-production.apk
$ADB install -r /tmp/ths-hook-production.apk
$ADB shell am force-stop com.hexin.plat.android
sleep 1
$ADB shell am start -n com.hexin.plat.android/.Hexin >/dev/null
for attempt in $(seq 1 60); do
  if curl -fsS --max-time 2 http://127.0.0.1:49301/health >/dev/null; then
    exit 0
  fi
  sleep 2
done
exit 1
REMOTE
