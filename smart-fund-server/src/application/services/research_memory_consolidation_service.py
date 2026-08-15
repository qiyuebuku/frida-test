"""Governed consolidation of repeated Research lessons into role memory."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from src.domain.trading.research_quality_reference import quality_ref_from_run_id
from src.infrastructure.persistence.repositories.agent_research_repository import (
    AgentResearchRepository,
)


_QUALITY_LESSONS = {
    "market_citation_subject_mismatch": (
        "市场主张必须引用同一对象的证据，不能用相关板块或指数替代目标对象。",
        "适用于所有包含精确行情、资金、宽度或历史表现的主张。",
        "若主张明确表述为跨对象比较，并同时打开两端证据，则不属于对象错配。",
    ),
    "primary_view_missing_own_multi_day_history": (
        "形成主要方向前必须检查观点对象自身的多日历史，不能只依赖单日快照或相关主题走势。",
        "适用于带趋势、持续性、突破、回撤或未来方向判断的主要观点。",
        "若结论只描述当日已观察事实且不外推持续性，可以不要求多日历史。",
    ),
    "observed_fact_contains_inference": (
        "已观察事实与因果推断必须拆开；共同变化不能直接写成已经证实的传导机制。",
        "适用于机制链、资金来源、事件影响和板块联动判断。",
        "存在对象对齐的时序证据或原始事件材料时，可把对应箭头升级为已观察。",
    ),
    "missing_narrative_source_evidence": (
        "产业或事件叙事进入核心结论前必须打开原始材料或独立正文，搜索摘要和行情相关性不够。",
        "适用于依赖政策、产业进展、公司事件或市场传闻解释行情的观点。",
        "纯粹基于透明行情统计且不声称事件因果时，不需要为了凑数引入叙事来源。",
    ),
    "missing_full_market_landscape": (
        "确定主线前应比较主要市场候选和共同约束，避免只研究最先注意到的方向。",
        "适用于全市场研究、盘前方向判断和候选主题筛选。",
        "明确限定为单一对象的事件复核任务，不要求重复完成全市场扫描。",
    ),
    "missing_direct_counterevidence": (
        "主要观点必须寻找能够直接推翻它的对象对齐证据，背景风险不能冒充直接反证。",
        "适用于方向预测、机制判断和候选淘汰。",
        "当结论仅为数据描述且没有方向或因果外推时，可只披露数据限制。",
    ),
    "missing_testable_forecast": (
        "有方向性的正式观点应声明对象、方向、验证窗口和失效条件，避免无法事后检验。",
        "适用于达到正式观点门槛的前瞻判断。",
        "证据不足而明确选择不形成方向观点时，不应强行制造预测。",
    ),
    "invalid_data_quality_hypothesis": (
        "数据质量问题只描述覆盖、新鲜度、口径和缺失；方向性解释必须进入主假设或替代假设。",
        "适用于数据缺口与市场解释同时存在的研究。",
        "数据异常本身直接改变数值可信度时，可以把它作为结论边界，但仍不能代替市场假设。",
    ),
    "missing_exact_market_evidence": (
        "正式市场主张必须打开记录级精确证据；概览、排名和历史聚合只能证明其明确返回的范围。",
        "适用于引用精确价格、资金、排名、宽度、日期或对象字段的正式事实主张。",
        "透明的确定性聚合可用自身证据定位符支持聚合结论，但承重的单条市场记录仍须能够独立审计。",
    ),
}


class ResearchMemoryConsolidationService:
    """Build auditable process and predictive memories under fixed gates."""

    def __init__(self, *, repository: AgentResearchRepository | None = None) -> None:
        self._repository = repository or AgentResearchRepository()

    def consolidate(self, *, now: datetime | None = None) -> dict[str, Any]:
        cutoff = now or datetime.now(UTC)
        quality = self._consolidate_quality_lessons(cutoff)
        predictive = self._consolidate_predictive_lessons(cutoff)
        return {
            "status": "completed",
            "quality_memories": quality,
            "predictive_memories": predictive,
            "consolidated_at": cutoff.isoformat(),
        }

    def _consolidate_quality_lessons(self, now: datetime) -> dict[str, int]:
        rows = self._repository.list_quality_memory_evidence(
            since=now - timedelta(days=90)
        )
        grouped: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            severity = {str(code): "advisory" for code in row["advisory_findings"]}
            severity.update({str(code): "hard" for code in row["hard_failures"]})
            for code, level in severity.items():
                if code in _QUALITY_LESSONS:
                    grouped[code].append({**row, "severity": level})

        promoted = candidates = 0
        for code, cases in grouped.items():
            cases = _unique_by(cases, "run_id")
            hard_count = sum(item["severity"] == "hard" for item in cases)
            should_promote = len(cases) >= 5 or (len(cases) >= 3 and hard_count >= 2)
            status = "promoted" if should_promote else "candidate"
            promoted += int(should_promote)
            candidates += int(not should_promote)
            summary, applicability, counterexample = _QUALITY_LESSONS[code]
            memory_id = _memory_id("quality", code)
            references = [
                quality_ref_from_run_id(item["run_id"])
                for item in cases[:20]
            ]
            self._repository.upsert_role_memory_with_cases(
                memory={
                    "memory_id": memory_id,
                    "role": "research",
                    "status": status,
                    "summary": summary,
                    "applicability": applicability,
                    "counterexample": counterexample,
                    "evidence_references": references,
                    "confidence": "high" if len(cases) >= 8 else "medium",
                    "scope": {
                        "memory_type": "process_quality",
                        "finding_code": code,
                        "sample_count": len(cases),
                        "hard_failure_count": hard_count,
                    },
                    "valid_from": min(item["evaluated_at"] for item in cases),
                    "expires_at": now + timedelta(days=180),
                    "version": 1,
                },
                cases=[
                    {
                        "case_id": _case_id(memory_id, item["run_id"]),
                        "memory_id": memory_id,
                        "role": "research",
                        "decision_ref": quality_ref_from_run_id(item["run_id"]),
                        "outcome_refs": [],
                        "context": {
                            "grade": item["grade"],
                            "overall_score": item["overall_score"],
                        },
                        "result": {
                            "finding_code": code,
                            "severity": item["severity"],
                            "improvement_actions": item["improvement_actions"][:5],
                        },
                    }
                    for item in cases[:50]
                ],
            )
        return {"promoted": promoted, "candidates": candidates}

    def _consolidate_predictive_lessons(self, now: datetime) -> dict[str, int]:
        rows = self._repository.list_outcome_memory_evidence(
            since=now - timedelta(days=365)
        )
        grouped: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            key = "|".join((
                _subject_family(row["subject_id"]),
                _metric_family(row["metric"]),
                row["expected_direction"],
            ))
            grouped[key].append(row)

        promoted = candidates = 0
        for key, cases in grouped.items():
            cases = _unique_by(cases, "forecast_id")
            successes = sum(
                item["status"] in {"confirmed", "partially_confirmed"}
                for item in cases
            )
            failures = sum(
                item["status"] in {"not_confirmed", "invalidated"}
                for item in cases
            )
            should_promote = len(cases) >= 3 and successes > 0 and failures > 0
            status = "promoted" if should_promote else "candidate"
            promoted += int(should_promote)
            candidates += int(not should_promote)
            subject_family, metric_family, direction = key.split("|", 2)
            memory_id = _memory_id("outcome", key)
            self._repository.upsert_role_memory_with_cases(
                memory={
                    "memory_id": memory_id,
                    "role": "research",
                    "status": status,
                    "summary": (
                        f"{subject_family} 的 {metric_family} {direction} 预测已有"
                        f"{len(cases)}个样本：{successes}个支持、{failures}个反例；"
                        "只能作为校准先验，当前结论仍须使用最新证据验证。"
                    ),
                    "applicability": (
                        f"仅适用于 {subject_family}、{metric_family}、"
                        f"预期方向 {direction} 的同口径预测。"
                    ),
                    "counterexample": (
                        f"现有样本中有{failures}个未确认或失效案例；必须打开案例"
                        "比较市场状态，不能只引用成功比例。"
                    ),
                    "evidence_references": [
                        item["evaluation_id"] for item in cases[:20]
                    ],
                    "confidence": "medium" if len(cases) < 8 else "high",
                    "scope": {
                        "memory_type": "predictive_outcome",
                        "subject_families": [subject_family],
                        "metric_families": [metric_family],
                        "expected_directions": [direction],
                        "sample_count": len(cases),
                        "support_count": successes,
                        "counterexample_count": failures,
                    },
                    "valid_from": min(item["evaluated_at"] for item in cases),
                    "expires_at": now + timedelta(days=180),
                    "version": 1,
                },
                cases=[
                    {
                        "case_id": _case_id(memory_id, item["forecast_id"]),
                        "memory_id": memory_id,
                        "role": "research",
                        "decision_ref": item["forecast_id"],
                        "outcome_refs": [item["evaluation_id"]],
                        "context": {
                            "subject_id": item["subject_id"],
                            "metric": item["metric"],
                            "expected_direction": item["expected_direction"],
                        },
                        "result": {
                            "status": item["status"],
                            "summary": item["summary"],
                        },
                    }
                    for item in cases[:50]
                ],
            )
        return {"promoted": promoted, "candidates": candidates}


def _memory_id(kind: str, key: str) -> str:
    digest = hashlib.blake2s(f"{kind}:{key}".encode(), digest_size=8).hexdigest()
    return f"RM_{digest}"


def _case_id(memory_id: str, source_id: str) -> str:
    digest = hashlib.blake2s(source_id.encode(), digest_size=8).hexdigest()
    return f"RMC_{memory_id.removeprefix('RM_')}_{digest}"


def _unique_by(rows: list[dict], key: str) -> list[dict]:
    return list({str(row[key]): row for row in rows}.values())


def _subject_family(subject_id: str) -> str:
    parts = str(subject_id).split(":")
    return ":".join(parts[:2]) if len(parts) >= 2 else str(subject_id)


def _metric_family(metric: str) -> str:
    value = str(metric).lower()
    if "相对" in value or "超额" in value or "relative" in value:
        return "relative_return"
    if "收益" in value or "涨跌" in value or "return" in value:
        return "return"
    if "close" in value or "收盘" in value:
        return "close"
    return value[:80]
