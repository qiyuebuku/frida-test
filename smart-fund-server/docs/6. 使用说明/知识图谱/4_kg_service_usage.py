#!/usr/bin/env python3
"""Knowledge graph service usage script converted from kg_service_usage.ipynb.

Default behavior matches the notebook step 5: read real prod data and write real KG rows.
Use --dry-run or --no-compile when you only want to inspect projection output.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the knowledge graph real-data workflow.")
    parser.add_argument("--target", default="prod", choices=["prod", "test"], help="database target")
    parser.add_argument("--adapter", default="financial", help="adapter name")
    parser.add_argument("--api-port", default=8910, type=int, help="API port for optional HTTP checks")
    parser.add_argument("--limit", default=5, type=int, help="max rows per ft_* table")
    parser.add_argument("--concurrency", default=2, type=int, help="compile concurrency")
    parser.add_argument("--dry-run", action="store_true", help="compile without writing kg_* tables")
    parser.add_argument("--no-compile", action="store_true", help="only build and print projected records")
    parser.add_argument("--skip-post", action="store_true", help="skip wiki/index/query/quality/CLI sections after compile")
    parser.add_argument("--http", action="store_true", help="run optional HTTP API checks; requires API server to be running")
    parser.add_argument("--fixture-bootstrap", action="store_true", help="load fixture records at the optional appendix step")
    parser.add_argument("--verbose-samples", action="store_true", help="print full DB/source samples")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    # %% [markdown] 0: 知识图谱服务使用 Notebook
    # # 知识图谱服务使用 Notebook
    #
    # 这份 Notebook 用来验证知识图谱服务的真实使用链路。默认直接读取 `target=prod`，也就是当前真实业务库 `jettask` 中的 `kg_*` 表和业务源表，不再用 fixture 模拟真实数据。
    #
    # **架构边界**
    #
    # ```
    # ft_news / ft_market_flow / ft_market_cache / ft_sentiment / ft_macro_indicators
    #     ↓  （Notebook 作为业务方：规则投影 → KnowledgeCompileCommand.records）
    # service.compile_kg()
    #     ↓  （Adapter 内部自动 async enrichment：FinancialNewsExtractionStrategy）
    #     │   - 文本类（news_articles/policy_news）：LLM 抽取 mentioned_entities / affected_entities
    #     │   - 信号类（derived_signal）：规则直接进图，不走 LLM
    #     ↓
    # kg_nodes / kg_edges / kg_evidence
    # ```
    #
    # Notebook 只负责**规则投影**（把 ft_* 字段转成 Adapter records），**不自己调 LLM**。LLM enrichment 已经在 Adapter 的 `_payload_for_compile()` 里统一处理，通过 `ClaudeProxyService` 调用已有的 tmux 会话池。
    #
    # `kg_*` 是知识图谱服务自己的事实库表：`kg_nodes / kg_edges / kg_evidence` 等。`ft_*` 是业务原始/业务处理表。fixture 只保留在最后的可选附录里。

    # %% [markdown] 1: 0. 项目路径和基础配置
    # ## 0. 项目路径和基础配置
    #
    # 先把项目根目录加入 `sys.path`，后面的代码会复用当前项目里的数据库连接、应用服务和 DTO。

    # %% [code] 2
    from pathlib import Path
    import logging
    import sys

    WORKSPACE_ROOT = Path(__file__).resolve()
    while (
        not (WORKSPACE_ROOT / "smart-fund-server" / "src").is_dir()
        and WORKSPACE_ROOT.parent != WORKSPACE_ROOT
    ):
        WORKSPACE_ROOT = WORKSPACE_ROOT.parent

    PROJECT_ROOT = WORKSPACE_ROOT / "smart-fund-server"
    if not (PROJECT_ROOT / "src").is_dir():
        raise RuntimeError("cannot locate smart-fund-server project root")

    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    # 打开 compile 进度日志和 LLM 调用日志（每条 record 一行 + 每次 LLM 调用一行）
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", force=True)
    logging.getLogger("src.domain.knowledge.compiler").setLevel(logging.INFO)
    logging.getLogger("src.infrastructure.llm_proxy.service").setLevel(logging.INFO)

    TARGET = args.target
    ADAPTER = args.adapter
    API_PORT = args.api_port
    BASE_URL = f"http://127.0.0.1:{API_PORT}"

    PROJECT_ROOT, TARGET, ADAPTER, BASE_URL

    # %% [markdown] 3: 1. 直接检查数据库状态
    # ## 1. 直接检查数据库状态
    #
    # 这里先看真实数据库里是否已经有知识图谱表和数据。若 `kg_nodes / kg_edges / kg_evidence` 都是 0，说明还没有把业务数据编译进知识图谱。

    # %% [code] 4
    from pprint import pprint
    from sqlalchemy import text

    from src.infrastructure.connections import get_session

    KG_TABLES = [
        "kg_nodes",
        "kg_edges",
        "kg_evidence",
        "kg_edge_evidence",
        "kg_versions",
        "kg_wiki_pages",
        "kg_graph_adjacency",
        "kg_evidence_chunks",
        "kg_review_items",
        "kg_compilation_runs",
    ]

    def db_rows(sql: str, params: dict | None = None) -> list[dict]:
        with get_session(TARGET) as session:
            rows = session.execute(text(sql), params or {}).mappings().all()
        return [dict(row) for row in rows]

    def table_exists(table_name: str) -> bool:
        sql = """
        select exists (
          select 1 from information_schema.tables
          where table_schema = 'public' and table_name = :table_name
        ) as exists
        """
        return bool(db_rows(sql, {"table_name": table_name})[0]["exists"])

    def table_count(table_name: str) -> int | None:
        if not table_exists(table_name):
            return None
        # table_name comes from a fixed allow-list in this notebook.
        return int(db_rows(f"select count(*) as count from {table_name}")[0]["count"])

    kg_counts = {table: table_count(table) for table in KG_TABLES}
    pprint(kg_counts)

    missing_kg_tables = [table for table, count in kg_counts.items() if count is None]
    if missing_kg_tables:
        print("\n缺少 kg_* 表:", missing_kg_tables)
        print("已有业务库只需要补 schema/06_knowledge.sql；不要对 jettask 跑全量 init db。")

    # %% [markdown] 5: 2. 查看真实图谱事实库样本
    # ## 2. 查看真实图谱事实库样本
    #
    # 下面直接从 `kg_nodes / kg_edges / kg_evidence` 读取样本。这里看到的就是后续 Wiki、索引、检索上下文使用的数据基础。

    # %% [code] 6
    def sample_table(table_name: str, columns: str = "*", limit: int = 5, where: str = "true", params: dict | None = None) -> list[dict]:
        if not table_exists(table_name):
            print(f"表不存在: {table_name}")
            return []
        limit = max(1, min(int(limit), 50))
        # table/columns/where are controlled by this notebook; user input only goes through params.
        return db_rows(f"select {columns} from {table_name} where {where} limit {limit}", params or {})

    if args.verbose_samples:
        nodes = sample_table(
            "kg_nodes",
            "node_id, node_type, canonical_name, status, external_ids, properties",
            where="adapter_name = :adapter",
            params={"adapter": ADAPTER},
        )
        edges = sample_table(
            "kg_edges",
            "edge_id, relation_type, source_node_id, target_node_id, confidence_label, confidence_score, status",
            where="adapter_name = :adapter",
            params={"adapter": ADAPTER},
        )
        evidence = sample_table(
            "kg_evidence",
            "evidence_id, source_type, source_id, evidence_type, left(coalesce(content, ''), 300) as content_preview, payload",
            where="adapter_name = :adapter",
            params={"adapter": ADAPTER},
        )

        print("nodes:")
        pprint(nodes)
        print("\nedges:")
        pprint(edges)
        print("\nevidence:")
        pprint(evidence)
    else:
        print("跳过 kg_* 样本明细；需要时传 --verbose-samples。")

    # %% [markdown] 7: 3. 查看业务源表
    # ## 3. 查看业务源表
    #
    # 知识图谱服务本身不直接耦合业务表。真实使用时，业务方负责读取、拼装、清洗自己的数据，然后提交符合领域 Adapter contract 的 records。下面先检查 Notebook 作为业务方能看到哪些源表数据。

    # %% [code] 8
    SOURCE_TABLES = [
        "ft_news",
        "ft_market_flow",
        "ft_market_cache",
        "ft_sentiment",
        "ft_macro_indicators",
    ]

    source_counts = {table: table_count(table) for table in SOURCE_TABLES}
    print("source table counts:")
    pprint(source_counts)

    if args.verbose_samples:
        for table in SOURCE_TABLES:
            if not table_exists(table) or not source_counts.get(table):
                continue
            print(f"\n{table} sample:")
            pprint(sample_table(table, limit=3))
    else:
        print("跳过 ft_* 样本明细；需要时传 --verbose-samples。")

    # %% [markdown] 9: 4. 应用服务健康检查
    # ## 4. 应用服务健康检查
    #
    # 应用服务层是 HTTP API 和 CLI 的共同入口。这里不走网络，直接在 Notebook 内调用 Python 应用服务，排除端口和进程问题。

    # %% [code] 10
    from src.application.services.knowledge_service import create_knowledge_service

    service = create_knowledge_service(target=TARGET)
    health = await service.health()
    pprint(health.to_dict())

    # %% [markdown] 11: 5. 业务方投影并编译真实数据
    # ## 5. 业务方投影并编译真实数据
    #
    # 这一段是 Notebook 作为业务调用方完成 **source mapping**：从 L1 之前的业务源表读取数据，转换成 `financial` Adapter 支持的 records，再调用知识图谱服务的 compile 入口。
    #
    # **Notebook 的职责（规则投影）：**
    # - `ft_news` → `news_articles` / `policy_news`：把 `related_stocks`、`tags` 转成 `mentioned_entities`，提取 `title`/`text`
    # - `ft_market_flow / ft_market_cache / ft_sentiment / ft_macro_indicators` → `derived_signal`：规则映射信号值和 target_ref
    #
    # **Adapter 内部的职责（compile 阶段自动执行）：**
    # - 文本类 records（`news_articles` / `policy_news`）：`FinancialNewsExtractionStrategy` 自动调用 LLM（`ClaudeProxyService`）抽取 `mentioned_entities` / `affected_entities`，与规则投影结果合并去重
    # - 信号类 records（`derived_signal`）：不走 LLM，直接规则建图
    #
    # **Notebook 不需要也不应该自己调用 LLM。** LLM enrichment 已经在 `FinancialKGAdapter._payload_for_compile()` 里统一管理。
    #
    # **不读取 `ft_events`。** 按设计，知识图谱应先于 L1a/L1b 事件抽取沉淀基础知识和信号上下文；`ft_events` 是 L1 输出，后续可以增量回写 KG，但不能作为冷启动/前置 KG 的数据来源。
    #
    # 默认只预览和 dry-run，确认映射符合预期后再打开写入开关。

    # %% [code] 12
    from collections import Counter
    import math
    from datetime import date, datetime
    from src.application.dto.knowledge_dto import KnowledgeCompileCommand

    # Notebook 作为业务方可以从一套库读源数据，再写入另一套 KG 库。
    # 默认都跟随 TARGET，也就是直接使用 jettask。
    BUSINESS_SOURCE_TARGET = TARGET
    KG_TARGET = TARGET
    # 每个 ft_* 表最多拉多少条（演示用 5 条；全量灌库时再调大）
    BUSINESS_COMPILE_LIMIT = args.limit
    # 演示并发数；tmux pool 已做 session/buffer 隔离，默认可跟随 CLAUDE_PROXY_MAX_CONCURRENCY
    BUSINESS_COMPILE_CONCURRENCY = args.concurrency

    # 安全开关：默认不写库。要真实写入，把 COMPILE_BUSINESS_RECORDS=True 且 BUSINESS_COMPILE_DRY_RUN=False。
    COMPILE_BUSINESS_RECORDS = not args.no_compile
    BUSINESS_COMPILE_DRY_RUN = args.dry_run

    # LLM enrichment 由 FinancialKGAdapter 在 compile 阶段自动执行（ClaudeProxyService 已注入）。
    # 如需禁用 LLM enrichment（仅规则投影），可手动构造 FinancialKGAdapter(news_extraction_strategy=FinancialNewsExtractionStrategy())。
    # 当前默认行为：compile 时自动走 LLM，第二次 compile 同一 source_id 会命中 TTL cache 不重复调用。
    # compile 内部每条 record 处理后会打印一行 INFO 进度日志（[compile] [N/total] ok ...）。
    # 并发执行时完成顺序不保证等于 source_id 顺序。

    PRE_L1_SOURCE_TABLES = [
        "ft_news",
        "ft_market_flow",
        "ft_market_cache",
        "ft_sentiment",
        "ft_macro_indicators",
    ]


    def db_rows_for(target: str, sql: str, params: dict | None = None) -> list[dict]:
        with get_session(target) as session:
            rows = session.execute(text(sql), params or {}).mappings().all()
        return [dict(row) for row in rows]


    def table_exists_for(target: str, table_name: str) -> bool:
        sql = """
        select exists (
          select 1 from information_schema.tables
          where table_schema = 'public' and table_name = :table_name
        ) as exists
        """
        return bool(db_rows_for(target, sql, {"table_name": table_name})[0]["exists"])


    def business_rows(table_name: str, *, order_by: str = "id desc", limit: int = BUSINESS_COMPILE_LIMIT) -> list[dict]:
        if not table_exists_for(BUSINESS_SOURCE_TARGET, table_name):
            return []
        limit = max(1, min(int(limit), 1000))
        return db_rows_for(BUSINESS_SOURCE_TARGET, f"select * from {table_name} order by {order_by} limit {limit}")


    def iso(value):
        if value is None:
            return None
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return str(value)


    def as_list(value) -> list:
        if value is None:
            return []
        return value if isinstance(value, list) else [value]


    def as_dict(value) -> dict:
        return value if isinstance(value, dict) else {}


    def json_safe(value):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if isinstance(value, dict):
            return {key: json_safe(item) for key, item in value.items() if json_safe(item) is not None}
        if isinstance(value, list):
            return [item for item in (json_safe(item) for item in value) if item is not None]
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return value


    def infer_exchange(code: str | None) -> str:
        code = str(code or "").strip()
        if code.startswith("6"):
            return "SH"
        if code.startswith(("0", "3")):
            return "SZ"
        if code.startswith(("4", "8", "9")):
            return "BJ"
        return "CN"


    def stock_entity(item, *, confidence: float = 0.75) -> dict | None:
        if isinstance(item, dict):
            code = item.get("code") or item.get("stock_code") or item.get("symbol")
            name = item.get("name") or item.get("stock_name") or code
            exchange = item.get("exchange") or item.get("stock_exchange") or infer_exchange(code)
        else:
            text = str(item or "").strip()
            code = text if text.isdigit() and len(text) in {5, 6} else None
            name = text
            exchange = infer_exchange(code)
        if not code:
            return None
        return {"type": "stock", "exchange": exchange, "code": str(code), "name": str(name), "confidence": confidence}


    def named_entity(entity_type: str, item, *, confidence: float = 0.7) -> dict | None:
        if isinstance(item, dict):
            name = item.get("name") or item.get("title") or item.get("code") or item.get("id")
            code = item.get("code") or item.get("industry_code") or item.get("concept_code") or item.get("id")
            taxonomy = item.get("taxonomy") or "business"
        else:
            name = str(item or "").strip()
            code = None
            taxonomy = "business"
        if not name:
            return None
        entity = {"type": entity_type, "name": str(name), "taxonomy": taxonomy, "confidence": confidence}
        if code:
            entity["code"] = str(code)
        return entity


    def macro_indicator_entity(code: str, *, name: str | None = None) -> dict:
        return {"type": "macro_indicator", "indicator_code": str(code), "name": name or str(code), "confidence": 0.9}


    def unique_entities(entities: list[dict | None]) -> list[dict]:
        result = []
        seen = set()
        for entity in entities:
            if not entity:
                continue
            key = (
                entity.get("type"),
                entity.get("exchange"),
                entity.get("code"),
                entity.get("fund_code"),
                entity.get("taxonomy"),
                entity.get("indicator_code"),
                entity.get("name"),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(entity)
        return result


    def derived_signal_record(
        *,
        table: str,
        pk,
        target_ref: dict,
        signal_type: str,
        observed_at,
        value,
        unit: str | None = None,
        window: str | None = None,
        title: str | None = None,
        payload_extra: dict | None = None,
    ) -> dict | None:
        observed_at_text = iso(observed_at)
        safe_value = json_safe(value)
        if observed_at_text is None or safe_value is None:
            return None
        payload = {
            "target_ref": json_safe(target_ref),
            "signal_type": signal_type,
            "observed_at": observed_at_text,
            "value": safe_value,
            "unit": unit,
            "window": window,
            "title": title,
            "source_table": table,
            "source_pk": pk,
        }
        if payload_extra:
            payload.update(json_safe(payload_extra))
        return {
            "source_type": "derived_signal",
            "source_id": f"{table}:{pk}",
            "observed_at": observed_at_text,
            "payload": payload,
            "raw_text": title,
            "metadata": {"source_table": table, "source_pk": pk},
        }


    def news_row_to_record(row: dict) -> dict | None:
        published_at = iso(row.get("published_at") or row.get("created_at"))
        if not published_at:
            return None
        category = (row.get("category") or "").lower()
        # 规则投影：只转换结构化字段，文本实体由 Adapter 内 LLM 抽取
        mentioned = []
        mentioned.extend(stock_entity(item, confidence=0.7) for item in as_list(row.get("related_stocks")))
        mentioned.extend(named_entity("concept", item, confidence=0.55) for item in as_list(row.get("tags")))
        source_type = "policy_news" if category in {"policy", "macro"} else "news_articles"
        payload = {
            "source_id": f"ft_news:{row['id']}",
            "document_id": f"ft_news:{row['id']}",
            "published_at": published_at,
            "title": row.get("title") or f"ft_news:{row['id']}",
            "text": row.get("content") or row.get("summary") or row.get("title") or "",
            "source_name": row.get("source_name") or row.get("source") or "ft_news",
            "mentioned_entities": unique_entities(mentioned),  # 规则结果；LLM 会在 compile 阶段补充
            "affected_entities": [],
        }
        return {
            "source_type": source_type,
            "source_id": payload["source_id"],
            "observed_at": payload["published_at"],
            "payload": payload,
            "raw_text": payload["text"],
            "metadata": {"source_table": "ft_news", "source_pk": row["id"]},
        }


    def market_flow_row_to_record(row: dict) -> dict | None:
        data = as_dict(row.get("data"))
        stock = stock_entity({"code": data.get("code"), "name": data.get("name")})
        if not stock:
            return None
        value = data.get("net_amt") or data.get("net_inflow") or data.get("main_net_inflow") or data.get("amount")
        title = f"{data.get('name') or stock['code']} {row.get('data_type')} {value}"
        return derived_signal_record(
            table="ft_market_flow",
            pk=row["id"],
            target_ref={k: stock[k] for k in ["type", "exchange", "code", "name"]},
            signal_type=f"market_flow.{row.get('data_type')}",
            observed_at=row.get("trade_date") or row.get("created_at"),
            value=value,
            unit="CNY",
            window="trade_date",
            title=title,
            payload_extra={"raw_data": data},
        )


    def market_cache_row_to_record(row: dict) -> dict | None:
        data_type = row.get("data_type") or "market_cache"
        data = as_dict(row.get("data"))
        value = data.get("score") or data.get("value") or 1
        return derived_signal_record(
            table="ft_market_cache",
            pk=row["id"],
            target_ref=macro_indicator_entity(f"market_cache.{data_type}", name=str(data_type)),
            signal_type=f"market_cache.{data_type}",
            observed_at=row.get("created_at"),
            value=value,
            unit="snapshot",
            window="latest",
            title=f"market_cache {data_type}",
            payload_extra={"raw_data": data},
        )


    def sentiment_row_to_record(row: dict) -> dict | None:
        data = as_dict(row.get("data"))
        stock = stock_entity({"code": data.get("code"), "name": data.get("name")})
        target = {k: stock[k] for k in ["type", "exchange", "code", "name"]} if stock else macro_indicator_entity(f"sentiment.{row.get('data_type')}")
        value = data.get("score") or data.get("sentiment") or data.get("count") or len(as_list(data.get("posts")))
        return derived_signal_record(
            table="ft_sentiment",
            pk=row["id"],
            target_ref=target,
            signal_type=f"sentiment.{row.get('data_type')}",
            observed_at=row.get("trade_date") or row.get("created_at"),
            value=value,
            unit="score_or_count",
            window="trade_date",
            title=f"sentiment {row.get('data_type')} {value}",
            payload_extra={"raw_data": data},
        )


    def macro_indicator_row_to_record(row: dict) -> dict | None:
        indicator = row.get("indicator")
        if not indicator:
            return None
        return derived_signal_record(
            table="ft_macro_indicators",
            pk=row["id"],
            target_ref=macro_indicator_entity(indicator),
            signal_type=f"macro.{indicator}",
            observed_at=row.get("published_at") or row.get("period") or row.get("created_at"),
            value=row.get("value"),
            unit=row.get("unit"),
            window=row.get("period"),
            title=f"macro {indicator} {row.get('value')}{row.get('unit') or ''}",
            payload_extra={"period": row.get("period"), "source": row.get("source"), "prev_value": row.get("prev_value"), "yoy": row.get("yoy"), "mom": row.get("mom")},
        )


    def build_business_kg_records() -> list[dict]:
        records = []
        records.extend(record for row in business_rows("ft_news", order_by="created_at desc") if (record := news_row_to_record(row)))
        records.extend(record for row in business_rows("ft_market_flow", order_by="created_at desc") if (record := market_flow_row_to_record(row)))
        records.extend(record for row in business_rows("ft_market_cache", order_by="created_at desc") if (record := market_cache_row_to_record(row)))
        records.extend(record for row in business_rows("ft_sentiment", order_by="created_at desc") if (record := sentiment_row_to_record(row)))
        records.extend(record for row in business_rows("ft_macro_indicators", order_by="created_at desc") if (record := macro_indicator_row_to_record(row)))
        return records

    def record_summary(record: dict) -> dict:
        payload = record.get("payload") or {}
        return {
            "source_type": record.get("source_type"),
            "source_id": record.get("source_id"),
            "title": payload.get("title"),
            "text_len": len(str(payload.get("text") or "")),
            "mentioned_count": len(payload.get("mentioned_entities") or []),
            "affected_count": len(payload.get("affected_entities") or []),
            "target_ref": payload.get("target_ref"),
            "signal_type": payload.get("signal_type"),
        }


    business_records = build_business_kg_records()
    projection_summary = {
        "business_source_target": BUSINESS_SOURCE_TARGET,
        "kg_target": KG_TARGET,
        "source_tables": PRE_L1_SOURCE_TABLES,
        "records": len(business_records),
        "by_source_type": dict(Counter(record["source_type"] for record in business_records)),
        "compile_enabled": COMPILE_BUSINESS_RECORDS,
        "dry_run": BUSINESS_COMPILE_DRY_RUN,
        "compile_limit_per_table": BUSINESS_COMPILE_LIMIT,
        "llm_enrichment": "auto（由 FinancialKGAdapter 在 compile 阶段执行，文本类 records 走 ClaudeProxyService）",
    }
    pprint(projection_summary)
    print("\nrecord sample:")
    if args.verbose_samples:
        pprint(business_records[:3])
    else:
        pprint([record_summary(record) for record in business_records[:5]])

    excluded_tables = {
        "ft_events": "L1a/L1b 输出表。KG 前置冷启动不能依赖它；后续可作为 L1 回写增量 source_type=l1_events。",
        "ft_event_streams": "事件聚合/故事线输出，位于 KG 和 L1 之后；需要单独扩展 event_stream/storyline source_type。",
        "ft_positions": "用户持仓，不属于前置市场知识；需要 portfolio_position 合约后再接入。",
        "ft_alipay_positions": "同 ft_positions，不能伪造成 fund_holdings。",
        "ft_watchlist_data": "宽 JSON 采集缓存，需要按 data_type 拆成明确 source mapping 后再接入。",
        "ft_raw_data": "原始归档表只适合回放到业务规范表，不直接进入 KG。",
    }
    print("\nexcluded tables:")
    pprint(excluded_tables)

    if COMPILE_BUSINESS_RECORDS and business_records:
        # compile 阶段：文本类 records 会自动触发 LLM enrichment（ClaudeProxyService → tmux pool）
        # 进度日志：每条 record 处理完打印一行 [compile] [N/total] ok source_id=... duration=...s
        print(f"\n开始 compile {len(business_records)} 条 records，并发={BUSINESS_COMPILE_CONCURRENCY}，文本类预计每条 30-60s，进度见下方日志：\n")
        compile_result = await service.compile_kg(KnowledgeCompileCommand(
            adapter_name=ADAPTER,
            target=KG_TARGET,
            records=business_records,
            dry_run=BUSINESS_COMPILE_DRY_RUN,
            request_id=f"notebook-pre-l1-projection:{BUSINESS_SOURCE_TARGET}:{BUSINESS_COMPILE_LIMIT}",
            concurrency=BUSINESS_COMPILE_CONCURRENCY,
        ))
        pprint(compile_result.to_dict())
    else:
        print("跳过业务投影编译；确认 record sample 后再打开 COMPILE_BUSINESS_RECORDS。")

    # %% [markdown] 13: 6. 基于真实库重建 Wiki 和索引
    # ## 6. 基于真实库重建 Wiki 和索引
    #
    # 这一步只使用当前数据库里的 `kg_nodes / kg_edges / kg_evidence`。如果图谱事实库为空，重建结果也会为空。

    # %% [code] 14
    if args.skip_post:
        print("跳过 post-compile sections（--skip-post）。")
        return

    from src.application.dto.knowledge_dto import KnowledgeRebuildIndexesCommand, KnowledgeRebuildWikiCommand

    wiki_result = await service.rebuild_wiki_for(
        KnowledgeRebuildWikiCommand(adapter_name=ADAPTER, target=TARGET)
    )
    index_result = await service.rebuild_indexes_for(
        KnowledgeRebuildIndexesCommand(adapter_name=ADAPTER, target=TARGET)
    )

    pprint(wiki_result.to_dict())
    pprint(index_result.to_dict())

    print("\nwiki sample:")
    pprint(sample_table(
        "kg_wiki_pages",
        "page_id, page_type, subject_type, subject_id, title, left(summary, 300) as summary_preview",
        where="adapter_name = :adapter",
        params={"adapter": ADAPTER},
    ))
    print("\nadjacency sample:")
    pprint(sample_table(
        "kg_graph_adjacency",
        "source_node_id, target_node_id, edge_id, relation_type",
        where="adapter_name = :adapter",
        params={"adapter": ADAPTER},
    ))
    print("\nevidence chunks sample:")
    pprint(sample_table(
        "kg_evidence_chunks",
        "chunk_id, evidence_id, left(content, 300) as content_preview, payload",
        where="adapter_name = :adapter",
        params={"adapter": ADAPTER},
    ))

    # %% [markdown] 15: 7. 构建投研上下文
    # ## 7. 构建投研上下文
    #
    # 检索会混合使用图谱邻接、Wiki 和 evidence chunks。这里仍然直接使用数据库中的真实图谱数据。

    # %% [code] 16
    from src.application.dto.knowledge_dto import KnowledgeResearchContextCommand

    # 查询词需要和你库里实际有的内容对得上。
    # 当前 demo 灌的是"并购重组"主题的新闻，所以用相关 query 才有命中。
    # 如果你后续灌了更多话题的新闻（如 BUSINESS_COMPILE_LIMIT 调大到 100），可以换成更广的 query。
    QUERY = "并购重组对哪些行业有影响"

    context = await service.build_research_context_for(
        KnowledgeResearchContextCommand(
            adapter_name=ADAPTER,
            target=TARGET,
            query=QUERY,
            graph_depth=3,
            graph_limit=20,
            wiki_limit=10,
            evidence_limit=20,
            max_chars=5000,
        )
    )

    context_data = context.to_dict()
    print({
        "query": QUERY,
        "hits": len(context_data["hits"]),
        "matched_nodes": len(context_data["matched_nodes"]),
        "matched_edges": len(context_data["matched_edges"]),
        "evidence_refs": len(context_data["evidence_refs"]),
        "context_chars": len(context_data["context_text"]),
    })
    print("\n--- context_text 前 1500 字符 ---")
    print(context_data["context_text"][:1500])

    # %% [markdown] 17: 8. 金融 Adapter 的实体解析和路径查询
    # ## 8. 金融 Adapter 的实体解析和路径查询
    #
    # 这两个能力依赖已经写入的金融领域节点和边。若候选为空，先回到第 1、2 节检查 `kg_nodes` 是否有金融实体。

    # %% [code] 18
    resolved = await service.resolve_financial_entities("宁德时代 固态电池 示例成长混合", limit=20)
    pprint(resolved)

    seed_node_ids = [item["node_id"] for item in resolved.get("candidates", [])[:2]]
    if seed_node_ids:
        paths = await service.find_financial_paths(seed_node_ids=seed_node_ids, max_depth=3, limit=10)
        pprint(paths)
    else:
        print("没有解析到种子实体，跳过路径查询。")

    # %% [markdown] 19: 9. 质量扫描和复核队列
    # ## 9. 质量扫描和复核队列
    #
    # 质量扫描会检查孤立节点、缺 evidence 的边、Wiki 覆盖等问题，并可写入 `kg_review_items`。

    # %% [code] 20
    from src.application.dto.knowledge_dto import KnowledgeQualityScanCommand

    quality = await service.quality_scan_for(
        KnowledgeQualityScanCommand(adapter_name=ADAPTER, target=TARGET, persist_review=True)
    )
    pprint(quality.to_dict())

    reviews = await service.list_reviews_for(status="open")
    pprint(reviews)

    # %% [markdown] 21: 10. CLI 调用
    # ## 10. CLI 调用
    #
    # CLI 走的也是同一套应用服务。这里默认只调用查询类命令，不在 Notebook 里自动写入 fixture。

    # %% [code] 22
    import json
    import subprocess

    def run_cli(args: list[str]) -> dict | str:
        proc = subprocess.run(
            [sys.executable, "-m", "src.interfaces.cli.main", *args],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            print(proc.stderr)
            raise RuntimeError(f"CLI 失败: {' '.join(args)}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            return proc.stdout.strip()

    pprint(run_cli(["kg", "health", "--target", TARGET, "--json"]))
    pprint(run_cli([
        "kg", "query",
        "--target", TARGET,
        "--adapter", ADAPTER,
        "--query", QUERY,
        "--json",
    ]))
    pprint(run_cli(["kg", "quality-scan", "--target", TARGET, "--adapter", ADAPTER, "--json"]))

    # %% [markdown] 23: 11. HTTP API 调用
    # ## 11. HTTP API 调用
    #
    # 先用单独端口启动服务：
    #
    # ```bash
    # SERVER_PORT=8910 python -m src.interfaces.cli.main api
    # ```
    #
    # 启动后再运行下面的单元格。

    # %% [code] 24
    if args.http:
        import httpx

        with httpx.Client(base_url=BASE_URL, timeout=30, trust_env=False) as client:
            health_resp = client.get("/api/kg/health", params={"target": TARGET})
            print("health", health_resp.status_code, health_resp.json())

            query_resp = client.post("/api/kg/research-context", json={
                "adapter_name": ADAPTER,
                "target": TARGET,
                "query": QUERY,
                "graph_depth": 3,
                "graph_limit": 20,
                "wiki_limit": 10,
                "evidence_limit": 20,
                "max_chars": 5000,
            })
            print("research-context", query_resp.status_code)
            pprint(query_resp.json())

            quality_resp = client.post("/api/kg/quality-scan", json={
                "adapter_name": ADAPTER,
                "target": TARGET,
                "persist_review": True,
            })
            print("quality-scan", quality_resp.status_code)
            pprint(quality_resp.json())
    else:
        print("跳过 HTTP API 检查；如需执行请传 --http 并先启动 API 服务。")

    # %% [markdown] 25: 12. 可选附录：初始化演示数据
    # ## 12. 可选附录：初始化演示数据
    #
    # 默认不要运行这段。只有当 `kg_*` 为空、你只是想验证服务链路时，才把 `USE_FIXTURE_BOOTSTRAP` 改成 `True`。
    #
    # 这不是生产数据处理路径。正式路径是上面的业务方投影单元：业务方读取自己的 `ft_*`，拼装成 Adapter records，再调用 KG compile。

    # %% [code] 26
    USE_FIXTURE_BOOTSTRAP = args.fixture_bootstrap

    if USE_FIXTURE_BOOTSTRAP:
        from src.application.dto.knowledge_dto import KnowledgeCompileCommand

        fixture_dir = PROJECT_ROOT / "tests" / "fixtures" / "knowledge" / "financial"
        records = []
        for path in sorted(fixture_dir.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                records.extend(payload)
            elif isinstance(payload, dict) and isinstance(payload.get("records"), list):
                records.extend(payload["records"])
            else:
                raise ValueError(f"fixture 格式不支持: {path}")

        compile_result = await service.compile_kg(KnowledgeCompileCommand(
            adapter_name=ADAPTER,
            target=TARGET,
            records=records,
            dry_run=False,
            request_id="notebook-fixture-bootstrap",
        ))
        pprint(compile_result.to_dict())
    else:
        print("跳过 fixture 初始化；当前 Notebook 默认使用数据库真实数据。")

    # %% [markdown] 27: 13. 常见判断标准
    # ## 13. 常见判断标准
    #
    # - `kg_nodes / kg_edges / kg_evidence` 有数据：说明图谱事实库已经建立。
    # - `kg_wiki_pages / kg_graph_adjacency / kg_evidence_chunks` 有数据：说明 Wiki 和索引已经重建。
    # - `research-context` 返回 `hits / matched_nodes / matched_edges / evidence_refs`：说明检索上下文链路可用。
    # - 查询为空时，先检查业务源表和 `kg_*` 表数量，再检查是否已经执行编译、重建 Wiki、重建索引。
    # - fixture 只用于空库演示和自动化测试，不作为 Notebook 的默认真实数据来源。
    # - LLM enrichment 在 compile 阶段自动执行，第二次 compile 同一 source_id 会命中 TTL cache，不会重复调用模型。若需禁用 LLM 只走规则，可在 `_build_financial_adapter()` 中去掉 `llm_service` 注入。


if __name__ == "__main__":
    asyncio.run(main())
