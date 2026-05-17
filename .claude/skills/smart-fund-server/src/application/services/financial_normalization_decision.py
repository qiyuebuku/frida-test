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

_WEAK_ENTITY_TYPES = {"industry", "concept", "policy", "institution", "macro_indicator", "commodity", "person", "region", "product"}
_STRONG_ENTITY_TYPES = {"stock", "fund"}
_MIN_WRITE_CONFIDENCE = 0.72

_DECISION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": [
                "use_existing_rule",
                "create_new_alias_rule",
                "create_new_canonical_entity",
                "create_type_boundary_rule",
                "quarantine",
            ],
        },
        "canonical_name": {"type": "string"},
        "entity_type": {"type": "string"},
        "taxonomy": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reason": {"type": "string"},
    },
    "required": ["decision", "canonical_name", "entity_type", "confidence", "reason"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = """\
你是金融知识图谱写入前实体标准化决策器。

你会收到一个待写入实体、原文上下文、已有 active 归一化规则和可能的候选 canonical 名称。
你的任务不是抽取新事实，而是判断这个实体在写入知识图谱前应该如何标准化。

决策规则：
- 如果已有规则适用，返回 use_existing_rule。
- 如果原始名称是某个 canonical 的明确别名，返回 create_new_alias_rule。
- 如果它是新的独立实体，返回 create_new_canonical_entity。
- 如果它体现新的类型边界规则，返回 create_type_boundary_rule。
- 如果上下文不足、存在歧义或会误合并，返回 quarantine。
- 不要把“风险资产”错误合并到“高股息”。
- 名称包含“产业链/供应链/生态链”通常是 concept，不是正式行业分类。
- 泛化主题不要标为 policy，只有具体政策文件、会议、监管规则才是 policy。

只输出符合 JSON Schema 的 JSON 对象。
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
    ) -> None:
        self._llm = llm_service
        self._llm_model = llm_model
        self._rule_repository = rule_repository
        self._rules = rules
        self._adapter_name = adapter_name

    async def normalize_payload(
        self,
        payload: dict[str, Any],
        *,
        source_id: str,
        source_type: str,
    ) -> dict[str, Any]:
        result = dict(payload)
        decisions: list[dict[str, Any]] = []
        quarantined: list[dict[str, Any]] = []

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
                if entity is None:
                    quarantined.append({"field": field, "entity": raw_entity, "decision": decision})
                    continue
                normalized_entities.append(entity)
            result[field] = _dedupe_entities(normalized_entities)

        if decisions:
            result["_normalization_decisions"] = decisions
        if quarantined:
            result["_normalization_quarantine"] = quarantined
        return result

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

        if raw_type in _STRONG_ENTITY_TYPES or raw_type not in _WEAK_ENTITY_TYPES:
            decision = _decision(
                decision_id=decision_id,
                decision="create_new_canonical_entity",
                raw_entity=raw_entity,
                normalized_entity=deterministic,
                confidence=float(raw_entity.get("confidence") or 1.0),
                reason="strong or unsupported weak normalization scope",
                source="deterministic",
            )
            deterministic["_normalization"] = decision
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
            )
        except Exception as exc:
            logger.warning("[kg_normalization] LLM decision failed, quarantining entity: %s", exc)
            decision = _decision(
                decision_id=decision_id,
                decision="quarantine",
                raw_entity=raw_entity,
                normalized_entity=deterministic,
                confidence=0.0,
                reason=f"LLM normalization decision failed: {type(exc).__name__}",
                source="llm_write_time",
            )
            return None, decision
        confidence = float(llm_decision.get("confidence") or 0.0)
        if llm_decision["decision"] == "quarantine" or confidence < _MIN_WRITE_CONFIDENCE:
            decision = _decision(
                decision_id=decision_id,
                decision="quarantine",
                raw_entity=raw_entity,
                normalized_entity=deterministic,
                confidence=confidence,
                reason=str(llm_decision.get("reason") or "low confidence normalization decision"),
                source="llm_write_time",
            )
            return None, decision

        normalized = dict(deterministic)
        canonical_name = _clean_text(llm_decision.get("canonical_name")) or _entity_name(deterministic)
        entity_type = _clean_text(llm_decision.get("entity_type")) or str(deterministic.get("type") or raw_type)
        taxonomy = _clean_text(llm_decision.get("taxonomy")) or str(deterministic.get("taxonomy") or "")
        normalized["type"] = entity_type
        normalized["name"] = canonical_name
        if taxonomy and entity_type == "concept":
            normalized["taxonomy"] = taxonomy
        if llm_decision["decision"] in {"create_new_alias_rule", "create_type_boundary_rule"} and raw_name != canonical_name:
            rule_id = f"kg_norm_rule:{self._adapter_name}:alias:{stable_hash([raw_name, 'active'])}"
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
                            "reason": llm_decision.get("reason"),
                            "merge_mode": "soft_merge",
                            "audit_status": "auto_applied",
                            "action": str(llm_decision["decision"]),
                        },
                    }
                ],
            )
            self._rules.aliases[raw_name] = canonical_name

        decision = _decision(
            decision_id=decision_id,
            decision=str(llm_decision["decision"]),
            raw_entity=raw_entity,
            normalized_entity=normalized,
            confidence=confidence,
            reason=str(llm_decision.get("reason") or ""),
            source="llm_write_time",
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
    ) -> dict[str, Any]:
        response = await self._llm.generate(
            LLMProxyRequest(
                prompt=json.dumps(
                    {
                        "source_id": source_id,
                        "source_type": source_type,
                        "raw_entity": raw_entity,
                        "active_rule_candidates": _candidate_rules(raw_entity, self._rules),
                        "context": context[:2000],
                    },
                    ensure_ascii=False,
                ),
                system_prompt=_SYSTEM_PROMPT,
                model=self._llm_model,
                json_schema=_DECISION_JSON_SCHEMA,
                temperature=0.0,
                max_tokens=800,
                metadata={"task": "financial_entity_normalization", "source_id": source_id, "decision_id": decision_id},
            )
        )
        structured = response.structured_output
        if not isinstance(structured, dict):
            structured = _try_parse_json(response.text)
        if not isinstance(structured, dict) or "decision" not in structured:
            logger.warning("[kg_normalization] LLM decision not parseable, source_id=%s entity=%s", source_id, raw_entity)
            return {
                "decision": "quarantine",
                "canonical_name": _entity_name(raw_entity),
                "entity_type": str(raw_entity.get("type") or ""),
                "confidence": 0.0,
                "reason": "LLM response not parseable",
            }
        return structured


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


def _merge_mode(decision: str) -> str:
    if decision == "use_existing_rule":
        return "strong_merge"
    if decision in {"create_new_alias_rule", "create_type_boundary_rule"}:
        return "soft_merge"
    if decision == "quarantine":
        return "block_merge"
    return "create_new"


def _audit_status(decision: str) -> str:
    if decision == "quarantine":
        return "quarantined"
    if decision in {"create_new_alias_rule", "create_type_boundary_rule"}:
        return "auto_applied"
    return "applied"


def _decision_id(source_id: str, entity: dict[str, Any]) -> str:
    return f"kg_norm_decision:{stable_hash([source_id, entity])}"


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


def _try_parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None
