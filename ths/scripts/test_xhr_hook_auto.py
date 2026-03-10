#!/usr/bin/env python3
"""
自动测试 XHR Hook - 使用 ADB 自动化工具进入基金页面并捕获 XHR 请求
"""

import sys
sys.path.append('/home/yuyang/frida-test/.claude/skills/reverse-app-skill/scripts')

from goto_fund_page_v2 import goto_fund_page_v2, ensure_ths_main_page
from adb_automation import ADBAutomation
import subprocess
import time
import json
import re


class XHRHookTester:
    """XHR Hook 测试工具"""

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

    def capture_logcat(self):
        """捕获 logcat"""
        result = subprocess.run(
            [self.adb_path, "-s", self.device_id, "logcat", "-d"],
            capture_output=True
        )
        # 使用 errors='ignore' 忽略无法解码的字节
        return result.stdout.decode('utf-8', errors='ignore')

    def extract_xhr_logs(self, logcat):
        """
        从 logcat 中提取 XHR Hook 日志

        Returns:
            (injected, requests, responses, errors)
        """
        injected = False
        requests = []
        responses = []
        errors = []
        injection_logs = []

        for line in logcat.split('\n'):
            # XHR Hook 注入日志
            if '[XHR_HOOK] Initialized' in line:
                injected = True

            if 'Injecting XHR Hook' in line or 'XHR Hook injected' in line:
                injection_logs.append(line)

            # XHR 请求
            if '[XHR_HOOK_REQUEST]' in line:
                try:
                    # 提取 JSON 部分
                    json_start = line.find('[XHR_HOOK_REQUEST]') + len('[XHR_HOOK_REQUEST]')
                    json_str = line[json_start:].strip()
                    data = json.loads(json_str)
                    requests.append(data)
                except:
                    # 如果解析失败，保存原始行
                    requests.append({'raw': line})

            # XHR 响应
            if '[XHR_HOOK_RESPONSE]' in line:
                try:
                    json_start = line.find('[XHR_HOOK_RESPONSE]') + len('[XHR_HOOK_RESPONSE]')
                    json_str = line[json_start:].strip()
                    data = json.loads(json_str)
                    responses.append(data)
                except:
                    responses.append({'raw': line})

            # XHR 错误
            if '[XHR_HOOK_ERROR]' in line:
                errors.append(line)

        return injected, requests, responses, errors, injection_logs

    def save_results(self, logcat, requests, responses):
        """保存结果"""
        # 保存完整 logcat
        with open('/tmp/xhr_hook_logcat_full.txt', 'w', encoding='utf-8') as f:
            f.write(logcat)

        # 保存请求
        with open('/tmp/xhr_hook_requests.json', 'w', encoding='utf-8') as f:
            json.dump(requests, f, indent=2, ensure_ascii=False)

        # 保存响应
        with open('/tmp/xhr_hook_responses.json', 'w', encoding='utf-8') as f:
            json.dump(responses, f, indent=2, ensure_ascii=False)

        print("\n📁 结果已保存:")
        print("  - /tmp/xhr_hook_logcat_full.txt (完整日志)")
        print("  - /tmp/xhr_hook_requests.json (XHR 请求)")
        print("  - /tmp/xhr_hook_responses.json (XHR 响应)")


