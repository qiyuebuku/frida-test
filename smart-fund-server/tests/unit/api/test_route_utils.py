from src.infrastructure import clients
from src.interfaces.api.routes import _utils


def test_route_utils_reads_initialized_client_dynamically(monkeypatch) -> None:
    sentinel = object()
    monkeypatch.setattr(clients, "ths", sentinel)

    assert _utils.ths is sentinel
