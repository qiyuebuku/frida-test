"""Financial text extraction strategy with LLM enrichment."""

from __future__ import annotations

import copy
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
    CandidateFactSignal,
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
                    "chunk_id": {"type": "string"},
                    "evidence_id": {"type": "string"},
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
                    "relationship_strength": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "boundary_strength": {"type": "string", "enum": ["strong", "medium", "weak"]},
                    "support_role": {"type": "string", "enum": ["core", "context", "mention"]},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "evidence_spans": _ENTITY_SCHEMA["properties"]["evidence_spans"],
                },
                "required": ["relation_type", "source", "target", "evidence_spans"],
            },
        },
        "fact_signals": {
            "type": "array",
            "description": "面向 Graph Index 聚合的语义信号，不替代事实关系，必须有 evidence_spans",
            "items": {
                "type": "object",
                "properties": {
                    "signal_type": {"type": "string"},
                    "topic_tags": {"type": "array", "items": {"type": "string"}},
                    "impact_tags": {"type": "array", "items": {"type": "string"}},
                    "risk_tags": {"type": "array", "items": {"type": "string"}},
                    "narrative_tags": {"type": "array", "items": {"type": "string"}},
                    "event_type_tags": {"type": "array", "items": {"type": "string"}},
                    "policy_tags": {"type": "array", "items": {"type": "string"}},
                    "asset_tags": {"type": "array", "items": {"type": "string"}},
                    "industry_tags": {"type": "array", "items": {"type": "string"}},
                    "governance_tags": {"type": "array", "items": {"type": "string"}},
                    "target_tags": {"type": "array", "items": {"type": "string"}},
                    "domain_tags": {"type": "array", "items": {"type": "string"}},
                    "affected_entities": {"type": "array", "items": {"type": "string"}},
                    "affected_assets": {"type": "array", "items": {"type": "string"}},
                    "affected_industries": {"type": "array", "items": {"type": "string"}},
                    "affected_targets": {"type": "array", "items": {"type": "string"}},
                    "affected_domains": {"type": "array", "items": {"type": "string"}},
                    "impact_direction": {"type": "string", "enum": ["positive", "negative", "neutral", "mixed"]},
                    "impact_mechanism": {"type": "string"},
                    "risk_type": {"type": "string"},
                    "catalyst_type": {"type": "string"},
                    "support_role": {"type": "string", "enum": ["core", "context", "mention"]},
                    "boundary_strength": {"type": "string", "enum": ["strong", "medium", "weak"]},
                    "sentiment": {"type": "string", "enum": ["positive", "negative", "neutral", "mixed"]},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "evidence_spans": _ENTITY_SCHEMA["properties"]["evidence_spans"],
                },
                "required": ["signal_type", "evidence_spans"],
            },
        },
        "uncertainties": {"type": "array", "items": {"type": "string"}},
        "rule_suggestions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["entities", "events", "relations", "fact_signals", "uncertainties"],
}


def _extraction_json_schema(
    *,
    allowed_entity_types: set[str],
    allowed_relation_types: set[str],
) -> dict[str, Any]:
    schema = copy.deepcopy(_EXTRACTION_JSON_SCHEMA)
    schema["properties"]["entities"]["items"]["properties"]["type"]["enum"] = sorted(allowed_entity_types)
    schema["properties"]["relations"]["items"]["properties"]["relation_type"]["enum"] = sorted(allowed_relation_types)
    return schema

_EXTRACTION_CACHE_SCHEMA_VERSION = "financial_news_extraction.v3.typed_relation_endpoints"

