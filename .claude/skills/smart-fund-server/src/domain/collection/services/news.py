"""新闻事件聚合 (P0)

数据源: 财联社电报、财联社深度、政府网站、人民银行(OMO+货币政策)、
        东方财富(资讯+研报)、同花顺资讯、新浪财经、雪球快讯
目标表: ft_news
"""

import hashlib
import json
import logging
from datetime import datetime, timezone, timedelta

from src.domain.collection.services.base import BaseAggregator, SourceDef

logger = logging.getLogger(__name__)

TZ_CST = timezone(timedelta(hours=8))

# 政府网站采集的部委列表（按重要性排序）
GOV_DEPARTMENTS = [
    "miit",          # 工信部·司局动态（验证可用）
    "miit_policy",   # 工信部·政策文件
    "ndrc",          # 发改委·新闻发布
    "ndrc_notice",   # 发改委·通知
    "csrc",          # 证监会
    "csrc_press",    # 证监会·新闻发布
    "gov",           # 中国政府网（可能无效，作为兜底）
]


async def _fetch_with_content(fetch_list_coro, client_with_get_content, max_fetch: int = 30) -> list:
    """通用辅助函数：先拉列表，再并发抓详情页正文

    Args:
        fetch_list_coro: 返回列表数据的 coroutine
        client_with_get_content: 有 get_content(url) 方法的 client 实例
        max_fetch: 最多抓取的详情数量
    """
    import asyncio as _asyncio
    items = await fetch_list_coro
    if not isinstance(items, list) or not items:
        return items
    to_fetch = [item for item in items if isinstance(item, dict) and item.get("url")][:max_fetch]
    if not to_fetch:
        return items
    tasks = [client_with_get_content.get_content(item["url"]) for item in to_fetch]
    contents = await _asyncio.gather(*tasks, return_exceptions=True)
    for item, c in zip(to_fetch, contents):
        if isinstance(c, str) and c:
            item["content"] = c
    return items


async def _fetch_gov_all_depts(gov_client, checkpoint=None) -> list:
    """遍历多部委并发采集政府网站公告，合并去重，并并发抓取每条的详情正文"""
    import asyncio as _asyncio
    # 第一阶段：并发拉列表
    list_tasks = []
    for dept in GOV_DEPARTMENTS:
        if checkpoint:
            list_tasks.append(gov_client.get_announcements_since(dept, checkpoint))
        else:
            list_tasks.append(gov_client.get_announcements(dept))
    list_results = await _asyncio.gather(*list_tasks, return_exceptions=True)

    merged = []
    seen_urls = set()
    for r in list_results:
        if isinstance(r, Exception) or not isinstance(r, list):
            continue
        for item in r:
            if not isinstance(item, dict):
                continue
            url = item.get("url") or ""
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            merged.append(item)

    # 第二阶段：分批并发抓详情正文（每批 10 条，避免一次打爆）
    to_fetch = [item for item in merged if item.get("url")]
    batch_size = 10
    for i in range(0, len(to_fetch), batch_size):
        batch = to_fetch[i:i + batch_size]
        tasks = [gov_client.get_content(item["url"]) for item in batch]
        contents = await _asyncio.gather(*tasks, return_exceptions=True)
        for item, c in zip(batch, contents):
            if isinstance(c, str) and c:
                item["content"] = c
    return merged


# ==================== 工具函数 ====================


def _fingerprint(title: str, source: str) -> str:
    return hashlib.sha256(f"{title}{source}".encode()).hexdigest()


def _ts_to_iso(ts: int | float) -> str:
    """Unix 时间戳 → ISO 8601"""
    return datetime.fromtimestamp(ts, tz=TZ_CST).isoformat()


def _iso_to_timestamp(value: str) -> int:
    """ISO 8601 时间 → Unix 秒级时间戳。

    采集 checkpoint 的 newest_time 带有时分秒；增量请求必须保留精确时间，
    不能只截取日期，否则会反复拉取当天全量数据并被 fingerprint 去重。
    """
    if not value:
        return 0
    text = str(value).strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.strptime(text[:10], "%Y-%m-%d").replace(tzinfo=TZ_CST)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_CST)
    return int(dt.timestamp())


def _date_to_iso(date_str: str) -> str:
    """日期字符串 → ISO 8601（保留时分秒）

    支持格式：
      YYYY-MM-DD                   → YYYY-MM-DDT00:00:00+08:00
      YYYY-MM-DD HH:MM:SS          → YYYY-MM-DDTHH:MM:SS+08:00
      YYYY-MM-DD HH:MM:SS.sss      → YYYY-MM-DDTHH:MM:SS+08:00
      YYYY-MM-DDTHH:MM:SS+08:00    → 原样返回
    """
    if not date_str:
        return ""
    s = str(date_str).strip()
    if "T" in s:
        return s
    # 去掉毫秒
    if "." in s:
        s = s.split(".")[0]
    # "2026-04-10 15:33:41" → "2026-04-10T15:33:41+08:00"
    if " " in s and len(s) >= 19:
        return f"{s[:10]}T{s[11:19]}+08:00"
    # 只有日期
    return f"{s[:10]}T00:00:00+08:00"


