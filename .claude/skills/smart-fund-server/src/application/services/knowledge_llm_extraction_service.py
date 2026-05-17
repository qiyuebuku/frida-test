"""Application adapter from domain LLM extraction port to LLM Proxy."""

from __future__ import annotations

from src.domain.knowledge.extraction import (
    LLMFactExtractionRequest,
    LLMFactExtractionResult,
)
from src.infrastructure.llm_proxy.service import LLMGatewayService
from src.infrastructure.llm_proxy.types import LLMProxyRequest


class KnowledgeLLMExtractionService:
    def __init__(self, gateway: LLMGatewayService):
        self._gateway = gateway

    async def extract(self, request: LLMFactExtractionRequest) -> LLMFactExtractionResult:
        response = await self._gateway.generate(
            LLMProxyRequest(
                prompt=request.prompt,
                system_prompt=request.system_prompt,
                messages=list(request.messages or []),
                model=request.model,
                json_schema=request.json_schema,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                metadata={
                    **request.metadata,
                    "task": request.task,
                    "source_id": request.source_id,
                    "source_type": request.source_type,
                },
                use_cache=request.use_cache,
            )
        )
        return LLMFactExtractionResult(
            text=response.text,
            structured_output=response.structured_output,
            metadata={
                "usage": dict(response.usage or {}),
                "session_id": response.session_id,
                "duration_ms": response.duration_ms,
                "cache_hit": response.cache_hit,
                "proxy": dict(response.proxy or {}),
            },
        )
