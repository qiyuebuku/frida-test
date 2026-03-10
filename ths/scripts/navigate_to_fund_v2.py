#!/usr/bin/env python3
"""
自动导航到同花顺基金页面（改进版）
"""

from adb_automation import ADBAutomation
import time
import json

def main():
    # 初始化自动化工具
    adb = ADBAutomation(device_id="3B15BJ00GZL00000")

    print("\n=== Step 1: 查看当前屏幕 ===")
    screen = adb.screenshot_and_ocr()
    print(f"OCR 返回 {len(screen.elements)} 个元素")

    # 输出 raw JSON 以便调试
    print("\n=== Raw JSON (前 1000 字符) ===")
    print(json.dumps(screen.raw_json, indent=2, ensure_ascii=False)[:1000])

    # 从截图可以看到底部有"交易"选项卡
    # 屏幕宽度 1080，高度约 2340
    # "交易"按钮在底部中间，约 (540, 2200)
    print("\n=== Step 2: 点击底部'交易'选项卡 ===")
    print("点击坐标 (540, 2200)...")
    adb.tap(540, 2200)

    # 等待页面加载
    print("等待页面加载...")
    time.sleep(2)

    print("\n=== Step 3: 进入交易页面后的屏幕识别 ===")
    screen = adb.screenshot_and_ocr()
    print(f"识别到 {len(screen.elements)} 个元素")

    # 显示所有元素
    print("\n屏幕上的元素:")
    for i, elem in enumerate(screen.elements[:30]):
        print(f"{i+1}. '{elem.text}' at {elem.center}")

    # 显示 Markdown 输出
    print("\n=== Markdown 输出（前 1000 字符）===")
    print(screen.markdown[:1000])

    # 查找"基金"相关元素
    print("\n=== 查找'基金'相关元素 ===")
    keywords = ["基金", "股票", "期权", "债券", "资产", "持仓"]
    for keyword in keywords:
        elements = adb.find_text(keyword)
        if elements:
            print(f"找到 {len(elements)} 个包含'{keyword}'的元素:")
            for elem in elements:
                print(f"  - '{elem.text}' at {elem.center}")

if __name__ == "__main__":
    main()
