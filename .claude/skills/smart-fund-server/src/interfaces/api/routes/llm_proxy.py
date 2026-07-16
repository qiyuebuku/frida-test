"""OpenAI-compatible LLM 代理接口。"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import StreamingResponse

from src.infrastructure.config import settings
from src.infrastructure.clients.embedding import embed_texts
from src.infrastructure.llm_proxy import (
    LLMProxyRequest,
    LLMProxyError,
    get_llm_gateway_service,
)

router = APIRouter(tags=["LLM代理"])


class ChatContentPart(BaseModel):
    type: str = Field("text")
    text: str | None = Field(None)


class ChatMessage(BaseModel):
    role: str
    content: str | list[ChatContentPart | dict[str, Any]]


class ResponseFormatConfig(BaseModel):
    type: str = Field("text")
    json_schema: dict[str, Any] | None = Field(None)


class ChatCompletionRequest(BaseModel):
    model: str | None = Field(None)
    messages: list[ChatMessage]
    temperature: float | None = Field(0.0)
    max_tokens: int | None = Field(None)
    stream: bool = Field(False)
    response_format: ResponseFormatConfig | None = Field(None)
    tools: list[dict[str, Any]] | None = Field(None)
    tool_choice: str | dict[str, Any] | None = Field(None)
    metadata: dict[str, Any] | None = Field(None)

    model_config = ConfigDict(extra="allow")


class EmbeddingRequest(BaseModel):
    input: str | list[str]
    model: str | None = Field(None)
    dimensions: int | None = Field(None)
    encoding_format: str | None = Field("float")
    user: str | None = Field(None)

    model_config = ConfigDict(extra="allow")


ChatContentPart.model_rebuild()
ChatMessage.model_rebuild()
ResponseFormatConfig.model_rebuild()
ChatCompletionRequest.model_rebuild()
EmbeddingRequest.model_rebuild()


@router.get("/api/llm-proxy/health", summary="LLM代理健康检查")
async def llm_proxy_health():
    return get_llm_gateway_service().health()


@router.post("/v1/chat/completions", summary="OpenAI-compatible chat completions")
async def chat_completions(request: ChatCompletionRequest):
    system_prompt, prompt = _split_messages(request.messages)
    if not prompt:
        raise HTTPException(status_code=400, detail="messages 中缺少可用的非 system 内容")

    json_schema = _resolve_json_schema(request.response_format)

    try:
        result = await get_llm_gateway_service().generate(
            LLMProxyRequest(
                prompt=prompt,
                system_prompt=system_prompt,
                model=request.model,
                messages=[message.model_dump(mode="json") for message in request.messages],
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                json_schema=json_schema,
                response_format=request.response_format.model_dump(mode="json") if request.response_format else None,
                tools=request.tools,
                tool_choice=request.tool_choice,
                metadata=request.metadata or {},
            )
        )
    except LLMProxyError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    content = result.text
    if result.structured_output is not None:
        content = json.dumps(result.structured_output, ensure_ascii=False)

    if request.stream:
        return _stream_chat_completion(
            content=content,
            reasoning_content=result.reasoning_content,
            model=result.proxy.get("resolved_model") or request.model or settings.LLM_PROXY_DEFAULT_MODEL,
        )

    usage = _normalize_usage(result.usage)
    response_model = result.proxy.get("resolved_model") or request.model or settings.LLM_PROXY_DEFAULT_MODEL
    raw_message = (result.raw_payload or {}).get("message") or {}
    message = {"role": "assistant", "content": content}
    if raw_message.get("tool_calls") is not None:
        message["tool_calls"] = raw_message["tool_calls"]
    if result.reasoning_content:
        message["reasoning_content"] = result.reasoning_content
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": response_model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": (result.raw_payload or {}).get("finish_reason") or "stop",
            }
        ],
        "usage": usage,
        "_proxy": {
            **result.proxy,
            "session_id": result.session_id,
            "cache_hit": result.cache_hit,
            "duration_ms": result.duration_ms,
            "structured_output": result.structured_output,
        },
    }


@router.post("/v1/embeddings", summary="OpenAI-compatible embeddings")
async def embeddings(request: EmbeddingRequest):
    if request.encoding_format and request.encoding_format != "float":
        raise HTTPException(status_code=400, detail="当前 embeddings 仅支持 encoding_format=float")

    texts = [request.input] if isinstance(request.input, str) else list(request.input)
    if not texts or any(not isinstance(text, str) or not text.strip() for text in texts):
        raise HTTPException(status_code=400, detail="input 必须是非空字符串或非空字符串数组")

    dim = request.dimensions or settings.EMBEDDING_DIM
    min_dim = getattr(settings, "EMBEDDING_MIN_DIM", 32)
    max_dim = getattr(settings, "EMBEDDING_MAX_DIM", 2560)
    if dim < min_dim or dim > max_dim:
        raise HTTPException(
            status_code=400,
            detail=f"dimensions 必须在 {min_dim}..{max_dim} 范围内",
        )

    vectors = await embed_texts(texts, dim=dim, normalize=True)
    if len(vectors) != len(texts) or any(vec is None for vec in vectors):
        raise HTTPException(status_code=502, detail="embedding 服务调用失败")

    model = request.model or settings.EMBEDDING_MODEL
    prompt_tokens = _estimate_embedding_tokens(texts)
    return {
        "object": "list",
        "data": [
            {
                "object": "embedding",
                "index": idx,
                "embedding": vector,
            }
            for idx, vector in enumerate(vectors)
        ],
        "model": model,
        "usage": {
            "prompt_tokens": prompt_tokens,
            "total_tokens": prompt_tokens,
        },
        "_proxy": {
            "provider": "embedding_service",
            "base_url": settings.EMBEDDING_URL,
            "dim": dim,
            "batch_size": settings.EMBEDDING_BATCH_SIZE,
        },
    }


def _stream_chat_completion(
    *,
    content: str,
    reasoning_content: str = "",
    model: str,
) -> StreamingResponse:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    async def event_stream():
        first = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(first, ensure_ascii=False)}\n\n"

        for idx in range(0, len(reasoning_content), 800):
            reasoning_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "reasoning_content": reasoning_content[idx : idx + 800]
                        },
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(reasoning_chunk, ensure_ascii=False)}\n\n"

        # This proxy is backed by an interactive TUI, so it cannot stream tokens
        # as they are generated. It returns an OpenAI-compatible SSE stream once
        # the full assistant message is available.
        for idx in range(0, len(content), 800):
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": content[idx : idx + 800]},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

        final = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(final, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _split_messages(messages: list[ChatMessage]) -> tuple[str | None, str]:
    system_parts: list[str] = []
    prompt_parts: list[str] = []

    for message in messages:
        text = _content_to_text(message.content).strip()
        if not text:
            continue
        if message.role == "system":
            system_parts.append(text)
            continue
        prompt_parts.append(f"{message.role.upper()}:\n{text}")

    system_prompt = "\n\n".join(system_parts).strip() or None
    prompt = "\n\n".join(prompt_parts).strip()
    return system_prompt, prompt


def _content_to_text(content: str | list[ChatContentPart | dict[str, Any]]) -> str:
    if isinstance(content, str):
        return content

    parts: list[str] = []
    for item in content:
        if isinstance(item, ChatContentPart):
            if item.type == "text" and item.text:
                parts.append(item.text)
            continue
        if isinstance(item, dict) and item.get("type") == "text":
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _resolve_json_schema(response_format: ResponseFormatConfig | None) -> dict[str, Any] | None:
    if not response_format:
        return None
    if response_format.type == "json_schema" and response_format.json_schema:
        if "schema" in response_format.json_schema and isinstance(
            response_format.json_schema.get("schema"), dict
        ):
            return response_format.json_schema["schema"]
        return response_format.json_schema
    if response_format.type == "json_object":
        return {"type": "object"}
    return None


def _normalize_usage(usage: dict[str, Any]) -> dict[str, int]:
    prompt_tokens = int(usage.get("input_tokens", 0) or 0)
    completion_tokens = int(usage.get("output_tokens", 0) or 0)
    normalized = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    if "prompt_cache_hit_tokens" in usage:
        normalized["prompt_cache_hit_tokens"] = int(usage.get("prompt_cache_hit_tokens", 0) or 0)
    if "prompt_cache_miss_tokens" in usage:
        normalized["prompt_cache_miss_tokens"] = int(usage.get("prompt_cache_miss_tokens", 0) or 0)
    if "reasoning_tokens" in usage:
        normalized["completion_tokens_details"] = {
            "reasoning_tokens": int(usage.get("reasoning_tokens", 0) or 0),
        }
    return normalized


def _estimate_embedding_tokens(texts: list[str]) -> int:
    # OpenAI-compatible clients expect usage, but the local embedding service
    # does not return tokenizer counts. This rough estimate is only for metadata.
    return sum(max(1, len(text) // 4) for text in texts)
