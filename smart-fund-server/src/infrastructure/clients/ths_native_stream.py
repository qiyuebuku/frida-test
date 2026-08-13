"""Persistent client for decoded THS native realtime subscriptions."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any


logger = logging.getLogger(__name__)
THS_NATIVE_STREAM_HEALTH_KEY = "smart_fund:ths_native_stream:health"
THS_NATIVE_EVENT_STREAM_HEALTH_KEY = "smart_fund:ths_native_stream:event_health"


@dataclass(frozen=True, slots=True)
class THSRealtimeSubscription:
    subscription_id: str
    key: str
    request_param: str
    request_channel: str

    def command(self) -> dict[str, str]:
        return {
            "op": "subscribe",
            "kind": "realtime",
            "subscription_id": self.subscription_id,
            "key": self.key,
            "request_param": self.request_param,
            "request_channel": self.request_channel,
        }


@dataclass(frozen=True, slots=True)
class THSUnifiedSubscription:
    subscription_id: str
    online_id: str
    protocol_id: int
    page_id: int
    request_dic: str
    cancel_request_dic: str = ""
    request_type: int = 262144

    def command(self) -> dict[str, str | int]:
        return {
            "op": "subscribe",
            "kind": "unified",
            "subscription_id": self.subscription_id,
            "online_id": self.online_id,
            "protocol_id": self.protocol_id,
            "page_id": self.page_id,
            "request_type": self.request_type,
            "request_dic": self.request_dic,
            "cancel_request_dic": self.cancel_request_dic,
        }


NativeSubscription = THSRealtimeSubscription | THSUnifiedSubscription


EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class THSNativeCommandClient:
    """Multiplex native commands over one persistent local broker session."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        connect_timeout: float = 10.0,
    ) -> None:
        self._host = host
        self._port = int(port)
        self._connect_timeout = max(1.0, float(connect_timeout))
        self._connect_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending_requests: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._closed = False

    @property
    def is_connected(self) -> bool:
        writer = self._writer
        return writer is not None and not writer.is_closing()

    async def request(
        self,
        *,
        route: str,
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("THS native command client is closed")
        normalized_route = route.strip()
        if not normalized_route:
            raise ValueError("THS native command route is required")
        normalized_timeout = max(1.0, float(timeout))
        writer = await self._ensure_connected()
        request_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending_requests[request_id] = future
        try:
            await self._write_message(
                writer,
                {
                    "request_id": request_id,
                    "route": normalized_route,
                    "payload": payload,
                    "timeout_seconds": normalized_timeout,
                },
            )
            response = await asyncio.wait_for(future, timeout=normalized_timeout + 5)
            if not response.get("success"):
                raise RuntimeError(
                    str(response.get("error") or "THS native command failed")
                )
            result = response.get("response")
            if not isinstance(result, dict):
                raise RuntimeError("THS native command payload must be an object")
            return result
        finally:
            self._pending_requests.pop(request_id, None)

    async def close(self) -> None:
        self._closed = True
        async with self._connect_lock:
            await self._disconnect_locked(
                ConnectionError("THS native command client closed")
            )

    async def _ensure_connected(self) -> asyncio.StreamWriter:
        writer = self._writer
        if writer is not None and not writer.is_closing():
            return writer
        async with self._connect_lock:
            writer = self._writer
            if writer is not None and not writer.is_closing():
                return writer
            if self._closed:
                raise RuntimeError("THS native command client is closed")
            await self._disconnect_locked(
                ConnectionError("THS native command broker reconnecting")
            )
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    self._host,
                    self._port,
                    limit=8 * 1024 * 1024,
                ),
                timeout=self._connect_timeout,
            )
            self._reader = reader
            self._writer = writer
            self._reader_task = asyncio.create_task(
                self._reader_loop(reader, writer),
                name="ths-native-command-reader",
            )
            return writer

    async def _reader_loop(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        error: Exception = ConnectionError("THS native command broker disconnected")
        try:
            while not self._closed:
                raw = await reader.readline()
                if not raw:
                    break
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        "THS native command broker returned invalid JSON"
                    ) from exc
                if not isinstance(message, dict):
                    raise RuntimeError(
                        "THS native command broker response must be an object"
                    )
                request_id = str(message.get("request_id") or "")
                pending = self._pending_requests.get(request_id)
                if pending is not None and not pending.done():
                    pending.set_result(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = exc
            logger.warning("THS native command broker reader stopped: %s", exc)
        finally:
            if self._writer is writer:
                self._reader = None
                self._writer = None
                self._reader_task = None
                self._fail_pending_requests(error)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _write_message(
        self,
        writer: asyncio.StreamWriter,
        message: dict[str, Any],
    ) -> None:
        encoded = json.dumps(
            message,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            async with self._write_lock:
                if writer.is_closing() or writer is not self._writer:
                    raise ConnectionError("THS native command broker disconnected")
                writer.write(encoded + b"\n")
                await writer.drain()
        except Exception:
            if self._writer is writer:
                writer.close()
            raise

    async def _disconnect_locked(self, error: Exception) -> None:
        writer = self._writer
        reader_task = self._reader_task
        self._reader = None
        self._writer = None
        self._reader_task = None
        self._fail_pending_requests(error)
        if writer is not None:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        if reader_task is not None and reader_task is not asyncio.current_task():
            reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader_task

    def _fail_pending_requests(self, error: Exception) -> None:
        for future in tuple(self._pending_requests.values()):
            if not future.done():
                future.set_exception(error)


class THSNativeRealtimeStreamClient:
    """Maintain one App session and restore all logical subscriptions."""

    PROTOCOL = "THSSTREAM/1"

    def __init__(
        self,
        *,
        host: str,
        port: int,
        subscriptions: Iterable[NativeSubscription],
        connect_timeout: float = 15.0,
        heartbeat_interval: float = 15.0,
        read_timeout: float = 45.0,
        subscription_interval: float = 0.25,
        initial_response_timeout: float = 4.0,
        dynamic_activation_interval: float | None = None,
        subscription_activation_lock: asyncio.Lock | None = None,
        reconnect_min_delay: float = 1.0,
        reconnect_max_delay: float = 30.0,
    ) -> None:
        values = tuple(subscriptions)
        ids = [item.subscription_id for item in values]
        if len(ids) != len(set(ids)):
            raise ValueError("THS stream subscription_id values must be unique")
        if not values:
            raise ValueError("at least one THS stream subscription is required")
        self._host = host
        self._port = int(port)
        self._initial_subscriptions = values
        self._subscriptions = list(values)
        self._subscription_by_id: dict[str, NativeSubscription] = {
            item.subscription_id: item for item in values
        }
        self._dynamic_subscription_ids: list[str] = []
        self._subscription_registry_lock = asyncio.Lock()
        self._connect_timeout = float(connect_timeout)
        self._heartbeat_interval = float(heartbeat_interval)
        self._read_timeout = float(read_timeout)
        self._subscription_interval = max(0.0, float(subscription_interval))
        self._initial_response_timeout = max(
            0.05,
            float(initial_response_timeout),
        )
        self._dynamic_activation_interval = (
            max(0.25, float(dynamic_activation_interval))
            if dynamic_activation_interval is not None
            else max(10.0, self._initial_response_timeout)
        )
        self._subscription_activation_lock = (
            subscription_activation_lock or asyncio.Lock()
        )
        self._reconnect_min_delay = float(reconnect_min_delay)
        self._reconnect_max_delay = float(reconnect_max_delay)
        self._stop_event = asyncio.Event()
        self._writer: asyncio.StreamWriter | None = None
        self._connected = False
        self._transport_ready_event = asyncio.Event()
        self._connected_event = asyncio.Event()
        self._write_lock = asyncio.Lock()
        self._pending_requests: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._acknowledged_subscription_ids: set[str] = set()
        self._active_subscription_ids: set[str] = set()

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def acknowledged_subscription_ids(self) -> frozenset[str]:
        return frozenset(self._acknowledged_subscription_ids)

    @property
    def active_subscription_ids(self) -> frozenset[str]:
        return frozenset(self._active_subscription_ids)

    @property
    def dynamic_subscriptions_ready(self) -> bool:
        return set(self._dynamic_subscription_ids).issubset(
            self._active_subscription_ids
        )

    async def wait_until_connected(self, *, timeout: float | None = None) -> None:
        """Wait until the initial App subscriptions are command-ready."""
        waiter = self._connected_event.wait()
        if timeout is None:
            await waiter
            return
        await asyncio.wait_for(waiter, timeout=max(0.1, float(timeout)))

    async def wait_until_transport_ready(
        self,
        *,
        timeout: float | None = None,
    ) -> None:
        """Wait for the bridge handshake, before the full base batch is ready."""
        waiter = self._transport_ready_event.wait()
        if timeout is None:
            await waiter
            return
        await asyncio.wait_for(waiter, timeout=max(0.1, float(timeout)))

    async def add_subscriptions(
        self,
        subscriptions: Iterable[NativeSubscription],
    ) -> int:
        """Register subscriptions activated in the background on this session."""
        added = 0
        async with self._subscription_registry_lock:
            for subscription in subscriptions:
                existing = self._subscription_by_id.get(
                    subscription.subscription_id
                )
                if existing is not None:
                    if existing != subscription:
                        raise ValueError(
                            "subscription_id already has a different contract: "
                            f"{subscription.subscription_id}"
                        )
                    continue
                self._subscription_by_id[subscription.subscription_id] = subscription
                self._subscriptions.append(subscription)
                self._dynamic_subscription_ids.append(subscription.subscription_id)
                added += 1
        return added

    async def refresh_subscription(self, subscription_id: str) -> bool:
        """Re-issue one subscription without reconnecting the shared session."""
        await self.wait_until_transport_ready(timeout=5)
        async with self._subscription_registry_lock:
            subscription = self._subscription_by_id.get(subscription_id)
        writer = self._writer
        if subscription is None or writer is None or writer.is_closing():
            return False
        # A one-shot request owns the App Unified frame until its correlated
        # response arrives. Re-subscription is maintenance traffic and must not
        # interfere with that request.
        while self._pending_requests and not self._stop_event.is_set():
            await asyncio.sleep(0.1)
        if self._stop_event.is_set():
            return False
        async with self._subscription_activation_lock:
            writer = self._writer
            if writer is None or writer.is_closing():
                return False
            await self._write_message(writer, subscription.command())
        return True

    async def run(self, handler: EventHandler) -> None:
        delay = self._reconnect_min_delay
        while not self._stop_event.is_set():
            try:
                await self._run_connection(handler)
                delay = self._reconnect_min_delay
            except asyncio.CancelledError:
                raise
            except Exception:
                if self._stop_event.is_set():
                    break
                logger.exception(
                    "THS native stream disconnected host=%s port=%s; reconnecting in %.1fs",
                    self._host,
                    self._port,
                    delay,
                )
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                except TimeoutError:
                    pass
                delay = min(self._reconnect_max_delay, max(delay * 2, 1.0))

    async def stop(self) -> None:
        self._stop_event.set()
        self._transport_ready_event.clear()
        self._connected_event.clear()
        self._fail_pending_requests(ConnectionError("THS native stream stopped"))
        writer = self._writer
        if writer is not None:
            writer.close()
            writer.transport.abort()

    def request_reconnect(self) -> bool:
        """Abort the current transport so run() establishes a clean session."""
        writer = self._writer
        if writer is None or writer.is_closing():
            return False
        writer.close()
        writer.transport.abort()
        return True

    async def _run_connection(self, handler: EventHandler) -> None:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                self._host,
                self._port,
                limit=8 * 1024 * 1024,
            ),
            timeout=self._connect_timeout,
        )
        self._writer = writer
        heartbeat_task: asyncio.Task[None] | None = None
        retry_task: asyncio.Task[None] | None = None
        unified_retry_task: asyncio.Task[None] | None = None
        dynamic_subscription_task: asyncio.Task[None] | None = None
        read_task: asyncio.Task[dict[str, Any]] | None = None
        stop_task: asyncio.Task[bool] | None = None
        seen_subscription_ids: set[str] = set()
        subscription_by_id = self._subscription_by_id
        try:
            self._acknowledged_subscription_ids.clear()
            writer.write((self.PROTOCOL + "\n").encode())
            await writer.drain()
            hello = await self._read_message(reader, timeout=self._connect_timeout)
            if hello.get("type") != "hello" or hello.get("protocol") != self.PROTOCOL:
                raise RuntimeError(f"invalid THS stream handshake: {hello}")
            self._transport_ready_event.set()

            realtime_subscriptions = tuple(
                item
                for item in self._initial_subscriptions
                if isinstance(item, THSRealtimeSubscription)
            )
            unified_subscriptions = tuple(
                item
                for item in self._initial_subscriptions
                if isinstance(item, THSUnifiedSubscription)
            )
            # HummerUnifiedRequestBridge reuses an App-global request frame.
            # Hook's `subscribed` message only confirms bridge.init() returned;
            # the frame remains busy until the first successful native callback.
            # Initialize the next bridge only after that callback arrives.
            for subscription in unified_subscriptions:
                initialized = await self._initialize_unified_subscription(
                    reader=reader,
                    writer=writer,
                    subscription=subscription,
                    handler=handler,
                    seen_subscription_ids=seen_subscription_ids,
                    subscription_by_id=subscription_by_id,
                )
                if not initialized:
                    logger.warning(
                        "THS unified subscription unavailable; one-shot fallback remains active: %s",
                        subscription.subscription_id,
                    )

            unified_ready = await self._wait_for_unified_initial_events(
                reader=reader,
                handler=handler,
                seen_subscription_ids=seen_subscription_ids,
                subscription_by_id=subscription_by_id,
                subscriptions=unified_subscriptions,
            )
            if not unified_ready:
                missing = sorted(
                    item.subscription_id
                    for item in unified_subscriptions
                    if item.subscription_id not in seen_subscription_ids
                )
                # Unified push tables are best-effort capabilities.  A missing
                # first callback must not invalidate the multiplexed transport:
                # one-shot futures/ETF/gold/US commands already share this
                # session and would all be failed by a reconnect loop.  Keep
                # the transport command-ready; the retry task and service-level
                # targeted refresh can recover only the missing subscriptions.
                logger.warning(
                    "THS unified subscriptions missing initial events; "
                    "keeping shared transport active missing=%s",
                    ",".join(missing),
                )

            for index, subscription in enumerate(realtime_subscriptions):
                await self._write_message(writer, subscription.command())
                if (
                    self._subscription_interval
                    and index + 1 < len(realtime_subscriptions)
                ):
                    await asyncio.sleep(self._subscription_interval)

            # The stream is command-ready only after the initial subscription
            # batch is fully written. Otherwise an RPC request can be inserted
            # between subscribe commands and break App-side command ordering.
            self._connected = True
            self._connected_event.set()

            logger.info(
                "THS native stream connected host=%s port=%s subscriptions=%s",
                self._host,
                self._port,
                len(self._subscriptions),
            )
            heartbeat_task = asyncio.create_task(
                self._heartbeat(writer),
                name="ths-native-stream-heartbeat",
            )
            retry_task = asyncio.create_task(
                self._retry_missing_subscriptions(writer, seen_subscription_ids),
                name="ths-native-stream-subscription-retry",
            )
            unified_retry_task = asyncio.create_task(
                self._retry_missing_unified_subscriptions(
                    writer,
                    seen_subscription_ids,
                ),
                name="ths-native-stream-unified-subscription-retry",
            )
            dynamic_subscription_task = asyncio.create_task(
                self._activate_dynamic_subscriptions(
                    writer,
                    seen_subscription_ids,
                ),
                name="ths-native-stream-dynamic-subscription-activation",
            )
            while not self._stop_event.is_set():
                read_task = asyncio.create_task(
                    self._read_message(reader, timeout=self._read_timeout)
                )
                stop_task = asyncio.create_task(self._stop_event.wait())
                done, _pending = await asyncio.wait(
                    {read_task, stop_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if stop_task in done:
                    read_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await read_task
                    break
                stop_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await stop_task
                message = read_task.result()
                read_task = None
                stop_task = None
                await self._dispatch_message(
                    message,
                    handler=handler,
                    seen_subscription_ids=seen_subscription_ids,
                    subscription_by_id=subscription_by_id,
                )
        finally:
            # The parent connection coroutine can be cancelled by the service
            # TaskGroup while readline() is still pending.  Always own and
            # retrieve those child tasks; otherwise a closed bridge leaves an
            # orphaned task ("Task exception was never retrieved") and the
            # service can remain alive without a functioning reader.
            for task in (read_task, stop_task):
                if task is not None and not task.done():
                    task.cancel()
            pending_connection_tasks = [
                task for task in (read_task, stop_task) if task is not None
            ]
            if pending_connection_tasks:
                await asyncio.gather(
                    *pending_connection_tasks,
                    return_exceptions=True,
                )
            self._connected = False
            self._transport_ready_event.clear()
            self._connected_event.clear()
            self._acknowledged_subscription_ids.clear()
            self._active_subscription_ids.clear()
            self._fail_pending_requests(
                ConnectionError("THS native stream disconnected")
            )
            if retry_task is not None:
                retry_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await retry_task
            if unified_retry_task is not None:
                unified_retry_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await unified_retry_task
            if dynamic_subscription_task is not None:
                dynamic_subscription_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await dynamic_subscription_task
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat_task
            if self._writer is writer:
                self._writer = None
            writer.close()

    async def _initialize_unified_subscription(
        self,
        *,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        subscription: THSUnifiedSubscription,
        handler: EventHandler,
        seen_subscription_ids: set[str],
        subscription_by_id: dict[str, NativeSubscription],
    ) -> bool:
        response_timeout = max(10.0, self._initial_response_timeout)
        for attempt in range(2):
            async with self._subscription_activation_lock:
                await self._write_message(writer, subscription.command())
                deadline = asyncio.get_running_loop().time() + response_timeout
                while not self._stop_event.is_set():
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    try:
                        message = await self._read_message(reader, timeout=remaining)
                    except TimeoutError:
                        break
                    response_status = self._unified_response_status(
                        message,
                        subscription.subscription_id,
                    )
                    await self._dispatch_message(
                        message,
                        handler=handler,
                        seen_subscription_ids=seen_subscription_ids,
                        subscription_by_id=subscription_by_id,
                    )
                    if response_status is True:
                        return True
                    if response_status is False:
                        break
            logger.warning(
                "Retrying THS unified subscription after missing/failed initial response: %s attempt=%s",
                subscription.subscription_id,
                attempt + 1,
            )
        return False

    async def _wait_for_unified_initial_events(
        self,
        *,
        reader: asyncio.StreamReader,
        handler: EventHandler,
        seen_subscription_ids: set[str],
        subscription_by_id: dict[str, NativeSubscription],
        subscriptions: tuple[THSUnifiedSubscription, ...],
    ) -> bool:
        expected = {item.subscription_id for item in subscriptions}
        if not expected:
            return True
        deadline = asyncio.get_running_loop().time() + max(
            30.0,
            self._initial_response_timeout,
        )
        while not self._stop_event.is_set() and not expected.issubset(
            seen_subscription_ids
        ):
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                message = await self._read_message(reader, timeout=remaining)
            except TimeoutError:
                break
            await self._dispatch_message(
                message,
                handler=handler,
                seen_subscription_ids=seen_subscription_ids,
                subscription_by_id=subscription_by_id,
            )
        return expected.issubset(seen_subscription_ids)

    async def _dispatch_message(
        self,
        message: dict[str, Any],
        *,
        handler: EventHandler,
        seen_subscription_ids: set[str],
        subscription_by_id: dict[str, NativeSubscription],
    ) -> None:
        message_type = message.get("type")
        if message_type == "event":
            subscription_id = str(message.get("subscription_id") or "")
            subscription = subscription_by_id.get(subscription_id)
            if subscription_id and (
                not isinstance(subscription, THSUnifiedSubscription)
                or self._unified_response_status(message, subscription_id) is True
            ):
                seen_subscription_ids.add(subscription_id)
                self._active_subscription_ids.add(subscription_id)
            await handler(message)
        elif message_type == "response":
            request_id = str(message.get("request_id") or "")
            pending = self._pending_requests.get(request_id)
            if pending is not None and not pending.done():
                pending.set_result(message)
        elif message_type == "error":
            request_id = str(message.get("request_id") or "")
            pending = self._pending_requests.get(request_id)
            if pending is not None and not pending.done():
                pending.set_exception(
                    RuntimeError(
                        str(message.get("error") or "THS stream request failed")
                    )
                )
            else:
                logger.warning("THS native stream command error: %s", message)
        elif message_type == "subscribed":
            subscription_id = str(message.get("subscription_id") or "")
            if subscription_id:
                self._acknowledged_subscription_ids.add(subscription_id)
                if isinstance(
                    subscription_by_id.get(subscription_id),
                    THSRealtimeSubscription,
                ):
                    seen_subscription_ids.add(subscription_id)
        elif message_type not in {"pong", "unsubscribed", "subscriptions"}:
            logger.debug("Ignoring THS native stream message: %s", message)

    @staticmethod
    def _unified_response_status(
        message: dict[str, Any],
        subscription_id: str,
    ) -> bool | None:
        if (
            message.get("type") != "event"
            or message.get("topic") != "unified"
            or str(message.get("subscription_id") or "") != subscription_id
        ):
            return None
        payload = message.get("data")
        if not isinstance(payload, dict):
            return None
        head = payload.get("head")
        if not isinstance(head, dict) or head.get("errorCode") is None:
            return None
        return head.get("errorCode") == 0

    async def request(
        self,
        *,
        route: str,
        payload: dict[str, Any],
        timeout: float = 75.0,
    ) -> dict[str, Any]:
        normalized_timeout = max(1.0, float(timeout))
        await asyncio.wait_for(
            self._connected_event.wait(),
            timeout=normalized_timeout,
        )
        writer = self._writer
        if writer is None or writer.is_closing():
            raise ConnectionError("THS native stream is not connected")
        request_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending_requests[request_id] = future
        try:
            # The bridge correlates command responses by request_id.  Holding
            # the App-global subscription activation lock until the response
            # arrives turns one slow Unified request into head-of-line
            # blocking for every other market module (up to 75 seconds each).
            # Only frame emission must be serialized with subscription
            # activation; responses can safely be awaited concurrently.
            async with self._subscription_activation_lock:
                await self._write_message(
                    writer,
                    {
                        "op": "request",
                        "request_id": request_id,
                        "route": route,
                        "payload": payload,
                    },
                )
            message = await asyncio.wait_for(future, timeout=normalized_timeout)
            response = message.get("payload")
            if not isinstance(response, dict):
                raise RuntimeError("THS stream response payload must be an object")
            return response
        finally:
            self._pending_requests.pop(request_id, None)

    def _fail_pending_requests(self, error: Exception) -> None:
        for future in tuple(self._pending_requests.values()):
            if not future.done():
                future.set_exception(error)

    async def _retry_missing_subscriptions(
        self,
        writer: asyncio.StreamWriter,
        seen_subscription_ids: set[str],
    ) -> None:
        await asyncio.sleep(self._initial_response_timeout)
        while not self._stop_event.is_set():
            missing = [
                item
                for item in self._initial_subscriptions
                if isinstance(item, THSRealtimeSubscription)
                and item.subscription_id not in seen_subscription_ids
            ]
            if not missing:
                return
            logger.warning(
                "Retrying THS subscriptions without an initial event: %s",
                ",".join(item.subscription_id for item in missing),
            )
            for index, subscription in enumerate(missing):
                await self._write_message(writer, subscription.command())
                if self._subscription_interval and index + 1 < len(missing):
                    await asyncio.sleep(self._subscription_interval)
            await asyncio.sleep(self._initial_response_timeout)

    async def _retry_missing_unified_subscriptions(
        self,
        writer: asyncio.StreamWriter,
        seen_subscription_ids: set[str],
    ) -> None:
        retry_interval = max(30.0, self._initial_response_timeout)
        await asyncio.sleep(retry_interval)
        while not self._stop_event.is_set():
            missing = [
                item
                for item in self._initial_subscriptions
                if isinstance(item, THSUnifiedSubscription)
                and item.subscription_id not in seen_subscription_ids
            ]
            if not missing:
                return
            logger.warning(
                "Retrying THS unified subscriptions without an initial event: %s",
                ",".join(item.subscription_id for item in missing),
            )
            for index, subscription in enumerate(missing):
                await self._write_message(writer, subscription.command())
                if self._subscription_interval and index + 1 < len(missing):
                    await asyncio.sleep(self._subscription_interval)
            await asyncio.sleep(retry_interval)

    async def _activate_dynamic_subscriptions(
        self,
        writer: asyncio.StreamWriter,
        seen_subscription_ids: set[str],
    ) -> None:
        """Activate large tables one-by-one without blocking base stream startup."""
        activation_interval = self._dynamic_activation_interval
        while not self._stop_event.is_set():
            missing = [
                self._subscription_by_id[subscription_id]
                for subscription_id in tuple(self._dynamic_subscription_ids)
                if subscription_id not in seen_subscription_ids
                and subscription_id in self._subscription_by_id
            ]
            if not missing:
                await asyncio.sleep(1.0)
                continue
            for subscription in missing:
                if self._stop_event.is_set():
                    return
                if subscription.subscription_id in seen_subscription_ids:
                    continue
                # One-shot collectors are freshness-critical and responses are
                # correlated by request_id.  Dynamic push restoration is only
                # maintenance work; pause it while any command is pending so
                # it cannot continuously occupy the App-global Unified frame.
                while self._pending_requests and not self._stop_event.is_set():
                    await asyncio.sleep(0.1)
                if self._stop_event.is_set():
                    return
                logger.info(
                    "Activating dynamic THS subscription: %s",
                    subscription.subscription_id,
                )
                async with self._subscription_activation_lock:
                    await self._write_message(writer, subscription.command())
                    await asyncio.sleep(activation_interval)
                    # Unified subscriptions sharing a protocol also share the
                    # App callback frame. Do not activate the next logical
                    # subscription until this one has produced its correlated
                    # first event; a fixed delay alone allowed protocol 1360
                    # ETF category callbacks to be attributed to the following
                    # category (for example, silver receiving a gold ETF row).
                    deadline = asyncio.get_running_loop().time() + self._initial_response_timeout
                    while (
                        subscription.subscription_id not in seen_subscription_ids
                        and not self._stop_event.is_set()
                        and asyncio.get_running_loop().time() < deadline
                    ):
                        await asyncio.sleep(0.05)

    async def _heartbeat(self, writer: asyncio.StreamWriter) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(self._heartbeat_interval)
            await self._write_message(writer, {"op": "ping"})

    async def _write_message(
        self,
        writer: asyncio.StreamWriter,
        message: dict[str, Any],
    ) -> None:
        payload = json.dumps(
            message,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        async with self._write_lock:
            writer.write(payload + b"\n")
            await writer.drain()

    @staticmethod
    async def _read_message(
        reader: asyncio.StreamReader,
        *,
        timeout: float,
    ) -> dict[str, Any]:
        raw = await asyncio.wait_for(reader.readline(), timeout=timeout)
        if not raw:
            raise ConnectionError("THS native stream closed")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("THS native stream returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise RuntimeError("THS native stream message must be an object")
        return value
