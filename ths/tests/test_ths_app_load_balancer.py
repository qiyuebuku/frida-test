import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "deployment"
    / "android-emulator"
    / "ths-app-load-balancer.py"
)
SPEC = importlib.util.spec_from_file_location("ths_app_load_balancer", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class _Response:
    status = 500

    @staticmethod
    def read() -> bytes:
        return b'{"upstream":"failed"}'

    @staticmethod
    def getheader(name: str, default: str) -> str:
        return default


class _Connection:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def request(self, *args, **kwargs) -> None:
        pass

    @staticmethod
    def getresponse() -> _Response:
        return _Response()

    def close(self) -> None:
        pass


def test_busy_native_backend_survives_transient_health_probe_failures(
    monkeypatch,
) -> None:
    backend = MODULE.Backend(
        "app", "127.0.0.1", 1, active=1, active_native=1, healthy=True
    )
    pool = MODULE.BackendPool([backend], timeout=1)
    monkeypatch.setattr(
        MODULE.http.client,
        "HTTPConnection",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError()),
    )

    for _ in range(4):
        pool._check_health(backend)

    assert backend.healthy is True
    assert backend.consecutive_health_failures == 4


def test_backend_http_500_is_returned_without_app_quarantine(monkeypatch) -> None:
    backend = MODULE.Backend("app", "127.0.0.1", 1, healthy=True)
    pool = MODULE.BackendPool([backend], timeout=1)
    monkeypatch.setattr(MODULE.http.client, "HTTPConnection", _Connection)
    monkeypatch.setattr(
        pool,
        "_quarantine_and_recover",
        lambda *args: (_ for _ in ()).throw(
            AssertionError("application HTTP errors must not restart the App")
        ),
    )

    status, payload, _, name = pool.forward("POST", "/proxy", b"{}", {})

    assert (status, payload, name) == (500, b'{"upstream":"failed"}', "app")
    assert backend.healthy is True
    assert backend.draining is False


def test_hurricane_http_can_use_any_initialized_backend() -> None:
    owner = MODULE.Backend("owner", "127.0.0.1", 1, healthy=True)
    clone = MODULE.Backend("clone", "127.0.0.1", 2, healthy=True)
    pool = MODULE.BackendPool([owner, clone], timeout=1)

    selected = []
    for _ in range(2):
        backend = pool.reserve("/native/hurricane", timeout=0.1)
        selected.append(backend.name)
        pool.release(backend, "/native/hurricane")

    assert set(selected) == {"owner", "clone"}
