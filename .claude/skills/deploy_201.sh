#!/bin/bash
# Skills 项目部署脚本 - rsync 同步整个项目到远程服务器
#
# 用法:
#   ./deploy.sh              # 同步代码 + 重启所有服务
#   ./deploy.sh --init       # 首次部署（创建环境、安装依赖、建库、装服务）
#   ./deploy.sh --sync-only  # 只同步代码，不重启
#   ./deploy.sh --restart    # 只重启所有服务
#   ./deploy.sh --status     # 查看所有服务状态
#   ./deploy.sh --logs [N]   # 查看 smart-fund-server 最近日志（默认 50 行）
#   ./deploy.sh --test       # 远程健康检查
#   ./deploy.sh --router     # 只部署/重启 smart-router

set -e

# ==================== 配置 ====================
REMOTE_HOST="10.168.1.201"
REMOTE_PORT="22"
REMOTE_USER="yuyangruan"
REMOTE_PASS="ruan19980418"
CONDA_ENV="smart-fund"
CONDA_BASE="/home/${REMOTE_USER}/anaconda3"

# 服务名
SVC_SERVER="smart-fund-server"
SVC_ROUTER="smart-router"

# 本地项目根目录（deploy.sh 所在目录，即 .claude/skills/）
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"

# 远程部署目录
PROJECT_ROOT="/home/${REMOTE_USER}/smart-fund"
REMOTE_DIR="${PROJECT_ROOT}/.claude/skills"
SERVER_DIR="${REMOTE_DIR}/smart-fund-server"
SKILL_DIR="${REMOTE_DIR}/fund-trade"
ROUTER_DIR="${REMOTE_DIR}/smart-router"

# SSH key（WSL2 路径）
SSH_KEY="/mnt/c/Users/阮雨阳/.ssh/id_rsa"
SSH_KEY_TMP="/tmp/deploy_key_skills"

# rsync 排除列表
EXCLUDES="--exclude=__pycache__ --exclude=*.pyc --exclude=images/ --exclude=server.log --exclude=router.log --exclude=router.pid --exclude=.git --exclude=deploy.sh --exclude=data/ --exclude=scraped_docs/ --exclude=output/"

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

# ==================== smart-fund-server 服务 ====================
restart_server() {
    echo "🔄 重启 ${SVC_SERVER}..."
    sudo_cmd "systemctl restart ${SVC_SERVER}"
    sleep 2

    local status
    status=$(ssh_cmd "curl -s http://127.0.0.1:8900/health 2>/dev/null")
    if echo "$status" | grep -q '"ok"'; then
        echo "✅ ${SVC_SERVER} 重启成功"
    else
        echo "❌ ${SVC_SERVER} 可能启动失败，查看日志:"
        ssh_cmd "tail -20 ${SERVER_DIR}/server.log 2>/dev/null"
        exit 1
    fi
}

# ==================== smart-router 服务 ====================
install_router_service() {
    echo "📦 安装 ${SVC_ROUTER} 服务..."

    # 安装 aiohttp
    ssh_cmd "${CONDA_BASE}/envs/${CONDA_ENV}/bin/pip install aiohttp 2>&1 | tail -3"

    # 生成 systemd service
    ssh_cmd "cat > /tmp/${SVC_ROUTER}.service << SVCEOF
[Unit]
Description=Smart API Router for Claude Code
After=network.target

[Service]
Type=simple
User=${REMOTE_USER}
WorkingDirectory=${ROUTER_DIR}
ExecStart=${CONDA_BASE}/envs/${CONDA_ENV}/bin/python ${ROUTER_DIR}/router.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SVCEOF"
    sudo_cmd "cp /tmp/${SVC_ROUTER}.service /etc/systemd/system/"
    sudo_cmd "systemctl daemon-reload"
    sudo_cmd "systemctl enable ${SVC_ROUTER}"
    echo "✅ ${SVC_ROUTER} 服务已安装"
}

