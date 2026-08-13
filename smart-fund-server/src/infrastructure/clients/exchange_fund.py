"""沪深交易所官方基金数据客户端。"""

from __future__ import annotations

import asyncio
import io
import random
import warnings
from datetime import date, datetime

import pandas as pd

from src.infrastructure.clients.base import BaseClient
from src.infrastructure.clients.market_contracts import (
    MarketDataStatus,
    market_error,
    market_result,
)


class ExchangeFundClient(BaseClient):
    """获取上交所和深交所公开的日级 ETF 份额。"""

    SSE_QUERY_URL = "https://query.sse.com.cn/commonQuery.do"
    SZSE_REPORT_URL = "https://www.szse.cn/api/report/ShowReport"
    SSE_REFERER = "https://www.sse.com.cn/assortment/fund/etf/list/scale/"
    SZSE_REFERER = "https://www.szse.cn/market/fund/volume/etf/index.html"

    @staticmethod
    def _parse_date(value: str, field_name: str) -> date:
        normalized = value.replace("-", "")
        try:
            return datetime.strptime(normalized, "%Y%m%d").date()
        except ValueError as exc:
            raise ValueError(f"{field_name} must use YYYYMMDD or YYYY-MM-DD") from exc

    async def get_sse_etf_daily_shares(self, trade_date: str) -> dict:
        """获取上交所指定交易日的全部 ETF 份额。"""
        requested_date = self._parse_date(trade_date, "trade_date")
        formatted_date = requested_date.isoformat()
        try:
            response = await self._client.get(
                self.SSE_QUERY_URL,
                params={
                    "isPagination": "true",
                    "pageHelp.pageSize": "10000",
                    "pageHelp.pageNo": "1",
                    "pageHelp.beginPage": "1",
                    "pageHelp.cacheSize": "1",
                    "pageHelp.endPage": "1",
                    "sqlId": "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L",
                    "STAT_DATE": formatted_date,
                },
                headers={
                    "Referer": self.SSE_REFERER,
                    "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
                },
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("result")
            if not isinstance(rows, list):
                return market_error(
                    provider="sse",
                    market="cn",
                    error="SSE ETF daily shares response schema changed",
                    status=MarketDataStatus.PARSE_ERROR,
                )

            items = []
            for row in rows:
                code = str(row.get("SEC_CODE") or "").strip()
                observed_date = str(row.get("STAT_DATE") or "").strip()
                if (
                    not code
                    or not observed_date
                    or observed_date != formatted_date
                ):
                    continue
                try:
                    shares = float(row["TOT_VOL"]) * 10_000
                except (KeyError, TypeError, ValueError):
                    continue
                items.append(
                    {
                        "exchange": "sse",
                        "market": "sh",
                        "date": observed_date,
                        "code": code.zfill(6),
                        "name": str(row.get("SEC_NAME") or "").strip(),
                        "etf_type": str(row.get("ETF_TYPE") or "").strip() or None,
                        "shares": shares,
                        "share_unit": "share",
                    }
                )

            total = ((payload.get("pageHelp") or {}).get("total"))
            if rows and not items:
                return market_error(
                    provider="sse",
                    market="cn",
                    error="SSE ETF daily shares contained no valid rows",
                    status=MarketDataStatus.PARSE_ERROR,
                )
            return market_result(
                provider="sse",
                market="cn",
                data={
                    "exchange": "sse",
                    "count": len(items),
                    "items": items,
                },
                source_time=items[0]["date"] if items else formatted_date,
                trade_date=items[0]["date"] if items else requested_date,
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "official_exchange_data": True,
                    "frequency": "daily",
                    "complete": total is None or len(items) == int(total),
                    "reported_total": total,
                    "source_share_unit": "ten_thousand_shares",
                    "net_subscription_available": False,
                },
            )
        except (TypeError, ValueError) as exc:
            return market_error(
                provider="sse",
                market="cn",
                error=exc,
                status=MarketDataStatus.PARSE_ERROR,
            )
        except Exception as exc:
            return market_error(provider="sse", market="cn", error=exc)

    async def get_szse_etf_daily_shares(
        self,
        start_date: str,
        end_date: str | None = None,
    ) -> dict:
        """获取深交所日期范围内的全部 ETF 日级份额。"""
        start = self._parse_date(start_date, "start_date")
        end = self._parse_date(end_date or start_date, "end_date")
        if start > end:
            raise ValueError("start_date cannot be later than end_date")
        if (end - start).days > 183:
            raise ValueError("SZSE ETF daily shares range cannot exceed 6 months")

        try:
            response = await self._client.get(
                self.SZSE_REPORT_URL,
                params={
                    "SHOWTYPE": "xlsx",
                    "CATALOGID": "scsj_fund_jjgm",
                    "TABKEY": "tab1",
                    "txtStart": start.isoformat(),
                    "txtEnd": end.isoformat(),
                    "jjlb": "ETF",
                    "random": str(random.random()),
                },
                headers={
                    "Referer": self.SZSE_REFERER,
                    "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
                },
            )
            response.raise_for_status()
            frame = await asyncio.to_thread(
                self._read_szse_excel,
                response.content,
            )
            required_columns = {"日期", "基金代码", "基金简称", "基金规模(份)"}
            if not required_columns.issubset(frame.columns):
                return market_error(
                    provider="szse",
                    market="cn",
                    error="SZSE ETF daily shares response schema changed",
                    status=MarketDataStatus.PARSE_ERROR,
                )

            items = []
            for row in frame.to_dict("records"):
                observed_date = pd.to_datetime(
                    row.get("日期"),
                    errors="coerce",
                )
                code_value = pd.to_numeric(
                    row.get("基金代码"),
                    errors="coerce",
                )
                shares = pd.to_numeric(
                    row.get("基金规模(份)"),
                    errors="coerce",
                )
                if pd.isna(observed_date) or pd.isna(code_value) or pd.isna(shares):
                    continue
                observed_day = observed_date.date()
                if observed_day < start or observed_day > end:
                    continue
                items.append(
                    {
                        "exchange": "szse",
                        "market": "sz",
                        "date": observed_day.isoformat(),
                        "code": str(int(code_value)).zfill(6),
                        "name": str(row.get("基金简称") or "").strip(),
                        "etf_type": None,
                        "shares": float(shares),
                        "share_unit": "share",
                    }
                )

            items.sort(key=lambda item: (item["date"], item["code"]))
            if not frame.empty and not items:
                return market_error(
                    provider="szse",
                    market="cn",
                    error="SZSE ETF daily shares contained no valid rows",
                    status=MarketDataStatus.PARSE_ERROR,
                )
            latest_date = items[-1]["date"] if items else end.isoformat()
            return market_result(
                provider="szse",
                market="cn",
                data={
                    "exchange": "szse",
                    "count": len(items),
                    "items": items,
                },
                source_time=latest_date,
                trade_date=latest_date,
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "official_exchange_data": True,
                    "frequency": "daily",
                    "complete": True,
                    "requested_start_date": start.isoformat(),
                    "requested_end_date": end.isoformat(),
                    "source_share_unit": "share",
                    "net_subscription_available": False,
                },
            )
        except ValueError:
            raise
        except Exception as exc:
            return market_error(provider="szse", market="cn", error=exc)

    async def get_etf_daily_shares(self, trade_date: str) -> dict:
        """合并指定交易日的沪深 ETF 日级份额。"""
        requested_date = self._parse_date(trade_date, "trade_date")
        sse_result, szse_result = await asyncio.gather(
            self.get_sse_etf_daily_shares(trade_date),
            self.get_szse_etf_daily_shares(trade_date, trade_date),
        )
        results = {"sse": sse_result, "szse": szse_result}
        failed = {
            exchange: result.get("status")
            for exchange, result in results.items()
            if result.get("status")
            not in {MarketDataStatus.OK.value, MarketDataStatus.EMPTY.value}
        }
        if failed:
            return market_error(
                provider="cn_exchanges",
                market="cn",
                error=f"ETF daily shares incomplete: {failed}",
                provider_metadata={
                    "official_exchange_data": True,
                    "exchange_statuses": {
                        exchange: result.get("status")
                        for exchange, result in results.items()
                    },
                },
            )

        items = [
            item
            for result in results.values()
            for item in ((result.get("data") or {}).get("items") or [])
        ]
        items.sort(key=lambda item: (item["exchange"], item["code"]))
        return market_result(
            provider="cn_exchanges",
            market="cn",
            data={
                "count": len(items),
                "exchange_counts": {
                    exchange: len(
                        ((result.get("data") or {}).get("items") or [])
                    )
                    for exchange, result in results.items()
                },
                "items": items,
            },
            source_time=requested_date.isoformat(),
            trade_date=requested_date,
            timezone_name="Asia/Shanghai",
            provider_metadata={
                "official_exchange_data": True,
                "frequency": "daily",
                "complete": True,
                "net_subscription_available": False,
            },
        )

    @staticmethod
    def _read_szse_excel(content: bytes) -> pd.DataFrame:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return pd.read_excel(io.BytesIO(content), engine="openpyxl")
