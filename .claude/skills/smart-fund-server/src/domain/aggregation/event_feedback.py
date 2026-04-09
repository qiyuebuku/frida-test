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

import psycopg2
import psycopg2.extras

from src.domain.aggregation.base import BaseAggregator, SourceDef
from src.infrastructure.db.fund_db import get_conn

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
        conditions = ["sector_change_1d IS NOT NULL"]
        values = []
        if filters.get("since"):
            conditions.append("created_at >= %s")
            values.append(filters["since"])
        if filters.get("industry"):
            conditions.append("industry = %s")
            values.append(filters["industry"])
        limit = filters.get("limit", 50)
        try:
            return self._query_table(
                "ft_events",
                conditions=conditions,
                values=values,
                order_by="created_at DESC",
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

        扫描 ft_events 中 sector_change_1d 为空的记录，计算并回填。
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
                if reaction:
                    self._update_event_reaction(event["id"], reaction)
                    filled += 1
            except Exception as e:
                logger.debug(f"回填事件 {event.get('id')} 失败: {e}")
        return filled

    def _get_unfilled_events(self, days: int) -> list[dict]:
        """获取近 N 天未回填市场反应的事件"""
        since = _days_ago(days)
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, title, industry, sectors, created_at
                    FROM ft_events
                    WHERE created_at >= %s AND sector_change_1d IS NULL
                    ORDER BY created_at DESC
                    LIMIT 100
                """, (since,))
                return [dict(r) for r in cur.fetchall()]

    async def _compute_reaction(self, event: dict) -> dict | None:
        """计算单个事件的市场反应"""
        reaction = {}

        # 北向资金 T+1
        try:
            north_data = self._get_northbound_flow_on_date(
                (event["created_at"] + timedelta(days=1)).strftime("%Y-%m-%d")
            )
            if north_data is not None:
                reaction["north_flow_1d"] = north_data
        except Exception:
            pass

        return reaction if reaction else None

    def _get_northbound_flow_on_date(self, trade_date: str) -> float | None:
        """从 ft_market_flow 获取指定日期的北向净流入"""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT data->'net_flow' AS net_flow
                        FROM ft_market_flow
                        WHERE data_type = 'northbound' AND trade_date = %s
                        ORDER BY captured_at DESC LIMIT 1
                    """, (trade_date,))
                    row = cur.fetchone()
                    if row and row[0] is not None:
                        return float(row[0])
        except Exception:
            pass
        return None

    def _update_event_reaction(self, event_id: int, reaction: dict):
        """回填事件的市场反应字段"""
        sets = []
        values = []
        for field in ("sector_change_1d", "sector_change_3d", "sector_volume_change",
                       "north_flow_1d", "reaction_delay_minutes"):
            if field in reaction:
                sets.append(f"{field} = %s")
                values.append(reaction[field])
        if not sets:
            return
        values.append(event_id)
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"UPDATE ft_events SET {', '.join(sets)} WHERE id = %s",
                        values,
                    )
                conn.commit()
        except Exception as e:
            logger.debug(f"回填事件 {event_id} 失败: {e}")

    # ==================== 2. 事件流衰退监控 ====================

    async def check_event_stream_decay(self, industries: list[str]) -> list[dict]:
        """检查指定行业的事件流衰退信号

        Returns:
            [{"industry": "AI", "signals": [...], "decay_score": 0.7}]
        """
        results = []
        for industry in industries:
            signals = []
            decay_score = 0.0

            decay_data = await self.get_decay_signals(industry)

            # 新闻密度
            news_count = decay_data.get("news_count_24h", 0)
            if news_count < 3:
                signals.append(f"新闻密度低: 24h 仅 {news_count} 条")
                decay_score += 0.3

            # 资金方向
            fund_flow_2d = decay_data.get("fund_flow_2d", [])
            if len(fund_flow_2d) >= 2 and all(f < 0 for f in fund_flow_2d):
                signals.append("连续 2 天板块净流出")
                decay_score += 0.4

            # 限制在 0-1
            decay_score = min(1.0, decay_score)

            results.append({
                "industry": industry,
                "signals": signals,
                "decay_score": round(decay_score, 2),
            })
        return results

    async def get_decay_signals(self, industry: str) -> dict:
        """获取单个行业的衰退监控指标"""
        result = {
            "news_count_24h": 0,
            "fund_flow_2d": [],
            "sentiment_trend": "unknown",
        }

        # 1. 新闻密度: ft_news 中含该行业关键词的近 24h 新闻数
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT COUNT(*) FROM ft_news
                        WHERE (title ILIKE %s OR content ILIKE %s)
                          AND published_at >= NOW() - INTERVAL '24 hours'
                    """, (f"%{industry}%", f"%{industry}%"))
                    row = cur.fetchone()
                    result["news_count_24h"] = row[0] if row else 0
        except Exception:
            pass

        # 2. 板块资金流: ft_market_flow 中含该行业的近 2 天数据
        try:
            with get_conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT data FROM ft_market_flow
                        WHERE data_type = 'sector_flow'
                          AND trade_date >= %s
                          AND data::text ILIKE %s
                        ORDER BY trade_date DESC
                        LIMIT 2
                    """, (_days_ago(2), f"%{industry}%"))
                    rows = cur.fetchall()
                    for row in rows:
                        data = row["data"]
                        if isinstance(data, dict):
                            net = data.get("net_amount") or data.get("net") or 0
                            result["fund_flow_2d"].append(float(net))
        except Exception:
            pass

        return result
