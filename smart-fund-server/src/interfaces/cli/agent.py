"""OpenAI Agents SDK Research Agent CLI."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import click

from src.application.agents.financial_research.runtime import FinancialAgentRuntime
from src.application.agents.financial_research.replay_suite import (
    DEFAULT_REPLAY_SUITE,
    load_replay_suite,
)
from src.application.agents.financial_research.schemas import (
    ResearchContextPack,
    ResearchRunMode,
    ResearchTriggerEnvelope,
    ResearchTriggerSlot,
)
from src.infrastructure.agent_runtime.langfuse_health import (
    check_langfuse_health,
)


@click.group("agent")
def agent() -> None:
    """运行或检查 Research Agent。"""


async def _list_tools() -> dict[str, object]:
    async with FinancialAgentRuntime() as runtime:
        tools = await runtime.list_tools()
        langfuse = await check_langfuse_health(runtime.settings)
        return {
            "status": "ok",
            "model": runtime.settings.model,
            "mcp_url": runtime.settings.mcp_url,
            "langfuse": langfuse.as_dict(),
            "tool_count": len(tools),
            "tools": tools,
        }


@agent.command("check")
def check_agent() -> None:
    """检查配置、MCP 连接和当前可用工具。"""

    payload = asyncio.run(_list_tools())
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


async def _run_agent(
    *,
    context_pack: ResearchContextPack,
    session_id: str | None,
):
    async with FinancialAgentRuntime() as runtime:
        return await runtime.run(
            context_pack,
            session_id=session_id,
        )


async def _prepare_and_run_agent(
    *,
    trigger: ResearchTriggerEnvelope,
    research_question: str | None,
    session_id: str | None,
    publish: bool,
):
    async with FinancialAgentRuntime() as runtime:
        return await runtime.prepare_and_run(
            trigger,
            research_question=research_question,
            session_id=session_id,
            publish=publish,
        )


@agent.command("run-context")
@click.argument(
    "context_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--session-id", help="仅用于 Trace 关联，不作为正式研究记忆")
@click.option("--json-output", is_flag=True, help="输出完整结构化 JSON")
def run_context_agent(
    context_file: Path,
    session_id: str | None,
    json_output: bool,
) -> None:
    """使用有界 Research Context Pack 执行一次只读研究。"""

    try:
        payload = json.loads(context_file.read_text(encoding="utf-8"))
        context_pack = ResearchContextPack.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise click.UsageError(f"无效 Research Context Pack: {exc}") from exc

    result = asyncio.run(
        _run_agent(
            context_pack=context_pack,
            session_id=session_id,
        )
    )
    if json_output:
        click.echo(result.model_dump_json(indent=2))
        return

    click.echo(result.report_summary)
    if result.evidence_gaps:
        click.echo("\nEvidence Gaps")
        for item in result.evidence_gaps:
            click.echo(f"- [{item.impact}] {item.description}")


@agent.command("run")
@click.option(
    "--trigger-slot",
    type=click.Choice([item.value for item in ResearchTriggerSlot]),
    default=ResearchTriggerSlot.EVENT.value,
    show_default=True,
)
@click.option(
    "--run-mode",
    type=click.Choice([item.value for item in ResearchRunMode]),
    default=ResearchRunMode.SHADOW.value,
    show_default=True,
)
@click.option("--reason", default="人工触发一次完整研究复核", show_default=True)
@click.option("--research-question", default="")
@click.option("--cutoff-at", default="", help="带时区 ISO-8601；默认当前时间")
@click.option("--session-id", help="仅用于 Trace 关联，不作为正式研究记忆")
@click.option("--publish", is_flag=True, help="仅 production 模式发布正式报告和观点")
@click.option("--json-output", is_flag=True, help="输出完整结构化 JSON")
def run_agent(
    trigger_slot: str,
    run_mode: str,
    reason: str,
    research_question: str,
    cutoff_at: str,
    session_id: str | None,
    publish: bool,
    json_output: bool,
) -> None:
    """由服务端自动准备上下文并执行一次真实 Research 运行。"""

    try:
        cutoff = (
            datetime.fromisoformat(cutoff_at.replace("Z", "+00:00"))
            if cutoff_at.strip()
            else datetime.now(UTC)
        )
        trigger = ResearchTriggerEnvelope(
            trigger_id=f"manual-{uuid4().hex}",
            trigger_slot=ResearchTriggerSlot(trigger_slot),
            source="replay" if run_mode == "replay" else "human",
            reason=reason,
            cutoff_at=cutoff,
            run_mode=ResearchRunMode(run_mode),
        )
    except ValueError as exc:
        raise click.UsageError(f"无效运行参数: {exc}") from exc
    result = asyncio.run(
        _prepare_and_run_agent(
            trigger=trigger,
            research_question=research_question or None,
            session_id=session_id,
            publish=publish,
        )
    )
    if json_output:
        click.echo(result.model_dump_json(indent=2))
    else:
        click.echo(result.report_summary)


@agent.command("replay")
@click.option(
    "--suite-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=DEFAULT_REPLAY_SUITE,
    show_default=True,
)
@click.option("--case-id", multiple=True, help="可重复指定；不传则运行完整30例")
@click.option("--json-output", is_flag=True)
def replay_agent(
    suite_file: Path,
    case_id: tuple[str, ...],
    json_output: bool,
) -> None:
    """按决策时点隔离未来数据，运行固定 Research 历史回放集。"""

    suite = load_replay_suite(suite_file)
    selected_ids = set(case_id)
    cases = [case for case in suite.cases if not selected_ids or case.case_id in selected_ids]
    missing = selected_ids.difference(case.case_id for case in cases)
    if missing:
        raise click.UsageError("未知 case-id: " + ", ".join(sorted(missing)))

    async def run_suite() -> list[dict[str, object]]:
        results = []
        async with FinancialAgentRuntime() as runtime:
            for case in cases:
                trigger = ResearchTriggerEnvelope(
                    trigger_id=f"replay:{suite.suite_id}:{case.case_id}",
                    trigger_slot=ResearchTriggerSlot.DEEP_RESEARCH,
                    source="replay",
                    reason=f"固定质量回放 {case.case_id}",
                    cutoff_at=case.decision_at,
                    run_mode=ResearchRunMode.REPLAY,
                )
                proposal = await runtime.prepare_and_run(
                    trigger,
                    research_question=case.research_question,
                    session_id=f"replay:{suite.suite_id}",
                    publish=False,
                )
                results.append({
                    "case_id": case.case_id,
                    "run_id": proposal.run_id,
                    "status": proposal.status.value,
                    "summary": proposal.report_summary,
                })
        return results

    results = asyncio.run(run_suite())
    if json_output:
        click.echo(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for result in results:
            click.echo(f"{result['case_id']} [{result['status']}] {result['summary']}")
