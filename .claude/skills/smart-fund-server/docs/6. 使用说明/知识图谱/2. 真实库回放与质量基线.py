#!/usr/bin/env python3
"""Run the real-database KG replay baseline.

This script is the executable version of "2. 真实库回放与质量基线.ipynb".
It writes controlled baseline records, rebuilds wiki/indexes, and replays
deterministic plus Agentic A-RAG bad cases against the real database/Milvus/LLM.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from pprint import pprint
from typing import Any

from sqlalchemy import text


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
    KnowledgeBadCaseReplayCommand,
    KnowledgeCompileCommand,
    KnowledgeIncrementalRefreshCommand,
    KnowledgeRebuildIndexesCommand,
    KnowledgeRebuildWikiCommand,
    KnowledgeResearchContextBadCase,
)
from src.application.services.knowledge_service import create_knowledge_service  # noqa: E402
from src.infrastructure.config import settings  # noqa: E402
from src.infrastructure.connections import get_session  # noqa: E402
from src.infrastructure.vector_store.milvus_hybrid_store import MilvusHybridStore  # noqa: E402


BASELINE_TS = "2026-04-29T09:30:00+08:00"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run KG real DB replay baseline.")
    parser.add_argument("--target", default="prod", choices=["prod", "test"])
    parser.add_argument("--adapter", default="financial")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-seed", action="store_true", help="skip controlled baseline compile")
    parser.add_argument("--skip-incremental", action="store_true", help="skip real ft_* incremental refresh")
    parser.add_argument("--skip-rebuild", action="store_true", help="skip rebuild-wiki and rebuild-indexes")
    parser.add_argument("--skip-agentic", action="store_true", help="skip Agentic A-RAG replay")
    parser.add_argument("--strict-agentic", action="store_true", help="treat Agentic A-RAG replay failures as script failures")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--stock-limit", type=int, default=50)
    parser.add_argument("--news-limit", type=int, default=5)
    parser.add_argument("--codes", nargs="*", default=["300750", "603305"])
    parser.add_argument("--max-chars", type=int, default=8000)
    return parser.parse_args()


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    logging.getLogger("src.domain.knowledge.compiler").setLevel(logging.INFO)
    logging.getLogger("src.domain.knowledge.retrieval").setLevel(logging.INFO)
    logging.getLogger("src.application.services.knowledge_service").setLevel(logging.INFO)
    logging.getLogger("src.infrastructure.llm_proxy.service").setLevel(logging.INFO)


def stock_entity(code: str, name: str, exchange: str = "SZ", confidence: float = 0.95) -> dict[str, Any]:
    return {
        "type": "stock",
        "exchange": exchange,
        "code": code,
        "name": name,
        "aliases": [code, f"{code}.{exchange}"],
        "confidence": confidence,
    }


def concept(name: str, *, taxonomy: str = "baseline", confidence: float = 0.86, **extra) -> dict[str, Any]:
    return {"type": "concept", "taxonomy": taxonomy, "name": name, "confidence": confidence, **extra}


def industry(name: str, *, taxonomy: str = "baseline", confidence: float = 0.86, **extra) -> dict[str, Any]:
    return {"type": "industry", "taxonomy": taxonomy, "name": name, "confidence": confidence, **extra}


def macro_indicator(code: str, name: str, confidence: float = 0.9) -> dict[str, Any]:
    return {
        "type": "macro_indicator",
        "indicator_code": code,
        "name": name,
        "confidence": confidence,
    }


def baseline_records() -> list[dict[str, Any]]:
    return [
        {
            "source_type": "stock_basics",
            "source_id": "notebook_baseline:stock:300750",
            "observed_at": BASELINE_TS,
            "payload": {
                "source_id": "notebook_baseline:stock:300750",
                "exchange": "SZ",
                "code": "300750",
                "name": "宁德时代",
                "company_name": "宁德时代新能源科技股份有限公司",
                "aliases": ["CATL", "300750", "300750.SZ"],
                "status": "active",
            },
        },
        {
            "source_type": "stock_basics",
            "source_id": "notebook_baseline:stock:603305",
            "observed_at": BASELINE_TS,
            "payload": {
                "source_id": "notebook_baseline:stock:603305",
                "exchange": "SH",
                "code": "603305",
                "name": "旭升集团",
                "aliases": ["603305", "603305.SH"],
                "status": "active",
            },
        },
        {
            "source_type": "news_articles",
            "source_id": "notebook_baseline:news:catl_overseas_capacity",
            "observed_at": BASELINE_TS,
            "payload": {
                "source_id": "notebook_baseline:news:catl_overseas_capacity",
                "document_id": "notebook_baseline:news:catl_overseas_capacity",
                "title": "宁德时代海外产能扩张带动储能供应链订单",
                "published_at": BASELINE_TS,
                "source_name": "Notebook基线",
                "text": "宁德时代推进欧洲和东南亚海外产能扩张，储能电芯、快充电池和新能源车产业链订单预期改善。市场认为海外工厂投产有助于降低贸易壁垒，并改善供应链交付能力。",
                "mentioned_entities": [
                    stock_entity("300750", "宁德时代"),
                    concept("海外产能"),
                    concept("快充"),
                    industry("储能产业链"),
                ],
                "affected_entities": [
                    {**stock_entity("300750", "宁德时代"), "direction": "positive", "reason": "海外产能扩张改善交付能力"},
                    {**industry("储能产业链"), "direction": "positive", "reason": "储能电芯订单预期改善"},
                    {**industry("新能源车产业链"), "direction": "positive", "reason": "快充和动力电池需求提升"},
                ],
            },
        },
        {
            "source_type": "news_articles",
            "source_id": "notebook_baseline:news:ma_industry_rotation",
            "observed_at": BASELINE_TS,
            "payload": {
                "source_id": "notebook_baseline:news:ma_industry_rotation",
                "document_id": "notebook_baseline:news:ma_industry_rotation",
                "title": "并购重组政策活跃提升券商、半导体设备和新能源车风险偏好",
                "published_at": BASELINE_TS,
                "source_name": "Notebook基线",
                "text": "资本市场并购重组审核提速，市场预期产业整合会带动券商投行业务、半导体设备国产替代和新能源车产业链估值修复。并购重组主题也可能提升中小市值公司的交易活跃度。",
                "mentioned_entities": [
                    concept("并购重组"),
                    industry("券商"),
                    industry("半导体设备"),
                    industry("新能源车产业链"),
                ],
                "affected_entities": [
                    {**industry("券商"), "direction": "positive", "reason": "投行业务弹性提升"},
                    {**industry("半导体设备"), "direction": "positive", "reason": "产业整合和国产替代预期加强"},
                    {**industry("新能源车产业链"), "direction": "positive", "reason": "估值修复和整合预期"},
                    {**concept("中小市值"), "direction": "positive", "reason": "交易活跃度提升"},
                ],
            },
        },
        {
            "source_type": "policy_news",
            "source_id": "notebook_baseline:policy:low_rate_growth_assets",
            "observed_at": BASELINE_TS,
            "payload": {
                "source_id": "notebook_baseline:policy:low_rate_growth_assets",
                "document_id": "notebook_baseline:policy:low_rate_growth_assets",
                "title": "低利率环境和资本市场改革提升成长资产估值",
                "published_at": BASELINE_TS,
                "source_name": "Notebook基线",
                "text": "低利率环境降低权益资产折现率，资本市场改革改善风险偏好。成长资产、科技创新、半导体设备和新能源车产业链可能受益，但高股息资产的相对吸引力可能下降。",
                "mentioned_entities": [
                    macro_indicator("low_rate_environment", "低利率环境"),
                    concept("资本市场改革"),
                    concept("成长资产"),
                ],
                "affected_entities": [
                    {**concept("成长资产"), "direction": "positive", "reason": "折现率下降提升估值"},
                    {**industry("半导体设备"), "direction": "positive", "reason": "成长风格风险偏好改善"},
                    {**industry("新能源车产业链"), "direction": "positive", "reason": "成长股估值弹性提升"},
                    {**concept("高股息资产"), "direction": "negative", "reason": "相对吸引力下降"},
                ],
            },
        },
        {
            "source_type": "news_articles",
            "source_id": "notebook_baseline:news:middle_east_assets",
            "observed_at": BASELINE_TS,
            "payload": {
                "source_id": "notebook_baseline:news:middle_east_assets",
                "document_id": "notebook_baseline:news:middle_east_assets",
                "title": "中东冲突升温推升原油和黄金避险需求",
                "published_at": BASELINE_TS,
                "source_name": "Notebook基线",
                "text": "中东冲突升温可能扰动能源运输通道，原油价格和黄金避险需求上升。航空运输行业面临燃油成本压力，化工产业链也会受到原油成本传导影响。",
                "mentioned_entities": [
                    concept("中东冲突"),
                    {"type": "commodity", "name": "原油", "confidence": 0.88},
                    {"type": "commodity", "name": "黄金", "confidence": 0.88},
                    industry("航空运输"),
                ],
                "affected_entities": [
                    {"type": "commodity", "name": "原油", "direction": "positive", "confidence": 0.88, "reason": "供应扰动和运输通道风险"},
                    {"type": "commodity", "name": "黄金", "direction": "positive", "confidence": 0.86, "reason": "避险需求上升"},
                    {**industry("航空运输"), "direction": "negative", "reason": "燃油成本压力上升"},
                    {**industry("化工产业链"), "direction": "negative", "reason": "原油成本向下游传导"},
                ],
            },
        },
        {
            "source_type": "derived_signal",
            "source_id": "notebook_baseline:signal:catl_flow",
            "observed_at": BASELINE_TS,
            "payload": {
                "source_id": "notebook_baseline:signal:catl_flow",
                "signal_type": "market_flow.stock_net_inflow",
                "observed_at": BASELINE_TS,
                "target_ref": stock_entity("300750", "宁德时代"),
                "title": "宁德时代资金净流入改善",
                "value": 1,
                "unit": "signal",
                "window": "1d",
                "confidence": 0.9,
                "raw_data": {"net_inflow_signal": "positive", "source": "notebook_baseline"},
            },
        },
        {
            "source_type": "derived_signal",
            "source_id": "notebook_baseline:signal:low_rate",
            "observed_at": BASELINE_TS,
            "payload": {
                "source_id": "notebook_baseline:signal:low_rate",
                "signal_type": "macro.low_rate_environment",
                "observed_at": BASELINE_TS,
                "target_ref": macro_indicator("low_rate_environment", "低利率环境"),
                "title": "低利率环境利好成长资产",
                "value": 1,
                "unit": "signal",
                "window": "latest",
                "confidence": 0.9,
                "raw_data": {"rate_signal": "low", "source": "notebook_baseline"},
            },
        },
    ]


def deterministic_bad_cases() -> list[dict[str, Any]]:
    return [
        _case("real_baseline_catl_recent_events", "宁德时代 300750 最近受哪些事件影响", ["宁德时代"], ["mentions"], 1),
        _case("real_baseline_ma_industry_targets", "并购重组对哪些行业有影响", ["并购重组", "券商"], ["mentions", "affects"], 2),
        _case("real_baseline_low_rate_beneficiaries", "低利率环境利好什么资产和行业", ["低利率环境", "成长资产"], ["mentions", "affects"], 2),
        _case("real_baseline_middle_east_asset_transmission", "中东冲突影响哪些资产和行业", ["中东冲突", "原油", "黄金"], ["mentions", "affects"], 3),
        _case("real_baseline_semantic_paraphrase_overseas_factory", "海外工厂投产会带动哪些产业链机会", ["海外产能", "储能产业链"], ["mentions"], 2),
    ]


def _case(
    case_id: str,
    query: str,
    expected_node_names: list[str],
    expected_relation_types: list[str],
    min_matched_nodes: int,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "query": query,
        "expected_node_names": expected_node_names,
        "expected_relation_types": expected_relation_types,
        "expected_channels_used": ["semantic_hybrid_search", "chunk_read"],
        "min_hits": 2,
        "min_evidence_refs": 1,
        "min_matched_nodes": min_matched_nodes,
        "min_matched_edges": 1,
        "retrieval_mode": "deterministic_plan",
    }


def agentic_cases_from(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for case in cases:
        # Agentic A-RAG 的价值就是由 LLM 自主选工具，不能硬断言固定 channel。
        # 仍然检查核心节点/关系/最小召回量，避免假通过。
        relaxed = dict(case, retrieval_mode="agentic_arag")
        relaxed["expected_channels_used"] = []
        result.append(relaxed)
    return result


async def compile_seed(service, args: argparse.Namespace) -> None:
    records = baseline_records()
    print(f"\n[seed] compile controlled records: {len(records)}")
    result = await service.compile_kg(
        KnowledgeCompileCommand(
            adapter_name=args.adapter,
            target=args.target,
            records=records,
            dry_run=args.dry_run,
            concurrency=args.concurrency,
        )
    )
    pprint(result.to_dict())
    if result.failed_records:
        raise AssertionError(result.to_dict())


async def incremental_refresh(service, args: argparse.Namespace) -> None:
    print("\n[incremental] refresh real ft_* sources")
    result = await service.refresh_financial_incremental(
        KnowledgeIncrementalRefreshCommand(
            target=args.target,
            codes=args.codes,
            stock_limit=args.stock_limit,
            news_limit=args.news_limit,
            dry_run=args.dry_run,
            concurrency=args.concurrency,
            rebuild_indexes=False,
        )
    )
    pprint(result.to_dict())
    failed_steps = [step for step in result.steps if step.get("failed_records", 0)]
    if failed_steps:
        raise AssertionError(failed_steps)


async def rebuild(service, args: argparse.Namespace) -> None:
    if args.dry_run:
        raise RuntimeError("DRY_RUN=True cannot rebuild wiki/indexes")
    print("\n[rebuild] wiki")
    wiki = await service.rebuild_wiki_for(
        KnowledgeRebuildWikiCommand(adapter_name=args.adapter, target=args.target)
    )
    pprint(wiki.to_dict())
    if wiki.pages <= 0:
        raise AssertionError(wiki.to_dict())

    print("\n[rebuild] graph/evidence/Milvus indexes")
    indexes = await service.rebuild_indexes_for(
        KnowledgeRebuildIndexesCommand(
            adapter_name=args.adapter,
            target=args.target,
            index_types=["graph_adjacency", "evidence_chunks", "hybrid_chunks"],
        )
    )
    pprint(indexes.to_dict())
    if indexes.graph_adjacency <= 0 or indexes.evidence_chunks <= 0 or indexes.hybrid_chunks <= 0:
        raise AssertionError(indexes.to_dict())


async def replay(
    service,
    args: argparse.Namespace,
    *,
    mode: str,
    cases: list[dict[str, Any]],
    fail_on_error: bool,
) -> dict[str, Any]:
    print(f"\n[replay] {mode}")
    result = await service.replay_research_context_bad_cases(
        KnowledgeBadCaseReplayCommand(
            adapter_name=args.adapter,
            target=args.target,
            cases=[KnowledgeResearchContextBadCase(**case) for case in cases],
            graph_depth=3,
            graph_limit=30,
            wiki_limit=10,
            evidence_limit=30,
            max_chars=args.max_chars,
        )
    )
    data = result.to_dict()
    pprint({key: data[key] for key in ["total", "passed", "failed", "metrics"]})
    for item in data["results"]:
        if item["passed"]:
            continue
        print(f"\nFAILED {item['case_id']}: {item['query']}")
        pprint({
            "missing_node_names": item["missing_node_names"],
            "missing_relation_types": item["missing_relation_types"],
            "missing_channels_used": item["missing_channels_used"],
            "metric_failures": item["metric_failures"],
            "channels_used": item["channels_used"],
        })
    if result.failed and fail_on_error:
        raise AssertionError(data)
    if result.failed:
        print(
            "\nAgentic A-RAG replay has failures but is observational by default. "
            "Use --strict-agentic if you want it to fail the script."
        )
    return data


def write_case_file(args: argparse.Namespace, cases: list[dict[str, Any]]) -> None:
    path = PROJECT_ROOT / "docs/6. 使用说明/知识图谱/generated_real_replay_bad_cases.json"
    path.write_text(
        json.dumps({"adapter_name": args.adapter, "target": args.target, "cases": cases}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n[cases] written: {path}")


def print_baseline_counts(target: str, adapter: str) -> None:
    sql = """
    select 'kg_nodes' table_name, count(*) count from kg_nodes where adapter_name = :adapter
    union all
    select 'kg_edges' table_name, count(*) count from kg_edges where adapter_name = :adapter
    union all
    select 'kg_evidence' table_name, count(*) count from kg_evidence where adapter_name = :adapter
    union all
    select 'kg_evidence_chunks' table_name, count(*) count from kg_evidence_chunks where adapter_name = :adapter
    """
    with get_session(target) as session:
        rows = [dict(row) for row in session.execute(text(sql), {"adapter": adapter}).mappings().all()]
    print("\n[counts]")
    pprint(rows)


async def main() -> None:
    args = parse_args()
    configure_logging()

    if settings.MILVUS_ENABLED is not True:
        raise RuntimeError("Milvus must be enabled")
    MilvusHybridStore().ensure_ready()

    service = create_knowledge_service(target=args.target)
    health = await service.health()
    pprint(health.to_dict())
    if health.status != "ok":
        raise RuntimeError(health.to_dict())

    cases = deterministic_bad_cases()
    write_case_file(args, cases)

    if not args.skip_seed:
        await compile_seed(service, args)
    if not args.skip_incremental:
        await incremental_refresh(service, args)
    if not args.skip_rebuild:
        await rebuild(service, args)

    await replay(service, args, mode="deterministic_plan", cases=cases, fail_on_error=True)
    if not args.skip_agentic:
        await replay(
            service,
            args,
            mode="agentic_arag",
            cases=agentic_cases_from(cases),
            fail_on_error=args.strict_agentic,
        )

    print_baseline_counts(args.target, args.adapter)
    print("\nOK")


if __name__ == "__main__":
    asyncio.run(main())
