#!/usr/bin/env bash
# 从生产库 pg_dump → 拆分到 schema/*.sql
#
# 用法: ./schema/regenerate.sh
#
# 会覆盖 schema/01-06.sql。生成后用 git diff 检查变更是否预期。

set -euo pipefail

REMOTE_HOST="${PROD_DB_HOST:-119.23.227.187}"
REMOTE_PORT="${PROD_DB_PORT:-5432}"
REMOTE_USER="${PROD_DB_USER:-jettask}"
REMOTE_DB="${PROD_DB_NAME:-jettask}"
REMOTE_PASS="${PROD_DB_PASSWORD:-123456}"

# SSH 跳到远程跑 pg_dump (本地没有 pg_dump 16)
SSH_KEY="/tmp/deploy_key_skills"
SSH_HOST="yuyangruan@${REMOTE_HOST}"
SSH_PORT=2210

DUMP=/tmp/ft_schema.sql
ssh -p ${SSH_PORT} -i ${SSH_KEY} -o StrictHostKeyChecking=no ${SSH_HOST} \
  "PGPASSWORD=${REMOTE_PASS} pg_dump -h 127.0.0.1 -U ${REMOTE_USER} -d ${REMOTE_DB} \
   --schema-only --no-owner --no-privileges -t 'ft_*'" > ${DUMP}

echo "✅ pg_dump 完成: $(wc -l < ${DUMP}) 行 → ${DUMP}"

# 调 Python 拆分脚本
python3 "$(dirname "$0")/_split_dump.py" "${DUMP}" "$(dirname "$0")"
