from types import SimpleNamespace

import pytest

from src.application.agents.financial_research.context import AgentRunContext
from src.application.agents.financial_research.schemas import ResearchTaskMode
from src.infrastructure.agent_runtime.observability import AgentAuditHooks


class _Observation:
    def __init__(self, record):
        self.record = record

    def update(self, **values):
        self.record["update"] = values

    def end(self):
        self.record["ended"] = True


class _Langfuse:
    def __init__(self):
        self.records = []

    def start_observation(self, **values):
        record = {"start": values}
        self.records.append(record)
        return _Observation(record)


def _hook_context(**values):
    run_context = AgentRunContext(
        run_id="run-business-trace",
        session_id="session-business-trace",
        task_mode=ResearchTaskMode.RESEARCH_REVIEW,
    )
    return SimpleNamespace(context=run_context, **values)


@pytest.mark.asyncio
async def test_business_trace_records_readable_llm_round_with_io():
    langfuse = _Langfuse()
    hooks = AgentAuditHooks(
        langfuse_client=langfuse,
        include_sensitive_data=True,
        model="glm-5.2",
    )
    context = _hook_context()
    agent = SimpleNamespace(name="Research Agent｜研究智能体", model="glm-5.2")

    await hooks.on_llm_start(
        context,
        agent,
        "system prompt",
        [{"role": "user", "content": "研究市场"}],
    )
    await hooks.on_llm_end(
        context,
        agent,
        SimpleNamespace(
            output=[{"role": "assistant", "content": "先看概览"}],
            usage=SimpleNamespace(
                input_tokens=120,
                output_tokens=30,
                total_tokens=150,
                input_tokens_details=SimpleNamespace(cached_tokens=40),
                output_tokens_details=SimpleNamespace(reasoning_tokens=10),
            ),
        ),
    )

    record = langfuse.records[0]
    assert record["start"]["name"] == "01 研究规划｜LLM 1"
    assert record["start"]["as_type"] == "generation"
    assert record["start"]["input"]["system_prompt"] == "system prompt"
    assert record["update"]["output"] == {
        "assistant_text": "先看概览",
        "tool_calls": [],
    }
    assert record["update"]["usage_details"] == {
        "input": 120,
        "output": 30,
        "total": 150,
        "cached_input": 40,
        "reasoning": 10,
    }
    assert record["ended"] is True


@pytest.mark.asyncio
async def test_business_trace_formats_json_user_message_as_object():
    langfuse = _Langfuse()
    hooks = AgentAuditHooks(
        langfuse_client=langfuse,
        include_sensitive_data=True,
        model="glm-5.2",
    )
    context = _hook_context()
    agent = SimpleNamespace(name="Research Agent｜研究智能体", model="glm-5.2")

    await hooks.on_llm_start(
        context,
        agent,
        "system prompt",
        [
            {
                "role": "user",
                "content": '{"research_task":{"question":"当前市场如何？"}}',
            }
        ],
    )

    conversation = langfuse.records[0]["start"]["input"]["conversation"]
    assert conversation[0]["content"] == {
        "research_task": {"question": "当前市场如何？"}
    }
    assert "text" not in conversation[0]
    hooks.close_open_observations()


@pytest.mark.asyncio
async def test_business_trace_simplifies_llm_tool_calls_and_json_tool_results():
    langfuse = _Langfuse()
    hooks = AgentAuditHooks(
        langfuse_client=langfuse,
        include_sensitive_data=True,
        model="glm-5.2",
    )
    context = _hook_context()
    agent = SimpleNamespace(name="Research Agent｜研究智能体", model="glm-5.2")

    await hooks.on_llm_start(context, agent, "system", [])
    await hooks.on_llm_end(
        context,
        agent,
        SimpleNamespace(
            output=[
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "检查市场概览。"}],
                },
                {
                    "type": "function_call",
                    "name": "market_frame_open",
                    "call_id": "call-frame",
                    "arguments": "{}",
                },
            ]
        ),
    )
    assert langfuse.records[0]["update"]["output"] == {
        "assistant_text": "检查市场概览。",
        "tool_calls": [
            {"tool": "market_frame_open", "call_id": "call-frame"}
        ],
    }

    tool_context = _hook_context(
        tool_call_id="call-frame",
        tool_name="market_frame_open",
        tool_arguments="{}",
    )
    tool = SimpleNamespace(name="market_frame_open")
    await hooks.on_tool_start(tool_context, agent, tool)
    await hooks.on_tool_end(
        tool_context,
        agent,
        tool,
        '{"type":"text","text":"{\\"status\\":\\"available\\",\\"count\\":11}"}',
    )
    assert langfuse.records[1]["update"]["output"] == {
        "status": "available",
        "count": 11,
    }


@pytest.mark.asyncio
async def test_business_trace_maps_tool_to_research_phase_and_marks_validation_failure():
    langfuse = _Langfuse()
    hooks = AgentAuditHooks(
        langfuse_client=langfuse,
        include_sensitive_data=True,
        model="glm-5.2",
    )
    context = _hook_context(
        tool_call_id="call-submit",
        tool_name="submit_research_conclusion",
        tool_arguments='{"proposal":{"status":"updated"}}',
    )
    tool = SimpleNamespace(name="submit_research_conclusion")

    await hooks.on_tool_start(context, SimpleNamespace(), tool)
    await hooks.on_tool_end(
        context,
        SimpleNamespace(),
        tool,
        "Research Proposal 校验失败：缺少已打开证据",
    )

    record = langfuse.records[0]
    assert record["start"]["name"] == (
        "07 报告校验与提交｜submit_research_conclusion"
    )
    assert record["start"]["input"]["arguments"] == {
        "proposal": {"status": "updated"}
    }
    assert record["update"]["level"] == "WARNING"
    assert record["update"]["status_message"] == (
        "proposal rejected; agent will revise"
    )
    assert record["ended"] is True
