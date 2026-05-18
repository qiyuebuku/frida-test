import pytest

from src.infrastructure.vector_store.milvus_hybrid_store import (
    MilvusHybridDocument,
    MilvusHybridStore,
)


def test_replace_documents_refuses_vector_count_mismatch_before_delete() -> None:
    store = MilvusHybridStore(dim=3)

    with pytest.raises(ValueError, match="vector/document count mismatch"):
        store.replace_documents(
            adapter_name="financial",
            target="prod",
            documents=[MilvusHybridDocument(chunk_id="doc-1", text="hello")],
            vectors=[],
            embedding_model="test-embedding",
        )


def test_replace_documents_refuses_invalid_vector_before_delete() -> None:
    store = MilvusHybridStore(dim=3)

    with pytest.raises(ValueError, match="invalid dense vector"):
        store.replace_documents(
            adapter_name="financial",
            target="prod",
            documents=[MilvusHybridDocument(chunk_id="doc-1", text="hello")],
            vectors=[[1.0, float("nan"), 3.0]],
            embedding_model="test-embedding",
        )


def test_hybrid_search_returns_empty_before_bm25_when_scope_has_no_rows() -> None:
    class FakeClient:
        def has_collection(self, collection_name: str) -> bool:
            return True

        def query(self, **kwargs):
            return []

    class Store(MilvusHybridStore):
        def ensure_collection(self, *, recreate_on_dim_mismatch: bool = False) -> None:
            return None

        def _get_client(self):
            return FakeClient()

    store = Store(dim=3)

    assert (
        store.hybrid_search(
            query_text="A股并购重组",
            query_vector=[0.1, 0.2, 0.3],
            adapter_name="financial",
            target="prod",
            limit=5,
        )
        == []
    )
