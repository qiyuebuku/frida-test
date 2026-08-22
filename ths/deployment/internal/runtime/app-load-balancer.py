#!/usr/bin/env python3
# Installed runtime implementation; not a public deployment entrypoint.
"""Health-aware least-inflight gateway for isolated THS App processes."""

from __future__ import annotations

import argparse
import http.client
import json
import logging
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


LOGGER = logging.getLogger("ths-app-load-balancer")


@dataclass
class Backend:
    name: str
    host: str
    port: int
    active: int = 0
    active_native: int = 0
    active_http: int = 0
    stream_sessions: int = 0
    healthy: bool = False
    last_health_at: float = 0.0
    last_error: str | None = None
    draining: bool = False
    consecutive_health_failures: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "name": self.name,
                "host": self.host,
                "port": self.port,
                "active": self.active,
                "active_native": self.active_native,
                "active_http": self.active_http,
                "stream_sessions": self.stream_sessions,
                "healthy": self.healthy,
                "last_health_at": self.last_health_at,
                "last_error": self.last_error,
                "draining": self.draining,
                "consecutive_health_failures": self.consecutive_health_failures,
            }


class BackendPool:
    HTTP_MAX_INFLIGHT_PER_BACKEND = 1
    # These endpoints inspect/reset process-local debug buffers or depend on the
    # owner WebView. They must not jump between App processes.
    OWNER_AFFINITY_PREFIXES = (
        "/jsbridge",
        "/native/wire-capture",
        "/native/table-capture",
        "/native/indicator-capture",
    )

    def __init__(
        self,
        backends: list[Backend],
        timeout: float,
        *,
        elastic_pool: bool = False,
        passive_recovery: bool = False,
        hook_health_only: bool = False,
    ) -> None:
        self.backends = backends
        self.timeout = timeout
        self.elastic_pool = elastic_pool
        self.passive_recovery = passive_recovery
        self.hook_health_only = hook_health_only
        self.http_max_inflight = 1 if elastic_pool else 8
        self._selection_lock = threading.Lock()
        self._capacity_changed = threading.Condition(self._selection_lock)
        self._cursor = 0
        self._stream_waiters = 0
        self._native_waiters = 0
        self._http_waiters = 0
        self._recovery_lock = threading.Lock()
        self._stop = threading.Event()

    def start_health_monitor(self, interval: float) -> None:
        def monitor() -> None:
            while not self._stop.is_set():
                for backend in self.backends:
                    self._check_health(backend)
                self._stop.wait(interval)

        threading.Thread(target=monitor, name="ths-lb-health", daemon=True).start()

    def _check_health(self, backend: Backend) -> None:
        connection: http.client.HTTPConnection | None = None
        try:
            connection = http.client.HTTPConnection(
                backend.host, backend.port, timeout=3
            )
            connection.request("GET", "/health", headers={"Connection": "close"})
            response = connection.getresponse()
            payload = json.loads(response.read())
            healthy = response.status == 200 and payload.get("ok") is True
            error = None if healthy else f"health status={response.status}"
            # 2026-08-19: hook 存活 ≠ 采集可用（新用户首启卡开户页时 /health 全绿
            # 但行情全超时）。新版 hook 暴露 collector_ready（unified 请求 10 分钟
            # 内真实成功过）；字段缺失（旧 dex）时退回 ok 判定。
            if healthy and not self.hook_health_only and "collector_ready" in payload:
                healthy = payload.get("collector_ready") is True
                if not healthy:
                    error = "collector not ready (unified probe stale)"
        except Exception as exc:  # noqa: BLE001 - health boundary
            healthy = False
            error = f"{type(exc).__name__}: {exc}"
        finally:
            if connection is not None:
                connection.close()
        with backend.lock:
            backend.last_health_at = time.time()
            backend.last_error = error
            if healthy:
                backend.consecutive_health_failures = 0
                backend.healthy = not backend.draining
            else:
                backend.consecutive_health_failures += 1
                # The in-App HTTP listener can be temporarily occupied while a
                # native callback is in flight.  Do not turn productive work
                # into a false failure; after the task is released, repeated
                # probes can still remove a genuinely dead process.
                if (
                    backend.active_native == 0
                    and backend.consecutive_health_failures >= 3
                ):
                    backend.healthy = False

    def choose(self, path: str, excluded: set[str] | None = None) -> Backend:
        """Inspect the preferred backend without reserving capacity."""
        excluded = excluded or set()
        if path.startswith(self.OWNER_AFFINITY_PREFIXES):
            candidates = [self.backends[0]]
        else:
            candidates = [item for item in self.backends if item.name not in excluded]
        healthy = [item for item in candidates if item.snapshot()["healthy"]]
        if not healthy:
            raise RuntimeError("no healthy THS App backend")
        session_free = [
            item for item in healthy if item.snapshot()["stream_sessions"] == 0
        ]
        if session_free:
            healthy = session_free
        with self._selection_lock:
            start = self._cursor
            self._cursor = (self._cursor + 1) % max(1, len(self.backends))
            order = {
                item.name: (index - start) % len(self.backends)
                for index, item in enumerate(self.backends)
            }
            return min(
                healthy,
                key=lambda item: (
                    item.snapshot()["stream_sessions"],
                    item.snapshot()["active"],
                    order[item.name],
                ),
            )

    def reserve(
        self,
        path: str,
        excluded: set[str] | None = None,
        timeout: float | None = None,
        preferred_backend: str | None = None,
    ) -> Backend:
        """Atomically select and occupy one idle App process."""
        excluded = excluded or set()
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        native_request = self._is_native_request(path)
        with self._capacity_changed:
            if native_request:
                self._native_waiters += 1
            else:
                self._http_waiters += 1
            try:
                while True:
                    if preferred_backend is not None:
                        candidates = [
                            item for item in self.backends
                            if item.name == preferred_backend
                            and item.name not in excluded
                        ]
                    elif path.startswith(self.OWNER_AFFINITY_PREFIXES):
                        candidates = [self.backends[0]]
                    else:
                        candidates = [
                            item for item in self.backends
                            if item.name not in excluded
                        ]
                    snapshots = [(item, item.snapshot()) for item in candidates]
                    healthy = [
                        (item, state)
                        for item, state in snapshots
                        if state["healthy"] and not state["draining"]
                    ]
                    available = [
                        (item, state)
                        for item, state in healthy
                        if (
                            state["active_native"] == 0
                            if native_request
                            else state["active_http"] < self.http_max_inflight
                        )
                    ]
                    if available and self._stream_waiters == 0:
                        if self.elastic_pool:
                            backend, _ = min(
                                available,
                                key=lambda pair: self.backends.index(pair[0]),
                            )
                        else:
                            start = self._cursor
                            self._cursor = (
                                self._cursor + 1
                            ) % max(1, len(self.backends))
                            order = {
                                item.name: (index - start) % len(self.backends)
                                for index, item in enumerate(self.backends)
                            }
                            backend, _ = min(
                                available,
                                key=lambda pair: order[pair[0].name],
                            )
                        with backend.lock:
                            backend.active += 1
                            if native_request:
                                backend.active_native += 1
                            else:
                                backend.active_http += 1
                        return backend
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        if not healthy:
                            raise RuntimeError("no healthy THS App backend")
                        raise TimeoutError("timed out waiting for an idle THS App backend")
                    self._capacity_changed.wait(timeout=min(remaining, 0.5))
            finally:
                if native_request:
                    self._native_waiters = max(0, self._native_waiters - 1)
                else:
                    self._http_waiters = max(0, self._http_waiters - 1)

    def release(self, backend: Backend, path: str) -> None:
        with self._capacity_changed:
            with backend.lock:
                backend.active = max(0, backend.active - 1)
                if self._is_native_request(path):
                    backend.active_native = max(0, backend.active_native - 1)
                else:
                    backend.active_http = max(0, backend.active_http - 1)
            self._capacity_changed.notify_all()

    def set_draining(self, backend_name: str, draining: bool) -> dict:
        backend = next(
            (item for item in self.backends if item.name == backend_name), None
        )
        if backend is None:
            raise ValueError(f"unknown THS backend: {backend_name}")
        with self._capacity_changed:
            with backend.lock:
                backend.draining = draining
                if draining:
                    backend.healthy = False
                else:
                    backend.consecutive_health_failures = 0
            self._capacity_changed.notify_all()
        return backend.snapshot()

    @classmethod
    def _is_native_request(cls, path: str) -> bool:
        return path.startswith("/native/") or path == "/jsbridge"

    def forward(
        self,
        method: str,
        path: str,
        body: bytes,
        headers: dict[str, str],
    ) -> tuple[int, bytes, str, str]:
        failed: set[str] = set()
        last_error: Exception | None = None
        queue_timeout = self._queue_timeout(path, body)
        for _ in range(2):
            backend = self.reserve(path, failed, timeout=queue_timeout)
            connection = http.client.HTTPConnection(
                backend.host, backend.port, timeout=self.timeout
            )
            try:
                request_headers = {
                    "Connection": "close",
                    "Content-Type": headers.get("Content-Type", "application/json"),
                }
                if body:
                    request_headers["Content-Length"] = str(len(body))
                connection.request(method, path, body=body or None, headers=request_headers)
                response = connection.getresponse()
                payload = response.read()
                if response.status >= 500 and self._is_native_request(path):
                    raise OSError(
                        f"backend HTTP {response.status}: "
                        f"{payload[:240].decode('utf-8', errors='replace')}"
                    )
                return (
                    response.status,
                    payload,
                    response.getheader("Content-Type", "application/json"),
                    backend.name,
                )
            except (OSError, http.client.HTTPException) as exc:
                last_error = exc
                failed.add(backend.name)
                self._quarantine_and_recover(backend, exc)
                LOGGER.warning("backend=%s request failed: %s", backend.name, exc)
            finally:
                connection.close()
                self.release(backend, path)
        raise RuntimeError(f"THS App backends failed: {last_error}")

    def _quarantine_and_recover(self, backend: Backend, exc: Exception) -> None:
        with backend.lock:
            backend.healthy = False
            backend.draining = True
            backend.last_error = f"{type(exc).__name__}: {exc}"

        def recover() -> None:
            if self.passive_recovery:
                # Let an in-process unified dispatch fully unwind before the
                # backend becomes eligible again.  /health is served by a
                # separate Hook thread and can stay green while that dispatch
                # is still timing out.
                time.sleep(5)
                for _ in range(45):
                    connection = http.client.HTTPConnection(
                        backend.host, backend.port, timeout=3
                    )
                    try:
                        connection.request("GET", "/health", headers={"Connection": "close"})
                        response = connection.getresponse()
                        payload = json.loads(response.read())
                        if response.status == 200 and payload.get("ok") is True:
                            with backend.lock:
                                backend.draining = False
                                backend.healthy = True
                                backend.consecutive_health_failures = 0
                                backend.last_error = None
                            with self._capacity_changed:
                                self._capacity_changed.notify_all()
                            LOGGER.info("backend=%s passive recovery completed", backend.name)
                            return
                    except Exception as recovery_exc:  # noqa: BLE001
                        with backend.lock:
                            backend.last_error = (
                                f"passive recovery {type(recovery_exc).__name__}: {recovery_exc}"
                            )
                    finally:
                        connection.close()
                    time.sleep(2)
                return
            # Android user switching and App restarts are device-global operations.
            # Only the gateway may run one recovery at a time.
            with self._recovery_lock:
                connection = http.client.HTTPConnection(
                    backend.host, backend.port, timeout=75
                )
                try:
                    connection.request(
                        "POST", "/admin/recover", body=b"{}",
                        headers={"Content-Type": "application/json", "Connection": "close"},
                    )
                    response = connection.getresponse()
                    response.read()
                    if response.status != 200:
                        raise OSError(f"recovery HTTP {response.status}")
                    with backend.lock:
                        backend.draining = False
                        backend.healthy = True
                        backend.last_error = None
                    LOGGER.info("backend=%s recovery completed", backend.name)
                except Exception as recovery_exc:  # noqa: BLE001 - recovery boundary
                    with backend.lock:
                        backend.last_error = (
                            f"recovery {type(recovery_exc).__name__}: {recovery_exc}"
                        )
                    LOGGER.exception("backend=%s recovery failed", backend.name)
                finally:
                    connection.close()
                    with self._capacity_changed:
                        self._capacity_changed.notify_all()

        threading.Thread(
            target=recover, daemon=True, name=f"ths-recover-{backend.name}"
        ).start()

    @staticmethod
    def _queue_timeout(path: str, body: bytes) -> float:
        # Production has more worker concurrency than the eight single-flight
        # Android processes. Apply bounded backpressure instead of returning a
        # premature 503 after 10-15 seconds. The upper bound remains below the
        # callers' 90-second transport timeout.
        if path.startswith("/native/") and body:
            try:
                payload = json.loads(body)
                request_timeout = float(
                    payload.get("timeoutSeconds")
                    or payload.get("timeout_seconds")
                    or 8
                )
                return max(30.0, min(60.0, request_timeout + 30.0))
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        return 45.0

    def health_payload(self) -> bytes:
        snapshots = [item.snapshot() for item in self.backends]
        return json.dumps(
            {
                "ok": any(item["healthy"] for item in snapshots),
                "mode": "least_inflight",
                "healthy_backends": sum(item["healthy"] for item in snapshots),
                "native_waiters": self._native_waiters,
                "http_waiters": self._http_waiters,
                "backends": snapshots,
            },
            ensure_ascii=False,
        ).encode()

    def reserve_stream_backend(self, timeout: float = 30.0) -> Backend:
        with self._capacity_changed:
            self._stream_waiters += 1
            deadline = time.monotonic() + timeout
            try:
                while True:
                    healthy = [
                        (item, item.snapshot())
                        for item in self.backends
                        if item.snapshot()["healthy"]
                    ]
                    if not healthy:
                        raise RuntimeError("no healthy THS App stream backend")
                    available = [
                        (item, state)
                        for item, state in healthy
                        if state["active_native"] == 0
                        and state["stream_sessions"] == 0
                    ]
                    if available:
                        backend, _ = min(
                            available,
                            key=lambda pair: self.backends.index(pair[0]),
                        )
                        with backend.lock:
                            backend.stream_sessions += 1
                        return backend
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(
                            "timed out waiting for an idle THS App stream backend"
                        )
                    self._capacity_changed.wait(timeout=min(remaining, 0.5))
            finally:
                self._stream_waiters = max(0, self._stream_waiters - 1)
                self._capacity_changed.notify_all()

    def release_stream_backend(self, backend: Backend) -> None:
        with self._capacity_changed:
            with backend.lock:
                backend.stream_sessions = max(0, backend.stream_sessions - 1)
            self._capacity_changed.notify_all()


