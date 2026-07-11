#!/usr/bin/env python3
"""使用真实 Evidence Chunk 验证原子 Cognitive Card 第一阶段链路。

默认只调用 LLM 并输出结果，不写数据库。传入 ``--persist`` 后会替换对应
Evidence 的 Card manifest，并将完整 Card upsert 到 Milvus。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.application.services.atomic_cognitive_card_service import (  # noqa: E402
    AtomicCognitiveCardExtractor,
    AtomicCognitiveCardStageService,
)
from src.domain.knowledge.chunking import evidence_content_for_chunking  # noqa: E402
from src.domain.knowledge.schemas import EvidenceChunk  # noqa: E402
from src.infrastructure.connections import get_session  # noqa: E402
from src.infrastructure.observability.langfuse_tracing import (  # noqa: E402
    langfuse_flush,
    langfuse_observation,
    langfuse_propagation_context,
    langfuse_update_span,
)
from src.infrastructure.persistence.models.knowledge import (  # noqa: E402
    KnowledgeEvidence,
    KnowledgeEvidenceChunk,
)
from src.infrastructure.persistence.repositories.knowledge_repository_impl import (  # noqa: E402
    KnowledgeRepositoryImpl,
)
from src.infrastructure.vector_store.semantic_hybrid_retriever import (  # noqa: E402
    MilvusSemanticHybridRetriever,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="原子 Cognitive Card + Relation Probe 全流程验证")
    parser.add_argument("--adapter", default="financial")
    parser.add_argument("--target", choices=["prod", "test"], default="prod")
    parser.add_argument("--limit", type=int, default=3, help="最多处理多少个 Chunk，范围 1-20")
    parser.add_argument("--chunk-id", action="append", default=[], help="只处理指定 Chunk，可重复传入")
    parser.add_argument("--source-id", action="append", default=[], help="只处理指定来源 ID，可重复传入")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--model", default="", help="临时指定 Card 模型；默认使用项目配置")
    parser.add_argument("--persist", action="store_true", help="写入 PG manifest 和 Milvus；默认只观察")
    parser.add_argument("--session-id", default="", help="指定 Langfuse session id")
    parser.add_argument("--output", default="", help="结果 JSON 路径；默认写入 /tmp")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def load_chunks(args: argparse.Namespace) -> list[EvidenceChunk]:
    limit = max(1, min(20, int(args.limit)))
    with get_session(args.target) as session:
        query = (
            select(KnowledgeEvidenceChunk, KnowledgeEvidence)
            .join(KnowledgeEvidence, KnowledgeEvidence.evidence_id == KnowledgeEvidenceChunk.evidence_id)
            .where(
                KnowledgeEvidenceChunk.adapter_name == args.adapter,
                KnowledgeEvidence.status == "active",
            )
        )
        if args.chunk_id:
            query = query.where(KnowledgeEvidenceChunk.chunk_id.in_(list(dict.fromkeys(args.chunk_id))))
        if args.source_id:
            query = query.where(KnowledgeEvidence.source_id.in_(list(dict.fromkeys(args.source_id))))
        rows = session.execute(
            query.order_by(
                KnowledgeEvidence.updated_at.desc().nullslast(),
                KnowledgeEvidenceChunk.chunk_index,
            ).limit(limit)
        ).all()

        result: list[EvidenceChunk] = []
        for chunk, evidence in rows:
            full_text = evidence_content_for_chunking(evidence.content, evidence.payload or {})
            start = 0 if chunk.start_offset is None else max(0, min(chunk.start_offset, len(full_text)))
            end = len(full_text) if chunk.end_offset is None else max(start, min(chunk.end_offset, len(full_text)))
            content = full_text[start:end].strip()
            if not content:
                continue
            result.append(
                EvidenceChunk(
                    chunk_id=chunk.chunk_id,
                    adapter_name=chunk.adapter_name,
                    evidence_id=chunk.evidence_id,
                    content=content,
                    chunk_index=chunk.chunk_index,
                    start_offset=chunk.start_offset,
                    end_offset=chunk.end_offset,
                    previous_chunk_id=chunk.previous_chunk_id,
                    next_chunk_id=chunk.next_chunk_id,
                    text_hash=chunk.text_hash or "",
                    chunker_version=chunk.chunker_version or "",
                    payload={
                        **(evidence.payload or {}),
                        "source_type": evidence.source_type,
                        "source_id": evidence.source_id,
                        "evidence_type": evidence.evidence_type,
                        "published_at": (evidence.payload or {}).get("published_at") or "",
                    },
                )
            )
        return result


def card_output(card) -> dict:
    return {
        "cognitive_card_id": card.cognitive_card_id,
        "source_id": card.source_id,
        "evidence_id": card.evidence_id,
        "primary_chunk_id": card.primary_chunk_id,
        "summary": card.summary,
        "focus_evidence_refs": card.focus_evidence_refs,
        "focus_span_offsets": card.focus_span_offsets,
        "factual_anchors": card.factual_anchors,
        "relation_probes": [probe.as_dict() for probe in card.relation_probes],
        "schema_version": card.schema_version,
        "generator_version": card.generator_version,
    }


async def run(args: argparse.Namespace) -> dict:
    chunks = load_chunks(args)
    if not chunks:
        raise RuntimeError("没有找到符合条件的 active Evidence Chunk")

    extractor = AtomicCognitiveCardExtractor(
        model=args.model or None,
        concurrency=max(1, min(8, int(args.concurrency))),
    )
    repository = KnowledgeRepositoryImpl(target=args.target)

    class _DryRunRetriever:
        async def upsert_semantic_documents(self, **_kwargs):
            raise AssertionError("dry-run 不应写 Milvus")

        async def delete_documents_by_role(self, **_kwargs):
            raise AssertionError("dry-run 不应删除 Milvus target")

    retriever = MilvusSemanticHybridRetriever() if args.persist else _DryRunRetriever()
    service = AtomicCognitiveCardStageService(
        repository=repository,
        semantic_retriever=retriever,
        extractor=extractor,
    )
    stage = await service.refresh(
        adapter_name=args.adapter,
        target=args.target,
        kg_version=f"atomic-card-validation:{datetime.now().isoformat()}",
        changed_chunks=chunks,
        persist=args.persist,
    )
    return {
        "status": stage.status,
        "persisted": args.persist,
        "input_chunks": [
            {
                "chunk_id": chunk.chunk_id,
                "evidence_id": chunk.evidence_id,
                "source_id": chunk.payload.get("source_id") or "",
                "title": chunk.payload.get("title") or "",
                "content": chunk.content,
            }
            for chunk in chunks
        ],
        "cards": [card_output(card) for card in stage.cards],
        "diagnostics": stage.diagnostics,
    }


async def main_async(args: argparse.Namespace) -> None:
    session_id = args.session_id or f"atomic-card-demo-{uuid4().hex[:12]}"
    trace_input = {
        "adapter": args.adapter,
        "target": args.target,
        "limit": max(1, min(20, int(args.limit))),
        "chunk_ids": args.chunk_id,
        "source_ids": args.source_id,
        "model": args.model or "project_default",
        "persist": args.persist,
        "session_id": session_id,
    }
    with langfuse_propagation_context(
        trace_name="kg.atomic_card.validation_demo",
        session_id=session_id,
        tags=["kg", "atomic-card", "validation"],
        metadata=trace_input,
    ):
        with langfuse_observation(
            name="kg.atomic_card.validation_demo",
            as_type="chain",
            input=trace_input,
            metadata=trace_input,
        ):
            result = await run(args)
            output_path = Path(args.output) if args.output else Path(
                f"/tmp/atomic_cognitive_card_validation_{session_id}.json"
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            langfuse_update_span(
                output={
                    "status": result["status"],
                    "cards": len(result["cards"]),
                    "diagnostics": result["diagnostics"],
                    "output_file": str(output_path),
                },
                status_message="completed",
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            print(f"\nLangfuse trace: kg.atomic_card.validation_demo")
            print(f"Session ID: {session_id}")
            print(f"结果文件: {output_path}")


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        asyncio.run(main_async(args))
    finally:
        langfuse_flush()


if __name__ == "__main__":
    main()
