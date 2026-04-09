# -*- coding: utf-8 -*-
"""
基金查询接口测试（routers/fund_query.py）

运行方式：
  PYTHONPATH=. python tests/test_fund.py
"""
import asyncio
import httpx
from tests.config import (
    BASE_URL, TIMEOUT, TEST_FUND,
    print_header, check, check_response, check_has_key, check_list,
    print_summary, check_service, skip,
)


async def test_fund_detail(client: httpx.AsyncClient):
    """基金综合详情"""
    print_header("GET /api/fund/{code} — 基金综合详情")
    r = await client.get(f"{BASE_URL}/api/fund/{TEST_FUND}", timeout=TIMEOUT)
    data = check_response(r)
    check_has_key(data, "data", "data")


async def test_fund_base(client: httpx.AsyncClient):
    """基金基础信息"""
    print_header("GET /api/fund/{code}/base — 基金基础信息")
    r = await client.get(f"{BASE_URL}/api/fund/{TEST_FUND}/base", timeout=TIMEOUT)
    data = check_response(r)
    check_has_key(data, "data", "data")
    inner = data.get("data", {})
    check("riskLevel" in inner or "risk_level" in inner, "data 中有风险等级字段", f"实际 keys: {list(inner.keys())[:10]}")


async def test_fund_info(client: httpx.AsyncClient):
    """基金行情信息"""
    print_header("GET /api/fund/{code}/info — 基金行情信息")
    r = await client.get(f"{BASE_URL}/api/fund/{TEST_FUND}/info", timeout=TIMEOUT)
    data = check_response(r)
    check_has_key(data, "data", "data")
    inner = data.get("data", {})
    for key in ("nav", "dayGrowth"):
        # nav 可能叫 net，dayGrowth 可能叫 day
        found = key in inner or "net" in inner or "day" in inner
        check(found, f"data 中有净值/涨幅相关字段", f"实际 keys: {list(inner.keys())[:15]}")


async def test_fund_holdings(client: httpx.AsyncClient):
    """前十大持仓"""
    print_header("GET /api/fund/{code}/holdings — 前十大持仓")
    r = await client.get(f"{BASE_URL}/api/fund/{TEST_FUND}/holdings", timeout=TIMEOUT)
    data = check_response(r)
    check_has_key(data, "data", "data")
    inner = data.get("data", {})
    # 持仓中通常有 stock 列表
    stock = inner.get("stock") or inner.get("stocks") or inner.get("list")
    if stock is not None:
        check_list(stock, min_len=1, label="data.stock")
    else:
        # 也可能 data 本身就是列表
        if isinstance(inner, list):
            check_list(inner, min_len=1, label="data(列表)")
        else:
            check(False, "data 中有 stock/stocks/list 字段", f"实际 keys: {list(inner.keys())[:15]}")


async def test_fund_rank(client: httpx.AsyncClient):
    """阶段涨幅排名"""
    print_header("GET /api/fund/{code}/rank — 阶段涨幅排名")
    r = await client.get(f"{BASE_URL}/api/fund/{TEST_FUND}/rank", timeout=TIMEOUT)
    data = check_response(r)
    check_has_key(data, "data", "data")


async def test_fund_nav(client: httpx.AsyncClient):
    """净值走势"""
    print_header("GET /api/fund/{code}/nav?period=year — 净值走势")
    r = await client.get(f"{BASE_URL}/api/fund/{TEST_FUND}/nav?period=year", timeout=TIMEOUT)
    data = check_response(r)
    check_has_key(data, "data", "data")
    inner = data.get("data")
    check(inner is not None, "data 非空", f"type={type(inner)}")


async def test_holdings_valuation(client: httpx.AsyncClient):
    """持仓股估值"""
    print_header("GET /api/fund/{code}/holdings/valuation — 持仓股估值")
    r = await client.get(f"{BASE_URL}/api/fund/{TEST_FUND}/holdings/valuation", timeout=TIMEOUT)
    data = check_response(r)
    check_has_key(data, "data", "data")
    inner = data.get("data")
    if isinstance(inner, list):
        check_list(inner, min_len=1, label="data(列表)")
    elif isinstance(inner, dict):
        check(len(inner) > 0, "data 非空", f"keys: {list(inner.keys())[:10]}")


async def test_fund_search(client: httpx.AsyncClient):
    """基金搜索"""
    print_header("GET /api/fund/search?keyword=消费&limit=3 — 基金搜索")
    r = await client.get(f"{BASE_URL}/api/fund/search", params={"keyword": "消费", "limit": 3}, timeout=TIMEOUT)
    data = check_response(r)
    # 返回可能是 {data: [...]} 或直接是列表
    inner = data.get("data", data)
    if isinstance(inner, list):
        check_list(inner, min_len=1, label="搜索结果")
    elif isinstance(inner, dict):
        items = inner.get("list") or inner.get("items") or inner.get("funds")
        if items is not None:
            check_list(items, min_len=1, label="搜索结果列表")
        else:
            check(len(inner) > 0, "搜索返回有数据", f"keys: {list(inner.keys())[:10]}")


async def test_fund_ranking(client: httpx.AsyncClient):
    """基金排行"""
    print_header("POST /api/fund/ranking — 基金排行")
    body = {"fundType": "stock", "sortField": "per_1y", "page": 1, "pageSize": 3}
    r = await client.post(f"{BASE_URL}/api/fund/ranking", json=body, timeout=TIMEOUT)
    data = check_response(r)
    check_has_key(data, "data", "data")


async def main():
    async with httpx.AsyncClient() as client:
        if not await check_service(client):
            return
        await test_fund_detail(client)
        await test_fund_base(client)
        await test_fund_info(client)
        await test_fund_holdings(client)
        await test_fund_rank(client)
        await test_fund_nav(client)
        await test_holdings_valuation(client)
        await test_fund_search(client)
        await test_fund_ranking(client)
    print_summary()


if __name__ == "__main__":
    asyncio.run(main())
