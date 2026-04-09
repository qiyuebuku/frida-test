# -*- coding: utf-8 -*-
"""
测试配置和公共工具

所有测试脚本共用的配置、断言工具、计数器。

用法:
  PYTHONPATH=. python tests/test_fund.py
  PYTHONPATH=. python tests/run_all.py
"""
import sys
import json
import httpx

# ============ 服务配置 ============

BASE_URL = "http://119.23.227.187:8900"
TIMEOUT = 15

# ============ 测试数据 ============

TEST_FUND = "110022"          # 易方达消费行业
TEST_FUND_2 = "005827"        # 易方达蓝筹精选
TEST_MANAGER = "30040233"     # 基金经理ID
TEST_STOCK = "600519"         # 贵州茅台
TEST_STOCK_SZ = "000858"      # 五粮液

# ============ 计数器 ============

passed = 0
failed = 0
skipped = 0


# ============ 工具函数 ============

def print_header(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def check(condition: bool, msg: str, detail: str = ""):
    """断言检查"""
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✓ {msg}")
    else:
        failed += 1
        print(f"  ✗ {msg}")
        if detail:
            print(f"    → {detail}")


def skip(msg: str):
    global skipped
    skipped += 1
    print(f"  ⊘ 跳过: {msg}")


def check_response(r, expect_status: int = 200) -> dict:
    """检查 HTTP 状态码，返回 JSON body"""
    check(r.status_code == expect_status, f"HTTP {r.status_code}")
    try:
        data = r.json()
    except Exception:
        data = {}
        check(False, "响应不是有效 JSON", r.text[:200])
    return data


def check_has_key(data: dict, key: str, label: str = ""):
    """检查字典中是否有指定 key"""
    check(key in data, f"{label or key} 字段存在", f"实际 keys: {list(data.keys())[:10]}")


def check_list(data, min_len: int = 0, label: str = "列表"):
    """检查是否为非空列表"""
    check(isinstance(data, list), f"{label}是列表类型")
    if isinstance(data, list):
        check(len(data) >= min_len, f"{label}长度 >= {min_len}（实际 {len(data)}）")


def print_summary():
    """打印测试汇总"""
    total = passed + failed + skipped
    print(f"\n{'='*60}")
    print(f"  测试完成: {total} 项")
    print(f"  ✓ 通过: {passed}")
    print(f"  ✗ 失败: {failed}")
    if skipped:
        print(f"  ⊘ 跳过: {skipped}")
    print(f"{'='*60}")
    if failed > 0:
        sys.exit(1)


async def check_service(client: httpx.AsyncClient) -> bool:
    """检查服务是否可用"""
    try:
        resp = await client.get(f"{BASE_URL}/health", timeout=TIMEOUT)
        if resp.status_code == 200:
            print("✓ 服务可用")
            return True
        print(f"✗ 服务不可用: HTTP {resp.status_code}")
    except httpx.ConnectError:
        print(f"✗ 无法连接: {BASE_URL}")
    return False
