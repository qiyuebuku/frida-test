import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "fetch_langfuse_traces.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("fetch_langfuse_traces", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _FakeLangfuseClient:
    def __init__(self):
        self.list_calls = []
        self.get_calls = []

    def list_traces(self, **params):
        self.list_calls.append(params)
        if params.get("session_id"):
            page = params.get("page", 1)
            if page == 1:
                return {
                    "data": [
                        {"id": "trace_a", "sessionId": "session_1", "timestamp": "2026-07-01T00:00:01Z"},
                    ],
                    "meta": {"page": 1, "limit": 1, "totalItems": 2, "totalPages": 2},
                }
            return {
                "data": [
                    {"id": "trace_b", "sessionId": "session_1", "timestamp": "2026-07-01T00:00:02Z"},
                ],
                "meta": {"page": 2, "limit": 1, "totalItems": 2, "totalPages": 2},
            }
        return {
            "data": [
                {"id": "trace_b", "sessionId": "session_1", "timestamp": "2026-07-01T00:00:02Z"},
            ],
            "meta": {"page": 1, "limit": 10, "totalItems": 1, "totalPages": 1},
        }

    def get_trace(self, trace_id, *, fields):
        self.get_calls.append((trace_id, fields))
        return {
            "id": trace_id,
            "sessionId": "session_1",
            "observations": [
                {"id": f"obs_{trace_id}", "input": {"x": 1}, "output": {"y": 2}, "metadata": {"z": 3}},
            ],
            "scores": [],
        }


def test_download_latest_session_writes_full_trace_files_and_manifest(tmp_path):
    module = _load_script_module()
    client = _FakeLangfuseClient()

    result = module.download_latest_session(
        client,
        out_dir=tmp_path,
        name="kg.write_path_demo",
        fields=module.DEFAULT_FULL_FIELDS,
        page_size=1,
    )

    assert result.session_id == "session_1"
    assert result.trace_count == 2
    assert client.get_calls == [
        ("trace_a", module.DEFAULT_FULL_FIELDS),
        ("trace_b", module.DEFAULT_FULL_FIELDS),
    ]
    manifest = json.loads((result.output_dir / "manifest.json").read_text())
    assert manifest["session_id"] == "session_1"
    assert manifest["latest_trace_id"] == "trace_b"
    assert [item["trace_id"] for item in manifest["traces"]] == ["trace_a", "trace_b"]
    assert (result.output_dir / "trace-trace_a-full.json").exists()
    trace_payload = json.loads((result.output_dir / "trace-trace_a-full.json").read_text())
    assert trace_payload["observations"][0]["input"] == {"x": 1}


def test_download_session_uses_given_session_without_latest_lookup(tmp_path):
    module = _load_script_module()
    client = _FakeLangfuseClient()

    result = module.download_session(
        client,
        session_id="session_1",
        out_dir=tmp_path,
        fields=module.DEFAULT_FULL_FIELDS,
        page_size=1,
        latest_trace_id="trace_b",
    )

    assert result.trace_count == 2
    assert all(call.get("session_id") == "session_1" for call in client.list_calls)
    assert not any(call.get("name") == "kg.write_path_demo" and not call.get("session_id") for call in client.list_calls)
