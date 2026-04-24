"""定时调度配置 — 被 main.py init schedules 命令导入"""

from jettask import Schedule

SCHEDULES = [
    # ── 数据采集 ──────────────────────────
    Schedule(name="agg_news_3min", queue="agg_news", interval_seconds=180, description="新闻采集 — 每 3 分钟"),
    Schedule(name="agg_fund_flow_5min", queue="agg_fund_flow", interval_seconds=300, description="资金流采集 — 每 5 分钟"),
    Schedule(name="agg_market_1min", queue="agg_market", interval_seconds=60, description="市场数据采集 — 每 1 分钟"),
    Schedule(name="agg_sentiment_15min", queue="agg_sentiment", interval_seconds=900, description="情绪舆情采集 — 每 15 分钟"),
    Schedule(name="agg_macro_1h", queue="agg_macro", interval_seconds=3600, description="宏观指标采集 — 每 1 小时"),

    # ── L1 事件识别 ────────────────────────
    Schedule(name="l1b_detect_fund_flow_5min", queue="l1b_detect_fund_flow", interval_seconds=300, description="L1b 资金流事件检测 — 每 5 分钟"),
    Schedule(name="l1b_detect_macro_1h", queue="l1b_detect_macro", interval_seconds=3600, description="L1b 宏观事件检测 — 每 1 小时"),
    Schedule(name="l1b_detect_sentiment_15min", queue="l1b_detect_sentiment", interval_seconds=900, description="L1b 情绪事件检测 — 每 15 分钟"),
    Schedule(name="l1b_detect_market_1min", queue="l1b_detect_market", interval_seconds=60, description="L1b 市场事件检测 — 每 1 分钟"),
    Schedule(name="l1_refresh_thresholds_daily", queue="l1_refresh_thresholds", cron_expression="30 0 * * *", description="L1 阈值刷新 — 每日 00:30"),
    Schedule(name="l1a_classify_news_3min", queue="l1a_classify_news", interval_seconds=180, description="L1a 新闻分类 — 每 3 分钟"),
    Schedule(name="l1a_extract_bucket_3min", queue="l1a_extract_bucket", interval_seconds=180, description="L1a 桶批量抽取 — 每 3 分钟"),

    # ── AI 处理 ───────────────────────────
    Schedule(name="agg_event_extraction_5min", queue="agg_event_extraction", interval_seconds=300, description="AI 事件抽取 — 每 5 分钟"),
    Schedule(name="agg_event_stream_10min", queue="agg_event_stream", interval_seconds=600, description="事件流聚合 — 每 10 分钟"),

    # ── 决策交易 ──────────────────────────
    Schedule(name="trade_decision_5min", queue="trade_decision", interval_seconds=300, description="事件驱动决策 — 每 5 分钟"),
    Schedule(name="trade_execution_2min", queue="trade_execution", interval_seconds=120, description="交易执行 — 每 2 分钟"),
    Schedule(name="trade_monitor_5min", queue="trade_monitor", interval_seconds=300, description="持仓监控 — 每 5 分钟"),

    # ── 盘后 ─────────────────────────────
    Schedule(name="review_decision_after_close", queue="review_decision", cron_expression="0 16 * * 1-5", description="决策复盘 — 盘后 16:00"),
    Schedule(name="agg_event_feedback_after_close", queue="agg_event_feedback", cron_expression="30 15 * * 1-5", description="事件反馈回填 — 盘后 15:30"),
    Schedule(name="materialize_sentiment_signal_after_close", queue="materialize_sentiment_signal", cron_expression="30 15 * * 1-5", description="L2 情绪信号物化 — 盘后 15:30"),
]
