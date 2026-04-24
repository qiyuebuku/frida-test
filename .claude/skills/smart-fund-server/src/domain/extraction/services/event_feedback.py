"""事件反馈聚合 (P2)

职责:
1. 事件市场反应回填 — T+1/T+3 回填板块涨跌、资金流入到 ft_events
2. 事件流衰退监控 — 追踪事件流发酵/衰退状态

前置依赖: 前 5 个聚合服务 + ft_events 表（数据处理层产出）
目标表: ft_events（回填字段）
"""

import json
import logging
from datetime import date, datetime, timedelta

from src.domain.collection.services.base import BaseAggregator, SourceDef

logger = logging.getLogger(__name__)

# ft_events 由数据处理层创建，此处确保回填字段存在
DDL_ENSURE_COLUMNS = """
DO $$
BEGIN
    -- 确保 ft_events 表有回填字段（如果表不存在则跳过）
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'ft_events') THEN
        BEGIN ALTER TABLE ft_events ADD COLUMN IF NOT EXISTS sector_change_1d FLOAT; EXCEPTION WHEN OTHERS THEN NULL; END;
        BEGIN ALTER TABLE ft_events ADD COLUMN IF NOT EXISTS sector_change_3d FLOAT; EXCEPTION WHEN OTHERS THEN NULL; END;
        BEGIN ALTER TABLE ft_events ADD COLUMN IF NOT EXISTS sector_volume_change FLOAT; EXCEPTION WHEN OTHERS THEN NULL; END;
        BEGIN ALTER TABLE ft_events ADD COLUMN IF NOT EXISTS north_flow_1d FLOAT; EXCEPTION WHEN OTHERS THEN NULL; END;
        BEGIN ALTER TABLE ft_events ADD COLUMN IF NOT EXISTS reaction_delay_minutes INT; EXCEPTION WHEN OTHERS THEN NULL; END;
    END IF;
END $$;
"""


def _today() -> str:
    return date.today().isoformat()


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


