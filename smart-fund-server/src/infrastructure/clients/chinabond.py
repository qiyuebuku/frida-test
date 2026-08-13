"""ChinaBond official index history client."""

from __future__ import annotations

from datetime import datetime, timezone

from src.infrastructure.clients.base import BaseClient
from src.infrastructure.clients.market_contracts import (
    MarketDataStatus,
    market_error,
    market_result,
)


class ChinaBondClient(BaseClient):
    """Fetch official ChinaBond New Composite Bond Index histories."""

    QUERY_URL = (
        "https://yield.chinabond.com.cn/"
        "cbweb-mn/indices/singleIndexQuery"
    )
    INDEX_ID = "8a8b2ca0332abed20134ea76d8885831"
    INDEX_NAME = "中债-新综合指数"
    INDICATORS = {
        "full_price": ("QJZS", "全价指数"),
        "clean_price": ("JJZS", "净价指数"),
        "wealth": ("CFZS", "财富指数"),
    }

    async def get_new_composite_index_history(self) -> dict:
        """Return full-price, clean-price and wealth index histories."""

        try:
            responses = {}
            for indicator, (indicator_code, indicator_name) in (
                self.INDICATORS.items()
            ):
                response = await self._client.post(
                    self.QUERY_URL,
                    data={
                        "indexid": self.INDEX_ID,
                        "qxlxt": "00",
                        "zslxt": indicator_code,
                        "lx": "1",
                    },
                    headers={
                        "Referer": (
                            "https://yield.chinabond.com.cn/"
                            "cbweb-mn/indices/single_index_query"
                        ),
                        "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
                    },
                )
                response.raise_for_status()
                rows = self._normalize_rows(
                    response.json(),
                    indicator=indicator,
                    indicator_code=indicator_code,
                    indicator_name=indicator_name,
                )
                if not rows:
                    return market_error(
                        provider="chinabond",
                        market="cn",
                        error=(
                            f"ChinaBond {indicator_name} response contained "
                            "no usable rows"
                        ),
                        status=MarketDataStatus.PARSE_ERROR,
                    )
                responses[indicator] = rows

            latest_date = max(
                rows[-1]["date"] for rows in responses.values() if rows
            )
            return market_result(
                provider="chinabond",
                market="cn",
                data={
                    "index_id": self.INDEX_ID,
                    "index_name": self.INDEX_NAME,
                    "series": responses,
                    "count": sum(len(rows) for rows in responses.values()),
                },
                source_time=latest_date,
                trade_date=latest_date,
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "official_source": True,
                    "frequency": "daily",
                    "index_scope": "total_value",
                    "complete": True,
                    "source_url": self.QUERY_URL,
                },
            )
        except Exception as exc:
            return market_error(
                provider="chinabond",
                market="cn",
                error=exc,
            )

    @staticmethod
    def _normalize_rows(
        payload: dict,
        *,
        indicator: str,
        indicator_code: str,
        indicator_name: str,
    ) -> list[dict]:
        raw = payload.get(f"{indicator_code}_00")
        if not isinstance(raw, dict):
            return []
        rows = []
        for timestamp, value in raw.items():
            try:
                observed_date = datetime.fromtimestamp(
                    int(timestamp) / 1000,
                    tz=timezone.utc,
                ).date()
                rows.append(
                    {
                        "date": observed_date.isoformat(),
                        "value": float(value),
                        "indicator": indicator,
                        "indicator_code": indicator_code,
                        "indicator_name": indicator_name,
                    }
                )
            except (TypeError, ValueError, OSError):
                continue
        return sorted(rows, key=lambda item: item["date"])
