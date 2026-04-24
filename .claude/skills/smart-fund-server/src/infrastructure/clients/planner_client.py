"""Planner API HTTP 客户端 — 调用 localhost:8899 /chat 端点"""
import logging

import httpx

logger = logging.getLogger(__name__)

PLANNER_URL = "http://localhost:8899"
DEFAULT_TIMEOUT = 180


def chat(
    prompt: str,
    system_prompt: str | None = None,
    model: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    cwd: str = "/home/yuyang/frida-test/.claude/skills/smart-fund-server",
) -> dict:
    """调用 Planner API /chat 端点

    Returns: {result, usage, duration_sec} 或空 dict
    """
    payload = {"prompt": prompt, "timeout": timeout, "cwd": cwd}
    if system_prompt:
        payload["system_prompt"] = system_prompt
    if model:
        payload["model"] = model

    try:
        resp = httpx.post(
            f"{PLANNER_URL}/chat",
            json=payload,
            timeout=timeout + 30,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.TimeoutException:
        logger.warning(f"[planner] chat 超时 ({timeout}s)")
        return {}
    except httpx.HTTPStatusError as e:
        logger.warning(f"[planner] chat HTTP {e.response.status_code}: {e.response.text[:300]}")
        return {}
    except Exception as e:
        logger.warning(f"[planner] chat 失败: {e}")
        return {}
