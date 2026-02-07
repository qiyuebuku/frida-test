#!/bin/bash
ADB="/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe -s 3B15BJ00GZL00000"

echo "部署搜索 Hook..."

# 先进入 adb shell，然后执行命令
$ADB shell "su -c 'cp /data/local/tmp/classes*.dex /data/adb/modules/qdhook_zygisk/dex/'" 2>&1

# 重启 App
$ADB shell "am force-stop com.qidian.QDReader" 2>&1
sleep 2
$ADB shell "am start -n com.qidian.QDReader/.ui.activity.MainGroupActivity" 2>&1

echo "部署完成，等待 App 启动..."
sleep 5

echo "检查搜索 Hook 日志..."
$ADB logcat -d -s QDHook:* | grep -E "搜索|SEARCH|Found search" | tail -20
