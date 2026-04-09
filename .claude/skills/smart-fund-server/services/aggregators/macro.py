"""宏观政策聚合 (P1)

数据源: 东方财富宏观指标(CPI/PPI/PMI/GDP/M2/人民币贷款/外汇储备/固定资产投资/FDI)
        + 人民银行货币数据(LPR/Shibor/汇率)
目标表: ft_macro_indicators
"""

import json
import logging
from datetime import date

from services.aggregators.base import BaseAggregator, SourceDef
from services.db.fund_db import get_conn

logger = logging.getLogger(__name__)

DDL = """
CREATE TABLE IF NOT EXISTS ft_macro_indicators (
    id              SERIAL PRIMARY KEY,
    indicator       VARCHAR(32) NOT NULL,
    period          VARCHAR(16) NOT NULL,
    value           FLOAT NOT NULL,
    unit            VARCHAR(16) DEFAULT '',
    prev_value      FLOAT,
    source          VARCHAR(32) DEFAULT '',
    published_at    DATE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(indicator, period, source)
);

CREATE INDEX IF NOT EXISTS idx_ft_macro_ind_period ON ft_macro_indicators(indicator, period);
"""

# ==================== 指标配置 ====================

# 东方财富宏观指标: (indicator_name, method_name, report_name, unit)
EM_INDICATORS = [
    ("cpi", "get_macro_cpi", "RPT_ECONOMY_CPI", "%"),
    ("ppi", "get_macro_ppi", "RPT_ECONOMY_PPI", "%"),
    ("pmi", "get_macro_pmi", "RPT_ECONOMY_PMI", ""),
    ("gdp", "get_macro_gdp", "RPT_ECONOMY_GDP", "亿元"),
    ("m2", "get_macro_money_supply", "RPT_ECONOMY_CURRENCY_SUPPLY", "亿元"),
    ("rmb_loan", "get_macro_rmb_loan", "RPT_ECONOMY_RMB_LOAN", "亿元"),
    ("forex_reserve", "get_macro_forex_reserve", "RPT_ECONOMY_GOLD_CURRENCY", "亿美元"),
    ("fai", "get_macro_fixed_asset_invest", "RPT_ECONOMY_ASSET_INVEST", "%"),
    ("fdi", "get_macro_fdi", "RPT_ECONOMY_FDI", "亿美元"),
]


# ==================== Normalize 函数 ====================


def _extract_period(report_date: str) -> str:
    """从 REPORT_DATE 提取 period

    '2026-02-01 00:00:00' → '2026-02'
    '2026-03-01 00:00:00' (GDP 季度) → '2026-Q1'
    """
    if not report_date:
        return ""
    date_str = report_date[:10]
    # GDP 是季度数据
    month = int(date_str[5:7])
    return date_str[:7]  # YYYY-MM


def _extract_value(item: dict) -> float | None:
    """从东方财富宏观数据提取主要值

    不同指标的字段名不同，按优先级尝试
    """
    for key in (
        "NATIONAL_SAME",    # CPI/PPI 同比
        "MAKE_SAME",        # PMI
        "SAMEINDUS_GDP",    # GDP
        "BASIC_CURRENCY",   # M2
        "RMB_LOAN",         # 人民币贷款
        "GOLD_RESERVES",    # 外汇储备
        "BASE_SAME",        # 固定资产投资
        "FDI_US",           # FDI
    ):
        val = item.get(key)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                continue
    # 兜底：取第一个数值字段
    for k, v in item.items():
        if k.startswith("REPORT_") or k.startswith("INDICATOR_"):
            continue
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _make_em_normalizer(indicator_name: str, unit: str):
    """工厂函数：生成东方财富指标的 normalize 函数"""

    def normalizer(raw_items: list) -> list[dict]:
        if not isinstance(raw_items, list):
            return []
        results = []
        prev_value = None
        # 数据按时间倒序排列，反转后方便取 prev_value
        for i, item in enumerate(reversed(raw_items)):
            report_date = item.get("REPORT_DATE") or ""
            period = _extract_period(report_date)
            value = _extract_value(item)
            if period and value is not None:
                results.append({
                    "indicator": indicator_name,
                    "period": period,
                    "value": value,
                    "unit": unit,
                    "prev_value": prev_value,
                    "source": "eastmoney",
                    "published_at": report_date[:10],
                })
                prev_value = value
        return results

    return normalizer


def normalize_shibor_lpr(raw) -> list[dict]:
    """人民银行 Shibor+LPR → 统一格式

    get_currency_data("shibor") 返回 dict: {status_code, data: {shibor:[...], lpr:[...]}}
    """
    results = []
    if not isinstance(raw, dict):
        return results

    data = raw.get("data", raw)
    if not isinstance(data, dict):
        return results

    today = date.today().isoformat()

    # Shibor
    shibor_list = data.get("shibor") or []
    if isinstance(shibor_list, list) and shibor_list:
        latest = shibor_list[0] if isinstance(shibor_list[0], dict) else {}
        value = latest.get("ON") or latest.get("overnight") or latest.get("value")
        if value is not None:
            results.append({
                "indicator": "shibor",
                "period": latest.get("date", today)[:10],
                "value": float(value),
                "unit": "%",
                "prev_value": None,
                "source": "pboc",
                "published_at": latest.get("date", today)[:10],
            })

    # LPR
    lpr_list = data.get("lpr") or []
    if isinstance(lpr_list, list) and lpr_list:
        latest = lpr_list[0] if isinstance(lpr_list[0], dict) else {}
        value = latest.get("1Y") or latest.get("lpr_1y") or latest.get("value")
        if value is not None:
            results.append({
                "indicator": "lpr",
                "period": latest.get("date", today)[:10],
                "value": float(value),
                "unit": "%",
                "prev_value": None,
                "source": "pboc",
                "published_at": latest.get("date", today)[:10],
            })

    return results


