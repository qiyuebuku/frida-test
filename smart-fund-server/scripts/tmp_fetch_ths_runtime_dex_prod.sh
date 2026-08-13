#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST=yuyangruan@119.23.227.187
SSH=(ssh -i /tmp/deploy_key_smart_fund_113 -p 1113 -o StrictHostKeyChecking=no)
SCP=(scp -i /tmp/deploy_key_smart_fund_113 -P 1113 -o StrictHostKeyChecking=no)

"${SSH[@]}" "$REMOTE_HOST" 'bash -s' <<'REMOTE'
set -euo pipefail
ADB=/home/yuyangruan/android-sdk/platform-tools/adb
rm -rf /tmp/ths-runtime-dex
mkdir -p /tmp/ths-runtime-dex
for dex in classes.dex classes2.dex classes3.dex classes4.dex classes5.dex classes6.dex; do
  "$ADB" shell su -c "cp /data/user/0/com.hexin.plat.android/files/dex/$dex /sdcard/Download/$dex"
  "$ADB" pull "/sdcard/Download/$dex" "/tmp/ths-runtime-dex/$dex" >/dev/null
  "$ADB" shell rm -f "/sdcard/Download/$dex"
done
tar -C /tmp -czf /tmp/ths-runtime-dex.tar.gz ths-runtime-dex
ls -lh /tmp/ths-runtime-dex.tar.gz /tmp/ths-runtime-dex/*.dex
REMOTE

mkdir -p /home/yuyang/frida-test/artifacts/reverse
"${SCP[@]}" "$REMOTE_HOST:/tmp/ths-runtime-dex.tar.gz" /home/yuyang/frida-test/artifacts/reverse/
tar -C /home/yuyang/frida-test/artifacts/reverse -xzf /home/yuyang/frida-test/artifacts/reverse/ths-runtime-dex.tar.gz
