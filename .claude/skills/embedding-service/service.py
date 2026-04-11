"""Qwen3-Embedding-4B HTTP 服务

部署：
    模型：Qwen/Qwen3-Embedding-4B (2560 维，MRL 支持任意维度裁剪)
    GPU：单卡 RTX 4090 (~8GB 显存)
    端口：127.0.0.1:8901（仅监听本机，不对外暴露）

接口：
    POST /embed { texts: [str, ...], dim: int = 1024 }
        → { embeddings: [[float, ...], ...], dim: int, model: str, latency_ms: int }
    GET  /health
        → { status: "ok", model: ..., device: ..., gpu_mem_mb: ... }

说明：
- 输出维度默认 1024（用 MRL 截断），平衡效果与存储
- 也可传 dim=2560 拿全维向量，或 dim=512 等更小维度
- 单批最多 64 条，避免一次塞太多 OOM
"""
import logging
import os
import time
from contextlib import asynccontextmanager

import torch
import torch.nn.functional as F
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from transformers import AutoModel, AutoTokenizer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("embedding-svc")

# 支持 HF 模型 ID 或本地路径（推荐用本地路径，避免运行时下载）
MODEL_NAME = os.environ.get("EMBEDDING_MODEL", "/home/yuyangruan/models/Qwen3-Embedding-4B")
DEVICE = os.environ.get("EMBEDDING_DEVICE", "cuda:0")
MAX_LENGTH = int(os.environ.get("EMBEDDING_MAX_LENGTH", "8192"))
DEFAULT_DIM = int(os.environ.get("EMBEDDING_DEFAULT_DIM", "1024"))
MAX_BATCH = int(os.environ.get("EMBEDDING_MAX_BATCH", "64"))

# 模型 / tokenizer 全局实例（lifespan 中初始化）
_state: dict = {"model": None, "tokenizer": None, "loaded_at": None}


def _last_token_pool(last_hidden_states: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
    """Qwen3-Embedding 推荐的 last-token pooling

    取每条序列最后一个非 padding token 的 hidden state。
    左 padding 时直接取 [-1]，右 padding 时按 attention_mask 找到最后非 0 位置。
    """
    left_padding = attention_mask[:, -1].sum() == attention_mask.shape[0]
    if left_padding:
        return last_hidden_states[:, -1]
    sequence_lengths = attention_mask.sum(dim=1) - 1
    batch_size = last_hidden_states.shape[0]
    return last_hidden_states[torch.arange(batch_size, device=last_hidden_states.device), sequence_lengths]


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"加载模型 {MODEL_NAME} → {DEVICE}")
    t0 = time.time()
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, padding_side="left")
    model = AutoModel.from_pretrained(
        MODEL_NAME,
        torch_dtype=torch.float16,
        device_map=DEVICE,
    )
    model.eval()
    _state["tokenizer"] = tokenizer
    _state["model"] = model
    _state["loaded_at"] = time.time()
    logger.info(f"模型加载完成，耗时 {time.time() - t0:.1f}s")
    yield
    logger.info("关闭服务")


app = FastAPI(title="Qwen3 Embedding Service", lifespan=lifespan)


class EmbedRequest(BaseModel):
    texts: list[str] = Field(..., min_length=1, max_length=MAX_BATCH)
    dim: int = Field(DEFAULT_DIM, ge=32, le=2560, description="输出维度（MRL 截断）")
    normalize: bool = Field(True, description="是否 L2 归一化")


class EmbedResponse(BaseModel):
    embeddings: list[list[float]]
    dim: int
    model: str
    latency_ms: int


@app.get("/health")
def health():
    if _state["model"] is None:
        raise HTTPException(503, "model not loaded")
    gpu_mem = None
    if torch.cuda.is_available():
        gpu_mem = round(torch.cuda.memory_allocated() / 1024 / 1024, 1)
    return {
        "status": "ok",
        "model": MODEL_NAME,
        "device": DEVICE,
        "default_dim": DEFAULT_DIM,
        "gpu_mem_mb": gpu_mem,
        "loaded_at": _state["loaded_at"],
    }


@torch.inference_mode()
def _encode(texts: list[str], dim: int, normalize: bool) -> list[list[float]]:
    tokenizer = _state["tokenizer"]
    model = _state["model"]
    batch = tokenizer(
        texts,
        padding=True,
        truncation=True,
        max_length=MAX_LENGTH,
        return_tensors="pt",
    ).to(model.device)
    outputs = model(**batch)
    embeds = _last_token_pool(outputs.last_hidden_state, batch["attention_mask"])
    # MRL 截断：取前 dim 维
    if dim < embeds.shape[1]:
        embeds = embeds[:, :dim]
    if normalize:
        embeds = F.normalize(embeds, p=2, dim=1)
    return embeds.float().cpu().tolist()


@app.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest):
    if _state["model"] is None:
        raise HTTPException(503, "model not loaded")
    t0 = time.time()
    try:
        embeddings = _encode(req.texts, req.dim, req.normalize)
    except torch.cuda.OutOfMemoryError:
        torch.cuda.empty_cache()
        raise HTTPException(507, "GPU OOM, try smaller batch")
    except Exception as e:
        logger.exception("encode failed")
        raise HTTPException(500, f"encode failed: {e}")
    return EmbedResponse(
        embeddings=embeddings,
        dim=len(embeddings[0]) if embeddings else 0,
        model=MODEL_NAME,
        latency_ms=int((time.time() - t0) * 1000),
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8901, log_level="info")
