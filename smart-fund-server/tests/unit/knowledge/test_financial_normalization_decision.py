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
        self.decisions: dict[tuple[str, str], dict] = {}
        self.decision_upserts: list[dict] = []
        self.rules: list[dict] = []

    def upsert_rules(self, adapter_name: str, rules: list[dict]) -> int:
        self.upserts.append((adapter_name, rules))
        self.rules.extend(rules)
        return len(rules)

    def list_rules(self, adapter_name: str, status: str | None = None) -> list[dict]:
        del adapter_name
        if status is None:
            return self.rules
        return [rule for rule in self.rules if rule.get("status") == status]

    def get_active_decision(self, adapter_name: str, *, object_kind: str, raw_signature: str) -> dict | None:
        del adapter_name
        return self.decisions.get((object_kind, raw_signature))

    def upsert_decision(
        self,
        adapter_name: str,
        *,
        object_kind: str,
        raw_signature: str,
        canonical_value: str,
        confidence: float,
        source: str,
        payload: dict,
    ) -> int:
        row = {
            "adapter_name": adapter_name,
            "object_kind": object_kind,
            "raw_signature": raw_signature,
            "canonical_value": canonical_value,
            "confidence": confidence,
            "source": source,
            "payload": payload,
        }
        self.decision_upserts.append(row)
        self.decisions[(object_kind, raw_signature)] = row
        return 1


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


class _LLMSequence:
    def __init__(self, outputs: list[dict[str, Any]]):
        self.outputs = list(outputs)
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        output = self.outputs.pop(0)
        return LLMProxyResponse(
            text="",
            structured_output=output,
            usage={},
            session_id=None,
            duration_ms=1,
            raw_payload={},
        )


class _SemanticCandidates:
    def __init__(self) -> None:
        self.calls = []

    async def search(self, *, query: str, entity_type: str, context: str, limit: int):
        self.calls.append(
            {
                "query": query,
                "entity_type": entity_type,
                "context": context,
                "limit": limit,
            }
        )
        return [
            {
                "id": "kg:financial:concept:dividend",
                "canonical_name": "高股息",
                "entity_type": "concept",
                "score": 0.91,
            }
        ]


class _RelationSemanticCandidates:
    def __init__(self) -> None:
        self.calls = []

    async def search_relations(self, *, query: str, relation_type: str, context: str, limit: int):
        self.calls.append(
            {
                "query": query,
                "relation_type": relation_type,
                "context": context,
                "limit": limit,
            }
        )
        return [
            {
                "id": "kg_edge:financial:benefits_from:known",
                "relation_type": "benefits_from",
                "source_name": "半导体",
                "target_name": "科创板八条",
                "score": 0.89,
                "summary": "半导体 benefits_from 科创板八条",
            }
        ]


class _EmptySemanticCandidates:
    def __init__(self) -> None:
        self.calls = []

    async def search(self, *, query: str, entity_type: str, context: str, limit: int):
        self.calls.append(
            {
                "query": query,
                "entity_type": entity_type,
                "context": context,
                "limit": limit,
            }
        )
        return []

    async def search_relations(self, *, query: str, relation_type: str, context: str, limit: int):
        self.calls.append(
            {
                "query": query,
                "relation_type": relation_type,
                "context": context,
                "limit": limit,
            }
        )
        return []


@pytest.mark.asyncio
async def test_existing_active_rule_normalizes_without_llm() -> None:
    repo = _Repo()
    llm = _LLM(
        {
            "decision": "create_new_canonical_entity",
            "canonical_name": "x",
            "entity_type": "concept",
            "confidence": 1,
            "reason": "",
        }
    )
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
async def test_candidate_package_entities_normalize_and_rewrite_relation_endpoints() -> None:
    repo = _Repo()
    llm = _LLM(
        {
            "decision": "create_new_canonical_entity",
            "canonical_name": "x",
            "entity_type": "concept",
            "confidence": 1,
            "reason": "",
        }
    )
    service = FinancialNormalizationDecisionService(
        llm_service=llm,
        llm_model="deepseek-v4-flash",
        rule_repository=repo,  # type: ignore[arg-type]
        rules=_rules(),
    )

    payload = await service.normalize_payload(
        {
            "candidate_fact_package": {
                "entities": [{"type": "concept", "name": "红利资产", "confidence": 0.9}],
                "events": [],
                "relations": [
                    {
                        "relation_type": "related_to",
                        "source": "红利资产",
                        "target": "市场风格",
                        "confidence": 0.8,
                    }
                ],
                "uncertainties": [],
            }
        },
        source_id="news:pkg",
        source_type="news_articles",
    )

    package = payload["candidate_fact_package"]
    assert package["entities"][0]["name"] == "高股息"
    assert package["relations"][0]["source"] == "高股息"
    assert payload["_normalization_decisions"][0]["decision"] == "use_existing_rule"
    assert llm.requests == []
    assert repo.upserts == []


