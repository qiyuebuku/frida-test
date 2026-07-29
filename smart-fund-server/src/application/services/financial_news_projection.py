"""Project ft_news rows into financial KG news records."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from sqlalchemy import text

from src.application.services.financial_stock_bootstrap import (
    build_stock_basics_records_from_sources,
    stock_from_any,
)
from src.domain.knowledge_adapters.financial.source_projection import project_ft_news_row
from src.infrastructure.connections import get_session

Target = Literal["prod", "test"]


def build_news_records_from_sources(
    *,
    target: Target = "prod",
    codes: list[str] | None = None,
    limit: int = 20,
    order_by_created_at: bool = False,
) -> list[dict[str, Any]]:
    """Project recent business news rows into adapter-owned KG source records."""

    if not _table_exists(target, "ft_news"):
        return []
    wanted_codes = {_normalize_code(code) for code in codes or [] if _normalize_code(code)}
    where = ""
    params: dict[str, Any] = {"limit": _limit(limit)}
    if wanted_codes:
        patterns = [f"%{code}%" for code in sorted(wanted_codes)]
        where = "where " + " or ".join(
            f"related_stocks::text like :pattern_{idx}"
            for idx, _code in enumerate(patterns)
        )
        params.update({f"pattern_{idx}": pattern for idx, pattern in enumerate(patterns)})
    stock_names = _stock_names_by_code(target=target, codes=wanted_codes)
    order_by = "created_at desc, id desc" if order_by_created_at else "published_at desc, id desc"
    rows = _rows(
        target,
        f"""
        select id, title, content, summary, source, source_name, category,
               source_reliability, url, tags, related_stocks, published_at,
               fingerprint, created_at
        from ft_news
        {where}
        order by {order_by}
        limit :limit
        """,
        params,
    )
    return [record for row in rows if (record := _news_row_to_record(row, stock_names))]


def _news_row_to_record(
    row: dict[str, Any],
    stock_names: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    return project_ft_news_row(row, stock_names=stock_names or {})


def _stock_entity(value: Any, stock_names: dict[str, str] | None = None) -> dict[str, Any] | None:
    stock = stock_from_any(value)
    if stock is None:
        return None
    name = (stock_names or {}).get(stock["code"]) or stock["name"]
    return {
        "type": "stock",
        "exchange": stock["exchange"],
        "code": stock["code"],
        "name": name,
        "confidence": 0.7,
    }


def _concept_entity(value: Any) -> dict[str, Any]:
    name = str(value).strip()
    return {"type": "concept", "taxonomy": "business", "name": name, "confidence": 0.55}


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


def _normalize_code(value: Any) -> str:
    stock = stock_from_any(str(value))
    return stock["code"] if stock else ""


def _stock_names_by_code(*, target: Target, codes: set[str]) -> dict[str, str]:
    if not codes:
        return {}
    records = build_stock_basics_records_from_sources(
        target=target,
        codes=sorted(codes),
        limit=500,
    )
    return {
        str(record.get("payload", {}).get("code")): str(record.get("payload", {}).get("name"))
        for record in records
        if record.get("payload", {}).get("code") and record.get("payload", {}).get("name")
    }


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value else ""


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
