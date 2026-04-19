#!/usr/bin/env bash
# 启动 Smart API Router（后台运行）
# 用法: bash ~/.claude/smart-router/start.sh [start|stop|status|log]

DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$DIR/router.pid"
LOG_FILE="$DIR/router.log"

case "${1:-start}" in
    start)
        if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            echo "Router 已在运行 (PID $(cat "$PID_FILE"))"
            exit 0
        fi
        nohup python3 "$DIR/router.py" >> "$LOG_FILE" 2>&1 &
        echo $! > "$PID_FILE"
        sleep 1
        if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            echo "✓ Router 已启动 (PID $(cat "$PID_FILE")), 日志: $LOG_FILE"
        else
            echo "✗ 启动失败，查看日志: $LOG_FILE"
            exit 1
        fi
        ;;
    stop)
        if [ -f "$PID_FILE" ]; then
            kill "$(cat "$PID_FILE")" 2>/dev/null
            rm -f "$PID_FILE"
            echo "✓ Router 已停止"
        else
            echo "Router 未在运行"
        fi
        ;;
    status)
        if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            echo "✓ Router 运行中 (PID $(cat "$PID_FILE"))"
        else
            echo "✗ Router 未运行"
        fi
        ;;
    log)
        tail -f "$LOG_FILE"
        ;;
    *)
        echo "用法: $0 [start|stop|status|log]"
        ;;
esac
