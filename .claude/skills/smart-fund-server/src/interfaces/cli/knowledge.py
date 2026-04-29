"""Knowledge graph CLI commands."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Literal

import click

from src.application.dto.knowledge_dto import (
    KnowledgeBadCaseReplayCommand,
    KnowledgeBootstrapStockNewsCommand,
    KnowledgeBootstrapStocksCommand,
    KnowledgeCompileCommand,
    KnowledgeQualityScanCommand,
    KnowledgeRebuildIndexesCommand,
    KnowledgeRebuildWikiCommand,
    KnowledgeResearchContextCommand,
    KnowledgeResearchContextBadCase,
    dto_to_dict,
)
from src.application.services.knowledge_adapter_registry import AdapterNotFoundError
from src.application.services.knowledge_service import create_knowledge_service

Target = Literal["prod", "test"]


@click.group("kg")
def kg():
    """Knowledge infrastructure commands."""


@kg.command("health")
@click.option("--target", type=click.Choice(["prod", "test"]), default="prod")
@click.option("--json", "json_output", is_flag=True, help="输出 JSON")
def health(target: Target, json_output: bool):
    """Show current knowledge infrastructure status."""
    result = _run(create_knowledge_service(target=target).health())
    _echo(result, json_output, _health_summary)


@kg.command("compile")
@click.option("--adapter", "adapter_name", default="financial", help="adapter 名称")
@click.option("--file", "file_path", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--target", type=click.Choice(["prod", "test"]), default="prod")
@click.option("--dry-run", is_flag=True, help="只编译不写入")
@click.option("--request-id", default=None, help="调用方幂等 ID")
@click.option("--concurrency", default=None, type=click.IntRange(1, 20), help="编译并发数")
@click.option("--json", "json_output", is_flag=True, help="输出 JSON")
def compile_kg(
    adapter_name: str,
    file_path: str,
    target: Target,
    dry_run: bool,
    request_id: str | None,
    concurrency: int | None,
    json_output: bool,
):
    """Compile raw records into the knowledge graph."""
    records = _load_records(Path(file_path))
    result = _run(
        create_knowledge_service(target=target).compile_kg(
            KnowledgeCompileCommand(
                adapter_name=adapter_name,
                records=records,
                target=target,
                dry_run=dry_run,
                request_id=request_id,
                concurrency=concurrency,
            )
        )
    )
    _echo(result, json_output, _compile_summary)


@kg.command("bootstrap-stocks")
@click.option("--target", type=click.Choice(["prod", "test"]), default="prod")
@click.option("--code", "codes", multiple=True, help="可重复传入，如 300750")
@click.option("--limit", default=500, type=click.IntRange(1, 5000))
@click.option("--dry-run", is_flag=True, help="只编译不写入")
@click.option("--request-id", default=None, help="调用方幂等 ID")
@click.option("--json", "json_output", is_flag=True, help="输出 JSON")
def bootstrap_stocks(
    target: Target,
    codes: tuple[str, ...],
    limit: int,
    dry_run: bool,
    request_id: str | None,
    json_output: bool,
):
    """Bootstrap stock nodes from existing business source tables."""
    result = _run(
        create_knowledge_service(target=target).bootstrap_financial_stock_entities(
            KnowledgeBootstrapStocksCommand(
                target=target,
                codes=list(codes),
                limit=limit,
                dry_run=dry_run,
                request_id=request_id,
            )
        )
    )
    _echo(result, json_output, _compile_summary)


@kg.command("bootstrap-stock-news")
@click.option("--target", type=click.Choice(["prod", "test"]), default="prod")
@click.option("--code", "codes", multiple=True, help="可重复传入，如 300750")
@click.option("--limit", default=20, type=click.IntRange(1, 5000))
@click.option("--dry-run", is_flag=True, help="只编译不写入")
@click.option("--request-id", default=None, help="调用方幂等 ID")
@click.option("--concurrency", default=1, type=click.IntRange(1, 20), help="编译并发数")
@click.option("--json", "json_output", is_flag=True, help="输出 JSON")
def bootstrap_stock_news(
    target: Target,
    codes: tuple[str, ...],
    limit: int,
    dry_run: bool,
    request_id: str | None,
    concurrency: int,
    json_output: bool,
):
    """Bootstrap stock-related news from ft_news into KG."""
    result = _run(
        create_knowledge_service(target=target).bootstrap_financial_stock_news(
            KnowledgeBootstrapStockNewsCommand(
                target=target,
                codes=list(codes),
                limit=limit,
                dry_run=dry_run,
                request_id=request_id,
                concurrency=concurrency,
            )
        )
    )
    _echo(result, json_output, _compile_summary)


@kg.command("rebuild-wiki")
@click.option("--adapter", "adapter_name", default="financial", help="adapter 名称")
@click.option("--target", type=click.Choice(["prod", "test"]), default="prod")
@click.option("--scope", default="all", help="第一版仅支持 all")
@click.option("--json", "json_output", is_flag=True, help="输出 JSON")
def rebuild_wiki(adapter_name: str, target: Target, scope: str, json_output: bool):
    """Rebuild knowledge wiki pages."""
    result = _run(
        create_knowledge_service(target=target).rebuild_wiki_for(
            KnowledgeRebuildWikiCommand(adapter_name=adapter_name, target=target, scope=scope)
        )
    )
    _echo(result, json_output, _wiki_summary)


@kg.command("rebuild-indexes")
@click.option("--adapter", "adapter_name", default="financial", help="adapter 名称")
@click.option("--target", type=click.Choice(["prod", "test"]), default="prod")
@click.option("--index-type", "index_types", multiple=True, help="可重复传入")
@click.option("--scope", default="all", help="第一版仅支持 all")
@click.option("--json", "json_output", is_flag=True, help="输出 JSON")
def rebuild_indexes(
    adapter_name: str,
    target: Target,
    index_types: tuple[str, ...],
    scope: str,
    json_output: bool,
):
    """Rebuild graph adjacency and evidence chunk indexes."""
    result = _run(
        create_knowledge_service(target=target).rebuild_indexes_for(
            KnowledgeRebuildIndexesCommand(
                adapter_name=adapter_name,
                target=target,
                index_types=list(index_types) or ["graph_adjacency", "evidence_chunks"],
                scope=scope,
            )
        )
    )
    _echo(result, json_output, _indexes_summary)


@kg.command("query")
@click.option("--adapter", "adapter_name", default="financial", help="adapter 名称")
@click.option("--query", "query_text", required=True, help="查询文本")
@click.option("--target", type=click.Choice(["prod", "test"]), default="prod")
@click.option("--graph-depth", default=3, type=click.IntRange(1, 4))
@click.option("--graph-limit", default=20, type=click.IntRange(1, 100))
@click.option("--wiki-limit", default=10, type=click.IntRange(1, 100))
@click.option("--evidence-limit", default=20, type=click.IntRange(1, 100))
@click.option("--max-chars", default=5000, type=click.IntRange(500, 20000))
@click.option(
    "--retrieval-mode",
    type=click.Choice(["deterministic_plan", "agentic_arag"]),
    default="deterministic_plan",
)
@click.option("--json", "json_output", is_flag=True, help="输出 JSON")
def query(
    adapter_name: str,
    query_text: str,
    target: Target,
    graph_depth: int,
    graph_limit: int,
    wiki_limit: int,
    evidence_limit: int,
    max_chars: int,
    retrieval_mode: str,
    json_output: bool,
):
    """Build a structured research context."""
    result = _run(
        create_knowledge_service(target=target).build_research_context_for(
            KnowledgeResearchContextCommand(
                adapter_name=adapter_name,
                target=target,
                query=query_text,
                retrieval_mode=retrieval_mode,
                graph_depth=graph_depth,
                graph_limit=graph_limit,
                wiki_limit=wiki_limit,
                evidence_limit=evidence_limit,
                max_chars=max_chars,
            )
        )
    )
    _echo(result, json_output, _query_summary)


@kg.command("replay-bad-cases")
@click.option("--adapter", "adapter_name", default="financial", help="adapter 名称")
@click.option("--file", "file_path", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--target", type=click.Choice(["prod", "test"]), default="prod")
@click.option("--graph-depth", default=3, type=click.IntRange(1, 4))
@click.option("--graph-limit", default=20, type=click.IntRange(1, 100))
@click.option("--wiki-limit", default=10, type=click.IntRange(1, 100))
@click.option("--evidence-limit", default=20, type=click.IntRange(1, 100))
@click.option("--max-chars", default=5000, type=click.IntRange(500, 20000))
@click.option("--json", "json_output", is_flag=True, help="输出 JSON")
def replay_bad_cases(
    adapter_name: str,
    file_path: str,
    target: Target,
    graph_depth: int,
    graph_limit: int,
    wiki_limit: int,
    evidence_limit: int,
    max_chars: int,
    json_output: bool,
):
    """Replay research-context bad cases."""
    result = _run(
        create_knowledge_service(target=target).replay_research_context_bad_cases(
            KnowledgeBadCaseReplayCommand(
                adapter_name=adapter_name,
                target=target,
                cases=_load_bad_case_cases(Path(file_path)),
                graph_depth=graph_depth,
                graph_limit=graph_limit,
                wiki_limit=wiki_limit,
                evidence_limit=evidence_limit,
                max_chars=max_chars,
            )
        )
    )
    _echo(result, json_output, _bad_case_replay_summary)


@kg.command("resolve-financial")
@click.option("--text", "text", required=True, help="待解析文本")
@click.option("--target", type=click.Choice(["prod", "test"]), default="prod")
@click.option("--limit", default=20, type=click.IntRange(1, 100))
@click.option("--json", "json_output", is_flag=True, help="输出 JSON")
def resolve_financial(text: str, target: Target, limit: int, json_output: bool):
    """Resolve financial entities from text."""
    result = _run(create_knowledge_service(target=target).resolve_financial_entities(text, limit=limit))
    _echo(result, json_output, lambda data: f"candidates={len(data.get('candidates', []))}")


@kg.command("quality-scan")
@click.option("--adapter", "adapter_name", default="financial", help="adapter 名称")
@click.option("--target", type=click.Choice(["prod", "test"]), default="prod")
@click.option("--persist-review/--no-persist-review", default=True)
@click.option("--json", "json_output", is_flag=True, help="输出 JSON")
def quality_scan(adapter_name: str, target: Target, persist_review: bool, json_output: bool):
    """Run knowledge graph quality scan."""
    result = _run(
        create_knowledge_service(target=target).quality_scan_for(
            KnowledgeQualityScanCommand(
                adapter_name=adapter_name,
                target=target,
                persist_review=persist_review,
            )
        )
    )
    _echo(result, json_output, _quality_summary)


@kg.command("reviews")
@click.option("--status", default="open", help="review 状态，传空字符串查看全部")
@click.option("--target", type=click.Choice(["prod", "test"]), default="prod")
@click.option("--json", "json_output", is_flag=True, help="输出 JSON")
def reviews(status: str, target: Target, json_output: bool):
    """List knowledge review queue."""
    result = _run(
        create_knowledge_service(target=target).list_reviews_for(status=status or None)
    )
    _echo(result, json_output, lambda data: f"total={data.get('total', 0)}")


def _run(coro):
    try:
        return asyncio.run(coro)
    except AdapterNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


def _load_records(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"JSON 解析失败: {exc}") from exc
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict) and isinstance(payload.get("records"), list):
        records = payload["records"]
    else:
        raise click.ClickException("JSON 必须是数组，或包含 records 数组的对象")
    if not all(isinstance(item, dict) for item in records):
        raise click.ClickException("records 中的每一项都必须是对象")
    return records


def _load_bad_case_cases(path: Path) -> list[KnowledgeResearchContextBadCase]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"JSON 解析失败: {exc}") from exc
    raw_cases = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(raw_cases, list):
        raise click.ClickException("bad case JSON 必须是数组，或包含 cases 数组的对象")
    try:
        return [KnowledgeResearchContextBadCase(**item) for item in raw_cases]
    except TypeError as exc:
        raise click.ClickException(f"bad case 字段错误: {exc}") from exc


def _echo(result, json_output: bool, summary_fn):
    data = result.to_dict() if hasattr(result, "to_dict") else dto_to_dict(result)
    if json_output:
        click.echo(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        return
    click.echo(summary_fn(data))


def _health_summary(data: dict[str, Any]) -> str:
    return (
        f"status={data.get('status')} database={data.get('database')} "
        f"adapters={','.join(data.get('adapters', []))}"
    )


def _compile_summary(data: dict[str, Any]) -> str:
    return (
        f"adapter={data.get('adapter_name')} nodes={data.get('nodes')} "
        f"edges={data.get('edges')} evidence={data.get('evidence')} "
        f"failed={data.get('failed_records')} dry_run={data.get('dry_run')}"
    )


def _wiki_summary(data: dict[str, Any]) -> str:
    return f"adapter={data.get('adapter_name')} pages={data.get('pages')} issues={data.get('issues')}"


def _indexes_summary(data: dict[str, Any]) -> str:
    return (
        f"adapter={data.get('adapter_name')} "
        f"graph_adjacency={data.get('graph_adjacency')} "
        f"evidence_chunks={data.get('evidence_chunks')} "
        f"hybrid_chunks={data.get('hybrid_chunks', 0)}"
    )


def _query_summary(data: dict[str, Any]) -> str:
    return (
        f"hits={len(data.get('hits', []))} "
        f"nodes={len(data.get('matched_nodes', []))} "
        f"edges={len(data.get('matched_edges', []))} "
        f"evidence_refs={len(data.get('evidence_refs', []))} "
        f"channels={','.join(data.get('retrieval_channels_used', []))}"
    )


def _bad_case_replay_summary(data: dict[str, Any]) -> str:
    metrics = data.get("metrics") or {}
    return (
        f"bad_cases total={data.get('total')} "
        f"passed={data.get('passed')} failed={data.get('failed')} "
        f"pass_rate={metrics.get('pass_rate', 0):.2f}"
    )


def _quality_summary(data: dict[str, Any]) -> str:
    return (
        f"adapter={data.get('adapter_name')} ok={data.get('ok')} "
        f"issues={len(data.get('issues', []))} review_items={data.get('review_items')}"
    )
