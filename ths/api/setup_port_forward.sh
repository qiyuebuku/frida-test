#!/bin/bash
# 设置adb端口转发

ADB_PATH="/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe"
DEVICE_ID="3B15BJ00GZL00000"

echo "设置adb端口转发..."

# 设置端口转发
$ADB_PATH -s $DEVICE_ID forward tcp:18900 tcp:18900

if [ $? -eq 0 ]; then
    echo "✅ 端口转发设置成功"
    echo ""

    # 验证
    echo "当前端口转发列表："
    $ADB_PATH forward --list | grep 18900

    echo ""
    echo "💡 测试连接："
    echo "   curl http://localhost:18900/auth"
else
    echo "❌ 端口转发设置失败"
    exit 1
fi
