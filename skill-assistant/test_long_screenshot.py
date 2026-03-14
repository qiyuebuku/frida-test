#!/usr/bin/env python3
"""
自动化测试长截图功能（稳健版）

用法:
  python3 test_long_screenshot.py           # 完整流程
  python3 test_long_screenshot.py --quick   # 跳过编译，只触发截图
  python3 test_long_screenshot.py --deploy  # 只编译安装+授权
"""

import subprocess
import time
import os
import sys
import glob
import re

# ===== 配置 =====
ADB_PATH = "/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe"
DEVICE_ID = "3B15BJ00GZL00000"
PKG = "com.example.screenshotassistant"
A11Y_SERVICE = f"{PKG}/{PKG}.service.ScreenAssistAccessibilityService"
PROJECT_DIR = "/home/yuyang/frida-test/screenshot-assistant"

for k in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"]:
    os.environ.pop(k, None)


# ===== ADB 工具 =====

def adb(*args):
    cmd = [ADB_PATH, "-s", DEVICE_ID] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.strip()


def adb_shell(cmd_str):
    proc = subprocess.Popen(
        [ADB_PATH, "-s", DEVICE_ID, "shell"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    stdout, _ = proc.communicate(cmd_str + "\n", timeout=10)
    return stdout.strip()


def ui_dump():
    """获取 UI 层次结构"""
    return subprocess.run(
        [ADB_PATH, "-s", DEVICE_ID, "exec-out", "uiautomator", "dump", "/dev/tty"],
        capture_output=True, text=True, timeout=10
    ).stdout


def find_bounds(dump, text, y_min=0, y_max=9999):
    """从 dump 中找到文本的 bounds 坐标，返回 (cx, cy) 或 None"""
    pattern = rf'text="[^"]*{re.escape(text)}[^"]*"[^/]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"'
    for match in re.finditer(pattern, dump):
        x1, y1, x2, y2 = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        if y_min <= cy <= y_max:
            return (cx, cy)
    return None


def tap(x, y, desc=""):
    if desc:
        print(f"  tap '{desc}': ({x}, {y})")
    adb("shell", "input", "tap", str(x), str(y))


def find_and_tap(text, y_min=0, y_max=9999, wait=0.5, retry=1):
    """查找文本并点击，支持重试"""
    for attempt in range(retry):
        if attempt > 0:
            time.sleep(1)
        dump = ui_dump()
        pos = find_bounds(dump, text, y_min, y_max)
        if pos:
            tap(pos[0], pos[1], text)
            time.sleep(wait)
            return True
    print(f"  未找到 '{text}'")
    return False


def wait_for_text(text, timeout=10, y_min=0, y_max=9999):
    """等待文本出现"""
    start = time.time()
    while time.time() - start < timeout:
        dump = ui_dump()
        pos = find_bounds(dump, text, y_min, y_max)
        if pos:
            return pos
        time.sleep(1)
    return None


def screenshot(path="/tmp/test_screenshot.png"):
    subprocess.run(f'"{ADB_PATH}" -s {DEVICE_ID} exec-out screencap -p > {path}', shell=True, timeout=10)


def step(msg):
    print(f"\n{'='*50}\n  {msg}\n{'='*50}")


# ===== 流程步骤 =====

def build_and_install():
    step("1. 编译安装")
    os.chdir(PROJECT_DIR)
    r = subprocess.run(["./gradlew", "assembleDebug", "-q"], capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        print(f"✗ 编译失败:\n{r.stderr[-300:]}")
        return False
    print("✓ 编译成功")
    result = adb("install", "-r", "app/build/outputs/apk/debug/app-debug.apk")
    ok = "Success" in result
    print(f"{'✓' if ok else '✗'} 安装: {result}")
    return ok


def bind_accessibility():
    step("2. 绑定无障碍服务")
    adb_shell("settings put secure enabled_accessibility_services null")
    adb_shell("settings put secure accessibility_enabled 0")
    time.sleep(2)
    adb_shell(f"settings put secure enabled_accessibility_services {A11Y_SERVICE}")
    adb_shell("settings put secure accessibility_enabled 1")
    time.sleep(2)
    result = adb_shell("settings get secure enabled_accessibility_services")
    ok = PKG in result
    print(f"{'✓' if ok else '✗'} 无障碍: {'已绑定' if ok else '失败'}")
    return ok


def authorize_screenshot():
    step("3. 授权截屏权限")
    adb_shell(f"am force-stop {PKG}")
    time.sleep(1)
    adb("shell", "am", "start", "-n", f"{PKG}/.MainActivity")
    time.sleep(5)

    # 点"启动服务"
    if not find_and_tap("启动服务", retry=3, wait=4):
        print("✗ 未找到启动服务按钮")
        return False

    # 授权流程：使用确认过的固定坐标序列
    # Spinner center=(540, 1061), "整个屏幕"=(540, 1277), "开始"=(870, 1610)
    time.sleep(2)

    # 检查对话框是否出现
    dump = ui_dump()
    if "screen_share" in dump or "录制或投放" in dump or "单个应用" in dump:
        print("  授权对话框已出现")

        # Step 1: 点击 Spinner 展开下拉框
        tap(540, 1061, "Spinner展开")
        time.sleep(2)

        # Step 2: 选择"整个屏幕"（下拉框中第二个选项）
        tap(540, 1277, "整个屏幕")
        time.sleep(2)

        # Step 3: 点击"开始"按钮
        tap(870, 1610, "开始")
        time.sleep(3)
    elif find_bounds(dump, "停止服务"):
        print("  服务已在运行")
    else:
        print("  未检测到授权对话框，尝试直接点击坐标序列")
        tap(540, 1061, "Spinner展开")
        time.sleep(2)
        tap(540, 1277, "整个屏幕")
        time.sleep(2)
        tap(870, 1610, "开始")
        time.sleep(3)

    # 设置 adb reverse
    adb("reverse", "tcp:8765", "tcp:8765")

    # 绑定无障碍服务（确保同时包含 AutoJs6）
    a11y_autojs = "org.autojs.autojs6/org.autojs.autojs.core.accessibility.AccessibilityServiceUsher"
    adb_shell(f"settings put secure enabled_accessibility_services {A11Y_SERVICE}:{a11y_autojs}")
    adb_shell("settings put secure accessibility_enabled 1")
    time.sleep(2)

    # 验证
    logs = adb("logcat", "-d", "-s", "ScreenCaptureService:D")
    if "MediaProjection setup complete" in logs:
        size_m = re.search(r'setup complete: (\d+x\d+)', logs)
        print(f"✓ 截屏服务就绪 {size_m.group(1) if size_m else ''}")
        return True
    print("⚠ 截屏服务未确认，可能需要手动授权")
    screenshot("/tmp/auth_debug.png")
    return False


def navigate_to_ths_holdings():
    step("4. 导航到同花顺持仓页面")
    adb("shell", "monkey", "-p", "com.hexin.plat.android",
        "-c", "android.intent.category.LAUNCHER", "1")
    time.sleep(4)

    dump = ui_dump()
    if find_bounds(dump, "持仓") and (find_bounds(dump, "总资产") or find_bounds(dump, "基金")):
        print("✓ 已在持仓页面")
        find_and_tap("持仓", y_max=400)
        time.sleep(2)
        return True

    print("  导航: 交易 → 基金 → 持仓")
    find_and_tap("交易", y_min=2000, y_max=2400)
    time.sleep(3)
    find_and_tap("基金", y_max=200)
    time.sleep(3)
    find_and_tap("持仓", y_max=400)
    time.sleep(3)
    print("✓ 导航完成")
    return True


def trigger_long_screenshot():
    step("5. 触发自动长截图")
    adb("logcat", "-c")
    time.sleep(0.5)

    # 方法1: 通过 am startservice 传递 trigger_action
    result = adb("shell", "am", "startservice",
                 "-n", f"{PKG}/.service.FloatingWindowService",
                 "--es", "trigger_action", "fund_holdings")
    if "Error" in result:
        print(f"  startservice 失败: {result}")
        print("  尝试 startforegroundservice...")
        result = adb("shell", "am", "start-foreground-service",
                     "-n", f"{PKG}/.service.FloatingWindowService",
                     "--es", "trigger_action", "fund_holdings")
        if "Error" in result:
            print(f"  也失败了: {result}")
            print("  回退到手动触发：点击悬浮球")
            # 点悬浮球位置（根据代码 gravity=TOP|START, x=0, y=200）
            # 悬浮球大小大约 48dp ≈ 96px，实际位置 (48, 296)
            tap(48, 296, "悬浮球")
            time.sleep(1)
            screenshot("/tmp/menu_check.png")
            # 从菜单中找"持仓分析"
            find_and_tap("持仓分析", retry=2)
            time.sleep(1)
    else:
        print("✓ 指令已发送")

    # 监控
    max_wait = 50
    start_time = time.time()
    last_msg = ""

    while time.time() - start_time < max_wait:
        time.sleep(3)
        logs = adb("logcat", "-d", "-s", "FloatingWindow:D", "ImageStitcher:D")
        captured = logs.count("captured #")
        scrolls = logs.count("scroll #")
        msg = f"截图={captured}帧 滚动={scrolls}次"

        if msg != last_msg:
            print(f"  [{time.time()-start_time:.0f}s] {msg}")
            last_msg = msg

        if "assembled:" in logs:
            time.sleep(3)
            print("✓ 拼接完成!")
            return True

        if captured >= 1 and any("sendOrSave" in l for l in logs.split("\n")[-5:]):
            print(f"{'✓' if captured > 1 else '⚠'} 已发送 ({captured}帧)")
            return captured > 1

        if "bitmap is null" in logs and captured == 0:
            print("✗ 截屏失败 (bitmap is null)")
            return False
        if "accessibility service not connected" in logs:
            print("✗ 无障碍服务未连接")
            return False

    print(f"⚠ 超时 ({max_wait}s)")
    return False


def analyze_result():
    step("6. 分析结果")
    logs = adb("logcat", "-d", "-s", "FloatingWindow:D", "ImageStitcher:D")

    keywords = [
        "captured #", "contentBounds", "measureScroll", "bestD=",
        "assembled", "performAutoScroll", "a11y instance",
        "bitmap is null", "saveToGallery", "stitch:", "paste frame",
        "matchRate", "sendOrSave", "overlap=", "trigger_action"
    ]
    relevant = [l for l in logs.split("\n") if any(k in l for k in keywords)]

    print("\n关键日志:")
    for line in relevant[-40:]:
        for tag in ["D FloatingWindow:", "D ImageStitcher:"]:
            if tag in line:
                prefix = "[Stitch] " if "ImageStitcher" in tag else ""
                line = prefix + line.split(tag)[-1].strip()
                break
        print(f"  {line.strip()[-120:]}")

    # 服务端图片
    images_dir = os.path.join(PROJECT_DIR, "server/images")
    images = sorted(glob.glob(f"{images_dir}/screenshot_*.jpg"), key=os.path.getmtime)
    if images:
        latest = images[-1]
        age = time.time() - os.path.getmtime(latest)
        size = os.path.getsize(latest)
        print(f"\n最新图片: {os.path.basename(latest)} ({size/1024:.0f}KB, {age:.0f}s前)")
        try:
            from PIL import Image
            img = Image.open(latest)
            print(f"尺寸: {img.size[0]}x{img.size[1]}")
        except Exception:
            pass

    screenshot("/tmp/test_final.png")
    print("最终屏幕: /tmp/test_final.png")


def main():
    start = time.time()
    quick = "--quick" in sys.argv
    deploy_only = "--deploy" in sys.argv
    os.chdir(PROJECT_DIR)

    if not quick:
        if not build_and_install():
            return
        if not bind_accessibility():
            return
        authorize_screenshot()
        if deploy_only:
            print(f"\n部署完成 ({time.time()-start:.1f}s)")
            return

    if not quick:
        navigate_to_ths_holdings()

    trigger_long_screenshot()
    analyze_result()
    print(f"\n总耗时: {time.time()-start:.1f}s")


if __name__ == "__main__":
    main()
