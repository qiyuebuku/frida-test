"""宏观政策聚合

数据源: 东方财富宏观指标(14 个月度/季度)
        + 人民银行货币数据(LPR/Shibor/汇率/OMO)
目标表: ft_macro_indicators + ft_macro_regime
"""

import logging
import math
import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from src.domain.collection.services.base import BaseAggregator, SourceDef
from src.infrastructure.time_utils import app_today_iso

logger = logging.getLogger(__name__)

# ==================== 指标配置 ====================

# CPI/PPI/FAI 原始值本身就是同比 %，不再二次算 yoy
PERCENT_IS_YOY = {"cpi", "ppi", "fai", "retail_sales", "m2", "customs_export", "gdp", "industrial_va"}

# 东方财富宏观指标: (indicator_name, report_name, unit, dim_tag)
EM_INDICATORS = [
    ("cpi",             "RPT_ECONOMY_CPI",             "%",     "inflation"),
    ("ppi",             "RPT_ECONOMY_PPI",             "%",     "inflation"),
    ("pmi",             "RPT_ECONOMY_PMI",             "",      "growth"),
    ("gdp",             "RPT_ECONOMY_GDP",             "亿元",  "growth"),
    ("industrial_va",   "RPT_ECONOMY_INDUS_GROW",     "%",     "growth"),
    ("m2",              "RPT_ECONOMY_CURRENCY_SUPPLY", "亿元",  "liquidity"),
    ("rmb_loan",        "RPT_ECONOMY_RMB_LOAN",        "亿元",  "liquidity"),
    ("forex_reserve",   "RPT_ECONOMY_GOLD_CURRENCY",   "亿美元", "external"),
    ("fai",             "RPT_ECONOMY_ASSET_INVEST",    "%",     "growth"),
    ("retail_sales",    "RPT_ECONOMY_TOTAL_RETAIL",    "%",     "growth"),
    ("customs_export",  "RPT_ECONOMY_CUSTOMS",         "亿美元", "external"),
    ("fdi",             "RPT_ECONOMY_FDI_NEW",         "亿美元", "external"),
]

# EM 自带同比字段的指标（value 是绝对值，但有独立的 yoy 字段）
EM_YOY_FIELDS = {
    "rmb_loan": "RMB_LOAN_SAME",   # 人民币贷款同比 %
    "fdi": "ACTUAL_FOREIGN_SAME",  # FDI 同比比率（需 ×100 转为 %）
}

# EM 同比字段值为比率（0~1），需 ×100 转为百分比
EM_YOY_RATIO_FIELDS = {"fdi"}


# ==================== Normalize 函数 ====================


def _extract_period(report_date: str) -> str:
    """从 REPORT_DATE 提取 period

    '2026-02-01 00:00:00' → '2026-02'
    '2024-12-01 00:00:00' (FDI 年度) → '2024'
    """
    if not report_date:
        return ""
    date_str = report_date[:10]
    return date_str[:7]  # YYYY-MM


def _extract_value(item: dict) -> float | None:
    """从东方财富宏观数据提取主要值

    不同指标的字段名不同，按优先级尝试
    """
    for key in (
        "NATIONAL_SAME",        # CPI/PPI 同比
        "MAKE_INDEX",           # PMI 指数（非 MAKE_SAME 环比）
        "SUM_SAME",             # GDP 同比
        "SAMEINDUS_GDP",        # GDP 同比（旧版字段名）
        "BASIC_CURRENCY_SAME",  # M2 同比（而非绝对值）
        "BASIC_CURRENCY",       # M2 绝对值（兜底）
        "RMB_LOAN",             # 人民币贷款
        "FOREX",                # 外汇储备（亿美元）
        "GOLD_RESERVES",        # 黄金储备（兜底）
        "BASE_SAME",            # 固定资产投资当月同比
        "ACTUAL_FOREIGN",       # FDI 年度绝对值（亿美元，RPT_ECONOMY_FDI_NEW）
        "FDI_US",               # FDI（旧版月度）
        "FOREIGN_ACCUMULATE_SAME",  # FDI 累计同比（旧版兜底）
        "RETAIL_TOTAL_SAME",    # 社零当月同比
        "RETAIL_ACCUMULATE_SAME",  # 社零累计同比（1-2 月合并时当月为空）
        "EXIT_BASE_SAME",       # 出口同比
        "EXIT_BASE",            # 出口金额
    ):
        val = item.get(key)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                continue
    logger.debug("未匹配到已知数值字段: %s", list(item.keys())[:10])
    return None


