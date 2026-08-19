#!/usr/bin/env bash
# smart-fund-server 生产部署脚本
#
# 用法:
#   REMOTE_SUDO_PASSWORD=... ./deployment/deploy_113.sh --init
#   REMOTE_SUDO_PASSWORD=... ./deployment/deploy_113.sh --components api,workers
#   REMOTE_SUDO_PASSWORD=... ./deployment/deploy_113.sh  # 同步代码、更新 units 并重启全部服务
#   ./deployment/deploy_113.sh --sync-only
#   REMOTE_SUDO_PASSWORD=... ./deployment/deploy_113.sh --restart
#   ./deployment/deploy_113.sh --status
#   ./deployment/deploy_113.sh --logs worker 100
#   ./deployment/deploy_113.sh --test
#   ./deployment/deploy_113.sh --config  # 从项目根目录 .env 重建生产 EnvironmentFile
#   ./deployment/deploy_113.sh --deps    # 更新生产 Python 依赖
#   REMOTE_SUDO_PASSWORD=... ./deployment/deploy_113.sh --units
#   REMOTE_SUDO_PASSWORD=... ./deployment/deploy_113.sh --migrate
#   REMOTE_SUDO_PASSWORD=... ./deployment/deploy_113.sh --langfuse
#   REMOTE_SUDO_PASSWORD=... ./deployment/deploy_113.sh --langfuse-upgrade
#   ./deployment/deploy_113.sh --langfuse-status
#   ./deployment/deploy_113.sh --langfuse-test
#   ./deployment/deploy_113.sh --langfuse-logs web 100
#   ./deployment/deploy_113.sh --langfuse-credentials

set -euo pipefail

DEPLOYMENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_SERVER_DIR="$(cd "${DEPLOYMENT_DIR}/.." && pwd)"
LOCAL_DEPLOY_ENV="${LOCAL_DEPLOY_ENV:-${DEPLOYMENT_DIR}/../../deployment/production.env}"
if [[ -f "${LOCAL_DEPLOY_ENV}" ]]; then
    # shellcheck disable=SC1090
    source "${LOCAL_DEPLOY_ENV}"
fi

REMOTE_HOST="${REMOTE_HOST:-119.23.227.187}"
REMOTE_PORT="${REMOTE_PORT:-1113}"
REMOTE_USER="${REMOTE_USER:-yuyangruan}"
REMOTE_SUDO_PASSWORD="${REMOTE_SUDO_PASSWORD:-}"
DEPLOY_REVISION="${DEPLOY_REVISION:-HEAD}"
DEPLOY_GIT_URL="${DEPLOY_GIT_URL:-git@github.com:qiyuebuku/frida-test.git}"
REMOTE_GIT_DIR="${REMOTE_GIT_DIR:-/home/${REMOTE_USER}/smart-fund-source}"
LOCAL_PYTHON="${LOCAL_PYTHON:-python3}"
COLLECTION_WORKER_CONCURRENCY="${COLLECTION_WORKER_CONCURRENCY:-8}"
THS_WORKER_CONCURRENCY="${THS_WORKER_CONCURRENCY:-8}"
THS_SECTOR_WORKER_CONCURRENCY="${THS_SECTOR_WORKER_CONCURRENCY:-4}"
GENERAL_WORKER_CONCURRENCY="${GENERAL_WORKER_CONCURRENCY:-12}"
KG_RELATION_WORKER_CONCURRENCY="${KG_RELATION_WORKER_CONCURRENCY:-3}"

CONDA_BASE="/home/${REMOTE_USER}/anaconda3"
CONDA_ENV="smart-fund"
PYTHON="${CONDA_BASE}/envs/${CONDA_ENV}/bin/python"

PROJECT_ROOT="/home/${REMOTE_USER}/smart-fund"
REMOTE_SKILLS_DIR="${PROJECT_ROOT}/.claude/skills"
SERVER_DIR="${PROJECT_ROOT}/smart-fund-server"
LEGACY_SERVER_DIR="${REMOTE_SKILLS_DIR}/smart-fund-server"
FUND_TRADE_DIR="${REMOTE_SKILLS_DIR}/fund-trade"
CONFIG_DIR="${PROJECT_ROOT}/config"
ENV_FILE="${CONFIG_DIR}/smart-fund-server.env"
LOG_DIR="${PROJECT_ROOT}/logs/smart-fund-server"
DATA_DIR="${PROJECT_ROOT}/data/smart-fund-server"
ARTIFACT_DIR="${PROJECT_ROOT}/artifacts"
MILVUS_DATA_DIR="${DATA_DIR}/milvus-standalone"
MILVUS_IMAGE="${MILVUS_IMAGE:-milvusdb/milvus:v2.6.20}"
ETCD_IMAGE="${ETCD_IMAGE:-quay.io/coreos/etcd:v3.5.23}"
LANGFUSE_WEB_PORT="${LANGFUSE_WEB_PORT:-3001}"
LANGFUSE_WORKER_PORT="${LANGFUSE_WORKER_PORT:-3031}"
LANGFUSE_MINIO_PORT="${LANGFUSE_MINIO_PORT:-9092}"
LANGFUSE_RETENTION_DAYS="${LANGFUSE_RETENTION_DAYS:-90}"
LANGFUSE_ADMIN_EMAIL="${LANGFUSE_ADMIN_EMAIL:-admin@smart-fund.local}"
LANGFUSE_BIND_ADDRESS="${LANGFUSE_BIND_ADDRESS:-0.0.0.0}"
LANGFUSE_PUBLIC_URL="${LANGFUSE_PUBLIC_URL:-http://${REMOTE_HOST}:${LANGFUSE_WEB_PORT}}"
LANGFUSE_MEDIA_EXTERNAL_URL="${LANGFUSE_MEDIA_EXTERNAL_URL:-http://${REMOTE_HOST}:${LANGFUSE_MINIO_PORT}}"
LOCAL_CAMOUFOX_CACHE="${LOCAL_CAMOUFOX_CACHE:-/home/yuyang/.cache/camoufox}"
REMOTE_CAMOUFOX_CACHE="/home/${REMOTE_USER}/.cache/camoufox"

SVC_MILVUS="smart-fund-milvus"
SVC_ETCD="smart-fund-etcd"
SVC_API="smart-fund-api"
SVC_PERSIST="smart-fund-persist"
SVC_SCHEDULER="smart-fund-scheduler"
SVC_WORKER_THS="smart-fund-worker-ths"
SVC_WORKER_THS_SECTOR="smart-fund-worker-ths-sector"
SVC_WORKER_GENERAL="smart-fund-worker-general"
SVC_THS_STREAM="smart-fund-ths-realtime-stream"
SVC_KG_CARD="smart-fund-kg-card"
SVC_KG_RELATION="smart-fund-kg-relation"
SVC_KG_GRAPH="smart-fund-kg-graph"
TARGET="smart-fund-collector.target"
SERVICES=(
    "${SVC_ETCD}"
    "${SVC_MILVUS}"
    "${SVC_API}"
    "${SVC_PERSIST}"
    "${SVC_SCHEDULER}"
    "${SVC_WORKER_THS}"
    "${SVC_WORKER_THS_SECTOR}"
    "${SVC_WORKER_GENERAL}"
    "${SVC_THS_STREAM}"
    "${SVC_KG_CARD}"
    "${SVC_KG_RELATION}"
    "${SVC_KG_GRAPH}"
)
REQUIRED_SERVICES=(
    "${SVC_ETCD}"
    "${SVC_MILVUS}"
    "${SVC_API}"
    "${SVC_PERSIST}"
    "${SVC_SCHEDULER}"
    "${SVC_WORKER_THS}"
    "${SVC_WORKER_THS_SECTOR}"
    "${SVC_WORKER_GENERAL}"
    "${SVC_THS_STREAM}"
    "${SVC_KG_CARD}"
    "${SVC_KG_RELATION}"
)

LOCAL_WORKSPACE_ROOT="$(cd "${LOCAL_SERVER_DIR}/.." && pwd)"
LOCAL_SKILLS_DIR="${LOCAL_WORKSPACE_ROOT}/.claude/skills"
LOCAL_FUND_TRADE_DIR="${LOCAL_SKILLS_DIR}/fund-trade"
LOCAL_LANGFUSE_DEPLOY_DIR="${LOCAL_SERVER_DIR}/deployment/langfuse"
LOCAL_ENV_FILE="${LOCAL_SERVER_DIR}/.env"
LOCAL_AICLIENT2API_ENV="${LOCAL_AICLIENT2API_ENV:-/home/yuyang/frida-test/AIClient2API/.deployment.local.env}"
JETTASK_WHEEL="/home/yuyang/easy-task/backend/jettask-rs/bindings/python/dist/jettask_python-0.1.0-py3-none-any.whl"
REMOTE_LANGFUSE_DEPLOY_DIR="${SERVER_DIR}/deployment/langfuse"
REMOTE_COMPOSE_FILE="${SERVER_DIR}/deployment/docker/compose.production.yml"
COMPOSE_ENV_FILE="${CONFIG_DIR}/smart-fund-compose.env"
COMPOSE_PROJECT="smart-fund"
COMPOSE_MIGRATION_MARKER="${CONFIG_DIR}/smart-fund-compose.migrated"
LANGFUSE_ENV_FILE="${CONFIG_DIR}/langfuse.env"
LANGFUSE_COMPOSE_PROJECT="smart-fund-langfuse"
REMOTE_FRP_DIR="/home/${REMOTE_USER}/frp_0.52.3_linux_amd64"

