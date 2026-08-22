#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ARTIFACT_DIR=
HOOK_APK=
IMAGE=
REVISION=
while (($#)); do
    case "$1" in
        --artifact-dir) ARTIFACT_DIR=${2:-}; shift 2 ;;
        --hook-apk) HOOK_APK=${2:-}; shift 2 ;;
        --image) IMAGE=${2:-}; shift 2 ;;
        --revision) REVISION=${2:-}; shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 64 ;;
    esac
done
[[ -d "$ARTIFACT_DIR" && -f "$HOOK_APK" && -n "$IMAGE" ]] || exit 64
[[ "$REVISION" =~ ^[0-9a-f]{40}$ ]] || { echo "revision must be a full Git SHA" >&2; exit 64; }
"$SCRIPT_DIR/verify-artifacts.sh" "$ARTIFACT_DIR"

context=$(mktemp -d)
trap 'rm -rf -- "$context"' EXIT
install -d "$context/native-bridge-overlay" "$context/magisk-bootstrap-overlay"
tar -xzf "$ARTIFACT_DIR/native-bridge-android11.tar.gz" --strip-components=1 -C "$context/native-bridge-overlay"
tar -xzf "$ARTIFACT_DIR/magisk-bootstrap-android11.tar.gz" --strip-components=1 -C "$context/magisk-bootstrap-overlay"
install -m 0644 "$ARTIFACT_DIR/ths-11.58.03.apk" "$context/ths.apk"
install -m 0644 "$ARTIFACT_DIR/riru-lsposed-redroid-bootstrap.tar.gz" "$context/riru-lsposed-redroid-bootstrap.tar.gz"
install -m 0644 "$HOOK_APK" "$context/ths-hook.apk"
install -m 0644 "$SCRIPT_DIR/image/Dockerfile" "$context/Dockerfile"
install -m 0755 "$SCRIPT_DIR/image/docker-entrypoint.sh" "$SCRIPT_DIR/image/ths-runtime-manager.sh" "$SCRIPT_DIR/image/ths-healthcheck.sh" "$SCRIPT_DIR/image/start-rirud.sh" "$context/"
install -m 0644 "$SCRIPT_DIR/image/init.ths-runtime.rc" "$SCRIPT_DIR/image/bootanim.riru.rc" "$context/"
docker build --pull=false --build-arg "BUILD_REVISION=$REVISION" -t "$IMAGE" "$context"