def _compute_yoy_mom(rows: list[dict]) -> None:
    """对同一 indicator 的列表回填 yoy/mom（就地修改）"""
    by_ind: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_ind[r["indicator"]].append(r)
    for ind, lst in by_ind.items():
        lst.sort(key=lambda x: x["period"])
        for i, r in enumerate(lst):
            if ind in PERCENT_IS_YOY:
                r["yoy"] = r["value"]
                r["mom"] = None
                continue
            # PMI 是扩散指数，yoy/mom 无意义
            if ind == "pmi":
                r["yoy"] = None
                r["mom"] = None
                continue
            r["mom"] = (
                (r["value"] - lst[i - 1]["value"]) / lst[i - 1]["value"] * 100
                if i >= 1 and lst[i - 1].get("value")
                else None
            )
            # yoy: 保留 EM 自带值（如 rmb_loan 的 RMB_LOAN_SAME），否则从历史数据计算
            if r.get("yoy") is None:
                r["yoy"] = (
                    (r["value"] - lst[i - 12]["value"]) / lst[i - 12]["value"] * 100
                    if i >= 12 and lst[i - 12].get("value")
                    else None
                )


def _make_em_normalizer(indicator_name: str, unit: str, dim_tag: str):
    """工厂函数：生成东方财富指标的 normalize 函数"""

    yoy_field = EM_YOY_FIELDS.get(indicator_name)

    def normalizer(raw_items: list) -> list[dict]:
        if not isinstance(raw_items, list):
            return []
        results = []
        prev_value = None
        for i, item in enumerate(reversed(raw_items)):
            report_date = item.get("REPORT_DATE") or ""
            period = _extract_period(report_date)
            value = _extract_value(item)
            if period and value is not None:
                row = {
                    "indicator": indicator_name,
                    "period": period,
                    "value": value,
                    "unit": unit,
                    "prev_value": prev_value,
                    "source": "eastmoney",
                    "published_at": report_date[:10],
                    "dim_tag": dim_tag,
                }
                # 提取 EM 自带的同比字段（如 rmb_loan 的 RMB_LOAN_SAME）
                if yoy_field:
                    em_yoy = item.get(yoy_field)
                    if em_yoy is not None:
                        try:
                            yoy_val = float(em_yoy)
                            if indicator_name in EM_YOY_RATIO_FIELDS:
                                yoy_val = round(yoy_val * 100, 2)
                            row["yoy"] = yoy_val
                        except (ValueError, TypeError):
                            pass
                results.append(row)
                prev_value = value
        _compute_yoy_mom(results)
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

    today = app_today_iso()

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
                    "dim_tag": "liquidity",
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
                    "dim_tag": "liquidity",
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

    today = app_today_iso()
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
            "dim_tag": "external",
        })

    return results


# ==================== 社会融资规模 ====================


async def fetch_social_financing(cp: dict | None = None) -> list[dict]:
    """通过 AKShare 获取社会融资规模增量数据（商务部数据源）

    Returns: [{month, total, rmb_loan, ...}, ...]
    """
    import asyncio
    import akshare as ak

    loop = asyncio.get_event_loop()
    df = await loop.run_in_executor(None, ak.macro_china_shrzgm)
    return df.to_dict("records")


