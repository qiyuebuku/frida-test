#!/bin/bash
# 通过 PowerShell 访问 Windows 端 RPC 转发器

function rpc_get() {
    local endpoint="$1"
    powershell.exe -Command "(Invoke-WebRequest -Uri 'http://localhost:8888${endpoint}' -UseBasicParsing).Content" 2>/dev/null
}

function format_json() {
    python3 -c "import sys, json; print(json.dumps(json.load(sys.stdin), indent=2, ensure_ascii=False))"
}

case "$1" in
    ping)
        rpc_get "/ping" | format_json
        ;;
    status)
        rpc_get "/status" | format_json
        ;;
    decrypted)
        limit="${2:-10}"
        rpc_get "/decrypted?limit=${limit}" | format_json
        ;;
    logs)
        limit="${2:-50}"
        rpc_get "/logs?limit=${limit}" | format_json
        ;;
    entries)
        rpc_get "/entries" | format_json
        ;;
    *)
        echo "Usage: $0 {ping|status|decrypted|logs|entries} [limit]"
        echo ""
        echo "Examples:"
        echo "  $0 ping         # Test connection"
        echo "  $0 status       # Get hook status"
        echo "  $0 decrypted 5  # Get recent decrypted content (limit 5)"
        echo "  $0 logs 20      # Get probe logs (limit 20)"
        ;;
esac