def _classify_by_keywords(text: str) -> str:
    """根据关键词简单分类"""
    text = (text or "").lower()
    if any(k in text for k in ("央行", "利率", "降息", "降准", "lpr", "shibor", "货币", "逆回购", "mlf")):
        return "macro"
    if any(k in text for k in ("政策", "法规", "条例", "意见", "通知", "公告", "部委")):
        return "policy"
    if any(k in text for k in ("公司", "股份", "上市", "业绩", "财报", "盈利")):
        return "company"
    if any(k in text for k in ("行业", "板块", "产业", "新能源", "半导体", "AI", "芯片")):
        return "industry"
    return ""


# ==================== Normalize 函数 ====================


def normalize_cls(raw_items: list) -> list[dict]:
    """财联社电报快讯 → 统一格式"""
    results = []
    for item in raw_items:
        title = item.get("title") or item.get("brief") or ""
        if not title:
            continue
        content = item.get("content") or ""
        stocks = []
        for s in (item.get("stock_list") or []):
            code = s.get("code") or s.get("stock_code") or ""
            if code:
                stocks.append(code)
        tags = [sub.get("name", "") for sub in (item.get("subjects") or []) if sub.get("name")]
        # 财联社电报：content 即是正文，摘要可用 brief 或 title
        results.append({
            "title": title,
            "summary": item.get("brief") or title[:100],
            "content": content,
            "source": "cls",
            "source_name": "财联社电报",
            "source_reliability": 0.85,
            "category": _classify_by_keywords(title + content),
            "url": "",
            "tags": tags,
            "related_stocks": stocks,
            "published_at": _ts_to_iso(item.get("ctime", 0)),
            "fingerprint": _fingerprint(title, "cls"),
        })
    return results


def normalize_cls_depth(raw_items: list) -> list[dict]:
    """财联社深度文章列表 → 统一格式"""
    results = []
    for item in raw_items:
        title = item.get("title") or ""
        if not title:
            continue
        item_id = item.get("id") or item.get("article_id") or ""
        brief = item.get("brief") or ""
        content = item.get("content") or brief

        tags = []
        for tag in item.get("article_tag") or []:
            name = tag.get("name") or ""
            if name:
                tags.append(name)
        for subject in item.get("subjects") or []:
            name = subject.get("subject_name") or subject.get("name") or ""
            if name:
                tags.append(name)
        subject = item.get("subjectStib") or {}
        if isinstance(subject, dict) and subject.get("name"):
            tags.append(subject["name"])

        related_stocks = []
        stocks = item.get("stocks") or ""
        if isinstance(stocks, str) and stocks:
            related_stocks.extend([s.strip() for s in stocks.split(",") if s.strip()])

        external_link = item.get("external_link") or ""
        url = external_link or (f"https://www.cls.cn/detail/{item_id}" if item_id else "")

        results.append({
            "title": title,
            "summary": brief or title[:100],
            "content": content,
            "source": "cls_depth",
            "source_name": "财联社深度",
            "source_reliability": 0.85,
            "category": _classify_by_keywords(title + brief + content),
            "url": url,
            "tags": list(dict.fromkeys(tags)),
            "related_stocks": related_stocks,
            "published_at": _ts_to_iso(item.get("ctime", 0)),
            "fingerprint": _fingerprint(f"{item_id}:{title}", "cls_depth"),
        })
    return results


def normalize_gov(raw_items: list) -> list[dict]:
    """政府网站 → 统一格式

    gov client 的列表页只有标题和 url，content 字段为空
    """
    results = []
    for item in raw_items:
        title = item.get("title") or ""
        if not title:
            continue
        source_id = item.get("source") or "gov"
        content = item.get("content") or ""
        results.append({
            "title": title,
            "summary": title[:100],  # 政府公告通常只有标题，summary 用标题截断
            "content": content,
            "source": source_id,
            "source_name": item.get("source_name") or "政府网站",
            "source_reliability": 0.95,
            "category": "policy",
            "url": item.get("url") or "",
            "tags": [],
            "related_stocks": [],
            "published_at": _date_to_iso(item.get("published_at") or ""),
            "fingerprint": _fingerprint(title, source_id),
        })
    return results


def normalize_pboc(raw_items: list) -> list[dict]:
    """人民银行 → 统一格式（OMO + 货币政策共用）"""
    results = []
    for item in raw_items:
        title = item.get("title") or ""
        if not title:
            continue
        content = item.get("content") or ""
        results.append({
            "title": title,
            "summary": content[:200] if content else title[:100],
            "content": content,
            "source": "pboc",
            "source_name": "中国人民银行",
            "source_reliability": 0.95,
            "category": "macro",
            "url": item.get("url") or "",
            "tags": [],
            "related_stocks": [],
            "published_at": _date_to_iso(item.get("published_at") or ""),
            "fingerprint": _fingerprint(title, "pboc"),
        })
    return results


