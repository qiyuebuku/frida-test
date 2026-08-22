#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
IMAGE=${1:?usage: $0 IMAGE@sha256:DIGEST REVISION}
REVISION=${2:?usage: $0 IMAGE@sha256:DIGEST REVISION}
TARGETS=${3:-all}
VERIFY_MODE=${4:-target}
[[ "$TARGETS" =~ ^(none|all|collectors|trade)$ ]] || {
    echo "targets must be none, all, collectors, or trade" >&2
    exit 64
}
[[ "$VERIFY_MODE" =~ ^(target|full)$ ]] || {
    echo "verify mode must be target or full" >&2
    exit 64
}
[[ "$IMAGE" =~ ^127\.0\.0\.1:5000/ths-redroid@sha256:[0-9a-f]{64}$ ]] || {
    echo "production requires an approved immutable ths-redroid digest" >&2
    exit 64
}
[[ "$REVISION" =~ ^[0-9a-f]{40}$ ]] || exit 64
[[ "${GITHUB_ACTIONS:-}" == true && "${GITHUB_EVENT_NAME:-}" == push \
    && "${GITHUB_REF:-}" == refs/heads/main && "${GITHUB_SHA:-}" == "$REVISION" \
    && "${RUNNER_ENVIRONMENT:-}" == self-hosted ]] || {
    echo "production rollout is only allowed from the main push on the self-hosted GitHub Actions runner" >&2
    exit 1
}

pulled=false
for attempt in 1 2 3 4 5; do
    if docker pull "$IMAGE"; then
        pulled=true
        break
    fi
    echo "registry pull attempt $attempt failed; retrying" >&2
    sleep $((attempt * 5))
done
[[ "$pulled" == true ]] || {
    echo "unable to pull immutable image after 5 attempts" >&2
    exit 69
}
actual_revision=$(docker image inspect "$IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
[[ "$actual_revision" == "$REVISION" ]] || {
    echo "image revision label does not match the main commit" >&2
    exit 65
}

if [[ "$TARGETS" == none ]]; then
    echo "Redroid image published without runtime rollout: $IMAGE ($REVISION)"
    exit 0
fi

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
if [[ "$VERIFY_MODE" == full ]]; then
    trap cleanup_canary EXIT
    cleanup_canary
    "$SCRIPT_DIR/add-instance.sh" --name ths-rebuild-canary --mode collector \
        --adb-port 5599 --http-port 49699 --image "$IMAGE" --ready-timeout 360
    wait_healthy ths-rebuild-canary
    cleanup_canary
    trap - EXIT
fi

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

if [[ "$TARGETS" == all || "$TARGETS" == collectors ]]; then
    for number in 1 2 3 4 5 6 7 8; do
        replace_collector "$number"
    done
fi

if [[ "$TARGETS" == all || "$TARGETS" == trade ]]; then
    secret_dir=${THS_TRADE_SECRET_DIR:-/home/yuyangruan/redroid-poc/secrets}
    trade_args=(
        --trade-init existing
        --account-secret "$secret_dir/trade_account"
        --broker-secret "$secret_dir/trade_broker"
        --qsid-secret "$secret_dir/trade_qsid"
        --password-secret "$secret_dir/trade_password"
    )
    old_trade_image=$(docker inspect --format '{{.Config.Image}}' ths-trade 2>/dev/null || true)
    docker rm -f ths-trade >/dev/null 2>&1 || true
    # Runtime state must be reproducible from the immutable image and protected
    # credentials. Never inherit an AVD directory containing drifted APKs,
    # SQLite locks, or stale session data.
    docker volume rm ths-trade-data >/dev/null 2>&1 || true
    if ! "$SCRIPT_DIR/add-instance.sh" --name ths-trade --mode trade \
        --adb-port 5560 --http-port 49600 --image "$IMAGE" \
        "${trade_args[@]}" --ready-timeout 360; then
        docker rm -f ths-trade >/dev/null 2>&1 || true
        docker volume rm ths-trade-data >/dev/null 2>&1 || true
        if [[ -n "$old_trade_image" ]]; then
            "$SCRIPT_DIR/add-instance.sh" --name ths-trade --mode trade \
                --adb-port 5560 --http-port 49600 --image "$old_trade_image" \
                "${trade_args[@]}" --ready-timeout 360
        else
            echo "trade creation failed and no previous container image exists for rollback" >&2
        fi
        exit 1
    fi
fi

echo "Redroid production rollout completed: $IMAGE ($REVISION), targets=$TARGETS, verify=$VERIFY_MODE"
