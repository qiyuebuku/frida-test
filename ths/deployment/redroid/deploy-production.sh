#!/usr/bin/env bash
set -euo pipefail

WORKSPACE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)
IMAGE=${1:?usage: $0 IMAGE@sha256:DIGEST REVISION ENV_FILE}
REVISION=${2:?usage: $0 IMAGE@sha256:DIGEST REVISION ENV_FILE}
ENV_FILE=${3:?usage: $0 IMAGE@sha256:DIGEST REVISION ENV_FILE}
[[ "$REVISION" =~ ^[0-9a-f]{40}$ ]] || exit 64
[[ "$IMAGE" =~ ^yuyangruan/ths-redroid@sha256:[0-9a-f]{64}$ ]] || exit 64
[[ "${GITHUB_ACTIONS:-}" == true && "${GITHUB_EVENT_NAME:-}" == push \
    && "${GITHUB_REF:-}" == refs/heads/main && "${GITHUB_SHA:-}" == "$REVISION" ]] || {
    echo "Redroid production deployment is only allowed for the current main push in GitHub Actions" >&2
    exit 1
}
# shellcheck disable=SC1090
source "$ENV_FILE"
: "${REMOTE_HOST:?required}" "${REMOTE_PORT:?required}" "${REMOTE_USER:?required}" "${SSH_KEY:?required}"
SSH_OPTIONS=(-i "$SSH_KEY" -p "$REMOTE_PORT" -o StrictHostKeyChecking=yes)
REMOTE_TARGET="$REMOTE_USER@$REMOTE_HOST"
REMOTE_RELEASE_DIR=${REMOTE_RELEASE_DIR:-/home/$REMOTE_USER/.smart-fund-deploy/$REVISION}

tar -C "$WORKSPACE" -czf - ths/deployment/redroid \
    | ssh "${SSH_OPTIONS[@]}" "$REMOTE_TARGET" \
        "set -euo pipefail; install -d '$REMOTE_RELEASE_DIR'; tar -xzf - -C '$REMOTE_RELEASE_DIR'"

ssh "${SSH_OPTIONS[@]}" "$REMOTE_TARGET" \
    "'$REMOTE_RELEASE_DIR/ths/deployment/redroid/rollout-production.sh' '$IMAGE' '$REVISION'"
