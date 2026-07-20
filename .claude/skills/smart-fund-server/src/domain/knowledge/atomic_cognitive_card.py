"""原子 Cognitive Card 的领域契约、证据切分与可读索引材料。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from src.domain.knowledge.schemas import EvidenceChunk
from src.domain.knowledge.relation_discovery import (
    RELATION_PROBE_ROLES,
    RelationProbe,
    VerifiedRelationDecision,
)
from src.domain.knowledge.semantic_index_materials import (
    SEMANTIC_COLLECTION_COGNITIVE_CARD,
    SEMANTIC_COLLECTION_COGNITIVE_CARD_FOCUS,
    SemanticVectorDocument,
)


ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION = "atomic_cognitive_card_v7"
ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION = "atomic_card_extractor_v103"
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

    def llm_fragment(self) -> str:
        """渲染为紧凑且可直接阅读的证据片段。"""

        return f"[{self.ref}]{self.text}"


@dataclass(frozen=True)
class SpanSentenceBlock:
    """供模型连续阅读的完整句子块，parts 仅承担精确证据引用。"""

    role: str
    parts: list[SpanReference]

    def llm_line(self) -> str:
        """保留完整句子边界，并仅为标题增加轻量角色标记。"""

        role_prefix = "<title>" if self.role == "title" else ""
        return role_prefix + "".join(part.llm_fragment() for part in self.parts)


def render_atomic_card_prompt_input(
    *,
    source_published_at: Any,
    source_title: Any = "",
    sentence_blocks: list[SpanSentenceBlock],
) -> str:
    """将动态输入渲染为按句换行的 Ref 文本，避免冗长 JSON 层级。"""

    lines = [f"published_at={str(source_published_at or '').strip()}"]
    normalized_title = str(source_title or "").strip()
    for index, block in enumerate(sentence_blocks):
        line = block.llm_line()
        block_text = "".join(part.text for part in block.parts).strip()
        if (
            index == 0
            and block.role != "title"
            and normalized_title
            and block_text == normalized_title
        ):
            line = f"<title>{line}"
        lines.append(line)
    return "\n".join(lines)


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
    relation_probes: list[RelationProbe] = field(default_factory=list)
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
            relation_probes=list(self.relation_probes),
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
    relation_probes: list[RelationProbe] = field(default_factory=list)
    status: str = "active"


@dataclass(frozen=True)
class AtomicCardExtractionResult:
    """单个 Chunk 的提取结果。"""

    chunk_id: str
    spans: list[SpanReference]
    cards: list[AtomicCognitiveCard]
    relations: list[VerifiedRelationDecision]
    selected_model: str = ""
    model_route: str = ""
    input_text_chars: int = 0
    repaired: bool = False
    repair_attempted: bool = False
    discarded_card_count: int = 0
    discarded_relation_count: int = 0
    validation_issues: list[str] = field(default_factory=list)
    skip_reason: str = ""


class StableSpanSegmenter:
    """按完整句子组织阅读上下文，并生成稳定的证据 Ref。"""

    _HARD_BOUNDARY_RE = re.compile(r"(?:[。！？!?；;]+[\"'”’）】》]*|\n+)")
    _LEADING_TITLE_RE = re.compile(r"^\s*【[^】\n]{1,200}】")
    _SOFT_BOUNDARY_RE = re.compile(r"[，,:：]+[\"'”’）】》]*")
    _MAX_SPAN_CHARS = 80

    def segment(self, content: str) -> list[SpanReference]:
        return [part for block in self.segment_blocks(content) for part in block.parts]

    def segment_blocks(self, content: str) -> list[SpanSentenceBlock]:
        if not content:
            return []
        spans: list[SpanReference] = []
        blocks: list[SpanSentenceBlock] = []
        cursor = 0

        title_match = self._LEADING_TITLE_RE.match(content)
        if title_match is not None:
            self._append_block(
                blocks,
                spans,
                content,
                title_match.start(),
                title_match.end(),
                role="title",
            )
            cursor = title_match.end()

        for match in self._HARD_BOUNDARY_RE.finditer(content, cursor):
            self._append_block(
                blocks,
                spans,
                content,
                cursor,
                match.end(),
                role="body",
            )
            cursor = match.end()
        self._append_block(
            blocks,
            spans,
            content,
            cursor,
            len(content),
            role="body",
        )
        return self._deduplicate_blocks(blocks)

    @staticmethod
    def _deduplicate_blocks(blocks: list[SpanSentenceBlock]) -> list[SpanSentenceBlock]:
        """删除正文中完全重复的句子块，同时保留第一次出现的原始 offset。"""

        result: list[SpanSentenceBlock] = []
        seen_body: set[str] = set()
        for block in blocks:
            if block.role != "body":
                result.append(block)
                continue
            normalized = _normalize_text("".join(part.text for part in block.parts))
            if normalized and normalized in seen_body:
                continue
            if normalized:
                seen_body.add(normalized)
            result.append(block)
        return result

    @classmethod
    def _append_block(
        cls,
        blocks: list[SpanSentenceBlock],
        spans: list[SpanReference],
        content: str,
        raw_start: int,
        raw_end: int,
        *,
        role: str,
    ) -> None:
        start, end = cls._trim_range(content, raw_start, raw_end)
        if start >= end:
            return
        first_part_index = len(spans)
        if role == "title":
            cls._append_span(spans, content, start, end)
        else:
            cls._append_semantic_range(spans, content, start, end)
        parts = spans[first_part_index:]
        if parts:
            blocks.append(SpanSentenceBlock(role=role, parts=parts))

    @classmethod
    def _append_semantic_range(
        cls,
        spans: list[SpanReference],
        content: str,
        raw_start: int,
        raw_end: int,
    ) -> None:
        start, end = cls._trim_range(content, raw_start, raw_end)
        if start >= end:
            return
        if end - start <= cls._MAX_SPAN_CHARS:
            cls._append_span(spans, content, start, end)
            return

        soft_boundaries = list(cls._SOFT_BOUNDARY_RE.finditer(content, start, end))
        if not soft_boundaries:
            cls._append_span(spans, content, start, end)
            return

        group_start = start
        current_end = start
        for part_end in [*(boundary.end() for boundary in soft_boundaries), end]:
            if (
                current_end > group_start
                and part_end - group_start > cls._MAX_SPAN_CHARS
            ):
                cls._append_span(spans, content, group_start, current_end)
                group_start = current_end
            current_end = part_end
        cls._append_span(spans, content, group_start, end)

    @staticmethod
    def _trim_range(content: str, raw_start: int, raw_end: int) -> tuple[int, int]:
        start = raw_start
        end = raw_end
        while start < end and content[start].isspace():
            start += 1
        while end > start and content[end - 1].isspace():
            end -= 1
        return start, end

    @staticmethod
    def _append_span(
        spans: list[SpanReference],
        content: str,
        raw_start: int,
        raw_end: int,
    ) -> None:
        start, end = StableSpanSegmenter._trim_range(content, raw_start, raw_end)
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

    focus_refs = _ordered_unique(
        _normalize_span_ref(value) for value in item.get("focus_evidence_refs") or []
    )
    if not focus_refs:
        raise ValueError("card.focus_evidence_refs 至少包含一个 Span Ref")
    unknown_refs = [ref for ref in focus_refs if ref not in span_by_ref]
    if unknown_refs:
        raise ValueError(f"card 引用了不存在的 Span Ref: {unknown_refs}")

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
        relation_probes=[],
        source_published_at=_clean_text(payload.get("published_at")),
        source_title=_clean_text(payload.get("title")),
    )


def intra_chunk_relation_from_llm_item(
    item: dict[str, Any],
    *,
    chunk: EvidenceChunk,
    spans: list[SpanReference],
    cards_by_local_id: dict[str, AtomicCognitiveCard],
) -> VerifiedRelationDecision:
    """把本次提取中的局部 Card 引用转换为正式关系决定。"""

    required = {
        "source_card_id",
        "target_card_id",
        "relation_kind",
        "basis",
        "relation_evidence_refs",
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

    relation_kind = _clean_text(item.get("relation_kind"))
    if relation_kind not in INTRA_CHUNK_RELATION_KINDS:
        raise ValueError(f"同 Chunk Relation relation_kind 非法: {relation_kind}")

    source_card = cards_by_local_id[source_local_id]
    target_card = cards_by_local_id[target_local_id]
    relation_refs = _ordered_unique(
        _normalize_span_ref(value)
        for value in item.get("relation_evidence_refs") or []
    )
    known_refs = {span.ref for span in spans}
    if not relation_refs:
        raise ValueError("同 Chunk Relation 必须引用直接证明连接成立的原文证据")
    unknown_refs = sorted(set(relation_refs).difference(known_refs))
    if unknown_refs:
        raise ValueError(f"同 Chunk Relation 引用了不存在的 Span Ref: {unknown_refs}")

    basis = _clean_text(item.get("basis"))
    if not basis:
        raise ValueError("同 Chunk Relation 必须包含可读的成立依据")

    relation_type, direction = _normalized_intra_chunk_relation_fields(relation_kind)

    return VerifiedRelationDecision(
        source_card_id=source_card.cognitive_card_id,
        target_card_id=target_card.cognitive_card_id,
        decision_class="observed",
        relation_kind=relation_kind,
        relation_type=relation_type,
        direction=direction,
        basis=basis,
        source_evidence_refs=list(source_card.focus_evidence_refs),
        target_evidence_refs=list(target_card.focus_evidence_refs),
        inference_mechanism="",
        confidence=1.0,
        relation_evidence_refs=[
            {"chunk_id": chunk.chunk_id, "refs": relation_refs}
        ],
    )


def _normalized_intra_chunk_relation_fields(relation_kind: str) -> tuple[str, str]:
    labels = {
        "confirmation": "原文事实相互印证",
        "contradiction": "原文事实相互冲突",
        "temporal_progression": "原文事实构成时间进展",
        "causal_influence": "原文明确因果影响",
        "common_driver": "原文明确共同驱动",
        "constraint": "原文明确约束关系",
    }
    direction = (
        "symmetric"
        if relation_kind in {"confirmation", "contradiction", "common_driver"}
        else "source_to_target"
    )
    return labels[relation_kind], direction


def _validate_raw_card_shape(item: dict[str, Any]) -> None:
    required = {"summary", "focus_evidence_refs"}
    missing = sorted(required.difference(item))
    extra = sorted(set(item).difference(required))
    if missing or extra:
        raise ValueError(f"Card 字段不符合契约: missing={missing}, extra={extra}")
    if not isinstance(item.get("focus_evidence_refs"), list):
        raise ValueError("focus_evidence_refs 必须是数组")


def relation_probes_from_llm_items(value: Any) -> list[RelationProbe]:
    """把独立 Probe 规划会话的输出转换为领域对象。"""

    result: list[RelationProbe] = []
    seen: set[tuple[str, str]] = set()
    for item in value or []:
        if not isinstance(item, dict):
            raise ValueError("Relation Probe 必须是对象")
        if set(item) != {"role", "query"}:
            raise ValueError("Relation Probe 只能包含 role 和 query")
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


def _normalize_span_ref(value: Any) -> str:
    """兼容模型偶尔返回的 `[s0001]`，领域内统一保存为 `s0001`。"""

    ref = _clean_text(value)
    match = re.fullmatch(r"\[?(s\d{4})\]?", ref)
    return match.group(1) if match else ref


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
