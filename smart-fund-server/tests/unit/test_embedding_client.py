from __future__ import annotations

import httpx
import pytest

from src.infrastructure.clients import embedding as embedding_module


class _Response:
    def __init__(self, embeddings=None, status_code: int = 200):
        self._embeddings = embeddings or []
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://embedding.local/v1/embeddings")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("error", request=request, response=response)

    def json(self):
        return {
            "object": "list",
            "data": [
                {"object": "embedding", "index": index, "embedding": embedding}
                for index, embedding in enumerate(self._embeddings)
            ],
            "model": "/models/Qwen3-Embedding-4B",
        }


class _Client:
    calls: list[list[str]] = []

    def __init__(self, *args, **kwargs):
        del args, kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, url, json):
        assert url.endswith("/v1/embeddings")
        assert json["model"] == embedding_module.EMBEDDING_MODEL
        texts = list(json["input"])
        self.calls.append(texts)
        if len(texts) > 2:
            return _Response(status_code=507)
        return _Response(embeddings=[[float(len(text))] for text in texts])


@pytest.mark.asyncio
async def test_embed_texts_splits_failed_batch_and_preserves_order(monkeypatch, tmp_path):
    _Client.calls = []
    monkeypatch.setattr(embedding_module.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(embedding_module, "EMBEDDING_BATCH_SIZE", 4)
    monkeypatch.setattr(embedding_module, "EMBEDDING_URL", "http://embedding.local")
    monkeypatch.setattr(embedding_module, "EMBEDDING_REQUEST_DIMENSIONS", False)
    monkeypatch.setattr(embedding_module, "EMBEDDING_FILE_CACHE_DIR", str(tmp_path / "embedding_cache"))
    monkeypatch.setattr(embedding_module, "_REMOTE_DIMENSIONS_SUPPORTED", None)

    vectors = await embedding_module.embed_texts(["a", "bb", "ccc", "dddd"], normalize=False)

    assert vectors == [[1.0], [2.0], [3.0], [4.0]]
    assert _Client.calls == [["a", "bb", "ccc", "dddd"], ["a", "bb"], ["ccc", "dddd"]]


@pytest.mark.asyncio
async def test_embed_texts_uses_file_cache_on_second_call(monkeypatch, tmp_path):
    _Client.calls = []
    monkeypatch.setattr(embedding_module.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(embedding_module, "EMBEDDING_BATCH_SIZE", 4)
    monkeypatch.setattr(embedding_module, "EMBEDDING_URL", "http://embedding.local")
    monkeypatch.setattr(embedding_module, "EMBEDDING_REQUEST_DIMENSIONS", False)
    monkeypatch.setattr(embedding_module, "EMBEDDING_FILE_CACHE_ENABLED", True)
    monkeypatch.setattr(embedding_module, "EMBEDDING_FILE_CACHE_DIR", str(tmp_path / "embedding_cache"))
    monkeypatch.setattr(embedding_module, "_REMOTE_DIMENSIONS_SUPPORTED", None)

    first = await embedding_module.embed_texts(["a"], dim=1, normalize=False)
    second = await embedding_module.embed_texts(["a"], dim=1, normalize=False)

    assert first == [[1.0]]
    assert second == [[1.0]]
    assert _Client.calls == [["a"]]
    assert len(list((tmp_path / "embedding_cache").glob("*/*.bin"))) == 1


class _AlwaysFailClient(_Client):
    async def post(self, url, json):
        del url
        texts = list(json["input"])
        self.calls.append(texts)
        return _Response(status_code=507)


@pytest.mark.asyncio
async def test_embed_texts_only_drops_single_items_after_split_retry(monkeypatch, tmp_path):
    _AlwaysFailClient.calls = []
    monkeypatch.setattr(embedding_module.httpx, "AsyncClient", _AlwaysFailClient)
    monkeypatch.setattr(embedding_module, "EMBEDDING_BATCH_SIZE", 2)
    monkeypatch.setattr(embedding_module, "EMBEDDING_URL", "http://embedding.local/v1")
    monkeypatch.setattr(embedding_module, "EMBEDDING_REQUEST_DIMENSIONS", False)
    monkeypatch.setattr(embedding_module, "EMBEDDING_FILE_CACHE_DIR", str(tmp_path / "embedding_cache"))
    monkeypatch.setattr(embedding_module, "_REMOTE_DIMENSIONS_SUPPORTED", None)

    vectors = await embedding_module.embed_texts(["a", "bb"])

    assert vectors == [None, None]
    assert _AlwaysFailClient.calls == [["a", "bb"], ["a"], ["bb"]]


class _MatryoshkaFallbackClient(_Client):
    calls: list[dict] = []

    async def post(self, url, json):
        del url
        self.calls.append(dict(json))
        if "dimensions" in json:
            return _MatryoshkaResponse()
        return _Response(embeddings=[[3.0, 4.0, 0.0, 9.0]])


class _MatryoshkaResponse:
    status_code = 400
    text = '{"error":{"message":"does not support matryoshka representation or dimensions"}}'

    def raise_for_status(self):
        request = httpx.Request("POST", "http://embedding.local/v1/embeddings")
        response = httpx.Response(self.status_code, request=request, text=self.text)
        raise httpx.HTTPStatusError("error", request=request, response=response)


@pytest.mark.asyncio
async def test_embed_texts_falls_back_to_local_truncate_when_dimensions_rejected(monkeypatch, tmp_path):
    _MatryoshkaFallbackClient.calls = []
    monkeypatch.setattr(embedding_module.httpx, "AsyncClient", _MatryoshkaFallbackClient)
    monkeypatch.setattr(embedding_module, "EMBEDDING_BATCH_SIZE", 1)
    monkeypatch.setattr(embedding_module, "EMBEDDING_URL", "http://embedding.local")
    monkeypatch.setattr(embedding_module, "EMBEDDING_REQUEST_DIMENSIONS", True)
    monkeypatch.setattr(embedding_module, "EMBEDDING_FILE_CACHE_DIR", str(tmp_path / "embedding_cache"))
    monkeypatch.setattr(embedding_module, "_REMOTE_DIMENSIONS_SUPPORTED", None)

    vectors = await embedding_module.embed_texts(["a"], dim=2, normalize=True)

    assert len(vectors) == 1
    assert vectors[0] == pytest.approx([0.6, 0.8])
    assert _MatryoshkaFallbackClient.calls == [
        {"model": embedding_module.EMBEDDING_MODEL, "input": ["a"], "dimensions": 2},
        {"model": embedding_module.EMBEDDING_MODEL, "input": ["a"]},
    ]
    assert embedding_module._REMOTE_DIMENSIONS_SUPPORTED is False


def test_embedding_endpoint_accepts_root_v1_or_full_url(monkeypatch):
    monkeypatch.setattr(embedding_module, "EMBEDDING_URL", "http://embedding.local")
    assert embedding_module._embedding_endpoint() == "http://embedding.local/v1/embeddings"

    monkeypatch.setattr(embedding_module, "EMBEDDING_URL", "http://embedding.local/v1")
    assert embedding_module._embedding_endpoint() == "http://embedding.local/v1/embeddings"

    monkeypatch.setattr(embedding_module, "EMBEDDING_URL", "http://embedding.local/v1/embeddings")
    assert embedding_module._embedding_endpoint() == "http://embedding.local/v1/embeddings"
