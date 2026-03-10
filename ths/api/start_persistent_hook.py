#!/usr/bin/env python3
"""
启动持久化的Frida Hook服务

功能：
1. 在同花顺App运行时自动注入OkHttp Interceptor
2. 后台持续监听，捕获所有HTTP请求
3. 自动提取并缓存认证参数（key1-key5）
4. 无需手动进入基金页面

使用方法：
    # 后台运行
    python start_persistent_hook.py &

    # 或使用nohup
    nohup python start_persistent_hook.py > /tmp/frida_hook.log 2>&1 &
"""

import subprocess
import time
import signal
import sys
from pathlib import Path

# 配置
DEVICE_ID = "3B15BJ00GZL00000"
ADB_PATH = "/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe"
PACKAGE = "com.hexin.plat.android"
FRIDA_HOST = "localhost:27042"

# Frida脚本路径（使用之前创建的登录Hook脚本）
HOOK_SCRIPT = "/tmp/hook_login.js"

# 全局变量
frida_process = None
should_exit = False


def signal_handler(sig, frame):
    """处理Ctrl+C"""
    global should_exit
    print("\n收到退出信号，正在清理...")
    should_exit = True
    if frida_process:
        try:
            frida_process.terminate()
            frida_process.wait(timeout=5)
        except:
            try:
                frida_process.kill()
            except:
                pass
    sys.exit(0)


def check_app_running():
    """检查App是否运行"""
    try:
        result = subprocess.run(
            [ADB_PATH, "-s", DEVICE_ID, "shell", "ps", "-A"],
            capture_output=True,
            text=True,
            timeout=5
        )
        return PACKAGE in result.stdout
    except:
        return False


def check_frida_server():
    """检查frida-server是否运行"""
    try:
        result = subprocess.run(
            [ADB_PATH, "-s", DEVICE_ID, "shell", "pgrep", "frida-server"],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.returncode == 0
    except:
        return False


def start_frida_hook():
    """启动Frida Hook"""
    global frida_process

    try:
        print(f"[{time.strftime('%H:%M:%S')}] 正在注入Frida Hook...")

        # 使用attach模式（App已经在运行）
        frida_cmd = [
            "frida",
            "-H", FRIDA_HOST,
            "-n", PACKAGE,
            "-l", str(HOOK_SCRIPT)
        ]

        frida_process = subprocess.Popen(
            frida_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        print(f"[{time.strftime('%H:%M:%S')}] ✅ Frida Hook已注入 (PID: {frida_process.pid})")
        return True

    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] ❌ Frida Hook注入失败: {e}")
        return False


def monitor_frida_output():
    """监控Frida输出（可选）"""
    global frida_process

    if not frida_process:
        return

    try:
        # 非阻塞读取
        import select
        while frida_process.poll() is None:
            ready, _, _ = select.select([frida_process.stdout], [], [], 1)
            if ready:
                line = frida_process.stdout.readline()
                if line:
                    print(f"[Frida] {line.rstrip()}")
    except:
        pass


def main():
    global should_exit, frida_process

    # 注册信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print("=" * 70)
    print("持久化Frida Hook服务")
    print("=" * 70)
    print()
    print("功能: 自动捕获同花顺App的HTTP请求，提取认证参数")
    print("策略: 只要App在运行，就会持续监听")
    print()

    # 检查frida-server
    if not check_frida_server():
        print("❌ frida-server未运行")
        print("   请先启动: adb shell 'su -c \"/data/local/tmp/frida-server -l 0.0.0.0:27042 &\"'")
        sys.exit(1)

    print("✅ frida-server运行中")
    print()

    # 主循环
    hook_active = False
    last_check = 0

    print(f"[{time.strftime('%H:%M:%S')}] 开始监控...")
    print()

    while not should_exit:
        current_time = time.time()

        # 每5秒检查一次
        if current_time - last_check >= 5:
            last_check = current_time

            app_running = check_app_running()

            # 如果App在运行但Hook未激活
            if app_running and not hook_active:
                print(f"[{time.strftime('%H:%M:%S')}] 检测到App运行，注入Hook...")
                if start_frida_hook():
                    hook_active = True

            # 如果App不在运行但Hook已激活
            elif not app_running and hook_active:
                print(f"[{time.strftime('%H:%M:%S')}] App已停止，清理Hook")
                if frida_process:
                    try:
                        frida_process.terminate()
                        frida_process.wait(timeout=2)
                    except:
                        try:
                            frida_process.kill()
                        except:
                            pass
                    frida_process = None
                hook_active = False

            # 如果Hook已激活，检查进程是否还活着
            elif hook_active and frida_process:
                if frida_process.poll() is not None:
                    print(f"[{time.strftime('%H:%M:%S')}] ⚠️ Frida进程意外退出，尝试重新注入")
                    hook_active = False

        # 短暂休眠
        time.sleep(0.5)

    print(f"[{time.strftime('%H:%M:%S')}] 服务已停止")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
