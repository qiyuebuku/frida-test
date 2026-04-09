#!/bin/bash
# Skills 项目部署脚本 - rsync 同步整个项目到远程服务器
#
# 用法:
#   ./deploy.sh              # 同步代码 + 重启服务
#   ./deploy.sh --init       # 首次部署（创建环境、安装依赖、建库、装服务）
#   ./deploy.sh --sync-only  # 只同步代码，不重启
#   ./deploy.sh --restart    # 只重启服务
#   ./deploy.sh --status     # 查看服务状态
#   ./deploy.sh --logs [N]   # 查看最近日志（默认 50 行）
#   ./deploy.sh --test       # 远程健康检查

set -e

# ==================== 配置 ====================
REMOTE_HOST="119.23.227.187"
REMOTE_PORT="2210"
REMOTE_USER="yuyangruan"
REMOTE_PASS="199848"
CONDA_ENV="smart-fund"
CONDA_BASE="/home/${REMOTE_USER}/anaconda3"
SERVICE_NAME="smart-fund-server"

# 本地项目根目录（deploy.sh 所在目录，即 .claude/skills/）
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"

# 远程部署目录 - 保持与本地相同的 .claude/skills/ 层级结构
PROJECT_ROOT="/home/${REMOTE_USER}/smart-fund"
REMOTE_DIR="${PROJECT_ROOT}/.claude/skills"
SERVER_DIR="${REMOTE_DIR}/smart-fund-server"
SKILL_DIR="${REMOTE_DIR}/fund-trade"

# SSH key（WSL2 路径）
SSH_KEY="/mnt/c/Users/阮雨阳/.ssh/id_rsa"
SSH_KEY_TMP="/tmp/deploy_key_skills"

# rsync 排除列表
EXCLUDES="--exclude=__pycache__ --exclude=*.pyc --exclude=images/ --exclude=server.log --exclude=.git --exclude=deploy.sh --exclude=data/ --exclude=scraped_docs/ --exclude=output/"

# ==================== 工具函数 ====================
setup_ssh_key() {
    if [ ! -f "$SSH_KEY_TMP" ] || [ "$SSH_KEY" -nt "$SSH_KEY_TMP" ]; then
        cp "$SSH_KEY" "$SSH_KEY_TMP"
        chmod 600 "$SSH_KEY_TMP"
    fi
}

SSH_OPTS="-p ${REMOTE_PORT} -i ${SSH_KEY_TMP} -o StrictHostKeyChecking=no -o ConnectTimeout=10"

ssh_cmd() {
    ssh $SSH_OPTS "${REMOTE_USER}@${REMOTE_HOST}" "$@"
}

sudo_cmd() {
    ssh_cmd "echo '${REMOTE_PASS}' | sudo -S $*"
}

# ==================== 同步代码（rsync） ====================
sync_code() {
    echo "📦 同步代码到远程..."
    ssh_cmd "mkdir -p ${REMOTE_DIR}"

    rsync -az --delete $EXCLUDES \
        -e "ssh ${SSH_OPTS}" \
        "${LOCAL_DIR}/" \
        "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/"

    # 创建运行时目录
    ssh_cmd "mkdir -p ${SERVER_DIR}/images ${SKILL_DIR}/data"

    # fund-trade 需要 fund_db.py 符号链接（client.py 直接 import fund_db）
    ssh_cmd "ln -sf ${SERVER_DIR}/services/fund_db.py ${SKILL_DIR}/fund_db.py 2>/dev/null || true"

    # 确保 python 指向 conda 环境
    ssh_cmd "
        echo '${REMOTE_PASS}' | sudo -S ln -sf ${CONDA_BASE}/envs/${CONDA_ENV}/bin/python /usr/local/bin/python 2>/dev/null || true
    " 2>/dev/null

    echo "✅ 代码同步完成"
}

# ==================== 重启服务 ====================
restart_service() {
    echo "🔄 重启服务..."
    # 已配置 sudoers 免密，直接 sudo（不需要 echo password）
    ssh_cmd "sudo systemctl restart ${SERVICE_NAME}"
    sleep 2

    local status
    status=$(ssh_cmd "curl -s http://127.0.0.1:8900/health 2>/dev/null")
    if echo "$status" | grep -q '"ok"'; then
        echo "✅ 服务重启成功"
    else
        echo "❌ 服务可能启动失败，查看日志:"
        ssh_cmd "tail -20 ${SERVER_DIR}/server.log 2>/dev/null"
        exit 1
    fi
}

