"""Financial KG entity normalization tests."""

from __future__ import annotations

import pytest

from src.domain.knowledge.compiler import KnowledgeCompiler
from src.domain.knowledge.ids import make_node_id
from src.domain.knowledge_adapters.financial.adapter import FinancialKGAdapter
from src.domain.knowledge_adapters.financial.baseline_rules import financial_baseline_normalization_rules
from src.domain.knowledge_adapters.financial.normalization import (
    NormalizationRules,
    normalize_entity_with_rules,
    normalize_term_name_with_rules,
)
from src.domain.knowledge_adapters.financial.sources import entity_stable_key


def _rules() -> NormalizationRules:
    return NormalizationRules(
        aliases={
            "并购重组主题": "并购重组",
            "并购重组概念": "并购重组",
            "红利资产": "高股息",
            "高股息资产": "高股息",
            "海外工厂": "海外产能",
        },
        weak_suffixes=("主题", "概念", "板块", "方向"),
        preserved_suffixes=("产业链", "供应链", "生态链"),
        generic_policy_suffixes=("政策",),
        concrete_policy_hints=("规则", "会议", "文件"),
        concept_taxonomy_default="business",
        concept_taxonomy_industry_chain="industry_chain",
        concept_taxonomy_policy_theme="policy_theme",
    )


def _rules_from_rows(rows: list[dict]) -> NormalizationRules:
    aliases = {
        row["raw_value"]: row["canonical_value"]
        for row in rows
        if row["rule_type"] == "alias"
    }
    concept_taxonomy = {
        row["raw_value"]: row["canonical_value"]
        for row in rows
        if row["rule_type"] == "concept_taxonomy"
    }
    return NormalizationRules(
        aliases=aliases,
        weak_suffixes=tuple(row["raw_value"] for row in rows if row["rule_type"] == "weak_suffix"),
        preserved_suffixes=tuple(row["raw_value"] for row in rows if row["rule_type"] == "preserved_suffix"),
        generic_policy_suffixes=tuple(row["raw_value"] for row in rows if row["rule_type"] == "generic_policy_suffix"),
        concrete_policy_hints=tuple(row["raw_value"] for row in rows if row["rule_type"] == "concrete_policy_hint"),
        concept_taxonomy_default=concept_taxonomy["default"],
        concept_taxonomy_industry_chain=concept_taxonomy["industry_chain"],
        concept_taxonomy_policy_theme=concept_taxonomy["policy_theme"],
    )


def test_normalize_term_name_maps_common_financial_aliases() -> None:
    rules = _rules()
    assert normalize_term_name_with_rules("并购重组主题", rules) == "并购重组"
    assert normalize_term_name_with_rules("红利资产", rules) == "高股息"
    assert normalize_term_name_with_rules("高股息资产", rules) == "高股息"
    assert normalize_term_name_with_rules("成长资产", rules) == "成长资产"
    assert normalize_term_name_with_rules("海外工厂", rules) == "海外产能"
    assert normalize_term_name_with_rules("新能源车产业链", rules) == "新能源车产业链"


def test_baseline_rules_cover_required_financial_normalization() -> None:
    rules = _rules_from_rows(financial_baseline_normalization_rules())

    assert normalize_term_name_with_rules("红利资产", rules) == "高股息"
    assert normalize_term_name_with_rules("高股息资产", rules) == "高股息"
    assert normalize_term_name_with_rules("海外工厂投产", rules) == "海外产能"
    industry_chain = normalize_entity_with_rules({"type": "industry", "name": "新能源车产业链"}, rules)
    generic_policy = normalize_entity_with_rules({"type": "policy", "name": "并购重组政策"}, rules)

    assert industry_chain["type"] == "concept"
    assert industry_chain["name"] == "新能源车产业链"
    assert industry_chain["taxonomy"] == "industry_chain"
    assert generic_policy["type"] == "concept"
    assert generic_policy["name"] == "并购重组"
    assert generic_policy["taxonomy"] == "policy_theme"


def test_normalize_entity_downgrades_industry_chain_to_concept() -> None:
    entity = normalize_entity_with_rules({"type": "industry", "name": "新能源车产业链"}, _rules())

    assert entity["type"] == "concept"
    assert entity["name"] == "新能源车产业链"
    assert entity["taxonomy"] == "industry_chain"


