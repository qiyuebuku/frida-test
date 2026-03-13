#!/bin/bash
# 自动化部署脚本 - 通过 git 同步 skills 仓库（含 smart-fund-server）
#
# 用法:
#   ./deploy.sh              # git pull + 重启服务
#   ./deploy.sh --init       # 首次部署（clone 仓库、创建 conda 环境、安装 PG、建库）
#   ./deploy.sh --sync-only  # 只 git pull，不重启
#   ./deploy.sh --restart    # 只重启服务
#   ./deploy.sh --status     # 查看服务状态
#   ./deploy.sh --logs       # 查看最近日志
#   ./deploy.sh --test       # 远程运行健康检查

set -e

# ==================== 配置 ====================
REMOTE_HOST="119.23.227.187"
REMOTE_PORT="2210"
REMOTE_USER="yuyangruan"
REMOTE_PASS="199848"
CONDA_ENV="smart-fund"
CONDA_BASE="/home/${REMOTE_USER}/anaconda3"
SERVICE_NAME="smart-fund-server"

# git 仓库
SKILLS_REPO="git@github.com:qiyuebuku/skills.git"
SKILLS_REMOTE="/home/${REMOTE_USER}/claude-skills"

# 远程路径（均在 .claude/skills/ 子目录下）
REMOTE_DIR="${SKILLS_REMOTE}/.claude/skills/smart-fund-server"
SKILL_REMOTE="${SKILLS_REMOTE}/.claude/skills/fund-trade"

# SSH key（WSL2 路径）
SSH_KEY="/mnt/c/Users/阮雨阳/.ssh/id_rsa"
SSH_KEY_TMP="/tmp/deploy_key_smart_fund"

# ==================== 工具函数 ====================
setup_ssh_key() {
    if [ ! -f "$SSH_KEY_TMP" ] || [ "$SSH_KEY" -nt "$SSH_KEY_TMP" ]; then
        cp "$SSH_KEY" "$SSH_KEY_TMP"
        chmod 600 "$SSH_KEY_TMP"
    fi
}

ssh_cmd() {
    ssh -p "$REMOTE_PORT" -i "$SSH_KEY_TMP" -o StrictHostKeyChecking=no -o ConnectTimeout=10 "${REMOTE_USER}@${REMOTE_HOST}" "$@"
}

sudo_cmd() {
    ssh_cmd "echo '${REMOTE_PASS}' | sudo -S $*"
}

# ==================== 同步代码（git pull） ====================
sync_code() {
    echo "📦 同步代码（git pull）..."
    ssh_cmd "
        if [ -d '${SKILLS_REMOTE}/.git' ]; then
            cd '${SKILLS_REMOTE}' && git fetch origin && git reset --hard origin/main
        else
            rm -rf '${SKILLS_REMOTE}'
            git clone '${SKILLS_REPO}' '${SKILLS_REMOTE}'
        fi
    "

    # 创建运行时目录 + 同步 config.json（敏感文件不入 git，首次需复制）
    ssh_cmd "mkdir -p ${REMOTE_DIR}/images"
    local LOCAL_DIR
    LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"
    if ! ssh_cmd "test -f ${REMOTE_DIR}/config.json"; then
        echo "📋 远程缺少 config.json，同步本地配置..."
        rsync -az \
            -e "ssh -p ${REMOTE_PORT} -i ${SSH_KEY_TMP} -o StrictHostKeyChecking=no" \
            "${LOCAL_DIR}/config.json" \
            "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/config.json"
    fi

    # fund-trade 符号链接（fund_db.py / config.json 指向同仓库内的 smart-fund-server）
    ssh_cmd "
        cd ${SKILL_REMOTE}
        mkdir -p data
        ln -sf ${REMOTE_DIR}/services/fund_db.py fund_db.py 2>/dev/null || true
        ln -sf ${REMOTE_DIR}/config.json config.json 2>/dev/null || true
    "

    # 确保远程 python 指向 conda 环境
    ssh_cmd "
        echo '${REMOTE_PASS}' | sudo -S mkdir -p /usr/local/bin 2>/dev/null
        echo '${REMOTE_PASS}' | sudo -S ln -sf ${CONDA_BASE}/envs/${CONDA_ENV}/bin/python /usr/local/bin/python 2>/dev/null || true
    "
    echo "✅ 代码同步完成"
}

# ==================== 重启服务 ====================
restart_service() {
    echo "🔄 重启服务..."
    sudo_cmd "systemctl restart ${SERVICE_NAME}"
    sleep 2

    # 检查状态
    local status
    status=$(ssh_cmd "curl -s http://127.0.0.1:8900/health 2>/dev/null")
    if echo "$status" | grep -q '"ok"'; then
        echo "✅ 服务重启成功"
    else
        echo "❌ 服务可能启动失败，查看日志:"
        ssh_cmd "tail -20 ${REMOTE_DIR}/server.log 2>/dev/null"
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
    echo ""
    echo "🔑 认证状态:"
    ssh_cmd "curl -s http://127.0.0.1:8900/api/auth/status"
    echo ""
}

# ==================== 查看日志 ====================
show_logs() {
    local lines="${1:-50}"
    ssh_cmd "tail -${lines} ${REMOTE_DIR}/server.log 2>/dev/null"
}

# ==================== 远程测试 ====================
remote_test() {
    echo "🧪 远程健康检查..."
    ssh_cmd "curl -s http://127.0.0.1:8900/health && echo '' && curl -s http://127.0.0.1:8900/api/auth/status && echo '' && curl -s http://127.0.0.1:8900/api/fund/006888/base | head -c 200 && echo '...'"
}

# ==================== 首次初始化 ====================
init_deploy() {
    echo "🚀 首次部署初始化..."

    # 1. clone 仓库
    sync_code

    # 2. 创建 conda 环境
    echo "📦 创建 conda 环境 ${CONDA_ENV}..."
    ssh_cmd "${CONDA_BASE}/bin/conda create -n ${CONDA_ENV} python=3.12 -y 2>&1 | tail -3" || echo "(环境可能已存在)"

    # 3. 安装 Python 依赖
    echo "📦 安装 Python 依赖..."
    ssh_cmd "${CONDA_BASE}/envs/${CONDA_ENV}/bin/pip install fastapi uvicorn httpx psycopg2-binary pydantic 2>&1 | tail -3"

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
    ssh_cmd "cd ${REMOTE_DIR} && ${CONDA_BASE}/envs/${CONDA_ENV}/bin/python -c 'from main import app; print(f\"Routes: {len(app.routes)}\"); print(\"Import OK\")'"

    # 7. 安装 systemd 服务
    echo "📦 安装 systemd 服务..."
    ssh_cmd "cat > /tmp/${SERVICE_NAME}.service << SVCEOF
[Unit]
Description=Smart Fund API Server
After=network.target postgresql.service

[Service]
Type=simple
User=${REMOTE_USER}
WorkingDirectory=${REMOTE_DIR}
ExecStart=${CONDA_BASE}/envs/${CONDA_ENV}/bin/uvicorn main:app --host 0.0.0.0 --port 8900
Restart=always
RestartSec=3
StandardOutput=append:${REMOTE_DIR}/server.log
StandardError=append:${REMOTE_DIR}/server.log

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
            # 默认：git pull + 重启
            sync_code
            restart_service
            echo ""
            echo "🎉 部署完成！"
            ;;
    esac
}

main "$@"
