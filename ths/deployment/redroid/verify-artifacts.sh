#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ARTIFACT_DIR=${1:?usage: $0 ARTIFACT_DIR}

while IFS='|' read -r name bytes digest; do
    [[ -n "$name" && "${name:0:1}" != "#" ]] || continue
    path="$ARTIFACT_DIR/$name"
    [[ -f "$path" ]] || { echo "missing artifact: $name" >&2; exit 66; }
    actual_bytes=$(stat -c '%s' "$path")
    actual_digest=$(sha256sum "$path" | cut -d' ' -f1)
    [[ "$actual_bytes" == "$bytes" ]] || { echo "size mismatch: $name" >&2; exit 65; }
    [[ "$actual_digest" == "$digest" ]] || { echo "sha256 mismatch: $name" >&2; exit 65; }
done < "$SCRIPT_DIR/artifacts.lock"
