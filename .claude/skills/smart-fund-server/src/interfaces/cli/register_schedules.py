"""注册定时调度任务到 jettask scheduler

运行：
    python src/interfaces/cli/register_schedules.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jettask import Schedule
from src.interfaces.tasks import app


# ==================== 定时调度配置 ====================

SCHEDULES = [
    # ── P0：核心数据（盘中高频） ──────────────────
    Schedule(
        scheduler_id="agg_news_3min",
        queue="agg_news",
        interval_seconds=180,
        description="新闻事件聚合 — 每 3 分钟",
    ),
    Schedule(
        scheduler_id="agg_fund_flow_5min",
        queue="agg_fund_flow",
        interval_seconds=300,
        description="资金流聚合 — 每 5 分钟",
    ),

    # ── P1：辅助数据（中低频） ──────────────────
    Schedule(
        scheduler_id="agg_market_1min",
        queue="agg_market",
        interval_seconds=60,
        description="市场数据聚合 — 每 1 分钟",
    ),
    Schedule(
        scheduler_id="agg_sentiment_15min",
        queue="agg_sentiment",
        interval_seconds=900,
        description="情绪舆情聚合 — 每 15 分钟",
    ),
    Schedule(
        scheduler_id="agg_macro_1h",
        queue="agg_macro",
        interval_seconds=3600,
        description="宏观指标聚合 — 每 1 小时",
    ),

    # ── AI 处理任务 ────────────────────────────
    Schedule(
        scheduler_id="agg_event_extraction_5min",
        queue="agg_event_extraction",
        interval_seconds=300,
        description="AI 事件抽取 — 每 5 分钟，从 ft_news 抽取到 ft_events",
    ),
    Schedule(
        scheduler_id="agg_event_stream_10min",
        queue="agg_event_stream",
        interval_seconds=600,
        description="事件流聚合 — 每 10 分钟，按 industry 聚类近 24h 事件",
    ),

    # ── 决策任务 ────────────────────────────
    Schedule(
        scheduler_id="trade_decision_5min",
        queue="trade_decision",
        interval_seconds=300,
        description="事件驱动决策 — 每 5 分钟，从 ft_event_streams 打分写 ft_pending_decisions",
    ),
    Schedule(
        scheduler_id="trade_execution_2min",
        queue="trade_execution",
        interval_seconds=120,
        description="交易执行 — 每 2 分钟扫描 pending 决策（默认 EXEC_DRY_RUN=true）",
    ),
    Schedule(
        scheduler_id="trade_monitor_5min",
        queue="trade_monitor",
        interval_seconds=300,
        description="持仓监控 — 每 5 分钟，硬止损 + 衰退检测 + 浮盈加仓",
    ),
    Schedule(
        scheduler_id="review_decision_after_close",
        queue="review_decision",
        cron_expression="0 16 * * 1-5",
        description="决策复盘 — 盘后 16:00，回填 T+1/T+2 收益 + 胜率统计",
    ),

    # ── P2：盘后任务 ──────────────────────────
    Schedule(
        scheduler_id="agg_event_feedback_after_close",
        queue="agg_event_feedback",
        cron_expression="30 15 * * 1-5",
        description="事件反馈回填 — 盘后 15:30",
    ),
]


async def main():
    # jettask 1.0.19: app.register_schedules(schedules) — async 方法
    result = await app.register_schedules(SCHEDULES)
    count = len(SCHEDULES) if result is None else (len(result) if hasattr(result, "__len__") else result)
    print(f"✅ 已注册 {count} 个定时任务")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
