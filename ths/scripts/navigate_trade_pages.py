#!/usr/bin/env python3
"""只读交易页面导航 + 事件窗口捕获。

按 reverse-app-skill 要求脚本化重复 UI 操作：每次只进入一个查询类页面，
记录页面加载窗口内 Hook 捕获的 bridge 事件（r9h.o / jniRequest / receive /
receiveLog），用于建立「页面 → Java 调用链 → 原生请求」映射。

用法:
    python navigate_trade_pages.py 持仓
    python navigate_trade_pages.py 查询
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.request

for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy"):
    os.environ.pop(key, None)

ADB = "/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe"
SERIAL = "3B15BJ00GZL00000"
HOOK = "http://10.168.1.158:18900"

# 交易首页按钮坐标（1080x2378，RapidOCR 实测）
TRADE_HOME_BUTTONS = {
    "持仓": (756, 1074),
    "查询": (972, 1072),
}


def adb(*args: str, timeout: float = 15) -> str:
    result = subprocess.run(
        [ADB, "-s", SERIAL, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout


def trade_logs() -> list:
    with urllib.request.urlopen(f"{HOOK}/stock/trade/logs", timeout=10) as resp:
        return json.load(resp).get("logs", [])


def logcat_since(marker: str) -> list:
    raw = adb("logcat", "-d", "-t", "800")
    lines = []
    keep = False
    for line in raw.splitlines():
        if "THSHook" not in line:
            continue
        if marker in line:
            keep = True
        if keep and any(
            key in line
            for key in ("TradeSend", "MasterBridge", "jniRequest CALL", "->")
        ):
            lines.append(line.split("THSHook : ", 1)[-1])
    return lines


def capture_window(page: str) -> dict:
    before = set(trade_logs())
    marker = f"NAV-{page}-{int(time.time())}"
    adb("shell", "input", "tap", *map(str, TRADE_HOME_BUTTONS[page]))
    time.sleep(6)
    adb("shell", f"exec-out screencap -p > /dev/null")  # keep device awake
    new_logs = [l for l in trade_logs() if l not in before]
    return {
        "page": page,
        "marker_time": marker,
        "new_trade_logs": new_logs,
    }


def main() -> int:
    page = sys.argv[1] if len(sys.argv) > 1 else "持仓"
    if page not in TRADE_HOME_BUTTONS:
        print(f"unknown page: {page}; choose from {list(TRADE_HOME_BUTTONS)}")
        return 2

    print(f"=== navigating to {page} ===")
    window = capture_window(page)
    print(json.dumps(window, ensure_ascii=False, indent=1))

    # 抓取页面加载窗口内的 logcat bridge 事件（含调用栈）
    events = logcat_since("TradeSend")
    print(f"=== logcat bridge events in tail window: {len(events)} ===")
    for e in events[-40:]:
        print(e[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
