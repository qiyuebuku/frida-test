from __future__ import annotations

from src.domain.knowledge.retrieval_document import RetrievalDocument
from src.domain.knowledge.retrieval_document_quality import build_retrieval_document_quality_report


def _document(**overrides) -> RetrievalDocument:
    data = {
        "document_id": "kg_rdoc:prod:node:1",
        "adapter_name": "financial",
        "target": "prod",
        "source_fact_type": "node",
        "source_fact_id": "kg:financial:node:1",
        "title": "宁德时代",
        "search_text": "宁德时代\n300750",
        "key_phrases": ["宁德时代", "300750"],
        "aliases": ["CATL"],
        "evidence_summary": "宁德时代资金净流入改善",
        "answer_candidate_type": "answer",
        "generation_version": "retrieval_doc_v2",
    }
    data.update(overrides)
    return RetrievalDocument(**data)


def test_retrieval_document_quality_report_counts_coverage_and_noise() -> None:
    documents = [
        _document(),
        _document(
            document_id="kg_rdoc:prod:node:2",
            source_fact_id="kg:financial:node:2",
            key_phrases=["aliases", "code"],
            aliases=[],
            evidence_summary="",
            generation_version="retrieval_doc_v1",
        ),
    ]

    report = build_retrieval_document_quality_report(
        documents,
        expected_generation_version="retrieval_doc_v2",
    )

    assert report["total"] == 2
    assert report["expected_generation_version_count"] == 1
    assert report["expected_generation_version_ratio"] == 0.5
    assert report["field_counts"]["search_text"] == 2
    assert report["field_counts"]["aliases"] == 1
    assert report["field_counts"]["evidence_summary"] == 1
    assert report["empty_summary_by_fact_type"] == {"node": 1}
    assert report["json_noise_count"] == 1
    assert "generation_version_mismatch expected=retrieval_doc_v2 actual=1/2" in report["warnings"]
    assert "key_phrases_json_noise_detected" in report["warnings"]
