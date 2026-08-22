#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ARTIFACT_DIR=
IMAGE=
CONTENT_ID=
while (($#)); do
    case "$1" in
        --artifact-dir) ARTIFACT_DIR=${2:-}; shift 2 ;;
        --image) IMAGE=${2:-}; shift 2 ;;
        --content-id) CONTENT_ID=${2:-}; shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 64 ;;
    esac
done
[[ -d "$ARTIFACT_DIR" && -n "$IMAGE" ]] || exit 64
[[ "$CONTENT_ID" =~ ^[0-9a-f]{64}$ ]] || { echo "content id must be a SHA-256" >&2; exit 64; }
"$SCRIPT_DIR/verify-artifacts.sh" "$ARTIFACT_DIR"

context=$(mktemp -d)
trap 'rm -rf -- "$context"' EXIT
install -d "$context/native-bridge-overlay" "$context/magisk-bootstrap-overlay"
tar -xzf "$ARTIFACT_DIR/native-bridge-android11.tar.gz" --strip-components=1 -C "$context/native-bridge-overlay"
tar -xzf "$ARTIFACT_DIR/magisk-bootstrap-android11.tar.gz" --strip-components=1 -C "$context/magisk-bootstrap-overlay"
install -m 0644 "$ARTIFACT_DIR/ths-11.58.03.apk" "$context/ths.apk"
install -m 0644 "$ARTIFACT_DIR/riru-lsposed-redroid-bootstrap.tar.gz" "$context/riru-lsposed-redroid-bootstrap.tar.gz"
install -m 0644 "$ARTIFACT_DIR/libriruloader-android11-x86_64.so" "$context/libriruloader.so"
install -m 0644 "$SCRIPT_DIR/image/Dockerfile.base" "$context/Dockerfile"
docker build --pull=false --build-arg "BASE_CONTENT_ID=$CONTENT_ID" -t "$IMAGE" "$context"
