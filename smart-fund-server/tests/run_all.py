# -*- coding: utf-8 -*-
"""
统一执行所有测试

运行方式：
  PYTHONPATH=. python tests/run_all.py
"""
import subprocess
import sys
import time

TEST_MODULES = [
    ("tests/test_fund.py", "基金查询"),
    ("tests/test_market.py", "大盘行情"),
    ("tests/test_stock.py", "个股查询"),
    ("tests/test_trade.py", "交易账户"),
    ("tests/test_strategy.py", "策略"),
    ("tests/test_hotlist.py", "热榜"),
    ("tests/test_news.py", "新闻"),
    ("tests/test_spy.py", "浏览器探索"),
]


def main():
    results = []
    total_start = time.time()

    for script, label in TEST_MODULES:
        print(f"\n{'#'*60}")
        print(f"# {label} ({script})")
        print(f"{'#'*60}")

        start = time.time()
        proc = subprocess.run(
            [sys.executable, script],
            env={**__import__("os").environ, "PYTHONPATH": "."},
            timeout=180,
        )
        elapsed = time.time() - start
        status = "PASS" if proc.returncode == 0 else "FAIL"
        results.append((label, status, elapsed))
        print(f"  -> {status} ({elapsed:.1f}s)")

    total_elapsed = time.time() - total_start

    print(f"\n{'='*60}")
    print(f"  汇总 ({total_elapsed:.1f}s)")
    print(f"{'='*60}")

    passed = 0
    failed = 0
    for label, status, elapsed in results:
        icon = "PASS" if status == "PASS" else "FAIL"
        print(f"  [{icon}] {label:12s}  {elapsed:.1f}s")
        if status == "PASS":
            passed += 1
        else:
            failed += 1

    print(f"\n  通过: {passed}/{len(results)}  失败: {failed}/{len(results)}")
    print(f"{'='*60}")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
