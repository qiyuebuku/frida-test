"""Pure financial Raw Row -> Source Record projection helpers."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from src.domain.knowledge_adapters.financial.source_classification import (
    classify_news_source_type,
)

_A_SHARE_RE = re.compile(r"(?<!\d)(?:[036]\d{5})(?!\d)")

PROJECTION_RULE_VERSION = "financial_source_projection_v2"

_COMMON_VALUE_KEYS = [
    "net_flow",
    "net_amount",
    "net_amt",
    "main_net_inflow",
    "buy_amt",
    "sell_amt",
    "amount",
    "value",
    "score",
    "sentiment_score",
    "market_temperature",
    "temperature",
    "hot",
    "count",
    "total",
    "close",
    "price",
    "latest",
    "change_rate",
    "changeRate",
    "percent",
    "chg",
    "rate",
    "index_value",
    "dealAmt",
    "DEAL_AMT",
]

_VALUE_PATHS: dict[tuple[str, str], list[str]] = {
    ("ft_market_flow", "northbound"): ["net_flow", "raw.DEAL_AMT"],
    ("ft_market_flow", "sector_flow"): ["net_amount"],
    ("ft_market_flow", "stock_flow"): ["net_amount", "main_net_inflow", "net_flow"],
    ("ft_market_flow", "dragon_tiger"): ["net_amt", "net_amount", "buy_amt", "change_rate", "chg"],
    ("ft_market_cache", "sector_ranking"): ["data.total", "data.topRise[].changeRate.avg", "data.topFall[].changeRate.avg"],
    ("ft_market_cache", "market_environment"): ["data.indices[].changeRate.avg", "data.margin.latest.rzye", "data.northbound.latest.dealAmt", "data.bond.devMa20"],
    ("ft_market_cache", "market_overview"): ["data.limit_up.total", "data.main_fund.net_amount", "data.indices[].changeRate.avg"],
    ("ft_market_cache", "global_index"): ["data.indices[].changeRate.avg", "data.count"],
    ("ft_market_cache", "forex"): ["data.items[].changeRate.avg", "data.items[].price.avg", "data.count"],
    ("ft_market_cache", "futures_intl"): ["data.items[].changeRate.avg", "data.items[].price.avg", "data.count"],
    ("ft_market_cache", "futures_domestic"): ["data.items[].changeRate.avg", "data.items[].price.avg", "data.count"],
    ("ft_sentiment", "guba_posts"): ["count", "posts[].reads.sum", "posts[].replies.sum"],
    ("ft_sentiment", "guba_popularity"): ["[].rc.sum", "[].hisRc.sum", "[].count"],
    ("ft_sentiment", "limit_pool"): ["limit_up_count.today.num", "limit_down_count.today.num", "info[].change_rate.avg"],
    ("ft_sentiment", "xueqiu_hot_stocks"): ["[].percent.avg", "[].chg.sum", "[].count"],
    ("ft_sentiment", "xueqiu_hot_topics"): ["[].discussions.sum", "[].hot.sum", "[].count"],
}


def project_ft_news_row(
    row: dict[str, Any],
    *,
    stock_names: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    observed_at = _iso(row.get("published_at") or row.get("created_at"))
    source_pk = row.get("id")
    if not source_pk or not observed_at:
        return None

    source_id = f"ft_news:{source_pk}"
    classification = classify_news_source_type(row)
    raw_text = _news_raw_text(row)
    title = row.get("title") or source_id
    tags = _clean_tags(row.get("tags"))
    related_stocks = _as_list(row.get("related_stocks"))
    mentioned_entities = []
    mentioned_entities.extend(
        entity
        for item in related_stocks
        if (entity := _stock_entity(item, stock_names or {})) is not None
    )
    payload = {
        "source_id": source_id,
        "document_id": source_id,
        "published_at": observed_at,
        "title": title,
        "text": raw_text,
        "source_name": row.get("source_name") or row.get("source") or "ft_news",
        "mentioned_entities": _unique_entities(mentioned_entities),
        "affected_entities": [],
    }
    return {
        "source_type": classification.source_type,
        "source_id": source_id,
        "observed_at": observed_at,
        "payload": payload,
        "raw_text": raw_text,
        "metadata": {
            "source_table": "ft_news",
            "source_pk": source_pk,
            "source": row.get("source"),
            "source_name": row.get("source_name"),
            "source_reliability": row.get("source_reliability"),
            "category": row.get("category"),
            "url": row.get("url"),
            "tags": tags,
            "weak_entity_hints": _weak_tag_hints(tags, title=title, raw_text=raw_text),
            "related_stocks": related_stocks,
            "fingerprint": row.get("fingerprint"),
            **classification.to_metadata(),
        },
    }


def project_ft_market_flow_row(row: dict[str, Any]) -> dict[str, Any] | None:
    source_pk = row.get("id")
    data_type = str(row.get("data_type") or "").strip()
    observed_at = _iso(row.get("trade_date") or row.get("created_at"))
    data = _normalize_signal_data(row.get("data"))
    if not source_pk or not data_type or not observed_at:
        return None
    target_ref = _target_ref_from_market_data(data) or _default_signal_target("market_flow", data_type)
    value, value_path = _signal_value(data, source_table="ft_market_flow", data_type=data_type)
    if target_ref is None or value is None:
        return None
    source_id = f"ft_market_flow:{source_pk}"
    return _derived_signal_record(
        source_id=source_id,
        observed_at=observed_at,
        source_table="ft_market_flow",
        source_pk=source_pk,
        signal_source_type=f"market_flow.{data_type}",
        target_ref=target_ref,
        signal_type=f"market_flow.{data_type}",
        value=value,
        data=data,
        metadata={"data_type": data_type, "trade_date": _iso(row.get("trade_date")), "value_path": value_path},
    )


def project_ft_market_cache_row(row: dict[str, Any]) -> dict[str, Any] | None:
    source_pk = row.get("id")
    data_type = str(row.get("data_type") or "").strip()
    observed_at = _iso(row.get("created_at") or row.get("expires_at"))
    data = _normalize_signal_data(row.get("data"))
    if not source_pk or not data_type or not observed_at:
        return None
    value, value_path = _signal_value(data, source_table="ft_market_cache", data_type=data_type)
    if value is None:
        return None
    source_id = f"ft_market_cache:{data_type}:{source_pk}"
    return _derived_signal_record(
        source_id=source_id,
        observed_at=observed_at,
        source_table="ft_market_cache",
        source_pk=source_pk,
        signal_source_type=f"market_snapshot.{data_type}",
        target_ref=_target_ref_from_market_data(data) or _default_signal_target("market_snapshot", data_type),
        signal_type=f"market_snapshot.{data_type}",
        value=value,
        data=data,
        metadata={
            "data_type": data_type,
            "created_at": _iso(row.get("created_at")),
            "expires_at": _iso(row.get("expires_at")),
            "value_path": value_path,
        },
    )


def project_ft_sentiment_row(row: dict[str, Any]) -> dict[str, Any] | None:
    source_pk = row.get("id")
    data_type = str(row.get("data_type") or "").strip()
    observed_at = _iso(row.get("trade_date") or row.get("created_at"))
    data = _normalize_signal_data(row.get("data"))
    if not source_pk or not data_type or not observed_at:
        return None
    value, value_path = _signal_value(data, source_table="ft_sentiment", data_type=data_type)
    if value is None:
        return None
    source_id = f"ft_sentiment:{source_pk}"
    return _derived_signal_record(
        source_id=source_id,
        observed_at=observed_at,
        source_table="ft_sentiment",
        source_pk=source_pk,
        signal_source_type=f"sentiment.{data_type}",
        target_ref=_target_ref_from_market_data(data) or _default_signal_target("sentiment", data_type),
        signal_type=f"sentiment.{data_type}",
        value=value,
        data=data,
        metadata={"data_type": data_type, "trade_date": _iso(row.get("trade_date")), "value_path": value_path},
    )


def project_ft_macro_indicator_row(row: dict[str, Any]) -> dict[str, Any] | None:
    source_pk = row.get("id")
    indicator = str(row.get("indicator") or "").strip()
    period = str(row.get("period") or "").strip()
    value = _to_number(row.get("value"))
    observed_at = _iso(row.get("published_at") or row.get("created_at"))
    if not source_pk or not indicator or not period or value is None or not observed_at:
        return None
    source = str(row.get("source") or "unknown")
    source_id = f"ft_macro_indicators:{indicator}:{period}:{source}"
    return _derived_signal_record(
        source_id=source_id,
        observed_at=observed_at,
        source_table="ft_macro_indicators",
        source_pk=source_pk,
        signal_source_type="macro_indicator",
        target_ref=f"macro_indicator:{indicator}",
        signal_type=f"macro_indicator.{indicator}",
        value=value,
        data={
            "indicator": indicator,
            "period": period,
            "prev_value": row.get("prev_value"),
            "yoy": row.get("yoy"),
            "mom": row.get("mom"),
        },
        metadata={
            "indicator": indicator,
            "period": period,
            "source": source,
            "published_at": _iso(row.get("published_at")),
            "dim_tag": row.get("dim_tag"),
            "value_path": "value",
        },
        payload_extra={
            "unit": row.get("unit"),
            "prev_value": row.get("prev_value"),
            "yoy": row.get("yoy"),
            "mom": row.get("mom"),
            "dim_tag": row.get("dim_tag"),
        },
    )


def _derived_signal_record(
    *,
    source_id: str,
    observed_at: str,
    source_table: str,
    source_pk: Any,
    signal_source_type: str,
    target_ref: str | dict[str, Any],
    signal_type: str,
    value: Any,
    data: dict[str, Any],
    metadata: dict[str, Any],
    payload_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "source_id": source_id,
        "signal_id": source_id,
        "target_ref": target_ref,
        "signal_type": signal_type,
        "observed_at": observed_at,
        "value": value,
        "data": data,
    }
    if payload_extra:
        payload.update({key: item for key, item in payload_extra.items() if item is not None})
    return {
        "source_type": "derived_signal",
        "source_id": source_id,
        "observed_at": observed_at,
        "payload": payload,
        "raw_text": None,
        "metadata": {
            "source_table": source_table,
            "source_pk": source_pk,
            "signal_source_type": signal_source_type,
            "projection_rule_version": PROJECTION_RULE_VERSION,
            "data_shape": _data_shape(data),
            **metadata,
        },
    }


def explain_projection_skip(source: str, row: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic skip reason for projection coverage reports."""

    if source == "ft_news":
        if not row.get("id"):
            return {"reason": "missing_source_pk"}
        if not _iso(row.get("published_at") or row.get("created_at")):
            return {"reason": "missing_observed_at"}
        return {"reason": "unknown_news_projection_failure"}

    if source == "ft_macro_indicators":
        if not row.get("id"):
            return {"reason": "missing_source_pk"}
        if not str(row.get("indicator") or "").strip():
            return {"reason": "missing_indicator"}
        if not str(row.get("period") or "").strip():
            return {"reason": "missing_period"}
        if _to_number(row.get("value")) is None:
            return {"reason": "missing_value"}
        if not _iso(row.get("published_at") or row.get("created_at")):
            return {"reason": "missing_observed_at"}
        return {"reason": "unknown_macro_projection_failure"}

    if source in {"ft_market_flow", "ft_market_cache", "ft_sentiment"}:
        if not row.get("id"):
            return {"reason": "missing_source_pk"}
        data_type = str(row.get("data_type") or "").strip()
        if not data_type:
            return {"reason": "missing_data_type"}
        observed_at_field = row.get("trade_date") or row.get("created_at")
        if source == "ft_market_cache":
            observed_at_field = row.get("created_at") or row.get("expires_at")
        if not _iso(observed_at_field):
            return {"reason": "missing_observed_at"}
        data = _normalize_signal_data(row.get("data"))
        if data in ({}, []):
            return {"reason": "empty_data", "data_type": data_type}
        value, _ = _signal_value(data, source_table=source, data_type=data_type)
        if value is None:
            return {
                "reason": "missing_value",
                "data_type": data_type,
                "data_shape": _data_shape(data),
                "top_level_keys": sorted(data.keys()) if isinstance(data, dict) else [],
            }
        return {"reason": "unknown_structured_projection_failure", "data_type": data_type}

    return {"reason": "unsupported_source"}


