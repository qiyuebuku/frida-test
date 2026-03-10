#!/usr/bin/env python3
"""
导航到基金交易页面（不重启 App）
前提：同花顺 App 已在前台运行
"""

from adb_automation import ADBAutomation
import time

def ensure_ths_main_page(adb, max_back=5):
    """
    确保回到同花顺主界面

    策略：连续按 Back 键直到看到底部导航栏
    """
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


def goto_fund_page(adb):
    """从主界面导航到基金交易页面"""

    # Step 1: 点击底部"交易"选项卡
    print("\n=== Step 1: 点击'交易'选项卡 ===")
    adb.tap(540, 2200)
    time.sleep(1.5)

    # Step 2: 点击顶部"基金"选项卡
    print("\n=== Step 2: 点击'基金'选项卡 ===")
    adb.tap(363, 81)
    time.sleep(1)

    # Step 3: 截图识别账户
    print("\n=== Step 3: 识别并点击账户 ===")
    screen = adb.screenshot_and_ocr()

    # 查找"川财证券" - 只在屏幕上部（Y < 350）
    elements = [e for e in adb.find_text("川财证券") if e.y < 350]
    if elements:
        print(f"找到账户: {elements[0].text}")
        cx, cy = elements[0].center
        adb.tap(cx, cy)
    else:
        # 查找包含"证券"的任何文本 - 只在屏幕上部
        elements = [e for e in adb.find_text("证券") if e.y < 350]
        if elements:
            print(f"找到账户: {elements[0].text}")
            cx, cy = elements[0].center
            adb.tap(cx, cy)
        else:
            # 直接点击账户区域（通常在屏幕上部中间）
            print("使用备用坐标点击账户区域...")
            adb.tap(360, 200)

    time.sleep(2)

    # Step 4: 验证
    print("\n=== Step 4: 验证基金页面 ===")
    screen = adb.screenshot_and_ocr()

    # 检查关键元素
    keywords = {
        "买入": False,
        "卖出": False,
        "持仓": False,
        "总资产": False,
        "收益": False
    }

    for key in keywords:
        if adb.find_text(key):
            keywords[key] = True

    found_count = sum(keywords.values())
    print(f"\n关键元素检测: {found_count}/5")
    for key, found in keywords.items():
        print(f"  {'✓' if found else '✗'} {key}")

    if found_count >= 3:
        print("\n✓ 成功进入基金交易页面！")

        # 显示账户信息
        print("\n=== 账户信息 ===")
        for elem in screen.elements[:20]:
            if elem.text and len(elem.text) > 1:
                # 过滤掉单字符和纯符号
                if not all(c in '.。,，- ' for c in elem.text):
                    print(f"  {elem.text}")

        return True
    else:
        print("\n✗ 未能进入基金页面")
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
    success = goto_fund_page(adb)

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
