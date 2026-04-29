"""Financial text extraction strategy with LLM enrichment."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from src.domain.knowledge.adapter import ValidationResult
from src.domain.knowledge.extraction import (
    EvidenceSpan,
    ExtractedEntity,
    TextExtractionInput,
    TextExtractionPipeline,
    TextExtractionResult,
)
from src.domain.knowledge_adapters.financial.ontology import CORE_ENTITY_TYPES, CORE_RELATION_TYPES

if TYPE_CHECKING:
    from src.infrastructure.llm_proxy.service import ClaudeProxyService

logger = logging.getLogger(__name__)

# JSON Schema for structured LLM output
_EXTRACTION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "mentioned_entities": {
            "type": "array",
            "description": "文章中明确提到的实体",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": sorted(CORE_ENTITY_TYPES),
                    },
                    "name": {"type": "string", "description": "实体标准名称"},
                    "code": {"type": "string", "description": "股票代码（stock类型必填）"},
                    "exchange": {
                        "type": "string",
                        "enum": ["SH", "SZ", "BJ", "HK", "US"],
                        "description": "交易所（stock类型必填）",
                    },
                    "fund_code": {"type": "string", "description": "基金代码（fund类型必填）"},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                },
                "required": ["type", "name"],
            },
        },
        "affected_entities": {
            "type": "array",
            "description": "预期受影响的实体",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": sorted(CORE_ENTITY_TYPES)},
                    "name": {"type": "string"},
                    "code": {"type": "string"},
                    "exchange": {"type": "string", "enum": ["SH", "SZ", "BJ", "HK", "US"]},
                    "fund_code": {"type": "string"},
                    "direction": {
                        "type": "string",
                        "enum": ["positive", "negative", "neutral"],
                        "description": "影响方向",
                    },
                    "reason": {"type": "string", "description": "影响原因（一句话）"},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                },
                "required": ["type", "name"],
            },
        },
    },
    "required": ["mentioned_entities", "affected_entities"],
}

_SYSTEM_PROMPT = """\
你是专业金融新闻实体抽取助手，擅长从中国A股、港股、基金市场相关新闻中提取结构化信息。

任务：从给定的金融新闻标题和正文中，识别并提取两类实体：
1. mentioned_entities：文章中明确提到的实体
2. affected_entities：预期会受到该新闻影响的实体（需判断影响方向）

实体类型说明：
- stock：股票，必须填写 exchange（SH/SZ/BJ/HK/US）和 code（股票代码）
- fund：基金，必须填写 fund_code
- industry：行业板块（如"新能源"、"半导体"）
- concept：概念主题（如"固态电池"、"AI算力"）
- institution：机构（政府部门、央行、证监会、交易所、券商等）
- policy：政策文件（如"双碳政策"、"新质生产力"）
- macro_indicator：宏观经济指标（如"CPI"、"PMI"、"社融"）
- commodity：大宗商品（如"原油"、"黄金"、"铜"）
- person：人物（政策制定者、公司高管等）
- region：地区（国家、省市等）

