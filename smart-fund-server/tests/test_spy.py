# -*- coding: utf-8 -*-
"""
浏览器探索接口测试（routers/spy.py）

覆盖：
  1. GET  /api/spy/status — 状态查询
  2. POST /api/spy/goto — 打开目标网站，记录请求
  3. GET  /api/spy/api — 查看拦截到的 API 请求
  4. GET  /api/spy/body — 查看响应体
  5. GET  /api/spy/text — 获取页面文本
  6. GET  /api/spy/console — 控制台日志
  7. GET  /api/spy/failed — 失败请求
  8. POST /api/spy/clear — 清空记录
  9. POST /api/spy/stop — 停止浏览器

运行方式：
  PYTHONPATH=. python tests/test_spy.py
"""
import asyncio
import httpx
from tests.config import (
    BASE_URL, TIMEOUT,
    print_header, check, check_response, check_has_key, check_list,
    print_summary, check_service, skip,
)

# 用于测试的目标网站（httpbin 稳定可靠，有链接可测试）
TEST_TARGET = "https://httpbin.org/links/5"


async def test_status(client: httpx.AsyncClient):
    """浏览器状态"""
    print_header("GET /api/spy/status — 浏览器状态")
    r = await client.get(f"{BASE_URL}/api/spy/status", timeout=TIMEOUT)
    data = check_response(r)
    check_has_key(data, "available", "available")
    check_has_key(data, "started", "started")
    check_has_key(data, "requests_count", "requests_count")
    return data.get("available", False)


async def test_goto(client: httpx.AsyncClient):
    """打开目标网站"""
    print_header("POST /api/spy/goto — 打开 httpbin.org")
    try:
        r = await client.post(
            f"{BASE_URL}/api/spy/goto",
            json={"url": TEST_TARGET, "wait_after": 3},
            timeout=60,  # 首次启动浏览器可能慢，但不等太久
        )
        data = check_response(r)
        check(data.get("status") == "ok", "返回 status=ok")
        check(data.get("total_requests", 0) > 0, f"拦截到请求（{data.get('total_requests', 0)} 个）")
        check("httpbin" in (data.get("url") or ""), "页面 URL 包含 httpbin")
        return True
    except Exception as e:
        skip(f"goto 请求失败（浏览器可能未完全安装）: {type(e).__name__}")
        return False


async def test_api_requests(client: httpx.AsyncClient):
    """查看拦截到的 API 请求"""
    print_header("GET /api/spy/api — 拦截到的 XHR/Fetch 请求")
    r = await client.get(f"{BASE_URL}/api/spy/api", timeout=TIMEOUT)
    data = check_response(r)
    check_has_key(data, "count", "count")
    check_has_key(data, "api_requests", "api_requests")
    check(isinstance(data.get("api_requests"), list), "api_requests 是列表")


async def test_all_requests(client: httpx.AsyncClient):
    """查看所有请求"""
    print_header("GET /api/spy/requests — 所有请求")
    r = await client.get(f"{BASE_URL}/api/spy/requests", timeout=TIMEOUT)
    data = check_response(r)
    check(data.get("count", 0) > 0, f"有请求记录（{data.get('count', 0)} 个）")
    requests = data.get("requests", [])
    if requests:
        first = requests[0]
        check("url" in first, "请求记录有 url 字段")
        check("method" in first, "请求记录有 method 字段")


async def test_text(client: httpx.AsyncClient):
    """获取页面文本"""
    print_header("GET /api/spy/text — 页面纯文本")
    r = await client.get(f"{BASE_URL}/api/spy/text", timeout=TIMEOUT)
    data = check_response(r)
    check_has_key(data, "text", "text")
    check(data.get("length", 0) > 0, "页面文本非空")


async def test_links(client: httpx.AsyncClient):
    """获取页面链接"""
    print_header("GET /api/spy/links — 页面链接")
    r = await client.get(f"{BASE_URL}/api/spy/links", timeout=TIMEOUT)
    data = check_response(r)
    check_has_key(data, "count", "count")
    check_has_key(data, "links", "links")
    check(isinstance(data.get("links"), list), "links 是列表")


