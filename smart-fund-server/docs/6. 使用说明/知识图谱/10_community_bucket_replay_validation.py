#!/usr/bin/env python3
"""Community Assignment bucket 分流验收脚本。

这个脚本不写入 PG，也不改正式 Redis bucket 账本。它会使用独立 validation namespace
保存本次验证的临时 Redis bucket cache 和 Milvus assignment_bucket 语义 cache，避免污染线上写入链路。它只用于验证：

- 从当前 `kg_cognitive_cards` 读取待分流 topic_intents；
- 让正式 Bucket Planner 自动划分并发 bucket；
- 输出 bucket 分布、每个 bucket 承接的 intent 和临时 bucket cache 结果。

它不读取 `kg_community_assignments` / `kg_graph_communities`，不执行正式 Community Assignment，
不调用一致性复核 LLM。正式写入链路只在 `7_kg_write_path_demo.py` 中执行。

运行方式：

    python "docs/6. 使用说明/知识图谱/10_community_bucket_replay_validation.py"

常用参数：

    --target prod
    --adapter financial
    --card-limit 20
    --json
    --cache-mode fresh  # 默认，运行前清理当前验证 namespace 的 bucket cache
    --cache-mode reuse  # 复用当前验证 namespace 的 bucket cache，专门用于验证缓存命中
    --llm-cache-mode bypass  # 本次 LLM 请求不使用本地 LLM 缓存，不删除缓存文件
    --bucket-thinking disabled  # 默认，关闭 DeepSeek 思考模式，避免 bucket planning 过慢
    --auto-merge-threshold 999  # 默认不触发 merge；需要验证 merge 时再显式调低
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from pprint import pprint
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)


def _project_root() -> Path:
    root = Path(__file__).resolve()
    while root.name != "smart-fund-server" and root.parent != root:
        root = root.parent
    if root.name != "smart-fund-server":
        raise RuntimeError("cannot locate smart-fund-server project root")
    return root


PROJECT_ROOT = _project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.application.services.cognitive_index_service import (  # noqa: E402
    AssignmentBucketStore,
    AssignmentBucketSemanticCache,
    CommunityBucketPlanner,
    _builder_intent_refs,
)
from src.infrastructure.observability.langfuse_tracing import (  # noqa: E402
    langfuse_flush,
    langfuse_observation,
    langfuse_propagation_context,
    langfuse_update_span,
)
from src.infrastructure.persistence.repositories.knowledge_repository_impl import KnowledgeRepositoryImpl  # noqa: E402
from src.infrastructure.vector_store.semantic_hybrid_retriever import MilvusSemanticHybridRetriever  # noqa: E402


OUTPUT_FILE = Path(__file__).with_name("generated_community_bucket_validation.json")


def _log(message: str, *, quiet: bool = False) -> None:
    if quiet:
        return
    print(message, file=sys.stderr, flush=True)


class _StepTimer:
    def __init__(self, name: str, *, quiet: bool = False) -> None:
        self._name = name
        self._quiet = quiet
        self._started = 0.0

    def __enter__(self) -> "_StepTimer":
        self._started = time.perf_counter()
        _log(f"[bucket_validation] {self._name} START", quiet=self._quiet)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        duration = time.perf_counter() - self._started
        status = "FAILED" if exc_type else "DONE"
        _log(f"[bucket_validation] {self._name} {status} duration={duration:.1f}s", quiet=self._quiet)
        return False


async def _await_with_heartbeat(
    name: str,
    awaitable,
    *,
    quiet: bool,
    interval_seconds: float = 15.0,
):
    started = time.perf_counter()
    task = asyncio.create_task(awaitable)
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=interval_seconds)
            if not task.done():
                elapsed = time.perf_counter() - started
                _log(f"[bucket_validation] {name} still running elapsed={elapsed:.1f}s", quiet=quiet)
        return await task
    except Exception:
        if not task.done():
            task.cancel()
        raise


def _bucket_summary(bucket_plan: dict[str, Any]) -> dict[str, Any]:
    buckets = bucket_plan.get("buckets") or {}
    bucket_sizes = {
        bucket_id: len(refs)
        for bucket_id, refs in sorted(buckets.items(), key=lambda item: item[0])
    }
    return {
        "bucket_count": len(buckets),
        "unknown_intents": bucket_plan.get("unknown_intents"),
        "llm_assignments": bucket_plan.get("llm_assignments"),
        "new_buckets": bucket_plan.get("new_buckets"),
        "merge_suggestions": bucket_plan.get("merge_suggestions"),
        "semantic_cache": bucket_plan.get("semantic_cache"),
        "merge_result": bucket_plan.get("merge_result"),
        "bucket_sizes": bucket_sizes,
    }


def _bucket_details(bucket_plan: dict[str, Any]) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    buckets = bucket_plan.get("buckets") or {}
    for bucket_id, refs in sorted(buckets.items(), key=lambda item: item[0]):
        details.append(
            {
                "bucket_id": bucket_id,
                "size": len(refs),
                "intents": [
                    {
                        "intent_id": ref.get("intent_id"),
                        "cognitive_card_id": getattr(ref.get("card"), "cognitive_card_id", ""),
                        "source_id": getattr(ref.get("card"), "source_id", ""),
                        "chunk_index": getattr(ref.get("card"), "chunk_index", 0),
                        "intent_index": ref.get("intent_index"),
                        "raw_theme": (ref.get("topic_intent") or {}).get("raw_theme"),
                        "title_candidate": (ref.get("topic_intent") or {}).get("title_candidate"),
                        "parent_themes": (ref.get("topic_intent") or {}).get("parent_themes") or [],
                        "broad_topics": (ref.get("topic_intent") or {}).get("broad_topics") or [],
                        "bucket_assignment": ref.get("bucket") or {},
                    }
                    for ref in refs
                ],
            }
        )
    return details


async def run_validation(
    *,
    adapter_name: str,
    target: str,
    cache_namespace: str,
    cache_mode: str,
    llm_cache_mode: str,
    auto_merge_threshold: int,
    auto_merge_candidate_limit: int,
    bucket_thinking: str,
    card_limit: int,
    intent_limit: int,
    quiet: bool,
) -> dict[str, Any]:
    with _StepTimer("load_pg_materials", quiet=quiet):
        repository = KnowledgeRepositoryImpl(target=target)
        cards = repository.list_cognitive_cards(adapter_name)
        if card_limit > 0:
            cards = cards[:card_limit]
        intent_refs = _builder_intent_refs(cards)
        if intent_limit > 0:
            intent_refs = intent_refs[:intent_limit]
        _log(
            "[bucket_validation] loaded "
            f"cards={len(cards)} intents={len(intent_refs)}",
            quiet=quiet,
        )
    validation_cache_target = f"{target}:{cache_namespace}"
    semantic_cache_target = validation_cache_target
    bucket_store = AssignmentBucketStore(target=validation_cache_target)
    reuse_cache = cache_mode == "reuse"
    cache_cleanup = {"enabled": not reuse_cache, "deleted": 0, "state_key": ""}
    if not reuse_cache:
        with _StepTimer("clear_bucket_cache", quiet=quiet):
            cache_cleanup = {
                "enabled": True,
                **bucket_store.clear(adapter_name=adapter_name, force_stale_lock=True),
            }
            _log(
                "[bucket_validation] cleared bucket cache "
                f"key={cache_cleanup.get('state_key')} deleted={cache_cleanup.get('deleted')} "
                f"lock_deleted={cache_cleanup.get('lock_deleted')}",
                quiet=quiet,
            )
    llm_use_cache = llm_cache_mode == "use"
    with _StepTimer("init_semantic_bucket_cache", quiet=quiet):
        semantic_bucket_cache = AssignmentBucketSemanticCache(
            target=semantic_cache_target,
            store=MilvusSemanticHybridRetriever().store,
        )
        semantic_cache_has_entries = semantic_bucket_cache.has_entries(adapter_name=adapter_name)
        _log(
            "[bucket_validation] semantic_bucket_cache "
            f"target={semantic_cache_target} has_entries={semantic_cache_has_entries}",
            quiet=quiet,
        )
    planner = CommunityBucketPlanner(
        store=bucket_store,
        use_cache=llm_use_cache,
        auto_merge_threshold=auto_merge_threshold,
        auto_merge_candidate_limit=auto_merge_candidate_limit,
        bucket_thinking=bucket_thinking,
        semantic_bucket_cache=semantic_bucket_cache,
    )
    _log(
        "[bucket_validation] mode=bucket_only "
        f"cache_namespace={validation_cache_target} "
        f"semantic_cache_target={semantic_cache_target} "
        f"llm_cache_mode={llm_cache_mode} "
        f"bucket_thinking={bucket_thinking} "
        f"auto_merge_threshold={auto_merge_threshold}",
        quiet=quiet,
    )
    with _StepTimer("bucket_planning", quiet=quiet):
        _log(
            f"[bucket_validation] bucket_planning input_intents={len(intent_refs)} "
            "note=按 planner batch size 分批规划；每批写入 bucket cache 后下一批复用",
            quiet=quiet,
        )
        bucket_plan = await _await_with_heartbeat(
            "bucket_planning.llm",
            planner.plan(adapter_name=adapter_name, intent_refs=intent_refs),
            quiet=quiet,
        )
        _log(
            "[bucket_validation] bucket_plan "
            f"bucket_count={len(bucket_plan.get('buckets') or {})} "
            f"unknown_intents={bucket_plan.get('unknown_intents')} "
            f"new_buckets={bucket_plan.get('new_buckets')} "
            f"semantic_cache={bucket_plan.get('semantic_cache')} "
            f"merge_result={bucket_plan.get('merge_result')}",
            quiet=quiet,
        )
    result = {
        "config": {
            "adapter_name": adapter_name,
            "target": target,
            "validation_cache_target": validation_cache_target,
            "semantic_cache_target": semantic_cache_target,
            "cache_mode": cache_mode,
            "llm_cache_mode": llm_cache_mode,
            "llm_use_cache": llm_use_cache,
            "bucket_thinking": bucket_thinking,
            "auto_merge_threshold": auto_merge_threshold,
            "auto_merge_candidate_limit": auto_merge_candidate_limit,
            "cache_cleanup": cache_cleanup,
            "card_limit": card_limit,
            "intent_limit": intent_limit,
        },
        "loaded": {
            "cognitive_cards": len(cards),
            "topic_intents": len(intent_refs),
        },
        "bucket_plan": _bucket_summary(bucket_plan),
        "bucket_details": _bucket_details(bucket_plan),
    }
    OUTPUT_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", default="financial")
    parser.add_argument("--target", default="prod")
    parser.add_argument("--cache-namespace", default="bucket_validation")
    parser.add_argument(
        "--cache-mode",
        choices=["fresh", "reuse"],
        default="fresh",
        help="fresh=运行前清理验证 bucket cache；reuse=复用验证 bucket cache",
    )
    parser.add_argument(
        "--llm-cache-mode",
        choices=["use", "bypass"],
        default="use",
        help="use=允许使用本地 LLM 文件/内存缓存；bypass=本次 LLM 请求不使用缓存但不删除缓存文件",
    )
    parser.add_argument(
        "--bucket-thinking",
        choices=["enabled", "disabled"],
        default="disabled",
        help="Bucket Planning 请求的 DeepSeek thinking 开关；默认 disabled，enabled 仅用于质量诊断",
    )
    parser.add_argument(
        "--auto-merge-threshold",
        type=int,
        default=999,
        help="bucket catalog 超过该数量后触发独立 merge；默认 999 表示普通分桶验证不触发 merge",
    )
    parser.add_argument(
        "--auto-merge-candidate-limit",
        type=int,
        default=24,
        help="自动 merge 每轮最多提交给 LLM 的候选 bucket pair 数量",
    )
    parser.add_argument("--card-limit", type=int, default=0, help="只读取前 N 张 card；0 表示全量")
    parser.add_argument("--intent-limit", type=int, default=0, help="只处理前 N 个 topic intent；0 表示全量")
    parser.add_argument("--reuse-cache", action="store_true", help="兼容旧参数，等价于 --cache-mode reuse")
    parser.add_argument("--no-llm-cache", action="store_true", help="兼容便捷参数，等价于 --llm-cache-mode bypass")
    parser.add_argument("--json", action="store_true", help="只输出 JSON 摘要")
    parser.add_argument("--quiet", action="store_true", help="不输出阶段进度日志")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    cache_mode = "reuse" if args.reuse_cache else args.cache_mode
    llm_cache_mode = "bypass" if args.no_llm_cache else args.llm_cache_mode
    run_session_id = f"kg-bucket-validation:{args.target}:{args.adapter}:{int(time.time())}"
    run_metadata = {
        "adapter_name": args.adapter,
        "target": args.target,
        "cache_namespace": args.cache_namespace,
        "cache_mode": cache_mode,
        "llm_cache_mode": llm_cache_mode,
        "llm_use_cache": llm_cache_mode == "use",
        "bucket_thinking": args.bucket_thinking,
        "auto_merge_threshold": max(1, args.auto_merge_threshold),
        "auto_merge_candidate_limit": max(1, args.auto_merge_candidate_limit),
        "bucket_only": True,
        "reuse_cache": cache_mode == "reuse",
        "card_limit": max(0, args.card_limit),
        "intent_limit": max(0, args.intent_limit),
        "session_id": run_session_id,
    }
    try:
        with langfuse_propagation_context(
            trace_name="kg.community_bucket_validation",
            session_id=run_session_id,
            tags=["kg", "community-bucket", "validation"],
            metadata=run_metadata,
        ):
            with langfuse_observation(
                name="kg.community_bucket_validation",
                as_type="chain",
                input=run_metadata,
                metadata=run_metadata,
            ):
                try:
                    result = await run_validation(
                        adapter_name=args.adapter,
                        target=args.target,
                        cache_namespace=args.cache_namespace,
                        cache_mode=cache_mode,
                        llm_cache_mode=llm_cache_mode,
                        auto_merge_threshold=max(1, args.auto_merge_threshold),
                        auto_merge_candidate_limit=max(1, args.auto_merge_candidate_limit),
                        bucket_thinking=args.bucket_thinking,
                        card_limit=max(0, args.card_limit),
                        intent_limit=max(0, args.intent_limit),
                        quiet=args.quiet,
                    )
                    langfuse_update_span(
                        output={
                            "loaded": result["loaded"],
                            "bucket_plan": result["bucket_plan"],
                            "output_file": str(OUTPUT_FILE),
                        },
                        status_message="completed",
                    )
                except Exception as exc:
                    langfuse_update_span(
                        metadata={"error_type": exc.__class__.__name__},
                        level="ERROR",
                        status_message=str(exc),
                    )
                    raise
    finally:
        langfuse_flush()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        pprint(
            {
                "loaded": result["loaded"],
                "bucket_plan": result["bucket_plan"],
                "bucket_details_sample": result["bucket_details"][:10],
                "output_file": str(OUTPUT_FILE),
            }
        )


if __name__ == "__main__":
    asyncio.run(main())
