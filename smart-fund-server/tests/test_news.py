# -*- coding: utf-8 -*-
"""
新闻接口测试（routers/market.py 中的 news 部分）

运行方式：
  PYTHONPATH=. python tests/test_news.py
"""
import asyncio
import httpx
from tests.config import (
    BASE_URL, TIMEOUT,
    print_header, check, check_response, check_has_key, check_list,
    print_summary, check_service, skip,
)


async def test_headlines(client: httpx.AsyncClient):
    """推荐头条"""
    print_header("GET /api/headlines — 推荐头条")
    r = await client.get(f"{BASE_URL}/api/headlines", timeout=TIMEOUT)
    data = check_response(r)
    check_has_key(data, "data", "data")


async def test_flash_news_tabs(client: httpx.AsyncClient):
    """快讯分类标签"""
    print_header("GET /api/flash_news/tabs — 快讯分类标签")
    r = await client.get(f"{BASE_URL}/api/flash_news/tabs", timeout=TIMEOUT)
    data = check_response(r)
    inner = data.get("data", data)
    if isinstance(inner, list):
        check_list(inner, min_len=1, label="快讯标签列表")
    elif isinstance(inner, dict):
        items = inner.get("list") or inner.get("items") or inner.get("tabs")
        if items is not None:
            check_list(items, min_len=1, label="快讯标签")
        else:
            check(len(inner) > 0, "返回有数据", f"keys: {list(inner.keys())[:10]}")


async def test_flash_news_list(client: httpx.AsyncClient):
    """分类快讯列表"""
    print_header("GET /api/flash_news/list?tag_id=21101 — A股快讯列表")
    r = await client.get(f"{BASE_URL}/api/flash_news/list", params={"tag_id": 21101}, timeout=TIMEOUT)
    data = check_response(r)
    check_has_key(data, "data", "data")


async def test_news_feed(client: httpx.AsyncClient):
    """滚动快讯"""
    print_header("GET /api/news_feed — 滚动快讯")
    r = await client.get(f"{BASE_URL}/api/news_feed", timeout=TIMEOUT)
    data = check_response(r)
    # news_feed 返回结构可能嵌套较深
    check(len(data) > 0, "返回有数据", f"keys: {list(data.keys())[:10]}")


async def test_news_themes(client: httpx.AsyncClient):
    """新闻主题分类"""
    print_header("GET /api/news_themes — 新闻主题分类")
    r = await client.get(f"{BASE_URL}/api/news_themes", timeout=TIMEOUT)
    data = check_response(r)
    check_has_key(data, "data", "data")


async def main():
    async with httpx.AsyncClient() as client:
        if not await check_service(client):
            return
        await test_headlines(client)
        await test_flash_news_tabs(client)
        await test_flash_news_list(client)
        await test_news_feed(client)
        await test_news_themes(client)
    print_summary()


if __name__ == "__main__":
    asyncio.run(main())
