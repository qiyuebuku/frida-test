"""注册定时调度任务到 jettask scheduler

运行：
    python src/interfaces/cli/register_schedules.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import asyncio

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

    # ── P2：盘后任务 ──────────────────────────
    Schedule(
        scheduler_id="agg_event_feedback_after_close",
        queue="agg_event_feedback",
        cron_expression="30 15 * * 1-5",
        description="事件反馈回填 — 盘后 15:30",
    ),
]


async def main():
    count = await app.register_schedules(SCHEDULES)
    print(f"✅ 已注册 {count} 个定时任务")

    all_schedules = await app.list_schedules()
    print(f"\n当前所有定时任务（{len(all_schedules)}）:")
    for s in all_schedules:
        print(f"  {s}")


if __name__ == "__main__":
    asyncio.run(main())
