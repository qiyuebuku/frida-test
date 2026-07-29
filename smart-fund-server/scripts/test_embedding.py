"""测试 embedding 服务

用法：
    python scripts/test_embedding.py
"""
import asyncio
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.infrastructure.clients.embedding import (
    decode_embedding, embed_texts, embedding_health, encode_embedding,
)


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


async def main():
    print("=== 1. Health check ===")
    h = await embedding_health()
    print(h)
    if h.get("status") != "ok":
        print("❌ embedding service 未就绪")
        return

    print("\n=== 2. 单条 embedding ===")
    texts = ["央行宣布降准0.5个百分点，释放长期资金1万亿"]
    vecs = await embed_texts(texts)
    print(f"  dim={len(vecs[0]) if vecs and vecs[0] else 0}")
    print(f"  前 5 维: {vecs[0][:5] if vecs and vecs[0] else None}")

    print("\n=== 3. 相似度测试 ===")
    samples = [
        "央行宣布降准0.5个百分点",
        "中国人民银行下调存款准备金率50bp，释放长期资金",  # 极相似
        "宁德时代发布全新麒麟电池，能量密度提升13%",  # 不相关
        "美联储加息25基点，符合市场预期",  # 部分相关（货币政策）
    ]
    vecs = await embed_texts(samples)
    if not vecs or any(v is None for v in vecs):
        print("❌ 部分 embedding 返回失败")
        return
    print(f"  {len(vecs)} 条向量，每条 {len(vecs[0])} 维")
    print()
    print(f"  央行降准 ↔ 央行下调存准率: {cosine(vecs[0], vecs[1]):.4f}  (期望 > 0.85)")
    print(f"  央行降准 ↔ 麒麟电池:       {cosine(vecs[0], vecs[2]):.4f}  (期望 < 0.50)")
    print(f"  央行降准 ↔ 美联储加息:     {cosine(vecs[0], vecs[3]):.4f}  (期望 0.50~0.75)")

    print("\n=== 4. BYTEA 序列化往返 ===")
    blob = encode_embedding(vecs[0])
    print(f"  序列化字节数: {len(blob)} (= dim*4 = {len(vecs[0]) * 4})")
    decoded = decode_embedding(blob)
    print(f"  反序列化后维度: {len(decoded)}")
    diff = sum(abs(a - b) for a, b in zip(vecs[0], decoded))
    print(f"  累计误差(应接近0): {diff:.6f}")


if __name__ == "__main__":
    asyncio.run(main())
