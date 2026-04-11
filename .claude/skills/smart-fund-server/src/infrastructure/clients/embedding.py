"""Embedding HTTP 客户端

调用远程 Qwen3-Embedding-4B 服务（部署在 GPU 机器上）。
默认走 127.0.0.1:8901（Worker 与 embedding-svc 同机部署）。

用法：
    from src.infrastructure.clients.embedding import embed_texts

    vecs = await embed_texts(["央行降准0.5%", "美联储加息25bp"])
    # vecs: list[list[float]]，每个内层列表长度 = EMBEDDING_DIM (默认 1024)

注意：
- 该服务不走 BaseClient 缓存装饰器（embedding 体积大、缓存收益低）
- 失败时返回空列表，调用方应优雅降级（保留事件，跳过 embedding）
"""
import logging
import struct
from typing import Optional

import httpx

from src.infrastructure.config.settings import (
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_DIM,
    EMBEDDING_TIMEOUT,
    EMBEDDING_URL,
)

logger = logging.getLogger(__name__)


async def embed_texts(
    texts: list[str],
    dim: int = EMBEDDING_DIM,
    normalize: bool = True,
) -> list[list[float]]:
    """批量计算文本向量

    Args:
        texts: 文本列表
        dim: 输出维度（MRL 截断），默认 1024
        normalize: 是否 L2 归一化，默认 True

    Returns:
        与 texts 同序的向量列表；调用失败时返回 []
    """
    if not texts:
        return []

    all_embeddings: list[list[float]] = []
    async with httpx.AsyncClient(timeout=EMBEDDING_TIMEOUT) as client:
        for i in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch = texts[i:i + EMBEDDING_BATCH_SIZE]
            try:
                resp = await client.post(
                    f"{EMBEDDING_URL}/embed",
                    json={"texts": batch, "dim": dim, "normalize": normalize},
                )
                resp.raise_for_status()
                data = resp.json()
                all_embeddings.extend(data["embeddings"])
            except Exception as e:
                logger.warning(
                    f"embedding 调用失败 (batch {i}/{len(texts)}): {e}"
                )
                # 局部失败：本批用 None 占位，让调用方知道哪些缺失
                all_embeddings.extend([None] * len(batch))  # type: ignore
    return all_embeddings


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
            resp = await client.get(f"{EMBEDDING_URL}/health")
            return resp.json()
    except Exception as e:
        return {"status": "error", "error": str(e)}
