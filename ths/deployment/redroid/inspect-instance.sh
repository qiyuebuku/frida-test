#!/usr/bin/env bash
set -euo pipefail
[[ $# == 1 ]] || { echo "usage: $0 CONTAINER" >&2; exit 64; }
name=$1
docker inspect --format 'container={{.Name}} state={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$name"
docker exec "$name" sh -c 'cat /data/local/tmp/ths-runtime/status.json 2>/dev/null || true'
