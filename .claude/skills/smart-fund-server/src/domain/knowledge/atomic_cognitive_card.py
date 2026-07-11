"""原子 Cognitive Card 的领域契约、证据切分与可读索引材料。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.domain.knowledge.schemas import EvidenceChunk
from src.domain.knowledge.semantic_index_materials import (
    SEMANTIC_COLLECTION_COGNITIVE_CARD,
    SemanticVectorDocument,
)


ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION = "atomic_cognitive_card_v1"
ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION = "atomic_card_extractor_v18"
RELATION_PROBE_ROLES = frozenset(
    {"same_event", "upstream", "downstream", "confirmation", "contradiction"}
)


@dataclass(frozen=True)
class SpanReference:
    """程序生成的稳定原文引用。"""

    ref: str
    start_offset: int
    end_offset: int
    text: str

    def pointer(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
        }

    def llm_payload(self) -> dict[str, Any]:
        return {"ref": self.ref, "text": self.text}


@dataclass(frozen=True)
class RelationProbe:
    """用于后续关系候选发现的搜索假设，不代表正式关系。"""

    role: str
    query: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "query": self.query}


@dataclass(frozen=True)
class AtomicCognitiveCard:
    """一个可由原文直接支撑的原子事件或事实主张。"""

    cognitive_card_id: str
    adapter_name: str
    source_type: str
    source_id: str
    evidence_id: str
    primary_chunk_id: str
    chunk_ids: list[str]
    chunk_index: int
    summary: str
    focus_evidence_refs: list[str]
    focus_span_offsets: list[dict[str, Any]]
    factual_anchors: dict[str, Any]
    relation_probes: list[RelationProbe]
    source_published_at: str = ""
    source_title: str = ""
    schema_version: str = ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION
    generator_version: str = ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION
    status: str = "active"

    def manifest(self) -> "CognitiveCardManifest":
        return CognitiveCardManifest(
            cognitive_card_id=self.cognitive_card_id,
            adapter_name=self.adapter_name,
            source_type=self.source_type,
            source_id=self.source_id,
            evidence_id=self.evidence_id,
            primary_chunk_id=self.primary_chunk_id,
            chunk_ids=list(self.chunk_ids),
            chunk_index=self.chunk_index,
            focus_evidence_refs=list(self.focus_evidence_refs),
            focus_span_offsets=[dict(item) for item in self.focus_span_offsets],
            factual_anchors=dict(self.factual_anchors),
            schema_version=self.schema_version,
            generator_version=self.generator_version,
            status=self.status,
        )


@dataclass(frozen=True)
class CognitiveCardManifest:
    """PostgreSQL 中保存的 Card 身份、指针与紧凑事实结构。"""

    cognitive_card_id: str
    adapter_name: str
    source_type: str
    source_id: str
    evidence_id: str
    primary_chunk_id: str
    chunk_ids: list[str]
    chunk_index: int
    focus_evidence_refs: list[str]
    focus_span_offsets: list[dict[str, Any]]
    factual_anchors: dict[str, Any]
    schema_version: str
    generator_version: str
    status: str = "active"


@dataclass(frozen=True)
class AtomicCardExtractionResult:
    """单个 Chunk 的提取结果。"""

    chunk_id: str
    spans: list[SpanReference]
    cards: list[AtomicCognitiveCard]
    repaired: bool = False
    skip_reason: str = ""


class StableSpanSegmenter:
    """按句末标点和段落边界生成稳定、可回溯的 Span Ref。"""

    _BOUNDARY_RE = re.compile(r"(?:[。！？!?；;，,:：]+[\"'”’）】》]*|\n+)")

    def segment(self, content: str) -> list[SpanReference]:
        if not content:
            return []
        spans: list[SpanReference] = []
        cursor = 0
        for match in self._BOUNDARY_RE.finditer(content):
            self._append_span(spans, content, cursor, match.end())
            cursor = match.end()
        self._append_span(spans, content, cursor, len(content))
        return spans

    @staticmethod
    def _append_span(
        spans: list[SpanReference],
        content: str,
        raw_start: int,
        raw_end: int,
    ) -> None:
        start = raw_start
        end = raw_end
        while start < end and content[start].isspace():
            start += 1
        while end > start and content[end - 1].isspace():
            end -= 1
        if start >= end:
            return
        spans.append(
            SpanReference(
                ref=f"s{len(spans) + 1:04d}",
                start_offset=start,
                end_offset=end,
                text=content[start:end],
            )
        )


def atomic_card_from_llm_item(
    chunk: EvidenceChunk,
    item: dict[str, Any],
    *,
    spans: list[SpanReference],
) -> AtomicCognitiveCard:
    """把一个已通过 JSON Schema 的 LLM Card 转成受证据约束的领域对象。"""

    _validate_raw_card_shape(item)
    span_by_ref = {span.ref: span for span in spans}
    payload = dict(chunk.payload or {})
    summary = _clean_text(item.get("summary"))
    if not summary:
        raise ValueError("card.summary 不能为空")

    focus_refs = _ordered_unique(_clean_text(value) for value in item.get("focus_evidence_refs") or [])
    if not focus_refs:
        raise ValueError("card.focus_evidence_refs 至少包含一个 Span Ref")
    unknown_refs = [ref for ref in focus_refs if ref not in span_by_ref]
    if unknown_refs:
        raise ValueError(f"card 引用了不存在的 Span Ref: {unknown_refs}")

    anchors = _normalize_factual_anchors(item.get("factual_anchors"))
    _validate_anchor_grounding(
        anchors,
        [span_by_ref[ref].text for ref in focus_refs],
        source_published_at=_clean_text(payload.get("published_at")),
    )
    _validate_summary_grounding(
        summary,
        [span_by_ref[ref].text for ref in focus_refs],
        source_published_at=_clean_text(payload.get("published_at")),
    )
    probes = _normalize_relation_probes(item.get("relation_probes"))

    card_id = build_atomic_card_id(
        chunk=chunk,
        focus_evidence_refs=focus_refs,
    )
    return AtomicCognitiveCard(
        cognitive_card_id=card_id,
        adapter_name=chunk.adapter_name,
        source_type=_clean_text(payload.get("source_type")),
        source_id=_clean_text(payload.get("source_id")),
        evidence_id=chunk.evidence_id,
        primary_chunk_id=chunk.chunk_id,
        chunk_ids=[chunk.chunk_id],
        chunk_index=chunk.chunk_index,
        summary=summary,
        focus_evidence_refs=focus_refs,
        focus_span_offsets=[span_by_ref[ref].pointer() for ref in focus_refs],
        factual_anchors=anchors,
        relation_probes=probes,
        source_published_at=_clean_text(payload.get("published_at")),
        source_title=_clean_text(payload.get("title")),
    )


def _validate_raw_card_shape(item: dict[str, Any]) -> None:
    required = {"summary", "focus_evidence_refs", "factual_anchors", "relation_probes"}
    missing = sorted(required.difference(item))
    extra = sorted(set(item).difference(required))
    if missing or extra:
        raise ValueError(f"Card 字段不符合契约: missing={missing}, extra={extra}")
    if not isinstance(item.get("focus_evidence_refs"), list):
        raise ValueError("focus_evidence_refs 必须是数组")
    anchors = item.get("factual_anchors")
    anchor_fields = {
        "actors",
        "action",
        "objects",
        "event_time",
        "explicit_causes",
        "explicit_effects",
    }
    if not isinstance(anchors, dict) or set(anchors) != anchor_fields:
        raise ValueError("factual_anchors 字段不符合契约")
    for field_name, limit in (
        ("actors", 8),
        ("objects", 8),
        ("explicit_causes", 6),
        ("explicit_effects", 6),
    ):
        value = anchors.get(field_name)
        if not isinstance(value, list) or len(value) > limit:
            raise ValueError(f"factual_anchors.{field_name} 必须是最多 {limit} 项的数组")
    if len(_clean_text(anchors.get("action"))) > 32:
        raise ValueError("factual_anchors.action 超过 32 字符")
    probes = item.get("relation_probes")
    if not isinstance(probes, list) or len(probes) > 12:
        raise ValueError("relation_probes 必须是最多 12 项的数组")


def build_atomic_card_id(
    *,
    chunk: EvidenceChunk,
    focus_evidence_refs: list[str],
) -> str:
    """使用 Chunk 内容版本和焦点证据集合生成与模型措辞无关的稳定 ID。"""

    ordered_refs = sorted(set(focus_evidence_refs))
    signature = {
        "focus_evidence_range": [ordered_refs[0], ordered_refs[-1]],
    }
    raw = "\n".join(
        [
            chunk.adapter_name,
            chunk.evidence_id,
            chunk.chunk_id,
            str(chunk.text_hash or ""),
            json.dumps(signature, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION,
        ]
    )
    return "kg_cognitive_card:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def atomic_card_document(card: AtomicCognitiveCard) -> SemanticVectorDocument:
    """构建可直接用于检索、rerank 和按 ID 取回的 Milvus Card 文档。"""

    probe_lines = [f"{probe.role}: {probe.query}" for probe in card.relation_probes]
    text_parts = [
        "Document Type: Atomic Cognitive Card",
        f"Summary: {card.summary}",
    ]
    if probe_lines:
        text_parts.append("Relation Probes:\n" + "\n".join(probe_lines))
    text_parts.append(
        "Expandable Handles: "
        f"cognitive_card_id={card.cognitive_card_id} "
        f"evidence_id={card.evidence_id} chunk_id={card.primary_chunk_id}"
    )
    return SemanticVectorDocument(
        document_id=card.cognitive_card_id,
        document_type="atomic_cognitive_card",
        collection_role=SEMANTIC_COLLECTION_COGNITIVE_CARD,
        source_type="kg_cognitive_card",
        source_id=card.cognitive_card_id,
        evidence_id=card.evidence_id,
        text="\n".join(text_parts),
        metadata={
            "target_id": card.cognitive_card_id,
            "target_type": "atomic_cognitive_card",
            "cognitive_card_id": card.cognitive_card_id,
            "original_source_type": card.source_type,
            "original_source_id": card.source_id,
            "evidence_id": card.evidence_id,
            "primary_chunk_id": card.primary_chunk_id,
            "cited_chunk_ids": list(card.chunk_ids),
            "cited_evidence_ids": [card.evidence_id],
            "focus_evidence_refs": list(card.focus_evidence_refs),
            "focus_span_offsets": [dict(item) for item in card.focus_span_offsets],
            "factual_anchors": dict(card.factual_anchors),
            "relation_probes": [probe.as_dict() for probe in card.relation_probes],
            "summary": card.summary,
            "title": card.source_title,
            "source_published_at": card.source_published_at,
            "published_at": card.source_published_at,
            "event_time": _clean_text(card.factual_anchors.get("event_time")),
            "schema_version": card.schema_version,
            "generator_version": card.generator_version,
        },
    )


def _normalize_factual_anchors(value: Any) -> dict[str, Any]:
    data = value if isinstance(value, dict) else {}
    return {
        "actors": _ordered_unique(_clean_text(item) for item in data.get("actors") or []),
        "action": _clean_text(data.get("action")),
        "objects": _ordered_unique(_clean_text(item) for item in data.get("objects") or []),
        "event_time": _clean_text(data.get("event_time")),
        "explicit_causes": _ordered_unique(_clean_text(item) for item in data.get("explicit_causes") or []),
        "explicit_effects": _ordered_unique(_clean_text(item) for item in data.get("explicit_effects") or []),
    }


def _normalize_relation_probes(value: Any) -> list[RelationProbe]:
    result: list[RelationProbe] = []
    seen: set[tuple[str, str]] = set()
    for item in value or []:
        if not isinstance(item, dict):
            continue
        role = _clean_text(item.get("role"))
        query = _clean_text(item.get("query"))
        if role not in RELATION_PROBE_ROLES:
            raise ValueError(f"未知 Relation Probe role: {role}")
        if not query:
            raise ValueError("Relation Probe query 不能为空")
        key = (role, _normalize_text(query))
        if key in seen:
            raise ValueError(f"同一 Card 内 Relation Probe 重复: role={role}, query={query}")
        seen.add(key)
        result.append(RelationProbe(role=role, query=query))
    return result


def _validate_anchor_grounding(
    anchors: dict[str, Any],
    focus_texts: list[str],
    *,
    source_published_at: str,
) -> None:
    """只校验程序能够确定的数字接地，避免用字符启发式冒充语义裁决。"""

    source = _normalize_text("\n".join(focus_texts))
    values = [
        *anchors.get("actors", []),
        anchors.get("action", ""),
        *anchors.get("objects", []),
        anchors.get("event_time", ""),
        *anchors.get("explicit_causes", []),
        *anchors.get("explicit_effects", []),
    ]
    grounded_years = _grounded_year_tokens(focus_texts, source_published_at)
    unsupported: list[str] = []
    for value in values:
        tokens = re.findall(r"\d{4}年|\d+(?:\.\d+)?%?", str(value or ""))
        if any(not _numeric_token_is_grounded(token, source, grounded_years) for token in tokens):
            unsupported.append(str(value))
    if unsupported:
        raise ValueError(f"事实锚点包含焦点证据无法支持的数字或年份: {unsupported}")


def _validate_summary_grounding(
    summary: str,
    focus_texts: list[str],
    *,
    source_published_at: str,
) -> None:
    """拦截最确定的数字幻觉，语义忠实度仍由模型和质量回放负责。"""

    source = _normalize_text("\n".join(focus_texts))
    summary_tokens = set(re.findall(r"\d{4}年|\d+(?:\.\d+)?%?", summary))
    grounded_years = _grounded_year_tokens(focus_texts, source_published_at)
    unsupported = sorted(
        token
        for token in summary_tokens
        if not _numeric_token_is_grounded(token, source, grounded_years)
    )
    if unsupported:
        raise ValueError(f"Summary 包含焦点证据未出现的数字或年份: {unsupported}")


def _grounded_year_tokens(focus_texts: list[str], source_published_at: str) -> set[str]:
    if not source_published_at:
        return set()
    try:
        published = datetime.fromisoformat(source_published_at.replace("Z", "+00:00"))
    except ValueError:
        return set()
    source = "\n".join(focus_texts)
    years: set[int] = set()
    if "今年" in source or re.search(r"(?<!\d)(?:1[0-2]|[1-9])月", source):
        years.add(published.year)
    if "去年" in source:
        years.add(published.year - 1)
    if "明年" in source:
        years.add(published.year + 1)
    return {f"{year}年" for year in years}


def _numeric_token_is_grounded(token: str, source: str, grounded_years: set[str]) -> bool:
    """允许百分比省略无意义的小数零，同时保持其他数字的精确接地。"""

    normalized = _normalize_text(token)
    if normalized in source or token in grounded_years:
        return True
    if not token.endswith("%"):
        return False
    try:
        expected = float(token[:-1])
    except ValueError:
        return False
    return any(
        float(value) == expected
        for value in re.findall(r"(\d+(?:\.\d+)?)%", source)
    )


def _ordered_unique(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean_text(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip().lower()
