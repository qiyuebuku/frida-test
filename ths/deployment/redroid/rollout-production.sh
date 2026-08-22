#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
IMAGE=${1:?usage: $0 IMAGE@sha256:DIGEST REVISION}
REVISION=${2:?usage: $0 IMAGE@sha256:DIGEST REVISION}
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

# Trading identity is sensitive runtime state, not an image build input. A
# production rollout must prove that a destroyed /data volume can be restored
# from independently protected secrets before replacing any collector or trade
# container. Password alone cannot reconstruct the broker account repository.
trade_account_seed=${THS_TRADE_ACCOUNT_SEED_SECRET:-/home/yuyangruan/redroid-poc/secrets/trade_account_seed}
trade_token=${THS_TRADE_TOKEN_SECRET:-/home/yuyangruan/redroid-poc/secrets/trade_token}
trade_password=${THS_TRADE_PASSWORD_SECRET:-/home/yuyangruan/redroid-poc/secrets/trade_password}
for required_secret in "$trade_account_seed" "$trade_password"; do
    [[ -s "$required_secret" ]] || {
        echo "required trade disaster-recovery secret is missing: $required_secret" >&2
        exit 66
    }
done
if [[ -e "$trade_token" && ! -s "$trade_token" ]]; then
    echo "trade token secret exists but is empty: $trade_token" >&2
    exit 66
fi
trade_secret_args=(
    --trade-init secrets
    --account-seed-secret "$trade_account_seed"
    --password-secret "$trade_password"
)
[[ -s "$trade_token" ]] && trade_secret_args+=(--token-secret "$trade_token")

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

old_trade_image=$(docker inspect --format '{{.Config.Image}}' ths-trade)
docker rm -f ths-trade >/dev/null
if ! "$SCRIPT_DIR/add-instance.sh" --name ths-trade --mode trade \
    --adb-port 5560 --http-port 49600 --image "$IMAGE" \
    "${trade_secret_args[@]}" --ready-timeout 360; then
    docker rm -f ths-trade >/dev/null 2>&1 || true
    "$SCRIPT_DIR/add-instance.sh" --name ths-trade --mode trade \
        --adb-port 5560 --http-port 49600 --image "$old_trade_image" \
        "${trade_secret_args[@]}" --ready-timeout 360
    exit 1
fi

echo "Redroid production rollout completed: $IMAGE ($REVISION)"