SSH_KEY="${SSH_KEY:-/mnt/c/Users/阮雨阳/.ssh/id_rsa}"
SSH_KEY_TMP="/tmp/deploy_key_smart_fund_113"
SSH_OPTS=(
    -p "${REMOTE_PORT}"
    -i "${SSH_KEY_TMP}"
    -o StrictHostKeyChecking=no
    -o ConnectTimeout=10
)
SCP_OPTS=(
    -P "${REMOTE_PORT}"
    -i "${SSH_KEY_TMP}"
    -o StrictHostKeyChecking=no
    -o ConnectTimeout=10
)

RSYNC_EXCLUDES=(
    --exclude=.git/
    --exclude=.env
    --exclude=.env.*
    --exclude=.deployment.local.env
    --exclude=.venv/
    --exclude=__pycache__/
    --exclude=.pytest_cache/
    --exclude=.ruff_cache/
    --exclude='*.pyc'
    --exclude=data/
    --exclude=logs/
    --exclude=output/
    --exclude=scraped_docs/
    --exclude=packages/references/
)

setup_ssh_key() {
    if [[ ! -f "${SSH_KEY}" ]]; then
        echo "SSH key 不存在: ${SSH_KEY}" >&2
        exit 1
    fi
    if [[ ! -f "${SSH_KEY_TMP}" || "${SSH_KEY}" -nt "${SSH_KEY_TMP}" ]]; then
        cp "${SSH_KEY}" "${SSH_KEY_TMP}"
        chmod 600 "${SSH_KEY_TMP}"
    fi
}

ssh_cmd() {
    ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" "$@"
}

sudo_cmd() {
    local command="$*"
    local escaped_command
    printf -v escaped_command '%q' "${command}"
    if [[ -n "${REMOTE_SUDO_PASSWORD}" ]]; then
        local escaped_password
        printf -v escaped_password '%q' "${REMOTE_SUDO_PASSWORD}"
        ssh_cmd "printf '%s\n' ${escaped_password} | sudo -S bash -lc ${escaped_command}"
    else
        ssh_cmd "sudo -n bash -lc ${escaped_command}"
    fi
}

ensure_remote_dirs() {
    ssh_cmd "mkdir -p \
        '${SERVER_DIR}' \
        '${FUND_TRADE_DIR}' \
        '${CONFIG_DIR}' \
        '${LOG_DIR}' \
        '${DATA_DIR}/milvus' \
        '${MILVUS_DATA_DIR}/volumes/milvus' \
        '${DATA_DIR}/embedding_cache' \
        '${DATA_DIR}/llm_proxy_cache' \
        '${ARTIFACT_DIR}'"
}

sync_code() {
    echo "通过 Git 同步 smart-fund-server revision ${DEPLOY_REVISION}..."
    ensure_remote_dirs
    ssh_cmd "set -euo pipefail
if [[ ! -d '${REMOTE_GIT_DIR}/.git' ]]; then
    git clone --filter=blob:none '${DEPLOY_GIT_URL}' '${REMOTE_GIT_DIR}'
fi
git -C '${REMOTE_GIT_DIR}' fetch --prune origin
git -C '${REMOTE_GIT_DIR}' cat-file -e '${DEPLOY_REVISION}^{commit}'
git -C '${REMOTE_GIT_DIR}' checkout --detach --force '${DEPLOY_REVISION}'
mkdir -p '${SERVER_DIR}'
rsync -a --delete --exclude='.git/' --exclude='.env' --exclude='.venv/' \
  '${REMOTE_GIT_DIR}/smart-fund-server/' '${SERVER_DIR}/'
printf '%s\n' '${DEPLOY_REVISION}' > '${SERVER_DIR}/.deployed-revision'"

    if [[ -d "${LOCAL_FUND_TRADE_DIR}" ]]; then
        rsync -az --delete "${RSYNC_EXCLUDES[@]}" \
            -e "ssh ${SSH_OPTS[*]}" \
            "${LOCAL_FUND_TRADE_DIR}/" \
            "${REMOTE_USER}@${REMOTE_HOST}:${FUND_TRADE_DIR}/"
    fi

    if [[ -f "${JETTASK_WHEEL}" ]]; then
        rsync -az \
            -e "ssh ${SSH_OPTS[*]}" \
            "${JETTASK_WHEEL}" \
            "${REMOTE_USER}@${REMOTE_HOST}:${ARTIFACT_DIR}/"
    fi
    echo "Git revision 同步完成"
}

sync_langfuse_files() {
    echo "同步 Langfuse 部署与健康检查文件..."
    ssh_cmd "mkdir -p '${REMOTE_LANGFUSE_DEPLOY_DIR}' \
        '${SERVER_DIR}/src/infrastructure/agent_runtime' \
        '${SERVER_DIR}/src/interfaces/cli'"
    rsync -az --delete \
        -e "ssh ${SSH_OPTS[*]}" \
        "${LOCAL_LANGFUSE_DEPLOY_DIR}/" \
        "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_LANGFUSE_DEPLOY_DIR}/"
    rsync -az \
        -e "ssh ${SSH_OPTS[*]}" \
        "${LOCAL_SERVER_DIR}/src/infrastructure/agent_runtime/langfuse_health.py" \
        "${REMOTE_USER}@${REMOTE_HOST}:${SERVER_DIR}/src/infrastructure/agent_runtime/"
    rsync -az \
        -e "ssh ${SSH_OPTS[*]}" \
        "${LOCAL_SERVER_DIR}/src/interfaces/cli/agent.py" \
        "${REMOTE_USER}@${REMOTE_HOST}:${SERVER_DIR}/src/interfaces/cli/"
    echo "Langfuse 部署文件同步完成"
}

