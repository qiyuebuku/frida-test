#!/usr/bin/env bash
set -euo pipefail
cd /home/yuyang/frida-test/smart-fund-server
set -a
source deployment/.deployment.local.env
set +a
source <(sed '/^main "$@"$/d' deployment/deploy_113.sh)
setup_ssh_key

remote_apk="$(ssh_cmd "/home/yuyangruan/android-sdk/platform-tools/adb shell pm path com.hexin.plat.android | head -1 | cut -d: -f2 | tr -d '\\r'")"
if [[ -z "${remote_apk}" ]]; then
  echo "THS APK path not found" >&2
  exit 1
fi
ssh_cmd "/home/yuyangruan/android-sdk/platform-tools/adb pull '${remote_apk}' /tmp/ths-production-base.apk >/dev/null"
mkdir -p /home/yuyang/frida-test/artifacts/reverse
scp "${SCP_OPTS[@]}" \
  "${REMOTE_USER}@${REMOTE_HOST}:/tmp/ths-production-base.apk" \
  /home/yuyang/frida-test/artifacts/reverse/ths-production-base.apk
