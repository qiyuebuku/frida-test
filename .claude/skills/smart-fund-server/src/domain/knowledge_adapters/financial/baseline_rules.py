"""Baseline normalization rules for the financial KG adapter."""

from __future__ import annotations

BASELINE_NORMALIZATION_RULE_VERSION = "financial_baseline_v1"


def financial_baseline_normalization_rules() -> list[dict]:
    """Return the minimum active rules required before financial KG compilation."""

    rules: list[dict] = [
        _rule("alias", "并购重组主题", "并购重组", confidence=0.95),
        _rule("alias", "并购重组概念", "并购重组", confidence=0.95),
        _rule("alias", "红利资产", "高股息", confidence=0.95),
        _rule("alias", "红利策略", "高股息", confidence=0.95),
        _rule("alias", "高股息资产", "高股息", confidence=0.95),
        _rule("alias", "海外工厂", "海外产能", confidence=0.9),
        _rule("alias", "海外基地", "海外产能", confidence=0.9),
        _rule("alias", "海外工厂投产", "海外产能", confidence=0.9),
    ]
    rules.extend(_rule("weak_suffix", value, confidence=0.8) for value in ["主题", "概念", "板块", "方向"])
    rules.extend(_rule("preserved_suffix", value, confidence=1.0) for value in ["产业链", "供应链", "生态链"])
    rules.append(_rule("generic_policy_suffix", "政策", confidence=0.8))
    rules.extend(
        _rule("concrete_policy_hint", value, confidence=0.8)
        for value in ["规划", "方案", "意见", "通知", "办法", "规定", "规则", "条例", "指引", "会议", "文件", "文号", "决定", "公告"]
    )
    rules.extend(
        [
            _rule("concept_taxonomy", "default", "business", confidence=1.0),
            _rule("concept_taxonomy", "industry_chain", "industry_chain", confidence=1.0),
            _rule("concept_taxonomy", "policy_theme", "policy_theme", confidence=1.0),
        ]
    )
    return rules


def _rule(
    rule_type: str,
    raw_value: str,
    canonical_value: str = "",
    *,
    confidence: float,
) -> dict:
    return {
        "rule_type": rule_type,
        "raw_value": raw_value,
        "canonical_value": canonical_value,
        "status": "active",
        "confidence": confidence,
        "source": "system_baseline",
        "version": BASELINE_NORMALIZATION_RULE_VERSION,
        "payload": {"baseline": True},
    }