set_env_value() {
    local file="$1"
    local key="$2"
    local value="$3"
    "${LOCAL_PYTHON}" - "${file}" "${key}" "${value}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
lines = path.read_text(encoding="utf-8").splitlines()
replacement = f"{key}={value}"
for index, line in enumerate(lines):
    if line.startswith(f"{key}="):
        lines[index] = replacement
        break
else:
    lines.append(replacement)
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

remove_env_value() {
    local file="$1"
    local key="$2"
    "${LOCAL_PYTHON}" - "${file}" "${key}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
lines = [
    line for line in path.read_text(encoding="utf-8").splitlines()
    if not line.startswith(f"{key}=")
]
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

copy_env_value() {
    local source_file="$1"
    local target_file="$2"
    local source_key="$3"
    local target_key="$4"
    "${LOCAL_PYTHON}" - "${source_file}" "${target_file}" "${source_key}" "${target_key}" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1])
target = Path(sys.argv[2])
source_key = sys.argv[3]
target_key = sys.argv[4]

value = ""
for raw in source.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    key, separator, candidate = line.partition("=")
    if separator and key.strip() == source_key:
        value = candidate.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        break
if not value:
    raise SystemExit(f"{source_key} is missing from {source}")

lines = target.read_text(encoding="utf-8").splitlines()
replacement = f"{target_key}={value}"
for index, line in enumerate(lines):
    if line.startswith(f"{target_key}="):
        lines[index] = replacement
        break
else:
    lines.append(replacement)
target.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

install_production_config() {
    if [[ ! -f "${LOCAL_ENV_FILE}" ]]; then
        echo "本地配置不存在: ${LOCAL_ENV_FILE}" >&2
        exit 1
    fi

    echo "生成生产 EnvironmentFile..."
    local plain_env redis_password redis_url systemd_env
    plain_env="$(mktemp)"
    systemd_env="$(mktemp)"
    cp "${LOCAL_ENV_FILE}" "${plain_env}"

    redis_password="$(
        ssh_cmd "if [[ -r '${CONFIG_DIR}/redis-access.secret' ]]; then cat '${CONFIG_DIR}/redis-access.secret'; fi"
    )"
    redis_url="redis://127.0.0.1:6379/0"
    if [[ -n "${redis_password}" ]]; then
        redis_url="redis://:${redis_password}@127.0.0.1:6379/0"
    fi

    set_env_value "${plain_env}" "DB_HOST" "10.168.1.113"
    set_env_value "${plain_env}" "DB_PORT" "5432"
    remove_env_value "${plain_env}" "PG_URL"
    set_env_value "${plain_env}" "REDIS_URL" "${redis_url}"
    set_env_value "${plain_env}" "JETTASK_PREFIX" "fund_aggregator_prod"
    set_env_value "${plain_env}" "SERVER_HOST" "0.0.0.0"
    set_env_value "${plain_env}" "SERVER_PORT" "8900"
    set_env_value "${plain_env}" "SERVICE_BASE_URL" "http://127.0.0.1:8900"
    set_env_value "${plain_env}" "THS_NATIVE_BRIDGE_URL" "http://127.0.0.1:49350"
    # 交易通道固定路由 trade 专属实例（user17，forward 49390→设备18980；2026-08-18
    # 起交易与采集解耦：owner/其余 8 实例 role 门禁全关，交易会话独占 user17）
    set_env_value "${plain_env}" "THS_TRADE_BASE_URL" "http://127.0.0.1:49350"
    remove_env_value "${plain_env}" "THS_NATIVE_BRIDGE_ROUTES"
    set_env_value "${plain_env}" "THS_APP_HTTP_BRIDGE_URL" "http://127.0.0.1:49350"
    set_env_value "${plain_env}" "THS_NATIVE_LOAD_BALANCED" "1"
    set_env_value "${plain_env}" "THS_APP_HTTP_MAX_CONCURRENCY" "8"
    set_env_value "${plain_env}" "THS_NATIVE_COMMAND_STREAM_ENABLED" "0"
    set_env_value "${plain_env}" "THS_NATIVE_COMMAND_HOST" "127.0.0.1"
    set_env_value "${plain_env}" "THS_NATIVE_COMMAND_PORT" "49302"
    set_env_value "${plain_env}" "THS_NATIVE_STREAM_PORT" "49352"
    set_env_value "${plain_env}" "THS_APP_GLOBAL_PUSH_ENABLED" "true"
    set_env_value "${plain_env}" "THS_NATIVE_SECTOR_MAX_CONCURRENCY" "8"
    set_env_value "${plain_env}" "COLLECTION_WORKER_CONCURRENCY" "8"
    set_env_value "${plain_env}" "SMART_FUND_MCP_TARGET" "prod"
    set_env_value "${plain_env}" "SMART_FUND_MCP_ADAPTER_NAME" "financial"
    set_env_value "${plain_env}" "SMART_FUND_MCP_PUBLIC_URL" "http://119.23.227.187:8900/mcp"
    set_env_value "${plain_env}" "EMBEDDING_URL" "http://10.168.1.113:8901"
    set_env_value "${plain_env}" "RERANKER_URL" "http://10.168.1.155:8860"
    remove_env_value "${plain_env}" "MILVUS_URI"
    set_env_value "${plain_env}" "KG_MILVUS_URI" "http://127.0.0.1:19530"
    set_env_value "${plain_env}" "EMBEDDING_FILE_CACHE_DIR" "${DATA_DIR}/embedding_cache"
    set_env_value "${plain_env}" "LLM_PROXY_FILE_CACHE_DIR" "${DATA_DIR}/llm_proxy_cache"
    set_env_value "${plain_env}" "AICLIENT2API_LLM_BASE_URL" "http://127.0.0.1:3000/v1"
    # Financial Agent uses the native OpenAI Responses route. GLM-5.3 keeps
    # thinking enabled and the role-specific runtime selects low effort.
    set_env_value "${plain_env}" "SMART_FUND_AGENT_LLM_BASE_URL" "http://127.0.0.1:3000/v1"
    set_env_value "${plain_env}" "SMART_FUND_AGENT_MODEL" "glm-5.3"
    if [[ -f "${LOCAL_AICLIENT2API_ENV}" ]]; then
        copy_env_value \
            "${LOCAL_AICLIENT2API_ENV}" \
            "${plain_env}" \
            "AICLIENT2API_API_KEY" \
            "AICLIENT2API_LLM_API_KEY"
        copy_env_value \
            "${LOCAL_AICLIENT2API_ENV}" \
            "${plain_env}" \
            "ZHIPU_ANTHROPIC_TOKEN" \
            "ZHIPU_CODING_PLAN_API_KEY"
    elif ! grep -q '^AICLIENT2API_LLM_API_KEY=.' "${plain_env}"; then
        echo "AIClient2API API Key 未配置: ${LOCAL_AICLIENT2API_ENV}" >&2
        exit 1
    fi
    set_env_value "${plain_env}" "SKILL_DIR" "${FUND_TRADE_DIR}"
    set_env_value "${plain_env}" "SKILLS_DIR" "${REMOTE_SKILLS_DIR}"
    copy_env_value \
        "${LOCAL_ENV_FILE}" \
        "${plain_env}" \
        "SMART_FUND_AGENT_LANGFUSE_PUBLIC_KEY" \
        "SMART_FUND_AGENT_LANGFUSE_PUBLIC_KEY"
    copy_env_value \
        "${LOCAL_ENV_FILE}" \
        "${plain_env}" \
        "SMART_FUND_AGENT_LANGFUSE_SECRET_KEY" \
        "SMART_FUND_AGENT_LANGFUSE_SECRET_KEY"
    set_env_value \
        "${plain_env}" \
        "SMART_FUND_AGENT_LANGFUSE_BASE_URL" \
        "http://127.0.0.1:${LANGFUSE_WEB_PORT}"

    "${LOCAL_PYTHON}" - "${plain_env}" "${systemd_env}" <<'PY'
from pathlib import Path
import shlex
import sys

source = Path(sys.argv[1])
target = Path(sys.argv[2])
output = []
for raw in source.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    key, separator, value = line.partition("=")
    if not separator:
        continue
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    output.append(f"{key.strip()}={shlex.quote(value)}")
target.write_text("\n".join(output) + "\n", encoding="utf-8")
PY

    scp "${SCP_OPTS[@]}" "${systemd_env}" \
        "${REMOTE_USER}@${REMOTE_HOST}:/tmp/smart-fund-server.env"
    ssh_cmd "install -m 600 /tmp/smart-fund-server.env '${ENV_FILE}' && rm -f /tmp/smart-fund-server.env"
    if ssh_cmd "test -s '${LANGFUSE_ENV_FILE}'"; then
        configure_langfuse_client_env
    fi
    rm -f "${plain_env}" "${systemd_env}"
    echo "生产配置已安装到 ${ENV_FILE}"
}

apply_schema_migrations() {
    echo "执行幂等数据库迁移..."
    local remote_migration="/tmp/smart-fund-server-migrations.sql"
    local remote_jettask_migration="/tmp/smart-fund-jettask-migrations.sql"
    ssh_cmd "cat '${SERVER_DIR}'/schema/migrations/*.sql \
        > '${remote_migration}' && chmod 644 '${remote_migration}'"
    ssh_cmd "set -a; . '${ENV_FILE}'; set +a; \
        PGPASSWORD=\"\${DB_PASSWORD:-}\" psql -v ON_ERROR_STOP=1 \
        -h \"\${DB_HOST:-127.0.0.1}\" -p \"\${DB_PORT:-5432}\" \
        -U \"\${DB_USER:-postgres}\" -d \"\${DB_NAME}\" \
        -f '${remote_migration}'; rm -f '${remote_migration}'"
    ssh_cmd "cat '${SERVER_DIR}'/schema/jettask_migrations/*.sql \
        > '${remote_jettask_migration}' && chmod 644 '${remote_jettask_migration}'"
    ssh_cmd "set -a; . '${ENV_FILE}'; set +a; \
        PGPASSWORD=\"\${DB_PASSWORD:-}\" psql -v ON_ERROR_STOP=1 \
        -h \"\${DB_HOST:-127.0.0.1}\" -p \"\${DB_PORT:-5432}\" \
        -U \"\${DB_USER:-postgres}\" -d \"\${JETTASK_DB_NAME:-jettask_queue}\" \
        -f '${remote_jettask_migration}'; rm -f '${remote_jettask_migration}'"
    echo "数据库迁移完成"
}

install_redis() {
    echo "检查系统依赖与 Redis..."
    if ! ssh_cmd "command -v redis-server >/dev/null 2>&1 \
        && command -v tmux >/dev/null 2>&1 \
        && command -v curl >/dev/null 2>&1 \
        && command -v psql >/dev/null 2>&1"; then
        sudo_cmd "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y redis-server tmux curl postgresql-client"
    fi
    sudo_cmd "systemctl enable --now redis-server"
    # 生产数据集较大且写入频繁；默认 save 60 10000 会近乎每分钟全量生成 RDB。
    # 保留 5/15 分钟快照，避免 BGSAVE 长时间周期性争用 CPU 与磁盘。
    sudo_cmd "sed -i '/^save 60 10000$/d' /etc/redis/redis.conf"
    ssh_cmd "REDISCLI_AUTH=\"\$(cat '${CONFIG_DIR}/redis-access.secret' 2>/dev/null || true)\" \
        redis-cli -h 127.0.0.1 CONFIG SET save '900 1 300 10' | grep -q OK"
    ssh_cmd "REDISCLI_AUTH=\"\$(cat '${CONFIG_DIR}/redis-access.secret' 2>/dev/null || true)\" \
        redis-cli -h 127.0.0.1 ping | grep -q PONG"
    echo "系统依赖与 Redis 可用"
}

ensure_docker_compose() {
    if ssh_cmd "command -v docker >/dev/null && docker compose version >/dev/null 2>&1"; then
        return
    fi
    sudo_cmd "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-compose-v2"
    sudo_cmd "systemctl enable --now docker.service; usermod -aG docker ${REMOTE_USER}"
    ssh_cmd "docker compose version >/dev/null"
}

camoufox_binary_ready() {
    ssh_cmd "test -x '${REMOTE_CAMOUFOX_CACHE}/camoufox-bin' \
        && '${PYTHON}' -m camoufox path >/dev/null"
}

sync_camoufox_cache() {
    if [[ ! -x "${LOCAL_CAMOUFOX_CACHE}/camoufox-bin" ]]; then
        echo "本机 Camoufox 运行时不存在: ${LOCAL_CAMOUFOX_CACHE}" >&2
        return 1
    fi

    echo "Camoufox 上游下载不可用，使用本机 Linux 运行时兜底..."
    ssh_cmd "mkdir -p '${REMOTE_CAMOUFOX_CACHE}'"
    rsync -az --delete \
        --exclude='fonts/windows/' \
        --exclude='fonts/macos/' \
        -e "ssh ${SSH_OPTS[*]}" \
        "${LOCAL_CAMOUFOX_CACHE}/" \
        "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_CAMOUFOX_CACHE}/"
}

