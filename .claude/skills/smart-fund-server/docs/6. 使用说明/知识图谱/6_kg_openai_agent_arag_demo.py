#!/usr/bin/env python3
"""OpenAI Agent 驱动的 KG A-RAG 检索演示脚本。

本脚本只演示 `openai_agents_arag` 这一条新路线，不对比旧路线，也不做写入、
reset、rebuild index。它默认假设写入期已经完成了这些前置工作：

- `kg_nodes / kg_edges / kg_evidence` 已经有图谱事实。
- `kg_retrieval_documents` 已经写入 search_text、key_phrases、aliases、
  readable_relations、evidence_summary 等检索增强字段。
- graph adjacency、wiki pages、Milvus hybrid vectors 已经刷新。

运行方式固定为：

    python "docs/6. 使用说明/知识图谱/6_kg_openai_agent_arag_demo.py"

所有参数都在脚本顶部常量区配置，不使用命令行参数。需要切换数据来源或执行块时，
直接编辑常量，或在 `main()` 中注释/取消注释对应代码。

脚本步骤说明：

Step 0: 固定运行配置
    读取顶部常量，例如 TARGET、ADAPTER、GRAPH_LIMIT、
    FROM_FT_NEWS_LIMIT、DEFAULT_QUERIES、EXTRA_QUERIES。
    这些配置决定连哪个数据库、跑哪些 query、上下文预算和输出文件。

Step 1: 配置日志和 trace
    打开 `KG_RETRIEVAL_LLM_TRACE` 和 `KG_RETRIEVAL_PROFILE`。
    脚本会在发现 `openai-agents` 包未安装时直接报错。`openai_agents_arag`
    运行时没有 fallback；SDK 缺失、被禁用或执行失败都会直接失败。
    观测优先使用 Langfuse。需要提前配置：
    - `LANGFUSE_PUBLIC_KEY`
    - `LANGFUSE_SECRET_KEY`
    - `LANGFUSE_BASE_URL` 或 `LANGFUSE_HOST`，自托管时填你的 Langfuse 地址。
    Langfuse 会展示 Agent、LLM 请求、工具调用、工具结果、错误和耗时。

    本地输出文件：
    - `generated_openai_agent_arag_console.log`：终端镜像日志，内容和 console 输出一致，防止 bash 滚动丢失。
    - `generated_openai_agent_arag_trace.log`：结构化事件 trace，用于机器排查。
    - `generated_openai_agent_arag_transcript.md` 和
      `generated_openai_agent_arag_sdk_trace.jsonl` 默认关闭。只有在
      LOCAL_TRANSCRIPT_ENABLED / LOCAL_SDK_TRACE_ENABLED 打开时才写。
    trace 用于观察：
    - openai_agents_bootstrap_search：原始 query 首轮无 LLM 召回
    - openai_agents_runner_start：OpenAI Agents SDK 开始接管
    - openai_agents_tool_result：Agent 调用 search/open/find/stop_check 的结果
    - openai_agents_final_selection：Agent 最终交付的候选和 evidence

Step 2: 检查运行环境
    输出 OpenAI Agent 相关环境变量、`openai-agents` 包是否安装、KG 表数量、
    Milvus 状态。这里的目标是先确认“数据和运行时是否具备演示条件”。

Step 3: 准备 demo queries
    默认使用 DEFAULT_QUERIES。需要临时补问题时，编辑 EXTRA_QUERIES。
    需要从真实 `ft_news` 最近标题生成问题时，把 FROM_FT_NEWS_LIMIT 改成正数。
    这一步不调用 LLM，只是准备要交给 Agent RAG 的用户问题。

Step 4: 执行 OpenAI Agent RAG
    对每个 query 调用 `KnowledgeService.build_research_context_for()`，
    retrieval_mode 固定为 `openai_agents_arag`。运行时会先做 raw query
    bootstrap recall，然后由 OpenAI Agent 决定是否继续调用 KG search、open、
    find、stop_check 等工具。Agent 的最终 JSON 就是交付结果；代码只做 ID
    映射、结构校验和 trace 记录，不再做 CandidateJudge 二次语义过滤。

Step 5: 输出结果摘要
    终端打印每个 query 的 Agent 状态、channels_used、top_hits、evidence_refs、
    matched_nodes、matched_edges、agent_decisions。完整 JSON 摘要写入
    `generated_openai_agent_arag_demo.json`。

Step 6: 人工判断重点
    主要看三件事：
    - agent_final 是否为 True：True 表示 Agent SDK 路线真正完成最终交付。
    - evidence_refs/top_hits 是否围绕问题主体和证据，不是泛词或跑题召回。
    - trace 中 Agent 是否按需补召回、open 证据、检查停止，而不是固定工作流。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import sys
import traceback
from datetime import datetime
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

from src.application.dto.knowledge_dto import KnowledgeResearchContextCommand  # noqa: E402
from src.application.services.knowledge_service import create_knowledge_service  # noqa: E402
from src.infrastructure.config import settings  # noqa: E402
from src.infrastructure.connections import get_session  # noqa: E402
from src.infrastructure.vector_store.milvus_hybrid_store import MilvusHybridStore  # noqa: E402


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_TRACE_FILE = SCRIPT_DIR / "generated_openai_agent_arag_trace.log"
DEFAULT_TRANSCRIPT_FILE = SCRIPT_DIR / "generated_openai_agent_arag_transcript.md"
DEFAULT_CONSOLE_LOG_FILE = SCRIPT_DIR / "generated_openai_agent_arag_console.log"
LEGACY_FULL_LOG_FILE = SCRIPT_DIR / "generated_openai_agent_arag_full.log"
DEFAULT_SDK_TRACE_FILE = SCRIPT_DIR / "generated_openai_agent_arag_sdk_trace.jsonl"
DEFAULT_SUMMARY_FILE = SCRIPT_DIR / "generated_openai_agent_arag_demo.json"

TARGET = "prod"
ADAPTER = "financial"
GRAPH_LIMIT = 30
WIKI_LIMIT = 10
EVIDENCE_LIMIT = 30
MAX_CHARS = 8000
SHOW_CONTEXT = False
PRINT_JSON_SUMMARY = False
FROM_FT_NEWS_LIMIT = 0
LANGFUSE_ENABLED = True
LANGFUSE_AUTH_CHECK = False
LOCAL_TRANSCRIPT_ENABLED = False
LOCAL_SDK_TRACE_ENABLED = False
SUMMARY_FILE = DEFAULT_SUMMARY_FILE
TRACE_FILE = DEFAULT_TRACE_FILE
TRANSCRIPT_FILE = DEFAULT_TRANSCRIPT_FILE
CONSOLE_LOG_FILE = DEFAULT_CONSOLE_LOG_FILE
SDK_TRACE_FILE = DEFAULT_SDK_TRACE_FILE

DEFAULT_QUERIES = [
    "A股并购重组市场呈现三方面新变化 这条新闻涉及哪些主体、行业或资产影响",
    # "2025年年报点评：25年储能业务实现量利齐升，静待海外产能&新兴业务兑现业绩 这条新闻涉及哪些主体、行业或资产影响",
    # "以色列国防部长卡茨：以方准备重启对伊战争 这条新闻涉及哪些主体、行业或资产影响",
]

EXTRA_QUERIES: list[str] = [
    # "这里写你临时想测试的问题",
]


class _Tee:
    def __init__(self, *streams) -> None:
        self.streams = streams

    def write(self, data: str) -> int:
        for stream in self.streams:
            stream.write(data)
            stream.flush()
        return len(data)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()


def configure_console_log(console_log_file: str):
    output = Path(console_log_file).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    handle = output.open("w", encoding="utf-8")
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = _Tee(original_stdout, handle)
    sys.stderr = _Tee(original_stderr, handle)
    return handle, original_stdout, original_stderr


def restore_console_log(handle, original_stdout, original_stderr) -> None:
    sys.stdout = original_stdout
    sys.stderr = original_stderr
    handle.close()


def configure_logging(trace_file: str, transcript_file: str, sdk_trace_file: str) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    logging.getLogger("src.domain.knowledge.retrieval").setLevel(logging.INFO)
    logging.getLogger("src.domain.knowledge.retrieval.profile").setLevel(logging.INFO)
    logging.getLogger("src.application.services.knowledge_service").setLevel(logging.INFO)
    logging.getLogger("src.infrastructure.llm_proxy.service").setLevel(logging.INFO)
    os.environ.setdefault("KG_RETRIEVAL_LLM_TRACE", "1")
    os.environ["KG_RETRIEVAL_LLM_TRACE_FILE"] = trace_file
    os.environ["KG_LANGFUSE_ENABLED"] = "1" if LANGFUSE_ENABLED else "0"
    os.environ["KG_LANGFUSE_AUTH_CHECK"] = "1" if LANGFUSE_AUTH_CHECK else "0"
    os.environ["KG_OPENAI_AGENTS_TRANSCRIPT"] = "1" if LOCAL_TRANSCRIPT_ENABLED else "0"
    if LOCAL_TRANSCRIPT_ENABLED:
        os.environ["KG_OPENAI_AGENTS_TRANSCRIPT_FILE"] = transcript_file
    else:
        os.environ.pop("KG_OPENAI_AGENTS_TRANSCRIPT_FILE", None)
    if LOCAL_SDK_TRACE_ENABLED:
        os.environ["KG_OPENAI_AGENTS_LOCAL_TRACE_FILE"] = sdk_trace_file
    else:
        os.environ.pop("KG_OPENAI_AGENTS_LOCAL_TRACE_FILE", None)
    os.environ.setdefault("KG_RETRIEVAL_PROFILE", "1")
    os.environ.setdefault("KG_RETRIEVAL_PROFILE_MIN_MS", "1000")


def assert_openai_agents_ready() -> None:
    if importlib.util.find_spec("agents") is None:
        raise RuntimeError(
            "openai-agents is not installed in the current Python environment. "
            "Install project dependencies or run: pip install 'openai-agents>=0.17,<1'."
        )


async def main() -> None:
    trace_file = str(TRACE_FILE)
    transcript_file = str(TRANSCRIPT_FILE)
    summary_file = str(SUMMARY_FILE)
    console_log_file = str(CONSOLE_LOG_FILE)
    sdk_trace_file = str(SDK_TRACE_FILE)
    LEGACY_FULL_LOG_FILE.unlink(missing_ok=True)
    console_log_handle, original_stdout, original_stderr = configure_console_log(console_log_file)
    try:
        configure_logging(trace_file, transcript_file, sdk_trace_file)
        Path(trace_file).parent.mkdir(parents=True, exist_ok=True)
        Path(trace_file).write_text("", encoding="utf-8")
        if LOCAL_TRANSCRIPT_ENABLED:
            Path(transcript_file).parent.mkdir(parents=True, exist_ok=True)
            Path(transcript_file).write_text("", encoding="utf-8")
        else:
            Path(transcript_file).unlink(missing_ok=True)
        if LOCAL_SDK_TRACE_ENABLED:
            Path(sdk_trace_file).parent.mkdir(parents=True, exist_ok=True)
            Path(sdk_trace_file).write_text("", encoding="utf-8")
        else:
            Path(sdk_trace_file).unlink(missing_ok=True)
        assert_openai_agents_ready()

        service = create_knowledge_service(target=TARGET)

        # 方式 1：使用脚本内固定问题。
        queries = list(DEFAULT_QUERIES)

        # # 方式 2：追加脚本内临时问题。需要时编辑 EXTRA_QUERIES。
        # queries.extend(EXTRA_QUERIES)

        # # 方式 3：使用数据库最近 ft_news 标题生成真实问题。需要时把 FROM_FT_NEWS_LIMIT 改成正数。
        # if FROM_FT_NEWS_LIMIT > 0:
        #     queries.extend(latest_ft_news_queries(TARGET, FROM_FT_NEWS_LIMIT))

        queries = _ordered_unique(query.strip() for query in queries if query and query.strip())

        summary: dict[str, Any] = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "target": TARGET,
            "adapter": ADAPTER,
            "runtime": runtime_status(TARGET),
            "langfuse_enabled": LANGFUSE_ENABLED,
            "trace_file": trace_file,
            "console_log_file": console_log_file,
            "transcript_file": transcript_file if LOCAL_TRANSCRIPT_ENABLED else None,
            "sdk_trace_file": sdk_trace_file if LOCAL_SDK_TRACE_ENABLED else None,
            "queries": [],
        }

        print_title("1. Runtime")
        pprint(summary["runtime"])

        print_title("2. Demo Queries")
        for index, query in enumerate(queries, start=1):
            print(f"{index}. {query}")

        print_title("3. Retrieval")
        for query in queries:
            item = await run_agent_rag_query(
                service,
                adapter=ADAPTER,
                target=TARGET,
                query=query,
                graph_limit=GRAPH_LIMIT,
                wiki_limit=WIKI_LIMIT,
                evidence_limit=EVIDENCE_LIMIT,
                max_chars=MAX_CHARS,
                show_context=SHOW_CONTEXT,
            )
            summary["queries"].append(item)
            if not item["agent_final"]:
                raise RuntimeError(
                    "openai_agents_arag did not reach agent_final. "
                    "Check whether openai-agents is installed or provider config is valid."
                )

        write_summary(summary_file, summary)
        print_title("4. Output")
        print(f"summary:    {summary_file}")
        print(f"console:    {console_log_file}")
        print(f"trace:      {trace_file}")
        if LOCAL_TRANSCRIPT_ENABLED:
            print(f"transcript: {transcript_file}")
        if LOCAL_SDK_TRACE_ENABLED:
            print(f"sdk_trace:  {sdk_trace_file}")
        print("优先看 Langfuse；console 保留终端镜像，trace 保留机器排查结构化事件。")

        if PRINT_JSON_SUMMARY:
            print_title("JSON Summary")
            print(json.dumps(summary, ensure_ascii=False, indent=2))
    except BaseException:
        print_title("Python Exception")
        traceback.print_exc()
        raise
    finally:
        restore_console_log(console_log_handle, original_stdout, original_stderr)


async def run_agent_rag_query(
    service,
    *,
    adapter: str,
    target: str,
    query: str,
    graph_limit: int,
    wiki_limit: int,
    evidence_limit: int,
    max_chars: int,
    show_context: bool,
) -> dict[str, Any]:
    mode = "openai_agents_arag"
    print(f"\n[{mode}] {query}", flush=True)
    context = await service.build_research_context_for(
        KnowledgeResearchContextCommand(
            adapter_name=adapter,
            target=target,  # type: ignore[arg-type]
            query=query,
            retrieval_mode=mode,  # type: ignore[arg-type]
            graph_depth=3,
            graph_limit=graph_limit,
            wiki_limit=wiki_limit,
            evidence_limit=evidence_limit,
            max_chars=max_chars,
        )
    )
    data = context.to_dict()
    trace = data.get("retrieval_trace") or {}
    controller_decisions = trace.get("controller_decisions") or []
    agent_final = _has_agent_final(controller_decisions)
    compact = {
        "query": query,
        "mode": data.get("mode") or mode,
        "agent_final": agent_final,
        "agent_stop_reason": _last_stop_reason(controller_decisions),
        "warnings": data.get("warnings") or trace.get("warnings") or [],
        "channels_used": data.get("retrieval_channels_used") or trace.get("channels_used") or [],
        "hits": len(data.get("hits") or []),
        "evidence_refs": data.get("evidence_refs") or [],
        "matched_nodes": [
            {
                "type": node.get("node_type") or node.get("type"),
                "name": node.get("canonical_name") or node.get("name"),
            }
            for node in (data.get("matched_nodes") or [])[:12]
        ],
        "matched_edges": [
            {
                "relation": edge.get("relation_type") or edge.get("relation"),
                "source": edge.get("source_node_id") or edge.get("source"),
                "target": edge.get("target_node_id") or edge.get("target"),
            }
            for edge in (data.get("matched_edges") or [])[:12]
        ],
        "top_hits": [
            {
                "id": hit.get("id") or hit.get("hit_id"),
                "type": hit.get("type") or hit.get("hit_type"),
                "title": hit.get("title"),
                "source": hit.get("source"),
                "channels": hit.get("source_channels") or hit.get("channels"),
                "evidence_refs": (hit.get("evidence_refs") or [])[:5],
            }
            for hit in (data.get("hits") or [])[:8]
        ],
        "agent_decisions": _agent_decision_summary(controller_decisions),
    }
    pprint(compact)
    if show_context:
        print("\ncontext_text preview:")
        print((data.get("context_text") or "")[:2000])
    return compact


def runtime_status(target: str) -> dict[str, Any]:
    return {
        "health_relevant_env": {
            "KG_OPENAI_AGENTS_SDK_ENABLED": os.getenv("KG_OPENAI_AGENTS_SDK_ENABLED", "1"),
            "KG_OPENAI_AGENTS_MODEL": os.getenv("KG_OPENAI_AGENTS_MODEL", ""),
            "KG_OPENAI_AGENTS_BASE_URL": _redact_url(os.getenv("KG_OPENAI_AGENTS_BASE_URL", "") or settings.DEEPSEEK_BASE_URL),
            "KG_OPENAI_AGENTS_MAX_TOKENS": os.getenv("KG_OPENAI_AGENTS_MAX_TOKENS", ""),
            "KG_LANGFUSE_ENABLED": os.getenv("KG_LANGFUSE_ENABLED", "1" if LANGFUSE_ENABLED else "0"),
            "LANGFUSE_BASE_URL": _redact_url(os.getenv("LANGFUSE_BASE_URL", "")),
            "LANGFUSE_HOST": _redact_url(os.getenv("LANGFUSE_HOST", "")),
        },
        "openai_agents_installed": _module_installed("agents"),
        "langfuse_installed": _module_installed("langfuse"),
        "openinference_openai_agents_installed": _module_installed("openinference.instrumentation.openai_agents"),
        "kg_counts": kg_counts(target),
        "milvus": milvus_status(),
    }


def kg_counts(target: str) -> dict[str, int | str]:
    tables = [
        "kg_nodes",
        "kg_edges",
        "kg_evidence",
        "kg_retrieval_documents",
        "kg_wiki_pages",
        "kg_graph_adjacency",
    ]
    result: dict[str, int | str] = {}
    with get_session(target) as session:
        for table in tables:
            try:
                result[table] = int(session.execute(text(f"select count(*) from {table}")).scalar_one())
            except Exception as exc:
                result[table] = f"error: {type(exc).__name__}: {exc}"
    return result


def milvus_status() -> dict[str, Any]:
    try:
        store = MilvusHybridStore()
        try:
            store.ensure_ready()
            return {
                "available": True,
                "collection": store.collection_name,
                "uri": store.uri,
                "dim": store.dim,
            }
        finally:
            store.close()
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def latest_ft_news_queries(target: str, limit: int) -> list[str]:
    if limit <= 0:
        return []
    sql = """
    select id, title
    from ft_news
    where title is not null and btrim(title) <> ''
    order by created_at desc nulls last, id desc
    limit :limit
    """
    with get_session(target) as session:
        rows = session.execute(text(sql), {"limit": min(limit, 50)}).mappings().all()
    return [
        f"{row['title']} 这条新闻涉及哪些主体、行业或资产影响"
        for row in rows
    ]


def _has_agent_final(controller_decisions: list[dict[str, Any]]) -> bool:
    return any(item.get("auto_action") == "agent_final" for item in controller_decisions if isinstance(item, dict))


def _last_stop_reason(controller_decisions: list[dict[str, Any]]) -> str | None:
    for item in reversed(controller_decisions):
        if isinstance(item, dict) and item.get("stop_reason"):
            return str(item["stop_reason"])
    return None


def _agent_decision_summary(controller_decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in controller_decisions:
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "tool": item.get("tool_name"),
                "auto_action": item.get("auto_action"),
                "raw_candidates": item.get("raw_candidate_count"),
                "packages": item.get("package_count"),
                "keep": item.get("keep_count"),
                "stop_reason": item.get("stop_reason"),
                "duration_ms": item.get("tool_duration_ms"),
            }
        )
    return items


def write_summary(path: str, summary: dict[str, Any]) -> None:
    output = Path(path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def _redact_url(value: str) -> str:
    if not value:
        return ""
    return value.split("@")[-1]


def _module_installed(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def _ordered_unique(values) -> list:
    result: list = []
    seen: set = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def print_title(title: str) -> None:
    print(f"\n{'=' * 12} {title} {'=' * 12}")


if __name__ == "__main__":
    asyncio.run(main())
