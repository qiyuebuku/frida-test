"""定时调度配置 — 被 main.py init schedules 命令导入"""

from jettask import Schedule

SCHEDULES = [
    # ── 数据采集 ──────────────────────────
    Schedule(name="collect_news_3min", queue="collect_news", interval_seconds=180, description="新闻采集 — 每 3 分钟"),
    Schedule(name="collect_fund_flow_5min", queue="collect_fund_flow", interval_seconds=300, description="资金流采集 — 每 5 分钟"),
    Schedule(name="collect_market_1min", queue="collect_market", interval_seconds=60, description="市场数据采集 — 每 1 分钟"),
    Schedule(name="collect_sentiment_15min", queue="collect_sentiment", interval_seconds=900, description="情绪舆情采集 — 每 15 分钟"),
    Schedule(name="collect_macro_1h", queue="collect_macro", interval_seconds=3600, description="宏观指标采集 — 每 1 小时"),

    # ── 知识图谱 ─────────────────────────
    Schedule(name="kg_community_insight_refresh_1min", queue="kg_community_insight_refresh", interval_seconds=60, description="Community Insight 高级认知报告刷新 — 每 1 分钟"),

    # ── 盘后 ─────────────────────────────
    # jettask-rs 当前按 UTC 计算 cron；北京时间 15:30 对应 UTC 07:30。
    Schedule(name="materialize_sentiment_signal_after_close", queue="materialize_sentiment_signal", cron_expression="30 7 * * 1-5", description="L2 情绪信号物化 — 北京时间盘后 15:30"),
]
