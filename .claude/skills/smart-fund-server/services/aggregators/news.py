"""新闻事件聚合 (P0)

数据源: 财联社快讯、政府网站、人民银行(OMO+货币政策)、东方财富(资讯+研报)、
        同花顺资讯、新浪财经、雪球快讯
目标表: ft_news
"""

import hashlib
import json
import logging
from datetime import datetime, timezone, timedelta

from services.aggregators.base import BaseAggregator, SourceDef
from services.db.fund_db import get_conn

logger = logging.getLogger(__name__)

TZ_CST = timezone(timedelta(hours=8))

DDL = """
CREATE TABLE IF NOT EXISTS ft_news (
    id              SERIAL PRIMARY KEY,
    title           TEXT NOT NULL,
    content         TEXT DEFAULT '',
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

# ==================== 工具函数 ====================


def _fingerprint(title: str, source: str) -> str:
    return hashlib.sha256(f"{title}{source}".encode()).hexdigest()


def _ts_to_iso(ts: int | float) -> str:
    """Unix 时间戳 → ISO 8601"""
    return datetime.fromtimestamp(ts, tz=TZ_CST).isoformat()


def _date_to_iso(date_str: str) -> str:
    """YYYY-MM-DD → ISO 8601"""
    if "T" in date_str:
        return date_str
    return f"{date_str[:10]}T00:00:00+08:00"


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
        results.append({
            "title": title,
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
    """政府网站 → 统一格式"""
    results = []
    for item in raw_items:
        title = item.get("title") or ""
        if not title:
            continue
        source_id = item.get("source") or "gov"
        results.append({
            "title": title,
            "content": item.get("content") or "",
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
        results.append({
            "title": title,
            "content": item.get("content") or "",
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
    """东方财富资讯 → 统一格式"""
    results = []
    for item in raw_items:
        title = item.get("title") or ""
        if not title:
            continue
        results.append({
            "title": title,
            "content": item.get("content") or "",
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
        results.append({
            "title": title,
            "content": item.get("content") or item.get("abstract") or "",
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


def normalize_ths(raw_items: list) -> list[dict]:
    """同花顺资讯 → 统一格式

    THS get_news_feed 返回 dict，data 字段为列表
    """
    results = []
    # 处理 dict 或 list 输入
    items = raw_items
    if isinstance(raw_items, dict):
        items = raw_items.get("data") or raw_items.get("list") or []
    if not isinstance(items, list):
        return results

    for item in items:
        title = item.get("title") or item.get("digest") or ""
        if not title:
            continue
        results.append({
            "title": title,
            "content": item.get("content") or item.get("digest") or "",
            "source": "ths",
            "source_name": "同花顺",
            "source_reliability": 0.75,
            "category": _classify_by_keywords(title),
            "url": item.get("url") or "",
            "tags": [],
            "related_stocks": [],
            "published_at": _date_to_iso(item.get("ctime") or item.get("time") or ""),
            "fingerprint": _fingerprint(title, "ths"),
        })
    return results


def normalize_sina(raw_items: list) -> list[dict]:
    """新浪财经 → 统一格式

    Sina get_news 返回 dict，data 下是列表
    """
    results = []
    items = raw_items
    if isinstance(raw_items, dict):
        items = raw_items.get("data") or raw_items.get("result", {}).get("data") or []
    if not isinstance(items, list):
        return results

    for item in items:
        title = item.get("title") or ""
        if not title:
            continue
        results.append({
            "title": title,
            "content": item.get("summary") or item.get("intro") or "",
            "source": "sina",
            "source_name": "新浪财经",
            "source_reliability": 0.70,
            "category": _classify_by_keywords(title),
            "url": item.get("url") or "",
            "tags": [],
            "related_stocks": [],
            "published_at": _date_to_iso(item.get("ctime") or item.get("create_time") or ""),
            "fingerprint": _fingerprint(title, "sina"),
        })
    return results


def normalize_xueqiu(raw_items: list) -> list[dict]:
    """雪球快讯 → 统一格式"""
    results = []
    for item in raw_items:
        text = item.get("text") or item.get("title") or ""
        if not text:
            continue
        # 雪球快讯一般没有 title，用 text 前 80 字符作 title
        title = text[:80].strip()
        results.append({
            "title": title,
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
        from routers import _utils

        self.sources = [
            # P0: 财联社快讯 — 3 分钟
            SourceDef(
                "cls",
                lambda cp: _utils.cls.get_telegraph_since(cp) if cp else _utils.cls.get_telegraph_list(rn=30),
                180,
                normalize_cls,
            ),
            # P0: 政府网站 5 部委 — 3 小时（每个 source 用同一个 normalize）
            SourceDef(
                "gov",
                lambda cp: _utils.gov.get_announcements_since("gov_cn", cp) if cp else _utils.gov.get_announcements("gov_cn"),
                10800,
                normalize_gov,
            ),
            # P0: 人民银行公开市场操作 — 24 小时
            SourceDef(
                "pboc_omo",
                lambda cp: _utils.pboc.get_omo_announcements_since(cp) if cp else _utils.pboc.get_omo_announcements(),
                86400,
                normalize_pboc,
            ),
            # P1: 人民银行货币政策 — 24 小时
            SourceDef(
                "pboc_monetary",
                lambda cp: _utils.pboc.get_monetary_policy_since(cp) if cp else _utils.pboc.get_monetary_policy(),
                86400,
                normalize_pboc,
            ),
            # P1: 东方财富资讯 — 15 分钟
            SourceDef(
                "em_news",
                lambda cp: _utils.eastmoney.get_news_by_keyword_since("A股", cp) if cp else _utils.eastmoney.get_news_by_keyword("A股"),
                900,
                normalize_eastmoney_news,
            ),
            # P2: 券商研报 — 2 小时
            SourceDef(
                "em_reports",
                lambda cp: _utils.eastmoney.get_research_reports_since(cp) if cp else _utils.eastmoney.get_research_reports(),
                7200,
                normalize_eastmoney_reports,
            ),
            # P2: 同花顺滚动快讯 — 30 分钟
            SourceDef(
                "ths",
                lambda cp: _utils.ths.get_news_feed(),
                1800,
                normalize_ths,
            ),
            # P2: 新浪财经 — 1 小时
            SourceDef(
                "sina",
                lambda cp: _utils.sina.get_news(),
                3600,
                normalize_sina,
            ),
            # P2: 雪球 7x24 快讯 — 30 分钟
            SourceDef(
                "xueqiu",
                lambda cp: _utils.xueqiu.get_live_news_since(cp) if cp else _utils.xueqiu.get_live_news(),
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

    def _save(self, items: list[dict]) -> int:
        if not items:
            return 0
        columns = [
            "title", "content", "source", "source_name", "source_reliability",
            "category", "url", "tags", "related_stocks", "published_at", "fingerprint",
        ]
        rows = []
        for item in items:
            if not item.get("title") or not item.get("published_at"):
                continue
            rows.append((
                item["title"],
                item.get("content", ""),
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
