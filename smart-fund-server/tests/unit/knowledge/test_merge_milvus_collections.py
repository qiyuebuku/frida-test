from scripts.merge_milvus_collections import _missing_rows


def test_missing_rows_keeps_target_records_and_deduplicates_source_batch() -> None:
    existing = {"newer"}
    rows = [
        {"target_id": "older", "text": "old"},
        {"target_id": "newer", "text": "must not overwrite"},
        {"target_id": "older", "text": "duplicate"},
        {"target_id": "", "text": "invalid"},
    ]

    assert _missing_rows(rows, existing) == [{"target_id": "older", "text": "old"}]
    assert existing == {"newer", "older"}
