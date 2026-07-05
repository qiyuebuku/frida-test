"""jettask-rs 消息投递工具。"""

from __future__ import annotations

from jettask import Jettask, TaskMessage

from src.infrastructure.config.settings import JETTASK_PREFIX, REDIS_URL


KG_NEWS_INGEST_QUEUE = "kg_news_ingest"
KG_NEWS_INGEST_TIMEOUT_SECONDS = 5000
KG_NEWS_INGEST_MAX_RETRIES = 10


async def send_kg_news_ingest(news_ids: list[int]) -> list[str]:
    """把新增 ft_news.id 直接投递给 kg_news_ingest 队列。

    这里不使用 Redis Stream 作为二级队列；消息可靠性交给 jettask-rs。
    """

    normalized_ids = _ordered_unique_positive_ints(news_ids)
    if not normalized_ids:
        return []

    app = Jettask(redis_url=REDIS_URL, prefix=JETTASK_PREFIX)
    try:
        return await app.send(
            [
                TaskMessage(
                    queue=KG_NEWS_INGEST_QUEUE,
                    kwargs={"news_ids": normalized_ids},
                    max_retries=KG_NEWS_INGEST_MAX_RETRIES,
                    timeout=KG_NEWS_INGEST_TIMEOUT_SECONDS,
                )
            ]
        )
    finally:
        app.close()


def _ordered_unique_positive_ints(values: list[int]) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            item = int(value)
        except (TypeError, ValueError):
            continue
        if item <= 0 or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
