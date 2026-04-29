"""Stable identifier helpers for knowledge objects."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from src.domain.knowledge.enums import EvidenceType


def stable_hash(value: Any, *, length: int = 16) -> str:
    if length <= 0:
        raise ValueError("length must be positive")

    data = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:length]


def make_node_id(adapter_name: str, node_type: str, stable_key: str) -> str:
    _require_text(adapter_name, "adapter_name")
    _require_text(node_type, "node_type")
    _require_text(stable_key, "stable_key")
    return f"kg:{adapter_name}:{node_type}:{stable_hash(stable_key)}"


def make_edge_id(
    adapter_name: str,
    relation_type: str,
    source_node_id: str,
    target_node_id: str,
    evidence_ids: list[str],
) -> str:
    _require_text(adapter_name, "adapter_name")
    _require_text(relation_type, "relation_type")
    _require_text(source_node_id, "source_node_id")
    _require_text(target_node_id, "target_node_id")
    payload = {
        "source_node_id": source_node_id,
        "target_node_id": target_node_id,
        "evidence_ids": sorted(evidence_ids),
    }
    return f"kg_edge:{adapter_name}:{relation_type}:{stable_hash(payload)}"


def make_evidence_id(
    adapter_name: str,
    source_type: str,
    source_id: str,
    evidence_type: EvidenceType,
    content: str | None,
    payload: dict[str, Any],
) -> str:
    _require_text(adapter_name, "adapter_name")
    _require_text(source_type, "source_type")
    _require_text(source_id, "source_id")
    evidence_value = evidence_type.value if isinstance(evidence_type, EvidenceType) else str(evidence_type)
    identity = {
        "evidence_type": evidence_value,
        "content": content,
        "payload": payload,
    }
    return f"kg_ev:{adapter_name}:{source_type}:{source_id}:{stable_hash(identity)}"


def _require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