def normalize_social_financing(raw_items: list) -> list[dict]:
    """社融增量 → 统一格式

    AKShare macro_china_shrzgm 返回:
        月份(YYYYMM), 社会融资规模增量, 其中-人民币贷款, ...
    """
    if not isinstance(raw_items, list) or not raw_items:
        return []

    results = []
    for item in raw_items:
        month_raw = str(item.get("月份", ""))
        if len(month_raw) != 6:
            continue
        period = f"{month_raw[:4]}-{month_raw[4:]}"

        total = item.get("社会融资规模增量")
        if total is None:
            continue
        try:
            total = float(total)
        except (ValueError, TypeError):
            continue

        results.append({
            "indicator": "social_financing",
            "period": period,
            "value": total,
            "unit": "亿元",
            "source": "mofcom",
            "published_at": f"{period}-01",
            "dim_tag": "liquidity",
        })

    _compute_yoy_mom(results)
    return results


# ==================== 海外宏观指标 (AKShare) ====================


async def _akshare_fetch(func_name: str) -> list[dict]:
    """在异步执行器中调用 AKShare 同步函数"""
    import asyncio
    import akshare as ak

    loop = asyncio.get_event_loop()
    fn = getattr(ak, func_name)
    df = await loop.run_in_executor(None, fn)
    return df.to_dict("records")


async def fetch_us_cpi(cp: dict | None = None) -> list[dict]:
    """美国 CPI 同比 (AKShare macro_usa_cpi_yoy)"""
    return await _akshare_fetch("macro_usa_cpi_yoy")


def normalize_us_cpi(raw_items: list) -> list[dict]:
    """US CPI YoY → 统一格式

    AKShare 返回: [{时间, 发布日期, 现值, 前值}, ...]
    """
    if not isinstance(raw_items, list) or not raw_items:
        return []

    results = []
    for item in raw_items:
        # 时间是 date 类型或字符串
        time_val = item.get("时间")
        if time_val is None:
            continue
        period = str(time_val)[:7]  # YYYY-MM
        value = item.get("现值")
        if value is None or (isinstance(value, float) and str(value) == "nan"):
            continue
        try:
            value = float(value)
        except (ValueError, TypeError):
            continue

        pub_date = item.get("发布日期", "")
        pub_str = str(pub_date)[:10] if pub_date else None

        results.append({
            "indicator": "us_cpi_yoy",
            "period": period,
            "value": value,
            "unit": "%",
            "source": "akshare",
            "published_at": pub_str,
            "dim_tag": "external",
        })

    return results


async def fetch_cnus_yield(cp: dict | None = None) -> list[dict]:
    """中美国债收益率 + 利差 (AKShare bond_zh_us_rate)"""
    return await _akshare_fetch("bond_zh_us_rate")


def normalize_cnus_yield(raw_items: list) -> list[dict]:
    """中美国债收益率 → 10 年期利差

    AKShare 返回: [{日期, 中国国债收益率10年, 美国国债收益率10年, ...}, ...]
    """
    if not isinstance(raw_items, list) or not raw_items:
        return []

    results = []
    for item in raw_items[-90:]:  # 只取最近 90 天
        date_val = item.get("日期")
        if date_val is None:
            continue
        date_str = str(date_val)[:10]
        if not date_str or len(date_str) < 10:
            continue

        cn_10y = item.get("中国国债收益率10年")
        us_10y = item.get("美国国债收益率10年")
        if cn_10y is None or us_10y is None:
            continue
        try:
            cn_val, us_val = float(cn_10y), float(us_10y)
            if math.isnan(cn_val) or math.isnan(us_val):
                continue
            spread = cn_val - us_val
        except (ValueError, TypeError):
            continue

        results.append({
            "indicator": "cnus_spread_10y",
            "period": date_str,
            "value": round(spread, 4),
            "unit": "%",
            "source": "akshare",
            "published_at": date_str,
            "dim_tag": "external",
        })

    return results


# ==================== OMO 净投放 ====================