restart_router() {
    echo "🔄 重启 ${SVC_ROUTER}..."
    sudo_cmd "systemctl restart ${SVC_ROUTER}"
    sleep 2

    # 健康检查：发一个简单请求
    local result
    result=$(ssh_cmd "curl -s -X POST http://127.0.0.1:8462/v1/messages \
        -H 'Content-Type: application/json' \
        -H 'anthropic-version: 2023-06-01' \
        -d '{\"model\":\"GLM-5.1\",\"max_tokens\":5,\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}' 2>/dev/null")
    if echo "$result" | grep -q '"content"'; then
        echo "✅ ${SVC_ROUTER} 重启成功（路由测试通过）"
    else
        echo "⚠️  ${SVC_ROUTER} 已启动，但路由测试未通过（可能是后端问题）"
        ssh_cmd "sudo journalctl -u ${SVC_ROUTER} --no-pager -n 10" 2>/dev/null
    fi
}

# ==================== 重启所有服务 ====================
restart_all() {
    restart_server
    restart_router
}

# ==================== 查看状态 ====================
show_status() {
    echo "═══════════════ ${SVC_SERVER} ═══════════════"
    sudo_cmd "systemctl status ${SVC_SERVER}" 2>/dev/null | head -15
    echo ""
    echo "📡 健康检查:"
    ssh_cmd "curl -s http://127.0.0.1:8900/health" 2>/dev/null
    echo ""

    echo ""
    echo "═══════════════ ${SVC_ROUTER} ═══════════════"
    sudo_cmd "systemctl status ${SVC_ROUTER}" 2>/dev/null | head -15
    echo ""
    echo "📡 路由测试:"
    ssh_cmd "curl -s -X POST http://127.0.0.1:8462/v1/messages \
        -H 'Content-Type: application/json' \
        -H 'anthropic-version: 2023-06-01' \
        -d '{\"model\":\"GLM-5.1\",\"max_tokens\":5,\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}' 2>/dev/null | head -1"
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
    echo ""
    echo "--- ${SVC_SERVER} ---"
    ssh_cmd "curl -s http://127.0.0.1:8900/health && echo '' && curl -s http://127.0.0.1:8900/api/auth/status"
    echo ""
    echo "--- ${SVC_ROUTER} ---"
    ssh_cmd "curl -s -X POST http://127.0.0.1:8462/v1/messages \
        -H 'Content-Type: application/json' \
        -H 'anthropic-version: 2023-06-01' \
        -d '{\"model\":\"GLM-5.1\",\"max_tokens\":5,\"messages\":[{\"role\":\"user\",\"content\":\"say ok\"}]}' 2>/dev/null"
    echo ""
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
    ssh_cmd "${CONDA_BASE}/envs/${CONDA_ENV}/bin/pip install fastapi uvicorn httpx psycopg2-binary pydantic html2text aiohttp 2>&1 | tail -3"

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

    # 7. 安装 smart-fund-server systemd 服务
    echo "📦 安装 ${SVC_SERVER} 服务..."
    ssh_cmd "cat > /tmp/${SVC_SERVER}.service << SVCEOF
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
    sudo_cmd "cp /tmp/${SVC_SERVER}.service /etc/systemd/system/"
    sudo_cmd "systemctl daemon-reload"
    sudo_cmd "systemctl enable ${SVC_SERVER}"

    # 8. 安装 smart-router systemd 服务
    install_router_service

    # 9. 启动所有服务
    restart_all

    echo ""
    echo "🎉 首次部署完成！"
    echo "   项目根目录: ${PROJECT_ROOT}"
    echo "   服务目录:   ${SERVER_DIR}"
    echo "   Skill目录:  ${SKILL_DIR}"
    echo "   路由目录:   ${ROUTER_DIR}"
    echo ""
    echo "   ${SVC_SERVER}: http://${REMOTE_HOST}:8900"
    echo "   ${SVC_ROUTER}:    http://${REMOTE_HOST}:8462"
    echo ""
    echo "   ⚠️  记得配置 nginx 反向代理 + HTTPS（见 smart-router/DEPLOY.md）"
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
            restart_all
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
        --router)
            sync_code
            install_router_service
            restart_router
            ;;
        *)
            sync_code
            restart_all
            echo ""
            echo "🎉 部署完成！"
            ;;
    esac
}

main "$@"
