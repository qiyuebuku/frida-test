"""新浪财经数据客户端 (*.sina.com.cn)"""

import json
import re

from src.infrastructure.clients.base import BaseClient, cached


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
        "dji": "int_dji", "nasdaq": "int_nasdaq", "sp500": "int_sp500",
        "hsi": "b_HSI", "twse": "b_TWSE",
    }

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

    @cached(ttl=1209600, source="sina", domain="market", frequency="daily",
            market="a_share", source_name="新浪财经")
    async def get_sector_ranking(self, sector_type: str = "concept", count: int = 20) -> dict:
        """板块涨跌排行（新浪财经 API）

        sector_type: "concept"=概念板块, "industry"=行业板块
        """
        param_map = {"concept": "class", "industry": "industry"}
        param = param_map.get(sector_type, "class")

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

        # 响应格式: var S_Finance_bankuai_class = {...} (GBK 编码)
        text = resp.content.decode("gbk", errors="replace")
        # 提取 JSON 对象
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < 0:
            return {"status_code": -1, "data": {"sectors": []}, "msg": "解析失败"}

        raw = json.loads(text[start:end + 1])

        sectors = []
        for key, val in raw.items():
            parts = val.split(",")
            if len(parts) < 13:
                continue
            sectors.append({
                "key": parts[0],
                "name": parts[1],
                "stockCount": int(parts[2]) if parts[2].isdigit() else 0,
                "avgPrice": float(parts[3]) if parts[3] else 0,
                "changeAmt": float(parts[4]) if parts[4] else 0,
                "changeRate": float(parts[5]) if parts[5] else 0,
                "volume": float(parts[6]) if parts[6] else 0,
                "turnover": float(parts[7]) if parts[7] else 0,
                "leadStock": {"code": parts[8], "changeRate": float(parts[9]) if parts[9] else 0,
                              "price": float(parts[10]) if parts[10] else 0, "name": parts[12]},
            })

        # 按涨跌幅排序
        sectors.sort(key=lambda x: x["changeRate"], reverse=True)

        top_rise = sectors[:count]
        top_fall = list(reversed(sectors[-count:])) if len(sectors) > count else []

        return {"status_code": 0, "data": {
            "type": sector_type,
            "total": len(sectors),
            "topRise": top_rise,
            "topFall": top_fall,
        }}

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
                   None 时返回全部 5 个主要指数
        """
        if names is None:
            codes = ["int_dji", "int_nasdaq", "int_sp500", "b_HSI", "b_TWSE"]
        else:
            codes = [self.GLOBAL_INDEX_CODES.get(n, n) for n in names]

        text = await self._fetch_hq(",".join(codes))

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

        return {"status_code": 0, "data": {"count": len(indices), "indices": indices}}

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

        text = await self._fetch_hq(",".join(codes))

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

        return {"status_code": 0, "data": {"count": len(rates), "rates": rates}}

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

        text = await self._fetch_hq(",".join(codes))

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
                    "price": _f(6),       # 当前价
                    "open": _f(2),
                    "high": _f(3),
                    "low": _f(4),
                    "prevClose": _f(5),
                    "volume": _f(7) if len(parts) > 7 else 0,
                    "date": parts[17] if len(parts) > 17 else "",
                })

        return {"status_code": 0, "data": {"count": len(futures), "futures": futures}}

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
        fenlei = 1 if sector_type == "concept" else 0

        resp = await self._client.get(
            self.SINA_MONEYFLOW_URL,
            params={"page": 1, "num": count, "sort": "netamount", "asc": 0, "fenlei": fenlei},
            headers=self.SINA_HEADERS,
        )
        resp.raise_for_status()
        items = resp.json()

        sectors = []
        for item in items:
            def _f(k):
                try:
                    return float(item.get(k, 0))
                except (TypeError, ValueError):
                    return 0.0

            # 原始单位是元，转换为万元
            sectors.append({
                "name": item.get("name", ""),
                "netAmount": round(_f("netamount") / 10000, 2),        # 净流入（万元）
                "bigIn": round(_f("r0x_in") / 10000, 2),
                "bigOut": round(_f("r0x_out") / 10000, 2),
                "largeIn": round(_f("r1_in") / 10000, 2),
                "largeOut": round(_f("r1_out") / 10000, 2),
                "midIn": round(_f("r2_in") / 10000, 2),
                "midOut": round(_f("r2_out") / 10000, 2),
                "smallIn": round(_f("r3_in") / 10000, 2),
                "smallOut": round(_f("r3_out") / 10000, 2),
            })

        return {"status_code": 0, "data": {
            "type": sector_type, "count": len(sectors), "sectors": sectors,
        }}

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
