"""Bounded, direct THS reads for ad-hoc Agent instrument research."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Literal

from src.application.services.market_evidence_locator import (
    MarketEvidenceIdentity,
    encode_market_evidence_locator,
)
from src.domain.collection.watchlist_instrument import normalize_instrument
from src.infrastructure import clients
from src.infrastructure.clients.ths import THSClient


InstrumentField = Literal[
    "quote",
    "identity",
    "fund_overview",
    "realtime_trend",
    "nav_trend",
    "performance",
    "holdings",
    "asset_allocation",
    "style",
    "scale",
    "holders",
    "manager",
    "manager_profile",
    "trade_rules",
    "technical",
    "announcements",
    "news",
]
_ALLOWED_FIELDS = frozenset(InstrumentField.__args__)
_ETF_ONLY_FIELDS = _ALLOWED_FIELDS - {"quote", "identity"}
_MARKET_CODES = {"sh": "17", "sz": "33", "bj": "151"}
_QUOTE_OVERVIEW_FIELDS = (
    "code", "market_code", "name", "latest", "change_rate", "turnover",
    "turnover_yuan", "turnover_rate", "amplitude", "industry",
)


class RealtimeInstrumentResearchService:
    """Query selected THS modules now, without reading tracked-instrument data."""

    async def open(
        self,
        *,
        codes: list[str],
        instrument_type: Literal["auto", "stock", "etf"] = "auto",
        fields: list[InstrumentField] | None = None,
        period: Literal["month", "year", "nowyear"] = "month",
        item_limit: int = 20,
    ) -> dict[str, Any]:
        selected_fields = list(dict.fromkeys(fields or ["quote", "identity"]))
        unknown = [field for field in selected_fields if field not in _ALLOWED_FIELDS]
        if unknown:
            raise ValueError(f"不支持的 fields: {unknown}")
        if not selected_fields:
            raise ValueError("fields 不能为空")
        if len(selected_fields) > 4:
            raise ValueError("单次最多选择 4 个字段组，请按研究问题分步查询")
        bounded_limit = max(1, min(int(item_limit), 60))
        requested = list(dict.fromkeys(
            str(code).strip() for code in codes if str(code).strip()
        ))
        if not requested:
            raise ValueError("codes 不能为空")
        maximum_codes = 5 if len(selected_fields) <= 2 else 2
        if len(requested) > maximum_codes:
            raise ValueError(
                f"选择 {len(selected_fields)} 个字段组时单次最多查询 {maximum_codes} 个标的"
            )
        client = clients.ths
        if client is None:
            raise RuntimeError("THS client is not initialized")

        identities = [
            normalize_instrument(code=code, instrument_type=instrument_type)
            for code in requested
        ]
        if any(item.instrument_type == "index" for item in identities):
            raise ValueError("本工具只支持股票和 ETF")
        if _ETF_ONLY_FIELDS.intersection(selected_fields):
            stocks = [item.code for item in identities if item.instrument_type == "stock"]
            if stocks:
                raise ValueError(
                    f"股票目前只支持 quote 和 identity，ETF 字段不能用于: {stocks}"
                )

        quote_by_code: dict[str, dict] = {}
        quote_error: str | None = None
        if "quote" in selected_fields:
            securities = [(_digits(item.code), _market_code(_digits(item.code))) for item in identities]
            quote_result = await client.get_native_security_quotes(securities)
            if quote_result.get("status") not in {"ok", "empty"}:
                quote_error = str(
                    quote_result.get("error") or quote_result.get("status")
                )
            else:
                quote_by_code = {
                    str(row.get("code")): row
                    for row in ((quote_result.get("data") or {}).get("securities") or [])
                }

        fetched_at = datetime.now(UTC).replace(microsecond=0)
        items = await asyncio.gather(*[
            self._open_one(
                client=client,
                code=_digits(identity.code),
                instrument_type=("etf" if identity.instrument_type == "fund" else "stock"),
                fields=selected_fields,
                quote=quote_by_code.get(_digits(identity.code)),
                quote_error=quote_error,
                period=period,
                item_limit=bounded_limit,
                fetched_at=fetched_at,
            )
            for identity in identities
        ])
        return {
            "operation": "market_instrument_realtime_open",
            "read_path": "direct_ths_upstream",
            "fields": selected_fields,
            "fetched_at": fetched_at.isoformat(),
            "items": items,
            "available_fields": sorted(_ALLOWED_FIELDS),
            "next_step": (
                "只继续请求验证当前假设所需的字段组；不要为了浏览而一次读取全部基金资料。"
            ),
        }

    async def compare_expressions(
        self,
        *,
        codes: list[str],
        item_limit: int = 10,
    ) -> dict[str, Any]:
        requested = list(dict.fromkeys(str(code).strip() for code in codes if str(code).strip()))
        if not 2 <= len(requested) <= 4:
            raise ValueError("codes 必须包含 2 到 4 个不同 ETF")
        raw_items = await asyncio.gather(*[
            self.open(
                codes=[code],
                instrument_type="etf",
                fields=["quote", "fund_overview", "holdings", "performance"],
                item_limit=max(3, min(item_limit, 20)),
            )
            for code in requested
        ])
        items = [payload["items"][0] for payload in raw_items]
        projections = [_expression_projection(item) for item in items]
        overlaps = []
        for left_index, left in enumerate(projections):
            for right in projections[left_index + 1:]:
                left_holdings = set(left.get("top_holding_codes") or [])
                right_holdings = set(right.get("top_holding_codes") or [])
                union = left_holdings | right_holdings
                overlaps.append({
                    "left_code": left["code"],
                    "right_code": right["code"],
                    "shared_holding_codes": sorted(left_holdings & right_holdings),
                    "holding_jaccard": round(len(left_holdings & right_holdings) / len(union), 4) if union else None,
                })
        return {
            "operation": "market_expression_compare_open",
            "read_path": "direct_ths_upstream",
            "expressions": projections,
            "pairwise_holding_overlap": overlaps,
            "comparison_limits": [
                "主题纯度仅依据同花顺返回的基金身份与前十大持仓，不替代完整指数编制文件",
                "缺失字段保持 null，不用相邻指标推断",
            ],
            "evidence_locators": [item["evidence_locator"] for item in items],
        }

    async def _open_one(
        self,
        *,
        client: THSClient,
        code: str,
        instrument_type: str,
        fields: list[str],
        quote: dict | None,
        quote_error: str | None,
        period: str,
        item_limit: int,
        fetched_at: datetime,
    ) -> dict[str, Any]:
        item: dict[str, Any] = {"code": code, "instrument_type": instrument_type}
        if "quote" in fields and quote:
            raw_quote = quote
            item["quote"] = {
                key: raw_quote[key]
                for key in _QUOTE_OVERVIEW_FIELDS
                if raw_quote.get(key) is not None
            }
            item["quote"]["quote_semantics"] = "exchange_realtime_quote"
        elif "quote" in fields and instrument_type == "etf":
            try:
                fallback = await self._read_etf_quote_fallback(client, code)
            except Exception as exc:
                fallback = None
                quote_error = quote_error or str(exc)
            if fallback:
                item["quote"] = fallback
                if quote_error:
                    item.setdefault("field_warnings", {})["quote"] = (
                        "原生实时行情不可用，已回退到同花顺基金行情；"
                        "latest 为最近基金净值，不是盘中成交价。"
                    )
            else:
                item.setdefault("field_errors", {})["quote"] = (
                    quote_error or "同花顺未返回该 ETF 行情"
                )
        elif "quote" in fields:
            item.setdefault("field_errors", {})["quote"] = (
                quote_error or "同花顺未返回该股票实时行情"
            )
        tasks = {
            field: asyncio.create_task(
                self._read_etf_field(client, code, field, period, item_limit)
            )
            for field in fields
            if field != "quote" and instrument_type == "etf"
        }
        for field, result in zip(
            tasks,
            await asyncio.gather(*tasks.values(), return_exceptions=True),
        ):
            if isinstance(result, Exception):
                item.setdefault("field_errors", {})[field] = str(result)
            else:
                item[field] = result
        if "identity" in fields and instrument_type == "stock":
            item["identity"] = {
                "code": code,
                "name": (quote or {}).get("name"),
                "market_code": _market_code(code),
                "instrument_type": "stock",
            }
        item["evidence_locator"] = encode_market_evidence_locator(
            MarketEvidenceIdentity(
                kind="live_upstream_response",
                domain="ths_realtime_instrument",
                identity={
                    "run_fetch": fetched_at.isoformat(),
                    "code": code,
                    "fields": fields,
                },
                data_type=f"ths_{instrument_type}_realtime",
                subject_id=f"cn:{instrument_type}:{code}",
                provider="ths_native",
                fact_time=fetched_at.isoformat(),
            )
        )
        return item

    async def _read_etf_quote_fallback(
        self,
        client: THSClient,
        code: str,
    ) -> dict[str, Any] | None:
        """Return the latest published fund value when native quotes are unavailable."""

        raw = await _uncached(client, "get_fund_info", code)
        data = raw.get("data") or {}
        if not data:
            return None
        return {
            "code": code,
            "name": data.get("name"),
            "latest": _optional_number(data.get("net")),
            "as_of": data.get("date"),
            "fetched_at": data.get("nowtime"),
            "market_id": data.get("defaultMarketId") or data.get("marktId"),
            "quote_semantics": "latest_published_fund_value",
            "source": "ths_fund_info",
        }

    async def _read_etf_field(
        self,
        client: THSClient,
        code: str,
        field: str,
        period: str,
        limit: int,
    ) -> Any:
        if field == "identity":
            result = await client.get_etf_identity(code)
            return result.get("data") if result.get("status") == "ok" else result
        if field == "fund_overview":
            return _bounded(await _uncached(client, "get_product_detail", code), limit)
        if field == "realtime_trend":
            return _bounded(await client.get_realtime_trend(code), limit)
        if field == "nav_trend":
            return _bounded(await client.get_nav_trend(code, period=period), limit)
        if field == "performance":
            rank, yearly, drawdown = await asyncio.gather(
                _uncached(client, "get_performance_rank", code),
                _uncached(client, "get_year_return", code),
                _uncached(client, "get_max_drawdown", code),
            )
            return _bounded({"rank": rank, "year_return": yearly, "max_drawdown": drawdown}, limit)
        if field == "holdings":
            overview, top10 = await asyncio.gather(
                _uncached(client, "get_holding_overview", code),
                _uncached(client, "get_top10_holdings", code),
            )
            return _bounded({"overview": overview, "top10": top10}, limit)
        if field == "asset_allocation":
            return _bounded(await _uncached(client, "get_asset_allocation", code), limit)
        if field == "style":
            return _bounded(await _uncached(client, "get_style_preference", code), limit)
        if field == "scale":
            return _bounded(await _uncached(client, "get_scale_change", code), limit)
        if field == "holders":
            return _bounded(await _uncached(client, "get_holder_ratio", code), limit)
        if field in {"manager", "manager_profile"}:
            detail = await _uncached(client, "get_fund_detail", code)
            managers = ((detail.get("data") or {}).get("managerInfo") or [])
            if field == "manager":
                return _bounded(managers, limit)
            profiles = await asyncio.gather(*[
                _uncached(client, "get_manager_profile", str(manager.get("id")))
                for manager in managers[:2]
                if manager.get("id")
            ])
            return _bounded(profiles, limit)
        if field == "trade_rules":
            return _bounded(await _uncached(client, "get_trade_rule", code), limit)
        if field == "technical":
            return _bounded(await client.get_nav_technical(code), limit)
        if field == "announcements":
            return _bounded(
                await _uncached(client, "get_announcements", code, page_size=limit),
                limit,
            )
        if field == "news":
            info = await _uncached(client, "get_fund_info", code)
            hqcode = (info.get("data") or {}).get("hqcode")
            if not hqcode:
                return {"status_code": -1, "data": {"contentList": []}}
            return _bounded(await client._get(
                f"{client.BASE_URL}/quotation/fund_content/v2/query",
                params={"code": hqcode, "marketId": "32", "limit": limit},
            ), limit)
        raise ValueError(f"不支持的 ETF field: {field}")


async def _uncached(client: THSClient, method_name: str, *args, **kwargs) -> Any:
    method = getattr(THSClient, method_name)
    direct = getattr(method, "__wrapped__", method)
    return await direct(client, *args, **kwargs)


def _digits(code: str) -> str:
    return str(code)[-6:]


def _market_code(code: str) -> str:
    exchange = (
        "sh" if code.startswith(("5", "6", "9"))
        else "bj" if code.startswith(("4", "8"))
        else "sz"
    )
    return _MARKET_CODES[exchange]


def _bounded(value: Any, item_limit: int) -> Any:
    if isinstance(value, str):
        return value if len(value) <= 2000 else value[:2000] + "…"
    if isinstance(value, list):
        return [_bounded(item, item_limit) for item in value[:item_limit]]
    if isinstance(value, dict):
        return {key: _bounded(item, item_limit) for key, item in value.items()}
    return value


def _optional_number(value: Any) -> float | None:
    try:
        return float(value) if value not in {None, ""} else None
    except (TypeError, ValueError):
        return None


def _expression_projection(item: dict[str, Any]) -> dict[str, Any]:
    quote = item.get("quote") if isinstance(item.get("quote"), dict) else {}
    holdings = _collect_six_digit_codes(item.get("holdings"))[:10]
    return {
        "code": item.get("code"),
        "name": quote.get("name") or _find_first(item.get("fund_overview"), {"name", "fundName", "productName"}),
        "latest": _optional_number(quote.get("latest")),
        "turnover_yuan": _optional_number(quote.get("turnover_yuan") or quote.get("turnover")),
        "turnover_rate_pct": _optional_number(quote.get("turnover_rate")),
        "tracking_index": _find_first(item.get("fund_overview"), {"trackingIndex", "trackIndex", "indexName", "benchmark"}),
        "fund_scale": _find_first(item.get("fund_overview"), {"scale", "fundScale", "netAsset"}),
        "year_return": _find_first(item.get("performance"), {"yearReturn", "year_return", "sylY"}),
        "max_drawdown": _find_first(item.get("performance"), {"maxDrawdown", "max_drawdown"}),
        "top_holding_codes": holdings,
        "top_holding_count": len(holdings),
        "evidence_locator": item.get("evidence_locator"),
        "field_errors": item.get("field_errors"),
    }


def _find_first(value: Any, keys: set[str]) -> Any:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in keys and child not in (None, "", [], {}):
                return child
        for child in value.values():
            found = _find_first(child, keys)
            if found not in (None, "", [], {}):
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_first(child, keys)
            if found not in (None, "", [], {}):
                return found
    return None


def _collect_six_digit_codes(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in {"code", "stockcode", "securitycode", "hqcode"}:
                digits = "".join(character for character in str(child) if character.isdigit())[-6:]
                if len(digits) == 6:
                    found.append(digits)
            found.extend(_collect_six_digit_codes(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_collect_six_digit_codes(child))
    return list(dict.fromkeys(found))
