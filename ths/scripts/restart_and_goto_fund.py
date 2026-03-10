#!/usr/bin/env python3
"""
一键重启同花顺并进入基金交易页面
"""

from adb_automation import ADBAutomation
import subprocess
import time

def restart_ths_app(adb_path="/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe",
                    device_id="3B15BJ00GZL00000",
                    package_name="com.hexin.plat.android"):
    """重启同花顺 App"""
    print("\n=== 重启同花顺 App ===")

    # 1. 强制停止 App
    print("停止 App...")
    subprocess.run([adb_path, "-s", device_id, "shell", "am", "force-stop", package_name],
                   capture_output=True)
    time.sleep(1)

    # 2. 启动 App
    print("启动 App...")
    subprocess.run([adb_path, "-s", device_id, "shell", "am", "start",
                   "-n", f"{package_name}/.main.ActivityMain"],
                   capture_output=True)

    # 3. 等待 App 启动
    print("等待 App 启动...")
    time.sleep(5)  # 增加等待时间
    print("✓ App 已启动")


def navigate_to_fund_page(adb):
    """导航到基金交易页面"""

    # Step 0: 确保在同花顺主界面
    print("\n=== Step 0: 确保在同花顺界面 ===")
    screen = adb.screenshot_and_ocr()

    # 检查是否在同花顺主界面（看是否有底部导航栏）
    ths_keywords = ["首页", "行情", "自选", "交易", "资讯"]
    is_ths_main = any(adb.find_text(k) for k in ths_keywords)

    if not is_ths_main:
        print("未在同花顺主界面，按 Home 键后重新启动...")
        adb.home()
        time.sleep(1)

        # 重新启动同花顺
        subprocess.run([
            "/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe",
            "-s", "3B15BJ00GZL00000",
            "shell", "am", "start",
            "-n", "com.hexin.plat.android/.main.ActivityMain"
        ], capture_output=True)

        print("等待同花顺加载...")
        time.sleep(4)

    # Step 1: 点击底部"交易"选项卡
    print("\n=== Step 1: 点击'交易'选项卡 ===")
    adb.tap(540, 2200)  # 底部"交易"按钮
    time.sleep(2)

    # Step 2: 点击顶部"基金"选项卡
    print("\n=== Step 2: 点击'基金'选项卡 ===")
    adb.tap(363, 81)  # 顶部"基金"选项
    time.sleep(1)

    # Step 3: 点击川财证券账户
    print("\n=== Step 3: 点击账户 ===")
    screen = adb.screenshot_and_ocr()

    # 查找"川财证券"
    elements = adb.find_text("川财证券")
    if elements:
        print(f"找到账户: {elements[0].text}")
        adb.click_text("川财证券")
    else:
        # 备用方案：直接点击账户区域
        print("使用备用坐标点击账户...")
        adb.tap(200, 240)

    time.sleep(2)

    # Step 4: 验证是否进入基金页面
    print("\n=== Step 4: 验证基金页面 ===")
    screen = adb.screenshot_and_ocr()

    # 检查关键元素
    keywords = ["买入", "卖出", "持仓", "总资产"]
    found_keywords = []
    for keyword in keywords:
        if adb.find_text(keyword):
            found_keywords.append(keyword)

    if len(found_keywords) >= 2:
        print(f"✓ 成功进入基金页面！找到关键元素: {', '.join(found_keywords)}")

        # 显示账户信息
        print("\n=== 账户信息 ===")
        for elem in screen.elements[:15]:
            if elem.text:
                print(f"  {elem.text}")

        return True
    else:
        print("✗ 未能确认是否进入基金页面")
        return False


def main():
    """主函数"""
    start_time = time.time()

    # 初始化
    adb = ADBAutomation(device_id="3B15BJ00GZL00000")

    # 重启 App
    restart_ths_app()

    # 导航到基金页面
    success = navigate_to_fund_page(adb)

    # 总结
    elapsed_time = time.time() - start_time
    print(f"\n{'='*60}")
    if success:
        print(f"✓ 任务完成！总耗时: {elapsed_time:.1f} 秒")
    else:
        print(f"✗ 任务失败，耗时: {elapsed_time:.1f} 秒")
    print(f"{'='*60}\n")

    return success


if __name__ == "__main__":
    main()