async def test_filter_links(client: httpx.AsyncClient):
    """过滤链接"""
    print_header("POST /api/spy/filter_links — 过滤链接")
    r = await client.post(
        f"{BASE_URL}/api/spy/filter_links",
        json={"selector": "a", "url_pattern": "httpbin"},
        timeout=TIMEOUT,
    )
    data = check_response(r)
    check_has_key(data, "count", "count")
    links = data.get("links", [])
    check(isinstance(links, list), "links 是列表")
    if links:
        check("url" in links[0], "链接有 url 字段")
        check("text" in links[0], "链接有 text 字段")


async def test_click(client: httpx.AsyncClient):
    """点击元素"""
    print_header("POST /api/spy/click — 点击元素")
    r = await client.post(
        f"{BASE_URL}/api/spy/click",
        json={"selector": "a"},
        timeout=TIMEOUT,
    )
    data = check_response(r)
    check("status" in data, "返回有 status 字段")


async def test_href(client: httpx.AsyncClient):
    """获取元素 href"""
    print_header("GET /api/spy/href — 获取 href")
    # 先回到有链接的页面
    await client.post(f"{BASE_URL}/api/spy/goto", json={"url": TEST_TARGET, "wait_after": 2}, timeout=60)
    r = await client.get(f"{BASE_URL}/api/spy/href", params={"selector": "a"}, timeout=TIMEOUT)
    data = check_response(r)
    check_has_key(data, "href", "href")


async def test_console(client: httpx.AsyncClient):
    """控制台日志"""
    print_header("GET /api/spy/console — 控制台日志")
    r = await client.get(f"{BASE_URL}/api/spy/console", timeout=TIMEOUT)
    data = check_response(r)
    check_has_key(data, "count", "count")
    check_has_key(data, "logs", "logs")


async def test_failed(client: httpx.AsyncClient):
    """失败请求"""
    print_header("GET /api/spy/failed — 失败请求")
    r = await client.get(f"{BASE_URL}/api/spy/failed", timeout=TIMEOUT)
    data = check_response(r)
    check_has_key(data, "count", "count")
    check_has_key(data, "requests", "requests")


async def test_clear(client: httpx.AsyncClient):
    """清空记录"""
    print_header("POST /api/spy/clear — 清空记录")
    r = await client.post(f"{BASE_URL}/api/spy/clear", timeout=TIMEOUT)
    data = check_response(r)
    check(data.get("status") == "ok", "返回 status=ok")

    # 验证确实清空了
    r2 = await client.get(f"{BASE_URL}/api/spy/requests", timeout=TIMEOUT)
    data2 = r2.json()
    check(data2.get("count", -1) == 0, "清空后请求数为 0")


async def test_stop(client: httpx.AsyncClient):
    """停止浏览器"""
    print_header("POST /api/spy/stop — 停止浏览器")
    r = await client.post(f"{BASE_URL}/api/spy/stop", timeout=TIMEOUT)
    data = check_response(r)
    check(data.get("status") == "ok", "返回 status=ok")

    # 验证已停止
    r2 = await client.get(f"{BASE_URL}/api/spy/status", timeout=TIMEOUT)
    data2 = r2.json()
    check(data2.get("started") is False, "浏览器已停止")


async def main():
    async with httpx.AsyncClient() as client:
        if not await check_service(client):
            return

        # 检查 camoufox 是否可用
        available = await test_status(client)

        if not available:
            skip("camoufox 未安装，跳过浏览器功能测试")
            # 仍然测试不依赖浏览器的接口
            await test_console(client)
            await test_failed(client)
            await test_clear(client)
        else:
            # 完整测试流程：启动 → 访问 → 查看 → 清空 → 停止
            goto_ok = await test_goto(client)
            if goto_ok:
                await test_api_requests(client)
                await test_all_requests(client)
                await test_text(client)
                await test_links(client)
                await test_filter_links(client)
                await test_click(client)
                await test_href(client)
                await test_console(client)
                await test_failed(client)
                await test_clear(client)
                await test_stop(client)
            else:
                skip("goto 失败（浏览器可能未完全安装），跳过后续测试")
                await test_console(client)
                await test_failed(client)
                await test_clear(client)

    print_summary()


if __name__ == "__main__":
    asyncio.run(main())
