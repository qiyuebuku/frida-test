#!/usr/bin/env python3
"""运行 LLM 主导的 L0 Community 合并维护。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.application.services.cognitive_index_service import AssignmentCandidateOrderStore
from src.application.services.community_maintenance_service import (
    CommunityMaintenanceCommand,
    CommunityMaintenanceService,
)
from src.application.services.knowledge_service import _semantic_hybrid_retriever
from src.infrastructure.persistence.repositories.knowledge_repository_impl import KnowledgeRepositoryImpl


async def run(args: argparse.Namespace) -> dict:
    service = CommunityMaintenanceService(
        repository=KnowledgeRepositoryImpl(target=args.target),
        semantic_retriever=_semantic_hybrid_retriever(),
        candidate_order_store=AssignmentCandidateOrderStore(target=args.target),
    )
    result = await service.review_and_apply(
        CommunityMaintenanceCommand(
            adapter_name=args.adapter,
            target=args.target,
            limit=args.limit,
            min_confidence=args.min_confidence,
            dry_run=not args.apply,
        )
    )
    return result.as_dict()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", default="financial")
    parser.add_argument("--target", default="prod")
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument("--min-confidence", type=float, default=0.78)
    parser.add_argument("--apply", action="store_true", help="默认只 dry-run；加此参数才会写入 PG/Milvus/Redis ledger")
    args = parser.parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