class EventFeedbackAggregator(BaseAggregator):
    """事件反馈聚合

    不采集新数据，复用已有聚合的数据做计算。
    包含两个子任务:
    1. backfill_market_reaction — 回填事件的市场反应
    2. check_event_stream_decay — 检查事件流衰退信号
    """

    data_domain = "event_feedback"
    task_interval = 3600  # 盘后执行，间隔不重要

    def __init__(self):
        super().__init__()
        self.sources = []  # 不采集新数据，无 sources
        try:
            self._exec_ddl(DDL_ENSURE_COLUMNS)
        except Exception:
            pass  # ft_events 可能还不存在

    def _get_checkpoint(self, source_name: str):
        return None

    def _save(self, items: list[dict]) -> int:
        return 0  # 不走通用入库

    async def query(self, **filters) -> list[dict]:
        """查询已回填的事件"""
        conditions = ["feedback_at IS NOT NULL"]
        values: list = []
        if filters.get("since"):
            conditions.append("event_time >= %s")
            values.append(filters["since"])
        if filters.get("industry"):
            conditions.append("industries @> %s::jsonb")
            values.append(json.dumps([filters["industry"]]))
        limit = filters.get("limit", 50)
        try:
            return self._query_table(
                "ft_events",
                conditions=conditions,
                values=values,
                order_by="event_time DESC",
                limit=limit,
            )
        except Exception:
            return []

    # ==================== tick 覆写 ====================

    async def tick(self):
        """盘后执行: 回填 + 衰退检查"""
        try:
            filled = await self.backfill_market_reaction()
            logger.info(f"[event_feedback] 回填了 {filled} 个事件的市场反应")
        except Exception as e:
            logger.warning(f"[event_feedback] 回填失败: {e}")

    # ==================== 1. 事件市场反应回填 ====================

    async def backfill_market_reaction(self, days: int = 3) -> int:
        """回填近 N 天事件的市场反应

        扫描 ft_events 中 feedback_at 为空的记录，计算并回填。
        即使所有数据源都没拿到值，也会写入 feedback_at 标记为已尝试。
        """
        try:
            events = self._get_unfilled_events(days)
        except Exception as e:
            logger.debug(f"获取未回填事件失败（ft_events 可能不存在）: {e}")
            return 0

        if not events:
            return 0

        filled = 0
        for event in events:
            try:
                reaction = await self._compute_reaction(event)
                # 无论 reaction 是否为空，都写 feedback_at（幂等保证）
                self._update_event_reaction(event["id"], reaction)
                filled += 1
            except Exception as e:
                logger.debug(f"回填事件 {event.get('id')} 失败: {e}")
        return filled

    def _get_unfilled_events(self, days: int) -> list[dict]:
        """获取近 N 天未回填市场反应的事件 — R2.7 走 EventRepository"""
        from src.infrastructure.persistence.repositories import EventRepositoryImpl
        return EventRepositoryImpl().find_unfilled_market_reaction(days=days)

    async def _compute_reaction(self, event: dict) -> dict | None:
        """计算单个事件的市场反应

        回填字段:
        - north_flow_1d: T+1 北向净流入
        - sector_volume_change: T+1 关联行业资金净流入
        - sector_change_1d: T+1 关联板块涨跌幅（来自 sector_kline 缓存）
        - sector_change_3d: T+3 关联板块涨跌幅（来自 sector_kline 缓存）
        """
        reaction: dict = {}
        event_time = event.get("event_time") or event.get("created_at")
        if not event_time:
            return None  # 事件时间缺失，确实无法计算

        # ── 北向资金 T+1 ──
        try:
            t1_date = (event_time + timedelta(days=1)).strftime("%Y-%m-%d")
            north_data = self._get_northbound_flow_on_date(t1_date)
            if north_data is not None:
                reaction["north_flow_1d"] = north_data
        except Exception:
            pass

        # ── 关联行业 T+1 资金净流入 ──
        industries = event.get("industries") or []
        if isinstance(industries, str):
            try:
                industries = json.loads(industries)
            except Exception:
                industries = []
        primary_industry = industries[0] if industries else None
        if primary_industry:
            try:
                t1_date = (event_time + timedelta(days=1)).strftime("%Y-%m-%d")
                net_in = self._get_sector_net_inflow(primary_industry, t1_date)
                if net_in is not None:
                    reaction["sector_volume_change"] = net_in
            except Exception:
                pass

        # ── 关联板块涨跌幅 T+1/T+3（来自 sector_kline 缓存） ──
        try:
            sector_change = await self._get_sector_change(event, industries)
            if sector_change:
                reaction.update(sector_change)
        except Exception:
            pass

        return reaction

    def _get_sector_net_inflow(self, industry: str, trade_date: str) -> float | None:
        """R2.7 走 MarketFlowRepository"""
        from src.infrastructure.persistence.repositories import MarketFlowRepositoryImpl
        return MarketFlowRepositoryImpl().get_sector_net_inflow(industry, trade_date)

    async def _get_sector_change(self, event: dict, industries: list[str]) -> dict:
        """从 sector_kline 缓存中取关联板块的 T+1/T+3 涨跌幅

        Returns: {"sector_change_1d": float, "sector_change_3d": float} 或 {}
        """
        if not industries:
            return {}

        from src.infrastructure.persistence.repositories import MarketCacheRepositoryImpl
        from src.domain.collection.services.market import MarketAggregator

        # 获取 sector_kline 缓存
        cache_repo = MarketCacheRepositoryImpl()
        cache = cache_repo.find_by_type("sector_kline")
        if not cache or "data" not in cache:
            return {}

        sectors_data = cache["data"].get("sectors", {})
        name_map = cache["data"].get("name_map", {})

        # 匹配第一个有 K 线数据的行业
        for industry_name in industries:
            bk_code = name_map.get(industry_name)
            if not bk_code or bk_code not in sectors_data:
                continue

            payload = sectors_data[bk_code]
            result = {}
            if payload.get("change_1d") is not None:
                result["sector_change_1d"] = payload["change_1d"]
            if payload.get("change_3d") is not None:
                result["sector_change_3d"] = payload["change_3d"]
            return result

        return {}

    def _get_northbound_flow_on_date(self, trade_date: str) -> float | None:
        """R2.7 走 MarketFlowRepository"""
        from src.infrastructure.persistence.repositories import MarketFlowRepositoryImpl
        return MarketFlowRepositoryImpl().get_northbound_net_flow(trade_date)

    def _update_event_reaction(self, event_id: int, reaction: dict | None):
        """回填事件的市场反应字段(含 feedback_at 时间戳)

        即使 reaction 为空/None，也会写入 feedback_at 标记为已尝试。
        feedback_at 语义: "首次尝试回填时间"，非"所有字段回填完成"。
        """
        from src.infrastructure.persistence.repositories import EventRepositoryImpl
        EventRepositoryImpl().update_market_reaction(event_id, reaction)

    # ==================== 2. 事件流衰退监控 ====================

    async def check_event_stream_decay(self, industries: list[str]) -> list[dict]:
        """检查指定行业的事件流衰退信号（盘后复盘用，不直接写决策表）

        4 信号加权:
        - 新闻密度低 (news_count_24h < 3)    → +0.25
        - 连续 2 天板块净流出                 → +0.30
        - 事件流状态恶化 (decaying/closed)    → +0.30
        - 情绪趋势转差 (falling)             → +0.15

        Returns:
            [{"industry": "AI", "signals": [...], "decay_score": 0.55}]

        注意: 本方法仅供盘后复盘/离线观察，不直接写 ft_pending_decisions。
        实时止损由 trade_monitor 负责，两者职责不重叠。
        """
        results = []
        for industry in industries:
            signals = []
            decay_score = 0.0

            decay_data = await self.get_decay_signals(industry)

            # 信号 1: 新闻密度
            news_count = decay_data.get("news_count_24h", 0)
            if news_count < 3:
                signals.append(f"新闻密度低: 24h 仅 {news_count} 条")
                decay_score += 0.25

            # 信号 2: 资金方向
            fund_flow_2d = decay_data.get("fund_flow_2d", [])
            if len(fund_flow_2d) >= 2 and all(f < 0 for f in fund_flow_2d):
                signals.append("连续 2 天板块净流出")
                decay_score += 0.30

            # 信号 3: 事件流状态
            stream_state = decay_data.get("stream_state")
            if stream_state in ("decaying", "closed"):
                signals.append(f"事件流状态恶化: {stream_state}")
                decay_score += 0.30

            # 信号 4: 情绪趋势
            sentiment_trend = decay_data.get("sentiment_trend", "unknown")
            if sentiment_trend in ("falling",):
                signals.append(f"情绪趋势转差: {sentiment_trend}")
                decay_score += 0.15

            decay_score = min(1.0, decay_score)

            results.append({
                "industry": industry,
                "signals": signals,
                "decay_score": round(decay_score, 2),
            })
        return results

    async def get_decay_signals(self, industry: str) -> dict:
        """获取单个行业的衰退监控指标（4 信号）

        信号源:
        1. news_count_24h — ft_news 中含该行业关键词的近 24h 新闻数
        2. fund_flow_2d — ft_market_flow.sector_flow 近 2 天含该行业的数据
        3. stream_state — ft_event_streams 中该行业的最新流状态
        4. sentiment_trend — ft_sentiment 中该行业近 3 天的情绪趋势
        """
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import func, or_, select

        from src.infrastructure.connections import get_session
        from src.infrastructure.persistence.models.collection import (
            MarketFlow, News,
        )

        result: dict = {
            "news_count_24h": 0,
            "fund_flow_2d": [],
            "stream_state": None,
            "sentiment_trend": "unknown",
        }

        # 1. 新闻密度: ft_news 中含该行业关键词的近 24h 新闻数
        try:
            with get_session() as s:
                cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
                pattern = f"%{industry}%"
                cnt = s.scalar(
                    select(func.count())
                    .select_from(News)
                    .where(
                        or_(News.title.ilike(pattern), News.content.ilike(pattern)),
                        News.published_at >= cutoff,
                    )
                )
                result["news_count_24h"] = cnt or 0
        except Exception as e:
            logger.debug(f"news_count_24h({industry}) 失败: {e}")

        # 2. 板块资金流: ft_market_flow.sector_flow 近 2 天含该行业的数据
        try:
            with get_session() as s:
                from sqlalchemy import text
                rows = s.execute(
                    text("""
                        SELECT data FROM ft_market_flow
                        WHERE data_type = 'sector_flow'
                          AND trade_date >= :since
                          AND data::text ILIKE :pat
                        ORDER BY trade_date DESC
                        LIMIT 2
                    """),
                    {"since": _days_ago(2), "pat": f"%{industry}%"},
                ).fetchall()
                for row in rows:
                    data = row[0]
                    if isinstance(data, dict):
                        net = data.get("net_amount") or data.get("net") or 0
                        result["fund_flow_2d"].append(float(net))
        except Exception as e:
            logger.debug(f"fund_flow_2d({industry}) 失败: {e}")

        # 3. 事件流状态: ft_event_streams 中该行业的最新流
        try:
            from src.infrastructure.persistence.repositories import (
                EventStreamRepositoryImpl,
            )
            stream = EventStreamRepositoryImpl().find_latest_by_industries([industry])
            if stream:
                result["stream_state"] = stream.get("state")
                result["stream_momentum"] = stream.get("momentum")
        except Exception as e:
            logger.debug(f"stream_state({industry}) 失败: {e}")

        # 4. 情绪趋势: ft_sentiment 中该行业近 3 天的情绪趋势
        try:
            from src.infrastructure.persistence.repositories import (
                SentimentRepositoryImpl,
            )
            result["sentiment_trend"] = SentimentRepositoryImpl().get_sentiment_trend(
                industry, days=3,
            )
        except Exception as e:
            logger.debug(f"sentiment_trend({industry}) 失败: {e}")

        return result
