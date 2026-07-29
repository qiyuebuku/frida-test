"""财联社数据客户端 (www.cls.cn)"""

import hashlib
from urllib.parse import urlparse

import httpx

from src.infrastructure.clients.base import BaseClient, cached


class CLSClient(BaseClient):
    """财联社公开 Web 数据采集"""

    CLS_API = "https://www.cls.cn"
    CLS_WEB_VERSION = "8.7.9"
    REALTIME_CACHE_TTL_SECONDS = 30
    DEPTH_CACHE_TTL_SECONDS = 300
    HOT_ARTICLE_CACHE_TTL_SECONDS = 300
    ARTICLE_DETAIL_CACHE_TTL_SECONDS = 1209600

    def __init__(self, timeout: float = 10.0):
        # 使用干净的 httpx 客户端，不带任何默认 headers
        # follow_redirects=False: 财联社 API 开启后会被 302 到 HTML 页面
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            proxy=None,
        )

    @cached(ttl=REALTIME_CACHE_TTL_SECONDS, source="cls", domain="news", frequency="realtime", market="a_share", source_name="财联社电报")
    async def get_telegraph_list(self, rn: int = 20, last_time: int = None) -> list:
        """获取财联社电报快讯列表

        Args:
            rn: 返回条数（默认 20，最大 50）
            last_time: 游标，传入上一页最后一条的 ctime，返回更早的数据（用于翻页/增量采集）

        Returns:
            快讯列表，每条包含 id, title, brief, content, ctime, subjects, stock_list 等字段
            按 ctime 降序排列（最新的在前）
        """
        params: dict[str, str | int] = {
            "app": "CailianpressWeb",
            "os": "web",
            "rn": rn,
            "refresh_type": 1,
            "sv": self.CLS_WEB_VERSION,
        }
        if last_time is not None:
            params["last_time"] = last_time
        params["sign"] = self._sign(params)

        resp = await self._client.get(
            f"{self.CLS_API}/v1/roll/get_roll_list",
            params=params,
            headers={
                "Referer": "https://www.cls.cn/telegraph",
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json, text/plain, */*",
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("errno") != 0:
            raise RuntimeError(f"CLS get_roll_list failed: errno={payload.get('errno')} msg={payload.get('msg')}")
        return payload.get("data", {}).get("roll_data", [])

    @cached(ttl=DEPTH_CACHE_TTL_SECONDS, source="cls_depth", domain="news", frequency="realtime", market="a_share", source_name="财联社深度")
    async def get_depth_list(self, depth_id: int = 1000, rn: int = 20, last_time: int | None = None) -> list:
        """获取财联社深度文章列表。

        `https://www.cls.cn/depth?id=1000` 页面前端使用该接口加载头条/深度文章。
        与电报源不同，这里返回的是文章列表，正文详情需要单独详情接口；当前先以
        title/brief/metadata 入库，避免把列表摘要伪装成全文。

        Args:
            depth_id: 财联社深度栏目 ID，1000 是官网“深度/头条”主栏目。
            rn: 返回条数。
            last_time: 翻页游标，传上一页最后一条 ctime。
        """
        params: dict[str, str | int] = self._web_params({
            "rn": rn,
        })
        if last_time is not None:
            params["last_time"] = last_time
        params["sign"] = self._sign(params)

        resp = await self._client.get(
            f"{self.CLS_API}/v3/depth/list/{depth_id}",
            params=params,
            headers={
                "Referer": f"https://www.cls.cn/depth?id={depth_id}",
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json, text/plain, */*",
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("errno") != 0:
            raise RuntimeError(f"CLS depth list failed: errno={payload.get('errno')} msg={payload.get('msg')}")
        data = payload.get("data", [])
        return data if isinstance(data, list) else []

    @cached(ttl=DEPTH_CACHE_TTL_SECONDS, source="cls_depth", domain="news", frequency="realtime", market="a_share", source_name="财联社深度")
    async def get_depth_home_assembled(self, depth_id: int = 1000) -> dict:
        """获取财联社深度首页聚合数据。

        该接口包含 banner、top_article、depth_list 等模块，适合用于调研和后续扩展。
        正式增量采集优先使用 `get_depth_list()`，因为列表接口具备 last_time 游标。
        """
        params: dict[str, str | int] = self._web_params({})
        params["sign"] = self._sign(params)

        resp = await self._client.get(
            f"{self.CLS_API}/v3/depth/home/assembled/{depth_id}",
            params=params,
            headers={
                "Referer": f"https://www.cls.cn/depth?id={depth_id}",
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json, text/plain, */*",
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("errno") != 0:
            raise RuntimeError(f"CLS depth home failed: errno={payload.get('errno')} msg={payload.get('msg')}")
        data = payload.get("data", {})
        return data if isinstance(data, dict) else {}

    @cached(
        ttl=HOT_ARTICLE_CACHE_TTL_SECONDS,
        source="cls_hot_article",
        domain="news",
        frequency="realtime",
        market="a_share",
        source_name="财联社热门文章",
    )
    async def get_hot_article_list(self) -> list:
        """获取财联社“热门文章排行榜”。

        `https://www.cls.cn/depth?id=1000` 页面使用该接口展示热门文章。
        返回上游已排好榜单顺序的原始文章列表，字段包括 id、title、brief、
        img、ctime、readNum、author、stocks 等。
        """
        params: dict[str, str | int] = self._web_params({})
        params["sign"] = self._sign(params)

        resp = await self._client.get(
            f"{self.CLS_API}/v2/article/hot/list",
            params=params,
            headers={
                "Referer": "https://www.cls.cn/depth?id=1000",
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json, text/plain, */*",
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("errno") != 0:
            raise RuntimeError(f"CLS hot article list failed: errno={payload.get('errno')} msg={payload.get('msg')}")
        data = payload.get("data", [])
        return data if isinstance(data, list) else []

    @cached(
        ttl=ARTICLE_DETAIL_CACHE_TTL_SECONDS,
        source="cls_article",
        domain="news",
        frequency="realtime",
        market="a_share",
        source_name="财联社文章详情",
    )
    async def get_article_detail(self, article: int | str) -> dict:
        """获取财联社文章详情及完整正文。

        Args:
            article: 文章数字 ID，或 `https://www.cls.cn/detail/{id}` 详情 URL。

        Returns:
            财联社原始文章对象。正文 HTML 位于 content 字段，其余字段包括
            title、brief、ctime、readingNum、author、images、subject 等。
        """
        article_id = self._parse_article_id(article)
        params: dict[str, str | int] = {
            "app": 0,
            "id": article_id,
            "os": "web",
            "sv": self.CLS_WEB_VERSION,
        }
        params["sign"] = self._sign(params)

        resp = await self._client.get(
            f"{self.CLS_API}/articles/v1/detail",
            params=params,
            headers={
                "Referer": f"https://www.cls.cn/detail/{article_id}",
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json, text/plain, */*",
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            raise RuntimeError("CLS article detail failed: invalid response")
        if payload.get("errno") is not None:
            raise RuntimeError(f"CLS article detail failed: errno={payload.get('errno')} msg={payload.get('msg')}")
        if payload.get("id") != article_id or payload.get("status") != 1:
            raise RuntimeError(f"CLS article detail failed: article {article_id} not found or unavailable")
        return payload

    @classmethod
    def _parse_article_id(cls, article: int | str) -> int:
        """从数字 ID 或财联社详情 URL 中解析文章 ID。"""
        if isinstance(article, bool):
            raise ValueError("CLS article id must be a positive integer or detail URL")
        if isinstance(article, int):
            article_id = article
        elif isinstance(article, str):
            value = article.strip()
            if value.isdigit():
                article_id = int(value)
            else:
                parsed = urlparse(value)
                if parsed.hostname not in {"cls.cn", "www.cls.cn"}:
                    raise ValueError("CLS article URL must use cls.cn")
                path_parts = [part for part in parsed.path.split("/") if part]
                if len(path_parts) != 2 or path_parts[0] != "detail" or not path_parts[1].isdigit():
                    raise ValueError("CLS article URL must match https://www.cls.cn/detail/{id}")
                article_id = int(path_parts[1])
        else:
            raise ValueError("CLS article id must be a positive integer or detail URL")

        if article_id <= 0:
            raise ValueError("CLS article id must be a positive integer")
        return article_id

    @classmethod
    def _sign(cls, params: dict) -> str:
        """复刻财联社 Web 端签名：MD5(SHA1(sorted query string))."""
        sorted_query = cls._stringify_params(params)
        sha1 = hashlib.sha1(sorted_query.encode("utf-8")).hexdigest()
        return hashlib.md5(sha1.encode("utf-8")).hexdigest()

    def _web_params(self, params: dict) -> dict[str, str | int]:
        """财联社 Web 公共参数。"""
        return {
            "app": "CailianpressWeb",
            "os": "web",
            "sv": self.CLS_WEB_VERSION,
            **params,
        }

    @classmethod
    def _stringify_params(cls, params: dict) -> str:
        def stringify(key: str, value) -> str:
            if value is None:
                return ""
            if isinstance(value, (str, int, float, bool)):
                return f"{key}={value}"
            if isinstance(value, list):
                if not value:
                    return f"{key}[]"
                return "&".join(filter(None, (stringify(f"{key}[{idx}]", item) for idx, item in enumerate(value))))
            if isinstance(value, dict):
                return "&".join(filter(None, (stringify(f"{key}[{child_key}]", value[child_key]) for child_key in sorted(value))))
            return ""

        return "&".join(filter(None, (stringify(key, params[key]) for key in sorted(params))))

    @cached(ttl=REALTIME_CACHE_TTL_SECONDS, source="cls", domain="news", frequency="realtime", market="a_share", source_name="财联社电报")
    async def get_telegraph_since(self, since_ctime: int, max_pages: int = 5, page_size: int = 20) -> list:
        """增量采集：获取 since_ctime 之后的所有新快讯

        从最新数据开始向前翻页，直到遇到 ctime <= since_ctime 的数据或达到最大页数。

        Args:
            since_ctime: 上次采集的最后一条 ctime（只返回比这个更新的）
            max_pages: 最大翻页数（防止无限翻页）
            page_size: 每页条数

        Returns:
            since_ctime 之后的所有快讯（按 ctime 降序）
        """
        all_items = []
        last_time = None

        for _ in range(max_pages):
            items = await self.get_telegraph_list(rn=page_size, last_time=last_time)
            if not items:
                break

            for item in items:
                if item.get("ctime", 0) <= since_ctime:
                    # 遇到已采集的数据，停止
                    return all_items
                all_items.append(item)

            # 用最后一条的 ctime 作为下一页游标
            last_time = items[-1].get("ctime")

        return all_items

    async def get_depth_since(
        self,
        since_ctime: int,
        depth_id: int = 1000,
        max_pages: int = 5,
        page_size: int = 20,
    ) -> list:
        """增量采集财联社深度文章。

        深度列表不是严格按 ctime 单调排序，因此不能遇到第一条旧数据就停止；
        只有当前页全部不新于 since_ctime 时才结束。
        """
        all_items = []
        last_time = None

        for _ in range(max_pages):
            items = await self.get_depth_list(depth_id=depth_id, rn=page_size, last_time=last_time)
            if not items:
                break

            newer_items = [item for item in items if item.get("ctime", 0) > since_ctime]
            all_items.extend(newer_items)
            if not newer_items:
                return all_items

            last_time = items[-1].get("ctime")
            if last_time is None:
                break

        return all_items


if __name__ == "__main__":
    import os
    import asyncio

    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        os.environ.pop(key, None)

    async def main():
        client = CLSClient()
        try:
            # 测试基本采集
            items = await client.get_telegraph_list(rn=10)
            print(f"采集到 {len(items)} 条快讯\n")
            for item in items:
                title = item.get("title", "")
                tags = [s.get("subject_name") for s in (item.get("subjects") or []) if s.get("subject_name")]
                stocks = [s.get("name") for s in (item.get("stock_list") or [])]
                print(f"  {title[:70]}")
                if tags:
                    print(f"    主题: {tags}")
                if stocks:
                    print(f"    关联股: {stocks[:5]}")
                print()

            # 测试增量采集：用第 5 条的 ctime 模拟"上次采集到这里"
            if len(items) >= 5:
                since = items[4]["ctime"]
                new_items = await client.get_telegraph_since(since_ctime=since)
                print(f"--- 增量采集（since={since}）: 获取到 {len(new_items)} 条新数据 ---")
                for item in new_items:
                    print(f"  [{item['ctime']}] {item.get('title', '')[:50]}")
        finally:
            await client.close()

    asyncio.run(main())
