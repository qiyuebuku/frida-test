"""HTTP 客户端基类 + 透明缓存层"""

import asyncio
import functools
import inspect
import json
import logging
import os
import time
from datetime import datetime, timedelta

import httpx
from typing import Optional

logger = logging.getLogger(__name__)

# 进程内并发锁：相同的 (source, method, params_hash) 只有一个请求在执行
# 其他并发请求等待结果，避免缓存击穿
_inflight_locks: dict[str, asyncio.Lock] = {}
_inflight_max = 500  # 防止锁泄漏

# 清除代理环境变量，避免 httpx 走代理导致上游请求失败
for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(key, None)


def cached(
    ttl: int,
    source: str = "",
    domain: str = "",
    frequency: str = "",
    market: str = "",
    source_name: str = "",
):
    """客户端方法缓存装饰器

    用法：
        @cached(ttl=300, source="eastmoney", domain="news", source_name="东方财富")
        async def get_news(self, keyword: str) -> dict:
            ...

    效果：
        1. 调用前检查 ft_raw_data 缓存（参数相同 + 未过期 → 直接返回）
        2. 缓存未命中 → 执行原方法 → 结果存入 ft_raw_data → 返回
        3. 对调用方完全透明

    Args:
        ttl: 缓存有效期（秒）
        source: 数据源标识（如不填则从类名推断）
        domain: 数据领域 news/fund_flow/macro/sentiment/market
        frequency: 数据频率 realtime/minute/daily/weekly/monthly
        market: 市场 a_share/hk/us/futures/forex/fund/macro
        source_name: 数据源中文名
    """
    # 预解析方法签名（只做一次）
    _sig_cache = {}

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(self, *args, **kwargs):
            # 延迟导入，避免循环依赖
            try:
                from services.db import raw_data as rd
                db_available = True
            except Exception:
                rd = None
                db_available = False

            # DB 不可用 → 降级为直接请求外部 API（不缓存）
            if not db_available:
                return await func(self, *args, **kwargs)

            src = source or _infer_source(self.__class__.__name__)
            method_name = func.__name__

            # 构造参数字典
            if func not in _sig_cache:
                _sig_cache[func] = list(inspect.signature(func).parameters.keys())[1:]
            param_names = _sig_cache[func]
            params = {param_names[i]: v for i, v in enumerate(args) if i < len(param_names)}
            params.update(kwargs)

            # 1. 查缓存（DB 查询失败 → 降级为直接请求外部 API）
            try:
                cached_data = rd.get_cached(src, method_name, params, ttl)
                if cached_data is not None:
                    return cached_data
            except Exception as e:
                logger.debug(f"缓存查询失败({src}.{method_name}): {e}, 降级为直接请求")
                return await func(self, *args, **kwargs)

            # 2. 并发锁：相同请求只执行一次，其他等待
            try:
                lock_key = f"{src}:{method_name}:{rd.params_hash(params)}"
            except Exception:
                lock_key = f"{src}:{method_name}"
            if lock_key not in _inflight_locks:
                if len(_inflight_locks) > _inflight_max:
                    _inflight_locks.clear()
                _inflight_locks[lock_key] = asyncio.Lock()

            async with _inflight_locks[lock_key]:
                # 获得锁后再查一次缓存（前一个并发请求可能已填充）
                try:
                    cached_data = rd.get_cached(src, method_name, params, ttl)
                    if cached_data is not None:
                        return cached_data
                except Exception:
                    pass

                # 3. 请求外部 API（失败直接抛异常，不降级到缓存）
                t0 = time.monotonic()
                result = await func(self, *args, **kwargs)
                latency = int((time.monotonic() - t0) * 1000)

                # 4. 存入 ft_raw_data（DB 写入失败不影响返回结果）
                try:
                    rd.save_raw(
                        source=src, method=method_name, params=params,
                        data=result, ttl_seconds=ttl, latency_ms=latency,
                        source_name=source_name, data_domain=domain,
                        data_frequency=frequency, market=market,
                        related_codes=_extract_codes(params),
                        trade_date=_extract_trade_date(result),
                        is_success=True,
                    )
                except Exception as e:
                    logger.debug(f"原始数据写入失败({src}.{method_name}): {e}")

                return result
        return wrapper
    return decorator


def _infer_source(class_name: str) -> str:
    """从类名推断 source 标识"""
    mapping = {
        "THSClient": "ths",
        "EastmoneyClient": "eastmoney",
        "TianTianClient": "tiantianjijin",
        "SinaClient": "sina",
        "TencentClient": "tencent",
        "PBOCClient": "pboc",
        "CLSClient": "cls",
        "GovClient": "gov",
        "XueqiuClient": "xueqiu",
    }
    return mapping.get(class_name, class_name.lower().replace("client", ""))


def _extract_codes(params: dict) -> list:
    """从参数中提取证券代码"""
    codes = []
    for key in ("code", "codes", "symbol", "symbols", "query_codes"):
        val = params.get(key)
        if val:
            if isinstance(val, str):
                codes.append(val)
            elif isinstance(val, list):
                codes.extend(val)
    return codes[:20]  # 最多保留 20 个


def _extract_trade_date(result) -> str | None:
    """从结果中提取交易日期"""
    if not isinstance(result, dict):
        return None
    data = result.get("data", result)
    if isinstance(data, dict):
        for key in ("trade_date", "date", "tradeDate"):
            val = data.get(key)
            if val and isinstance(val, str) and len(val) >= 10:
                return val[:10]
    return None


class BaseClient:
    """HTTP 客户端基类，提供通用的 GET/POST 方法"""

    DEFAULT_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; OnePlus) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        "Referer": "https://fund.10jqka.com.cn/hxapp/ifundDetailTab/dist/index.html",
        "Cache-Control": "max-age=60",
    }

    def __init__(self, timeout: float = 10.0):
        """初始化客户端

        Args:
            timeout: HTTP请求超时时间
        """
        self._client = httpx.AsyncClient(
            headers=self.DEFAULT_HEADERS,
            timeout=timeout,
            follow_redirects=True,
        )

    async def _get(self, url: str, params: Optional[dict] = None) -> dict:
        resp = await self._client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    async def _post(self, url: str, data: Optional[dict] = None, json: Optional[dict] = None) -> dict:
        resp = await self._client.post(url, data=data, json=json)
        resp.raise_for_status()
        return resp.json()

    async def close(self):
        await self._client.aclose()