_SYSTEM_PROMPT = """\
你是专业金融新闻实体抽取助手，擅长从中国A股、港股、基金市场相关新闻中提取结构化信息。

任务：从给定的金融新闻标题和正文中，输出一个候选事实包：
1. entities：候选实体，必须有文本证据片段 evidence_spans
2. events：候选事件，必须有文本证据片段 evidence_spans
3. relations：候选关系，必须有 source、target、relation_type、evidence_spans
4. fact_signals：面向社区聚合的语义信号，必须有 evidence_spans
5. uncertainties：无法确定的歧义说明
6. rule_suggestions：可选的新规则建议，只作为候选建议，不代表事实

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
- 如果用户输入中提供了“证据分片索引”，evidence_spans 应优先填写对应 chunk_id；不要自造 chunk_id
- 股票必须提供 code 和 exchange（不确定时可省略，不要填错）
- 名称包含“产业链/供应链/生态链”时优先标为 concept，不要标为 industry
- “并购重组/低利率/高股息/新质生产力”这类泛化主题优先标为 concept，不要标为 policy
- confidence 为 0.6-1.0 之间的置信度
- relations 中的 direction 根据业务逻辑判断（利好=positive/利空=negative/中性=neutral）
- relations 中的 relationship_strength 表示 source 与 target 的业务相关性强弱，0.0-1.0；弱提及不要给高分
- relations 中的 boundary_strength 表示该关系能否定义社区边界：strong/medium/weak
- relations 中的 support_role 表示该关系在当前文本中的证据角色：core/context/mention
- 如果无法确定精确关系类型，优先使用 related_to，不要自造 ontology 之外的关系类型
- relations 的 source/target 必须精确等于本次输出的某个 entity.name、event.title 或当前新闻标题；否则先把该端点作为 entity/event 输出，仍不确定就不要输出该 relation
- belongs_to 只用于股票/概念/地区等分类归属，不表示人物任职、机构所在地或事件主题；人物与机构、机构与地区、事件与主题的弱关系使用 related_to 或 mentions
- event/policy 影响行业、资产、概念时使用 affects；股票/行业/概念受益于事件或政策时使用 benefits_from；不确定时用 related_to
- mentions 优先用于当前新闻事件/政策提及某实体，不要用于 person->policy、person->institution、institution->policy 这类弱关系
- affects 的 source 必须是 event 或 policy；如果 source 是 institution/person/concept 且只是“提到/发布/表态”，使用 related_to
- benefits_from/hurt_by 的 source 必须是 stock、industry、concept、fund 等受益或受损对象；不要用 event->stock 表达影响，应用 affects
- belongs_to 只能表达稳定分类归属；不要用它表达人物任职、机构所在地、事件属于某主题
- 候选实体最多抽取 10 个，聚焦最重要的

Graph Index 信号要求：
- fact_signals 用来帮助后续图算法聚合 community，不是单独事实，不要编造没有证据的主题
- topic_tags 写可复用主题短语，如“并购重组政策窗口”“AI算力链”“新能源产能出海”
- event_type_tags 写事件类型短语，如“政策窗口”“供应链短缺”“产业并购”“监管约束”“产能扩张”
- impact_tags 写影响机制，如“产业链整合”“估值重估”“研发能力整合”“融资环境改善”
- risk_tags 只写真实风险线索，如“商誉减值”“整合不及预期”“监管约束”“需求放缓”
- narrative_tags 写市场叙事线索，如“硬科技资产重估”“价值投资回归”
- affected_* 写直接受影响对象，优先使用本次 entities/events 中的名称
- impact_direction/sentiment 必须基于文本判断，不确定用 neutral 或 mixed
- support_role=core 表示该 signal 是当前文本核心事实，context 表示背景，mention 表示只是提及
- boundary_strength=strong/medium/weak；只有强因果、强影响、明确风险或明确政策传导才写 strong，泛化行业共同出现只能写 weak
"""


def _system_prompt_with_type_registry(base_prompt: str, registry: list[dict[str, Any]]) -> str:
    if not registry:
        return base_prompt
    lines = [
        "",
        "Active type registry（可用的扩展类型，必须按定义谨慎使用）：",
    ]
    for item in registry[:80]:
        if not isinstance(item, dict):
            continue
        type_kind = str(item.get("type_kind") or item.get("rule_type") or "").strip()
        type_name = str(item.get("type_name") or item.get("raw_value") or "").strip()
        definition = str(item.get("definition") or item.get("canonical_value") or "").strip()
        if not type_kind or not type_name:
            continue
        suffix = f"：{definition}" if definition else ""
        lines.append(f"- {type_kind}: {type_name}{suffix}")
    if len(lines) == 2:
        return base_prompt
    return f"{base_prompt}\n" + "\n".join(lines)


