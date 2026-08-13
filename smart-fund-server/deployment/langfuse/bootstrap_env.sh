#!/usr/bin/env bash
set -euo pipefail

target_file="${1:?target env file is required}"
web_port="${2:-3001}"
worker_port="${3:-3031}"
minio_port="${4:-9092}"
retention_days="${5:-90}"
admin_email="${6:-admin@smart-fund.local}"
bind_address="${7:-127.0.0.1}"
public_url="${8:-http://127.0.0.1:${web_port}}"
media_external_url="${9:-http://127.0.0.1:${minio_port}}"

if [[ -s "${target_file}" ]]; then
    python3 - "${target_file}" "${bind_address}" "${public_url}" "${media_external_url}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
updates = {
    "LANGFUSE_BIND_ADDRESS": sys.argv[2],
    "NEXTAUTH_URL": sys.argv[3],
    "LANGFUSE_MEDIA_EXTERNAL_URL": sys.argv[4],
}
lines = path.read_text(encoding="utf-8").splitlines()
output = []
for raw in lines:
    key, separator, _ = raw.partition("=")
    if separator and key in updates:
        output.append(f"{key}={updates.pop(key)}")
    else:
        output.append(raw)
output.extend(f"{key}={value}" for key, value in updates.items())
temporary = path.with_suffix(".tmp")
temporary.write_text("\n".join(output) + "\n", encoding="utf-8")
temporary.chmod(0o600)
temporary.replace(path)
PY
    exit 0
fi

if ! [[ "${web_port}" =~ ^[0-9]+$ && "${worker_port}" =~ ^[0-9]+$ && "${minio_port}" =~ ^[0-9]+$ ]]; then
    echo "Langfuse ports must be integers" >&2
    exit 1
fi
if ! [[ "${retention_days}" =~ ^[0-9]+$ ]]; then
    echo "Langfuse retention days must be a non-negative integer" >&2
    exit 1
fi

target_dir="$(dirname "${target_file}")"
mkdir -p "${target_dir}"
umask 077
temporary_file="$(mktemp "${target_dir}/.langfuse.env.XXXXXX")"
trap 'rm -f "${temporary_file}"' EXIT

random_hex() {
    openssl rand -hex "${1}"
}

cat >"${temporary_file}" <<EOF
LANGFUSE_IMAGE_TAG=4
LANGFUSE_WEB_PORT=${web_port}
LANGFUSE_WORKER_PORT=${worker_port}
LANGFUSE_MINIO_PORT=${minio_port}
LANGFUSE_BIND_ADDRESS=${bind_address}
NEXTAUTH_URL=${public_url}
LANGFUSE_MEDIA_EXTERNAL_URL=${media_external_url}
NEXTAUTH_SECRET=$(random_hex 32)
SALT=$(random_hex 32)
ENCRYPTION_KEY=$(random_hex 32)
POSTGRES_PASSWORD=$(random_hex 32)
CLICKHOUSE_PASSWORD=$(random_hex 32)
REDIS_AUTH=$(random_hex 32)
MINIO_ROOT_USER=langfuse
MINIO_ROOT_PASSWORD=$(random_hex 32)
TELEMETRY_ENABLED=false
LANGFUSE_INIT_ORG_ID=smart-fund
LANGFUSE_INIT_ORG_NAME=Smart Fund
LANGFUSE_INIT_PROJECT_ID=smart-fund-production
LANGFUSE_INIT_PROJECT_NAME=Smart Fund Production
LANGFUSE_INIT_PROJECT_RETENTION=${retention_days}
LANGFUSE_INIT_PROJECT_PUBLIC_KEY=pk-lf-$(random_hex 24)
LANGFUSE_INIT_PROJECT_SECRET_KEY=sk-lf-$(random_hex 24)
LANGFUSE_INIT_USER_EMAIL=${admin_email}
LANGFUSE_INIT_USER_NAME=Smart Fund Admin
LANGFUSE_INIT_USER_PASSWORD=Lf!$(random_hex 24)
EOF

install -m 600 "${temporary_file}" "${target_file}"
