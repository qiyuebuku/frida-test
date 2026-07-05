"""情绪舆情聚合 (P1)

数据源: 股吧人气(EM)、涨跌停池(THS)、雪球热门话题/热股(Xueqiu)、
        腾讯热门股(Tencent)、问财选股(THS)
目标表: ft_sentiment

L1 原始采集 ✅ + L2 派生信号 (sentiment_score / overheat_set / leading_theme)
"""

import logging
from collections import Counter
from datetime import date, datetime

from src.domain.collection.services.base import BaseAggregator, SourceDef
from src.infrastructure.time_utils import app_now, app_today, app_today_iso

logger = logging.getLogger(__name__)


def _today() -> str:
    return app_today_iso()


def _is_empty(data) -> bool:
    """判断 data 是否为空（空 dict / 空 list / None）"""
    if data is None:
        return True
    if isinstance(data, (dict, list, str)) and len(data) == 0:
        return True
    return False


def _wrap(data_type: str, data) -> list[dict]:
    """封装为聚合结果，空数据直接跳过"""
    if _is_empty(data):
        return []
    return [{
        "data_type": data_type,
        "trade_date": _today(),
        "data": data,
    }]


def _normalize_code(code) -> str:
    """归一化股票代码为纯 6 位数字

    支持输入: 'SZ000001', 'sz000001', 'sh600519', '000001', 600519
    输出: '000001', '600519'
    """
    code = str(code).strip().upper()
    for prefix in ("SH", "SZ", "BJ"):
        if code.startswith(prefix):
            code = code[len(prefix):]
            break
    return code.zfill(6)[:6]


# ==================== Normalize 函数 ====================


def normalize_guba_popularity(raw) -> list[dict]:
    """东方财富股吧人气排行 → 统一格式

    get_guba_popularity 返回 list: [{sc, rk, rc, hisRc}, ...]
    """
    items = raw if isinstance(raw, list) else []
    return _wrap("guba_popularity", items)


def normalize_limit_pool(raw) -> list[dict]:
    """同花顺涨停/跌停池 → 统一格式

    get_limit_pool 返回 dict: {status_code, data: {info:[...], date, ...}}
    """
    if not raw:
        return []
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(data, dict):
        info = data.get("info") or data.get("list") or data.get("items")
        if _is_empty(info):
            return []
    return _wrap("limit_pool", data)


def normalize_xueqiu_hot_topics(raw) -> list[dict]:
    """雪球热门话题 → 统一格式

    get_hot_topics 返回 dict: {data: {count, topics:[...]}, status_code}
    """
    if not raw:
        return []
    items = None
    if isinstance(raw, dict):
        data = raw.get("data", {})
        if isinstance(data, dict):
            items = data.get("topics") or data.get("items") or data.get("list")
    return _wrap("xueqiu_hot_topics", items)


def normalize_xueqiu_hot_stocks(raw) -> list[dict]:
    """雪球热股排行 → 统一格式

    get_hot_stocks 返回 dict: {data: {count, stocks:[...]}, status_code}
    """
    if not raw:
        return []
    items = None
    if isinstance(raw, dict):
        data = raw.get("data", {})
        if isinstance(data, dict):
            items = data.get("stocks") or data.get("items") or data.get("stock_list")
    return _wrap("xueqiu_hot_stocks", items)


def normalize_tencent_hot_stocks(raw) -> list[dict]:
    """腾讯热门股 → 统一格式

    get_hot_stocks 返回 dict: {data: {5min:[...], 1hour:[...]}, status_code}
    """
    if not raw:
        return []
    data = None
    if isinstance(raw, dict):
        data = raw.get("data")
        if isinstance(data, dict):
            has_content = any(not _is_empty(v) for v in data.values())
            if not has_content:
                return []
    return _wrap("tencent_hot_stocks", data)