class TaskAwareStreamGateway:
    """Multiplex one client session over persistent per-App THSSTREAM channels."""

    def __init__(
        self,
        pool: BackendPool,
        listen_port: int,
        stream_ports: dict[str, int],
        elastic_pool: bool = False,
    ) -> None:
        self.pool = pool
        self.listen_port = listen_port
        self.stream_ports = stream_ports
        self.elastic_pool = elastic_pool
        self._session_lock = threading.Lock()

    def serve(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", self.listen_port))
        server.listen(32)
        LOGGER.info(
            "THS task-aware stream gateway listening on 127.0.0.1:%s",
            self.listen_port,
        )
        while True:
            downstream, _ = server.accept()
            threading.Thread(
                target=self._handle,
                args=(downstream,),
                daemon=True,
                name="ths-sticky-stream",
            ).start()

    def _handle(self, downstream: socket.socket) -> None:
        if not self._session_lock.acquire(blocking=False):
            LOGGER.warning("rejecting additional THS stream client session")
            downstream.close()
            return
        upstreams: dict[str, socket.socket] = {}
        upstream_files: dict[str, object] = {}
        pending: dict[str, tuple[Backend, threading.Timer]] = {}
        subscriptions: dict[str, Backend] = {}
        pending_lock = threading.Lock()
        downstream_lock = threading.Lock()
        upstream_lock = threading.Lock()
        detaching: set[str] = set()
        closed = threading.Event()

        def send_downstream(message: dict) -> None:
            payload = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
            with downstream_lock:
                downstream.sendall(payload.encode("utf-8") + b"\n")

        def release_pending(request_id: str) -> None:
            with pending_lock:
                value = pending.pop(request_id, None)
            if value is None:
                return
            backend, timeout_timer = value
            timeout_timer.cancel()
            self.pool.release(backend, "/native/stream")
            LOGGER.info(
                "stream task released request_id=%s backend=%s",
                request_id,
                backend.name,
            )
            if self.elastic_pool and backend is not self.pool.backends[0]:
                timer = threading.Timer(60, detach_if_idle, args=(backend,))
                timer.daemon = True
                timer.start()

        def read_upstream(backend: Backend, stream_file: object) -> None:
            try:
                while not closed.is_set():
                    raw = stream_file.readline()
                    if not raw:
                        raise ConnectionError("THS App stream closed")
                    message = json.loads(raw)
                    request_id = str(message.get("request_id") or "")
                    if request_id and message.get("type") in {"response", "error"}:
                        release_pending(request_id)
                    send_downstream(message)
            except Exception as exc:  # noqa: BLE001 - stream boundary
                if not closed.is_set() and backend.name not in detaching:
                    LOGGER.warning(
                        "stream backend=%s disconnected error=%s", backend.name, exc
                    )
                    closed.set()
            finally:
                if backend.name in detaching:
                    with upstream_lock:
                        upstreams.pop(backend.name, None)
                        upstream_files.pop(backend.name, None)
                        detaching.discard(backend.name)
                    with self.pool._capacity_changed:
                        with backend.lock:
                            backend.stream_sessions = max(
                                0, backend.stream_sessions - 1
                            )
                        self.pool._capacity_changed.notify_all()

        def connect_backend(backend: Backend) -> dict:
            with upstream_lock:
                existing = upstreams.get(backend.name)
                if existing is not None:
                    return {"type": "hello"}
                upstream = socket.create_connection(
                    (backend.host, self.stream_ports[backend.name]), timeout=10
                )
                upstream.settimeout(None)
                stream_file = upstream.makefile("rb")
                upstream.sendall(b"THSSTREAM/1\n")
                hello = json.loads(stream_file.readline())
                if hello.get("type") != "hello":
                    upstream.close()
                    raise RuntimeError(f"invalid stream hello from {backend.name}")
                upstreams[backend.name] = upstream
                upstream_files[backend.name] = stream_file
                with backend.lock:
                    backend.stream_sessions += 1
            threading.Thread(
                target=read_upstream,
                args=(backend, stream_file),
                daemon=True,
                name=f"ths-stream-read-{backend.name}",
            ).start()
            LOGGER.info("stream backend=%s connected lazily", backend.name)
            return hello

        def detach_if_idle(backend: Backend) -> None:
            if backend is self.pool.backends[0] or closed.is_set():
                return
            with pending_lock:
                has_pending = any(item[0] is backend for item in pending.values())
            has_subscription = any(item is backend for item in subscriptions.values())
            if has_pending or has_subscription or backend.snapshot()["active"] > 0:
                return
            with upstream_lock:
                connection = upstreams.get(backend.name)
                if connection is None or backend.name in detaching:
                    return
                detaching.add(backend.name)
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()
            LOGGER.info("stream backend=%s detached after idle task", backend.name)

        def choose_subscription_backend() -> Backend:
            if not self.elastic_pool:
                counts = {backend.name: 0 for backend in self.pool.backends}
                for backend in subscriptions.values():
                    counts[backend.name] += 1
                candidates = [
                    backend
                    for backend in self.pool.backends
                    if backend.name in upstreams and backend.snapshot()["healthy"]
                ]
                if not candidates:
                    raise RuntimeError("no connected THS App stream backend")
                return min(candidates, key=lambda item: counts[item.name])
            owner = self.pool.backends[0]
            if owner.name not in upstreams or not owner.snapshot()["healthy"]:
                raise RuntimeError("no connected THS App stream backend")
            return owner

        try:
            downstream.settimeout(None)
            downstream_file = downstream.makefile("rb")
            protocol = downstream_file.readline().decode("utf-8").strip()
            if protocol != "THSSTREAM/1":
                raise ValueError(f"unsupported stream protocol: {protocol}")

            owner = self.pool.backends[0]
            if self.elastic_pool:
                hello = connect_backend(owner)
            else:
                hellos = [connect_backend(backend) for backend in self.pool.backends]
                hello = hellos[0]
            send_downstream(
                {
                    "type": "hello",
                    "protocol": "THSSTREAM/1",
                    "gateway_session_id": uuid.uuid4().hex,
                    "backend_count": len(upstreams),
                    "android_user_id": hello.get("android_user_id"),
                    "pid": hello.get("pid"),
                }
            )

            while not closed.is_set():
                raw = downstream_file.readline()
                if not raw:
                    break
                command = json.loads(raw)
                operation = str(command.get("op") or "")
                if operation == "ping":
                    send_downstream({"type": "pong", "gateway": True})
                    continue
                subscription_id = str(command.get("subscription_id") or "")
                if operation == "subscribe":
                    backend = subscriptions.get(subscription_id)
                    if backend is None:
                        backend = choose_subscription_backend()
                        subscriptions[subscription_id] = backend
                    upstreams[backend.name].sendall(raw)
                    continue
                if operation == "unsubscribe":
                    backend = subscriptions.pop(subscription_id, None)
                    if backend is not None:
                        upstreams[backend.name].sendall(raw)
                    else:
                        send_downstream(
                            {"type": "unsubscribed", "subscription_id": subscription_id}
                        )
                    continue
                if operation == "list":
                    send_downstream(
                        {
                            "type": "subscriptions",
                            "subscription_ids": sorted(subscriptions),
                        }
                    )
                    continue
                if operation != "request":
                    send_downstream(
                        {
                            "type": "error",
                            "request_id": command.get("request_id", ""),
                            "error": f"unsupported gateway operation: {operation}",
                        }
                    )
                    continue
                request_id = str(command.get("request_id") or "")
                route = str(command.get("route") or "")
                payload = command.get("payload") or {}
                try:
                    backend = self.pool.reserve(
                        "/native/stream",
                        timeout=120,
                    )
                    connect_backend(backend)
                    timeout_seconds = max(
                        1.0,
                        min(
                            180.0,
                            float(
                                payload.get("timeoutSeconds")
                                or payload.get("timeout_seconds")
                                or 75
                            )
                            + 5.0,
                        ),
                    )

                    def expire_request(
                        expired_request_id: str = request_id,
                        expired_route: str = route,
                        expired_timeout: float = timeout_seconds,
                    ) -> None:
                        with pending_lock:
                            exists = expired_request_id in pending
                        if not exists:
                            return
                        try:
                            send_downstream(
                                {
                                    "type": "error",
                                    "request_id": expired_request_id,
                                    "route": expired_route,
                                    "error": (
                                        "gateway task timed out after "
                                        f"{expired_timeout:.1f}s"
                                    ),
                                }
                            )
                        except OSError:
                            pass
                        finally:
                            release_pending(expired_request_id)

                    timeout_timer = threading.Timer(timeout_seconds, expire_request)
                    timeout_timer.daemon = True
                    with pending_lock:
                        pending[request_id] = (backend, timeout_timer)
                    timeout_timer.start()
                    upstreams[backend.name].sendall(raw)
                    LOGGER.info(
                        "stream task dispatched request_id=%s route=%s backend=%s",
                        request_id,
                        route,
                        backend.name,
                    )
                except Exception:
                    with pending_lock:
                        request_was_pending = request_id in pending
                    if request_was_pending:
                        release_pending(request_id)
                    raise
        except Exception as exc:  # noqa: BLE001 - TCP gateway boundary
            LOGGER.warning("task-aware stream failed error=%s", exc)
        finally:
            closed.set()
            with pending_lock:
                pending_ids = list(pending)
            for request_id in pending_ids:
                release_pending(request_id)
            for backend in self.pool.backends:
                connection = upstreams.get(backend.name)
                if connection is not None:
                    try:
                        connection.close()
                    except OSError:
                        pass
                    if backend.name not in detaching:
                        with self.pool._capacity_changed:
                            with backend.lock:
                                backend.stream_sessions = max(
                                    0, backend.stream_sessions - 1
                                )
                            self.pool._capacity_changed.notify_all()
            try:
                downstream.close()
            except OSError:
                pass
            self._session_lock.release()


class LoadBalancerHandler(BaseHTTPRequestHandler):
    pool: BackendPool

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/health", "/lb/status"}:
            self._send(200, self.pool.health_payload(), "application/json")
            return
        self._proxy("GET")

    def do_POST(self) -> None:  # noqa: N802
        if self.path.startswith("/admin/backend/"):
            self._set_backend_drain()
            return
        self._proxy("POST")

    def _set_backend_drain(self) -> None:
        parts = self.path.split("?")[0].strip("/").split("/")
        if len(parts) != 3:
            self.send_error(404)
            return
        backend_name = parts[2]
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) if length else b"{}")
            state = self.pool.set_draining(
                backend_name,
                bool(payload.get("draining", True)),
            )
            self._send(200, json.dumps(state).encode(), "application/json")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            self._send(
                400,
                json.dumps({"success": False, "error": str(exc)}).encode(),
                "application/json",
            )

    def _proxy(self, method: str) -> None:
        started = time.monotonic()
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 2 * 1024 * 1024:
                self.send_error(413, "request body too large")
                return
            body = self.rfile.read(length) if length else b""
            status, payload, content_type, backend = self.pool.forward(
                method,
                self.path,
                body,
                dict(self.headers.items()),
            )
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("X-THS-Backend", backend)
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
            LOGGER.info(
                "dispatch method=%s path=%s backend=%s status=%s duration_ms=%s",
                method,
                self.path,
                backend,
                status,
                round((time.monotonic() - started) * 1000),
            )
        except (BrokenPipeError, ConnectionResetError):
            # The upstream response has already been completed. A caller that
            # closes its socket must not be recorded as a gateway-generated
            # 503, nor can a second HTTP response be sent on this connection.
            LOGGER.warning(
                "client disconnected before response body completed method=%s path=%s",
                method,
                self.path,
            )
        except Exception as exc:  # noqa: BLE001 - gateway boundary
            LOGGER.exception("request failed method=%s path=%s", method, self.path)
            payload = json.dumps(
                {"success": False, "error": f"THS load balancer failed: {exc}"},
                ensure_ascii=False,
            ).encode()
            try:
                self._send(503, payload, "application/json")
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _send(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        LOGGER.info("%s - %s", self.client_address[0], format % args)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-port", type=int, default=49350)
    parser.add_argument("--backend", action="append", required=True)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--health-interval", type=float, default=2)
    parser.add_argument("--stream-listen-port", type=int, default=49352)
    parser.add_argument("--stream-backend", action="append", required=True)
    parser.add_argument("--elastic-pool", action="store_true")
    parser.add_argument("--passive-recovery", action="store_true")
    parser.add_argument("--hook-health-only", action="store_true")
    args = parser.parse_args()
    backends = []
    for item in args.backend:
        name, address = item.split("=", 1)
        host, port = address.rsplit(":", 1)
        backends.append(Backend(name=name, host=host, port=int(port)))
    pool = BackendPool(
        backends,
        args.timeout,
        elastic_pool=args.elastic_pool,
        passive_recovery=args.passive_recovery,
        hook_health_only=args.hook_health_only,
    )
    for backend in backends:
        pool._check_health(backend)
    pool.start_health_monitor(args.health_interval)
    stream_ports = {}
    for item in args.stream_backend:
        name, port = item.split("=", 1)
        stream_ports[name] = int(port)
    if set(stream_ports) != {item.name for item in backends}:
        raise ValueError("stream backends must match HTTP backends")
    stream_gateway = TaskAwareStreamGateway(
        pool,
        args.stream_listen_port,
        stream_ports,
        elastic_pool=args.elastic_pool,
    )
    threading.Thread(
        target=stream_gateway.serve,
        daemon=True,
        name="ths-stream-gateway",
    ).start()
    LoadBalancerHandler.pool = pool
    server = ThreadingHTTPServer(("127.0.0.1", args.listen_port), LoadBalancerHandler)
    LOGGER.info("THS App load balancer listening on 127.0.0.1:%s", args.listen_port)
    server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
