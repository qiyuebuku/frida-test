"""Tests for Chinese query retrieval tokenization."""

from __future__ import annotations

from src.domain.knowledge.enums import NodeStatus
from src.domain.knowledge.retrieval import HybridRetrievalRuntime, RetrievalOptions, _terms
from src.domain.knowledge.retrieval_document import RetrievalDocument
from src.domain.knowledge.schemas import CompiledNode


class _Repo:
    def list_nodes(self, adapter_name: str):
        assert adapter_name == "financial"
        return [
            CompiledNode(
                node_id="kg:financial:concept:ma",
                adapter_name="financial",
                node_type="concept",
                canonical_name="并购重组",
                status=NodeStatus.CANDIDATE,
                version="v1",
            )
        ]

    def search_retrieval_documents(self, adapter_name: str, query: str, *, target: str = "prod", limit: int = 20):
        assert adapter_name == "financial"
        return [
            RetrievalDocument(
                document_id="kg_rdoc:prod:node:kg:financial:concept:ma",
                adapter_name="financial",
                source_fact_type="node",
                source_fact_id="kg:financial:concept:ma",
                title="并购重组",
                search_text="并购重组 对 行业 影响",
                key_phrases=["并购重组", "行业", "影响"],
                node_refs=["kg:financial:concept:ma"],
                answer_candidate_type="answer",
            )
        ][:limit]


def test_chinese_query_terms_include_domain_phrase_and_bigrams() -> None:
    terms = _terms("并购重组对哪些行业有影响")

    assert "并购重组" in terms
    assert "并购" in terms
    assert "重组" in terms
    assert "行业" in terms
    assert "影响" in terms


def test_keyword_search_matches_chinese_sentence_without_spaces() -> None:
    hits = HybridRetrievalRuntime(_Repo()).keyword_search(
        "并购重组对哪些行业有影响",
        RetrievalOptions(adapter_name="financial", keyword_limit=10),
    )

    assert len(hits) == 1
    assert hits[0].title == "并购重组"
