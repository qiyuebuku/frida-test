#!/usr/bin/env bash
# smart-fund-server 生产部署脚本
#
# 用法:
#   REMOTE_SUDO_PASSWORD=... ./deploy_113.sh --init
#   REMOTE_SUDO_PASSWORD=... ./deploy_113.sh  # 同步代码、更新 units 并重启全部服务
#   ./deploy_113.sh --sync-only
#   REMOTE_SUDO_PASSWORD=... ./deploy_113.sh --restart
#   ./deploy_113.sh --status
#   ./deploy_113.sh --logs worker 100
#   ./deploy_113.sh --test
#   ./deploy_113.sh --config        # 从本地 .env 重建生产 EnvironmentFile
#   ./deploy_113.sh --deps          # 更新生产 Python 依赖
#   REMOTE_SUDO_PASSWORD=... ./deploy_113.sh --migrate

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCAL_DEPLOY_ENV="${LOCAL_DEPLOY_ENV:-${SCRIPT_DIR}/.deployment.local.env}"
if [[ -f "${LOCAL_DEPLOY_ENV}" ]]; then
    # shellcheck disable=SC1090
    source "${LOCAL_DEPLOY_ENV}"
fi

REMOTE_HOST="${REMOTE_HOST:-119.23.227.187}"
REMOTE_PORT="${REMOTE_PORT:-1113}"
REMOTE_USER="${REMOTE_USER:-yuyangruan}"
REMOTE_SUDO_PASSWORD="${REMOTE_SUDO_PASSWORD:-}"

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
LOCAL_CAMOUFOX_CACHE="${LOCAL_CAMOUFOX_CACHE:-/home/yuyang/.cache/camoufox}"
REMOTE_CAMOUFOX_CACHE="/home/${REMOTE_USER}/.cache/camoufox"

SVC_MILVUS="smart-fund-milvus"
SVC_API="smart-fund-api"
SVC_PERSIST="smart-fund-persist"
SVC_SCHEDULER="smart-fund-scheduler"
SVC_WORKER="smart-fund-worker"
SVC_KG_CARD="smart-fund-kg-card"
SVC_KG_RELATION="smart-fund-kg-relation"
SVC_KG_GRAPH="smart-fund-kg-graph"
TARGET="smart-fund-collector.target"
SERVICES=(
    "${SVC_MILVUS}"
    "${SVC_API}"
    "${SVC_PERSIST}"
    "${SVC_SCHEDULER}"
    "${SVC_WORKER}"
    "${SVC_KG_CARD}"
    "${SVC_KG_RELATION}"
    "${SVC_KG_GRAPH}"
)

LOCAL_SERVER_DIR="${SCRIPT_DIR}"
LOCAL_WORKSPACE_ROOT="$(cd "${LOCAL_SERVER_DIR}/.." && pwd)"
LOCAL_SKILLS_DIR="${LOCAL_WORKSPACE_ROOT}/.claude/skills"
LOCAL_FUND_TRADE_DIR="${LOCAL_SKILLS_DIR}/fund-trade"
LOCAL_ENV_FILE="${LOCAL_SERVER_DIR}/.env"
LOCAL_AICLIENT2API_ENV="${LOCAL_AICLIENT2API_ENV:-/home/yuyang/frida-test/AIClient2API/.deployment.local.env}"
JETTASK_WHEEL="/home/yuyang/easy-task/backend/jettask-rs/bindings/python/dist/jettask_python-0.1.0-py3-none-any.whl"

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
    --exclude=docs/.backup/
    --exclude='docs/6. 使用说明/知识图谱/data/'
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
    echo "同步 smart-fund-server..."
    ensure_remote_dirs
    rsync -az --delete "${RSYNC_EXCLUDES[@]}" \
        -e "ssh ${SSH_OPTS[*]}" \
        "${LOCAL_SERVER_DIR}/" \
        "${REMOTE_USER}@${REMOTE_HOST}:${SERVER_DIR}/"

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
    echo "代码同步完成"
}

