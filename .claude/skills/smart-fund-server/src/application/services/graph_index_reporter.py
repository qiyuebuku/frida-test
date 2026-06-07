"""LLM-backed Graph Index community report generation."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import replace
from typing import Any

from src.application.services.knowledge_llm_config import resolve_kg_llm_model
from src.domain.knowledge.graph_index import (
    GraphIndexBuildResult,
    GraphIndexCommunity,
    GraphIndexDelta,
    GraphIndexFinding,
    build_graph_index_documents,
    build_rolling_delta_index,
)
from src.domain.knowledge.schemas import CompiledEdge, CompiledNode, EvidenceChunk
from src.infrastructure.llm_proxy.service import get_llm_gateway_service
from src.infrastructure.llm_proxy.types import LLMProxyRequest, LLMProxyResponse
from src.infrastructure.observability.langfuse_tracing import langfuse_observation, langfuse_update_generation

_MAX_LLM_CONCURRENCY = 5
_L0_FINDING_BUDGET = 3
_LEAF_FINDING_BUDGET = 4
_DELTA_FINDING_BUDGET = 2
_FINDING_VALIDATION_BATCH_SIZE = 8


_COMMUNITY_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "summary", "rating", "rating_explanation", "findings"],
    "properties": {
        "title": {"type": "string", "maxLength": 120},
        "summary": {"type": "string", "maxLength": 420},
        "rating": {"type": "number"},
        "rating_explanation": {"type": "string", "maxLength": 220},
        "findings": {
            "type": "array",
            "minItems": 1,
            "maxItems": _LEAF_FINDING_BUDGET,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["summary", "explanation", "finding_type", "confidence", "cited_chunk_ids"],
                "properties": {
                    "summary": {"type": "string", "maxLength": 160},
                    "explanation": {"type": "string", "maxLength": 320},
                    "finding_type": {"type": "string"},
                    "confidence": {"type": "number"},
                    "cited_chunk_ids": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                    },
                    "supporting_edge_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        },
    },
}


_DELTA_FINDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["delta_summary", "refresh_decision", "findings"],
    "properties": {
        "delta_summary": {"type": "string", "maxLength": 360},
        "refresh_decision": {
            "type": "string",
            "enum": ["append_delta_only", "rewrite_main_summary"],
        },
        "findings": {
            "type": "array",
            "minItems": 0,
            "maxItems": _DELTA_FINDING_BUDGET,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["summary", "explanation", "finding_type", "confidence", "cited_chunk_ids"],
                "properties": {
                    "summary": {"type": "string", "maxLength": 160},
                    "explanation": {"type": "string", "maxLength": 320},
                    "finding_type": {"type": "string"},
                    "confidence": {"type": "number"},
                    "cited_chunk_ids": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                    },
                    "supporting_edge_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        },
    },
}


_BATCH_FINDING_VALIDATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["validations"],
    "properties": {
        "validations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["finding_id", "support_status", "reason"],
                "properties": {
                    "finding_id": {"type": "string"},
                    "support_status": {"type": "string", "enum": ["supported", "weak_supported", "unsupported"]},
                    "reason": {"type": "string"},
                },
            },
        }
    },
}


class GraphIndexReportGenerationError(RuntimeError):
    """Raised when the LLM community report cannot be used safely."""


class GraphIndexLLMReporter:
    """Generate bottom-up community reports and findings from KG facts."""

    def __init__(self, llm: Any | None = None):
        self._llm = llm or get_llm_gateway_service()

    async def enrich(
        self,
        *,
        graph_index: GraphIndexBuildResult,
        nodes: list[CompiledNode],
        edges: list[CompiledEdge],
        chunks: list[EvidenceChunk],
    ) -> GraphIndexBuildResult:
        node_by_id = {node.node_id: node for node in nodes}
        edge_by_id = {edge.edge_id: edge for edge in edges}
        chunk_by_id = {chunk.chunk_id: chunk for chunk in chunks}
        children_by_parent: dict[str, list[GraphIndexCommunity]] = {}
        for community in graph_index.communities:
            if community.parent_community_id:
                children_by_parent.setdefault(community.parent_community_id, []).append(community)
        child_community_ids = {community.parent_community_id for community in graph_index.communities if community.parent_community_id}
        leaf_community_ids = {community.community_id for community in graph_index.communities if community.community_id not in child_community_ids}
        report_by_community_id: dict[str, dict[str, Any]] = {}
        communities: list[GraphIndexCommunity] = []
        findings: list[GraphIndexFinding] = []
        report_semaphore = asyncio.Semaphore(_MAX_LLM_CONCURRENCY)
        for level in sorted({community.level for community in graph_index.communities}, reverse=True):
            level_communities = sorted(
                [community for community in graph_index.communities if community.level == level],
                key=lambda item: (item.projection, item.community_id),
            )

            async def generate_level_report(community: GraphIndexCommunity) -> tuple[GraphIndexCommunity, dict[str, Any]]:
                child_reports = [
                    report_by_community_id[child.community_id]
                    for child in sorted(children_by_parent.get(community.community_id, []), key=lambda item: item.community_id)
                    if child.community_id in report_by_community_id
                ]
                async with report_semaphore:
                    report = await self._generate_report(
                        community=community,
                        node_by_id=node_by_id,
                        edge_by_id=edge_by_id,
                        chunk_by_id=chunk_by_id,
                        child_reports=child_reports,
                    )
                return community, report

            level_reports = await asyncio.gather(*(generate_level_report(community) for community in level_communities))
            level_raw_findings: list[GraphIndexFinding] = []
            for community, report in level_reports:
                report_by_community_id[community.community_id] = report
                updated_community = replace(
                    community,
                    title=_accepted_report_title(community=community, report_title=report["title"]),
                    summary=_clean_text(report["summary"]),
                    metrics={
                        **community.metrics,
                        "llm_report": {
                            "rating": float(report["rating"]),
                            "rating_explanation": _clean_text(report["rating_explanation"]),
                            "task": "kg_community_report",
                        },
                    },
                )
                communities.append(updated_community)
                level_raw_findings.extend(
                    _findings_from_report(
                        community=updated_community,
                        report=report,
                        existing_edge_ids=community.member_edge_ids,
                        node_ids=community.member_node_ids,
                        chunk_by_id=chunk_by_id,
                    )
                )
            selected_findings = _select_findings_for_level(
                level_raw_findings,
                accepted_findings=findings,
                community_by_id={community.community_id: community for community in communities},
            )
            findings.extend(await self._validate_findings(findings=selected_findings, chunk_by_id=chunk_by_id))
        communities.sort(key=lambda item: (item.projection, item.level, item.parent_community_id, item.community_id))
        deltas = build_rolling_delta_index(communities=communities, findings=findings, chunks=chunks)
        deltas, delta_findings, delta_refresh_decisions = await self._generate_delta_updates(
            deltas=deltas,
            communities=communities,
            edge_by_id=edge_by_id,
            chunk_by_id=chunk_by_id,
            leaf_community_ids=leaf_community_ids,
        )
        if delta_findings:
            selected_delta_findings = _select_delta_findings(delta_findings, accepted_findings=findings)
            validated_delta_findings = await self._validate_findings(
                findings=selected_delta_findings,
                chunk_by_id=chunk_by_id,
            )
            findings.extend(validated_delta_findings)
            deltas = _attach_delta_findings(deltas, validated_delta_findings)
        documents = build_graph_index_documents(
            communities=communities,
            findings=findings,
            deltas=deltas,
            nodes=nodes,
        )
        return GraphIndexBuildResult(
            communities=communities,
            findings=findings,
            deltas=deltas,
            documents=documents,
            diagnostics={
                **graph_index.diagnostics,
                "community_report_generator": "llm",
                "report_generation_order": "bottom_up_children_first",
                "llm_reported_communities": len(communities),
                "llm_reported_findings": len(findings),
                "rolling_delta_count": len(deltas),
                "delta_finding_generator": "llm",
                "delta_finding_scope": "leaf_communities_only",
                "delta_refresh_decisions": delta_refresh_decisions,
                "finding_validation": "structural_then_batched_llm_supported_or_weak_supported_only",
                "finding_budget": {
                    "l0": _L0_FINDING_BUDGET,
                    "leaf_or_child": _LEAF_FINDING_BUDGET,
                    "delta": _DELTA_FINDING_BUDGET,
                },
            },
            unassigned_signals=graph_index.unassigned_signals,
        )

    async def enrich_delta_refresh(
        self,
        *,
        graph_index: GraphIndexBuildResult,
        nodes: list[CompiledNode],
        edges: list[CompiledEdge],
        chunks: list[EvidenceChunk],
    ) -> GraphIndexBuildResult:
        """Refresh existing communities with true rolling-delta analysis.

        This path is used when graph structure is stable enough that rebuilding
        communities is unnecessary. It still asks the LLM to summarize the new
        delta evidence and produce delta findings; if the delta changes the
        long-lived theme, the affected community report is regenerated.
        """

        node_by_id = {node.node_id: node for node in nodes}
        edge_by_id = {edge.edge_id: edge for edge in edges}
        chunk_by_id = {chunk.chunk_id: chunk for chunk in chunks}
        communities = list(graph_index.communities)
        findings = list(graph_index.findings)
        deltas = graph_index.deltas or build_rolling_delta_index(
            communities=communities,
            findings=findings,
            chunks=chunks,
        )
        child_community_ids = {
            community.parent_community_id
            for community in communities
            if community.parent_community_id
        }
        leaf_community_ids = {
            community.community_id
            for community in communities
            if community.community_id not in child_community_ids
        }
        updated_deltas, raw_delta_findings, refresh_decisions = await self._generate_delta_updates(
            deltas=deltas,
            communities=communities,
            edge_by_id=edge_by_id,
            chunk_by_id=chunk_by_id,
            leaf_community_ids=leaf_community_ids,
        )
        selected_delta_findings = _select_delta_findings(raw_delta_findings, accepted_findings=findings)
        validated_delta_findings = await self._validate_findings(
            findings=selected_delta_findings,
            chunk_by_id=chunk_by_id,
        )
        if validated_delta_findings:
            findings.extend(validated_delta_findings)
            updated_deltas = _attach_delta_findings(updated_deltas, validated_delta_findings)

        rewrite_community_ids = _ordered_unique(
            community_id
            for delta_id, decision in refresh_decisions.items()
            if decision == "rewrite_main_summary"
            for delta in updated_deltas
            if delta.delta_id == delta_id
            for community_id in delta.community_ids
        )
        if rewrite_community_ids:
            rewritten = await self._rewrite_communities_from_delta(
                community_ids=set(rewrite_community_ids),
                communities=communities,
                findings=findings,
                node_by_id=node_by_id,
                edge_by_id=edge_by_id,
                chunk_by_id=chunk_by_id,
            )
            communities = rewritten["communities"]
            findings = rewritten["findings"]
            updated_deltas = build_rolling_delta_index(
                communities=communities,
                findings=findings,
                chunks=chunks,
            )
            updated_deltas, raw_delta_findings, _ = await self._generate_delta_updates(
                deltas=updated_deltas,
                communities=communities,
                edge_by_id=edge_by_id,
                chunk_by_id=chunk_by_id,
                leaf_community_ids=leaf_community_ids,
            )
            extra_delta_findings = await self._validate_findings(
                findings=_select_delta_findings(raw_delta_findings, accepted_findings=findings),
                chunk_by_id=chunk_by_id,
            )
            findings.extend(extra_delta_findings)
            updated_deltas = _attach_delta_findings(updated_deltas, extra_delta_findings)

        documents = build_graph_index_documents(
            communities=communities,
            findings=findings,
            deltas=updated_deltas,
            nodes=nodes,
        )
        return GraphIndexBuildResult(
            communities=communities,
            findings=findings,
            deltas=updated_deltas,
            documents=documents,
            diagnostics={
                **graph_index.diagnostics,
                "community_report_generator": "llm_delta_refresh",
                "delta_finding_generator": "llm",
                "delta_refresh_decisions": dict(refresh_decisions),
                "llm_reported_findings": len(findings),
                "rolling_delta_count": len(updated_deltas),
            },
            unassigned_signals=graph_index.unassigned_signals,
        )

    async def _generate_report(
        self,
        *,
        community: GraphIndexCommunity,
        node_by_id: dict[str, CompiledNode],
        edge_by_id: dict[str, CompiledEdge],
        chunk_by_id: dict[str, EvidenceChunk],
        child_reports: list[dict[str, Any]],
    ) -> dict[str, Any]:
        prompt = _community_prompt(
            community=community,
            node_by_id=node_by_id,
            edge_by_id=edge_by_id,
            chunk_by_id=chunk_by_id,
            child_reports=child_reports,
        )
        metadata = {
            "task": "kg_community_report",
            "adapter_name": community.adapter_name,
            "projection": community.projection,
            "community_id": community.community_id,
            "community_level": community.level,
            "child_report_count": len(child_reports),
        }
        with langfuse_observation(
            name="graph_index.report_generate",
            as_type="generation",
            input={"prompt": prompt, "metadata": metadata},
            metadata=metadata,
            model=resolve_kg_llm_model("kg_community_report"),
        ):
            data, response, attempts = await self._generate_json_object(
                prompt=prompt,
                model=resolve_kg_llm_model("kg_community_report"),
                json_schema=_COMMUNITY_REPORT_SCHEMA,
                max_tokens=_community_report_max_tokens(community),
                metadata=metadata,
                error_label="community report",
                object_id=community.community_id,
            )
            langfuse_update_generation(
                output=response.structured_output or response.text,
                usage_details=response.usage or {},
                metadata={"cache_hit": response.cache_hit, "attempts": attempts},
                status_message="completed",
            )
        _validate_report(data, community.community_id)
        return data

    async def _generate_delta_findings(
        self,
        *,
        deltas: list[Any],
        communities: list[GraphIndexCommunity],
        edge_by_id: dict[str, CompiledEdge],
        chunk_by_id: dict[str, EvidenceChunk],
        leaf_community_ids: set[str],
    ) -> list[GraphIndexFinding]:
        _updated_deltas, findings, _refresh_decisions = await self._generate_delta_updates(
            deltas=deltas,
            communities=communities,
            edge_by_id=edge_by_id,
            chunk_by_id=chunk_by_id,
            leaf_community_ids=leaf_community_ids,
        )
        return findings

    async def _generate_delta_updates(
        self,
        *,
        deltas: list[GraphIndexDelta],
        communities: list[GraphIndexCommunity],
        edge_by_id: dict[str, CompiledEdge],
        chunk_by_id: dict[str, EvidenceChunk],
        leaf_community_ids: set[str],
    ) -> tuple[list[GraphIndexDelta], list[GraphIndexFinding], dict[str, str]]:
        community_by_id = {community.community_id: community for community in communities}
        semaphore = asyncio.Semaphore(_MAX_LLM_CONCURRENCY)

        async def generate_one(delta: GraphIndexDelta) -> tuple[GraphIndexDelta, list[GraphIndexFinding], str]:
            if not delta.cited_chunk_ids:
                return delta, [], "append_delta_only"
            if not set(delta.community_ids).intersection(leaf_community_ids):
                return delta, [], "append_delta_only"
            prompt = _delta_finding_prompt(
                delta=delta,
                community_by_id=community_by_id,
                edge_by_id=edge_by_id,
                chunk_by_id=chunk_by_id,
            )
            metadata = {
                "task": "kg_delta_finding",
                "adapter_name": delta.adapter_name,
                "projection": delta.projection,
                "delta_id": delta.delta_id,
                "window_name": delta.window_name,
            }
            llm_metadata = {
                "task": "kg_delta_finding",
                "adapter_name": delta.adapter_name,
                "projection": delta.projection,
                "window_name": delta.window_name,
            }
            async with semaphore:
                with langfuse_observation(
                    name="graph_index.delta_finding_generate",
                    as_type="generation",
                    input={"prompt": prompt, "metadata": metadata},
                    metadata=metadata,
                    model=resolve_kg_llm_model("kg_delta_finding"),
                ):
                    data, response, attempts = await self._generate_json_object(
                        prompt=prompt,
                        model=resolve_kg_llm_model("kg_delta_finding"),
                        json_schema=_DELTA_FINDING_SCHEMA,
                        max_tokens=700,
                        metadata=llm_metadata,
                        error_label="delta finding",
                        object_id=delta.delta_id,
                    )
                    langfuse_update_generation(
                        output=response.structured_output or response.text,
                        usage_details=response.usage or {},
                        metadata={"cache_hit": response.cache_hit, "attempts": attempts},
                        status_message="completed",
                    )
                if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
                    raise GraphIndexReportGenerationError(f"delta finding payload invalid: {delta.delta_id}")
                decision = str(data.get("refresh_decision") or "").strip()
                if decision not in {"append_delta_only", "rewrite_main_summary"}:
                    raise GraphIndexReportGenerationError(f"delta refresh_decision invalid: {delta.delta_id}")
                updated_delta = replace(
                    delta,
                    summary=_clean_text(data.get("delta_summary")) or delta.summary,
                    metrics={
                        **delta.metrics,
                        "llm_delta": {
                            "task": "kg_delta_finding",
                            "refresh_decision": decision,
                        },
                    },
                )
                findings = _findings_from_delta(
                    delta=updated_delta,
                    payload=data,
                    chunk_by_id=chunk_by_id,
                )[:_DELTA_FINDING_BUDGET]
                return updated_delta, findings, decision

        scoped_deltas = _select_delta_generation_deltas(deltas, leaf_community_ids=leaf_community_ids)
        updates = await asyncio.gather(*(generate_one(delta) for delta in scoped_deltas))
        update_by_delta_id = {delta.delta_id: delta for delta, _findings, _decision in updates}
        refresh_decisions = {delta.delta_id: decision for delta, _findings, decision in updates}
        updated_deltas = [update_by_delta_id.get(delta.delta_id, delta) for delta in deltas]
        findings: list[GraphIndexFinding] = []
        for _delta, batch, _decision in updates:
            findings.extend(batch)
        return updated_deltas, findings, refresh_decisions

    async def _rewrite_communities_from_delta(
        self,
        *,
        community_ids: set[str],
        communities: list[GraphIndexCommunity],
        findings: list[GraphIndexFinding],
        node_by_id: dict[str, CompiledNode],
        edge_by_id: dict[str, CompiledEdge],
        chunk_by_id: dict[str, EvidenceChunk],
    ) -> dict[str, list[Any]]:
        rewritten_by_id: dict[str, GraphIndexCommunity] = {}
        rewritten_findings: list[GraphIndexFinding] = []
        for community in communities:
            if community.community_id not in community_ids:
                continue
            report = await self._generate_report(
                community=community,
                node_by_id=node_by_id,
                edge_by_id=edge_by_id,
                chunk_by_id=chunk_by_id,
                child_reports=[],
            )
            updated = replace(
                community,
                title=_accepted_report_title(community=community, report_title=report["title"]),
                summary=_clean_text(report["summary"]),
                metrics={
                    **community.metrics,
                    "llm_report": {
                        "rating": float(report["rating"]),
                        "rating_explanation": _clean_text(report["rating_explanation"]),
                        "task": "kg_community_report",
                        "reason": "delta_requested_rewrite",
                    },
                },
            )
            rewritten_by_id[community.community_id] = updated
            rewritten_findings.extend(
                _findings_from_report(
                    community=updated,
                    report=report,
                    existing_edge_ids=community.member_edge_ids,
                    node_ids=community.member_node_ids,
                    chunk_by_id=chunk_by_id,
                )
            )
        selected = _select_findings_for_level(
            rewritten_findings,
            accepted_findings=[finding for finding in findings if finding.community_id not in community_ids],
            community_by_id={**{community.community_id: community for community in communities}, **rewritten_by_id},
        )
        validated = await self._validate_findings(findings=selected, chunk_by_id=chunk_by_id)
        return {
            "communities": [rewritten_by_id.get(community.community_id, community) for community in communities],
            "findings": [
                finding for finding in findings if finding.community_id not in community_ids
            ] + validated,
        }

    async def _validate_finding_batch_once(
        self,
        *,
        findings: list[GraphIndexFinding],
        chunk_by_id: dict[str, EvidenceChunk],
        semaphore: asyncio.Semaphore,
        recoverable_batch_error: bool = False,
    ) -> list[GraphIndexFinding]:
        if not findings:
            return []
        prompt = _finding_validation_prompt(findings=findings, chunk_by_id=chunk_by_id)
        metadata = {
            "task": "kg_finding_evidence_validate",
            "adapter_name": findings[0].adapter_name,
            "projection": findings[0].projection,
            "finding_count": len(findings),
            "finding_ids": [finding.finding_id for finding in findings],
        }
        async with semaphore:
            with langfuse_observation(
                name="graph_index.finding_validate",
                as_type="generation",
                input={"prompt": prompt, "metadata": metadata},
                metadata=metadata,
                model=resolve_kg_llm_model("kg_finding_evidence_validate"),
            ):
                try:
                    data, response, attempts = await self._generate_json_object(
                        prompt=prompt,
                        model=resolve_kg_llm_model("kg_finding_evidence_validate"),
                        json_schema=_BATCH_FINDING_VALIDATION_SCHEMA,
                        max_tokens=max(420, 120 + 90 * len(findings)),
                        metadata=metadata,
                        error_label="finding validation",
                        object_id=",".join(finding.finding_id for finding in findings),
                    )
                except GraphIndexReportGenerationError as exc:
                    if not recoverable_batch_error:
                        raise
                    langfuse_update_generation(
                        output={
                            "recoverable_error": str(exc),
                            "fallback": "split_validation_batch",
                            "finding_count": len(findings),
                        },
                        usage_details={},
                        metadata={"recoverable": True, "fallback": "split_validation_batch"},
                        status_message="retrying_with_smaller_batch",
                    )
                    raise
                langfuse_update_generation(
                    output=response.structured_output or response.text,
                    usage_details=response.usage or {},
                    metadata={"cache_hit": response.cache_hit, "attempts": attempts},
                    status_message="completed",
                )
        validations = data.get("validations")
        if not isinstance(validations, list):
            raise GraphIndexReportGenerationError("finding validation payload missing validations")
        validation_by_id = {
            str(item.get("finding_id") or ""): item
            for item in validations
            if isinstance(item, dict)
        }
        result: list[GraphIndexFinding] = []
        for finding in findings:
            validation = validation_by_id.get(finding.finding_id)
            if not validation:
                raise GraphIndexReportGenerationError(f"finding validation missing result: {finding.finding_id}")
            support_status = str(validation.get("support_status") or "").strip()
            if support_status not in {"supported", "weak_supported", "unsupported"}:
                raise GraphIndexReportGenerationError(
                    f"finding validation support_status invalid: {finding.finding_id}"
                )
            if support_status == "unsupported":
                continue
            result.append(
                replace(
                    finding,
                    payload={
                        **finding.payload,
                        "evidence_validation": {
                            "support_status": support_status,
                            "reason": _clean_text(validation.get("reason")),
                            "task": "kg_finding_evidence_validate",
                            "mode": "batched_llm",
                        },
                    },
                )
            )
        return result

    async def _validate_finding_batch(
        self,
        *,
        findings: list[GraphIndexFinding],
        chunk_by_id: dict[str, EvidenceChunk],
        semaphore: asyncio.Semaphore,
    ) -> list[GraphIndexFinding]:
        """Validate findings with recursive batch fallback.

        Provider-side JSON mode can occasionally return an empty response for a
        larger batch. Retrying the same large prompt is usually not enough; the
        reliable recovery is to split the batch and ask for smaller independent
        JSON objects. Single-finding failure still fails the write path instead
        of silently accepting unvalidated findings.
        """

        try:
            return await self._validate_finding_batch_once(
                findings=findings,
                chunk_by_id=chunk_by_id,
                semaphore=semaphore,
                recoverable_batch_error=len(findings) > 1,
            )
        except GraphIndexReportGenerationError:
            if len(findings) <= 1:
                raise
            midpoint = max(1, len(findings) // 2)
            left, right = await asyncio.gather(
                self._validate_finding_batch(
                    findings=findings[:midpoint],
                    chunk_by_id=chunk_by_id,
                    semaphore=semaphore,
                ),
                self._validate_finding_batch(
                    findings=findings[midpoint:],
                    chunk_by_id=chunk_by_id,
                    semaphore=semaphore,
                ),
            )
            return [*left, *right]

    async def _validate_findings(
        self,
        *,
        findings: list[GraphIndexFinding],
        chunk_by_id: dict[str, EvidenceChunk],
    ) -> list[GraphIndexFinding]:
        structurally_valid = [
            finding
            for finding in findings
            if _finding_has_valid_structure(finding, chunk_by_id=chunk_by_id)
        ]
        if not structurally_valid:
            return []
        semaphore = asyncio.Semaphore(_MAX_LLM_CONCURRENCY)
        batches = [
            structurally_valid[index : index + _FINDING_VALIDATION_BATCH_SIZE]
            for index in range(0, len(structurally_valid), _FINDING_VALIDATION_BATCH_SIZE)
        ]
        results = await asyncio.gather(
            *(
                self._validate_finding_batch(
                    findings=batch,
                    chunk_by_id=chunk_by_id,
                    semaphore=semaphore,
                )
                for batch in batches
            )
        )
        return [finding for batch in results for finding in batch]

    async def _generate_json_object(
        self,
        *,
        prompt: str,
        model: str,
        json_schema: dict[str, Any],
        max_tokens: int,
        metadata: dict[str, Any],
        error_label: str,
        object_id: str,
    ) -> tuple[dict[str, Any], LLMProxyResponse, int]:
        last_response: LLMProxyResponse | None = None
        for attempt in (1, 2):
            request_max_tokens = max_tokens if attempt == 1 else min(max_tokens * 2, 2200)
            response = await self._llm.generate(
                LLMProxyRequest(
                    prompt=prompt,
                    model=model,
                    json_schema=json_schema,
                    temperature=0.0,
                    max_tokens=request_max_tokens,
                    metadata={**metadata, "attempt": attempt},
                    use_cache=attempt == 1,
                )
            )
            last_response = response
            data = _parse_json_object_from_response(response)
            if data is not None:
                return data, response, attempt
        raise GraphIndexReportGenerationError(
            f"{error_label} is not valid JSON: {object_id}; "
            f"cache_hit={last_response.cache_hit if last_response else None}; "
            f"text_excerpt={_response_text_excerpt(last_response)}"
        )


def _community_prompt(
    *,
    community: GraphIndexCommunity,
    node_by_id: dict[str, CompiledNode],
    edge_by_id: dict[str, CompiledEdge],
    chunk_by_id: dict[str, EvidenceChunk],
    child_reports: list[dict[str, Any]],
) -> str:
    finding_budget = _community_finding_budget(community)
    entities = []
    for index, node_id in enumerate(community.member_node_ids[:40], start=1):
        node = node_by_id.get(node_id)
        if node is None:
            continue
        entities.append(
            f"{index},{node.canonical_name},{node.node_type},{_clean_text(str(node.properties or {}))[:160]}"
        )
    relationships = []
    for index, edge_id in enumerate(community.member_edge_ids[:80], start=1):
        edge = edge_by_id.get(edge_id)
        if edge is None:
            continue
        source = node_by_id.get(edge.source_node_id)
        target = node_by_id.get(edge.target_node_id)
        relationships.append(
            ",".join(
                [
                    str(index),
                    (source.canonical_name if source else edge.source_node_id),
                    (target.canonical_name if target else edge.target_node_id),
                    edge.relation_type,
                    _clean_text(str(edge.properties or {}))[:180],
                    f"{edge.confidence_score:.3f}",
                ]
            )
        )
    text_units = []
    for index, chunk_id in enumerate(community.chunk_ids[:20], start=1):
        chunk = chunk_by_id.get(chunk_id)
        if chunk is None:
            continue
        text_units.append(f"{index},{chunk.chunk_id},{_clean_text(chunk.content)[:500]}")
    child_report_lines = []
    for index, report in enumerate(child_reports[:20], start=1):
        child_report_lines.append(
            f"{index},{_clean_text(report.get('title'))[:160]},{_clean_text(report.get('summary'))[:500]}"
        )
    return f"""你是金融知识图谱分析师。请基于给定 community 内的实体、关系和证据 chunk 生成可检索的社区报告。