# 逆回购: "开展了X亿元Y天期逆回购操作"
_RE_REVERSE_REPO = re.compile(r"开展了(\d+)亿元(\d+)天期逆回购操作")
# MLF: "开展了X亿元中期借贷便利(MLF)操作"
_RE_MLF = re.compile(r"开展了(\d+)亿元.*?中期借贷便利")
# 国债买卖: "买入/卖出国债X亿元" (较少见)
_RE_TREASURY_BUY = re.compile(r"买入.*?国债.*?(\d+)亿元")
_RE_TREASURY_SELL = re.compile(r"卖出.*?国债.*?(\d+)亿元")


async def fetch_omo_announcements(cp: dict | None = None) -> list[dict]:
    """获取 OMO 公告列表 + 全文

    fetch OMO announcements with full content for amount extraction.
    Returns [{published_at, title, url, content}, ...] (倒序)
    """
    from src.infrastructure import clients

    limit = 50
    if isinstance(cp, dict):
        config = cp.get("_config", {})
        limit = config.get("page_size", 50)

    announcements = await clients.pboc.get_omo_announcements(limit=limit)
    results = []
    for ann in announcements:
        url = ann.get("url", "")
        content = ""
        if url:
            try:
                content = await clients.pboc.get_content(url) or ""
            except Exception:
                logger.warning("OMO content fetch failed: %s", url)
        results.append({
            "published_at": ann.get("published_at", ""),
            "title": ann.get("title", ""),
            "url": url,
            "content": content,
        })
    return results


def normalize_omo_net(raw_items: list) -> list[dict]:
    """解析 OMO 公告全文 → 每日净投放（亿元）

    净投放 = 投放(逆回购/MLF/买入国债) - 回笼(到期逆回购/MLF/卖出国债)
    逆回购到期: 按期限(T天)自动计算 T 天后的到期金额
    MLF 到期:  通常 1 年期
    """
    if not isinstance(raw_items, list) or not raw_items:
        return []

    injections_by_date: dict[str, float] = defaultdict(float)
    maturity_by_date: dict[str, float] = defaultdict(float)

    for ann in raw_items:
        date_str = (ann.get("published_at") or "")[:10]
        if not date_str:
            continue
        content = ann.get("content", "")

        # ── 逆回购 ──
        for m in _RE_REVERSE_REPO.finditer(content):
            amount = float(m.group(1))
            term_days = int(m.group(2))
            injections_by_date[date_str] += amount
            mat_date = (datetime.strptime(date_str, "%Y-%m-%d")
                        + timedelta(days=term_days)).strftime("%Y-%m-%d")
            maturity_by_date[mat_date] += amount

        # ── MLF ──
        for m in _RE_MLF.finditer(content):
            amount = float(m.group(1))
            injections_by_date[date_str] += amount
            # MLF 通常 1 年期到期
            mat_date = (datetime.strptime(date_str, "%Y-%m-%d")
                        + timedelta(days=365)).strftime("%Y-%m-%d")
            maturity_by_date[mat_date] += amount

        # ── 国债买卖 ──
        for m in _RE_TREASURY_BUY.finditer(content):
            injections_by_date[date_str] += float(m.group(1))
        for m in _RE_TREASURY_SELL.finditer(content):
            injections_by_date[date_str] -= float(m.group(1))

    # 计算每日净投放
    all_dates = sorted(set(
        list(injections_by_date.keys()) + list(maturity_by_date.keys())
    ))

    results = []
    for d in all_dates:
        net = injections_by_date.get(d, 0) - maturity_by_date.get(d, 0)
        if net == 0 and d not in injections_by_date:
            continue  # 纯到期日、无操作的日期跳过
        results.append({
            "indicator": "omo_net",
            "period": d,
            "value": round(net, 2),
            "unit": "亿元",
            "source": "pboc",
            "published_at": d,
            "dim_tag": "liquidity",
        })

    return results


# ==================== 聚合器 ====================


