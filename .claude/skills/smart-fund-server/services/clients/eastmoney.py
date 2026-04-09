"""东方财富数据客户端 (*.eastmoney.com)"""

import asyncio
import json
from datetime import datetime, timedelta

from services.clients.base import BaseClient, cached


class EastmoneyClient(BaseClient):
    """东方财富数据客户端"""

    EM_DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    EM_UT = "7eea3edcaed734bea9cbfc24409ed989"
    EM_FFLOW_UT = "b2884a393a59ad64002292a3e90d46a5"
    INDEX_SECIDS = {
        "上证指数": "1.000001", "深证成指": "0.399001", "创业板指": "0.399006",
        "沪深300": "1.000300", "上证50": "1.000016", "中证500": "1.000905",
        "中证1000": "0.399852", "国证2000": "0.399303", "北证50": "0.899050",
    }

    EASTMONEY_PUSH2EX = "https://push2ex.eastmoney.com"
    PUSH2HIS = "http://push2his.eastmoney.com"

    CHANGE_TYPES = {
        8201: "火箭发射", 8202: "快速反弹", 8203: "高台跳水", 8204: "加速下跌",
        8207: "竞价上涨", 8208: "竞价下跌", 8209: "高开5日线", 8210: "低开5日线",
        8211: "向上缺口", 8212: "向下缺口", 8213: "60日新高", 8214: "60日新低",
        8215: "60日大幅上涨", 8216: "60日大幅下跌",
        8193: "大笔买入", 8194: "大笔卖出", 64: "有大买盘", 128: "有大卖盘",
        4: "封涨停板", 8: "封跌停板", 16: "打开涨停板", 32: "打开跌停板",
    }
    # 中文名 → 编码（用于用户输入）
    CHANGE_TYPE_ALIAS = {v: k for k, v in CHANGE_TYPES.items()}
    # 预设分组
    CHANGE_TYPE_GROUPS = {
        "all": list(CHANGE_TYPES.keys()),
        "竞价": [8207, 8208],
        "拉升": [8201, 8202],
        "跳水": [8203, 8204],
        "大单": [8193, 8194, 64, 128],
        "涨停": [4, 16],
        "跌停": [8, 32],
        "缺口": [8211, 8212],
        "新高新低": [8213, 8214],
        "大幅": [8215, 8216],
    }

    def __init__(self, timeout: float = 10.0):
        super().__init__(timeout)

    async def _em_datacenter(self, report_name: str, sort_col: str, sort_type: str = "-1",
                             page_size: int = 50, date_filter: str = "", extra_filter: str = "") -> dict:
        """东方财富 datacenter 通用查询"""
        filters = []
        if date_filter:
            filters.append(date_filter)
        if extra_filter:
            filters.append(extra_filter)

        params = {
            "reportName": report_name,
            "columns": "ALL",
            "source": "WEB",
            "client": "WEB",
            "sortColumns": sort_col,
            "sortTypes": sort_type,
            "pageSize": str(page_size),
            "pageNumber": "1",
        }
        if filters:
            params["filter"] = "".join(filters)

        resp = await self._client.get(self.EM_DATACENTER, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("result"):
            return {"status_code": -1, "data": None, "msg": data.get("message", "无数据")}
        return {"status_code": 0, "data": data["result"]}

    @cached(ttl=14400, source="eastmoney", source_name="东方财富", domain="market", frequency="daily", market="a_share")
    async def get_stock_valuation_history(self, stock_code: str, years: int = 3) -> list[dict]:
        """从东方财富获取单只股票的历史 PE_TTM/PB 数据
        stock_code: 纯数字股票代码，如 "300308"
        years: 回溯年数，默认 3
        返回: [{"date", "pe_ttm", "pb", "market_cap"}, ...]
        """
        cutoff = (datetime.now() - timedelta(days=years * 365)).strftime("%Y-%m-%d")
        all_data = []
        page = 1
        while True:
            resp = await self._client.get(
                "https://datacenter-web.eastmoney.com/api/data/v1/get",
                params={
                    "reportName": "RPT_VALUEANALYSIS_DET",
                    "columns": "TRADE_DATE,PE_TTM,PB_MRQ,TOTAL_MARKET_CAP",
                    "filter": f'(SECURITY_CODE="{stock_code}")',
                    "pageNumber": str(page),
                    "pageSize": "200",
                    "sortTypes": "-1",
                    "sortColumns": "TRADE_DATE",
                },
                headers={
                    "Referer": "https://data.eastmoney.com/",
                    "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
                },
            )
            resp.raise_for_status()
            body = resp.json()
            result = body.get("result", {})
            rows = result.get("data") or []
            if not rows:
                break
            for row in rows:
                date_str = (row.get("TRADE_DATE") or "")[:10]
                if date_str < cutoff:
                    return all_data
                all_data.append({
                    "date": date_str,
                    "pe_ttm": row.get("PE_TTM"),
                    "pb": row.get("PB_MRQ"),
                    "market_cap": row.get("TOTAL_MARKET_CAP"),
                })
            # 检查是否还有下一页
            total_pages = result.get("pages", 1)
            if page >= total_pages:
                break
            page += 1
        return all_data

    @cached(ttl=3600, source="eastmoney", source_name="东方财富", domain="fund_flow", frequency="daily", market="a_share")
    async def get_dragon_tiger(self, tab: str = "stock", days: int = 3, count: int = 30) -> dict:
        """龙虎榜数据

        tab: "stock"=个股明细, "dept"=活跃营业部(游资), "org"=机构买卖
        days: 回溯天数（默认3个交易日）
        """
        from datetime import date, timedelta
        start = (date.today() - timedelta(days=max(days, 1) + 4)).strftime("%Y-%m-%d")
        date_filter_ge = f"(TRADE_DATE>='{start}')"
        date_filter_onlist = f"(ONLIST_DATE>='{start}')"

        if tab == "stock":
            r = await self._em_datacenter(
                "RPT_DAILYBILLBOARD_DETAILSNEW",
                sort_col="BILLBOARD_NET_AMT", sort_type="-1",
                page_size=min(count, 50),
                date_filter=date_filter_ge,
            )
            if r["status_code"] != 0:
                return r
            items = []
            for row in (r["data"].get("data") or []):
                items.append({
                    "date": (row.get("TRADE_DATE") or "")[:10],
                    "code": row.get("SECURITY_CODE", ""),
                    "name": row.get("SECURITY_NAME_ABBR", ""),
                    "close": row.get("CLOSE_PRICE"),
                    "changeRate": row.get("CHANGE_RATE"),
                    "netAmt": row.get("BILLBOARD_NET_AMT"),       # 净买额
                    "buyAmt": row.get("BILLBOARD_BUY_AMT"),       # 买入额
                    "sellAmt": row.get("BILLBOARD_SELL_AMT"),      # 卖出额
                    "dealAmt": row.get("BILLBOARD_DEAL_AMT"),      # 龙虎榜成交额
                    "accumAmt": row.get("ACCUM_AMOUNT"),           # 总成交额
                    "turnoverRate": row.get("TURNOVERRATE"),
                    "freeCap": row.get("FREE_MARKET_CAP"),         # 流通市值
                    "reason": row.get("EXPLANATION", ""),
                    "explain": row.get("EXPLAIN", ""),             # "4家机构买入"等
                    "d1Chg": row.get("D1_CLOSE_ADJCHRATE"),       # 次日涨幅
                    "d5Chg": row.get("D5_CLOSE_ADJCHRATE"),       # 5日涨幅
                    "d10Chg": row.get("D10_CLOSE_ADJCHRATE"),     # 10日涨幅
                })
            return {"status_code": 0, "data": {
                "tab": "stock", "total": r["data"].get("count", 0), "items": items,
            }}

        elif tab == "dept":
            r = await self._em_datacenter(
                "RPT_OPERATEDEPT_ACTIVE",
                sort_col="TOTAL_NETAMT", sort_type="-1",
                page_size=min(count, 50),
                date_filter=date_filter_onlist,
            )
            if r["status_code"] != 0:
                return r
            items = []
            for row in (r["data"].get("data") or []):
                items.append({
                    "date": (row.get("ONLIST_DATE") or "")[:10],
                    "deptName": row.get("OPERATEDEPT_NAME", ""),
                    "deptAbbr": row.get("ORG_NAME_ABBR", ""),
                    "netAmt": row.get("TOTAL_NETAMT"),
                    "buyAmt": row.get("TOTAL_BUYAMT"),
                    "sellAmt": row.get("TOTAL_SELLAMT"),
                    "buyTimes": row.get("BUYER_APPEAR_NUM"),
                    "sellTimes": row.get("SELLER_APPEAR_NUM"),
                    "buyStocks": row.get("BUY_STOCK", ""),
                })
            return {"status_code": 0, "data": {
                "tab": "dept", "total": r["data"].get("count", 0), "items": items,
            }}

        elif tab == "org":
            r = await self._em_datacenter(
                "RPT_ORGANIZATION_TRADE_DETAILSNEW",
                sort_col="NET_BUY_AMT", sort_type="-1",
                page_size=min(count, 50),
                date_filter=date_filter_ge,
            )
            if r["status_code"] != 0:
                return r
            items = []
            for row in (r["data"].get("data") or []):
                items.append({
                    "date": (row.get("TRADE_DATE") or "")[:10],
                    "code": row.get("SECURITY_CODE", ""),
                    "name": row.get("SECURITY_NAME_ABBR", ""),
                    "netBuy": row.get("NET_BUY_AMT"),
                    "buyAmt": row.get("BUY_AMT"),
                    "sellAmt": row.get("SELL_AMT"),
                    "buyTimes": row.get("BUY_TIMES"),
                    "sellTimes": row.get("SELL_TIMES"),
                    "buyCount": row.get("BUY_COUNT"),        # 买入机构数
                    "sellCount": row.get("SELL_COUNT"),       # 卖出机构数
                    "accumAmt": row.get("ACCUM_AMOUNT"),
                    "turnoverRate": row.get("TURNOVERRATE"),
                    "freeCap": row.get("FREECAP"),
                    "d1Chg": row.get("D1_CLOSE_ADJCHRATE"),
                    "d5Chg": row.get("D5_CLOSE_ADJCHRATE"),
                })
            return {"status_code": 0, "data": {
                "tab": "org", "total": r["data"].get("count", 0), "items": items,
            }}

        return {"status_code": -1, "msg": f"未知tab: {tab}"}

    @cached(ttl=600, source="eastmoney", source_name="东方财富", domain="fund_flow", frequency="daily", market="a_share")
    async def get_capital_flow(self, tab: str = "market", days: int = 20) -> dict:
        """资金流向

        tab: "market"=大盘资金, "north"=北向资金
        days: 返回近 N 个交易日
        """
        if tab == "market":
            return await self._get_market_capital_flow(days)
        elif tab == "north":
            return await self._get_northbound_flow(days)
        return {"status_code": -1, "msg": f"未知tab: {tab}"}

    async def _get_market_capital_flow(self, days: int = 20) -> dict:
        """大盘资金净流入（主力/大单/超大单/中单/小单）"""
        headers = {
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
            "Referer": "http://data.eastmoney.com/zjlx/dpzjlx.html",
        }
        resp = await self._client.get(
            f"{self.PUSH2HIS}/api/qt/stock/fflow/daykline/get",
            params={
                "secid": "1.000001",
                "klt": "101",
                "lmt": str(days),
                "fields1": "f1,f2,f3,f7",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
                "ut": self.EM_FFLOW_UT,
            },
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("rc") != 0 or not data.get("data"):
            return {"status_code": -1, "msg": "无数据"}

        items = []
        for line in data["data"].get("klines", []):
            parts = line.split(",")
            if len(parts) < 13:
                continue
            items.append({
                "date": parts[0],
                "mainNet": float(parts[1]),       # 主力净流入(元)
                "smallNet": float(parts[2]),       # 小单净流入
                "midNet": float(parts[3]),         # 中单净流入
                "bigNet": float(parts[4]),         # 大单净流入
                "superNet": float(parts[5]),       # 超大单净流入
                "mainPct": float(parts[6]),        # 主力净流入占比%
                "smallPct": float(parts[7]),
                "midPct": float(parts[8]),
                "bigPct": float(parts[9]),
                "superPct": float(parts[10]),
                "close": float(parts[11]),         # 收盘价
                "changeRate": float(parts[12]),    # 涨跌幅%
            })

        # 计算统计
        if items:
            recent = items[-1]
            main_5d = sum(i["mainNet"] for i in items[-5:]) if len(items) >= 5 else None
            main_10d = sum(i["mainNet"] for i in items[-10:]) if len(items) >= 10 else None
            main_20d = sum(i["mainNet"] for i in items[-20:]) if len(items) >= 20 else None
        else:
            recent = {}
            main_5d = main_10d = main_20d = None

        return {
            "status_code": 0,
            "data": {
                "tab": "market",
                "name": data["data"].get("name", "上证指数"),
                "latest": recent,
                "sum5d": main_5d,
                "sum10d": main_10d,
                "sum20d": main_20d,
                "items": items,
            },
        }

    async def _get_northbound_flow(self, days: int = 20) -> dict:
        """北向资金历史数据（沪股通+深股通）"""
        headers = {
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
            "Referer": "http://data.eastmoney.com/hsgt/index.html",
        }
        resp = await self._client.get(
            f"{self.PUSH2HIS}/api/qt/kamt.kline/get",
            params={
                "fields1": "f1,f2,f3,f4",
                "fields2": "f51,f52,f53,f54,f55,f56",
                "klt": "101",
                "lmt": str(days),
                "ut": self.EM_FFLOW_UT,
            },
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("rc") != 0 or not data.get("data"):
            return {"status_code": -1, "msg": "无数据"}

        raw = data["data"]

        def _parse_lines(lines):
            result = []
            for line in (lines or []):
                parts = line.split(",")
                if len(parts) < 4:
                    continue
                result.append({
                    "date": parts[0],
                    "netBuy": float(parts[1]),       # 净买入(万元)，2024-08-19 后为 0
                    "quotaBalance": float(parts[2]), # 额度余额(万元)
                    "accumNet": float(parts[3]),     # 累计净买入(万元)
                })
            return result

        hk2sh = _parse_lines(raw.get("hk2sh", []))  # 沪股通
        hk2sz = _parse_lines(raw.get("hk2sz", []))  # 深股通

        # 合并沪股通+深股通
        items = []
        for sh, sz in zip(hk2sh, hk2sz):
            items.append({
                "date": sh["date"],
                "shNetBuy": sh["netBuy"],
                "szNetBuy": sz["netBuy"],
                "totalNetBuy": sh["netBuy"] + sz["netBuy"],
                "shAccum": sh["accumNet"],
                "szAccum": sz["accumNet"],
                "totalAccum": sh["accumNet"] + sz["accumNet"],
            })

        # 同时获取成交额数据（datacenter API）
        deal_data = await self._get_northbound_deal(days)

        return {
            "status_code": 0,
            "data": {
                "tab": "north",
                "items": items,
                "deals": deal_data,
            },
        }

    async def _get_northbound_deal(self, days: int = 20) -> list:
        """北向资金成交额（RPT_MUTUAL_DEAL_HISTORY）"""
        from datetime import date, timedelta
        start = (date.today() - timedelta(days=max(days, 1) + 10)).strftime("%Y-%m-%d")
        params = {
            "reportName": "RPT_MUTUAL_DEAL_HISTORY",
            "columns": "ALL",
            "source": "WEB",
            "client": "WEB",
            "sortColumns": "TRADE_DATE",
            "sortTypes": "-1",
            "pageSize": str(days * 3),
            "pageNumber": "1",
            "filter": f"(MUTUAL_TYPE=\"001\")(TRADE_DATE>='{start}')",
        }
        try:
            resp = await self._client.get(self.EM_DATACENTER, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("result") or not data["result"].get("data"):
                return []

            # 沪股通数据
            sh_deals = {}
            for row in data["result"]["data"]:
                d = (row.get("TRADE_DATE") or "")[:10]
                sh_deals[d] = {
                    "dealAmt": row.get("DEAL_AMT"),      # 万元
                    "dealNum": row.get("DEAL_NUM"),
                    "indexClose": row.get("INDEX_CLOSE_PRICE"),
                    "indexChg": row.get("INDEX_CHANGE_RATE"),
                }

            # 深股通数据
            params["filter"] = f"(MUTUAL_TYPE=\"003\")(TRADE_DATE>='{start}')"
            resp2 = await self._client.get(self.EM_DATACENTER, params=params, timeout=15)
            resp2.raise_for_status()
            data2 = resp2.json()
            sz_deals = {}
            if data2.get("result") and data2["result"].get("data"):
                for row in data2["result"]["data"]:
                    d = (row.get("TRADE_DATE") or "")[:10]
                    sz_deals[d] = {"dealAmt": row.get("DEAL_AMT")}

            # 合并
            result = []
            for d in sorted(sh_deals.keys(), reverse=True)[:days]:
                sh = sh_deals[d]
                sz = sz_deals.get(d, {})
                sh_amt = sh.get("dealAmt") or 0
                sz_amt = sz.get("dealAmt") or 0
                result.append({
                    "date": d,
                    "shDealAmt": sh_amt,
                    "szDealAmt": sz_amt,
                    "totalDealAmt": sh_amt + sz_amt,
                    "indexClose": sh.get("indexClose"),
                    "indexChg": sh.get("indexChg"),
                })
            return result
        except Exception:
            return []

    @cached(ttl=3600, source="eastmoney", source_name="东方财富", domain="market", frequency="daily", market="a_share")
    async def get_yesterday_limit_performance(self) -> dict:
        """昨日涨停今日表现（东方财富 push2ex API）"""
        from datetime import date
        today = date.today().strftime("%Y%m%d")

        em_headers = {
            "Referer": "https://quote.eastmoney.com/",
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
        }

        for attempt in range(3):
            try:
                resp = await self._client.get(
                    f"{self.EASTMONEY_PUSH2EX}/getYesterdayZTPool",
                    params={"ut": self.EM_UT, "dpt": "wz.ztzt",
                            "Pageindex": 0, "pagesize": 200, "sort": "zdp:desc",
                            "date": today},
                    headers=em_headers,
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("data") and data["data"].get("pool"):
                    break
            except Exception:
                pass
            if attempt < 2:
                await asyncio.sleep(1)
        else:
            return {"status_code": 0, "data": {"stocks": [], "stats": {}, "date": today,
                                                "msg": "非交易日或数据暂未更新"}}

        # qdate 是 API 自动对齐到的实际交易日（周末/假日自动回溯到最近交易日）
        qdate = str(data["data"].get("qdate", today))
        pool = data["data"]["pool"]
        stocks = []
        rise_count = fall_count = flat_count = 0
        total_chg = 0

        for s in pool:
            price = s.get("p", 0) / 1000
            chg = s.get("zdp", 0)
            amp = s.get("zf", 0)
            hs = s.get("hs", 0)
            ylbc = s.get("ylbc", 0)  # 昨日连板数
            yfbt = s.get("yfbt", 0)  # 昨日封板时间

            # 格式化封板时间
            fbt_str = ""
            if yfbt:
                t = str(yfbt).zfill(6)
                fbt_str = f"{t[:2]}:{t[2:4]}:{t[4:6]}"

            stocks.append({
                "code": s.get("c", ""),
                "name": s.get("n", ""),
                "price": price,
                "changeRate": chg,
                "amplitude": amp,
                "turnoverRate": hs,
                "yesterdayBoardCount": ylbc,
                "yesterdayBoardTime": fbt_str,
                "industry": s.get("hybk", ""),
                "turnover": round(s.get("amount", 0) / 1e8, 2),
            })

            if chg > 0:
                rise_count += 1
            elif chg < 0:
                fall_count += 1
            else:
                flat_count += 1
            total_chg += chg

        avg_chg = round(total_chg / max(len(stocks), 1), 2)

        # 统计连板数分布
        board_dist = {}
        for s in stocks:
            bc = s["yesterdayBoardCount"]
            board_dist[bc] = board_dist.get(bc, 0) + 1

        stats = {
            "total": len(stocks),
            "riseCount": rise_count,
            "fallCount": fall_count,
            "flatCount": flat_count,
            "avgChangeRate": avg_chg,
            "boardDistribution": board_dist,
        }

        return {"status_code": 0, "data": {"stocks": stocks, "stats": stats, "date": qdate}}

    @cached(ttl=3600, source="eastmoney", source_name="东方财富", domain="market", frequency="daily", market="a_share")
    async def get_stock_financial(self, code: str, limit: int = 10) -> dict:
        """获取个股财务数据（EPS/营收/净利/ROE等）

        code: 纯数字股票代码
        limit: 返回报告期数
        """
        headers = {
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
            "Referer": "https://data.eastmoney.com/",
        }
        resp = await self._client.get(
            "https://datacenter-web.eastmoney.com/api/data/v1/get",
            params={
                "reportName": "RPT_LICO_FN_CPD",
                "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,REPORTDATE,BASIC_EPS,"
                           "DEDUCT_BASIC_EPS,TOTAL_OPERATE_INCOME,PARENT_NETPROFIT,"
                           "WEIGHTAVG_ROE,YSTZ,SJLTZ,BPS,MGJYXJJE,XSMLL,YSHZ,SJLHZ,"
                           "ASSIGNDSCRPT,QDATE",
                "filter": f'(SECURITY_CODE="{code}")',
                "pageNumber": "1",
                "pageSize": str(min(limit, 50)),
                "sortTypes": "-1",
                "sortColumns": "REPORTDATE",
            },
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
        result = body.get("result", {})
        rows = result.get("data") or []
        if not rows:
            return {"status_code": -1, "msg": "无财务数据"}

        name = rows[0].get("SECURITY_NAME_ABBR", "")
        items = []
        for row in rows:
            report_date = (row.get("REPORTDATE") or "")[:10]
            items.append({
                "reportDate": report_date,
                "quarter": row.get("QDATE", ""),
                "basicEps": row.get("BASIC_EPS"),
                "deductEps": row.get("DEDUCT_BASIC_EPS"),
                "revenue": row.get("TOTAL_OPERATE_INCOME"),
                "netProfit": row.get("PARENT_NETPROFIT"),
                "roe": row.get("WEIGHTAVG_ROE"),
                "revenueYoy": row.get("YSTZ"),      # 营收同比 %
                "profitYoy": row.get("SJLTZ"),       # 净利同比 %
                "bps": row.get("BPS"),               # 每股净资产
                "cashPerShare": row.get("MGJYXJJE"), # 每股经营现金流
                "grossMargin": row.get("XSMLL"),     # 销售毛利率 %
                "revenueQoq": row.get("YSHZ"),       # 营收环比 %
                "profitQoq": row.get("SJLHZ"),       # 净利环比 %
                "dividend": row.get("ASSIGNDSCRPT"),  # 分红方案
            })

        return {
            "status_code": 0,
            "data": {
                "code": code,
                "name": name,
                "total": len(items),
                "items": items,
            },
        }

    @staticmethod
    def _stock_secid(code: str) -> str:
        """股票代码转东财 secid 格式: 6开头/9开头→1.{code}(沪), 其他→0.{code}(深)"""
        code = code.strip()
        if code.startswith(("6", "9")):
            return f"1.{code}"
        return f"0.{code}"

    @cached(ttl=3600, source="eastmoney", source_name="东方财富", domain="market", frequency="daily", market="fund")
    async def search_fund(self, keyword: str, limit: int = 20) -> dict:
        """搜索基金（使用东方财富 API）

        Args:
            keyword: 搜索关键词（如 "标普500"、"纳斯达克"）
            limit: 返回数量限制

        Returns:
            {
                "status_code": 0,
                "data": [
                    {"code": "007722", "name": "天弘标普500发起(QDII-FOF)C", "type": "指数型-海外股票", ...}
                ]
            }
        """
        import httpx
        url = f"https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx?m=1&key={keyword}"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers={
                    "User-Agent": "Mozilla/5.0 (Linux; Android 11) AppleWebKit/537.36"
                })
                data = resp.json()

                if data.get("ErrCode") != 0:
                    return {"status_code": -1, "status_msg": data.get("ErrMsg", "搜索失败"), "data": []}

                results = []
                for item in data.get("Datas", [])[:limit]:
                    fund_info = item.get("FundBaseInfo", {}) or {}
                    results.append({
                        "code": item.get("CODE", ""),
                        "name": item.get("NAME", ""),
                        "type": fund_info.get("FTYPE", ""),
                        "company": fund_info.get("JJGS", ""),
                        "manager": fund_info.get("JJJL", ""),
                        "nav": fund_info.get("DWJZ"),
                        "nav_date": fund_info.get("FSRQ", ""),
                        "min_buy": fund_info.get("MINSG"),
                        "can_buy": fund_info.get("ISBUY", "") != "0",
                    })

                return {"status_code": 0, "status_msg": "Success", "data": results}

        except Exception as e:
            return {"status_code": -1, "status_msg": str(e), "data": []}

    @cached(ttl=14400, source="eastmoney", source_name="东方财富", domain="market", frequency="daily", market="a_share")
    async def get_stock_kline(self, code: str, period: str = "101", limit: int = 60) -> dict:
        """获取个股K线数据

        code: 股票代码
        period: 101=日K, 102=周K, 103=月K
        limit: 返回条数
        """
        secid = self._stock_secid(code)
        headers = {
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
            "Referer": "https://quote.eastmoney.com/",
        }
        for attempt in range(3):
            try:
                resp = await self._client.get(
                    "https://push2his.eastmoney.com/api/qt/stock/kline/get",
                    params={
                        "secid": secid,
                        "fields1": "f1,f2,f3",
                        "fields2": "f51,f52,f53,f54,f55,f56,f57",
                        "klt": period,
                        "fqt": "1",  # 前复权
                        "end": "20500101",
                        "lmt": str(min(limit, 500)),
                    },
                    headers=headers,
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception:
                if attempt < 2:
                    await asyncio.sleep(1)
                else:
                    return {"status_code": -1, "msg": "获取K线失败"}

        if data.get("rc") != 0 or not data.get("data"):
            return {"status_code": -1, "msg": "无数据"}

        raw = data["data"]
        name = raw.get("name", "")
        klines = []
        for line in raw.get("klines", []):
            parts = line.split(",")
            if len(parts) < 7:
                continue
            klines.append({
                "date": parts[0],
                "open": float(parts[1]),
                "close": float(parts[2]),
                "high": float(parts[3]),
                "low": float(parts[4]),
                "volume": int(parts[5]),  # 手
                "turnover": float(parts[6]),  # 元
            })

        period_name = {"101": "日K", "102": "周K", "103": "月K"}.get(period, "日K")
        return {
            "status_code": 0,
            "data": {
                "code": code,
                "name": name,
                "period": period_name,
                "total": len(klines),
                "klines": klines,
            },
        }

    @cached(ttl=600, source="eastmoney", source_name="东方财富", domain="fund_flow", frequency="daily", market="a_share")
    async def get_stock_capital_flow(self, code: str, days: int = 20) -> dict:
        """获取个股资金流向（主力/超大单/大单/中单/小单）

        code: 股票代码
        days: 回溯天数
        """
        secid = self._stock_secid(code)
        headers = {
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
            "Referer": "http://data.eastmoney.com/zjlx/detail.html",
        }
        resp = await self._client.get(
            f"{self.PUSH2HIS}/api/qt/stock/fflow/daykline/get",
            params={
                "secid": secid,
                "klt": "101",
                "lmt": "0",
                "fields1": "f1,f2,f3,f7",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
                "ut": self.EM_FFLOW_UT,
            },
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("rc") != 0 or not data.get("data"):
            return {"status_code": -1, "msg": "无数据"}

        name = data["data"].get("name", "")
        items = []
        for line in data["data"].get("klines", []):
            parts = line.split(",")
            if len(parts) < 13:
                continue
            items.append({
                "date": parts[0],
                "mainNet": float(parts[1]),
                "smallNet": float(parts[2]),
                "midNet": float(parts[3]),
                "bigNet": float(parts[4]),
                "superNet": float(parts[5]),
                "mainPct": float(parts[6]),
                "smallPct": float(parts[7]),
                "midPct": float(parts[8]),
                "bigPct": float(parts[9]),
                "superPct": float(parts[10]),
                "close": float(parts[11]),
                "changeRate": float(parts[12]),
            })

        items = items[-days:] if days else items
        latest = items[-1] if items else {}
        main_5d = sum(i["mainNet"] for i in items[-5:]) if len(items) >= 5 else None
        main_10d = sum(i["mainNet"] for i in items[-10:]) if len(items) >= 10 else None

        return {
            "status_code": 0,
            "data": {
                "code": code,
                "name": name,
                "latest": latest,
                "sum5d": main_5d,
                "sum10d": main_10d,
                "total": len(items),
                "items": items,
            },
        }

    @cached(ttl=60, source="eastmoney", source_name="东方财富", domain="market", frequency="realtime", market="a_share")
    async def get_stock_changes(self, change_type: str = "all", count: int = 50) -> dict:
        """盘中异动（东方财富 push2ex API）

        change_type: "all"=全部, "竞价"/"拉升"/"跳水"/"大单"/"涨停"/"跌停" 等分组，或具体类型如 "火箭发射"
        """
        # 解析类型
        if change_type in self.CHANGE_TYPE_GROUPS:
            type_codes = self.CHANGE_TYPE_GROUPS[change_type]
        elif change_type in self.CHANGE_TYPE_ALIAS:
            type_codes = [self.CHANGE_TYPE_ALIAS[change_type]]
        elif change_type.isdigit():
            type_codes = [int(change_type)]
        else:
            type_codes = self.CHANGE_TYPE_GROUPS["all"]

        type_str = ",".join(str(t) for t in type_codes)
        em_headers = {
            "Referer": "https://quote.eastmoney.com/",
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
        }

        for attempt in range(3):
            try:
                resp = await self._client.get(
                    f"{self.EASTMONEY_PUSH2EX}/getAllStockChanges",
                    params={"ut": self.EM_UT, "dpt": "wzchanges",
                            "pageindex": 0, "pagesize": count, "type": type_str},
                    headers=em_headers,
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("data") and data["data"].get("allstock"):
                    break
            except Exception:
                pass
            if attempt < 2:
                await asyncio.sleep(1)
        else:
            return {"status_code": 0, "data": {"changes": [], "total": 0,
                                                "msg": "非交易时段或数据暂未更新"}}

        raw_list = data["data"]["allstock"]
        changes = []
        for item in raw_list:
            tm = item.get("tm", 0)
            tm_str = ""
            if tm:
                t = str(tm).zfill(6)
                tm_str = f"{t[:2]}:{t[2:4]}:{t[4:6]}"

            type_code = item.get("t", 0)
            type_name = self.CHANGE_TYPES.get(type_code, f"未知({type_code})")

            # 解析 info 字段（不同类型格式不同）
            info = item.get("i", "")
            parts = info.split(",") if info else []
            price = change_rate = amount = None
            if type_code in (4, 8, 16, 32):
                # 封涨停/跌停/打开涨停/跌停: "价格,封单量,价格,涨跌幅"
                price = float(parts[0]) if len(parts) > 0 and parts[0] else None
                change_rate = float(parts[3]) if len(parts) > 3 and parts[3] else None
                amount = float(parts[1]) if len(parts) > 1 and parts[1] else None  # 封单量
            elif type_code in (8201, 8202, 8203, 8204):
                # 火箭发射/快速反弹/高台跳水/加速下跌: "涨跌幅,价格,涨跌幅"
                price = float(parts[1]) if len(parts) > 1 and parts[1] else None
                change_rate = float(parts[0]) if len(parts) > 0 and parts[0] else None
            else:
                # 大笔买入/卖出/竞价等: "手数,价格,涨跌幅,成交金额"
                price = float(parts[1]) if len(parts) > 1 and parts[1] else None
                change_rate = float(parts[2]) if len(parts) > 2 and parts[2] else None
                amount = float(parts[3]) if len(parts) > 3 and parts[3] else None

            changes.append({
                "time": tm_str,
                "code": item.get("c", ""),
                "market": item.get("m", 0),
                "name": item.get("n", ""),
                "typeCode": type_code,
                "typeName": type_name,
                "price": price,
                "changeRate": round(change_rate * 100, 2) if change_rate is not None else None,
                "amount": round(amount / 1e4, 2) if amount else None,  # 元→万元
            })

        return {"status_code": 0, "data": {
            "changes": changes,
            "total": data["data"].get("tc", len(changes)),
            "typeFilter": change_type,
            "availableTypes": list(self.CHANGE_TYPE_GROUPS.keys()) + list(self.CHANGE_TYPE_ALIAS.keys()),
        }}

    @cached(ttl=14400, source="eastmoney", source_name="东方财富", domain="market", frequency="daily", market="a_share")
    async def get_index_kline(self, secid: str, beg_date: str, lmt: int = 250) -> dict:
        """获取指数/ETF K线数据（含重试）

        Args:
            secid: 东财 secid，如 "1.000300"
            beg_date: 起始日期，格式 "YYYYMMDD"
            lmt: 返回条数上限
        Returns:
            东财原始 JSON，含 data.klines 等
        """
        headers = {
            "Referer": "https://quote.eastmoney.com/",
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
        }
        for attempt in range(3):
            try:
                resp = await self._client.get(
                    "https://push2his.eastmoney.com/api/qt/stock/kline/get",
                    params={
                        "secid": secid, "fields1": "f1,f2,f3",
                        "fields2": "f51,f52,f53,f54,f55,f56,f57",
                        "klt": "101", "fqt": "1", "beg": beg_date,
                        "end": "20500101", "lmt": str(lmt),
                    },
                    headers=headers,
                )
                resp.raise_for_status()
                return resp.json()
            except Exception:
                if attempt < 2:
                    await asyncio.sleep(1)
        return {}

    @cached(ttl=600, source="eastmoney", source_name="东方财富", domain="fund_flow", frequency="daily", market="a_share")
    async def get_northbound_recent(self, page_size: int = 30) -> dict:
        """北向资金近N个交易日成交额（RPT_MUTUAL_DEAL_HISTORY，MUTUAL_TYPE=005 合计）

        Returns:
            东财 datacenter 原始 JSON
        """
        headers = {
            "Referer": "https://data.eastmoney.com/",
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
        }
        resp = await self._client.get(
            "https://datacenter-web.eastmoney.com/api/data/v1/get",
            params={
                "reportName": "RPT_MUTUAL_DEAL_HISTORY",
                "columns": "TRADE_DATE,MUTUAL_TYPE,DEAL_AMT",
                "filter": '(MUTUAL_TYPE="005")',
                "pageNumber": "1", "pageSize": str(page_size),
                "sortTypes": "-1", "sortColumns": "TRADE_DATE",
            },
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()

    @cached(ttl=3600, source="eastmoney", source_name="东方财富", domain="market", frequency="daily", market="a_share")
    async def get_margin_recent(self, page_size: int = 30) -> dict:
        """全市场融资融券余额（近N个交易日）

        Returns:
            东财 datacenter 原始 JSON
        """
        headers = {
            "Referer": "https://data.eastmoney.com/",
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
        }
        resp = await self._client.get(
            "https://datacenter-web.eastmoney.com/api/data/v1/get",
            params={
                "reportName": "RPTA_RZRQ_LSHJ",
                "columns": "DIM_DATE,RZYE,RQYE,RZRQYE,RZJME",
                "pageNumber": "1", "pageSize": str(page_size),
                "sortTypes": "-1", "sortColumns": "dim_date",
            },
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()

    @cached(ttl=60, source="eastmoney", source_name="东方财富", domain="market", frequency="realtime", market="a_share")
    async def get_indices_realtime(self, secids: str) -> dict:
        """获取主要指数实时行情（含涨跌家数），带重试

        Args:
            secids: 逗号分隔的 secid 列表，如 "1.000001,0.399001"
        Returns:
            东财 push2 原始 JSON
        """
        headers = {
            "Referer": "https://quote.eastmoney.com/",
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
        }
        for attempt in range(3):
            try:
                resp = await self._client.get(
                    "https://push2.eastmoney.com/api/qt/ulist.np/get",
                    params={"fltt": 2, "secids": secids,
                            "fields": "f2,f3,f4,f5,f6,f7,f8,f12,f14,f104,f105,f106"},
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("data", {}).get("diff"):
                    return data
            except Exception:
                pass
            if attempt < 2:
                await asyncio.sleep(1)
        return {}

    @cached(ttl=300, source="eastmoney", source_name="东方财富", domain="fund_flow", frequency="realtime", market="a_share")
    async def get_index_capital_flow_daily(self, secid: str) -> dict:
        """获取指数当日资金流向，带重试

        Args:
            secid: 东财 secid，如 "1.000001"
        Returns:
            东财 push2 原始 JSON
        """
        headers = {
            "Referer": "https://quote.eastmoney.com/",
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
        }
        for attempt in range(3):
            try:
                resp = await self._client.get(
                    "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get",
                    params={"secid": secid, "fields1": "f1,f2,f3",
                            "fields2": "f51,f52,f53,f54,f55,f56", "lmt": 1, "klt": 101},
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("data", {}).get("klines"):
                    return data
            except Exception:
                pass
            if attempt < 2:
                await asyncio.sleep(1)
        return {}

    # ==================== 宏观经济指标 ====================

    @cached(ttl=3600, source="eastmoney", source_name="东方财富", domain="macro", frequency="monthly", market="macro")
    async def get_macro_indicator(self, report_name: str, page_size: int = 12) -> list:
        """宏观经济指标通用查询

        Args:
            report_name: datacenter reportName
            page_size: 返回条数（默认 12 条 = 近 1 年月度数据）

        Returns:
            [{"REPORT_DATE": "2026-02-01", ...}, ...]
        """
        result = await self._em_datacenter(report_name, sort_col="REPORT_DATE", page_size=page_size)
        if result.get("status_code") == 0 and result.get("data"):
            return result["data"].get("data", [])
        return []

    @cached(ttl=3600, source="eastmoney", source_name="东方财富", domain="macro", frequency="monthly", market="macro")
    async def get_macro_cpi(self, page_size: int = 12) -> list:
        """CPI 居民消费价格指数"""
        return await self.get_macro_indicator("RPT_ECONOMY_CPI", page_size)

    @cached(ttl=3600, source="eastmoney", source_name="东方财富", domain="macro", frequency="monthly", market="macro")
    async def get_macro_ppi(self, page_size: int = 12) -> list:
        """PPI 工业生产者出厂价格指数"""
        return await self.get_macro_indicator("RPT_ECONOMY_PPI", page_size)

    @cached(ttl=3600, source="eastmoney", source_name="东方财富", domain="macro", frequency="monthly", market="macro")
    async def get_macro_pmi(self, page_size: int = 12) -> list:
        """PMI 制造业采购经理指数"""
        return await self.get_macro_indicator("RPT_ECONOMY_PMI", page_size)

    @cached(ttl=3600, source="eastmoney", source_name="东方财富", domain="macro", frequency="monthly", market="macro")
    async def get_macro_gdp(self, page_size: int = 12) -> list:
        """GDP"""
        return await self.get_macro_indicator("RPT_ECONOMY_GDP", page_size)

    @cached(ttl=3600, source="eastmoney", source_name="东方财富", domain="macro", frequency="monthly", market="macro")
    async def get_macro_money_supply(self, page_size: int = 12) -> list:
        """M2 货币供应量"""
        return await self.get_macro_indicator("RPT_ECONOMY_CURRENCY_SUPPLY", page_size)

    @cached(ttl=3600, source="eastmoney", source_name="东方财富", domain="macro", frequency="monthly", market="macro")
    async def get_macro_rmb_loan(self, page_size: int = 12) -> list:
        """人民币贷款（社融核心组成）"""
        return await self.get_macro_indicator("RPT_ECONOMY_RMB_LOAN", page_size)

    @cached(ttl=3600, source="eastmoney", source_name="东方财富", domain="macro", frequency="monthly", market="macro")
    async def get_macro_forex_reserve(self, page_size: int = 12) -> list:
        """外汇储备"""
        return await self.get_macro_indicator("RPT_ECONOMY_GOLD_CURRENCY", page_size)

    @cached(ttl=3600, source="eastmoney", source_name="东方财富", domain="macro", frequency="monthly", market="macro")
    async def get_macro_fixed_asset_invest(self, page_size: int = 12) -> list:
        """固定资产投资"""
        return await self.get_macro_indicator("RPT_ECONOMY_ASSET_INVEST", page_size)

    @cached(ttl=3600, source="eastmoney", source_name="东方财富", domain="macro", frequency="monthly", market="macro")
    async def get_macro_fdi(self, page_size: int = 12) -> list:
        """外商直接投资 (FDI)"""
        return await self.get_macro_indicator("RPT_ECONOMY_FDI", page_size)

    # ==================== 券商研报 ====================

    @cached(ttl=3600, source="eastmoney", source_name="东方财富", domain="news", frequency="daily", market="a_share")
    async def get_research_reports(self, page_size: int = 20, industry: str = "*", rating: str = "*") -> list:
        """获取券商研报列表

        Args:
            page_size: 返回条数
            industry: 行业筛选（* 为全部）
            rating: 评级筛选（* 为全部）

        Returns:
            [{title, orgSName, publishDate, emRatingName, industryName, ...}, ...]
        """
        resp = await self._client.get(
            "https://reportapi.eastmoney.com/report/list",
            params={
                "industryCode": "*",
                "pageSize": str(page_size),
                "industry": industry,
                "rating": rating,
                "ratingChange": "*",
                "beginTime": "",
                "endTime": "",
                "pageNo": "1",
                "fields": "",
                "qType": "0",
                "orgCode": "",
                "rcode": "",
            },
            headers={
                "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
                "Referer": "https://data.eastmoney.com/",
            },
        )
        resp.raise_for_status()
        return resp.json().get("data", [])

    # ==================== 新闻资讯 ====================

    @cached(ttl=300, source="eastmoney", source_name="东方财富", domain="news", frequency="realtime", market="a_share")
    async def get_news_by_keyword(self, keyword: str, page_size: int = 20) -> list:
        """按关键词搜索东方财富新闻

        Args:
            keyword: 搜索关键词（如"AI"、"新能源"）
            page_size: 返回条数

        Returns:
            [{title, date, content(摘要), mediaName(来源), url}, ...]
        """
        import json as _json
        param = _json.dumps({
            "uid": "",
            "keyword": keyword,
            "type": ["cmsArticleWebOld"],
            "pageIndex": 1,
            "pageSize": page_size,
        }, ensure_ascii=False)
        resp = await self._client.get(
            "https://search-api-web.eastmoney.com/search/jsonp",
            params={"cb": "callback", "param": param},
            headers={
                "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
                "Referer": "https://so.eastmoney.com/",
            },
        )
        resp.raise_for_status()
        text = resp.text
        json_start = text.index("(") + 1
        json_end = text.rindex(")")
        data = _json.loads(text[json_start:json_end])
        items = data.get("result", {}).get("cmsArticleWebOld", [])
        # 清理搜索高亮标签
        import re
        for item in items:
            for key in ("title", "content"):
                if key in item and item[key]:
                    item[key] = re.sub(r"</?em>", "", item[key])
        return items

    # ==================== 股吧人气榜 ====================

    @cached(ttl=1800, source="eastmoney", source_name="东方财富", domain="sentiment", frequency="daily", market="a_share")
    async def get_guba_popularity(self, page_size: int = 50) -> list:
        """获取股吧人气排行榜

        Args:
            page_size: 返回条数

        Returns:
            [{sc(股票代码), rk(排名), rc, hisRc}, ...]
        """
        resp = await self._client.post(
            "https://emappdata.eastmoney.com/stockrank/getAllCurrentList",
            json={
                "appId": "appId01",
                "globalId": "786e4c21-70dc-435a-93bb-38",
                "pageNo": 1,
                "pageSize": page_size,
            },
            headers={"User-Agent": self.DEFAULT_HEADERS["User-Agent"]},
        )
        resp.raise_for_status()
        return resp.json().get("data", [])

    # ==================== 增量方法 ====================

    @cached(ttl=3600, source="eastmoney", source_name="东方财富", domain="macro", frequency="monthly", market="macro")
    async def get_macro_since(self, report_name: str, since_date: str, page_size: int = 24) -> list:
        """宏观指标增量采集：只返回 since_date 之后的数据

        Args:
            report_name: datacenter reportName
            since_date: 上次采集日期（"2026-01-01"）
        """
        items = await self.get_macro_indicator(report_name, page_size)
        return [item for item in items if (item.get("REPORT_DATE") or "")[:10] > since_date]

    @cached(ttl=3600, source="eastmoney", source_name="东方财富", domain="news", frequency="daily", market="a_share")
    async def get_research_reports_since(self, since_date: str, page_size: int = 50) -> list:
        """研报增量采集：只返回 since_date 之后的"""
        items = await self.get_research_reports(page_size)
        return [item for item in items if (item.get("publishDate") or "")[:10] > since_date]

    @cached(ttl=300, source="eastmoney", source_name="东方财富", domain="news", frequency="realtime", market="a_share")
    async def get_news_by_keyword_since(self, keyword: str, since_date: str, page_size: int = 50) -> list:
        """新闻增量采集：只返回 since_date 之后的"""
        items = await self.get_news_by_keyword(keyword, page_size)
        return [item for item in items if (item.get("date") or "")[:10] > since_date]

    # ==================== 股吧帖子（SSR HTML 解析） ====================

    @cached(ttl=1800, source="eastmoney", source_name="东方财富", domain="sentiment", frequency="daily", market="a_share")
    async def get_guba_posts(self, code: str, page: int = 1) -> dict:
        """个股股吧帖子列表（HTML 解析，非 API）

        Args:
            code: 股票代码，如 "600519"（纯数字，不带 sh/sz）
            page: 页码

        Returns:
            {"status_code": 0, "data": {"code": "600519", "count": N, "posts": [...]}}
        """
        import re
        resp = await self._client.get(
            f"https://guba.eastmoney.com/list,{code},f_{page}.html",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://guba.eastmoney.com/",
            },
        )
        resp.raise_for_status()
        html = resp.text

        posts = []
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
        for row in rows:
            link_match = re.search(
                r'href="(/news,\w+,(\d+)\.html)"[^>]*>(.*?)</a>', row, re.DOTALL)
            if not link_match:
                continue
            rel_url = link_match.group(1)
            post_id = link_match.group(2)
            title = re.sub(r'<[^>]+>', '', link_match.group(3)).strip()
            if not title or len(title) < 3:
                continue

            # 阅读数 / 回复数（前两个 <td>）
            tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            reads = re.sub(r'<[^>]+>', '', tds[0]).strip() if len(tds) > 0 else ""
            replies = re.sub(r'<[^>]+>', '', tds[1]).strip() if len(tds) > 1 else ""

            # 时间
            time_match = re.search(r'(\d{2}-\d{2}\s+\d{2}:\d{2})', row)
            post_time = time_match.group(1) if time_match else ""

            posts.append({
                "id": post_id,
                "title": title,
                "url": f"https://guba.eastmoney.com{rel_url}",
                "reads": reads,
                "replies": replies,
                "time": post_time,
            })

        return {"status_code": 0, "data": {"code": code, "count": len(posts), "posts": posts}}

    @cached(ttl=1800, source="eastmoney", source_name="东方财富", domain="sentiment", frequency="daily", market="a_share")
    async def get_guba_posts_since(self, code: str, since_id: str) -> list:
        """增量采集股吧帖子：只返回 id > since_id 的帖子"""
        r = await self.get_guba_posts(code)
        if r["status_code"] != 0:
            return []
        return [p for p in r["data"]["posts"] if p.get("id", "") > since_id]


if __name__ == "__main__":
    import os
    import asyncio

    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        os.environ.pop(key, None)

    async def main():
        client = EastmoneyClient()
        try:
            print("=== 宏观指标 ===")
            for name, method in [
                ("CPI", client.get_macro_cpi),
                ("PPI", client.get_macro_ppi),
                ("PMI", client.get_macro_pmi),
                ("GDP", client.get_macro_gdp),
                ("M2", client.get_macro_money_supply),
                ("人民币贷款", client.get_macro_rmb_loan),
                ("外汇储备", client.get_macro_forex_reserve),
                ("固定资产投资", client.get_macro_fixed_asset_invest),
                ("FDI", client.get_macro_fdi),
            ]:
                items = await method(page_size=2)
                date = items[0].get("REPORT_DATE", "")[:10] if items else "N/A"
                print(f"  {name}: {len(items)}条 latest={date}")

            print("\n=== 券商研报 ===")
            reports = await client.get_research_reports(page_size=3)
            for r in reports[:3]:
                print(f"  [{r.get('publishDate','')[:10]}] [{r.get('orgSName','')}] {r.get('title','')[:40]}")

            print("\n=== 新闻(AI) ===")
            news = await client.get_news_by_keyword("AI", page_size=3)
            for n in news[:3]:
                print(f"  [{n.get('date','')[:10]}] {n.get('title','')[:40]} ({n.get('mediaName','')})")

            print("\n=== 股吧人气 ===")
            pop = await client.get_guba_popularity(page_size=5)
            for p in pop:
                print(f"  #{p.get('rk','')} {p.get('sc','')}")

            print("\n=== 增量采集测试 ===")
            new_cpi = await client.get_macro_since("RPT_ECONOMY_CPI", "2026-01-01")
            print(f"  CPI(since 2026-01-01): {len(new_cpi)}条")
        finally:
            await client.close()

    asyncio.run(main())
