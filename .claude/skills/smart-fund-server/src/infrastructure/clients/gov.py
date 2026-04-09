"""政府网站政策公告客户端"""

import re
import httpx

from src.infrastructure.clients.base import BaseClient, cached


# 政策级别推断规则
_LEVEL_KEYWORDS = {
    "国务院": ["国务院令", "国务院关于", "国务院办公厅", "国务院批复", "国务院通知"],
    "中央": ["中共中央", "中央办公厅", "中办", "国办"],
    "部委": ["部令", "部关于", "部办公厅", "公告", "通报", "规定", "办法", "条例"],
}


def _infer_level(title: str, source: str) -> str:
    """根据标题和来源推断政策级别"""
    if source == "gov":
        for kw in _LEVEL_KEYWORDS["中央"]:
            if kw in title:
                return "中央"
        for kw in _LEVEL_KEYWORDS["国务院"]:
            if kw in title:
                return "国务院"
        return "国务院"
    return "部委"


class GovClient(BaseClient):
    """政府网站政策公告采集（政府网/发改委/证监会/工信部，人民银行见 PBOCClient）"""

    SOURCES = {
        # ===== 政府网 =====
        "gov": {
            "name": "中国政府网",
            "list_url": "https://www.gov.cn/pushinfo/v150203/",
            "parser": "_parse_gov",
        },
        # ===== 发改委 =====
        "ndrc": {
            "name": "发改委·新闻发布",
            "list_url": "https://www.ndrc.gov.cn/xwdt/xwfb/",
            "base_url": "https://www.ndrc.gov.cn/xwdt/xwfb",
            "parser": "_parse_ndrc",
        },
        "ndrc_decree": {
            "name": "发改委·发改委令",
            "list_url": "https://www.ndrc.gov.cn/xxgk/zcfb/fzggwl/",
            "base_url": "https://www.ndrc.gov.cn/xxgk/zcfb/fzggwl",
            "parser": "_parse_ndrc",
        },
        "ndrc_notice": {
            "name": "发改委·通知",
            "list_url": "https://www.ndrc.gov.cn/xxgk/zcfb/tz/",
            "base_url": "https://www.ndrc.gov.cn/xxgk/zcfb/tz",
            "parser": "_parse_ndrc",
        },
        "ndrc_plan": {
            "name": "发改委·规划文件",
            "list_url": "https://www.ndrc.gov.cn/xxgk/zcfb/ghxwj/",
            "base_url": "https://www.ndrc.gov.cn/xxgk/zcfb/ghxwj",
            "parser": "_parse_ndrc",
        },
        # ===== 证监会 =====
        "csrc": {
            "name": "证监会·要闻",
            "list_url": "https://www.csrc.gov.cn/searchList/a1a078ee0bc54721ab6b148884c784a8",
            "parser": "_parse_csrc",
        },
        "csrc_interpret": {
            "name": "证监会·政策解读",
            "list_url": "https://www.csrc.gov.cn/searchList/625e0ed8a45842c28751458bfcacb422",
            "parser": "_parse_csrc",
        },
        "csrc_press": {
            "name": "证监会·新闻发布会",
            "list_url": "https://www.csrc.gov.cn/csrc/c100029/common_list.shtml?channelid=c100029",
            "parser": "_parse_csrc_press",
        },
        "csrc_penalty": {
            "name": "证监会·失信处罚",
            "list_url": "https://neris.csrc.gov.cn/shixinchaxun/sxcx/PublicData/specialPublic",
            "parser": "_parse_csrc_penalty",
        },
        # ===== 工信部 =====
        "miit": {
            "name": "工信部·司局动态",
            "list_url": "https://www.miit.gov.cn/api-gateway/jpaas-publish-server/front/page/build/unit",
            "base_url": "https://www.miit.gov.cn",
            "parser": "_parse_miit",
            "page_id": "028da85b0dbd4c9cb96fd5f421cd32b8",
        },
        "miit_policy": {
            "name": "工信部·政策文件",
            "list_url": "https://www.miit.gov.cn/api-gateway/jpaas-publish-server/front/page/build/unit",
            "base_url": "https://www.miit.gov.cn",
            "parser": "_parse_miit",
            "page_id": "3e3ad1a3bec74939890a0d3e54815141",
        },
        "miit_standard": {
            "name": "工信部·技术标准",
            "list_url": "https://std.miit.gov.cn/kjsStandproject/front/zxd/stand/queryFullDisclosureStandards",
            "parser": "_parse_miit_standard",
        },
    }

    def __init__(self, timeout: float = 15.0):
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            proxy=None,
        )

    @cached(ttl=3600, source="gov", domain="news", frequency="daily", market="macro", source_name="政府网站")
    async def get_announcements(self, source: str, limit: int = 20, fetch_content: bool = False) -> list:
        """获取指定来源的最新公告列表

        Args:
            source: 数据源标识
            limit: 返回条数
            fetch_content: 是否同时获取每条的正文内容（会增加请求数，建议增量采集时开启）

        Returns:
            [{title, content, source, level, url, published_at, source_name}, ...]
        """
        config = self.SOURCES.get(source)
        if not config:
            return []
        items = await self._fetch_via_http(config, source, limit)

        if fetch_content:
            import asyncio
            # 并发获取正文（最多 5 个并发，避免被封）
            semaphore = asyncio.Semaphore(5)

            async def _fill_content(item):
                if not item.get("url"):
                    return
                async with semaphore:
                    item["content"] = await self.get_content(item["url"])

            await asyncio.gather(*[_fill_content(item) for item in items])

        return items

    @cached(ttl=3600, source="gov", domain="news", frequency="daily", market="macro", source_name="政府网站")
    async def get_announcements_since(self, source: str, since_date: str, limit: int = 50) -> list:
        """增量采集：只获取 since_date 之后的公告"""
        all_items = await self.get_announcements(source, limit=limit)
        return [item for item in all_items if item.get("published_at", "") > since_date]

    # 各网站正文区域的标记（用于定位正文起始位置）
    _CONTENT_MARKERS = {
        "gov.cn": ['class="pages_content"', 'id="UCAP-CONTENT"'],
        "ndrc.gov.cn": ['class=TRS_Editor', 'class="TRS_Editor"'],
        "csrc.gov.cn": ['class="detail-news"'],
        "miit.gov.cn": ['id="con_con"', 'class="ccontent'],
    }

    @cached(ttl=86400, source="gov", domain="news", frequency="daily", market="macro", source_name="政府网站")
    async def get_content(self, url: str) -> str:
        """获取公告详情页的完整正文

        Args:
            url: 公告详情页 URL

        Returns:
            正文纯文本（完整，不截断）
        """
        try:
            resp = await self._client.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            )
            resp.raise_for_status()
            html = resp.text

            # 尝试按网站提取正文区域
            content_html = None
            for domain, markers in self._CONTENT_MARKERS.items():
                if domain in url:
                    for marker in markers:
                        idx = html.find(marker)
                        if idx >= 0:
                            # 找到标记所在 div 的起始 <div
                            div_start = html.rfind('<div', 0, idx)
                            if div_start >= 0:
                                content_html = html[div_start:]
                            break
                    break

            if not content_html:
                content_html = html

            # 去掉 script/style
            content_html = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', content_html, flags=re.DOTALL | re.IGNORECASE)
            # HTML → 纯文本
            text = re.sub(r'<br\s*/?>', '\n', content_html)
            text = re.sub(r'<p[^>]*>', '\n', text)
            text = re.sub(r'<[^>]+>', '', text)
            text = text.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
            text = re.sub(r'[ \t]+', ' ', text)
            text = re.sub(r'\n{3,}', '\n\n', text)
            return text.strip()
        except Exception:
            return ""

    async def _fetch_via_http(self, config: dict, source: str, limit: int) -> list:
        """通过 HTTP 请求获取"""
        parser = getattr(self, config["parser"])

        if source == "csrc_penalty":
            resp = await self._client.post(
                config["list_url"],
                json={"obj_type": "2"},
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            )
            resp.raise_for_status()
            return parser(resp.json(), config, source, limit)
        elif source == "miit_standard":
            resp = await self._client.post(
                config["list_url"],
                data={
                    "piHyid": "", "hyName": "", "userCode": "游客", "userId": "",
                    "sourceType": "more", "stadardNum": "", "siZbzname": "",
                    "piProjectname": "", "bpiJysstime": "", "bpiJysstimeEnd": "",
                    "approvalDate": "", "approvalDateEnd": "",
                    "pageNum": "1", "pageSize": str(limit),
                },
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": "https://std.miit.gov.cn/",
                },
            )
            resp.raise_for_status()
            return parser(resp.json(), config, source, limit)
        elif source.startswith("miit"):
            resp = await self._client.get(
                config["list_url"],
                params={
                    "parseType": "buildstatic",
                    "webId": "8d828e408d90447786ddbe128d495e9e",
                    "tplSetId": "209741b2109044b5b7695700b2bec37e",
                    "pageType": "column",
                    "tagId": "右侧内容",
                    "editType": "null",
                    "pageId": config.get("page_id", "028da85b0dbd4c9cb96fd5f421cd32b8"),
                },
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": "https://www.miit.gov.cn/xwdt/gxdt/sjdt/",
                },
            )
            resp.raise_for_status()
            return parser(resp.json(), config, source, limit)
        elif source.startswith("csrc") and config["parser"] == "_parse_csrc":
            resp = await self._client.get(
                config["list_url"],
                params={
                    "_isAgg": "true", "_isJson": "true",
                    "_pageSize": str(limit), "_template": "index",
                    "_rangeTimeGte": "", "_channelName": "", "page": "1",
                },
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": "https://www.csrc.gov.cn/csrc/c100028/common_xq_list.shtml",
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "*/*",
                },
            )
            resp.raise_for_status()
            return parser(resp.json(), config, source, limit)
        else:
            resp = await self._client.get(
                config["list_url"],
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "text/html",
                },
            )
            resp.raise_for_status()
            return parser(resp.text, config, source, limit)

    # ==================== 解析器 ====================

    def _make_item(self, title: str, url: str, date_str: str, source: str, source_name: str) -> dict:
        """构造标准返回项（统一添加 level 和 content 占位）"""
        return {
            "title": title,
            "content": "",  # 列表页无正文，需调用 get_content(url) 获取
            "source": source,
            "level": _infer_level(title, source),
            "url": url,
            "published_at": date_str,
            "source_name": source_name,
        }

    def _parse_gov(self, html: str, config: dict, source: str, limit: int) -> list:
        """解析中国政府网 pushinfo 政策列表页"""
        pattern = re.compile(
            r'<li[^>]*>.*?<a[^>]*href="(https://www\.gov\.cn/[^"]+)"[^>]*target="_blank"[^>]*>\s*([^<]+?)\s*</a>'
            r'.*?(\d{4}-\d{2}-\d{2}).*?</li>',
            re.DOTALL,
        )
        items = []
        for match in pattern.finditer(html):
            url, title, date_str = match.group(1), match.group(2).strip(), match.group(3)
            if not title or title in ("首页", "更多") or len(title) < 5:
                continue
            items.append(self._make_item(title, url, date_str, source, config["name"]))
            if len(items) >= limit:
                break
        return items

    def _parse_ndrc(self, html: str, config: dict, source: str, limit: int) -> list:
        """解析发改委新闻发布列表页"""
        pattern = re.compile(
            r'<li[^>]*>\s*'
            r'<a[^>]*href="(\./[^"]+)"[^>]*>\s*([^<]+?)\s*</a>'
            r'.*?<span[^>]*>\s*(\d{4}/\d{2}/\d{2})\s*</span>'
            r'.*?</li>',
            re.DOTALL,
        )
        items = []
        for match in pattern.finditer(html):
            rel_url, title, date_str = match.group(1), match.group(2).strip(), match.group(3)
            if not title or title in ("首页", "更多"):
                continue
            full_url = rel_url.replace("./", f"{config['base_url']}/", 1)
            items.append(self._make_item(title, full_url, date_str.replace("/", "-"), source, config["name"]))
            if len(items) >= limit:
                break
        return items

    def _parse_csrc(self, json_data: dict, config: dict, source: str, limit: int) -> list:
        """解析证监会 JSON API 响应"""
        results = json_data.get("data", {}).get("results", [])
        items = []
        for r in results:
            title = r.get("title", "").strip()
            if not title:
                continue
            url = r.get("url", "")
            if url and not url.startswith("http"):
                url = f"https:{url}"
            items.append(self._make_item(title, url, (r.get("publishedTimeStr") or "")[:10], source, config["name"]))
            if len(items) >= limit:
                break
        return items

    def _parse_csrc_press(self, html: str, config: dict, source: str, limit: int) -> list:
        """解析证监会新闻发布会静态列表页"""
        pattern = re.compile(
            r'<a[^>]*href="(/csrc/c100029/[^"]+)"[^>]*>([^<]*\d{4}年[^<]*发布会[^<]*)</a>',
        )
        items = []
        for match in pattern.finditer(html):
            rel_url, title = match.group(1), match.group(2).strip()
            if not title:
                continue
            # 从标题中提取日期
            date_match = re.search(r'(\d{4})年(\d+)月(\d+)日', title)
            date_str = f"{date_match.group(1)}-{int(date_match.group(2)):02d}-{int(date_match.group(3)):02d}" if date_match else ""
            url = f"https://www.csrc.gov.cn{rel_url}"
            items.append(self._make_item(title, url, date_str, source, config["name"]))
            if len(items) >= limit:
                break
        return items

    def _parse_miit(self, json_data: dict, config: dict, source: str, limit: int) -> list:
        """解析工信部 JSON API 响应"""
        html = json_data.get("data", {}).get("html", "")
        pattern = re.compile(
            r'<a[^>]*href="([^"]+)"[^>]*title="([^"]+)"[^>]*>.*?</a>.*?(\d{4}-\d{2}-\d{2})',
            re.DOTALL,
        )
        items = []
        for match in pattern.finditer(html):
            rel_url, title, date_str = match.group(1), match.group(2).strip(), match.group(3)
            if not title or len(title) < 5:
                continue
            url = rel_url if rel_url.startswith("http") else f"{config['base_url']}{rel_url}"
            items.append(self._make_item(title, url, date_str, source, config["name"]))
            if len(items) >= limit:
                break
        return items

    def _parse_csrc_penalty(self, json_data: dict, config: dict, source: str, limit: int) -> list:
        """解析证监会失信处罚 JSON API"""
        items_raw = json_data.get("data", [])
        items = []
        for r in items_raw:
            name = r.get("obj_name", "").strip()
            if not name:
                continue
            items.append(self._make_item(
                title=name,
                url="https://neris.csrc.gov.cn/shixinchaxun/",
                date_str="",
                source=source,
                source_name=config["name"],
            ))
            if len(items) >= limit:
                break
        return items

    def _parse_miit_standard(self, json_data: dict, config: dict, source: str, limit: int) -> list:
        """解析工信部技术标准 JSON API"""
        items_raw = json_data.get("data", [])
        items = []
        for r in items_raw:
            std_no = r.get("bpiBzno", "")
            name = r.get("piProjectname", "")
            date_str = (r.get("bpiJysstime") or "")[:10]
            if not name:
                continue
            title = f"{std_no} {name}".strip() if std_no else name
            items.append(self._make_item(title, "", date_str, source, config["name"]))
            if len(items) >= limit:
                break
        return items


if __name__ == "__main__":
    import os
    import asyncio

    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        os.environ.pop(key, None)

    async def main():
        client = GovClient()
        try:
            # 只测前 4 个栏目（含正文），其余只测列表
            detail_sources = ["gov", "ndrc", "csrc", "miit"]

            for source in client.SOURCES:
                name = client.SOURCES[source]["name"]
                fetch = source in detail_sources
                print(f"{name}:")

                try:
                    items = await client.get_announcements(source, limit=2, fetch_content=fetch)
                    print(f"  采集到 {len(items)} 条\n")
                    for item in items[:2]:
                        print(f"  [{item['published_at']}] [{item['level']}] {item['title'][:55]}")
                        print(f"    url: {item['url']}")
                        if item.get("content"):
                            print(f"    正文: {len(item['content'])}字 | {item['content'][:60]}...")
                    print()

                except Exception as e:
                    print(f"  失败: {e}\n")
        finally:
            await client.close()

    asyncio.run(main())
