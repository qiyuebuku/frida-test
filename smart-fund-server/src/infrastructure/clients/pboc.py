"""人民银行/汇率数据客户端"""

import re
import asyncio
from datetime import datetime, timedelta

from src.infrastructure.clients.base import BaseClient, cached


class PBOCClient(BaseClient):
    """人民银行/汇率/利率数据客户端"""

    EM_DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get"

    def __init__(self, timeout: float = 10.0):
        super().__init__(timeout)

    PBOC_NEWS_URL = "https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/index.html"
    PBOC_BASE_URL = "https://www.pbc.gov.cn"

    @cached(ttl=1209600, source="pboc", domain="news", frequency="daily", market="macro", source_name="人民银行")
    async def get_announcements(self, limit: int = 20) -> list:
        """获取人民银行新闻发布列表

        Returns:
            [{title, url, published_at, source, source_name}, ...]
        """
        return await self._fetch_pboc_list(
            self.PBOC_NEWS_URL,
            "/goutongjiaoliu/113456/113469/",
            limit=limit,
            source_tag="pboc",
        )

    @cached(ttl=1209600, source="pboc", domain="news", frequency="daily", market="macro", source_name="人民银行")
    async def get_announcements_since(self, since_date: str, limit: int = 50) -> list:
        """增量采集：只获取 since_date 之后的公告"""
        all_items = await self.get_announcements(limit=limit)
        return [item for item in all_items if item.get("published_at", "") > since_date]

    # ==================== 公开市场操作公告 ====================

    PBOC_OMO_URL = "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/index.html"

    @cached(ttl=1209600, source="pboc", domain="news", frequency="daily", market="macro", source_name="人民银行")
    async def get_omo_announcements(self, limit: int = 20) -> list:
        """获取公开市场操作公告列表（每日逆回购/MLF/国债买卖）

        Returns:
            [{title, url, published_at, source, source_name}, ...]
        """
        return await self._fetch_pboc_list(
            self.PBOC_OMO_URL,
            "/zhengcehuobisi/125207/125213/125431/125475/",
            limit=limit,
            source_tag="pboc_omo",
        )

    @cached(ttl=1209600, source="pboc", domain="news", frequency="daily", market="macro", source_name="人民银行")
    async def get_omo_announcements_since(self, since_date: str, limit: int = 50) -> list:
        """增量采集公开市场操作公告"""
        all_items = await self.get_omo_announcements(limit=limit)
        return [item for item in all_items if item.get("published_at", "") > since_date]

    # ==================== 货币政策公告 ====================

    PBOC_MONETARY_URL = "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125440/index.html"

    @cached(ttl=1209600, source="pboc", domain="news", frequency="daily", market="macro", source_name="人民银行")
    async def get_monetary_policy(self, limit: int = 20) -> list:
        """获取货币政策公告列表（LPR公告、利率决议、政策解读等）

        Returns:
            [{title, url, published_at, source, source_name}, ...]
        """
        return await self._fetch_pboc_list(
            self.PBOC_MONETARY_URL,
            "/zhengcehuobisi/125207/125213/125440/",
            limit=limit,
            source_tag="pboc_monetary",
        )

    @cached(ttl=1209600, source="pboc", domain="news", frequency="daily", market="macro", source_name="人民银行")
    async def get_monetary_policy_since(self, since_date: str, limit: int = 50) -> list:
        """增量采集货币政策公告"""
        all_items = await self.get_monetary_policy(limit=limit)
        return [item for item in all_items if item.get("published_at", "") > since_date]

    # ==================== 通用列表解析 ====================

    PBOC_ARTICLE_PATTERNS = [
        r'<div[^>]*id="zoom"[^>]*>',
        r'<div[^>]*class="[^"]*content[^"]*"[^>]*>',
        r'<div[^>]*class="[^"]*main_r[^"]*"[^>]*>',
    ]

    @cached(ttl=1209600, source="pboc", domain="news", frequency="daily", market="macro", source_name="人民银行")
    async def get_content(self, url: str) -> str:
        """抓取人民银行公告详情页正文"""
        html = await self._fetch_article_html(url, referer="http://www.pbc.gov.cn/")
        return self._extract_article_text(html, self.PBOC_ARTICLE_PATTERNS)

    async def _fetch_pboc_list(self, url: str, path_prefix: str,
                                limit: int = 20, source_tag: str = "pboc") -> list:
        """通用人民银行列表页解析"""
        resp = await self._client.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html",
            },
        )
        resp.raise_for_status()
        pattern = re.compile(
            r'<a[^>]*href="(' + re.escape(path_prefix) + r'[^"]+)"[^>]*\btitle="([^"]+)"[^>]*>.*?</a>'
            r'.*?<span[^>]*>\s*(\d{4}-\d{2}-\d{2})\s*</span>',
            re.DOTALL,
        )
        items = []
        for match in pattern.finditer(resp.text):
            rel_url, title, date_str = match.group(1), match.group(2).strip(), match.group(3)
            if not title or len(title) < 5:
                continue
            items.append({
                "title": title,
                "url": f"{self.PBOC_BASE_URL}{rel_url}",
                "published_at": date_str,
                "source": source_tag,
                "source_name": "中国人民银行",
            })
            if len(items) >= limit:
                break
        return items

    @cached(ttl=1209600, source="pboc", domain="macro", frequency="daily", market="macro", source_name="人民银行")
    async def get_currency_data(self, tab: str = "usdcny", days: int = 120) -> dict:
        """货币风向

        tab: "usdcny"=美元/离岸人民币汇率, "shibor"=Shibor利率+LPR
        days: 返回近 N 个交易日
        """
        if tab == "usdcny":
            return await self._get_usdcny_kline(days)
        elif tab == "shibor":
            return await self._get_shibor_trend(days)
        return {"status_code": -1, "msg": f"未知tab: {tab}"}

    async def _get_usdcny_kline(self, days: int = 120) -> dict:
        """美元/人民币中间价 (CFETS) + 离岸人民币 (push2his) 双数据源"""
        items = await self._fetch_usdcny_cfets(days)
        source = "CFETS中间价"
        if not items:
            items = await self._fetch_usdcnh_push2his(days)
            source = "离岸人民币(USDCNH)"

        if not items:
            return {"status_code": -1, "msg": "CFETS和push2his均无数据"}

        # 计算均线和趋势
        closes = [i["close"] for i in items]
        latest = items[-1]

        def _ma(n):
            return round(sum(closes[-n:]) / n, 4) if len(closes) >= n else None

        ma5, ma20, ma60 = _ma(5), _ma(20), _ma(60)

        # 涨跌幅
        prev = items[-2]["close"] if len(items) >= 2 else latest["close"]
        change_rate = round((latest["close"] - prev) / prev * 100, 4)

        # 近30日/近60日变化
        chg_30d = round((latest["close"] - items[-30]["close"]) / items[-30]["close"] * 100, 2) if len(items) >= 30 else None
        chg_60d = round((latest["close"] - items[-60]["close"]) / items[-60]["close"] * 100, 2) if len(items) >= 60 else None

        # 近一年最高/最低
        year_high = max(closes[-250:]) if len(closes) >= 20 else max(closes)
        year_low = min(closes[-250:]) if len(closes) >= 20 else min(closes)

        signals = []
        if ma5 and ma20:
            if latest["close"] > ma20:
                signals.append("汇率在20日均线上方，美元短期偏强")
            else:
                signals.append("汇率在20日均线下方，人民币短期偏强")
        if ma5 and ma20 and ma60:
            if ma5 > ma20 > ma60:
                signals.append("均线多头排列，美元趋势走强（人民币贬值压力）")
            elif ma5 < ma20 < ma60:
                signals.append("均线空头排列，美元趋势走弱（人民币升值方向）")
        if chg_30d is not None:
            if chg_30d > 1:
                signals.append(f"近30日人民币贬值 {chg_30d:.2f}%，注意汇率风险")
            elif chg_30d < -1:
                signals.append(f"近30日人民币升值 {abs(chg_30d):.2f}%，有利于进口型基金")

        return {
            "status_code": 0,
            "data": {
                "tab": "usdcny",
                "name": f"美元/人民币 ({source})",
                "latest": latest,
                "changeRate": change_rate,
                "ma5": ma5, "ma20": ma20, "ma60": ma60,
                "chg30d": chg_30d, "chg60d": chg_60d,
                "yearHigh": year_high, "yearLow": year_low,
                "signals": signals,
                "items": items[-days:],
            },
        }

    async def _fetch_usdcny_cfets(self, days: int = 120) -> list:
        """从中国外汇交易中心(CFETS)获取 USD/CNY 中间价历史
        CFETS 限制单次查询不超过约 90 天，超过时分段查询合并"""
        cfets_headers = {
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
            "Referer": "https://www.chinamoney.com.cn/chinese/bkccpr/",
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
        end = datetime.now()
        chunk_days = 80  # 每段查 80 天以内
        all_records = []

        # 分段查询
        seg_end = end
        remaining = days + 10  # 多取一些确保够用
        while remaining > 0:
            seg_start = seg_end - timedelta(days=chunk_days)
            try:
                resp = await self._client.get(
                    "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-ccpr/CcprHisNew",
                    params={
                        "startDate": seg_start.strftime("%Y-%m-%d"),
                        "endDate": seg_end.strftime("%Y-%m-%d"),
                        "currency": "USD/CNY",
                        "pageNo": 1,
                        "pageSize": 200,
                    },
                    headers=cfets_headers,
                    timeout=15,
                )
                if resp.status_code != 200:
                    break
                data = resp.json()
                records = data.get("records", [])
                if records:
                    all_records.extend(records)
                remaining -= chunk_days
                seg_end = seg_start - timedelta(days=1)
                if not records:
                    break
                # 避免频率限制
                await asyncio.sleep(0.3)
            except Exception:
                break

        if not all_records:
            return []

        # 去重 + 按日期升序排列
        seen = set()
        items = []
        for r in all_records:
            date_str = r.get("date", "")
            if date_str in seen:
                continue
            seen.add(date_str)
            vals = r.get("values", [])
            if vals:
                rate = float(vals[0])
                items.append({
                    "date": date_str,
                    "close": rate,
                    "open": rate,
                    "high": rate,
                    "low": rate,
                })
        items.sort(key=lambda x: x["date"])
        return items

    async def _fetch_usdcnh_push2his(self, days: int = 120) -> list:
        """从东方财富 push2his 获取离岸人民币(USDCNH)日K线"""
        headers = {
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
            "Referer": "https://quote.eastmoney.com/",
        }
        for attempt in range(3):
            try:
                resp = await self._client.get(
                    "https://push2his.eastmoney.com/api/qt/stock/kline/get",
                    params={
                        "secid": "133.USDCNH",
                        "klt": "101", "fqt": "1",
                        "lmt": str(min(days, 500)),
                        "end": "20500000", "iscca": "1",
                        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8",
                        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64",
                        "ut": "f057cbcbce2a86e2866ab8877db1d059",
                        "forcect": 1,
                    },
                    headers=headers,
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("rc") != 0 or not data.get("data"):
                    return []
                items = []
                for line in data["data"].get("klines", []):
                    parts = line.split(",")
                    if len(parts) < 7:
                        continue
                    items.append({
                        "date": parts[0],
                        "open": float(parts[1]),
                        "close": float(parts[2]),
                        "high": float(parts[3]),
                        "low": float(parts[4]),
                    })
                return items
            except Exception:
                if attempt < 2:
                    await asyncio.sleep(1)
        return []

    async def _get_shibor_trend(self, days: int = 60) -> dict:
        """Shibor利率走势 + LPR变化"""
        em_headers = {
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
            "Referer": "https://data.eastmoney.com/",
        }

        async def _fetch_shibor():
            """获取 Shibor 隔夜/1周/1月/3月 近N日数据"""
            # 获取隔夜(O/N)利率
            periods = [
                ("001", "隔夜"),
                ("101", "1周"),
                ("201", "1月"),
                ("203", "3月"),
            ]
            result = {}
            for indicator_id, name in periods:
                resp = await self._client.get(
                    self.EM_DATACENTER,
                    params={
                        "reportName": "RPT_IMP_INTRESTRATEN",
                        "columns": "REPORT_DATE,IR_RATE,CHANGE_RATE,INDICATOR_ID",
                        "filter": f'(MARKET_CODE="001")(INDICATOR_ID="{indicator_id}")',
                        "pageNumber": "1",
                        "pageSize": str(days),
                        "sortTypes": "-1",
                        "sortColumns": "REPORT_DATE",
                        "source": "WEB",
                        "client": "WEB",
                    },
                    headers=em_headers,
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
                rows = []
                if data.get("result") and data["result"].get("data"):
                    for r in data["result"]["data"]:
                        rows.append({
                            "date": (r.get("REPORT_DATE") or "")[:10],
                            "rate": r.get("IR_RATE"),
                            "change": r.get("CHANGE_RATE"),
                        })
                result[name] = rows
            return result

        async def _fetch_lpr():
            """获取 LPR 历史数据"""
            resp = await self._client.get(
                self.EM_DATACENTER,
                params={
                    "reportName": "RPTA_WEB_RATE",
                    "columns": "ALL",
                    "pageNumber": "1",
                    "pageSize": "24",
                    "sortTypes": "-1",
                    "sortColumns": "TRADE_DATE",
                    "source": "WEB",
                    "client": "WEB",
                },
                headers=em_headers,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            rows = []
            if data.get("result") and data["result"].get("data"):
                for r in data["result"]["data"]:
                    rows.append({
                        "date": (r.get("TRADE_DATE") or "")[:10],
                        "lpr1y": r.get("LPR1Y"),
                        "lpr5y": r.get("LPR5Y"),
                    })
            return rows

        shibor_raw, lpr_raw = await asyncio.gather(
            _fetch_shibor(), _fetch_lpr(), return_exceptions=True,
        )

        result = {"tab": "shibor", "shibor": {}, "lpr": [], "signals": []}

        if isinstance(shibor_raw, dict):
            result["shibor"] = shibor_raw
            # 生成信号
            overnight = shibor_raw.get("隔夜", [])
            if overnight:
                latest_rate = overnight[0].get("rate")
                if latest_rate is not None:
                    if latest_rate < 1.5:
                        result["signals"].append(f"Shibor隔夜 {latest_rate}%，资金面宽松")
                    elif latest_rate > 2.5:
                        result["signals"].append(f"Shibor隔夜 {latest_rate}%，资金面偏紧")
                    else:
                        result["signals"].append(f"Shibor隔夜 {latest_rate}%，资金面适中")
                # 近5日趋势
                if len(overnight) >= 5:
                    avg_recent = sum(r["rate"] for r in overnight[:5]) / 5
                    avg_prev = sum(r["rate"] for r in overnight[5:10]) / min(5, len(overnight[5:10])) if len(overnight) >= 6 else avg_recent
                    if avg_recent > avg_prev * 1.1:
                        result["signals"].append("近5日Shibor隔夜利率上升，流动性收紧信号")
                    elif avg_recent < avg_prev * 0.9:
                        result["signals"].append("近5日Shibor隔夜利率下降，流动性宽松信号")

        if isinstance(lpr_raw, list):
            result["lpr"] = lpr_raw
            if len(lpr_raw) >= 2:
                curr, prev = lpr_raw[0], lpr_raw[1]
                if curr.get("lpr1y") and prev.get("lpr1y"):
                    if curr["lpr1y"] < prev["lpr1y"]:
                        result["signals"].append(f"LPR 1年期从 {prev['lpr1y']}% 降至 {curr['lpr1y']}%，货币政策偏宽松")
                    elif curr["lpr1y"] > prev["lpr1y"]:
                        result["signals"].append(f"LPR 1年期从 {prev['lpr1y']}% 升至 {curr['lpr1y']}%，货币政策偏紧缩")
                    else:
                        result["signals"].append(f"LPR 1年期维持 {curr['lpr1y']}% 不变")

        return {"status_code": 0, "data": result}


if __name__ == "__main__":
    import os

    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        os.environ.pop(key, None)

    async def main():
        client = PBOCClient()
        try:
            # 1. 新闻发布
            items = await client.get_announcements(limit=5)
            print(f"1. 新闻发布: {len(items)} 条")
            for item in items[:3]:
                print(f"   [{item['published_at']}] {item['title'][:60]}")
            print()

            # 2. 公开市场操作
            omo = await client.get_omo_announcements(limit=10)
            print(f"2. 公开市场操作: {len(omo)} 条")
            for item in omo[:5]:
                print(f"   [{item['published_at']}] {item['title'][:60]}")
            print()

            # 3. 增量采集
            if len(omo) >= 3:
                since = omo[2]["published_at"]
                new_items = await client.get_omo_announcements_since(since_date=since)
                print(f"   增量（since={since}）: {len(new_items)} 条\n")

            # 4. 货币政策
            mp = await client.get_monetary_policy(limit=5)
            print(f"3. 货币政策: {len(mp)} 条")
            for item in mp[:3]:
                print(f"   [{item['published_at']}] {item['title'][:60]}")
        finally:
            await client.close()

    asyncio.run(main())
