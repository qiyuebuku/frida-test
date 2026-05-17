from src.domain.knowledge.retrieval_document import RetrievalDocument
from src.domain.knowledge.retrieval_keyword import (
    build_keyword_match,
    lexical_query_terms,
    retrieval_document_query_score,
)


def test_lexical_query_terms_keeps_code_and_chinese_phrases():
    terms = lexical_query_terms("宁德时代 300750 海外产能扩张")

    assert "300750" in terms
    assert "宁德时代" in terms
    assert "海外产能扩张" in terms


def test_keyword_match_returns_terms_fields_and_score():
    document = RetrievalDocument(
        document_id="doc-1",
        adapter_name="financial",
        source_fact_type="node",
        source_fact_id="kg:financial:event:1",
        title="宁德时代海外产能扩张带动储能供应链订单",
        search_text="宁德时代推进欧洲和东南亚海外产能扩张。",
        key_phrases=["300750", "海外产能扩张"],
        aliases=["CATL"],
        readable_relations=["宁德时代海外产能扩张 affects 宁德时代"],
        evidence_summary="储能电芯和快充电池订单预期改善。",
        answer_candidate_type="answer",
        node_refs=["kg:financial:event:1"],
        evidence_refs=["kg_ev:1"],
    )

    terms = lexical_query_terms("300750 宁德时代 海外产能扩张")
    match = build_keyword_match(document, terms)

    assert match.hit_type == "node"
    assert "300750" in match.matched_terms
    assert "title" in match.matched_fields
    assert "key_phrases" in match.matched_fields
    assert retrieval_document_query_score(document, terms) == match.score
    assert match.score > 4.0
