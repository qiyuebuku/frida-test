"""Financial text extraction strategy with LLM enrichment."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from src.domain.knowledge.adapter import ValidationResult
from src.domain.knowledge.extraction import (
    CandidateFactEntity,
    CandidateFactEvent,
    CandidateFactPackage,
    CandidateFactRelation,
    EvidenceSpan,
    ExtractedEntity,
    LLMFactExtractionPort,
    LLMFactExtractionRequest,
    TextExtractionInput,
    TextExtractionPipeline,
    TextExtractionResult,
)
from src.domain.knowledge_adapters.financial.ontology import (
    CORE_ENTITY_TYPES,
    CORE_RELATION_TYPES,
    FINANCIAL_ADAPTER_SPEC,
)
from src.domain.knowledge_adapters.financial.normalization import normalize_entity_type
from src.domain.knowledge_adapters.financial.relation_normalization import (
    normalize_candidate_relation_type,
)
from src.domain.knowledge_adapters.financial.semantic_certainty import (
    SemanticCertaintyAssessment,
    assessment_from_metadata,
)

logger = logging.getLogger(__name__)

_EXTRACTION_MAX_TOKENS = 8192


@dataclass(frozen=True)
class CandidateValidationIssue:
    path: str
    code: str
    message: str
    obj: Any | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_message(self) -> str:
        return self.message

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "path": self.path,
            "code": self.code,
            "message": self.message,
        }
        if self.obj is not None:
            payload["object"] = self.obj
        if self.details:
            payload["details"] = self.details
        return payload

# JSON Schema for structured LLM output
_ENTITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": sorted(CORE_ENTITY_TYPES)},
        "name": {"type": "string"},
        "code": {"type": "string"},
        "exchange": {"type": "string", "enum": ["SH", "SZ", "BJ", "HK", "US"]},
        "fund_code": {"type": "string"},
        "taxonomy": {"type": "string"},
        "direction": {"type": "string", "enum": ["positive", "negative", "neutral"]},
        "reason": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "evidence_spans": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field_name": {"type": "string", "enum": ["title", "text", "summary", "content"]},
                    "text": {"type": "string"},
                    "start": {"type": "integer"},
                    "end": {"type": "integer"},
                },
                "required": ["field_name", "text"],
            },
        },
    },
    "required": ["type", "name", "evidence_spans"],
}

_EXTRACTION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "description": "候选实体，必须带 evidence_spans",
            "items": _ENTITY_SCHEMA,
        },
        "events": {
            "type": "array",
            "description": "候选事件",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "evidence_spans": _ENTITY_SCHEMA["properties"]["evidence_spans"],
                },
                "required": ["title", "evidence_spans"],
            },
        },
        "relations": {
            "type": "array",
            "description": "候选关系，必须带 evidence_spans",
            "items": {
                "type": "object",
                "properties": {
                    "relation_type": {"type": "string", "enum": sorted(CORE_RELATION_TYPES)},
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                    "direction": {"type": "string", "enum": ["positive", "negative", "neutral"]},
                    "reason": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "evidence_spans": _ENTITY_SCHEMA["properties"]["evidence_spans"],
                },
                "required": ["relation_type", "source", "target", "evidence_spans"],
            },
        },
        "uncertainties": {"type": "array", "items": {"type": "string"}},
        "rule_suggestions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["entities", "events", "relations", "uncertainties"],
}

_EXTRACTION_CACHE_SCHEMA_VERSION = "financial_news_extraction.v3.typed_relation_endpoints"

_SYSTEM_PROMPT = """\
你是专业金融新闻实体抽取助手，擅长从中国A股、港股、基金市场相关新闻中提取结构化信息。

任务：从给定的金融新闻标题和正文中，输出一个候选事实包：
1. entities：候选实体，必须有文本证据片段 evidence_spans
2. events：候选事件，必须有文本证据片段 evidence_spans
3. relations：候选关系，必须有 source、target、relation_type、evidence_spans
4. uncertainties：无法确定的歧义说明
5. rule_suggestions：可选的新规则建议，只作为候选建议，不代表事实

实体类型说明：
- stock：股票，必须填写 exchange（SH/SZ/BJ/HK/US）和 code（股票代码）
- fund：基金，必须填写 fund_code
- industry：正式行业分类或明确行业板块（如"半导体设备"、"证券"）
- concept：概念主题、产业链、供应链、策略和交易线索（如"固态电池"、"AI算力"、"新能源车产业链"、"高股息"）
- institution：机构（政府部门、央行、证监会、交易所、券商等）
- policy：具体政策文件、会议或监管表述（如"证监会并购重组审核规则"）；泛化主题不要标为 policy
- macro_indicator：宏观经济指标（如"CPI"、"PMI"、"社融"）
- commodity：大宗商品（如"原油"、"黄金"、"铜"）
- person：人物（政策制定者、公司高管等）
- region：地区（国家、省市等）

