#!/usr/bin/env python3
"""Evaluate Agent-facing Card, Edge, and Community graph retrieval tools."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.application.services.relation_graph_agent_retrieval_service import (  # noqa: E402
    create_relation_graph_agent_retrieval_service,
)
from src.infrastructure.observability.langfuse_tracing import (  # noqa: E402
    langfuse_flush,
    langfuse_observation,
    langfuse_propagation_context,
    langfuse_update_span,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="批量验证关系图 Agent Tool 的 Card Recall、Edge Recall 和证据完整性"
    )
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument(
        "--dataset",
        type=Path,
        help="JSON 数组或包含 cases 数组的 JSON 文件",
    )
    parser.add_argument("--target", choices=["prod", "test"], default="prod")
    parser.add_argument("--adapter", default="financial")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--seed-limit", type=int, default=10)
    parser.add_argument("--candidate-limit", type=int, default=40)
    parser.add_argument("--expand-hops", type=int, choices=[1, 2], default=1)
    parser.add_argument("--node-limit", type=int, default=50)
    parser.add_argument("--edge-limit", type=int, default=100)
    parser.add_argument("--session-id", default="")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def load_cases(args: argparse.Namespace) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    if args.dataset:
        payload = json.loads(args.dataset.read_text(encoding="utf-8"))
        raw_cases = payload.get("cases", []) if isinstance(payload, dict) else payload
        if not isinstance(raw_cases, list):
            raise ValueError("dataset 必须是 JSON 数组或包含 cases 数组")
        cases.extend(item for item in raw_cases if isinstance(item, dict))
    cases.extend(
        {
            "case_id": f"cli-{index}",
            "query": query,
        }
        for index, query in enumerate(args.query, start=1)
        if str(query).strip()
    )
    if args.limit > 0:
        cases = cases[: args.limit]
    if not cases:
        raise ValueError("至少提供一个 --query 或 --dataset")
    for index, case in enumerate(cases, start=1):
        if not str(case.get("query") or "").strip():
            raise ValueError(f"第 {index} 个 case 缺少 query")
        case.setdefault("case_id", f"case-{index}")
    return cases


async def evaluate_case(
    service,
    case: dict[str, Any],
    *,
    adapter_name: str,
    seed_limit: int,
    candidate_limit: int,
    expand_hops: int,
    node_limit: int,
    edge_limit: int,
) -> dict[str, Any]:
    query = str(case["query"]).strip()
    expected_seed_cards = _set(case.get("expected_seed_card_ids"))
    expected_expanded_cards = _set(case.get("expected_card_ids"))
    expected_edges = _set(case.get("expected_edge_ids"))
    expected_kinds = _set(case.get("expected_relation_kinds"))
    forbidden_cards = _set(case.get("forbidden_card_ids"))
    labeled = any(
        (
            expected_seed_cards,
            expected_expanded_cards,
            expected_edges,
            expected_kinds,
            forbidden_cards,
        )
    )
    with langfuse_observation(
        name="kg.relation_graph_agent.validation_case",
        as_type="span",
        input=case,
        metadata={"case_id": case["case_id"]},
    ):
        search = await service.search(
            query=query,
            adapter_name=adapter_name,
            seed_limit=seed_limit,
            candidate_limit=candidate_limit,
        )
        seed_ids = [
            card["card_id"]
            for card in search.get("cards", [])
        ]
        seed_cards = [
            {
                "card_id": card["card_id"],
                "fact_id": card.get("fact_id", ""),
                "fact_card_count": card.get("fact_card_count", 1),
                "summary": card.get("summary", ""),
                "retrieval": card.get("retrieval", {}),
            }
            for card in search.get("cards", [])
        ]
        found_seed_cards = set(seed_ids)
        expanded = (
            await service.expand_cards(
                card_ids=seed_ids,
                adapter_name=adapter_name,
                hop_limit=expand_hops,
                node_limit=max(node_limit, len(seed_ids)),
                edge_limit=edge_limit,
            )
            if seed_ids
            else {
                "cards": [],
                "edges": [],
                "communities": [],
                "truncated": False,
            }
        )
        found_cards = {
            card["card_id"]
            for card in expanded.get("cards", [])
        }
        found_edges = {
            edge["edge_id"]
            for edge in expanded.get("edges", [])
        }
        found_kinds = {
            edge["relation_kind"]
            for edge in expanded.get("edges", [])
        }
        opened_cards = (
            await service.open_cards(
                card_ids=list(found_cards)[:30],
                adapter_name=adapter_name,
            )
            if found_cards
            else {"cards": []}
        )
        opened_edges = (
            await service.open_edges(
                edge_ids=list(found_edges)[:50],
                adapter_name=adapter_name,
            )
            if found_edges
            else {"edges": []}
        )
        metrics = {
            "seed_card_recall_at_k": _recall(
                expected_seed_cards,
                found_seed_cards,
            ),
            "seed_card_precision_at_k": _precision(
                expected_seed_cards,
                found_seed_cards,
            ),
            "expanded_card_recall": _recall(
                expected_expanded_cards,
                found_cards,
            ),
            "edge_recall": _recall(expected_edges, found_edges),
            "relation_kind_recall": _recall(
                expected_kinds,
                found_kinds,
            ),
            "forbidden_seed_card_hits": len(
                forbidden_cards.intersection(found_seed_cards)
            ),
            "forbidden_expanded_card_hits": len(
                forbidden_cards.intersection(found_cards)
            ),
            "opened_card_focus_evidence_rate": _non_empty_rate(
                opened_cards.get("cards", []),
                "focus_evidence",
            ),
            "opened_edge_basis_rate": _non_empty_rate(
                opened_edges.get("edges", []),
                "basis",
            ),
        }
        passed = (
            (
                _is_complete(metrics["seed_card_recall_at_k"])
                and _is_complete(metrics["expanded_card_recall"])
                and _is_complete(metrics["edge_recall"])
                and _is_complete(metrics["relation_kind_recall"])
                and metrics["forbidden_seed_card_hits"] == 0
                and metrics["forbidden_expanded_card_hits"] == 0
                and metrics["opened_card_focus_evidence_rate"] >= 1.0
                and metrics["opened_edge_basis_rate"] >= 1.0
            )
            if labeled
            else None
        )
        result = {
            "case_id": case["case_id"],
            "query": query,
            "labeled": labeled,
            "passed": passed,
            "metrics": metrics,
            "seed_cards": seed_cards,
            "seed_card_ids": seed_ids,
            "found_card_ids": sorted(found_cards),
            "found_edge_ids": sorted(found_edges),
            "found_relation_kinds": sorted(found_kinds),
            "community_ids": sorted(
                {
                    community["community_id"]
                    for community in expanded.get("communities", [])
                }
            ),
            "truncated": bool(expanded.get("truncated")),
        }
        langfuse_update_span(
            output=result,
            status_message=(
                "exploratory"
                if passed is None
                else ("passed" if passed else "failed")
            ),
        )
        return result


async def run(args: argparse.Namespace) -> dict[str, Any]:
    cases = load_cases(args)
    session_id = (
        args.session_id.strip()
        or f"kg-relation-graph-agent-eval-{uuid4().hex}"
    )
    service = create_relation_graph_agent_retrieval_service(
        target=args.target
    )
    try:
        with langfuse_propagation_context(
            trace_name="kg.relation_graph_agent.validation",
            session_id=session_id,
            tags=["kg", "agent-tool", "relation-graph", "validation"],
            metadata={
                "target": args.target,
                "adapter_name": args.adapter,
                "case_count": len(cases),
            },
        ):
            results = []
            for case in cases:
                results.append(
                    await evaluate_case(
                        service,
                        case,
                        adapter_name=args.adapter,
                        seed_limit=args.seed_limit,
                        candidate_limit=args.candidate_limit,
                        expand_hops=args.expand_hops,
                        node_limit=args.node_limit,
                        edge_limit=args.edge_limit,
                    )
                )
    finally:
        langfuse_flush()
    quality_metric_names = [
        "seed_card_recall_at_k",
        "seed_card_precision_at_k",
        "expanded_card_recall",
        "edge_recall",
        "relation_kind_recall",
    ]
    completeness_metric_names = [
        "opened_card_focus_evidence_rate",
        "opened_edge_basis_rate",
    ]
    labeled_results = [result for result in results if result["labeled"]]
    return {
        "session_id": session_id,
        "target": args.target,
        "adapter_name": args.adapter,
        "total": len(results),
        "labeled": len(labeled_results),
        "exploratory": len(results) - len(labeled_results),
        "passed": sum(
            1 for result in labeled_results if result["passed"]
        ),
        "failed": sum(
            1 for result in labeled_results if not result["passed"]
        ),
        "quality_metrics": {
            name: _average_metric(labeled_results, name)
            for name in quality_metric_names
        }
        if labeled_results
        else None,
        "completeness_metrics": {
            name: round(
                sum(result["metrics"][name] for result in results)
                / len(results),
                4,
            )
            for name in completeness_metric_names
        },
        "forbidden_seed_card_hits": sum(
            result["metrics"]["forbidden_seed_card_hits"]
            for result in results
        ),
        "forbidden_expanded_card_hits": sum(
            result["metrics"]["forbidden_expanded_card_hits"]
            for result in results
        ),
        "results": results,
    }


def _set(values: Any) -> set[str]:
    if not isinstance(values, list):
        return set()
    return {
        str(value).strip()
        for value in values
        if str(value).strip()
    }


def _recall(expected: set[str], found: set[str]) -> float | None:
    if not expected:
        return None
    return len(expected.intersection(found)) / len(expected)


def _precision(expected: set[str], found: set[str]) -> float | None:
    if not expected:
        return None
    if not found:
        return 0.0
    return len(expected.intersection(found)) / len(found)


def _is_complete(value: float | None) -> bool:
    return value is None or value >= 1.0


def _average_metric(
    results: list[dict[str, Any]],
    name: str,
) -> float | None:
    values = [
        result["metrics"][name]
        for result in results
        if result["metrics"][name] is not None
    ]
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _non_empty_rate(items: list[dict[str, Any]], field: str) -> float:
    if not items:
        return 1.0
    return sum(
        1
        for item in items
        if str(item.get(field) or "").strip()
    ) / len(items)


def main() -> None:
    args = parse_args()
    result = asyncio.run(run(args))
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
