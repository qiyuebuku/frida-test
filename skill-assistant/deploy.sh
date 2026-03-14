#!/bin/bash
# 一键编译、安装、重绑无障碍服务
set -e

ADB="/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe -s 3B15BJ00GZL00000"
PKG="com.example.screenshotassistant"
A11Y="$PKG/$PKG.service.ScreenAssistAccessibilityService"

cd "$(dirname "$0")"

echo "=== 编译 ==="
./gradlew assembleDebug -q

echo "=== 安装 ==="
$ADB install -r app/build/outputs/apk/debug/app-debug.apk

echo "=== 重绑无障碍服务 ==="
echo "settings put secure enabled_accessibility_services null" | $ADB shell
echo "settings put secure accessibility_enabled 0" | $ADB shell
sleep 2
echo "settings put secure enabled_accessibility_services $A11Y" | $ADB shell
echo "settings put secure accessibility_enabled 1" | $ADB shell
sleep 2

# 验证
RESULT=$(echo "settings get secure enabled_accessibility_services" | $ADB shell)
if echo "$RESULT" | grep -q "$PKG"; then
    echo "=== 无障碍服务已绑定 ==="
else
    echo "!!! 无障碍服务绑定失败: $RESULT"
fi

# 检查 onServiceConnected
$ADB logcat -d | grep "ScreenAssistA11y.*onServiceConnected" | tail -1

echo "=== 部署完成 ==="
