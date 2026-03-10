#!/usr/bin/env python3
"""
使用 ADB 自动化 + OCR 辅助抓取基金 API 请求
目标：捕获完整的请求参数，分析签名算法

使用场景：
1. 自动操作 App（点击持仓、买入、卖出等）
2. 通过 logcat 抓取 JSBridge 调用
3. 分析请求的完整参数（Header、签名、Token 等）
"""

import sys
sys.path.append('/home/yuyang/frida-test/.claude/skills/reverse-app-skill/scripts')

from goto_fund_page import goto_fund_page, ensure_ths_main_page
from adb_automation import ADBAutomation
import subprocess
import time
import json
import re


class FundRequestCapture:
    """基金请求抓包工具"""

    def __init__(self, device_id="3B15BJ00GZL00000",
                 adb_path="/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe"):
        self.device_id = device_id
        self.adb_path = adb_path
        self.adb = ADBAutomation(device_id=device_id)

    def clear_logcat(self):
        """清空 logcat"""
        subprocess.run([self.adb_path, "-s", self.device_id, "logcat", "-c"],
                       capture_output=True)
        print("✓ Logcat cleared")

    def capture_logcat(self, duration=3):
        """
        捕获一段时间的 logcat

        Args:
            duration: 捕获时长（秒）

        Returns:
            logcat 内容
        """
        print(f"Capturing logcat for {duration} seconds...")
        time.sleep(duration)

        result = subprocess.run(
            [self.adb_path, "-s", self.device_id, "logcat", "-d"],
            capture_output=True,
            text=True
        )

        return result.stdout

    def extract_jsbridge_calls(self, logcat_output):
        """
        从 logcat 中提取 JSBridge 调用

        Returns:
            list of dict: JSBridge 调用列表
        """
        calls = []

        # 匹配 JSBridge 调用日志
        # 格式: THSHook : JSBridge call: handler=clientRequestHX, data={...}
        pattern = r'THSHook.*?clientRequestHX.*?data=(\{.*?\})'

        matches = re.finditer(pattern, logcat_output, re.DOTALL)

        for match in matches:
            try:
                data_str = match.group(1)
                # 尝试解析 JSON（可能需要清理）
                data = json.loads(data_str)
                calls.append(data)
            except:
                # 如果解析失败，保存原始字符串
                calls.append({"raw": match.group(0)})

        return calls

    def click_and_capture(self, action_name, x, y, wait_before=1, wait_after=3):
        """
        点击并捕获请求

        Args:
            action_name: 操作名称
            x, y: 点击坐标
            wait_before: 点击前等待时间
            wait_after: 点击后等待时间

        Returns:
            捕获的 JSBridge 调用
        """
        print(f"\n=== {action_name} ===")

        # 清空日志
        self.clear_logcat()

        # 等待
        if wait_before > 0:
            time.sleep(wait_before)

        # 点击
        print(f"点击坐标 ({x}, {y})...")
        self.adb.tap(x, y)

        # 捕获
        logcat = self.capture_logcat(duration=wait_after)

        # 提取
        calls = self.extract_jsbridge_calls(logcat)

        print(f"✓ 捕获到 {len(calls)} 个 JSBridge 调用")

        return calls, logcat

    def click_text_and_capture(self, text, action_name, wait_before=1, wait_after=3):
        """
        通过 OCR 查找文本并点击，然后捕获请求

        Args:
            text: 要查找的文本
            action_name: 操作名称
            wait_before: 点击前等待时间
            wait_after: 点击后等待时间

        Returns:
            捕获的 JSBridge 调用
        """
        print(f"\n=== {action_name} ===")

        # 截图识别
        screen = self.adb.screenshot_and_ocr()
        elements = self.adb.find_text(text)

        if not elements:
            print(f"✗ 未找到文本: {text}")
            return [], ""

        # 清空日志
        self.clear_logcat()

        # 等待
        if wait_before > 0:
            time.sleep(wait_before)

        # 点击
        print(f"点击'{text}'...")
        self.adb.click_text(text)

        # 捕获
        logcat = self.capture_logcat(duration=wait_after)

        # 提取
        calls = self.extract_jsbridge_calls(logcat)

        print(f"✓ 捕获到 {len(calls)} 个 JSBridge 调用")

        return calls, logcat

    def save_capture(self, data, filename):
        """保存捕获的数据"""
        filepath = f"/tmp/{filename}"
        with open(filepath, 'w', encoding='utf-8') as f:
            if isinstance(data, str):
                f.write(data)
            else:
                json.dump(data, f, indent=2, ensure_ascii=False)

        print(f"✓ 数据已保存到: {filepath}")
        return filepath


def scenario_1_view_positions(capture):
    """
    场景 1：查看持仓
    目标：捕获持仓查询 API
    """
    print("\n" + "="*60)
    print("场景 1：查看持仓")
    print("="*60)

    # 确保在基金页面
    ensure_ths_main_page(capture.adb)
    goto_fund_page(capture.adb)

    # 点击"持仓"按钮
    calls, logcat = capture.click_text_and_capture(
        text="持仓",
        action_name="点击持仓",
        wait_after=4
    )

    # 保存
    capture.save_capture(calls, "captured_positions_api.json")
    capture.save_capture(logcat, "captured_positions_logcat.txt")

    return calls


def scenario_2_view_fund_detail(capture, fund_name):
    """
    场景 2：查看基金详情
    目标：捕获基金详情 API
    """
    print("\n" + "="*60)
    print(f"场景 2：查看基金详情 - {fund_name}")
    print("="*60)

    # 假设已经在持仓页面
    calls, logcat = capture.click_text_and_capture(
        text=fund_name,
        action_name=f"点击基金 {fund_name}",
        wait_after=4
    )

    # 保存
    capture.save_capture(calls, f"captured_fund_detail_{fund_name}_api.json")
    capture.save_capture(logcat, f"captured_fund_detail_{fund_name}_logcat.txt")

    return calls


def scenario_3_buy_fund(capture):
    """
    场景 3：模拟买入操作（不真正提交）
    目标：捕获买入页面的加载请求
    """
    print("\n" + "="*60)
    print("场景 3：模拟买入")
    print("="*60)

    # 回到基金主页
    capture.adb.back()
    time.sleep(1)

    # 点击"买入"
    calls, logcat = capture.click_text_and_capture(
        text="买入",
        action_name="点击买入",
        wait_after=4
    )

    # 保存
    capture.save_capture(calls, "captured_buy_page_api.json")
    capture.save_capture(logcat, "captured_buy_page_logcat.txt")

    return calls


def main():
    """主流程"""
    print("\n" + "="*60)
    print("基金 API 请求抓包工具")
    print("="*60)

    # 初始化
    capture = FundRequestCapture()

    # 场景 1：查看持仓
    scenario_1_view_positions(capture)

    # 场景 2：查看基金详情（选择第一只基金）
    # TODO: 从持仓中提取基金名称
    # scenario_2_view_fund_detail(capture, "华泰柏瑞量化")

    # 场景 3：买入页面
    # scenario_3_buy_fund(capture)

    print("\n" + "="*60)
    print("✓ 抓包完成")
    print("="*60)
    print("\n捕获的文件:")
    print("  - /tmp/captured_positions_api.json")
    print("  - /tmp/captured_positions_logcat.txt")
    print("\n下一步：分析这些文件，找到签名算法")


if __name__ == "__main__":
    main()