def _news_raw_text(row: dict[str, Any]) -> str:
    title = _clean_news_text(row.get("title"))
    summary = _clean_news_text(row.get("summary"))
    content = _clean_news_text(row.get("content"))

    parts: list[str] = []
    if content:
        parts.append(content)
    if title and not _news_text_contains(content, title):
        parts.insert(0, title)
    if summary and not any(_news_text_contains(part, summary) or _news_text_contains(summary, part) for part in parts):
        parts.append(summary)
    return "\n".join(parts)


def _clean_news_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _news_text_contains(container: str, item: str) -> bool:
    container_key = re.sub(r"\s+", "", container or "")
    item_key = re.sub(r"\s+", "", item or "")
    return bool(container_key and item_key and item_key in container_key)


def _target_ref_from_market_data(data: dict[str, Any] | list[Any]) -> str | None:
    if isinstance(data, list):
        return None
    stock = _stock_entity_from_any(data)
    if stock:
        return f"stock:{stock['exchange']}:{stock['code']}"
    name = data.get("name") or data.get("sector_name") or data.get("industry") or data.get("concept")
    if name:
        return f"industry:business:{name}"
    indicator = data.get("indicator") or data.get("metric")
    if indicator:
        return f"macro_indicator:{indicator}"
    return None


def _default_signal_target(prefix: str, data_type: str) -> str:
    return f"macro_indicator:{prefix}.{data_type}"


