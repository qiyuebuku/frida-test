#!/bin/bash
# 检查JSBridge服务状态

echo "正在检查JSBridge服务..."
echo

# 检查服务是否可达
if curl -s --connect-timeout 2 http://localhost:18900/auth > /dev/null 2>&1; then
    echo "✅ JSBridge服务正在运行"
    echo

    # 获取状态
    STATUS=$(curl -s http://localhost:18900/auth | jq -r '.available')
    HAS_KEY5=$(curl -s http://localhost:18900/auth | jq -r '.key5 != null and .key5 != ""')

    if [ "$STATUS" = "true" ]; then
        echo "✅ JSBridge可用（App已在交易页面）"

        if [ "$HAS_KEY5" = "true" ]; then
            echo "✅ 已捕获到key5 token"
            echo
            echo "💡 可以直接运行 auto-refresh，会从手机获取token"
        else
            echo "⚠️ 未捕获到key5"
            echo
            echo "💡 建议：在手机上进入 交易 → 基金 页面"
        fi
    else
        echo "⚠️ JSBridge不可用（App可能未在交易页面）"
        echo
        echo "💡 建议："
        echo "   1. 在手机上打开同花顺App"
        echo "   2. 进入 交易 → 基金 页面"
        echo "   或运行: python start_persistent_hook.py &"
    fi
else
    echo "❌ JSBridge服务未运行"
    echo
    echo "💡 建议："
    echo "   1. 启动JSBridge: cd /home/yuyang/frida-test/ths && ./start_jsbridge.sh"
    echo "   2. 或启动持久化Hook: python api/start_persistent_hook.py &"
fi

echo
echo "─────────────────────────────────────────"
echo "刷新策略说明："
echo "─────────────────────────────────────────"
echo "优先级1: 从手机获取 (如果JSBridge可用)"
echo "  ✅ 不会踢掉手机端"
echo "  ⚠️ 需要JSBridge服务运行"
echo
echo "优先级2: 使用密码登录 (如果手机不可用)"
echo "  ✅ 完全自动化，无需手机"
echo "  ⚠️ 会踢掉手机端（单点登录限制）"
echo