install_dependencies() {
    echo "安装生产 Python 环境..."
    if ! ssh_cmd "test -x '${PYTHON}'"; then
        ssh_cmd "'${CONDA_BASE}/bin/conda' create -n '${CONDA_ENV}' python=3.12 -y"
    fi

    ssh_cmd "'${PYTHON}' -m pip install --upgrade pip wheel 'setuptools<81'"
    # 本地 wheel 处于开发版本，文件名版本号可能不变；必须强制覆盖旧 Python/Rust 协议实现。
    ssh_cmd "'${PYTHON}' -m pip install --force-reinstall --no-deps \
        '${ARTIFACT_DIR}/jettask_python-0.1.0-py3-none-any.whl'"
    ssh_cmd "'${PYTHON}' -m pip install \
        'fastapi>=0.100' 'uvicorn[standard]' psycopg2-binary 'sqlalchemy>=2.0' \
        asyncpg httpx redis 'pydantic>=2.12,<2.13' pydantic-settings click prometheus-client \
        'pymilvus[milvus_lite]>=2.6,<3' 'milvus-lite>=2.5,<3' \
        'langfuse>=4.7,<5' 'mcp>=1.27,<2' \
        'openai-agents>=0.17,<0.18' 'openai>=2.37,<2.41' \
        'openinference-instrumentation-openai-agents>=1.5,<2' \
        'networkx>=3.0' 'graspologic-native>=1.2,<2' 'setuptools<81' \
        'exchange-calendars>=4.11,<5' \
        akshare curl_cffi PyYAML 'camoufox==0.4.11' html2text beautifulsoup4 lxml"
    if ! camoufox_binary_ready; then
        ssh_cmd "'${PYTHON}' -m camoufox fetch" || true
    fi
    if ! camoufox_binary_ready; then
        sync_camoufox_cache
    fi
    camoufox_binary_ready

    ssh_cmd "cd '${SERVER_DIR}' && '${PYTHON}' -c \
        'import fastapi, jettask, redis, sqlalchemy, pymilvus, akshare, camoufox, mcp, exchange_calendars, agents, openai, openinference.instrumentation.openai_agents; print(\"imports ok\")'"
    echo "Python 依赖安装完成"
}

