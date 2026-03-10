#!/usr/bin/env python3
"""
进入川财证券基金账户
"""

from adb_automation import ADBAutomation
import time

def main():
    # 初始化自动化工具
    adb = ADBAutomation(device_id="3B15BJ00GZL00000")

    print("\n=== 当前屏幕识别 ===")
    screen = adb.screenshot_and_ocr()
    print(f"识别到 {len(screen.elements)} 个元素")

    # 显示所有元素
    print("\n屏幕上的元素:")
    for i, elem in enumerate(screen.elements[:20]):
        print(f"{i+1}. '{elem.text}' at {elem.center}")

    # 查找"川财证券"
    print("\n=== 查找'川财证券'账户 ===")
    elements = adb.find_text("川财证券")
    if elements:
        print(f"找到 {len(elements)} 个包含'川财证券'的元素:")
        for elem in elements:
            print(f"  - '{elem.text}' at {elem.center}")

        print("\n点击'川财证券'账户...")
        adb.click_text("川财证券")
    else:
        # 如果没找到文字，尝试点击账户区域（图标+文字的位置）
        # 从截图看，账户区域大约在 (200, 240) 附近
        print("未找到'川财证券'文字，尝试点击账户区域...")
        adb.tap(200, 240)

    # 等待页面加载
    print("等待页面加载...")
    time.sleep(2)

    print("\n=== 识别基金交易页面 ===")
    screen = adb.screenshot_and_ocr()
    print(f"识别到 {len(screen.elements)} 个元素")

    # 显示所有元素
    print("\n屏幕上的元素:")
    for i, elem in enumerate(screen.elements[:50]):
        if elem.text:  # 只显示有文本的元素
            print(f"{i+1}. '{elem.text}' at {elem.center}")

    # 显示 Markdown 输出
    print("\n=== Markdown 输出（前 2000 字符）===")
    print(screen.markdown[:2000])

    # 查找基金相关的关键词
    print("\n=== 查找基金交易相关元素 ===")
    keywords = ["持有", "持仓", "净值", "收益", "基金", "可用", "市值", "买入", "卖出", "赎回"]
    for keyword in keywords:
        elements = adb.find_text(keyword)
        if elements:
            print(f"✓ 找到 '{keyword}': {len(elements)} 个")

if __name__ == "__main__":
    main()
