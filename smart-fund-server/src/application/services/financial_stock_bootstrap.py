"""Build stock basics KG records from existing business source tables."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import text

from src.infrastructure.connections import get_session

Target = Literal["prod", "test"]

_A_SHARE_RE = re.compile(r"(?<!\d)(?:[036]\d{5})(?!\d)")


def build_stock_basics_records_from_sources(
    *,
    target: Target = "prod",
    codes: list[str] | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Project known stocks from business tables into financial stock_basics records."""

    wanted_codes = {_normalize_code(code) for code in codes or [] if _normalize_code(code)}
    candidates: list[dict[str, Any]] = []
    candidates.extend(_stocks_from_ft_news(target=target, codes=wanted_codes, limit=limit))
    candidates.extend(_stocks_from_json_table(target=target, table="ft_sentiment", codes=wanted_codes, limit=limit))
    for table in (
        "ft_instrument_profiles",
        "ft_instrument_disclosures",
        "ft_instrument_observations",
    ):
        candidates.extend(
            _stocks_from_json_table(
                target=target,
                table=table,
                codes=wanted_codes,
                limit=limit,
            )
        )
    return [_stock_record(stock) for stock in _dedupe_stocks(candidates, wanted_codes)]


def _stocks_from_ft_news(
    *,
    target: Target,
    codes: set[str],
    limit: int,
) -> list[dict[str, Any]]:
    if not _table_exists(target, "ft_news"):
        return []
    where = ""
    params: dict[str, Any] = {"limit": _limit(limit)}
    if codes:
        patterns = [f"%{code}%" for code in sorted(codes)]
        where = "where " + " or ".join(
            f"related_stocks::text like :pattern_{idx}"
            for idx, _code in enumerate(patterns)
        )
        params.update({f"pattern_{idx}": pattern for idx, pattern in enumerate(patterns)})
    rows = _rows(
        target,
        f"""
        select id, title, related_stocks, created_at
        from ft_news
        {where}
        order by created_at desc
        limit :limit
        """,
        params,
    )
    stocks: list[dict[str, str]] = []
    for row in rows:
        for item in _as_list(row.get("related_stocks")):
            stock = stock_from_any(item)
            if stock:
                stock.setdefault("observed_at", row.get("created_at"))
                stocks.append(stock)
        title = str(row.get("title") or "")
        for code in _A_SHARE_RE.findall(title):
            stocks.append(_stock(code=code, name=code, observed_at=row.get("created_at")))
    return stocks


def _stocks_from_json_table(
    *,
    target: Target,
    table: str,
    codes: set[str],
    limit: int,
) -> list[dict[str, Any]]:
    if not _table_exists(target, table):
        return []
    where = ""
    params: dict[str, Any] = {"limit": _limit(limit)}
    if codes:
        patterns = [f"%{code}%" for code in sorted(codes)]
        where = "where " + " or ".join(
            f"data::text like :pattern_{idx}"
            for idx, _code in enumerate(patterns)
        )
        params.update({f"pattern_{idx}": pattern for idx, pattern in enumerate(patterns)})
    rows = _rows(
        target,
        f"""
        select id, data_type, data, created_at
        from {table}
        {where}
        order by created_at desc
        limit :limit
        """,
        params,
    )
    stocks: list[dict[str, str]] = []
    for row in rows:
        for stock in _stocks_from_json_value(row.get("data")):
            stock.setdefault("observed_at", row.get("created_at"))
            stocks.append(stock)
    return stocks


def _stocks_from_json_value(value: Any) -> list[dict[str, str]]:
    stocks: list[dict[str, str]] = []
    if isinstance(value, list):
        for item in value:
            stocks.extend(_stocks_from_json_value(item))
        return stocks
    if not isinstance(value, dict):
        return stocks
    direct = stock_from_any(value)
    if direct:
        stocks.append(direct)
    for key in ("stocks", "items", "list", "data", "posts"):
        nested = value.get(key)
        if isinstance(nested, (list, dict)):
            stocks.extend(_stocks_from_json_value(nested))
    return stocks


