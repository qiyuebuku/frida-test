# -*- coding: utf-8 -*-
"""
大盘行情接口测试（routers/market.py）

运行方式：
  PYTHONPATH=. python tests/test_market.py
"""
import asyncio
import httpx
from tests.config import (
    BASE_URL, TIMEOUT,
    print_header, check, check_response, check_has_key, check_list,
    print_summary, check_service, skip,
)


async def test_market_overview(client: httpx.AsyncClient):
    """A股大盘总览"""
    print_header("GET /api/market/overview — A股大盘总览")
    r = await client.get(f"{BASE_URL}/api/market/overview", timeout=TIMEOUT)
    data = check_response(r)
    check_has_key(data, "data", "data")
    inner = data.get("data", {})
    # 检查 indices 列表
    indices = inner.get("indices") or inner.get("index") or inner.get("indexList")
    if indices is not None:
        check_list(indices, min_len=1, label="data.indices")
    else:
        check(len(inner) > 0, "data 有内容", f"keys: {list(inner.keys())[:10]}")


async def test_market_environment(client: httpx.AsyncClient):
    """大盘与行业环境"""
    print_header("GET /api/market/environment — 大盘与行业环境")
    r = await client.get(f"{BASE_URL}/api/market/environment", timeout=TIMEOUT)
    data = check_response(r)
    check_has_key(data, "data", "data")
    inner = data.get("data", {})
    for key in ("indices", "northbound"):
        found = key in inner
        if not found:
            # 尝试其他可能的 key 名
            alt_keys = [k for k in inner.keys() if key[:4] in k.lower()]
            found = len(alt_keys) > 0
        check(found, f"data 中有 {key} 相关字段", f"实际 keys: {list(inner.keys())[:10]}")


async def test_stock_ranking(client: httpx.AsyncClient):
    """个股涨跌幅排行"""
    print_header("GET /api/market/stock_ranking?sort=rise&count=5 — 个股涨跌幅排行")
    r = await client.get(f"{BASE_URL}/api/market/stock_ranking", params={"sort": "rise", "count": 5}, timeout=TIMEOUT)
    data = check_response(r)
    inner = data.get("data", data)
    if isinstance(inner, list):
        check_list(inner, min_len=1, label="涨幅榜")
    elif isinstance(inner, dict):
        items = inner.get("list") or inner.get("items") or inner.get("stocks")
        if items is not None:
            check_list(items, min_len=1, label="涨幅榜列表")
        else:
            check(len(inner) > 0, "返回有数据", f"keys: {list(inner.keys())[:10]}")


async def test_sector_ranking(client: httpx.AsyncClient):
    """板块涨跌排行"""
    print_header("GET /api/market/sector_ranking?sector_type=concept&count=5 — 板块涨跌排行")
    r = await client.get(f"{BASE_URL}/api/market/sector_ranking", params={"sector_type": "concept", "count": 5}, timeout=TIMEOUT)
    data = check_response(r)
    inner = data.get("data", data)
    if isinstance(inner, list):
        check_list(inner, min_len=1, label="板块排行")
    elif isinstance(inner, dict):
        items = inner.get("list") or inner.get("items") or inner.get("sectors")
        if items is not None:
            check_list(items, min_len=1, label="板块排行列表")
        else:
            check(len(inner) > 0, "返回有数据", f"keys: {list(inner.keys())[:10]}")


async def test_capital_flow(client: httpx.AsyncClient):
    """资金流向"""
    print_header("GET /api/market/capital_flow — 资金流向")
    r = await client.get(f"{BASE_URL}/api/market/capital_flow", timeout=TIMEOUT)
    data = check_response(r)
    check_has_key(data, "data", "data")


async def test_hot_board(client: httpx.AsyncClient):
    """热点板块排行"""
    print_header("GET /api/market/hot_board — 热点板块排行")
    r = await client.get(f"{BASE_URL}/api/market/hot_board", timeout=TIMEOUT)
    data = check_response(r)
    check_has_key(data, "data", "data")


async def test_currency(client: httpx.AsyncClient):
    """货币风向"""
    print_header("GET /api/market/currency — 货币风向")
    r = await client.get(f"{BASE_URL}/api/market/currency", timeout=TIMEOUT)
    data = check_response(r)
    # 返回格式可能是 {status_code, data} 或直接是数据
    check("data" in data or "status_code" in data, "返回有数据", f"keys: {list(data.keys())[:5]}")


async def main():
    async with httpx.AsyncClient() as client:
        if not await check_service(client):
            return
        await test_market_overview(client)
        await test_market_environment(client)
        await test_stock_ranking(client)
        await test_sector_ranking(client)
        await test_capital_flow(client)
        await test_hot_board(client)
        await test_currency(client)
    print_summary()


if __name__ == "__main__":
    asyncio.run(main())
