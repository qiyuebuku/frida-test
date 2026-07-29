#!/usr/bin/env python3
"""使用人工标注集评测 Relation Discovery 各阶段质量。"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.application.services.relation_discovery_service import (  # noqa: E402
    RELATION_DISCOVERY_PIPELINE_VERSION,
    RelationDiscoveryService,
)
from src.domain.knowledge.relation_evaluation import (  # noqa: E402
    aggregate_relation_evaluation,
    evaluate_relation_case,
    validate_relation_evaluation_dataset,
)
from src.infrastructure.observability.langfuse_tracing import (  # noqa: E402
    langfuse_flush,
    langfuse_observation,
    langfuse_propagation_context,
    langfuse_update_span,
)
from src.infrastructure.persistence.repositories.knowledge_repository_impl import (  # noqa: E402
    KnowledgeRepositoryImpl,
)


DEFAULT_DATASET = Path(__file__).with_name("datasets") / "relation_discovery_eval.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Relation Discovery 批量标注评测")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET), help="标注集 JSON 路径")
    parser.add_argument("--adapter", default="", help="覆盖标注集 adapter")
    parser.add_argument("--target", choices=["prod", "test"], default="prod")
    parser.add_argument("--case-id", action="append", default=[], help="只执行指定案例，可重复")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--output", default="", help="评测报告路径，默认写入 /tmp")
    parser.add_argument("--fail-on-quality", action="store_true", help="存在不通过案例时退出码为 2")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def load_dataset(path: Path, selected_case_ids: list[str]) -> tuple[dict, list[dict]]:
    if not path.is_file():
        raise FileNotFoundError(f"关系评测集不存在: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("关系评测集顶层必须是 JSON object")
    cases = validate_relation_evaluation_dataset(data)
    selected = set(selected_case_ids)
    if selected:
        cases = [item for item in cases if item["case_id"] in selected]
        missing = sorted(selected - {item["case_id"] for item in cases})
        if missing:
            raise ValueError(f"指定 case_id 不存在: {missing}")
    return data, cases


async def evaluate_case(
    service: RelationDiscoveryService,
    case: dict,
    *,
    adapter: str,
    target: str,
) -> dict:
    trace_input = {
        "case_id": case["case_id"],
        "source_card_id": case["source_card_id"],
        "adapter": adapter,
        "target": target,
    }
    with langfuse_observation(
        name="kg.relation_discovery.eval.case",
        as_type="chain",
        input=trace_input,
        metadata={"description": case["description"]},
    ):
        relation_result = await service.discover_card_relations(
            [case["source_card_id"]],
            adapter_name=adapter,
            target=target,
            include_evaluation_details=True,
            persist_edges=False,
        )
        evaluation = evaluate_relation_case(case, relation_result)
        result = {
            "case": case,
            "evaluation": evaluation,
            "relation_discovery": relation_result,
        }
        langfuse_update_span(
            output={
                "passed": evaluation["passed"],
                "stage_metrics": evaluation["stage_metrics"],
                "relation_checks": evaluation["relation_checks"],
                "false_positive_hard_negative_ids": evaluation[
                    "false_positive_hard_negative_ids"
                ],
            },
            status_message="passed" if evaluation["passed"] else "quality_failed",
            level="DEFAULT" if evaluation["passed"] else "WARNING",
        )
        return result


async def run(args: argparse.Namespace) -> tuple[dict, Path]:
    dataset_path = Path(args.dataset).expanduser().resolve()
    dataset, cases = load_dataset(dataset_path, args.case_id)
    adapter = args.adapter or str(dataset.get("adapter") or "financial")
    service = RelationDiscoveryService(
        repository=KnowledgeRepositoryImpl(target=args.target),
    )
    case_outputs: list[dict] = []
    for case in cases:
        case_outputs.append(
            await evaluate_case(
                service,
                case,
                adapter=adapter,
                target=args.target,
            )
        )
    summary = aggregate_relation_evaluation(
        [item["evaluation"] for item in case_outputs]
    )
    result = {
        "status": "passed" if summary["all_passed"] else "quality_failed",
        "pipeline_version": RELATION_DISCOVERY_PIPELINE_VERSION,
        "dataset": str(dataset_path),
        "dataset_version": dataset.get("version"),
        "adapter": adapter,
        "target": args.target,
        "summary": summary,
        "cases": case_outputs,
    }
    session_id = args.session_id
    output = Path(args.output) if args.output else Path(
        f"/tmp/relation_discovery_eval_{session_id}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result, output


async def main_async(args: argparse.Namespace) -> int:
    args.session_id = args.session_id or f"relation-eval-{uuid4().hex[:12]}"
    trace_input = {
        "dataset": str(Path(args.dataset).expanduser()),
        "target": args.target,
        "case_ids": args.case_id,
        "pipeline_version": RELATION_DISCOVERY_PIPELINE_VERSION,
    }
    with langfuse_propagation_context(
        trace_name="kg.relation_discovery.eval",
        session_id=args.session_id,
        tags=["kg", "relation-discovery", "evaluation"],
        metadata=trace_input,
    ):
        with langfuse_observation(
            name="kg.relation_discovery.eval",
            as_type="evaluator",
            input=trace_input,
        ):
            result, output = await run(args)
            langfuse_update_span(
                output={"summary": result["summary"], "output_file": str(output)},
                status_message=result["status"],
                level="DEFAULT" if result["summary"]["all_passed"] else "WARNING",
            )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(f"\nLangfuse trace: kg.relation_discovery.eval")
    print(f"Session ID: {args.session_id}")
    print(f"评测报告: {output}")
    return 2 if args.fail_on_quality and not result["summary"]["all_passed"] else 0


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        code = asyncio.run(main_async(args))
    finally:
        langfuse_flush()
    raise SystemExit(code)


if __name__ == "__main__":
    main()
