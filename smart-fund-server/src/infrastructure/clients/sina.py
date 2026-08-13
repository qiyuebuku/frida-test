"""新浪财经数据客户端 (*.sina.com.cn)"""

import asyncio
import json
import re
from datetime import date, datetime, timedelta

import akshare as ak
import pandas as pd

from src.infrastructure.clients.base import BaseClient, cached
from src.infrastructure.clients.market_contracts import (
    MarketDataStatus,
    market_error,
    market_result,
)


class SinaClient(BaseClient):
    """新浪财经数据客户端"""

    SINA_STOCK_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    SINA_SECTOR_URL = "https://money.finance.sina.com.cn/q/view/newFLJK.php"
    SINA_HQ_URL = "https://hq.sinajs.cn"
    SINA_KLINE_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
    SINA_NEWS_URL = "https://feed.mix.sina.com.cn/api/roll/get"
    SINA_MONEYFLOW_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_bkzj_bk"
    SINA_FUND_NAV_URL = "http://stock.finance.sina.com.cn/fundInfo/api/openapi.php/CaihuiFundInfoService.getNav"

    SINA_HEADERS = {
        "Referer": "https://finance.sina.com.cn/",
    }

    # 全球指数代码映射
    GLOBAL_INDEX_CODES = {
        "道琼斯": "int_dji", "纳斯达克": "int_nasdaq", "标普500": "int_sp500",
        "恒生指数": "b_HSI", "台湾加权": "b_TWSE",
        "日经225": "b_NKY", "韩国KOSPI": "b_KOSPI",
        "韩国KOSDAQ": "b_KOSDAQ", "德国DAX": "b_DAX",
        "英国富时100": "b_FTSE", "法国CAC40": "b_CAC",
        "新加坡海峡时报": "b_STI", "印度孟买30": "b_SENSEX",
        "dji": "int_dji", "nasdaq": "int_nasdaq", "sp500": "int_sp500",
        "hsi": "b_HSI", "twse": "b_TWSE", "nikkei225": "b_NKY",
        "kospi": "b_KOSPI", "kosdaq": "b_KOSDAQ", "dax": "b_DAX",
        "ftse100": "b_FTSE", "cac40": "b_CAC", "sti": "b_STI",
        "sensex": "b_SENSEX",
    }
    DEFAULT_GLOBAL_INDEX_CODES = [
        "int_dji",
        "int_nasdaq",
        "int_sp500",
        "b_HSI",
        "b_TWSE",
        "b_NKY",
        "b_KOSPI",
        "b_KOSDAQ",
        "b_DAX",
        "b_FTSE",
        "b_CAC",
        "b_STI",
        "b_SENSEX",
    ]

    # 外汇代码映射
    FOREX_CODES = {
        "美元": "fx_susdcny", "欧元": "fx_seurcny", "日元": "fx_sjpycny",
        "英镑": "fx_sgbpcny", "港币": "fx_shkdcny",
        "usd": "fx_susdcny", "eur": "fx_seurcny", "jpy": "fx_sjpycny",
        "gbp": "fx_sgbpcny", "hkd": "fx_shkdcny",
    }

    # 期货代码映射
    FUTURES_CODES = {
        "黄金": "nf_AU0", "铜": "nf_CU0", "原油": "nf_SC0",
        "白银": "nf_AG0", "铁矿石": "nf_I0",
        "au": "nf_AU0", "cu": "nf_CU0", "sc": "nf_SC0",
        "ag": "nf_AG0", "i": "nf_I0",
    }
    DCE_PRODUCT_NAMES = {
        "A": "豆一",
        "B": "豆二",
        "BB": "胶合板",
        "BZ": "纯苯",
        "C": "玉米",
        "CS": "玉米淀粉",
        "EB": "苯乙烯",
        "EG": "乙二醇",
        "FB": "纤维板",
        "I": "铁矿石",
        "J": "焦炭",
        "JD": "鸡蛋",
        "JM": "焦煤",
        "L": "塑料",
        "LG": "原木",
        "LH": "生猪",
        "M": "豆粕",
        "P": "棕榈",
        "PG": "液化石油气",
        "PP": "PP",
        "RR": "粳米",
        "V": "PVC",
        "Y": "豆油",
    }

    def __init__(self, timeout: float = 10.0):
        super().__init__(timeout)

    async def get_stock_ranking(self, sort: str = "rise", count: int = 20) -> dict:
        """个股涨跌幅排行（新浪财经 API）

        sort: "rise"=涨幅榜, "fall"=跌幅榜, "volume"=成交量榜, "turnover"=成交额榜, "amplitude"=振幅榜, "turnover_rate"=换手率榜
        """
        sort_map = {
            "rise": ("changepercent", "0"),    # 涨幅降序
            "fall": ("changepercent", "1"),     # 涨幅升序（即跌幅榜）
            "volume": ("volume", "0"),          # 成交量降序
            "turnover": ("amount", "0"),        # 成交额降序
            "amplitude": ("pricechange", "0"),  # 涨跌额降序
            "turnover_rate": ("turnoverratio", "0"),  # 换手率降序
        }
        sort_field, asc = sort_map.get(sort, ("changepercent", "0"))

        headers = {
            "Referer": "https://finance.sina.com.cn/",
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
        }
        resp = await self._client.get(
            self.SINA_STOCK_URL,
            params={"page": 1, "num": count, "sort": sort_field,
                    "asc": asc, "node": "hs_a", "symbol": "", "_s_r_a": "init"},
            headers=headers,
        )
        resp.raise_for_status()
        stocks = resp.json()

        def _float(v, default=0):
            try:
                return float(v)
            except (TypeError, ValueError):
                return default

        items = []
        for s in stocks:
            settle = _float(s.get("settlement"), 1)
            high = _float(s.get("high"))
            low = _float(s.get("low"))
            items.append({
                "code": s.get("code", ""),
                "name": s.get("name", ""),
                "close": _float(s.get("trade")),
                "changeRate": _float(s.get("changepercent")),
                "changeAmt": _float(s.get("pricechange")),
                "volume": s.get("volume"),
                "turnover": s.get("amount"),
                "amplitude": round((high - low) / max(settle, 0.01) * 100, 2) if settle else None,
                "turnoverRate": _float(s.get("turnoverratio")),
                "pe": s.get("per"),
                "pb": s.get("pb"),
                "marketCap": round(_float(s.get("mktcap")) / 10000, 2) if s.get("mktcap") else None,
            })

        return {"status_code": 0, "data": {"sort": sort, "count": len(items), "stocks": items}}

    async def get_market_breadth(self) -> dict:
        """获取全 A 股上涨、下跌、平盘家数和成交额。"""
        try:
            frame = await asyncio.to_thread(ak.stock_zh_a_spot)
            changes = frame["涨跌幅"]
            breadth = {
                "listed_count": int(len(frame)),
                "valid_quote_count": int(changes.notna().sum()),
                "up_count": int((changes > 0).sum()),
                "down_count": int((changes < 0).sum()),
                "flat_count": int((changes == 0).sum()),
                "no_valid_quote_count": int(changes.isna().sum()),
                "turnover": float(frame["成交额"].fillna(0).sum()),
                "turnover_unit": "yuan",
            }
            source_time = None
            if "时间戳" in frame.columns:
                values = [str(value) for value in frame["时间戳"].dropna().tolist() if value]
                source_time = max(values) if values else None
            return market_result(
                provider="sina",
                market="cn",
                data=breadth,
                source_time=source_time,
                timezone_name="Asia/Shanghai",
                provider_metadata={"universe": "all_a_shares", "complete": True},
            )
        except Exception as exc:
            return market_error(provider="sina", market="cn", error=exc)

    async def get_sector_constituents(
        self,
        provider_sector_code: str,
        *,
        sector_type: str = "industry",
        page_size: int = 100,
    ) -> dict:
        """按新浪板块代码分页获取全部成分股。"""
        if sector_type not in {"industry", "concept"}:
            raise ValueError("sector_type must be industry or concept")
        if page_size < 1 or page_size > 500:
            raise ValueError("page_size must be between 1 and 500")
        headers = {
            "Referer": "https://finance.sina.com.cn/",
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
        }
        constituents_by_code = {}
        page = 1
        try:
            while True:
                response = await self._client.get(
                    self.SINA_STOCK_URL,
                    params={
                        "page": page,
                        "num": page_size,
                        "sort": "symbol",
                        "asc": "1",
                        "node": provider_sector_code,
                        "symbol": "",
                        "_s_r_a": "init",
                    },
                    headers=headers,
                )
                response.raise_for_status()
                rows = response.json()
                if not isinstance(rows, list):
                    return market_error(
                        provider="sina",
                        market="cn",
                        error="Sina sector constituents response schema changed",
                        status=MarketDataStatus.PARSE_ERROR,
                        provider_metadata={
                            "provider_sector_code": provider_sector_code,
                            "page": page,
                        },
                    )
                for row in rows:
                    code = str(row.get("code") or "")
                    if not code:
                        continue
                    constituents_by_code[code] = {
                        "stock_code": code,
                        "quote_symbol": row.get("symbol"),
                        "stock_name": row.get("name"),
                        "latest": row.get("trade"),
                        "change_amount": row.get("pricechange"),
                        "change_pct": row.get("changepercent"),
                        "previous_close": row.get("settlement"),
                        "open": row.get("open"),
                        "high": row.get("high"),
                        "low": row.get("low"),
                        "volume": row.get("volume"),
                        "turnover": row.get("amount"),
                        "source_time": row.get("ticktime"),
                        "pe": row.get("per"),
                        "pb": row.get("pb"),
                        "market_cap": row.get("mktcap"),
                        "free_float_market_cap": row.get("nmc"),
                        "turnover_rate": row.get("turnoverratio"),
                        "weight": None,
                    }
                if len(rows) < page_size:
                    break
                page += 1

            constituents = list(constituents_by_code.values())
            source_time = next(
                (
                    item.get("source_time")
                    for item in constituents
                    if item.get("source_time")
                ),
                None,
            )
            return market_result(
                provider="sina",
                market="cn",
                data={
                    "provider_sector_code": provider_sector_code,
                    "sector_type": sector_type,
                    "count": len(constituents),
                    "constituents": constituents,
                },
                source_time=source_time,
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "complete": True,
                    "page_count": page,
                    "page_size": page_size,
                    "weight_available": False,
                    "turnover_unit": "yuan",
                    "market_cap_source_unit": "ten_thousand_yuan",
                },
            )
        except Exception as exc:
            return market_error(
                provider="sina",
                market="cn",
                error=exc,
                provider_metadata={
                    "provider_sector_code": provider_sector_code,
                    "page": page,
                },
            )

    @cached(ttl=60, source="sina", domain="market", frequency="realtime",
            market="a_share", source_name="新浪财经")
    async def get_sector_ranking(self, sector_type: str = "concept", count: int = 20) -> dict:
        """板块涨跌排行（新浪财经 API）

        sector_type: "concept"=概念板块, "industry"=行业板块
        """
        if sector_type not in {"industry", "concept"}:
            raise ValueError("sector_type must be industry or concept")
        param_map = {"concept": "class", "industry": "industry"}
        param = param_map[sector_type]

        try:
            headers = {
                "Referer": "https://finance.sina.com.cn/",
                "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
            }
            resp = await self._client.get(
                self.SINA_SECTOR_URL,
                params={"param": param},
                headers=headers,
            )
            resp.raise_for_status()
            text = resp.content.decode("gbk", errors="replace")
        except Exception as exc:
            return market_error(provider="sina", market="cn", error=exc)
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < 0:
            return market_error(
                provider="sina",
                market="cn",
                error="sector response does not contain a JSON object",
                status=MarketDataStatus.PARSE_ERROR,
            )
        try:
            raw = json.loads(text[start:end + 1])
        except (TypeError, ValueError) as exc:
            return market_error(
                provider="sina",
                market="cn",
                error=exc,
                status=MarketDataStatus.PARSE_ERROR,
            )

        sectors = []
        for key, val in raw.items():
            parts = val.split(",")
            if len(parts) < 13:
                continue
            sectors.append({
                "key": parts[0],
                "name": parts[1],
                "provider_sector_code": parts[0],
                "sector_name": parts[1],
                "sector_type": sector_type,
                "classification": "sina",
                "stock_count": int(parts[2]) if parts[2].isdigit() else 0,
                "latest": float(parts[3]) if parts[3] else None,
                "change_amount": float(parts[4]) if parts[4] else None,
                "change_pct": float(parts[5]) if parts[5] else None,
                "volume": float(parts[6]) if parts[6] else 0,
                "turnover": float(parts[7]) if parts[7] else 0,
                "lead_stock": {
                    "code": parts[8],
                    "change_pct": float(parts[9]) if parts[9] else None,
                    "price": float(parts[10]) if parts[10] else None,
                    "name": parts[12],
                },
                "stockCount": int(parts[2]) if parts[2].isdigit() else 0,
                "avgPrice": float(parts[3]) if parts[3] else None,
                "changeAmt": float(parts[4]) if parts[4] else None,
                "changeRate": float(parts[5]) if parts[5] else None,
                "leadStock": {
                    "code": parts[8],
                    "changeRate": float(parts[9]) if parts[9] else None,
                    "price": float(parts[10]) if parts[10] else None,
                    "name": parts[12],
                },
            })

        sectors.sort(key=lambda item: item["change_pct"] or 0, reverse=True)
        return market_result(
            provider="sina",
            market="cn",
            data={
                "sector_type": sector_type,
                "total": len(sectors),
                "sectors": sectors,
                "top_rise": sectors[:count],
                "top_fall": list(reversed(sectors[-count:])) if sectors else [],
                "topRise": sectors[:count],
                "topFall": list(reversed(sectors[-count:])) if sectors else [],
            },
            timezone_name="Asia/Shanghai",
            provider_metadata={
                "complete": True,
                "ranking_limit": count,
                "turnover_unit": "yuan",
            },
        )

    # ==================== hq.sinajs.cn 实时行情系列 ====================

    async def _fetch_hq(self, codes: str) -> str:
        """请求 hq.sinajs.cn 并返回 GBK 解码后的文本"""
        resp = await self._client.get(
            self.SINA_HQ_URL + "/list=" + codes,
            headers=self.SINA_HEADERS,
        )
        resp.raise_for_status()
        return resp.content.decode("gbk", errors="replace")

    async def get_realtime_quotes(self, symbols: list[str]) -> dict:
        """A股实时行情（批量报价）

        Args:
            symbols: 股票代码列表，如 ["sh600519", "sz000858", "sz300750"]
                     自动补全前缀：纯数字6开头补sh，0/3开头补sz

        Returns:
            {"status_code": 0, "data": {"count": N, "quotes": [...]}}
        """
        # 自动补全前缀
        normed = []
        for s in symbols:
            s = s.strip().lower()
            if s.startswith(("sh", "sz")):
                normed.append(s)
            elif s.startswith("6"):
                normed.append("sh" + s)
            elif s.startswith(("0", "3")):
                normed.append("sz" + s)
            else:
                normed.append(s)

        text = await self._fetch_hq(",".join(normed))

        quotes = []
        for line in text.strip().split("\n"):
            line = line.strip()
            if not line or "=" not in line:
                continue
            # var hq_str_sh600519="贵州茅台,..."
            m = re.match(r'var hq_str_(\w+)="(.+)"', line)
            if not m:
                continue
            code = m.group(1)
            parts = m.group(2).split(",")
            if len(parts) < 32:
                continue

            def _f(idx, default=0.0):
                try:
                    return float(parts[idx])
                except (IndexError, ValueError):
                    return default

            quotes.append({
                "code": code,
                "name": parts[0],
                "open": _f(1),
                "prevClose": _f(2),
                "price": _f(3),
                "high": _f(4),
                "low": _f(5),
                "volume": _f(8),         # 成交量（股）
                "turnover": _f(9),       # 成交额（元）
                "date": parts[30] if len(parts) > 30 else "",
                "time": parts[31] if len(parts) > 31 else "",
            })

        return {"status_code": 0, "data": {"count": len(quotes), "quotes": quotes}}

    async def get_global_index(self, names: list[str] | None = None) -> dict:
        """全球指数实时行情

        Args:
            names: 指数名称或代码列表，如 ["道琼斯", "纳斯达克", "恒生指数"]
                   None 时返回默认的全球主要指数
        """
        if names is None:
            codes = self.DEFAULT_GLOBAL_INDEX_CODES
        else:
            codes = [self.GLOBAL_INDEX_CODES.get(n, n) for n in names]

        try:
            text = await self._fetch_hq(",".join(codes))
        except Exception as exc:
            return market_error(provider="sina", market="global", error=exc)

        indices = []
        for line in text.strip().split("\n"):
            line = line.strip()
            if not line or "=" not in line:
                continue
            m = re.match(r'var hq_str_(\w+)="(.+)"', line)
            if not m:
                continue
            code = m.group(1)
            parts = m.group(2).split(",")

            # 国际指数格式: 名称,当前价,涨跌点,涨跌幅%[,开盘时间,收盘时间]
            if len(parts) >= 4:
                def _f(idx):
                    try:
                        return float(parts[idx])
                    except (IndexError, ValueError):
                        return 0.0

                indices.append({
                    "code": code,
                    "name": parts[0],
                    "price": _f(1),
                    "change": _f(2),
                    "changeRate": _f(3),
                })

        return market_result(
            provider="sina",
            market="global",
            data={"count": len(indices), "indices": indices},
            timezone_name="source_market",
            provider_metadata={"quote_type": "index_snapshot"},
        )

    async def get_forex(self, currencies: list[str] | None = None) -> dict:
        """外汇行情

        Args:
            currencies: 货币名称或代码，如 ["美元", "欧元"] 或 ["usd", "eur"]
                        None 时返回全部 5 种主要外汇
        """
        if currencies is None:
            codes = ["fx_susdcny", "fx_seurcny", "fx_sjpycny", "fx_sgbpcny", "fx_shkdcny"]
        else:
            codes = [self.FOREX_CODES.get(c.lower(), c) for c in currencies]

        try:
            text = await self._fetch_hq(",".join(codes))
        except Exception as exc:
            return market_error(provider="sina", market="forex", error=exc)

        rates = []
        for line in text.strip().split("\n"):
            line = line.strip()
            if not line or "=" not in line:
                continue
            m = re.match(r'var hq_str_(\w+)="(.+)"', line)
            if not m:
                continue
            code = m.group(1)
            parts = m.group(2).split(",")

            # 外汇格式: 时间,当前价,买价,卖价,?,昨收,最高,最低,现价,名称,...
            if len(parts) >= 9:
                def _f(idx):
                    try:
                        return float(parts[idx])
                    except (IndexError, ValueError):
                        return 0.0

                rates.append({
                    "code": code,
                    "name": parts[9] if len(parts) > 9 else code,
                    "price": _f(1),
                    "bid": _f(2),
                    "ask": _f(3),
                    "prevClose": _f(5),
                    "high": _f(6),
                    "low": _f(7),
                    "time": parts[0],
                })

        source_time = next((item.get("time") for item in rates if item.get("time")), None)
        return market_result(
            provider="sina",
            market="forex",
            data={"count": len(rates), "rates": rates},
            source_time=source_time,
            timezone_name="Asia/Shanghai",
            provider_metadata={"price_unit": "CNY per quoted currency"},
        )

    async def get_futures(self, names: list[str] | None = None) -> dict:
        """期货行情

        Args:
            names: 期货名称或代码，如 ["黄金", "原油"] 或 ["au", "sc"]
                   None 时返回全部 5 种主要期货
        """
        if names is None:
            codes = ["nf_AU0", "nf_CU0", "nf_SC0", "nf_AG0", "nf_I0"]
        else:
            codes = [self.FUTURES_CODES.get(n.lower(), n) for n in names]

        try:
            text = await self._fetch_hq(",".join(codes))
        except Exception as exc:
            return market_error(provider="sina", market="futures", error=exc)

        futures = []
        for line in text.strip().split("\n"):
            line = line.strip()
            if not line or "=" not in line:
                continue
            m = re.match(r'var hq_str_(\w+)="(.+)"', line)
            if not m:
                continue
            code = m.group(1)
            parts = m.group(2).split(",")

            if len(parts) >= 8:
                def _f(idx):
                    try:
                        return float(parts[idx])
                    except (IndexError, ValueError):
                        return 0.0

                futures.append({
                    "code": code,
                    "name": parts[0] if parts[0] else code,
                    "exchange": self._domestic_futures_exchange(code),
                    "contract": code.removeprefix("nf_"),
                    "is_main_contract": code.endswith("0"),
                    "price": _f(6),       # 当前价
                    "open": _f(2),
                    "high": _f(3),
                    "low": _f(4),
                    "previous_close": _f(5),
                    "volume": _f(7) if len(parts) > 7 else 0,
                    "date": parts[17] if len(parts) > 17 else "",
                    "currency": "CNY",
                })

        trade_date = next((item.get("date") for item in futures if item.get("date")), None)
        return market_result(
            provider="sina",
            market="futures",
            data={"count": len(futures), "futures": futures},
            trade_date=trade_date,
            timezone_name="Asia/Shanghai",
            provider_metadata={"quote_type": "domestic_main_continuous_snapshot"},
        )

    async def get_bond_futures(self) -> dict:
        """获取十年与二年国债期货连续合约快照。"""

        codes = ["nf_T0", "nf_TS0"]
        try:
            text = await self._fetch_hq(",".join(codes))
        except Exception as exc:
            return market_error(provider="sina", market="cn", error=exc)

        items = []
        for line in text.strip().split("\n"):
            line = line.strip()
            match = re.match(r'var hq_str_(\w+)="(.*)";', line)
            if not match:
                continue
            code = match.group(1)
            parts = match.group(2).split(",")
            if len(parts) < 50:
                continue

            def number(position: int) -> float | None:
                try:
                    return float(parts[position])
                except (IndexError, ValueError):
                    return None

            items.append(
                {
                    "code": code,
                    "contract": code.removeprefix("nf_"),
                    "name": parts[49] or code,
                    "open": number(0),
                    "high": number(1),
                    "low": number(2),
                    "price": number(3),
                    "volume": number(4),
                    "turnover": number(5),
                    "open_interest": number(6),
                    "settlement": number(26),
                    "date": parts[36],
                    "time": parts[37],
                    "exchange": "CFFEX",
                    "currency": "CNY",
                    "is_main_contract": True,
                }
            )
        trade_date = next(
            (item.get("date") for item in items if item.get("date")),
            None,
        )
        source_time = next(
            (item.get("time") for item in items if item.get("time")),
            None,
        )
        return market_result(
            provider="sina",
            market="cn",
            data={"count": len(items), "futures": items},
            source_time=source_time,
            trade_date=trade_date,
            timezone_name="Asia/Shanghai",
            provider_metadata={
                "quote_type": "government_bond_main_continuous_snapshot",
            },
        )

    async def get_benchmark_kline(
        self,
        market: str,
        symbol: str,
        limit: int = 250,
    ) -> dict:
        """获取 A股、港股或美股基准指数日 K 线。"""
        fetchers = {
            "cn": (ak.stock_zh_index_daily, {"symbol": symbol}, "Asia/Shanghai"),
            "hk": (ak.stock_hk_index_daily_sina, {"symbol": symbol}, "Asia/Hong_Kong"),
            "us": (ak.index_us_stock_sina, {"symbol": symbol}, "America/New_York"),
        }
        if market not in fetchers:
            raise ValueError("market must be cn, hk or us")
        fetcher, kwargs, timezone_name = fetchers[market]
        try:
            frame = await asyncio.to_thread(fetcher, **kwargs)
            if limit > 0:
                frame = frame.tail(limit)
            bars = [
                {
                    "date": row.get("date"),
                    "open": row.get("open"),
                    "high": row.get("high"),
                    "low": row.get("low"),
                    "close": row.get("close"),
                    "volume": row.get("volume"),
                    "turnover": row.get("amount"),
                }
                for row in frame.to_dict("records")
            ]
            return market_result(
                provider="sina",
                market=market,
                data={
                    "symbol": symbol,
                    "interval": "1d",
                    "count": len(bars),
                    "bars": bars,
                },
                trade_date=bars[-1]["date"] if bars else None,
                timezone_name=timezone_name,
                provider_metadata={"asset_type": "benchmark_index"},
            )
        except Exception as exc:
            return market_error(provider="sina", market=market, error=exc)

    async def get_commodity_kline(
        self,
        symbol: str,
        *,
        international: bool = False,
        start_date: str = "19900101",
        end_date: str = "22220101",
        limit: int = 250,
    ) -> dict:
        """获取国内主力连续或国际商品期货日 K 线。"""
        try:
            if international:
                frame = await asyncio.to_thread(ak.futures_foreign_hist, symbol=symbol)
                market = "global"
                exchange = "international"
                columns = {
                    "date": "date",
                    "open": "open",
                    "high": "high",
                    "low": "low",
                    "close": "close",
                    "volume": "volume",
                    "open_interest": "position",
                    "settlement": "settlement",
                }
                currency = "USD"
            else:
                frame = await asyncio.to_thread(
                    ak.futures_main_sina,
                    symbol=symbol,
                    start_date=start_date,
                    end_date=end_date,
                )
                market = "cn"
                exchange = self._domestic_futures_exchange("nf_" + symbol)
                columns = {
                    "date": "日期",
                    "open": "开盘价",
                    "high": "最高价",
                    "low": "最低价",
                    "close": "收盘价",
                    "volume": "成交量",
                    "open_interest": "持仓量",
                    "settlement": "动态结算价",
                }
                currency = "CNY"
            if limit > 0:
                frame = frame.tail(limit)
            bars = [
                {
                    output_name: row.get(source_name)
                    for output_name, source_name in columns.items()
                }
                for row in frame.to_dict("records")
            ]
            return market_result(
                provider="sina",
                market=market,
                data={
                    "symbol": symbol,
                    "exchange": exchange,
                    "contract": symbol,
                    "is_main_contract": not international and symbol.endswith("0"),
                    "currency": currency,
                    "interval": "1d",
                    "count": len(bars),
                    "bars": bars,
                },
                trade_date=bars[-1]["date"] if bars else None,
                timezone_name="Asia/Shanghai" if not international else "source_market",
                provider_metadata={"continuous_contract": not international},
            )
        except Exception as exc:
            return market_error(
                provider="sina",
                market="global" if international else "cn",
                error=exc,
            )

    async def get_futures_term_structure(
        self,
        root_symbol: str,
        *,
        exchange: str = "SHFE",
        trade_date: date | str | None = None,
        contract_limit: int = 8,
    ) -> dict:
        """获取国内商品期货有效合约目录和期限结构快照。"""
        if contract_limit < 1:
            raise ValueError("contract_limit must be greater than zero")
        exchange = exchange.upper()
        root_symbol = root_symbol.upper()
        requested_date = self._normalize_contract_trade_date(trade_date)
        try:
            if exchange == "DCE":
                return await self._get_dce_futures_term_structure(
                    root_symbol,
                    requested_date=requested_date,
                    contract_limit=contract_limit,
                )
            effective_date = requested_date
            if exchange == "INE":
                contract_frame, effective_date = await self._fetch_ine_contracts(
                    requested_date
                )
            else:
                contract_frame = await asyncio.to_thread(
                    self._fetch_futures_contracts,
                    exchange,
                    requested_date,
                )
            contracts = self._normalize_active_contracts(
                contract_frame,
                root_symbol=root_symbol,
                exchange=exchange,
                trade_date=effective_date,
                limit=contract_limit,
            )
            quotes = await asyncio.gather(
                *[
                    asyncio.to_thread(
                        self._fetch_futures_contract_quote,
                        contract["contract_code"],
                    )
                    for contract in contracts
                ],
                return_exceptions=True,
            )
            curve = []
            quote_errors = []
            for contract, quote in zip(contracts, quotes, strict=True):
                if isinstance(quote, Exception):
                    quote_errors.append(
                        {
                            "contract_code": contract["contract_code"],
                            "error_type": type(quote).__name__,
                            "message": str(quote),
                        }
                    )
                    continue
                curve.append({**contract, **quote})

            if contracts and not curve:
                return market_error(
                    provider="sina",
                    market="cn",
                    error="all futures contract quote requests failed",
                    provider_metadata={
                        "exchange": exchange,
                        "root_symbol": root_symbol,
                        "quote_errors": quote_errors,
                    },
                )

            main_contract = max(
                curve,
                key=lambda item: item.get("open_interest") or 0,
                default=None,
            )
            main_contract_code = (
                main_contract["contract_code"] if main_contract else None
            )
            for item in curve:
                item["is_main_contract"] = (
                    item["contract_code"] == main_contract_code
                )

            return market_result(
                provider="sina",
                market="cn",
                data={
                    "root_symbol": root_symbol,
                    "exchange": exchange,
                    "currency": "CNY",
                    "count": len(curve),
                    "contracts": curve,
                    "main_contract_code": main_contract_code,
                },
                source_time=next(
                    (item.get("source_time") for item in curve if item.get("source_time")),
                    None,
                ),
                trade_date=effective_date,
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "contract_source": (
                        "ine_official_contract_file"
                        if exchange == "INE"
                        else f"akshare.futures_contract_info_{exchange.lower()}"
                    ),
                    "quote_source": "akshare.futures_zh_spot",
                    "main_contract_method": "maximum_open_interest_in_returned_curve",
                    "requested_contract_limit": contract_limit,
                    "requested_trade_date": requested_date.isoformat(),
                    "effective_trade_date": effective_date.isoformat(),
                    "quote_errors": quote_errors,
                },
            )
        except ValueError:
            raise
        except Exception as exc:
            return market_error(
                provider="sina",
                market="cn",
                error=exc,
                provider_metadata={
                    "exchange": exchange,
                    "root_symbol": root_symbol,
                },
            )

    async def _fetch_ine_contracts(self, requested_date: date):
        errors = []
        for offset in range(8):
            candidate_date = requested_date - timedelta(days=offset)
            if candidate_date.weekday() >= 5:
                continue
            date_text = candidate_date.strftime("%Y%m%d")
            try:
                response = await self._client.get(
                    (
                        "https://www.ine.cn/data/busiparamdata/future/"
                        f"ContractBaseInfo{date_text}.dat"
                    ),
                    params={"rnd": "0.8312696798757147"},
                    headers={
                        "Referer": "https://www.ine.cn/bourseService/summary/",
                        "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
                    },
                    timeout=10,
                )
                response.raise_for_status()
                rows = response.json().get("ContractBaseInfo") or []
                if not rows:
                    continue
                frame = pd.DataFrame(rows).rename(
                    columns={
                        "INSTRUMENTID": "合约代码",
                        "OPENDATE": "上市日",
                        "EXPIREDATE": "到期日",
                        "ENDDELIVDATE": "最后交割日",
                    }
                )
                return frame, candidate_date
            except Exception as exc:
                errors.append(
                    {
                        "trade_date": candidate_date.isoformat(),
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    }
                )
        raise RuntimeError(f"INE contract directory unavailable: {errors}")

    async def _get_dce_futures_term_structure(
        self,
        root_symbol: str,
        *,
        requested_date: date,
        contract_limit: int,
    ) -> dict:
        product_name = self.DCE_PRODUCT_NAMES.get(root_symbol)
        if product_name is None:
            raise ValueError(f"unsupported DCE root symbol: {root_symbol}")
        frame = await asyncio.to_thread(ak.futures_zh_realtime, symbol=product_name)
        contracts = []
        pattern = re.compile(rf"^{re.escape(root_symbol)}(\d{{4}})$")
        for row in frame.to_dict("records"):
            contract_code = str(row.get("symbol") or "").upper()
            matched = pattern.fullmatch(contract_code)
            if not matched:
                continue
            contract_month = self._contract_month(matched.group(1))
            if contract_month is None:
                continue
            contracts.append(
                {
                    "contract_code": contract_code,
                    "root_symbol": root_symbol,
                    "exchange": "DCE",
                    "contract_month": contract_month,
                    "listed_at": None,
                    "expires_at": None,
                    "delivery_date": None,
                    "open": self._optional_number(row.get("open")),
                    "high": self._optional_number(row.get("high")),
                    "low": self._optional_number(row.get("low")),
                    "last_price": self._optional_number(row.get("trade")),
                    "settlement": self._optional_number(row.get("settlement")),
                    "previous_settlement": self._optional_number(
                        row.get("presettlement")
                    ),
                    "bid_price": self._optional_number(row.get("bidprice1")),
                    "ask_price": self._optional_number(row.get("askprice1")),
                    "volume": self._optional_number(row.get("volume")),
                    "open_interest": self._optional_number(row.get("position")),
                    "source_time": row.get("ticktime"),
                    "source_trade_date": row.get("tradedate"),
                }
            )
        contracts.sort(
            key=lambda item: (item["contract_month"], item["contract_code"])
        )
        contracts = contracts[:contract_limit]
        main_contract = max(
            contracts,
            key=lambda item: item.get("open_interest") or 0,
            default=None,
        )
        main_contract_code = (
            main_contract["contract_code"] if main_contract else None
        )
        for item in contracts:
            item["is_main_contract"] = (
                item["contract_code"] == main_contract_code
            )
        source_trade_date = next(
            (
                item["source_trade_date"]
                for item in contracts
                if item.get("source_trade_date")
            ),
            None,
        )
        return market_result(
            provider="sina",
            market="cn",
            data={
                "root_symbol": root_symbol,
                "exchange": "DCE",
                "currency": "CNY",
                "count": len(contracts),
                "contracts": contracts,
                "main_contract_code": main_contract_code,
            },
            source_time=next(
                (
                    item["source_time"]
                    for item in contracts
                    if item.get("source_time")
                ),
                None,
            ),
            trade_date=source_trade_date or requested_date,
            timezone_name="Asia/Shanghai",
            provider_metadata={
                "contract_source": "akshare.futures_zh_realtime",
                "quote_source": "akshare.futures_zh_realtime",
                "main_contract_method": "maximum_open_interest_in_returned_curve",
                "requested_contract_limit": contract_limit,
                "requested_trade_date": requested_date.isoformat(),
                "exact_expiration_available": False,
            },
        )

    @staticmethod
    def _contract_month(value: str) -> str | None:
        if len(value) != 4:
            return None
        month = int(value[2:])
        if not 1 <= month <= 12:
            return None
        return f"20{value[:2]}-{value[2:]}"

    @staticmethod
    def _optional_number(value):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if number == number else None

    @staticmethod
    def _normalize_contract_trade_date(value: date | str | None) -> date:
        if value is None:
            return datetime.now().date()
        if isinstance(value, date):
            return value
        normalized = str(value).replace("-", "")
        return datetime.strptime(normalized, "%Y%m%d").date()

    @staticmethod
    def _fetch_futures_contracts(exchange: str, trade_date: date):
        date_text = trade_date.strftime("%Y%m%d")
        if exchange == "SHFE":
            return ak.futures_contract_info_shfe(date=date_text)
        if exchange == "CZCE":
            return ak.futures_contract_info_czce(date=date_text)
        if exchange == "GFEX":
            return ak.futures_contract_info_gfex()
        raise ValueError(f"unsupported futures exchange: {exchange}")

    @staticmethod
    def _normalize_active_contracts(
        frame,
        *,
        root_symbol: str,
        exchange: str,
        trade_date: date,
        limit: int,
    ) -> list[dict]:
        expiration_columns = (
            "到期日",
            "最后交易日待国家公布2025年节假日安排后进行调整",
            "最后交易日",
        )
        listing_columns = ("上市日", "第一交易日", "开始交易日")
        contracts = []
        for row in frame.to_dict("records"):
            code = str(row.get("合约代码") or "").strip().upper()
            product_code = str(row.get("产品代码") or "").strip().upper()
            if not code or not (
                code.startswith(root_symbol)
                or product_code == root_symbol
            ):
                continue
            expiration = next(
                (
                    row.get(column)
                    for column in expiration_columns
                    if row.get(column) is not None
                ),
                None,
            )
            expiration_date = SinaClient._coerce_contract_date(expiration)
            if expiration_date is None or expiration_date < trade_date:
                continue
            listing = next(
                (
                    row.get(column)
                    for column in listing_columns
                    if row.get(column) is not None
                ),
                None,
            )
            contracts.append(
                {
                    "contract_code": code,
                    "root_symbol": root_symbol,
                    "exchange": exchange,
                    "listed_at": SinaClient._coerce_contract_date(listing),
                    "expires_at": expiration_date,
                    "delivery_date": SinaClient._coerce_contract_date(
                        row.get("最后交割日")
                    ),
                }
            )
        contracts.sort(key=lambda item: (item["expires_at"], item["contract_code"]))
        return contracts[:limit]

    @staticmethod
    def _coerce_contract_date(value) -> date | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value)
        match = re.search(r"\d{4}-\d{2}-\d{2}", text)
        if match:
            return date.fromisoformat(match.group(0))
        compact_match = re.search(r"\d{8}", text)
        if compact_match:
            return datetime.strptime(compact_match.group(0), "%Y%m%d").date()
        return None

    @staticmethod
    def _fetch_futures_contract_quote(contract_code: str) -> dict:
        frame = ak.futures_zh_spot(
            symbol=contract_code,
            market="CF",
            adjust="0",
        )
        if frame.empty:
            raise ValueError(f"empty futures quote: {contract_code}")
        row = frame.iloc[0].to_dict()
        return {
            "contract_name": row.get("symbol"),
            "source_time": row.get("time"),
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "latest": row.get("current_price"),
            "bid": row.get("bid_price"),
            "ask": row.get("ask_price"),
            "volume": row.get("volume"),
            "open_interest": row.get("hold"),
            "previous_close": row.get("last_close"),
            "previous_settlement": row.get("last_settle_price"),
        }

    @staticmethod
    def _domestic_futures_exchange(code: str) -> str | None:
        contract = code.removeprefix("nf_").rstrip("0123456789").upper()
        if contract in {"AU", "AG", "CU", "AL", "ZN", "PB", "NI", "SN", "SC"}:
            return "SHFE"
        if contract in {"I", "J", "JM", "C", "M", "Y", "P", "L", "V", "PP"}:
            return "DCE"
        if contract in {"TA", "MA", "SR", "CF", "FG", "RM", "OI"}:
            return "CZCE"
        return None

    # ==================== K线 / 新闻 / 资金流 / 基金净值 ====================

    async def get_kline(self, symbol: str, scale: int = 240, datalen: int = 20) -> dict:
        """K线数据

        Args:
            symbol: 股票/指数代码，如 "sh000001", "sh600519"
            scale: K线周期（分钟）：5/15/30/60/240
            datalen: 返回条数
        """
        resp = await self._client.get(
            self.SINA_KLINE_URL,
            params={"symbol": symbol, "scale": scale, "ma": "no", "datalen": datalen},
            headers=self.SINA_HEADERS,
        )
        resp.raise_for_status()
        bars = resp.json()
        if bars is None:
            bars = []
        if not isinstance(bars, list):
            return {
                "status_code": -1,
                "data": None,
                "msg": "K线响应格式异常",
            }

        items = []
        for bar in bars:
            items.append({
                "date": bar.get("day", ""),
                "open": float(bar.get("open", 0)),
                "close": float(bar.get("close", 0)),
                "high": float(bar.get("high", 0)),
                "low": float(bar.get("low", 0)),
                "volume": float(bar.get("volume", 0)),
            })

        return {"status_code": 0, "data": {
            "symbol": symbol, "scale": scale, "count": len(items), "bars": items,
        }}

    SINA_ARTICLE_PATTERNS = [
        r'<div[^>]*id="artibody"[^>]*>',
        r'<div[^>]*class="article-content"[^>]*>',
        r'<div[^>]*class="article"[^>]*>',
    ]

    @cached(ttl=1209600, source="sina", domain="news", frequency="daily",
            market="a_share", source_name="新浪财经")
    async def fetch_article_content(self, url: str) -> str:
        """抓取新浪财经文章正文"""
        html = await self._fetch_article_html(url, referer="https://finance.sina.com.cn/")
        return self._extract_article_text(html, self.SINA_ARTICLE_PATTERNS)

    # 不缓存：滚动列表翻页，page=N 在不同时间返回不同数据
    async def get_news(self, num: int = 20, page: int = 1, with_content: bool = True) -> dict:
        """财经新闻（列表 + 正文）

        Args:
            num: 每页条数
            page: 页码
            with_content: 是否抓取每篇文章的真实正文（并发请求）
        """
        import asyncio

        resp = await self._client.get(
            self.SINA_NEWS_URL,
            params={"pageid": 153, "lid": 2509, "num": num, "page": page},
            headers=self.SINA_HEADERS,
        )
        resp.raise_for_status()
        data = resp.json()

        result = data.get("result", {})
        raw_items = result.get("data", [])

        # 并发抓取所有文章的正文
        contents = [""] * len(raw_items)
        if with_content and raw_items:
            urls = [item.get("url", "") for item in raw_items]
            tasks = [self.fetch_article_content(u) for u in urls]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            contents = [r if isinstance(r, str) else "" for r in results]

        articles = []
        for item, content in zip(raw_items, contents):
            articles.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "source": item.get("media_name", ""),
                "summary": item.get("intro", ""),     # 列表页摘要（保留）
                "content": content,                    # 真实正文（新增）
                "time": item.get("ctime", ""),
            })

        return {"status_code": 0, "data": {
            "count": len(articles), "total": result.get("num", 0), "articles": articles,
        }}

    async def get_sector_money_flow(self, sector_type: str = "concept", count: int = 20) -> dict:
        """板块资金流

        Args:
            sector_type: "concept"=概念板块, "industry"=行业板块
            count: 返回条数
        """
        if sector_type not in {"industry", "concept"}:
            raise ValueError("sector_type must be industry or concept")
        fenlei = 1 if sector_type == "concept" else 0
        try:
            resp = await self._client.get(
                self.SINA_MONEYFLOW_URL,
                params={"page": 1, "num": count, "sort": "netamount", "asc": 0, "fenlei": fenlei},
                headers=self.SINA_HEADERS,
            )
            resp.raise_for_status()
            items = resp.json()
            if not isinstance(items, list):
                raise TypeError("money flow response must be a list")
        except (TypeError, ValueError) as exc:
            return market_error(
                provider="sina",
                market="cn",
                error=exc,
                status=MarketDataStatus.PARSE_ERROR,
            )
        except Exception as exc:
            return market_error(provider="sina", market="cn", error=exc)

        sectors = []
        for item in items:
            def _f(k):
                try:
                    return float(item.get(k, 0))
                except (TypeError, ValueError):
                    return 0.0

            sectors.append({
                "name": item.get("name", ""),
                "provider_sector_code": item.get("symbol") or item.get("code"),
                "sector_name": item.get("name", ""),
                "sector_type": sector_type,
                "main_net_inflow": _f("netamount"),
                "super_large_inflow": _f("r0x_in"),
                "super_large_outflow": _f("r0x_out"),
                "large_inflow": _f("r1_in"),
                "large_outflow": _f("r1_out"),
                "medium_inflow": _f("r2_in"),
                "medium_outflow": _f("r2_out"),
                "small_inflow": _f("r3_in"),
                "small_outflow": _f("r3_out"),
                "unit": "yuan",
                "currency": "CNY",
                "netAmount": round(_f("netamount") / 10000, 2),
                "bigIn": round(_f("r0x_in") / 10000, 2),
                "bigOut": round(_f("r0x_out") / 10000, 2),
                "largeIn": round(_f("r1_in") / 10000, 2),
                "largeOut": round(_f("r1_out") / 10000, 2),
                "midIn": round(_f("r2_in") / 10000, 2),
                "midOut": round(_f("r2_out") / 10000, 2),
                "smallIn": round(_f("r3_in") / 10000, 2),
                "smallOut": round(_f("r3_out") / 10000, 2),
            })

        return market_result(
            provider="sina",
            market="cn",
            data={"sector_type": sector_type, "count": len(sectors), "sectors": sectors},
            timezone_name="Asia/Shanghai",
            provider_metadata={
                "complete": False,
                "ranking_limit": count,
                "money_flow_method": "sina_moneyflow_ssl_bkzj_bk",
            },
        )

    async def get_etf_catalog(self) -> dict:
        """获取新浪全量 ETF 行情目录。"""
        try:
            frame = await asyncio.to_thread(ak.fund_etf_category_sina, symbol="ETF基金")
            etfs = []
            for row in frame.to_dict("records"):
                raw_code = str(row.get("代码") or "")
                if not raw_code:
                    continue
                etfs.append(
                    {
                        "code": raw_code[2:] if raw_code[:2] in {"sh", "sz"} else raw_code,
                        "symbol": raw_code,
                        "market": raw_code[:2] if raw_code[:2] in {"sh", "sz"} else None,
                        "name": row.get("名称"),
                        "price": row.get("最新价"),
                        "change_amount": row.get("涨跌额"),
                        "change_pct": row.get("涨跌幅"),
                        "previous_close": row.get("昨收"),
                        "open": row.get("今开"),
                        "high": row.get("最高"),
                        "low": row.get("最低"),
                        "volume": row.get("成交量"),
                        "turnover": row.get("成交额"),
                        "trading_status": (
                            "unknown"
                            if row.get("最新价") not in (None, "")
                            else "no_valid_quote"
                        ),
                    }
                )
            return market_result(
                provider="sina",
                market="cn",
                data={"count": len(etfs), "etfs": etfs},
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "complete": True,
                    "volume_unit": "share",
                    "turnover_unit": "yuan",
                },
            )
        except Exception as exc:
            return market_error(provider="sina", market="cn", error=exc)

    async def get_etf_kline(self, symbol: str, limit: int = 250) -> dict:
        """获取 ETF 日 K 线，symbol 使用 sh510300/sz159915 格式。"""
        normalized = symbol.lower()
        if not normalized.startswith(("sh", "sz")):
            normalized = ("sh" if normalized.startswith("5") else "sz") + normalized
        try:
            frame = await asyncio.to_thread(ak.fund_etf_hist_sina, symbol=normalized)
            if limit > 0:
                frame = frame.tail(limit)
            bars = [
                {
                    "date": row.get("date"),
                    "open": row.get("open"),
                    "high": row.get("high"),
                    "low": row.get("low"),
                    "close": row.get("close"),
                    "volume": row.get("volume"),
                    "turnover": row.get("amount"),
                }
                for row in frame.to_dict("records")
            ]
            return market_result(
                provider="sina",
                market="cn",
                data={
                    "symbol": normalized,
                    "interval": "1d",
                    "adjustment": "none",
                    "count": len(bars),
                    "bars": bars,
                },
                trade_date=bars[-1]["date"] if bars else None,
                timezone_name="Asia/Shanghai",
                provider_metadata={"turnover_unit": "yuan", "volume_unit": "share"},
            )
        except Exception as exc:
            return market_error(provider="sina", market="cn", error=exc)

    @cached(ttl=1209600, source="sina", domain="market", frequency="daily",
            market="fund", source_name="新浪财经")
    async def get_fund_nav(self, symbol: str, date_from: str = "", date_to: str = "") -> dict:
        """基金净值

        Args:
            symbol: 基金代码，如 "110022"
            date_from: 起始日期，如 "2026-03-10"，默认取最近 30 天
            date_to: 结束日期，如 "2026-03-19"，默认今天
        """
        from datetime import datetime, timedelta
        if not date_to:
            date_to = datetime.now().strftime("%Y-%m-%d")
        if not date_from:
            date_from = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        resp = await self._client.get(
            self.SINA_FUND_NAV_URL,
            params={"symbol": symbol, "datefrom": date_from, "dateto": date_to},
            headers=self.SINA_HEADERS,
        )
        resp.raise_for_status()
        data = resp.json()

        # 响应结构: {"result":{"status":{"code":0},"data":{"data":[...], "total_num": N}}}
        result_data = data.get("result", {}).get("data", {})
        nav_list = result_data.get("data", [])

        items = []
        for item in nav_list:
            items.append({
                "date": item.get("fbrq", ""),
                "nav": float(item.get("jjjz", 0)),       # 单位净值
                "accNav": float(item.get("ljjz", 0)),     # 累计净值
            })

        return {"status_code": 0, "data": {
            "symbol": symbol, "count": len(items), "navs": items,
        }}