抽取原则：
- 只抽取有文本依据的实体，不凭空推断
- 股票必须提供 code 和 exchange（不确定时可省略，不要填错）
- confidence 为 0.6-1.0 之间的置信度
- affected_entities 中的 direction 根据业务逻辑判断（利好=positive/利空=negative/中性=neutral）
- 每类实体最多抽取 10 个，聚焦最重要的
"""


class FinancialNewsExtractionStrategy:
    name = "financial_news_extraction"
    version = "v1"

    def __init__(self, llm_service: "ClaudeProxyService | None" = None) -> None:
        self._llm = llm_service

    async def extract(self, item: TextExtractionInput) -> TextExtractionResult:
        payload = dict(item.metadata.get("payload") or {})

        # Deterministic: convert symbols and pre-existing entity hints
        mentioned = [_entity_to_extracted(entity) for entity in payload.get("mentioned_entities", [])]
        affected = [_entity_to_extracted(entity) for entity in payload.get("affected_entities", [])]
        mentioned.extend(_symbol_entities(payload.get("symbols", [])))
        mentioned.extend(_entity_to_extracted(entity) for entity in payload.get("entity_hints", []))

        # LLM enrichment when service is configured and there's text content
        if self._llm is not None and (item.title or item.text):
            try:
                llm_mentioned, llm_affected = await self._llm_extract(item)
                mentioned.extend(llm_mentioned)
                affected.extend(llm_affected)
            except Exception as exc:
                logger.warning("[news_extraction] LLM extraction failed, using deterministic only: %s", exc)

        return TextExtractionResult(
            mentioned_entities=_dedupe_extracted(mentioned),
            affected_entities=_dedupe_extracted(affected),
        )

    async def _llm_extract(
        self, item: TextExtractionInput
    ) -> tuple[list[ExtractedEntity], list[ExtractedEntity]]:
        from src.infrastructure.llm_proxy.service import ClaudeProxyRequest

        parts = []
        if item.title:
            parts.append(f"标题：{item.title}")
        if item.text:
            content = item.text[:2000]  # cap to avoid oversized prompts
            parts.append(f"正文：{content}")
        elif item.fields.get("content"):
            parts.append(f"正文：{item.fields['content'][:2000]}")
        elif item.fields.get("summary"):
            parts.append(f"摘要：{item.fields['summary'][:500]}")

        prompt = "\n".join(parts)
        request = ClaudeProxyRequest(
            prompt=prompt,
            system_prompt=_SYSTEM_PROMPT,
            json_schema=_EXTRACTION_JSON_SCHEMA,
            temperature=0.0,
            max_tokens=2048,
            metadata={"task": "financial_news_extraction", "source_id": item.source_id},
        )
        response = await self._llm.generate(request)
        structured = response.structured_output
        if not isinstance(structured, dict):
            structured = _try_parse_json(response.text)
        if not isinstance(structured, dict):
            logger.warning("[news_extraction] LLM response not parseable as JSON, source_id=%s", item.source_id)
            return [], []

        mentioned = [
            _entity_to_extracted(e)
            for e in structured.get("mentioned_entities", [])
            if isinstance(e, dict)
        ]
        affected = [
            _entity_to_extracted(e)
            for e in structured.get("affected_entities", [])
            if isinstance(e, dict)
        ]
        return mentioned, affected

    def validate_result(self, result: TextExtractionResult, item: TextExtractionInput) -> ValidationResult:
        del item
        invalid_entities = [
            entity.entity_type
            for entity in result.mentioned_entities + result.affected_entities
            if entity.entity_type not in CORE_ENTITY_TYPES
        ]
        invalid_relations = [
            relation.relation_type
            for relation in result.relations
            if relation.relation_type not in CORE_RELATION_TYPES
        ]
        if invalid_entities or invalid_relations:
            return ValidationResult.error(
                "financial extraction result contains unsupported types",
                details={
                    "invalid_entities": sorted(set(invalid_entities)),
                    "invalid_relations": sorted(set(invalid_relations)),
                },
            )
        return ValidationResult.success()


async def enrich_financial_text_payload(
    payload: dict[str, Any],
    *,
    source_id: str,
    source_type: str,
    pipeline: TextExtractionPipeline,
    strategy: FinancialNewsExtractionStrategy,
) -> dict[str, Any]:
    title = _optional_text(payload.get("title"))
    text = _optional_text(payload.get("text") or payload.get("content") or payload.get("summary"))
    if not title and not text:
        return payload
    item = TextExtractionInput(
        source_id=source_id,
        source_type=source_type,
        title=title,
        text=text,
        fields={
            name: value
            for name, value in {
                "summary": _optional_text(payload.get("summary")),
                "content": _optional_text(payload.get("content")),
            }.items()
            if value
        },
        metadata={"payload": payload},
    )
    result = await pipeline.extract(item, strategy)
    enriched = dict(payload)
    enriched["mentioned_entities"] = _merge_entities(
        payload.get("mentioned_entities", []),
        [_extracted_to_entity(entity) for entity in result.mentioned_entities],
    )
    enriched["affected_entities"] = _merge_entities(
        payload.get("affected_entities", []),
        [_extracted_to_entity(entity) for entity in result.affected_entities],
    )
    if result.warnings:
        enriched["_extraction_warnings"] = result.warnings
    return enriched


def _entity_to_extracted(entity: dict[str, Any]) -> ExtractedEntity:
    entity_type = str(entity.get("type") or entity.get("entity_type"))
    canonical_name = str(
        entity.get("name")
        or entity.get("canonical_name")
        or entity.get("code")
        or entity.get("fund_code")
        or entity.get("indicator_code")
        or entity.get("id")
        or entity_type
    )
    identifiers = {
        name: str(value)
        for name, value in entity.items()
        if name
        in {
            "exchange",
            "code",
            "fund_code",
            "taxonomy",
            "indicator_code",
            "document_id",
            "source_id",
            "event_id",
            "external_id",
        }
        and value is not None
    }
    spans = [
        EvidenceSpan(
            field_name=str(span.get("field_name") or span.get("field") or "text"),
            text=str(span["text"]),
            start=span.get("start"),
            end=span.get("end"),
        )
        for span in entity.get("evidence_spans", [])
        if span.get("text")
    ]
    return ExtractedEntity(
        entity_type=entity_type,
        canonical_name=canonical_name,
        identifiers=identifiers,
        aliases=[str(item) for item in entity.get("aliases", [])],
        confidence=float(entity.get("confidence", 0.85)),
        evidence_spans=spans,
        properties={name: value for name, value in entity.items() if name not in {"type", "entity_type"}},
    )


def _symbol_entities(symbols: Any) -> list[ExtractedEntity]:
    if not isinstance(symbols, list):
        return []
    entities: list[ExtractedEntity] = []
    for symbol in symbols:
        entity = _symbol_to_entity(symbol)
        if entity:
            entities.append(_entity_to_extracted(entity))
    return entities


def _symbol_to_entity(symbol: Any) -> dict[str, Any] | None:
    if isinstance(symbol, dict):
        code = symbol.get("code") or symbol.get("symbol")
        if not code:
            return None
        return {
            "type": "stock",
            "exchange": symbol.get("exchange") or symbol.get("market") or "CN",
            "code": str(code),
            "name": symbol.get("name") or str(code),
            "confidence": symbol.get("confidence", 0.9),
        }
    if not isinstance(symbol, str) or not symbol.strip():
        return None
    parts = symbol.strip().split(":")
    if len(parts) == 2:
        exchange, code = parts
    else:
        exchange, code = "CN", parts[0]
    return {"type": "stock", "exchange": exchange, "code": code, "name": code, "confidence": 0.9}


def _extracted_to_entity(entity: ExtractedEntity) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": entity.entity_type,
        "name": entity.canonical_name,
        "confidence": entity.confidence,
    }
    result.update(entity.identifiers)
    if entity.aliases:
        result["aliases"] = entity.aliases
    for name in ["direction", "reason"]:
        if name in entity.properties:
            result[name] = entity.properties[name]
    if entity.evidence_spans:
        result["evidence_spans"] = [
            {
                "field": span.field_name,
                "text": span.text,
                "start": span.start,
                "end": span.end,
            }
            for span in entity.evidence_spans
        ]
    return result


def _merge_entities(base: Any, extracted: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = [dict(item) for item in base if isinstance(item, dict)]
    seen = {_entity_key(item) for item in result}
    for entity in extracted:
        key = _entity_key(entity)
        if key in seen:
            continue
        seen.add(key)
        result.append(entity)
    return result


def _dedupe_extracted(entities: list[ExtractedEntity]) -> list[ExtractedEntity]:
    result: list[ExtractedEntity] = []
    seen: set[tuple[str, str, str, str]] = set()
    for entity in entities:
        key = (
            entity.entity_type,
            entity.identifiers.get("exchange", ""),
            entity.identifiers.get("code") or entity.identifiers.get("fund_code") or "",
            entity.canonical_name,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(entity)
    return result


def _entity_key(entity: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(entity.get("type") or entity.get("entity_type") or ""),
        str(entity.get("exchange") or ""),
        str(entity.get("code") or entity.get("fund_code") or entity.get("indicator_code") or ""),
        str(entity.get("name") or entity.get("canonical_name") or ""),
    )


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _try_parse_json(text: str) -> Any | None:
    """Best-effort JSON extraction from LLM response text."""
    import re
    candidate = (text or "").strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.I)
        candidate = re.sub(r"\s*```\s*$", "", candidate)
    try:
        return json.loads(candidate)
    except Exception:
        pass
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(candidate[start : end + 1])
        except Exception:
            pass
    return None
