"""Redis 分布式锁 (SET NX EX + Lua 安全 release)

用法:
    from src.infrastructure.db import redis_lock

    with redis_lock.acquire("fund_flow:northbound", ttl=300) as lock:
        if not lock:
            return  # 别人正在跑
        # ... do work

设计:
    - SET key token NX EX ttl  原子获锁
    - token 是 16 字节随机 hex,防止"我加的锁被别人误删"
    - release 用 Lua 脚本: 先比 token 再删,保证 owner 校验
    - worker 崩溃 → 锁靠 TTL 自动过期 → 不会死锁

注意:
    - TTL 必须大于 task 实际执行时间,否则锁过期后第二个 worker 进来会和第一个并发
    - 默认 ttl=300s（5 min）够大部分 aggregator 用
"""

import logging
import secrets
from contextlib import contextmanager
from typing import Optional

import redis

from src.infrastructure.config.settings import JETTASK_PREFIX, REDIS_URL

logger = logging.getLogger(__name__)


# 全局 redis client（懒加载）
_client: Optional[redis.Redis] = None


def _get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(REDIS_URL, decode_responses=True)
    return _client


# Lua: 先比 token 再删, 保证只有 owner 能 release
_UNLOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class _LockHandle:
    """锁句柄,bool() 返回是否成功获锁"""
    __slots__ = ("key", "token", "locked", "ttl")

    def __init__(self, key: str, token: str, locked: bool, ttl: int = 300):
        self.key = key
        self.token = token
        self.locked = locked
        self.ttl = ttl

    def __bool__(self) -> bool:
        return self.locked

    def renew(self) -> bool:
        """续锁：重置 TTL（只有锁持有者才能续）"""
        if not self.locked:
            return False
        try:
            client = _get_client()
            # 只有 token 匹配才续，防止续了别人的锁
            current = client.get(self.key)
            if current == self.token:
                client.expire(self.key, self.ttl)
                return True
            return False
        except Exception as e:
            logger.warning(f"redis_lock renew {self.key} 失败: {e}")
            return False


def _make_key(name: str) -> str:
    """带 jettask prefix 的完整锁 key,避免和其他 redis key 冲突"""
    return f"{JETTASK_PREFIX}:lock:{name}"


@contextmanager
def acquire(name: str, ttl: int = 300):
    """尝试获取分布式锁

    Args:
        name: 锁名(自动加 prefix),如 "fund_flow:northbound"
        ttl:  锁过期时间(秒),应当大于任务最长执行时间

    Yields:
        _LockHandle: bool(handle) 为 True 表示获锁成功

    Example:
        with acquire("fund_flow:northbound", ttl=300) as lock:
            if not lock:
                return  # 别人在跑
            # ... do work
    """
    full_key = _make_key(name)
    token = secrets.token_hex(16)

    try:
        client = _get_client()
        ok = client.set(full_key, token, nx=True, ex=ttl)
    except Exception as e:
        logger.warning(f"redis_lock acquire {full_key} 失败: {e}")
        ok = False

    handle = _LockHandle(full_key, token, bool(ok), ttl)
    try:
        yield handle
    finally:
        if handle.locked:
            try:
                _get_client().eval(_UNLOCK_LUA, 1, full_key, token)
            except Exception as e:
                logger.warning(f"redis_lock release {full_key} 失败: {e}")


def force_release(name: str) -> bool:
    """强制释放锁(运维用,慎用!可能导致并发)"""
    try:
        return bool(_get_client().delete(_make_key(name)))
    except Exception as e:
        logger.warning(f"force_release {name} 失败: {e}")
        return False


def is_locked(name: str) -> bool:
    """检查锁是否被持有(调试/监控用)"""
    try:
        return bool(_get_client().exists(_make_key(name)))
    except Exception:
        return False
