from __future__ import annotations

from src.domain.knowledge.card_fact import (
    CardFactState,
    project_card_fact_ids,
)
from src.domain.knowledge.atomic_cognitive_card import default_card_fact_id


def _card(card_id: str, fact_id: str = "") -> CardFactState:
    return CardFactState(
        card_id=card_id,
        fact_id=fact_id or default_card_fact_id(card_id),
    )


def test_same_fact_edges_merge_cards_without_discarding_cards() -> None:
    result = project_card_fact_ids(
        [_card("card:a"), _card("card:b"), _card("card:c")],
        same_fact_pairs=[("card:a", "card:b"), ("card:b", "card:c")],
    )

    assert set(result) == {"card:a", "card:b", "card:c"}
    assert len(set(result.values())) == 1


def test_removing_same_fact_edge_splits_previous_group_reversibly() -> None:
    previous_fact_id = default_card_fact_id("card:a")
    result = project_card_fact_ids(
        [
            _card("card:a", previous_fact_id),
            _card("card:b", previous_fact_id),
            _card("card:c", previous_fact_id),
        ],
        same_fact_pairs=[("card:a", "card:b")],
    )

    assert result["card:a"] == result["card:b"] == previous_fact_id
    assert result["card:c"] != previous_fact_id


def test_merge_reuses_existing_fact_with_largest_overlap() -> None:
    left_fact = default_card_fact_id("card:a")
    right_fact = default_card_fact_id("card:z")
    result = project_card_fact_ids(
        [
            _card("card:a", left_fact),
            _card("card:b", left_fact),
            _card("card:c", left_fact),
            _card("card:z", right_fact),
        ],
        same_fact_pairs=[
            ("card:a", "card:b"),
            ("card:b", "card:c"),
            ("card:c", "card:z"),
        ],
    )

    assert set(result.values()) == {left_fact}
