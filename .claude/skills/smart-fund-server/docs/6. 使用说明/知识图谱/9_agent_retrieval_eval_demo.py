#!/usr/bin/env python3
"""模拟 Agent 多维检索，评测 Agent Retrieval Context 效果。

这个脚本只读，不写入 PG / Milvus。它要求写入链路已经提前跑通：

    python "docs/6. 使用说明/知识图谱/7_kg_write_path_demo.py"

它调用正式的 Agent 检索入口：

    KnowledgeService.agent_search()
    KnowledgeService.agent_open()
    KnowledgeService.agent_expand()
    KnowledgeService.agent_refine()

用途：
- 验证 community / cognitive card / evidence chunk 三层是否都能被召回；
- 验证 time_range / sort / limit / focus_aspects 是否进入正式检索链路；
- 模拟 Agent 读取 available_operations 后自行选择 open / expand / refine；
- 输出每个 case 的覆盖摘要、质量诊断、可用检索操作和命中层级分布；
- 生成 JSON，方便多轮代码优化后对比检索效果。

运行方式：

    python "docs/6. 使用说明/知识图谱/9_agent_retrieval_eval_demo.py"

常用参数：

    --case ai_compute
    --limit 8
    --candidate-limit 80
    --no-follow-hints
    --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
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

from src.application.dto.knowledge_dto import (  # noqa: E402
    KnowledgeAgentExpandCommand,
    KnowledgeAgentOpenCommand,
    KnowledgeAgentRefineCommand,
    KnowledgeAgentSearchCommand,
)
from src.application.services.knowledge_service import create_knowledge_service  # noqa: E402
from src.infrastructure.observability.langfuse_tracing import langfuse_flush  # noqa: E402
from src.infrastructure.vector_store.semantic_hybrid_retriever import MilvusSemanticHybridRetriever  # noqa: E402


OUTPUT_FILE = Path(__file__).with_name("generated_agent_retrieval_eval_demo.json")


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    query: str
    dimension: str
    focus_aspects: list[str] = field(default_factory=list)
    sort: str = "relevance"
    time_start: str | None = None
    time_end: str | None = None
    refinement: str = ""
    expected_layers: tuple[str, ...] = ("community", "cognitive_card", "evidence_chunk")
    note: str = ""


DEFAULT_CASES: tuple[EvalCase, ...] = (
    EvalCase(
        case_id="ai_compute",
        dimension="macro_theme",
        query="最近 AI 算力链、AI芯片、光模块和数据中心有哪些主线、机会和风险？",
        focus_aspects=["topic", "risk", "impact", "actor"],
        sort="evidence_strength",
        refinement="补充风险侧面和原始证据",
        note="宏观主题检索，期望先命中 AI 算力链 community，再下钻到 card/chunk。",
    ),
    EvalCase(
        case_id="merger_restructuring",
        dimension="community_navigation",
        query="A股并购重组和产业整合最近有哪些变化？影响哪些行业？",
        focus_aspects=["topic", "impact", "actor"],
        sort="evidence_strength",
        refinement="补充具体并购案例和政策驱动",
        note="验证高维主题聚合和支撑证据追溯。",
    ),
    EvalCase(
        case_id="geopolitical_energy",
        dimension="risk_search",
        query="中东地缘风险、霍尔木兹海峡和能源供应冲击对市场有什么影响？",
        focus_aspects=["risk", "impact", "time"],
        sort="freshness",
        refinement="补充油气供应、航运通道和市场风险偏好",
        note="验证风险信号、时间过滤和能源主题是否能被覆盖。",
    ),
    EvalCase(
        case_id="new_energy_overseas",
        dimension="impact_chain",
        query="新能源企业出海、海外建厂和储能电池供应链有哪些进展？",
        focus_aspects=["topic", "impact", "actor"],
        sort="diversity",
        refinement="补充区域合作、贸易壁垒和供应链风险",
        note="验证 diversity 是否能避免全部集中到单一 source。",
    ),
    EvalCase(
        case_id="macro_liquidity",
        dimension="time_sensitive_macro",
        query="最近央行、利率、汇率和宏观流动性有哪些关键变化？",
        focus_aspects=["topic", "risk", "time"],
        sort="freshness",
        time_start="2026-04-01T00:00:00+00:00",
        refinement="补充债券市场、汇率压力和社融信贷",
        note="验证 time_range 是否参与底层召回。",
    ),
    EvalCase(
        case_id="commodity_supply",
        dimension="commodity",
        query="铜、镍、油气等大宗商品供需冲击近期有哪些线索？",
        focus_aspects=["topic", "risk", "impact"],
        sort="diversity",
        refinement="补充不同商品品种和上游约束",
        note="验证商品主题是否被过度合并或过度分散。",
    ),
    EvalCase(
        case_id="company_earnings",
        dimension="local_evidence",
        query="近期公司业绩、订单、营收利润变化反映了哪些产业景气线索？",
        focus_aspects=["topic", "actor", "impact"],
        sort="relevance",
        refinement="补充具体公司和原始财报证据",
        note="验证局部证据问题是否能返回 chunk/card，而不是只有 community。",
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate Agent KG retrieval across representative dimensions.")
    parser.add_argument("--target", default="prod", choices=["prod", "test"])
    parser.add_argument("--adapter", default="financial")
    parser.add_argument("--case", action="append", help="case_id to run; can be repeated")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--candidate-limit", type=int, default=80)
    parser.add_argument("--max-chars", type=int, default=10000)
    parser.add_argument("--no-follow-hints", action="store_true", help="only run search; do not simulate open/expand/refine")
    parser.add_argument("--json", action="store_true", help="print full JSON summary to stdout")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    service = create_knowledge_service(target=args.target)
    session_id = f"kg-agent-retrieval-eval:{int(time.time())}"
    cases = _select_cases(args.case)

    print_title("Step 0. Agent Retrieval Eval 配置")
    config = {
        "target": args.target,
        "adapter": args.adapter,
        "session_id": session_id,
        "limit": args.limit,
        "candidate_limit": args.candidate_limit,
        "case_count": len(cases),
        "follow_hints": not args.no_follow_hints,
        "output_file": str(OUTPUT_FILE),
    }
    pprint(config)

    health = (await service.health()).to_dict()
    index_status = inspect_agent_read_indexes(args.adapter, args.target)
    if not index_status.get("ready"):
        print_title("Agent 读索引未就绪")
        pprint(index_status)
    results: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        print_title(f"Step {index}. Case {case.case_id} / {case.dimension}")
        try:
            result = await run_case(
                service=service,
                case=case,
                adapter=args.adapter,
                target=args.target,
                session_id=session_id,
                limit=args.limit,
                candidate_limit=args.candidate_limit,
                max_chars=args.max_chars,
                follow_hints=not args.no_follow_hints,
            )
        except Exception as exc:
            result = failed_case_result(case, exc)
        results.append(result)
        pprint(_console_case_summary(result))

    summary = {
        "config": config,
        "health": health,
        "index_status": index_status,
        "aggregate": aggregate_results(results),
        "cases": results,
    }
    OUTPUT_FILE.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print_title("完成")
    pprint({"output_file": str(OUTPUT_FILE), "aggregate": summary["aggregate"]})
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    langfuse_flush()


async def run_case(
    *,
    service,
    case: EvalCase,
    adapter: str,
    target: str,
    session_id: str,
    limit: int,
    candidate_limit: int,
    max_chars: int,
    follow_hints: bool,
) -> dict[str, Any]:
    search = await service.agent_search(
        KnowledgeAgentSearchCommand(
            query=case.query,
            adapter_name=adapter,
            target=target,
            session_id=session_id,
            limit=limit,
            candidate_limit=candidate_limit,
            sort=case.sort,  # type: ignore[arg-type]
            time_start=_parse_datetime(case.time_start),
            time_end=_parse_datetime(case.time_end),
            max_chars=max_chars,
            focus_aspects=case.focus_aspects,
        )
    )
    search_data = search.to_dict()
    followups: list[dict[str, Any]] = []
    if follow_hints:
        followups = await follow_available_operations(
            service=service,
            context=search_data,
            case=case,
            adapter=adapter,
            target=target,
            session_id=session_id,
            limit=limit,
            max_chars=max_chars,
        )
    return {
        "case": {
            "case_id": case.case_id,
            "dimension": case.dimension,
            "query": case.query,
            "focus_aspects": case.focus_aspects,
            "sort": case.sort,
            "time_range": {"start": case.time_start, "end": case.time_end},
            "expected_layers": list(case.expected_layers),
            "note": case.note,
        },
        "search": search_data,
        "followups": followups,
        "assessment": assess_case(search_data, followups, expected_layers=case.expected_layers),
    }


async def follow_available_operations(
    *,
    service,
    context: dict[str, Any],
    case: EvalCase,
    adapter: str,
    target: str,
    session_id: str,
    limit: int,
    max_chars: int,
) -> list[dict[str, Any]]:
    followups: list[dict[str, Any]] = []
    hints = context.get("available_operations") or []
    opened = False
    expanded = False
    refined = False
    for hint in hints:
        action = hint.get("action")
        target_ids = [str(item) for item in hint.get("target_ids") or [] if item]
        if action == "open_result" and target_ids and not opened:
            opened = True
            opened_context = await service.agent_open(
                KnowledgeAgentOpenCommand(
                    target_ids=target_ids[:3],
                    query=case.query,
                    adapter_name=adapter,
                    target=target,
                    session_id=session_id,
                    include_neighbors=True,
                    limit=limit,
                    max_chars=max_chars,
                )
            )
            followups.append({"action": "open_result", "context": opened_context.to_dict()})
            continue
        if action == "expand_context" and target_ids and not expanded:
            expanded = True
            expanded_context = await service.agent_expand(
                KnowledgeAgentExpandCommand(
                    target_id=target_ids[0],
                    query=case.query,
                    adapter_name=adapter,
                    target=target,
                    session_id=session_id,
                    direction="auto",
                    limit=max(limit, 12),
                    max_chars=max_chars,
                )
            )
            followups.append({"action": "expand_context", "context": expanded_context.to_dict()})
            continue
        if action in {"refine_query", "refine_uncovered_observed_aspect"} and case.refinement and not refined:
            refined = True
            refined_context = await service.agent_refine(
                KnowledgeAgentRefineCommand(
                    query=case.query,
                    adapter_name=adapter,
                    target=target,
                    session_id=session_id,
                    limit=limit,
                    candidate_limit=max(limit * 6, 50),
                    sort=case.sort,  # type: ignore[arg-type]
                    max_chars=max_chars,
                    focus_aspects=case.focus_aspects,
                    previous_context=context,
                    refinement=case.refinement,
                )
            )
            followups.append({"action": "refine_query", "context": refined_context.to_dict()})
    return followups


def assess_case(
    context: dict[str, Any],
    followups: list[dict[str, Any]],
    *,
    expected_layers: tuple[str, ...],
) -> dict[str, Any]:
    packages = context.get("evidence_package") or []
    layers = [str(item.get("layer") or "") for item in packages if isinstance(item, dict)]
    layer_counts = Counter(layers)
    coverage = context.get("coverage_summary") or {}
    diagnostics = context.get("quality_diagnostics") or {}
    expected_layer_hits = {layer: layer_counts.get(layer, 0) for layer in expected_layers}
    missing_expected_layers = [layer for layer, count in expected_layer_hits.items() if count <= 0]
    evidence_count = int(coverage.get("evidence_count") or 0)
    chunk_count = int(coverage.get("chunk_count") or 0)
    community_count = int(coverage.get("community_count") or 0)
    covered_aspects = diagnostics.get("coverage", {}).get("covered_aspects") or []
    hints = context.get("available_operations") or []
    return {
        "result_count": len(packages),
        "layer_counts": dict(layer_counts),
        "missing_expected_layers": missing_expected_layers,
        "has_expected_layers": not missing_expected_layers,
        "evidence_count": evidence_count,
        "chunk_count": chunk_count,
        "community_count": community_count,
        "covered_aspects": covered_aspects,
        "available_operation_actions": [hint.get("action") for hint in hints if isinstance(hint, dict)],
        "followup_actions": [item.get("action") for item in followups],
        "diagnostic_status": {
            "evidence_sufficiency": diagnostics.get("evidence_sufficiency", {}).get("status"),
            "diversity": diagnostics.get("diversity", {}).get("status"),
            "information_redundancy": diagnostics.get("information_redundancy", {}).get("status"),
        },
        "quality_flags": _assessment_quality_flags(
            packages=packages,
            missing_expected_layers=missing_expected_layers,
            evidence_count=evidence_count,
            chunk_count=chunk_count,
            community_count=community_count,
            diagnostics=diagnostics,
        ),
    }


def failed_case_result(case: EvalCase, exc: Exception) -> dict[str, Any]:
    return {
        "case": {
            "case_id": case.case_id,
            "dimension": case.dimension,
            "query": case.query,
            "focus_aspects": case.focus_aspects,
            "sort": case.sort,
            "time_range": {"start": case.time_start, "end": case.time_end},
            "expected_layers": list(case.expected_layers),
            "note": case.note,
        },
        "search": {},
        "followups": [],
        "error": {
            "type": exc.__class__.__name__,
            "message": str(exc),
            "hint": _error_hint(exc),
        },
        "assessment": {
            "result_count": 0,
            "layer_counts": {},
            "missing_expected_layers": list(case.expected_layers),
            "has_expected_layers": False,
            "evidence_count": 0,
            "chunk_count": 0,
            "community_count": 0,
            "covered_aspects": [],
            "available_operation_actions": [],
            "followup_actions": [],
            "diagnostic_status": {"error": exc.__class__.__name__},
            "quality_flags": ["execution_failed"],
        },
    }


def inspect_agent_read_indexes(adapter: str, target: str) -> dict[str, Any]:
    try:
        retriever = MilvusSemanticHybridRetriever()
        role_counts: dict[str, int] = {}
        errors: dict[str, str] = {}
        for role in ("chunk", "cognitive_card", "community"):
            try:
                role_counts[role] = len(
                    retriever.store.list_target_ids(
                        collection_role=role,
                        adapter_name=adapter,
                        target=target,
                        limit=5,
                    )
                )
            except Exception as exc:
                errors[role] = f"{exc.__class__.__name__}: {exc}"
        ready = not errors and all(role_counts.get(role, 0) > 0 for role in ("chunk", "cognitive_card", "community"))
        return {
            "ready": ready,
            "role_sample_counts": role_counts,
            "errors": errors,
            "hint": "" if ready else "请先运行 7_kg_write_path_demo.py，让正式写入链路重建 Milvus schema 和读索引。",
        }
    except Exception as exc:
        return {
            "ready": False,
            "role_sample_counts": {},
            "errors": {"milvus": f"{exc.__class__.__name__}: {exc}"},
            "hint": "请先确认 Milvus 可用，并运行 7_kg_write_path_demo.py 生成读索引。",
        }


def _error_hint(exc: Exception) -> str:
    message = str(exc)
    if "schema mismatch" in message and "has_time_fields=False" in message:
        return "Milvus collection 仍是旧 schema，缺少时间 scalar 字段。需要清理/重建语义索引后再评测。"
    if "Milvus" in message:
        return "检查 Milvus Lite/服务端连接、collection schema 和语义索引是否已刷新。"
    return ""


def aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    layer_counts = Counter()
    missing_layers = Counter()
    available_operation_actions = Counter()
    quality_flags = Counter()
    for item in results:
        assessment = item.get("assessment") or {}
        layer_counts.update(assessment.get("layer_counts") or {})
        missing_layers.update(assessment.get("missing_expected_layers") or [])
        available_operation_actions.update(assessment.get("available_operation_actions") or [])
        quality_flags.update(assessment.get("quality_flags") or [])
    return {
        "case_count": len(results),
        "failed_count": sum(1 for item in results if item.get("error")),
        "layer_counts": dict(layer_counts),
        "missing_expected_layers": dict(missing_layers),
        "available_operation_actions": dict(available_operation_actions),
        "quality_flags": dict(quality_flags),
    }


def _console_case_summary(result: dict[str, Any]) -> dict[str, Any]:
    search = result.get("search") or {}
    return {
        "case_id": result.get("case", {}).get("case_id"),
        "dimension": result.get("case", {}).get("dimension"),
        "assessment": result.get("assessment"),
        "coverage_summary": search.get("coverage_summary"),
        "available_operations": [
            {
                "action": item.get("action"),
                "availability_reason": item.get("availability_reason"),
                "target_ids": item.get("target_ids"),
            }
            for item in (search.get("available_operations") or [])[:4]
            if isinstance(item, dict)
        ],
    }


def _assessment_quality_flags(
    *,
    packages: list[dict[str, Any]],
    missing_expected_layers: list[str],
    evidence_count: int,
    chunk_count: int,
    community_count: int,
    diagnostics: dict[str, Any],
) -> list[str]:
    flags: list[str] = []
    if not packages:
        flags.append("no_results")
    if missing_expected_layers:
        flags.append("missing_expected_layer")
    if evidence_count <= 0 and chunk_count <= 0:
        flags.append("no_raw_evidence")
    if community_count <= 0:
        flags.append("no_community")
    if diagnostics.get("diversity", {}).get("status") == "concentrated":
        flags.append("concentrated_results")
    if diagnostics.get("information_redundancy", {}).get("status") == "homogeneous":
        flags.append("homogeneous_results")
    if diagnostics.get("evidence_sufficiency", {}).get("status") == "thin":
        flags.append("thin_evidence")
    return flags


def _select_cases(case_ids: list[str] | None) -> list[EvalCase]:
    if not case_ids:
        return list(DEFAULT_CASES)
    by_id = {case.case_id: case for case in DEFAULT_CASES}
    missing = [case_id for case_id in case_ids if case_id not in by_id]
    if missing:
        raise SystemExit(f"unknown case_id: {missing}; available={sorted(by_id)}")
    return [by_id[case_id] for case_id in case_ids]


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def print_title(title: str) -> None:
    print("\n" + "=" * 12 + f" {title} " + "=" * 12)


if __name__ == "__main__":
    asyncio.run(main())