# ==================== 查看状态 ====================
show_status() {
    echo "📊 服务状态:"
    sudo_cmd "systemctl status ${SERVICE_NAME}" 2>/dev/null | head -15
    echo ""
    echo "📡 健康检查:"
    ssh_cmd "curl -s http://127.0.0.1:8900/health"
    echo ""
}

# ==================== 查看日志 ====================
show_logs() {
    local lines="${1:-50}"
    ssh_cmd "tail -${lines} ${SERVER_DIR}/server.log 2>/dev/null"
}

# ==================== 远程测试 ====================
remote_test() {
    echo "🧪 远程健康检查..."
    ssh_cmd "curl -s http://127.0.0.1:8900/health && echo '' && curl -s http://127.0.0.1:8900/api/auth/status"
}

# ==================== 首次初始化 ====================
init_deploy() {
    echo "🚀 首次部署初始化..."

    # 1. 同步代码
    sync_code

    # 2. 创建 conda 环境
    echo "📦 创建 conda 环境 ${CONDA_ENV}..."
    ssh_cmd "${CONDA_BASE}/bin/conda create -n ${CONDA_ENV} python=3.12 -y 2>&1 | tail -3" || echo "(环境可能已存在)"

    # 3. 安装 Python 依赖
    echo "📦 安装 Python 依赖..."
    ssh_cmd "${CONDA_BASE}/envs/${CONDA_ENV}/bin/pip install fastapi uvicorn httpx psycopg2-binary pydantic html2text 2>&1 | tail -3"

    # 4. 安装 PostgreSQL
    echo "📦 安装 PostgreSQL..."
    sudo_cmd "apt-get install -y postgresql postgresql-contrib 2>&1 | tail -3" || echo "(PG 可能已安装)"
    sudo_cmd "systemctl start postgresql" 2>/dev/null
    sudo_cmd "systemctl enable postgresql" 2>/dev/null

    # 5. 创建数据库和用户
    echo "📦 创建数据库..."
    sudo_cmd "-u postgres psql -c \"CREATE USER jettask WITH PASSWORD '123456';\"" 2>/dev/null || echo "(用户可能已存在)"
    sudo_cmd "-u postgres psql -c \"CREATE DATABASE jettask OWNER jettask;\"" 2>/dev/null || echo "(数据库可能已存在)"
    sudo_cmd "-u postgres psql -c \"GRANT ALL PRIVILEGES ON DATABASE jettask TO jettask;\"" 2>/dev/null

    # 6. 测试导入
    echo "🧪 测试导入..."
    ssh_cmd "cd ${SERVER_DIR} && ${CONDA_BASE}/envs/${CONDA_ENV}/bin/python -c 'from main import app; print(f\"Routes: {len(app.routes)}\"); print(\"Import OK\")'"

    # 7. 安装 systemd 服务
    echo "📦 安装 systemd 服务..."
    ssh_cmd "cat > /tmp/${SERVICE_NAME}.service << SVCEOF
[Unit]
Description=Smart Fund API Server
After=network.target postgresql.service

[Service]
Type=simple
User=${REMOTE_USER}
WorkingDirectory=${SERVER_DIR}
Environment=SKILL_DIR=${SKILL_DIR}
ExecStart=${CONDA_BASE}/envs/${CONDA_ENV}/bin/uvicorn main:app --host 0.0.0.0 --port 8900
Restart=always
RestartSec=3
StandardOutput=append:${SERVER_DIR}/server.log
StandardError=append:${SERVER_DIR}/server.log

[Install]
WantedBy=multi-user.target
SVCEOF"
    sudo_cmd "cp /tmp/${SERVICE_NAME}.service /etc/systemd/system/"
    sudo_cmd "systemctl daemon-reload"
    sudo_cmd "systemctl enable ${SERVICE_NAME}"

    # 8. 启动服务
    restart_service

    echo ""
    echo "🎉 首次部署完成！"
    echo "   项目根目录: ${PROJECT_ROOT}"
    echo "   服务目录: ${SERVER_DIR}"
    echo "   Skill目录: ${SKILL_DIR}"
}

# ==================== 主逻辑 ====================
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
            restart_service
            ;;
        --status)
            show_status
            ;;
        --logs)
            show_logs "${2:-50}"
            ;;
        --test)
            remote_test
            ;;
        *)
            sync_code
            restart_service
            echo ""
            echo "🎉 部署完成！"
            ;;
    esac
}

main "$@"
