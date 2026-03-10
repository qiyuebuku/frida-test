#!/usr/bin/env python3
"""
测试 XHR Hook - 手动操作后查看 logcat
"""

import subprocess
import time

def wait_and_capture_xhr():
    """等待用户操作并捕获 XHR 请求"""

    adb_path = "/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe"
    device_id = "3B15BJ00GZL00000"

    print("\n" + "="*60)
    print("XHR Hook 测试")
    print("="*60)

    print("\n请手动操作同花顺 App：")
    print("1. 打开同花顺")
    print("2. 进入 交易 → 基金 → 选择账户")
    print("3. 等待页面加载完成")
    print("\n按回车键开始捕获 XHR 请求...")
    input()

    # 清空 logcat
    print("\n清空 logcat...")
    subprocess.run([adb_path, "-s", device_id, "logcat", "-c"], capture_output=True)

    print("✓ 开始捕获（持续 10 秒）...")
    print("  请在基金页面进行操作（点击持仓、查看基金详情等）")

    # 等待 10 秒
    time.sleep(10)

    # 捕获 logcat
    result = subprocess.run(
        [adb_path, "-s", device_id, "logcat", "-d"],
        capture_output=True,
        text=True
    )

    logcat = result.stdout

    # 查找 XHR Hook 日志
    print("\n" + "="*60)
    print("捕获结果")
    print("="*60)

    xhr_requests = []
    xhr_responses = []
    injected = False

    for line in logcat.split('\n'):
        if '[XHR_HOOK] Initialized' in line:
            injected = True
            print("✓ XHR Hook 已注入!")

        if 'Injecting XHR Hook' in line:
            print(f"✓ {line}")

        if '[XHR_HOOK_REQUEST]' in line:
            xhr_requests.append(line)
            print(f"\n📤 REQUEST: {line[line.find('[XHR_HOOK_REQUEST]'):][:200]}")

        if '[XHR_HOOK_RESPONSE]' in line:
            xhr_responses.append(line)
            print(f"\n📥 RESPONSE: {line[line.find('[XHR_HOOK_RESPONSE]'):][:200]}")

    # 保存完整日志
    with open('/tmp/xhr_hook_logcat.txt', 'w', encoding='utf-8') as f:
        f.write(logcat)

    print("\n" + "="*60)
    print(f"✓ XHR Hook 注入: {'是' if injected else '否'}")
    print(f"✓ 捕获到 XHR 请求: {len(xhr_requests)} 个")
    print(f"✓ 捕获到 XHR 响应: {len(xhr_responses)} 个")
    print(f"\n完整日志已保存到: /tmp/xhr_hook_logcat.txt")
    print("="*60)

if __name__ == "__main__":
    wait_and_capture_xhr()