def normalize_eastmoney_news(raw_items: list) -> list[dict]:
    """东方财富资讯 → 统一格式

    client 已填充 `content`（真实正文）和 `summary`（列表页摘要）
    """
    results = []
    for item in raw_items:
        title = item.get("title") or ""
        if not title:
            continue
        results.append({
            "title": title,
            "summary": item.get("summary") or "",
            "content": item.get("content") or item.get("summary") or "",
            "source": "eastmoney",
            "source_name": item.get("mediaName") or "东方财富",
            "source_reliability": 0.75,
            "category": _classify_by_keywords(title),
            "url": item.get("url") or "",
            "tags": [],
            "related_stocks": [],
            "published_at": _date_to_iso(item.get("date") or ""),
            "fingerprint": _fingerprint(title, "eastmoney"),
        })
    return results


def normalize_eastmoney_reports(raw_items: list) -> list[dict]:
    """东方财富研报 → 统一格式"""
    results = []
    for item in raw_items:
        title = item.get("title") or ""
        if not title:
            continue
        content = item.get("content") or item.get("abstract") or ""
        results.append({
            "title": title,
            "summary": item.get("abstract") or content[:200],
            "content": content,
            "source": "eastmoney_report",
            "source_name": item.get("orgSName") or "券商研报",
            "source_reliability": 0.80,
            "category": "industry",
            "url": item.get("infoCode") or "",
            "tags": [item.get("industryName")] if item.get("industryName") else [],
            "related_stocks": [item.get("stockCode")] if item.get("stockCode") else [],
            "published_at": _date_to_iso(item.get("publishDate") or ""),
            "fingerprint": _fingerprint(title, "eastmoney_report"),
        })
    return results


def normalize_ths(raw_items) -> list[dict]:
    """同花顺资讯 → 统一格式

    get_news_feed 返回 {"msg","code","data":{"list":[...]},"time"}
    每个 item 的 ctime 是 Unix 时间戳字符串。
    """
    results = []
    items = raw_items
    if isinstance(raw_items, dict):
        data = raw_items.get("data", {})
        if isinstance(data, dict):
            items = data.get("list") or data.get("data") or []
        elif isinstance(data, list):
            items = data
    if not isinstance(items, list):
        return results

    for item in items:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or item.get("digest") or item.get("short") or ""
        if not title:
            continue
        # ths 的 ctime 是 Unix 时间戳字符串
        ctime = item.get("ctime") or item.get("rtime") or item.get("time") or ""
        try:
            published_at = _ts_to_iso(int(ctime)) if ctime else ""
        except (ValueError, TypeError):
            published_at = _date_to_iso(str(ctime))
        summary = item.get("digest") or item.get("short") or ""
        # client 已抓取真实正文到 content_full 字段
        content = item.get("content_full") or summary
        results.append({
            "title": title,
            "summary": summary,
            "content": content,
            "source": "ths",
            "source_name": "同花顺",
            "source_reliability": 0.75,
            "category": _classify_by_keywords(title),
            "url": item.get("url") or "",
            "tags": [t.get("name", "") for t in (item.get("tags") or []) if isinstance(t, dict)],
            "related_stocks": [s.get("code", "") for s in (item.get("stock") or []) if isinstance(s, dict) and s.get("code")],
            "published_at": published_at,
            "fingerprint": _fingerprint(title, "ths"),
        })
    return results


def normalize_sina(raw_items) -> list[dict]:
    """新浪财经 → 统一格式

    get_news 返回 {"data":{"count":N,"total":0,"articles":[...]}, "status_code":0}
    每个 item 的 time 是 Unix 时间戳字符串。
    """
    results = []
    items = raw_items
    if isinstance(raw_items, dict):
        data = raw_items.get("data", {})
        if isinstance(data, dict):
            items = data.get("articles") or data.get("data") or data.get("list") or []
        elif isinstance(data, list):
            items = data
    if not isinstance(items, list):
        return results

    for item in items:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or ""
        if not title:
            continue
        # sina 的 time 是 Unix 时间戳字符串
        ts = item.get("time") or item.get("ctime") or item.get("create_time") or ""
        try:
            published_at = _ts_to_iso(int(ts)) if ts else ""
        except (ValueError, TypeError):
            published_at = _date_to_iso(str(ts))
        summary = item.get("summary") or item.get("intro") or ""
        content = item.get("content") or summary
        results.append({
            "title": title,
            "summary": summary,
            "content": content,
            "source": "sina",
            "source_name": item.get("source") or "新浪财经",
            "source_reliability": 0.70,
            "category": _classify_by_keywords(title),
            "url": item.get("url") or "",
            "tags": [],
            "related_stocks": [],
            "published_at": published_at,
            "fingerprint": _fingerprint(title, "sina"),
        })
    return results


def normalize_xueqiu(raw_items) -> list[dict]:
    """雪球快讯 → 统一格式

    get_live_news 返回 {"data": {"items": [...]}, "status_code": 0}
    get_live_news_since 可能返回同样结构或直接返回列表
    """
    results = []
    # 解包嵌套结构
    items = raw_items
    if isinstance(raw_items, dict):
        items = raw_items.get("data", {})
        if isinstance(items, dict):
            items = items.get("items", [])
    if not isinstance(items, list):
        return results
    for item in items:
        text = item.get("text") or item.get("title") or ""
        if not text:
            continue
        # 雪球快讯一般没有 title，用 text 前 80 字符作 title
        title = text[:80].strip()
        results.append({
            "title": title,
            "summary": text[:200],
            "content": text,
            "source": "xueqiu",
            "source_name": "雪球",
            "source_reliability": 0.50,
            "category": _classify_by_keywords(text),
            "url": "",
            "tags": [],
            "related_stocks": [],
            "published_at": _ts_to_iso(item.get("created_at", 0) / 1000) if item.get("created_at") else "",
            "fingerprint": _fingerprint(title, "xueqiu"),
        })
    return results


