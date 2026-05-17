"""Embedding HTTP 客户端

调用远程 Qwen3-Embedding-4B OpenAI-compatible embedding 服务。
默认走内网 10.168.1.113:8901。

用法：
    from src.infrastructure.clients.embedding import embed_texts

    vecs = await embed_texts(["央行降准0.5%", "美联储加息25bp"])
    # vecs: list[list[float]]，每个内层列表长度 = EMBEDDING_DIM (默认 2560)

注意：
- 该服务不走 BaseClient 缓存装饰器（embedding 体积大、缓存收益低）
- 失败时返回空列表，调用方应优雅降级（保留事件，跳过 embedding）
- 当前 Qwen3-Embedding-4B vLLM 服务拒绝 dimensions/MRL 参数，默认不发送 dimensions，直接使用 2560 维原始向量
"""
import logging
import math
import hashlib
import json
import os
import struct
import tempfile
import time
from pathlib import Path
from typing import Optional

import httpx

from src.domain.knowledge.retrieval_profile import profile_event, profile_span
from src.infrastructure.config.settings import (
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_DIM,
    EMBEDDING_FILE_CACHE_DIR,
    EMBEDDING_FILE_CACHE_ENABLED,
    EMBEDDING_MODEL,
    EMBEDDING_REQUEST_DIMENSIONS,
    EMBEDDING_TIMEOUT,
    EMBEDDING_URL,
)

logger = logging.getLogger(__name__)
_REMOTE_DIMENSIONS_SUPPORTED: bool | None = None


async def embed_texts(
    texts: list[str],
    dim: int = EMBEDDING_DIM,
    normalize: bool = True,
) -> list[list[float]]:
    """批量计算文本向量

    Args:
        texts: 文本列表
        dim: 输出维度，默认 2560；当 dim 小于服务端返回维度时会本地截断
        normalize: 是否 L2 归一化，默认 True

    Returns:
        与 texts 同序的向量列表；调用失败时返回 []
    """
    if not texts:
        return []

    cached_vectors: list[list[float] | None] = [None] * len(texts)
    missing_texts: list[str] = []
    missing_indices: list[int] = []
    for index, text in enumerate(texts):
        cached = _cache_get(text, dim=dim, normalize=normalize)
        if cached is not None:
            cached_vectors[index] = cached
        else:
            missing_indices.append(index)
            missing_texts.append(text)

    estimated_batches = (len(missing_texts) + EMBEDDING_BATCH_SIZE - 1) // EMBEDDING_BATCH_SIZE
    logger.info(
        "embedding 批量请求计划: texts=%d cache_hits=%d cache_misses=%d batch_size=%d "
        "estimated_http_requests=%d dim=%d request_dimensions=%s file_cache=%s",
        len(texts),
        len(texts) - len(missing_texts),
        len(missing_texts),
        EMBEDDING_BATCH_SIZE,
        estimated_batches,
        dim,
        EMBEDDING_REQUEST_DIMENSIONS,
        EMBEDDING_FILE_CACHE_ENABLED,
    )
    if not missing_texts:
        profile_event("embedding.embed_texts_result", total=len(texts), vectors=len(cached_vectors), cache_hits=len(texts))
        return cached_vectors  # type: ignore[return-value]

    cache_writes = 0
    cache_write_skips = 0
    failed_vectors = 0
    returned_dims: dict[int, int] = {}
    with profile_span(
        "embedding.embed_texts",
        total=len(texts),
        cache_misses=len(missing_texts),
        batch_size=EMBEDDING_BATCH_SIZE,
        url=_embedding_endpoint(),
        model=EMBEDDING_MODEL,
    ):
        async with httpx.AsyncClient(timeout=EMBEDDING_TIMEOUT) as client:
            for i in range(0, len(missing_texts), EMBEDDING_BATCH_SIZE):
                batch = missing_texts[i:i + EMBEDDING_BATCH_SIZE]
                batch_indices = missing_indices[i:i + EMBEDDING_BATCH_SIZE]
                with profile_span(
                    "embedding.embed_batch",
                    batch_start=i,
                    batch_size=len(batch),
                    total=len(texts),
                ):
                    batch_embeddings = await _embed_batch_with_split_retry(
                        client,
                        batch,
                        dim=dim,
                        normalize=normalize,
                        batch_start=i,
                        total=len(texts),
                    )
                for index, vector in zip(batch_indices, batch_embeddings, strict=False):
                    cached_vectors[index] = vector
                    if not vector:
                        failed_vectors += 1
                        continue
                    vector_dim = len(vector)
                    returned_dims[vector_dim] = returned_dims.get(vector_dim, 0) + 1
                    if _cache_set(texts[index], vector, dim=dim, normalize=normalize):
                        cache_writes += 1
                    else:
                        cache_write_skips += 1
        profile_event(
            "embedding.embed_texts_result",
            total=len(texts),
            vectors=len(cached_vectors),
            cache_hits=len(texts) - len(missing_texts),
            cache_misses=len(missing_texts),
        )
    logger.info(
        "embedding 文件缓存写入结果: cache_writes=%d cache_write_skips=%d failed_vectors=%d returned_dims=%s cache_dir=%s",
        cache_writes,
        cache_write_skips,
        failed_vectors,
        returned_dims,
        EMBEDDING_FILE_CACHE_DIR,
    )
    return cached_vectors  # type: ignore[return-value]