要求：
- 只使用输入数据，不要编造没有证据支持的事实。
- 输出 JSON，字段必须符合 schema。
- title 要短，能代表这个 community 的核心市场主题。
- 如果 level=0，title 必须是较大的主题容器，不要使用单家公司、单条新闻、单个项目或一次性事件作为标题。
- level=0 title 示例：A股并购重组、AI算力链、储能出海、半导体产业链整合、政策驱动的产业并购。
- level=0 title 反例：海辰储能西班牙建厂计划、某公司订单公告、某公司一季度业绩。
- 如果 Suggested Broad Title 非空，优先沿用或轻微改写它，不要收窄成单个公司动作。
- summary 解释 community 结构、核心实体、关系链条和可能的金融影响，控制在 420 字以内。
- findings 需要是可独立检索的洞察，每条都要说明依据，适合后续 RAG/Agent 使用。
- findings 最多输出 {finding_budget} 条；优先保留不同 finding_type，避免同义重复。
- 如果 maturity_level 是 single_source_signal，必须明确这是早期单源线索，不要写成成熟市场共识。
- 父级 community 不要重复输出子社区已覆盖的细节，只保留跨子社区综合判断。
- 每条 finding 的 summary 控制在 160 字以内，explanation 控制在 320 字以内。
- finding_type 使用简短英文枚举，例如 market_narrative、policy_impact、risk_event、industry_chain、asset_signal。
- 如果 Child Reports 非空，必须先综合子社区报告，再回到 Relationships 和 TextUnits 校验，形成父级 bottom-up 报告。

