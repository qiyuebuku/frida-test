#!/usr/bin/env python3
"""使用隔离 jettask 前缀验证 Worker 崩溃恢复与重复投递语义。"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import redis
from jettask import Jettask, TaskMessage
from sqlalchemy import delete


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.infrastructure.config import settings  # noqa: E402
from src.domain.knowledge.card_relation import build_card_relation_edge  # noqa: E402
from src.domain.knowledge.relation_discovery import VerifiedRelationDecision  # noqa: E402
from src.infrastructure.connections import get_session  # noqa: E402
from src.infrastructure.observability.langfuse_tracing import (  # noqa: E402
    langfuse_flush,
    langfuse_observation,
    langfuse_propagation_context,
    langfuse_update_span,
)
from src.infrastructure.persistence.models.knowledge import KnowledgeCardRelation  # noqa: E402
from src.infrastructure.persistence.repositories.card_relation_repository import (  # noqa: E402
    CardRelationRepository,
)


TASK_NAME = "relation_reliability_probe"
QUEUE_NAME = "relation_reliability_probe"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Relation jettask 可靠性隔离验收")
    parser.add_argument("--timeout", type=int, default=30, help="单阶段等待上限，单位秒")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--keep-redis", action="store_true", help="保留隔离前缀 Redis 数据")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--prefix", default="", help=argparse.SUPPRESS)
    parser.add_argument("--run-id", default="", help=argparse.SUPPRESS)
    return parser.parse_args()


def redis_client() -> redis.Redis:
    return redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


def probe_key(run_id: str, suffix: str) -> str:
    return f"relation-reliability:{run_id}:{suffix}"


def run_worker(prefix: str, run_id: str) -> None:
    app = Jettask(redis_url=settings.REDIS_URL, prefix=prefix)

    @app.task(queue=QUEUE_NAME, max_retries=0)
    def relation_reliability_probe(
        logical_id: str,
        first_attempt_sleep: float = 0.0,
    ) -> dict:
        client = redis_client()
        attempts = client.hincrby(probe_key(run_id, "attempts"), logical_id, 1)
        client.rpush(
            probe_key(run_id, "started"),
            json.dumps(
                {"logical_id": logical_id, "attempt": attempts, "pid": os.getpid()},
                ensure_ascii=False,
            ),
        )
        if attempts == 1 and first_attempt_sleep > 0:
            time.sleep(first_attempt_sleep)
        else:
            time.sleep(0.1)
        client.hincrby(probe_key(run_id, "completed"), logical_id, 1)
        return {"logical_id": logical_id, "attempt": attempts}

    try:
        app.start_worker(
            task_names=[TASK_NAME],
            concurrency=1,
            prefetch=20,
            heartbeat_interval=1,
            heartbeat_timeout=3,
            health_check_interval=1,
        )
    finally:
        app.close()


def start_worker_process(prefix: str, run_id: str, log_path: Path) -> subprocess.Popen:
    log_file = log_path.open("ab")
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            "--prefix",
            prefix,
            "--run-id",
            run_id,
        ],
        cwd=str(PROJECT_ROOT),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log_file.close()
    return process


def stop_worker(process: subprocess.Popen, *, force: bool) -> None:
    if process.poll() is not None:
        return
    if force:
        os.killpg(process.pid, signal.SIGKILL)
    else:
        os.killpg(process.pid, signal.SIGINT)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def wait_until(predicate, *, timeout: int, description: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.2)
    raise TimeoutError(f"等待超时: {description}")


def task_status(app: Jettask, event_id: str) -> dict | None:
    return app.status_sync(event_id, QUEUE_NAME, TASK_NAME)


def cleanup_prefix(client: redis.Redis, prefix: str, run_id: str) -> None:
    keys = list(client.scan_iter(match=f"{prefix}:*"))
    keys.extend(client.scan_iter(match=f"relation-reliability:{run_id}:*"))
    unique = list(dict.fromkeys(keys))
    if unique:
        client.delete(*unique)


def validate_edge_idempotency(run_id: str) -> dict:
    """在真实 PG 当前态表中验证重复、更新和失效语义。"""

    source_card_id = f"kg_cognitive_card:reliability:{run_id}:source"
    target_card_id = f"kg_cognitive_card:reliability:{run_id}:target"
    repository = CardRelationRepository(target="prod")

    def edge_for(basis: str):
        decision = VerifiedRelationDecision(
            source_card_id=source_card_id,
            target_card_id=target_card_id,
            decision_class="observed",
            relation_kind="confirmation",
            relation_type="两个来源对同一事实形成独立确认",
            direction="双向确认",
            basis=basis,
            source_evidence_refs=["s0001"],
            target_evidence_refs=["s0001"],
            inference_mechanism="",
            confidence=0.93,
        )
        return build_card_relation_edge(
            decision,
            pipeline_version="relation_reliability_v1",
            model_name="deterministic_validation",
            prompt_version="relation_reliability_v1",
        )

    first_edge = edge_for("双方原文对同一事实给出一致且独立的明确描述。")
    try:
        first = repository.synchronize_batch(
            accepted_edges=[first_edge],
            rejected_pairs=[],
        )
        repository.mark_semantic_synced([first_edge.id])
        repository.mark_graph_events_published([first_edge.id])

        duplicate = repository.synchronize_batch(
            accepted_edges=[first_edge],
            rejected_pairs=[],
        )

        updated_edge = edge_for("双方原文补充了同一事实的执行范围和发生时间。")
        updated = repository.synchronize_batch(
            accepted_edges=[updated_edge],
            rejected_pairs=[],
        )

        invalidated = repository.synchronize_batch(
            accepted_edges=[],
            rejected_pairs=[(source_card_id, target_card_id)],
        )
        passed = all(
            (
                first.changed_edge_ids == [first_edge.id],
                first.active_edges_to_publish[0].id == first_edge.id,
                duplicate.changed_edge_ids == [],
                duplicate.active_edges_to_publish == [],
                updated.changed_edge_ids == [first_edge.id],
                updated.active_edges_to_publish[0].content_version
                == updated_edge.content_version,
                invalidated.changed_edge_ids == [first_edge.id],
                invalidated.inactive_edge_ids_to_delete == [first_edge.id],
            )
        )
        return {
            "passed": passed,
            "edge_id": first_edge.id,
            "insert_changed": first.changed_edge_ids,
            "duplicate_changed": duplicate.changed_edge_ids,
            "update_changed": updated.changed_edge_ids,
            "invalidation_changed": invalidated.changed_edge_ids,
        }
    finally:
        with get_session("prod") as session:
            session.execute(
                delete(KnowledgeCardRelation).where(
                    KnowledgeCardRelation.id == first_edge.id
                )
            )


def run_validation(args: argparse.Namespace) -> dict:
    run_id = uuid4().hex[:12]
    prefix = f"{settings.JETTASK_PREFIX}_relation_reliability_{run_id}"
    log_path = Path(f"/tmp/relation_task_reliability_worker_{run_id}.log")
    client = redis_client()
    app = Jettask(redis_url=settings.REDIS_URL, prefix=prefix)
    workers: list[subprocess.Popen] = []
    try:
        cleanup_prefix(client, prefix, run_id)
        first_worker = start_worker_process(prefix, run_id, log_path)
        workers.append(first_worker)
        time.sleep(1)
        recovery_event_id = app.send_sync(
            [
                TaskMessage(
                    queue=QUEUE_NAME,
                    kwargs={"logical_id": "recovery", "first_attempt_sleep": 20.0},
                    timeout=60,
                    max_retries=0,
                )
            ]
        )[0]
        wait_until(
            lambda: int(client.hget(probe_key(run_id, "attempts"), "recovery") or 0) >= 1,
            timeout=args.timeout,
            description="第一 Worker 开始 recovery 任务",
        )
        stop_worker(first_worker, force=True)

        second_worker = start_worker_process(prefix, run_id, log_path)
        workers.append(second_worker)
        recovery_status = wait_until(
            lambda: _success_status(task_status(app, recovery_event_id)),
            timeout=args.timeout,
            description="新 Worker 恢复并完成 pending 任务",
        )
        recovery_attempts = int(
            client.hget(probe_key(run_id, "attempts"), "recovery") or 0
        )
        recovery_completed = int(
            client.hget(probe_key(run_id, "completed"), "recovery") or 0
        )

        duplicate_event_ids = app.send_sync(
            [
                TaskMessage(
                    queue=QUEUE_NAME,
                    kwargs={"logical_id": "duplicate", "first_attempt_sleep": 0.0},
                    timeout=30,
                    max_retries=0,
                ),
                TaskMessage(
                    queue=QUEUE_NAME,
                    kwargs={"logical_id": "duplicate", "first_attempt_sleep": 0.0},
                    timeout=30,
                    max_retries=0,
                ),
            ]
        )
        duplicate_statuses = [
            wait_until(
                lambda event_id=event_id: _success_status(task_status(app, event_id)),
                timeout=args.timeout,
                description=f"重复消息完成: {event_id}",
            )
            for event_id in duplicate_event_ids
        ]
        duplicate_attempts = int(
            client.hget(probe_key(run_id, "attempts"), "duplicate") or 0
        )
        duplicate_completed = int(
            client.hget(probe_key(run_id, "completed"), "duplicate") or 0
        )
        recovery_passed = recovery_attempts >= 2 and recovery_completed == 1
        duplicate_delivery_passed = (
            len(set(duplicate_event_ids)) == 2
            and duplicate_attempts == 2
            and duplicate_completed == 2
        )
        edge_idempotency = validate_edge_idempotency(run_id)
        return {
            "status": "passed"
            if recovery_passed and duplicate_delivery_passed and edge_idempotency["passed"]
            else "failed",
            "run_id": run_id,
            "isolated_prefix": prefix,
            "worker_log": str(log_path),
            "recovery": {
                "passed": recovery_passed,
                "event_id": recovery_event_id,
                "attempts": recovery_attempts,
                "completed": recovery_completed,
                "final_status": recovery_status,
            },
            "duplicate_delivery": {
                "passed": duplicate_delivery_passed,
                "event_ids": duplicate_event_ids,
                "attempts": duplicate_attempts,
                "completed": duplicate_completed,
                "final_statuses": duplicate_statuses,
                "queue_semantics": "两个重复逻辑消息拥有独立 event_id，框架不会自动业务去重",
            },
            "edge_idempotency": edge_idempotency,
        }
    finally:
        for worker in workers:
            stop_worker(worker, force=False)
        app.close()
        if not args.keep_redis:
            cleanup_prefix(client, prefix, run_id)
        client.close()


def _success_status(status: dict | None) -> dict | None:
    return status if status and str(status.get("status") or "").upper() == "SUCCESS" else None


def main() -> None:
    args = parse_args()
    if args.worker:
        if not args.prefix or not args.run_id:
            raise SystemExit("worker 模式缺少 --prefix/--run-id")
        run_worker(args.prefix, args.run_id)
        return

    session_id = args.session_id or f"relation-reliability-{uuid4().hex[:12]}"
    with langfuse_propagation_context(
        trace_name="kg.relation.task_reliability_validation",
        session_id=session_id,
        tags=["kg", "relation-discovery", "jettask", "reliability"],
    ):
        with langfuse_observation(
            name="kg.relation.task_reliability_validation",
            as_type="chain",
            input={"timeout": args.timeout},
        ):
            result = run_validation(args)
            output = Path(args.output) if args.output else Path(
                f"/tmp/relation_task_reliability_{session_id}.json"
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            langfuse_update_span(
                output={**result, "output_file": str(output)},
                status_message=result["status"],
                level="DEFAULT" if result["status"] == "passed" else "ERROR",
            )
    langfuse_flush()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\nLangfuse trace: kg.relation.task_reliability_validation")
    print(f"Session ID: {session_id}")
    print(f"验收报告: {output}")
    raise SystemExit(0 if result["status"] == "passed" else 2)


if __name__ == "__main__":
    main()
