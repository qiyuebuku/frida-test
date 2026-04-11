"""新闻事件聚合 (P0)

数据源: 财联社快讯、政府网站、人民银行(OMO+货币政策)、东方财富(资讯+研报)、
        同花顺资讯、新浪财经、雪球快讯
目标表: ft_news
"""

import hashlib
import json
import logging
from datetime import datetime, timezone, timedelta

from src.domain.aggregation.base import BaseAggregator, SourceDef
from src.infrastructure.db.fund_db import get_conn

logger = logging.getLogger(__name__)

TZ_CST = timezone(timedelta(hours=8))

DDL = """
CREATE TABLE IF NOT EXISTS ft_news (
    id              SERIAL PRIMARY KEY,
    title           TEXT NOT NULL,
    summary         TEXT DEFAULT '',        -- 列表页摘要（快速预览，短）
    content         TEXT DEFAULT '',        -- 真实正文（完整内容，用于 AI 抽取）
    source          VARCHAR(32) NOT NULL,
    source_name     VARCHAR(50) DEFAULT '',
    source_reliability FLOAT DEFAULT 0.5,
    category        VARCHAR(20) DEFAULT '',
    url             TEXT DEFAULT '',
    tags            JSONB DEFAULT '[]',
    related_stocks  JSONB DEFAULT '[]',
    published_at    TIMESTAMPTZ NOT NULL,
    fingerprint     VARCHAR(64) NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ft_news_fingerprint ON ft_news(fingerprint);
CREATE INDEX IF NOT EXISTS idx_ft_news_source_time ON ft_news(source, published_at);
CREATE INDEX IF NOT EXISTS idx_ft_news_category ON ft_news(category, published_at);
"""

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
    """财联社快讯 → 统一格式"""
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
        # 财联社快讯：content 即是正文，摘要可用 brief 或 title
        results.append({
            "title": title,
            "summary": item.get("brief") or title[:100],
            "content": content,
            "source": "cls",
            "source_name": "财联社",
            "source_reliability": 0.85,
            "category": _classify_by_keywords(title + content),
            "url": "",
            "tags": tags,
            "related_stocks": stocks,
            "published_at": _ts_to_iso(item.get("ctime", 0)),
            "fingerprint": _fingerprint(title, "cls"),
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
    """新闻事件聚合

    9 个数据源，统一采集到 ft_news，用 fingerprint 去重。
    """

    data_domain = "news"
    task_interval = 180  # 3 分钟

    def __init__(self):
        super().__init__()
        self._init_sources()
        self._exec_ddl(DDL)

    def _init_sources(self):
        from src.infrastructure import clients

        self.sources = [
            # P0: 财联社快讯 — 3 分钟
            SourceDef(
                "cls",
                lambda cp: clients.cls.get_telegraph_since(cp) if cp else clients.cls.get_telegraph_list(rn=30),
                180,
                normalize_cls,
            ),
            # P0: 政府网站（多部委合并采集）— 3 小时
            SourceDef(
                "gov",
                lambda cp: _fetch_gov_all_depts(clients.gov, cp),
                10800,
                normalize_gov,
            ),
            # P0: 人民银行公开市场操作 — 24 小时（含正文抓取）
            SourceDef(
                "pboc_omo",
                lambda cp: _fetch_with_content(
                    clients.pboc.get_omo_announcements_since(cp) if cp else clients.pboc.get_omo_announcements(),
                    clients.pboc,
                ),
                86400,
                normalize_pboc,
            ),
            # P1: 人民银行货币政策 — 24 小时（含正文抓取）
            SourceDef(
                "pboc_monetary",
                lambda cp: _fetch_with_content(
                    clients.pboc.get_monetary_policy_since(cp) if cp else clients.pboc.get_monetary_policy(),
                    clients.pboc,
                ),
                86400,
                normalize_pboc,
            ),
            # P1: 东方财富资讯 — 15 分钟
            SourceDef(
                "em_news",
                lambda cp: clients.eastmoney.get_news_by_keyword_since("A股", cp) if cp else clients.eastmoney.get_news_by_keyword("A股"),
                900,
                normalize_eastmoney_news,
            ),
            # P2: 券商研报 — 2 小时
            SourceDef(
                "em_reports",
                lambda cp: clients.eastmoney.get_research_reports_since(cp) if cp else clients.eastmoney.get_research_reports(),
                7200,
                normalize_eastmoney_reports,
            ),
            # P2: 同花顺滚动快讯 — 30 分钟
            SourceDef(
                "ths",
                lambda cp: clients.ths.get_news_feed(),
                1800,
                normalize_ths,
            ),
            # P2: 新浪财经 — 1 小时
            SourceDef(
                "sina",
                lambda cp: clients.sina.get_news(),
                3600,
                normalize_sina,
            ),
            # P2: 雪球 7x24 快讯 — 30 分钟
            SourceDef(
                "xueqiu",
                lambda cp: clients.xueqiu.get_live_news_since(cp) if cp else clients.xueqiu.get_live_news(),
                1800,
                normalize_xueqiu,
            ),
        ]

    # ==================== checkpoint ====================

    def _get_checkpoint(self, source_name: str):
        """从结果表最新记录推算 checkpoint"""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    if source_name == "cls":
                        # cls 用 ctime（unix 时间戳），从 published_at 转回
                        cur.execute("""
                            SELECT EXTRACT(EPOCH FROM published_at)::bigint
                            FROM ft_news WHERE source = 'cls'
                            ORDER BY published_at DESC LIMIT 1
                        """)
                        row = cur.fetchone()
                        return int(row[0]) if row else None
                    elif source_name == "xueqiu":
                        # xueqiu 用 since_id，无法从 ft_news 推算，返回 None 走全量
                        return None
                    else:
                        # gov/pboc/em 用 since_date
                        source_filter = source_name
                        if source_name.startswith("em_"):
                            source_filter = "eastmoney"
                        elif source_name.startswith("pboc_"):
                            source_filter = "pboc"
                        cur.execute("""
                            SELECT published_at::date::text
                            FROM ft_news WHERE source LIKE %s
                            ORDER BY published_at DESC LIMIT 1
                        """, (f"{source_filter}%",))
                        row = cur.fetchone()
                        return row[0] if row else None
        except Exception:
            return None

    # ==================== 入库 ====================

    def _query_today_titles(self) -> list[str]:
        """查询今日已入库的所有标题（用于跨源相似度去重）"""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT title FROM ft_news
                        WHERE published_at >= CURRENT_DATE
                        ORDER BY id DESC LIMIT 2000
                    """)
                    return [r[0] for r in cur.fetchall()]
        except Exception:
            return []

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
        if not items:
            return 0
        columns = [
            "title", "summary", "content", "source", "source_name", "source_reliability",
            "category", "url", "tags", "related_stocks", "published_at", "fingerprint",
        ]
        # 跨源相似度去重：先拉今日已入库的所有标题，与新条目做相似度比对
        existing_titles = self._query_today_titles()
        # 批次内按 content hash 去重：同内容的新闻只保留第一条
        # （处理 sina 编辑把同一发言拆成多篇文章的场景）
        seen_content_hashes: set[str] = set()
        seen_titles_in_batch: list[str] = []   # 本批次已处理的标题
        rows = []
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
            rows.append((
                item["title"],
                summary,
                content,
                item["source"],
                item.get("source_name", ""),
                item.get("source_reliability", 0.5),
                item.get("category", ""),
                item.get("url", ""),
                json.dumps(item.get("tags", []), ensure_ascii=False),
                json.dumps(item.get("related_stocks", []), ensure_ascii=False),
                item["published_at"],
                item["fingerprint"],
            ))
        return self._insert_many(
            "ft_news", columns, rows,
            conflict_clause="ON CONFLICT (fingerprint) DO NOTHING",
        )

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