Community Metadata:
projection={community.projection}
level={community.level}
community_id={community.community_id}
maturity_level={community.metrics.get("maturity_level", "")}
support_score={community.metrics.get("support_score", "")}
evidence_count={community.metrics.get("evidence_count", "")}
source_count={community.metrics.get("source_count", "")}
strong_edge_count={community.metrics.get("strong_edge_count", "")}
topic_fingerprint_digest={community.metrics.get("topic_fingerprint_digest", "")}
suggested_broad_title={community.metrics.get("broad_title", community.title)}

Child Reports:
human_readable_id,title,summary
{chr(10).join(child_report_lines)}

Entities:
human_readable_id,title,type,description
{chr(10).join(entities)}

Relationships:
human_readable_id,source,target,type,description,weight
{chr(10).join(relationships)}

TextUnits:
human_readable_id,chunk_id,text
{chr(10).join(text_units)}
"""


def _finding_validation_prompt(
    *,
    findings: list[GraphIndexFinding],
    chunk_by_id: dict[str, EvidenceChunk],
) -> str:
    finding_lines = []
    cited_chunk_ids: list[str] = []
    for index, finding in enumerate(findings, start=1):
        cited_chunk_ids.extend(finding.cited_chunk_ids[:8])
        finding_lines.append(
            ",".join(
                [
                    str(index),
                    finding.finding_id,
                    _clean_text(finding.title)[:180],
                    _clean_text(finding.statement)[:500],
                    _clean_text(finding.finding_type)[:80],
                    " ".join(finding.cited_chunk_ids[:8]),
                ]
            )
        )
    text_units = []
    for index, chunk_id in enumerate(_ordered_unique(cited_chunk_ids)[:30], start=1):
        chunk = chunk_by_id.get(chunk_id)
        if chunk is None:
            continue
        text_units.append(f"{index},{chunk.chunk_id},{_clean_text(chunk.content)[:700]}")
    return f"""请批量校验 findings 是否被各自 cited chunks 支撑。

