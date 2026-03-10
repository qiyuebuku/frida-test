#!/usr/bin/env python3
"""
同花顺登录流程捕获工具

功能：
1. 使用 Frida spawn 模式启动 App 并注入 Hook
2. 捕获登录请求和加密操作
3. 保存完整日志供后续分析

使用方法：
  python capture_login.py

  然后在手机上：
  1. 点击"我的"
  2. 点击"登录"
  3. 输入账号密码并登录
  4. 等待捕获完成后 Ctrl+C 停止
"""

import subprocess
import signal
import sys
import time
from datetime import datetime

# 配置
DEVICE_ID = "3B15BJ00GZL00000"
ADB_PATH = "/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe"
PACKAGE = "com.hexin.plat.android"
HOOK_SCRIPT = "/tmp/hook_login.js"
LOG_FILE = f"/tmp/login_capture_{int(time.time())}.log"

# 全局变量
frida_process = None


def cleanup():
    """清理进程"""
    global frida_process
    print("\n\n正在清理...")

    if frida_process:
        try:
            frida_process.terminate()
            frida_process.wait(timeout=5)
        except:
            try:
                frida_process.kill()
            except:
                pass

    print("✅ 清理完成")
    print(f"📄 日志已保存到: {LOG_FILE}")


def signal_handler(sig, frame):
    """处理 Ctrl+C"""
    cleanup()
    sys.exit(0)


def main():
    global frida_process

    # 注册信号处理
    signal.signal(signal.SIGINT, signal_handler)

    print("=" * 70)
    print("同花顺登录流程捕获工具")
    print("=" * 70)
    print()

    # 1. 检查 frida-server
    print("[1/4] 检查 frida-server...")
    result = subprocess.run(
        [ADB_PATH, "-s", DEVICE_ID, "shell", "pgrep frida-server"],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print("⚠️  frida-server 未运行，请先启动 frida-server")
        print("    提示: adb shell 'su -c \"/data/local/tmp/frida-server -l 0.0.0.0:27042 &\"'")
        sys.exit(1)
    else:
        print("✅ frida-server 运行中")
    print()

    # 2. 设置端口转发
    print("[2/4] 设置 adb 端口转发...")
    subprocess.run([ADB_PATH, "forward", "tcp:27042", "tcp:27042"], check=True)
    print("✅ 端口转发已设置")
    print()

    # 3. 停止 App
    print("[3/4] 停止同花顺 App...")
    subprocess.run(
        [ADB_PATH, "-s", DEVICE_ID, "shell", "am", "force-stop", PACKAGE],
        check=True
    )
    time.sleep(1)
    print("✅ App 已停止")
    print()

    # 4. 启动 Frida Hook（spawn 模式）
    print("[4/4] 启动 Frida Hook（spawn 模式）...")
    print(f"📄 日志文件: {LOG_FILE}")
    print()

    frida_cmd = [
        "frida",
        "-H", "localhost:27042",
        "-f", PACKAGE,
        "-l", HOOK_SCRIPT,
        "--kill-on-exit"
    ]

    with open(LOG_FILE, "w") as log_file:
        frida_process = subprocess.Popen(
            frida_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

    print("=" * 70)
    print("✅ Frida Hook 已启动，App 正在运行")
    print("=" * 70)
    print()
    print("📱 请在手机上完成以下操作：")
    print()
    print("  1️⃣  点击底部导航栏的\"我的\"按钮")
    print("  2️⃣  如果已登录，先点击\"退出登录\"")
    print("  3️⃣  点击\"登录\"按钮")
    print("  4️⃣  输入账号和密码")
    print("  5️⃣  点击\"登录\"并等待登录成功")
    print()
    print("💡 操作完成后，按 Ctrl+C 停止捕获")
    print()
    print("=" * 70)
    print("实时日志输出：")
    print("=" * 70)
    print()

    # 实时显示 Frida 输出
    try:
        with open(LOG_FILE, "w") as log_file:
            for line in frida_process.stdout:
                print(line, end='')
                log_file.write(line)
                log_file.flush()
    except KeyboardInterrupt:
        pass

    cleanup()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        cleanup()
        sys.exit(1)
