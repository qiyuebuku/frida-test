#!/usr/bin/env python3
"""
点击基金选项卡
"""

from adb_automation import ADBAutomation
import time

def main():
    # 初始化自动化工具
    adb = ADBAutomation(device_id="3B15BJ00GZL00000")

    print("\n=== 点击'基金'选项卡 ===")
    # 从 OCR 结果可知顶部选项卡 "A股 基金 期货 黄金 模拟"
    # bbox: [165, 67, 828, 95]
    # "基金"是第二个选项，估计中心在 (330, 81) 左右
    # 宽度 663，分 5 个选项，每个约 132px
    # A股: 165 + 66 = 231
    # 基金: 165 + 66 + 132 = 363

    print("点击'基金'选项卡坐标 (363, 81)...")
    adb.tap(363, 81)

    # 等待页面加载
    print("等待页面加载...")
    time.sleep(2)

    print("\n=== 识别基金页面 ===")
    screen = adb.screenshot_and_ocr()
    print(f"识别到 {len(screen.elements)} 个元素")

    # 显示所有元素
    print("\n屏幕上的元素:")
    for i, elem in enumerate(screen.elements[:40]):
        print(f"{i+1}. '{elem.text}' at {elem.center}")

    # 显示 Markdown 输出
    print("\n=== Markdown 输出（前 1500 字符）===")
    print(screen.markdown[:1500])

    # 查找基金相关的关键词
    print("\n=== 查找基金相关元素 ===")
    keywords = ["持有", "持仓", "净值", "收益", "基金名称", "我的基金", "可用", "市值"]
    for keyword in keywords:
        elements = adb.find_text(keyword)
        if elements:
            print(f"✓ 找到 {len(elements)} 个包含'{keyword}'的元素")

if __name__ == "__main__":
    main()
