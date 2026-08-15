"""Compact MCP projections for relation-graph Agent tools."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo


_MARKET_PROJECTIONS = {
    "market_frame_open",
    "market_change_brief_open",
    "market_dimension_open",
    "market_global_overview_open",
    "market_sector_overview",
    "market_sector_rankings",
    "market_sector_compare_open",
    "market_sector_open",
    "market_instrument_history",
    "market_historical_analogue_open",
    "market_evidence_open",
    "market_technical_state_open",
    "market_expression_compare_open",
    "agent_evidence_ledger_open",
    "research_quality_list",
    "research_quality_open",
    "research_view_open",
    "research_current_report_open",
}
_GRAPH_PROJECTIONS = {
    "kg_relation_graph_search",
    "kg_card_open",
    "kg_card_expand",
    "kg_edge_open",
    "kg_community_open",
    "kg_community_expand",
}


def project_tool_result(
    tool_name: str,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the final model-facing MCP contract for one tool."""

    return _normalize_model_values(_project_tool_result_fields(tool_name, result))


def _project_tool_result_fields(
    tool_name: str,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """Remove storage diagnostics while preserving evidence and graph handles."""

    if tool_name in _MARKET_PROJECTIONS:
        return _project_market_result(tool_name, result)
    if tool_name not in _GRAPH_PROJECTIONS:
        return dict(result)

    projected: dict[str, Any] = _select(
        result,
        (
            "operation",
            "query",
            "seed_card_ids",
            "seed_community_ids",
            "truncated",
            "missing_card_ids",
            "missing_edge_ids",
            "missing_community_ids",
            "missing_summary_card_ids",
            "missing_focus_evidence_card_ids",
            "incident_relations_truncated",
            "next_operations",
        ),
    )
    if isinstance(result.get("cards"), list):
        projected["cards"] = [
            _project_card(
                card,
                include_evidence=tool_name == "kg_card_open",
            )
            for card in result["cards"]
            if isinstance(card, Mapping)
        ]
    if isinstance(result.get("edges"), list):
        projected["edges"] = [
            _project_edge(
                edge,
                include_evidence=tool_name == "kg_edge_open",
            )
            for edge in result["edges"]
            if isinstance(edge, Mapping)
        ]
    if (
        tool_name in {"kg_community_expand", "kg_community_open"}
        and isinstance(result.get("communities"), list)
    ):
        projected["communities"] = [
            _project_community(
                community,
                include_members=tool_name == "kg_community_open",
            )
            for community in result["communities"]
            if isinstance(community, Mapping)
        ]
    if isinstance(result.get("community_relations"), list):
        projected["community_relations"] = [
            _select(
                relation,
                (
                    "relation_id",
                    "source_community_id",
                    "target_community_id",
                    "relation_kind",
                    "edge_count",
                    "confidence",
                    "hop",
                ),
            )
            for relation in result["community_relations"]
            if isinstance(relation, Mapping)
        ]
    return projected


def _project_market_result(
    tool_name: str,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    if tool_name == "market_frame_open":
        if any(
            isinstance(item, Mapping) and "available_data_types" in item
            for item in result.get("dimensions") or []
        ):
            return dict(result)
        dimensions = []
        for item in result.get("dimensions") or []:
            if not isinstance(item, Mapping):
                continue
            dates = list(item.get("trade_dates") or [])
            data_types = [
                child.get("data_type")
                for child in item.get("data_types") or []
                if isinstance(child, Mapping) and child.get("data_type")
            ]
            dimension = _select(item, ("dimension",)) | {
                "latest_fact_time": item.get("as_of"),
                "latest_trade_dates": dates[:3],
                "available_data_types": data_types,
            }
            if item.get("trade_dates_truncated"):
                dimension["more_trade_dates_available"] = True
            if item.get("data_types_truncated"):
                dimension["more_data_types_available"] = True
            dimensions.append(_nonempty(dimension))
        return _nonempty(
            _select(
                result,
                (
                    "market", "market_session", "is_trading_day", "trade_date",
                    "previous_trade_date",
                ),
            ) | {
                "data_quality_flags": [
                    _nonempty({
                        "code": item.get("issue_code"),
                        "severity": item.get("severity"),
                        "affected_trade_dates": item.get("affected_trade_dates"),
                        "affected_dimensions": item.get("affected_dimensions"),
                    })
                    for item in result.get("quality_issues") or []
                    if isinstance(item, Mapping)
                ],
                "dimensions": dimensions,
            }
        )
    if tool_name == "market_change_brief_open":
        changes = []
        for item in result.get("significant_changes") or []:
            if not isinstance(item, Mapping) or item.get("metric") == "volume_ratio":
                continue
            changes.append(_select(item, (
                "dimension", "subject_id", "metric", "unit",
                "current_value", "baseline_value", "percent_change",
                "current_as_of", "baseline_as_of",
                "current_evidence_locator", "baseline_evidence_locator",
            )))
        return _nonempty({
            "market_session": result.get("market_session"),
            "as_of": result.get("as_of"),
            "significant_changes": changes,
        })
    if tool_name == "market_dimension_open":
        if any(
            isinstance(item, Mapping) and "values" in item
            for item in result.get("facts") or []
        ):
            return dict(result)
        return _nonempty(
            _select(result, ("dimension", "total", "truncated"))
            | {"facts": [_project_market_fact(item) for item in result.get("facts") or []]}
        )
    if tool_name == "market_global_overview_open":
        if any(
            isinstance(item, Mapping) and "values" in item
            for item in result.get("other_global_facts") or []
        ):
            return dict(result)
        facts = []
        for item in result.get("other_global_facts") or []:
            if not isinstance(item, Mapping):
                continue
            preview = _project_preview(item.get("data_preview") or {})
            if preview:
                facts.append(_project_market_fact(dict(item) | {"data_preview": preview}))
        us_market = result.get("us_market")
        if isinstance(us_market, Mapping):
            us_market = _nonempty({
                "trade_date": us_market.get("trade_date"),
                "indices": [
                    _select(item, ("code", "name", "latest", "change_pct"))
                    for item in us_market.get("indices") or []
                    if isinstance(item, Mapping)
                ],
                "indices_evidence_locator": us_market.get("indices_evidence_locator"),
                "breadth": us_market.get("breadth"),
                "breadth_evidence_locator": us_market.get("breadth_evidence_locator"),
                "leading_industries": us_market.get("leading_industries"),
                "leading_industries_evidence_locator": us_market.get("leading_industries_evidence_locator"),
                "leading_concepts": us_market.get("leading_concepts"),
                "leading_concepts_evidence_locator": us_market.get("leading_concepts_evidence_locator"),
            })
        return _nonempty(
            _select(result, ("us_indices", "us_breadth", "evidence"))
            | {
                "us_market": us_market,
                "other_global_facts": facts,
                "other_global_truncated": result.get("other_global_truncated") or None,
            }
        )
    if tool_name == "market_evidence_open":
        if "data" in result or "data_type" in result:
            return dict(result)
        record = result.get("record")
        record = record if isinstance(record, Mapping) else {}
        identity = result.get("identity")
        identity = identity if isinstance(identity, Mapping) else {}
        return _nonempty({
            "status": result.get("status"),
            "reason": result.get("reason") or result.get("error"),
            "evidence_locator": result.get("evidence_locator"),
            "data_type": record.get("data_type") or identity.get("data_type"),
            "subject_id": record.get("subject_id") or identity.get("subject_id"),
            "provider": record.get("provider") or identity.get("provider"),
            "trade_date": record.get("trade_date"),
            "fact_time": record.get("observed_at") or identity.get("fact_time"),
            "freshness": record.get("freshness_status"),
            "data": _project_preview(record.get("data") or {}),
            "values": result.get("values"),
            "missing_fields": result.get("missing_fields"),
        })
    if tool_name == "market_sector_overview":
        return _nonempty({
            key: [
                projected
                for item in (result.get(key) or [])[:8]
                if (projected := _project_sector_signal(item))
            ]
            for key in ("fact_highlights", "provider_signal_highlights")
        })
    if tool_name == "market_sector_rankings":
        return _nonempty(
            _select(result, ("data_type", "metric", "total", "offset"))
            | {"items": [
                projected
                for item in result.get("items") or []
                if (projected := _project_sector_signal(item))
            ]}
        )
    if tool_name == "market_instrument_history":
        if "bars" in result:
            return dict(result)
        return _project_market_history(result)
    if tool_name == "market_historical_analogue_open":
        if "full_sample_distribution" in result:
            return dict(result)
        return _project_historical_analogue(result)
    if tool_name == "market_sector_compare_open":
        return _project_sector_comparison(result)
    if tool_name == "market_sector_open":
        if "candidates" in result or "latest_signals" in result:
            return dict(result)
        latest = result.get("latest") or []
        first = next((item for item in latest if isinstance(item, Mapping)), {})
        return _nonempty({
            "provider_sector_code": result.get("provider_sector_code"),
            "sector_name": first.get("sector_name"),
            "sector_type": result.get("sector_type") or first.get("sector_type"),
            "found": result.get("found"),
            "latest_signals": [
                projected
                for item in latest if isinstance(item, Mapping)
                if (projected := _project_sector_signal(item, identity=False))
            ],
            "representative_etf": result.get("representative_etf"),
            "constituent_count": result.get("constituent_count"),
            "top_gainers": result.get("top_gainers"),
            "top_losers": result.get("top_losers"),
            "constituents_truncated": result.get("constituents_truncated"),
        })
    if tool_name == "agent_evidence_ledger_open":
        if "opened_reference_counts" in result:
            return dict(result)
        grouped: dict[str, list[str]] = {}
        for entry in result.get("entries") or []:
            if not isinstance(entry, Mapping) or not entry.get("tool_name"):
                continue
            bucket = grouped.setdefault(str(entry["tool_name"]), [])
            for reference in entry.get("evidence_refs") or []:
                if isinstance(reference, str) and reference not in bucket:
                    bucket.append(reference)
        exact_tools = {
            "market_evidence_open", "kg_card_open", "kg_edge_open",
            "external_web_read", "external_content_read", "external_repo_read",
        }
        return {
            "opened_reference_counts": {
                tool: len(references) for tool, references in grouped.items()
            },
            "exact_opened_references": {
                tool: references
                for tool, references in grouped.items()
                if tool in exact_tools
            },
            "tool_count": len(grouped),
            "reference_count": sum(len(items) for items in grouped.values()),
        }
    if tool_name == "market_technical_state_open":
        windows = {}
        for name, item in (result.get("windows") or {}).items():
            if not isinstance(item, Mapping):
                continue
            windows[name] = _nonempty({
                "intraday_high": item.get("high"),
                "intraday_high_trade_date": item.get("high_trade_date"),
                "intraday_low": item.get("low"),
                "intraday_low_trade_date": item.get("low_trade_date"),
                "close_distance_to_intraday_high_pct": item.get("distance_to_high_pct"),
                "close_distance_from_intraday_low_pct": item.get("distance_from_low_pct"),
                "close_return_pct": item.get("return_pct"),
                "position_state": item.get("position_state"),
                "intraday_high_evidence_locator": item.get("high_evidence_locator"),
                "intraday_low_evidence_locator": item.get("low_evidence_locator"),
            })
        recent = result.get("recent_swing")
        if isinstance(recent, Mapping):
            recent = _nonempty({
                "rule": recent.get("rule"),
                "swing_high": recent.get("high"),
                "swing_low": recent.get("low"),
            })
        return _nonempty({
            **_select(result, (
                "subject_id", "benchmark_subject_id", "latest_trade_date",
                "latest_close", "available_bars", "relative_strength",
                "evidence_locators",
            )),
            "price_basis": "窗口高低点使用K线盘中最高/最低价；区间收益使用收盘价",
            "windows": windows,
            "recent_swing": recent,
            "drawdown_from_intraday_peak_pct": result.get("peak_drawdown_pct"),
        })
    if tool_name == "market_expression_compare_open":
        return _nonempty(_select(result, (
            "expressions", "pairwise_holding_overlap", "comparison_limits",
            "evidence_locators",
        )))
    if tool_name == "research_quality_list":
        return _nonempty({
            "evaluations": [
                _select(item, (
                    "evaluation_id", "overall_score", "outcome_adjusted_score",
                    "grade", "passed", "hard_failures", "advisory_findings",
                ))
                for item in result.get("evaluations") or []
                if isinstance(item, Mapping)
            ]
        })
    if tool_name == "research_quality_open":
        item = result.get("evaluation")
        if not isinstance(item, Mapping):
            return _select(result, ("status",))
        if "semantic_summary" in item:
            return dict(result)
        semantic = item.get("semantic_evaluation")
        semantic = semantic if isinstance(semantic, Mapping) else {}
        return {"evaluation": _nonempty(
            _select(item, (
                "evaluation_id", "overall_score", "outcome_adjusted_score",
                "grade", "passed", "hard_failures", "advisory_findings",
                "scores", "improvement_actions", "tool_coverage",
                "evidence_reference_count",
            ))
            | {
                "semantic_summary": _select(semantic, (
                    "overall_assessment", "scores", "critical_findings",
                    "improvement_actions", "unsupported_claims",
                ))
            }
        )}
    if tool_name == "research_view_open":
        view = result.get("view")
        if not isinstance(view, Mapping):
            return _select(result, ("status",))
        return {"view": _project_research_view(view)}
    if tool_name == "research_current_report_open":
        report = result.get("report")
        if not isinstance(report, Mapping):
            return _select(result, ("status",))
        return {"report": _project_current_research_report(report)}
    return dict(result)


_PREVIEW_INTERNAL_FIELDS = {
    "summary", "indicator_key", "response_type", "stream_sequence",
    "status_code", "_field_count", "_item_count", "_omitted_field_count",
    "volume_ratio",
}


def _project_preview(value: Any) -> Any:
    if isinstance(value, list):
        return [_project_preview(item) for item in value]
    if not isinstance(value, Mapping):
        return value
    return {
        key: _project_preview(child)
        for key, child in value.items()
        if key not in _PREVIEW_INTERNAL_FIELDS
    }


def _project_market_fact(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    return _nonempty({
        "data_type": value.get("data_type"),
        "subject_id": value.get("subject_id"),
        "market": value.get("market"),
        "provider": value.get("provider"),
        "trade_date": value.get("trade_date"),
        "fact_time": value.get("observed_at") or value.get("bucket_at"),
        "freshness": value.get("freshness_status"),
        "values": _project_preview(value.get("data_preview") or {}),
        "evidence_locator": value.get("evidence_locator"),
    })


_SECTOR_FIELDS = (
    "subject_id", "provider_sector_code", "sector_name", "sector_type",
    "metric", "metric_value", "rank", "change_pct", "main_net_inflow",
    "limit_up_count", "heat_rank", "heat_score", "representative_etf_code",
    "representative_etf_name", "trade_date", "source_date", "evidence_locator",
)


def _project_sector_signal(value: Any, *, identity: bool = True) -> Any:
    if not isinstance(value, Mapping):
        return value
    if value.get("metric") == "volume_ratio":
        return {}
    hidden = set() if identity else {
        "subject_id", "provider_sector_code", "sector_name", "sector_type"
    }
    return _select(value, (field for field in _SECTOR_FIELDS if field not in hidden))


def _project_sector_comparison(result: Mapping[str, Any]) -> dict[str, Any]:
    candidates = result.get("candidates") or []
    if result.get("provider_sector_code") and result.get("latest"):
        candidates = [result]
    compact = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        signals = candidate.get("latest_signals") or candidate.get("latest") or []
        first = next((item for item in signals if isinstance(item, Mapping)), {})
        projected_signals = []
        for item in signals:
            if not isinstance(item, Mapping):
                continue
            if any(key in item for key in (
                "metric", "metric_value", "main_net_inflow", "change_pct",
                "limit_up_count", "heat_rank", "heat_score",
            )):
                projected_signals.append(_project_sector_signal(item, identity=False))
            elif item.get("trade_date") and item.get("evidence_locator"):
                projected_signals.append({
                    "trade_date": item["trade_date"],
                    "evidence_locator": item["evidence_locator"],
                    "comparison_role": "baseline_identity_only",
                    "citation_ready": False,
                    "required_action": "market_evidence_open",
                })
        compact.append(_nonempty({
            "subject_id": candidate.get("subject_id") or first.get("subject_id"),
            "provider_sector_code": candidate.get("provider_sector_code")
            or first.get("provider_sector_code"),
            "sector_name": candidate.get("sector_name") or first.get("sector_name"),
            "sector_type": candidate.get("sector_type") or first.get("sector_type"),
            "found": candidate.get("found"),
            "latest_signals": projected_signals,
            "constituent_breadth": candidate.get("constituent_breadth"),
        }))
    return {"candidate_count": result.get("candidate_count", len(compact)), "candidates": compact}


def _project_market_history(result: Mapping[str, Any]) -> dict[str, Any]:
    bars = []
    for item in result.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        data = item.get("data") if isinstance(item.get("data"), Mapping) else {}
        bars.append([
            item.get("trade_date") or data.get("date"),
            *[_compact_number(data.get(key)) for key in ("open", "high", "low", "close", "volume")],
        ])
    closes = [float(item[4]) for item in bars if isinstance(item[4], (int, float))]
    statistics: dict[str, Any] = {}
    if closes:
        statistics["latest_close"] = _compact_number(closes[0])
        for window in (5, 20, 60, 120):
            if len(closes) < window:
                continue
            sample = closes[:window]
            baseline = sample[-1]
            statistics[f"return_{window}_bars_pct"] = _compact_number(
                (sample[0] / baseline - 1) * 100 if baseline else None
            )
            statistics[f"up_transitions_within_{window}_bars"] = sum(
                newer > older for newer, older in zip(sample[:-1], sample[1:], strict=False)
            )
            statistics[f"down_transitions_within_{window}_bars"] = sum(
                newer < older for newer, older in zip(sample[:-1], sample[1:], strict=False)
            )
            sample_bars = bars[:window]
            close_high = max(sample_bars, key=lambda item: float(item[4]))
            close_low = min(sample_bars, key=lambda item: float(item[4]))
            statistics[f"close_high_{window}_bars"] = [close_high[0], close_high[4]]
            statistics[f"close_low_{window}_bars"] = [close_low[0], close_low[4]]
            statistics[f"drawdown_from_close_high_{window}_bars_pct"] = _compact_number(
                (sample_bars[0][4] / close_high[4] - 1) * 100 if close_high[4] else None
            )
            high_bars = [item for item in sample_bars if isinstance(item[2], (int, float))]
            if high_bars:
                intraday_high = max(high_bars, key=lambda item: float(item[2]))
                statistics[f"intraday_high_{window}_bars"] = [
                    intraday_high[0], intraday_high[2]
                ]
                statistics[f"drawdown_from_intraday_high_{window}_bars_pct"] = _compact_number(
                    (sample_bars[0][4] / intraday_high[2] - 1) * 100
                    if intraday_high[2] else None
                )
    return _nonempty({
        "code": result.get("code"),
        "data_type": result.get("data_type"),
        "bar_count": len(bars),
        "order": "newest_first",
        "bar_fields": ["trade_date", "open", "high", "low", "close", "volume_raw"],
        "bars": bars[:10],
        "bars_truncated": len(bars) > 10,
        "window_statistics": statistics,
        "window_evidence": result.get("window_evidence"),
        "series_semantics": result.get("series_semantics"),
    })


def _project_historical_analogue(result: Mapping[str, Any]) -> dict[str, Any]:
    signal = result.get("signal_definition")
    signal = signal if isinstance(signal, Mapping) else {}
    robustness = result.get("robustness")
    robustness = robustness if isinstance(robustness, Mapping) else {}
    leakage_controls = robustness.get("leakage_controls")
    leakage_controls = (
        leakage_controls if isinstance(leakage_controls, Mapping) else {}
    )
    temporal = robustness.get("temporal_holdout")
    relative = robustness.get("relative_temporal_holdout")
    sensitivity = [
        _select(item, (
            "match_distance_threshold", "sample_count", "median_return_pct",
            "positive_share",
        ))
        for item in robustness.get("threshold_sensitivity") or []
        if isinstance(item, Mapping)
    ]
    return _nonempty({
        "subject_id": result.get("subject_id"),
        "benchmark_subject_id": result.get("benchmark_subject_id"),
        "signal_definition": _select(
            signal, ("features", "match_distance_threshold", "current_signal")
        ),
        "forward_window_bars": result.get("forward_window_bars"),
        "sample_count": result.get("sample_count"),
        "minimum_sample_count": result.get("minimum_sample_count"),
        "calibration_status": result.get("calibration_status"),
        "full_sample_distribution": result.get("statistics"),
        "calibration_readout": _nonempty({
            "absolute_return": _holdout_summary(temporal),
            "relative_to_benchmark": _holdout_summary(relative),
        }),
        "distribution_stability_readout": _distribution_stability(
            result.get("statistics"), robustness
        ),
        "robustness": _nonempty({
            # Only the three boolean safeguards determine whether the
            # calculation is leakage-safe.  The same source object also
            # contains an integer gap and explanatory text; treating every
            # value as a boolean made every real result appear unsafe.
            "leakage_safe": all(
                leakage_controls.get(key) is True
                for key in (
                    "point_in_time_features_only",
                    "future_bars_excluded_from_signal",
                    "non_overlapping_forward_windows",
                )
            ) if leakage_controls else None,
            "threshold_sensitivity": sensitivity,
            "strict_distance_threshold": robustness.get("strict_distance_threshold"),
            "strict_sample_count": robustness.get("strict_sample_count"),
            "wide_match_share": robustness.get("wide_match_share"),
        }),
        "analysis_evidence_locator": result.get("analysis_evidence_locator"),
    })


def _holdout_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    development = value.get("development_statistics")
    holdout = value.get("holdout_statistics")
    development = development if isinstance(development, Mapping) else {}
    holdout = holdout if isinstance(holdout, Mapping) else {}
    return _nonempty({
        "development_median_return_pct": development.get("median_return_pct"),
        "holdout_median_return_pct": holdout.get("median_return_pct"),
        "development_positive_share": development.get("positive_share"),
        "holdout_positive_share": holdout.get("positive_share"),
        "median_direction_consistent": value.get("median_direction_consistent"),
        "validation_status": value.get("validation_status"),
    })


def _distribution_stability(statistics: Any, robustness: Mapping[str, Any]) -> dict[str, Any] | None:
    if not isinstance(statistics, Mapping):
        return None
    strict = robustness.get("strict_statistics")
    trimmed = robustness.get("trimmed_one_each_tail_statistics")
    strict = strict if isinstance(strict, Mapping) else {}
    trimmed = trimmed if isinstance(trimmed, Mapping) else {}
    full_median = statistics.get("median_return_pct")
    strict_median = strict.get("median_return_pct")
    def sign(value: Any) -> int | None:
        if not isinstance(value, (int, float)):
            return None
        return 1 if value > 0 else -1 if value < 0 else 0
    return _nonempty({
        "full_sample_median_return_pct": full_median,
        "strict_subset_median_return_pct": strict_median,
        "trimmed_sample_median_return_pct": trimmed.get("median_return_pct"),
        "strict_vs_full_direction_conflict": (
            sign(full_median) is not None
            and sign(strict_median) is not None
            and sign(full_median) != sign(strict_median)
        ),
    })


def _compact_number(value: Any) -> Any:
    if not isinstance(value, (int, float)):
        return value
    rounded = round(float(value), 4)
    return int(rounded) if rounded.is_integer() else rounded


def _project_research_view(view: Mapping[str, Any]) -> dict[str, Any]:
    confidence = view.get("confidence")
    if isinstance(confidence, Mapping):
        confidence = _select(confidence, ("overall", "rationale"))
    market_structure = view.get("market_structure")
    if isinstance(market_structure, Mapping):
        market_structure = _select(market_structure, (
            "breadth", "leadership_concentration", "crowding_and_reversal_risk",
            "persistence_assessment", "pricing_state",
        ))
    mechanism_chain = [
        _select(link, (
            "link_id", "cause", "mechanism", "effect", "status",
            "invalidation_condition",
        ))
        for link in view.get("mechanism_chain") or []
        if isinstance(link, Mapping)
    ]
    return _nonempty({
        **_select(view, (
            "view_id", "revision_id", "title", "status", "event", "scope",
            "thesis", "valid_until", "hypotheses", "decision_boundary",
            "invalidation_conditions", "forecasts",
        )),
        "confidence": confidence,
        "mechanism_chain": mechanism_chain,
        "market_structure": market_structure,
    })


def _project_current_research_report(report: Mapping[str, Any]) -> dict[str, Any]:
    return _nonempty({
        "report_id": report.get("report_id"),
        "report_revision_id": report.get("proposed_report_revision_id")
        or report.get("report_revision_id"),
        "status": report.get("status"),
        "research_question": report.get("research_question"),
        "active_views": [
            _project_research_view(item)
            for item in report.get("active_views") or []
            if isinstance(item, Mapping)
        ],
        "observation_requirements": report.get("observation_requirements"),
        "evidence_gaps": report.get("evidence_gaps"),
        "no_change_reason": report.get("no_change_reason"),
    })


def _nonempty(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: child
        for key, child in value.items()
        if child not in (None, "", [], {})
    }


_CHINA_TIMEZONE = ZoneInfo("Asia/Shanghai")
_SERVER_ONLY_FIELDS = {
    "created_at", "updated_at", "collected_at", "fetched_at", "ingested_at",
    "source_latency_seconds", "snapshot_id", "payload_hash", "version",
}
_FACT_TIME_FIELDS = {"as_of", "fact_time", "current_as_of", "baseline_as_of", "observed_at"}


def _normalize_model_values(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, datetime):
        if value.tzinfo is not None and value.utcoffset() is not None:
            value = value.astimezone(_CHINA_TIMEZONE)
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, (float, Decimal)):
        rounded = round(float(value), 4)
        return int(rounded) if rounded.is_integer() else rounded
    if isinstance(value, str):
        return _compact_timestamp(value)
    if isinstance(value, list):
        return [_normalize_model_values(item) for item in value]
    if not isinstance(value, Mapping):
        return value
    normalized = {
        key: _normalize_model_values(child)
        for key, child in value.items()
        if key not in _SERVER_ONLY_FIELDS
    }
    for field, items in list(normalized.items()):
        if not isinstance(items, list) or len(items) < 2:
            continue
        if not all(isinstance(item, dict) for item in items):
            continue
        shared = {}
        for time_field in _FACT_TIME_FIELDS:
            times = [item.get(time_field) for item in items]
            if times[0] is not None and all(item == times[0] for item in times):
                shared[time_field] = times[0]
        if shared:
            for item in items:
                for time_field in shared:
                    item.pop(time_field, None)
            normalized[f"{field}_shared_time"] = shared
    return normalized


def _compact_timestamp(value: str) -> str:
    if "T" not in value:
        return value
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is not None and parsed.utcoffset() is not None:
        parsed = parsed.astimezone(_CHINA_TIMEZONE)
    return parsed.strftime("%Y-%m-%d %H:%M")


def _project_card(
    card: Mapping[str, Any],
    *,
    include_evidence: bool,
) -> dict[str, Any]:
    fields = [
        "card_id",
        "fact_id",
        "summary",
        "source_id",
        "source_published_at",
        "relation_ids",
        "hop",
        "fact_card_count",
    ]
    if include_evidence:
        fields.extend(
            (
                "focus_evidence",
                "evidence_id",
                "primary_chunk_id",
                "community_ids",
            )
        )
    return _select(card, fields)


def _project_edge(
    edge: Mapping[str, Any],
    *,
    include_evidence: bool,
) -> dict[str, Any]:
    fields = [
        "edge_id",
        "source_card_id",
        "target_card_id",
        "relation_kind",
        "relation_type",
        "direction",
        "decision_class",
        "confidence",
    ]
    if include_evidence:
        fields.extend(("basis", "inference_mechanism"))
    projected = _select(edge, fields)
    if include_evidence:
        for endpoint_name in ("source_card", "target_card"):
            endpoint = edge.get(endpoint_name)
            if isinstance(endpoint, Mapping):
                projected[endpoint_name] = _select(
                    endpoint,
                    (
                        "card_id",
                        "fact_id",
                        "summary",
                        "focus_evidence",
                        "source_id",
                        "source_published_at",
                    ),
                )
    return projected


def _project_community(
    community: Mapping[str, Any],
    *,
    include_members: bool,
) -> dict[str, Any]:
    projected = _select(
        community,
        (
            "community_id",
            "title",
            "representative_summary",
            "identity_anchor_card_id",
            "card_count",
            "edge_count",
            "graph_version",
            "graph_changed_at",
            "hop",
            "members_truncated",
            "edges_truncated",
        ),
    )
    if include_members:
        projected["members"] = [
            _select(
                member,
                (
                    "card_id",
                    "summary",
                    "source_id",
                    "source_published_at",
                ),
            )
            for member in community.get("members", [])
            if isinstance(member, Mapping)
        ]
        projected["edges"] = [
            _project_edge(edge, include_evidence=False)
            for edge in community.get("edges", [])
            if isinstance(edge, Mapping)
        ]
    return projected


def _select(
    value: Mapping[str, Any],
    fields: Iterable[str],
) -> dict[str, Any]:
    return {
        key: value[key]
        for key in fields
        if key in value and value[key] not in (None, "", [], {})
    }
