"""注册定时调度任务到 jettask-python scheduler

前提：
    1. jettask scheduler 正在运行
    2. jettask persist 正在运行（Schedule 持久化需要）

运行：
    python -m tasks.register_schedules

启动 scheduler / persist：
    python -m jettask scheduler start -a tasks:app --db-url postgresql://...
    python -m jettask persist start -a tasks:app --db-url postgresql://...
"""

import asyncio

from jettask import Schedule
from tasks import app


# ==================== 定时调度配置 ====================

SCHEDULES = [
    # ── P0：核心数据（盘中高频） ──────────────────
    Schedule(
        name="agg_news_3min",
        queue="agg_news",
        interval_seconds=180,
    ),
    Schedule(
        name="agg_fund_flow_5min",
        queue="agg_fund_flow",
        interval_seconds=300,
    ),

    # ── P1：辅助数据（中低频） ──────────────────
    Schedule(
        name="agg_market_1min",
        queue="agg_market",
        interval_seconds=60,
    ),
    Schedule(
        name="agg_sentiment_15min",
        queue="agg_sentiment",
        interval_seconds=900,
    ),
    Schedule(
        name="agg_macro_1h",
        queue="agg_macro",
        interval_seconds=3600,
    ),

    # ── P2：盘后任务 ──────────────────────────
    Schedule(
        name="agg_event_feedback_after_close",
        queue="agg_event_feedback",
        cron_expression="30 15 * * 1-5",
    ),
]


async def main():
    await app.schedule_register(SCHEDULES)
    print(f"✅ 已注册 {len(SCHEDULES)} 个定时任务")


if __name__ == "__main__":
    asyncio.run(main())