def test_normalize_entity_downgrades_generic_policy_to_concept() -> None:
    entity = normalize_entity_with_rules({"type": "policy", "name": "并购重组政策"}, _rules())

    assert entity["type"] == "concept"
    assert entity["name"] == "并购重组"
    assert entity["taxonomy"] == "policy_theme"


def test_normalize_entity_maps_country_alias_to_region() -> None:
    entity = normalize_entity_with_rules({"type": "country", "name": "俄罗斯"}, _rules())

    assert entity["type"] == "region"
    assert entity["name"] == "俄罗斯"


def test_entity_stable_key_uses_canonical_name_after_normalization() -> None:
    rules = _rules()
    assert entity_stable_key({"type": "concept", "name": "红利资产", "taxonomy": "strategy"}, normalization_rules=rules) == "strategy:高股息"
    assert entity_stable_key({"type": "concept", "name": "高股息资产", "taxonomy": "strategy"}, normalization_rules=rules) == "strategy:高股息"


@pytest.mark.asyncio
async def test_financial_adapter_dedupes_alias_entities_and_edges() -> None:
    record = {
        "source_type": "news_articles",
        "payload": {
            "source_id": "news-normalize-001",
            "title": "红利资产走强",
            "published_at": "2026-04-24T09:30:00+08:00",
            "mentioned_entities": [
                {"type": "concept", "name": "红利资产", "taxonomy": "strategy", "confidence": 0.8},
                {"type": "concept", "name": "高股息资产", "taxonomy": "strategy", "confidence": 0.8},
            ],
            "affected_entities": [
                {"type": "concept", "name": "红利资产", "taxonomy": "strategy", "confidence": 0.8},
            ],
        },
    }

    adapter = FinancialKGAdapter(enable_text_extraction=False, normalization_rules=_rules())
    result = await KnowledgeCompiler().compile(adapter, adapter.normalize([record]))

    assert result.failed_records == []
    concept_nodes = [node for node in result.nodes if node.node_type == "concept"]
    assert len(concept_nodes) == 1
    assert concept_nodes[0].canonical_name == "高股息"
    assert concept_nodes[0].node_id == make_node_id("financial", "concept", "strategy:高股息")
    assert {edge.target_node_id for edge in result.edges} == {
        make_node_id("financial", "concept", "strategy:高股息")
    }


@pytest.mark.asyncio
async def test_financial_adapter_keeps_structured_industry_components_as_industry() -> None:
    record = {
        "source_type": "industry_components",
        "observed_at": "2026-04-24T09:30:00+08:00",
        "payload": {
            "taxonomy": "citics",
            "component_name": "新能源车产业链",
            "component_code": "CI999",
            "member_stock_code": "300750",
            "member_stock_exchange": "SZ",
            "member_stock_name": "宁德时代",
        },
    }

    adapter = FinancialKGAdapter(enable_text_extraction=False, normalization_rules=_rules())
    result = await KnowledgeCompiler().compile(adapter, adapter.normalize([record]))

    assert result.failed_records == []
    assert any(
        node.node_type == "industry"
        and node.canonical_name == "新能源车产业链"
        and node.node_id == make_node_id("financial", "industry", "citics:CI999")
        for node in result.nodes
    )


@pytest.mark.asyncio
async def test_financial_adapter_downgrades_generic_policy_mentions_to_concept() -> None:
    record = {
        "source_type": "news_articles",
        "payload": {
            "source_id": "news-normalize-002",
            "title": "并购重组政策活跃",
            "published_at": "2026-04-24T09:30:00+08:00",
            "mentioned_entities": [
                {"type": "policy", "name": "并购重组政策", "confidence": 0.8},
            ],
        },
    }

    adapter = FinancialKGAdapter(enable_text_extraction=False, normalization_rules=_rules())
    result = await KnowledgeCompiler().compile(adapter, adapter.normalize([record]))

    assert result.failed_records == []
    assert any(
        node.node_type == "concept"
        and node.canonical_name == "并购重组"
        and node.node_id == make_node_id("financial", "concept", "policy_theme:并购重组")
        for node in result.nodes
    )
    assert any(
        edge.target_node_id == make_node_id("financial", "concept", "policy_theme:并购重组")
        for edge in result.edges
    )
