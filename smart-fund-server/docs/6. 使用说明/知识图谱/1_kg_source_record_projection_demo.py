#!/usr/bin/env python3
"""演示：把数据库业务表记录投影为知识图谱 Source Record。

这个脚本只演示数据源投影层能力：

    数据库业务表 Raw Row
      -> KnowledgeSourceProjectionService
      -> Source Record

它不会写入 KG，不会编译节点/关系/证据，不会调用 LLM。

每个步骤都可以单独运行：

- Step 0：打印脚本配置。
  用来看本次会读取哪个库、哪些表、每张表读取多少条。

- Step 1：读取五张核心业务表并统一投影为 Source Record。
  这是主演示步骤，用来观察完整投影入口能不能把真实数据库记录转换成标准输入。

- Step 2：只读取 ft_news 并展示 news_articles / policy_news 分类结果。
  用来检查 category 为空时，source/source_name/title/content 规则如何决定 source_type，
  以及 source_type_reason、matched_rules、uncertain 是否写入 metadata。

- Step 3：只读取结构化表并展示 derived_signal 投影结果。
  覆盖 ft_market_flow、ft_market_cache、ft_sentiment、ft_macro_indicators。
  用来检查 target_ref、signal_type、value、observed_at 是否能从结构化数据中确定。

- Step 4：展示一条完整 Source Record 的结构。
  用来理解最终交给 Knowledge Compiler 的输入长什么样。为了避免刷屏，正文会截断。

- Step 5：把完整投影结果写入 generated_source_record_projection_demo.json。
  用来后续打开文件详细查看，不依赖终端输出。

运行方式：

    python "docs/6. 使用说明/知识图谱/1_kg_source_record_projection_demo.py"

不需要命令行参数。需要运行哪个步骤，就在 main() 里注释或放开对应函数调用。
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from pprint import pprint
from typing import Any


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

from src.application.dto.knowledge_dto import KnowledgeSourceProjectionCommand  # noqa: E402
from src.application.services.knowledge_source_projection_service import DEFAULT_SOURCES  # noqa: E402
from src.application.services.knowledge_service import create_knowledge_service  # noqa: E402


OUTPUT_FILE = Path(__file__).with_name("generated_source_record_projection_demo.json")

# 修改这里即可控制演示范围。
TARGET = "prod"
LIMIT_PER_SOURCE = 3
NEWS_LIMIT = 5
STRUCTURED_LIMIT = 3
CODES: list[str] = []
INCLUDE_SKIPPED = True


def main() -> None:
    """通过注释/放开下面函数，决定本次要运行哪些步骤。"""

    step_0_print_config()

    # 主流程：读取五张核心表并投影为 Source Record。
    # step_1_project_all_sources()

    # 如果只想看新闻 source_type 判断，放开这一行。
    step_2_project_ft_news_only()

    # 如果只想看结构化表 derived_signal 投影，放开这一行。
    # step_3_project_structured_signals_only()

    # 如果想看单条 Source Record 的完整结构，放开这一行。
    # step_4_show_one_full_source_record()

    # 如果只想重新生成完整 JSON 文件，放开这一行。
    # step_5_write_full_projection_json()


def step_0_print_config() -> None:
    print("\n[Step 0] 配置")
    print("[demo] 数据库记录 -> Source Record 投影演示")
    print("[demo] 本脚本不写 KG、不调用 LLM、不重建索引")
    pprint(
        {
            "target": TARGET,
            "default_sources": list(DEFAULT_SOURCES),
            "limit_per_source": LIMIT_PER_SOURCE,
            "news_limit": NEWS_LIMIT,
            "structured_limit": STRUCTURED_LIMIT,
            "codes": CODES,
            "include_skipped": INCLUDE_SKIPPED,
        },
        sort_dicts=False,
    )


def step_1_project_all_sources() -> dict[str, Any]:
    """读取五张核心业务表，统一投影为 Source Record。"""

    print("\n[Step 1] 五张核心表 -> Source Record")
    data = _project_sources(
        sources=list(DEFAULT_SOURCES),
        limit=LIMIT_PER_SOURCE,
        codes=CODES,
    )
    _print_summary(data)
    _print_examples(data["records"])
    _print_skipped(data["skipped"])
    _write_output(data)
    return data


def step_2_project_ft_news_only() -> dict[str, Any]:
    """只读取 ft_news，重点观察 news_articles / policy_news 分类 metadata。"""

    print("\n[Step 2] ft_news -> news_articles / policy_news")
    data = _project_sources(sources=["ft_news"], limit=NEWS_LIMIT, codes=CODES)
    _print_summary(data)
    print("\n[news classification examples]")
    for record in data["records"]:
        metadata = record.get("metadata") or {}
        pprint(
            {
                "source_id": record.get("source_id"),
                "source_type": record.get("source_type"),
                "title": _short((record.get("payload") or {}).get("title"), 120),
                "source": metadata.get("source"),
                "source_name": metadata.get("source_name"),
                "category": metadata.get("category"),
                "reason": metadata.get("source_type_reason"),
                "matched_rules": metadata.get("source_type_matched_rules"),
                "confidence": metadata.get("source_type_confidence"),
                "uncertain": metadata.get("source_type_uncertain"),
            },
            sort_dicts=False,
        )
    return data


def step_3_project_structured_signals_only() -> dict[str, Any]:
    """只读取结构化表，重点观察 derived_signal 的 target_ref / signal_type / value。"""

    print("\n[Step 3] 结构化业务表 -> derived_signal")
    data = _project_sources(
        sources=["ft_market_flow", "ft_market_cache", "ft_sentiment", "ft_macro_indicators"],
        limit=STRUCTURED_LIMIT,
        codes=[],
    )
    _print_summary(data)
    _print_examples(data["records"])
    _print_skipped(data["skipped"])
    return data


def step_4_show_one_full_source_record() -> dict[str, Any] | None:
    """展示一条完整 Source Record 结构，正文截断方便阅读。"""

    print("\n[Step 4] 单条 Source Record 完整结构")
    data = _project_sources(sources=["ft_news"], limit=1, codes=CODES)
    if not data["records"]:
        print("[record] none")
        return None
    record = _truncate_record(data["records"][0])
    pprint(record, sort_dicts=False)
    return record


def step_5_write_full_projection_json() -> dict[str, Any]:
    """把完整投影结果写到 JSON 文件，方便后续打开查看。"""

    print("\n[Step 5] 写入完整投影 JSON")
    data = _project_sources(sources=list(DEFAULT_SOURCES), limit=LIMIT_PER_SOURCE, codes=CODES)
    _write_output(data)
    return data


def _project_sources(
    *,
    sources: list[str],
    limit: int,
    codes: list[str],
) -> dict[str, Any]:
    service = create_knowledge_service(target=TARGET)
    result = asyncio.run(
        service.project_sources(
            KnowledgeSourceProjectionCommand(
                target=TARGET,
                sources=sources,
                codes=codes,
                limit=limit,
                include_skipped=INCLUDE_SKIPPED,
            )
        )
    )
    return result.to_dict()


def _write_output(data: dict[str, Any]) -> None:
    OUTPUT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[output] full output written: {OUTPUT_FILE}")


def _print_summary(data: dict[str, Any]) -> None:
    records = data["records"]
    source_type_counts = Counter(record.get("source_type") for record in records)
    source_table_counts = Counter(
        (record.get("metadata") or {}).get("source_table") for record in records
    )
    reason_counts = Counter(
        (record.get("metadata") or {}).get("source_type_reason")
        for record in records
        if (record.get("metadata") or {}).get("source_type_reason")
    )
    uncertain_count = sum(
        1 for record in records if (record.get("metadata") or {}).get("source_type_uncertain")
    )

    print("\n[summary]")
    pprint(
        {
            "total_records": data["total_records"],
            "source_counts": data["source_counts"],
            "coverage": _compact_coverage(data.get("coverage") or {}),
            "source_type_counts": dict(source_type_counts),
            "source_table_counts": dict(source_table_counts),
            "source_type_uncertain": uncertain_count,
            "top_source_type_reasons": reason_counts.most_common(10),
            "skipped": len(data["skipped"]),
            "warnings": data["warnings"],
        },
        sort_dicts=False,
    )


def _compact_coverage(coverage: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for source, item in coverage.items():
        data_types = item.get("data_types") or {}
        result[source] = {
            "total_rows": item.get("total_rows"),
            "projected": item.get("projected"),
            "skipped": item.get("skipped"),
            "projection_rate": item.get("projection_rate"),
            "skip_reasons": item.get("skip_reasons") or {},
            "data_types": {
                key: value
                for key, value in sorted(
                    data_types.items(),
                    key=lambda pair: (-(pair[1].get("total") or 0), pair[0]),
                )[:10]
            },
        }
    return result


def _print_examples(records: list[dict[str, Any]]) -> None:
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        source_table = (record.get("metadata") or {}).get("source_table", "unknown")
        if len(examples[source_table]) < 2:
            examples[source_table].append(_compact_record(record))

    print("\n[examples] 每个来源最多展示 2 条 Source Record")
    for source_table, items in sorted(examples.items()):
        print(f"\n--- {source_table} ---")
        for item in items:
            pprint(item, sort_dicts=False)


def _print_skipped(skipped: list[dict[str, Any]]) -> None:
    if not skipped:
        print("\n[skipped] none")
        return
    print("\n[skipped] 前 10 条")
    for item in skipped[:10]:
        pprint(item, sort_dicts=False)


def _compact_record(record: dict[str, Any]) -> dict[str, Any]:
    metadata = record.get("metadata") or {}
    payload = record.get("payload") or {}
    compact_payload = {
        "source_id": payload.get("source_id"),
        "document_id": payload.get("document_id"),
        "title": _short(payload.get("title")),
        "target_ref": payload.get("target_ref"),
        "signal_type": payload.get("signal_type"),
        "observed_at": payload.get("observed_at"),
        "value": payload.get("value"),
        "mentioned_entities": payload.get("mentioned_entities", [])[:5],
    }
    compact_payload = {key: value for key, value in compact_payload.items() if value not in (None, "", [])}
    return {
        "source_type": record.get("source_type"),
        "source_id": record.get("source_id"),
        "observed_at": record.get("observed_at"),
        "raw_text_len": len(record.get("raw_text") or ""),
        "metadata": {
            "source_table": metadata.get("source_table"),
            "source_pk": metadata.get("source_pk"),
            "source_type_reason": metadata.get("source_type_reason"),
            "source_type_matched_rules": metadata.get("source_type_matched_rules"),
            "source_type_confidence": metadata.get("source_type_confidence"),
            "source_type_uncertain": metadata.get("source_type_uncertain"),
            "signal_source_type": metadata.get("signal_source_type"),
            "data_type": metadata.get("data_type"),
            "value_path": metadata.get("value_path"),
            "projection_rule_version": metadata.get("projection_rule_version"),
        },
        "payload": compact_payload,
    }


def _short(value: Any, limit: int = 80) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _truncate_record(record: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(record, ensure_ascii=False, default=str))
    if result.get("raw_text"):
        result["raw_text"] = _short(result["raw_text"], 500)
    payload = result.get("payload") or {}
    if payload.get("text"):
        payload["text"] = _short(payload["text"], 500)
    return result


if __name__ == "__main__":
    main()