@pytest.mark.asyncio
async def test_high_confidence_clean_entity_fast_path_skips_llm_and_persists_memory() -> None:
    repo = _Repo()
    llm = _LLM(
        {
            "decision": "create_new_alias_rule",
            "canonical_name": "错误结果",
            "entity_type": "concept",
            "confidence": 0.99,
            "reason": "should not be called",
        }
    )
    candidates = _EmptySemanticCandidates()
    service = FinancialNormalizationDecisionService(
        llm_service=llm,
        llm_model="deepseek-v4-flash",
        rule_repository=repo,  # type: ignore[arg-type]
        rules=_rules(),
        semantic_candidate_provider=candidates,
    )

    payload = await service.normalize_payload(
        {
            "mentioned_entities": [
                {
                    "type": "industry",
                    "name": "高端装备制造",
                    "confidence": 0.85,
                    "evidence_spans": [
                        {
                            "text": "半导体、生物医药、高端装备制造和汽车零部件等行业成为产业并购的重要方向",
                            "field_name": "text",
                        }
                    ],
                }
            ]
        },
        source_id="news:fast-path",
        source_type="news_articles",
    )

    entity = payload["mentioned_entities"][0]
    assert entity["name"] == "高端装备制造"
    assert entity["_normalization"]["source"] == "fast_path"
    assert entity["_normalization"]["decision"] == "create_new_canonical_entity"
    assert llm.requests == []
    assert candidates.calls[0]["query"] == "高端装备制造"
    assert repo.decision_upserts[0]["object_kind"] == "entity"
    assert repo.decision_upserts[0]["canonical_value"] == "高端装备制造"


@pytest.mark.asyncio
async def test_high_confidence_clean_region_fast_path_skips_llm_and_persists_memory() -> None:
    repo = _Repo()
    llm = _LLM(
        {
            "decision": "create_new_alias_rule",
            "canonical_name": "错误结果",
            "entity_type": "region",
            "confidence": 0.99,
            "reason": "should not be called",
        }
    )
    service = FinancialNormalizationDecisionService(
        llm_service=llm,
        llm_model="deepseek-v4-flash",
        rule_repository=repo,  # type: ignore[arg-type]
        rules=_rules(),
        semantic_candidate_provider=_EmptySemanticCandidates(),
    )

    payload = await service.normalize_payload(
        {
            "mentioned_entities": [
                {
                    "type": "region",
                    "name": "广东",
                    "confidence": 0.85,
                    "evidence_spans": [{"text": "广东一季度末存贷款余额分别突破40万亿元和30万亿元"}],
                }
            ]
        },
        source_id="news:region-fast-path",
        source_type="news_articles",
    )

    entity = payload["mentioned_entities"][0]
    assert entity["name"] == "广东"
    assert entity["_normalization"]["source"] == "fast_path"
    assert entity["_normalization"]["decision"] == "create_new_canonical_entity"
    assert llm.requests == []
    assert repo.decision_upserts[0]["canonical_value"] == "广东"


