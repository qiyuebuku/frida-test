"""Persistence helpers for database-backed KG normalization rules."""

from __future__ import annotations

from typing import Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.domain.knowledge_adapters.financial.normalization import (
    EMPTY_NORMALIZATION_RULES,
    NormalizationRules,
)
from src.domain.knowledge.ids import stable_hash
from src.infrastructure.connections import get_session
from src.infrastructure.connections.database import get_engine
from src.infrastructure.persistence.models.knowledge import KnowledgeNormalizationRule

Target = Literal["prod", "test"]


class KnowledgeNormalizationRuleRepository:
    def __init__(self, target: Target | None = None):
        self.target = target

    def load_active_rules(self, adapter_name: str) -> NormalizationRules:
        self.ensure_table()
        with get_session(self.target) as session:
            rows = session.scalars(
                select(KnowledgeNormalizationRule)
                .where(
                    KnowledgeNormalizationRule.adapter_name == adapter_name,
                    KnowledgeNormalizationRule.status == "active",
                )
                .order_by(KnowledgeNormalizationRule.rule_type, KnowledgeNormalizationRule.raw_value)
            ).all()
        if not rows:
            return EMPTY_NORMALIZATION_RULES

        aliases: dict[str, str] = {}
        weak_suffixes: list[str] = []
        preserved_suffixes: list[str] = []
        generic_policy_suffixes: list[str] = []
        concrete_policy_hints: list[str] = []
        concept_taxonomy = {
            "default": EMPTY_NORMALIZATION_RULES.concept_taxonomy_default,
            "industry_chain": EMPTY_NORMALIZATION_RULES.concept_taxonomy_industry_chain,
            "policy_theme": EMPTY_NORMALIZATION_RULES.concept_taxonomy_policy_theme,
        }
        for row in rows:
            if row.rule_type == "alias" and row.raw_value and row.canonical_value:
                aliases[row.raw_value] = row.canonical_value
            elif row.rule_type == "weak_suffix":
                weak_suffixes.append(row.raw_value)
            elif row.rule_type == "preserved_suffix":
                preserved_suffixes.append(row.raw_value)
            elif row.rule_type == "generic_policy_suffix":
                generic_policy_suffixes.append(row.raw_value)
            elif row.rule_type == "concrete_policy_hint":
                concrete_policy_hints.append(row.raw_value)
            elif row.rule_type == "concept_taxonomy" and row.raw_value:
                concept_taxonomy[row.raw_value] = row.canonical_value

        return NormalizationRules(
            aliases=aliases,
            weak_suffixes=tuple(_unique(weak_suffixes)),
            preserved_suffixes=tuple(_unique(preserved_suffixes)),
            generic_policy_suffixes=tuple(_unique(generic_policy_suffixes)),
            concrete_policy_hints=tuple(_unique(concrete_policy_hints)),
            concept_taxonomy_default=concept_taxonomy["default"],
            concept_taxonomy_industry_chain=concept_taxonomy["industry_chain"],
            concept_taxonomy_policy_theme=concept_taxonomy["policy_theme"],
        )

    def ensure_active_rules(self, adapter_name: str, baseline_rules: list[dict]) -> int:
        """Ensure an adapter has active normalization rules before compilation."""

        self.ensure_table()
        missing_rules = self._missing_active_baseline_rules(adapter_name, baseline_rules)
        if not missing_rules:
            return 0
        affected = self.upsert_rules(adapter_name, missing_rules)
        if self._missing_active_baseline_rules(adapter_name, baseline_rules):
            raise RuntimeError(f"KG normalization baseline rules bootstrap failed: adapter={adapter_name}")
        return affected

    def has_active_rules(self, adapter_name: str) -> bool:
        self.ensure_table()
        with get_session(self.target) as session:
            return (
                session.scalar(
                    select(KnowledgeNormalizationRule.rule_id)
                    .where(
                        KnowledgeNormalizationRule.adapter_name == adapter_name,
                        KnowledgeNormalizationRule.status == "active",
                    )
                    .limit(1)
                )
                is not None
            )

    def list_rules(self, adapter_name: str, status: str | None = None) -> list[dict]:
        self.ensure_table()
        with get_session(self.target) as session:
            stmt = select(KnowledgeNormalizationRule).where(KnowledgeNormalizationRule.adapter_name == adapter_name)
            if status is not None:
                stmt = stmt.where(KnowledgeNormalizationRule.status == status)
            rows = session.scalars(stmt.order_by(KnowledgeNormalizationRule.rule_type, KnowledgeNormalizationRule.raw_value)).all()
        return [
            {
                "rule_id": row.rule_id,
                "adapter_name": row.adapter_name,
                "rule_type": row.rule_type,
                "raw_value": row.raw_value,
                "canonical_value": row.canonical_value,
                "status": row.status,
                "confidence": row.confidence,
                "source": row.source,
                "version": row.version,
                "payload": dict(row.payload or {}),
            }
            for row in rows
        ]

    def get_active_decision(self, adapter_name: str, *, object_kind: str, raw_signature: str) -> dict | None:
        self.ensure_table()
        rule_type = _decision_rule_type(object_kind)
        with get_session(self.target) as session:
            row = session.scalar(
                select(KnowledgeNormalizationRule)
                .where(
                    KnowledgeNormalizationRule.adapter_name == adapter_name,
                    KnowledgeNormalizationRule.rule_type == rule_type,
                    KnowledgeNormalizationRule.raw_value == raw_signature,
                    KnowledgeNormalizationRule.status == "active",
                )
                .limit(1)
            )
        if row is None:
            return None
        return {
            "rule_id": row.rule_id,
            "adapter_name": row.adapter_name,
            "rule_type": row.rule_type,
            "raw_signature": row.raw_value,
            "canonical_value": row.canonical_value,
            "status": row.status,
            "confidence": row.confidence,
            "source": row.source,
            "version": row.version,
            "payload": dict(row.payload or {}),
        }

    def upsert_decision(
        self,
        adapter_name: str,
        *,
        object_kind: str,
        raw_signature: str,
        canonical_value: str,
        confidence: float,
        source: str,
        payload: dict,
    ) -> int:
        self.ensure_table()
        rule_type = _decision_rule_type(object_kind)
        return self.upsert_rules(
            adapter_name,
            [
                {
                    "rule_id": f"kg_norm_decision:{adapter_name}:{object_kind}:{stable_hash([raw_signature, 'active'])}",
                    "rule_type": rule_type,
                    "raw_value": raw_signature,
                    "canonical_value": canonical_value,
                    "status": "active",
                    "confidence": confidence,
                    "source": source,
                    "payload": payload,
                }
            ],
        )

    def upsert_rules(self, adapter_name: str, rules: list[dict]) -> int:
        self.ensure_table()
        rows = []
        for item in rules:
            rule_type = str(item["rule_type"])
            raw_value = str(item["raw_value"])
            canonical_value = str(item.get("canonical_value") or "")
            status = str(item.get("status") or "candidate")
            rows.append(
                {
                    "rule_id": item.get("rule_id")
                    or f"kg_norm_rule:{adapter_name}:{rule_type}:{stable_hash([raw_value, status])}",
                    "adapter_name": adapter_name,
                    "rule_type": rule_type,
                    "raw_value": raw_value,
                    "canonical_value": canonical_value,
                    "status": status,
                    "confidence": float(item.get("confidence") or 0.0),
                    "source": str(item.get("source") or ""),
                    "version": str(item.get("version") or "v1"),
                    "payload": dict(item.get("payload") or {}),
                }
            )
        if not rows:
            return 0
        with get_session(self.target) as session:
            stmt = pg_insert(KnowledgeNormalizationRule).values(rows)
            excluded = stmt.excluded
            result = session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["rule_id"],
                    set_={
                        "canonical_value": excluded.canonical_value,
                        "status": excluded.status,
                        "confidence": excluded.confidence,
                        "source": excluded.source,
                        "version": excluded.version,
                        "payload": excluded.payload,
                    },
                )
            )
            return result.rowcount or 0

    def ensure_table(self) -> None:
        KnowledgeNormalizationRule.__table__.create(bind=get_engine(self.target), checkfirst=True)

    def _missing_active_baseline_rules(self, adapter_name: str, baseline_rules: list[dict]) -> list[dict]:
        expected = {
            _rule_key(item)
            for item in baseline_rules
            if str(item.get("status") or "candidate") == "active"
        }
        if not expected:
            return []
        with get_session(self.target) as session:
            rows = session.execute(
                select(
                    KnowledgeNormalizationRule.rule_type,
                    KnowledgeNormalizationRule.raw_value,
                    KnowledgeNormalizationRule.status,
                ).where(
                    KnowledgeNormalizationRule.adapter_name == adapter_name,
                    KnowledgeNormalizationRule.status == "active",
                )
            ).all()
        existing = {(rule_type, raw_value, status) for rule_type, raw_value, status in rows}
        return [item for item in baseline_rules if _rule_key(item) not in existing]


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _rule_key(item: dict) -> tuple[str, str, str]:
    return (
        str(item["rule_type"]),
        str(item["raw_value"]),
        str(item.get("status") or "candidate"),
    )


def _decision_rule_type(object_kind: str) -> str:
    kind = str(object_kind or "").strip()
    if kind not in {"entity", "relation"}:
        raise ValueError(f"unsupported normalization decision object_kind: {object_kind}")
    return f"{kind}_decision"
