"""A-share market-level valuation history client."""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from src.infrastructure.clients.base import BaseClient
from src.infrastructure.clients.market_contracts import (
    MarketDataStatus,
    market_error,
    market_result,
)


CN_TIMEZONE = ZoneInfo("Asia/Shanghai")


class MarketValuationClient(BaseClient):
    """Fetch long-run Shanghai and Shenzhen market PE/PB histories.

    The public source is Legulegu. It is not an exchange or index-provider
    official data source, so callers must retain the provider metadata.
    """

    BASE_URL = "https://legulegu.com"
    MARKET_CONFIG = {
        "sh": {
            "market_id": "1",
            "index_code": "1",
            "page": "/stockdata/shanghaiPE",
            "name": "上证市场",
        },
        "sz": {
            "market_id": "2",
            "index_code": "2",
            "page": "/stockdata/shenzhenPE",
            "name": "深证市场",
        },
    }
    CSRF_PATTERN = re.compile(
        r'<meta[^>]+name=["\']_csrf["\'][^>]+content=["\']([^"\']+)["\']',
        re.IGNORECASE,
    )

    def __init__(self, timeout: float = 30.0):
        super().__init__(timeout)
        self._session_lock = asyncio.Lock()

    async def get_market_valuation_history(self, market: str) -> dict:
        """Return normalized PE and PB history for one broad A-share market."""

        normalized_market = market.strip().lower()
        config = self.MARKET_CONFIG.get(normalized_market)
        if config is None:
            raise ValueError("market must be 'sh' or 'sz'")

        try:
            async with self._session_lock:
                csrf_token = await self._open_market_page(config["page"])
                daily_token = hashlib.md5(
                    datetime.now(CN_TIMEZONE).date().isoformat().encode()
                ).hexdigest()
                headers = {
                    "Referer": f"{self.BASE_URL}{config['page']}",
                    "X-CSRF-Token": csrf_token,
                    "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
                }
                pe_response, pb_response = await asyncio.gather(
                    self._client.get(
                        f"{self.BASE_URL}/api/stock-data/market-pe",
                        params={
                            "marketId": config["market_id"],
                            "token": daily_token,
                        },
                        headers=headers,
                    ),
                    self._client.get(
                        f"{self.BASE_URL}/api/stockdata/index-basic-pb",
                        params={
                            "indexCode": config["index_code"],
                            "token": daily_token,
                        },
                        headers=headers,
                    ),
                )
            pe_response.raise_for_status()
            pb_response.raise_for_status()
            pe_rows = self._normalize_pe_rows(pe_response.json())
            pb_rows = self._normalize_pb_rows(pb_response.json())
            if not pe_rows or not pb_rows:
                return market_error(
                    provider="legulegu",
                    market="cn",
                    error="market valuation response contained no usable PE/PB rows",
                    status=MarketDataStatus.PARSE_ERROR,
                    provider_metadata={
                        "requested_market": normalized_market,
                        "pe_count": len(pe_rows),
                        "pb_count": len(pb_rows),
                    },
                )
            latest_date = max(pe_rows[-1]["date"], pb_rows[-1]["date"])
            return market_result(
                provider="legulegu",
                market="cn",
                data={
                    "market_code": normalized_market,
                    "market_name": config["name"],
                    "pe": pe_rows,
                    "pb": pb_rows,
                    "count": len(pe_rows) + len(pb_rows),
                },
                source_time=latest_date,
                trade_date=latest_date,
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "official_source": False,
                    "third_party_source": True,
                    "frequency": {
                        "pe": "source_defined",
                        "pb": "daily",
                    },
                    "valuation_scope": "broad_market",
                    "complete": True,
                    "source_url": f"{self.BASE_URL}{config['page']}",
                },
            )
        except Exception as exc:
            return market_error(
                provider="legulegu",
                market="cn",
                error=exc,
                provider_metadata={"requested_market": normalized_market},
            )

    async def _open_market_page(self, page: str) -> str:
        response = await self._client.get(
            f"{self.BASE_URL}{page}",
            headers={"User-Agent": self.DEFAULT_HEADERS["User-Agent"]},
        )
        response.raise_for_status()
        match = self.CSRF_PATTERN.search(response.text)
        if match is None:
            raise ValueError("Legulegu CSRF token was not found")
        return match.group(1)

    @staticmethod
    def _normalize_pe_rows(payload: dict) -> list[dict]:
        rows = payload.get("data")
        if not isinstance(rows, list):
            return []
        result = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("date"):
                continue
            try:
                result.append(
                    {
                        "date": str(row["date"])[:10],
                        "pe": float(row["pe"]),
                        "close": (
                            float(row["close"])
                            if row.get("close") is not None
                            else None
                        ),
                    }
                )
            except (TypeError, ValueError, KeyError):
                continue
        return sorted(result, key=lambda item: item["date"])

    @staticmethod
    def _normalize_pb_rows(payload: dict) -> list[dict]:
        rows = payload.get("data")
        if not isinstance(rows, list):
            return []
        result = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("date"):
                continue
            try:
                result.append(
                    {
                        "date": str(row["date"])[:10],
                        "pb": float(row["pb"]),
                        "weighted_pb": (
                            float(row["addPb"])
                            if row.get("addPb") is not None
                            else None
                        ),
                        "median_pb": (
                            float(row["middlePb"])
                            if row.get("middlePb") is not None
                            else None
                        ),
                        "close": (
                            float(row["close"])
                            if row.get("close") is not None
                            else None
                        ),
                    }
                )
            except (TypeError, ValueError, KeyError):
                continue
        return sorted(result, key=lambda item: item["date"])