@pytest.mark.asyncio
async def test_persisted_entity_memory_skips_semantic_and_llm() -> None:
    repo = _Repo()
    first_service = FinancialNormalizationDecisionService(
        llm_service=_LLM(
            {
                "decision": "create_new_alias_rule",
                "canonical_name": "错误结果",
                "entity_type": "concept",
                "confidence": 0.99,
                "reason": "should not be called",
            }
        ),
        llm_model="deepseek-v4-flash",
        rule_repository=repo,  # type: ignore[arg-type]
        rules=_rules(),
        semantic_candidate_provider=_EmptySemanticCandidates(),
    )
    await first_service.normalize_payload(
        {
            "mentioned_entities": [
                {
                    "type": "industry",
                    "name": "高端装备制造",
                    "confidence": 0.85,
                    "evidence_spans": [{"text": "高端装备制造成为产业并购的重要方向"}],
                }
            ]
        },
        source_id="news:memory-prime",
        source_type="news_articles",
    )

    llm = _LLM(
        {
            "decision": "create_new_alias_rule",
            "canonical_name": "错误结果",
            "entity_type": "concept",
            "confidence": 0.99,
            "reason": "should not be called",
        }
    )
    candidates = _EmptySemanticCandidates()
    service = FinancialNormalizationDecisionService(
        llm_service=llm,
        llm_model="deepseek-v4-flash",
        rule_repository=repo,  # type: ignore[arg-type]
        rules=_rules(),
        semantic_candidate_provider=candidates,
    )

    payload = await service.normalize_payload(
        {"mentioned_entities": [{"type": "industry", "name": "高端装备制造", "confidence": 0.4}]},
        source_id="news:memory-hit",
        source_type="news_articles",
    )

    entity = payload["mentioned_entities"][0]
    assert entity["name"] == "高端装备制造"
    assert entity["_normalization"]["source"] == "normalization_memory"
    assert llm.requests == []
    assert candidates.calls == []


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
async def test_incomplete_entity_llm_decision_falls_back_when_gateway_repair_is_not_available() -> None:
    repo = _Repo()
    llm = _LLMSequence(
        [
            {
                "decision": "create_new_canonical_entity",
                "reason": "缺少 required 字段",
            }
        ]
    )
    service = FinancialNormalizationDecisionService(
        llm_service=llm,
        llm_model="deepseek-v4-flash",
        rule_repository=repo,  # type: ignore[arg-type]
        rules=_rules(),
    )

    payload = await service.normalize_payload(
        {"mentioned_entities": [{"type": "concept", "name": "风险资产", "confidence": 0.7}]},
        source_id="news:entity-schema-retry",
        source_type="news_articles",
    )

    entity = payload["mentioned_entities"][0]
    assert entity["name"] == "风险资产"
    assert entity["_normalization"]["source"] == "llm_write_time"
    assert entity["_normalization"]["confidence"] == 0.7
    assert "schema invalid" in entity["_normalization"]["reason"]
    assert len(llm.requests) == 1
    assert llm.requests[0].response_format == {"type": "json_object"}


@pytest.mark.asyncio
async def test_llm_can_reuse_embedding_recalled_semantic_candidate() -> None:
    repo = _Repo()
    llm = _LLM(
        {
            "decision": "reuse_semantic_candidate",
            "canonical_name": "高股息",
            "entity_type": "concept",
            "taxonomy": "strategy",
            "confidence": 0.93,
            "reason": "语义候选显示该实体与高股息一致",
        }
    )
    candidates = _SemanticCandidates()
    rules = _rules()
    service = FinancialNormalizationDecisionService(
        llm_service=llm,
        llm_model="deepseek-v4-flash",
        rule_repository=repo,  # type: ignore[arg-type]
        rules=rules,
        semantic_candidate_provider=candidates,
    )

    payload = await service.normalize_payload(
        {
            "title": "红利策略继续走强",
            "mentioned_entities": [{"type": "concept", "name": "红利策略", "taxonomy": "strategy", "confidence": 0.88}],
        },
        source_id="news:semantic",
        source_type="news_articles",
    )

    entity = payload["mentioned_entities"][0]
    assert entity["name"] == "高股息"
    assert entity["_normalization"]["decision"] == "reuse_semantic_candidate"
    assert entity["_normalization"]["merge_mode"] == "soft_merge"
    assert candidates.calls[0]["query"] == "红利策略"
    request_payload = llm.requests[0].prompt
    assert "semantic_candidates" in request_payload
    assert repo.upserts[0][1][0]["payload"]["action"] == "reuse_semantic_candidate"
    assert rules.aliases["红利策略"] == "高股息"


