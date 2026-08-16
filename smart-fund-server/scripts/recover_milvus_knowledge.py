#!/usr/bin/env python3
"""从 PostgreSQL 事实清单渐进恢复 Milvus 中的新闻 Card 与关系文档。"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.application.services.knowledge_news_ingestion_service import (
    KnowledgeNewsIngestionService,
)
from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.knowledge import KnowledgeEvidence

LOGGER = logging.getLogger("recover_milvus_knowledge")
FT_NEWS_SOURCE_ID = re.compile(r"^ft_news:(\d+)$")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("prod", "test"), default="prod")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--pause-seconds", type=float, default=10.0)
    parser.add_argument("--retry-seconds", type=float, default=15.0)
    parser.add_argument("--batch-timeout-seconds", type=float, default=180.0)
    parser.add_argument("--max-batches", type=int, default=0)
    parser.add_argument("--max-record-attempts", type=int, default=3)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument(
        "--allow-llm-reprocessing",
        action="store_true",
        help="明确允许重新执行新闻投影、Card 抽取和关系发现 LLM 链路。",
    )
    parser.add_argument(
        "--confirm-prod-reprocess",
        default="",
        help="prod 环境必须精确传入 REPROCESS_PROD_WITH_LLM。",
    )
    return parser.parse_args()


def _validate_reprocessing_authorization(args: argparse.Namespace) -> None:
    if not bool(getattr(args, "allow_llm_reprocessing", False)):
        raise ValueError(
            "该脚本会重跑 Card/关系 LLM 链路；必须显式传入 "
            "--allow-llm-reprocessing"
        )
    if args.target == "prod" and str(
        getattr(args, "confirm_prod_reprocess", "")
    ) != "REPROCESS_PROD_WITH_LLM":
        raise ValueError(
            "prod 环境还必须传入 "
            "--confirm-prod-reprocess REPROCESS_PROD_WITH_LLM"
        )


def _news_id_from_source_id(source_id: str) -> int | None:
    match = FT_NEWS_SOURCE_ID.fullmatch(str(source_id or "").strip())
    return int(match.group(1)) if match else None


def _load_recovery_news_ids(target: str) -> list[int]:
    with get_session(target) as session:
        source_ids = session.scalars(
            select(KnowledgeEvidence.source_id)
            .where(KnowledgeEvidence.adapter_name == "financial")
            .where(KnowledgeEvidence.source_type == "news_articles")
            .where(KnowledgeEvidence.status == "active")
            .where(KnowledgeEvidence.source_id.like("ft_news:%"))
            .distinct()
        ).all()
    news_ids = {
        news_id
        for source_id in source_ids
        if (news_id := _news_id_from_source_id(source_id)) is not None
    }
    return sorted(news_ids, reverse=True)


def _load_state(path: Path) -> tuple[set[int], dict[int, dict[str, Any]]]:
    if not path.exists():
        return set(), {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    completed = {
        int(item)
        for item in payload.get("completed_news_ids", [])
        if int(item) > 0
    }
    failures = {
        int(news_id): dict(details)
        for news_id, details in (payload.get("failed_news") or {}).items()
        if int(news_id) > 0 and isinstance(details, dict)
    }
    return completed, failures


def _load_completed_ids(path: Path) -> set[int]:
    """Backward-compatible reader used by operational checks and tests."""

    return _load_state(path)[0]


def _save_state(
    path: Path,
    *,
    completed_ids: set[int],
    failed_news: dict[int, dict[str, Any]],
    total: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "completed_news_ids": sorted(completed_ids),
                "completed": len(completed_ids),
                "failed_news": {
                    str(news_id): details
                    for news_id, details in sorted(failed_news.items())
                },
                "quarantined": sum(
                    details.get("status") == "quarantined"
                    for details in failed_news.values()
                ),
                "total": total,
                "updated_at_epoch": int(time.time()),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


async def _run(args: argparse.Namespace) -> None:
    _validate_reprocessing_authorization(args)
    if args.batch_size < 1 or args.batch_size > 30:
        raise ValueError("batch-size 必须在 1..30 之间")
    max_record_attempts = int(getattr(args, "max_record_attempts", 3))
    if max_record_attempts < 1 or max_record_attempts > 20:
        raise ValueError("max-record-attempts 必须在 1..20 之间")
    all_ids = _load_recovery_news_ids(args.target)
    completed, failed_news = _load_state(args.state_file)
    quarantined = {
        news_id
        for news_id, details in failed_news.items()
        if details.get("status") == "quarantined"
    }
    pending = [
        news_id
        for news_id in all_ids
        if news_id not in completed and news_id not in quarantined
    ]
    service = KnowledgeNewsIngestionService(target=args.target)
    LOGGER.info(
        "Milvus 历史恢复开始 total=%s completed=%s pending=%s quarantined=%s "
        "batch_size=%s",
        len(all_ids),
        len(completed),
        len(pending),
        len(quarantined),
        args.batch_size,
    )

    successful_batches = 0
    while pending:
        batch = pending[: args.batch_size]
        try:
            result = await asyncio.wait_for(
                service.ingest_ft_news_ids(
                    batch,
                    workflow_id=f"milvus_recovery:ft_news:{batch[-1]}-{batch[0]}",
                ),
                timeout=args.batch_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            LOGGER.warning(
                "恢复批次超过 %.1fs，将拆分为单条隔离处理 ids=%s",
                args.batch_timeout_seconds,
                batch,
            )
            result = None
            batch_error: Exception | None = exc
        except Exception as exc:  # one bad source must not terminate recovery
            if isinstance(exc, RuntimeError) and "分布式锁已被占用" in str(exc):
                LOGGER.info(
                    "实时 Card 任务正在运行，恢复任务让出锁并于 %.1fs 后重试",
                    args.retry_seconds,
                )
                await asyncio.sleep(args.retry_seconds)
                continue
            LOGGER.exception("恢复批次失败，将拆分为单条隔离处理 ids=%s", batch)
            result = None
            batch_error = exc
        else:
            batch_error = None

        if batch_error is not None:
            pending = pending[len(batch) :]
            successful_batches += await _recover_failed_batch_individually(
                batch=batch,
                pending=pending,
                completed=completed,
                failed_news=failed_news,
                service=service,
                args=args,
                total=len(all_ids),
                max_record_attempts=max_record_attempts,
                batch_error=batch_error,
            )
            if args.max_batches and successful_batches >= args.max_batches:
                break
            continue

        completed.update(batch)
        for news_id in batch:
            failed_news.pop(news_id, None)
        _save_state(
            args.state_file,
            completed_ids=completed,
            failed_news=failed_news,
            total=len(all_ids),
        )
        pending = pending[len(batch) :]
        successful_batches += 1
        LOGGER.info(
            "恢复批次完成 ids=%s consumed=%s cards=%s progress=%s/%s",
            batch,
            result.get("consumed_ids"),
            len(result.get("relation_card_ids") or []),
            len(completed),
            len(all_ids),
        )
        if args.max_batches and successful_batches >= args.max_batches:
            break
        if pending and args.pause_seconds > 0:
            await asyncio.sleep(args.pause_seconds)


async def _recover_failed_batch_individually(
    *,
    batch: list[int],
    pending: list[int],
    completed: set[int],
    failed_news: dict[int, dict[str, Any]],
    service: KnowledgeNewsIngestionService,
    args: argparse.Namespace,
    total: int,
    max_record_attempts: int,
    batch_error: Exception,
) -> int:
    successful = 0
    for news_id in batch:
        try:
            result = await asyncio.wait_for(
                service.ingest_ft_news_ids(
                    [news_id],
                    workflow_id=f"milvus_recovery:ft_news:{news_id}:isolated",
                ),
                timeout=args.batch_timeout_seconds,
            )
        except Exception as exc:  # keep the failure explicit and bounded
            if isinstance(exc, RuntimeError) and "分布式锁已被占用" in str(exc):
                pending.append(news_id)
                LOGGER.info("单条恢复遇到实时锁，移到队尾 news_id=%s", news_id)
                continue
            previous = failed_news.get(news_id) or {}
            attempts = int(previous.get("attempts") or 0) + 1
            status = (
                "quarantined"
                if attempts >= max_record_attempts
                else "retry_pending"
            )
            failed_news[news_id] = {
                "status": status,
                "attempts": attempts,
                "first_failed_at": previous.get("first_failed_at")
                or _utc_now(),
                "last_failed_at": _utc_now(),
                "error_type": type(exc).__name__,
                "last_error": str(exc)[:1000],
                "batch_error_type": type(batch_error).__name__,
            }
            if status == "retry_pending":
                pending.append(news_id)
            LOGGER.error(
                "单条恢复失败 news_id=%s attempts=%s/%s status=%s error=%s",
                news_id,
                attempts,
                max_record_attempts,
                status,
                str(exc)[:500],
            )
        else:
            completed.add(news_id)
            failed_news.pop(news_id, None)
            successful += 1
            LOGGER.info(
                "单条隔离恢复完成 news_id=%s consumed=%s cards=%s progress=%s/%s",
                news_id,
                result.get("consumed_ids"),
                len(result.get("relation_card_ids") or []),
                len(completed),
                total,
            )
        _save_state(
            args.state_file,
            completed_ids=completed,
            failed_news=failed_news,
            total=total,
        )
        if args.retry_seconds > 0:
            await asyncio.sleep(args.retry_seconds)
    return successful


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(_run(_parse_args()))


if __name__ == "__main__":
    main()
