import importlib.util
import json
import threading
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "deployment"
    / "android-emulator"
    / "ths-native-proxy.py"
)
SPEC = importlib.util.spec_from_file_location("ths_native_proxy", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_upstream_timeout_covers_native_callback_budget() -> None:
    assert MODULE.AndroidNativeBridge.UPSTREAM_TIMEOUT_SECONDS >= 60


def test_ranking_callback_cooldown_covers_async_unregistration() -> None:
    assert MODULE.AndroidNativeBridge.RANKING_CALLBACK_COOLDOWN_SECONDS >= 0.8


def test_connection_timeout_recognizes_native_error_code() -> None:
    timeout_payload = json.dumps(
        {"success": False, "response": {"head": {"errorCode": -131}}}
    ).encode()
    normal_payload = json.dumps(
        {"success": True, "response": {"head": {"errorCode": 0}}}
    ).encode()

    assert MODULE.AndroidNativeBridge._is_connection_timeout(200, timeout_payload)
    assert not MODULE.AndroidNativeBridge._is_connection_timeout(200, normal_payload)


def test_realtime_stream_session_count_reads_hook_health() -> None:
    bridge = object.__new__(MODULE.AndroidNativeBridge)
    bridge._request_upstream = lambda method, path, body: (
        200,
        b'{"ok":true,"realtime_stream_sessions":2}',
        "application/json",
    )

    assert bridge._realtime_stream_session_count() == 2


def test_realtime_stream_session_count_falls_back_when_health_is_unavailable() -> None:
    bridge = object.__new__(MODULE.AndroidNativeBridge)
    bridge._request_upstream = lambda method, path, body: (
        503,
        b'{}',
        "application/json",
    )

    assert bridge._realtime_stream_session_count() == 0


def test_recovery_does_not_restart_app_while_realtime_stream_is_active() -> None:
    bridge = object.__new__(MODULE.AndroidNativeBridge)
    bridge._recovery_lock = threading.Lock()
    bridge._generation = 0
    bridge._realtime_stream_session_count = lambda: 1
    bridge._adb_run = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("App restart must not be attempted")
    )

    assert bridge._recover_if_needed(0) is False


def test_ranking_callback_timeout_is_returned_without_app_recovery(monkeypatch) -> None:
    bridge = object.__new__(MODULE.AndroidNativeBridge)
    bridge._native_request_lock = threading.Lock()
    bridge._generation = 0
    payload = b'{"success":false,"error":"protocol response timed out"}'
    bridge._request_upstream = lambda method, path, body: (
        200,
        payload,
        "application/json",
    )
    bridge._recover_if_needed = lambda generation: (_ for _ in ()).throw(
        AssertionError("ranking callback misses must not restart the App")
    )
    monkeypatch.setattr(MODULE.time, "sleep", lambda seconds: None)

    assert bridge.forward("POST", "/native/ranking-debug", b"{}") == (
        200,
        payload,
        "application/json",
    )


def test_unified_callback_timeout_is_returned_without_app_recovery(monkeypatch) -> None:
    bridge = object.__new__(MODULE.AndroidNativeBridge)
    bridge._native_request_lock = threading.Lock()
    bridge._generation = 0
    payload = b'{"success":false,"error":"unified request timed out"}'
    bridge._request_upstream = lambda method, path, body: (
        200,
        payload,
        "application/json",
    )
    bridge._recover_if_needed = lambda generation: (_ for _ in ()).throw(
        AssertionError("one-shot callback misses must not restart the App")
    )
    monkeypatch.setattr(MODULE.time, "sleep", lambda seconds: None)

    assert bridge.forward("POST", "/native/unified", b"{}") == (
        200,
        payload,
        "application/json",
    )


def test_all_native_families_share_one_single_flight_guard() -> None:
    bridge = object.__new__(MODULE.AndroidNativeBridge)
    bridge._native_request_lock = threading.Lock()
    bridge._generation = 0
    active = 0
    max_active = 0
    active_lock = threading.Lock()
    barrier = threading.Barrier(3)

    # Release both worker threads together before they contend for the proxy
    # guard. The barrier belongs outside request_upstream because the guard is
    # intentionally acquired before the upstream call.
    def invoke(path: str) -> None:
        barrier.wait()
        bridge.forward("POST", path, b"{}")

    bridge._request_upstream = lambda method, path, body: (
        active_request(method, path, body)
    )

    def active_request(method: str, path: str, body: bytes):
        nonlocal active, max_active
        with active_lock:
            active += 1
            max_active = max(max_active, active)
        try:
            threading.Event().wait(0.02)
            return 200, b'{"success":true}', "application/json"
        finally:
            with active_lock:
                active -= 1

    first = threading.Thread(target=invoke, args=("/native/ranking-debug",))
    second = threading.Thread(target=invoke, args=("/native/hurricane",))
    first.start()
    second.start()
    barrier.wait()
    first.join()
    second.join()

    assert max_active == 1
