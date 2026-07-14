"""原子 Cognitive Card 的领域契约、证据切分与可读索引材料。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.domain.knowledge.schemas import EvidenceChunk
from src.domain.knowledge.relation_discovery import VerifiedRelationDecision
from src.domain.knowledge.semantic_index_materials import (
    SEMANTIC_COLLECTION_COGNITIVE_CARD,
    SEMANTIC_COLLECTION_COGNITIVE_CARD_FOCUS,
    SemanticVectorDocument,
)


ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION = "atomic_cognitive_card_v5"
ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION = "atomic_card_extractor_v38"
INTRA_CHUNK_RELATION_KINDS = frozenset(
    {
        "confirmation",
        "contradiction",
        "temporal_progression",
        "causal_influence",
        "common_driver",
        "constraint",
    }
)
_LOCAL_CARD_ID_RE = re.compile(r"(?<![A-Za-z0-9_])c(?:[1-9]|1[0-2])(?![A-Za-z0-9_])", re.IGNORECASE)


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
            schema_version=self.schema_version,
            generator_version=self.generator_version,
            status=self.status,
        )


@dataclass(frozen=True)
class CognitiveCardManifest:
    """PostgreSQL 中保存的 Card 身份与证据指针。"""

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
    schema_version: str
    generator_version: str
    status: str = "active"


@dataclass(frozen=True)
class AtomicCardExtractionResult:
    """单个 Chunk 的提取结果。"""

    chunk_id: str
    spans: list[SpanReference]
    cards: list[AtomicCognitiveCard]
    relations: list[VerifiedRelationDecision]
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

    _validate_summary_grounding(
        summary,
        [span_by_ref[ref].text for ref in focus_refs],
        source_published_at=_clean_text(payload.get("published_at")),
    )
    card_id = build_atomic_card_id(
        chunk=chunk,
        focus_evidence_refs=focus_refs,
        summary=summary,
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
        source_published_at=_clean_text(payload.get("published_at")),
        source_title=_clean_text(payload.get("title")),
    )


def intra_chunk_relation_from_llm_item(
    item: dict[str, Any],
    *,
    cards_by_local_id: dict[str, AtomicCognitiveCard],
) -> VerifiedRelationDecision:
    """把本次提取中的局部 Card 引用转换为正式关系决定。"""

    required = {
        "source_card_id",
        "target_card_id",
        "decision_class",
        "relation_kind",
        "relation_type",
        "direction",
        "basis",
        "source_evidence_refs",
        "target_evidence_refs",
        "inference_mechanism",
        "confidence",
    }
    missing = sorted(required.difference(item))
    extra = sorted(set(item).difference(required))
    if missing or extra:
        raise ValueError(f"同 Chunk Relation 字段不符合契约: missing={missing}, extra={extra}")

    source_local_id = _clean_text(item.get("source_card_id"))
    target_local_id = _clean_text(item.get("target_card_id"))
    if source_local_id == target_local_id:
        raise ValueError("同 Chunk Relation 两端不能引用同一个 Card")
    if source_local_id not in cards_by_local_id or target_local_id not in cards_by_local_id:
        raise ValueError(
            "同 Chunk Relation 引用了不存在的局部 Card: "
            f"source={source_local_id}, target={target_local_id}"
        )

    decision_class = _clean_text(item.get("decision_class"))
    if decision_class not in {"observed", "inferred"}:
        raise ValueError(f"同 Chunk Relation decision_class 非法: {decision_class}")
    relation_kind = _clean_text(item.get("relation_kind"))
    if relation_kind not in INTRA_CHUNK_RELATION_KINDS:
        raise ValueError(f"同 Chunk Relation relation_kind 非法: {relation_kind}")

    source_card = cards_by_local_id[source_local_id]
    target_card = cards_by_local_id[target_local_id]
    source_refs = _ordered_unique(item.get("source_evidence_refs") or [])
    target_refs = _ordered_unique(item.get("target_evidence_refs") or [])
    if not source_refs or not target_refs:
        raise ValueError("同 Chunk Relation 必须引用双方最小充分 Focus Evidence")
    if not set(source_refs).issubset(source_card.focus_evidence_refs):
        raise ValueError(f"同 Chunk Relation source refs 不属于 {source_local_id}")
    if not set(target_refs).issubset(target_card.focus_evidence_refs):
        raise ValueError(f"同 Chunk Relation target refs 不属于 {target_local_id}")

    relation_type = _clean_text(item.get("relation_type"))
    direction = _clean_text(item.get("direction"))
    basis = _clean_text(item.get("basis"))
    mechanism = _clean_text(item.get("inference_mechanism"))
    if not relation_type or not direction or not basis:
        raise ValueError("同 Chunk Relation 必须包含关系类型、方向和成立依据")
    if any(_LOCAL_CARD_ID_RE.search(value) for value in (relation_type, direction, basis, mechanism)):
        raise ValueError("同 Chunk Relation 的语义说明不能引用临时 local_card_id")
    if decision_class == "inferred" and not mechanism:
        raise ValueError("同 Chunk inferred Relation 必须包含 inference_mechanism")
    try:
        confidence = float(item.get("confidence"))
    except (TypeError, ValueError) as exc:
        raise ValueError("同 Chunk Relation confidence 必须是 0 到 1 的数字") from exc
    if not 0 <= confidence <= 1:
        raise ValueError("同 Chunk Relation confidence 必须处于 0 到 1")

    return VerifiedRelationDecision(
        source_card_id=source_card.cognitive_card_id,
        target_card_id=target_card.cognitive_card_id,
        decision_class=decision_class,  # type: ignore[arg-type]
        relation_kind=relation_kind,
        relation_type=relation_type,
        direction=direction,
        basis=basis,
        source_evidence_refs=source_refs,
        target_evidence_refs=target_refs,
        inference_mechanism=mechanism,
        confidence=confidence,
    )


def _validate_raw_card_shape(item: dict[str, Any]) -> None:
    required = {"summary", "focus_evidence_refs"}
    missing = sorted(required.difference(item))
    extra = sorted(set(item).difference(required))
    if missing or extra:
        raise ValueError(f"Card 字段不符合契约: missing={missing}, extra={extra}")
    if not isinstance(item.get("focus_evidence_refs"), list):
        raise ValueError("focus_evidence_refs 必须是数组")


def build_atomic_card_id(
    *,
    chunk: EvidenceChunk,
    focus_evidence_refs: list[str],
    summary: str,
) -> str:
    """使用 Chunk、焦点范围和原子事实摘要生成稳定 Card ID。"""

    ordered_refs = sorted(set(focus_evidence_refs))
    signature = {
        "focus_evidence_range": [ordered_refs[0], ordered_refs[-1]],
        "summary_fingerprint": hashlib.sha256(
            _normalize_text(summary).encode("utf-8")
        ).hexdigest()[:16],
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


def atomic_card_summary_document(card: AtomicCognitiveCard) -> SemanticVectorDocument:
    """构建只包含 Summary 的 Milvus 语义视图。"""

    return SemanticVectorDocument(
        document_id=card.cognitive_card_id,
        document_type="atomic_cognitive_card_summary",
        collection_role=SEMANTIC_COLLECTION_COGNITIVE_CARD,
        source_type="kg_cognitive_card",
        source_id=card.cognitive_card_id,
        evidence_id=card.evidence_id,
        text=card.summary,
        metadata=_atomic_card_milvus_metadata(card, target_type="atomic_cognitive_card_summary"),
    )


def atomic_card_focus_document(
    card: AtomicCognitiveCard,
    *,
    chunk_content: str,
) -> SemanticVectorDocument:
    """按 PG offset 从 Primary Chunk 确定性拼接原始焦点证据。"""

    focus_text = materialize_focus_evidence_text(
        chunk_content,
        focus_span_offsets=card.focus_span_offsets,
    )
    return SemanticVectorDocument(
        document_id=card.cognitive_card_id,
        document_type="atomic_cognitive_card_focus_evidence",
        collection_role=SEMANTIC_COLLECTION_COGNITIVE_CARD_FOCUS,
        source_type="kg_cognitive_card_focus_evidence",
        source_id=card.cognitive_card_id,
        evidence_id=card.evidence_id,
        text=focus_text,
        metadata=_atomic_card_milvus_metadata(
            card,
            target_type="atomic_cognitive_card_focus_evidence",
        ),
    )


def materialize_focus_evidence_text(
    chunk_content: str,
    *,
    focus_span_offsets: list[dict[str, Any]],
) -> str:
    """从当前 Chunk 提取有序原文片段，不做任何模型改写。"""

    return "\n".join(
        item["text"]
        for item in materialize_focus_evidence_items(
            chunk_content,
            focus_span_offsets=focus_span_offsets,
        )
    )


def materialize_focus_evidence_items(
    chunk_content: str,
    *,
    focus_span_offsets: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """恢复带稳定 ref 的焦点原文，供关系核验直接引用。"""

    parts: list[dict[str, str]] = []
    seen: set[tuple[int, int]] = set()
    ordered = sorted(
        focus_span_offsets,
        key=lambda item: (int(item.get("start_offset", -1)), int(item.get("end_offset", -1))),
    )
    for pointer in ordered:
        start = int(pointer.get("start_offset", -1))
        end = int(pointer.get("end_offset", -1))
        if start < 0 or end <= start or end > len(chunk_content):
            raise ValueError(
                f"Focus Evidence offset 越界: ref={pointer.get('ref')} start={start} end={end} "
                f"chunk_length={len(chunk_content)}"
            )
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        text = chunk_content[start:end]
        if text.strip():
            ref = str(pointer.get("ref") or "").strip()
            if not ref:
                raise ValueError("Focus Evidence offset 缺少 ref")
            parts.append({"ref": ref, "text": text})
    if not parts:
        raise ValueError("Focus Evidence 无法从 Primary Chunk 拼接出正文")
    return parts


def materialize_focus_evidence_context(
    chunk_content: str,
    *,
    focus_span_offsets: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """把完整原文切成有序片段，焦点片段与稳定 ref 严格一一对应。"""

    spans: list[tuple[int, int, str]] = []
    for pointer in focus_span_offsets:
        start = int(pointer.get("start_offset", -1))
        end = int(pointer.get("end_offset", -1))
        ref = str(pointer.get("ref") or "").strip()
        if start < 0 or end <= start or end > len(chunk_content):
            raise ValueError(
                f"Focus Evidence offset 越界: ref={ref} start={start} end={end} "
                f"chunk_length={len(chunk_content)}"
            )
        if not ref:
            raise ValueError("Focus Evidence offset 缺少 ref")
        spans.append((start, end, ref))
    if not spans:
        raise ValueError("Focus Evidence 不能为空")

    spans.sort(key=lambda item: (item[0], item[1], item[2]))
    rendered: list[dict[str, Any]] = []
    refs: list[str] = []
    cursor = 0
    for start, end, ref in spans:
        if start < cursor:
            raise ValueError(
                f"同一 Card 的 Focus Evidence 区间不能重叠: ref={ref} start={start} cursor={cursor}"
            )
        if cursor < start:
            rendered.append(
                {"text": chunk_content[cursor:start], "evidence_ref": None}
            )
        rendered.append(
            {"text": chunk_content[start:end], "evidence_ref": ref}
        )
        refs.append(ref)
        cursor = end
    if cursor < len(chunk_content):
        rendered.append(
            {"text": chunk_content[cursor:], "evidence_ref": None}
        )
    if not rendered:
        raise ValueError("Focus Evidence 无法生成核验上下文")
    return rendered, refs


def _atomic_card_milvus_metadata(
    card: AtomicCognitiveCard,
    *,
    target_type: str,
) -> dict[str, Any]:
    """Milvus 只保存语义检索和精确取回所需的最小指针。"""

    return {
        "target_id": card.cognitive_card_id,
        "target_type": target_type,
        "cognitive_card_id": card.cognitive_card_id,
        "original_source_type": card.source_type,
        "original_source_id": card.source_id,
        "evidence_id": card.evidence_id,
        "primary_chunk_id": card.primary_chunk_id,
        "cited_chunk_ids": list(card.chunk_ids),
        "cited_evidence_ids": [card.evidence_id],
        "source_published_at": card.source_published_at,
        "published_at": card.source_published_at,
        "schema_version": card.schema_version,
        "generator_version": card.generator_version,
        "status": card.status,
    }


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
