#!/usr/bin/env python3
"""
自动导航到同花顺基金页面
"""

from adb_automation import ADBAutomation
import time

def main():
    # 初始化自动化工具
    adb = ADBAutomation(device_id="3B15BJ00GZL00000")

    print("\n=== Step 1: 当前屏幕识别 ===")
    screen = adb.screenshot_and_ocr()
    print(f"识别到 {len(screen.elements)} 个元素")

    # 查找"基金"元素
    fund_elements = adb.find_text("基金")
    if fund_elements:
        print(f"\n找到 {len(fund_elements)} 个'基金'元素:")
        for elem in fund_elements:
            print(f"  - '{elem.text}' at {elem.center}")

        print("\n点击'基金'元素...")
        adb.click_text("基金")

        # 等待页面加载
        print("等待页面加载...")
        time.sleep(2)

        print("\n=== Step 2: 进入基金页面后的屏幕识别 ===")
        screen = adb.screenshot_and_ocr()
        print(f"识别到 {len(screen.elements)} 个元素")

        # 显示所有元素，查找"交易"相关的选项
        print("\n屏幕上的元素:")
        for i, elem in enumerate(screen.elements[:30]):  # 显示前30个元素
            print(f"{i+1}. '{elem.text}' at {elem.center}")

        # 查找"交易"相关元素
        print("\n=== 查找'交易'相关元素 ===")
        trade_keywords = ["交易", "持仓", "我的", "资产", "买入", "卖出"]
        for keyword in trade_keywords:
            elements = adb.find_text(keyword)
            if elements:
                print(f"找到 {len(elements)} 个包含'{keyword}'的元素:")
                for elem in elements:
                    print(f"  - '{elem.text}' at {elem.center}")

        # 显示 Markdown 输出的前部分
        print("\n=== Markdown 输出（前 800 字符）===")
        print(screen.markdown[:800])

    else:
        print("\n未找到'基金'元素")
        print("显示当前屏幕上的所有元素:")
        for i, elem in enumerate(screen.elements[:20]):
            print(f"{i+1}. '{elem.text}' at {elem.center}")


if __name__ == "__main__":
    main()
