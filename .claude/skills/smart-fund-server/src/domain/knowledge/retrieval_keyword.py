"""Lexical keyword retrieval helpers for retrieval documents."""

from __future__ import annotations

import re

from pydantic import Field

from src.domain.knowledge.retrieval_document import RetrievalDocument
from src.domain.knowledge.schemas import KnowledgeBaseModel


class KeywordRetrievalMatch(KnowledgeBaseModel):
    document: RetrievalDocument
    hit_type: str
    matched_terms: list[str] = Field(default_factory=list)
    matched_fields: list[str] = Field(default_factory=list)
    score: float = 0.0


def lexical_query_terms(query: str) -> list[str]:
    raw_terms = [
        item.lower()
        for item in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", query)
        if item.strip()
    ]
    terms: list[str] = []
    for term in raw_terms:
        if _is_cjk(term) and len(term) > 2:
            terms.extend(_cjk_terms(term))
        terms.append(term)
    compact = query.strip()
    if compact and compact not in terms and len(compact) <= 40:
        terms.append(compact)
    return _ordered_unique(terms)


def build_keyword_match(document: RetrievalDocument, terms: list[str]) -> KeywordRetrievalMatch:
    matched_terms = keyword_matched_terms(document, terms)
    matched_fields = keyword_matched_fields(document, matched_terms)
    return KeywordRetrievalMatch(
        document=document,
        hit_type=retrieval_document_hit_type(document),
        matched_terms=matched_terms,
        matched_fields=matched_fields,
        score=retrieval_document_keyword_score(document, matched_terms, matched_fields),
    )


def retrieval_document_hit_type(document: RetrievalDocument) -> str:
    if document.source_fact_type in {"node", "edge", "evidence", "wiki"}:
        return document.source_fact_type
    return "evidence"


def keyword_matched_terms(document: RetrievalDocument, terms: list[str]) -> list[str]:
    haystack = "\n".join(
        [
            document.title,
            document.search_text,
            " ".join(document.key_phrases),
            " ".join(document.aliases),
            " ".join(document.readable_relations),
            document.evidence_summary,
        ]
    ).lower()
    return _ordered_unique(term for term in terms if term.lower() in haystack)


def keyword_matched_fields(document: RetrievalDocument, matched_terms: list[str]) -> list[str]:
    field_values = {
        "title": document.title,
        "search_text": document.search_text,
        "key_phrases": " ".join(document.key_phrases),
        "aliases": " ".join(document.aliases),
        "readable_relations": " ".join(document.readable_relations),
        "evidence_summary": document.evidence_summary,
    }
    fields: list[str] = []
    for field, value in field_values.items():
        lowered = value.lower()
        if any(term.lower() in lowered for term in matched_terms):
            fields.append(field)
    return fields


def retrieval_document_keyword_score(
    document: RetrievalDocument,
    matched_terms: list[str] | None = None,
    matched_fields: list[str] | None = None,
) -> float:
    terms = matched_terms if matched_terms is not None else keyword_matched_terms(document, [])
    fields = matched_fields if matched_fields is not None else keyword_matched_fields(document, terms)
    score = float(len(terms))
    score += 2.0 if "title" in fields else 0.0
    score += 1.5 if "key_phrases" in fields else 0.0
    score += 1.0 if "readable_relations" in fields else 0.0
    score += 0.5 if document.answer_candidate_type == "answer" else 0.0
    return score


def retrieval_document_query_score(document: RetrievalDocument, query_terms: list[str]) -> float:
    match = build_keyword_match(document, query_terms)
    return match.score


def _is_cjk(value: str) -> bool:
    return all("\u4e00" <= char <= "\u9fff" for char in value)


def _cjk_terms(value: str) -> list[str]:
    phrases = [value]
    if len(value) <= 6:
        phrases.extend(value[index : index + 2] for index in range(len(value) - 1))
        return phrases
    phrases.extend(value[index : index + 2] for index in range(len(value) - 1))
    phrases.extend(value[index : index + 3] for index in range(len(value) - 2))
    return phrases


def _ordered_unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
