# -*- coding: utf-8 -*-
"""
个股查询接口测试（routers/market.py 中的 stock 部分）

运行方式：
  PYTHONPATH=. python tests/test_stock.py
"""
import asyncio
import httpx
from tests.config import (
    BASE_URL, TIMEOUT, TEST_STOCK,
    print_header, check, check_response, check_has_key, check_list,
    print_summary, check_service, skip,
)


async def test_stock_quote(client: httpx.AsyncClient):
    """个股实时行情"""
    print_header("GET /api/stock/quote?codes=600519 — 个股实时行情")
    r = await client.get(f"{BASE_URL}/api/stock/quote", params={"codes": TEST_STOCK}, timeout=TIMEOUT)
    data = check_response(r)
    inner = data.get("data", data)
    if isinstance(inner, list):
        check_list(inner, min_len=1, label="行情列表")
    elif isinstance(inner, dict):
        items = inner.get("list") or inner.get("items") or inner.get("quotes")
        if items is not None:
            check_list(items, min_len=1, label="行情列表")
        else:
            check(len(inner) > 0, "返回有数据", f"keys: {list(inner.keys())[:10]}")


async def test_stock_kline(client: httpx.AsyncClient):
    """个股K线"""
    print_header("GET /api/stock/{code}/kline — 个股K线")
    r = await client.get(f"{BASE_URL}/api/stock/{TEST_STOCK}/kline", timeout=TIMEOUT)
    data = check_response(r)
    # kline 非交易时间可能返回 {status_code, msg} 无 data
    check("data" in data or "status_code" in data, "返回有结果", f"keys: {list(data.keys())[:5]}")


async def test_stock_financial(client: httpx.AsyncClient):
    """个股财务数据"""
    print_header("GET /api/stock/{code}/financial — 个股财务数据")
    r = await client.get(f"{BASE_URL}/api/stock/{TEST_STOCK}/financial", timeout=TIMEOUT)
    data = check_response(r)
    check_has_key(data, "data", "data")
    inner = data.get("data")
    check(inner is not None, "data 非空")


async def test_stock_valuation(client: httpx.AsyncClient):
    """个股估值历史"""
    print_header("GET /api/stock/{code}/valuation — 个股估值历史")
    r = await client.get(f"{BASE_URL}/api/stock/{TEST_STOCK}/valuation", timeout=TIMEOUT)
    data = check_response(r)
    check_has_key(data, "data", "data")
    inner = data.get("data", {})
    items = inner.get("items") or inner.get("list")
    if items is not None:
        check_list(items, min_len=0, label="估值历史列表")
    elif isinstance(inner, list):
        check_list(inner, min_len=0, label="估值历史(列表)")
    else:
        check(len(inner) > 0, "data 有内容", f"keys: {list(inner.keys())[:10]}")


async def main():
    async with httpx.AsyncClient() as client:
        if not await check_service(client):
            return
        await test_stock_quote(client)
        await test_stock_kline(client)
        await test_stock_financial(client)
        await test_stock_valuation(client)
    print_summary()


if __name__ == "__main__":
    asyncio.run(main())
