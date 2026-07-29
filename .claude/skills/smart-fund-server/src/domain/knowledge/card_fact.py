"""Project source-specific Cards into reversible equivalent-fact groups."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.knowledge.atomic_cognitive_card import default_card_fact_id


@dataclass(frozen=True)
class CardFactState:
    card_id: str
    fact_id: str


def project_card_fact_ids(
    cards: list[CardFactState],
    *,
    same_fact_pairs: list[tuple[str, str]],
) -> dict[str, str]:
    """Derive fact IDs from the current active observed same_fact graph.

    Existing IDs are reused by anchor/overlap so ordinary incremental updates do
    not rename stable groups. Removing a same_fact edge can split a group again.
    """

    state_by_card = {
        item.card_id: item
        for item in cards
        if item.card_id
    }
    if not state_by_card:
        return {}

    parent = {card_id: card_id for card_id in state_by_card}

    def find(card_id: str) -> str:
        root = card_id
        while parent[root] != root:
            root = parent[root]
        while parent[card_id] != card_id:
            previous = parent[card_id]
            parent[card_id] = root
            card_id = previous
        return root

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        if left_root < right_root:
            parent[right_root] = left_root
        else:
            parent[left_root] = right_root

    for left, right in same_fact_pairs:
        if left in parent and right in parent and left != right:
            union(left, right)

    component_by_root: dict[str, set[str]] = {}
    for card_id in sorted(state_by_card):
        component_by_root.setdefault(find(card_id), set()).add(card_id)
    components = sorted(component_by_root.values(), key=lambda item: min(item))

    old_members: dict[str, set[str]] = {}
    for item in state_by_card.values():
        fact_id = item.fact_id or default_card_fact_id(item.card_id)
        old_members.setdefault(fact_id, set()).add(item.card_id)

    candidates: list[tuple[int, int, str, int]] = []
    for index, members in enumerate(components):
        for fact_id, previous_members in old_members.items():
            overlap = len(members & previous_members)
            if not overlap:
                continue
            has_anchor = any(
                default_card_fact_id(card_id) == fact_id
                for card_id in members
            )
            candidates.append(
                (
                    int(has_anchor),
                    overlap,
                    fact_id,
                    index,
                )
            )

    assigned_by_component: dict[int, str] = {}
    used_fact_ids: set[str] = set()
    for has_anchor, overlap, fact_id, index in sorted(
        candidates,
        key=lambda item: (
            -item[0],
            -item[1],
            item[2],
            item[3],
        ),
    ):
        if index in assigned_by_component or fact_id in used_fact_ids:
            continue
        assigned_by_component[index] = fact_id
        used_fact_ids.add(fact_id)

    projected: dict[str, str] = {}
    for index, members in enumerate(components):
        fact_id = assigned_by_component.get(index)
        if fact_id is None:
            fact_id = default_card_fact_id(min(members))
            if fact_id in used_fact_ids:
                raise ValueError(f"事实投影产生重复 fact_id: {fact_id}")
            used_fact_ids.add(fact_id)
        for card_id in members:
            projected[card_id] = fact_id
    return projected
