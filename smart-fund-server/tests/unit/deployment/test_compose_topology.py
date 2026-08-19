from pathlib import Path

import yaml


SERVER_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = SERVER_ROOT / "deployment" / "docker" / "compose.production.yml"
DOCKERFILE = SERVER_ROOT / "deployment" / "docker" / "Dockerfile"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))


def test_compose_uses_one_server_image_with_distinct_commands() -> None:
    services = _compose()["services"]
    app_services = {
        "api": ["api"],
        "persist": ["persist"],
        "scheduler": ["scheduler"],
        "worker-ths": ["worker", "--group", "ths", "-c", "${THS_WORKER_CONCURRENCY:-8}"],
        "worker-ths-sector": ["worker", "--group", "ths-sector", "-c", "${THS_SECTOR_WORKER_CONCURRENCY:-4}"],
        "worker-general": ["worker", "--group", "general", "-c", "${GENERAL_WORKER_CONCURRENCY:-12}"],
        "ths-realtime-stream": ["ths-realtime-stream"],
        "kg-card": ["knowledge-worker", "--stage", "card", "-c", "1"],
        "kg-relation": ["knowledge-worker", "--stage", "relation", "-c", "${KG_RELATION_WORKER_CONCURRENCY:-3}"],
    }

    for service_name, command in app_services.items():
        service = services[service_name]
        assert service["image"] == "${SMART_FUND_IMAGE:?SMART_FUND_IMAGE is required}"
        assert service["network_mode"] == "host"
        assert service["command"] == command


def test_android_runtime_is_not_in_server_compose() -> None:
    services = _compose()["services"]

    assert not any("android" in name or "emulator" in name for name in services)
    assert {"etcd", "milvus"} <= set(services)
    assert services["kg-graph"]["profiles"] == ["manual"]


def test_image_build_requires_staged_internal_jettask_wheel() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "/app/.docker-build/jettask.whl" in dockerfile
    assert "debug.keystore" not in dockerfile
    assert "production.env" not in dockerfile
