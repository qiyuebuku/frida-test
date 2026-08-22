#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
IMAGE=${1:?usage: $0 IMAGE@sha256:DIGEST REVISION}
REVISION=${2:?usage: $0 IMAGE@sha256:DIGEST REVISION}
[[ "$IMAGE" =~ ^yuyangruan/ths-redroid@sha256:[0-9a-f]{64}$ ]] || {
    echo "production requires an immutable yuyangruan/ths-redroid digest" >&2
    exit 64
}
[[ "$REVISION" =~ ^[0-9a-f]{40}$ ]] || exit 64

docker pull "$IMAGE"
actual_revision=$(docker image inspect "$IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
[[ "$actual_revision" == "$REVISION" ]] || {
    echo "image revision label does not match the main commit" >&2
    exit 65
}

wait_healthy() {
    local name=$1 deadline=$((SECONDS + 360)) state
    while ((SECONDS < deadline)); do
        state=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$name" 2>/dev/null || true)
        [[ "$state" == healthy ]] && return 0
        sleep 2
    done
    return 1
}

cleanup_canary() {
    docker rm -f ths-rebuild-canary >/dev/null 2>&1 || true
    docker volume rm ths-rebuild-canary-data >/dev/null 2>&1 || true
}
trap cleanup_canary EXIT
cleanup_canary
"$SCRIPT_DIR/add-instance.sh" --name ths-rebuild-canary --mode collector \
    --adb-port 5599 --http-port 49699 --image "$IMAGE" --ready-timeout 360
wait_healthy ths-rebuild-canary
cleanup_canary
trap - EXIT

replace_collector() {
    local number=$1 name="ths-collector$1" adb_port=$((5560 + number)) http_port=$((49609 + number)) old_image
    old_image=$(docker inspect --format '{{.Config.Image}}' "$name")
    docker rm -f "$name" >/dev/null
    if ! "$SCRIPT_DIR/add-instance.sh" --name "$name" --mode collector \
        --adb-port "$adb_port" --http-port "$http_port" --image "$IMAGE" --ready-timeout 360; then
        docker rm -f "$name" >/dev/null 2>&1 || true
        "$SCRIPT_DIR/add-instance.sh" --name "$name" --mode collector \
            --adb-port "$adb_port" --http-port "$http_port" --image "$old_image" --ready-timeout 360
        return 1
    fi
}

for number in 1 2 3 4 5 6 7 8; do
    replace_collector "$number"
done

trade_data=${THS_TRADE_DATA_DIR:-/home/yuyangruan/redroid-poc/data-trade}
trade_password=${THS_TRADE_PASSWORD_SECRET:-/home/yuyangruan/redroid-poc/secrets/trade_password}
old_trade_image=$(docker inspect --format '{{.Config.Image}}' ths-trade)
docker rm -f ths-trade >/dev/null
if ! "$SCRIPT_DIR/add-instance.sh" --name ths-trade --mode trade \
    --adb-port 5560 --http-port 49600 --image "$IMAGE" \
    --trade-init existing --data-dir "$trade_data" \
    --password-secret "$trade_password" --ready-timeout 360; then
    docker rm -f ths-trade >/dev/null 2>&1 || true
    "$SCRIPT_DIR/add-instance.sh" --name ths-trade --mode trade \
        --adb-port 5560 --http-port 49600 --image "$old_trade_image" \
        --trade-init existing --data-dir "$trade_data" \
        --password-secret "$trade_password" --ready-timeout 360
    exit 1
fi

echo "Redroid production rollout completed: $IMAGE ($REVISION)"
