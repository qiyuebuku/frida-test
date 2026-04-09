"""分布式任务（基于 jettask）

启动 Worker：
    jettask worker -a tasks:app -t agg_news,agg_fund_flow,agg_macro,agg_sentiment,agg_market,agg_event_feedback -c 1

注册定时调度：
    python -m tasks.register_schedules
"""

import os
from jettask import Jettask

REDIS_URL = os.getenv("JETTASK_REDIS_URL", "redis://localhost:6379/0")

app = Jettask(redis_url=REDIS_URL)

# 注册任务路由
from tasks.aggregator_tasks import router as aggregator_router
app.include_router(aggregator_router)
