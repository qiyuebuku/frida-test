#!/bin/bash
# Fund Trade Agent - Cron Job Wrapper
# 确保 cron 环境下有正确的 PATH 和工作目录

set -e

# 设置 PATH（cron 环境下 PATH 可能不完整）
export PATH="/mnt/c/Users/阮雨阳/AppData/Roaming/npm:$HOME/.local/bin:$PATH"

# 工作目录
WORK_DIR="/home/yuyang/frida-test"
LOG_FILE="$HOME/fund-agent.log"
SKILL_DIR="$WORK_DIR/.claude/skills/fund-trade"

# ADB 路径
ADB="/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe"
ADB_DEVICE="3B15BJ00GZL00000"

# 日志函数
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

# 确保 server.py 运行
ensure_server() {
    if ! curl -s --noproxy '*' --connect-timeout 3 http://127.0.0.1:8900/health > /dev/null 2>&1; then
        log "server.py 未运行，正在启动..."
        cd "$SKILL_DIR"
        nohup python server.py > /tmp/fund-server.log 2>&1 &
        sleep 3
        if curl -s --noproxy '*' --connect-timeout 3 http://127.0.0.1:8900/health > /dev/null 2>&1; then
            log "server.py 启动成功"
        else
            log "ERROR: server.py 启动失败"
            return 1
        fi
        cd "$WORK_DIR"
    else
        log "server.py 已运行"
    fi
}

# 确保 adb forward 设置
ensure_adb_forward() {
    # 检查 adb forward 是否已设置
    if ! $ADB -s $ADB_DEVICE forward --list 2>/dev/null | grep -q "tcp:18900"; then
        log "设置 adb forward tcp:18900..."
        $ADB -s $ADB_DEVICE forward tcp:18900 tcp:18900 2>/dev/null || {
            log "WARNING: adb forward 设置失败（手机可能未连接）"
            return 1
        }
        log "adb forward 设置成功"
    else
        log "adb forward 已设置"
    fi
}

# 确保同花顺 App 已启动
ensure_ths_app() {
    THS_PACKAGE="com.hexin.plat.android"

    # 检查 App 是否在前台运行
    CURRENT_APP=$($ADB -s $ADB_DEVICE shell dumpsys window | grep -E 'mCurrentFocus|mFocusedApp' | head -1 2>/dev/null)

    if echo "$CURRENT_APP" | grep -q "$THS_PACKAGE"; then
        log "同花顺 App 已在前台"
    else
        log "启动同花顺 App..."
        $ADB -s $ADB_DEVICE shell monkey -p $THS_PACKAGE -c android.intent.category.LAUNCHER 1 > /dev/null 2>&1 || {
            log "WARNING: 同花顺 App 启动失败"
            return 1
        }
        # 等待 App 启动
        sleep 5
        log "同花顺 App 已启动"
    fi
}

# 检查命令
COMMAND="$1"
if [ -z "$COMMAND" ]; then
    echo "Usage: $0 <run|retrospect|sync>"
    exit 1
fi

cd "$WORK_DIR"

log "========== Starting: $COMMAND =========="

# 自动完成前置条件
ensure_server || log "WARNING: server.py 启动失败，继续执行..."
ensure_adb_forward || log "WARNING: adb forward 失败，交易功能可能不可用"
ensure_ths_app || log "WARNING: 同花顺 App 启动失败，交易功能可能不可用"

case "$COMMAND" in
    run)
        # 尾盘决策 + 执行（14:50）
        log "Executing: /fund-trade run --auto"
        claude -p "/fund-trade run --auto" >> "$LOG_FILE" 2>&1
        ;;
    retrospect)
        # 盘后复盘（15:30）
        log "Executing: /fund-trade retrospect"
        claude -p "/fund-trade retrospect" >> "$LOG_FILE" 2>&1
        ;;
    sync)
        # 持仓同步（18:00）
        log "Executing: /fund-trade sync"
        # sync 命令直接调用 Python 脚本，不需要 LLM
        python "$SKILL_DIR/fund_api.py" sync >> "$LOG_FILE" 2>&1
        ;;
    *)
        log "Unknown command: $COMMAND"
        exit 1
        ;;
esac

log "========== Finished: $COMMAND =========="
