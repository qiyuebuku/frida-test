"""批量抽取 — 从桶中取出一批同类新闻，调 Planner API 抽取事件"""
import logging

from src.domain.extraction.services.l1a.json_validator import coerce_event, repair_and_parse, validate_event
from src.domain.extraction.services.l1a.prompts.base import format_user_message
from src.domain.extraction.services.l1a.prompts.event_types import get_system_prompt
from src.infrastructure.clients.llm_client import chat

logger = logging.getLogger(__name__)


def extract_bucket(event_type: str, news_batch: list[dict]) -> list[dict]:
    """从同一桶中抽取事件

    Args:
        event_type: 桶的事件类型
        news_batch: 8-16 条同类新闻

    Returns: 校验后的事件列表
    """
    if not news_batch:
        return []

    system_prompt = get_system_prompt(event_type)
    user_message = format_user_message(news_batch)

    resp = chat(
        prompt=user_message,
        system_prompt=system_prompt,
        timeout=180,
    )

    raw_result = resp.get("result", "")
    if not raw_result:
        logger.warning(f"[extractor] {event_type} 桶 {len(news_batch)} 条新闻抽取无输出")
        return []

    # 解析 JSON
    raw_events = repair_and_parse(raw_result)
    if not raw_events:
        logger.warning(f"[extractor] {event_type} JSON 解析失败: {raw_result[:300]}")
        return []

    # 校验 + 强制转换
    valid_events = []
    for event in raw_events:
        if not isinstance(event, dict):
            continue
        event = coerce_event(event)
        is_valid, flags = validate_event(event)
        if is_valid:
            event["quality_flags"] = flags
            valid_events.append(event)
        elif event.get("is_event") is False:
            continue  # 正常：新闻不包含事件
        else:
            # 不完整但可能是事件，保留并标记
            if event.get("title"):
                event["quality_flags"] = flags
                valid_events.append(event)

    logger.info(
        f"[extractor] {event_type}: {len(news_batch)} 条新闻 → "
        f"{len(raw_events)} 个原始事件 → {len(valid_events)} 个有效事件"
    )
    return valid_events
