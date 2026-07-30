#!/usr/bin/env python3
"""Demonstrate the current KG retrieval experience.

This script is intentionally a product/demo script, not a strict benchmark.
Default mode is read-only: it shows current KG/Milvus status and runs several
representative research-context queries through the deterministic fast path.

Write paths are explicit:
- --seed-baseline compiles the controlled baseline records from script 2.
- --run-incremental-task submits and executes the trackable incremental task.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import logging
import sys
from pathlib import Path
from pprint import pprint
from typing import Any

from sqlalchemy import text


def _project_root() -> Path:
    for workspace_root in Path(__file__).resolve().parents:
        candidate = workspace_root / "smart-fund-server"
        if (candidate / "src").is_dir():
            return candidate
    raise RuntimeError("cannot locate smart-fund-server project root")


PROJECT_ROOT = _project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.application.dto.knowledge_dto import (  # noqa: E402
    KnowledgeCompileCommand,
    KnowledgeIncrementalRefreshCommand,
    KnowledgeResearchContextCommand,
)
from src.application.services.knowledge_service import create_knowledge_service  # noqa: E402
from src.infrastructure.connections import get_session  # noqa: E402
from src.infrastructure.vector_store.milvus_hybrid_store import MilvusHybridStore  # noqa: E402


DEMO_QUERIES = [
    "宁德时代 300750 最近受哪些事件影响",
    "并购重组对哪些行业有影响",
    "低利率环境利好什么资产和行业",
    "中东冲突影响哪些资产和行业",
    "海外工厂投产会带动哪些产业链机会",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Demonstrate KG usage and retrieval effects.")
    parser.add_argument("--target", default="prod", choices=["prod", "test"])
    parser.add_argument("--adapter", default="financial")
    parser.add_argument("--query", action="append", help="extra query; can be repeated")
    parser.add_argument("--seed-baseline", action="store_true", help="compile controlled baseline records")
    parser.add_argument("--dry-run", action="store_true", help="compile without writing when --seed-baseline is used")
    parser.add_argument("--run-incremental-task", action="store_true", help="submit and run the trackable incremental task")
    parser.add_argument("--codes", nargs="*", default=["300750"], help="codes for incremental task demo")
    parser.add_argument("--stock-limit", type=int, default=20)
    parser.add_argument("--news-limit", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--max-chars", type=int, default=6000)
    parser.add_argument("--show-context", action="store_true", help="print context_text preview")
    parser.add_argument("--status-only", action="store_true", help="only print health/counts/Milvus status")
    parser.add_argument("--json", action="store_true", help="print machine-readable final summary")
    return parser.parse_args()


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    logging.getLogger("src.domain.knowledge.retrieval").setLevel(logging.INFO)
    logging.getLogger("src.application.services.knowledge_service").setLevel(logging.INFO)
    logging.getLogger("src.infrastructure.llm_proxy.service").setLevel(logging.INFO)


async def main() -> None:
    args = parse_args()
    configure_logging()

    service = create_knowledge_service(target=args.target)
    summary: dict[str, Any] = {
        "target": args.target,
        "adapter": args.adapter,
        "health": {},
        "counts": {},
        "milvus": {},
        "seed": {},
        "incremental_task": {},
        "queries": [],
    }

    print_title("1. Runtime 状态")
    health = (await service.health()).to_dict()
    counts = kg_counts(args.target)
    milvus_status = check_milvus()
    summary["health"] = health
    summary["counts"] = counts
    summary["milvus"] = milvus_status
    pprint({"health": health, "kg_counts": counts, "milvus": milvus_status})

    if args.status_only:
        if args.json:
            print_title("JSON Summary")
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    if not milvus_status.get("available"):
        raise SystemExit(
            "Milvus 当前不可用，知识图谱检索不允许降级到 PG text fallback。\n"
            f"错误: {milvus_status.get('error')}\n"
            "请先修复当前 Python 环境的 Milvus Lite/服务端连接后再运行演示。"
        )

    if args.seed_baseline:
        print_title("2. 写入受控 baseline records，并触发 compile 后增量索引刷新")
        records = load_baseline_records()
        compile_result = await service.compile_kg(
            KnowledgeCompileCommand(
                adapter_name=args.adapter,
                records=records,
                target=args.target,
                dry_run=args.dry_run,
                request_id="kg_demo_baseline",
                concurrency=args.concurrency,
            )
        )
        seed_data = compile_result.to_dict()
        summary["seed"] = seed_data
        pprint(
            {
                "run_id": seed_data["run_id"],
                "dry_run": seed_data["dry_run"],
                "nodes": seed_data["nodes"],
                "edges": seed_data["edges"],
                "evidence": seed_data["evidence"],
                "failed_records": seed_data["failed_records"],
                "index_refresh": seed_data["index_refresh"],
            }
        )
    else:
        print_title("2. 跳过写入")
        print("默认只读演示；需要造一批可控演示数据时，加 --seed-baseline。")

    if args.run_incremental_task:
        print_title("3. 可追踪增量刷新后台任务演示")
        command = KnowledgeIncrementalRefreshCommand(
            target=args.target,
            codes=args.codes,
            stock_limit=args.stock_limit,
            news_limit=args.news_limit,
            dry_run=args.dry_run,
            request_id="kg_demo_incremental_task",
            concurrency=args.concurrency,
            rebuild_indexes=True,
        )
        task = await service.enqueue_financial_incremental_refresh_task(command, max_retries=1)
        print("submitted:")
        pprint(task.to_dict())
        task_result = await service.run_financial_incremental_refresh_task(task.run_id)
        task_data = task_result.to_dict()
        summary["incremental_task"] = task_data
        print("finished:")
        pprint(
            {
                "run_id": task_data["run_id"],
                "status": task_data["status"],
                "attempt": task_data["attempt"],
                "error": task_data["error"],
                "result_steps": [step.get("name") for step in task_data.get("result", {}).get("steps", [])],
            }
        )
    else:
        print_title("3. 跳过后台任务")
        print("需要演示 run_id 可追踪、失败可重试的增量刷新时，加 --run-incremental-task。")

    print_title("4. 检索效果演示：Deterministic Fast Path")
    queries = [*DEMO_QUERIES, *(args.query or [])]
    for query in queries:
        result = await run_query(
            service,
            adapter=args.adapter,
            target=args.target,
            query=query,
            mode="deterministic_plan",
            max_chars=args.max_chars,
            show_context=args.show_context,
        )
        summary["queries"].append(result)

    print_title("6. 当前系统特点")
    print(
        "\n".join(
            [
                "- PG kg_* 是事实主存储；Milvus 是可重建的 hybrid 检索索引。",
                "- Milvus hybrid 同时承载 evidence、node、edge、wiki 文档召回。",
                "- 在线默认是 deterministic fast path：entity_resolve / graph_search / semantic_hybrid_search / wiki_search / chunk_read。",
                "- Agentic A-RAG 是显式深度研究路径，controller 会在停止前确保至少走过 semantic_hybrid_search。",
                "- compile 后会按变更集增量刷新 adjacency、evidence chunk、wiki page 和 Milvus documents。",
                "- 增量刷新可以后台任务化，状态落 kg_compilation_runs，可按 run_id 查询和重试。",
            ]
        )
    )

    if args.json:
        print_title("JSON Summary")
        print(json.dumps(summary, ensure_ascii=False, indent=2))


async def run_query(
    service,
    *,
    adapter: str,
    target: str,
    query: str,
    mode: str,
    max_chars: int,
    show_context: bool,
) -> dict[str, Any]:
    context = await service.build_research_context_for(
        KnowledgeResearchContextCommand(
            adapter_name=adapter,
            target=target,  # type: ignore[arg-type]
            query=query,
            retrieval_mode=mode,  # type: ignore[arg-type]
            graph_depth=3,
            graph_limit=20,
            wiki_limit=10,
            evidence_limit=20,
            max_chars=max_chars,
        )
    )
    data = context.to_dict()
    compact = {
        "query": query,
        "mode": mode,
        "hits": len(data["hits"]),
        "matched_nodes": len(data["matched_nodes"]),
        "matched_edges": len(data["matched_edges"]),
        "evidence_refs": len(data["evidence_refs"]),
        "channels_used": data["retrieval_channels_used"],
        "milvus_enabled": data["milvus_enabled"],
        "top_hits": [
            {
                "type": hit.get("type"),
                "title": hit.get("title"),
                "score": hit.get("score"),
                "evidence_refs": hit.get("evidence_refs", [])[:3],
            }
            for hit in data["hits"][:8]
        ],
        "nodes": [
            {"type": node.get("node_type") or node.get("type"), "name": node.get("canonical_name") or node.get("name")}
            for node in data["matched_nodes"][:10]
        ],
        "edges": [
            {
                "relation": edge.get("relation_type") or edge.get("relation"),
                "source": edge.get("source_node_id") or edge.get("source"),
                "target": edge.get("target_node_id") or edge.get("target"),
            }
            for edge in data["matched_edges"][:10]
        ],
    }
    print(f"\nQuery: {query}")
    pprint(compact)
    if show_context:
        print("\ncontext_text preview:")
        print(data["context_text"][:1800])
    return compact


def kg_counts(target: str) -> dict[str, int | str]:
    tables = [
        "kg_nodes",
        "kg_edges",
        "kg_evidence",
        "kg_wiki_pages",
        "kg_graph_adjacency",
        "kg_evidence_chunks",
        "kg_compilation_runs",
    ]
    result: dict[str, int | str] = {}
    with get_session(target) as session:
        for table in tables:
            try:
                count = session.execute(text(f"select count(*) from {table}")).scalar_one()
                result[table] = int(count)
            except Exception as exc:
                result[table] = f"error: {exc}"
    return result


def check_milvus() -> dict[str, Any]:
    try:
        store = MilvusHybridStore()
        store.ensure_ready()
        return {
            "available": True,
            "collection": store.collection_name,
            "uri": store.uri,
            "dim": store.dim,
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def load_baseline_records() -> list[dict[str, Any]]:
    script_path = Path(__file__).with_name("3_kg_real_replay_quality_baseline.py")
    spec = importlib.util.spec_from_file_location("kg_real_baseline_script", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import baseline script: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return list(module.baseline_records())


def print_title(title: str) -> None:
    print("\n" + "=" * 100)
    print(title)
    print("=" * 100)


if __name__ == "__main__":
    asyncio.run(main())