install_units() {
    echo "安装 systemd units..."
    local unit_dir
    unit_dir="$(mktemp -d)"

    cat > "${unit_dir}/milvus-embed-etcd.yaml" <<EOF
listen-client-urls: http://0.0.0.0:2379
advertise-client-urls: http://0.0.0.0:2379
quota-backend-bytes: 4294967296
# The embedded server and Milvus components start in the same process.  Do not
# advance the first election tick: on a loaded host the default can elect and
# immediately replace a leader while MixCoord is obtaining its first session
# ID, which makes Milvus panic with "etcdserver: leader changed".
heartbeat-interval: 500
election-timeout: 5000
initial-election-tick-advance: false
auto-compaction-mode: revision
auto-compaction-retention: "1000"
EOF

    cat > "${unit_dir}/milvus-user.yaml" <<EOF
# Production overrides for Smart Fund Milvus Standalone.  Metadata is served
# by the separately supervised etcd process so Milvus cannot race its own
# embedded etcd leader election during startup.
etcd:
  endpoints: localhost:2379
  use:
    embed: false
EOF

    cat > "${unit_dir}/${SVC_ETCD}.service" <<EOF
[Unit]
Description=Smart Fund etcd for Milvus metadata
Wants=network-online.target docker.service
After=network-online.target docker.service
PartOf=${TARGET}

[Service]
Type=simple
User=${REMOTE_USER}
Group=${REMOTE_USER}
Environment=DOCKER_CONFIG=${CONFIG_DIR}/docker-no-credential
ExecStartPre=-/usr/bin/docker rm -f ${SVC_ETCD}
ExecStart=/usr/bin/docker run --rm --name ${SVC_ETCD} --network host \
    -v ${MILVUS_DATA_DIR}/volumes/milvus/etcd:/etcd-data \
    ${ETCD_IMAGE} /usr/local/bin/etcd \
    --name default --data-dir /etcd-data \
    --listen-client-urls http://0.0.0.0:2379 \
    --advertise-client-urls http://127.0.0.1:2379 \
    --listen-peer-urls http://localhost:2380 \
    --initial-advertise-peer-urls http://localhost:2380 \
    --initial-cluster default=http://localhost:2380 \
    --initial-cluster-state existing \
    --heartbeat-interval 500 --election-timeout 5000 \
    --initial-election-tick-advance=false
ExecStartPost=/bin/bash -c 'for i in {1..60}; do /usr/bin/curl --fail --silent --max-time 2 http://127.0.0.1:2379/health >/dev/null && exit 0; sleep 1; done; exit 1'
ExecStop=/usr/bin/docker stop -t 60 ${SVC_ETCD}
Restart=always
RestartSec=10
TimeoutStartSec=90
TimeoutStopSec=90
KillSignal=SIGTERM
StandardOutput=append:${LOG_DIR}/etcd.log
StandardError=append:${LOG_DIR}/etcd.log

[Install]
WantedBy=${TARGET}
EOF

    cat > "${unit_dir}/smart-fund-milvus-prestart.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

container_name="${1:?container name is required}"
image="${2:?Milvus image is required}"

# Only remove the named production container. Never scan or signal Milvus
# processes globally: recovery/maintenance instances may intentionally run on
# the same host and must not be terminated by a production restart.
/usr/bin/docker rm -f "${container_name}" >/dev/null 2>&1 || true
EOF

    cat > "${unit_dir}/smart-fund-milvus-wait-ready.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

for _ in $(seq 1 150); do
    if /usr/bin/curl --fail --silent --max-time 3 \
        http://127.0.0.1:9091/healthz >/dev/null; then
        exit 0
    fi
    sleep 2
done

echo "Milvus did not become healthy before the startup deadline" >&2
exit 1
EOF

    cat > "${unit_dir}/${SVC_MILVUS}.service" <<EOF
[Unit]
Description=Smart Fund Milvus Standalone
Wants=network-online.target docker.service ${SVC_ETCD}.service
After=network-online.target docker.service ${SVC_ETCD}.service
PartOf=${TARGET}

[Service]
Type=simple
User=${REMOTE_USER}
Group=${REMOTE_USER}
Environment=DOCKER_CONFIG=${CONFIG_DIR}/docker-no-credential
ExecStartPre=${CONFIG_DIR}/smart-fund-milvus-prestart.sh ${SVC_MILVUS} ${MILVUS_IMAGE}
ExecStart=/usr/bin/docker run --rm --name ${SVC_MILVUS} --network host --security-opt seccomp:unconfined -e ETCD_USE_EMBED=false -e COMMON_STORAGETYPE=local -e DEPLOY_MODE=STANDALONE -v ${MILVUS_DATA_DIR}/volumes/milvus:/var/lib/milvus -v ${CONFIG_DIR}/milvus-user.yaml:/milvus/configs/user.yaml:ro ${MILVUS_IMAGE} milvus run standalone
ExecStartPost=${CONFIG_DIR}/smart-fund-milvus-wait-ready.sh
ExecStop=/usr/bin/docker stop -t 60 ${SVC_MILVUS}
Restart=always
RestartSec=10
TimeoutStartSec=0
TimeoutStopSec=90
KillSignal=SIGTERM
StandardOutput=append:${LOG_DIR}/milvus.log
StandardError=append:${LOG_DIR}/milvus.log

[Install]
WantedBy=${TARGET}
EOF

    cat > "${unit_dir}/${SVC_API}.service" <<EOF
[Unit]
Description=Smart Fund API
Wants=network-online.target postgresql.service redis-server.service ${SVC_MILVUS}.service
After=network-online.target postgresql.service redis-server.service ${SVC_MILVUS}.service
PartOf=${TARGET}

[Service]
Type=simple
User=${REMOTE_USER}
Group=${REMOTE_USER}
WorkingDirectory=${SERVER_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONUNBUFFERED=1
ExecStart=${PYTHON} -m src.interfaces.cli api
Restart=always
RestartSec=5
TimeoutStopSec=60
KillSignal=SIGTERM
UMask=0027
StandardOutput=append:${LOG_DIR}/api.log
StandardError=append:${LOG_DIR}/api.log

[Install]
WantedBy=${TARGET}
EOF

    cat > "${unit_dir}/${SVC_PERSIST}.service" <<EOF
[Unit]
Description=Smart Fund Jettask Persist
Wants=network-online.target postgresql.service redis-server.service
After=network-online.target postgresql.service redis-server.service
PartOf=${TARGET}

[Service]
Type=simple
User=${REMOTE_USER}
Group=${REMOTE_USER}
WorkingDirectory=${SERVER_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONUNBUFFERED=1
ExecStart=${PYTHON} -m src.interfaces.cli persist
Restart=always
RestartSec=5
TimeoutStopSec=60
KillSignal=SIGTERM
UMask=0027
StandardOutput=append:${LOG_DIR}/persist.log
StandardError=append:${LOG_DIR}/persist.log

[Install]
WantedBy=${TARGET}
EOF

    cat > "${unit_dir}/${SVC_SCHEDULER}.service" <<EOF
[Unit]
Description=Smart Fund Jettask Scheduler
Wants=network-online.target ${SVC_PERSIST}.service
After=network-online.target ${SVC_PERSIST}.service
PartOf=${TARGET}

[Service]
Type=simple
User=${REMOTE_USER}
Group=${REMOTE_USER}
WorkingDirectory=${SERVER_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONUNBUFFERED=1
ExecStart=${PYTHON} -m src.interfaces.cli scheduler
Restart=always
RestartSec=5
TimeoutStopSec=60
KillSignal=SIGTERM
UMask=0027
StandardOutput=append:${LOG_DIR}/scheduler.log
StandardError=append:${LOG_DIR}/scheduler.log

[Install]
WantedBy=${TARGET}
EOF

    local worker_name worker_group worker_concurrency
    for worker_group in ths ths-sector general; do
        case "${worker_group}" in
            ths) worker_name="${SVC_WORKER_THS}"; worker_concurrency="${THS_WORKER_CONCURRENCY}" ;;
            ths-sector) worker_name="${SVC_WORKER_THS_SECTOR}"; worker_concurrency="${THS_SECTOR_WORKER_CONCURRENCY}" ;;
            general) worker_name="${SVC_WORKER_GENERAL}"; worker_concurrency="${GENERAL_WORKER_CONCURRENCY}" ;;
        esac
    cat > "${unit_dir}/${worker_name}.service" <<EOF
[Unit]
Description=Smart Fund Collection Worker (${worker_group})
Wants=network-online.target ${SVC_API}.service ${SVC_PERSIST}.service redis-server.service
After=network-online.target ${SVC_API}.service ${SVC_PERSIST}.service redis-server.service
PartOf=${TARGET}

[Service]
Type=simple
User=${REMOTE_USER}
Group=${REMOTE_USER}
WorkingDirectory=${SERVER_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONUNBUFFERED=1
ExecStart=${PYTHON} -m src.interfaces.cli worker --group ${worker_group} -c ${worker_concurrency}
Restart=always
RestartSec=5
TimeoutStopSec=60
KillSignal=SIGTERM
UMask=0027
StandardOutput=append:${LOG_DIR}/worker-${worker_group}.log
StandardError=append:${LOG_DIR}/worker-${worker_group}.log

[Install]
WantedBy=${TARGET}
EOF
    done

    cat > "${unit_dir}/${SVC_THS_STREAM}.service" <<EOF
[Unit]
Description=Smart Fund THS Native Realtime Stream
Wants=network-online.target postgresql.service ths-collector-bridge.service
After=network-online.target postgresql.service ths-collector-bridge.service
PartOf=${TARGET}

[Service]
Type=simple
User=${REMOTE_USER}
Group=${REMOTE_USER}
WorkingDirectory=${SERVER_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONUNBUFFERED=1
ExecStart=${PYTHON} -m src.interfaces.cli ths-realtime-stream
ExecStartPre=/bin/bash -c 'for i in {1..45}; do /usr/bin/curl --fail --silent --max-time 3 http://127.0.0.1:49301/health >/dev/null && exit 0; sleep 1; done; exit 1'
ExecStartPost=/bin/bash -c 'for i in {1..45}; do (exec 3<>/dev/tcp/127.0.0.1/49302) 2>/dev/null && exit 0; sleep 1; done; exit 1'
Restart=always
RestartSec=5
TimeoutStopSec=60
KillSignal=SIGTERM
UMask=0027
StandardOutput=append:${LOG_DIR}/ths-realtime-stream.log
StandardError=append:${LOG_DIR}/ths-realtime-stream.log

[Install]
WantedBy=${TARGET}
EOF

    cat > "${unit_dir}/${SVC_KG_CARD}.service" <<EOF
[Unit]
Description=Smart Fund KG Card Worker
Wants=network-online.target ${SVC_API}.service ${SVC_PERSIST}.service ${SVC_MILVUS}.service redis-server.service
After=network-online.target ${SVC_API}.service ${SVC_PERSIST}.service ${SVC_MILVUS}.service redis-server.service
PartOf=${TARGET}

[Service]
Type=simple
User=${REMOTE_USER}
Group=${REMOTE_USER}
WorkingDirectory=${SERVER_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONUNBUFFERED=1
ExecStart=${PYTHON} -m src.interfaces.cli knowledge-worker --stage card -c 1
Restart=always
RestartSec=5
TimeoutStopSec=600
KillSignal=SIGTERM
UMask=0027
StandardOutput=append:${LOG_DIR}/kg-card.log
StandardError=append:${LOG_DIR}/kg-card.log

[Install]
WantedBy=${TARGET}
EOF

    cat > "${unit_dir}/${SVC_KG_RELATION}.service" <<EOF
[Unit]
Description=Smart Fund KG Relation Worker
Wants=network-online.target ${SVC_API}.service ${SVC_PERSIST}.service ${SVC_MILVUS}.service redis-server.service
After=network-online.target ${SVC_API}.service ${SVC_PERSIST}.service ${SVC_MILVUS}.service redis-server.service
PartOf=${TARGET}

[Service]
Type=simple
User=${REMOTE_USER}
Group=${REMOTE_USER}
WorkingDirectory=${SERVER_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONUNBUFFERED=1
ExecStart=${PYTHON} -m src.interfaces.cli knowledge-worker --stage relation -c ${KG_RELATION_WORKER_CONCURRENCY}
Restart=always
RestartSec=5
TimeoutStopSec=600
KillSignal=SIGTERM
UMask=0027
StandardOutput=append:${LOG_DIR}/kg-relation.log
StandardError=append:${LOG_DIR}/kg-relation.log

[Install]
WantedBy=${TARGET}
EOF

    cat > "${unit_dir}/${SVC_KG_GRAPH}.service" <<EOF
[Unit]
Description=Smart Fund KG Graph Community Worker
Wants=network-online.target ${SVC_PERSIST}.service redis-server.service
After=network-online.target ${SVC_PERSIST}.service redis-server.service
PartOf=${TARGET}

[Service]
Type=simple
User=${REMOTE_USER}
Group=${REMOTE_USER}
WorkingDirectory=${SERVER_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONUNBUFFERED=1
Environment="PGOPTIONS=-c statement_timeout=300000"
ExecStart=${PYTHON} -m src.interfaces.cli knowledge-worker --stage graph -c 1
Restart=always
RestartSec=5
TimeoutStopSec=600
KillSignal=SIGTERM
MemoryMax=4G
CPUQuota=100%
UMask=0027
StandardOutput=append:${LOG_DIR}/kg-graph.log
StandardError=append:${LOG_DIR}/kg-graph.log

[Install]
WantedBy=${TARGET}
EOF

    cat > "${unit_dir}/${TARGET}" <<EOF
[Unit]
Description=Smart Fund Collection Stack
Wants=${SVC_ETCD}.service ${SVC_MILVUS}.service ${SVC_API}.service ${SVC_PERSIST}.service ${SVC_SCHEDULER}.service ${SVC_WORKER_THS}.service ${SVC_WORKER_THS_SECTOR}.service ${SVC_WORKER_GENERAL}.service ${SVC_THS_STREAM}.service ${SVC_KG_CARD}.service ${SVC_KG_RELATION}.service
After=network-online.target

[Install]
WantedBy=multi-user.target
EOF

    cat > "${unit_dir}/smart-fund-server.logrotate" <<EOF
${LOG_DIR}/*.log {
    su ${REMOTE_USER} ${REMOTE_USER}
    daily
    size 100M
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    create 0640 ${REMOTE_USER} ${REMOTE_USER}
}
EOF

    scp "${SCP_OPTS[@]}" "${unit_dir}"/*.service "${unit_dir}/${TARGET}" \
        "${unit_dir}/milvus-embed-etcd.yaml" "${unit_dir}/milvus-user.yaml" \
        "${unit_dir}/smart-fund-milvus-prestart.sh" \
        "${unit_dir}/smart-fund-milvus-wait-ready.sh" \
        "${REMOTE_USER}@${REMOTE_HOST}:/tmp/"
    scp "${SCP_OPTS[@]}" "${unit_dir}/smart-fund-server.logrotate" \
        "${REMOTE_USER}@${REMOTE_HOST}:/tmp/"
    rm -rf "${unit_dir}"

    sudo_cmd "systemctl disable --now smart-fund-server.service 2>/dev/null || true
rm -f /etc/systemd/system/smart-fund-server.service
if [[ -d '${MILVUS_DATA_DIR}/volumes/milvus/etcd' && ! -e '${MILVUS_DATA_DIR}/volumes/milvus/etcd.before-external-service' ]]; then
    cp -a '${MILVUS_DATA_DIR}/volumes/milvus/etcd' '${MILVUS_DATA_DIR}/volumes/milvus/etcd.before-external-service'
fi
install -m 644 /tmp/${SVC_ETCD}.service /etc/systemd/system/${SVC_ETCD}.service
install -m 644 /tmp/${SVC_MILVUS}.service /etc/systemd/system/${SVC_MILVUS}.service
install -m 644 /tmp/${SVC_API}.service /etc/systemd/system/${SVC_API}.service
install -m 644 /tmp/${SVC_PERSIST}.service /etc/systemd/system/${SVC_PERSIST}.service
install -m 644 /tmp/${SVC_SCHEDULER}.service /etc/systemd/system/${SVC_SCHEDULER}.service
systemctl disable --now smart-fund-worker.service 2>/dev/null || true
rm -f /etc/systemd/system/smart-fund-worker.service
install -m 644 /tmp/${SVC_WORKER_THS}.service /etc/systemd/system/${SVC_WORKER_THS}.service
install -m 644 /tmp/${SVC_WORKER_THS_SECTOR}.service /etc/systemd/system/${SVC_WORKER_THS_SECTOR}.service
install -m 644 /tmp/${SVC_WORKER_GENERAL}.service /etc/systemd/system/${SVC_WORKER_GENERAL}.service
systemctl disable --now smart-fund-worker-http.service smart-fund-worker-internal.service 2>/dev/null || true
rm -f /etc/systemd/system/smart-fund-worker-http.service /etc/systemd/system/smart-fund-worker-internal.service
install -m 644 /tmp/${SVC_THS_STREAM}.service /etc/systemd/system/${SVC_THS_STREAM}.service
install -m 644 /tmp/${SVC_KG_CARD}.service /etc/systemd/system/${SVC_KG_CARD}.service
install -m 644 /tmp/${SVC_KG_RELATION}.service /etc/systemd/system/${SVC_KG_RELATION}.service
install -m 644 /tmp/${SVC_KG_GRAPH}.service /etc/systemd/system/${SVC_KG_GRAPH}.service
install -m 644 /tmp/${TARGET} /etc/systemd/system/${TARGET}
install -m 644 /tmp/smart-fund-server.logrotate /etc/logrotate.d/smart-fund-server
install -m 644 /tmp/milvus-embed-etcd.yaml '${CONFIG_DIR}/milvus-embed-etcd.yaml'
install -m 644 /tmp/milvus-user.yaml '${CONFIG_DIR}/milvus-user.yaml'
install -m 755 /tmp/smart-fund-milvus-prestart.sh '${CONFIG_DIR}/smart-fund-milvus-prestart.sh'
install -m 755 /tmp/smart-fund-milvus-wait-ready.sh '${CONFIG_DIR}/smart-fund-milvus-wait-ready.sh'
mkdir -p '${CONFIG_DIR}/docker-no-credential'
printf '{}\n' > '${CONFIG_DIR}/docker-no-credential/config.json'
chown -R ${REMOTE_USER}:${REMOTE_USER} '${CONFIG_DIR}/docker-no-credential'
rm -f /tmp/${SVC_ETCD}.service /tmp/${SVC_MILVUS}.service /tmp/${SVC_API}.service /tmp/${SVC_PERSIST}.service \
    /tmp/${SVC_SCHEDULER}.service /tmp/${SVC_WORKER_THS}.service \
    /tmp/${SVC_WORKER_THS_SECTOR}.service /tmp/${SVC_WORKER_GENERAL}.service /tmp/${SVC_THS_STREAM}.service \
    /tmp/${SVC_KG_CARD}.service /tmp/${SVC_KG_RELATION}.service \
    /tmp/${SVC_KG_GRAPH}.service \
    /tmp/${TARGET} /tmp/smart-fund-server.logrotate \
    /tmp/milvus-embed-etcd.yaml /tmp/milvus-user.yaml \
    /tmp/smart-fund-milvus-prestart.sh /tmp/smart-fund-milvus-wait-ready.sh
touch '${LOG_DIR}/etcd.log' '${LOG_DIR}/milvus.log' '${LOG_DIR}/api.log' '${LOG_DIR}/persist.log' \
    '${LOG_DIR}/scheduler.log' '${LOG_DIR}/worker-ths.log' \
    '${LOG_DIR}/worker-ths-sector.log' '${LOG_DIR}/worker-general.log' \
    '${LOG_DIR}/ths-realtime-stream.log' \
    '${LOG_DIR}/kg-card.log' '${LOG_DIR}/kg-relation.log' \
    '${LOG_DIR}/kg-graph.log'
chown ${REMOTE_USER}:${REMOTE_USER} '${LOG_DIR}'/*.log
chmod 0640 '${LOG_DIR}'/*.log
systemctl daemon-reload
systemctl enable ${TARGET}"
    echo "systemd units 安装完成"
}

wait_for_api() {
    local attempt
    for attempt in $(seq 1 40); do
        if ssh_cmd "curl --fail --silent --max-time 3 http://127.0.0.1:8900/health >/dev/null"; then
            return 0
        fi
        sleep 2
    done
    echo "API 未在预期时间内就绪" >&2
    ssh_cmd "tail -80 '${LOG_DIR}/api.log' 2>/dev/null || true"
    return 1
}

wait_for_ths_command_broker() {
    local attempt
    for attempt in $(seq 1 45); do
        if ssh_cmd "/bin/bash -c '(exec 3<>/dev/tcp/127.0.0.1/49302) 2>/dev/null'"; then
            return 0
        fi
        sleep 1
    done
    echo "同花顺原生指令代理未在预期时间内监听 49302" >&2
    ssh_cmd "systemctl status '${SVC_THS_STREAM}.service' --no-pager -l || true"
    ssh_cmd "tail -120 '${LOG_DIR}/ths-realtime-stream.log' 2>/dev/null || true"
    return 1
}

wait_for_milvus() {
    local attempt
    for attempt in $(seq 1 150); do
        if ssh_cmd "curl --fail --silent --max-time 3 http://127.0.0.1:9091/healthz >/dev/null"; then
            return 0
        fi
        sleep 2
    done
    echo "Milvus 未在预期时间内就绪" >&2
    ssh_cmd "tail -120 '${LOG_DIR}/milvus.log' 2>/dev/null || true"
    return 1
}

register_schedules() {
    ssh_cmd "cd '${SERVER_DIR}' && set -a && . '${ENV_FILE}' && set +a && \
        '${PYTHON}' -m src.interfaces.cli init schedules"
}

initialize_runtime() {
    echo "按顺序初始化运行时..."
    sudo_cmd "systemctl start ${SVC_MILVUS}.service"
    wait_for_milvus
    sudo_cmd "systemctl start ${SVC_API}.service"
    wait_for_api
    sudo_cmd "systemctl start ${SVC_PERSIST}.service"
    sleep 3

    ssh_cmd "cd '${SERVER_DIR}' && set -a && . '${ENV_FILE}' && set +a && \
        '${PYTHON}' -m src.interfaces.cli init state"
    register_schedules
    ssh_cmd "cd '${SERVER_DIR}' && set -a && . '${ENV_FILE}' && set +a && \
        '${PYTHON}' - <<'PY'
from src.interfaces.tasks import app

deleted = app.schedule_delete(['kg_community_insight_refresh_1min'])
print(f'已移除旧 Community Insight 调度: {deleted}')
app.close()
PY"

    sudo_cmd "systemctl start ${SVC_SCHEDULER}.service"
    sudo_cmd "systemctl start ${SVC_WORKER_THS}.service ${SVC_WORKER_THS_SECTOR}.service ${SVC_WORKER_GENERAL}.service"
    sudo_cmd "systemctl start ${SVC_THS_STREAM}.service"
    wait_for_ths_command_broker
    sudo_cmd "systemctl start ${SVC_KG_CARD}.service"
    sudo_cmd "systemctl start ${SVC_KG_RELATION}.service"
    sudo_cmd "systemctl start ${TARGET}"
    echo "核心服务已启动；KG Graph Worker 保持按需手动启动"
}

restart_all() {
    echo "重启应用服务..."
    if ! ssh_cmd "systemctl is-active --quiet ${SVC_MILVUS}.service"; then
        echo "Milvus 未运行，先启动 Milvus..."
        sudo_cmd "systemctl start ${SVC_MILVUS}.service"
    fi
    wait_for_milvus
    sudo_cmd "systemctl restart ${SVC_API}.service"
    wait_for_api
    sudo_cmd "systemctl stop ${SVC_SCHEDULER}.service"
    sudo_cmd "systemctl restart ${SVC_PERSIST}.service"
    sleep 2
    register_schedules
    sudo_cmd "systemctl start ${SVC_SCHEDULER}.service"
    sudo_cmd "systemctl restart ${SVC_WORKER_THS}.service ${SVC_WORKER_THS_SECTOR}.service ${SVC_WORKER_GENERAL}.service"
    sudo_cmd "systemctl restart ${SVC_THS_STREAM}.service"
    wait_for_ths_command_broker
    sudo_cmd "systemctl restart ${SVC_KG_CARD}.service"
    sudo_cmd "systemctl restart ${SVC_KG_RELATION}.service"
    # KG Graph Worker 资源消耗较高，不随常规部署自动启动或重启。
}

compose_cmd() {
    local args="$1"
    ssh_cmd "docker compose --project-name '${COMPOSE_PROJECT}' --env-file '${COMPOSE_ENV_FILE}' -f '${REMOTE_COMPOSE_FILE}' ${args}"
}

build_server_image() {
    local image="smart-fund-server:${DEPLOY_REVISION}"
    echo "构建服务端镜像 ${image}..."
    ssh_cmd "set -euo pipefail
test -s '${ARTIFACT_DIR}/jettask_python-0.1.0-py3-none-any.whl'
mkdir -p '${SERVER_DIR}/.docker-build'
cp '${ARTIFACT_DIR}/jettask_python-0.1.0-py3-none-any.whl' '${SERVER_DIR}/.docker-build/jettask.whl'
if ! docker image inspect '${image}' >/dev/null 2>&1; then
  if [[ -e /home/${REMOTE_USER}/.docker/cli-plugins/docker-buildx ]] \
      && [[ ! -s /home/${REMOTE_USER}/.docker/cli-plugins/docker-buildx ]]; then
    rm -f /home/${REMOTE_USER}/.docker/cli-plugins/docker-buildx
  fi
  docker build --pull --build-arg APP_UID=\$(id -u) --build-arg APP_GID=\$(id -g) -f '${SERVER_DIR}/deployment/docker/Dockerfile' -t '${image}' '${SERVER_DIR}'
fi
rm -rf '${SERVER_DIR}/.docker-build'"
    ssh_cmd "install -d -m 0700 '${CONFIG_DIR}/claude-container'; rsync -a --delete '/home/${REMOTE_USER}/.claude/' '${CONFIG_DIR}/claude-container/'; chmod 0700 '${CONFIG_DIR}/claude-container'"
    ssh_cmd "cat > '${COMPOSE_ENV_FILE}' <<'EOF'
SMART_FUND_IMAGE=${image}
SMART_FUND_ENV_FILE=${ENV_FILE}
SMART_FUND_ARTIFACT_DIR=${ARTIFACT_DIR}
SMART_FUND_DATA_DIR=${DATA_DIR}
SMART_FUND_SKILLS_DIR=${REMOTE_SKILLS_DIR}
SMART_FUND_CONFIG_DIR=${CONFIG_DIR}
MILVUS_DATA_DIR=${MILVUS_DATA_DIR}
CAMOUFOX_CACHE_DIR=${REMOTE_CAMOUFOX_CACHE}
CLAUDE_BIN_PATH=/home/${REMOTE_USER}/.local/bin/claude
CLAUDE_CONFIG_DIR=${CONFIG_DIR}/claude-container
MILVUS_IMAGE=${MILVUS_IMAGE}
ETCD_IMAGE=${ETCD_IMAGE}
THS_WORKER_CONCURRENCY=${THS_WORKER_CONCURRENCY}
THS_SECTOR_WORKER_CONCURRENCY=${THS_SECTOR_WORKER_CONCURRENCY}
GENERAL_WORKER_CONCURRENCY=${GENERAL_WORKER_CONCURRENCY}
KG_RELATION_WORKER_CONCURRENCY=${KG_RELATION_WORKER_CONCURRENCY}
EOF
chmod 0600 '${COMPOSE_ENV_FILE}'"
    compose_cmd "config --quiet"
}

install_compose_service() {
    sudo_cmd "cat > /etc/systemd/system/smart-fund-compose.service <<'EOF'
[Unit]
Description=Smart Fund Docker Compose stack
Wants=network-online.target docker.service redis-server.service
After=network-online.target docker.service redis-server.service

[Service]
Type=oneshot
RemainAfterExit=yes
User=${REMOTE_USER}
Group=${REMOTE_USER}
WorkingDirectory=${SERVER_DIR}
ExecStart=/usr/bin/docker compose --project-name ${COMPOSE_PROJECT} --env-file ${COMPOSE_ENV_FILE} -f ${REMOTE_COMPOSE_FILE} up -d
ExecStop=/usr/bin/docker compose --project-name ${COMPOSE_PROJECT} --env-file ${COMPOSE_ENV_FILE} -f ${REMOTE_COMPOSE_FILE} stop
TimeoutStartSec=600
TimeoutStopSec=180

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload"
}

rollback_compose_migration() {
    echo "Compose 首次迁移失败，恢复旧 systemd 栈..." >&2
    compose_cmd "down --remove-orphans" || true
    sudo_cmd "if systemctl cat ${TARGET} >/dev/null 2>&1; then systemctl enable --now ${TARGET}; fi"
}

migrate_systemd_to_compose() {
    if ssh_cmd "test -f '${COMPOSE_MIGRATION_MARKER}'"; then
        return
    fi
    echo "首次切换到 Docker Compose；停止旧 systemd 进程但保留全部数据目录..."
    sudo_cmd "systemctl disable --now ${TARGET} ${SVC_API}.service ${SVC_PERSIST}.service ${SVC_SCHEDULER}.service ${SVC_WORKER_THS}.service ${SVC_WORKER_THS_SECTOR}.service ${SVC_WORKER_GENERAL}.service smart-fund-worker-http.service smart-fund-worker-internal.service ${SVC_THS_STREAM}.service ${SVC_KG_CARD}.service ${SVC_KG_RELATION}.service ${SVC_KG_GRAPH}.service ${SVC_MILVUS}.service ${SVC_ETCD}.service 2>/dev/null || true"
    if ! compose_cmd "up -d etcd milvus" || ! wait_for_milvus; then
        rollback_compose_migration
        return 1
    fi
    if ! compose_cmd "up -d api persist scheduler worker-ths worker-ths-sector worker-general ths-realtime-stream kg-card kg-relation" \
        || ! wait_for_api || ! wait_for_ths_command_broker; then
        rollback_compose_migration
        return 1
    fi
    ssh_cmd "touch '${COMPOSE_MIGRATION_MARKER}'"
    sudo_cmd "systemctl enable smart-fund-compose.service"
}

deploy_compose_services() {
    local requested=",${1}," services=()
    [[ "${requested}" == *,api,* ]] && services+=(api)
    [[ "${requested}" == *,persist,* ]] && services+=(persist)
    [[ "${requested}" == *,scheduler,* ]] && services+=(scheduler)
    [[ "${requested}" == *,workers,* ]] && services+=(worker-ths worker-ths-sector worker-general)
    [[ "${requested}" == *,ths-stream,* ]] && services+=(ths-realtime-stream)
    [[ "${requested}" == *,kg,* ]] && services+=(kg-card kg-relation)
    ((${#services[@]} > 0)) || return 0
    if [[ "${requested}" == *,scheduler,* ]]; then
        compose_cmd "run --rm --no-deps api init schedules"
    fi
    compose_cmd "up -d --no-deps ${services[*]}"
    [[ "${requested}" == *,api,* ]] && wait_for_api
    [[ "${requested}" == *,ths-stream,* ]] && wait_for_ths_command_broker
    local service running
    running="$(compose_cmd "ps --status running --services")"
    for service in "${services[@]}"; do
        grep -qx "${service}" <<<"${running}"
    done
}

deploy_components() {
    local requested=",${1},"
    if ! production_is_initialized; then
        echo "检测到服务端尚未初始化，自动执行首次部署"
        init_deploy
        return
    fi
    sync_code
    apply_schema_migrations
    build_server_image
    install_compose_service
    migrate_systemd_to_compose
    sudo_cmd "systemctl enable smart-fund-compose.service"
    deploy_compose_services "${1}"
    cleanup_legacy_server_dir
    echo "服务端组件部署完成: ${1}"
}

show_status() {
    if ssh_cmd "test -f '${COMPOSE_MIGRATION_MARKER}'"; then
        compose_cmd "ps"
        return
    fi
    local service
    for service in "${REQUIRED_SERVICES[@]}"; do
        echo "===== ${service} ====="
        ssh_cmd "systemctl status '${service}.service' --no-pager | sed -n '1,14p'" || true
    done
}

show_logs() {
    local service="${1:-worker-ths}"
    local lines="${2:-100}"
    case "${service}" in
        milvus|api|persist|scheduler|worker-ths|worker-ths-sector|worker-general|ths-realtime-stream|kg-card|kg-relation|kg-graph) ;;
        *)
            echo "日志服务必须是 milvus|api|persist|scheduler|worker-ths|worker-ths-sector|worker-general|ths-realtime-stream|kg-card|kg-relation|kg-graph" >&2
            exit 1
            ;;
    esac
    if ssh_cmd "test -f '${COMPOSE_MIGRATION_MARKER}'"; then
        compose_cmd "logs --tail '${lines}' '${service}"
    else
        ssh_cmd "tail -n '${lines}' '${LOG_DIR}/${service}.log'"
    fi
}

remote_test() {
    echo "执行生产健康检查..."
    local service running
    if ssh_cmd "test -f '${COMPOSE_MIGRATION_MARKER}'"; then
        running="$(compose_cmd "ps --status running --services")"
        for service in etcd milvus api persist scheduler worker-ths worker-ths-sector worker-general ths-realtime-stream kg-card kg-relation; do
            grep -qx "${service}" <<<"${running}"
            echo "${service}: running"
        done
    else
        for service in "${REQUIRED_SERVICES[@]}"; do
            ssh_cmd "systemctl is-active --quiet '${service}.service'"
            echo "${service}: active"
        done
    fi

    ssh_cmd "curl --fail --silent http://127.0.0.1:8900/health"
    echo
    ssh_cmd "curl --fail --silent --max-time 60 -X POST \
        'http://127.0.0.1:8900/api/spy/start?headless=true' \
        >/tmp/smart-fund-spy-start.json"
    ssh_cmd "curl --fail --silent http://127.0.0.1:8900/api/spy/status >/tmp/smart-fund-spy-health.json"
    ssh_cmd "'${PYTHON}' -c \
        'import json; d=json.load(open(\"/tmp/smart-fund-spy-health.json\")); assert d.get(\"available\") is True and d.get(\"started\") is True; print(\"browser spy: ok\")'"
    ssh_cmd "REDISCLI_AUTH=\"\$(cat '${CONFIG_DIR}/redis-access.secret' 2>/dev/null || true)\" \
        redis-cli -h 127.0.0.1 ping | grep -q PONG"
    echo "redis: ok"
    ssh_cmd "pg_isready -h 10.168.1.113 -p 5432"
    ssh_cmd "curl --fail --silent --max-time 5 http://127.0.0.1:9091/healthz >/dev/null"
    echo "milvus: ok"

    ssh_cmd "curl --fail --silent --max-time 30 \
        http://10.168.1.113:8901/v1/embeddings \
        -H 'Content-Type: application/json' \
        -d '{\"model\":\"/models/Qwen3-Embedding-4B\",\"input\":[\"health check\"]}' \
        >/tmp/smart-fund-embedding-health.json"
    ssh_cmd "'${PYTHON}' -c \
        'import json; d=json.load(open(\"/tmp/smart-fund-embedding-health.json\")); assert d[\"data\"][0][\"embedding\"]; print(\"embedding: ok\")'"

    ssh_cmd "curl --fail --silent --max-time 30 \
        http://10.168.1.155:8860/v1/rerank \
        -H 'Content-Type: application/json' \
        -d '{\"query\":\"半导体\",\"documents\":[\"半导体设备需求增长\",\"今日天气晴朗\"],\"top_n\":1}' \
        >/tmp/smart-fund-reranker-health.json"
    ssh_cmd "'${PYTHON}' -c \
        'import json; d=json.load(open(\"/tmp/smart-fund-reranker-health.json\")); assert d[\"results\"]; print(\"reranker: ok\")'"

    ssh_cmd "cd '${SERVER_DIR}' && set -a && . '${ENV_FILE}' && set +a && '${PYTHON}' - <<'PY'
from src.infrastructure.persistence.repositories.collection_state_repository_impl import CollectionStateRepositoryImpl

rows = CollectionStateRepositoryImpl().list_all()
assert rows, 'ft_collection_state is empty'
latest = max((row for row in rows if row.get('last_success_at')), key=lambda row: row['last_success_at'])
print(
    'collection_state: ok',
    f\"count={len(rows)}\",
    f\"latest={latest['aggregator']}:{latest['source_name']}\",
    f\"last_success_at={latest['last_success_at']}\",
)
PY"
    echo "生产健康检查通过"
}

cleanup_legacy_server_dir() {
    if [[ "${LEGACY_SERVER_DIR}" == "${SERVER_DIR}" ]]; then
        echo "拒绝清理：新旧服务目录相同" >&2
        return 1
    fi
    ssh_cmd "if [[ -d '${LEGACY_SERVER_DIR}' ]]; then rm -rf '${LEGACY_SERVER_DIR}'; fi"
    echo "旧服务目录已清理: ${LEGACY_SERVER_DIR}"
}

langfuse_compose() {
    local arguments="$*"
    ssh_cmd "DOCKER_CONFIG='${CONFIG_DIR}/docker-no-credential' COMPOSE_PARALLEL_LIMIT=2 \
        docker compose --project-name '${LANGFUSE_COMPOSE_PROJECT}' \
        --env-file '${LANGFUSE_ENV_FILE}' \
        --file '${REMOTE_LANGFUSE_DEPLOY_DIR}/docker-compose.yml' \
        ${arguments}"
}

bootstrap_langfuse_env() {
    local quoted_bind_address quoted_email quoted_media_url quoted_public_url
    printf -v quoted_email '%q' "${LANGFUSE_ADMIN_EMAIL}"
    printf -v quoted_bind_address '%q' "${LANGFUSE_BIND_ADDRESS}"
    printf -v quoted_public_url '%q' "${LANGFUSE_PUBLIC_URL}"
    printf -v quoted_media_url '%q' "${LANGFUSE_MEDIA_EXTERNAL_URL}"
    ssh_cmd "bash '${REMOTE_LANGFUSE_DEPLOY_DIR}/bootstrap_env.sh' \
        '${LANGFUSE_ENV_FILE}' \
        '${LANGFUSE_WEB_PORT}' \
        '${LANGFUSE_WORKER_PORT}' \
        '${LANGFUSE_MINIO_PORT}' \
        '${LANGFUSE_RETENTION_DAYS}' \
        ${quoted_email} \
        ${quoted_bind_address} \
        ${quoted_public_url} \
        ${quoted_media_url}"
}

configure_langfuse_client_env() {
    ssh_cmd "'${PYTHON}' '${REMOTE_LANGFUSE_DEPLOY_DIR}/configure_client_env.py' \
        --langfuse-env '${LANGFUSE_ENV_FILE}' \
        --client-env '${ENV_FILE}' \
        --base-url 'http://127.0.0.1:${LANGFUSE_WEB_PORT}'"
    echo "Smart Fund 已切换到自建 Langfuse"
}

wait_for_langfuse() {
    local attempt
    for attempt in $(seq 1 120); do
        if ssh_cmd "curl --fail --silent --max-time 3 \
            'http://127.0.0.1:${LANGFUSE_WEB_PORT}/api/public/health?failIfDatabaseUnavailable=true' \
            >/dev/null" \
            && ssh_cmd "curl --fail --silent --max-time 3 \
            'http://127.0.0.1:${LANGFUSE_WORKER_PORT}/api/health' >/dev/null"; then
            return 0
        fi
        sleep 2
    done
    echo "Langfuse 未在预期时间内就绪" >&2
    langfuse_compose "ps" || true
    langfuse_compose "logs --tail 120 langfuse-web langfuse-worker" || true
    return 1
}

install_langfuse_frp() {
    echo "安装 Langfuse 公网 FRP 映射..."
    ssh_cmd "install -m 600 \
        '${REMOTE_LANGFUSE_DEPLOY_DIR}/frpc_langfuse.toml' \
        '${REMOTE_FRP_DIR}/frpc_langfuse.toml'"
    scp "${SCP_OPTS[@]}" \
        "${LOCAL_LANGFUSE_DEPLOY_DIR}/frpc_langfuse.conf" \
        "${REMOTE_USER}@${REMOTE_HOST}:/tmp/frpc_langfuse.conf"
    sudo_cmd "install -m 644 /tmp/frpc_langfuse.conf \
        /etc/supervisor/conf.d/frpc_langfuse.conf && \
        rm -f /tmp/frpc_langfuse.conf && \
        supervisorctl reread && \
        supervisorctl update && \
        supervisorctl restart frpc_langfuse"
    echo "Langfuse 公网 FRP 映射已启动"
}

restart_langfuse_clients() {
    echo "重启读取 Langfuse 配置的 Smart Fund 服务..."
    sudo_cmd "systemctl try-restart \
        ${SVC_API}.service \
        ${SVC_PERSIST}.service \
        ${SVC_SCHEDULER}.service \
        ${SVC_WORKER_THS}.service \
        ${SVC_WORKER_THS_SECTOR}.service \
        ${SVC_WORKER_GENERAL}.service \
        ${SVC_THS_STREAM}.service \
        ${SVC_KG_CARD}.service \
        ${SVC_KG_RELATION}.service \
        ${SVC_KG_GRAPH}.service"
    wait_for_api
}

test_langfuse() {
    echo "执行 Langfuse 服务、认证与真实 OTLP 写入检查..."
    wait_for_langfuse
    ssh_cmd "cd '${SERVER_DIR}' && set -a && . '${ENV_FILE}' && set +a && \
        '${PYTHON}' - <<'PY'
import asyncio
import json

from src.infrastructure.agent_runtime.config import AgentSettings
from src.infrastructure.agent_runtime.langfuse_health import check_langfuse_health

result = asyncio.run(check_langfuse_health(AgentSettings.from_env()))
print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
if not result.writable:
    raise SystemExit(f'Langfuse is not writable: {result.status}')
PY"
    echo "Langfuse 健康检查通过"
}

deploy_langfuse() {
    sync_langfuse_files
    bootstrap_langfuse_env
    langfuse_compose "config --quiet"
    echo "拉取并启动 Langfuse v4..."
    langfuse_compose "up -d --pull missing --remove-orphans"
    wait_for_langfuse
    install_langfuse_frp
    configure_langfuse_client_env
    restart_langfuse_clients
    test_langfuse
}

upgrade_langfuse() {
    sync_langfuse_files
    bootstrap_langfuse_env
    langfuse_compose "config --quiet"
    echo "拉取 Langfuse v4 最新镜像..."
    langfuse_compose "pull"
    langfuse_compose "up -d --pull never --remove-orphans"
    wait_for_langfuse
    install_langfuse_frp
    configure_langfuse_client_env
    restart_langfuse_clients
    test_langfuse
}

show_langfuse_status() {
    langfuse_compose "ps"
    ssh_cmd "curl --silent --show-error --max-time 5 \
        'http://127.0.0.1:${LANGFUSE_WEB_PORT}/api/public/health?failIfDatabaseUnavailable=true'"
    echo
    ssh_cmd "curl --silent --show-error --max-time 5 \
        'http://127.0.0.1:${LANGFUSE_WORKER_PORT}/api/health'"
    echo
}

show_langfuse_logs() {
    local service="${1:-web}"
    local lines="${2:-100}"
    case "${service}" in
        web) service="langfuse-web" ;;
        worker) service="langfuse-worker" ;;
        postgres|redis|clickhouse|minio) ;;
        *)
            echo "Langfuse 日志服务必须是 web|worker|postgres|redis|clickhouse|minio" >&2
            exit 1
            ;;
    esac
    langfuse_compose "logs --tail '${lines}' '${service}'"
}

show_langfuse_credentials() {
    ssh_cmd "awk -F= '/^LANGFUSE_INIT_USER_(EMAIL|PASSWORD)=/ {print}' '${LANGFUSE_ENV_FILE}'"
}

init_deploy() {
    sync_code
    install_production_config
    install_redis
    ensure_docker_compose
    if ! ssh_cmd "test -x '${REMOTE_CAMOUFOX_CACHE}/camoufox-bin'"; then
        sync_camoufox_cache
    fi
    apply_schema_migrations
    build_server_image
    install_compose_service
    migrate_systemd_to_compose
    compose_cmd "run --rm --no-deps api init state"
    compose_cmd "run --rm --no-deps api init schedules"
    compose_cmd "restart scheduler"
    remote_test
    cleanup_legacy_server_dir
}

production_is_initialized() {
    ssh_cmd "test -f '${ENV_FILE}' && { test -f '${COMPOSE_MIGRATION_MARKER}' || { test -x '${PYTHON}' && systemctl cat '${SVC_API}.service' >/dev/null 2>&1; }; }"
}

main() {
    setup_ssh_key
    case "${1:-}" in
        --init)
            init_deploy
            ;;
        --sync-only)
            sync_code
            ;;
        --restart)
            restart_all
            remote_test
            ;;
        --components)
            [[ -n "${2:-}" ]] || { echo "--components 需要组件列表" >&2; exit 2; }
            IFS=',' read -r -a requested_components <<<"${2}"
            for component in "${requested_components[@]}"; do
                case "${component}" in api|persist|scheduler|workers|ths-stream|kg) ;;
                    *) echo "未知服务端组件: ${component}" >&2; exit 2 ;;
                esac
            done
            deploy_components "${2}"
            ;;
        --status)
            show_status
            ;;
        --logs)
            show_logs "${2:-worker-ths}" "${3:-100}"
            ;;
        --test)
            remote_test
            ;;
        --config)
            ensure_remote_dirs
            install_production_config
            ;;
        --deps)
            ensure_remote_dirs
            install_dependencies
            ;;
        --units)
            ensure_remote_dirs
            install_units
            ;;
        --migrate)
            apply_schema_migrations
            ;;
        --langfuse)
            deploy_langfuse
            ;;
        --langfuse-upgrade)
            upgrade_langfuse
            ;;
        --langfuse-status)
            show_langfuse_status
            ;;
        --langfuse-test)
            test_langfuse
            ;;
        --langfuse-logs)
            show_langfuse_logs "${2:-web}" "${3:-100}"
            ;;
        --langfuse-credentials)
            show_langfuse_credentials
            ;;
        "")
            if production_is_initialized; then
                sync_code
                install_units
                apply_schema_migrations
                restart_all
                remote_test
                cleanup_legacy_server_dir
            else
                echo "检测到服务端尚未初始化，自动执行首次部署"
                init_deploy
            fi
            ;;
        *)
            echo "未知参数: $1" >&2
            exit 1
            ;;
    esac
}

main "$@"