@pytest.mark.asyncio
async def test_strong_entity_types_also_use_semantic_candidate_normalization() -> None:
    repo = _Repo()
    llm = _LLM(
        {
            "decision": "reuse_semantic_candidate",
            "canonical_name": "中际旭创",
            "entity_type": "stock",
            "confidence": 0.94,
            "reason": "语义候选显示公司简称和既有股票实体一致",
        }
    )
    candidates = _SemanticCandidates()
    service = FinancialNormalizationDecisionService(
        llm_service=llm,
        llm_model="deepseek-v4-flash",
        rule_repository=repo,  # type: ignore[arg-type]
        rules=_rules(),
        semantic_candidate_provider=candidates,
    )

    payload = await service.normalize_payload(
        {"mentioned_entities": [{"type": "stock", "name": "中际旭创股份有限公司", "confidence": 0.9}]},
        source_id="news:stock-semantic",
        source_type="news_articles",
    )

    entity = payload["mentioned_entities"][0]
    assert entity["name"] == "中际旭创"
    assert entity["_normalization"]["decision"] == "reuse_semantic_candidate"
    assert candidates.calls[0]["entity_type"] == "stock"
    assert llm.requests


@pytest.mark.asyncio
async def test_low_confidence_llm_decision_keeps_independent_entity() -> None:
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

    entity = payload["mentioned_entities"][0]
    assert entity["name"] == "风险资产"
    assert entity["_normalization"]["decision"] == "create_new_canonical_entity"
    assert entity["_normalization"]["merge_mode"] == "create_new"
    assert "low confidence" in entity["_normalization"]["reason"]
    assert "_normalization_quarantine" not in payload
    assert repo.upserts == []


@pytest.mark.asyncio
async def test_llm_can_auto_register_new_entity_type() -> None:
    repo = _Repo()
    repo.rules.append(
        {
            "rule_type": "entity_type",
            "raw_value": "concept",
            "canonical_value": "概念主题",
            "status": "active",
            "payload": {"positive_examples": ["AI算力"]},
        }
    )
    llm = _LLM(
        {
            "decision": "suggest_new_type",
            "canonical_name": "AI算力基础设施",
            "entity_type": "infrastructure_theme",
            "taxonomy": "technology",
            "confidence": 0.9,
            "reason": "现有类型无法区分基础设施型主题",
            "new_type_suggestion": {
                "type_name": "infrastructure_theme",
                "type_kind": "entity_type",
                "definition": "基础设施型投资和产业主题",
                "endpoint_constraints": "can participate in related_to/affects relations",
                "positive_examples": ["AI算力基础设施"],
                "negative_examples": ["普通行业名称"],
            },
        }
    )
    service = FinancialNormalizationDecisionService(
        llm_service=llm,
        llm_model="deepseek-v4-flash",
        rule_repository=repo,  # type: ignore[arg-type]
        rules=_rules(),
    )

    payload = await service.normalize_payload(
        {
            "title": "AI算力基础设施投资升温",
            "mentioned_entities": [{"type": "concept", "name": "AI算力基础设施", "confidence": 0.86}],
        },
        source_id="news:type-registry",
        source_type="news_articles",
    )

    entity = payload["mentioned_entities"][0]
    assert entity["type"] == "infrastructure_theme"
    assert entity["_normalization"]["decision"] == "suggest_new_type"
    assert "active_type_registry" in llm.requests[0].prompt
    type_rule = repo.upserts[0][1][0]
    assert type_rule["rule_type"] == "entity_type"
    assert type_rule["raw_value"] == "infrastructure_theme"
    assert type_rule["status"] == "active"
    assert type_rule["source"] == "llm_type_registry"


