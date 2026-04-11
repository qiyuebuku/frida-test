"""分布式任务（基于 jettask）

启动 Worker：
    jettask worker -a tasks:app -t agg_news,agg_fund_flow,agg_macro,agg_sentiment,agg_market,agg_event_feedback -c 1

注册定时调度：
    python -m tasks.register_schedules
"""

from jettask import Jettask
from src.infrastructure.config.settings import REDIS_URL, JETTASK_PREFIX, PG_URL

app = Jettask(redis_url=REDIS_URL, prefix=JETTASK_PREFIX)
DB_URL = PG_URL  # start_scheduler / start_persist 时传入

# 注册任务路由
from src.interfaces.tasks.aggregator_tasks import router as aggregator_router
app.include_router(aggregator_router)