def normalize_guba_posts(raw) -> list[dict]:
    """东方财富股吧帖子 → 统一格式

    支持两种输入：
    1. 单个 dict：{status_code, data: {code, posts: [...]}}
    2. dict 列表：[{...}, {...}]（来自 _fetch_guba_posts_for_held_stocks 的批量结果）
    每只股票生成一条 ft_sentiment 记录（data_type=guba_posts），data 包含股票代码 + 帖子列表
    """
    if not raw:
        return []

    if isinstance(raw, list):
        results = []
        for item in raw:
            results.extend(normalize_guba_posts(item))
        return results

    if not isinstance(raw, dict):
        return []
    data = raw.get("data") or {}
    if not isinstance(data, dict):
        return []
    posts = data.get("posts") or data.get("list") or []
    if _is_empty(posts):
        return []
    code = data.get("code") or ""
    return [{
        "data_type": "guba_posts",
        "trade_date": _today(),
        "data": {
            "code": code,
            "count": len(posts),
            "posts": posts,
        },
    }]


async def _fetch_guba_posts_for_held_stocks(em_client, ths_client, max_stocks: int = 10) -> list:
    """从 watchlist 读取自选股拉取股吧帖子"""
    import asyncio as _asyncio
    from src.infrastructure.db import checkpoint_store

    all_items = checkpoint_store.list_all("watchlist")
    codes = [
        item["source_name"]
        for item in all_items
        if item.get("enabled", True) and item.get("config", {}).get("type") in ("stock", None)
    ][:max_stocks]

    if not codes:
        return []
    pure_codes = [c[2:] if c[:2] in ("sh", "sz", "bj") else c for c in codes]

    tasks = [em_client.get_guba_posts(code) for code in pure_codes]
    results = await _asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in results if not isinstance(r, Exception) and r]


# ==================== 聚合器 ====================


