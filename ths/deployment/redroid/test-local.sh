#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd -- "$SCRIPT_DIR/../.." && pwd)
NAME=ths-local-test
MODE=collector
BASE_IMAGE=${THS_LOCAL_BASE_IMAGE:-ths-redroid:reproducibility-test}
SECRET_DIR=
READY_TIMEOUT=180
ADB_PORT=5559
HTTP_PORT=49690

usage() {
    cat >&2 <<'EOF'
usage: test-local.sh [--name NAME] [--mode collector|trade] [--base-image IMAGE]
                     [--secret-dir DIR] [--ready-timeout SECONDS]
                     [--adb-port PORT] [--http-port PORT]

Builds the current working tree and replaces only ths-local-test plus its
dedicated data volume. Trade mode requires trade_account, trade_broker,
trade_qsid and trade_password files in --secret-dir.
EOF
    exit 64
}

while (($#)); do
    case "$1" in
        --name) NAME=${2:-}; shift 2 ;;
        --mode) MODE=${2:-}; shift 2 ;;
        --base-image) BASE_IMAGE=${2:-}; shift 2 ;;
        --secret-dir) SECRET_DIR=${2:-}; shift 2 ;;
        --ready-timeout) READY_TIMEOUT=${2:-}; shift 2 ;;
        --adb-port) ADB_PORT=${2:-}; shift 2 ;;
        --http-port) HTTP_PORT=${2:-}; shift 2 ;;
        *) usage ;;
    esac
done
[[ "$MODE" == collector || "$MODE" == trade ]] || usage
[[ "$READY_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || usage
[[ "$NAME" =~ ^ths-(local|dev)-[a-zA-Z0-9_.-]+$ ]] || usage
[[ "$ADB_PORT" =~ ^[0-9]+$ && "$HTTP_PORT" =~ ^[0-9]+$ ]] || usage

if [[ -r /proc/config.gz ]] && zgrep -q '^# CONFIG_ANDROID_BINDER_IPC is not set' /proc/config.gz; then
    echo "local kernel does not support Android Binder (CONFIG_ANDROID_BINDER_IPC is disabled)" >&2
    echo "Redroid cannot boot on this Docker host; use a Binder-enabled Linux kernel" >&2
    exit 78
fi
if [[ ! -e /dev/binder && ! -e /dev/binderfs/binder ]] \
    && ! grep -qw binder /proc/filesystems; then
    echo "Android Binder device is unavailable on this Docker host" >&2
    echo "load/mount binder_linux before running the local Redroid test" >&2
    exit 78
fi
docker image inspect "$BASE_IMAGE" >/dev/null 2>&1 || {
    echo "local environment image not found: $BASE_IMAGE" >&2
    echo "set THS_LOCAL_BASE_IMAGE or pass --base-image" >&2
    exit 66
}

trade_args=()
if [[ "$MODE" == trade ]]; then
    [[ -d "$SECRET_DIR" ]] || { echo "trade mode requires --secret-dir" >&2; exit 66; }
    SECRET_DIR=$(realpath "$SECRET_DIR")
    for file in trade_account trade_broker trade_qsid trade_password; do
        [[ -s "$SECRET_DIR/$file" ]] || { echo "missing local trade secret: $file" >&2; exit 66; }
    done
    trade_args=(
        --trade-init existing
        --account-secret "$SECRET_DIR/trade_account"
        --broker-secret "$SECRET_DIR/trade_broker"
        --qsid-secret "$SECRET_DIR/trade_qsid"
        --password-secret "$SECRET_DIR/trade_password"
    )
fi

echo "building Hook from the current local working tree"
(cd "$ROOT" && ./gradlew --no-daemon :app:assembleDebug -x lint)

context=$(mktemp -d)
trap 'rm -rf -- "$context"' EXIT
install -m 0644 "$ROOT/app/build/outputs/apk/debug/app-debug.apk" "$context/ths-hook.apk"
install -m 0644 "$SCRIPT_DIR/image/Dockerfile" "$context/Dockerfile"
install -m 0755 "$SCRIPT_DIR/image/docker-entrypoint.sh" "$SCRIPT_DIR/image/ths-runtime-manager.sh" \
    "$SCRIPT_DIR/image/ths-healthcheck.sh" "$SCRIPT_DIR/image/start-rirud.sh" "$context/"
install -m 0644 "$SCRIPT_DIR/image/init.ths-runtime.rc" "$SCRIPT_DIR/image/bootanim.riru.rc" "$context/"
if revision=$(git -C "$ROOT" rev-parse HEAD 2>/dev/null); then
    :
else
    revision=$(sha256sum "$ROOT/app/build/outputs/apk/debug/app-debug.apk" | cut -d ' ' -f 1)
fi
image="ths-redroid-local:${revision:0:12}"
docker build --pull=false --build-arg "BASE_IMAGE=$BASE_IMAGE" \
    --build-arg "BUILD_REVISION=$revision" -t "$image" "$context"

docker rm -f "$NAME" >/dev/null 2>&1 || true
docker volume rm "${NAME}-data" >/dev/null 2>&1 || true
"$SCRIPT_DIR/add-instance.sh" --name "$NAME" --mode "$MODE" \
    --adb-port "$ADB_PORT" --http-port "$HTTP_PORT" --image "$image" \
    --ready-timeout "$READY_TIMEOUT" "${trade_args[@]}"
echo "development verification passed: name=$NAME, mode=$MODE, http=http://127.0.0.1:$HTTP_PORT"
