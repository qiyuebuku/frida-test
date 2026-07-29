"""直接调 GLM API 的 LLM 客户端 — 用于 L1a 事件抽取等批量任务

使用 Anthropic 兼容接口调用智谱 GLM 模型，不依赖 Planner API。
"""
import logging
import os

import httpx

logger = logging.getLogger(__name__)

# 从环境变量读取，fallback 到默认值
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://open.bigmodel.cn/api/anthropic")
LLM_API_KEY = os.getenv("LLM_API_KEY", "97186f86c6d24eb2b333676b17ad77fc.hFRyyLmNSDCRQ1z6")
LLM_MODEL = os.getenv("LLM_MODEL", "GLM-5.1")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "180"))


def chat(
    prompt: str,
    system_prompt: str | None = None,
    model: str | None = None,
    timeout: int = LLM_TIMEOUT,
    temperature: float = 0.3,
) -> dict:
    """调用 GLM API（Anthropic Messages 兼容接口）

    Args:
        prompt: 用户消息
        system_prompt: 系统提示词
        model: 模型名，默认 GLM-5.1
        timeout: 超时秒数
        temperature: 温度，抽取任务用低温度

    Returns: {"result": str, "usage": dict} 或空 dict
    """
    model = model or LLM_MODEL
    messages = [{"role": "user", "content": prompt}]

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 4096,
        "temperature": temperature,
    }
    if system_prompt:
        payload["system"] = system_prompt

    try:
        resp = httpx.post(
            f"{LLM_BASE_URL}/v1/messages",
            headers={
                "x-api-key": LLM_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
            timeout=timeout + 30,
        )
        resp.raise_for_status()
        data = resp.json()

        # 提取文本内容
        content_blocks = data.get("content", [])
        result_text = ""
        for block in content_blocks:
            if block.get("type") == "text":
                result_text += block.get("text", "")

        usage = data.get("usage", {})
        logger.info(
            f"[llm] {model}: input={usage.get('input_tokens', 0)} "
            f"output={usage.get('output_tokens', 0)}"
        )

        return {"result": result_text, "usage": usage}

    except httpx.TimeoutException:
        logger.warning(f"[llm] chat 超时 ({timeout}s)")
        return {}
    except httpx.HTTPStatusError as e:
        logger.warning(f"[llm] HTTP {e.response.status_code}: {e.response.text[:300]}")
        return {}
    except Exception as e:
        logger.warning(f"[llm] chat 失败: {e}")
        return {}