set_env_value() {
    local file="$1"
    local key="$2"
    local value="$3"
    python - "${file}" "${key}" "${value}" <<'PY'
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
    python - "${file}" "${key}" <<'PY'
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
    python - "${source_file}" "${target_file}" "${source_key}" "${target_key}" <<'PY'
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
    local plain_env systemd_env
    plain_env="$(mktemp)"
    systemd_env="$(mktemp)"
    cp "${LOCAL_ENV_FILE}" "${plain_env}"

    set_env_value "${plain_env}" "DB_HOST" "10.168.1.113"
    set_env_value "${plain_env}" "DB_PORT" "5432"
    remove_env_value "${plain_env}" "PG_URL"
    set_env_value "${plain_env}" "REDIS_URL" "redis://127.0.0.1:6379/0"
    set_env_value "${plain_env}" "JETTASK_PREFIX" "fund_aggregator_prod"
    set_env_value "${plain_env}" "SERVER_HOST" "0.0.0.0"
    set_env_value "${plain_env}" "SERVER_PORT" "8900"
    set_env_value "${plain_env}" "SERVICE_BASE_URL" "http://127.0.0.1:8900"
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

    python - "${plain_env}" "${systemd_env}" <<'PY'
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
    rm -f "${plain_env}" "${systemd_env}"
    echo "生产配置已安装到 ${ENV_FILE}"
}

apply_schema_migrations() {
    echo "执行幂等数据库迁移..."
    local database_name
    local remote_migration="/tmp/20260729_watchlist_tracking.sql"
    database_name="$(
        ssh_cmd "cd '${SERVER_DIR}' && set -a && . '${ENV_FILE}' && set +a && \
            '${PYTHON}' -c 'from src.infrastructure.config.settings import DB_CONFIG; print(DB_CONFIG[\"dbname\"])'"
    )"
    ssh_cmd "install -m 644 \
        '${SERVER_DIR}/schema/migrations/20260729_watchlist_tracking.sql' \
        '${remote_migration}'"
    sudo_cmd "sudo -u postgres psql -v ON_ERROR_STOP=1 -d '${database_name}' \
        -f '${remote_migration}' && rm -f '${remote_migration}'"
    echo "数据库迁移完成"
}

