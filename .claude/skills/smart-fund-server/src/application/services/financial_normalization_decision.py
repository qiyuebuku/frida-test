"""Write-time financial entity normalization decisions."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import TYPE_CHECKING, Any

from src.domain.knowledge.ids import stable_hash
from src.domain.knowledge_adapters.financial.normalization import (
    NormalizationRules,
    normalize_entity_with_rules,
)
from src.infrastructure.llm_proxy.types import LLMProxyRequest
from src.infrastructure.persistence.repositories.knowledge_normalization_rule_repository import (
    KnowledgeNormalizationRuleRepository,
)

if TYPE_CHECKING:
    from src.infrastructure.llm_proxy.service import LLMGatewayService

logger = logging.getLogger(__name__)

_MIN_WRITE_CONFIDENCE = 0.72
_FAST_PATH_MIN_ENTITY_CONFIDENCE = 0.80
_FAST_PATH_MIN_RELATION_CONFIDENCE = 0.80
_SAFE_RELATION_KEEP_MIN_CONFIDENCE = 0.60
_LOCAL_CONTEXT_CHARS = 320
_LOCAL_CONTEXT_TOTAL_CHARS = 620
_FAST_PATH_ENTITY_TYPES = {
    "concept",
    "event",
    "industry",
    "institution",
    "policy",
    "product",
    "asset",
    "region",
}
_GENERIC_ENTITY_NAMES = {
    "市场",
    "行业",
    "板块",
    "资产",
    "风险",
    "政策",
    "事件",
    "主体",
    "方向",
    "影响",
    "产业链",
    "供应链",
    "生态链",
    "相关主体",
    "受影响资产",
    "主要方向",
}
_SAFE_RELATION_TYPES = {
    "mentions",
    "related_to",
    "affects",
    "benefits_from",
    "risk_to",
    "causes",
    "supports",
}

_DECISION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": [
                "use_existing_rule",
                "reuse_semantic_candidate",
                "create_new_alias_rule",
                "create_new_canonical_entity",
                "create_type_boundary_rule",
                "suggest_new_type",
            ],
        },
        "canonical_name": {"type": "string"},
        "entity_type": {"type": "string"},
        "taxonomy": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reason": {"type": "string"},
        "new_type_suggestion": {
            "type": ["object", "null"],
            "properties": {
                "type_name": {"type": "string"},
                "type_kind": {"type": "string"},
                "definition": {"type": "string"},
                "endpoint_constraints": {"type": "string"},
                "positive_examples": {"type": "array", "items": {"type": "string"}},
                "negative_examples": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": True,
        },
    },
    "required": ["decision", "canonical_name", "entity_type", "confidence", "reason"],
    "additionalProperties": False,
}

_RELATION_DECISION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": [
                "reuse_semantic_candidate",
                "keep_current_relation",
                "suggest_new_type",
            ],
        },
        "relation_type": {"type": "string"},
        "canonical_relation_label": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reason": {"type": "string"},
        "new_type_suggestion": {
            "type": ["object", "null"],
            "properties": {
                "type_name": {"type": "string"},
                "type_kind": {"type": "string"},
                "definition": {"type": "string"},
                "endpoint_constraints": {"type": "string"},
                "positive_examples": {"type": "array", "items": {"type": "string"}},
                "negative_examples": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": True,
        },
    },
    "required": ["decision", "relation_type", "confidence", "reason"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = """\
你是金融知识图谱写入前实体标准化决策器。

你会收到一个待写入实体、原文上下文、已有 active 归一化规则和可能的候选 canonical 名称。
你的任务不是抽取新事实，而是判断这个实体在写入知识图谱前应该如何标准化。

决策规则：
- 如果已有规则适用，返回 use_existing_rule。
- 如果语义候选中已有实体/关系适用，返回 reuse_semantic_candidate。
- 如果原始名称是某个 canonical 的明确别名，返回 create_new_alias_rule。
- 如果它是新的独立实体，返回 create_new_canonical_entity。
- 如果它体现新的类型边界规则，返回 create_type_boundary_rule。
- 如果当前类型集合无法表达，但新增类型会改变查询语义或统计口径，返回 suggest_new_type，并给出 new_type_suggestion。
- 如果上下文不足、存在歧义或会误合并，不要阻塞写入；返回 create_new_canonical_entity，并在 reason 中说明低置信度原因。
- 不要把“风险资产”错误合并到“高股息”。
- 名称包含“产业链/供应链/生态链”通常是 concept，不是正式行业分类。
- 泛化主题不要标为 policy，只有具体政策文件、会议、监管规则才是 policy。

只输出符合 JSON Schema 的 JSON 对象。

输出 JSON 必须包含所有 required 字段。即使某字段为空，也要显式输出空字符串或 null。
输出模板：
{
  "decision": "create_new_canonical_entity",
  "canonical_name": "实体规范名",
  "entity_type": "entity_type",
  "taxonomy": "",
  "confidence": 0.85,
  "reason": "一句话说明决策依据",
  "new_type_suggestion": null
}
"""

_RELATION_SYSTEM_PROMPT = """\
你是金融知识图谱写入前关系标准化决策器。

你会收到一个待写入关系、原文上下文、已有 relation type registry 和 embedding 召回的相似关系候选。
你的任务不是重新抽取事实，而是判断当前 relation_type 应该保持、复用已有语义候选的关系类型，还是注册新的关系类型。

决策规则：
- 如果当前 relation_type 已经能表达事实，返回 keep_current_relation。
- 如果语义候选中的关系类型更稳定、更适合当前事实，返回 reuse_semantic_candidate。
- 如果当前类型集合无法表达，且新增关系类型会改变查询语义、统计口径或图谱展开逻辑，返回 suggest_new_type。
- 不要输出 uncertain；上下文不足或候选不可靠时，返回 keep_current_relation，并在 reason 中说明原因。
- 不要随便扩展 relation type。优先复用 active_type_registry 中已有类型。