@pytest.mark.asyncio
async def test_candidate_package_relations_use_semantic_candidates_for_type_normalization() -> None:
    repo = _Repo()
    llm = _LLM(
        {
            "decision": "reuse_semantic_candidate",
            "relation_type": "benefits_from",
            "canonical_relation_label": "受益于",
            "confidence": 0.92,
            "reason": "语义候选显示该关系应归一为 benefits_from",
        }
    )
    candidates = _RelationSemanticCandidates()
    service = FinancialNormalizationDecisionService(
        llm_service=llm,
        llm_model="deepseek-v4-flash",
        rule_repository=repo,  # type: ignore[arg-type]
        rules=_rules(),
        semantic_candidate_provider=candidates,
    )

    payload = await service.normalize_payload(
        {
            "title": "科创板八条推动半导体并购",
            "candidate_fact_package": {
                "entities": [],
                "events": [],
                "relations": [
                    {
                        "source": "半导体",
                        "target": "科创板八条",
                        "relation_type": "related_to",
                        "confidence": 0.8,
                        "reason": "政策推动产业整合",
                    }
                ],
                "uncertainties": [],
            },
        },
        source_id="news:relation-semantic",
        source_type="news_articles",
    )

    relation = payload["candidate_fact_package"]["relations"][0]
    assert relation["relation_type"] == "benefits_from"
    assert relation["properties"]["_normalization"]["decision"] == "reuse_semantic_candidate"
    assert relation["properties"]["_normalization"]["raw_relation_type"] == "related_to"
    assert candidates.calls[0]["relation_type"] == "related_to"
    assert "semantic_relation_candidates" in llm.requests[0].prompt
    assert payload["_normalization_decisions"][0]["decision"] == "reuse_semantic_candidate"


@pytest.mark.asyncio
async def test_relation_fast_path_skips_llm_when_no_semantic_candidates() -> None:
    repo = _Repo()
    llm = _LLM(
        {
            "decision": "reuse_semantic_candidate",
            "relation_type": "benefits_from",
            "confidence": 0.95,
            "reason": "should not be called",
        }
    )
    candidates = _EmptySemanticCandidates()
    service = FinancialNormalizationDecisionService(
        llm_service=llm,
        llm_model="deepseek-v4-flash",
        rule_repository=repo,  # type: ignore[arg-type]
        rules=_rules(),
        semantic_candidate_provider=candidates,
    )

    payload = await service.normalize_payload(
        {
            "candidate_fact_package": {
                "entities": [],
                "events": [],
                "relations": [
                    {
                        "source": "半导体",
                        "target": "科创板八条",
                        "relation_type": "related_to",
                        "confidence": 0.88,
                        "evidence_spans": [
                            {
                                "text": "政策支持会强化并购重组、科创板八条、新质生产力和价值投资等主题之间的联系",
                                "field_name": "text",
                            }
                        ],
                    }
                ],
                "uncertainties": [],
            },
        },
        source_id="news:relation-fast-path",
        source_type="news_articles",
    )

    relation = payload["candidate_fact_package"]["relations"][0]
    assert relation["relation_type"] == "related_to"
    assert relation["properties"]["_normalization"]["source"] == "fast_path"
    assert llm.requests == []
    assert candidates.calls[0]["context"]
    assert repo.decision_upserts[0]["object_kind"] == "relation"
    assert repo.decision_upserts[0]["canonical_value"] == "related_to"


@pytest.mark.asyncio
async def test_compatible_rule_candidates_do_not_force_entity_llm() -> None:
    repo = _Repo()
    rules = _rules()
    rules.aliases["并购重组主题"] = "并购重组"
    rules.aliases["并购重组概念"] = "并购重组"
    llm = _LLM(
        {
            "decision": "create_new_alias_rule",
            "canonical_name": "错误结果",
            "entity_type": "concept",
            "confidence": 0.99,
            "reason": "should not be called",
        }
    )
    candidates = _EmptySemanticCandidates()
    service = FinancialNormalizationDecisionService(
        llm_service=llm,
        llm_model="deepseek-v4-flash",
        rule_repository=repo,  # type: ignore[arg-type]
        rules=rules,
        semantic_candidate_provider=candidates,
    )

    payload = await service.normalize_payload(
        {
            "mentioned_entities": [
                {
                    "type": "concept",
                    "name": "并购重组",
                    "confidence": 0.95,
                    "evidence_spans": [{"text": "并购重组有助于提高关键环节集中度"}],
                }
            ]
        },
        source_id="news:compatible-rules",
        source_type="news_articles",
    )

    entity = payload["mentioned_entities"][0]
    assert entity["name"] == "并购重组"
    assert entity["_normalization"]["source"] == "fast_path"
    assert llm.requests == []
    assert candidates.calls[0]["query"] == "并购重组"