# ==================== 聚合器 ====================


class NewsAggregator(BaseAggregator):
    """新闻事件聚合 — 时间驱动模型

    10 个数据源，统一采集到 ft_news，用 fingerprint 去重。

    采集策略（全部以时间为准）:
        backfill 模式: 从最新向历史方向翻页，直到数据时间 <= target_time 或触顶
        incremental 模式: 每次拉取 newest_time 之后的新数据
    """

    data_domain = "news"
    task_interval = 180

    # 源模板配置 — 仅在首次初始化时写入 DB，运行时从 DB 读取
    SOURCE_CONFIGS = {
        "cls":           {"target_days": 1,   "page_size": 50, "interval": 180},    # API 只有几小时数据
        "cls_depth":     {"target_days": 7,   "page_size": 20, "interval": 600, "depth_id": 1000},  # 财联社深度/头条文章
        "gov":           {"target_days": 90,  "page_size": 20, "interval": 10800},   # 政府网站历史深
        "pboc_omo":      {"target_days": 180, "page_size": 50, "interval": 86400},   # 央行发布频率低
        "pboc_monetary": {"target_days": 180, "page_size": 50, "interval": 86400},
        "em_news":       {"target_days": 3,   "page_size": 50, "interval": 900},     # 东方财富搜索 API 约 2-3 天深度
        "em_reports":    {"target_days": 7,   "page_size": 50, "interval": 7200},    # 研报 API 约 3-7 天深度
        "ths":           {"target_days": 60,  "page_size": 20, "interval": 1800},    # 同花顺可翻很深
        "sina":          {"target_days": 60,  "page_size": 20, "interval": 3600},    # 新浪可翻很深
        "xueqiu":        {"target_days": 4,   "page_size": 10, "interval": 1800},    # 雪球 API 最多 1000 条/4 天
    }

    # MAX_PAGES_PER_TICK 继承自 BaseAggregator

    def __init__(self):
        super().__init__()
        self._init_sources()
        self._backfill_cursor = None  # fetch 方法设置，_compute_checkpoint 读取
        self.last_saved_ids: list[int] = []

    # ==================== 时间工具 ====================

    @staticmethod
    def _extract_item_date(item: dict) -> str:
        """从原始 item 提取日期 YYYY-MM-DD"""
        for key in ("ctime", "rtime", "time", "created_at"):
            v = item.get(key)
            if v:
                try:
                    ts = float(v)
                    if ts > 1e12:
                        ts = ts / 1000
                    return datetime.fromtimestamp(ts, tz=TZ_CST).strftime("%Y-%m-%d")
                except (ValueError, TypeError, OSError):
                    pass
        for key in ("date", "published_at", "publishDate"):
            v = item.get(key)
            if isinstance(v, str) and len(v) >= 10:
                return v[:10]
        return ""

    @staticmethod
    def _extract_item_timestamp(item: dict) -> float:
        """从原始 item 提取精确时间戳（秒），用于回填进展判断（天级日期太粗）"""
        for key in ("ctime", "rtime", "time", "created_at"):
            v = item.get(key)
            if v:
                try:
                    ts = float(v)
                    if ts > 1e12:
                        ts = ts / 1000
                    return ts
                except (ValueError, TypeError):
                    pass
        for key in ("date", "published_at", "publishDate"):
            v = item.get(key)
            if isinstance(v, str) and len(v) >= 10:
                try:
                    return datetime.strptime(v[:10], "%Y-%m-%d").replace(tzinfo=TZ_CST).timestamp()
                except Exception:
                    pass
        return 0.0

    @staticmethod
    def _extract_normalized_date(item: dict) -> str:
        """从 normalized item 提取日期（published_at 是 ISO 字符串）"""
        pa = item.get("published_at", "")
        if not pa:
            return ""
        return str(pa)[:10]

    @staticmethod
    def _unwrap_items(raw) -> list[dict]:
        """解包常见嵌套结构"""
        if isinstance(raw, list):
            return [i for i in raw if isinstance(i, dict)]
        if isinstance(raw, dict):
            data = raw.get("data", raw)
            if isinstance(data, dict):
                for key in ("list", "items", "data", "articles", "roll_data"):
                    v = data.get(key)
                    if isinstance(v, list):
                        return [i for i in v if isinstance(i, dict)]
            if isinstance(data, list):
                return [i for i in data if isinstance(i, dict)]
        return []

    async def _fetch_content_for_items(self, items, content_client, max_content=30):
        """批量并发抓取详情页正文"""
        import asyncio as _asyncio
        if not items or not content_client:
            return items
        fetch_fn = getattr(content_client, "get_content", None) or \
                   getattr(content_client, "fetch_article_content", None)
        if not fetch_fn:
            return items
        to_fetch = [i for i in items if i.get("url")][:max_content]
        for i in range(0, len(to_fetch), 10):
            batch = to_fetch[i:i + 10]
            contents = await _asyncio.gather(
                *[fetch_fn(item["url"]) for item in batch], return_exceptions=True
            )
            for item, c in zip(batch, contents):
                if isinstance(c, str) and c:
                    item["content"] = c
                    item["content_full"] = c
        return items

    # ==================== 回填循环（核心） ====================

    BACKFILL_SAVE_INTERVAL = 5  # 每 5 页存一次

    async def _backfill_loop(self, source_name, fetch_one_page, cp,
                             normalize_fn=None, content_client=None):
        """通用回填循环：一直翻页直到时间满足或触顶，每 5 页落库一次

        Args:
            source_name: 源名
            fetch_one_page: async (cursor) -> (items: list[dict], next_cursor)
            cp: checkpoint dict（含 target_time, cursor, _config, _lock）
            normalize_fn: 可选，normalize 函数（传入则每批自动 normalize+save+update_checkpoint）
            content_client: 可选，详情页抓取 client（flush 时批量抓正文）
        """
        from src.infrastructure.db import checkpoint_store

        target_time = cp.get("target_time", "")
        cursor = cp.get("cursor")
        lock = cp.get("_lock")

        all_items = []       # 当前批次缓冲（每 5 页清空）
        total_saved = 0
        prev_oldest_ts = None
        done = False

        for page_num in range(1, self.MAX_PAGES_PER_TICK + 1):
            # 续锁
            if lock and page_num % 5 == 0:
                lock.renew()

            try:
                items, next_cursor = await fetch_one_page(cursor)
            except Exception as e:
                logger.warning(f"[news:{source_name}] 回填 page={page_num} 失败: {e}")
                break

            if not items:
                logger.info(f"[news:{source_name}] 回填触顶: 返回空列表 (page={page_num})")
                self._backfill_cursor = "__CEILING__"
                done = True
                break

            # 游标无效检测
            if next_cursor is not None and (next_cursor == -1 or next_cursor == cursor):
                logger.info(f"[news:{source_name}] 回填触顶: 游标无效 cursor={next_cursor} (page={page_num})")
                all_items.extend(items)
                self._backfill_cursor = "__CEILING__"
                done = True
                break

            all_items.extend(items)

            # 提取本页最早时间戳
            timestamps = [self._extract_item_timestamp(i) for i in items]
            timestamps = [t for t in timestamps if t > 0]
            if not timestamps:
                cursor = next_cursor
                continue

            page_oldest_ts = min(timestamps)
            page_oldest_str = datetime.fromtimestamp(page_oldest_ts, tz=TZ_CST).strftime("%m/%d %H:%M")
            page_oldest_date = datetime.fromtimestamp(page_oldest_ts, tz=TZ_CST).strftime("%Y-%m-%d")

            # 时间达标
            if target_time and page_oldest_date <= target_time:
                logger.info(f"[news:{source_name}] 回填完成: oldest={page_oldest_str} <= target={target_time}")
                self._backfill_cursor = "__DONE__"
                done = True
                break

            # 时间无进展
            if prev_oldest_ts is not None and page_oldest_ts >= prev_oldest_ts:
                logger.info(
                    f"[news:{source_name}] 回填触顶: 时间无进展 {page_oldest_str}，目标 {target_time}"
                )
                self._backfill_cursor = "__CEILING__"
                done = True
                break

            prev_oldest_ts = page_oldest_ts
            cursor = next_cursor

            logger.info(f"[news:{source_name}] 回填 page={page_num} items={len(items)} oldest={page_oldest_str}")

            # ── 每 5 页落库一次 ──
            if normalize_fn and page_num % self.BACKFILL_SAVE_INTERVAL == 0:
                saved = await self._flush_backfill_batch(source_name, all_items, normalize_fn, cursor, cp, content_client)
                total_saved += saved
                all_items = []

        else:
            # 安全阀
            self._backfill_cursor = cursor
            logger.info(f"[news:{source_name}] 回填暂停: 达到 {self.MAX_PAGES_PER_TICK} 页上限，cursor={cursor}")

        # 最后剩余的 items 也要落库
        if normalize_fn and all_items:
            saved = await self._flush_backfill_batch(source_name, all_items, normalize_fn, cursor, cp, content_client)
            total_saved += saved
            all_items = []

        # normalize_fn 模式下，tick() 不会走到 _compute_checkpoint，
        # 所以模式切换必须在这里完成
        if normalize_fn and self._backfill_cursor in ("__DONE__", "__CEILING__"):
            is_done = self._backfill_cursor == "__DONE__"
            # 读当前 state 拿 newest/oldest/target 用于日志
            state = checkpoint_store.get(self.data_domain, source_name) or {}
            final_cp = {
                "mode": "incremental",
                "cursor": None,
                "backfill_status": "done" if is_done else "ceiling",
                "newest_time": state.get("newest_time"),
                "oldest_time": state.get("oldest_time"),
                "target_time": state.get("target_time"),
            }
            checkpoint_store.update_success(self.data_domain, source_name, final_cp, 0)
            if is_done:
                logger.info(f"[news:{source_name}] 回填完成 → 切增量")
            else:
                logger.warning(
                    f"[news:{source_name}] 回填触顶 → 切增量: "
                    f"覆盖 {state.get('oldest_time')} ~ {state.get('newest_time')}, "
                    f"目标 {state.get('target_time')} 未达到"
                )
            self._backfill_cursor = None

        if normalize_fn:
            logger.info(f"[news:{source_name}] 回填批量入库合计 {total_saved} 条")

        return all_items  # normalize_fn 模式下返回空（已入库），否则返回全部

    async def _flush_backfill_batch(self, source_name, raw_items, normalize_fn, cursor, cp,
                                     content_client=None):
        """把一批 raw items normalize → 批量抓正文 → save → update checkpoint"""
        from src.infrastructure.db import checkpoint_store

        import time as _time

        items = normalize_fn(raw_items)

        # 先用 fingerprint 过滤已入库的，避免对已有数据白抓正文
        if content_client and items:
            existing_fps = set()
            try:
                from src.infrastructure.connections import get_session
                from src.infrastructure.persistence.models.collection import News
                from sqlalchemy import select
                fps = [i.get("fingerprint") for i in items if i.get("fingerprint")]
                if fps:
                    with get_session() as s:
                        existing_fps = set(s.scalars(
                            select(News.fingerprint).where(News.fingerprint.in_(fps))
                        ).all())
            except Exception:
                pass
            new_items = [i for i in items if i.get("fingerprint") not in existing_fps]
        else:
            new_items = items

        # 只为新条目抓正文
        t_content = 0
        if content_client and new_items:
            t0 = _time.monotonic()
            await self._fetch_content_for_items(new_items, content_client)
            t_content = _time.monotonic() - t0

        t0 = _time.monotonic()
        saved = self._save(items) if items else 0  # 全量传入 _save，fingerprint 去重
        t_save = _time.monotonic() - t0

        # 中间 checkpoint：保存 cursor 进度（不切模式，mode 还是 backfill）
        mid_cp = {k: v for k, v in cp.items() if not k.startswith("_")}
        mid_cp["cursor"] = cursor
        # 更新时间范围
        dates = [str(i.get("published_at", "")) for i in items if i.get("published_at")]
        if dates:
            newest = max(dates)
            oldest = min(dates)
            prev_newest = mid_cp.get("newest_time") or ""
            prev_oldest = mid_cp.get("oldest_time") or ""
            mid_cp["newest_time"] = max(newest, prev_newest) if prev_newest else newest
            mid_cp["oldest_time"] = min(oldest, prev_oldest) if prev_oldest else oldest

        checkpoint_store.update_success(self.data_domain, source_name, mid_cp, saved)
        content_info = f"，抓正文 {len(new_items)}篇 {t_content:.1f}s" if t_content > 0.1 else ""
        skip_info = f"，跳过已有 {len(items) - len(new_items)}篇" if content_client and len(new_items) < len(items) else ""
        logger.info(f"[news:{source_name}] 批量落库 {saved} 条，cursor={cursor} (save={t_save:.1f}s{content_info}{skip_info})")
        return saved

    # ==================== 各源 fetch 方法 ====================

    async def _fetch_cls(self, cp: dict) -> list:
        from src.infrastructure import clients
        mode = cp.get("mode", "incremental")

        if mode == "backfill":
            async def fetch_page(cursor):
                items = await clients.cls.get_telegraph_list(rn=50, last_time=cursor)
                next_cursor = items[-1].get("ctime") if items else cursor
                return items, next_cursor
            return await self._backfill_loop("cls", fetch_page, cp, normalize_fn=normalize_cls)
        else:
            newest = cp.get("newest_time")
            if newest:
                ctime = _iso_to_timestamp(newest)
                return await clients.cls.get_telegraph_since(ctime)
            return await clients.cls.get_telegraph_list(rn=50)

    async def _fetch_cls_depth(self, cp: dict) -> list:
        from src.infrastructure import clients
        mode = cp.get("mode", "incremental")
        config = cp.get("_config") or {}
        page_size = config.get("page_size", 20)
        depth_id = config.get("depth_id", 1000)

        if mode == "backfill":
            async def fetch_page(cursor):
                items = await clients.cls.get_depth_list(depth_id=depth_id, rn=page_size, last_time=cursor)
                next_cursor = items[-1].get("ctime") if items else cursor
                return items, next_cursor
            return await self._backfill_loop("cls_depth", fetch_page, cp, normalize_fn=normalize_cls_depth)
        newest = cp.get("newest_time")
        if newest:
            ctime = _iso_to_timestamp(newest)
            return await clients.cls.get_depth_since(ctime, depth_id=depth_id, page_size=page_size)
        return await clients.cls.get_depth_list(depth_id=depth_id, rn=page_size)

    async def _fetch_gov(self, cp: dict) -> list:
        from src.infrastructure import clients
        mode = cp.get("mode", "incremental")
        newest = cp.get("newest_time")
        # Gov 是单次拉取，无翻页
        if mode == "incremental" and newest:
            return await _fetch_gov_all_depts(clients.gov, newest)
        return await _fetch_gov_all_depts(clients.gov, None)

    async def _fetch_pboc_omo(self, cp: dict) -> list:
        from src.infrastructure import clients
        mode = cp.get("mode", "incremental")
        newest = cp.get("newest_time")
        if mode == "incremental" and newest:
            raw = await clients.pboc.get_omo_announcements_since(newest)
        else:
            raw = await clients.pboc.get_omo_announcements(limit=50)
        return await self._fetch_content_for_items(raw, clients.pboc)

    async def _fetch_pboc_monetary(self, cp: dict) -> list:
        from src.infrastructure import clients
        mode = cp.get("mode", "incremental")
        newest = cp.get("newest_time")
        if mode == "incremental" and newest:
            raw = await clients.pboc.get_monetary_policy_since(newest)
        else:
            raw = await clients.pboc.get_monetary_policy(limit=50)
        return await self._fetch_content_for_items(raw, clients.pboc)

    async def _fetch_em_news(self, cp: dict) -> list:
        from src.infrastructure import clients
        mode = cp.get("mode", "incremental")
        config = cp.get("_config") or {}
        page_size = config.get("page_size", 50)

        if mode == "backfill":
            async def fetch_page(cursor):
                page = cursor or 1
                items = await clients.eastmoney.get_news_by_keyword("A股", page_size=page_size, page=page, with_content=False)
                return items, page + 1
            return await self._backfill_loop("em_news", fetch_page, cp,
                                            normalize_fn=normalize_eastmoney_news, content_client=clients.eastmoney)
        else:
            newest = cp.get("newest_time")
            if newest:
                return await clients.eastmoney.get_news_by_keyword_since("A股", newest)
            return await clients.eastmoney.get_news_by_keyword("A股", page_size=page_size)

    async def _fetch_em_reports(self, cp: dict) -> list:
        from src.infrastructure import clients
        mode = cp.get("mode", "incremental")
        config = cp.get("_config") or {}
        page_size = config.get("page_size", 50)

        if mode == "backfill":
            async def fetch_page(cursor):
                page = cursor or 1
                items = await clients.eastmoney.get_research_reports(page_size=page_size, page=page)
                return items, page + 1
            return await self._backfill_loop("em_reports", fetch_page, cp,
                                            normalize_fn=normalize_eastmoney_reports, content_client=clients.eastmoney)
        else:
            newest = cp.get("newest_time")
            if newest:
                return await clients.eastmoney.get_research_reports_since(newest)
            return await clients.eastmoney.get_research_reports(page_size=page_size)

    async def _fetch_ths(self, cp: dict) -> list:
        from src.infrastructure import clients
        mode = cp.get("mode", "incremental")

        if mode == "backfill":
            async def fetch_page(cursor):
                page = cursor or 1
                # 回填只拉列表，不抓正文（正文后续增量补抓），速度快 10 倍
                raw = await clients.ths.get_news_feed(page=page, with_content=False)
                items = self._unwrap_items(raw)
                return items, page + 1
            return await self._backfill_loop("ths", fetch_page, cp,
                                            normalize_fn=normalize_ths, content_client=clients.ths)
        else:
            newest = cp.get("newest_time")
            raw = await clients.ths.get_news_feed(with_content=False)
            items = self._unwrap_items(raw)
            if newest:
                items = [i for i in items if self._extract_item_date(i) >= newest]
            if not items:
                return []
            return await self._fetch_content_for_items(items, clients.ths)

    async def _fetch_sina(self, cp: dict) -> list:
        from src.infrastructure import clients
        mode = cp.get("mode", "incremental")

        if mode == "backfill":
            async def fetch_page(cursor):
                page = cursor or 1
                raw = await clients.sina.get_news(page=page, with_content=True)
                items = self._unwrap_items(raw)
                return items, page + 1
            return await self._backfill_loop("sina", fetch_page, cp,
                                            normalize_fn=normalize_sina, content_client=clients.sina)
        else:
            newest = cp.get("newest_time")
            raw = await clients.sina.get_news(with_content=False)
            items = self._unwrap_items(raw)
            if newest:
                items = [i for i in items if self._extract_item_date(i) >= newest]
            if not items:
                return []
            return await self._fetch_content_for_items(items, clients.sina)

    async def _fetch_xueqiu(self, cp: dict) -> list:
        from src.infrastructure import clients
        mode = cp.get("mode", "incremental")

        if mode == "backfill":
            async def fetch_page(cursor):
                # 传 max_id 给 API 做服务端分页，而不是拉全量再客户端过滤
                max_id = cursor if cursor is not None else -1
                raw = await clients.xueqiu.get_live_news(count=50, max_id=max_id)
                items = raw.get("data", {}).get("items", []) if isinstance(raw, dict) else []
                # API 返回 next_max_id 作为下一页游标
                next_cursor = raw.get("data", {}).get("next_max_id") if isinstance(raw, dict) else None
                if not next_cursor and items:
                    next_cursor = min(i.get("id", 0) for i in items)
                return items, next_cursor
            return await self._backfill_loop("xueqiu", fetch_page, cp, normalize_fn=normalize_xueqiu)
        else:
            return await clients.xueqiu.get_live_news()

    # ==================== 源注册 ====================

    def _init_sources(self):
        self.sources = [
            SourceDef("cls", self._fetch_cls, 180, normalize_cls),
            SourceDef("cls_depth", self._fetch_cls_depth, 600, normalize_cls_depth),
            SourceDef("gov", self._fetch_gov, 10800, normalize_gov),
            SourceDef("pboc_omo", self._fetch_pboc_omo, 86400, normalize_pboc),
            SourceDef("pboc_monetary", self._fetch_pboc_monetary, 86400, normalize_pboc),
            SourceDef("em_news", self._fetch_em_news, 900, normalize_eastmoney_news),
            SourceDef("em_reports", self._fetch_em_reports, 7200, normalize_eastmoney_reports),
            SourceDef("ths", self._fetch_ths, 1800, normalize_ths),
            SourceDef("sina", self._fetch_sina, 3600, normalize_sina),
            SourceDef("xueqiu", self._fetch_xueqiu, 1800, normalize_xueqiu),
        ]

    # ==================== backfill 信号 hook ====================

    def _get_backfill_signal(self, source_name: str) -> str | None:
        """把 _backfill_loop 设置的 cursor 信号转发给 BaseAggregator"""
        signal = self._backfill_cursor
        self._backfill_cursor = None
        if signal == "__DONE__":
            return "done"
        if signal == "__CEILING__":
            return "ceiling"
        return signal  # 翻页游标或 None

    # ==================== 入库 ====================

    def _query_today_titles(self) -> list[str]:
        """查询今日已入库的所有标题（用于跨源相似度去重）"""
        try:
            return self._news_repo().find_today_titles()
        except Exception:
            return []

    @staticmethod
    def _news_repo():
        """懒加载 NewsRepository(避免 module-level import 触发 ORM 初始化)"""
        from src.infrastructure.persistence.repositories import NewsRepositoryImpl
        return NewsRepositoryImpl()

    @staticmethod
    def _is_similar(a: str, b: str, threshold: float = 0.85) -> bool:
        """快速相似度判断：先做长度差预筛 + 字符集交集，再算 SequenceMatcher"""
        from difflib import SequenceMatcher
        if not a or not b:
            return False
        # 长度差 > 30% 直接返回 False（性能优化）
        len_a, len_b = len(a), len(b)
        if abs(len_a - len_b) / max(len_a, len_b) > 0.3:
            return False
        return SequenceMatcher(None, a, b).ratio() > threshold

    def _save(self, items: list[dict]) -> int:
        """改造后: 走 NewsRepository.upsert_batch (R2.3)

        相似度去重 + content hash 去重的业务逻辑保留在这里(domain 层职责),
        repository 只负责"批量入库 + ON CONFLICT 跳过"。
        """
        if not items:
            return 0

        # 跨源相似度去重: 先拉今日已入库的所有标题
        existing_titles = self._query_today_titles()
        # 批次内按 content hash 去重
        seen_content_hashes: set[str] = set()
        seen_titles_in_batch: list[str] = []

        records: list[dict] = []
        for item in items:
            if not item.get("title") or not item.get("published_at"):
                continue
            title = item["title"]
            # 跨源标题相似度去重
            is_dup = False
            for exist in existing_titles:
                if self._is_similar(title, exist):
                    is_dup = True
                    break
            if not is_dup:
                for exist in seen_titles_in_batch:
                    if self._is_similar(title, exist):
                        is_dup = True
                        break
            if is_dup:
                continue
            seen_titles_in_batch.append(title)
            content = item.get("content", "") or ""
            summary = item.get("summary", "") or ""
            if content.strip():
                content_hash = hashlib.sha256(content.encode()).hexdigest()
                if content_hash in seen_content_hashes:
                    continue
                seen_content_hashes.add(content_hash)
            records.append({
                "title": title,
                "summary": summary,
                "content": content,
                "source": item["source"],
                "source_name": item.get("source_name", ""),
                "source_reliability": item.get("source_reliability", 0.5),
                "category": item.get("category", ""),
                "url": item.get("url", ""),
                "tags": item.get("tags", []),
                "related_stocks": item.get("related_stocks", []),
                "published_at": item["published_at"],
                "fingerprint": item["fingerprint"],
            })
        repo = self._news_repo()
        new_ids = repo.upsert_batch_returning_ids(records)
        self.last_saved_ids.extend(new_ids)
        return len(new_ids)

    # ==================== 查询 ====================

    async def query(
        self,
        since: str | None = None,
        category: str | None = None,
        source: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        conditions = []
        values = []
        if since:
            conditions.append("published_at >= %s")
            values.append(since)
        if category:
            conditions.append("category = %s")
            values.append(category)
        if source:
            conditions.append("source = %s")
            values.append(source)
        return self._query_table(
            "ft_news",
            conditions=conditions or None,
            values=values or None,
            order_by="published_at DESC",
            limit=limit,
        )
