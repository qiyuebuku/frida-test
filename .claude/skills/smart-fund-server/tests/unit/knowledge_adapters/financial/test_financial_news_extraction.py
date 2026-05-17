"""Financial news extraction strategy tests."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from src.domain.knowledge.compiler import KnowledgeCompiler
from src.domain.knowledge.enums import EdgeStatus, RecordKind
from src.domain.knowledge.adapter import ValidationResult
from src.domain.knowledge.extraction import ExtractedEntity, TextExtractionInput, TextExtractionResult
from src.domain.knowledge.schemas import KnowledgeInput
from src.domain.knowledge_adapters.financial.adapter import FinancialKGAdapter
from src.domain.knowledge_adapters.financial.news_extraction import (
    FinancialNewsExtractionStrategy,
    enrich_financial_text_payload,
)


class _LLMResponse:
    text = ""
    structured_output = {
        "entities": [
            {
                "type": "concept",
                "name": "固态电池",
                "confidence": 0.8,
                "evidence_spans": [{"field_name": "text", "text": "固态电池"}],
            },
        ],
        "events": [],
        "relations": [],
        "uncertainties": [],
    }


class _LLM:
    def __init__(self) -> None:
        self.requests = []

    async def extract(self, request):
        self.requests.append(request)
        return _LLMResponse()


class _LLMResponseWithoutEvidence:
    text = ""
    structured_output = {
        "entities": [
            {
                "type": "concept",
                "name": "无证据概念",
                "confidence": 0.8,
            },
        ],
        "events": [],
        "relations": [],
        "uncertainties": [],
    }


class _LLMWithoutEvidence:
    async def extract(self, request):
        del request
        return _LLMResponseWithoutEvidence()


class _LLMResponseWithoutEvidenceButSourceBacked:
    text = ""
    structured_output = {
        "entities": [
            {
                "type": "concept",
                "name": "固态电池",
                "confidence": 0.8,
            },
        ],
        "events": [
            {
                "title": "宁德时代加码固态电池研发",
                "confidence": 0.75,
            },
        ],
        "relations": [],
        "uncertainties": [],
    }


class _LLMWithoutEvidenceButSourceBacked:
    async def extract(self, request):
        del request
        return _LLMResponseWithoutEvidenceButSourceBacked()


class _LLMWithEventSummaryOnlyEvidenceSource:
    async def extract(self, request):
        del request

        class Response:
            text = ""
            structured_output = {
                "entities": [],
                "events": [
                    {
                        "title": "创业板人工智能ETF华宝再创新高",
                        "summary": "创业板人工智能ETF华宝（159363）场内价格再创新高。",
                        "evidence_spans": [],
                    }
                ],
                "relations": [],
                "uncertainties": [],
            }
            metadata = {"cache_hit": True}

        return Response()


class _LLMInvalidSchema:
    async def extract(self, request):
        del request

        class Response:
            text = ""
            structured_output = {"entities": "bad"}

        return Response()


class _LLMInvalidCachedThenValid:
    def __init__(self) -> None:
        self.requests = []

    async def extract(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            class InvalidCachedResponse:
                text = ""
                structured_output = {"entities": "bad"}
                metadata = {"cache_hit": True}

            return InvalidCachedResponse()
        return _LLMResponse()


class _LLMNonJsonRealResponse:
    async def extract(self, request):
        del request

        class Response:
            text = '{"entities":[{"type":"concept","name":"半导体"'
            structured_output = None
            metadata = {"cache_hit": False}

        return Response()


class _LLMWithCountryAlias:
    async def extract(self, request):
        del request

        class Response:
            text = ""
            structured_output = {
                "entities": [
                    {
                        "type": "country",
                        "name": "俄罗斯",
                        "confidence": 0.8,
                        "evidence_spans": [{"field_name": "text", "text": "俄罗斯"}],
                    },
                ],
                "events": [],
                "relations": [],
                "uncertainties": [],
            }

        return Response()


class _LLMWithRelationPackage:
    async def extract(self, request):
        del request

        class Response:
            text = ""
            structured_output = {
                "entities": [
                    {
                        "type": "concept",
                        "name": "固态电池",
                        "confidence": 0.8,
                        "evidence_spans": [{"field_name": "text", "text": "固态电池"}],
                    }
                ],
                "events": [
                    {
                        "title": "固态电池政策支持",
                        "summary": "政策支持固态电池研发。",
                        "confidence": 0.75,
                        "evidence_spans": [{"field_name": "text", "text": "政策支持固态电池"}],
                    }
                ],
                "relations": [
                    {
                        "relation_type": "affects",
                        "source": "固态电池政策支持",
                        "target": "固态电池",
                        "direction": "positive",
                        "confidence": 0.7,
                        "evidence_spans": [{"field_name": "text", "text": "政策支持固态电池"}],
                    }
                ],
                "uncertainties": ["政策细节待确认"],
                "rule_suggestions": ["固态电池可作为新能源主题候选"],
            }

        return Response()


class _LLMWithUnknownRelationPackage:
    async def extract(self, request):
        del request

        class Response:
            text = ""
            structured_output = {
                "entities": [
                    {
                        "type": "concept",
                        "name": "固态电池",
                        "confidence": 0.8,
                        "evidence_spans": [{"field_name": "text", "text": "固态电池"}],
                    }
                ],
                "events": [
                    {
                        "title": "固态电池政策支持",
                        "confidence": 0.75,
                        "evidence_spans": [{"field_name": "text", "text": "政策支持固态电池"}],
                    }
                ],
                "relations": [
                    {
                        "relation_type": "supports",
                        "source": "固态电池政策支持",
                        "target": "固态电池",
                        "confidence": 0.7,
                        "evidence_spans": [{"field_name": "text", "text": "政策支持固态电池"}],
                    },
                    {
                        "relation_type": "conflicts_with",
                        "source": "固态电池政策支持",
                        "target": "固态电池",
                        "confidence": 0.5,
                        "evidence_spans": [{"field_name": "text", "text": "政策支持固态电池"}],
                    },
                ],
                "uncertainties": [],
            }

        return Response()


class _LLMWithInvalidEndpointRelationPackage:
    async def extract(self, request):
        del request

        class Response:
            text = ""
            structured_output = {
                "entities": [
                    {
                        "type": "person",
                        "name": "佩斯科夫",
                        "confidence": 0.8,
                        "evidence_spans": [{"field_name": "text", "text": "佩斯科夫"}],
                    }
                ],
                "events": [
                    {
                        "title": "欧洲对俄全面对抗正在回归",
                        "confidence": 0.7,
                        "evidence_spans": [{"field_name": "text", "text": "全面对抗正在回归"}],
                    }
                ],
                "relations": [
                    {
                        "relation_type": "mentions",
                        "source": "佩斯科夫",
                        "target": "欧洲对俄全面对抗正在回归",
                        "confidence": 0.6,
                        "evidence_spans": [{"field_name": "text", "text": "佩斯科夫称"}],
                    }
                ],
                "uncertainties": [],
            }

        return Response()


class _LLMWithMissingRelationEndpointEntity:
    async def extract(self, request):
        del request

        class Response:
            text = ""
            structured_output = {
                "entities": [
                    {
                        "type": "concept",
                        "name": "储能业务",
                        "confidence": 0.8,
                        "evidence_spans": [{"field_name": "text", "text": "储能业务"}],
                    }
                ],
                "events": [],
                "relations": [
                    {
                        "relation_type": "related_to",
                        "source": "储能业务",
                        "target": "量利齐升",
                        "confidence": 0.7,
                        "evidence_spans": [{"field_name": "text", "text": "储能业务实现量利齐升"}],
                    }
                ],
                "uncertainties": [],
            }

        return Response()


class _LLMWithBelongsToAffiliationRelation:
    async def extract(self, request):
        del request

        class Response:
            text = ""
            structured_output = {
                "entities": [
                    {
                        "type": "person",
                        "name": "科斯塔",
                        "confidence": 0.8,
                        "evidence_spans": [{"field_name": "text", "text": "科斯塔"}],
                    },
                    {
                        "type": "institution",
                        "name": "欧洲理事会",
                        "confidence": 0.8,
                        "evidence_spans": [{"field_name": "text", "text": "欧洲理事会"}],
                    },
                ],
                "events": [],
                "relations": [
                    {
                        "relation_type": "belongs_to",
                        "source": "科斯塔",
                        "target": "欧洲理事会",
                        "confidence": 0.7,
                        "evidence_spans": [{"field_name": "text", "text": "欧洲理事会主席科斯塔"}],
                    }
                ],
                "uncertainties": [],
            }

        return Response()


@pytest.mark.asyncio
async def test_financial_news_strategy_turns_symbols_into_mentioned_entities() -> None:
    payload = {
        "source_id": "news-001",
        "title": "宁德时代加码固态电池研发",
        "published_at": "2026-04-24T09:30:00+08:00",
        "text": "宁德时代宣布加码固态电池研发。",
        "symbols": [{"exchange": "SZ", "code": "300750", "name": "宁德时代"}],
    }
    adapter = FinancialKGAdapter()
    enriched = await enrich_financial_text_payload(
        payload,
        source_id="news-001",
        source_type="news_articles",
        pipeline=adapter.text_extraction_pipeline,
        strategy=FinancialNewsExtractionStrategy(),
    )

    assert enriched["mentioned_entities"] == [
        {"type": "stock", "name": "宁德时代", "confidence": 0.9, "exchange": "SZ", "code": "300750"}
    ]


@pytest.mark.asyncio
async def test_financial_news_strategy_uses_configured_llm_model() -> None:
    llm = _LLM()
    payload = {
        "source_id": "news-001",
        "title": "宁德时代加码固态电池研发",
        "published_at": "2026-04-24T09:30:00+08:00",
        "text": "宁德时代宣布加码固态电池研发。",
    }
    adapter = FinancialKGAdapter()

    enriched = await enrich_financial_text_payload(
        payload,
        source_id="news-001",
        source_type="news_articles",
        pipeline=adapter.text_extraction_pipeline,
        strategy=FinancialNewsExtractionStrategy(
            llm_port=llm,
            llm_model="deepseek-v4-flash",
        ),
    )

    assert llm.requests[0].model == "deepseek-v4-flash"
    package = enriched["candidate_fact_package"]
    assert any(
        entity["entity_type"] == "concept"
        and entity["canonical_name"] == "固态电池"
        and entity["confidence"] == 0.8
        and entity.get("evidence_spans")
        for entity in package["entities"]
    )


@pytest.mark.asyncio
async def test_financial_news_strategy_passes_weak_hints_as_llm_context_only() -> None:
    llm = _LLM()
    payload = {
        "source_id": "news-001",
        "title": "宁德时代加码固态电池研发",
        "published_at": "2026-04-24T09:30:00+08:00",
        "text": "宁德时代宣布加码固态电池研发。",
        "weak_entity_hints": [{"kind": "tag", "value": "快充", "confidence": 0.25}],
    }
    adapter = FinancialKGAdapter()

    await enrich_financial_text_payload(
        payload,
        source_id="news-001",
        source_type="news_articles",
        pipeline=adapter.text_extraction_pipeline,
        strategy=FinancialNewsExtractionStrategy(llm_port=llm),
    )

    assert "来源侧弱标签" in llm.requests[0].prompt
    assert "快充" in llm.requests[0].prompt
    assert "必须在标题或正文中找到证据" in llm.requests[0].prompt


@pytest.mark.asyncio
async def test_financial_news_strategy_repairs_missing_evidence_spans_when_text_matches() -> None:
    payload = {
        "source_id": "news-001",
        "title": "宁德时代加码固态电池研发",
        "published_at": "2026-04-24T09:30:00+08:00",
        "text": "宁德时代宣布加码固态电池研发。",
    }
    adapter = FinancialKGAdapter()

    enriched = await enrich_financial_text_payload(
        payload,
        source_id="news-001",
        source_type="news_articles",
        pipeline=adapter.text_extraction_pipeline,
        strategy=FinancialNewsExtractionStrategy(llm_port=_LLMWithoutEvidenceButSourceBacked()),
    )

    package = enriched["candidate_fact_package"]
    assert package["entities"][0]["canonical_name"] == "固态电池"
    assert package["entities"][0]["evidence_spans"][0]["text"] == "固态电池"
    assert package["events"][0]["title"] == "宁德时代加码固态电池研发"
    assert package["events"][0]["evidence_spans"][0]["field_name"] == "title"
    assert any("evidence_spans repaired" in warning for warning in enriched["_extraction_warnings"])


@pytest.mark.asyncio
async def test_financial_news_strategy_repairs_event_empty_evidence_spans_from_summary_text() -> None:
    payload = {
        "source_id": "ft_news:74416",
        "title": "重仓“易中天”的创业板人工智能ETF华宝（159363）再创新高",
        "published_at": "2026-04-24T09:30:00+08:00",
        "text": "创业板人工智能ETF华宝（159363）场内价格再创新高。",
    }
    adapter = FinancialKGAdapter()

    enriched = await enrich_financial_text_payload(
        payload,
        source_id="ft_news:74416",
        source_type="news_articles",
        pipeline=adapter.text_extraction_pipeline,
        strategy=FinancialNewsExtractionStrategy(llm_port=_LLMWithEventSummaryOnlyEvidenceSource()),
    )

    event = enriched["candidate_fact_package"]["events"][0]
    assert event["title"] == "创业板人工智能ETF华宝再创新高"
    assert event["evidence_spans"][0]["text"] == "创业板人工智能ETF华宝（159363）场内价格再创新高。"
    assert any("events[0].evidence_spans repaired" in warning for warning in enriched["_extraction_warnings"])


@pytest.mark.asyncio
async def test_financial_news_strategy_falls_back_when_llm_candidates_without_evidence_spans() -> None:
    payload = {
        "source_id": "news-001",
        "title": "测试新闻",
        "published_at": "2026-04-24T09:30:00+08:00",
        "text": "正文提到了一个概念。",
    }
    adapter = FinancialKGAdapter()

    enriched = await enrich_financial_text_payload(
        payload,
        source_id="news-001",
        source_type="news_articles",
        pipeline=adapter.text_extraction_pipeline,
        strategy=FinancialNewsExtractionStrategy(llm_port=_LLMWithoutEvidence()),
    )

    assert "candidate_fact_package" in enriched
    assert enriched["candidate_fact_package"]["events"][0]["properties"]["fallback_from_source"] is True
    assert any("schema invalid" in warning for warning in enriched["_extraction_warnings"])
    assert any("self-repair failed" in warning for warning in enriched["_extraction_warnings"])


@pytest.mark.asyncio
async def test_financial_news_strategy_records_invalid_llm_schema_warning() -> None:
    payload = {
        "source_id": "news-001",
        "title": "测试新闻",
        "published_at": "2026-04-24T09:30:00+08:00",
        "text": "正文。",
    }
    adapter = FinancialKGAdapter()

    enriched = await enrich_financial_text_payload(
        payload,
        source_id="news-001",
        source_type="news_articles",
        pipeline=adapter.text_extraction_pipeline,
        strategy=FinancialNewsExtractionStrategy(llm_port=_LLMInvalidSchema()),
    )

    assert enriched.get("mentioned_entities", []) == []
    assert any("schema invalid" in warning for warning in enriched["_extraction_warnings"])


@pytest.mark.asyncio
async def test_financial_news_strategy_retries_invalid_cached_llm_response_without_cache() -> None:
    llm = _LLMInvalidCachedThenValid()
    payload = {
        "source_id": "news-001",
        "title": "宁德时代加码固态电池研发",
        "published_at": "2026-04-24T09:30:00+08:00",
        "text": "宁德时代宣布加码固态电池研发。",
    }
    adapter = FinancialKGAdapter()

    enriched = await enrich_financial_text_payload(
        payload,
        source_id="news-001",
        source_type="news_articles",
        pipeline=adapter.text_extraction_pipeline,
        strategy=FinancialNewsExtractionStrategy(llm_port=llm),
    )

    assert len(llm.requests) == 2
    assert llm.requests[0].use_cache is True
    assert llm.requests[1].use_cache is True
    assert llm.requests[1].metadata["retry_reason"] == "schema_invalid_after_cache_hit"
    assert llm.requests[1].messages[:2] == [
        {"role": "system", "content": llm.requests[0].system_prompt},
        {"role": "user", "content": llm.requests[0].prompt},
    ]
    assert llm.requests[1].messages[2]["role"] == "assistant"
    assert "validation_issues" in llm.requests[1].messages[3]["content"]
    assert "field_type_invalid" in llm.requests[1].messages[3]["content"]
    assert "candidate_fact_package" in enriched
    assert any("self-repair succeeded" in warning for warning in enriched["_extraction_warnings"])


@pytest.mark.asyncio
async def test_financial_news_strategy_logs_raw_preview_for_non_json_real_response() -> None:
    payload = {
        "source_id": "news-001",
        "title": "并购重组市场升温",
        "published_at": "2026-04-24T09:30:00+08:00",
        "text": "并购重组市场升温。",
    }
    adapter = FinancialKGAdapter()

    enriched = await enrich_financial_text_payload(
        payload,
        source_id="news-001",
        source_type="news_articles",
        pipeline=adapter.text_extraction_pipeline,
        strategy=FinancialNewsExtractionStrategy(llm_port=_LLMNonJsonRealResponse()),
    )

    assert "candidate_fact_package" in enriched
    assert any("raw_preview=" in warning for warning in enriched["_extraction_warnings"])
    assert any("fallback candidate package created" in warning for warning in enriched["_extraction_warnings"])


@pytest.mark.asyncio
async def test_financial_news_strategy_normalizes_country_alias_to_region() -> None:
    payload = {
        "source_id": "news-001",
        "title": "俄罗斯相关消息",
        "published_at": "2026-04-24T09:30:00+08:00",
        "text": "俄罗斯发布相关消息。",
    }
    adapter = FinancialKGAdapter()

    enriched = await enrich_financial_text_payload(
        payload,
        source_id="news-001",
        source_type="news_articles",
        pipeline=adapter.text_extraction_pipeline,
        strategy=FinancialNewsExtractionStrategy(llm_port=_LLMWithCountryAlias()),
    )

    assert any(
        entity["entity_type"] == "region" and entity["canonical_name"] == "俄罗斯"
        for entity in enriched["candidate_fact_package"]["entities"]
    )


@pytest.mark.asyncio
async def test_financial_adapter_compiles_news_article_without_business_pre_extraction() -> None:
    record = {
        "source_type": "news_articles",
        "payload": {
            "source_id": "news-001",
            "title": "宁德时代加码固态电池研发",
            "published_at": "2026-04-24T09:30:00+08:00",
            "text": "宁德时代宣布加码固态电池研发。",
            "symbols": [{"exchange": "SZ", "code": "300750", "name": "宁德时代"}],
        },
    }

    adapter = FinancialKGAdapter()
    result = await KnowledgeCompiler().compile(adapter, adapter.normalize([record]))

    assert result.failed_records == []
    assert {node.node_type for node in result.nodes} == {"event", "stock"}
    assert any(edge.relation_type == "mentions" and edge.status == EdgeStatus.ACTIVE for edge in result.edges)


@pytest.mark.asyncio
async def test_financial_adapter_infers_exchange_for_extracted_us_stock_without_exchange() -> None:
    record = {
        "source_type": "news_articles",
        "payload": {
            "source_id": "news-us-001",
            "title": "Adobe宣布回购股票",
            "published_at": "2026-04-24T09:30:00+08:00",
            "text": "科技巨头Adobe（ADBE）宣布将斥资回购股票。",
            "mentioned_entities": [
                {"type": "stock", "code": "ADBE", "name": "Adobe", "confidence": 0.8},
            ],
        },
    }

    adapter = FinancialKGAdapter()
    result = await KnowledgeCompiler().compile(adapter, adapter.normalize([record]))

    assert result.failed_records == []
    stock = next(node for node in result.nodes if node.node_type == "stock")
    assert stock.external_ids == {"exchange": "US", "code": "ADBE"}


@pytest.mark.asyncio
async def test_financial_adapter_skips_incomplete_stock_entity_without_code() -> None:
    record = {
        "source_type": "news_articles",
        "payload": {
            "source_id": "news-stock-missing-code",
            "title": "芯片股走低",
            "published_at": "2026-04-24T09:30:00+08:00",
            "text": "港股芯片股走低，市场风险偏好下降。",
            "mentioned_entities": [
                {"type": "stock", "name": "芯片股", "confidence": 0.5},
                {"type": "concept", "name": "芯片股", "confidence": 0.7},
            ],
        },
    }

    adapter = FinancialKGAdapter()
    result = await KnowledgeCompiler().compile(adapter, adapter.normalize([record]))

    assert result.failed_records == []
    assert not any(node.node_type == "stock" and node.canonical_name == "芯片股" for node in result.nodes)
    assert any(node.node_type == "concept" and node.canonical_name == "芯片股" for node in result.nodes)


@pytest.mark.asyncio
async def test_financial_adapter_passes_source_record_weak_hints_to_text_extraction() -> None:
    llm = _LLM()
    adapter = FinancialKGAdapter(news_extraction_strategy=FinancialNewsExtractionStrategy(llm_port=llm))
    item = adapter.normalize(
        {
            "source_type": "news_articles",
            "payload": {
                "source_id": "news-001",
                "title": "宁德时代加码固态电池研发",
                "published_at": "2026-04-24T09:30:00+08:00",
                "text": "宁德时代宣布加码固态电池研发。",
            },
            "metadata": {
                "weak_entity_hints": [{"kind": "tag", "value": "快充", "confidence": 0.25}],
            },
        }
    )[0]

    await adapter.extract_node_drafts(item)

    assert "快充" in llm.requests[0].prompt


@pytest.mark.asyncio
async def test_financial_adapter_allows_news_event_to_mention_another_event() -> None:
    record = {
        "source_type": "news_articles",
        "payload": {
            "source_id": "news-002",
            "title": "欧洲地缘风险升温",
            "published_at": "2026-04-24T09:30:00+08:00",
            "text": "俄方就波法联合军事演习发出警告。",
            "mentioned_entities": [
                {"type": "event", "name": "波法联合军事演习", "confidence": 0.8},
            ],
        },
    }

    adapter = FinancialKGAdapter()
    result = await KnowledgeCompiler().compile(adapter, adapter.normalize([record]))

    assert result.failed_records == []
    assert sum(1 for node in result.nodes if node.node_type == "event") == 2
    assert any(edge.relation_type == "mentions" and edge.status == EdgeStatus.ACTIVE for edge in result.edges)


@pytest.mark.asyncio
async def test_financial_adapter_compiles_country_alias_as_region() -> None:
    record = {
        "source_type": "news_articles",
        "payload": {
            "source_id": "news-003",
            "title": "俄罗斯相关消息",
            "published_at": "2026-04-24T09:30:00+08:00",
            "mentioned_entities": [
                {"type": "country", "name": "俄罗斯", "confidence": 0.8},
            ],
        },
    }

    adapter = FinancialKGAdapter(enable_text_extraction=False)
    result = await KnowledgeCompiler().compile(adapter, adapter.normalize([record]))

    assert result.failed_records == []
    assert any(node.node_type == "region" and node.canonical_name == "俄罗斯" for node in result.nodes)
    assert any(edge.relation_type == "mentions" for edge in result.edges)


@pytest.mark.asyncio
async def test_financial_adapter_consumes_candidate_fact_package_directly() -> None:
    record = {
        "source_type": "news_articles",
        "payload": {
            "source_id": "news-candidate-001",
            "title": "固态电池政策支持",
            "published_at": "2026-04-24T09:30:00+08:00",
            "text": "政策支持固态电池研发。",
        },
    }

    adapter = FinancialKGAdapter(news_extraction_strategy=FinancialNewsExtractionStrategy(llm_port=_LLMWithRelationPackage()))
    result = await KnowledgeCompiler().compile(adapter, adapter.normalize([record]))

    assert result.failed_records == []
    assert any(node.node_type == "concept" and node.canonical_name == "固态电池" for node in result.nodes)
    assert any(
        edge.relation_type == "affects"
        and edge.properties.get("candidate_fact_package") is True
        and edge.properties.get("direction") == "positive"
        for edge in result.edges
    )


@pytest.mark.asyncio
async def test_financial_adapter_normalizes_unknown_llm_relation_types() -> None:
    record = {
        "source_type": "news_articles",
        "payload": {
            "source_id": "news-candidate-unknown-relation",
            "title": "固态电池政策支持",
            "published_at": "2026-04-24T09:30:00+08:00",
            "text": "政策支持固态电池研发。",
        },
    }

    adapter = FinancialKGAdapter(
        news_extraction_strategy=FinancialNewsExtractionStrategy(llm_port=_LLMWithUnknownRelationPackage())
    )
    result = await KnowledgeCompiler().compile(adapter, adapter.normalize([record]))

    assert result.failed_records == []
    affects_edges = [edge for edge in result.edges if edge.properties.get("original_relation_type") == "supports"]
    related_edges = [edge for edge in result.edges if edge.properties.get("original_relation_type") == "conflicts_with"]
    assert any(edge.relation_type == "affects" and edge.properties.get("direction") == "positive" for edge in affects_edges)
    assert any(edge.relation_type == "related_to" and edge.properties.get("direction") == "negative" for edge in related_edges)
    assert all(edge.properties.get("relation_type_normalized") is True for edge in [*affects_edges, *related_edges])


@pytest.mark.asyncio
async def test_financial_adapter_repairs_invalid_relation_endpoint_combo_before_compile() -> None:
    record = {
        "source_type": "news_articles",
        "payload": {
            "source_id": "news-candidate-invalid-endpoint",
            "title": "欧洲地缘风险升温",
            "published_at": "2026-04-24T09:30:00+08:00",
            "text": "佩斯科夫称，欧洲对俄全面对抗正在回归。",
        },
    }

    adapter = FinancialKGAdapter(
        news_extraction_strategy=FinancialNewsExtractionStrategy(llm_port=_LLMWithInvalidEndpointRelationPackage())
    )
    result = await KnowledgeCompiler().compile(adapter, adapter.normalize([record]))

    assert result.failed_records == []
    edge = next(edge for edge in result.edges if edge.properties.get("original_source_type") == "person")
    assert edge.relation_type == "related_to"
    assert edge.properties["original_relation_type"] == "mentions"
    assert edge.properties["relation_type_fallback"] == "invalid_endpoint_to_related_to"


@pytest.mark.asyncio
async def test_financial_adapter_repairs_relation_only_endpoints_before_compile() -> None:
    record = {
        "source_type": "news_articles",
        "payload": {
            "source_id": "news-candidate-missing-relation-endpoint",
            "title": "储能业务改善",
            "published_at": "2026-04-24T09:30:00+08:00",
            "text": "储能业务实现量利齐升。",
        },
    }

    adapter = FinancialKGAdapter(
        news_extraction_strategy=FinancialNewsExtractionStrategy(llm_port=_LLMWithMissingRelationEndpointEntity())
    )
    result = await KnowledgeCompiler().compile(adapter, adapter.normalize([record]))

    assert result.failed_records == []
    assert any(node.node_type == "concept" and node.canonical_name == "量利齐升" for node in result.nodes)
    assert any(edge.relation_type == "related_to" for edge in result.edges)


@pytest.mark.asyncio
async def test_financial_adapter_repairs_invalid_belongs_to_affiliation_relation_before_compile() -> None:
    record = {
        "source_type": "news_articles",
        "payload": {
            "source_id": "news-candidate-invalid-belongs-to",
            "title": "欧洲理事会主席发声",
            "published_at": "2026-04-24T09:30:00+08:00",
            "text": "欧洲理事会主席科斯塔发声。",
        },
    }

    adapter = FinancialKGAdapter(
        news_extraction_strategy=FinancialNewsExtractionStrategy(llm_port=_LLMWithBelongsToAffiliationRelation())
    )
    result = await KnowledgeCompiler().compile(adapter, adapter.normalize([record]))

    assert result.failed_records == []
    edge = next(edge for edge in result.edges if edge.properties.get("original_relation_type") == "belongs_to")
    assert edge.relation_type == "related_to"
    assert edge.properties["relation_type_fallback"] == "invalid_endpoint_to_related_to"
    assert edge.properties["original_source_type"] == "person"
    assert edge.properties["original_target_type"] == "institution"


@pytest.mark.asyncio
async def test_financial_adapter_can_disable_text_extraction() -> None:
    item = KnowledgeInput(
        input_type=FinancialKGAdapter.spec.sources[4].input_type,
        source_type="news_articles",
        source_id="news-001",
        observed_at=datetime.fromisoformat("2026-04-24T09:30:00+08:00"),
        adapter_name="financial",
        adapter_version="v1",
        record_kind=RecordKind.TEXT_DOCUMENT,
        payload={
            "source_id": "news-001",
            "title": "宁德时代加码固态电池研发",
            "published_at": "2026-04-24T09:30:00+08:00",
            "symbols": [{"exchange": "SZ", "code": "300750", "name": "宁德时代"}],
        },
        raw_text="宁德时代加码固态电池研发",
    )

    nodes = await FinancialKGAdapter(enable_text_extraction=False).extract_node_drafts(item)

    assert [node.node_type for node in nodes] == ["event"]


@pytest.mark.asyncio
async def test_financial_adapter_reuses_enriched_payload_for_nodes_and_edges() -> None:
    class CountingStrategy:
        name = "counting"
        version = "v1"

        def __init__(self) -> None:
            self.calls = 0

        async def extract(self, item: TextExtractionInput) -> TextExtractionResult:
            del item
            self.calls += 1
            return TextExtractionResult(
                mentioned_entities=[
                    ExtractedEntity(
                        entity_type="institution",
                        canonical_name=f"机构{self.calls}",
                        confidence=0.9,
                    )
                ]
            )

        def validate_result(self, result: TextExtractionResult, item: TextExtractionInput) -> ValidationResult:
            del result, item
            return ValidationResult.success()

    strategy = CountingStrategy()
    adapter = FinancialKGAdapter(news_extraction_strategy=strategy)
    item = adapter.normalize(
        {
            "source_type": "news_articles",
            "payload": {
                "source_id": "news-001",
                "title": "测试新闻",
                "published_at": "2026-04-24T09:30:00+08:00",
                "text": "测试新闻正文。",
            },
        }
    )[0]

    nodes = await adapter.extract_node_drafts(item)
    edges = await adapter.extract_edge_drafts(item, nodes)

    assert strategy.calls == 1
    assert any(node.node_type == "institution" and node.canonical_name == "机构1" for node in nodes)
    assert any(edge.target_ref == "institution:机构1" for edge in edges)