def _normalize_signal_data(value: Any) -> dict[str, Any] | list[Any]:
    if isinstance(value, (dict, list)):
        return value
    return {}


def _signal_value(data: dict[str, Any] | list[Any], *, source_table: str, data_type: str) -> tuple[float | int | None, str | None]:
    for path in _VALUE_PATHS.get((source_table, data_type), []):
        value = _value_at_path(data, path)
        if value is not None:
            return value, path
    value, key = _number_from_any(data, _COMMON_VALUE_KEYS)
    if value is not None:
        return value, key
    if isinstance(data, list) and data:
        return len(data), "[].count"
    return None, None


def _value_at_path(value: Any, path: str) -> float | int | None:
    if path == "[].count":
        return len(value) if isinstance(value, list) else None
    tokens = path.split(".")
    aggregate: str | None = None
    if tokens and tokens[-1] in {"avg", "sum", "count"}:
        aggregate = tokens.pop()
    values = [value]
    for token in tokens:
        next_values: list[Any] = []
        is_list_token = token.endswith("[]")
        key = token[:-2] if is_list_token else token
        for item in values:
            candidate: Any = item
            if key:
                if not isinstance(item, dict) or key not in item:
                    continue
                candidate = item[key]
            if is_list_token:
                if isinstance(candidate, list):
                    next_values.extend(candidate)
            else:
                next_values.append(candidate)
        values = next_values
        if not values:
            return None
    numbers = [_to_number(item) for item in values]
    valid = [item for item in numbers if item is not None]
    if aggregate == "count":
        return len(values)
    if not valid:
        return None
    if aggregate == "avg":
        return sum(valid) / len(valid)
    if aggregate == "sum":
        return sum(valid)
    return valid[0]