class FinancialNewsExtractionStrategy:
    name = "financial_news_extraction"
    version = "v1"

    def __init__(
        self,
        llm_port: LLMFactExtractionPort | None = None,
        llm_model: str | None = None,
        allowed_entity_types: set[str] | None = None,
        allowed_relation_types: set[str] | None = None,
        active_type_registry: list[dict[str, Any]] | None = None,
    ) -> None:
        self._llm = llm_port
        self._llm_model = llm_model
        self._allowed_entity_types = set(CORE_ENTITY_TYPES) | set(allowed_entity_types or set())
        self._allowed_relation_types = set(CORE_RELATION_TYPES) | set(allowed_relation_types or set())
        self._active_type_registry = list(active_type_registry or [])
        self._json_schema = _extraction_json_schema(
            allowed_entity_types=self._allowed_entity_types,
            allowed_relation_types=self._allowed_relation_types,
        )
        self._system_prompt = _system_prompt_with_type_registry(_SYSTEM_PROMPT, self._active_type_registry)

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
        if payload.get("chunk_first_extraction"):
            parts.append(
                "当前正文是单个 evidence chunk，不是整篇文章。"
                "只抽取本 chunk 明确支持或可从本 chunk 合理推断的实体、事件和关系；"
                "不要根据其他 chunk 或整篇标题扩展无文本依据的事实。"
            )
        chunk_hints = _chunk_hint_text(payload.get("evidence_chunk_hints"))
        if chunk_hints:
            parts.append(
                "证据分片索引，用于定位 evidence_spans。抽取关系时优先引用能支持该关系的分片文本；"
                "如果证据来自某个分片，evidence_spans 必须填写该分片的 chunk_id；"
                "不要因为分片存在就抽取没有文本依据的事实，不要自造 chunk_id。\n"
                f"{chunk_hints}"
            )

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
            system_prompt=self._system_prompt,
            model=self._llm_model,
            json_schema=self._json_schema,
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
            {"role": "system", "content": self._system_prompt},
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

        normalization_warnings = _normalize_structured_relation_types(
            structured,
            allowed_relation_types=self._allowed_relation_types,
        )
        evidence_warnings = _repair_candidate_evidence_spans(structured, item)
        relation_warnings = _repair_candidate_relations(
            structured,
            item,
            allowed_entity_types=self._allowed_entity_types,
            allowed_relation_types=self._allowed_relation_types,
        )
        if relation_warnings:
            logger.info(
                "[news_extraction] repaired candidate relation(s), source_id=%s retry=%s count=%s sample=%s",
                item.source_id,
                retry,
                len(relation_warnings),
                relation_warnings[:5],
            )
        schema_issues = _candidate_package_schema_issues(
            structured,
            item,
            allowed_entity_types=self._allowed_entity_types,
            allowed_relation_types=self._allowed_relation_types,
        )
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
            if entity.entity_type not in self._allowed_entity_types
        ]
        invalid_relations = [
            relation.relation_type
            for relation in result.relations
            if relation.relation_type not in self._allowed_relation_types
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
    chunk_items = _chunk_first_items(
        source_id=source_id,
        source_type=source_type,
        title=title,
        payload=payload,
        semantic_assessment=semantic_assessment,
    )
    if chunk_items:
        result = await _extract_chunk_first(chunk_items, pipeline=pipeline, strategy=strategy)
    else:
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


def _chunk_first_items(
    *,
    source_id: str,
    source_type: str,
    title: str | None,
    payload: dict[str, Any],
    semantic_assessment: SemanticCertaintyAssessment | None,
) -> list[TextExtractionInput]:
    chunk_hints = payload.get("evidence_chunk_hints")
    if not isinstance(chunk_hints, list):
        return []
    items: list[TextExtractionInput] = []
    for hint in chunk_hints:
        if not isinstance(hint, dict):
            continue
        chunk_id = _optional_text(hint.get("chunk_id"))
        chunk_text = _optional_text(hint.get("text"))
        if not chunk_id or not chunk_text:
            continue
        chunk_payload = {
            **payload,
            "text": chunk_text,
            "content": chunk_text,
            "chunk_first_extraction": True,
            "evidence_chunk_hints": [hint],
        }
        items.append(
            TextExtractionInput(
                source_id=source_id,
                source_type=source_type,
                title=title,
                text=chunk_text,
                fields={
                    "content": chunk_text,
                    "chunk_id": chunk_id,
                    **(
                        {"summary": _optional_text(payload.get("summary"))}
                        if _optional_text(payload.get("summary"))
                        else {}
                    ),
                },
                metadata={
                    "payload": chunk_payload,
                    "chunk_id": chunk_id,
                    **({"semantic_certainty": semantic_assessment.model_dump()} if semantic_assessment else {}),
                },
            )
        )
    return items


async def _extract_chunk_first(
    items: list[TextExtractionInput],
    *,
    pipeline: TextExtractionPipeline,
    strategy: FinancialNewsExtractionStrategy,
) -> TextExtractionResult:
    results = [await pipeline.extract(item, strategy) for item in items]
    return TextExtractionResult(
        mentioned_entities=_dedupe_extracted(
            [entity for result in results for entity in result.mentioned_entities]
        ),
        affected_entities=_dedupe_extracted(
            [entity for result in results for entity in result.affected_entities]
        ),
        candidate_package=_merge_candidate_packages(
            [result.candidate_package for result in results if result.candidate_package is not None]
        ),
        warnings=[
            f"chunk-first extraction used chunks={len(items)}",
            *(warning for result in results for warning in result.warnings),
        ],
    )


def _merge_candidate_packages(packages: list[CandidateFactPackage]) -> CandidateFactPackage | None:
    entities: list[CandidateFactEntity] = []
    events: list[CandidateFactEvent] = []
    relations: list[CandidateFactRelation] = []
    fact_signals: list[CandidateFactSignal] = []
    uncertainties: list[str] = []
    rule_suggestions: list[str] = []
    seen_entities: set[str] = set()
    seen_events: set[str] = set()
    seen_relations: set[str] = set()
    seen_signals: set[str] = set()
    for package in packages:
        for entity in package.entities:
            key = _package_item_key(entity.model_dump(mode="json"))
            if key not in seen_entities:
                seen_entities.add(key)
                entities.append(entity)
        for event in package.events:
            key = _package_item_key(event.model_dump(mode="json"))
            if key not in seen_events:
                seen_events.add(key)
                events.append(event)
        for relation in package.relations:
            key = _package_item_key(relation.model_dump(mode="json"))
            if key not in seen_relations:
                seen_relations.add(key)
                relations.append(relation)
        for signal in package.fact_signals:
            key = _package_item_key(signal.model_dump(mode="json"))
            if key not in seen_signals:
                seen_signals.add(key)
                fact_signals.append(signal)
        uncertainties.extend(package.uncertainties)
        rule_suggestions.extend(package.rule_suggestions)
    if not entities and not events and not relations and not fact_signals:
        return None
    return CandidateFactPackage(
        entities=entities,
        events=events,
        relations=relations,
        fact_signals=fact_signals,
        uncertainties=_ordered_unique_texts(uncertainties),
        rule_suggestions=_ordered_unique_texts(rule_suggestions),
    )


def _package_item_key(item: dict[str, Any]) -> str:
    item = dict(item)
    item.pop("confidence", None)
    return json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)