async def _embed_batch_with_split_retry(
    client: httpx.AsyncClient,
    batch: list[str],
    *,
    dim: int,
    normalize: bool,
    batch_start: int,
    total: int,
) -> list[list[float]]:
    try:
        profile_event("embedding.http_request", batch_start=batch_start, batch_size=len(batch), total=total)
        data = await _post_embeddings(client, batch, dim=dim)
        embeddings = _openai_embeddings_from_response(data)
        if not isinstance(embeddings, list) or len(embeddings) != len(batch):
            raise ValueError(
                f"embedding 响应数量不匹配: expected={len(batch)} got={len(embeddings or [])}"
            )
        return [_prepare_embedding_vector(vector, dim=dim, normalize=normalize) for vector in embeddings]
    except Exception as exc:
        if len(batch) <= 1:
            logger.warning(
                "embedding 调用失败 (item %d/%d): %s",
                batch_start,
                total,
                exc,
            )
            return [None] * len(batch)  # type: ignore

        mid = len(batch) // 2
        logger.warning(
            "embedding 调用失败，自动降批重试 (batch %d/%d size=%d -> %d+%d): %s",
            batch_start,
            total,
            len(batch),
            mid,
            len(batch) - mid,
            exc,
        )
        left = await _embed_batch_with_split_retry(
            client,
            batch[:mid],
            dim=dim,
            normalize=normalize,
            batch_start=batch_start,
            total=total,
        )
        right = await _embed_batch_with_split_retry(
            client,
            batch[mid:],
            dim=dim,
            normalize=normalize,
            batch_start=batch_start + mid,
            total=total,
        )
        return [*left, *right]


def encode_embedding(vec: list[float]) -> bytes:
    """把 float 列表序列化为紧凑 bytes（float32），存入 BYTEA 字段

    存储格式：连续的 little-endian float32，长度 = dim * 4 字节
    """
    if not vec:
        return b""
    return struct.pack(f"<{len(vec)}f", *vec)


def decode_embedding(blob: Optional[bytes]) -> list[float]:
    """从 BYTEA 反序列化为 float 列表"""
    if not blob:
        return []
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


