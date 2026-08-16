import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "fetch_langfuse_traces.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("fetch_langfuse_traces", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _FakeLangfuseClient:
    def __init__(self):
        self.calls = []

    def list_observations(self, **params):
        self.calls.append(params)
        trace_id = params.get("trace_id")
        if trace_id:
            return {
                "data": [{
                    "id": f"obs_{trace_id}", "traceId": trace_id,
                    "sessionId": "session_1", "traceName": "kg.write_path_demo",
                    "isRootObservation": True, "startTime": f"2026-08-0{1 if trace_id == 'trace_a' else 2}T00:00:00Z",
                    "input": {"x": 1}, "output": {"y": 2}, "metadata": {"z": 3},
                    "usageDetails": {"input": 10, "output": 2, "total": 12},
                }],
                "meta": {"cursor": None},
            }
        if params.get("cursor") == "page_2":
            return {
                "data": [{
                    "id": "obs_b", "traceId": "trace_b", "sessionId": "session_1",
                    "traceName": "kg.write_path_demo", "isRootObservation": True,
                    "startTime": "2026-08-02T00:00:00Z", "tags": ["research"],
                }],
                "meta": {"cursor": None},
            }
        return {
            "data": [{
                "id": "obs_a", "traceId": "trace_a", "sessionId": "session_1",
                "traceName": "kg.write_path_demo", "isRootObservation": True,
                "startTime": "2026-08-01T00:00:00Z", "tags": ["research"],
            }],
            "meta": {"cursor": "page_2"},
        }


def test_client_configuration_requires_explicit_project_prefix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_script_module()
    monkeypatch.setenv("SMART_FUND_AGENT_LANGFUSE_BASE_URL", "http://agent:3001")
    monkeypatch.setenv("SMART_FUND_AGENT_LANGFUSE_PUBLIC_KEY", "pk-agent")
    monkeypatch.setenv("SMART_FUND_AGENT_LANGFUSE_SECRET_KEY", "sk-agent")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-legacy")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-legacy")

    client = module.client_from_env(
        env_file=tmp_path / "missing.env",
        project="agent",
    )

    assert client._host == "http://agent:3001"


def test_trace_cli_requires_project() -> None:
    module = _load_script_module()
    with pytest.raises(SystemExit):
        module.parse_args(["--session-id", "session_1"])


def test_download_latest_session_writes_reconstructed_trace_files(tmp_path):
    module = _load_script_module()
    client = _FakeLangfuseClient()

    result = module.download_latest_session(
        client, out_dir=tmp_path, name="kg.write_path_demo",
        fields=module.DEFAULT_FULL_FIELDS, page_size=1, tags=["research"],
        from_timestamp="2026-08-01T00:00:00Z",
        to_timestamp="2026-08-03T00:00:00Z",
    )

    assert result.session_id == "session_1"
    assert result.latest_trace_id == "trace_b"
    assert result.trace_count == 2
    manifest = json.loads((result.output_dir / "manifest.json").read_text())
    assert [item["trace_id"] for item in manifest["traces"]] == ["trace_a", "trace_b"]
    payload = json.loads((result.output_dir / "trace-trace_a-full.json").read_text())
    assert payload["observations"][0]["input"] == {"x": 1}
    assert payload["observations"][0]["usageDetails"]["total"] == 12
    assert any(call.get("cursor") == "page_2" for call in client.calls)
    assert any(call.get("trace_id") == "trace_a" for call in client.calls)


def test_download_session_filters_client_side_and_always_uses_time_bounds(tmp_path):
    module = _load_script_module()
    client = _FakeLangfuseClient()

    result = module.download_session(
        client, session_id="session_1", out_dir=tmp_path,
        fields=module.DEFAULT_FULL_FIELDS, page_size=1,
        latest_trace_id="trace_b",
        from_timestamp="2026-08-01T00:00:00Z",
        to_timestamp="2026-08-03T00:00:00Z",
    )

    assert result.trace_count == 2
    discovery_calls = [call for call in client.calls if not call.get("trace_id")]
    assert discovery_calls
    assert all(call["from_start_time"] == "2026-08-01T00:00:00Z" for call in discovery_calls)
    assert all(call["to_start_time"] == "2026-08-03T00:00:00Z" for call in discovery_calls)


def test_summarize_trace_accepts_langfuse_v4_usage_keys():
    module = _load_script_module()
    summary = module.summarize_trace({
        "observations": [{"usageDetails": {"input": 8, "output": 3, "total": 11}}],
    })
    assert summary["usage"] == {"input_tokens": 8, "output_tokens": 3, "total_tokens": 11}


def test_langfuse_client_retries_transient_read_failure(monkeypatch):
    module = _load_script_module()
    attempts = 0

    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def fake_urlopen(_request, timeout):
        nonlocal attempts
        assert timeout == 1
        attempts += 1
        if attempts < 3:
            raise ConnectionResetError("transient")
        return _Response(b'{"data": []}')

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    client = module.LangfuseClient(host="http://langfuse", public_key="pk", secret_key="sk", timeout=1)

    assert client.list_observations() == {"data": []}
    assert attempts == 3