def normalize_usdcny(raw) -> list[dict]:
    """人民银行 USD/CNY → 统一格式

    get_currency_data("usdcny") 返回 dict: {status_code, data: {prices:[...], signals:{...}}}
    """
    results = []
    if not isinstance(raw, dict):
        return results

    data = raw.get("data", raw)
    if not isinstance(data, dict):
        return results

    prices = data.get("prices") or []
    if isinstance(prices, list) and prices:
        latest = prices[0] if isinstance(prices[0], dict) else {}
        value = latest.get("price") or latest.get("close") or latest.get("value")
        if value is not None:
            today = date.today().isoformat()
            results.append({
                "indicator": "usdcny",
                "period": latest.get("date", today)[:10],
                "value": float(value),
                "unit": "",
                "prev_value": None,
                "source": "pboc",
                "published_at": latest.get("date", today)[:10],
            })

    return results


# ==================== 聚合器 ====================


class MacroAggregator(BaseAggregator):
    """宏观政策聚合

    东方财富 9 个宏观指标 + 人民银行 Shibor/LPR + USD/CNY
    """

    data_domain = "macro"
    task_interval = 3600  # 1 小时

    def __init__(self):
        super().__init__()
        self._init_sources()
        self._exec_ddl(DDL)

    def _init_sources(self):
        from routers import _utils

        sources = []

        # 东方财富宏观指标 — 每个指标独立源，6 小时间隔
        for indicator_name, method_name, report_name, unit in EM_INDICATORS:
            normalizer = _make_em_normalizer(indicator_name, unit)
            # 用闭包捕获 method_name
            def make_fetch(mname, rname):
                async def fetch(cp):
                    if cp:
                        return await _utils.eastmoney.get_macro_since(rname, cp)
                    return await getattr(_utils.eastmoney, mname)()
                return fetch

            sources.append(SourceDef(
                f"em_{indicator_name}",
                make_fetch(method_name, report_name),
                21600,  # 6 小时
                normalizer,
            ))

        # 人民银行 Shibor + LPR — 日度，1 小时间隔
        sources.append(SourceDef(
            "pboc_shibor_lpr",
            lambda cp: _utils.pboc.get_currency_data("shibor"),
            3600,
            normalize_shibor_lpr,
        ))

        # 人民银行 USD/CNY — 日度，1 小时间隔
        sources.append(SourceDef(
            "pboc_usdcny",
            lambda cp: _utils.pboc.get_currency_data("usdcny"),
            3600,
            normalize_usdcny,
        ))

        self.sources = sources

    def _get_checkpoint(self, source_name: str):
        """从结果表推算 since_date"""
        try:
            indicator = source_name.replace("em_", "")
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT published_at::text
                        FROM ft_macro_indicators
                        WHERE indicator = %s
                        ORDER BY published_at DESC LIMIT 1
                    """, (indicator,))
                    row = cur.fetchone()
                    return row[0] if row else None
        except Exception:
            return None

    # ==================== 入库 ====================

    def _save(self, items: list[dict]) -> int:
        if not items:
            return 0
        columns = ["indicator", "period", "value", "unit", "prev_value", "source", "published_at"]
        rows = []
        for item in items:
            if not item.get("indicator") or not item.get("period"):
                continue
            rows.append((
                item["indicator"],
                item["period"],
                item["value"],
                item.get("unit", ""),
                item.get("prev_value"),
                item.get("source", ""),
                item.get("published_at"),
            ))
        return self._insert_many(
            "ft_macro_indicators", columns, rows,
            conflict_clause="ON CONFLICT (indicator, period, source) DO NOTHING",
        )

    # ==================== 查询 ====================

    async def query(
        self,
        indicator: str | None = None,
        since: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        conditions = []
        values = []
        if indicator:
            conditions.append("indicator = %s")
            values.append(indicator)
        if since:
            conditions.append("published_at >= %s")
            values.append(since)
        return self._query_table(
            "ft_macro_indicators",
            conditions=conditions or None,
            values=values or None,
            order_by="published_at DESC",
            limit=limit,
        )

    async def get_latest_indicators(self) -> list[dict]:
        """获取每个指标的最新值"""
        with get_conn() as conn:
            with conn.cursor(cursor_factory=__import__("psycopg2").extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT DISTINCT ON (indicator)
                        indicator, period, value, unit, prev_value, source, published_at
                    FROM ft_macro_indicators
                    ORDER BY indicator, published_at DESC
                """)
                return [dict(r) for r in cur.fetchall()]
