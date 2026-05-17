"""Write-time financial normalization decision tests."""

from __future__ import annotations

from typing import Any

import pytest

from src.application.services.financial_normalization_decision import FinancialNormalizationDecisionService
from src.domain.knowledge_adapters.financial.normalization import NormalizationRules
from src.infrastructure.llm_proxy.types import LLMProxyResponse


def _rules() -> NormalizationRules:
    return NormalizationRules(
        aliases={"红利资产": "高股息"},
        weak_suffixes=("主题", "概念", "板块", "方向"),
        preserved_suffixes=("产业链", "供应链", "生态链"),
        generic_policy_suffixes=("政策",),
        concrete_policy_hints=("规则", "会议", "文件"),
        concept_taxonomy_default="business",
        concept_taxonomy_industry_chain="industry_chain",
        concept_taxonomy_policy_theme="policy_theme",
    )


class _Repo:
    def __init__(self) -> None:
        self.upserts: list[tuple[str, list[dict]]] = []

    def upsert_rules(self, adapter_name: str, rules: list[dict]) -> int:
        self.upserts.append((adapter_name, rules))
        return len(rules)


class _LLM:
    def __init__(self, output: dict[str, Any]):
        self.output = output
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        return LLMProxyResponse(
            text="",
            structured_output=self.output,
            usage={},
            session_id=None,
            duration_ms=1,
            raw_payload={},
        )


@pytest.mark.asyncio
async def test_existing_active_rule_normalizes_without_llm() -> None:
    repo = _Repo()
    llm = _LLM({"decision": "quarantine", "canonical_name": "x", "entity_type": "concept", "confidence": 1, "reason": ""})
    service = FinancialNormalizationDecisionService(
        llm_service=llm,
        llm_model="deepseek-v4-flash",
        rule_repository=repo,  # type: ignore[arg-type]
        rules=_rules(),
    )

    payload = await service.normalize_payload(
        {"mentioned_entities": [{"type": "concept", "name": "红利资产", "confidence": 0.9}]},
        source_id="news:1",
        source_type="news_articles",
    )

    assert payload["mentioned_entities"][0]["name"] == "高股息"
    assert payload["mentioned_entities"][0]["_normalization"]["decision"] == "use_existing_rule"
    assert llm.requests == []
    assert repo.upserts == []


@pytest.mark.asyncio
async def test_llm_can_create_write_time_alias_rule() -> None:
    repo = _Repo()
    llm = _LLM(
        {
            "decision": "create_new_alias_rule",
            "canonical_name": "高股息",
            "entity_type": "concept",
            "taxonomy": "strategy",
            "confidence": 0.91,
            "reason": "红利策略在上下文中表示高股息策略",
        }
    )
    rules = _rules()
    service = FinancialNormalizationDecisionService(
        llm_service=llm,
        llm_model="deepseek-v4-flash",
        rule_repository=repo,  # type: ignore[arg-type]
        rules=rules,
    )

    payload = await service.normalize_payload(
        {
            "title": "红利策略走强",
            "mentioned_entities": [{"type": "concept", "name": "红利策略", "taxonomy": "strategy", "confidence": 0.88}],
        },
        source_id="news:2",
        source_type="news_articles",
    )

    entity = payload["mentioned_entities"][0]
    assert entity["name"] == "高股息"
    assert entity["taxonomy"] == "strategy"
    assert entity["_normalization"]["source"] == "llm_write_time"
    assert entity["_normalization"]["merge_mode"] == "soft_merge"
    assert entity["_normalization"]["audit_status"] == "auto_applied"
    assert repo.upserts[0][1][0]["raw_value"] == "红利策略"
    assert repo.upserts[0][1][0]["canonical_value"] == "高股息"
    assert repo.upserts[0][1][0]["payload"]["audit_status"] == "auto_applied"
    assert repo.upserts[0][1][0]["payload"]["merge_mode"] == "soft_merge"
    assert rules.aliases["红利策略"] == "高股息"


@pytest.mark.asyncio
async def test_low_confidence_llm_decision_quarantines_entity() -> None:
    repo = _Repo()
    llm = _LLM(
        {
            "decision": "create_new_alias_rule",
            "canonical_name": "高股息",
            "entity_type": "concept",
            "taxonomy": "strategy",
            "confidence": 0.4,
            "reason": "上下文不足",
        }
    )
    service = FinancialNormalizationDecisionService(
        llm_service=llm,
        llm_model="deepseek-v4-flash",
        rule_repository=repo,  # type: ignore[arg-type]
        rules=_rules(),
    )

    payload = await service.normalize_payload(
        {"mentioned_entities": [{"type": "concept", "name": "风险资产", "confidence": 0.7}]},
        source_id="news:3",
        source_type="news_articles",
    )

    assert payload["mentioned_entities"] == []
    assert payload["_normalization_quarantine"][0]["decision"]["decision"] == "quarantine"
    assert repo.upserts == []