install_redis() {
    echo "检查系统依赖与 Redis..."
    if ! ssh_cmd "command -v redis-server >/dev/null 2>&1 \
        && command -v tmux >/dev/null 2>&1 \
        && command -v curl >/dev/null 2>&1"; then
        sudo_cmd "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y redis-server tmux curl"
    fi
    sudo_cmd "systemctl enable --now redis-server"
    ssh_cmd "redis-cli -h 127.0.0.1 ping | grep -q PONG"
    echo "系统依赖与 Redis 可用"
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
    ssh_cmd "'${PYTHON}' -m pip install '${ARTIFACT_DIR}/jettask_python-0.1.0-py3-none-any.whl'"
    ssh_cmd "'${PYTHON}' -m pip install \
        'fastapi>=0.100' 'uvicorn[standard]' psycopg2-binary 'sqlalchemy>=2.0' \
        asyncpg httpx redis pydantic pydantic-settings click prometheus-client \
        'pymilvus[milvus_lite]>=2.6,<3' 'milvus-lite>=2.5,<3' \
        'langfuse>=3.0' 'mcp>=1.27,<2' \
        'networkx>=3.0' 'graspologic-native>=1.2,<2' 'setuptools<81' \
        akshare curl_cffi PyYAML 'camoufox==0.4.11' html2text beautifulsoup4 lxml"
    if ! camoufox_binary_ready; then
        ssh_cmd "'${PYTHON}' -m camoufox fetch" || true
    fi
    if ! camoufox_binary_ready; then
        sync_camoufox_cache
    fi
    camoufox_binary_ready

    ssh_cmd "cd '${SERVER_DIR}' && '${PYTHON}' -c \
        'import fastapi, jettask, redis, sqlalchemy, pymilvus, akshare, camoufox, mcp; print(\"imports ok\")'"
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
auto-compaction-mode: revision
auto-compaction-retention: "1000"
EOF

    cat > "${unit_dir}/milvus-user.yaml" <<EOF
# Production overrides for Smart Fund Milvus Standalone.
EOF

    cat > "${unit_dir}/smart-fund-milvus-prestart.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

container_name="${1:?container name is required}"
image="${2:?Milvus image is required}"

# `docker rm -f` can return before a detached Milvus process releases the
# embedded-etcd file lock. Remove the named container first, then terminate
# only stale Milvus processes that remain before the new container starts.
/usr/bin/docker rm -f "${container_name}" >/dev/null 2>&1 || true

/usr/bin/docker run --rm --pid=host --privileged \
    --entrypoint /bin/sh "${image}" -c '
        list_milvus_pids() {
            for proc in /proc/[0-9]*; do
                [ -r "${proc}/comm" ] || continue
                [ "$(cat "${proc}/comm")" = "milvus" ] || continue
                printf "%s\n" "${proc#/proc/}"
            done
        }

        pids="$(list_milvus_pids)"
        [ -z "${pids}" ] && exit 0
        kill -TERM ${pids} 2>/dev/null || true
        for _ in $(seq 1 30); do
            [ -z "$(list_milvus_pids)" ] && exit 0
            sleep 1
        done

        pids="$(list_milvus_pids)"
        [ -z "${pids}" ] && exit 0
        kill -KILL ${pids} 2>/dev/null || true
        sleep 2
        [ -z "$(list_milvus_pids)" ]
    '
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
Wants=network-online.target docker.service
After=network-online.target docker.service
PartOf=${TARGET}

[Service]
Type=simple
User=${REMOTE_USER}
Group=${REMOTE_USER}
Environment=DOCKER_CONFIG=${CONFIG_DIR}/docker-no-credential
ExecStartPre=${CONFIG_DIR}/smart-fund-milvus-prestart.sh ${SVC_MILVUS} ${MILVUS_IMAGE}
ExecStart=/usr/bin/docker run --rm --name ${SVC_MILVUS} --security-opt seccomp:unconfined -e ETCD_USE_EMBED=true -e ETCD_DATA_DIR=/var/lib/milvus/etcd -e ETCD_CONFIG_PATH=/milvus/configs/embedEtcd.yaml -e COMMON_STORAGETYPE=local -e DEPLOY_MODE=STANDALONE -v ${MILVUS_DATA_DIR}/volumes/milvus:/var/lib/milvus -v ${CONFIG_DIR}/milvus-embed-etcd.yaml:/milvus/configs/embedEtcd.yaml:ro -v ${CONFIG_DIR}/milvus-user.yaml:/milvus/configs/user.yaml:ro -p 19530:19530 -p 127.0.0.1:9091:9091 ${MILVUS_IMAGE} milvus run standalone
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

    cat > "${unit_dir}/${SVC_WORKER}.service" <<EOF
[Unit]
Description=Smart Fund Collection Worker
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
ExecStart=${PYTHON} -m src.interfaces.cli worker -c 1
Restart=always
RestartSec=5
TimeoutStopSec=60
KillSignal=SIGTERM
UMask=0027
StandardOutput=append:${LOG_DIR}/worker.log
StandardError=append:${LOG_DIR}/worker.log

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
ExecStart=${PYTHON} -m src.interfaces.cli knowledge-worker --stage relation -c 1
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
ExecStart=${PYTHON} -m src.interfaces.cli knowledge-worker --stage graph -c 1
Restart=always
RestartSec=5
TimeoutStopSec=600
KillSignal=SIGTERM
UMask=0027
StandardOutput=append:${LOG_DIR}/kg-graph.log
StandardError=append:${LOG_DIR}/kg-graph.log

[Install]
WantedBy=${TARGET}
EOF

    cat > "${unit_dir}/${TARGET}" <<EOF
[Unit]
Description=Smart Fund Collection Stack
Wants=${SVC_MILVUS}.service ${SVC_API}.service ${SVC_PERSIST}.service ${SVC_SCHEDULER}.service ${SVC_WORKER}.service ${SVC_KG_CARD}.service ${SVC_KG_RELATION}.service ${SVC_KG_GRAPH}.service
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
install -m 644 /tmp/${SVC_MILVUS}.service /etc/systemd/system/${SVC_MILVUS}.service
install -m 644 /tmp/${SVC_API}.service /etc/systemd/system/${SVC_API}.service
install -m 644 /tmp/${SVC_PERSIST}.service /etc/systemd/system/${SVC_PERSIST}.service
install -m 644 /tmp/${SVC_SCHEDULER}.service /etc/systemd/system/${SVC_SCHEDULER}.service
install -m 644 /tmp/${SVC_WORKER}.service /etc/systemd/system/${SVC_WORKER}.service
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
rm -f /tmp/${SVC_MILVUS}.service /tmp/${SVC_API}.service /tmp/${SVC_PERSIST}.service \
    /tmp/${SVC_SCHEDULER}.service /tmp/${SVC_WORKER}.service \
    /tmp/${SVC_KG_CARD}.service /tmp/${SVC_KG_RELATION}.service \
    /tmp/${SVC_KG_GRAPH}.service \
    /tmp/${TARGET} /tmp/smart-fund-server.logrotate \
    /tmp/milvus-embed-etcd.yaml /tmp/milvus-user.yaml \
    /tmp/smart-fund-milvus-prestart.sh /tmp/smart-fund-milvus-wait-ready.sh
touch '${LOG_DIR}/milvus.log' '${LOG_DIR}/api.log' '${LOG_DIR}/persist.log' \
    '${LOG_DIR}/scheduler.log' '${LOG_DIR}/worker.log' \
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
    ssh_cmd "cd '${SERVER_DIR}' && set -a && . '${ENV_FILE}' && set +a && \
        '${PYTHON}' -m src.interfaces.cli init schedules"
    ssh_cmd "cd '${SERVER_DIR}' && set -a && . '${ENV_FILE}' && set +a && \
        '${PYTHON}' - <<'PY'
from src.interfaces.tasks import app

deleted = app.schedule_delete(['kg_community_insight_refresh_1min'])
print(f'已移除旧 Community Insight 调度: {deleted}')
app.close()
PY"

    sudo_cmd "systemctl start ${SVC_SCHEDULER}.service"
    sudo_cmd "systemctl start ${SVC_WORKER}.service"
    sudo_cmd "systemctl start ${SVC_KG_CARD}.service"
    sudo_cmd "systemctl start ${SVC_KG_RELATION}.service"
    sudo_cmd "systemctl start ${SVC_KG_GRAPH}.service"
    sudo_cmd "systemctl start ${TARGET}"
    echo "全部服务已启动"
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
    sudo_cmd "systemctl restart ${SVC_PERSIST}.service"
    sleep 2
    sudo_cmd "systemctl restart ${SVC_SCHEDULER}.service"
    sudo_cmd "systemctl restart ${SVC_WORKER}.service"
    sudo_cmd "systemctl restart ${SVC_KG_CARD}.service"
    sudo_cmd "systemctl restart ${SVC_KG_RELATION}.service"
    sudo_cmd "systemctl restart ${SVC_KG_GRAPH}.service"
}