def stock_from_any(value: Any) -> dict[str, str] | None:
    if isinstance(value, str):
        return _stock_from_code_text(value)
    if not isinstance(value, dict):
        return None
    raw_code = (
        value.get("code")
        or value.get("stock_code")
        or value.get("symbol")
        or value.get("sc")
        or value.get("thsStockCode")
    )
    if raw_code is None:
        return None
    parsed = _parse_code_exchange(str(raw_code))
    if parsed is None:
        return None
    code, exchange = parsed
    name = (
        value.get("name")
        or value.get("stock_name")
        or value.get("secName")
        or value.get("stockName")
        or code
    )
    return _stock(code=code, exchange=exchange, name=str(name))


def _stock_from_code_text(value: str) -> dict[str, str] | None:
    parsed = _parse_code_exchange(value)
    if parsed is None:
        return None
    code, exchange = parsed
    return _stock(code=code, exchange=exchange, name=code)


def _parse_code_exchange(value: str) -> tuple[str, str] | None:
    text_value = value.strip().upper()
    match = re.fullmatch(r"(SH|SZ|BJ)(\d{6})", text_value)
    if match:
        return match.group(2), match.group(1)
    match = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", text_value)
    if match:
        return match.group(1), match.group(2)
    match = re.fullmatch(r"\d{6}", text_value)
    if match:
        return text_value, _infer_exchange(text_value)
    return None


def _stock(
    *,
    code: str,
    name: str,
    exchange: str | None = None,
    observed_at: Any = None,
) -> dict[str, Any]:
    normalized_code = _normalize_code(code)
    stock = {
        "code": normalized_code,
        "exchange": exchange or _infer_exchange(normalized_code),
        "name": name or normalized_code,
    }
    if observed_at is not None:
        stock["observed_at"] = observed_at
    return stock


def _stock_record(stock: dict[str, str]) -> dict[str, Any]:
    return {
        "source_type": "stock_basics",
        "observed_at": _iso_observed_at(stock.get("observed_at")),
        "payload": {
            "code": stock["code"],
            "exchange": stock["exchange"],
            "name": stock["name"],
            "status": "active",
        },
    }


def _dedupe_stocks(
    stocks: list[dict[str, str]],
    wanted_codes: set[str],
) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for stock in stocks:
        code = _normalize_code(stock.get("code"))
        if not code:
            continue
        if wanted_codes and code not in wanted_codes:
            continue
        exchange = stock.get("exchange") or _infer_exchange(code)
        key = (exchange, code)
        existing = by_key.get(key)
        if existing is None or existing["name"] == existing["code"]:
            by_key[key] = {
                "exchange": exchange,
                "code": code,
                "name": stock.get("name") or code,
                "observed_at": stock.get("observed_at"),
            }
    return [by_key[key] for key in sorted(by_key)]


def _normalize_code(value: Any) -> str:
    text_value = str(value or "").strip().upper()
    parsed = _parse_code_exchange(text_value)
    return parsed[0] if parsed else ""


def _infer_exchange(code: str) -> str:
    if code.startswith("6"):
        return "SH"
    if code.startswith(("0", "3")):
        return "SZ"
    if code.startswith(("4", "8", "9")):
        return "BJ"
    return "CN"


def _iso_observed_at(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if value:
        return str(value)
    return "1970-01-01T00:00:00+00:00"


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _limit(value: int) -> int:
    return max(1, min(int(value), 5000))


def _table_exists(target: Target, table_name: str) -> bool:
    rows = _rows(
        target,
        """
        select exists (
          select 1 from information_schema.tables
          where table_schema = 'public' and table_name = :table_name
        ) as exists
        """,
        {"table_name": table_name},
    )
    return bool(rows and rows[0]["exists"])


def _rows(target: Target, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with get_session(target) as session:
        return [dict(row) for row in session.execute(text(sql), params or {}).mappings().all()]
