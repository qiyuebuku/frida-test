"""Redis 分桶管理 — L1a 新闻按 event_type 分桶，攒批后批量抽取"""
import json
import logging
import time

import redis

from src.infrastructure.config.settings import REDIS_URL

logger = logging.getLogger(__name__)

BUCKET_PREFIX = "l1a:bucket:"
MIN_BATCH = 8
MAX_BATCH = 16
MAX_WAIT_SECONDS = 60


def _get_redis() -> redis.Redis:
    return redis.from_url(REDIS_URL, decode_responses=True)


def add(event_type: str, news_item: dict) -> int:
    """将新闻推入指定类型的桶

    Returns: 桶当前大小
    """
    r = _get_redis()
    key = f"{BUCKET_PREFIX}{event_type}"
    value = json.dumps({
        "id": news_item.get("id"),
        "title": news_item.get("title"),
        "summary": (news_item.get("summary") or "")[:500],
        "content": (news_item.get("content") or "")[:2000],
        "source": news_item.get("source"),
        "published_at": str(news_item.get("published_at", "")),
    }, ensure_ascii=False)
    r.rpush(key, value)
    # 设置桶的过期时间（防止堆积）
    r.expire(key, 300)
    return r.llen(key)


def drain(event_type: str, min_size: int = MIN_BATCH, max_size: int = MAX_BATCH) -> list[dict] | None:
    """从桶中取出满足数量的一批新闻

    桶大小 >= min_size 时取出前 max_size 条
    Returns: news list 或 None（桶太小）
    """
    r = _get_redis()
    key = f"{BUCKET_PREFIX}{event_type}"
    size = r.llen(key)
    if size < min_size:
        return None

    count = min(size, max_size)
    items = []
    for _ in range(count):
        raw = r.lpop(key)
        if raw:
            try:
                items.append(json.loads(raw))
            except json.JSONDecodeError:
                pass
    return items if items else None


def timeout_drain(event_type: str, max_wait: int = MAX_WAIT_SECONDS) -> list[dict] | None:
    """取出等待超时的桶内容（桶中有数据但不满 min_size）

    通过检查桶中第一条的入队时间判断是否超时
    简化实现：直接检查桶 TTL 是否 < (300 - max_wait)
    """
    r = _get_redis()
    key = f"{BUCKET_PREFIX}{event_type}"
    size = r.llen(key)
    if size == 0:
        return None

    ttl = r.ttl(key)
    # TTL < (300 - max_wait) 说明已经等待了超过 max_wait 秒
    if ttl is not None and ttl < (300 - max_wait):
        items = []
        for _ in range(size):
            raw = r.lpop(key)
            if raw:
                try:
                    items.append(json.loads(raw))
                except json.JSONDecodeError:
                    pass
        return items if items else None

    return None


def bucket_types() -> list[str]:
    """返回所有有待处理数据的桶类型"""
    r = _get_redis()
    keys = r.keys(f"{BUCKET_PREFIX}*")
    return [k.replace(BUCKET_PREFIX, "") for k in keys if r.llen(k) > 0]


def bucket_sizes() -> dict[str, int]:
    """返回所有桶的大小"""
    r = _get_redis()
    keys = r.keys(f"{BUCKET_PREFIX}*")
    return {k.replace(BUCKET_PREFIX, ""): r.llen(k) for k in keys}