要求：
- 只基于 TextUnits 判断，不要引入外部知识。
- supported: finding 可由 cited chunks 明确支撑。
- weak_supported: finding 基本由 cited chunks 支撑，但需要合理综合多个句子。
- unsupported: cited chunks 不能支撑 finding。
- 对每个 finding_id 必须返回一条 validation。
- 输出 JSON，字段为 validations。每条 validation 包含 finding_id、support_status 和 reason。

Findings:
human_readable_id,finding_id,title,statement,finding_type,cited_chunk_ids
{chr(10).join(finding_lines)}

TextUnits:
human_readable_id,chunk_id,text
{chr(10).join(text_units)}
    """


def _delta_finding_prompt(
    *,
    delta: Any,
    community_by_id: dict[str, GraphIndexCommunity],
    edge_by_id: dict[str, CompiledEdge],
    chunk_by_id: dict[str, EvidenceChunk],
) -> str:
    communities = []
    for index, community_id in enumerate(delta.community_ids[:12], start=1):
        community = community_by_id.get(community_id)
        if community is None:
            continue
        communities.append(f"{index},{community.community_id},{community.title},{community.summary[:500]}")
    relationships = []
    for index, edge_id in enumerate(delta.supporting_edge_ids[:40], start=1):
        edge = edge_by_id.get(edge_id)
        if edge is None:
            continue
        relationships.append(
            ",".join(
                [
                    str(index),
                    edge.edge_id,
                    edge.source_node_id,
                    edge.target_node_id,
                    edge.relation_type,
                    _clean_text(str(edge.properties or {}))[:180],
                ]
            )
        )
    text_units = []
    for index, chunk_id in enumerate(delta.cited_chunk_ids[:24], start=1):
        chunk = chunk_by_id.get(chunk_id)
        if chunk is None:
            continue
        text_units.append(f"{index},{chunk.chunk_id},{_clean_text(chunk.content)[:700]}")
    window_meaning = _delta_window_meaning(str(delta.window_name))
    return f"""你是金融知识图谱分析师。请基于 rolling delta 窗口内的证据生成近期变化 finding。

