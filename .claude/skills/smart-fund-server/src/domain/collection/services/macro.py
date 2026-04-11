"""宏观政策聚合 (P1)

数据源: 东方财富宏观指标(CPI/PPI/PMI/GDP/M2/人民币贷款/外汇储备/固定资产投资/FDI)
        + 人民银行货币数据(LPR/Shibor/汇率)
目标表: ft_macro_indicators
"""

import json
import logging
from datetime import date

from src.domain.collection.services.base import BaseAggregator, SourceDef

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

    pboc.get_currency_data("shibor") 返回:
        {status_code, data: {
            shibor: {"隔夜": [{date,rate,change},...], "1周": [...], ...},
            lpr:    [{date, lpr1y, lpr5y}, ...]   # 按时间倒序
        }}
    """
    results = []
    if not isinstance(raw, dict):
        return results

    data = raw.get("data", raw)
    if not isinstance(data, dict):
        return results

    today = date.today().isoformat()

    # ── Shibor 各期限：每个期限作为独立指标 ──
    shibor_dict = data.get("shibor") or {}
    if isinstance(shibor_dict, dict):
        TERM_MAP = {
            "隔夜": "shibor_on",
            "1周": "shibor_1w",
            "1月": "shibor_1m",
            "3月": "shibor_3m",
        }
        for term_name, indicator_name in TERM_MAP.items():
            rows = shibor_dict.get(term_name) or []
            if not isinstance(rows, list) or not rows:
                continue
            for i, row in enumerate(rows[:30]):  # 入近 30 个交易日
                if not isinstance(row, dict):
                    continue
                rate = row.get("rate")
                if rate is None:
                    continue
                row_date = (row.get("date") or today)[:10]
                prev = rows[i + 1].get("rate") if i + 1 < len(rows) and isinstance(rows[i + 1], dict) else None
                results.append({
                    "indicator": indicator_name,
                    "period": row_date,
                    "value": float(rate),
                    "unit": "%",
                    "prev_value": float(prev) if prev is not None else None,
                    "source": "pboc",
                    "published_at": row_date,
                })

    # ── LPR 1y / 5y ──
    lpr_list = data.get("lpr") or []
    if isinstance(lpr_list, list):
        for i, row in enumerate(lpr_list[:24]):  # 近 24 期
            if not isinstance(row, dict):
                continue
            row_date = (row.get("date") or today)[:10]
            prev_row = lpr_list[i + 1] if i + 1 < len(lpr_list) and isinstance(lpr_list[i + 1], dict) else {}
            for field, indicator_name in (("lpr1y", "lpr_1y"), ("lpr5y", "lpr_5y")):
                val = row.get(field)
                if val is None:
                    continue
                prev = prev_row.get(field) if prev_row else None
                results.append({
                    "indicator": indicator_name,
                    "period": row_date,
                    "value": float(val),
                    "unit": "%",
                    "prev_value": float(prev) if prev is not None else None,
                    "source": "pboc",
                    "published_at": row_date,
                })

    return results


def normalize_usdcny(raw) -> list[dict]:
    """人民银行 USD/CNY → 统一格式

    pboc.get_currency_data("usdcny") 返回:
        {status_code, data: {
            tab, name, items: [{date, open, high, low, close}, ...],  # 时间升序
            ma5, ma20, ma60
        }}
    """
    results = []
    if not isinstance(raw, dict):
        return results

    data = raw.get("data", raw)
    if not isinstance(data, dict):
        return results

    items = data.get("items") or []
    if not isinstance(items, list) or not items:
        return results

    today = date.today().isoformat()
    # items 按时间升序，取近 60 天反转为最新优先
    rev = list(reversed(items[-60:]))
    for i, row in enumerate(rev):
        if not isinstance(row, dict):
            continue
        close = row.get("close")
        if close is None:
            continue
        row_date = (row.get("date") or today)[:10]
        prev_row = rev[i + 1] if i + 1 < len(rev) else None
        prev = prev_row.get("close") if isinstance(prev_row, dict) else None
        results.append({
            "indicator": "usdcny",
            "period": row_date,
            "value": float(close),
            "unit": "",
            "prev_value": float(prev) if prev is not None else None,
            "source": "pboc",
            "published_at": row_date,
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
        from src.infrastructure import clients

        sources = []

        # 东方财富宏观指标 — 每个指标独立源，6 小时间隔
        for indicator_name, method_name, report_name, unit in EM_INDICATORS:
            normalizer = _make_em_normalizer(indicator_name, unit)
            # 用闭包捕获 method_name
            def make_fetch(mname, rname):
                async def fetch(cp):
                    # cp 现在是 dict (R2.10 重构后),需要解出 since_date
                    since_date = None
                    if isinstance(cp, dict):
                        v = cp.get("max_trade_date")
                        if isinstance(v, str):
                            since_date = v
                    elif isinstance(cp, str):  # 向后兼容
                        since_date = cp
                    if since_date:
                        return await clients.eastmoney.get_macro_since(rname, since_date)
                    return await getattr(clients.eastmoney, mname)()
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
            lambda cp: clients.pboc.get_currency_data("shibor"),
            3600,
            normalize_shibor_lpr,
        ))

        # 人民银行 USD/CNY — 日度，1 小时间隔
        sources.append(SourceDef(
            "pboc_usdcny",
            lambda cp: clients.pboc.get_currency_data("usdcny"),
            3600,
            normalize_usdcny,
        ))

        self.sources = sources

    # ==================== 入库 ====================
    # 旧的 _get_checkpoint 已删除 (R2.5),checkpoint 由 base.py 通过
    # CollectionStateRepository 统一管理

    def _save(self, items: list[dict]) -> int:
        """改造后: 走 MacroRepository (R2.5)"""
        if not items:
            return 0
        clean = [it for it in items if it.get("indicator") and it.get("period")]
        from src.infrastructure.persistence.repositories import MacroRepositoryImpl
        return MacroRepositoryImpl().upsert_batch(clean)

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
        """获取每个指标的最新值 — R2.5 走 MacroRepository"""
        from src.infrastructure.persistence.repositories import MacroRepositoryImpl
        return MacroRepositoryImpl().latest_per_indicator()
