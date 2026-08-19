#!/usr/bin/env bash
set -euo pipefail

DEPLOYMENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="$(cd "${DEPLOYMENT_DIR}/../.." && pwd)"
COMPONENTS="ths-hook,ths-runtime"
ENV_FILE="${WORKSPACE}/deployment/production.env"
while (($#)); do
    case "$1" in
        --component) COMPONENTS="${2:?missing component list}"; shift 2 ;;
        --env-file) ENV_FILE="${2:?missing env file}"; shift 2 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done
[[ -f "${ENV_FILE}" ]] || { echo "missing deployment env: ${ENV_FILE}" >&2; exit 1; }
# shellcheck disable=SC1090
source "${ENV_FILE}"
: "${REMOTE_HOST:?required}"
: "${REMOTE_PORT:?required}"
: "${REMOTE_USER:?required}"
: "${SSH_KEY:?required}"
: "${DEPLOY_REVISION:?must be provided by the workspace deploy.sh}"
DEPLOY_GIT_URL="${DEPLOY_GIT_URL:-ssh://git@ssh.github.com:443/qiyuebuku/frida-test.git}"
REMOTE_GIT_DIR="${REMOTE_GIT_DIR:-/home/${REMOTE_USER}/smart-fund-source}"
SSH=(ssh -i "${SSH_KEY}" -p "${REMOTE_PORT}" -o StrictHostKeyChecking=no "${REMOTE_USER}@${REMOTE_HOST}")

remote_sudo() {
    local command="$1" quoted
    printf -v quoted '%q' "${command}"
    if [[ -n "${REMOTE_SUDO_PASSWORD:-}" ]]; then
        local password
        printf -v password '%q' "${REMOTE_SUDO_PASSWORD}"
        "${SSH[@]}" "printf '%s\n' ${password} | sudo -S bash -lc ${quoted}"
    else
        "${SSH[@]}" "sudo -n bash -lc ${quoted}"
    fi
}

install_fresh_host_config() {
    local required value payload=""
    for required in \
        THS_ANDROID_SDK_ARCHIVE THS_ANDROID_SDK_ARCHIVE_SHA256 \
        THS_AVD_ARCHIVE THS_AVD_ARCHIVE_SHA256 \
        THS_APP_APK THS_APP_APK_SHA256 \
        THS_HOOK_APK THS_HOOK_APK_SHA256 \
        THS_COLLECTOR_TEMPLATE THS_COLLECTOR_TEMPLATE_SHA256; do
        value="${!required:-}"
        [[ -n "${value}" ]] || {
            echo "fresh host requires ${required} in ${ENV_FILE}" >&2
            return 1
        }
        printf -v payload '%s%s=%q\n' "${payload}" "${required}" "${value}"
    done
    payload+="THS_DEPLOY_USER=${REMOTE_USER}"$'\n'
    payload+="THS_DEPLOY_HOME=/home/${REMOTE_USER}"$'\n'
    payload+="THS_ANDROID_SERIAL=emulator-5556"$'\n'
    encoded="$(printf '%s' "${payload}" | base64 -w 0)"
    remote_sudo "install -d -m 0755 /etc/smart-fund; printf '%s' '${encoded}' | base64 -d > /etc/smart-fund/ths-deployment.env; chmod 0600 /etc/smart-fund/ths-deployment.env"
}

"${SSH[@]}" "set -euo pipefail
if [[ ! -d '${REMOTE_GIT_DIR}/.git' ]]; then
  git clone --filter=blob:none '${DEPLOY_GIT_URL}' '${REMOTE_GIT_DIR}'
fi
git -C '${REMOTE_GIT_DIR}' fetch --prune origin
git -C '${REMOTE_GIT_DIR}' cat-file -e '${DEPLOY_REVISION}^{commit}'
git -C '${REMOTE_GIT_DIR}' checkout --detach --force '${DEPLOY_REVISION}'"

bootstrapped=0
if ! "${SSH[@]}" "test -x /home/${REMOTE_USER}/android-sdk/emulator/emulator && test -d /home/${REMOTE_USER}/.android/avd/ths-futures.avd"; then
    if ! "${SSH[@]}" "test -f /etc/smart-fund/ths-deployment.env"; then
        install_fresh_host_config
    fi
    remote_sudo "ADB_BIN=/home/${REMOTE_USER}/android-sdk/platform-tools/adb '${REMOTE_GIT_DIR}/ths/deployment/internal/remote/provision-host.sh' /etc/smart-fund/ths-deployment.env"
    bootstrapped=1
fi

if [[ ",${COMPONENTS}," == *,ths-hook,* ]]; then
    "${SSH[@]}" "set -euo pipefail
export ANDROID_HOME=/home/${REMOTE_USER}/android-sdk
export ANDROID_SDK_ROOT=/home/${REMOTE_USER}/android-sdk
cd '${REMOTE_GIT_DIR}/ths'
./gradlew --no-daemon :app:assembleDebug
unsigned=app/build/outputs/apk/debug/app-debug.apk
signed=/tmp/ths-hook-${DEPLOY_REVISION}.apk
apksigner=/home/${REMOTE_USER}/android-sdk/build-tools/34.0.0/apksigner
keystore=/home/${REMOTE_USER}/.android/debug.keystore
rm -f \"\${signed}\"
\"\${apksigner}\" sign --ks \"\${keystore}\" --ks-key-alias androiddebugkey \
  --ks-pass pass:android --key-pass pass:android --out \"\${signed}\" \"\${unsigned}\"
fingerprint=\"\$(\"\${apksigner}\" verify --print-certs \"\${signed}\" | sed -n 's/^Signer #1 certificate SHA-256 digest: //p')\"
[[ \"\${fingerprint,,}\" == '9505d29aca6006eef0fe473b68e4eea03afd41019cf5435a5ee6963262559dbf' ]]
/home/${REMOTE_USER}/android-sdk/platform-tools/adb -s emulator-5556 install -r \"\${signed}\""
fi

if [[ ",${COMPONENTS}," == *,ths-runtime,* ]]; then
    # Missing SDK/AVD means a fresh host. Existing hosts only update runtime;
    # collector data is never overwritten by an ordinary deployment.
    if (( bootstrapped == 0 )); then
        remote_sudo "'${REMOTE_GIT_DIR}/ths/deployment/internal/remote/install-services.sh'"
    fi
fi

# Hook-only updates do not reinstall the AVD. Restart user 0 and let the
# repository-managed trade bridge restore the runtime idempotently.
if [[ ",${COMPONENTS}," == *,ths-hook,* && ",${COMPONENTS}," != *,ths-runtime,* ]]; then
    remote_sudo "systemctl restart ths-trade-bridge.service"
fi

"${SSH[@]}" "curl -fsS --max-time 10 http://127.0.0.1:49500/stock/trade/runtime/status | grep -q '\"write_ready\":true'"
echo "THS deployment completed: ${DEPLOY_REVISION} (${COMPONENTS})"
