# -*- coding: utf-8 -*-
"""
交易账户接口测试（routers/trade.py）

运行方式：
  PYTHONPATH=. python tests/test_trade.py

注意：交易相关接口可能因未认证返回 502，测试只检查不 500。
"""
import asyncio
import httpx
from tests.config import (
    BASE_URL, TIMEOUT,
    print_header, check, check_response, check_has_key, check_list,
    print_summary, check_service, skip,
)


async def test_auth_status(client: httpx.AsyncClient):
    """认证状态"""
    print_header("GET /api/auth/status — 认证状态")
    r = await client.get(f"{BASE_URL}/api/auth/status", timeout=TIMEOUT)
    data = check_response(r)
    # 返回中应有 status 字段
    check("status" in data, "有 status 字段", f"keys: {list(data.keys())[:10]}")
    # 如果 status=success，检查 data 中有认证信息
    if data.get("status") == "success":
        inner = data.get("data", {})
        check("status" in inner or "is_expired" in inner,
              "data 中有认证状态字段", f"keys: {list(inner.keys())[:10]}")


async def test_trade_overview(client: httpx.AsyncClient):
    """账户总览"""
    print_header("GET /api/trade/overview — 账户总览")
    r = await client.get(f"{BASE_URL}/api/trade/overview", timeout=TIMEOUT)
    # 可能 502（未认证），只检查不是 500
    check(r.status_code != 500, f"HTTP {r.status_code} (非500)")


async def test_trade_positions(client: httpx.AsyncClient):
    """基金持仓"""
    print_header("GET /api/trade/positions — 基金持仓")
    r = await client.get(f"{BASE_URL}/api/trade/positions", timeout=TIMEOUT)
    check(r.status_code != 500, f"HTTP {r.status_code} (非500)")


async def test_trade_orders(client: httpx.AsyncClient):
    """订单列表"""
    print_header("GET /api/trade/orders — 订单列表")
    r = await client.get(f"{BASE_URL}/api/trade/orders", timeout=TIMEOUT)
    check(r.status_code != 500, f"HTTP {r.status_code} (非500)")


async def main():
    async with httpx.AsyncClient() as client:
        if not await check_service(client):
            return
        await test_auth_status(client)
        await test_trade_overview(client)
        await test_trade_positions(client)
        await test_trade_orders(client)
    print_summary()


if __name__ == "__main__":
    asyncio.run(main())