async def embedding_health() -> dict:
    """探测 embedding 服务健康状态"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(_embedding_health_endpoint())
            return resp.json()
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _embedding_endpoint() -> str:
    base = EMBEDDING_URL.rstrip("/")
    if base.endswith("/v1/embeddings"):
        return base
    if base.endswith("/v1"):
        return f"{base}/embeddings"
    return f"{base}/v1/embeddings"


def _embedding_health_endpoint() -> str:
    base = EMBEDDING_URL.rstrip("/")
    if base.endswith("/v1/embeddings"):
        return base[: -len("/v1/embeddings")] + "/health"
    if base.endswith("/v1"):
        return base[: -len("/v1")] + "/health"
    return f"{base}/health"


async def _post_embeddings(client: httpx.AsyncClient, batch: list[str], *, dim: int) -> object:
    global _REMOTE_DIMENSIONS_SUPPORTED
    payload: dict[str, object] = {
        "model": EMBEDDING_MODEL,
        "input": batch,
    }
    start_time = time.time()
    should_request_dimensions = EMBEDDING_REQUEST_DIMENSIONS and _REMOTE_DIMENSIONS_SUPPORTED is not False
    if should_request_dimensions:
        payload["dimensions"] = dim
    resp = await client.post(_embedding_endpoint(), json=payload)
    logger.info(
        "embedding http request done: batch_size=%d latency=%.2fs request_dimensions=%s status_code=%d",
        len(batch),
        time.time() - start_time,
        should_request_dimensions,
        resp.status_code,
    )
    if resp.status_code != 400:
        resp.raise_for_status()
        return resp.json()

    body = resp.text
    if not should_request_dimensions or ("matryoshka" not in body and "dimensions" not in body):
        resp.raise_for_status()

    _REMOTE_DIMENSIONS_SUPPORTED = False
    logger.warning(
        "embedding 服务拒绝 dimensions 参数，后续请求将不再发送 dimensions，并改为本地截断: %s",
        _clip_log(body),
    )
    fallback_payload = {
        "model": EMBEDDING_MODEL,
        "input": batch,
    }
    fallback_resp = await client.post(_embedding_endpoint(), json=fallback_payload)
    fallback_resp.raise_for_status()
    return fallback_resp.json()


def _openai_embeddings_from_response(data: object) -> list[list[float]] | None:
    if not isinstance(data, dict):
        return None
    items = data.get("data")
    if not isinstance(items, list):
        return None
    parsed: list[tuple[int, list[float]]] = []
    for fallback_index, item in enumerate(items):
        if not isinstance(item, dict):
            return None
        embedding = item.get("embedding")
        if not isinstance(embedding, list):
            return None
        index = item.get("index", fallback_index)
        if not isinstance(index, int):
            index = fallback_index
        parsed.append((index, embedding))
    return [embedding for _, embedding in sorted(parsed, key=lambda pair: pair[0])]


def _prepare_embedding_vector(vector: list[float], *, dim: int, normalize: bool) -> list[float]:
    prepared = vector[:dim] if dim > 0 and len(vector) > dim else list(vector)
    if not normalize:
        return prepared
    norm = math.sqrt(sum(float(value) * float(value) for value in prepared))
    if norm <= 0:
        return prepared
    return [float(value) / norm for value in prepared]


def _clip_log(text: str, limit: int = 500) -> str:
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


def _cache_get(text: str, *, dim: int, normalize: bool) -> list[float] | None:
    if not EMBEDDING_FILE_CACHE_ENABLED:
        return None
    path = _cache_path(text, dim=dim, normalize=normalize)
    if not path.exists():
        return None
    try:
        vector = decode_embedding(path.read_bytes())
        if len(vector) != dim:
            return None
        return vector
    except Exception as exc:
        logger.warning("embedding 文件缓存读取失败 path=%s error=%s", path, exc)
        return None


def _cache_set(text: str, vector: list[float], *, dim: int, normalize: bool) -> bool:
    if not EMBEDDING_FILE_CACHE_ENABLED or len(vector) != dim:
        return False
    path = _cache_path(text, dim=dim, normalize=normalize)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=str(path.parent),
            delete=False,
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as handle:
            handle.write(encode_embedding(vector))
            tmp_path = Path(handle.name)
        os.replace(tmp_path, path)
        return True
    except Exception as exc:
        logger.warning("embedding 文件缓存写入失败 path=%s error=%s", path, exc)
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
        return False


def _cache_path(text: str, *, dim: int, normalize: bool) -> Path:
    payload = {
        "version": 1,
        "model": EMBEDDING_MODEL,
        "dim": dim,
        "normalize": normalize,
        "text": text,
    }
    key = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return Path(EMBEDDING_FILE_CACHE_DIR) / key[:2] / f"{key}.bin"