@pytest.mark.asyncio
async def test_safe_low_confidence_relation_keeps_current_without_llm_when_uncontested() -> None:
    repo = _Repo()
    llm = _LLM(
        {
            "decision": "reuse_semantic_candidate",
            "relation_type": "benefits_from",
            "confidence": 0.95,
            "reason": "should not be called",
        }
    )
    candidates = _EmptySemanticCandidates()
    service = FinancialNormalizationDecisionService(
        llm_service=llm,
        llm_model="deepseek-v4-flash",
        rule_repository=repo,  # type: ignore[arg-type]
        rules=_rules(),
        semantic_candidate_provider=candidates,
    )

    payload = await service.normalize_payload(
        {
            "candidate_fact_package": {
                "entities": [],
                "events": [],
                "relations": [
                    {
                        "source": "A股并购重组市场呈现三方面新变化",
                        "target": "监管层",
                        "relation_type": "affects",
                        "confidence": 0.7,
                        "evidence_spans": [{"text": "监管层鼓励上市公司围绕主业开展产业并购"}],
                    }
                ],
                "uncertainties": [],
            }
        },
        source_id="news:safe-relation",
        source_type="news_articles",
    )

    relation = payload["candidate_fact_package"]["relations"][0]
    assert relation["relation_type"] == "affects"
    assert relation["properties"]["_normalization"]["source"] == "fast_path"
    assert llm.requests == []
    assert candidates.calls[0]["relation_type"] == "affects"


@pytest.mark.asyncio
async def test_low_confidence_relation_decision_keeps_current_relation() -> None:
    repo = _Repo()
    llm = _LLM(
        {
            "decision": "reuse_semantic_candidate",
            "relation_type": "benefits_from",
            "confidence": 0.4,
            "reason": "候选相似但证据不足",
        }
    )
    service = FinancialNormalizationDecisionService(
        llm_service=llm,
        llm_model="deepseek-v4-flash",
        rule_repository=repo,  # type: ignore[arg-type]
        rules=_rules(),
        semantic_candidate_provider=_RelationSemanticCandidates(),
    )

    payload = await service.normalize_payload(
        {
            "candidate_fact_package": {
                "entities": [],
                "events": [],
                "relations": [
                    {
                        "source": "半导体",
                        "target": "科创板八条",
                        "relation_type": "related_to",
                        "confidence": 0.8,
                    }
                ],
                "uncertainties": [],
            }
        },
        source_id="news:relation-low",
        source_type="news_articles",
    )

    relation = payload["candidate_fact_package"]["relations"][0]
    assert relation["relation_type"] == "related_to"
    assert relation["properties"]["_normalization"]["decision"] == "keep_current_relation"
    assert "low confidence" in relation["properties"]["_normalization"]["reason"]


@pytest.mark.asyncio
async def test_incomplete_relation_llm_decision_falls_back_when_gateway_repair_is_not_available() -> None:
    repo = _Repo()
    llm = _LLMSequence(
        [
            {
                "decision": "reuse_semantic_candidate",
                "reason": "缺少 required 字段",
            }
        ]
    )
    service = FinancialNormalizationDecisionService(
        llm_service=llm,
        llm_model="deepseek-v4-flash",
        rule_repository=repo,  # type: ignore[arg-type]
        rules=_rules(),
        semantic_candidate_provider=_RelationSemanticCandidates(),
    )

    payload = await service.normalize_payload(
        {
            "candidate_fact_package": {
                "entities": [],
                "events": [],
                "relations": [
                    {
                        "source": "半导体",
                        "target": "科创板八条",
                        "relation_type": "related_to",
                        "confidence": 0.8,
                    }
                ],
                "uncertainties": [],
            }
        },
        source_id="news:relation-schema-retry",
        source_type="news_articles",
    )

    relation = payload["candidate_fact_package"]["relations"][0]
    assert relation["relation_type"] == "related_to"
    assert relation["properties"]["_normalization"]["decision"] == "keep_current_relation"
    assert "schema invalid" in relation["properties"]["_normalization"]["reason"]
    assert len(llm.requests) == 1
    assert llm.requests[0].response_format == {"type": "json_object"}