要求：
- 只使用输入的 TextUnits、Relationships 和 Community Context。
- 每条 finding 必须引用 TextUnits 中真实存在的 chunk_id。
- 不要把长期 report 当作证据；长期 report 只能帮助理解主题。
- 如果窗口内证据不足，可以返回空 findings 数组。
- delta_summary 只总结本窗口内证据体现的新增、增强或仍活跃变化，不能复述长期 report。
- refresh_decision 只能二选一：
  - append_delta_only: 近期变化不改变长期社区主叙事，只追加 delta。
  - rewrite_main_summary: 近期证据足以改变长期社区主 summary，需要重写主报告。
- findings 最多输出 {_DELTA_FINDING_BUDGET} 条；只输出本窗口真正新增或增强的变化，不要重复长期 report。
- 每条 finding 的 summary 控制在 160 字以内，explanation 控制在 320 字以内。
- 输出 JSON，字段必须符合 schema。

Delta Context:
window={delta.window_name}
window_meaning={window_meaning}
projection={delta.projection}

Community Context:
human_readable_id,community_id,title,summary
{chr(10).join(communities)}

Relationships:
human_readable_id,edge_id,source_node_id,target_node_id,type,description
{chr(10).join(relationships)}

TextUnits:
human_readable_id,chunk_id,text
{chr(10).join(text_units)}
"""


def _attach_delta_findings(
    deltas: list[GraphIndexDelta],
    findings: list[GraphIndexFinding],
) -> list[GraphIndexDelta]:
    if not findings:
        return deltas
    finding_ids_by_delta_id: dict[str, list[str]] = {}
    for finding in findings:
        delta_id = str((finding.payload or {}).get("delta_id") or "").strip()
        if delta_id:
            finding_ids_by_delta_id.setdefault(delta_id, []).append(finding.finding_id)
    return [
        replace(
            delta,
            finding_ids=_ordered_unique([*delta.finding_ids, *finding_ids_by_delta_id.get(delta.delta_id, [])]),
        )
        for delta in deltas
    ]


def _delta_window_meaning(window_name: str) -> str:
    if window_name == "rolling_24h":
        return "过去 24 小时内的突发变化、短期冲击和风险线索"
    if window_name == "rolling_7d":
        return "过去 7 天内的近期叙事变化、风险聚集和影响扩散"
    if window_name == "rolling_30d":
        return "过去 30 天内的阶段性变化、结构性趋势和主题增强"
    return "rolling delta 窗口内的近期变化"


def _select_findings_for_level(
    findings: list[GraphIndexFinding],
    *,
    accepted_findings: list[GraphIndexFinding],
    community_by_id: dict[str, GraphIndexCommunity],
) -> list[GraphIndexFinding]:
    by_community: dict[str, list[GraphIndexFinding]] = {}
    accepted_signatures = {_finding_signature(finding) for finding in accepted_findings}
    for finding in sorted(findings, key=_finding_priority_key):
        signature = _finding_signature(finding)
        if signature in accepted_signatures:
            continue
        if _has_similar_accepted_finding(finding, accepted_findings):
            continue
        by_community.setdefault(finding.community_id, []).append(finding)
        accepted_signatures.add(signature)

    selected: list[GraphIndexFinding] = []
    for community_id, items in by_community.items():
        community = community_by_id.get(community_id)
        budget = _finding_budget_for_community(community)
        selected.extend(_dedupe_findings(items)[:budget])
    return selected


def _community_finding_budget(community: GraphIndexCommunity) -> int:
    if community.level <= 0:
        return _L0_FINDING_BUDGET
    return _LEAF_FINDING_BUDGET


def _community_report_max_tokens(community: GraphIndexCommunity) -> int:
    if community.level <= 0:
        return 1050
    return 1150


def _select_delta_generation_deltas(deltas: list[Any], *, leaf_community_ids: set[str]) -> list[Any]:
    """Select one narrowest useful delta per leaf community for LLM generation.

    Rolling delta documents may exist for multiple windows. LLM finding
    generation should not repeat the same evidence across 24h/7d/30d windows;
    wider-window delta targets can aggregate existing community findings.
    """

    window_order = {"rolling_24h": 0, "rolling_7d": 1, "rolling_30d": 2}
    by_community: dict[str, list[Any]] = {}
    for delta in deltas:
        community_ids = [community_id for community_id in delta.community_ids if community_id in leaf_community_ids]
        if not community_ids or not delta.cited_chunk_ids:
            continue
        for community_id in community_ids:
            by_community.setdefault(community_id, []).append(delta)

    selected: list[Any] = []
    for community_id, items in sorted(by_community.items()):
        del community_id
        items.sort(
            key=lambda delta: (
                window_order.get(str(delta.window_name), 99),
                -len(delta.cited_chunk_ids),
                str(delta.delta_id),
            )
        )
        selected.append(items[0])
    return selected


def _select_delta_findings(
    findings: list[GraphIndexFinding],
    *,
    accepted_findings: list[GraphIndexFinding],
) -> list[GraphIndexFinding]:
    by_community: dict[str, list[GraphIndexFinding]] = {}
    accepted_signatures = {_finding_signature(finding) for finding in accepted_findings}
    for finding in sorted(findings, key=_finding_priority_key):
        if _finding_signature(finding) in accepted_signatures:
            continue
        if _has_similar_accepted_finding(finding, accepted_findings):
            continue
        by_community.setdefault(finding.community_id, []).append(finding)
        accepted_signatures.add(_finding_signature(finding))
    selected: list[GraphIndexFinding] = []
    for items in by_community.values():
        selected.extend(_dedupe_findings(items)[:_DELTA_FINDING_BUDGET])
    return selected


def _finding_budget_for_community(community: GraphIndexCommunity | None) -> int:
    if community is None:
        return _LEAF_FINDING_BUDGET
    if community.level <= 0:
        return _L0_FINDING_BUDGET
    return _LEAF_FINDING_BUDGET


def _dedupe_findings(findings: list[GraphIndexFinding]) -> list[GraphIndexFinding]:
    result: list[GraphIndexFinding] = []
    seen: set[str] = set()
    for finding in sorted(findings, key=_finding_priority_key):
        signature = _finding_signature(finding)
        if signature in seen:
            continue
        if _has_similar_accepted_finding(finding, result):
            continue
        seen.add(signature)
        result.append(finding)
    return result


def _finding_priority_key(finding: GraphIndexFinding) -> tuple[float, int, str]:
    return (-float(finding.confidence), -len(finding.supporting_edge_ids), finding.finding_id)


def _finding_signature(finding: GraphIndexFinding) -> str:
    normalized = _normalize_finding_text(" ".join([finding.finding_type, finding.title, finding.statement]))
    return _digest([finding.community_id, finding.finding_type, normalized[:160]])


def _has_similar_accepted_finding(finding: GraphIndexFinding, accepted_findings: list[GraphIndexFinding]) -> bool:
    finding_terms = _finding_terms(finding)
    if not finding_terms:
        return False
    for accepted in accepted_findings:
        if finding.finding_type != accepted.finding_type:
            continue
        accepted_terms = _finding_terms(accepted)
        if not accepted_terms:
            continue
        overlap = len(finding_terms.intersection(accepted_terms)) / max(1, min(len(finding_terms), len(accepted_terms)))
        if overlap >= 0.72:
            return True
    return False


def _finding_terms(finding: GraphIndexFinding) -> set[str]:
    text = _normalize_finding_text(" ".join([finding.title, finding.statement]))
    if not text:
        return set()
    terms = {text[index : index + 3] for index in range(0, max(0, len(text) - 2))}
    terms.update(token for token in re.split(r"[^0-9A-Za-z\u4e00-\u9fff]+", text) if len(token) >= 2)
    return {term for term in terms if term.strip()}


def _normalize_finding_text(text: str) -> str:
    return re.sub(r"\s+", "", _clean_text(text).lower())


def _finding_has_valid_structure(finding: GraphIndexFinding, *, chunk_by_id: dict[str, EvidenceChunk]) -> bool:
    if not _clean_text(finding.title) or not _clean_text(finding.statement):
        return False
    if not finding.cited_chunk_ids:
        return False
    if not any(chunk_id in chunk_by_id for chunk_id in finding.cited_chunk_ids):
        return False
    if not (0.0 <= float(finding.confidence) <= 1.0):
        return False
    return True


def _findings_from_report(
    *,
    community: GraphIndexCommunity,
    report: dict[str, Any],
    existing_edge_ids: list[str],
    node_ids: list[str],
    chunk_by_id: dict[str, EvidenceChunk],
) -> list[GraphIndexFinding]:
    findings: list[GraphIndexFinding] = []
    valid_chunk_ids = set(community.chunk_ids)
    for index, item in enumerate(report["findings"], start=1):
        summary = _clean_text(item["summary"])
        explanation = _clean_text(item["explanation"])
        finding_type = _clean_text(item["finding_type"]) or "community_finding"
        cited_chunk_ids = _ordered_unique(
            str(chunk_id)
            for chunk_id in item.get("cited_chunk_ids", [])
            if str(chunk_id) in valid_chunk_ids
        )
        if not cited_chunk_ids:
            continue
        supporting_edge_ids = _ordered_unique(
            str(edge_id)
            for edge_id in item.get("supporting_edge_ids", [])
            if str(edge_id) in set(existing_edge_ids)
        ) or existing_edge_ids[:12]
        digest = _digest([community.community_id, community.version_id, str(index), summary, explanation])
        findings.append(
            GraphIndexFinding(
                finding_id=f"kg_finding:{digest}",
                community_id=community.community_id,
                adapter_name=community.adapter_name,
                projection=community.projection,
                finding_type=finding_type,
                title=summary[:240],
                statement=explanation,
                cited_chunk_ids=cited_chunk_ids,
                cited_evidence_ids=_evidence_ids_from_chunk_ids(cited_chunk_ids, chunk_by_id=chunk_by_id),
                supporting_edge_ids=supporting_edge_ids,
                node_ids=node_ids,
                confidence=max(0.0, min(1.0, float(item["confidence"]))),
                version=community.version_id,
                payload={
                    "community_level": community.level,
                    "source": "llm_community_report",
                    "report_rating": report["rating"],
                    "report_rating_explanation": report["rating_explanation"],
                },
            )
        )
    return findings


def _findings_from_delta(
    *,
    delta: Any,
    payload: dict[str, Any],
    chunk_by_id: dict[str, EvidenceChunk],
) -> list[GraphIndexFinding]:
    valid_chunk_ids = set(delta.cited_chunk_ids)
    valid_edge_ids = set(delta.supporting_edge_ids)
    findings: list[GraphIndexFinding] = []
    for index, item in enumerate(payload.get("findings") or [], start=1):
        if not isinstance(item, dict):
            continue
        cited_chunk_ids = _ordered_unique(
            str(chunk_id)
            for chunk_id in item.get("cited_chunk_ids", [])
            if str(chunk_id) in valid_chunk_ids
        )
        if not cited_chunk_ids:
            continue
        summary = _clean_text(item.get("summary"))
        explanation = _clean_text(item.get("explanation"))
        if not summary or not explanation:
            continue
        supporting_edge_ids = _ordered_unique(
            str(edge_id)
            for edge_id in item.get("supporting_edge_ids", [])
            if str(edge_id) in valid_edge_ids
        ) or delta.supporting_edge_ids[:12]
        digest = _digest([delta.delta_id, str(index), summary, explanation])
        community_id = delta.community_ids[0] if delta.community_ids else ""
        findings.append(
            GraphIndexFinding(
                finding_id=f"kg_finding:delta:{digest}",
                community_id=community_id,
                adapter_name=delta.adapter_name,
                projection=delta.projection,
                finding_type=_clean_text(item.get("finding_type")) or "delta_finding",
                title=summary[:240],
                statement=explanation,
                cited_chunk_ids=cited_chunk_ids,
                cited_evidence_ids=_evidence_ids_from_chunk_ids(cited_chunk_ids, chunk_by_id=chunk_by_id),
                supporting_edge_ids=supporting_edge_ids,
                node_ids=delta.node_ids,
                confidence=max(0.0, min(1.0, float(item.get("confidence") or 0.0))),
                version=delta.version,
                payload={
                    "source": "llm_delta_finding",
                    "delta_id": delta.delta_id,
                    "window_name": delta.window_name,
                },
            )
        )
    return findings


def _validate_report(data: Any, community_id: str) -> None:
    if not isinstance(data, dict):
        raise GraphIndexReportGenerationError(f"community report must be object: {community_id}")
    for key in ("title", "summary", "rating", "rating_explanation", "findings"):
        if key not in data:
            raise GraphIndexReportGenerationError(f"community report missing {key}: {community_id}")
    if not isinstance(data["findings"], list) or not data["findings"]:
        raise GraphIndexReportGenerationError(f"community report missing findings: {community_id}")
    for item in data["findings"]:
        if not isinstance(item, dict):
            raise GraphIndexReportGenerationError(f"community finding must be object: {community_id}")
        for key in ("summary", "explanation", "finding_type", "confidence", "cited_chunk_ids"):
            if key not in item:
                raise GraphIndexReportGenerationError(f"community finding missing {key}: {community_id}")
        if not isinstance(item["cited_chunk_ids"], list) or not item["cited_chunk_ids"]:
            raise GraphIndexReportGenerationError(f"community finding missing cited chunks: {community_id}")


def _accepted_report_title(*, community: GraphIndexCommunity, report_title: Any) -> str:
    candidate = _clean_text(report_title)[:240]
    fallback = _clean_text(community.metrics.get("broad_title"))[:240] or community.title
    if not candidate:
        return fallback
    if int(community.level) == 0 and _is_over_specific_l0_report_title(candidate):
        return fallback
    return candidate


def _is_over_specific_l0_report_title(title: str) -> bool:
    text = _clean_text(title)
    if not text:
        return True
    if len(text) > 24:
        return True
    return any(marker in text for marker in _L0_REPORT_TITLE_SPECIFIC_MARKERS)


_L0_REPORT_TITLE_SPECIFIC_MARKERS = {
    "计划",
    "项目",
    "建厂",
    "签约",
    "订单",
    "中标",
    "公告",
    "业绩",
    "一季度",
    "二季度",
    "三季度",
    "四季度",
    "上半年",
    "下半年",
    "发布",
    "披露",
    "增持",
    "减持",
    "股东",
    "董事",
}


def _parse_json_object_from_response(response: LLMProxyResponse) -> dict[str, Any] | None:
    if isinstance(response.structured_output, dict):
        return response.structured_output
    if isinstance(response.structured_output, str):
        return _parse_json_object(response.structured_output)
    return _parse_json_object(response.text)


def _parse_json_object(text: str | None) -> dict[str, Any] | None:
    candidate = str(text or "").strip()
    if not candidate:
        return None
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _response_text_excerpt(response: LLMProxyResponse | None) -> str:
    if response is None:
        return ""
    text = response.text
    if not text and response.structured_output is not None:
        text = str(response.structured_output)
    return _clean_text(text)[:240]


def _digest(parts: list[str]) -> str:
    data = "\n".join(str(part) for part in parts)
    return __import__("hashlib").sha256(data.encode("utf-8")).hexdigest()[:16]


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())


def _ordered_unique(values: Any) -> list[str]:
    result: list[str] = []
    for value in values or []:
        item = str(value or "").strip()
        if item and item not in result:
            result.append(item)
    return result


def _evidence_ids_from_chunk_ids(
    chunk_ids: list[str],
    *,
    chunk_by_id: dict[str, EvidenceChunk] | None = None,
) -> list[str]:
    evidence_ids: list[str] = []
    for chunk_id in chunk_ids:
        chunk = (chunk_by_id or {}).get(chunk_id)
        if chunk is not None and chunk.evidence_id:
            evidence_id = chunk.evidence_id
            if evidence_id not in evidence_ids:
                evidence_ids.append(evidence_id)
            continue
        if chunk_id.startswith("kg_chunk:"):
            body = chunk_id[len("kg_chunk:") :]
            evidence_id = body.rsplit(":", 1)[0]
            if evidence_id and evidence_id not in evidence_ids:
                evidence_ids.append(evidence_id)
    return evidence_ids
