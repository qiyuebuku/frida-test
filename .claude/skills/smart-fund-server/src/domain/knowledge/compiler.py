"""Generic knowledge compiler."""

from __future__ import annotations

import asyncio
import logging
import time
from uuid import uuid4

from src.domain.knowledge.adapter import DomainAdapter
from src.domain.knowledge.evidence import EvidenceManager
from src.domain.knowledge.relation_compiler import RelationCompiler
from src.domain.knowledge.retrieval_profile import profile_span
from src.domain.knowledge.repositories import KnowledgeRepository
from src.domain.knowledge.resolver import EntityResolver
from src.domain.knowledge.schemas import (
    CompileResult,
    EdgeDraft,
    EvidenceDraft,
    FailedRecord,
    KnowledgeInput,
    NodeDraft,
    ValidationIssue,
)
from src.domain.knowledge.source_record import validate_source_record_contract

logger = logging.getLogger(__name__)


class KnowledgeCompiler:
    def __init__(
        self,
        repository: KnowledgeRepository | None = None,
        entity_resolver: EntityResolver | None = None,
        relation_compiler: RelationCompiler | None = None,
        evidence_manager: EvidenceManager | None = None,
        concurrency: int = 1,
    ):
        self.repository = repository
        self.entity_resolver = entity_resolver or EntityResolver()
        self.relation_compiler = relation_compiler or RelationCompiler()
        self.evidence_manager = evidence_manager or EvidenceManager()
        # Controls per-record extraction concurrency. Keep it aligned with the
        # downstream LLM proxy/pool limit when adapters call remote models.
        self.concurrency = max(1, int(concurrency))

    async def compile(self, adapter: DomainAdapter, inputs: list[KnowledgeInput]) -> CompileResult:
        run_id = f"kg_run:{adapter.spec.name}:{uuid4()}"
        version = adapter.spec.version
        failed_records: list[FailedRecord] = []
        warnings: list[ValidationIssue] = []

        total = len(inputs)
        logger.info(
            "[compile] adapter=%s start total_records=%d concurrency=%d run_id=%s",
            adapter.spec.name, total, self.concurrency, run_id,
        )

        sem = asyncio.Semaphore(self.concurrency)
        completed = [0]
        run_t0 = time.monotonic()

        async def process_one(item: KnowledgeInput):
            async with sem:
                t0 = time.monotonic()
                try:
                    validate_source_record_contract(item)
                    item_evidence = adapter.extract_evidence_drafts(item)
                    item_nodes = await adapter.extract_node_drafts(item)
                    item_edges = await adapter.extract_edge_drafts(item, item_nodes)
                except Exception as exc:
                    completed[0] += 1
                    logger.warning(
                        "[compile] [%d/%d] FAILED source_type=%s source_id=%s reason=%s",
                        completed[0], total, item.source_type, item.source_id, exc,
                    )
                    return None, FailedRecord(
                        source_type=item.source_type,
                        source_id=item.source_id,
                        reason=str(exc),
                    )
                completed[0] += 1
                logger.debug(
                    "[compile] [%d/%d] ok source_type=%s source_id=%s nodes=%d edges=%d duration=%.1fs",
                    completed[0], total, item.source_type, item.source_id,
                    len(item_nodes), len(item_edges), time.monotonic() - t0,
                )
                return (item_evidence, item_nodes, item_edges), None

        results = await asyncio.gather(*(process_one(item) for item in inputs))

        node_drafts: list[NodeDraft] = []
        edge_drafts: list[EdgeDraft] = []
        evidence_drafts: list[EvidenceDraft] = []
        for success, fail in results:
            if fail is not None:
                failed_records.append(fail)
                continue
            item_evidence, item_nodes, item_edges = success
            evidence_drafts.extend(item_evidence)
            node_drafts.extend(item_nodes)
            edge_drafts.extend(item_edges)

        logger.info(
            "[compile] adapter=%s extraction done: nodes=%d edges=%d evidence=%d failed=%d total_duration=%.1fs",
            adapter.spec.name, len(node_drafts), len(edge_drafts), len(evidence_drafts),
            len(failed_records), time.monotonic() - run_t0,
        )

        with profile_span("kg_compile.evidence_compile", drafts=len(evidence_drafts)):
            evidence_result = self.evidence_manager.compile(
                adapter_name=adapter.spec.name,
                version=version,
                drafts=evidence_drafts,
            )
        with profile_span("kg_compile.node_resolve", drafts=len(node_drafts)):
            node_result = self.entity_resolver.resolve(
                adapter_spec=adapter.spec,
                version=version,
                drafts=node_drafts,
            )
        with profile_span("kg_compile.edge_compile", drafts=len(edge_drafts)):
            edge_result = self.relation_compiler.compile(
                adapter_spec=adapter.spec,
                version=version,
                drafts=edge_drafts,
                draft_by_ref=node_result.draft_by_ref,
                node_id_by_ref=node_result.node_id_by_ref,
                evidence_ref_map=evidence_result.ref_map,
            )

        failed_records.extend(node_result.failed_records)
        failed_records.extend(edge_result.failed_records)
        warnings.extend(node_result.warnings)
        warnings.extend(edge_result.warnings)

        result = CompileResult(
            run_id=run_id,
            adapter_name=adapter.spec.name,
            adapter_version=adapter.spec.version,
            version=version,
            nodes=node_result.nodes,
            edges=edge_result.edges,
            evidence=evidence_result.evidence,
            failed_records=failed_records,
            warnings=warnings,
        )
        with profile_span(
            "kg_compile.persist",
            nodes=len(result.nodes),
            edges=len(result.edges),
            evidence=len(result.evidence),
            failed=len(result.failed_records),
        ):
            self._persist_result(result, input_count=len(inputs))
        return result

    def _persist_result(self, result: CompileResult, *, input_count: int) -> None:
        if self.repository is None:
            return
        with profile_span("kg_compile.persist.create_run", run_id=result.run_id):
            self.repository.create_compilation_run(
                {
                    "run_id": result.run_id,
                    "adapter_name": result.adapter_name,
                    "adapter_version": result.adapter_version,
                    "input_count": input_count,
                    "status": "running",
                }
            )
        with profile_span("kg_compile.persist.upsert_nodes", nodes=len(result.nodes)):
            self.repository.upsert_nodes(result.nodes)
        with profile_span("kg_compile.persist.upsert_evidence", evidence=len(result.evidence)):
            self.repository.upsert_evidence(result.evidence)
        with profile_span("kg_compile.persist.upsert_edges", edges=len(result.edges)):
            self.repository.upsert_edges(result.edges)
        with profile_span("kg_compile.persist.finish_run", run_id=result.run_id):
            self.repository.finish_compilation_run(
                result.run_id,
                {
                    "status": "success" if not result.failed_records else "partial",
                    "input_count": input_count,
                    "node_count": len(result.nodes),
                    "edge_count": len(result.edges),
                    "evidence_count": len(result.evidence),
                    "failed_count": len(result.failed_records),
                },
            )
