"""Evidence chunking utilities for KG semantic indexing."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from src.domain.knowledge.schemas import CompiledEvidence, EvidenceChunk

DEFAULT_CHUNK_MAX_CHARS = 900
DEFAULT_CHUNKER_VERSION = "recursive_zh_v1"

_SEPARATORS = ("\n\n", "\n", "。", "！", "？", "；", "，", " ")


@dataclass(frozen=True)
class TextSegment:
    start: int
    end: int
    text: str


def build_chunks_for_compiled_evidence(evidence: CompiledEvidence) -> list[EvidenceChunk]:
    return build_evidence_chunks(
        adapter_name=evidence.adapter_name,
        evidence_id=evidence.evidence_id,
        content=evidence_content_for_chunking(evidence.content, evidence.payload),
        payload={
            **(evidence.payload or {}),
            "status": evidence.status.value,
            "source_type": evidence.source_type,
            "source_id": evidence.source_id,
            "evidence_type": evidence.evidence_type.value,
            "version": evidence.version,
        },
    )


def build_evidence_chunks(
    *,
    adapter_name: str,
    evidence_id: str,
    content: str,
    payload: dict[str, Any] | None = None,
    max_chars: int = DEFAULT_CHUNK_MAX_CHARS,
    chunker_version: str = DEFAULT_CHUNKER_VERSION,
) -> list[EvidenceChunk]:
    text = content.strip()
    if not text:
        return []
    segments = recursive_text_segments(text, max_chars=max_chars)
    chunks: list[EvidenceChunk] = []
    total = len(segments)
    for index, segment in enumerate(segments):
        chunk_id = f"kg_chunk:{evidence_id}:{index}"
        previous_chunk_id = f"kg_chunk:{evidence_id}:{index - 1}" if index > 0 else ""
        next_chunk_id = f"kg_chunk:{evidence_id}:{index + 1}" if index + 1 < total else ""
        chunks.append(
            EvidenceChunk(
                chunk_id=chunk_id,
                adapter_name=adapter_name,
                evidence_id=evidence_id,
                content=segment.text,
                chunk_index=index,
                start_offset=segment.start,
                end_offset=segment.end,
                previous_chunk_id=previous_chunk_id,
                next_chunk_id=next_chunk_id,
                text_hash=_text_hash(segment.text),
                chunker_version=chunker_version,
                payload={
                    **(payload or {}),
                    "chunk_index": index,
                    "start_offset": segment.start,
                    "end_offset": segment.end,
                    "previous_chunk_id": previous_chunk_id,
                    "next_chunk_id": next_chunk_id,
                    "chunker_version": chunker_version,
                    "text_hash": _text_hash(segment.text),
                },
            )
        )
    return chunks


def evidence_content_for_chunking(content: str | None, payload: dict[str, Any] | None) -> str:
    """Return the exact text whose offsets are stored in kg_evidence_chunks."""

    if content and content.strip():
        return content.strip()
    if payload:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return ""


def evidence_text_for_chunking(content: str | None, payload: dict[str, Any] | None) -> str:
    """Return enriched text for semantic indexing, not for chunk offset manifests."""

    payload = payload or {}
    parts: list[str] = []
    if isinstance(payload, dict):
        parts.extend(
            str(payload.get(name) or "")
            for name in ("title", "source_name", "signal_type")
            if payload.get(name)
        )
        parts.extend(_entity_search_terms(payload.get("mentioned_entities")))
        parts.extend(_entity_search_terms(payload.get("affected_entities")))
        parts.extend(_entity_search_terms([payload.get("target_ref")]))
    if content and content.strip():
        parts.append(content)
    elif payload:
        parts.append(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return "\n".join(_ordered_unique(part.strip() for part in parts if part and part.strip()))


def recursive_text_segments(text: str, *, max_chars: int = DEFAULT_CHUNK_MAX_CHARS) -> list[TextSegment]:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    raw_segments = _recursive_split(text, start_offset=0, separators=_SEPARATORS, max_chars=max_chars)
    return _merge_segments(raw_segments, max_chars=max_chars)


def _recursive_split(
    text: str,
    *,
    start_offset: int,
    separators: tuple[str, ...],
    max_chars: int,
) -> list[TextSegment]:
    text = text or ""
    if len(text) <= max_chars:
        segment = _trim_segment(start_offset, start_offset + len(text), text)
        return [segment] if segment.text else []
    if not separators:
        return _fixed_segments(text, start_offset=start_offset, max_chars=max_chars)

    separator = separators[0]
    if separator not in text:
        return _recursive_split(
            text,
            start_offset=start_offset,
            separators=separators[1:],
            max_chars=max_chars,
        )

    pieces = _split_with_offsets(text, separator=separator, start_offset=start_offset)
    segments: list[TextSegment] = []
    for piece in pieces:
        if len(piece.text) > max_chars:
            segments.extend(
                _recursive_split(
                    piece.text,
                    start_offset=piece.start,
                    separators=separators[1:],
                    max_chars=max_chars,
                )
            )
        else:
            trimmed = _trim_segment(piece.start, piece.end, piece.text)
            if trimmed.text:
                segments.append(trimmed)
    return segments


def _split_with_offsets(text: str, *, separator: str, start_offset: int) -> list[TextSegment]:
    pieces: list[TextSegment] = []
    cursor = 0
    sep_len = len(separator)
    while cursor < len(text):
        pos = text.find(separator, cursor)
        if pos < 0:
            end = len(text)
        else:
            end = pos + sep_len
        piece = text[cursor:end]
        pieces.append(TextSegment(start=start_offset + cursor, end=start_offset + end, text=piece))
        cursor = end
    return pieces


def _merge_segments(segments: list[TextSegment], *, max_chars: int) -> list[TextSegment]:
    merged: list[TextSegment] = []
    current: list[TextSegment] = []
    current_len = 0
    for segment in segments:
        if len(segment.text) > max_chars:
            if current:
                merged.append(_join_segments(current))
                current = []
                current_len = 0
            merged.extend(_fixed_segments(segment.text, start_offset=segment.start, max_chars=max_chars))
            continue
        sep_len = 1 if current else 0
        if current and current_len + sep_len + len(segment.text) > max_chars:
            merged.append(_join_segments(current))
            current = [segment]
            current_len = len(segment.text)
        else:
            current.append(segment)
            current_len += sep_len + len(segment.text)
    if current:
        merged.append(_join_segments(current))
    return merged


def _join_segments(segments: list[TextSegment]) -> TextSegment:
    start = segments[0].start
    end = segments[-1].end
    text = "\n".join(segment.text.strip() for segment in segments if segment.text.strip())
    return TextSegment(start=start, end=end, text=text)


def _fixed_segments(text: str, *, start_offset: int, max_chars: int) -> list[TextSegment]:
    result: list[TextSegment] = []
    for start in range(0, len(text), max_chars):
        end = min(start + max_chars, len(text))
        segment = _trim_segment(start_offset + start, start_offset + end, text[start:end])
        if segment.text:
            result.append(segment)
    return result


def _trim_segment(start: int, end: int, text: str) -> TextSegment:
    left_trimmed = len(text) - len(text.lstrip())
    right_trimmed = len(text.rstrip())
    return TextSegment(
        start=start + left_trimmed,
        end=start + right_trimmed,
        text=text.strip(),
    )


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _entity_search_terms(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    terms: list[str] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        for name in ("name", "code", "indicator_code", "taxonomy"):
            if item.get(name):
                terms.append(str(item[name]))
        if item.get("exchange") and item.get("code"):
            terms.append(f"{item['exchange']}:{item['code']}")
    return terms


def _ordered_unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
