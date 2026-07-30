"""OpenAI Agents SDK financial research CLI."""

from __future__ import annotations

import asyncio
import json
import sys

import click

from src.application.agents.financial_research import (
    FinancialAgentRuntime,
    ResearchTaskMode,
)


@click.group("agent")
def agent() -> None:
    """运行或检查自动化金融 Agent。"""


async def _list_tools(*, allow_writes: bool) -> dict[str, object]:
    async with FinancialAgentRuntime() as runtime:
        tools = await runtime.list_tools(allow_writes=allow_writes)
        return {
            "status": "ok",
            "model": runtime.settings.model,
            "mcp_url": runtime.settings.mcp_url,
            "langfuse": runtime.settings.langfuse_configured,
            "tool_count": len(tools),
            "tools": tools,
        }


@agent.command("check")
@click.option("--allow-writes", is_flag=True, help="同时检查跟踪名单写工具")
def check_agent(allow_writes: bool) -> None:
    """检查配置、MCP 连接和当前可用工具。"""

    payload = asyncio.run(_list_tools(allow_writes=allow_writes))
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


async def _run_agent(
    *,
    prompt: str,
    mode: ResearchTaskMode,
    session_id: str | None,
    allow_writes: bool,
):
    async with FinancialAgentRuntime() as runtime:
        return await runtime.run(
            prompt,
            task_mode=mode,
            session_id=session_id,
            allow_writes=allow_writes,
        )


@agent.command("run")
@click.argument("prompt", required=False)
@click.option(
    "--mode",
    type=click.Choice([mode.value for mode in ResearchTaskMode]),
    default=ResearchTaskMode.RESEARCH.value,
    show_default=True,
)
@click.option("--session-id", help="需要跨轮延续时使用的持久会话 ID")
@click.option("--allow-writes", is_flag=True, help="允许调用跟踪名单写工具")
@click.option("--json-output", is_flag=True, help="输出完整结构化 JSON")
def run_agent(
    prompt: str | None,
    mode: str,
    session_id: str | None,
    allow_writes: bool,
    json_output: bool,
) -> None:
    """执行一次金融研究；未传 PROMPT 时从标准输入读取。"""

    task = prompt if prompt is not None else sys.stdin.read()
    if not task.strip():
        raise click.UsageError("必须提供研究任务或通过标准输入传入")

    result = asyncio.run(
        _run_agent(
            prompt=task,
            mode=ResearchTaskMode(mode),
            session_id=session_id,
            allow_writes=allow_writes,
        )
    )
    if json_output:
        click.echo(result.model_dump_json(indent=2))
        return

    click.echo(result.conclusion)
    if result.uncertainties:
        click.echo("\n不确定性")
        for item in result.uncertainties:
            click.echo(f"- {item}")