def _number_from_any(value: Any, keys: list[str], *, prefix: str = "") -> tuple[float | int | None, str | None]:
    if isinstance(value, dict):
        for key in keys:
            if key in value:
                number = _to_number(value[key])
                if number is not None:
                    return number, f"{prefix}{key}"
        for key, item in value.items():
            number, path = _number_from_any(item, keys, prefix=f"{prefix}{key}.")
            if number is not None:
                return number, path
    elif isinstance(value, list):
        for index, item in enumerate(value):
            number, path = _number_from_any(item, keys, prefix=f"{prefix}[{index}].")
            if number is not None:
                return number, path
    return None, None


def _number_from_data(data: dict[str, Any], keys: list[str]) -> float | int | None:
    for key in keys:
        value = _to_number(data.get(key))
        if value is not None:
            return value
    return None


def _to_number(value: Any) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace("%", "")
        if not text or text in {"-", "--", "None", "null"}:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _data_shape(value: Any) -> str:
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return type(value).__name__


def _stock_entity(value: Any, stock_names: dict[str, str]) -> dict[str, Any] | None:
    stock = _stock_entity_from_any(value)
    if stock is None:
        return None
    stock["name"] = stock_names.get(stock["code"]) or stock.get("name") or stock["code"]
    stock["confidence"] = 0.7
    return stock


def _stock_entity_from_any(value: Any) -> dict[str, Any] | None:
    raw_code: Any
    raw_name: Any = None
    if isinstance(value, str):
        raw_code = value
    elif isinstance(value, dict):
        raw_code = (
            value.get("code")
            or value.get("stock_code")
            or value.get("symbol")
            or value.get("sc")
            or value.get("thsStockCode")
        )
        raw_name = value.get("name") or value.get("stock_name") or value.get("secName") or value.get("stockName")
    else:
        return None
    parsed = _parse_code_exchange(str(raw_code or ""))
    if parsed is None:
        return None
    code, exchange = parsed
    return {"type": "stock", "exchange": exchange, "code": code, "name": str(raw_name or code)}


def _parse_code_exchange(value: str) -> tuple[str, str] | None:
    text = value.strip().upper()
    match = re.fullmatch(r"(SH|SZ|BJ)(\d{6})", text)
    if match:
        return match.group(2), match.group(1)
    match = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", text)
    if match:
        return match.group(1), match.group(2)
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
    if match:
        code = match.group(1)
        return code, _infer_exchange(code)
    return None


def _infer_exchange(code: str) -> str:
    if code.startswith("6"):
        return "SH"
    if code.startswith(("0", "3")):
        return "SZ"
    if code.startswith(("4", "8", "9")):
        return "BJ"
    return "CN"


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _clean_tags(value: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in _as_list(value):
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _weak_tag_hints(tags: list[str], *, title: str, raw_text: str) -> list[dict[str, Any]]:
    text = raw_text or title
    hints: list[dict[str, Any]] = []
    for tag in tags:
        hints.append(
            {
                "kind": "tag",
                "value": tag,
                "confidence": 0.25,
                "in_title": tag in title,
                "in_text": tag in text,
            }
        )
    return hints


def _unique_entities(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for entity in entities:
        key = (
            str(entity.get("type") or ""),
            str(entity.get("exchange") or entity.get("taxonomy") or "")
            + ":"
            + str(entity.get("code") or entity.get("name") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(entity)
    return result


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value) if value else ""