def _ordered_unique_texts(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


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
            chunk_id=_optional_text(span.get("chunk_id")),
            evidence_id=_optional_text(span.get("evidence_id")),
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


def _chunk_hint_text(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    lines: list[str] = []
    for item in value[:8]:
        if not isinstance(item, dict):
            continue
        chunk_id = str(item.get("chunk_id") or "").strip()
        text = str(item.get("text") or "").strip()
        if not chunk_id or not text:
            continue
        offsets = ""
        if item.get("start_offset") is not None and item.get("end_offset") is not None:
            offsets = f" offsets={item.get('start_offset')}-{item.get('end_offset')}"
        lines.append(f"- chunk_id={chunk_id}{offsets}: {text[:360]}")
    return "\n".join(lines)


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

    fact_signals: list[CandidateFactSignal] = []
    for signal in structured.get("fact_signals") or []:
        if isinstance(signal, dict) and _has_evidence_spans(signal):
            fact_signals.append(_candidate_fact_signal(signal))

    return CandidateFactPackage(
        entities=entities,
        events=events,
        relations=relations,
        fact_signals=fact_signals,
        uncertainties=[str(item) for item in structured.get("uncertainties", []) if str(item).strip()],
        rule_suggestions=[str(item) for item in structured.get("rule_suggestions", []) if str(item).strip()],
    )


def _normalize_structured_relation_types(
    structured: dict[str, Any],
    *,
    allowed_relation_types: set[str] | None = None,
) -> list[str]:
    warnings: list[str] = []
    for index, relation in enumerate(structured.get("relations") or []):
        if not isinstance(relation, dict):
            continue
        original = relation.get("relation_type")
        normalized, metadata = normalize_candidate_relation_type(
            original,
            direction=relation.get("direction"),
            allowed_relation_types=allowed_relation_types,
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


def _candidate_fact_signal(signal: dict[str, Any]) -> CandidateFactSignal:
    excluded = {
        "signal_type",
        "topic_tags",
        "impact_tags",
        "risk_tags",
        "narrative_tags",
        "event_type_tags",
        "policy_tags",
        "asset_tags",
        "industry_tags",
        "governance_tags",
        "target_tags",
        "domain_tags",
        "affected_entities",
        "affected_assets",
        "affected_industries",
        "affected_targets",
        "affected_domains",
        "impact_direction",
        "impact_mechanism",
        "risk_type",
        "catalyst_type",
        "support_role",
        "boundary_strength",
        "sentiment",
        "confidence",
        "evidence_spans",
    }
    return CandidateFactSignal(
        signal_type=str(signal["signal_type"]),
        topic_tags=_string_list(signal.get("topic_tags")),
        impact_tags=_string_list(signal.get("impact_tags")),
        risk_tags=_string_list(signal.get("risk_tags")),
        narrative_tags=_string_list(signal.get("narrative_tags")),
        event_type_tags=_string_list(signal.get("event_type_tags")),
        governance_tags=_string_list(signal.get("governance_tags")) or _string_list(signal.get("policy_tags")),
        target_tags=_string_list(signal.get("target_tags")) or _string_list(signal.get("asset_tags")),
        domain_tags=_string_list(signal.get("domain_tags")) or _string_list(signal.get("industry_tags")),
        affected_entities=_string_list(signal.get("affected_entities")),
        affected_targets=_string_list(signal.get("affected_targets")) or _string_list(signal.get("affected_assets")),
        affected_domains=_string_list(signal.get("affected_domains")) or _string_list(signal.get("affected_industries")),
        impact_direction=_optional_text(signal.get("impact_direction")),
        impact_mechanism=_optional_text(signal.get("impact_mechanism")),
        risk_type=_optional_text(signal.get("risk_type")),
        catalyst_type=_optional_text(signal.get("catalyst_type")),
        support_role=_optional_text(signal.get("support_role")),
        boundary_strength=_optional_text(signal.get("boundary_strength")),
        sentiment=_optional_text(signal.get("sentiment")),
        confidence=float(signal.get("confidence", 0.7)),
        evidence_spans=_evidence_spans(signal),
        properties={name: value for name, value in signal.items() if name not in excluded},
    )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


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
        continuation = proxy.get("json_prefix_continuation")
        if isinstance(continuation, dict) and continuation.get("finish_reason") is not None:
            diagnostics.append(
                "llm json_prefix_continuation_finish_reason="
                f"{continuation.get('finish_reason')}"
            )
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
    for field in ("entities", "events", "relations", "fact_signals", "uncertainties"):
        if field not in structured:
            structured[field] = []
    if "rule_suggestions" not in structured:
        structured["rule_suggestions"] = []


def _top_level_schema_issues(structured: dict[str, Any]) -> list[CandidateValidationIssue]:
    issues: list[CandidateValidationIssue] = []
    for field in ("entities", "events", "relations", "fact_signals", "uncertainties"):
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


def _repair_candidate_relations(
    structured: dict[str, Any],
    item: TextExtractionInput,
    *,
    allowed_entity_types: set[str] | None = None,
    allowed_relation_types: set[str] | None = None,
) -> list[str]:
    relations = structured.get("relations")
    if not isinstance(relations, list):
        return []
    if not isinstance(structured.get("entities"), list):
        structured["entities"] = []
    allowed_entity_types = allowed_entity_types or CORE_ENTITY_TYPES
    allowed_relation_types = allowed_relation_types or CORE_RELATION_TYPES
    endpoint_types = _candidate_endpoint_types(structured, item, allowed_entity_types=allowed_entity_types)
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
        if _relation_endpoint_allowed(
            relation_type,
            source_type,
            target_type,
            allowed_relation_types=allowed_relation_types,
        ):
            continue
        fallback_relation = _fallback_relation_type_for_endpoints(
            relation_type,
            source_type,
            target_type,
            allowed_relation_types=allowed_relation_types,
        )
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
    *,
    allowed_relation_types: set[str] | None = None,
) -> str:
    if relation_type in {"benefits_from", "hurt_by"} and _relation_endpoint_allowed(
        "affects",
        source_type,
        target_type,
        allowed_relation_types=allowed_relation_types,
    ):
        return "affects"
    if relation_type == "belongs_to" and _relation_endpoint_allowed(
        "mentions",
        source_type,
        target_type,
        allowed_relation_types=allowed_relation_types,
    ):
        return "mentions"
    if _relation_endpoint_allowed(
        "related_to",
        source_type,
        target_type,
        allowed_relation_types=allowed_relation_types,
    ):
        return "related_to"
    if _relation_endpoint_allowed(
        "mentions",
        source_type,
        target_type,
        allowed_relation_types=allowed_relation_types,
    ):
        return "mentions"
    if _relation_endpoint_allowed(
        "affects",
        source_type,
        target_type,
        allowed_relation_types=allowed_relation_types,
    ):
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
    *,
    allowed_entity_types: set[str] | None = None,
    allowed_relation_types: set[str] | None = None,
) -> list[CandidateValidationIssue]:
    allowed_entity_types = allowed_entity_types or CORE_ENTITY_TYPES
    allowed_relation_types = allowed_relation_types or CORE_RELATION_TYPES
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
        if entity.get("type") not in allowed_entity_types:
            issues.append(
                CandidateValidationIssue(
                    path=f"{path}.type",
                    code="unsupported_entity_type",
                    message=f"{path}.type unsupported: {entity.get('type')}",
                    obj=entity,
                    details={
                        "value": entity.get("type"),
                        "allowed_types": sorted(allowed_entity_types),
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
        if relation.get("relation_type") not in allowed_relation_types:
            issues.append(
                CandidateValidationIssue(
                    path=f"{path}.relation_type",
                    code="unsupported_relation_type",
                    message=f"{path}.relation_type unsupported: {relation.get('relation_type')}",
                    obj=relation,
                    details={
                        "value": relation.get("relation_type"),
                        "allowed_types": sorted(allowed_relation_types),
                        "allowed_actions": ["rewrite_relation", "drop_relation"],
                    },
                )
            )

    for index, signal in enumerate(structured.get("fact_signals") or []):
        path = f"fact_signals[{index}]"
        if not isinstance(signal, dict):
            issues.append(
                CandidateValidationIssue(
                    path=path,
                    code="object_type_invalid",
                    message=f"{path} must be object",
                    obj=signal,
                    details={"expected_type": "object", "actual_type": type(signal).__name__},
                )
            )
            continue
        _require_candidate_fields(issues, path, signal, ("signal_type", "evidence_spans"))
    issues.extend(
        _relation_endpoint_schema_issues(
            structured,
            item,
            allowed_entity_types=allowed_entity_types,
            allowed_relation_types=allowed_relation_types,
        )
    )

    return issues


def _candidate_package_schema_errors(structured: dict[str, Any], item: TextExtractionInput) -> list[str]:
    return _issue_messages(_candidate_package_schema_issues(structured, item))


def _relation_endpoint_schema_issues(
    structured: dict[str, Any],
    item: TextExtractionInput,
    *,
    allowed_entity_types: set[str] | None = None,
    allowed_relation_types: set[str] | None = None,
) -> list[CandidateValidationIssue]:
    allowed_entity_types = allowed_entity_types or CORE_ENTITY_TYPES
    allowed_relation_types = allowed_relation_types or CORE_RELATION_TYPES
    endpoint_types = _candidate_endpoint_types(structured, item, allowed_entity_types=allowed_entity_types)
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
        if not _relation_endpoint_allowed(
            relation_type,
            source_type,
            target_type,
            allowed_relation_types=allowed_relation_types,
        ):
            endpoint_allowed_relation_types = _allowed_relation_types_for_endpoint(
                source_type,
                target_type,
                allowed_relation_types=allowed_relation_types,
            )
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
                        "allowed_relation_types": endpoint_allowed_relation_types,
                        "allowed_actions": ["rewrite_relation", "reverse_relation", "drop_relation"],
                    },
                )
            )
    return issues


def _relation_endpoint_schema_errors(structured: dict[str, Any], item: TextExtractionInput) -> list[str]:
    return _issue_messages(_relation_endpoint_schema_issues(structured, item))


def _candidate_endpoint_types(
    structured: dict[str, Any],
    item: TextExtractionInput,
    *,
    allowed_entity_types: set[str] | None = None,
) -> dict[str, str]:
    allowed_entity_types = allowed_entity_types or CORE_ENTITY_TYPES
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
        if name and entity_type in allowed_entity_types:
            endpoint_types[name] = entity_type
    for event in structured.get("events") or []:
        if not isinstance(event, dict):
            continue
        title = str(event.get("title") or "").strip()
        if title:
            endpoint_types[title] = source_node_type
    return endpoint_types


def _relation_endpoint_allowed(
    relation_type: str,
    source_type: str,
    target_type: str,
    *,
    allowed_relation_types: set[str] | None = None,
) -> bool:
    if allowed_relation_types is not None and relation_type not in allowed_relation_types:
        return False
    for relation in FINANCIAL_ADAPTER_SPEC.relations:
        if relation.name == relation_type:
            return source_type in relation.source_types and target_type in relation.target_types
    if allowed_relation_types is not None and relation_type in allowed_relation_types:
        return True
    return False


def _allowed_relation_types_for_endpoint(
    source_type: str,
    target_type: str,
    *,
    allowed_relation_types: set[str] | None = None,
) -> list[str]:
    result = {
        relation.name
        for relation in FINANCIAL_ADAPTER_SPEC.relations
        if source_type in relation.source_types and target_type in relation.target_types
    }
    if allowed_relation_types is not None:
        result.update(allowed_relation_types - CORE_RELATION_TYPES)
    return sorted(result)


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
            chunk_id=_optional_text(span.get("chunk_id")),
            evidence_id=_optional_text(span.get("evidence_id")),
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
        result["evidence_spans"] = [_span_payload(span) for span in entity.evidence_spans]
    return result


def _span_payload(span: EvidenceSpan) -> dict[str, Any]:
    return {
        name: value
        for name, value in {
            "field": span.field_name,
            "text": span.text,
            "start": span.start,
            "end": span.end,
            "chunk_id": span.chunk_id,
            "evidence_id": span.evidence_id,
        }.items()
        if value is not None
    }


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