class SentimentAggregator(BaseAggregator):
    """情绪舆情聚合

    L1: 7 个数据源，统一采集到 ft_sentiment。
    L2: 派生信号 — sentiment_score / overheat_set / leading_theme / market_temperature
    """

    data_domain = "sentiment"
    task_interval = 900  # 15 分钟

    SOURCE_CONFIGS = {
        "guba_popularity":    {"target_days": 0, "interval": 1800, "default_mode": "incremental"},
        "limit_pool_up":      {"target_days": 0, "interval": 10800, "default_mode": "incremental"},
        "limit_pool_down":    {"target_days": 0, "interval": 10800, "default_mode": "incremental"},
        "xueqiu_hot_topics":  {"target_days": 0, "interval": 1800, "default_mode": "incremental"},
        "xueqiu_hot_stocks":  {"target_days": 0, "interval": 1800, "default_mode": "incremental"},
        "tencent_hot_stocks": {"target_days": 0, "interval": 1800, "default_mode": "incremental"},
        "guba_posts":         {"target_days": 0, "interval": 1800, "default_mode": "incremental"},
    }

    def __init__(self):
        super().__init__()
        self._init_sources()
        # L2 缓存：trade_date → 当日全量 ft_sentiment 行
        self._cache: dict[str, list[dict]] = {}
        # temperature v2: trade_date → 上一次计算的温度值（用于 trend）
        self._prev_temperature: dict[str, int] = {}

    def _init_sources(self):
        from src.infrastructure import clients

        self.sources = [
            SourceDef(
                "guba_popularity",
                lambda cp: clients.eastmoney.get_guba_popularity(),
                1800,
                normalize_guba_popularity,
            ),
            SourceDef(
                "limit_pool_up",
                lambda cp: clients.ths.get_limit_pool("up"),
                10800,
                normalize_limit_pool,
            ),
            SourceDef(
                "limit_pool_down",
                lambda cp: clients.ths.get_limit_pool("down"),
                10800,
                normalize_limit_pool,
            ),
            SourceDef(
                "xueqiu_hot_topics",
                lambda cp: clients.xueqiu.get_hot_topics(),
                1800,
                normalize_xueqiu_hot_topics,
            ),
            SourceDef(
                "xueqiu_hot_stocks",
                lambda cp: clients.xueqiu.get_hot_stocks(),
                1800,
                normalize_xueqiu_hot_stocks,
            ),
            SourceDef(
                "tencent_hot_stocks",
                lambda cp: clients.tencent.get_hot_stocks(),
                1800,
                normalize_tencent_hot_stocks,
            ),
            SourceDef(
                "guba_posts",
                lambda cp: _fetch_guba_posts_for_held_stocks(clients.eastmoney, clients.ths),
                1800,
                normalize_guba_posts,
            ),
        ]

    def _get_checkpoint(self, source_name: str):
        return None

    # ==================== 入库 ====================

    def _save(self, items: list[dict]) -> int:
        if not items:
            return 0
        clean = []
        for item in items:
            if not item.get("data_type") or not item.get("trade_date"):
                continue
            data = item.get("data")
            if _is_empty(data):
                continue
            clean.append({
                "data_type": item["data_type"],
                "trade_date": item["trade_date"],
                "data": data,
            })
        from src.infrastructure.persistence.repositories import SentimentRepositoryImpl
        return SentimentRepositoryImpl().upsert_batch(clean)

    # ==================== 查询 ====================

    async def query(
        self,
        data_type: str | None = None,
        trade_date: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        conditions = []
        values = []
        if data_type:
            conditions.append("data_type = %s")
            values.append(data_type)
        if trade_date:
            conditions.append("trade_date = %s")
            values.append(trade_date)
        return self._query_table(
            "ft_sentiment",
            conditions=conditions or None,
            values=values or None,
            order_by="created_at DESC",
            limit=limit,
        )

    # ==================== L2 缓存 ====================

    async def _ensure_cache(self) -> list[dict]:
        """确保当日全量数据已缓存，返回所有行"""
        today = _today()
        if today not in self._cache:
            rows = await self.query(trade_date=today, limit=500)
            self._cache[today] = rows
        return self._cache[today]

    def _get_cached_by_type(self, rows: list[dict], data_type: str) -> list[dict]:
        """从缓存行中筛选指定 data_type 的 data 字段列表"""
        results = []
        for r in rows:
            if r.get("data_type") == data_type:
                d = r.get("data")
                if d and not _is_empty(d):
                    results.append(d)
        return results

    # ==================== L2: 市场温度 v2 ====================

    async def get_market_temperature(self) -> dict:
        """市场情绪温度 v2（综合多指标 + 方向指示）

        Returns:
            {"temperature": 0-100, "level": str, "trend": str, "signals": list}
        """
        try:
            return await self._calc_market_temperature()
        except Exception:
            logger.warning("get_market_temperature 失败，返回中性默认值", exc_info=True)
            return {"temperature": 50, "level": "warm", "trend": "flat",
                    "signals": []}

    async def _calc_market_temperature(self) -> dict:
        today = _today()
        rows = await self._ensure_cache()
        temperature = 50
        signals = []

        # 1. 涨停/跌停比（纳入 limit_pool_down）
        up_data_list = self._get_cached_by_type(rows, "limit_pool")
        # limit_pool_up 和 limit_pool_down 都写入 data_type=limit_pool，
        # 需要从 source_name 区分。但实际 normalize 后区分丢失。
        # 改为直接从 limit_pool 的 data 中读取 info 数量
        up_count = 0
        down_count = 0
        for d in up_data_list:
            if isinstance(d, dict):
                info = d.get("info") or []
                if isinstance(info, list):
                    # 通过 info 中的 change 字段判断涨跌停方向
                    for item in info:
                        if isinstance(item, dict):
                            change = item.get("change") or item.get("price_change")
                            if change is not None:
                                try:
                                    if float(change) > 0:
                                        up_count += 1
                                    else:
                                        down_count += 1
                                except (ValueError, TypeError):
                                    up_count += 1  # 无法判断时默认涨停
                            else:
                                up_count += 1

        # 如果无法从 info 区分，用 total 字段
        if up_count == 0 and down_count == 0:
            for d in up_data_list:
                if isinstance(d, dict):
                    total = d.get("total") or d.get("count") or 0
                    if total and total > up_count:
                        up_count = total
            down_count = 0  # 无跌停数据时按旧逻辑只看涨停

        if up_count > 0 or down_count > 0:
            total_pool = up_count + down_count
            if total_pool > 0:
                ratio = up_count / total_pool
                if ratio > 0.7:
                    temperature += 20
                    signals.append({"kind": "limit_ratio", "delta": 20,
                                    "text": f"涨停{up_count}/跌停{down_count}，多方占优"})
                elif ratio < 0.3:
                    temperature -= 20
                    signals.append({"kind": "limit_ratio", "delta": -20,
                                    "text": f"涨停{up_count}/跌停{down_count}，空方占优"})

            # 旧逻辑保底：绝对数量阈值
            if up_count > 80:
                delta = 20 if not any(s["kind"] == "limit_ratio" and s["delta"] == 20 for s in signals) else 0
                if delta:
                    signals.append({"kind": "limit_absolute", "delta": 5,
                                    "text": f"涨停 {up_count} 只，绝对数量活跃"})
            elif up_count < 20 and up_count > 0:
                signals.append({"kind": "limit_absolute", "delta": -5,
                                "text": f"涨停仅 {up_count} 只，市场冷淡"})

        # 2. 热股重叠度
        xq_stocks = self._extract_stock_codes(rows, "xueqiu_hot_stocks", top_n=20)
        tc_stocks = self._extract_tencent_codes(rows, top_n=20)
        overlap = xq_stocks & tc_stocks
        if len(xq_stocks) > 0 and len(tc_stocks) > 0:
            if len(overlap) > 8:
                temperature += 10
                signals.append({"kind": "hot_overlap", "delta": 10,
                                "text": f"雪球∩腾讯热股重叠 {len(overlap)} 只，市场聚焦度高"})
            elif len(overlap) < 3:
                temperature -= 5
                signals.append({"kind": "hot_overlap", "delta": -5,
                                "text": f"热股重叠仅 {len(overlap)} 只，情绪分散"})

        # 3. 股吧人气
        guba_rows = self._get_cached_by_type(rows, "guba_popularity")
        if guba_rows:
            top_items = guba_rows[0] if isinstance(guba_rows[0], list) else []
            if isinstance(top_items, list) and len(top_items) > 0:
                # 取 top10 的热度均值
                top10 = top_items[:10]
                rc_values = []
                for item in top10:
                    if isinstance(item, dict):
                        rc = item.get("rc") or item.get("sc") or 0
                        try:
                            rc_values.append(float(rc))
                        except (ValueError, TypeError):
                            pass
                if rc_values:
                    avg_rc = sum(rc_values) / len(rc_values)
                    if avg_rc > 50000:
                        temperature += 10
                        signals.append({"kind": "guba_heat", "delta": 10,
                                        "text": f"股吧 top10 平均热度 {avg_rc:.0f}，讨论热烈"})

        # clip
        temperature = max(0, min(100, temperature))

        # 分级
        if temperature >= 80:
            level = "extreme"
        elif temperature >= 60:
            level = "hot"
        elif temperature >= 40:
            level = "warm"
        else:
            level = "cold"

        # trend 方向
        prev = self._prev_temperature.get(today)
        self._prev_temperature[today] = temperature
        if prev is not None:
            diff = temperature - prev
            if diff > 5:
                trend = "rising"
            elif diff < -5:
                trend = "falling"
            else:
                trend = "flat"
        else:
            trend = "flat"

        return {"temperature": temperature, "level": level, "trend": trend,
                "signals": signals}

    def _extract_stock_codes(self, rows: list[dict], data_type: str,
                             top_n: int = 20) -> set[str]:
        """从缓存行中提取归一化后的股票代码集合"""
        codes = set()
        for data in self._get_cached_by_type(rows, data_type):
            if isinstance(data, list):
                items = data[:top_n]
            elif isinstance(data, dict):
                stocks = data.get("stocks") or data.get("items") or []
                items = stocks[:top_n]
            else:
                continue
            for item in items:
                if isinstance(item, dict):
                    raw_code = item.get("code") or item.get("stock_code") or ""
                    if raw_code:
                        codes.add(_normalize_code(raw_code))
        return codes

    def _extract_tencent_codes(self, rows: list[dict], top_n: int = 20) -> set[str]:
        """腾讯热股特殊处理：data 是 {5min: [...], 1hour: [...]}"""
        codes = set()
        for data in self._get_cached_by_type(rows, "tencent_hot_stocks"):
            if not isinstance(data, dict):
                continue
            for period, stocks in data.items():
                if isinstance(stocks, list):
                    for item in stocks[:top_n]:
                        if isinstance(item, dict):
                            raw_code = item.get("code") or item.get("stock_code") or ""
                            if raw_code:
                                codes.add(_normalize_code(raw_code))
        return codes

    # ==================== L2: sentiment_score ====================

    async def get_sentiment_score(self, code: str | None = None) -> float:
        """情绪得分 ∈ [0, 1]，0.5 = 中性

        Args:
            code: 股票代码（支持 sh600519/600519/SZ000001 等格式）。
                  None 时返回市场整体情绪。
        """
        try:
            if code is None:
                return await self._market_sentiment_score()
            return await self._stock_sentiment_score(code)
        except Exception:
            logger.warning("get_sentiment_score(%s) 失败，返回 0.5", code, exc_info=True)
            return 0.5

    async def _market_sentiment_score(self) -> float:
        """市场整体情绪 → 从 temperature 映射"""
        temp_result = await self.get_market_temperature()
        temperature = temp_result.get("temperature", 50)

        score = temperature / 100.0

        # 极端过热反向惩罚
        if temperature > 80:
            score = 0.6 - (temperature - 80) / 100.0

        # 极端冷淡保底
        if temperature < 20:
            score = max(0.2, score)

        return max(0.0, min(1.0, score))

    async def _stock_sentiment_score(self, code: str) -> float:
        """单股情绪得分"""
        nc = _normalize_code(code)
        rows = await self._ensure_cache()
        score = 0.5  # 中性起点

        # 1. 雪球热股上榜 + 排名前 10
        xq_codes = []
        for data in self._get_cached_by_type(rows, "xueqiu_hot_stocks"):
            if isinstance(data, list):
                xq_codes = data
            elif isinstance(data, dict):
                xq_codes = data.get("stocks") or data.get("items") or []
        for i, item in enumerate(xq_codes[:10]):
            if isinstance(item, dict):
                if _normalize_code(item.get("code") or item.get("stock_code") or "") == nc:
                    score += 0.10
                    break

        # 2. 腾讯热股上榜（任一时段）
        tc_codes = self._extract_tencent_codes(rows)
        if nc in tc_codes:
            score += 0.10

        # 3. 股吧人气前 50
        for data in self._get_cached_by_type(rows, "guba_popularity"):
            if isinstance(data, list):
                for item in data[:50]:
                    if isinstance(item, dict):
                        item_code = item.get("code") or ""
                        if item_code and _normalize_code(item_code) == nc:
                            score += 0.05
                            break

        # 4. guba_posts 帖子激增（当日 posts 数 > 近期均值 1.5 倍）
        for data in self._get_cached_by_type(rows, "guba_posts"):
            if isinstance(data, dict):
                post_code = _normalize_code(data.get("code") or "")
                if post_code == nc:
                    post_count = data.get("count") or 0
                    # 简单阈值判断：帖子数 >= 20 视为激增（避免额外查询历史均值）
                    if post_count >= 20:
                        score += 0.10
                    break

        # 5. 涨停命中
        for data in self._get_cached_by_type(rows, "limit_pool"):
            if isinstance(data, dict):
                info = data.get("info") or []
                for item in info:
                    if isinstance(item, dict):
                        item_code = item.get("code") or item.get("symbol") or ""
                        if _normalize_code(item_code) == nc:
                            change = item.get("change") or item.get("price_change")
                            if change is not None:
                                try:
                                    if float(change) > 0:
                                        score += 0.15
                                except (ValueError, TypeError):
                                    pass
                            else:
                                score += 0.15
                            break

        return max(0.0, min(1.0, score))

    # ==================== L2: overheat_set ====================

    async def get_overheat_set(self) -> dict:
        """过热降权集合

        Returns:
            {"codes": {code: penalty ∈ [0, 0.5]}, "updated_at": iso}
        """
        try:
            return await self._calc_overheat_set()
        except Exception:
            logger.warning("get_overheat_set 失败，返回空集", exc_info=True)
            return {"codes": {}, "updated_at": app_now().isoformat()}

    async def _calc_overheat_set(self) -> dict:
        rows = await self._ensure_cache()

        # 收集各维度的 code 集合
        xq_codes = self._extract_stock_codes(rows, "xueqiu_hot_stocks")
        tc_codes = self._extract_tencent_codes(rows)

        guba_top30 = set()
        for data in self._get_cached_by_type(rows, "guba_popularity"):
            if isinstance(data, list):
                for item in data[:30]:
                    if isinstance(item, dict):
                        c = item.get("code") or ""
                        if c:
                            guba_top30.add(_normalize_code(c))

        limit_up_codes = set()
        for data in self._get_cached_by_type(rows, "limit_pool"):
            if isinstance(data, dict):
                info = data.get("info") or []
                for item in info:
                    if isinstance(item, dict):
                        c = item.get("code") or item.get("symbol") or ""
                        change = item.get("change") or item.get("price_change")
                        if c:
                            nc = _normalize_code(c)
                            if change is not None:
                                try:
                                    if float(change) > 0:
                                        limit_up_codes.add(nc)
                                except (ValueError, TypeError):
                                    pass
                            else:
                                limit_up_codes.add(nc)

        guba_posts_surge = set()
        for data in self._get_cached_by_type(rows, "guba_posts"):
            if isinstance(data, dict):
                post_count = data.get("count") or 0
                if post_count >= 20:
                    c = data.get("code") or ""
                    if c:
                        guba_posts_surge.add(_normalize_code(c))

        # 汇总所有出现过的 code
        all_codes = xq_codes | tc_codes | guba_top30 | limit_up_codes | guba_posts_surge

        conditions = {
            "xueqiu": xq_codes,
            "tencent": tc_codes,
            "guba_top30": guba_top30,
            "limit_up": limit_up_codes,
            "guba_posts_surge": guba_posts_surge,
        }

        codes_penalty = {}
        for code in all_codes:
            hits = sum(1 for cond_codes in conditions.values() if code in cond_codes)
            if hits >= 3:
                penalty = min(0.45, (hits - 2) * 0.15)
                penalty = min(penalty, 0.5)  # 硬上限
                codes_penalty[code] = round(penalty, 2)

        return {"codes": codes_penalty, "updated_at": app_now().isoformat()}

    # ==================== L2: leading_theme ====================

    async def get_leading_theme(self) -> dict:
        """当日市场主线（涨停原因集中度）

        Returns:
            {"theme": str | None, "confidence": float ∈ [0, 1]}
        """
        try:
            return await self._calc_leading_theme()
        except Exception:
            logger.warning("get_leading_theme 失败，返回空", exc_info=True)
            return {"theme": None, "confidence": 0}

    async def _calc_leading_theme(self) -> dict:
        rows = await self._ensure_cache()

        # 1. 从 limit_pool info[] 提取涨停原因
        reasons = []
        for data in self._get_cached_by_type(rows, "limit_pool"):
            if isinstance(data, dict):
                info = data.get("info") or []
                for item in info:
                    if isinstance(item, dict):
                        reason = (item.get("reason_type")
                                  or item.get("reason_stock_type")
                                  or item.get("reason")
                                  or "")
                        if reason and isinstance(reason, str) and reason.strip():
                            reasons.append(reason.strip())

        if not reasons:
            return {"theme": None, "confidence": 0}

        # 2. 按原因分组计数
        counter = Counter(reasons)
        total = len(reasons)
        top_reason, top_count = counter.most_common(1)[0]
        confidence = top_count / total if total > 0 else 0

        if confidence < 0.3:
            return {"theme": None, "confidence": round(confidence, 2)}

        # 3. 可选：该 theme 在 xueqiu_hot_topics title 中出现 → ×1.2
        topic_titles = []
        for data in self._get_cached_by_type(rows, "xueqiu_hot_topics"):
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        title = item.get("title") or item.get("name") or ""
                        if title:
                            topic_titles.append(title)
            elif isinstance(data, dict):
                topics = data.get("topics") or data.get("items") or []
                for item in topics:
                    if isinstance(item, dict):
                        title = item.get("title") or item.get("name") or ""
                        if title:
                            topic_titles.append(title)

        # 检查 theme 关键词是否在 topic title 中
        theme_keywords = top_reason.replace("、", " ").replace(",", " ").split()
        for title in topic_titles:
            if any(kw in title for kw in theme_keywords):
                confidence = min(1.0, confidence * 1.2)
                break

        return {"theme": top_reason, "confidence": round(confidence, 2)}

    # ==================== L2 信号物化 ====================

    async def materialize_snapshot(self, trade_date: date | None = None) -> dict:
        """将当前 L2 信号物化到 ft_sentiment_signal 快照表

        Args:
            trade_date: 目标日期，None 时使用今天。支持传入历史日期做回补。
        """
        target = trade_date or app_today()
        target_str = target.isoformat()

        # 加载目标日期的 ft_sentiment 数据到缓存
        if target_str not in self._cache:
            rows = await self.query(trade_date=target_str, limit=500)
            if not rows:
                logger.info(f"跳过物化: ft_sentiment 无 {target_str} 数据（可能非交易日）")
                return {}
            self._cache[target_str] = rows

        # 计算各 L2 信号
        temp_result = await self._calc_market_temperature_for_date(target_str)
        overheat = await self.get_overheat_set()
        theme = await self.get_leading_theme()
        overall_score = await self.get_sentiment_score(code=None)

        from src.infrastructure.persistence.repositories import SentimentSignalRepositoryImpl
        repo = SentimentSignalRepositoryImpl()

        payload = {
            "market_temperature": temp_result["temperature"],
            "market_level": temp_result["level"],
            "market_trend": temp_result.get("trend"),
            "signals": temp_result,
            "overheat_codes": overheat,
            "leading_theme": theme,
            "sentiment_agg": {"overall_score": overall_score},
            "contributors": {},
        }

        repo.upsert_snapshot(
            snapshot_date=target,
            market_temperature=payload["market_temperature"],
            market_level=payload["market_level"],
            market_trend=payload["market_trend"],
            signals=payload["signals"],
            overheat_codes=payload["overheat_codes"],
            leading_theme=payload["leading_theme"],
            sentiment_agg=payload["sentiment_agg"],
            contributors=payload["contributors"],
        )

        logger.info(
            f"sentiment_signal 物化完成: {target_str} "
            f"temp={payload['market_temperature']} level={payload['market_level']}"
        )
        return payload

    async def _calc_market_temperature_for_date(self, target_date: str) -> dict:
        """为指定日期计算市场温度（复用缓存）"""
        rows = self._cache.get(target_date, [])
        if not rows:
            return {"temperature": 50, "level": "warm", "trend": "flat", "signals": []}

        # 复用 _calc_market_temperature 的逻辑但使用指定日期的数据
        # 临时替换缓存调用
        old_cache = self._cache.copy()
        # 确保指定日期在缓存中
        if _today() not in self._cache:
            self._cache[_today()] = self._cache.get(_today(), [])
        # 将指定日期的数据设为"当天"缓存来复用计算逻辑
        self._cache[_today()] = rows
        try:
            result = await self._calc_market_temperature()
        finally:
            # 恢复原始缓存
            self._cache = old_cache

        return result
