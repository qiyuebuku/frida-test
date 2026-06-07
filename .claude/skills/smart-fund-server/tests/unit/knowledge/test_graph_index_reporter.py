"""Tests for LLM-backed graph-index report generation."""

from __future__ import annotations

from src.application.services.graph_index_reporter import GraphIndexLLMReporter
from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus, NodeStatus
from src.domain.knowledge.graph_index import GraphIndexFinding, build_graph_index
from src.domain.knowledge.schemas import CompiledEdge, CompiledNode, EvidenceChunk
from src.infrastructure.llm_proxy.types import LLMProxyResponse


async def test_graph_index_reporter_replaces_deterministic_findings_with_llm_output() -> None:
    event = CompiledNode(
        node_id="kg:financial:event:ai-chain",
        adapter_name="financial",
        node_type="event",
        canonical_name="AI算力链叙事",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    semi = CompiledNode(
        node_id="kg:financial:industry:semi",
        adapter_name="financial",
        node_type="industry",
        canonical_name="半导体",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    policy = CompiledNode(
        node_id="kg:financial:concept:policy",
        adapter_name="financial",
        node_type="concept",
        canonical_name="科创板八条",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    edge_1 = CompiledEdge(
        edge_id="kg_edge:financial:mentions:semi",
        adapter_name="financial",
        source_node_id=event.node_id,
        target_node_id=semi.node_id,
        relation_type="mentions",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.9,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news:1"],
        version="v1",
    )
    edge_2 = CompiledEdge(
        edge_id="kg_edge:financial:benefits_from:semi",
        adapter_name="financial",
        source_node_id=semi.node_id,
        target_node_id=policy.node_id,
        relation_type="benefits_from",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.9,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news:2"],
        version="v1",
    )
    edge_3 = CompiledEdge(
        edge_id="kg_edge:financial:affects:semi",
        adapter_name="financial",
        source_node_id=event.node_id,
        target_node_id=semi.node_id,
        relation_type="affects",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.9,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news:1"],
        version="v1",
    )
    edge_4 = CompiledEdge(
        edge_id="kg_edge:financial:related_to:event_policy",
        adapter_name="financial",
        source_node_id=event.node_id,
        target_node_id=policy.node_id,
        relation_type="related_to",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.9,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news:2"],
        version="v1",
    )
    chunk_1 = EvidenceChunk(
        chunk_id="kg_chunk:kg_ev:financial:news:1:0",
        adapter_name="financial",
        evidence_id="kg_ev:financial:news:1",
        content="AI算力链中半导体受益于科创板八条。",
        payload={"published_at": "2026-05-30T00:00:00+00:00", "source_type": "news", "source_id": "source-a"},
    )
    chunk_2 = EvidenceChunk(
        chunk_id="kg_chunk:kg_ev:financial:news:2:0",
        adapter_name="financial",
        evidence_id="kg_ev:financial:news:2",
        content="科创板八条继续影响半导体政策叙事。",
        payload={"published_at": "2026-05-30T00:00:00+00:00", "source_type": "news", "source_id": "source-b"},
    )
    chunks = [chunk_1, chunk_2]
    edges = [edge_1, edge_2, edge_3, edge_4]
    graph_index = build_graph_index(nodes=[event, semi, policy], edges=edges, chunks=chunks)

    fake_llm = _FakeLLM()
    enriched = await GraphIndexLLMReporter(fake_llm).enrich(
        graph_index=graph_index,
        nodes=[event, semi, policy],
        edges=edges,
        chunks=chunks,
    )

    assert enriched.communities
    assert enriched.findings
    assert enriched.deltas
    assert {finding.payload["source"] for finding in enriched.findings} >= {
        "llm_community_report",
        "llm_delta_finding",
    }
    assert all(finding.payload["evidence_validation"]["support_status"] == "supported" for finding in enriched.findings)
    known_chunk_ids = {chunk.chunk_id for chunk in chunks}
    assert all(set(finding.cited_chunk_ids).issubset(known_chunk_ids) for finding in enriched.findings)
    assert all(finding.cited_chunk_ids for finding in enriched.findings)
    assert "community_report_generator" in enriched.diagnostics
    delta_requests = [
        request for request in fake_llm.requests if request.metadata.get("task") == "kg_delta_finding"
    ]
    assert delta_requests
    assert all("delta_id=" not in request.prompt for request in delta_requests)
    assert all("started_at=" not in request.prompt for request in delta_requests)
    assert all("ended_at=" not in request.prompt for request in delta_requests)
    assert all("window_meaning=" in request.prompt for request in delta_requests)
    assert all("delta_id" not in request.metadata for request in delta_requests)
    validation_requests = [
        request for request in fake_llm.requests if request.metadata.get("task") == "kg_finding_evidence_validate"
    ]
    assert validation_requests
    assert all(request.metadata.get("finding_count", 0) >= 1 for request in validation_requests)


async def test_graph_index_reporter_retries_bad_cached_json() -> None:
    event = CompiledNode(
        node_id="kg:financial:event:retry",
        adapter_name="financial",
        node_type="event",
        canonical_name="并购重组政策影响",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    industry = CompiledNode(
        node_id="kg:financial:industry:equipment",
        adapter_name="financial",
        node_type="industry",
        canonical_name="高端装备制造",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    policy = CompiledNode(
        node_id="kg:financial:concept:policy",
        adapter_name="financial",
        node_type="concept",
        canonical_name="并购六条",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    edge_1 = CompiledEdge(
        edge_id="kg_edge:financial:mentions:equipment",
        adapter_name="financial",
        source_node_id=event.node_id,
        target_node_id=industry.node_id,
        relation_type="mentions",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.9,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news:retry"],
        version="v1",
    )
    edge_2 = CompiledEdge(
        edge_id="kg_edge:financial:related_to:policy",
        adapter_name="financial",
        source_node_id=industry.node_id,
        target_node_id=policy.node_id,
        relation_type="related_to",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.9,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news:retry:2"],
        version="v1",
    )
    edge_3 = CompiledEdge(
        edge_id="kg_edge:financial:affects:equipment",
        adapter_name="financial",
        source_node_id=event.node_id,
        target_node_id=industry.node_id,
        relation_type="affects",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.9,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news:retry"],
        version="v1",
    )
    edge_4 = CompiledEdge(
        edge_id="kg_edge:financial:related_to:event_policy",
        adapter_name="financial",
        source_node_id=event.node_id,
        target_node_id=policy.node_id,
        relation_type="related_to",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.9,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news:retry:2"],
        version="v1",
    )
    chunk_1 = EvidenceChunk(
        chunk_id="kg_chunk:kg_ev:financial:news:retry:0",
        adapter_name="financial",
        evidence_id="kg_ev:financial:news:retry",
        content="并购重组政策推动高端装备制造产业链整合。",
        payload={"published_at": "2026-05-30T00:00:00+00:00", "source_type": "news", "source_id": "source-a"},
    )
    chunk_2 = EvidenceChunk(
        chunk_id="kg_chunk:kg_ev:financial:news:retry:1",
        adapter_name="financial",
        evidence_id="kg_ev:financial:news:retry:2",
        content="并购六条影响高端装备制造资产整合。",
        payload={"published_at": "2026-05-30T00:00:00+00:00", "source_type": "news", "source_id": "source-b"},
    )
    edges = [edge_1, edge_2, edge_3, edge_4]
    chunks = [chunk_1, chunk_2]
    graph_index = build_graph_index(nodes=[event, industry, policy], edges=edges, chunks=chunks)

    fake_llm = _FlakyCommunityReportLLM()
    enriched = await GraphIndexLLMReporter(fake_llm).enrich(
        graph_index=graph_index,
        nodes=[event, industry, policy],
        edges=edges,
        chunks=chunks,
    )

    report_requests = [
        request for request in fake_llm.requests if request.metadata.get("task") == "kg_community_report"
    ]
    assert len(report_requests) >= 2
    assert report_requests[0].use_cache is True
    assert report_requests[1].use_cache is False
    assert enriched.communities[0].title == "并购重组政策与高端装备制造"
    assert enriched.findings


async def test_graph_index_reporter_splits_failed_validation_batch() -> None:
    chunk = EvidenceChunk(
        chunk_id="kg_chunk:kg_ev:financial:news:batch:0",
        adapter_name="financial",
        evidence_id="kg_ev:financial:news:batch",
        content="并购重组市场变化涉及政策影响、产业链整合、风险定价和资产质量。",
        payload={"published_at": "2026-05-30T00:00:00+00:00"},
    )
    findings = [
        GraphIndexFinding(
            finding_id=f"kg_finding:batch:{index}",
            community_id="kg_community:test",
            adapter_name="financial",
            projection="market_narrative",
            finding_type=finding_type,
            title=title,
            statement=statement,
            cited_chunk_ids=[chunk.chunk_id],
            cited_evidence_ids=[chunk.evidence_id],
            supporting_edge_ids=[],
            node_ids=[],
            confidence=0.8,
            version="v1",
        )
        for index, (finding_type, title, statement) in enumerate(
            [
                ("policy_impact", "政策影响成为并购重组观察点", "chunk 提到并购重组市场变化涉及政策影响。"),
                ("industry_chain", "产业链整合是并购重组线索", "chunk 提到并购重组市场变化涉及产业链整合。"),
                ("risk_event", "风险定价需要被纳入观察", "chunk 提到并购重组市场变化涉及风险定价。"),
            ],
            start=1,
        )
    ]

    fake_llm = _FlakyValidationBatchLLM()
    validated = await GraphIndexLLMReporter(fake_llm)._validate_findings(
        findings=findings,
        chunk_by_id={chunk.chunk_id: chunk},
    )

    validation_requests = [
        request for request in fake_llm.requests if request.metadata.get("task") == "kg_finding_evidence_validate"
    ]
    assert any(request.metadata.get("finding_count", 0) > 1 for request in validation_requests)
    assert any(request.metadata.get("finding_count") == 1 for request in validation_requests)
    assert len(validated) == len(findings)
    assert all(finding.payload["evidence_validation"]["support_status"] == "supported" for finding in validated)


async def test_graph_index_reporter_rejects_over_specific_l0_title() -> None:
    event_a = CompiledNode(
        node_id="kg:financial:event:hithium-spain",
        adapter_name="financial",
        node_type="event",
        canonical_name="海辰储能西班牙建厂计划",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    event_b = CompiledNode(
        node_id="kg:financial:event:storage-overseas",
        adapter_name="financial",
        node_type="event",
        canonical_name="储能企业海外产能布局",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    storage = CompiledNode(
        node_id="kg:financial:industry:storage",
        adapter_name="financial",
        node_type="industry",
        canonical_name="储能",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    signal = {
        "topic_tags": ["海辰储能西班牙建厂计划"],
        "event_type_tags": ["海外建厂"],
        "impact_tags": ["海外产能"],
        "domain_tags": ["储能"],
        "support_role": "core",
        "boundary_strength": "strong",
        "relationship_strength": 0.9,
    }
    edge_a = CompiledEdge(
        edge_id="kg_edge:financial:affects:hithium",
        adapter_name="financial",
        source_node_id=event_a.node_id,
        target_node_id=storage.node_id,
        relation_type="affects",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.9,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news:hithium"],
        properties=signal,
        version="v1",
    )
    edge_b = CompiledEdge(
        edge_id="kg_edge:financial:affects:storage",
        adapter_name="financial",
        source_node_id=event_b.node_id,
        target_node_id=storage.node_id,
        relation_type="affects",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.9,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news:storage"],
        properties=signal,
        version="v1",
    )
    chunk_a = EvidenceChunk(
        chunk_id="kg_chunk:kg_ev:financial:news:hithium:0",
        adapter_name="financial",
        evidence_id="kg_ev:financial:news:hithium",
        content="海辰储能推进西班牙建厂计划，扩展海外产能。",
        payload={"source_type": "news", "source_id": "source-hithium"},
    )
    chunk_b = EvidenceChunk(
        chunk_id="kg_chunk:kg_ev:financial:news:storage:0",
        adapter_name="financial",
        evidence_id="kg_ev:financial:news:storage",
        content="储能企业加快海外产能布局。",
        payload={"source_type": "news", "source_id": "source-storage"},
    )
    nodes = [event_a, event_b, storage]
    edges = [edge_a, edge_b]
    chunks = [chunk_a, chunk_b]
    graph_index = build_graph_index(nodes=nodes, edges=edges, chunks=chunks)

    fake_llm = _SpecificTitleLLM()
    enriched = await GraphIndexLLMReporter(fake_llm).enrich(
        graph_index=graph_index,
        nodes=nodes,
        edges=edges,
        chunks=chunks,
    )

    [community] = enriched.communities
    assert community.title == "储能海外产能"
    report_requests = [
        request for request in fake_llm.requests if request.metadata.get("task") == "kg_community_report"
    ]
    assert report_requests
    assert "suggested_broad_title=储能海外产能" in report_requests[0].prompt
    assert "level=0，title 必须是较大的主题容器" in report_requests[0].prompt


class _FakeLLM:
    def __init__(self) -> None:
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        task = request.metadata.get("task") if request.metadata else ""
        if task == "kg_finding_evidence_validate":
            finding_ids = request.metadata.get("finding_ids") or []
            return LLMProxyResponse(
                text="",
                structured_output={
                    "validations": [
                        {
                            "finding_id": finding_id,
                            "support_status": "supported",
                            "reason": "chunk 明确支持 finding。",
                        }
                        for finding_id in finding_ids
                    ],
                },
                usage={},
                session_id=None,
                duration_ms=1,
                raw_payload={},
                cache_hit=False,
            )
        if task == "kg_delta_finding":
            return LLMProxyResponse(
                text="",
                structured_output={
                    "delta_summary": "rolling window 内半导体政策叙事继续增强。",
                    "refresh_decision": "append_delta_only",
                    "findings": [
                        {
                            "summary": "半导体近期政策叙事增强",
                            "explanation": "rolling window 内 chunk 显示半导体受益于科创板八条。",
                            "finding_type": "delta_policy_impact",
                            "confidence": 0.75,
                            "cited_chunk_ids": ["kg_chunk:kg_ev:financial:news:1:0"],
                            "supporting_edge_ids": ["kg_edge:financial:benefits_from:semi"],
                        }
                    ]
                },
                usage={},
                session_id=None,
                duration_ms=1,
                raw_payload={},
                cache_hit=False,
            )
        return LLMProxyResponse(
            text="",
            structured_output={
                "title": "AI算力链与半导体政策支持",
                "summary": "半导体与科创板八条形成政策支持叙事。",
                "rating": 7.0,
                "rating_explanation": "该社区连接政策和行业影响。",
                "findings": [
                    {
                        "summary": "半导体政策支持增强",
                        "explanation": "半导体与科创板八条存在受益关系，需回到 chunk 精读确认。",
                        "finding_type": "policy_impact",
                        "confidence": 0.8,
                        "cited_chunk_ids": ["kg_chunk:kg_ev:financial:news:1:0"],
                        "supporting_edge_ids": ["kg_edge:financial:benefits_from:semi"],
                    }
                ],
            },
            usage={},
            session_id=None,
            duration_ms=1,
            raw_payload={},
            cache_hit=False,
        )


class _SpecificTitleLLM(_FakeLLM):
    async def generate(self, request):
        task = request.metadata.get("task") if request.metadata else ""
        if task == "kg_community_report":
            self.requests.append(request)
            return LLMProxyResponse(
                text="",
                structured_output={
                    "title": "海辰储能西班牙建厂计划",
                    "summary": "储能企业海外产能布局相关。",
                    "rating": 7.0,
                    "rating_explanation": "两条证据都涉及储能海外产能。",
                    "findings": [
                        {
                            "summary": "储能企业海外产能布局增强",
                            "explanation": "两个 TextUnit 都指向储能企业海外产能布局。",
                            "finding_type": "industry_chain",
                            "confidence": 0.8,
                            "cited_chunk_ids": ["kg_chunk:kg_ev:financial:news:hithium:0"],
                            "supporting_edge_ids": ["kg_edge:financial:affects:hithium"],
                        }
                    ],
                },
                usage={},
                session_id=None,
                duration_ms=1,
                raw_payload={},
                cache_hit=False,
            )
        return await super().generate(request)


class _FlakyCommunityReportLLM(_FakeLLM):
    def __init__(self) -> None:
        super().__init__()
        self._bad_report_returned = False

    async def generate(self, request):
        task = request.metadata.get("task") if request.metadata else ""
        if task == "kg_community_report" and not self._bad_report_returned:
            self._bad_report_returned = True
            self.requests.append(request)
            return LLMProxyResponse(
                text="模型暂时没有返回 JSON",
                structured_output=None,
                usage={},
                session_id=None,
                duration_ms=1,
                raw_payload={},
                cache_hit=True,
            )
        if task == "kg_community_report":
            self.requests.append(request)
            return LLMProxyResponse(
                text="""```json
{
  "title": "并购重组政策与高端装备制造",
  "summary": "并购重组政策与高端装备制造产业链整合相关。",
  "rating": 6.5,
  "rating_explanation": "chunk 直接提到政策推动产业链整合。",
  "findings": [
    {
      "summary": "高端装备制造产业链整合受到并购政策推动",
      "explanation": "TextUnit 显示并购重组政策推动高端装备制造产业链整合。",
      "finding_type": "policy_impact",
      "confidence": 0.8,
      "cited_chunk_ids": ["kg_chunk:kg_ev:financial:news:retry:0"],
      "supporting_edge_ids": ["kg_edge:financial:mentions:equipment"]
    }
  ]
}
```""",
                structured_output=None,
                usage={},
                session_id=None,
                duration_ms=1,
                raw_payload={},
                cache_hit=False,
            )
        return await super().generate(request)


class _FlakyValidationBatchLLM(_FakeLLM):
    async def generate(self, request):
        task = request.metadata.get("task") if request.metadata else ""
        if task == "kg_community_report":
            self.requests.append(request)
            return LLMProxyResponse(
                text="",
                structured_output={
                    "title": "并购重组市场变化",
                    "summary": "并购重组市场变化涉及政策、产业链、风险和资产质量。",
                    "rating": 6.0,
                    "rating_explanation": "chunk 同时覆盖多个主题。",
                    "findings": [
                        {
                            "summary": "政策影响成为并购重组观察点",
                            "explanation": "chunk 提到并购重组市场变化涉及政策影响。",
                            "finding_type": "policy_impact",
                            "confidence": 0.8,
                            "cited_chunk_ids": ["kg_chunk:kg_ev:financial:news:batch:0"],
                            "supporting_edge_ids": [],
                        },
                        {
                            "summary": "产业链整合是并购重组线索",
                            "explanation": "chunk 提到并购重组市场变化涉及产业链整合。",
                            "finding_type": "industry_chain",
                            "confidence": 0.8,
                            "cited_chunk_ids": ["kg_chunk:kg_ev:financial:news:batch:0"],
                            "supporting_edge_ids": [],
                        },
                        {
                            "summary": "风险定价需要被纳入观察",
                            "explanation": "chunk 提到并购重组市场变化涉及风险定价。",
                            "finding_type": "risk_event",
                            "confidence": 0.8,
                            "cited_chunk_ids": ["kg_chunk:kg_ev:financial:news:batch:0"],
                            "supporting_edge_ids": [],
                        },
                        {
                            "summary": "资产质量影响并购判断",
                            "explanation": "chunk 提到并购重组市场变化涉及资产质量。",
                            "finding_type": "asset_signal",
                            "confidence": 0.8,
                            "cited_chunk_ids": ["kg_chunk:kg_ev:financial:news:batch:0"],
                            "supporting_edge_ids": [],
                        },
                    ],
                },
                usage={},
                session_id=None,
                duration_ms=1,
                raw_payload={},
                cache_hit=False,
            )
        if task == "kg_finding_evidence_validate" and request.metadata.get("finding_count", 0) > 1:
            self.requests.append(request)
            return LLMProxyResponse(
                text="",
                structured_output=None,
                usage={},
                session_id=None,
                duration_ms=1,
                raw_payload={},
                cache_hit=False,
            )
        return await super().generate(request)
