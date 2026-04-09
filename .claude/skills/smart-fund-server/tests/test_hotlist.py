# -*- coding: utf-8 -*-
"""
热榜接口测试（routers/market.py 中的 hotlist 部分）

运行方式：
  PYTHONPATH=. python tests/test_hotlist.py
"""
import asyncio
import httpx
from tests.config import (
    BASE_URL, TIMEOUT,
    print_header, check, check_response, check_has_key, check_list,
    print_summary, check_service, skip,
)


async def test_hot_stocks(client: httpx.AsyncClient):
    """个股热榜"""
    print_header("GET /api/hotlist/stocks — 个股热榜")
    r = await client.get(f"{BASE_URL}/api/hotlist/stocks", timeout=TIMEOUT)
    data = check_response(r)
    inner = data.get("data", data)
    if isinstance(inner, list):
        check_list(inner, min_len=1, label="个股热榜")
    elif isinstance(inner, dict):
        items = inner.get("list") or inner.get("items") or inner.get("stocks")
        if items is not None:
            check_list(items, min_len=1, label="个股热榜列表")
        else:
            check(len(inner) > 0, "返回有数据", f"keys: {list(inner.keys())[:10]}")


async def test_hot_plate(client: httpx.AsyncClient):
    """概念/行业热榜"""
    print_header("GET /api/hotlist/plate?plate_type=concept — 概念热榜")
    r = await client.get(f"{BASE_URL}/api/hotlist/plate", params={"plate_type": "concept"}, timeout=TIMEOUT)
    data = check_response(r)
    inner = data.get("data", data)
    if isinstance(inner, list):
        check_list(inner, min_len=1, label="概念热榜")
    elif isinstance(inner, dict):
        items = inner.get("list") or inner.get("items")
        if items is not None:
            check_list(items, min_len=1, label="概念热榜列表")
        else:
            check(len(inner) > 0, "返回有数据", f"keys: {list(inner.keys())[:10]}")


async def test_hot_etf(client: httpx.AsyncClient):
    """ETF 热榜"""
    print_header("GET /api/hotlist/etf — ETF 热榜")
    r = await client.get(f"{BASE_URL}/api/hotlist/etf", timeout=TIMEOUT)
    data = check_response(r)
    inner = data.get("data", data)
    if isinstance(inner, list):
        check_list(inner, min_len=1, label="ETF 热榜")
    elif isinstance(inner, dict):
        items = inner.get("list") or inner.get("items")
        if items is not None:
            check_list(items, min_len=1, label="ETF 热榜列表")
        else:
            check(len(inner) > 0, "返回有数据", f"keys: {list(inner.keys())[:10]}")


async def test_hot_topics(client: httpx.AsyncClient):
    """热榜话题"""
    print_header("GET /api/hotlist/topics — 热榜话题")
    r = await client.get(f"{BASE_URL}/api/hotlist/topics", timeout=TIMEOUT)
    data = check_response(r)
    inner = data.get("data", data)
    if isinstance(inner, list):
        check_list(inner, min_len=1, label="热榜话题")
    elif isinstance(inner, dict):
        items = inner.get("list") or inner.get("items") or inner.get("topics")
        if items is not None:
            check_list(items, min_len=1, label="热榜话题列表")
        else:
            check(len(inner) > 0, "返回有数据", f"keys: {list(inner.keys())[:10]}")


async def main():
    async with httpx.AsyncClient() as client:
        if not await check_service(client):
            return
        await test_hot_stocks(client)
        await test_hot_plate(client)
        await test_hot_etf(client)
        await test_hot_topics(client)
    print_summary()


if __name__ == "__main__":
    asyncio.run(main())
