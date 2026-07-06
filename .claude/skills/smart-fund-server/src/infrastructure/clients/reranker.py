"""HTTP client for the KG reranker service."""

from __future__ import annotations

import logging
import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any

import httpx

from src.infrastructure.config.settings import (
    RERANKER_MAX_DOCUMENTS,
    RERANKER_MAX_RETRIES,
    RERANKER_RETRY_BASE_DELAY,
    RERANKER_RETRY_MAX_DELAY,
    RERANKER_TIMEOUT,
    RERANKER_URL,
)
from src.infrastructure.observability.langfuse_tracing import (
    clip_trace_text,
    langfuse_observation,
    langfuse_update_span,
)

logger = logging.getLogger(__name__)
_RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
_RETRYABLE_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
    httpx.ProxyError,
)


class RerankerError(RuntimeError):
    """Raised when the reranker service cannot produce a valid ranked result."""


@dataclass(frozen=True)
class RerankResult:
    index: int
    relevance_score: float
    document: str


@dataclass(frozen=True)
class RerankResponse:
    model: str
    results: list[RerankResult]
    latency_ms: float
    total_documents: int


class RerankerClient:
    """Small OpenAI-independent client for the external reranker endpoint."""

    def __init__(
        self,
        *,
        base_url: str = RERANKER_URL,
        timeout: float = RERANKER_TIMEOUT,
        max_documents: int = RERANKER_MAX_DOCUMENTS,
        max_retries: int = RERANKER_MAX_RETRIES,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_documents = max_documents
        self.max_retries = max(1, int(max_retries))

    async def rerank(
        self,
        *,
        query: str,
        documents: list[str],
        top_n: int | None = None,
    ) -> RerankResponse:
        query = query.strip()
        if not query:
            raise RerankerError("rerank query is empty")
        if not documents:
            raise RerankerError("rerank documents are empty")
        if len(documents) > self.max_documents:
            raise RerankerError(
                f"rerank documents exceed max_documents: {len(documents)} > {self.max_documents}"
            )

        payload: dict[str, Any] = {"query": query, "documents": documents}
        if top_n and top_n > 0:
            payload["top_n"] = top_n

        started = time.perf_counter()
        with langfuse_observation(
            name="reranker.http_request",
            as_type="span",
            input={
                "query": query,
                "documents": [clip_trace_text(document, limit=2000) for document in documents[:10]],
                "document_count": len(documents),
                "top_n": top_n,
            },
            metadata={
                "endpoint": f"{self.base_url}/v1/rerank",
                "max_documents": self.max_documents,
                "timeout": self.timeout,
                "max_retries": self.max_retries,
            },
        ):
            try:
                response, attempts = await self._post_with_retry(payload)
                latency_ms = (time.perf_counter() - started) * 1000
                logger.info(
                    "reranker http request done: documents=%d top_n=%s latency=%.2fs status_code=%d attempts=%d endpoint=%s",
                    len(documents),
                    top_n or "all",
                    latency_ms / 1000,
                    response.status_code,
                    attempts,
                    f"{self.base_url}/v1/rerank",
                )
                langfuse_update_span(
                    output={
                        "status_code": response.status_code,
                        "latency_ms": round(latency_ms, 2),
                        "documents": len(documents),
                        "top_n": top_n or "all",
                        "attempts": attempts,
                    },
                    metadata={"status_code": response.status_code, "latency_ms": latency_ms, "attempts": attempts},
                )
            except Exception as exc:
                langfuse_update_span(
                    metadata={"error_type": exc.__class__.__name__},
                    level="ERROR",
                    status_message=str(exc),
                )
                raise

            try:
                data = response.json()
            except ValueError as exc:
                langfuse_update_span(
                    metadata={"error_type": exc.__class__.__name__},
                    level="ERROR",
                    status_message=f"reranker response is not JSON: {response.text[:1000]}",
                )
                raise RerankerError(f"reranker response is not JSON: {response.text[:1000]}") from exc
            parsed = _parse_rerank_response(data, fallback_latency_ms=latency_ms)
            langfuse_update_span(
                output={
                    "model": parsed.model,
                    "ranked_count": len(parsed.results),
                    "top_results": [
                        {
                            "index": item.index,
                            "score": item.relevance_score,
                            "document": clip_trace_text(item.document, limit=1200),
                        }
                        for item in parsed.results[:10]
                    ],
                    "latency_ms": parsed.latency_ms,
                },
                status_message="completed",
            )
            return parsed

    async def _post_with_retry(self, payload: dict[str, Any]) -> tuple[httpx.Response, int]:
        last_error: Exception | None = None
        endpoint = f"{self.base_url}/v1/rerank"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(1, self.max_retries + 1):
                try:
                    response = await client.post(endpoint, json=payload)
                    if response.status_code in _RETRYABLE_STATUS_CODES and attempt < self.max_retries:
                        delay = _retry_delay(attempt)
                        logger.warning(
                            "reranker request retryable status: status=%d attempt=%d/%d delay=%.1fs endpoint=%s",
                            response.status_code,
                            attempt,
                            self.max_retries,
                            delay,
                            endpoint,
                        )
                        await asyncio.sleep(delay)
                        continue
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        raise RerankerError(
                            f"reranker request failed: status={response.status_code} body={response.text[:1000]}"
                        ) from exc
                    return response, attempt
                except _RETRYABLE_EXCEPTIONS as exc:
                    last_error = exc
                    if attempt >= self.max_retries:
                        break
                    delay = _retry_delay(attempt)
                    logger.warning(
                        "reranker request retry: error=%s attempt=%d/%d delay=%.1fs endpoint=%s",
                        exc.__class__.__name__,
                        attempt,
                        self.max_retries,
                        delay,
                        endpoint,
                    )
                    await asyncio.sleep(delay)
        assert last_error is not None
        raise last_error


def _retry_delay(attempt: int) -> float:
    delay = RERANKER_RETRY_BASE_DELAY * (2 ** max(0, attempt - 1))
    capped = min(delay, RERANKER_RETRY_MAX_DELAY)
    return min(capped + random.uniform(0, capped * 0.2), RERANKER_RETRY_MAX_DELAY)


def _parse_rerank_response(data: Any, *, fallback_latency_ms: float) -> RerankResponse:
    if not isinstance(data, dict):
        raise RerankerError("reranker response root is not an object")
    raw_results = data.get("results")
    if not isinstance(raw_results, list):
        raise RerankerError("reranker response missing results array")

    results: list[RerankResult] = []
    seen_indexes: set[int] = set()
    for item in raw_results:
        if not isinstance(item, dict):
            raise RerankerError("reranker result item is not an object")
        index = item.get("index")
        score = item.get("relevance_score")
        document = item.get("document")
        if not isinstance(index, int) or index < 0:
            raise RerankerError(f"reranker result has invalid index: {index!r}")
        if index in seen_indexes:
            raise RerankerError(f"reranker result has duplicate index: {index}")
        if not isinstance(score, int | float):
            raise RerankerError(f"reranker result has invalid relevance_score: {score!r}")
        if not isinstance(document, str):
            raise RerankerError("reranker result has invalid document")
        seen_indexes.add(index)
        results.append(
            RerankResult(
                index=index,
                relevance_score=float(score),
                document=document,
            )
        )

    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    latency_ms = usage.get("latency_ms", fallback_latency_ms)
    total_documents = usage.get("total_documents", len(results))
    return RerankResponse(
        model=str(data.get("model") or ""),
        results=results,
        latency_ms=float(latency_ms) if isinstance(latency_ms, int | float) else fallback_latency_ms,
        total_documents=int(total_documents) if isinstance(total_documents, int) else len(results),
    )