只输出符合 JSON Schema 的 JSON 对象。

输出 JSON 必须包含所有 required 字段。即使某字段为空，也要显式输出空字符串或 null。
输出模板：
{
  "decision": "keep_current_relation",
  "relation_type": "related_to",
  "canonical_relation_label": "",
  "confidence": 0.85,
  "reason": "一句话说明决策依据",
  "new_type_suggestion": null
}
"""


class FinancialNormalizationDecisionService:
    """Normalizes financial weak-ID entities before they enter the main graph."""

    def __init__(
        self,
        *,
        llm_service: "LLMGatewayService | None",
        llm_model: str | None,
        rule_repository: KnowledgeNormalizationRuleRepository,
        rules: NormalizationRules,
        adapter_name: str = "financial",
        semantic_candidate_provider: Any | None = None,
    ) -> None:
        self._llm = llm_service
        self._llm_model = llm_model
        self._rule_repository = rule_repository
        self._rules = rules
        self._adapter_name = adapter_name
        self._semantic_candidate_provider = semantic_candidate_provider

    async def normalize_payload(
        self,
        payload: dict[str, Any],
        *,
        source_id: str,
        source_type: str,
    ) -> dict[str, Any]:
        result = dict(payload)
        decisions: list[dict[str, Any]] = []

        for field in ("mentioned_entities", "affected_entities"):
            normalized_entities: list[dict[str, Any]] = []
            for raw_entity in result.get(field, []) or []:
                if not isinstance(raw_entity, dict):
                    continue
                entity, decision = await self._normalize_entity(
                    raw_entity,
                    source_id=source_id,
                    source_type=source_type,
                    context=_payload_context(payload),
                )
                decisions.append(decision)
                normalized_entities.append(entity)
            result[field] = _dedupe_entities(normalized_entities)

        package = result.get("candidate_fact_package")
        if isinstance(package, dict):
            normalized_package, package_decisions = await self._normalize_candidate_package(
                package,
                source_id=source_id,
                source_type=source_type,
                context=_payload_context(payload),
            )
            result["candidate_fact_package"] = normalized_package
            decisions.extend(package_decisions)

        if decisions:
            result["_normalization_decisions"] = decisions
        return result

    async def _normalize_candidate_package(
        self,
        package: dict[str, Any],
        *,
        source_id: str,
        source_type: str,
        context: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        result = dict(package)
        decisions: list[dict[str, Any]] = []
        endpoint_name_map: dict[str, str] = {}
        normalized_entities: list[dict[str, Any]] = []

        for raw_entity in result.get("entities", []) or []:
            if not isinstance(raw_entity, dict):
                continue
            raw_name = _entity_name(raw_entity)
            entity, decision = await self._normalize_entity(
                raw_entity,
                source_id=source_id,
                source_type=source_type,
                context=context,
            )
            decisions.append(decision)
            if entity is None:
                continue
            normalized_name = _entity_name(entity)
            if raw_name and normalized_name:
                endpoint_name_map[raw_name] = normalized_name
            normalized_entities.append(entity)

        if normalized_entities:
            result["entities"] = _dedupe_entities(normalized_entities)
        result["relations"] = _rewrite_relation_endpoint_names(result.get("relations"), endpoint_name_map)
        result["relations"], relation_decisions = await self._normalize_relations(
            result.get("relations"),
            source_id=source_id,
            source_type=source_type,
            context=context,
        )
        decisions.extend(relation_decisions)
        return result, decisions

    async def _normalize_relations(
        self,
        relations: Any,
        *,
        source_id: str,
        source_type: str,
        context: str,
    ) -> tuple[list[Any], list[dict[str, Any]]]:
        if not isinstance(relations, list):
            return [], []
        normalized_relations: list[Any] = []
        decisions: list[dict[str, Any]] = []
        for relation in relations:
            if not isinstance(relation, dict):
                normalized_relations.append(relation)
                continue
            normalized, decision = await self._normalize_relation(
                relation,
                source_id=source_id,
                source_type=source_type,
                context=context,
            )
            normalized_relations.append(normalized)
            if decision:
                decisions.append(decision)
        return normalized_relations, decisions

    async def _normalize_relation(
        self,
        relation: dict[str, Any],
        *,
        source_id: str,
        source_type: str,
        context: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        result = dict(relation)
        relation_type = _clean_text(result.get("relation_type"))
        if self._llm is None or not relation_type:
            return result, None

        decision_id = _relation_decision_id(source_id, result)
        memory = self._lookup_memory(object_kind="relation", raw_signature=_relation_memory_signature(result))
        if memory:
            result["relation_type"] = str(memory.get("relation_type") or relation_type)
            decision = _relation_decision(
                decision_id=decision_id,
                decision=str(memory.get("decision") or "keep_current_relation"),
                raw_relation=relation,
                normalized_relation=result,
                confidence=float(memory.get("confidence") or relation.get("confidence") or 0.8),
                reason=str(memory.get("reason") or "matched persisted normalization decision"),
                source="normalization_memory",
            )
            properties = dict(result.get("properties") or {})
            properties["_normalization"] = decision
            result["properties"] = properties
            return result, decision

        search_relations = getattr(self._semantic_candidate_provider, "search_relations", None)
        if not callable(search_relations):
            return result, None

        try:
            semantic_candidates = await search_relations(
                query=_relation_query_text(result),
                relation_type=relation_type,
                context=_local_relation_context(result, context),
                limit=8,
            )
            if not isinstance(semantic_candidates, list):
                semantic_candidates = []
            if _can_keep_relation_without_llm(result, semantic_candidates):
                decision = _relation_decision(
                    decision_id=decision_id,
                    decision="keep_current_relation",
                    raw_relation=relation,
                    normalized_relation=result,
                    confidence=float(relation.get("confidence") or _SAFE_RELATION_KEEP_MIN_CONFIDENCE),
                    reason="fast path: safe relation type with evidence and no semantic conflict candidate",
                    source="fast_path",
                )
                properties = dict(result.get("properties") or {})
                properties["_normalization"] = decision
                result["properties"] = properties
                self._persist_memory(
                    object_kind="relation",
                    raw_signature=_relation_memory_signature(result),
                    canonical_value=str(result.get("relation_type") or ""),
                    confidence=float(decision["confidence"]),
                    source="fast_path",
                    decision=decision,
                )
                return result, decision
            llm_decision = await self._llm_decide_relation(
                result,
                source_id=source_id,
                source_type=source_type,
                context=context,
                decision_id=decision_id,
                semantic_candidates=semantic_candidates[:8],
            )
        except Exception as exc:
            logger.warning("[kg_normalization] relation decision failed, keeping relation: %s", exc)
            return result, _relation_decision(
                decision_id=decision_id,
                decision="keep_current_relation",
                raw_relation=relation,
                normalized_relation=result,
                confidence=float(relation.get("confidence") or 0.6),
                reason=f"relation normalization failed; kept current relation: {type(exc).__name__}",
                source="llm_write_time",
            )

        confidence = float(llm_decision.get("confidence") or 0.0)
        if confidence < _MIN_WRITE_CONFIDENCE:
            llm_decision = {
                **llm_decision,
                "decision": "keep_current_relation",
                "relation_type": relation_type,
                "reason": (
                    "low confidence relation normalization decision; kept current relation: "
                    f"{llm_decision.get('reason') or ''}"
                ).strip(),
            }
        canonical_relation_type = _clean_text(llm_decision.get("relation_type")) or relation_type
        if llm_decision["decision"] in {"reuse_semantic_candidate", "suggest_new_type"}:
            result["relation_type"] = canonical_relation_type
        if llm_decision["decision"] == "suggest_new_type":
            self._upsert_type_registry_rule(
                llm_decision={**llm_decision, "entity_type": canonical_relation_type},
                confidence=confidence,
                decision_id=decision_id,
                source_id=source_id,
                source_type=source_type,
            )

        decision = _relation_decision(
            decision_id=decision_id,
            decision=str(llm_decision["decision"]),
            raw_relation=relation,
            normalized_relation=result,
            confidence=confidence,
            reason=str(llm_decision.get("reason") or ""),
            source="llm_write_time",
        )
        self._persist_memory(
            object_kind="relation",
            raw_signature=_relation_memory_signature(result),
            canonical_value=str(result.get("relation_type") or ""),
            confidence=confidence,
            source="llm_write_time",
            decision=decision,
        )
        properties = dict(result.get("properties") or {})
        properties["_normalization"] = decision
        result["properties"] = properties
        return result, decision

    async def _normalize_entity(
        self,
        raw_entity: dict[str, Any],
        *,
        source_id: str,
        source_type: str,
        context: str,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        raw_name = _entity_name(raw_entity)
        raw_type = str(raw_entity.get("type") or raw_entity.get("entity_type") or "").strip()
        decision_id = _decision_id(source_id, raw_entity)

        deterministic = normalize_entity_with_rules(raw_entity, self._rules)
        if _has_deterministic_change(raw_entity, deterministic):
            decision = _decision(
                decision_id=decision_id,
                decision="use_existing_rule",
                raw_entity=raw_entity,
                normalized_entity=deterministic,
                confidence=1.0,
                reason="matched active normalization rules",
                source="active_rules",
            )
            deterministic["_normalization"] = decision
            return deterministic, decision

        memory = self._lookup_memory(object_kind="entity", raw_signature=_entity_memory_signature(deterministic))
        if memory:
            normalized = dict(deterministic)
            normalized["name"] = str(memory.get("canonical_name") or _entity_name(deterministic))
            normalized["type"] = str(memory.get("entity_type") or normalized.get("type") or raw_type)
            taxonomy = str(memory.get("taxonomy") or normalized.get("taxonomy") or "")
            if taxonomy and normalized["type"] == "concept":
                normalized["taxonomy"] = taxonomy
            normalized = normalize_entity_with_rules(normalized, self._rules)
            decision = _decision(
                decision_id=decision_id,
                decision=str(memory.get("decision") or "create_new_canonical_entity"),
                raw_entity=raw_entity,
                normalized_entity=normalized,
                confidence=float(memory.get("confidence") or raw_entity.get("confidence") or 0.8),
                reason=str(memory.get("reason") or "matched persisted normalization decision"),
                source="normalization_memory",
            )
            normalized["_normalization"] = decision
            return normalized, decision

        rule_candidates = _candidate_rules(raw_entity, self._rules)
        semantic_candidates = await self._semantic_candidates(raw_entity, context=context)
        if _can_fast_path_entity(raw_entity, deterministic, rule_candidates, semantic_candidates):
            decision = _decision(
                decision_id=decision_id,
                decision="create_new_canonical_entity",
                raw_entity=raw_entity,
                normalized_entity=deterministic,
                confidence=float(raw_entity.get("confidence") or _FAST_PATH_MIN_ENTITY_CONFIDENCE),
                reason="fast path: high-confidence clean entity without rules or semantic candidates",
                source="fast_path",
            )
            deterministic["_normalization"] = decision
            self._persist_memory(
                object_kind="entity",
                raw_signature=_entity_memory_signature(deterministic),
                canonical_value=_entity_name(deterministic),
                confidence=float(decision["confidence"]),
                source="fast_path",
                decision=decision,
            )
            return deterministic, decision
        if _can_reuse_current_entity_without_llm(raw_entity, deterministic, rule_candidates, semantic_candidates):
            decision = _decision(
                decision_id=decision_id,
                decision="create_new_canonical_entity",
                raw_entity=raw_entity,
                normalized_entity=deterministic,
                confidence=float(raw_entity.get("confidence") or _FAST_PATH_MIN_ENTITY_CONFIDENCE),
                reason="fast path: existing rule candidates are compatible with current canonical entity",
                source="fast_path",
            )
            deterministic["_normalization"] = decision
            self._persist_memory(
                object_kind="entity",
                raw_signature=_entity_memory_signature(deterministic),
                canonical_value=_entity_name(deterministic),
                confidence=float(decision["confidence"]),
                source="fast_path",
                decision=decision,
            )
            return deterministic, decision

        if self._llm is None:
            decision = _decision(
                decision_id=decision_id,
                decision="create_new_canonical_entity",
                raw_entity=raw_entity,
                normalized_entity=deterministic,
                confidence=float(raw_entity.get("confidence") or 0.75),
                reason="LLM normalization service unavailable; kept deterministic canonical entity",
                source="deterministic_no_llm",
            )
            deterministic["_normalization"] = decision
            return deterministic, decision

        try:
            llm_decision = await self._llm_decide(
                raw_entity,
                source_id=source_id,
                source_type=source_type,
                context=context,
                decision_id=decision_id,
                rule_candidates=rule_candidates,
                semantic_candidates=semantic_candidates,
            )
        except Exception as exc:
            logger.warning("[kg_normalization] LLM decision failed, keeping deterministic entity: %s", exc)
            decision = _decision(
                decision_id=decision_id,
                decision="create_new_canonical_entity",
                raw_entity=raw_entity,
                normalized_entity=deterministic,
                confidence=float(raw_entity.get("confidence") or 0.6),
                reason=f"LLM normalization decision failed; kept deterministic canonical entity: {type(exc).__name__}",
                source="llm_write_time",
            )
            deterministic["_normalization"] = decision
            return deterministic, decision
        confidence = float(llm_decision.get("confidence") or 0.0)
        if confidence < _MIN_WRITE_CONFIDENCE:
            llm_decision = dict(llm_decision)
            llm_decision["decision"] = "create_new_canonical_entity"
            llm_decision["canonical_name"] = _entity_name(deterministic)
            llm_decision["entity_type"] = str(deterministic.get("type") or raw_type)
            llm_decision["reason"] = (
                f"low confidence normalization decision; kept as independent canonical entity: "
                f"{llm_decision.get('reason') or ''}"
            ).strip()

        normalized = dict(deterministic)
        canonical_name = _clean_text(llm_decision.get("canonical_name")) or _entity_name(deterministic)
        entity_type = _clean_text(llm_decision.get("entity_type")) or str(deterministic.get("type") or raw_type)
        taxonomy = _clean_text(llm_decision.get("taxonomy")) or str(deterministic.get("taxonomy") or "")
        normalized["type"] = entity_type
        normalized["name"] = canonical_name
        if taxonomy and entity_type == "concept":
            normalized["taxonomy"] = taxonomy
        normalized = normalize_entity_with_rules(normalized, self._rules)
        if llm_decision["decision"] in {"create_new_alias_rule", "create_type_boundary_rule"} and raw_name != canonical_name:
            self._upsert_alias_rule(
                raw_name=raw_name,
                canonical_name=canonical_name,
                confidence=confidence,
                decision_id=decision_id,
                source_id=source_id,
                source_type=source_type,
                reason=llm_decision.get("reason"),
                action=str(llm_decision["decision"]),
            )
        if llm_decision["decision"] == "reuse_semantic_candidate" and raw_name != canonical_name:
            self._upsert_alias_rule(
                raw_name=raw_name,
                canonical_name=canonical_name,
                confidence=confidence,
                decision_id=decision_id,
                source_id=source_id,
                source_type=source_type,
                reason=llm_decision.get("reason"),
                action="reuse_semantic_candidate",
            )
        if llm_decision["decision"] == "suggest_new_type":
            self._upsert_type_registry_rule(
                llm_decision=llm_decision,
                confidence=confidence,
                decision_id=decision_id,
                source_id=source_id,
                source_type=source_type,
            )

        decision = _decision(
            decision_id=decision_id,
            decision=str(llm_decision["decision"]),
            raw_entity=raw_entity,
            normalized_entity=normalized,
            confidence=confidence,
            reason=str(llm_decision.get("reason") or ""),
            source="llm_write_time",
        )
        self._persist_memory(
            object_kind="entity",
            raw_signature=_entity_memory_signature(deterministic),
            canonical_value=_entity_name(normalized),
            confidence=confidence,
            source="llm_write_time",
            decision=decision,
        )
        normalized["_normalization"] = decision
        return normalized, decision

    async def _llm_decide(
        self,
        raw_entity: dict[str, Any],
        *,
        source_id: str,
        source_type: str,
        context: str,
        decision_id: str,
        rule_candidates: list[dict[str, str]] | None = None,
        semantic_candidates: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        prompt = json.dumps(
            {
                "source_id": source_id,
                "source_type": source_type,
                "raw_entity": raw_entity,
                "active_rule_candidates": rule_candidates or [],
                "semantic_candidates": semantic_candidates or [],
                "active_type_registry": self._active_type_registry(),
                "context": _local_entity_context(raw_entity, context),
            },
            ensure_ascii=False,
        )
        response = await self._llm.generate(
            LLMProxyRequest(
                prompt=prompt,
                system_prompt=_SYSTEM_PROMPT,
                model=self._llm_model,
                json_schema=_DECISION_JSON_SCHEMA,
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=800,
                metadata={"task": "financial_entity_normalization", "source_id": source_id, "decision_id": decision_id},
            )
        )
        structured = _extract_structured_json(response)
        structured, issues = _validated_entity_decision(structured)
        if structured is None:
            logger.warning(
                "[kg_normalization] LLM decision schema invalid, source_id=%s entity=%s issues=%s",
                source_id,
                raw_entity,
                issues,
            )
            return {
                "decision": "create_new_canonical_entity",
                "canonical_name": _entity_name(raw_entity),
                "entity_type": str(raw_entity.get("type") or raw_entity.get("entity_type") or ""),
                "taxonomy": str(raw_entity.get("taxonomy") or ""),
                "confidence": float(raw_entity.get("confidence") or 0.6),
                "reason": f"LLM response schema invalid; kept as independent canonical entity: {', '.join(issues)}",
                "_schema_invalid_fallback": True,
            }
        if structured.get("decision") == "quarantine":
            structured = dict(structured)
            structured["decision"] = "create_new_canonical_entity"
            structured["reason"] = f"legacy quarantine decision converted to create_new: {structured.get('reason') or ''}".strip()
        return structured

    async def _llm_decide_relation(
        self,
        relation: dict[str, Any],
        *,
        source_id: str,
        source_type: str,
        context: str,
        decision_id: str,
        semantic_candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        prompt = json.dumps(
            {
                "source_id": source_id,
                "source_type": source_type,
                "raw_relation": relation,
                "semantic_relation_candidates": semantic_candidates,
                "active_type_registry": self._active_type_registry(),
                "context": _local_relation_context(relation, context),
            },
            ensure_ascii=False,
        )
        response = await self._llm.generate(
            LLMProxyRequest(
                prompt=prompt,
                system_prompt=_RELATION_SYSTEM_PROMPT,
                model=self._llm_model,
                json_schema=_RELATION_DECISION_JSON_SCHEMA,
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=800,
                metadata={"task": "financial_relation_normalization", "source_id": source_id, "decision_id": decision_id},
            )
        )
        structured, issues = _validated_relation_decision(_extract_structured_json(response))
        if structured is None:
            logger.warning(
                "[kg_normalization] relation decision schema invalid, source_id=%s relation=%s issues=%s",
                source_id,
                relation,
                issues,
            )
            return {
                "decision": "keep_current_relation",
                "relation_type": str(relation.get("relation_type") or ""),
                "canonical_relation_label": "",
                "confidence": float(relation.get("confidence") or 0.6),
                "reason": f"LLM relation response schema invalid; kept current relation: {', '.join(issues)}",
                "_schema_invalid_fallback": True,
            }
        if structured.get("decision") not in {"reuse_semantic_candidate", "keep_current_relation", "suggest_new_type"}:
            structured = {
                "decision": "keep_current_relation",
                "relation_type": str(relation.get("relation_type") or ""),
                "canonical_relation_label": "",
                "confidence": float(relation.get("confidence") or 0.6),
                "reason": f"unsupported relation decision converted to keep_current_relation: {structured.get('reason') or ''}",
            }
        return structured

    def _active_type_registry(self) -> list[dict[str, Any]]:
        list_rules = getattr(self._rule_repository, "list_rules", None)
        if not callable(list_rules):
            return []
        try:
            rules = list_rules(self._adapter_name, status="active")
        except Exception as exc:
            logger.warning("[kg_normalization] failed to load active type registry: %s", exc)
            return []
        result: list[dict[str, Any]] = []
        for rule in rules:
            if not isinstance(rule, dict) or rule.get("rule_type") not in {"entity_type", "relation_type"}:
                continue
            result.append(
                {
                    "type_kind": rule.get("rule_type"),
                    "type_name": rule.get("raw_value"),
                    "definition": rule.get("canonical_value"),
                    "payload": rule.get("payload") or {},
                }
            )
        return result[:80]

    def _lookup_memory(self, *, object_kind: str, raw_signature: str) -> dict[str, Any] | None:
        get_active_decision = getattr(self._rule_repository, "get_active_decision", None)
        if not callable(get_active_decision) or not raw_signature:
            return None
        try:
            row = get_active_decision(
                self._adapter_name,
                object_kind=object_kind,
                raw_signature=raw_signature,
            )
        except Exception as exc:
            logger.warning("[kg_normalization] failed to read normalization memory: %s", exc)
            return None
        if not isinstance(row, dict):
            return None
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
        merged = {**payload, **decision}
        merged.setdefault("confidence", row.get("confidence"))
        merged.setdefault("source", row.get("source"))
        return merged

    def _persist_memory(
        self,
        *,
        object_kind: str,
        raw_signature: str,
        canonical_value: str,
        confidence: float,
        source: str,
        decision: dict[str, Any],
    ) -> None:
        upsert_decision = getattr(self._rule_repository, "upsert_decision", None)
        if not callable(upsert_decision) or not raw_signature:
            return
        try:
            upsert_decision(
                self._adapter_name,
                object_kind=object_kind,
                raw_signature=raw_signature,
                canonical_value=canonical_value,
                confidence=confidence,
                source=source,
                payload={
                    "decision": decision,
                    "raw_signature": raw_signature,
                    "object_kind": object_kind,
                },
            )
        except Exception as exc:
            logger.warning("[kg_normalization] failed to persist normalization memory: %s", exc)

    async def _semantic_candidates(self, raw_entity: dict[str, Any], *, context: str) -> list[dict[str, Any]]:
        provider = self._semantic_candidate_provider
        if provider is None:
            return []
        name = _entity_name(raw_entity)
        if not name:
            return []
        search = getattr(provider, "search", None)
        if not callable(search):
            search = getattr(provider, "search_entities", None)
        if not callable(search):
            return []
        try:
            candidates = await search(
                query=name,
                entity_type=str(raw_entity.get("type") or raw_entity.get("entity_type") or ""),
                context=context[:1000],
                limit=8,
            )
        except TypeError:
            candidates = await search(name)
        if not isinstance(candidates, list):
            return []
        result: list[dict[str, Any]] = []
        for candidate in candidates[:8]:
            if isinstance(candidate, dict):
                result.append(candidate)
            else:
                result.append(
                    {
                        "id": str(getattr(candidate, "id", "") or getattr(candidate, "node_id", "")),
                        "canonical_name": str(getattr(candidate, "canonical_name", "") or getattr(candidate, "name", "")),
                        "entity_type": str(getattr(candidate, "entity_type", "") or getattr(candidate, "type", "")),
                        "score": getattr(candidate, "score", None),
                    }
                )
        return result

    def _upsert_alias_rule(
        self,
        *,
        raw_name: str,
        canonical_name: str,
        confidence: float,
        decision_id: str,
        source_id: str,
        source_type: str,
        reason: Any,
        action: str,
    ) -> None:
        rule_id = f"kg_norm_rule:{self._adapter_name}:alias:{stable_hash([raw_name, canonical_name, 'active'])}"
        self._rule_repository.upsert_rules(
            self._adapter_name,
            [
                {
                    "rule_id": rule_id,
                    "rule_type": "alias",
                    "raw_value": raw_name,
                    "canonical_value": canonical_name,
                    "status": "active",
                    "confidence": confidence,
                    "source": "llm_write_time",
                    "payload": {
                        "decision_id": decision_id,
                        "source_id": source_id,
                        "source_type": source_type,
                        "reason": reason,
                        "merge_mode": "soft_merge",
                        "audit_status": "auto_applied",
                        "action": action,
                    },
                }
            ],
        )
        self._rules.aliases[raw_name] = canonical_name

    def _upsert_type_registry_rule(
        self,
        *,
        llm_decision: dict[str, Any],
        confidence: float,
        decision_id: str,
        source_id: str,
        source_type: str,
    ) -> None:
        suggestion = llm_decision.get("new_type_suggestion")
        suggestion = suggestion if isinstance(suggestion, dict) else {}
        type_name = _clean_text(suggestion.get("type_name")) or _clean_text(llm_decision.get("entity_type"))
        if not type_name:
            return
        type_kind = str(suggestion.get("type_kind") or "entity_type").strip()
        if type_kind not in {"entity_type", "relation_type"}:
            type_kind = "entity_type"
        definition = str(suggestion.get("definition") or llm_decision.get("reason") or "").strip()
        rule_id = f"kg_type_registry:{self._adapter_name}:{type_kind}:{stable_hash([type_name, 'active'])}"
        self._rule_repository.upsert_rules(
            self._adapter_name,
            [
                {
                    "rule_id": rule_id,
                    "rule_type": type_kind,
                    "raw_value": type_name,
                    "canonical_value": definition,
                    "status": "active",
                    "confidence": confidence,
                    "source": "llm_type_registry",
                    "payload": {
                        "decision_id": decision_id,
                        "source_id": source_id,
                        "source_type": source_type,
                        "reason": llm_decision.get("reason"),
                        "endpoint_constraints": suggestion.get("endpoint_constraints"),
                        "positive_examples": suggestion.get("positive_examples") or [],
                        "negative_examples": suggestion.get("negative_examples") or [],
                    },
                }
            ],
        )


def _can_fast_path_entity(
    raw_entity: dict[str, Any],
    normalized_entity: dict[str, Any],
    rule_candidates: list[dict[str, str]],
    semantic_candidates: list[dict[str, Any]],
) -> bool:
    if rule_candidates or semantic_candidates:
        return False
    return _can_accept_current_entity(raw_entity, normalized_entity)


def _can_reuse_current_entity_without_llm(
    raw_entity: dict[str, Any],
    normalized_entity: dict[str, Any],
    rule_candidates: list[dict[str, str]],
    semantic_candidates: list[dict[str, Any]],
) -> bool:
    if semantic_candidates:
        return False
    if not rule_candidates:
        return False
    if not _all_rule_candidates_compatible_with_entity(rule_candidates, normalized_entity):
        return False
    return _can_accept_current_entity(raw_entity, normalized_entity)


def _can_accept_current_entity(raw_entity: dict[str, Any], normalized_entity: dict[str, Any]) -> bool:
    name = _entity_name(normalized_entity)
    entity_type = str(normalized_entity.get("type") or raw_entity.get("type") or raw_entity.get("entity_type") or "")
    if entity_type not in _FAST_PATH_ENTITY_TYPES:
        return False
    if float(raw_entity.get("confidence") or 0.0) < _FAST_PATH_MIN_ENTITY_CONFIDENCE:
        return False
    if not _has_evidence_span(raw_entity):
        return False
    return _is_clean_entity_name(name, entity_type=entity_type)


def _can_keep_relation_without_llm(relation: dict[str, Any], semantic_candidates: list[dict[str, Any]]) -> bool:
    if semantic_candidates:
        return False
    relation_type = _clean_text(relation.get("relation_type"))
    if relation_type not in _SAFE_RELATION_TYPES:
        return False
    if float(relation.get("confidence") or 0.0) < _SAFE_RELATION_KEEP_MIN_CONFIDENCE:
        return False
    if not _clean_text(relation.get("source")) or not _clean_text(relation.get("target")):
        return False
    return _has_evidence_span(relation)


def _all_rule_candidates_compatible_with_entity(
    rule_candidates: list[dict[str, str]],
    normalized_entity: dict[str, Any],
) -> bool:
    canonical_name = _entity_name(normalized_entity)
    if not canonical_name:
        return False
    canonical_key = _memory_key_text(canonical_name)
    for candidate in rule_candidates:
        if not isinstance(candidate, dict):
            return False
        if candidate.get("rule_type") != "alias":
            return False
        candidate_canonical = _memory_key_text(candidate.get("canonical_value"))
        if candidate_canonical != canonical_key:
            return False
    return True


def _has_evidence_span(value: dict[str, Any]) -> bool:
    spans = value.get("evidence_spans")
    if not isinstance(spans, list):
        return False
    return any(isinstance(span, dict) and str(span.get("text") or "").strip() for span in spans)


def _is_clean_entity_name(name: str, *, entity_type: str) -> bool:
    if not name:
        return False
    if len(name) < 2:
        return False
    lowered = name.lower()
    if name in _GENERIC_ENTITY_NAMES:
        return False
    if any(token in name for token in ("kg_ev:", "kg_edge:", "kg:", "http://", "https://", "www.")):
        return False
    if re.fullmatch(r"[a-f0-9]{16,}", lowered):
        return False
    if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f-]{13,}", lowered):
        return False
    if re.fullmatch(r"\d{5,}", name):
        return False
    if re.fullmatch(r"[A-Z]{1,3}:\w+", name):
        return False
    if any(name.endswith(suffix) for suffix in ("产业链", "供应链", "生态链")) and entity_type == "industry":
        return False
    if name.endswith("政策") and entity_type not in {"concept", "policy"}:
        return False
    return True


def _entity_memory_signature(entity: dict[str, Any]) -> str:
    name = _entity_name(entity)
    entity_type = str(entity.get("type") or entity.get("entity_type") or "").strip()
    taxonomy = str(entity.get("taxonomy") or "").strip()
    return f"entity:{entity_type}:{taxonomy}:{_memory_key_text(name)}"


def _relation_memory_signature(relation: dict[str, Any]) -> str:
    return "relation:{relation_type}:{source}:{target}".format(
        relation_type=_memory_key_text(relation.get("relation_type")),
        source=_memory_key_text(relation.get("source")),
        target=_memory_key_text(relation.get("target")),
    )


def _memory_key_text(value: Any) -> str:
    return _clean_text(value).lower()


def _local_entity_context(raw_entity: dict[str, Any], context: str) -> str:
    evidence = _evidence_text(raw_entity)
    parts = [evidence, _window_around_terms(context, [_entity_name(raw_entity), evidence], max_chars=_LOCAL_CONTEXT_CHARS)]
    return "\n".join(part for part in parts if part).strip()[:_LOCAL_CONTEXT_TOTAL_CHARS]


def _local_relation_context(relation: dict[str, Any], context: str) -> str:
    evidence = _evidence_text(relation)
    parts = [
        evidence,
        _window_around_terms(
            context,
            [relation.get("source"), relation.get("target"), relation.get("relation_type"), evidence],
            max_chars=_LOCAL_CONTEXT_CHARS,
        ),
    ]
    return "\n".join(part for part in parts if part).strip()[:_LOCAL_CONTEXT_TOTAL_CHARS]


def _evidence_text(value: dict[str, Any]) -> str:
    spans = value.get("evidence_spans")
    if not isinstance(spans, list):
        return ""
    texts = [
        str(span.get("text") or "").strip()
        for span in spans
        if isinstance(span, dict) and str(span.get("text") or "").strip()
    ]
    return "\n".join(dict.fromkeys(texts))[:500]


def _window_around_terms(context: str, terms: list[Any], *, max_chars: int) -> str:
    source = _clean_text(context)
    if not source:
        return ""
    for term in terms:
        text = _clean_text(term)
        if not text:
            continue
        index = source.find(text[: min(len(text), 32)])
        if index < 0:
            index = source.find(text)
        if index >= 0:
            half = max_chars // 2
            start = max(0, index - half)
            end = min(len(source), index + len(text) + half)
            return source[start:end]
    return source[:max_chars]


def _candidate_rules(entity: dict[str, Any], rules: NormalizationRules) -> list[dict[str, str]]:
    raw_name = _entity_name(entity)
    if not raw_name:
        return []
    candidates: list[dict[str, str]] = []
    for raw_value, canonical_value in rules.aliases.items():
        if _loosely_related(raw_name, raw_value) or _loosely_related(raw_name, canonical_value):
            candidates.append({"rule_type": "alias", "raw_value": raw_value, "canonical_value": canonical_value})
        if len(candidates) >= 12:
            break
    return candidates


def _loosely_related(left: str, right: str) -> bool:
    left = _clean_text(left)
    right = _clean_text(right)
    return bool(left and right and (left in right or right in left))


def _has_deterministic_change(raw_entity: dict[str, Any], normalized: dict[str, Any]) -> bool:
    raw_name = _entity_name(raw_entity)
    raw_type = str(raw_entity.get("type") or raw_entity.get("entity_type") or "").strip()
    raw_taxonomy = str(raw_entity.get("taxonomy") or "")
    return (
        _entity_name(normalized) != raw_name
        or str(normalized.get("type") or "") != raw_type
        or bool(raw_taxonomy and str(normalized.get("taxonomy") or "") != raw_taxonomy)
    )


def _decision(
    *,
    decision_id: str,
    decision: str,
    raw_entity: dict[str, Any],
    normalized_entity: dict[str, Any],
    confidence: float,
    reason: str,
    source: str,
) -> dict[str, Any]:
    return {
        "decision_id": decision_id,
        "decision": decision,
        "source": source,
        "raw_name": _entity_name(raw_entity),
        "raw_type": str(raw_entity.get("type") or raw_entity.get("entity_type") or ""),
        "canonical_name": _entity_name(normalized_entity),
        "entity_type": str(normalized_entity.get("type") or ""),
        "taxonomy": str(normalized_entity.get("taxonomy") or ""),
        "confidence": round(float(confidence), 4),
        "reason": reason,
        "merge_mode": _merge_mode(decision),
        "audit_status": _audit_status(decision),
    }


def _relation_decision(
    *,
    decision_id: str,
    decision: str,
    raw_relation: dict[str, Any],
    normalized_relation: dict[str, Any],
    confidence: float,
    reason: str,
    source: str,
) -> dict[str, Any]:
    return {
        "decision_id": decision_id,
        "decision": decision,
        "source": source,
        "raw_relation_type": str(raw_relation.get("relation_type") or ""),
        "relation_type": str(normalized_relation.get("relation_type") or ""),
        "source_name": _clean_text(normalized_relation.get("source")),
        "target_name": _clean_text(normalized_relation.get("target")),
        "confidence": round(float(confidence), 4),
        "reason": reason,
        "merge_mode": "soft_merge" if decision == "reuse_semantic_candidate" else "create_new",
        "audit_status": "auto_applied" if decision in {"reuse_semantic_candidate", "suggest_new_type"} else "applied",
    }


def _merge_mode(decision: str) -> str:
    if decision == "use_existing_rule":
        return "strong_merge"
    if decision in {"create_new_alias_rule", "create_type_boundary_rule", "reuse_semantic_candidate"}:
        return "soft_merge"
    return "create_new"


def _audit_status(decision: str) -> str:
    if decision in {"create_new_alias_rule", "create_type_boundary_rule", "reuse_semantic_candidate"}:
        return "auto_applied"
    return "applied"


def _decision_id(source_id: str, entity: dict[str, Any]) -> str:
    return f"kg_norm_decision:{stable_hash([source_id, entity])}"


def _relation_decision_id(source_id: str, relation: dict[str, Any]) -> str:
    return f"kg_relation_norm_decision:{stable_hash([source_id, relation])}"


def _relation_query_text(relation: dict[str, Any]) -> str:
    parts = [
        str(relation.get("source") or ""),
        str(relation.get("relation_type") or ""),
        str(relation.get("target") or ""),
        str(relation.get("reason") or ""),
        str(relation.get("summary") or ""),
    ]
    return " ".join(part.strip() for part in parts if part and str(part).strip())


def _payload_context(payload: dict[str, Any]) -> str:
    parts = [
        str(payload.get("title") or ""),
        str(payload.get("summary") or ""),
        str(payload.get("content") or payload.get("text") or ""),
    ]
    return "\n".join(part for part in parts if part).strip()


def _entity_name(entity: dict[str, Any]) -> str:
    return _clean_text(
        entity.get("name")
        or entity.get("canonical_name")
        or entity.get("code")
        or entity.get("fund_code")
        or entity.get("indicator_code")
        or entity.get("id")
        or ""
    )


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = re.sub(r"\s+", "", text)
    return text.strip()


def _extract_structured_json(response: Any) -> Any:
    structured = getattr(response, "structured_output", None)
    if isinstance(structured, dict):
        return structured
    return _try_parse_json(str(getattr(response, "text", "") or ""))


def _validated_entity_decision(value: Any) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(value, dict):
        return None, ["response_not_object"]
    issues: list[str] = []
    decision = _clean_text(value.get("decision"))
    if decision not in {
        "use_existing_rule",
        "reuse_semantic_candidate",
        "create_new_alias_rule",
        "create_new_canonical_entity",
        "create_type_boundary_rule",
        "suggest_new_type",
    }:
        issues.append("decision_invalid_or_missing")
    canonical_name = _clean_text(value.get("canonical_name"))
    if not canonical_name:
        issues.append("canonical_name_missing")
    entity_type = _clean_text(value.get("entity_type"))
    if not entity_type:
        issues.append("entity_type_missing")
    confidence = _optional_float(value.get("confidence"))
    if confidence is None or confidence < 0.0 or confidence > 1.0:
        issues.append("confidence_invalid_or_missing")
    reason = str(value.get("reason") or "").strip()
    if not reason:
        issues.append("reason_missing")
    suggestion = value.get("new_type_suggestion")
    if suggestion is not None and not isinstance(suggestion, dict):
        issues.append("new_type_suggestion_invalid")
    if issues:
        return None, issues
    return {
        "decision": decision,
        "canonical_name": canonical_name,
        "entity_type": entity_type,
        "taxonomy": _clean_text(value.get("taxonomy")),
        "confidence": float(confidence),
        "reason": reason,
        "new_type_suggestion": suggestion,
    }, []


def _validated_relation_decision(value: Any) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(value, dict):
        return None, ["response_not_object"]
    issues: list[str] = []
    decision = _clean_text(value.get("decision"))
    if decision not in {"reuse_semantic_candidate", "keep_current_relation", "suggest_new_type"}:
        issues.append("decision_invalid_or_missing")
    relation_type = _clean_text(value.get("relation_type"))
    if not relation_type:
        issues.append("relation_type_missing")
    confidence = _optional_float(value.get("confidence"))
    if confidence is None or confidence < 0.0 or confidence > 1.0:
        issues.append("confidence_invalid_or_missing")
    reason = str(value.get("reason") or "").strip()
    if not reason:
        issues.append("reason_missing")
    suggestion = value.get("new_type_suggestion")
    if suggestion is not None and not isinstance(suggestion, dict):
        issues.append("new_type_suggestion_invalid")
    if issues:
        return None, issues
    return {
        "decision": decision,
        "relation_type": relation_type,
        "canonical_relation_label": _clean_text(value.get("canonical_relation_label")),
        "confidence": float(confidence),
        "reason": reason,
        "new_type_suggestion": suggestion,
    }, []


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe_entities(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str, str]] = set()
    result: list[dict[str, Any]] = []
    for entity in entities:
        key = (
            str(entity.get("type") or ""),
            _entity_name(entity),
            str(entity.get("taxonomy") or ""),
            str(entity.get("code") or ""),
            str(entity.get("fund_code") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(entity)
    return result


def _rewrite_relation_endpoint_names(relations: Any, endpoint_name_map: dict[str, str]) -> list[Any]:
    if not isinstance(relations, list):
        return []
    if not endpoint_name_map:
        return list(relations)
    result: list[Any] = []
    for relation in relations:
        if not isinstance(relation, dict):
            result.append(relation)
            continue
        rewritten = dict(relation)
        for side in ("source", "target"):
            name = _clean_text(rewritten.get(side))
            if name in endpoint_name_map:
                rewritten[side] = endpoint_name_map[name]
        result.append(rewritten)
    return result


def _try_parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None
