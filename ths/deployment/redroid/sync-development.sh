#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)
ENV_FILE=${THS_DEPLOY_ENV_FILE:-$ROOT/deployment/production.env}
REMOTE_DIR=${THS_DEV_REMOTE_DIR:-/home/yuyangruan/frida-test-dev}
DEV_SECRET_DIR=${THS_DEV_SECRET_DIR:-/home/yuyangruan/redroid-dev/secrets}
BASE_IMAGE=${THS_DEV_BASE_IMAGE:-127.0.0.1:5000/ths-redroid-base:env-911f60d295de9c41405fc2ad1ef288436be954a0b8b5085324d2346ed9cadfea}
LOCAL_ANDROID_SDK=${ANDROID_HOME:-/home/yuyang/android-sdk}
REMOTE_ANDROID_SDK=${THS_DEV_ANDROID_SDK:-/home/yuyangruan/android-sdk}

value() { sed -n "s/^$1=//p" "$ENV_FILE" | head -n 1; }
remote_host=$(value REMOTE_HOST)
remote_port=$(value REMOTE_PORT)
remote_user=$(value REMOTE_USER)
ssh_key=$(value SSH_KEY)
[[ -n "$remote_port" ]] || remote_port=22
[[ -n "$remote_host" && -n "$remote_user" && -f "$ssh_key" ]] || {
    echo "invalid remote connection configuration: $ENV_FILE" >&2
    exit 66
}

key_copy=$(mktemp /tmp/ths-dev-key.XXXXXX)
cleanup() { shred -u "$key_copy" 2>/dev/null || true; }
trap cleanup EXIT
install -m 600 "$ssh_key" "$key_copy"
ssh_command=(ssh -i "$key_copy" -p "$remote_port" -o BatchMode=yes -o StrictHostKeyChecking=accept-new)

"${ssh_command[@]}" "$remote_user@$remote_host" \
    "install -d -m 700 '$REMOTE_DIR' '$DEV_SECRET_DIR'; for name in trade_account trade_broker trade_qsid trade_password; do install -m 600 '/home/yuyangruan/redroid-poc/secrets/'\"\$name\" '$DEV_SECRET_DIR/'\"\$name\"; done"

rsync -az --delete \
    --exclude .gradle --exclude build --exclude '*/build' \
    -e "ssh -i $key_copy -p $remote_port -o BatchMode=yes -o StrictHostKeyChecking=accept-new" \
    "$ROOT/ths/" "$remote_user@$remote_host:$REMOTE_DIR/ths/"
rsync -az \
    -e "ssh -i $key_copy -p $remote_port -o BatchMode=yes -o StrictHostKeyChecking=accept-new" \
    "$ROOT/AGENTS.md" "$ROOT/CLAUDE.local.md" "$remote_user@$remote_host:$REMOTE_DIR/"
for sdk_component in platforms/android-34 build-tools/34.0.0 platform-tools; do
    [[ -d "$LOCAL_ANDROID_SDK/$sdk_component" ]] || {
        echo "missing local Android SDK component: $LOCAL_ANDROID_SDK/$sdk_component" >&2
        exit 66
    }
    "${ssh_command[@]}" "$remote_user@$remote_host" \
        "install -d -m 755 '$REMOTE_ANDROID_SDK/$(dirname "$sdk_component")'"
    rsync -az \
        -e "ssh -i $key_copy -p $remote_port -o BatchMode=yes -o StrictHostKeyChecking=accept-new" \
        "$LOCAL_ANDROID_SDK/$sdk_component/" \
        "$remote_user@$remote_host:$REMOTE_ANDROID_SDK/$sdk_component/"
done
"${ssh_command[@]}" "$remote_user@$remote_host" \
    "sed -i 's|^sdk.dir=.*|sdk.dir=$REMOTE_ANDROID_SDK|' '$REMOTE_DIR/ths/local.properties'"

"${ssh_command[@]}" "$remote_user@$remote_host" \
    "docker pull '$BASE_IMAGE' >/dev/null && cd '$REMOTE_DIR' && THS_LOCAL_BASE_IMAGE='$BASE_IMAGE' ths/deployment/redroid/test-local.sh --name ths-dev-trade --mode trade --secret-dir '$DEV_SECRET_DIR' --adb-port 5579 --http-port 49690 --ready-timeout 120"
