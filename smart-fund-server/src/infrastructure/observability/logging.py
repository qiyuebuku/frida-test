"""统一 logger 工厂

替代散落的 logging.getLogger 调用,提供:
- 一致的格式 (timestamp + level + name + message)
- 一次性配置 (configure_logging,启动时调用一次)
- 子 logger 自动继承父配置
- 可选的文件输出

用法:
    from src.infrastructure.observability import get_logger, configure_logging

    configure_logging(level="INFO")  # 启动时
    log = get_logger(__name__)
    log.info("hello")
"""
import logging
import os
import sys
from typing import Optional

DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"


def configure_logging(
    level: str | None = None,
    *,
    fmt: str = DEFAULT_FORMAT,
    datefmt: str = DEFAULT_DATEFMT,
    log_file: str | None = None,
    quiet_third_party: bool = True,
) -> None:
    """统一配置 root logger

    Args:
        level: 日志级别(DEBUG/INFO/WARNING/ERROR), 默认从环境变量 LOG_LEVEL 读, 否则 INFO
        fmt: 格式化字符串
        datefmt: 时间格式
        log_file: 可选文件路径(同时输出到文件)
        quiet_third_party: True 时把 httpx/urllib3 等三方库降到 WARNING

    幂等: 重复调用不会重复添加 handler
    """
    if level is None:
        level = os.environ.get("LOG_LEVEL", "INFO")
    log_level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    # 清空已有 handler 防止重复
    for h in list(root.handlers):
        root.removeHandler(h)

    formatter = logging.Formatter(fmt=fmt, datefmt=datefmt)

    # stderr 控制台 handler
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    root.addHandler(console)

    # 可选文件 handler
    if log_file:
        file_h = logging.FileHandler(log_file, encoding="utf-8")
        file_h.setFormatter(formatter)
        root.addHandler(file_h)

    root.setLevel(log_level)

    if quiet_third_party:
        for name in ("httpx", "httpcore", "urllib3", "asyncio", "sqlalchemy.engine"):
            logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """获取一个命名的 logger

    使用模块名作为 name (`__name__`),自动继承 root 配置。
    """
    return logging.getLogger(name)