show_status() {
    local service
    for service in "${SERVICES[@]}"; do
        echo "===== ${service} ====="
        ssh_cmd "systemctl status '${service}.service' --no-pager | sed -n '1,14p'" || true
    done
}

show_logs() {
    local service="${1:-worker}"
    local lines="${2:-100}"
    case "${service}" in
        milvus|api|persist|scheduler|worker|kg-card|kg-relation|kg-graph) ;;
        *)
            echo "日志服务必须是 milvus|api|persist|scheduler|worker|kg-card|kg-relation|kg-graph" >&2
            exit 1
            ;;
    esac
    ssh_cmd "tail -n '${lines}' '${LOG_DIR}/${service}.log'"
}

remote_test() {
    echo "执行生产健康检查..."
    local service
    for service in "${SERVICES[@]}"; do
        ssh_cmd "systemctl is-active --quiet '${service}.service'"
        echo "${service}: active"
    done

    ssh_cmd "curl --fail --silent http://127.0.0.1:8900/health"
    echo
    ssh_cmd "curl --fail --silent --max-time 60 -X POST \
        'http://127.0.0.1:8900/api/spy/start?headless=true' \
        >/tmp/smart-fund-spy-start.json"
    ssh_cmd "curl --fail --silent http://127.0.0.1:8900/api/spy/status >/tmp/smart-fund-spy-health.json"
    ssh_cmd "'${PYTHON}' -c \
        'import json; d=json.load(open(\"/tmp/smart-fund-spy-health.json\")); assert d.get(\"available\") is True and d.get(\"started\") is True; print(\"browser spy: ok\")'"
    ssh_cmd "redis-cli -h 127.0.0.1 ping | grep -q PONG"
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

init_deploy() {
    sync_code
    install_production_config
    install_redis
    install_dependencies
    install_units
    apply_schema_migrations
    initialize_runtime
    remote_test
    cleanup_legacy_server_dir
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
        --status)
            show_status
            ;;
        --logs)
            show_logs "${2:-worker}" "${3:-100}"
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
        --migrate)
            apply_schema_migrations
            ;;
        "")
            sync_code
            install_units
            apply_schema_migrations
            restart_all
            remote_test
            cleanup_legacy_server_dir
            ;;
        *)
            echo "未知参数: $1" >&2
            exit 1
            ;;
    esac
}

main "$@"
