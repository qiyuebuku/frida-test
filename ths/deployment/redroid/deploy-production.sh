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
SOURCE_IMAGE="yuyangruan/ths-redroid:git-$REVISION"
REGISTRY_IMAGE="registry:2.8.3@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373"
REGISTRY_BOOTSTRAP_TAG="smart-fund-registry-bootstrap:2.8.3"
EXPECTED_DIGEST=${IMAGE#*@}

tar -C "$WORKSPACE" -czf - ths/deployment/redroid \
    | ssh "${SSH_OPTIONS[@]}" "$REMOTE_TARGET" \
        "set -euo pipefail; install -d '$REMOTE_RELEASE_DIR'; tar -xzf - -C '$REMOTE_RELEASE_DIR'"

# The host currently cannot establish outbound TLS to public registries.
# Bootstrap a digest-pinned loopback registry over SSH, then push the exact
# Actions-built image through an SSH tunnel. Its manifest digest must equal the
# public registry digest before rollout is allowed.
docker pull "$REGISTRY_IMAGE"
docker tag "$REGISTRY_IMAGE" "$REGISTRY_BOOTSTRAP_TAG"
registry_image_id=$(docker image inspect "$REGISTRY_BOOTSTRAP_TAG" --format '{{.Id}}')
docker save "$REGISTRY_BOOTSTRAP_TAG" | gzip -1 \
    | ssh "${SSH_OPTIONS[@]}" "$REMOTE_TARGET" 'gzip -d | docker load >/dev/null'
ssh "${SSH_OPTIONS[@]}" "$REMOTE_TARGET" "set -euo pipefail
[[ \"\$(docker image inspect '$REGISTRY_BOOTSTRAP_TAG' --format '{{.Id}}')\" == '$registry_image_id' ]]
if docker container inspect smart-fund-registry >/dev/null 2>&1; then
    [[ \"\$(docker inspect smart-fund-registry --format '{{.Config.Image}}')\" == '$REGISTRY_BOOTSTRAP_TAG' ]]
    docker start smart-fund-registry >/dev/null
else
    docker run -d --name smart-fund-registry --restart unless-stopped \
        -p 127.0.0.1:5000:5000 -v smart-fund-registry-data:/var/lib/registry \
        '$REGISTRY_BOOTSTRAP_TAG' >/dev/null
fi
for attempt in \$(seq 1 30); do
    curl -fsS http://127.0.0.1:5000/v2/ >/dev/null && exit 0
    sleep 1
done
exit 70"

control_dir=$(mktemp -d /tmp/ths-registry-tunnel.XXXXXX)
control_socket="$control_dir/control"
cleanup_tunnel() {
    ssh "${SSH_OPTIONS[@]}" -S "$control_socket" -O exit "$REMOTE_TARGET" >/dev/null 2>&1 || true
    rmdir "$control_dir" 2>/dev/null || true
}
trap cleanup_tunnel EXIT
ssh "${SSH_OPTIONS[@]}" -M -S "$control_socket" -fN \
    -L 127.0.0.1:55000:127.0.0.1:5000 "$REMOTE_TARGET"
local_tag="127.0.0.1:55000/ths-redroid:git-$REVISION"
docker tag "$SOURCE_IMAGE" "$local_tag"
push_output=$(docker push "$local_tag" 2>&1)
printf '%s\n' "$push_output"
local_digest=$(sed -nE 's/^.*digest: (sha256:[0-9a-f]{64}).*$/\1/p' <<<"$push_output" | tail -n 1)
[[ "$local_digest" == "$EXPECTED_DIGEST" ]] || {
    echo "loopback registry digest does not match the published digest" >&2
    exit 71
}
cleanup_tunnel
trap - EXIT

LOCAL_IMAGE="127.0.0.1:5000/ths-redroid@$EXPECTED_DIGEST"
ssh "${SSH_OPTIONS[@]}" "$REMOTE_TARGET" \
    "'$REMOTE_RELEASE_DIR/ths/deployment/redroid/rollout-production.sh' '$LOCAL_IMAGE' '$REVISION'"
