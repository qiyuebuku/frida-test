#!/usr/bin/env python3
"""
导航到基金交易页面（改进版）- 每步都截图验证
"""

import sys
sys.path.append('/home/yuyang/frida-test/.claude/skills/reverse-app-skill/scripts')

from adb_automation import ADBAutomation
import time


def show_current_page(adb, title="当前页面"):
    """显示当前页面的关键元素"""
    screen = adb.screenshot_and_ocr()
    print(f"\n{title}:")
    visible_elements = [e.text for e in screen.elements[:20] if e.text and len(e.text) > 1]
    for i, text in enumerate(visible_elements[:15], 1):
        print(f"  {i}. {text}")
    return screen


def ensure_ths_main_page(adb, max_back=5):
    """确保回到同花顺主界面"""
    print("\n=== 确保在同花顺主界面 ===")

    for i in range(max_back):
        screen = adb.screenshot_and_ocr()

        # 检查是否在主界面
        ths_keywords = ["首页", "行情", "自选", "交易", "资讯"]
        found = [k for k in ths_keywords if adb.find_text(k)]

        if len(found) >= 3:
            print(f"✓ 已在主界面（找到: {', '.join(found)}）")
            return True

        print(f"  第 {i+1} 次尝试：按 Back 键...")
        adb.back()
        time.sleep(0.5)

    print("✗ 无法回到主界面")
    return False


def goto_fund_page_v2(adb):
    """从主界面导航到基金交易页面（改进版 - 每步验证）"""

    # Step 1: 点击底部"交易"选项卡
    print("\n" + "="*60)
    print("Step 1: 点击底部'交易'选项卡")
    print("="*60)

    # 先截图找到"交易"按钮的准确位置（应该在底部，Y > 2000）
    screen = adb.screenshot_and_ocr()
    trade_buttons = [e for e in adb.find_text("交易") if e.y > 2000]

    if trade_buttons:
        print(f"找到'交易'按钮: {trade_buttons[0].text} at {trade_buttons[0].center}")
        cx, cy = trade_buttons[0].center
        adb.tap(cx, cy)
    else:
        print("未找到'交易'按钮文字，使用备用坐标 (540, 2200)...")
        adb.tap(540, 2200)

    time.sleep(2)

    # 验证 Step 1
    screen = show_current_page(adb, "点击'交易'后")

    # 检查是否进入交易页面（应该看到 A股/基金/期货等选项卡）
    trade_tabs = adb.find_text("基金")
    if not trade_tabs:
        print("\n⚠ 未找到'基金'选项卡，尝试其他方法...")
        # 尝试点击"交易"文字
        if adb.find_text("交易"):
            print("点击'交易'文字...")
            adb.click_text("交易")
            time.sleep(2)
            screen = show_current_page(adb, "点击'交易'文字后")

    # Step 2: 点击顶部"基金"选项卡
    print("\n" + "="*60)
    print("Step 2: 点击顶部'基金'选项卡")
    print("="*60)

    # 查找"基金"选项卡（应该在屏幕上部，Y < 200）
    fund_tabs = [e for e in adb.find_text("基金") if e.y < 200]

    if fund_tabs:
        print(f"找到'基金'选项卡: {fund_tabs[0].text} at {fund_tabs[0].center}")
        cx, cy = fund_tabs[0].center
        adb.tap(cx, cy)
    else:
        print("未找到'基金'选项卡，使用备用坐标 (363, 81)...")
        adb.tap(363, 81)

    time.sleep(2)

    # 验证 Step 2
    screen = show_current_page(adb, "点击'基金'后")

    # Step 3: 验证是否已在基金交易页面
    print("\n" + "="*60)
    print("Step 3: 验证是否已在基金交易页面")
    print("="*60)

    # 检查关键元素
    keywords = {
        "买入": False,
        "卖出": False,
        "持仓": False,
        "总资产": False,
        "收益": False,
        "定投": False
    }

    for key in keywords:
        if adb.find_text(key):
            keywords[key] = True

    found_count = sum(keywords.values())
    print(f"\n关键元素检测: {found_count}/{len(keywords)}")
    for key, found in keywords.items():
        print(f"  {'✓' if found else '✗'} {key}")

    if found_count >= 3 and keywords["持仓"]:
        print("\n✓ 成功进入基金交易页面！")

        # Step 4: 点击"持仓"按钮
        print("\n" + "="*60)
        print("Step 4: 点击'持仓'按钮")
        print("="*60)

        holdings_buttons = adb.find_text("持仓")
        if holdings_buttons:
            print(f"找到'持仓'按钮: {holdings_buttons[0].text} at {holdings_buttons[0].center}")
            cx, cy = holdings_buttons[0].center
            adb.tap(cx, cy)
            time.sleep(3)

            # 查看点击后的页面
            screen = show_current_page(adb, "点击'持仓'后")

            print("\n✓ 已点击持仓按钮！")
            print("\n提示：点击持仓按钮后，App会通过JSBridge发送API请求获取持仓数据。")
            print("请查看logcat或HTTP代理日志以捕获持仓API请求。")
            return True
        else:
            print("\n⚠ 未找到'持仓'按钮")
            return False
    else:
        print("\n✗ 未能进入基金页面")
        print("\n可能的问题:")
        print("  1. 账户未找到或点击位置不正确")
        print("  2. 页面加载时间不够")
        print("  3. 需要手动选择账户")
        return False


def main():
    """主函数"""
    start_time = time.time()

    # 初始化
    adb = ADBAutomation(device_id="3B15BJ00GZL00000")

    # 确保在主界面
    if not ensure_ths_main_page(adb):
        print("\n请手动打开同花顺 App 并回到主界面")
        return False

    # 导航到基金页面
    success = goto_fund_page_v2(adb)

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
