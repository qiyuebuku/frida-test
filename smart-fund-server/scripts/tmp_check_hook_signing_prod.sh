#!/usr/bin/env bash
set -euo pipefail

ssh -i /tmp/deploy_key_smart_fund_113 \
  -p 1113 \
  -o StrictHostKeyChecking=no \
  yuyangruan@119.23.227.187 'bash -s' <<'REMOTE'
set -euo pipefail
ADB=/home/yuyangruan/android-sdk/platform-tools/adb
APK_SIGNER=/home/yuyangruan/android-sdk/build-tools/34.0.0/apksigner
installed_path="$($ADB shell pm path com.yuyang.thshook | tr -d '\r' | sed 's/^package://')"
echo "installed_path=$installed_path"
$ADB pull "$installed_path" /tmp/installed-ths-hook.apk >/dev/null
echo 'INSTALLED'
$APK_SIGNER verify --print-certs /tmp/installed-ths-hook.apk
for key in /home/yuyangruan/.android/debug.keystore /home/yuyangruan/builds/ths-hook-home/.android/debug.keystore; do
  echo "KEYSTORE $key"
  keytool -list -v -keystore "$key" -storepass android -alias androiddebugkey | grep 'SHA256:'
done
REMOTE
