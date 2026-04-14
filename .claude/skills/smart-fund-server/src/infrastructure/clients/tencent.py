"""腾讯证券客户端（web.sqt.gtimg.cn / ifzq.gtimg.cn / proxy.finance.qq.com）"""

import json as _json
import re

from src.infrastructure.clients.base import BaseClient, cached


class TencentClient(BaseClient):
    """腾讯证券数据客户端"""

    QT_URL = "https://qt.gtimg.cn"
    SQT_URL = "https://web.sqt.gtimg.cn"
    PROXY_URL = "https://proxy.finance.qq.com"
    IFZQ_URL = "https://web.ifzq.gtimg.cn"

    REFERER = {"Referer": "https://gu.qq.com/"}

    # 国际期货代码映射
    INTL_FUTURES = {
        "黄金": "hf_GC", "白银": "hf_SI", "原油": "hf_CL",
        "天然气": "hf_NG", "铜": "hf_HG", "大豆": "hf_S",
        "玉米": "hf_C", "小麦": "hf_W",
        "gc": "hf_GC", "si": "hf_SI", "cl": "hf_CL",
        "ng": "hf_NG", "hg": "hf_HG", "s": "hf_S",
        "c": "hf_C", "w": "hf_W",
    }

    def __init__(self, timeout: float = 10.0):
        super().__init__(timeout)

    @staticmethod
    def _stock_tencent(code: str) -> str:
        """股票代码转腾讯行情格式: 6开头→sh{code}, 其他→sz{code}"""
        code = code.strip()
        if code.startswith(("sh", "sz", "hk", "us", "r_")):
            return code
        if code.startswith(("6", "9")):
            return f"sh{code}"
        return f"sz{code}"

    async def get_stock_quote(self, codes: list[str]) -> dict:
        """从腾讯证券获取实时行情（支持批量，最多20只）

        codes: 股票代码列表，如 ["600519", "000001"]
        """
        codes = codes[:20]
        tencent_codes = ",".join(self._stock_tencent(c) for c in codes)
        resp = await self._client.get(
            f"https://web.sqt.gtimg.cn/q={tencent_codes}",
            headers={"User-Agent": self.DEFAULT_HEADERS["User-Agent"]},
            timeout=10,
        )
        resp.raise_for_status()
        text = resp.content.decode("gbk", errors="replace")

        stocks = []
        for line in text.strip().split(";"):
            line = line.strip()
            if not line or "=" not in line:
                continue
            raw = line.split("=", 1)[1].strip('" \n')
            parts = raw.split("~")
            if len(parts) < 50:
                continue
            try:
                stocks.append({
                    "name": parts[1],
                    "code": parts[2],
                    "price": float(parts[3]) if parts[3] else None,
                    "prevClose": float(parts[4]) if parts[4] else None,
                    "open": float(parts[5]) if parts[5] else None,
                    "high": float(parts[33]) if parts[33] else None,
                    "low": float(parts[34]) if parts[34] else None,
                    "volume": int(parts[36]) if parts[36] else 0,  # 手
                    "turnover": float(parts[37]) * 10000 if parts[37] else 0,  # 万→元
                    "changeAmt": float(parts[31]) if parts[31] else 0,
                    "changeRate": float(parts[32]) if parts[32] else 0,
                    "amplitude": float(parts[43]) if parts[43] else 0,
                    "turnoverRate": float(parts[38]) if parts[38] else 0,
                    "pe": float(parts[39]) if parts[39] else None,
                    "pb": float(parts[46]) if parts[46] else None,
                    "marketCap": float(parts[44]) * 1e8 if parts[44] else None,  # 亿→元
                    "time": parts[30] if len(parts) > 30 else "",
                })
            except (ValueError, IndexError):
                continue

        return {"status_code": 0, "data": {"stocks": stocks, "total": len(stocks)}}

    async def get_stock_batch_quote(self, query_codes: list[str]) -> str:
        """批量获取股票行情原始数据 (web.sqt.gtimg.cn)

        Args:
            query_codes: 腾讯格式代码列表，如 ["sh600519", "sz000858"]
        Returns:
            原始 GBK 解码后的文本
        """
        resp = await self._client.get(
            f"{self.SQT_URL}/q={','.join(query_codes)}",
            headers={"User-Agent": self.DEFAULT_HEADERS["User-Agent"]},
        )
        return resp.content.decode("gbk", errors="replace")

    # ==================== 个股资金流向 ====================

    async def get_stock_fund_flow(self, code: str, history_days: int = 10) -> dict:
        """个股资金流向（今日汇总 + 逐分钟趋势 + 5日 + 历史N日）

        Args:
            code: 股票代码，如 "600519" 或 "sh600519"
            history_days: 历史天数（最多20）
        """
        tc = self._stock_tencent(code)
        resp = await self._client.get(
            f"{self.PROXY_URL}/cgi/cgi-bin/fundflow/hsfundtab",
            params={
                "code": tc,
                "type": "todayFundFlow,todayFundTrend,fiveDayFundFlow,historyFundFlow",
                "klineNeedDay": min(history_days, 20),
            },
            headers=self.REFERER,
        )
        resp.raise_for_status()
        raw = resp.json()
        if raw.get("code") != 0:
            return {"status_code": -1, "msg": raw.get("msg", ""), "data": {}}

        data = raw.get("data", {})
        result = {}

        # 今日汇总
        today = data.get("todayFundFlow", {})
        if today:
            def _i(k):
                try:
                    return int(today.get(k, 0))
                except (TypeError, ValueError):
                    return 0
            result["today"] = {
                "mainNetIn": _i("mainNetIn"),
                "mainIn": _i("mainIn"),
                "mainOut": _i("mainOut"),
                "mainInRate": today.get("mainInRate", ""),
                "retailNetIn": _i("retailNetIn") if "retailNetIn" in today else None,
            }

        # 逐分钟趋势
        trend = data.get("todayFundTrend", {})
        if trend and "minList" in trend:
            minutes = []
            for m in trend["minList"]:
                minutes.append({
                    "time": m.get("time", ""),
                    "price": m.get("Price", ""),
                    "mainNetIn": int(m.get("MainNetInflow", 0)),
                    "retailNetIn": int(m.get("RetailNetInflow", 0)),
                })
            result["minuteTrend"] = minutes

        # 5日汇总
        five = data.get("fiveDayFundFlow", {})
        if five:
            result["fiveDayNetIn"] = int(five.get("fiveDayMainNetIn", 0))
            result["fiveDayList"] = [
                {"date": d.get("date", ""), "mainNetIn": int(d.get("mainNetIn", 0))}
                for d in five.get("DayMainNetInList", [])
            ]

        # 历史
        hist = data.get("historyFundFlow", {})
        if hist and "oneDayKlineList" in hist:
            result["history"] = [
                {
                    "date": d.get("date", ""),
                    "mainNetIn": int(d.get("mainNetIn", 0)),
                    "price": d.get("price", ""),
                }
                for d in hist["oneDayKlineList"]
            ]

        return {"status_code": 0, "data": {"code": tc, **result}}

    # ==================== 分时数据 ====================

    async def get_minute_data(self, code: str) -> dict:
        """个股分时数据（逐分钟 价格+成交量+成交额）

        Args:
            code: 股票代码，如 "600519" 或 "sh600519"
        """
        tc = self._stock_tencent(code)
        resp = await self._client.get(
            f"{self.IFZQ_URL}/appstock/app/minute/query",
            params={"code": tc},
        )
        resp.raise_for_status()
        raw = resp.json()

        points_raw = raw.get("data", {}).get(tc, {}).get("data", {}).get("data", [])
        points = []
        for p in points_raw:
            parts = p.split(" ")
            if len(parts) >= 4:
                points.append({
                    "time": parts[0],
                    "price": float(parts[1]),
                    "volume": int(parts[2]),
                    "turnover": float(parts[3]),
                })

        return {"status_code": 0, "data": {"code": tc, "count": len(points), "points": points}}

    # ==================== 港股行情 ====================

    async def get_hk_quote(self, codes: list[str]) -> dict:
        """港股实时行情（支持批量）

        Args:
            codes: 港股代码列表，如 ["00700", "09988", "01810"]
        """
        tc_codes = ",".join(f"r_hk{c.replace('hk','')}" for c in codes)
        resp = await self._client.get(
            f"{self.SQT_URL}/q={tc_codes}",
            headers={"User-Agent": self.DEFAULT_HEADERS["User-Agent"]},
        )
        resp.raise_for_status()
        text = resp.content.decode("gbk", errors="replace")

        stocks = []
        for line in text.strip().split(";"):
            line = line.strip()
            if not line or "=" not in line:
                continue
            raw = line.split("=", 1)[1].strip('" \n')
            parts = raw.split("~")
            if len(parts) < 50:
                continue
            try:
                stocks.append({
                    "name": parts[1],
                    "code": parts[2],
                    "price": float(parts[3]) if parts[3] else None,
                    "prevClose": float(parts[4]) if parts[4] else None,
                    "open": float(parts[5]) if parts[5] else None,
                    "high": float(parts[33]) if parts[33] else None,
                    "low": float(parts[34]) if parts[34] else None,
                    "volume": float(parts[36]) if parts[36] else 0,
                    "turnover": float(parts[37]) if parts[37] else 0,
                    "changeAmt": float(parts[31]) if parts[31] else 0,
                    "changeRate": float(parts[32]) if parts[32] else 0,
                    "pe": float(parts[39]) if parts[39] else None,
                    "marketCap": float(parts[44]) if parts[44] else None,  # 亿港元
                    "time": parts[30] if len(parts) > 30 else "",
                })
            except (ValueError, IndexError):
                continue

        return {"status_code": 0, "data": {"stocks": stocks, "total": len(stocks)}}

    # ==================== 美股行情 ====================

    async def get_us_quote(self, codes: list[str]) -> dict:
        """美股实时行情（支持批量）

        Args:
            codes: 美股代码列表，如 ["AAPL", "MSFT", "NVDA", "TSLA"]
                   自动补全 .OQ 后缀和 us 前缀
        """
        normed = []
        for c in codes:
            c = c.strip().split(".")[0]  # 去掉 .OQ 等后缀
            if not c.lower().startswith("us"):
                c = "us" + c.upper()
            normed.append(c)

        resp = await self._client.get(
            f"{self.SQT_URL}/q={','.join(normed)}",
            headers={"User-Agent": self.DEFAULT_HEADERS["User-Agent"]},
        )
        resp.raise_for_status()
        text = resp.content.decode("gbk", errors="replace")

        stocks = []
        for line in text.strip().split(";"):
            line = line.strip()
            if not line or "=" not in line:
                continue
            raw = line.split("=", 1)[1].strip('" \n')
            parts = raw.split("~")
            if len(parts) < 40:
                continue
            try:
                stocks.append({
                    "name": parts[1],
                    "code": parts[2],
                    "price": float(parts[3]) if parts[3] else None,
                    "prevClose": float(parts[4]) if parts[4] else None,
                    "open": float(parts[5]) if parts[5] else None,
                    "high": float(parts[33]) if parts[33] else None,
                    "low": float(parts[34]) if parts[34] else None,
                    "changeAmt": float(parts[31]) if parts[31] else 0,
                    "changeRate": float(parts[32]) if parts[32] else 0,
                    "pe": float(parts[39]) if parts[39] else None,
                    "marketCap": float(parts[44]) if parts[44] else None,  # 亿美元
                    "time": parts[30] if len(parts) > 30 else "",
                })
            except (ValueError, IndexError):
                continue

        return {"status_code": 0, "data": {"stocks": stocks, "total": len(stocks)}}

    # ==================== 国际期货 ====================

    async def get_intl_futures(self, names: list[str] | None = None) -> dict:
        """国际期货行情（纽约金/银/原油/天然气/铜/大豆/玉米/小麦）

        Args:
            names: 期货名称或代码，如 ["黄金", "原油"] 或 ["gc", "cl"]
                   None 时返回全部 8 种
        """
        if names is None:
            codes = ["hf_GC", "hf_SI", "hf_CL", "hf_NG", "hf_HG", "hf_S", "hf_C", "hf_W"]
        else:
            codes = [self.INTL_FUTURES.get(n.lower(), n) for n in names]

        resp = await self._client.get(
            f"{self.QT_URL}/q={','.join(codes)}",
            headers=self.REFERER,
        )
        resp.raise_for_status()
        text = resp.content.decode("gbk", errors="replace")

        futures = []
        for line in text.strip().split(";"):
            line = line.strip()
            if not line or "=" not in line:
                continue
            key = line.split("=")[0].replace("v_", "").strip()
            val = line.split('"')[1] if '"' in line else ""
            if not val or val == "1":
                continue
            parts = val.split(",")
            if len(parts) < 10:
                continue

            def _f(idx):
                try:
                    return float(parts[idx])
                except (IndexError, ValueError):
                    return 0.0

            futures.append({
                "code": key,
                "name": parts[-1] if parts[-1] else key,
                "price": _f(0),
                "change": _f(1),
                "open": _f(2),
                "prevSettle": _f(3),
                "high": _f(4),
                "low": _f(5),
                "time": parts[6] if len(parts) > 6 else "",
                "date": parts[12] if len(parts) > 12 else "",
            })

        return {"status_code": 0, "data": {"count": len(futures), "futures": futures}}

    # ==================== 个股所属板块 ====================

    @cached(ttl=1209600, domain="market", frequency="daily", market="a_share", source="tencent", source_name="腾讯证券")
    async def get_stock_plates(self, code: str) -> dict:
        """个股所属板块（行业+概念+地域，含实时涨跌幅）

        Args:
            code: 股票代码，如 "600519" 或 "sh600519"
        """
        tc = self._stock_tencent(code)
        resp = await self._client.get(
            f"{self.PROXY_URL}/ifzqgtimg/appstock/app/stockinfo/plateNew",
            params={"code": tc, "app": "wzq", "zdf": 1},
            headers=self.REFERER,
        )
        resp.raise_for_status()
        raw = resp.json()
        data = raw.get("data", {})

        result = {}
        for key in ("plate", "concept", "area"):
            items = data.get(key, [])
            result[key] = [
                {"name": p.get("name", ""), "id": p.get("id", ""),
                 "changeRate": float(p.get("zdf", 0)) if p.get("zdf") else 0,
                 "tag": p.get("tag", "")}
                for p in items
            ]

        return {"status_code": 0, "data": {"code": tc, **result}}

    # ==================== 热门股排行 ====================

    async def get_hot_stocks(self) -> dict:
        """热门股票排行（5分钟/1小时/1天/1周维度）"""
        resp = await self._client.get(
            f"{self.PROXY_URL}/ifzqgtimg/appstock/app/HotStock/getHotRankIndex",
            headers=self.REFERER,
        )
        resp.raise_for_status()
        text = resp.text
        if "=" in text and text.startswith("htstock"):
            text = text.split("=", 1)[1]
        raw = _json.loads(text)
        data = raw.get("data", {})

        result = {}
        for period, stocks in data.items():
            if not isinstance(stocks, list):
                continue
            items = []
            for s in stocks:
                if isinstance(s, dict):
                    items.append({
                        "code": s.get("code", ""),
                        "name": s.get("name", ""),
                        "hot": s.get("hot", 0),
                        "changeRate": s.get("zdf", 0),
                    })
            result[period] = items

        return {"status_code": 0, "data": result}

    # ==================== 港美股K线 ====================

    @cached(ttl=1209600, domain="market", frequency="daily", market="hk", source="tencent", source_name="腾讯证券")
    async def get_hk_us_kline(self, code: str, period: str = "day", count: int = 20) -> dict:
        """港股/美股K线数据（前复权）

        Args:
            code: 腾讯格式代码，如 "hk00700", "usAAPL.OQ"
            period: "day" / "week" / "month"
            count: 返回条数
        """
        resp = await self._client.get(
            f"{self.IFZQ_URL}/appstock/app/fqkline/get",
            params={"param": f"{code},{period},,,{count},qfq"},
        )
        resp.raise_for_status()
        raw = resp.json()

        bars = []
        for code_key, code_data in raw.get("data", {}).items():
            if not isinstance(code_data, dict):
                continue
            for k, v in code_data.items():
                if isinstance(v, list) and v:
                    for bar in v:
                        if isinstance(bar, list) and len(bar) >= 6:
                            bars.append({
                                "date": bar[0],
                                "open": float(bar[1]),
                                "close": float(bar[2]),
                                "high": float(bar[3]),
                                "low": float(bar[4]),
                                "volume": float(bar[5]),
                            })

        return {"status_code": 0, "data": {
            "code": code, "period": period, "count": len(bars), "bars": bars,
        }}

    # ==================== 行业排名 ====================

    async def get_industry_rank(self, code: str) -> dict:
        """个股在行业内的排名（PE/市值/EPS）

        Args:
            code: 股票代码，如 "600519" 或 "sh600519"
        """
        tc = self._stock_tencent(code)
        resp = await self._client.get(
            f"{self.PROXY_URL}/ifzqgtimg/appstock/hs/hypm/get",
            params={"code": tc},
            headers=self.REFERER,
        )
        resp.raise_for_status()
        text = resp.text
        if "=" in text and not text.startswith("{"):
            text = text.split("=", 1)[1]
        raw = _json.loads(text)
        data = raw.get("data", {})

        hy = data.get("hyinfo", {})
        info = data.get("data", {})
        pm = data.get("pm", {})
        avg = data.get("plate_avg", {})

        return {"status_code": 0, "data": {
            "code": tc,
            "industry": hy.get("hymc", ""),
            "industryCode": hy.get("dm", ""),
            "pe": float(info.get("syl", 0)) if info.get("syl") else None,
            "marketCap": float(info.get("zsz", 0)) if info.get("zsz") else None,
            "eps": float(info.get("mgsy", 0)) if info.get("mgsy") else None,
            "peRank": pm.get("syl_pm"),
            "marketCapRank": pm.get("zsz_pm"),
            "epsRank": pm.get("mgsy_pm"),
            "industryAvgPe": avg.get("avg_syl"),
            "industryAvgCap": avg.get("avg_zsz"),
            "industryStockCount": avg.get("count"),
        }}
