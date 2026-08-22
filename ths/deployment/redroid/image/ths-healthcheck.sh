#!/system/bin/sh
set -eu

STATE_DIR=/data/local/tmp/ths-runtime
[ -f "$STATE_DIR/ready" ]
grep -q '"ready":true' "$STATE_DIR/status.json"
pidof com.hexin.plat.android >/dev/null

# ready 只是启动管理器的落盘结果；同时确认 Hook HTTP 监听仍存活。
printf 'GET /health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n' \
    | toybox nc -w 3 127.0.0.1 18900 \
    | grep -q '"ok":true'

runtime_status=$(
    printf 'GET /native/runtime/status HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n' \
        | toybox nc -w 3 127.0.0.1 18900 | sed '1,/^\r$/d'
)
printf '%s' "$runtime_status" | grep -q '"runtime_ready":true'

. "$STATE_DIR/bootstrap.env"
if [ "$THS_MODE" = trade ]; then
    trade_status=$(
        printf 'GET /stock/trade/runtime/status HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n' \
            | toybox nc -w 3 127.0.0.1 18900 | sed '1,/^\r$/d'
    )
    printf '%s' "$trade_status" | grep -q '"write_ready":true'
fi
