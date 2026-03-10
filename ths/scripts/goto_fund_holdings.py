#!/usr/bin/env python3
"""
导航到基金持仓页面 - 自动化脚本
"""

import sys
sys.path.append('/home/yuyang/frida-test/.claude/skills/reverse-app-skill/scripts')

from adb_automation import ADBAutomation
import time


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


def goto_fund_holdings():
    """导航到基金持仓页面"""
    start_time = time.time()

    # 初始化
    adb = ADBAutomation(device_id="3B15BJ00GZL00000")

    # 确保在主界面
    if not ensure_ths_main_page(adb):
        print("\n请手动打开同花顺 App 并回到主界面")
        return False

    # Step 1: 点击底部"交易"选项卡
    print("\n" + "="*60)
    print("Step 1: 点击底部'交易'选项卡")
    print("="*60)

    adb.tap(540, 2200)
    time.sleep(2)

    screen = adb.screenshot_and_ocr()
    print(f"✓ 已点击交易")

    # Step 2: 点击账户进入基金页面
    print("\n" + "="*60)
    print("Step 2: 点击账户进入基金页面")
    print("="*60)

    # 查找账户（"川财证券自动判断 ***3857"或类似文本）
    account = None
    for elem in screen.elements:
        if ("川财证券" in elem.text or "***" in elem.text) and elem.y < 300:
            account = elem
            print(f"找到账户: '{elem.text}' @ {elem.center}")
            break

    if account:
        adb.tap(*account.center)
    else:
        # 备用坐标
        print("使用备用坐标...")
        adb.tap(353, 155)

    time.sleep(3)

    # Step 3: 向下滚动使持仓按钮可见
    print("\n" + "="*60)
    print("Step 3: 向下滚动页面")
    print("="*60)

    adb.swipe(360, 800, 360, 400, duration=500)
    time.sleep(2)

    # Step 4: 点击"持仓"按钮
    print("\n" + "="*60)
    print("Step 4: 点击'持仓'按钮进入持仓详情")
    print("="*60)

    screen = adb.screenshot_and_ocr()

    # 查找"持仓"按钮（纯文本，长度<=3）
    holdings = [e for e in screen.elements if "持仓" in e.text and len(e.text) <= 3]

    if holdings:
        print(f"找到持仓按钮: '{holdings[0].text}' @ {holdings[0].center}")
        adb.tap(*holdings[0].center)
    else:
        print("未找到持仓按钮")
        print("\n所有元素:")
        for e in screen.elements[:30]:
            if e.text:
                print(f"  [Y={e.y}] '{e.text}'")
        return False

    time.sleep(5)

    # Step 5: 验证是否成功进入持仓页面
    print("\n" + "="*60)
    print("Step 5: 验证持仓页面")
    print("="*60)

    screen = adb.screenshot_and_ocr()

    # 检查是否有持仓数据（查找包含"总金额"、"收益"等关键词的元素）
    keywords = ["总金额", "产品类型", "持有收益", "基金"]
    found = [k for k in keywords if any(k in e.text for e in screen.elements)]

    print(f"\n持仓页面关键元素: {', '.join(found)}")

    if len(found) >= 2:
        print("\n✓ 成功进入持仓页面！")

        # 显示持仓数据
        print("\n=== 持仓数据 ===")
        for elem in screen.elements[:40]:
            if elem.text and len(elem.text) > 5:
                # 跳过导航栏等无关元素
                if elem.text not in ["首页", "行情", "自选", "交易", "资讯", "理财"]:
                    print(f"  {elem.text[:100]}")

        elapsed_time = time.time() - start_time
        print(f"\n{'='*60}")
        print(f"✓ 任务完成！总耗时: {elapsed_time:.1f} 秒")
        print(f"{'='*60}\n")
        return True
    else:
        print("\n✗ 未能进入持仓页面")
        elapsed_time = time.time() - start_time
        print(f"\n{'='*60}")
        print(f"✗ 任务失败，耗时: {elapsed_time:.1f} 秒")
        print(f"{'='*60}\n")
        return False


def main():
    """主函数"""
    return goto_fund_holdings()


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
