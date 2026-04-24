"""L1a 编排器 — classify_tick + extract_tick"""
import logging
from datetime import datetime, timezone

from src.domain.extraction.services.l1a import bucket
from src.domain.extraction.services.l1a.batch_extractor import extract_bucket
from src.domain.extraction.services.l1a.classifier import classify_batch
from src.domain.extraction.services.l1a.dedup import check_and_merge, compute_dedup_key
from src.infrastructure.persistence.repositories import (
    EventRepositoryImpl,
    NewsRepositoryImpl,
)

logger = logging.getLogger(__name__)


def classify_tick() -> dict:
    """分类 tick：读未分类新闻 → 分类 → 推入桶 → 标记 l1_classified_at

    Returns: {classified, skipped}
    """
    news_repo = NewsRepositoryImpl()
    news_list = news_repo.find_unclassified(limit=200)

    if not news_list:
        return {"classified": 0, "skipped": 0}

    logger.info(f"[l1a:classify] 待分类 {len(news_list)} 条新闻")

    # 批量分类
    classifications = classify_batch(news_list)

    # 按类别分桶
    classified_ids = []
    bucket_counts: dict[str, int] = {}
    for i, news in enumerate(news_list):
        if i >= len(classifications):
            break
        event_type, confidence = classifications[i]
        if event_type and confidence > 0.3:
            size = bucket.add(event_type, news)
            bucket_counts[event_type] = bucket_counts.get(event_type, 0) + 1
            classified_ids.append(news["id"])
        else:
            # 低置信度的推入 other 桶
            bucket.add("other", news)
            bucket_counts["other"] = bucket_counts.get("other", 0) + 1
            classified_ids.append(news["id"])

    # 标记为已分类
    if classified_ids:
        news_repo.mark_classified(classified_ids)

    logger.info(f"[l1a:classify] 完成: {bucket_counts}")
    return {"classified": len(classified_ids), "skipped": 0}


def extract_tick() -> dict:
    """抽取 tick：遍历所有桶 → 取出满足条件的批次 → 抽取 → 去重 → 入库

    Returns: {processed, saved}
    """
    event_repo = EventRepositoryImpl()
    news_repo = NewsRepositoryImpl()

    types = bucket.bucket_types()
    if not types:
        return {"processed": 0, "saved": 0}

    total_saved = 0
    total_processed = 0
    extracted_news_ids = []

    for event_type in types:
        # 先尝试满桶抽取
        batch = bucket.drain(event_type)
        if not batch:
            # 再尝试超时抽取
            batch = bucket.timeout_drain(event_type)

        if not batch:
            continue

        total_processed += len(batch)
        logger.info(f"[l1a:extract] {event_type} 桶取 {len(batch)} 条")

        # 批量抽取
        try:
            events = extract_bucket(event_type, batch)
        except Exception as e:
            logger.warning(f"[l1a:extract] {event_type} 抽取失败: {e}")
            continue

        # 逐事件去重入库
        for event in events:
            # 构建 dedup_key
            subtype = event.get("event_subtype") or event.get("event_type", "")
            entity = ""
            stocks = event.get("affected_stocks") or []
            if stocks and isinstance(stocks, list) and len(stocks) > 0:
                entity = stocks[0].get("code", "") if isinstance(stocks[0], dict) else str(stocks[0])
            event_time = datetime.now(timezone.utc)

            dedup_key = compute_dedup_key(subtype, entity, event_time)

            # 查重
            existing = check_and_merge(dedup_key, event)
            if existing:
                continue

            # 入库
            event_data = {
                **event,
                "source_type": "text",
                "source_table": "ft_news",
                "event_time": event_time,
                "dedup_key": dedup_key,
                "schema_version": "v1.0",
                "extractor_version": "l1a-v1",
                "evidence_refs": [{
                    "table": "ft_news",
                    "pk": n.get("id"),
                    "excerpt": (n.get("title") or "")[:200],
                } for n in batch],
                "source_news_ids": [n.get("id") for n in batch if n.get("id")],
            }

            if event_repo.upsert_l1_event(event_data):
                total_saved += 1
                extracted_news_ids.extend(n.get("id") for n in batch if n.get("id"))

    # 标记新闻为已抽取
    if extracted_news_ids:
        news_repo.mark_extracted(list(set(extracted_news_ids)))

    logger.info(f"[l1a:extract] 完成: processed={total_processed} saved={total_saved}")
    return {"processed": total_processed, "saved": total_saved}
