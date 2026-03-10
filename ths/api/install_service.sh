#!/bin/bash
# 安装 THS Fund Server 服务

echo "🚀 安装 THS Fund Server systemd 服务"
echo "=" * 50

# 1. 复制 service 文件
echo "1. 复制 service 文件到 systemd 目录..."
sudo cp /tmp/ths-fund-server.service /etc/systemd/system/

# 2. 重载 systemd
echo "2. 重载 systemd..."
sudo systemctl daemon-reload

# 3. 启动服务
echo "3. 启动服务..."
sudo systemctl start ths-fund-server

# 4. 设置开机自启动
echo "4. 设置开机自启动..."
sudo systemctl enable ths-fund-server

# 5. 查看状态
echo ""
echo "✅ 安装完成！"
echo ""
echo "服务状态："
sudo systemctl status ths-fund-server --no-pager

echo ""
echo "常用命令："
echo "  启动服务: sudo systemctl start ths-fund-server"
echo "  停止服务: sudo systemctl stop ths-fund-server"
echo "  重启服务: sudo systemctl restart ths-fund-server"
echo "  查看状态: sudo systemctl status ths-fund-server"
echo "  查看日志: tail -f /home/yuyang/frida-test/ths/api/server.log"
echo "  禁用自启: sudo systemctl disable ths-fund-server"
