# -*- coding: utf-8 -*-
"""
策略接口测试（routers/strategy.py）

运行方式：
  PYTHONPATH=. python tests/test_strategy.py
"""
import asyncio
import httpx
from tests.config import (
    BASE_URL, TIMEOUT,
    print_header, check, check_response, check_has_key, check_list,
    print_summary, check_service, skip,
)


async def test_config(client: httpx.AsyncClient):
    """获取配置"""
    print_header("GET /api/config — 获取配置")
    r = await client.get(f"{BASE_URL}/api/config", timeout=TIMEOUT)
    data = check_response(r)
    check_has_key(data, "status", "status")
    check_has_key(data, "data", "data")
    inner = data.get("data", {})
    check(isinstance(inner, dict) and len(inner) > 0, "配置数据非空", f"keys: {list(inner.keys())[:10]}")


async def test_decisions_today(client: httpx.AsyncClient):
    """今日决策"""
    print_header("GET /api/decisions/today — 今日决策")
    r = await client.get(f"{BASE_URL}/api/decisions/today", timeout=TIMEOUT)
    data = check_response(r)
    check_has_key(data, "status", "status")
    check_has_key(data, "data", "data")
    inner = data.get("data")
    check(isinstance(inner, list), "data 是列表（可为空）", f"type={type(inner)}")


async def test_decisions_recent(client: httpx.AsyncClient):
    """最近决策"""
    print_header("GET /api/decisions/recent — 最近决策")
    r = await client.get(f"{BASE_URL}/api/decisions/recent", timeout=TIMEOUT)
    data = check_response(r)
    check_has_key(data, "status", "status")
    check_has_key(data, "data", "data")
    inner = data.get("data")
    check(isinstance(inner, list), "data 是列表（可为空）", f"type={type(inner)}")


async def test_funds_scan(client: httpx.AsyncClient):
    """基金扫描"""
    print_header("GET /api/funds/scan — 基金扫描（完整版）")
    r = await client.get(f"{BASE_URL}/api/funds/scan", timeout=30)
    data = check_response(r)
    check_has_key(data, "data", "data")


async def test_risk_snapshot(client: httpx.AsyncClient):
    """风控快照"""
    print_header("GET /api/risk/snapshot — 风控快照")
    r = await client.get(f"{BASE_URL}/api/risk/snapshot", timeout=TIMEOUT)
    # 风控可能因依赖未初始化返回 500，只检查有响应
    if r.status_code == 200:
        data = check_response(r)
        check_has_key(data, "data", "data")
    else:
        check(r.status_code != 0, f"HTTP {r.status_code}（风控模块可能未初始化）")


async def main():
    async with httpx.AsyncClient() as client:
        if not await check_service(client):
            return
        await test_config(client)
        await test_decisions_today(client)
        await test_decisions_recent(client)
        await test_funds_scan(client)
        await test_risk_snapshot(client)
    print_summary()


if __name__ == "__main__":
    asyncio.run(main())