抽取原则：
- 只抽取有文本依据的实体，不凭空推断
- 所有 entities、events、relations 都必须带 evidence_spans；没有证据片段就不要输出
- 股票必须提供 code 和 exchange（不确定时可省略，不要填错）
- 名称包含“产业链/供应链/生态链”时优先标为 concept，不要标为 industry
- “并购重组/低利率/高股息/新质生产力”这类泛化主题优先标为 concept，不要标为 policy
- confidence 为 0.6-1.0 之间的置信度
- relations 中的 direction 根据业务逻辑判断（利好=positive/利空=negative/中性=neutral）
- 如果无法确定精确关系类型，优先使用 related_to，不要自造 ontology 之外的关系类型
- relations 的 source/target 必须精确等于本次输出的某个 entity.name、event.title 或当前新闻标题；否则先把该端点作为 entity/event 输出，仍不确定就不要输出该 relation
- belongs_to 只用于股票/概念/地区等分类归属，不表示人物任职、机构所在地或事件主题；人物与机构、机构与地区、事件与主题的弱关系使用 related_to 或 mentions
- event/policy 影响行业、资产、概念时使用 affects；股票/行业/概念受益于事件或政策时使用 benefits_from；不确定时用 related_to
- mentions 优先用于当前新闻事件/政策提及某实体，不要用于 person->policy、person->institution、institution->policy 这类弱关系
- affects 的 source 必须是 event 或 policy；如果 source 是 institution/person/concept 且只是“提到/发布/表态”，使用 related_to
- benefits_from/hurt_by 的 source 必须是 stock、industry、concept、fund 等受益或受损对象；不要用 event->stock 表达影响，应用 affects
- belongs_to 只能表达稳定分类归属；不要用它表达人物任职、机构所在地、事件属于某主题
- 候选实体最多抽取 10 个，聚焦最重要的
"""


class FinancialNewsExtractionStrategy:
    name = "financial_news_extraction"
    version = "v1"

    def __init__(
        self,
        llm_port: LLMFactExtractionPort | None = None,
        llm_model: str | None = None,
    ) -> None:
        self._llm = llm_port
        self._llm_model = llm_model

    async def extract(self, item: TextExtractionInput) -> TextExtractionResult:
        payload = dict(item.metadata.get("payload") or {})
        warnings: list[str] = []
        candidate_package: CandidateFactPackage | None = None

        # Deterministic: convert symbols and pre-existing entity hints
        mentioned = [_entity_to_extracted(entity) for entity in payload.get("mentioned_entities", [])]
        affected = [_entity_to_extracted(entity) for entity in payload.get("affected_entities", [])]
        mentioned.extend(_symbol_entities(payload.get("symbols", [])))
        mentioned.extend(_entity_to_extracted(entity) for entity in payload.get("entity_hints", []))

        assessment = assessment_from_metadata(item.metadata.get("semantic_certainty"))
        should_call_llm = (
            self._llm is not None
            and (item.title or item.text)
            and (assessment is None or assessment.decision == "llm_candidate")
        )
        if should_call_llm:
            try:
                candidate_package, llm_warnings = await self._llm_extract(item)
                warnings.extend(llm_warnings)
            except Exception as exc:
                logger.warning("[news_extraction] LLM extraction failed, using deterministic only: %s", exc)
                warnings.append(f"llm extraction failed: {exc}")

        return TextExtractionResult(
            mentioned_entities=_dedupe_extracted(mentioned),
            affected_entities=_dedupe_extracted(affected),
            candidate_package=candidate_package,
            warnings=warnings,
        )

    async def _llm_extract(
        self, item: TextExtractionInput
    ) -> tuple[CandidateFactPackage | None, list[str]]:
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
        payload = dict(item.metadata.get("payload") or {})
        weak_hints = _weak_hint_text(payload.get("weak_entity_hints"))
        if weak_hints:
            parts.append(f"来源侧弱标签，仅作参考，必须在标题或正文中找到证据才可抽取：{weak_hints}")

        prompt = "\n".join(parts)
        request = self._build_llm_request(item, prompt, use_cache=True)
        response = await self._llm.extract(request)
        package, warnings, retry_action, validation_issues = self._parse_llm_response(item, response)
        if retry_action == "self_repair":
            retry_reason = (
                "schema_invalid_after_cache_hit"
                if (getattr(response, "metadata", {}) or {}).get("cache_hit") is True
                else "quality_validation_failed_after_llm_output"
            )
            retry_request = self._build_llm_repair_request(
                item,
                prompt,
                response=response,
                validation_issues=validation_issues,
                retry_reason=retry_reason,
            )
            logger.info(
                "[news_extraction] retrying LLM extraction with validation feedback, source_id=%s reason=%s issues=%s",
                item.source_id,
                retry_reason,
                _issue_dicts(validation_issues[:8]),
            )
            retry_response = await self._llm.extract(retry_request)
            retry_package, retry_warnings, _, _ = self._parse_llm_response(
                item,
                retry_response,
                retry=True,
            )
            if retry_package is not None:
                if _candidate_package_is_fallback(retry_package):
                    return retry_package, [
                        *warnings,
                        "llm self-repair failed after validation feedback; fallback candidate package created",
                        *retry_warnings,
                    ]
                return retry_package, ["llm self-repair succeeded after validation feedback", *retry_warnings]
            fallback_package = _fallback_candidate_package_from_source(item, "llm self-repair failed")
            return fallback_package, [
                *warnings,
                "llm self-repair failed after validation feedback; fallback candidate package created",
                *retry_warnings,
            ]
        return package, warnings

    def _build_llm_request(
        self,
        item: TextExtractionInput,
        prompt: str,
        *,
        use_cache: bool,
        retry_reason: str | None = None,
        messages: list[dict[str, Any]] | None = None,
    ) -> LLMFactExtractionRequest:
        metadata = {
            "task": "financial_news_extraction",
            "source_id": item.source_id,
            "cache_schema_version": _EXTRACTION_CACHE_SCHEMA_VERSION,
        }
        if retry_reason:
            metadata["retry_reason"] = retry_reason
        return LLMFactExtractionRequest(
            task="financial_news_extraction",
            source_id=item.source_id,
            source_type=item.source_type,
            prompt=prompt,
            system_prompt=_SYSTEM_PROMPT,
            model=self._llm_model,
            json_schema=_EXTRACTION_JSON_SCHEMA,
            temperature=0.0,
            max_tokens=_EXTRACTION_MAX_TOKENS,
            metadata=metadata,
            messages=list(messages or []),
            use_cache=use_cache,
        )

    def _build_llm_repair_request(
        self,
        item: TextExtractionInput,
        prompt: str,
        *,
        response: Any,
        validation_issues: list[CandidateValidationIssue],
        retry_reason: str,
    ) -> LLMFactExtractionRequest:
        assistant_content = _llm_response_text_for_continuation(response)
        issues_payload = json.dumps(_issue_dicts(validation_issues[:12]), ensure_ascii=False, indent=2)
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": assistant_content[:12000]},
            {
                "role": "user",
                "content": (
                    "质量检验没有通过。请基于同一个新闻输入，针对下面的 validation_issues 修复输出。"
                    "每个问题都包含 path、code、message、object 和 details；请只修复这些问题相关的对象，"
                    "不要编造无文本依据的新事实。\n"
                    "修复要求：\n"
                    "- 最终只输出一个合法 JSON 对象，不要输出 Markdown、解释文字或代码块。\n"
                    "- 必须严格符合前面给出的 JSON Schema。\n"
                    "- 如果对象缺少 evidence_spans，就补充标题或正文中的原文片段；找不到证据就删除该对象。\n"
                    "- 如果 relation endpoint 缺失或类型不合法，就补齐端点实体/事件、改成 allowed_relation_types 中的关系，"
                    "或删除该 relation。\n"
                    f"validation_issues:\n{issues_payload}"
                ),
            },
        ]
        return self._build_llm_request(
            item,
            prompt,
            use_cache=True,
            retry_reason=retry_reason,
            messages=messages,
        )

    def _parse_llm_response(
        self,
        item: TextExtractionInput,
        response: Any,
        *,
        retry: bool = False,
    ) -> tuple[CandidateFactPackage | None, list[str], str | None, list[CandidateValidationIssue]]:
        structured = response.structured_output
        if not isinstance(structured, dict):
            structured = _try_parse_json(response.text)
        if not isinstance(structured, dict):
            warning = "llm response not parseable as JSON"
            diagnostics = _llm_response_diagnostics(getattr(response, "metadata", {}) or {})
            raw_preview = _llm_raw_text_preview(str(getattr(response, "text", "") or ""))
            issue = CandidateValidationIssue(
                path="$",
                code="json_not_parseable",
                message=warning,
                obj=_llm_response_text_for_continuation(response)[:12000],
                details={"diagnostics": diagnostics, "raw_preview": raw_preview},
            )
            logger.warning(
                "[news_extraction] %s, source_id=%s diagnostics=%s issues=%s raw_preview=%s",
                warning,
                item.source_id,
                diagnostics,
                _issue_dicts([issue]),
                raw_preview,
            )
            warnings = [warning, *diagnostics, f"validation_issues={_issue_dicts([issue])}", f"raw_preview={raw_preview}"]
            if _should_retry_with_feedback(response, retry=retry):
                return None, warnings, "self_repair", [issue]
            fallback_package = _fallback_candidate_package_from_source(item, warning)
            return fallback_package, [*warnings, "fallback candidate package created"], None, []

        _normalize_structured_defaults(structured)
        top_level_issues = _top_level_schema_issues(structured)
        if top_level_issues:
            warning = f"llm candidate package schema invalid: {len(top_level_issues)} issue(s)"
            raw_preview = _llm_raw_text_preview_for_response(response, structured)
            issue_messages = _issue_messages(top_level_issues)
            logger.warning(
                "[news_extraction] %s, source_id=%s cache_hit=%s retry=%s errors=%s issues=%s raw_preview=%s",
                warning,
                item.source_id,
                (getattr(response, "metadata", {}) or {}).get("cache_hit"),
                retry,
                issue_messages[:5],
                _issue_dicts(top_level_issues[:5]),
                raw_preview,
            )
            return (
                None,
                [warning, *issue_messages[:5], f"validation_issues={_issue_dicts(top_level_issues[:5])}", f"raw_preview={raw_preview}"],
                "self_repair" if _should_retry_with_feedback(response, retry=retry) else None,
                top_level_issues,
            )

        normalization_warnings = _normalize_structured_relation_types(structured)
        evidence_warnings = _repair_candidate_evidence_spans(structured, item)
        relation_warnings = _repair_candidate_relations(structured, item)
        if relation_warnings:
            logger.info(
                "[news_extraction] repaired candidate relation(s), source_id=%s retry=%s count=%s sample=%s",
                item.source_id,
                retry,
                len(relation_warnings),
                relation_warnings[:5],
            )
        schema_issues = _candidate_package_schema_issues(structured, item)
        if schema_issues:
            warning = f"llm candidate package schema invalid: {len(schema_issues)} issue(s)"
            raw_preview = _llm_raw_text_preview_for_response(response, structured)
            issue_messages = _issue_messages(schema_issues)
            logger.warning(
                "[news_extraction] %s, source_id=%s cache_hit=%s retry=%s errors=%s issues=%s raw_preview=%s",
                warning,
                item.source_id,
                (getattr(response, "metadata", {}) or {}).get("cache_hit"),
                retry,
                issue_messages[:5],
                _issue_dicts(schema_issues[:5]),
                raw_preview,
            )
            return (
                None,
                [warning, *issue_messages[:5], f"validation_issues={_issue_dicts(schema_issues[:5])}", f"raw_preview={raw_preview}"],
                "self_repair" if _should_retry_with_feedback(response, retry=retry) else None,
                schema_issues,
            )

        package = _candidate_package_from_structured(structured)
        warnings = [*normalization_warnings, *evidence_warnings, *relation_warnings]
        if not _candidate_package_has_facts(package):
            return None, warnings or ["llm candidate package empty after validation"], None, []
        return package, warnings, None, []

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
    semantic_assessment: SemanticCertaintyAssessment | None = None,
) -> dict[str, Any]:
    title = _optional_text(payload.get("title"))
    text = _optional_text(payload.get("text") or payload.get("content") or payload.get("summary"))
    has_entity_hints = any(payload.get(name) for name in ("symbols", "entity_hints", "mentioned_entities", "affected_entities"))
    if not title and not text and not has_entity_hints:
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
                "payload": "structured entity hints" if has_entity_hints else None,
            }.items()
            if value
        },
        metadata={
            "payload": payload,
            **({"semantic_certainty": semantic_assessment.model_dump()} if semantic_assessment else {}),
        },
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
    if result.candidate_package is not None:
        enriched["candidate_fact_package"] = result.candidate_package.model_dump(mode="json")
    if result.warnings:
        enriched["_extraction_warnings"] = result.warnings
    return enriched


def _entity_to_extracted(entity: dict[str, Any]) -> ExtractedEntity:
    entity_type = normalize_entity_type(entity.get("type") or entity.get("entity_type"))
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


def _weak_hint_text(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    hints: list[str] = []
    for item in value[:10]:
        if isinstance(item, dict):
            text = str(item.get("value") or item.get("name") or "").strip()
        else:
            text = str(item or "").strip()
        if text and text not in hints:
            hints.append(text)
    return "、".join(hints)


def _symbol_entities(symbols: Any) -> list[ExtractedEntity]:
    if not isinstance(symbols, list):
        return []
    entities: list[ExtractedEntity] = []
    for symbol in symbols:
        entity = _symbol_to_entity(symbol)
        if entity:
            entities.append(_entity_to_extracted(entity))
    return entities


def _candidate_package_from_structured(structured: dict[str, Any]) -> CandidateFactPackage:
    entities: list[CandidateFactEntity] = []
    for entity in structured.get("entities") or []:
        if isinstance(entity, dict) and _has_evidence_spans(entity):
            entities.append(_candidate_entity(entity))

    events: list[CandidateFactEvent] = []
    for event in structured.get("events") or []:
        if isinstance(event, dict) and _has_evidence_spans(event):
            events.append(
                CandidateFactEvent(
                    title=str(event["title"]),
                    summary=_optional_text(event.get("summary")),
                    confidence=float(event.get("confidence", 0.7)),
                    evidence_spans=_evidence_spans(event),
                    properties={name: value for name, value in event.items() if name not in {"title", "summary", "confidence", "evidence_spans"}},
                )
            )

    relations: list[CandidateFactRelation] = []
    for relation in structured.get("relations") or []:
        if isinstance(relation, dict) and _has_evidence_spans(relation):
            relations.append(
                CandidateFactRelation(
                    relation_type=str(relation["relation_type"]),
                    source=str(relation["source"]),
                    target=str(relation["target"]),
                    direction=_optional_text(relation.get("direction")),
                    reason=_optional_text(relation.get("reason")),
                    confidence=float(relation.get("confidence", 0.65)),
                    evidence_spans=_evidence_spans(relation),
                    properties={
                        name: value
                        for name, value in relation.items()
                        if name not in {"relation_type", "source", "target", "direction", "reason", "confidence", "evidence_spans"}
                    },
                )
            )

    return CandidateFactPackage(
        entities=entities,
        events=events,
        relations=relations,
        uncertainties=[str(item) for item in structured.get("uncertainties", []) if str(item).strip()],
        rule_suggestions=[str(item) for item in structured.get("rule_suggestions", []) if str(item).strip()],
    )


def _normalize_structured_relation_types(structured: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    for index, relation in enumerate(structured.get("relations") or []):
        if not isinstance(relation, dict):
            continue
        original = relation.get("relation_type")
        normalized, metadata = normalize_candidate_relation_type(
            original,
            direction=relation.get("direction"),
        )
        relation["relation_type"] = normalized
        if metadata.get("direction") and not relation.get("direction"):
            relation["direction"] = metadata["direction"]
        for key, value in metadata.items():
            if key != "direction":
                relation[key] = value
        if metadata.get("relation_type_normalized"):
            warnings.append(
                f"relations[{index}].relation_type normalized: {original} -> {normalized}"
            )
    return warnings


def _candidate_entity(entity: dict[str, Any]) -> CandidateFactEntity:
    extracted = _entity_to_extracted(entity)
    return CandidateFactEntity(
        entity_type=extracted.entity_type,
        canonical_name=extracted.canonical_name,
        identifiers=extracted.identifiers,
        aliases=extracted.aliases,
        confidence=extracted.confidence,
        evidence_spans=extracted.evidence_spans,
        properties=extracted.properties,
    )


def _llm_response_diagnostics(metadata: dict[str, Any]) -> list[str]:
    diagnostics: list[str] = []
    if metadata.get("cache_hit") is not None:
        diagnostics.append(f"llm cache_hit={metadata.get('cache_hit')}")
    proxy = metadata.get("proxy")
    if isinstance(proxy, dict):
        for key in (
            "provider",
            "resolved_model",
            "finish_reason",
            "json_repair_attempted",
            "json_repair_success",
            "json_repair_error",
        ):
            if key in proxy and proxy.get(key) is not None:
                diagnostics.append(f"llm {key}={proxy.get(key)}")
        repair = proxy.get("json_repair")
        if isinstance(repair, dict) and repair.get("finish_reason") is not None:
            diagnostics.append(f"llm json_repair_finish_reason={repair.get('finish_reason')}")
        retry = proxy.get("json_mode_retry")
        if isinstance(retry, dict) and retry.get("finish_reason") is not None:
            diagnostics.append(f"llm json_mode_retry_finish_reason={retry.get('finish_reason')}")
    return diagnostics


def _llm_raw_text_preview(text: str, *, edge_chars: int = 500) -> dict[str, Any]:
    value = str(text or "")
    if not value:
        return {"len": 0, "head": "", "tail": "", "likely_truncated": False}
    return {
        "len": len(value),
        "head": value[:edge_chars],
        "tail": value[-edge_chars:] if len(value) > edge_chars else "",
        "likely_truncated": not value.rstrip().endswith(("}", "]")),
    }


def _llm_raw_text_preview_for_response(
    response: Any,
    structured: dict[str, Any] | None = None,
    *,
    edge_chars: int = 500,
) -> dict[str, Any]:
    text = str(getattr(response, "text", "") or "")
    if not text and structured is not None:
        text = json.dumps(structured, ensure_ascii=False)
    return _llm_raw_text_preview(text, edge_chars=edge_chars)


def _llm_response_text_for_continuation(response: Any) -> str:
    text = str(getattr(response, "text", "") or "")
    if text.strip():
        return text
    structured = getattr(response, "structured_output", None)
    if structured is not None:
        return json.dumps(structured, ensure_ascii=False)
    return ""


def _should_retry_with_feedback(response: Any, *, retry: bool) -> bool:
    del response
    if retry:
        return False
    return True


def _normalize_structured_defaults(structured: dict[str, Any]) -> None:
    for field in ("entities", "events", "relations", "uncertainties"):
        if field not in structured:
            structured[field] = []
    if "rule_suggestions" not in structured:
        structured["rule_suggestions"] = []


def _top_level_schema_issues(structured: dict[str, Any]) -> list[CandidateValidationIssue]:
    issues: list[CandidateValidationIssue] = []
    for field in ("entities", "events", "relations", "uncertainties"):
        if not isinstance(structured.get(field), list):
            value = structured.get(field)
            issues.append(
                CandidateValidationIssue(
                    path=field,
                    code="field_type_invalid",
                    message=f"field must be list: {field}",
                    obj=value,
                    details={"field": field, "expected_type": "list", "actual_type": type(value).__name__},
                )
            )
    return issues


def _issue_messages(issues: list[CandidateValidationIssue]) -> list[str]:
    return [issue.to_message() for issue in issues]


def _issue_dicts(issues: list[CandidateValidationIssue]) -> list[dict[str, Any]]:
    return [issue.to_dict() for issue in issues]


def _fallback_candidate_package_from_source(
    item: TextExtractionInput,
    reason: str,
) -> CandidateFactPackage:
    title = (item.title or "").strip() or f"{item.source_type}:{item.source_id}"
    summary = (item.text or item.fields.get("summary") or item.fields.get("content") or "").strip()
    return CandidateFactPackage(
        entities=[],
        events=[
            CandidateFactEvent(
                title=title,
                summary=summary[:180] if summary else None,
                confidence=0.55,
                evidence_spans=_fallback_evidence_spans_for_source(item),
                properties={
                    "fallback_from_source": True,
                    "fallback_reason": reason,
                    "source_id": item.source_id,
                    "source_type": item.source_type,
                },
            )
        ],
        relations=[],
        uncertainties=[reason, "LLM structured extraction unavailable; fallback event created from source title"],
        rule_suggestions=[],
    )


def _fallback_evidence_spans_for_source(item: TextExtractionInput) -> list[EvidenceSpan]:
    if item.title:
        return [EvidenceSpan(field_name="title", text=item.title)]
    text = (item.text or item.fields.get("summary") or item.fields.get("content") or "").strip()
    if text:
        return [EvidenceSpan(field_name="text", text=text[:160])]
    return [EvidenceSpan(field_name="text", text=f"{item.source_type}:{item.source_id}")]


def _repair_candidate_evidence_spans(structured: dict[str, Any], item: TextExtractionInput) -> list[str]:
    warnings: list[str] = []
    for index, entity in enumerate(structured.get("entities") or []):
        if not isinstance(entity, dict) or _has_evidence_spans(entity):
            continue
        span = _evidence_span_for_exact_source_text([entity.get("name")], item)
        if span:
            entity["evidence_spans"] = [span]
            entity["evidence_spans_repaired"] = True
            warnings.append(f"entities[{index}].evidence_spans repaired")

    for index, event in enumerate(structured.get("events") or []):
        if not isinstance(event, dict) or _has_evidence_spans(event):
            continue
        span = _evidence_span_for_exact_source_text(
            [
                event.get("summary"),
                event.get("title"),
                *_event_title_span_candidates(str(event.get("title") or "")),
            ],
            item,
        )
        if span:
            event["evidence_spans"] = [span]
            event["evidence_spans_repaired"] = True
            warnings.append(f"events[{index}].evidence_spans repaired")
    return warnings


def _repair_candidate_relations(structured: dict[str, Any], item: TextExtractionInput) -> list[str]:
    relations = structured.get("relations")
    if not isinstance(relations, list):
        return []
    if not isinstance(structured.get("entities"), list):
        structured["entities"] = []
    endpoint_types = _candidate_endpoint_types(structured, item)
    warnings: list[str] = []

    for index, relation in enumerate(relations):
        if not isinstance(relation, dict):
            continue
        source_name = str(relation.get("source") or "").strip()
        target_name = str(relation.get("target") or "").strip()
        if not source_name or not target_name:
            continue

        if not _has_evidence_spans(relation):
            repaired_span = _fallback_evidence_span_for_relation(relation, item)
            if repaired_span:
                relation["evidence_spans"] = [repaired_span]
                relation["evidence_spans_repaired"] = True
                warnings.append(f"relations[{index}].evidence_spans repaired")

        for side, name in (("source", source_name), ("target", target_name)):
            if name in endpoint_types:
                continue
            fallback_entity = {
                "type": "concept",
                "name": name,
                "confidence": relation.get("confidence", 0.55),
                "evidence_spans": relation.get("evidence_spans") or _fallback_evidence_span_list(item),
                "properties": {
                    "candidate_relation_endpoint": True,
                    "endpoint_side": side,
                    "fallback_endpoint_type": "concept",
                },
            }
            structured["entities"].append(fallback_entity)
            endpoint_types[name] = "concept"
            warnings.append(f"relations[{index}].{side} endpoint synthesized as concept: {name}")

        relation_type = str(relation.get("relation_type") or "").strip()
        source_type = endpoint_types.get(source_name)
        target_type = endpoint_types.get(target_name)
        if not relation_type or not source_type or not target_type:
            continue
        if _relation_endpoint_allowed(relation_type, source_type, target_type):
            continue
        fallback_relation = _fallback_relation_type_for_endpoints(relation_type, source_type, target_type)
        if fallback_relation != relation_type:
            relation["original_relation_type"] = relation.get("original_relation_type") or relation_type
            relation["relation_type"] = fallback_relation
            relation["relation_type_normalized"] = True
            relation["relation_type_fallback"] = f"invalid_endpoint_to_{fallback_relation}"
            relation["original_source_type"] = source_type
            relation["original_target_type"] = target_type
            warnings.append(
                f"relations[{index}].relation_type repaired: {relation_type} "
                f"{source_type}->{target_type} -> {fallback_relation}"
            )
    return warnings


def _fallback_relation_type_for_endpoints(
    relation_type: str,
    source_type: str,
    target_type: str,
) -> str:
    if relation_type in {"benefits_from", "hurt_by"} and _relation_endpoint_allowed("affects", source_type, target_type):
        return "affects"
    if relation_type == "belongs_to" and _relation_endpoint_allowed("mentions", source_type, target_type):
        return "mentions"
    if _relation_endpoint_allowed("related_to", source_type, target_type):
        return "related_to"
    if _relation_endpoint_allowed("mentions", source_type, target_type):
        return "mentions"
    if _relation_endpoint_allowed("affects", source_type, target_type):
        return "affects"
    return relation_type


def _fallback_evidence_span_for_relation(
    relation: dict[str, Any],
    item: TextExtractionInput,
) -> dict[str, Any] | None:
    candidates = [
        str(relation.get("reason") or "").strip(),
        str(relation.get("source") or "").strip(),
        str(relation.get("target") or "").strip(),
    ]
    for field_name, text in (("title", item.title or ""), ("text", item.text or "")):
        for candidate in candidates:
            if not candidate:
                continue
            start = text.find(candidate)
            if start >= 0:
                return {"field_name": field_name, "text": candidate, "start": start, "end": start + len(candidate)}
    fallback = (item.title or item.text or "").strip()
    if not fallback:
        return None
    return {"field_name": "title" if item.title else "text", "text": fallback[:160]}


def _evidence_span_for_exact_source_text(candidates: list[Any], item: TextExtractionInput) -> dict[str, Any] | None:
    fields = [
        ("title", item.title or ""),
        ("text", item.text or ""),
        ("summary", item.fields.get("summary") or ""),
        ("content", item.fields.get("content") or ""),
    ]
    seen: set[str] = set()
    for raw_candidate in candidates:
        candidate = str(raw_candidate or "").strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        for field_name, text in fields:
            if not text:
                continue
            start = text.find(candidate)
            if start >= 0:
                return {
                    "field_name": field_name,
                    "text": candidate,
                    "start": start,
                    "end": start + len(candidate),
                }
    return None


def _event_title_span_candidates(title: str) -> list[str]:
    value = str(title or "").strip()
    if not value:
        return []
    suffixes = (
        "再创新高",
        "创新高",
        "市值突破万亿",
        "上涨",
        "下跌",
        "涨停",
        "跌停",
        "获批",
        "发布",
        "签约",
    )
    candidates: list[str] = []
    for suffix in suffixes:
        if value.endswith(suffix):
            prefix = value[: -len(suffix)].strip()
            if prefix:
                candidates.append(prefix)
            candidates.append(suffix)
    return candidates


def _fallback_evidence_span_list(item: TextExtractionInput) -> list[dict[str, Any]]:
    span = _fallback_evidence_span_for_relation({}, item)
    return [span] if span else []


def _candidate_package_has_facts(package: CandidateFactPackage) -> bool:
    return bool(package.entities or package.events or package.relations)


def _candidate_package_is_fallback(package: CandidateFactPackage) -> bool:
    return any(bool(event.properties.get("fallback_from_source")) for event in package.events)


def _candidate_package_schema_issues(
    structured: dict[str, Any],
    item: TextExtractionInput,
) -> list[CandidateValidationIssue]:
    issues: list[CandidateValidationIssue] = []
    for index, entity in enumerate(structured.get("entities") or []):
        path = f"entities[{index}]"
        if not isinstance(entity, dict):
            issues.append(
                CandidateValidationIssue(
                    path=path,
                    code="object_type_invalid",
                    message=f"{path} must be object",
                    obj=entity,
                    details={"expected_type": "object", "actual_type": type(entity).__name__},
                )
            )
            continue
        if entity.get("type"):
            entity["type"] = normalize_entity_type(entity.get("type"))
        _require_candidate_fields(issues, path, entity, ("type", "name", "evidence_spans"))
        if entity.get("type") not in CORE_ENTITY_TYPES:
            issues.append(
                CandidateValidationIssue(
                    path=f"{path}.type",
                    code="unsupported_entity_type",
                    message=f"{path}.type unsupported: {entity.get('type')}",
                    obj=entity,
                    details={
                        "value": entity.get("type"),
                        "allowed_types": sorted(CORE_ENTITY_TYPES),
                        "allowed_actions": ["rewrite_type", "drop_object"],
                    },
                )
            )

    for index, event in enumerate(structured.get("events") or []):
        path = f"events[{index}]"
        if not isinstance(event, dict):
            issues.append(
                CandidateValidationIssue(
                    path=path,
                    code="object_type_invalid",
                    message=f"{path} must be object",
                    obj=event,
                    details={"expected_type": "object", "actual_type": type(event).__name__},
                )
            )
            continue
        _require_candidate_fields(issues, path, event, ("title", "evidence_spans"))

    for index, relation in enumerate(structured.get("relations") or []):
        path = f"relations[{index}]"
        if not isinstance(relation, dict):
            issues.append(
                CandidateValidationIssue(
                    path=path,
                    code="object_type_invalid",
                    message=f"{path} must be object",
                    obj=relation,
                    details={"expected_type": "object", "actual_type": type(relation).__name__},
                )
            )
            continue
        _require_candidate_fields(
            issues,
            path,
            relation,
            ("relation_type", "source", "target", "evidence_spans"),
        )
        if relation.get("relation_type") not in CORE_RELATION_TYPES:
            issues.append(
                CandidateValidationIssue(
                    path=f"{path}.relation_type",
                    code="unsupported_relation_type",
                    message=f"{path}.relation_type unsupported: {relation.get('relation_type')}",
                    obj=relation,
                    details={
                        "value": relation.get("relation_type"),
                        "allowed_types": sorted(CORE_RELATION_TYPES),
                        "allowed_actions": ["rewrite_relation", "drop_relation"],
                    },
                )
            )
    issues.extend(_relation_endpoint_schema_issues(structured, item))

    return issues


def _candidate_package_schema_errors(structured: dict[str, Any], item: TextExtractionInput) -> list[str]:
    return _issue_messages(_candidate_package_schema_issues(structured, item))


def _relation_endpoint_schema_issues(
    structured: dict[str, Any],
    item: TextExtractionInput,
) -> list[CandidateValidationIssue]:
    endpoint_types = _candidate_endpoint_types(structured, item)
    issues: list[CandidateValidationIssue] = []
    relations = structured.get("relations")
    if not isinstance(relations, list):
        return issues
    for index, relation in enumerate(relations):
        if not isinstance(relation, dict):
            continue
        path = f"relations[{index}]"
        relation_type = str(relation.get("relation_type") or "").strip()
        source_name = str(relation.get("source") or "").strip()
        target_name = str(relation.get("target") or "").strip()
        if not relation_type or not source_name or not target_name:
            continue
        source_type = endpoint_types.get(source_name)
        target_type = endpoint_types.get(target_name)
        if source_type is None:
            issues.append(
                CandidateValidationIssue(
                    path=f"{path}.source",
                    code="endpoint_missing",
                    message=f"{path}.source endpoint missing from entities/events: {source_name}",
                    obj=relation,
                    details={
                        "endpoint_side": "source",
                        "endpoint": source_name,
                        "relation_type": relation_type,
                        "allowed_actions": ["add_endpoint_entity_or_event", "rewrite_endpoint", "drop_relation"],
                    },
                )
            )
            continue
        if target_type is None:
            issues.append(
                CandidateValidationIssue(
                    path=f"{path}.target",
                    code="endpoint_missing",
                    message=f"{path}.target endpoint missing from entities/events: {target_name}",
                    obj=relation,
                    details={
                        "endpoint_side": "target",
                        "endpoint": target_name,
                        "relation_type": relation_type,
                        "allowed_actions": ["add_endpoint_entity_or_event", "rewrite_endpoint", "drop_relation"],
                    },
                )
            )
            continue
        if not _relation_endpoint_allowed(relation_type, source_type, target_type):
            allowed_relation_types = _allowed_relation_types_for_endpoint(source_type, target_type)
            issues.append(
                CandidateValidationIssue(
                    path=path,
                    code="invalid_endpoint",
                    message=f"{path}.endpoint invalid for {relation_type}: {source_type}->{target_type}",
                    obj=relation,
                    details={
                        "relation_type": relation_type,
                        "source": source_name,
                        "target": target_name,
                        "source_type": source_type,
                        "target_type": target_type,
                        "allowed_relation_types": allowed_relation_types,
                        "allowed_actions": ["rewrite_relation", "reverse_relation", "drop_relation"],
                    },
                )
            )
    return issues


def _relation_endpoint_schema_errors(structured: dict[str, Any], item: TextExtractionInput) -> list[str]:
    return _issue_messages(_relation_endpoint_schema_issues(structured, item))


def _candidate_endpoint_types(structured: dict[str, Any], item: TextExtractionInput) -> dict[str, str]:
    source_node_type = "policy" if item.source_type == "policy_news" else "event"
    endpoint_types: dict[str, str] = {}
    document_title = str(item.title or "").strip()
    if document_title:
        endpoint_types[document_title] = source_node_type
    for entity in structured.get("entities") or []:
        if not isinstance(entity, dict):
            continue
        name = str(entity.get("name") or entity.get("canonical_name") or "").strip()
        entity_type = normalize_entity_type(entity.get("type") or entity.get("entity_type"))
        if name and entity_type in CORE_ENTITY_TYPES:
            endpoint_types[name] = entity_type
    for event in structured.get("events") or []:
        if not isinstance(event, dict):
            continue
        title = str(event.get("title") or "").strip()
        if title:
            endpoint_types[title] = source_node_type
    return endpoint_types


def _relation_endpoint_allowed(relation_type: str, source_type: str, target_type: str) -> bool:
    for relation in FINANCIAL_ADAPTER_SPEC.relations:
        if relation.name == relation_type:
            return source_type in relation.source_types and target_type in relation.target_types
    return False


def _allowed_relation_types_for_endpoint(source_type: str, target_type: str) -> list[str]:
    return sorted(
        relation.name
        for relation in FINANCIAL_ADAPTER_SPEC.relations
        if source_type in relation.source_types and target_type in relation.target_types
    )


def _require_candidate_fields(
    issues: list[CandidateValidationIssue],
    prefix: str,
    candidate: dict[str, Any],
    fields: tuple[str, ...],
) -> None:
    for field in fields:
        if field == "evidence_spans":
            if not _has_evidence_spans(candidate):
                issues.append(
                    CandidateValidationIssue(
                        path=f"{prefix}.evidence_spans",
                        code="missing_evidence_spans",
                        message=f"{prefix}.evidence_spans required",
                        obj=candidate,
                        details={
                            "field": "evidence_spans",
                            "allowed_actions": ["add_exact_source_evidence_span", "drop_object"],
                        },
                    )
                )
            continue
        if not str(candidate.get(field) or "").strip():
            issues.append(
                CandidateValidationIssue(
                    path=f"{prefix}.{field}",
                    code="missing_field",
                    message=f"{prefix}.{field} required",
                    obj=candidate,
                    details={"field": field, "allowed_actions": ["fill_field", "drop_object"]},
                )
            )


def _has_evidence_spans(value: dict[str, Any]) -> bool:
    spans = value.get("evidence_spans")
    return isinstance(spans, list) and any(isinstance(span, dict) and span.get("text") for span in spans)


def _evidence_spans(value: dict[str, Any]) -> list[EvidenceSpan]:
    return [
        EvidenceSpan(
            field_name=str(span.get("field_name") or span.get("field") or "text"),
            text=str(span["text"]),
            start=span.get("start"),
            end=span.get("end"),
        )
        for span in value.get("evidence_spans", [])
        if isinstance(span, dict) and span.get("text")
    ]


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
    if result.get("type") == "stock" and result.get("code") and not result.get("exchange"):
        result["exchange"] = _infer_stock_exchange(str(result["code"]))
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


def _infer_stock_exchange(code: str) -> str:
    normalized = str(code or "").strip().upper()
    if normalized.isdigit() and len(normalized) == 6:
        if normalized.startswith("6"):
            return "SH"
        if normalized.startswith(("0", "3")):
            return "SZ"
        if normalized.startswith(("4", "8")):
            return "BJ"
    if normalized.endswith(".HK") or normalized.isdigit() and len(normalized) <= 5:
        return "HK"
    return "US"


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
