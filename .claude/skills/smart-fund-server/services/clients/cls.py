"""财联社数据客户端 (www.cls.cn)"""

import httpx

from services.clients.base import BaseClient, cached


class CLSClient(BaseClient):
    """财联社电报快讯采集"""

    CLS_API = "https://www.cls.cn/nodeapi"

    def __init__(self, timeout: float = 10.0):
        # 使用干净的 httpx 客户端，不带任何默认 headers
        # follow_redirects=False: 财联社 API 开启后会被 302 到 HTML 页面
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            proxy=None,
        )

    @cached(ttl=300, source="cls", domain="news", frequency="realtime", market="a_share", source_name="财联社")
    async def get_telegraph_list(self, rn: int = 20, last_time: int = None) -> list:
        """获取财联社电报快讯列表

        Args:
            rn: 返回条数（默认 20，最大 50）
            last_time: 游标，传入上一页最后一条的 ctime，返回更早的数据（用于翻页/增量采集）

        Returns:
            快讯列表，每条包含 id, title, brief, content, ctime, subjects, stock_list 等字段
            按 ctime 降序排列（最新的在前）
        """
        params = {
            "app": "CailianpressWeb",
            "os": "web",
            "rn": rn,
            "refresh_type": 1,
            "order": 1,
            "sv": "8.4.6",
        }
        if last_time is not None:
            params["lastTime"] = last_time

        resp = await self._client.get(
            f"{self.CLS_API}/telegraphList",
            params=params,
            headers={
                "Referer": "https://www.cls.cn/telegraph",
                "User-Agent": "Mozilla/5.0",
                "Accept": "*/*",
            },
        )
        resp.raise_for_status()
        return resp.json().get("data", {}).get("roll_data", [])

    @cached(ttl=300, source="cls", domain="news", frequency="realtime", market="a_share", source_name="财联社")
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
