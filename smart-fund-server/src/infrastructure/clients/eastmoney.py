"""东方财富数据客户端 (*.eastmoney.com)"""

import asyncio
import json
import random
from datetime import datetime, timedelta, timezone

import akshare as ak

from src.infrastructure.clients.base import BaseClient, cached
from src.infrastructure.clients.market_contracts import (
    MarketDataStatus,
    market_error,
    market_result,
)


class EastmoneyClient(BaseClient):
    """东方财富数据客户端"""

    EM_DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    EM_UT = "7eea3edcaed734bea9cbfc24409ed989"
    EM_FFLOW_UT = "b2884a393a59ad64002292a3e90d46a5"
    INDEX_SECIDS = {
        "上证指数": "1.000001", "深证成指": "0.399001", "创业板指": "0.399006",
        "科创综指": "1.000680", "科创50": "1.000688", "科创100": "1.000698",
        "中证A500": "1.000510",
        "沪深300": "1.000300", "上证50": "1.000016", "中证500": "1.000905",
        "中证1000": "1.000852", "深证100": "0.399330",
        "国证2000": "0.399303", "北证50": "0.899050",
    }

    EASTMONEY_PUSH2EX = "https://push2ex.eastmoney.com"
    PUSH2HIS = "https://push2his.eastmoney.com"
    PUSH2_HOSTS = (
        "https://push2.eastmoney.com",
        "https://2.push2.eastmoney.com",
        "https://82.push2.eastmoney.com",
    )
    PUSH2_DELAY = "https://push2delay.eastmoney.com"
    MARKET_BREADTH_SECIDS = (
        "1.000001",  # 上证指数：指数行情和上交所成交额
        "0.399001",  # 深证成指：指数行情和深交所成交额
        "0.399006",  # 创业板指：指数行情
        "1.000680",  # 科创综指
        "1.000688",  # 科创 50
        "1.000510",  # 中证 A500
        "1.000300",  # 沪深 300
        "1.000852",  # 中证 1000
        "1.000016",  # 上证 50
        "1.000905",  # 中证 500
        "0.399330",  # 深证 100
        "1.000698",  # 科创 100
        "1.000002",  # 上证 A 股指数：A 股涨跌家数
        "0.399107",  # 深证 A 指：A 股涨跌家数
        "0.899050",  # 北证 50：北交所涨跌家数、成交额和指数行情
    )
    MARKET_TURNOVER_SECIDS = (
        "1.000001",  # 上交所成交额
        "0.399001",  # 深交所成交额
        "0.899050",  # 北交所成交额
    )

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
        self._request_timeout = timeout

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

    @cached(ttl=1209600, source="eastmoney", source_name="东方财富", domain="market", frequency="daily", market="a_share")
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

    async def get_yesterday_limit_performance(self) -> dict:
        """昨日涨停今日表现（东方财富 push2ex API）"""
        from datetime import date
        today = date.today().strftime("%Y%m%d")

        em_headers = {
            "Referer": "https://quote.eastmoney.com/",
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
        }

        last_error: Exception | None = None
        received_valid_payload = False
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
        """股票/ETF 代码转东财 secid 格式

        - 6/9 开头 → 1.{code}(沪股)
        - 5 开头 → 1.{code}(沪 ETF/LOF, 51xxxx/56xxxx/58xxxx)
        - 11/15 开头 → 0.{code}(深 ETF, 11xxxx/15xxxx)
        - 其他 → 0.{code}(深股)
        """
        code = code.strip()
        if code.startswith(("6", "9", "5")):
            return f"1.{code}"
        return f"0.{code}"

    @cached(ttl=1209600, source="eastmoney", source_name="东方财富", domain="market", frequency="daily", market="fund")
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

    @cached(ttl=1209600, source="eastmoney", source_name="东方财富", domain="market", frequency="daily", market="a_share")
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

    # ==================== 板块 K 线 ====================

    async def get_sector_list(self, sector_type: str = "industry") -> dict:
        """获取 EM 板块列表（含 BK 代码和名称）

        Args:
            sector_type: "industry"=行业板块 / "concept"=概念板块

        Returns:
            {sectors: [{bk_code, name, change_pct}, ...]}
        """
        if sector_type not in {"industry", "concept"}:
            raise ValueError("sector_type must be industry or concept")
        fs_map = {"industry": "m:90+t:2", "concept": "m:90+t:3"}
        fs = fs_map[sector_type]
        headers = {
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
            "Referer": "https://quote.eastmoney.com/center/boardlist.html",
        }
        params = {
            "pn": 1,
            "pz": 500,
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f3",
            "fs": fs,
            "fields": "f2,f3,f4,f5,f6,f7,f8,f12,f14,f20,f62,f104,f105,f109,f128,f140",
        }
        raw = None
        last_error = None
        hosts = [*self.PUSH2_HOSTS, self.PUSH2_DELAY]
        random.shuffle(hosts)
        for host in hosts:
            try:
                resp = await self._client.get(
                    f"{host}/api/qt/clist/get",
                    params=params,
                    headers=headers,
                    timeout=15,
                )
                resp.raise_for_status()
                raw = resp.json()
                if (raw.get("data") or {}).get("diff") is not None:
                    break
            except Exception as exc:
                last_error = exc
        if raw is None:
            return market_error(
                provider="eastmoney",
                market="cn",
                error=last_error or "all Push2 hosts failed",
                provider_metadata={"attempted_hosts": hosts},
            )
        if not isinstance(raw.get("data"), dict) or not isinstance(raw["data"].get("diff"), list):
            return market_error(
                provider="eastmoney",
                market="cn",
                error="Push2 response schema changed",
                status=MarketDataStatus.PARSE_ERROR,
            )

        sectors = []
        for row in raw["data"]["diff"]:
            bk_code = row.get("f12", "")
            name = row.get("f14", "")
            if bk_code and name:
                sectors.append({
                    "bk_code": bk_code,
                    "name": name,
                    "provider_sector_code": bk_code,
                    "sector_name": name,
                    "sector_type": sector_type,
                    "classification": "eastmoney",
                    "latest": row.get("f2"),
                    "change_pct": row.get("f3"),
                    "change_amount": row.get("f4"),
                    "volume": row.get("f5"),
                    "turnover": row.get("f6") or row.get("f20"),
                    "amplitude_pct": row.get("f7"),
                    "turnover_rate": row.get("f8"),
                    "main_net_inflow": row.get("f62"),
                    "up_count": row.get("f104"),
                    "down_count": row.get("f105"),
                    "change_5d_pct": row.get("f109"),
                    "lead_stock_name": row.get("f128"),
                    "lead_stock_code": row.get("f140"),
                })
        result = market_result(
            provider="eastmoney",
            market="cn",
            data={
                "sector_type": sector_type,
                "count": len(sectors),
                "total": raw["data"].get("total"),
                "sectors": sectors,
            },
            timezone_name="Asia/Shanghai",
            provider_metadata={
                "complete": len(sectors) == raw["data"].get("total"),
                "turnover_unit": "yuan",
                "main_net_inflow_unit": "yuan",
            },
        )
        result["sectors"] = sectors
        return result

    async def get_stock_ranking(
        self,
        sort: str = "rise",
        count: int = 20,
    ) -> dict:
        """Return A-share rankings with industry, speed and fund-flow fields."""

        sort_config = {
            "rise": ("f3", 1),
            "fall": ("f3", 0),
            "quick": ("f22", 1),
            "turnover": ("f6", 1),
            "large_order": ("f62", 1),
        }
        if sort not in sort_config:
            raise ValueError(
                "sort must be rise, fall, quick, turnover or large_order"
            )
        field, order = sort_config[sort]
        params = {
            "pn": 1,
            "pz": max(1, min(int(count), 100)),
            "po": order,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": field,
            "fs": (
                "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,"
                "m:0+t:81+s:2048"
            ),
            "fields": "f2,f3,f6,f12,f14,f22,f62,f100",
        }
        headers = {
            "Referer": "https://quote.eastmoney.com/center/",
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
        }
        raw = None
        last_error: Exception | None = None
        for host in (*self.PUSH2_HOSTS, self.PUSH2_DELAY):
            try:
                response = await self._client.get(
                    f"{host}/api/qt/clist/get",
                    params=params,
                    headers=headers,
                    timeout=15,
                )
                response.raise_for_status()
                raw = response.json()
                if isinstance((raw.get("data") or {}).get("diff"), list):
                    break
            except Exception as exc:
                last_error = exc
        if not isinstance((raw or {}).get("data"), dict):
            return market_error(
                provider="eastmoney",
                market="cn",
                error=last_error or "stock ranking source failed",
            )
        rows = (raw["data"].get("diff") or [])
        stocks = [
            {
                "code": row.get("f12"),
                "name": row.get("f14"),
                "close": row.get("f2"),
                "changeRate": row.get("f3"),
                "turnover": row.get("f6"),
                "speed": row.get("f22"),
                "largeOrderNet": row.get("f62"),
                "industry": row.get("f100"),
            }
            for row in rows
        ]
        result = market_result(
            provider="eastmoney",
            market="cn",
            data={"sort": sort, "count": len(stocks), "stocks": stocks},
            timezone_name="Asia/Shanghai",
        )
        result["status_code"] = 0
        return result

    async def get_hot_board(
        self,
        board_type: str = "concept",
        sort: str = "rise",
        count: int = 10,
    ) -> dict:
        """获取东方财富板块涨幅、资金流或五日涨幅排行。"""
        if board_type not in {"industry", "concept"}:
            raise ValueError("board_type must be industry or concept")
        sort_fields = {
            "rise": "change_pct",
            "flow": "main_net_inflow",
            "5day": "change_5d_pct",
        }
        if sort not in sort_fields:
            raise ValueError("sort must be rise, flow or 5day")
        result = await self.get_sector_list(board_type)
        if result.get("status") != "ok":
            return result
        sectors = (result.get("data") or {}).get("sectors") or []
        sort_field = sort_fields[sort]
        ranked = sorted(
            sectors,
            key=lambda item: item.get(sort_field)
            if item.get(sort_field) is not None
            else float("-inf"),
            reverse=True,
        )[:count]
        boards = [
            {
                "code": item.get("provider_sector_code"),
                "name": item.get("sector_name"),
                "changeRate": item.get("change_pct"),
                "changeAmt": item.get("change_amount"),
                "turnoverRate": item.get("turnover_rate"),
                "amount": item.get("turnover"),
                "netFlow": item.get("main_net_inflow"),
                "upCount": item.get("up_count"),
                "downCount": item.get("down_count"),
                "leadStock": item.get("lead_stock_name"),
                "leadCode": item.get("lead_stock_code"),
                "change5day": item.get("change_5d_pct"),
            }
            for item in ranked
        ]
        return market_result(
            provider="eastmoney",
            market="cn",
            data={
                "boardType": board_type,
                "sort": sort,
                "sortName": {
                    "rise": "今日涨幅最大",
                    "flow": "资金流入最多",
                    "5day": "5日涨幅最大",
                }[sort],
                "total": len(sectors),
                "boards": boards,
            },
            timezone_name="Asia/Shanghai",
            provider_metadata={
                "ranking_field": sort_field,
                "requested_count": count,
            },
        )

    async def get_sector_constituents(
        self,
        bk_code: str,
        *,
        sector_type: str = "industry",
    ) -> dict:
        """获取东方财富板块成分股；不在 Client 内计算成分权重。"""
        if sector_type not in {"industry", "concept"}:
            raise ValueError("sector_type must be industry or concept")
        headers = {
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
            "Referer": f"https://quote.eastmoney.com/bk/90.{bk_code}.html",
        }
        params = {
            "pn": 1,
            "pz": 500,
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f3",
            "fs": f"b:{bk_code}+f:!50",
            "fields": "f2,f3,f5,f6,f8,f9,f12,f14,f15,f16,f17,f18,f20,f21",
        }
        raw = None
        last_error = None
        hosts = list(self.PUSH2_HOSTS)
        random.shuffle(hosts)
        for host in hosts:
            try:
                response = await self._client.get(
                    f"{host}/api/qt/clist/get",
                    params=params,
                    headers=headers,
                    timeout=15,
                )
                response.raise_for_status()
                raw = response.json()
                if (raw.get("data") or {}).get("diff") is not None:
                    break
            except Exception as exc:
                last_error = exc
        if raw is None:
            return market_error(
                provider="eastmoney",
                market="cn",
                error=last_error or "all Push2 hosts failed",
                provider_metadata={
                    "attempted_hosts": hosts,
                    "bk_code": bk_code,
                },
            )
        data = raw.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("diff"), list):
            return market_error(
                provider="eastmoney",
                market="cn",
                error="Push2 constituent response schema changed",
                status=MarketDataStatus.PARSE_ERROR,
                provider_metadata={"bk_code": bk_code},
            )

        constituents = []
        for row in data["diff"]:
            code = str(row.get("f12") or "")
            name = str(row.get("f14") or "")
            if not code or not name:
                continue
            constituents.append(
                {
                    "stock_code": code,
                    "stock_name": name,
                    "latest": row.get("f2"),
                    "change_pct": row.get("f3"),
                    "volume": row.get("f5"),
                    "turnover": row.get("f6"),
                    "turnover_rate": row.get("f8"),
                    "pe": row.get("f9"),
                    "high": row.get("f15"),
                    "low": row.get("f16"),
                    "open": row.get("f17"),
                    "previous_close": row.get("f18"),
                    "market_cap": row.get("f20"),
                    "free_float_market_cap": row.get("f21"),
                    "weight": None,
                }
            )
        result = market_result(
            provider="eastmoney",
            market="cn",
            data={
                "provider_sector_code": bk_code,
                "sector_type": sector_type,
                "count": len(constituents),
                "total": data.get("total"),
                "constituents": constituents,
            },
            timezone_name="Asia/Shanghai",
            provider_metadata={
                "complete": len(constituents) == data.get("total"),
                "weight_available": False,
                "turnover_unit": "yuan",
                "market_cap_unit": "yuan",
            },
        )
        return result

    async def get_sector_kline(self, bk_code: str, period: str = "101", limit: int = 60) -> dict:
        """获取板块 K 线数据（行业/概念板块）

        Args:
            bk_code: 板块代码，如 "BK0475"
            period: "101"=日K, "102"=周K
            limit: 返回条数（≤500）
        """
        secid = f"90.{bk_code}"
        headers = {
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
            "Referer": "https://quote.eastmoney.com/",
        }
        params = {
            "secid": secid,
            "fields1": "f1,f2,f3",
            "fields2": "f51,f52,f53,f54,f55,f56,f57",
            "klt": period,
            "fqt": "1",
            "end": "20500101",
            "lmt": str(min(limit, 500)),
        }
        data = None
        selected_host = None
        errors: list[str] = []
        # 日 K 不要求盘中实时，生产网络对 delay 域更稳定。
        hosts = (
            self.PUSH2_DELAY,
            self.PUSH2HIS,
            *self.PUSH2_HOSTS,
        )
        for host in hosts:
            try:
                resp = await self._client.get(
                    f"{host}/api/qt/stock/kline/get",
                    params=params,
                    headers=headers,
                    timeout=15,
                )
                resp.raise_for_status()
                candidate = resp.json()
                candidate_data = candidate.get("data")
                if (
                    candidate.get("rc") == 0
                    and isinstance(candidate_data, dict)
                    and candidate_data.get("klines")
                ):
                    data = candidate
                    selected_host = host
                    break
                errors.append(f"{host}: empty_kline_payload")
            except Exception as exc:
                errors.append(f"{host}: {type(exc).__name__}: {exc}")
        if data is None:
            return market_result(
                provider="eastmoney",
                market="cn",
                data={
                    "bk_code": bk_code,
                    "interval": {
                        "101": "1d", "102": "1w", "103": "1mo"
                    }.get(period, period),
                    "bars": [],
                    "klines": [],
                },
                status=MarketDataStatus.EMPTY,
                provider_metadata={
                    "attempted_hosts": list(hosts),
                    "errors": errors,
                },
            )

        if data.get("rc") != 0 or not data.get("data"):
            return market_result(
                provider="eastmoney",
                market="cn",
                data=None,
                status=MarketDataStatus.EMPTY,
                provider_metadata={
                    "bk_code": bk_code,
                    "upstream_rc": data.get("rc"),
                    "selected_host": selected_host,
                },
            )

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
                "volume": int(parts[5]),
                "turnover": float(parts[6]),
            })
        result = market_result(
            provider="eastmoney",
            market="cn",
            data={
                "bk_code": bk_code,
                "name": name,
                "interval": {"101": "1d", "102": "1w", "103": "1mo"}.get(period, period),
                "adjustment": "forward",
                "total": len(klines),
                "bars": klines,
                "klines": klines,
            },
            trade_date=klines[-1]["date"] if klines else None,
            timezone_name="Asia/Shanghai",
            provider_metadata={
                "is_sector_index": True,
                "selected_host": selected_host,
                "delayed_fallback": selected_host == self.PUSH2_DELAY,
            },
        )
        return result

    async def get_futures_inventory(self, symbol: str = "沪铜") -> dict:
        """获取东方财富期货库存序列。"""
        units = {
            "沪铜": "tonne",
            "沪铝": "tonne",
            "沪锌": "tonne",
            "沪铅": "tonne",
            "镍": "tonne",
            "锡": "tonne",
            "沪金": "kg",
            "沪银": "kg",
        }
        try:
            frame = await asyncio.to_thread(ak.futures_inventory_em, symbol=symbol)
            items = [
                {
                    "date": row.get("日期"),
                    "inventory": row.get("库存"),
                    "change": row.get("增减"),
                    "unit": units.get(symbol, "source_defined"),
                    "region": "exchange_aggregate",
                }
                for row in frame.to_dict("records")
            ]
            return market_result(
                provider="eastmoney",
                market="cn",
                data={
                    "symbol": symbol,
                    "count": len(items),
                    "items": items,
                    "scope": "exchange_inventory",
                },
                trade_date=items[-1]["date"] if items else None,
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "source_page": "data.eastmoney.com/ifdata/kcsj.html",
                    "unit_source": "exchange_contract_convention",
                },
            )
        except ValueError:
            raise
        except Exception as exc:
            return market_error(provider="eastmoney", market="cn", error=exc)

    async def get_sector_minute_kline(self, bk_code: str, trade_date) -> dict:
        """获取板块当日分钟 K 线（5 分钟粒度）

        Args:
            bk_code: 板块代码，如 "BK0475"
            trade_date: 日期对象或 "YYYY-MM-DD" 字符串
        """
        secid = f"90.{bk_code}"
        date_str = str(trade_date).replace("-", "")
        headers = {
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
            "Referer": "https://quote.eastmoney.com/",
        }
        try:
            resp = await self._client.get(
                "https://push2his.eastmoney.com/api/qt/stock/kline/get",
                params={
                    "secid": secid,
                    "fields1": "f1,f2,f3",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57",
                    "klt": "5",
                    "fqt": "1",
                    "beg": date_str,
                    "end": date_str,
                    "lmt": "250",
                },
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            return market_error(provider="eastmoney", market="cn", error=exc)

        if data.get("rc") != 0 or not data.get("data"):
            return market_result(
                provider="eastmoney",
                market="cn",
                data=None,
                status=MarketDataStatus.EMPTY,
                provider_metadata={"bk_code": bk_code, "upstream_rc": data.get("rc")},
            )

        klines = []
        for line in data["data"].get("klines", []):
            parts = line.split(",")
            if len(parts) < 7:
                continue
            klines.append({
                "datetime": parts[0],
                "open": float(parts[1]),
                "close": float(parts[2]),
                "high": float(parts[3]),
                "low": float(parts[4]),
                "volume": int(parts[5]),
                "turnover": float(parts[6]),
            })
        result = market_result(
            provider="eastmoney",
            market="cn",
            data={
                "bk_code": bk_code,
                "interval": "5m",
                "count": len(klines),
                "bars": klines,
            },
            source_time=klines[-1]["datetime"] if klines else None,
            trade_date=str(trade_date),
            timezone_name="Asia/Shanghai",
            provider_metadata={"is_sector_index": True},
        )
        result["klines"] = klines
        return result

    async def get_sector_margin(
        self,
        sector_type: str = "industry",
        *,
        interval_days: int = 1,
        count: int = 1000,
    ) -> dict:
        """获取东方财富行业、概念或地域板块融资融券统计。"""
        type_codes = {
            "industry": "005",
            "concept": "006",
            "region": "004",
        }
        if sector_type not in type_codes:
            raise ValueError("sector_type must be industry, concept or region")
        if interval_days not in {1, 3, 5, 10}:
            raise ValueError("interval_days must be one of 1, 3, 5 or 10")
        if count < 1:
            raise ValueError("count must be greater than zero")

        report_name = (
            "RPTA_WEB_BKJYMXN"
            if interval_days == 1
            else "RPTA_WEB_BKQJYMXN"
        )
        filters = [f'(BOARD_TYPE_CODE="{type_codes[sector_type]}")']
        if interval_days != 1:
            filters.insert(0, f'(INTERVAL_TYPE="{interval_days}日")')
        try:
            rows = []
            source_total = None
            page_number = 1
            while len(rows) < count:
                response = await self._client.get(
                    self.EM_DATACENTER,
                    params={
                        "reportName": report_name,
                        "columns": "ALL",
                        "pageNumber": str(page_number),
                        "pageSize": str(min(500, count - len(rows))),
                        "sortColumns": "FIN_NETBUY_AMT",
                        "sortTypes": "-1",
                        "stat": "1",
                        "source": "WEB",
                        "client": "WEB",
                        "filter": "".join(filters),
                    },
                    headers={
                        "Referer": "https://data.eastmoney.com/rzrq/hy.html",
                        "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
                    },
                )
                response.raise_for_status()
                payload = response.json()
                result = payload.get("result") or {}
                page_rows = result.get("data") or []
                source_total = result.get("count")
                rows.extend(page_rows)
                if (
                    not page_rows
                    or source_total is None
                    or len(rows) >= int(source_total)
                ):
                    break
                page_number += 1
            sectors = [
                {
                    "provider_sector_code": f"BK{int(row['BOARD_CODE']):04d}",
                    "sector_name": row.get("BOARD_NAME"),
                    "sector_type": sector_type,
                    "trade_date": (
                        row.get("TRADE_DATE") or row.get("END_DATE") or ""
                    )[:10],
                    "interval_days": interval_days,
                    "financing_balance": row.get("FIN_BALANCE"),
                    "financing_buy": row.get("FIN_BUY_AMT"),
                    "financing_repayment": row.get("FIN_REPAY_AMT"),
                    "financing_net_buy": row.get("FIN_NETBUY_AMT"),
                    "securities_lending_balance": row.get("LOAN_BALANCE"),
                    "securities_lending_balance_volume": row.get("LOAN_BALANCE_VOL"),
                    "securities_lending_sell_volume": row.get("LOAN_SELL_VOL"),
                    "securities_lending_repay_volume": row.get("LOAN_REPAY_VOL"),
                    "securities_lending_net_sell_volume": row.get("FIN_NETSELL_AMT"),
                    "margin_balance": row.get("MARGIN_BALANCE"),
                    "financing_balance_ratio": row.get("FIN_BALANCE_RATIO"),
                    "unrestricted_market_cap": row.get("NOTLIMITED_MARKETCAP_A"),
                    "currency": "CNY",
                }
                for row in rows
                if row.get("BOARD_CODE") is not None
            ]
            source_date = next(
                (item["trade_date"] for item in sectors if item["trade_date"]),
                None,
            )
            return market_result(
                provider="eastmoney",
                market="cn",
                data={
                    "sector_type": sector_type,
                    "interval_days": interval_days,
                    "count": len(sectors),
                    "sectors": sectors,
                },
                source_time=source_date,
                trade_date=source_date,
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "classification": "eastmoney_wealth_board",
                    "source_report": report_name,
                    "source_total": source_total,
                    "pages_fetched": page_number,
                    "amount_unit": "yuan",
                    "volume_unit": "share",
                },
            )
        except (KeyError, TypeError, ValueError) as exc:
            return market_error(
                provider="eastmoney",
                market="cn",
                error=exc,
                status=MarketDataStatus.PARSE_ERROR,
                provider_metadata={
                    "sector_type": sector_type,
                    "interval_days": interval_days,
                },
            )
        except Exception as exc:
            return market_error(
                provider="eastmoney",
                market="cn",
                error=exc,
                provider_metadata={
                    "sector_type": sector_type,
                    "interval_days": interval_days,
                },
            )

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

        last_error: Exception | None = None
        received_valid_payload = False
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
                if isinstance(data.get("data"), dict):
                    received_valid_payload = True
                if data.get("data") and data["data"].get("allstock"):
                    break
            except Exception as exc:
                last_error = exc
            if attempt < 2:
                await asyncio.sleep(1)
        else:
            if not received_valid_payload and last_error is not None:
                return {
                    "status_code": -1,
                    "msg": (
                        "盘中异动接口请求失败: "
                        f"{type(last_error).__name__}: {last_error}"
                    ),
                    "data": None,
                }
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

    async def get_index_daily_bars(
        self,
        index: str,
        *,
        limit: int = 120,
    ) -> dict:
        """返回主要 A 股指数的标准化日 K 线。

        指数日线每天都会新增，因此这里不能使用跨日原始响应缓存。 ``index``
        接受 ``INDEX_SECIDS`` 中的中文名称或东方财富 secid。
        """

        secid = self.INDEX_SECIDS.get(index, index)
        if secid not in self.INDEX_SECIDS.values():
            raise ValueError(f"unsupported A-share index: {index}")
        normalized_limit = max(2, min(int(limit), 500))
        begin = (datetime.now(timezone.utc) - timedelta(days=800)).strftime(
            "%Y%m%d"
        )
        raw = await self.get_index_kline(secid, begin, normalized_limit)
        payload = raw.get("data") if isinstance(raw, dict) else None
        if not isinstance(payload, dict):
            return market_error(
                provider="eastmoney",
                market="cn",
                error="index daily response contains no data",
                provider_metadata={"index": index, "secid": secid},
            )
        bars = []
        for line in payload.get("klines") or []:
            parts = str(line).split(",")
            if len(parts) < 7:
                continue
            try:
                bars.append(
                    {
                        "date": parts[0],
                        "open": float(parts[1]),
                        "close": float(parts[2]),
                        "high": float(parts[3]),
                        "low": float(parts[4]),
                        "volume": float(parts[5]),
                        "turnover": float(parts[6]),
                    }
                )
            except (TypeError, ValueError):
                continue
        bars = bars[-normalized_limit:]
        name = next(
            (key for key, value in self.INDEX_SECIDS.items() if value == secid),
            str(payload.get("name") or index),
        )
        return market_result(
            provider="eastmoney",
            market="cn",
            data={
                "name": name,
                "symbol": secid.split(".", 1)[-1],
                "secid": secid,
                "interval": "1d",
                "count": len(bars),
                "bars": bars,
            },
            trade_date=bars[-1]["date"] if bars else None,
            timezone_name="Asia/Shanghai",
            provider_metadata={
                "asset_type": "benchmark_index",
                "complete": bool(bars),
            },
        )

    # 不缓存：每天有新数据，同参数不同时间返回不同结果
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

    async def get_market_breadth(self) -> dict:
        """一次请求获取全 A 股涨跌家数、成交额和主要指数行情。"""
        fields = "f2,f3,f4,f5,f6,f12,f13,f14,f104,f105,f106,f124"
        headers = {
            "Referer": "https://quote.eastmoney.com/center/",
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
        }
        params = {
            "fltt": "2",
            "invt": "2",
            "np": "1",
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
            "secids": ",".join(self.MARKET_BREADTH_SECIDS),
            "fields": fields,
        }
        errors: list[str] = []
        hosts = (self.PUSH2_HOSTS[0], self.PUSH2_DELAY)
        for host in hosts:
            try:
                response = await self._client.get(
                    f"{host}/api/qt/ulist.np/get",
                    params=params,
                    headers=headers,
                    timeout=min(
                        self._request_timeout,
                        3 if host != self.PUSH2_DELAY else 8,
                    ),
                )
                response.raise_for_status()
                payload = response.json()
                rows = (payload.get("data") or {}).get("diff")
                if not isinstance(rows, list):
                    return market_error(
                        provider="eastmoney",
                        market="cn",
                        error="Eastmoney market breadth response schema changed",
                        status=MarketDataStatus.PARSE_ERROR,
                        provider_metadata={"host": host},
                    )
                rows_by_code = {
                    str(row.get("f12") or ""): row
                    for row in rows
                    if isinstance(row, dict)
                }
                required_codes = {
                    secid.split(".", 1)[1]
                    for secid in self.MARKET_BREADTH_SECIDS
                }
                missing_codes = sorted(required_codes - rows_by_code.keys())
                if missing_codes:
                    return market_error(
                        provider="eastmoney",
                        market="cn",
                        error=f"Eastmoney market breadth missing indices: {missing_codes}",
                        status=MarketDataStatus.PARSE_ERROR,
                        provider_metadata={"host": host},
                    )

                def as_int(value, default: int = 0) -> int:
                    try:
                        return int(float(value))
                    except (TypeError, ValueError):
                        return default

                def as_float(value) -> float | None:
                    try:
                        return float(value)
                    except (TypeError, ValueError):
                        return None

                source_times = {
                    code: datetime.fromtimestamp(
                        as_int(row["f124"]),
                        tz=timezone.utc,
                    )
                    for code, row in rows_by_code.items()
                    if row.get("f124") not in (None, "", "-")
                }
                if not source_times:
                    return market_error(
                        provider="eastmoney",
                        market="cn",
                        error="Eastmoney market breadth missing source timestamps",
                        status=MarketDataStatus.PARSE_ERROR,
                        provider_metadata={"host": host},
                    )
                china_timezone = timezone(timedelta(hours=8))
                latest_source_time = max(source_times.values())
                latest_source_time_local = latest_source_time.astimezone(
                    china_timezone
                )

                breadth_codes = ("000002", "399107", "899050")
                turnover_codes = ("000001", "399001", "899050")
                up_count = sum(as_int(rows_by_code[code]["f104"]) for code in breadth_codes)
                down_count = sum(as_int(rows_by_code[code]["f105"]) for code in breadth_codes)
                flat_count = sum(as_int(rows_by_code[code]["f106"]) for code in breadth_codes)
                turnover = sum(
                    as_float(rows_by_code[code]["f6"]) or 0.0
                    for code in turnover_codes
                )
                quote_codes = (
                    "000001",
                    "399001",
                    "399006",
                    "899050",
                    "000680",
                    "000688",
                    "000510",
                    "000300",
                    "000852",
                    "000016",
                    "000905",
                    "399330",
                    "000698",
                )
                indices = [
                    {
                        "code": code,
                        "name": rows_by_code[code].get("f14"),
                        "close": as_float(rows_by_code[code]["f2"]),
                        "change": as_float(rows_by_code[code]["f4"]),
                        "change_percent": as_float(rows_by_code[code]["f3"]),
                        "volume": as_float(rows_by_code[code]["f5"]),
                        "turnover": as_float(rows_by_code[code]["f6"]),
                        "turnover_unit": "yuan",
                        "source_time": source_times.get(
                            code,
                            latest_source_time,
                        ).astimezone(
                            china_timezone
                        ).isoformat(),
                    }
                    for code in quote_codes
                ]
                components = [
                    {
                        "code": code,
                        "name": rows_by_code[code].get("f14"),
                        "up_count": as_int(rows_by_code[code]["f104"]),
                        "down_count": as_int(rows_by_code[code]["f105"]),
                        "flat_count": as_int(rows_by_code[code]["f106"]),
                        "source_time": source_times.get(
                            code,
                            latest_source_time,
                        ).astimezone(
                            china_timezone
                        ).isoformat(),
                    }
                    for code in breadth_codes
                ]
                covered_count = up_count + down_count + flat_count
                return market_result(
                    provider="eastmoney",
                    market="cn",
                    data={
                        "covered_security_count": covered_count,
                        "valid_quote_count": covered_count,
                        "up_count": up_count,
                        "down_count": down_count,
                        "flat_count": flat_count,
                        "turnover": turnover,
                        "turnover_unit": "yuan",
                        "indices": indices,
                        "breadth_components": components,
                    },
                    observed_at=latest_source_time,
                    source_time=latest_source_time_local.isoformat(),
                    trade_date=latest_source_time_local.date(),
                    timezone_name="Asia/Shanghai",
                    provider_metadata={
                        "host": host,
                        "universe": "sh_sz_bj_a_shares",
                        "complete": True,
                        "aggregation": "provider_index_aggregate",
                        "single_request": True,
                        "freshness": (
                            "delayed" if host == self.PUSH2_DELAY else "realtime"
                        ),
                        "delayed_fallback": host == self.PUSH2_DELAY,
                        "breadth_fields": {
                            "f104": "up_count",
                            "f105": "down_count",
                            "f106": "flat_count",
                            "f124": "source_timestamp",
                        },
                    },
                )
            except Exception as exc:
                errors.append(f"{host}: {type(exc).__name__}: {exc}")
        return market_error(
            provider="eastmoney",
            market="cn",
            error="; ".join(errors) or "Eastmoney market breadth request failed",
            provider_metadata={"attempted_hosts": list(hosts)},
        )

    async def get_market_intraday_turnover_comparison(self) -> dict:
        """获取沪深北最近两日逐分钟成交额，并对齐上一交易日同时刻。"""
        headers = {
            "Referer": "https://quote.eastmoney.com/",
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
        }

        async def fetch_one(secid: str) -> dict:
            errors: list[str] = []
            for host in (self.PUSH2HIS, self.PUSH2_HOSTS[0], self.PUSH2_DELAY):
                try:
                    response = await self._client.get(
                        f"{host}/api/qt/stock/trends2/get",
                        params={
                            "secid": secid,
                            "fields1": (
                                "f1,f2,f3,f4,f5,f6,f7,f8,"
                                "f9,f10,f11,f12,f13"
                            ),
                            "fields2": (
                                "f51,f52,f53,f54,f55,f56,f57,f58"
                            ),
                            "ndays": "2",
                            "iscr": "0",
                            "iscca": "0",
                            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
                        },
                        headers=headers,
                        timeout=min(
                            self._request_timeout,
                            5 if host != self.PUSH2_DELAY else 8,
                        ),
                    )
                    response.raise_for_status()
                    payload = response.json()
                    data = payload.get("data") or {}
                    trends = data.get("trends")
                    if not isinstance(trends, list) or not trends:
                        raise ValueError("empty intraday turnover trends")
                    return {
                        "secid": secid,
                        "name": data.get("name") or secid,
                        "trends": trends,
                        "host": host,
                    }
                except Exception as exc:
                    errors.append(f"{host}: {type(exc).__name__}: {exc}")
            raise RuntimeError(
                f"{secid} intraday turnover unavailable: {'; '.join(errors)}"
            )

        try:
            responses = await asyncio.gather(
                *(fetch_one(secid) for secid in self.MARKET_TURNOVER_SECIDS)
            )
            series_by_market: list[dict] = []
            for response in responses:
                values_by_date: dict[str, dict[str, float]] = {}
                for line in response["trends"]:
                    parts = str(line).split(",")
                    if len(parts) < 7 or " " not in parts[0]:
                        continue
                    trade_date, minute = parts[0].split(" ", 1)
                    try:
                        turnover = float(parts[6])
                    except (TypeError, ValueError):
                        continue
                    values_by_date.setdefault(trade_date, {})[minute] = turnover
                if len(values_by_date) < 2:
                    raise ValueError(
                        f"{response['secid']} has fewer than two trade dates"
                    )
                series_by_market.append(
                    {
                        **response,
                        "values_by_date": values_by_date,
                    }
                )

            common_dates = set.intersection(
                *(
                    set(item["values_by_date"])
                    for item in series_by_market
                )
            )
            if len(common_dates) < 2:
                raise ValueError(
                    "intraday turnover markets have no two common trade dates"
                )
            previous_trade_date, current_trade_date = sorted(common_dates)[-2:]
            current_last_minutes = [
                max(item["values_by_date"][current_trade_date])
                for item in series_by_market
            ]
            comparison_minute = min(current_last_minutes)

            components = []
            current_turnover = 0.0
            previous_turnover = 0.0
            for item in series_by_market:
                current_value = sum(
                    value
                    for minute, value in item["values_by_date"][
                        current_trade_date
                    ].items()
                    if minute <= comparison_minute
                )
                previous_value = sum(
                    value
                    for minute, value in item["values_by_date"][
                        previous_trade_date
                    ].items()
                    if minute <= comparison_minute
                )
                current_turnover += current_value
                previous_turnover += previous_value
                components.append(
                    {
                        "secid": item["secid"],
                        "name": item["name"],
                        "current_turnover": current_value,
                        "previous_turnover": previous_value,
                    }
                )

            china_timezone = timezone(timedelta(hours=8))
            source_time = datetime.strptime(
                f"{current_trade_date} {comparison_minute}",
                "%Y-%m-%d %H:%M",
            ).replace(tzinfo=china_timezone)
            return market_result(
                provider="eastmoney",
                market="cn",
                data={
                    "current_trade_date": current_trade_date,
                    "previous_trade_date": previous_trade_date,
                    "comparison_time": comparison_minute,
                    "current_turnover": current_turnover,
                    "previous_turnover": previous_turnover,
                    "turnover_unit": "yuan",
                    "components": components,
                },
                source_time=source_time.isoformat(),
                trade_date=current_trade_date,
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "interval": "1m",
                    "amount_field": "f56",
                    "aggregation": (
                        "sum minute turnover through comparison time "
                        "across sh_sz_bj"
                    ),
                    "hosts": [item["host"] for item in series_by_market],
                },
            )
        except Exception as exc:
            return market_error(
                provider="eastmoney",
                market="cn",
                error=exc,
                provider_metadata={
                    "secids": list(self.MARKET_TURNOVER_SECIDS),
                    "interval": "1m",
                },
            )

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

    @cached(ttl=1209600, source="eastmoney", source_name="东方财富", domain="macro", frequency="monthly", market="macro")
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

    @cached(ttl=1209600, source="eastmoney", source_name="东方财富", domain="macro", frequency="monthly", market="macro")
    async def get_macro_cpi(self, page_size: int = 12) -> list:
        """CPI 居民消费价格指数"""
        return await self.get_macro_indicator("RPT_ECONOMY_CPI", page_size)

    @cached(ttl=1209600, source="eastmoney", source_name="东方财富", domain="macro", frequency="monthly", market="macro")
    async def get_macro_ppi(self, page_size: int = 12) -> list:
        """PPI 工业生产者出厂价格指数"""
        return await self.get_macro_indicator("RPT_ECONOMY_PPI", page_size)

    @cached(ttl=1209600, source="eastmoney", source_name="东方财富", domain="macro", frequency="monthly", market="macro")
    async def get_macro_pmi(self, page_size: int = 12) -> list:
        """PMI 制造业采购经理指数"""
        return await self.get_macro_indicator("RPT_ECONOMY_PMI", page_size)

    @cached(ttl=1209600, source="eastmoney", source_name="东方财富", domain="macro", frequency="monthly", market="macro")
    async def get_macro_gdp(self, page_size: int = 12) -> list:
        """GDP"""
        return await self.get_macro_indicator("RPT_ECONOMY_GDP", page_size)

    @cached(ttl=1209600, source="eastmoney", source_name="东方财富", domain="macro", frequency="monthly", market="macro")
    async def get_macro_money_supply(self, page_size: int = 12) -> list:
        """M2 货币供应量"""
        return await self.get_macro_indicator("RPT_ECONOMY_CURRENCY_SUPPLY", page_size)

    @cached(ttl=1209600, source="eastmoney", source_name="东方财富", domain="macro", frequency="monthly", market="macro")
    async def get_macro_rmb_loan(self, page_size: int = 12) -> list:
        """人民币贷款（社融核心组成）"""
        return await self.get_macro_indicator("RPT_ECONOMY_RMB_LOAN", page_size)

    @cached(ttl=1209600, source="eastmoney", source_name="东方财富", domain="macro", frequency="monthly", market="macro")
    async def get_macro_forex_reserve(self, page_size: int = 12) -> list:
        """外汇储备"""
        return await self.get_macro_indicator("RPT_ECONOMY_GOLD_CURRENCY", page_size)

    @cached(ttl=1209600, source="eastmoney", source_name="东方财富", domain="macro", frequency="monthly", market="macro")
    async def get_macro_fixed_asset_invest(self, page_size: int = 12) -> list:
        """固定资产投资"""
        return await self.get_macro_indicator("RPT_ECONOMY_ASSET_INVEST", page_size)

    @cached(ttl=1209600, source="eastmoney", source_name="东方财富", domain="macro", frequency="yearly", market="macro")
    async def get_macro_fdi(self, page_size: int = 20) -> list:
        """外商直接投资 (FDI) — 年度数据（RPT_ECONOMY_FDI_NEW）

        旧接口 RPT_ECONOMY_FDI 为月度数据，2023-07 后停更。
        新接口为年度数据，字段: ACTUAL_FOREIGN(亿美元), ACTUAL_FOREIGN_SAME(同比比率)
        """
        return await self.get_macro_indicator("RPT_ECONOMY_FDI_NEW", page_size)

    # ==================== 券商研报 ====================

    # 不缓存：页码翻页，page=N 在不同时间返回不同数据
    async def get_research_reports(self, page_size: int = 20, page: int = 1, industry: str = "*", rating: str = "*") -> list:
        """获取券商研报列表

        Args:
            page_size: 每页条数
            page: 页码（从 1 开始）
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
                "pageNo": str(page),
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

    EM_ARTICLE_PATTERNS = [
        r'<div[^>]*id="ContentBody"[^>]*>',
        r'<div[^>]*class="[^"]*ContentBody[^"]*"[^>]*>',
        r'<div[^>]*class="[^"]*newsContent[^"]*"[^>]*>',
        r'<div[^>]*class="[^"]*article-body[^"]*"[^>]*>',
    ]

    @cached(ttl=1209600, source="eastmoney", source_name="东方财富", domain="news", frequency="daily", market="a_share")
    async def fetch_article_content(self, url: str) -> str:
        """抓取东方财富文章正文"""
        html = await self._fetch_article_html(url, referer="https://finance.eastmoney.com/")
        return self._extract_article_text(html, self.EM_ARTICLE_PATTERNS)

    # 不缓存：搜索结果翻页，page=N 在不同时间返回不同数据
    async def get_news_by_keyword(self, keyword: str, page_size: int = 20, page: int = 1, with_content: bool = True) -> list:
        """按关键词搜索东方财富新闻

        Args:
            keyword: 搜索关键词（如"AI"、"新能源"）
            page_size: 每页条数
            page: 页码（从 1 开始）
            with_content: 是否并发抓取每条的详情页正文

        Returns:
            [{title, date, content(真实正文), summary(列表页摘要), mediaName(来源), url}, ...]
        """
        import json as _json
        import asyncio as _asyncio
        param = _json.dumps({
            "uid": "",
            "keyword": keyword,
            "type": ["cmsArticleWebOld"],
            "pageIndex": page,
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
            # 原始 content 字段作为 summary 保留
            item["summary"] = item.get("content", "")

        # 并发抓取正文
        if with_content and items:
            urls = [item.get("url", "") for item in items]
            tasks = [self.fetch_article_content(u) for u in urls]
            contents = await _asyncio.gather(*tasks, return_exceptions=True)
            for item, c in zip(items, contents):
                if isinstance(c, str) and c:
                    item["content"] = c  # 用真实正文覆盖
        return items

    # ==================== 股吧人气榜 ====================

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

    async def get_macro_since(self, report_name: str, since_date: str, page_size: int = 24) -> list:
        """宏观指标增量采集：只返回 since_date 之后的数据

        Args:
            report_name: datacenter reportName
            since_date: 上次采集日期（"2026-01-01"）
        """
        items = await self.get_macro_indicator(report_name, page_size)
        return [item for item in items if (item.get("REPORT_DATE") or "")[:10] > since_date]

    async def get_research_reports_since(self, since_date: str, page_size: int = 50) -> list:
        """研报增量采集：只返回 since_date 之后的"""
        items = await self.get_research_reports(page_size)
        return [item for item in items if (item.get("publishDate") or "")[:10] > since_date]

    async def get_news_by_keyword_since(self, keyword: str, since_date: str, page_size: int = 50) -> list:
        """新闻增量采集：只返回 since_date 之后的"""
        items = await self.get_news_by_keyword(keyword, page_size)
        return [item for item in items if (item.get("date") or "")[:10] > since_date]

    # ==================== 股吧帖子（SSR HTML 解析） ====================

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
