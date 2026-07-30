#!/usr/bin/env python3
"""演示：把 Source Record 编译并写入知识图谱。

这个脚本演示《知识编译与写入技术方案》的核心能力：

    Source Record
      -> KnowledgeService.compile_kg()
      -> KnowledgeCompiler
      -> FinancialKGAdapter
      -> Node / Edge / Evidence
      -> Fact Store
      -> Incremental Index Refresh
      -> Research Context Retrieval

它和 `1_kg_source_record_projection_demo.py` 的关系：

- `1_kg_source_record_projection_demo.py`：演示 Raw Row 如何变成 Source Record。
- 本脚本：演示 Source Record 如何变成知识图谱事实，并如何被检索出来。

每个步骤都可以单独运行：

- Step 0：打印脚本配置。
  用来看本次写入哪个库、使用哪个 adapter、是否 dry-run、编译并发是多少。

- Step 1：编译受控 Source Record，但不写库。
  用来观察 Source Record 经过编译后会产生多少节点、关系、证据，以及是否有失败项。

- Step 2：写入受控 Source Record 到生产 KG。
  用来演示 Fact Store 写入、关系证据绑定和增量索引刷新。写入的是 demo 前缀的受控样例数据。

- Step 3：演示单条坏 Source Record 不阻断整批。
  同一批里混入一条非法记录，正常记录仍然会编译，坏记录进入 failed_records。

- Step 4：检索刚才写入的上下文。
  用 deterministic_plan 查询图谱，观察命中的节点、关系、证据和检索通道。

- Step 5：质量扫描。
  检查当前 KG 事实的基础质量指标。默认不写 review 队列，避免演示脚本制造复核数据。

- Step 6：可选，从真实 ft_news 投影后直接编译写入。
  这一步可能调用 LLM，也会写真实新闻入图。默认注释，需要时手动放开。

- Step 6.5：检索 Step 6 写入的真实 ft_news 上下文。
  用真实新闻标题/关键词查询 KG，验证刚写入的节点、关系、证据是否能被检索消费。

- Step 7：把本次演示输出写入 generated_compile_and_write_demo.json。
  方便后续打开完整查看，不依赖终端输出。

运行方式：

    python "docs/6. 使用说明/知识图谱/2_kg_compile_and_write_demo.py"

不需要命令行参数。需要运行哪个步骤，就在 main() 里注释或放开对应函数调用。
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
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
    KnowledgeQualityScanCommand,
    KnowledgeResearchContextCommand,
    KnowledgeSourceProjectionCommand,
)
from src.application.services.knowledge_llm_config import kg_llm_config_summary  # noqa: E402
from src.application.services.knowledge_service import create_knowledge_service  # noqa: E402
from src.infrastructure.connections import get_session  # noqa: E402
from src.infrastructure.llm_proxy.service import get_llm_gateway_service  # noqa: E402


OUTPUT_FILE = Path(__file__).with_name("generated_compile_and_write_demo.json")

# 修改这里即可控制演示范围。按用户要求，默认和投影 demo 一样使用生产库。
TARGET = "prod"
ADAPTER = "financial"
CONCURRENCY = 2
CONTROLLED_DRY_RUN = True
WRITE_DRY_RUN = False
REAL_NEWS_LIMIT = 2
QUERY = "宁德时代 固态电池 产业链 资金流入"

DEMO_TS = "2026-05-14T09:30:00+08:00"
DEMO_PREFIX = "usage_demo_compile_write"


def main() -> None:
    """通过注释/放开下面函数，决定本次要运行哪些步骤。"""

    outputs: dict[str, Any] = {}

    step_0_print_config()

    # Step 1：只编译不写库，适合先观察编译产物。
    # outputs["dry_run_compile"] = step_1_compile_controlled_records_dry_run()

    # # Step 2：写入受控 demo 数据到生产 KG。
    # outputs["write_compile"] = step_2_write_controlled_records()

    # # Step 3：演示坏记录不会阻断整批。
    # outputs["failure_isolation"] = step_3_compile_with_one_bad_record()

    # # Step 4：检索刚才写入后的上下文。
    # outputs["research_context"] = step_4_query_written_context()

    # # Step 5：质量扫描。默认不写 review 队列。
    # outputs["quality_scan"] = step_5_quality_scan()

    # Step 6：可选，真实 ft_news -> Source Record -> compile -> write。
    # 这一步可能调用 LLM，也会写真实新闻入图。需要时再放开。
    outputs["real_ft_news_compile"] = step_6_project_real_ft_news_and_compile()
    print(f'{outputs=}')
    # Step 6.5：检索 Step 6 写入的真实新闻上下文，验证写入后能被消费。
    outputs["real_ft_news_retrieval"] = step_6_5_query_real_ft_news_context(outputs["real_ft_news_compile"])

    step_7_write_output(outputs)


def step_0_print_config() -> None:
    print("\n[Step 0] 配置")
    print("[demo] Source Record -> 编译 -> 写入 -> 增量刷新 -> 检索")
    pprint(
        {
            "target": TARGET,
            "adapter": ADAPTER,
            "controlled_dry_run": CONTROLLED_DRY_RUN,
            "write_dry_run": WRITE_DRY_RUN,
            "concurrency": CONCURRENCY,
            "query": QUERY,
            "output_file": str(OUTPUT_FILE),
            "kg_llm": kg_llm_config_summary(),
            "llm_proxy": _llm_proxy_summary(),
        },
        sort_dicts=False,
    )


def step_1_compile_controlled_records_dry_run() -> dict[str, Any]:
    """编译受控 Source Record，但不写库。"""

    print("\n[Step 1] 编译受控 Source Record dry-run")
    result = _compile_records(controlled_source_records(), dry_run=CONTROLLED_DRY_RUN)
    _print_compile_summary(result)
    return result


def step_2_write_controlled_records() -> dict[str, Any]:
    """把受控 Source Record 写入 KG，并触发增量索引刷新。"""

    print("\n[Step 2] 写入受控 Source Record 到 KG")
    result = _compile_records(controlled_source_records(), dry_run=WRITE_DRY_RUN)
    _print_compile_summary(result)
    _print_index_refresh(result.get("index_refresh") or {})
    return result


def step_3_compile_with_one_bad_record() -> dict[str, Any]:
    """演示 normalize / contract 失败只影响单条记录。"""

    print("\n[Step 3] 单条坏 Source Record 失败隔离")
    records = [
        controlled_source_records()[0],
        {
            "source_type": "stock_basics",
            "source_id": f"{DEMO_PREFIX}:bad_stock_missing_code",
            "observed_at": DEMO_TS,
            "payload": {
                "source_id": f"{DEMO_PREFIX}:bad_stock_missing_code",
                "exchange": "SZ",
                "name": "缺少代码的股票",
            },
            "metadata": {
                "external_source": "kg_compile_and_write_demo",
                "source_origin": "controlled_bad_record",
            },
        },
    ]
    result = _compile_records(records, dry_run=True)
    _print_compile_summary(result)
    print("\n[failures]")
    for item in result.get("failures", []):
        pprint(item, sort_dicts=False)
    return result


def step_4_query_written_context() -> dict[str, Any]:
    """查询写入后的 KG 上下文。"""

    print("\n[Step 4] 检索写入后的投研上下文")
    service = create_knowledge_service(target=TARGET)
    result = asyncio.run(
        service.build_research_context_for(
            KnowledgeResearchContextCommand(
                adapter_name=ADAPTER,
                target=TARGET,
                query=QUERY,
                retrieval_mode="deterministic_plan",
                graph_depth=3,
                graph_limit=20,
                wiki_limit=10,
                evidence_limit=20,
                max_chars=5000,
            )
        )
    ).to_dict()
    _print_context_summary(result)
    return result


def step_5_quality_scan() -> dict[str, Any]:
    """运行质量扫描。默认不持久化 review 队列。"""

    print("\n[Step 5] 质量扫描")
    service = create_knowledge_service(target=TARGET)
    result = asyncio.run(
        service.quality_scan_for(
            KnowledgeQualityScanCommand(
                adapter_name=ADAPTER,
                target=TARGET,
                persist_review=False,
            )
        )
    ).to_dict()
    pprint(
        {
            "ok": result.get("ok"),
            "metrics": result.get("metrics"),
            "issues": len(result.get("issues", [])),
            "review_items": result.get("review_items"),
        },
        sort_dicts=False,
    )
    return result


def step_6_project_real_ft_news_and_compile() -> dict[str, Any]:
    """可选：真实 ft_news -> Source Record -> compile -> write。"""

    print("\n[Step 6] 真实 ft_news 投影并编译写入")
    service = create_knowledge_service(target=TARGET)
    projection = asyncio.run(
        service.project_sources(
            KnowledgeSourceProjectionCommand(
                target=TARGET,
                sources=["ft_news"],
                codes=[],
                limit=REAL_NEWS_LIMIT,
                include_skipped=True,
            )
        )
    ).to_dict()
    print(f"[projection] records={len(projection['records'])} skipped={len(projection['skipped'])}")
    result = _compile_records(projection["records"], dry_run=False)
    _print_compile_summary(result)
    return {"projection": projection, "compile": result}


def step_6_5_query_real_ft_news_context(real_ft_news_output: dict[str, Any] | None = None) -> dict[str, Any]:
    """检索 Step 6 写入的真实新闻上下文。"""

    print("\n[Step 6.5] 检索真实 ft_news 写入后的上下文")
    real_ft_news_output = real_ft_news_output or _load_real_ft_news_output_from_file()
    projection = real_ft_news_output.get("projection") or {}
    compile_result = real_ft_news_output.get("compile") or {}
    records = projection.get("records") or []
    if not records:
        print("[real retrieval] no projected records found, skip")
        return {"skipped": True, "reason": "no projected records"}

    queries = _real_ft_news_queries(records)
    evidence_ids = set(compile_result.get("evidence_ids") or [])
    results: list[dict[str, Any]] = []
    for index, query in enumerate(queries, start=1):
        print(f"\n[real retrieval] [{index}/{len(queries)}] query={query}")
        context = _query_context(query, graph_depth=2, graph_limit=20, wiki_limit=8, evidence_limit=20)
        matched_evidence_ids = _matched_evidence_ids(context)
        expected_evidence_hit = bool(evidence_ids & matched_evidence_ids) if evidence_ids else None
        _print_context_summary(context)
        pprint(
            {
                "expected_evidence_hit": expected_evidence_hit,
                "expected_evidence_ids": sorted(evidence_ids)[:8],
                "matched_evidence_ids": sorted(matched_evidence_ids)[:8],
            },
            sort_dicts=False,
        )
        results.append(
            {
                "query": query,
                "expected_evidence_hit": expected_evidence_hit,
                "matched_evidence_ids": sorted(matched_evidence_ids),
                "context": context,
            }
        )
    return {
        "queries": queries,
        "expected_evidence_ids": sorted(evidence_ids),
        "results": results,
    }


def step_7_write_output(outputs: dict[str, Any]) -> None:
    print("\n[Step 7] 写入演示输出 JSON")
    OUTPUT_FILE.write_text(json.dumps(outputs, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"[output] written: {OUTPUT_FILE}")


def controlled_source_records() -> list[dict[str, Any]]:
    """受控 Source Record：覆盖实体快照、关系断言、结构化信号和事件断言。"""

    return [
        {
            "source_type": "stock_basics",
            "source_id": f"{DEMO_PREFIX}:stock:300750",
            "record_kind": "entity_snapshot",
            "observed_at": DEMO_TS,
            "payload": {
                "source_id": f"{DEMO_PREFIX}:stock:300750",
                "exchange": "SZ",
                "code": "300750",
                "name": "宁德时代",
                "company_name": "宁德时代新能源科技股份有限公司",
                "aliases": ["CATL", "300750", "300750.SZ"],
                "status": "active",
            },
            "metadata": _metadata("controlled_stock", "300750"),
        },
        {
            "source_type": "concept_components",
            "source_id": f"{DEMO_PREFIX}:concept:solid_state_battery:300750",
            "record_kind": "relation_assertion",
            "observed_at": DEMO_TS,
            "payload": {
                "source_id": f"{DEMO_PREFIX}:concept:solid_state_battery:300750",
                "taxonomy": "theme",
                "component_name": "固态电池产业链",
                "member_stock_exchange": "SZ",
                "member_stock_code": "300750",
                "member_stock_name": "宁德时代",
                "weight": 0.92,
            },
            "metadata": _metadata("controlled_concept_component", "solid_state_battery:300750"),
        },
        {
            "source_type": "derived_signal",
            "source_id": f"{DEMO_PREFIX}:signal:catl_flow",
            "record_kind": "structured_signal",
            "observed_at": DEMO_TS,
            "payload": {
                "source_id": f"{DEMO_PREFIX}:signal:catl_flow",
                "signal_id": f"{DEMO_PREFIX}:signal:catl_flow",
                "target_ref": {
                    "type": "stock",
                    "exchange": "SZ",
                    "code": "300750",
                    "name": "宁德时代",
                },
                "signal_type": "capital_flow",
                "observed_at": DEMO_TS,
                "value": 1.28,
                "unit": "亿元",
                "window": "1d",
                "confidence": 0.88,
                "title": "宁德时代资金流入改善",
            },
            "metadata": _metadata("controlled_signal", "catl_flow"),
        },
        {
            "source_type": "l1_events",
            "source_id": f"{DEMO_PREFIX}:event:solid_state_policy",
            "record_kind": "event_assertion",
            "observed_at": DEMO_TS,
            "payload": {
                "source_id": f"{DEMO_PREFIX}:event:solid_state_policy",
                "event_id": f"{DEMO_PREFIX}:event:solid_state_policy",
                "event_type": "policy_support",
                "event_time": DEMO_TS,
                "title": "固态电池政策支持带动宁德时代产业链预期",
                "mentioned_entities": [
                    {
                        "type": "concept",
                        "taxonomy": "theme",
                        "name": "固态电池产业链",
                        "confidence": 0.9,
                    }
                ],
                "affected_entities": [
                    {
                        "type": "stock",
                        "exchange": "SZ",
                        "code": "300750",
                        "name": "宁德时代",
                        "direction": "positive",
                        "reason": "政策支持提升固态电池产业链预期",
                        "confidence": 0.86,
                    }
                ],
            },
            "metadata": _metadata("controlled_l1_event", "solid_state_policy"),
        },
    ]


def _compile_records(records: list[dict[str, Any]], *, dry_run: bool) -> dict[str, Any]:
    service = create_knowledge_service(target=TARGET)
    result = asyncio.run(
        service.compile_kg(
            KnowledgeCompileCommand(
                adapter_name=ADAPTER,
                records=records,
                target=TARGET,
                dry_run=dry_run,
                request_id=f"{DEMO_PREFIX}:{'dry_run' if dry_run else 'write'}",
                concurrency=CONCURRENCY,
            )
        )
    )
    return result.to_dict()


def _query_context(
    query: str,
    *,
    graph_depth: int,
    graph_limit: int,
    wiki_limit: int,
    evidence_limit: int,
) -> dict[str, Any]:
    service = create_knowledge_service(target=TARGET)
    return asyncio.run(
        service.build_research_context_for(
            KnowledgeResearchContextCommand(
                adapter_name=ADAPTER,
                target=TARGET,
                query=query,
                retrieval_mode="deterministic_plan",
                graph_depth=graph_depth,
                graph_limit=graph_limit,
                wiki_limit=wiki_limit,
                evidence_limit=evidence_limit,
                max_chars=5000,
            )
        )
    ).to_dict()


def _real_ft_news_queries(records: list[dict[str, Any]]) -> list[str]:
    queries: list[str] = []
    for record in records[:REAL_NEWS_LIMIT]:
        payload = record.get("payload") or {}
        title = str(payload.get("title") or "").strip()
        source_id = str(record.get("source_id") or payload.get("source_id") or "").strip()
        entities = [
            str(entity.get("name") or entity.get("canonical_name") or "").strip()
            for entity in (payload.get("mentioned_entities") or []) + (payload.get("affected_entities") or [])
            if isinstance(entity, dict) and str(entity.get("name") or entity.get("canonical_name") or "").strip()
        ]
        query = " ".join(part for part in [title, *entities[:5], source_id] if part)
        if query:
            queries.append(query[:300])
    return queries or [QUERY]


def _matched_evidence_ids(context: dict[str, Any]) -> set[str]:
    evidence_ids: set[str] = set()
    for evidence_id in context.get("evidence_refs") or []:
        if evidence_id:
            evidence_ids.add(str(evidence_id))
    for hit in context.get("hits") or []:
        for evidence_id in hit.get("evidence_ids") or []:
            if evidence_id:
                evidence_ids.add(str(evidence_id))
    for edge in context.get("matched_edges") or []:
        for evidence_id in edge.get("evidence_ids") or []:
            if evidence_id:
                evidence_ids.add(str(evidence_id))
    return evidence_ids


def _load_real_ft_news_output_from_file() -> dict[str, Any]:
    if not OUTPUT_FILE.exists():
        return {}
    try:
        data = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data.get("real_ft_news_compile") or {}


def _metadata(source_table: str, source_pk: str) -> dict[str, Any]:
    return {
        "source_table": source_table,
        "source_pk": source_pk,
        "external_source": "kg_compile_and_write_demo",
        "source_origin": "controlled_demo",
        "projection_rule_version": "demo-v1",
    }


def _print_compile_summary(result: dict[str, Any]) -> None:
    pprint(
        {
            "adapter_name": result.get("adapter_name"),
            "run_id": result.get("run_id"),
            "dry_run": result.get("dry_run"),
            "nodes": result.get("nodes"),
            "edges": result.get("edges"),
            "evidence": result.get("evidence"),
            "failed_records": result.get("failed_records"),
            "warnings": result.get("warnings"),
            "sample_node_ids": result.get("node_ids", [])[:8],
            "sample_edge_ids": result.get("edge_ids", [])[:8],
            "sample_evidence_ids": result.get("evidence_ids", [])[:8],
        },
        sort_dicts=False,
    )


def _print_index_refresh(index_refresh: dict[str, Any]) -> None:
    print("\n[index_refresh]")
    if not index_refresh:
        print("none")
        return
    pprint(
        {
            "mode": index_refresh.get("mode"),
            "graph_adjacency": index_refresh.get("graph_adjacency"),
            "evidence_chunks": index_refresh.get("evidence_chunks"),
            "wiki_pages": index_refresh.get("wiki_pages"),
            "hybrid_chunks": index_refresh.get("hybrid_chunks"),
        },
        sort_dicts=False,
    )


def _print_context_summary(result: dict[str, Any]) -> None:
    nodes = result.get("matched_nodes", [])
    edges = result.get("matched_edges", [])
    hits = result.get("hits", [])
    print("\n[context summary]")
    pprint(
        {
            "query": result.get("query"),
            "mode": result.get("mode"),
            "hits": len(hits),
            "matched_nodes": len(nodes),
            "matched_edges": len(edges),
            "evidence_refs": len(result.get("evidence_refs", [])),
            "channels_used": result.get("retrieval_channels_used"),
            "node_type_counts": dict(Counter(node.get("node_type") for node in nodes)),
            "relation_type_counts": dict(Counter(edge.get("relation_type") for edge in edges)),
            "top_hits": [_compact_hit(hit) for hit in hits[:5]],
            "top_nodes": [_compact_node(node) for node in nodes[:8]],
            "top_edges": [_compact_edge(edge) for edge in edges[:8]],
        },
        sort_dicts=False,
    )


def _compact_hit(hit: dict[str, Any]) -> dict[str, Any]:
    return {
        "hit_id": hit.get("hit_id"),
        "title": hit.get("title"),
        "score": hit.get("score"),
        "type": hit.get("hit_type"),
    }


def _compact_node(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_id": node.get("node_id"),
        "node_type": node.get("node_type"),
        "name": node.get("canonical_name"),
    }


def _compact_edge(edge: dict[str, Any]) -> dict[str, Any]:
    return {
        "edge_id": edge.get("edge_id"),
        "relation_type": edge.get("relation_type"),
        "source": edge.get("source_node_id"),
        "target": edge.get("target_node_id"),
        "evidence_ids": edge.get("evidence_ids", [])[:3],
    }


def _llm_proxy_summary() -> dict[str, Any]:
    health = get_llm_gateway_service().health()
    return {
        "default_provider": health.get("default_provider"),
        "default_model": health.get("default_model"),
        "model_routes": health.get("model_routes"),
        "providers": health.get("providers"),
    }


def inspect_demo_rows() -> None:
    """手动调试用：查看 demo 前缀写入了哪些 KG 行。默认 main 不调用。"""

    with get_session(TARGET) as session:
        for table, id_col in [
            ("kg_nodes", "node_id"),
            ("kg_edges", "edge_id"),
            ("kg_evidence", "evidence_id"),
            ("kg_wiki_pages", "page_id"),
        ]:
            rows = session.execute(
                text(f"select {id_col} from {table} where adapter_name = :adapter order by {id_col}")
                if table != "kg_wiki_pages"
                else text(f"select {id_col} from {table} where adapter_name = :adapter order by {id_col}")
            ).fetchall()
            demo_rows = [row[0] for row in rows if DEMO_PREFIX in row[0]]
            print(f"[{table}] demo_rows={len(demo_rows)}")
            for row_id in demo_rows[:10]:
                print(" ", row_id)


if __name__ == "__main__":
    main()
