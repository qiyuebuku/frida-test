#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
BASE_IMAGE=
HOOK_APK=
IMAGE=
REVISION=
while (($#)); do
    case "$1" in
        --base-image) BASE_IMAGE=${2:-}; shift 2 ;;
        --hook-apk) HOOK_APK=${2:-}; shift 2 ;;
        --image) IMAGE=${2:-}; shift 2 ;;
        --revision) REVISION=${2:-}; shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 64 ;;
    esac
done
[[ "$BASE_IMAGE" =~ ^127\.0\.0\.1:5000/ths-redroid-base@sha256:[0-9a-f]{64}$ ]] || {
    echo "base image must be an immutable local-registry digest" >&2
    exit 64
}
[[ -f "$HOOK_APK" && -n "$IMAGE" ]] || exit 64
[[ "$REVISION" =~ ^[0-9a-f]{40}$ ]] || { echo "revision must be a full Git SHA" >&2; exit 64; }

context=$(mktemp -d)
trap 'rm -rf -- "$context"' EXIT
install -m 0644 "$HOOK_APK" "$context/ths-hook.apk"
install -m 0644 "$SCRIPT_DIR/image/Dockerfile" "$context/Dockerfile"
install -m 0755 "$SCRIPT_DIR/image/docker-entrypoint.sh" "$SCRIPT_DIR/image/ths-runtime-manager.sh" "$SCRIPT_DIR/image/ths-healthcheck.sh" "$SCRIPT_DIR/image/start-rirud.sh" "$context/"
install -m 0644 "$SCRIPT_DIR/image/init.ths-runtime.rc" "$SCRIPT_DIR/image/bootanim.riru.rc" "$context/"
docker build --pull=false --build-arg "BASE_IMAGE=$BASE_IMAGE" \
    --build-arg "BUILD_REVISION=$REVISION" -t "$IMAGE" "$context"
