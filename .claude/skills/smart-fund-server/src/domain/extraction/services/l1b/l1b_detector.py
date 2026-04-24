"""L1b 检测编排器 — 从数据源读增量数据 → 评估规则 → 入库 ft_events"""
import hashlib
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from src.domain.extraction.services.l1b.rules import (
    ALL_FUND_FLOW_RULES,
    ALL_MACRO_RULES,
    ALL_MARKET_RULES,
    ALL_SENTIMENT_RULES,
)
from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.collection import (
    MacroIndicator,
    MarketCache,
    MarketFlow,
    Sentiment,
)
from src.infrastructure.persistence.repositories import (
    EventRepositoryImpl,
    RuleThresholdRepositoryImpl,
)

logger = logging.getLogger(__name__)


class L1bDetector:
    """L1b 数值事件检测编排器"""

    def __init__(self):
        self._event_repo = EventRepositoryImpl()
        self._threshold_repo = RuleThresholdRepositoryImpl()

    # ==================== 公共入口 ====================

    def detect_fund_flow(self) -> dict:
        """检测资金流异常事件"""
        records = self._read_recent("ft_market_flow", hours=6)
        rules = ALL_FUND_FLOW_RULES
        return self._run_detection(records, rules, "ft_market_flow")

    def detect_macro(self) -> dict:
        """检测宏观指标异常"""
        records = self._read_recent("ft_macro_indicators", hours=48)
        # RateChange 需要历史序列
        self._enrich_macro_history(records)
        rules = ALL_MACRO_RULES
        return self._run_detection(records, rules, "ft_macro_indicators")

    def detect_sentiment(self) -> dict:
        """检测情绪极值事件"""
        records = self._read_recent("ft_sentiment", hours=6)
        rules = ALL_SENTIMENT_RULES
        return self._run_detection(records, rules, "ft_sentiment")

    def detect_market(self) -> dict:
        """检测市场快照异常"""
        records = self._read_market_cache()
        rules = ALL_MARKET_RULES
        return self._run_detection(records, rules, "ft_market_cache")

    # ==================== 内部方法 ====================

    def _run_detection(self, records: list[dict], rules: list, source: str) -> dict:
        """通用检测流程：加载阈值 → 逐规则评估 → 去重入库"""
        checked = len(records)
        fired = 0
        saved = 0

        for rule in rules:
            thresholds = self._threshold_repo.get_threshold(rule.rule_name)
            if not thresholds:
                logger.debug(f"[l1b] {rule.rule_name} 无阈值配置，跳过")
                continue

            for record in records:
                try:
                    results = rule.evaluate(record, thresholds)
                except Exception as e:
                    logger.debug(f"[l1b] {rule.rule_name} 评估失败: {e}")
                    continue

                for result in results:
                    if not result.fired:
                        continue
                    fired += 1

                    event_data = rule.build_event(result, record)
                    event_data["event_time"] = event_data.get("event_time") or datetime.now(timezone.utc)

                    # 计算 dedup_key
                    dedup_key = self._compute_dedup_key(
                        event_data.get("event_subtype", ""),
                        source,
                        event_data["event_time"],
                    )
                    event_data["dedup_key"] = dedup_key

                    # 查重
                    existing = self._event_repo.find_by_dedup_key(dedup_key)
                    if existing:
                        continue

                    if self._event_repo.upsert_l1_event(event_data):
                        saved += 1

        logger.info(f"[l1b:{source}] checked={checked} fired={fired} saved={saved}")
        return {"checked": checked, "fired": fired, "saved": saved}

    def _read_recent(self, table: str, hours: int = 6) -> list[dict]:
        """从指定表读取最近 N 小时的增量数据"""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        model_map = {
            "ft_market_flow": MarketFlow,
            "ft_macro_indicators": MacroIndicator,
            "ft_sentiment": Sentiment,
        }
        model = model_map.get(table)
        if not model:
            return []

        try:
            with get_session() as s:
                rows = s.scalars(
                    select(model).where(model.created_at >= cutoff).limit(500)
                ).all()
                return [self._model_to_dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"[l1b] 读 {table} 失败: {e}")
            return []

    def _read_market_cache(self) -> list[dict]:
        """ft_market_cache 是覆盖语义，读最新快照"""
        try:
            with get_session() as s:
                rows = s.scalars(
                    select(MarketCache).where(
                        MarketCache.data_type.in_(["market_overview"])
                    ).limit(10)
                ).all()
                return [self._model_to_dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"[l1b] 读 ft_market_cache 失败: {e}")
            return []

    @staticmethod
    def _model_to_dict(row) -> dict:
        """ORM 对象转 dict（含 data JSONB）"""
        d = {}
        for col in row.__table__.columns:
            val = getattr(row, col.name, None)
            d[col.name] = val
        # 确保 data 是 dict
        if isinstance(d.get("data"), str):
            import json
            try:
                d["data"] = json.loads(d["data"])
            except Exception:
                pass
        return d

    @staticmethod
    def _compute_dedup_key(event_subtype: str, source: str, event_time: datetime) -> str:
        """计算去重键 — hash(event_subtype + source + date)"""
        date_str = event_time.strftime("%Y-%m-%d") if event_time else "unknown"
        raw = f"{event_subtype}|{source}|{date_str}"
        return hashlib.sha256(raw.encode()).hexdigest()[:64]

    @staticmethod
    def _enrich_macro_history(records: list[dict]):
        """为宏观指标记录附加历史序列（供 TrendRule 使用）"""
        if not records:
            return
        # 按 indicator 分组
        by_indicator: dict[str, list] = {}
        for r in records:
            ind = r.get("indicator", "")
            if ind:
                by_indicator.setdefault(ind, []).append(r)

        try:
            with get_session() as s:
                for ind, recs in by_indicator.items():
                    # 取最近 6 期
                    rows = s.scalars(
                        select(MacroIndicator)
                        .where(MacroIndicator.indicator == ind)
                        .order_by(MacroIndicator.published_at.desc())
                        .limit(6)
                    ).all()
                    history = [
                        {"value": r.value, "data": {"value": r.value}}
                        for r in reversed(rows)
                    ]
                    for rec in recs:
                        rec["_history"] = history + [{"value": rec.get("value"), "data": {"value": rec.get("value")}}]
        except Exception as e:
            logger.debug(f"[l1b] 宏观历史查询失败: {e}")