def main():
    """主流程"""
    print("\n" + "="*60)
    print("XHR Hook 自动化测试")
    print("="*60)

    tester = XHRHookTester()

    # Step 1: 确保在主界面
    print("\n=== Step 1: 确保在同花顺主界面 ===")
    ensure_ths_main_page(tester.adb)

    # Step 2: 清空 logcat
    print("\n=== Step 2: 清空 logcat ===")
    tester.clear_logcat()

    # Step 3: 导航到基金页面
    print("\n=== Step 3: 导航到基金页面 ===")
    success = goto_fund_page_v2(tester.adb)

    if not success:
        print("\n✗ 导航失败，但继续尝试捕获...")

    # Step 4: 等待页面加载和 XHR 请求
    print("\n=== Step 4: 等待页面加载 ===")
    print("等待 5 秒，让 WebView 加载并触发 XHR 请求...")
    time.sleep(5)

    # Step 5: 捕获 logcat
    print("\n=== Step 5: 捕获 logcat ===")
    logcat = tester.capture_logcat()

    # Step 6: 分析结果
    print("\n=== Step 6: 分析 XHR Hook 结果 ===")
    injected, requests, responses, errors, injection_logs = tester.extract_xhr_logs(logcat)

    # 显示注入状态
    print(f"\n✓ XHR Hook 注入状态: {'成功' if injected else '失败'}")

    if injection_logs:
        print("\n注入日志:")
        for log in injection_logs:
            print(f"  {log}")

    # 显示捕获的请求
    print(f"\n✓ 捕获到 {len(requests)} 个 XHR 请求:")
    for i, req in enumerate(requests[:5], 1):  # 只显示前 5 个
        if 'raw' in req:
            print(f"  {i}. [原始] {req['raw'][:100]}")
        else:
            method = req.get('method', 'N/A')
            url = req.get('url', 'N/A')
            print(f"  {i}. {method} {url}")

    if len(requests) > 5:
        print(f"  ... 还有 {len(requests) - 5} 个请求")

    # 显示捕获的响应
    print(f"\n✓ 捕获到 {len(responses)} 个 XHR 响应:")
    for i, resp in enumerate(responses[:5], 1):
        if 'raw' in resp:
            print(f"  {i}. [原始] {resp['raw'][:100]}")
        else:
            url = resp.get('url', 'N/A')
            status = resp.get('status', 'N/A')
            duration = resp.get('duration', 'N/A')
            print(f"  {i}. {status} {url} ({duration}ms)")

    if len(responses) > 5:
        print(f"  ... 还有 {len(responses) - 5} 个响应")

    # 显示错误
    if errors:
        print(f"\n⚠ XHR 错误: {len(errors)} 个")
        for error in errors[:3]:
            print(f"  {error}")

    # 保存结果
    tester.save_results(logcat, requests, responses)

    # Step 7: 尝试点击"持仓"按钮触发更多请求
    print("\n=== Step 7: 点击持仓触发更多请求 ===")
    tester.clear_logcat()

    screen = tester.adb.screenshot_and_ocr()
    elements = tester.adb.find_text("持仓")

    if elements:
        print("找到'持仓'按钮，点击...")
        tester.adb.click_text("持仓")
        time.sleep(3)

        # 再次捕获
        logcat2 = tester.capture_logcat()
        _, requests2, responses2, _, _ = tester.extract_xhr_logs(logcat2)

        print(f"\n✓ 点击持仓后捕获到:")
        print(f"  - {len(requests2)} 个新请求")
        print(f"  - {len(responses2)} 个新响应")

        # 显示详情
        for i, req in enumerate(requests2, 1):
            if 'raw' not in req:
                print(f"\n  请求 {i}:")
                print(f"    方法: {req.get('method')}")
                print(f"    URL: {req.get('url')}")
                if req.get('headers'):
                    print(f"    Headers: {json.dumps(req.get('headers'), indent=6, ensure_ascii=False)}")
                if req.get('body'):
                    body_str = str(req.get('body'))[:200]
                    print(f"    Body: {body_str}")

        for i, resp in enumerate(responses2, 1):
            if 'raw' not in resp:
                print(f"\n  响应 {i}:")
                print(f"    URL: {resp.get('url')}")
                print(f"    状态: {resp.get('status')}")
                resp_text = resp.get('responseText', '')[:500]
                print(f"    响应: {resp_text}")

        # 保存额外的结果
        with open('/tmp/xhr_hook_holdings_requests.json', 'w', encoding='utf-8') as f:
            json.dump(requests2, f, indent=2, ensure_ascii=False)
        with open('/tmp/xhr_hook_holdings_responses.json', 'w', encoding='utf-8') as f:
            json.dump(responses2, f, indent=2, ensure_ascii=False)

        print("\n📁 持仓数据已保存:")
        print("  - /tmp/xhr_hook_holdings_requests.json")
        print("  - /tmp/xhr_hook_holdings_responses.json")

    else:
        print("✗ 未找到'持仓'按钮")

    # 总结
    print("\n" + "="*60)
    print("测试完成")
    print("="*60)

    if injected and (len(requests) > 0 or len(requests2) > 0):
        print("\n✓ XHR Hook 工作正常！")
        print("✓ 成功捕获到 WebView 内部的 AJAX 请求")
        print("\n下一步：分析捕获的请求，找到签名/加密算法")
    elif injected:
        print("\n⚠ XHR Hook 已注入，但未捕获到请求")
        print("  可能原因：")
        print("  1. 基金页面使用了 Fetch API 而不是 XMLHttpRequest")
        print("  2. 请求不是发往 trade.5ifund.com")
        print("  3. 页面还未加载完成")
    else:
        print("\n✗ XHR Hook 未注入")
        print("  请检查：")
        print("  1. MainHook 是否正确编译")
        print("  2. Zygisk 模块是否正确部署")
        print("  3. App 是否重启")


if __name__ == "__main__":
    main()
