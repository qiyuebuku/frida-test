"""雪球数据客户端（xueqiu.com — 需 curl_cffi + spy cookie）

雪球使用阿里云 WAF + httpOnly cookie，纯 HTTP 请求被拦截。
破解方案：通过 spy 浏览器服务获取 httpOnly cookie，用 curl_cffi 模拟 Chrome TLS 指纹。
cookie 在首次请求时懒加载，缓存 1 小时，过期或失败时自动刷新重试。
"""

import time
import logging

from src.infrastructure.clients.base import BaseClient, cached

logger = logging.getLogger(__name__)

try:
    from curl_cffi import requests as cf_requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False


class XueqiuClient(BaseClient):
    """雪球数据客户端

    依赖：
    - curl_cffi: pip install curl_cffi（模拟 Chrome TLS 指纹）
    - spy 浏览器服务运行中（获取 httpOnly cookie）
    """

    SPY_URL = ""
    XQ_BASE = "https://xueqiu.com"
    XQ_STOCK = "https://stock.xueqiu.com"

    HEADERS = {
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://xueqiu.com/",
        "Origin": "https://xueqiu.com",
    }

    def __init__(self, timeout: float = 15.0):
        super().__init__(timeout)
        from src.infrastructure.config.settings import SERVICE_BASE_URL
        self.SPY_URL = f"{SERVICE_BASE_URL}/api/spy"
        self._cookie_str: str = ""
        self._cookie_time: float = 0
        self._cookie_ttl: float = 3600  # cookie 缓存 1 小时
        self._cf_session = None

    # ==================== cookie 管理 ====================

    def _get_session(self):
        if not CURL_CFFI_AVAILABLE:
            raise RuntimeError("curl_cffi 未安装，请: pip install curl_cffi")
        if self._cf_session is None:
            self._cf_session = cf_requests.Session(impersonate="chrome120")
        return self._cf_session

    async def _ensure_cookies(self) -> str:
        """懒加载 cookie：首次调用时从 spy 获取，之后缓存复用"""
        now = time.time()
        if self._cookie_str and (now - self._cookie_time) < self._cookie_ttl:
            return self._cookie_str

        # 从 spy 获取 cookie
        try:
            resp = await self._client.get(
                f"{self.SPY_URL}/cookies", params={"domain": "xueqiu"}, timeout=10)
            cookies = resp.json().get("cookies", [])
        except Exception:
            cookies = []

        # 如果没有 xq_a_token，让 spy 访问雪球首页通过 WAF
        if not any(c.get("name") == "xq_a_token" for c in cookies):
            logger.info("xq_a_token 不存在，让 spy 访问雪球首页...")
            try:
                await self._client.post(
                    f"{self.SPY_URL}/goto",
                    json={"url": "https://xueqiu.com/", "wait_after": 10},
                    timeout=30,
                )
                resp = await self._client.get(
                    f"{self.SPY_URL}/cookies", params={"domain": "xueqiu"}, timeout=10)
                cookies = resp.json().get("cookies", [])
            except Exception as e:
                logger.warning(f"spy 访问雪球失败: {e}")
                return ""

        if not cookies:
            return ""

        parts = [f'{c["name"]}={c["value"]}' for c in cookies if "xueqiu.com" in c.get("domain", "")]
        self._cookie_str = "; ".join(parts)
        self._cookie_time = now
        logger.info(f"雪球 cookie 已刷新，长度={len(self._cookie_str)}")
        return self._cookie_str

    def _invalidate_cookies(self):
        self._cookie_str = ""
        self._cookie_time = 0

    # ==================== 通用请求（自动重试） ====================

    async def _xq_get(self, url: str) -> dict:
        """通用雪球请求：获取 cookie → 请求 → 失败则刷新 cookie 重试一次

        Returns:
            成功: {"status_code": 0, "raw": {原始JSON}}
            失败: {"status_code": -1, "msg": "错误信息"}
        """
        cookie = await self._ensure_cookies()
        if not cookie:
            return {"status_code": -1, "msg": "无法获取雪球 cookie，请确保 spy 浏览器服务运行中"}

        try:
            raw = self._get_session().get(url, headers={**self.HEADERS, "Cookie": cookie}, timeout=15).json()
        except Exception as e:
            return {"status_code": -1, "msg": f"请求失败: {e}"}

        # cookie 过期 → 刷新一次重试
        if raw.get("error_code"):
            logger.info(f"雪球请求失败({raw.get('error_code')}), 刷新 cookie 重试...")
            self._invalidate_cookies()
            cookie = await self._ensure_cookies()
            if not cookie:
                return {"status_code": -1, "msg": "cookie 刷新失败"}
            try:
                raw = self._get_session().get(url, headers={**self.HEADERS, "Cookie": cookie}, timeout=15).json()
            except Exception as e:
                return {"status_code": -1, "msg": f"重试失败: {e}"}
            if raw.get("error_code"):
                return {"status_code": -1, "msg": raw.get("error_description", str(raw.get("error_code")))}

        return {"status_code": 0, "raw": raw}

    # ==================== 业务方法 ====================

    @cached(ttl=1800, source="xueqiu", domain="sentiment", frequency="realtime", market="a_share", source_name="雪球")
    async def get_hot_topics(self, count: int = 10) -> dict:
        """雪球热门话题

        Returns:
            {"status_code": 0, "data": {"count": N, "topics": [...]}}
        """
        r = await self._xq_get(f"{self.XQ_BASE}/hot_event/list.json?count={count}")
        if r["status_code"] != 0:
            return {**r, "data": {}}

        topics = []
        for item in r["raw"].get("list", []):
            topics.append({
                "id": item.get("id"),
                "tag": (item.get("tag") or "").replace("#", ""),
                "content": item.get("content", ""),
                "discussions": item.get("status_count", 0),
                "hot": item.get("hot", 0),
                "pic": item.get("pic", ""),
            })
        return {"status_code": 0, "data": {"count": len(topics), "topics": topics}}

    @cached(ttl=1800, source="xueqiu", domain="sentiment", frequency="realtime", market="a_share", source_name="雪球")
    async def get_hot_topics_since(self, since_id: int) -> list:
        """增量采集热门话题：只返回 id > since_id 的话题"""
        r = await self.get_hot_topics(count=20)
        if r["status_code"] != 0:
            return []
        return [t for t in r["data"]["topics"] if (t.get("id") or 0) > since_id]

    @cached(ttl=1800, source="xueqiu", domain="sentiment", frequency="realtime", market="a_share", source_name="雪球")
    async def get_hot_stocks(self, size: int = 10) -> dict:
        """雪球热股排行

        Returns:
            {"status_code": 0, "data": {"count": N, "stocks": [...]}}
        """
        r = await self._xq_get(f"{self.XQ_STOCK}/v5/stock/hot_stock/list.json?size={size}&_type=10&type=10")
        if r["status_code"] != 0:
            return {**r, "data": {}}

        stocks = []
        for item in r["raw"].get("data", {}).get("items", []):
            stocks.append({
                "name": item.get("name", ""),
                "code": item.get("code", ""),
                "price": item.get("current"),
                "percent": item.get("percent"),
                "chg": item.get("chg"),
                "market_capital": item.get("market_capital"),
            })
        return {"status_code": 0, "data": {"count": len(stocks), "stocks": stocks}}

    @cached(ttl=300, source="xueqiu", domain="news", frequency="realtime", market="a_share", source_name="雪球")
    async def get_live_news(self, count: int = 20, max_id: int = -1) -> dict:
        """雪球 7×24 快讯

        Returns:
            {"status_code": 0, "data": {"count": N, "next_max_id": N, "items": [...]}}
        """
        r = await self._xq_get(
            f"{self.XQ_BASE}/statuses/livenews/list.json?since_id=-1&max_id={max_id}&count={count}")
        if r["status_code"] != 0:
            return {**r, "data": {}}

        items = [
            {"id": item.get("id"), "text": item.get("text", ""), "created_at": item.get("created_at")}
            for item in r["raw"].get("items", [])
        ]
        return {"status_code": 0, "data": {
            "count": len(items), "next_max_id": r["raw"].get("next_max_id"), "items": items,
        }}

    @cached(ttl=300, source="xueqiu", domain="news", frequency="realtime", market="a_share", source_name="雪球")
    async def get_live_news_since(self, since_id: int, count: int = 50) -> list:
        """增量采集快讯：只返回 id > since_id 的快讯"""
        r = await self.get_live_news(count=count)
        if r["status_code"] != 0:
            return []
        return [item for item in r["data"]["items"] if (item.get("id") or 0) > since_id]


if __name__ == "__main__":
    import os
    import asyncio

    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        os.environ.pop(key, None)

    async def main():
        client = XueqiuClient()
        try:
            r = await client.get_hot_topics(count=5)
            if r["status_code"] == 0:
                print(f"1. 热门话题: {r['data']['count']} 条")
                for t in r["data"]["topics"]:
                    print(f"   [{t['discussions']}讨论] {t['tag']}")
            else:
                print(f"1. 失败: {r.get('msg')}")

            print()
            r = await client.get_hot_stocks(size=5)
            if r["status_code"] == 0:
                print(f"2. 热股: {r['data']['count']} 只")
                for s in r["data"]["stocks"]:
                    print(f"   {s['name']} ({s['code']}) 价格={s['price']} 涨跌%={s['percent']}")
            else:
                print(f"2. 失败: {r.get('msg')}")

            print()
            r = await client.get_live_news(count=5)
            if r["status_code"] == 0:
                print(f"3. 快讯: {r['data']['count']} 条")
                for item in r["data"]["items"]:
                    print(f"   {item['text'][:60]}")
            else:
                print(f"3. 失败: {r.get('msg')}")
        finally:
            await client.close()

    asyncio.run(main())
