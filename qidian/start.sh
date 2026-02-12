#!/bin/sh
# 一键启动起点 App + 自动同步日志
# 用法: sh ./start.sh

ADB="/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe -s 3B15BJ00GZL00000"
PKG="com.qidian.QDReader"
RPC_PORT=12345
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$SCRIPT_DIR/logs/qdhook.log"

mkdir -p "$(dirname "$LOG_FILE")"

echo "[1/3] 强制停止 $PKG ..."
$ADB shell "su -c 'am force-stop $PKG'" 2>/dev/null
sleep 1

echo "[2/3] 启动 App ..."
$ADB shell "monkey -p $PKG -c android.intent.category.LAUNCHER 1" >/dev/null 2>&1

echo "[3/3] 等待 Hook 初始化..."
MAX_WAIT=30
READY=0
i=0
while [ $i -lt $MAX_WAIT ]; do
    i=$((i + 1))
    sleep 1
    $ADB forward tcp:$RPC_PORT localabstract:qdhook_rpc >/dev/null 2>&1
    STATUS=$(python3 -c "
import socket, json
s = socket.socket()
s.settimeout(3)
try:
    s.connect(('127.0.0.1', $RPC_PORT))
    s.sendall(b'{\"cmd\":\"getStatus\"}\n')
    d = b''
    while True:
        c = s.recv(65536)
        if not c: break
        d += c
        if b'\n' in d: break
    s.close()
    r = json.loads(d)
    if r.get('success'):
        print('OK|' + str(r['data']['logBufferCount']))
    else:
        print('FAIL')
except:
    print('FAIL')
" 2>/dev/null)
    case "$STATUS" in
        OK*)
            LOG_COUNT=$(echo "$STATUS" | cut -d'|' -f2)
            echo "   RPC 就绪 (${i}s)，缓冲区已有 ${LOG_COUNT} 条日志"
            READY=1
            break
            ;;
    esac
    case $((i % 5)) in
        0) echo "   等待中... (${i}s)" ;;
    esac
done

if [ "$READY" = "0" ]; then
    echo "   RPC 连接失败 (${MAX_WAIT}s 超时)，请检查设备连接"
    exit 1
fi

echo ""
echo "同步日志到 $LOG_FILE ..."
echo "按 Ctrl+C 停止"
echo ""

python3 "$SCRIPT_DIR/scripts/log_poller.py"
