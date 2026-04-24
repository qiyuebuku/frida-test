"""dedup_key 计算 + 查重 + evidence 合并"""
import hashlib
from datetime import datetime

from src.infrastructure.persistence.repositories import EventRepositoryImpl


def compute_dedup_key(event_subtype: str, entity_id: str, event_time: datetime) -> str:
    """计算 L1a 去重键

    格式: hash(event_subtype + entity_id + date)
    """
    date_str = event_time.strftime("%Y-%m-%d") if event_time else "unknown"
    raw = f"l1a:{event_subtype}|{entity_id}|{date_str}"
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


def check_and_merge(dedup_key: str, event: dict) -> dict | None:
    """查重：如果已存在相同 dedup_key 的事件，合并 evidence_refs

    Returns:
        None — 新事件（未重复）
        dict — 已合并到现有事件（跳过入库）
    """
    repo = EventRepositoryImpl()
    existing = repo.find_by_dedup_key(dedup_key)
    if not existing:
        return None

    # 已存在 — 合并 evidence_refs
    new_evidence = event.get("evidence_refs") or []
    if new_evidence:
        merged = (existing.get("evidence_refs") or []) + new_evidence
        repo.upsert_l1_event({
            **event,
            "dedup_key": dedup_key,
            "evidence_refs": merged,
        })
    return existing