class MacroAggregator(BaseAggregator):
    """宏观政策聚合

    东方财富 14+ 个宏观指标 + 人民银行 Shibor/LPR + USD/CNY + OMO + 社融
    """

    data_domain = "macro"
    task_interval = 3600  # 1 小时

    SOURCE_CONFIGS = {
        **{f"em_{name}": {"target_days": 365, "page_size": 24, "interval": 21600}
           for name, _, _, _ in EM_INDICATORS},
        "pboc_shibor_lpr":      {"target_days": 90, "page_size": 30, "interval": 3600},
        "pboc_usdcny":          {"target_days": 90, "page_size": 60, "interval": 3600},
        "pboc_omo_net":         {"target_days": 60, "page_size": 50, "interval": 21600},
        "mofcom_social_fin":    {"target_days": 365, "page_size": 132, "interval": 21600},
        "akshare_us_cpi":       {"target_days": 365, "page_size": 219, "interval": 21600},
        "akshare_cnus_yield":   {"target_days": 90, "page_size": 90, "interval": 21600},
    }
    BACKFILL_SOURCES = frozenset(SOURCE_CONFIGS)

    def __init__(self):
        super().__init__()
        self._init_sources()

    def _init_sources(self):
        from src.infrastructure import clients

        sources = []

        # 东方财富宏观指标 — 每个指标独立源，6 小时间隔
        for indicator_name, report_name, unit, dim_tag in EM_INDICATORS:
            normalizer = _make_em_normalizer(indicator_name, unit, dim_tag)

            def make_fetch(rname):
                async def fetch(cp):
                    since_date = None
                    if isinstance(cp, dict):
                        v = cp.get("max_trade_date")
                        if isinstance(v, str):
                            since_date = v
                    elif isinstance(cp, str):
                        since_date = cp
                    if since_date:
                        return await clients.eastmoney.get_macro_since(rname, since_date)
                    return await clients.eastmoney.get_macro_indicator(rname)
                return fetch

            sources.append(SourceDef(
                f"em_{indicator_name}",
                make_fetch(report_name),
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

        # 人民银行 OMO 净投放 — 日度，6 小时间隔
        sources.append(SourceDef(
            "pboc_omo_net",
            fetch_omo_announcements,
            21600,
            normalize_omo_net,
        ))

        # 社会融资规模 — 商务部，6 小时间隔
        sources.append(SourceDef(
            "mofcom_social_fin",
            fetch_social_financing,
            21600,
            normalize_social_financing,
        ))

        # 美国 CPI 同比 — AKShare，6 小时间隔
        sources.append(SourceDef(
            "akshare_us_cpi",
            fetch_us_cpi,
            21600,
            normalize_us_cpi,
        ))

        # 中美国债利差 — AKShare，6 小时间隔
        sources.append(SourceDef(
            "akshare_cnus_yield",
            fetch_cnus_yield,
            21600,
            normalize_cnus_yield,
        ))

        self.sources = sources

    # ==================== 入库 ====================
    # 旧的 _get_checkpoint 已删除 (R2.5),checkpoint 由 base.py 通过
    # CollectionStateRepository 统一管理

    def _save(self, items: list[dict]) -> int:
        if not items:
            return 0
        clean = [it for it in items if it.get("indicator") and it.get("period")]
        from src.infrastructure.persistence.repositories import MacroRepositoryImpl
        n = MacroRepositoryImpl().upsert_batch(clean)
        if n > 0:
            try:
                from src.domain.collection.services.macro_regime import MacroRegimeEngine
                MacroRegimeEngine().recompute()
            except Exception as e:
                logger.warning("regime recompute failed: %s", e)
        return n

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
        from src.infrastructure.persistence.repositories import MacroRepositoryImpl
        return MacroRepositoryImpl().latest_per_indicator()

    async def get_current_regime(self) -> dict | None:
        from src.infrastructure.persistence.repositories import MacroRepositoryImpl
        return MacroRepositoryImpl().get_current_regime()

    async def get_regime_history(self, days: int = 30) -> list[dict]:
        from src.infrastructure.persistence.repositories import MacroRepositoryImpl
        return MacroRepositoryImpl().get_regime_history(days)
