#!/system/bin/sh
# 这个脚本将在手机端执行

# 复制 dex 文件到模块目录
cp /data/local/tmp/classes*.dex /data/adb/modules/qdhook_zygisk/dex/

# 重启起点读书
am force-stop com.qidian.QDReader
sleep 2
am start -n com.qidian.QDReader/.ui.activity.MainGroupActivity

echo "模块已更新，App 已重启"
