"""同花顺基金 API 客户端 - 逆向自 App v11.47.03"""

import asyncio
import json
import os
from datetime import datetime, timedelta

import httpx
from typing import Optional

# 清除代理环境变量，避免 httpx 走代理导致上游请求失败
for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(key, None)


class THSFundClient:
    """同花顺基金数据客户端"""

    BASE_URL = "https://fund.10jqka.com.cn"
    DQ_BASE_URL = "https://dq.10jqka.com.cn"

    DEFAULT_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; OnePlus) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        "Referer": "https://fund.10jqka.com.cn/hxapp/ifundDetailTab/dist/index.html",
        "Cache-Control": "max-age=60",
    }

    # 可重试的错误码（如 9100=系统繁忙）
    RETRYABLE_STATUS_CODES = {9100, 9101, 9102, -1}
    MAX_RETRIES = 3
    BASE_DELAY = 1.0  # 基础延迟秒数

    def __init__(self, timeout: float = 10.0):
        self._client = httpx.AsyncClient(
            headers=self.DEFAULT_HEADERS,
            timeout=timeout,
            follow_redirects=True,
        )

    async def close(self):
        await self._client.aclose()

    async def _retry_on_busy(self, coro_func, *args, **kwargs) -> dict:
        """指数退避重试：遇到系统繁忙等临时错误时自动重试"""
        last_result = None
        for attempt in range(self.MAX_RETRIES):
            result = await coro_func(*args, **kwargs)
            # 检查是否为可重试的错误
            status_code = result.get("status_code") if isinstance(result, dict) else None
            if status_code not in self.RETRYABLE_STATUS_CODES:
                return result
            last_result = result
            # 指数退避：1s, 2s, 4s...
            delay = self.BASE_DELAY * (2 ** attempt)
            import sys
            print(f"[THS API] 系统繁忙(code={status_code})，{delay:.1f}s 后重试({attempt+1}/{self.MAX_RETRIES})...", file=sys.stderr)
            await asyncio.sleep(delay)
        # 重试耗尽，返回最后一次结果
        return last_result

    async def _get(self, url: str, params: Optional[dict] = None) -> dict:
        resp = await self._client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    async def _get_with_retry(self, url: str, params: Optional[dict] = None) -> dict:
        """带重试的 GET 请求"""
        return await self._retry_on_busy(self._get, url, params)

    async def _post(self, url: str, data: Optional[dict] = None, json: Optional[dict] = None) -> dict:
        resp = await self._client.post(url, data=data, json=json)
        resp.raise_for_status()
        return resp.json()

    async def _post_with_retry(self, url: str, data: Optional[dict] = None, json: Optional[dict] = None) -> dict:
        """带重试的 POST 请求"""
        return await self._retry_on_busy(self._post, url, data, json)

    # ========== 基金详情 ==========

    async def get_fund_detail(self, fund_code: str) -> dict:
        """基金综合详情（含净值、涨幅、基金经理、交易规则等）"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/fund/detail/data/{fund_code}/123"
        )

    async def get_fund_base(self, fund_code: str) -> dict:
        """基金基础信息（评分、风险等级、风格、基金经理）"""
        return await self._get(
            f"{self.BASE_URL}/quotation/fund_detail/v2/base/{fund_code}"
        )

    async def get_fund_info(self, fund_code: str) -> dict:
        """基金行情信息（净值、涨幅、规模、交易状态）"""
        return await self._get(
            f"{self.BASE_URL}/quotation/fund_detail/get",
            params={"fundCode": fund_code},
        )

    async def get_product_detail(self, fund_code: str) -> dict:
        """产品详情页（基本信息、投资理念、业绩基准、风险特征、分红等）"""
        resp = await self._client.get(
            f"{self.BASE_URL}/mobile/{fund_code}/newcpxq20171115.html"
        )
        resp.raise_for_status()
        raw = resp.content
        # 页面是 GBK 编码
        try:
            html = raw.decode("gbk")
        except Exception:
            html = raw.decode("utf-8", errors="replace")

        import re
        result = {}
        # 提取表格中的 key-value
        for m in re.finditer(r'<td class="u-t_th">(.*?)</td>\s*<td class="f-tr">(.*?)</td>', html, re.S):
            key = m.group(1).strip()
            val = m.group(2).strip()
            result[key] = val
        # 按 section 提取标题和内容
        for m in re.finditer(r'<h3 class="u-title f-b_1px">(.*?)</h3>(.*?)</section>', html, re.S):
            raw_title = m.group(1)
            body = m.group(2)
            # 先去掉 <em> 及其后面的所有子标签内容（tooltip），再去 HTML 标签
            clean_title = re.sub(r'<em.*', '', raw_title, flags=re.S)
            # 检查标题中是否含 span（分红统计 无 / 拆分详情 无）
            span = re.search(r'<span[^>]*>(.*?)</span>', raw_title)
            # 去掉 span 标签再提取纯文本标题
            no_span = re.sub(r'<span.*?</span>', '', clean_title, flags=re.S)
            title = re.sub(r'<.*?>', '', no_span).strip()
            if span:
                result[title] = span.group(1).strip()
                continue
            # 提取 body 中的 <p> 内容（去重，跳过注释中的重复）
            ps = re.findall(r'<p(?:\s[^>]*)?>(.*?)</p>', body, re.S)
            seen = set()
            contents = []
            for p in ps:
                text = re.sub(r'<.*?>', '', p).replace('\u3000', '').strip()
                if text and text not in seen:
                    seen.add(text)
                    contents.append(text)
            if contents:
                result[title] = contents[0]
        return {"status_code": 0, "data": result}

    async def get_fund_flag(self, fund_code: str) -> dict:
        """基金标志（是否LOF/退市、二级分类）"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/static/detail/over/{fund_code}_flag"
        )

    # ========== 净值走势 ==========

    async def get_nav_trend(self, fund_code: str, period: str = "year") -> dict:
        """净值走势图数据
        period: year(近一年) / month(近一月) / nowyear(今年以来)
        """
        return await self._get(
            f"{self.BASE_URL}/hqapi/static/detail/flashnew/{fund_code}/{period}"
        )

    async def get_realtime_trend(self, fund_code: str) -> dict:
        """实时估值分时走势（每分钟更新）"""
        return await self._get(
            f"{self.BASE_URL}/quotation/fund/detail/holder/v2/stock_trend",
            params={"fundCode": fund_code},
        )

    # ========== 业绩表现 ==========

    async def get_performance_rank(self, fund_code: str) -> dict:
        """阶段涨幅及同类排名（近一周/月/季/半年/1-5年）"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/single_fund/detail/query_rate",
            params={"fundCode": fund_code, "type": "range"},
        )

    async def get_year_return(self, fund_code: str) -> dict:
        """年度收益率及同类排名"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/single_fund/detail/query_rate",
            params={"fundCode": fund_code, "type": "year"},
        )

    async def get_max_drawdown(self, fund_code: str) -> dict:
        """最大回撤（近半年/近一年/近三年/成立以来）"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/single_fund/detail/query_drawdown",
            params={"fundCode": fund_code, "type": "range"},
        )

    async def get_periodic_rate(self, fund_code: str, group_type: str = "dayPeriodicRate") -> dict:
        """定期收益率（收益稳定度）
        group_type: dayPeriodicRate / weekPeriodicRate / monthPeriodicRate / quarterPeriodicRate / yearPeriodicRate
        """
        return await self._post(
            f"{self.BASE_URL}/quotation/fund_detail/v2/periodic_rate",
            json={"groupType": group_type, "tradeCode": fund_code, "limit": 200},
        )

    async def get_profit_contribution(self, fund_code: str, time_type: str = "threeMonth") -> dict:
        """收益贡献分析
        time_type: threeMonth / halfYear / year
        """
        return await self._get(
            f"{self.BASE_URL}/hqapi/single_fund/detail/profit_contribution",
            params={"fundCode": fund_code, "timeType": time_type},
        )

    # ========== 持仓信息 ==========

    async def get_top10_holdings(self, fund_code: str) -> dict:
        """前十大持仓"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/single_fund/detail/ten_asset_info",
            params={"fundCode": fund_code},
        )

    async def get_holding_overview(self, fund_code: str) -> dict:
        """持仓概览"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/single_fund/detail/query_hold_head",
            params={"fundCode": fund_code},
        )

    async def get_asset_allocation(self, fund_code: str, manager_id: str = "") -> dict:
        """资产配置"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/single_fund/detail/query_asset_config",
            params={"fundCode": fund_code, "managerId": manager_id},
        )

    async def get_style_preference(self, fund_code: str) -> dict:
        """投资风格偏好"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/single_fund/detail/query_type_prefer",
            params={"fundCode": fund_code},
        )

    async def get_position_dates(self, fund_code: str) -> dict:
        """持仓回顾 - 获取可用的季度日期列表及行业概要"""
        return await self._get(
            f"{self.BASE_URL}/bff-server/v1/fund/position_rank",
            params={"fund_code": fund_code},
        )

    async def get_position_detail(self, fund_code: str, end_date: str = "") -> dict:
        """持仓回顾 - 获取指定季度的前十大持仓明细"""
        return await self._get(
            f"{self.BASE_URL}/bff-server/v1/fund/position_detail",
            params={"fund_code": fund_code, "end_date": end_date},
        )

    # ========== 基金经理 ==========

    async def get_manager_info(self, fund_code: str, manager_id: str) -> dict:
        """基金经理详细信息"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/single_fund/detail/manager_label_info",
            params={"fundManagerList": manager_id, "fundCode": fund_code},
        )

    async def get_manager_profile(self, manager_id: str) -> dict:
        """基金经理完整档案（个人简历、雷达图、管理基金列表等）"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/static/fundmanager/info/{manager_id}/0"
        )

    async def get_manager_invest_history(self, manager_id: str) -> dict:
        """基金经理投资历史（管理的所有基金业绩、重仓股）"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/static/fundmanager/investhistory/{manager_id}"
        )

    async def get_manager_diagnose(self, manager_id: str) -> dict:
        """基金经理诊断评分（历史规模、回撤、年化收益）"""
        return await self._get(
            f"{self.BASE_URL}/feQuotation/manager/diagnose/detail",
            params={"id": manager_id},
        )

    async def get_manager_industry_prefer(self, manager_id: str) -> dict:
        """基金经理行业偏好"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/fund/manager/investment/get_fund_manager_industry_prefer",
            params={"fundManagerId": manager_id},
        )

    async def get_manager_represent_fund(self, manager_id: str) -> dict:
        """基金经理代表基金"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/fund/manager/investment/get_represent_fund",
            params={"fundManagerId": manager_id},
        )

    # ========== 交易规则与费率 ==========

    async def get_trade_rule(self, fund_code: str) -> dict:
        """交易规则与费率（申购/赎回费率、管理费、托管费、服务费、交易确认时间）"""
        return await self._get(
            f"{self.BASE_URL}/interface/fund/tradeRule/{fund_code}"
        )

    # ========== 规模与持有人 ==========

    async def get_scale_change(self, fund_code: str) -> dict:
        """规模变动历史（季度净资产、申购赎回金额、份额变动、持有人结构）"""
        return await self._get(
            f"{self.BASE_URL}/interface/fund/detail/{fund_code}_gmbd"
        )

    async def get_holder_ratio(self, fund_code: str) -> dict:
        """机构持仓比例历史（半年度机构持有占比变化）"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/fund/detail/holder/const/{fund_code}"
        )

    # ========== 分红历史 ==========

    async def get_dividend_history(self, fund_code: str) -> dict:
        """分红历史（从产品详情页 HTML 解析分红和拆分记录）"""
        import re
        resp = await self._client.get(
            f"{self.BASE_URL}/mobile/{fund_code}/newcpxq20171115.html"
        )
        resp.raise_for_status()
        try:
            html = resp.content.decode("gbk")
        except Exception:
            html = resp.content.decode("utf-8", errors="replace")

        result = {"dividends": [], "splits": [], "summary": ""}

        # 提取分红 section
        for m in re.finditer(r'分红统计(.*?)</section>', html, re.S):
            section = m.group(1)
            # 检查是否"无"
            span = re.search(r'<span[^>]*>(.*?)</span>', section)
            if span and span.group(1).strip() == "无":
                result["summary"] = "无分红记录"
                break
            # 提取累计分红摘要
            summary_m = re.search(r'累计分红(\d+)次.*?([\d.]+)元', section, re.S)
            if summary_m:
                result["summary"] = f"累计分红{summary_m.group(1)}次，{summary_m.group(2)}元/份"
            # 提取分红明细表
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', section, re.S)
            for row in rows:
                cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
                cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
                if len(cells) >= 3 and re.match(r'\d{4}', cells[0]):
                    result["dividends"].append({
                        "payDate": cells[0],
                        "recordDate": cells[1],
                        "perShare": cells[2],
                    })

        # 提取拆分 section
        for m in re.finditer(r'拆分详情(.*?)</section>', html, re.S):
            section = m.group(1)
            span = re.search(r'<span[^>]*>(.*?)</span>', section)
            if span and span.group(1).strip() == "无":
                break
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', section, re.S)
            for row in rows:
                cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
                cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
                if len(cells) >= 2 and re.match(r'\d{4}', cells[0]):
                    result["splits"].append({
                        "date": cells[0],
                        "detail": cells[1] if len(cells) > 1 else "",
                    })

        return {"status_code": 0, "data": result}

    # ========== 持仓股估值 ==========

    # 东方财富 secid 前缀: 沪市(含科创)=1, 深市(含创业板)=0
    async def get_holdings_valuation(self, fund_code: str) -> dict:
        """获取前十大持仓股的估值数据（PE/PB/市值）
        数据来源: 腾讯证券 web.sqt.gtimg.cn 公开行情 API
        """
        holdings = await self.get_top10_holdings(fund_code)
        stocks = holdings.get("data", {}).get("stock", [])
        if not stocks:
            return {"status_code": 0, "data": []}

        # 构建批量查询代码列表
        code_map = {}  # query_code -> stock info
        query_codes = []
        for s in stocks:
            code = s.get("secCode", "")
            if not code:
                continue
            prefix = "sh" if code.startswith(("6", "9")) else "sz"
            qcode = f"{prefix}{code}"
            query_codes.append(qcode)
            code_map[code] = s

        try:
            resp = await self._client.get(
                f"https://web.sqt.gtimg.cn/q={','.join(query_codes)}",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            raw = resp.content.decode("gbk", errors="replace")
        except Exception:
            return {"status_code": 0, "data": [
                {"secCode": s.get("secCode", ""), "secName": s.get("secName", ""),
                 "fundNavRate": s.get("fundNavRate"), "error": "获取失败"}
                for s in stocks
            ]}

        # 解析腾讯行情数据
        parsed = {}
        for line in raw.strip().split(";"):
            line = line.strip()
            if not line:
                continue
            eq_idx = line.find("=")
            if eq_idx < 0:
                continue
            content = line[eq_idx + 1:].strip().strip('"')
            fields = content.split("~")
            if len(fields) < 53:
                continue
            parsed[fields[2]] = fields

        result = []
        for s in stocks:
            code = s.get("secCode", "")
            fields = parsed.get(code)
            if not fields:
                result.append({
                    "secCode": code, "secName": s.get("secName", ""),
                    "fundNavRate": s.get("fundNavRate"), "error": "获取失败",
                })
                continue

            def _float(v):
                try:
                    return float(v) if v else None
                except (ValueError, TypeError):
                    return None

            result.append({
                "secCode": code,
                "secName": fields[1],
                "fundNavRate": s.get("fundNavRate"),
                "price": _float(fields[3]),
                "changeRate": _float(fields[32]),
                "pe": _float(fields[39]),
                "peTTM": _float(fields[52]),
                "pb": _float(fields[46]),
                "marketCap": _float(fields[44]),  # 亿元
            })
        return {"status_code": 0, "data": result}

    # ========== 持仓股历史估值 ==========

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

    async def get_holdings_valuation_percentile(self, fund_code: str, years: int = 3) -> dict:
        """获取前十大持仓的估值百分位
        1. 获取持仓列表 + 当前 PE/PB
        2. 并发获取每只股票的历史估值
        3. 计算百分位并返回汇总
        """
        # 并发获取持仓和当前估值
        holdings_task = self.get_top10_holdings(fund_code)
        valuation_task = self.get_holdings_valuation(fund_code)
        holdings_data, valuation_data = await asyncio.gather(holdings_task, valuation_task)

        stocks = holdings_data.get("data", {}).get("stock", [])
        val_list = valuation_data.get("data", [])
        val_map = {v["secCode"]: v for v in val_list if v.get("secCode")}

        if not stocks:
            return {"status_code": 0, "data": {"stocks": [], "summary": {}}}

        # 并发获取历史估值
        stock_codes = [s.get("secCode", "") for s in stocks if s.get("secCode")]
        history_tasks = [self.get_stock_valuation_history(code, years) for code in stock_codes]
        history_results = await asyncio.gather(*history_tasks, return_exceptions=True)
        history_map = {}
        for code, result in zip(stock_codes, history_results):
            if isinstance(result, list):
                history_map[code] = result

        def _percentile(current_val, history, field):
            """计算百分位: 历史中 ≤ 当前值的比例"""
            if current_val is None or not history:
                return None
            values = [h[field] for h in history if h.get(field) is not None]
            if not values:
                return None
            count_le = sum(1 for v in values if v <= current_val)
            return round(count_le / len(values) * 100, 1)

        def _rating(pct):
            if pct is None:
                return "N/A"
            if pct <= 20:
                return "低估"
            elif pct <= 40:
                return "偏低"
            elif pct <= 60:
                return "适中"
            elif pct <= 80:
                return "偏高"
            else:
                return "过热"

        result_stocks = []
        weighted_pe_pct_sum = 0.0
        weighted_pb_pct_sum = 0.0
        covered_weight = 0.0

        for s in stocks:
            code = s.get("secCode", "")
            name = s.get("secName", "")
            nav_rate = s.get("fundNavRate")
            v = val_map.get(code, {})
            pe_ttm = v.get("peTTM")
            pb = v.get("pb")
            history = history_map.get(code, [])

            pe_pct = _percentile(pe_ttm, history, "pe_ttm")
            pb_pct = _percentile(pb, history, "pb")

            # 综合评级取 PE 和 PB 百分位的较高者
            max_pct = max(pe_pct or 0, pb_pct or 0) if (pe_pct is not None or pb_pct is not None) else None
            rating = _rating(max_pct)

            item = {
                "secCode": code,
                "secName": name,
                "fundNavRate": nav_rate,
                "peTTM": pe_ttm,
                "pePct": pe_pct,
                "pb": pb,
                "pbPct": pb_pct,
                "rating": rating,
                "historyCount": len(history),
            }
            result_stocks.append(item)

            if nav_rate is not None and pe_pct is not None:
                weighted_pe_pct_sum += pe_pct * nav_rate
                covered_weight += nav_rate
            if nav_rate is not None and pb_pct is not None:
                weighted_pb_pct_sum += pb_pct * nav_rate

        weighted_pe_pct = round(weighted_pe_pct_sum / covered_weight, 1) if covered_weight > 0 else None
        weighted_pb_pct = round(weighted_pb_pct_sum / covered_weight, 1) if covered_weight > 0 else None

        return {
            "status_code": 0,
            "data": {
                "years": years,
                "stocks": result_stocks,
                "summary": {
                    "weightedPePct": weighted_pe_pct,
                    "weightedPbPct": weighted_pb_pct,
                    "coveredWeight": round(covered_weight, 2) if covered_weight else None,
                    "rating": _rating(weighted_pe_pct),
                },
            },
        }

    # ========== 净值技术面 ==========

    async def get_nav_technical(self, fund_code: str) -> dict:
        """基于近一年日净值计算技术面指标（RSI14/MA5/MA20/MA60/偏离度/信号）"""
        raw = await self.get_nav_trend(fund_code, "year")
        data_str = raw.get("data", "")
        if not data_str:
            return {"status_code": -1, "data": {}, "msg": "无净值数据"}

        # 解析净值序列：格式 "日期;x;净值;涨幅|..."，数据从新到旧排列，需反转为正序
        records = []
        for line in data_str.split("|"):
            parts = line.split(";")
            if len(parts) >= 3:
                try:
                    records.append({"date": parts[0], "nav": float(parts[2])})
                except (ValueError, IndexError):
                    continue
        records.reverse()  # 反转为时间正序（最早在前，最新在后）
        if len(records) < 15:
            return {"status_code": -1, "data": {}, "msg": f"净值数据不足({len(records)}条)"}

        navs = [r["nav"] for r in records]
        latest = records[-1]

        # RSI(14) - Wilder 平滑
        changes = [navs[i] - navs[i - 1] for i in range(1, len(navs))]
        period = 14
        gains = [max(c, 0) for c in changes[:period]]
        losses = [abs(min(c, 0)) for c in changes[:period]]
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        for c in changes[period:]:
            avg_gain = (avg_gain * (period - 1) + max(c, 0)) / period
            avg_loss = (avg_loss * (period - 1) + abs(min(c, 0))) / period
        rsi14 = round(100 * avg_gain / (avg_gain + avg_loss), 1) if (avg_gain + avg_loss) > 0 else 50.0

        # MA
        def _ma(n):
            if len(navs) < n:
                return None
            return round(sum(navs[-n:]) / n, 4)

        ma5 = _ma(5)
        ma20 = _ma(20)
        ma60 = _ma(60)
        cur = latest["nav"]

        def _dev(ma_val):
            if ma_val is None or ma_val == 0:
                return None
            return round((cur - ma_val) / ma_val * 100, 2)

        dev5 = _dev(ma5)
        dev20 = _dev(ma20)
        dev60 = _dev(ma60)

        # 信号判断
        signals = []
        if rsi14 > 70:
            signals.append("RSI超买(>70)，短期回调风险")
        elif rsi14 < 30:
            signals.append("RSI超卖(<30)，可能反弹")
        else:
            signals.append("RSI适中，未超买超卖")

        if ma5 and ma20 and ma60:
            if cur > ma5 and cur > ma20 and cur > ma60:
                signals.append("净值在所有均线之上，短期强势")
            if cur < ma20:
                signals.append("跌破20日均线，短期趋势转弱")
            if ma5 > ma20 > ma60 and cur > ma60:
                signals.append("多头排列（MA5>MA20>MA60），中期趋势向上")
            elif ma5 < ma20 < ma60:
                signals.append("空头排列（MA5<MA20<MA60），中期趋势向下")
            # 检查MA5与MA20交叉（近3日）
            if len(navs) >= 22:
                ma5_prev = sum(navs[-6:-1]) / 5
                ma20_prev = sum(navs[-21:-1]) / 20
                if ma5_prev >= ma20_prev and ma5 < ma20:
                    signals.append("短期均线死叉（MA5下穿MA20），注意风险")
                elif ma5_prev <= ma20_prev and ma5 > ma20:
                    signals.append("短期均线金叉（MA5上穿MA20），关注机会")

        return {
            "status_code": 0,
            "data": {
                "nav": cur, "date": latest["date"],
                "rsi14": rsi14,
                "ma5": ma5, "ma20": ma20, "ma60": ma60,
                "devMa5": dev5, "devMa20": dev20, "devMa60": dev60,
                "signals": signals,
            },
        }

    # ========== 大盘与行业环境 ==========

    async def get_market_environment(self) -> dict:
        """获取沪深300/创业板指趋势 + 北向资金 + 融资融券 + 国债收益率"""

        beg_date = (datetime.now() - timedelta(days=400)).strftime("%Y%m%d")
        em_kline_headers = {
            "Referer": "https://quote.eastmoney.com/",
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
        }
        em_data_headers = {
            "Referer": "https://data.eastmoney.com/",
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
        }

        async def _fetch_index_kline(secid: str):
            """获取指数近1年日K线（含重试）"""
            for attempt in range(3):
                try:
                    resp = await self._client.get(
                        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
                        params={
                            "secid": secid, "fields1": "f1,f2,f3",
                            "fields2": "f51,f52,f53,f54,f55,f56,f57",
                            "klt": "101", "fqt": "1", "beg": beg_date,
                            "end": "20500101", "lmt": "250",
                        },
                        headers=em_kline_headers,
                    )
                    resp.raise_for_status()
                    return resp.json()
                except Exception:
                    if attempt < 2:
                        await asyncio.sleep(1)
            return {}

        async def _fetch_northbound():
            """北向资金近30个交易日成交额"""
            resp = await self._client.get(
                "https://datacenter-web.eastmoney.com/api/data/v1/get",
                params={
                    "reportName": "RPT_MUTUAL_DEAL_HISTORY",
                    "columns": "TRADE_DATE,MUTUAL_TYPE,DEAL_AMT",
                    "filter": '(MUTUAL_TYPE="005")',
                    "pageNumber": "1", "pageSize": "30",
                    "sortTypes": "-1", "sortColumns": "TRADE_DATE",
                },
                headers=em_data_headers,
            )
            resp.raise_for_status()
            return resp.json()

        async def _fetch_margin():
            """全市场融资融券余额（近30个交易日）"""
            resp = await self._client.get(
                "https://datacenter-web.eastmoney.com/api/data/v1/get",
                params={
                    "reportName": "RPTA_RZRQ_LSHJ",
                    "columns": "DIM_DATE,RZYE,RQYE,RZRQYE,RZJME",
                    "pageNumber": "1", "pageSize": "30",
                    "sortTypes": "-1", "sortColumns": "dim_date",
                },
                headers=em_data_headers,
            )
            resp.raise_for_status()
            return resp.json()

        async def _fetch_bond_etf():
            """十年国债ETF（511260）近60日，价格与收益率反向"""
            return await _fetch_index_kline("1.511260")

        # 并发获取所有数据
        hs300_raw, cyb_raw, nb_raw, margin_raw, bond_raw = await asyncio.gather(
            _fetch_index_kline("1.000300"),  # 沪深300
            _fetch_index_kline("0.399006"),  # 创业板指
            _fetch_northbound(),
            _fetch_margin(),
            _fetch_bond_etf(),
            return_exceptions=True,
        )

        result = {"indices": [], "northbound": {}, "margin": {}, "bond": {}, "signals": []}

        def _parse_index(raw, name_override=None):
            """解析指数K线数据，返回标准结构"""
            if not isinstance(raw, dict):
                return None
            klines = raw.get("data", {}).get("klines", [])
            name = name_override or raw.get("data", {}).get("name", "")
            closes = []
            for line in klines:
                parts = line.split(",")
                if len(parts) >= 3:
                    try:
                        closes.append({"date": parts[0], "close": float(parts[2])})
                    except ValueError:
                        continue
            if not closes:
                return None
            latest = closes[-1]
            prev_close = closes[-2]["close"] if len(closes) >= 2 else latest["close"]
            change_rate = round((latest["close"] - prev_close) / prev_close * 100, 2)
            vals = [c["close"] for c in closes]

            def _ma(n):
                return round(sum(vals[-n:]) / n, 2) if len(vals) >= n else None

            ma5, ma20, ma60 = _ma(5), _ma(20), _ma(60)
            cur = latest["close"]
            if ma5 and ma20 and ma60:
                if ma5 > ma20 > ma60:
                    trend = "多头排列"
                elif ma5 < ma20 < ma60:
                    trend = "空头排列"
                else:
                    trend = "震荡整理"
            else:
                trend = "数据不足"
            dev_ma20 = round((cur - ma20) / ma20 * 100, 2) if ma20 else None
            return {
                "name": name, "close": cur, "date": latest["date"],
                "changeRate": change_rate,
                "ma5": ma5, "ma20": ma20, "ma60": ma60,
                "trend": trend, "devMa20": dev_ma20,
            }

        # 解析沪深300
        hs300 = _parse_index(hs300_raw)
        if hs300:
            result["indices"].append(hs300)
            if hs300["trend"] == "多头排列":
                result["signals"].append("沪深300多头排列，大盘趋势向上")
            elif hs300["trend"] == "空头排列":
                result["signals"].append("沪深300空头排列，大盘趋势偏弱")
            else:
                result["signals"].append("沪深300震荡整理，方向不明")

        # 解析创业板指
        cyb = _parse_index(cyb_raw)
        if cyb:
            result["indices"].append(cyb)

        # 解析北向资金
        if isinstance(nb_raw, dict):
            rows = nb_raw.get("result", {}).get("data") or []
            nb_data = []
            for row in rows:
                amt = row.get("DEAL_AMT")
                date_str = (row.get("TRADE_DATE") or "")[:10]
                if amt is not None:
                    nb_data.append({"date": date_str, "dealAmt": round(amt / 100, 2)})  # 百万→亿
            if nb_data:
                amts = [d["dealAmt"] for d in nb_data]
                avg5 = round(sum(amts[:5]) / min(5, len(amts)), 2)
                avg20 = round(sum(amts[:20]) / min(20, len(amts)), 2)
                nb_trend = "活跃度上升" if avg5 > avg20 * 1.1 else "活跃度下降" if avg5 < avg20 * 0.9 else "活跃度平稳"
                result["northbound"] = {"latest": nb_data[0], "avg5d": avg5, "avg20d": avg20, "trend": nb_trend}
                if nb_trend == "活跃度上升":
                    result["signals"].append("北向资金成交活跃，市场情绪偏暖")
                elif nb_trend == "活跃度下降":
                    result["signals"].append("北向资金成交萎缩，市场情绪偏冷")

        # 解析融资融券
        if isinstance(margin_raw, dict):
            rows = margin_raw.get("result", {}).get("data") or []
            mg_data = []
            for row in rows:
                date_str = (row.get("DIM_DATE") or "")[:10]
                rzye = row.get("RZYE")
                rzjme = row.get("RZJME")
                if rzye is not None:
                    mg_data.append({
                        "date": date_str,
                        "rzye": round(rzye / 1e8, 2),  # 元→亿
                        "rzjme": round(rzjme / 1e8, 2) if rzjme is not None else None,
                    })
            if mg_data:
                latest_mg = mg_data[0]
                rzye_vals = [d["rzye"] for d in mg_data]
                avg5 = round(sum(rzye_vals[:5]) / min(5, len(rzye_vals)), 2)
                avg20 = round(sum(rzye_vals[:20]) / min(20, len(rzye_vals)), 2)
                if avg5 > avg20 * 1.03:
                    mg_trend = "融资余额上升，杠杆情绪升温"
                elif avg5 < avg20 * 0.97:
                    mg_trend = "融资余额下降，杠杆情绪降温"
                else:
                    mg_trend = "融资余额平稳"
                result["margin"] = {
                    "latest": latest_mg, "avg5d": avg5, "avg20d": avg20, "trend": mg_trend,
                }
                if "升温" in mg_trend:
                    result["signals"].append("融资余额上升，市场杠杆情绪升温")
                elif "降温" in mg_trend:
                    result["signals"].append("融资余额下降，市场杠杆情绪降温")

        # 解析十年国债ETF（价格上涨=收益率下行=利好成长股）
        if isinstance(bond_raw, dict):
            klines = bond_raw.get("data", {}).get("klines", [])
            if klines:
                bond_closes = []
                for line in klines:
                    parts = line.split(",")
                    if len(parts) >= 3:
                        try:
                            bond_closes.append({"date": parts[0], "close": float(parts[2])})
                        except ValueError:
                            continue
                if len(bond_closes) >= 20:
                    latest_bond = bond_closes[-1]
                    ma20_bond = sum(c["close"] for c in bond_closes[-20:]) / 20
                    dev = round((latest_bond["close"] - ma20_bond) / ma20_bond * 100, 2)
                    # ETF价格上涨 = 收益率下行
                    if dev > 0.3:
                        bond_trend = "国债价格走强（收益率下行），利好成长股"
                    elif dev < -0.3:
                        bond_trend = "国债价格走弱（收益率上行），利空成长股"
                    else:
                        bond_trend = "国债收益率平稳"
                    result["bond"] = {
                        "etfName": "十年国债ETF", "close": latest_bond["close"],
                        "date": latest_bond["date"],
                        "ma20": round(ma20_bond, 3), "devMa20": dev,
                        "trend": bond_trend,
                    }
                    if "利好" in bond_trend:
                        result["signals"].append("国债收益率下行，利率环境利好成长股估值")
                    elif "利空" in bond_trend:
                        result["signals"].append("国债收益率上行，利率环境压制成长股估值")

        return {"status_code": 0, "data": result}

    # ========== 基金申赎资金流趋势 ==========

    async def get_fund_flow_trend(self, fund_code: str) -> dict:
        """基于规模变动 + 机构持仓比例，分析申赎资金流趋势"""
        scale_raw, holder_raw = await asyncio.gather(
            self.get_scale_change(fund_code),
            self.get_holder_ratio(fund_code),
            return_exceptions=True,
        )

        # 解析规模变动
        quarters = []
        if isinstance(scale_raw, dict):
            gmbd = scale_raw.get("data", {}).get("gmbd", {})
            dates = sorted(gmbd.keys(), reverse=True)
            prev_nav = None
            def _safe_float(v, default=0.0):
                try:
                    return float(v) if v not in (None, "") else default
                except (ValueError, TypeError):
                    return default

            for date in dates:
                info = gmbd[date]
                jzc = _safe_float(info.get("jzc"))
                qjsg = _safe_float(info.get("qjsg"))
                qjsh = _safe_float(info.get("qjsh"))
                net_flow = round(qjsg - qjsh, 2)

                # 净申赎率：净申赎 / 上期净资产
                net_flow_rate = None
                if prev_nav and prev_nav > 0:
                    net_flow_rate = round(net_flow / prev_nav * 100, 2)
                prev_nav = jzc

                quarters.append({
                    "date": date,
                    "nav": jzc,
                    "subscribe": qjsg,
                    "redeem": qjsh,
                    "netFlow": net_flow,
                    "netFlowRate": net_flow_rate,
                })
            # 修正：dates是倒序的，prev_nav的赋值逻辑需要调整
            # 重新计算：净申赎率 = 净申赎 / 本期净资产（因为上期净资产在更早的日期）
            # 用相邻季度：当前季度的上一季度的净资产
            for i, q in enumerate(quarters):
                if i + 1 < len(quarters):
                    prev = quarters[i + 1]["nav"]
                    if prev > 0:
                        q["netFlowRate"] = round(q["netFlow"] / prev * 100, 2)
                    else:
                        q["netFlowRate"] = None
                else:
                    q["netFlowRate"] = None

        # 判断趋势
        trend = "数据不足"
        if len(quarters) >= 2:
            recent_flows = [q["netFlow"] for q in quarters[:4] if q["netFlow"] != 0]
            neg_count = sum(1 for f in recent_flows if f < 0)
            pos_count = sum(1 for f in recent_flows if f > 0)
            if neg_count >= 3:
                trend = "持续净赎回，资金在撤离"
            elif pos_count >= 3:
                trend = "持续净申购，资金在流入"
            elif neg_count >= 2 and pos_count >= 2:
                trend = "申赎交替，短线资金博弈"
            elif len(recent_flows) >= 1 and recent_flows[0] < 0:
                trend = "最近一季净赎回"
            elif len(recent_flows) >= 1 and recent_flows[0] > 0:
                trend = "最近一季净申购"

        # 解析机构占比
        org_trend = ""
        org_data = []
        if isinstance(holder_raw, dict):
            items = holder_raw.get("data", [])
            for item in items:
                date = item.get("date", "")
                org_rate = item.get("orgRate")
                if org_rate not in (None, ""):
                    try:
                        org_data.append({"date": date, "orgRate": round(float(org_rate), 2)})
                    except (ValueError, TypeError):
                        continue

        signals = []
        if org_data and len(org_data) >= 2:
            latest_org = org_data[0]["orgRate"]
            # 取较早的一期做对比（倒序排列，索引越大越早）
            earlier_idx = min(3, len(org_data) - 1)
            earlier_org = org_data[earlier_idx]["orgRate"]
            if earlier_org > 0 and latest_org < earlier_org * 0.5:
                org_trend = "机构占比大幅下降"
                signals.append(f"机构占比从{earlier_org}%降至{latest_org}%，聪明钱已撤退")
            elif earlier_org > 0 and latest_org > earlier_org * 1.5:
                org_trend = "机构占比大幅上升"
                signals.append(f"机构占比从{earlier_org}%升至{latest_org}%，机构在加仓")
            elif latest_org > earlier_org:
                org_trend = "机构占比小幅上升"
            elif latest_org < earlier_org:
                org_trend = "机构占比小幅下降"
            else:
                org_trend = "机构占比持平"

        # 添加资金流信号
        if "持续净赎回" in trend:
            signals.insert(0, "近期持续净赎回，资金在撤离")
        elif "持续净申购" in trend:
            signals.insert(0, "近期持续净申购，资金在流入")
        elif "交替" in trend:
            signals.insert(0, "近期申赎交替，无明确方向，短线资金博弈")

        return {
            "status_code": 0,
            "data": {
                "quarters": quarters[:8],
                "trend": trend,
                "orgRatioTrend": org_trend,
                "orgData": org_data[:6],
                "signals": signals,
            },
        }

    # ========== 指标与追踪 ==========

    async def get_rsi_indicator(self, fund_code: str) -> dict:
        """RSI 买卖指标"""
        return await self._get(
            f"{self.DQ_BASE_URL}/fuyao/fund/default/v1/fund/indic",
            params={"tradeCodeList": fund_code, "typeList": "rsiBestLimitDown,rsiBestLimitUp"},
        )

    async def get_fund_track(self, fund_code: str) -> dict:
        """基金追踪"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/fund_track/query/{fund_code}"
        )

    # 公告分类 catId 映射
    ANNOUNCEMENT_CATEGORIES = {
        "all": "0",
        "report": "003001",       # 业绩
        "dividend": "003004",     # 分红
        "change": "003007",       # 变更
        "operation": "003003,003002",  # 运作
        "other": "other",         # 其他
    }

    async def get_announcements(self, fund_code: str, category: str = "all",
                                page: int = 1, page_size: int = 15) -> dict:
        """基金公告
        category: all/report/dividend/change/operation/other
        """
        cat_id = self.ANNOUNCEMENT_CATEGORIES.get(category, "0")
        return await self._get(
            f"{self.BASE_URL}/interface/net/pubnote2/{cat_id}_{fund_code}_{page}_{page_size}"
        )

    async def get_news(self, fund_code: str, limit: int = 10) -> dict:
        """基金相关资讯"""
        # 需要先获取 hqcode
        info = await self.get_fund_info(fund_code)
        hqcode = info.get("data", {}).get("hqcode", "")
        if not hqcode:
            return {"status_code": -1, "data": {"contentList": []}, "status_msg": "未找到 hqcode"}
        return await self._get(
            f"{self.BASE_URL}/quotation/fund_content/v2/query",
            params={"code": hqcode, "marketId": "32", "limit": limit},
        )

    # ========== 基金排行与筛选（同花顺原生API） ==========

    # 默认查询字段列表
    _RANKING_FIELDS = [
        "unitNav", "chgpctDate", "chgpct", "week", "month", "tmonth",
        "hyear", "year", "twoyear", "tyear", "fyear", "nowyear", "now",
        "sharpeYear", "automaticYear", "maxDrawDownYear", "fundScale",
        "fundTags", "simpleName", "showType", "heavyRate", "rsi", "insPosition",
    ]

    # 默认过滤条件：规模>1000万、场外基金、可申购、不限大额赎回
    _DEFAULT_FILTERS = [
        {"filterField": "fundScale", "filterTypeList": [{"filterValue": "10000000", "filterSymbol": "GREATER"}], "isRankConfig": True},
        {"filterField": "otcFund", "innerJoinType": "OR", "filterTypeList": [{"filterValue": "1", "filterSymbol": "EQUAL"}]},
        {"filterField": "buyStatus", "innerJoinType": "OR", "filterTypeList": [{"filterValue": "1", "filterSymbol": "EQUAL"}]},
        {"filterField": "largeRedemptionNow", "innerJoinType": "OR", "filterTypeList": [{"filterValue": "0", "filterSymbol": "EQUAL"}]},
    ]

    # 本地策略筛选映射（基于已验证可用的 API 过滤字段）
    _STRATEGY_FILTERS = {
        "fund0001": {
            "name": "年年正收益",
            "desc": "连续5年正收益，成立超5年",
            "sort_type": "year",
            "sort": "DESC",
            "filters": [
                {"filterField": "yearPeriodicUpStreak", "filterTypeList": [{"filterValue": "5", "filterSymbol": "GREATER_EQUAL"}]},
                {"filterField": "nowDayAmount", "filterTypeList": [{"filterValue": "1825", "filterSymbol": "GREATER_EQUAL"}]},
            ],
        },
        "fund0002": {
            "name": "三年翻倍",
            "desc": "近3年涨幅超100%，成立超3年",
            "sort_type": "tyear",
            "sort": "DESC",
            "filters": [
                {"filterField": "tyear", "filterTypeList": [{"filterValue": "100", "filterSymbol": "GREATER_EQUAL"}]},
                {"filterField": "nowDayAmount", "filterTypeList": [{"filterValue": "1095", "filterSymbol": "GREATER_EQUAL"}]},
            ],
        },
        "fund0003": {
            "name": "十年十倍",
            "desc": "成立以来涨幅超1000%，成立超10年",
            "sort_type": "now",
            "sort": "DESC",
            "filters": [
                {"filterField": "now", "filterTypeList": [{"filterValue": "1000", "filterSymbol": "GREATER_EQUAL"}]},
                {"filterField": "nowDayAmount", "filterTypeList": [{"filterValue": "3650", "filterSymbol": "GREATER_EQUAL"}]},
            ],
        },
        "fund0004": {
            "name": "十年绩优",
            "desc": "成立超10年，近3年收益排名前1/3",
            "sort_type": "now",
            "sort": "DESC",
            "filters": [
                {"filterField": "nowDayAmount", "filterTypeList": [{"filterValue": "3650", "filterSymbol": "GREATER_EQUAL"}]},
                {"filterField": "rateRankPercentTyear", "filterTypeList": [{"filterValue": "33", "filterSymbol": "LESS_EQUAL"}]},
            ],
        },
        "fund0005": {
            "name": "低回撤率",
            "desc": "近3年最大回撤<5%，成立超3年",
            "sort_type": "maxDrawDownYear",
            "sort": "ASC",
            "filters": [
                {"filterField": "maxDrawDownTyear", "filterTypeList": [{"filterValue": "5", "filterSymbol": "LESS"}]},
                {"filterField": "nowDayAmount", "filterTypeList": [{"filterValue": "1095", "filterSymbol": "GREATER_EQUAL"}]},
            ],
        },
        "fund0007": {
            "name": "高性价比",
            "desc": "近3年夏普比率排名前10%，成立超3年",
            "sort_type": "sharpeYear",
            "sort": "DESC",
            "filters": [
                {"filterField": "sharpeRankPercentTyear", "filterTypeList": [{"filterValue": "10", "filterSymbol": "LESS_EQUAL"}]},
                {"filterField": "nowDayAmount", "filterTypeList": [{"filterValue": "1095", "filterSymbol": "GREATER_EQUAL"}]},
            ],
        },
        "fund0010": {
            "name": "能涨抗跌",
            "desc": "近3年收益前1/3，近3年回撤<5%，成立超3年",
            "sort_type": "tyear",
            "sort": "DESC",
            "filters": [
                {"filterField": "rateRankPercentTyear", "filterTypeList": [{"filterValue": "33", "filterSymbol": "LESS_EQUAL"}]},
                {"filterField": "maxDrawDownTyear", "filterTypeList": [{"filterValue": "5", "filterSymbol": "LESS"}]},
                {"filterField": "nowDayAmount", "filterTypeList": [{"filterValue": "1095", "filterSymbol": "GREATER_EQUAL"}]},
            ],
        },
        "fund0011": {
            "name": "机构偏爱",
            "desc": "机构持仓占比超80%",
            "sort_type": "year",
            "sort": "DESC",
            "filters": [
                {"filterField": "insPosition", "filterTypeList": [{"filterValue": "80", "filterSymbol": "GREATER_EQUAL"}]},
            ],
        },
        "fund0012": {
            "name": "小规模大潜力",
            "desc": "规模2-30亿，近1年夏普排名前10%",
            "sort_type": "sharpeYear",
            "sort": "DESC",
            "filters": [
                {"filterField": "fundScale", "filterTypeList": [{"filterValue": "200000000,3000000000", "filterSymbol": "BETWEEN"}]},
                {"filterField": "sharpeRankPercentYear", "filterTypeList": [{"filterValue": "10", "filterSymbol": "LESS_EQUAL"}]},
            ],
        },
    }

    async def get_fund_ranking(self, sort_type: str = "year", sort: str = "DESC",
                               limit: int = 30, offset: int = 0,
                               fund_type: str = None, fund_company: str = None,
                               min_scale: float = None,
                               strategy: str = None,
                               extra_filters: list = None) -> dict:
        """同花顺基金排行（原生API）
        sort_type: year/hyear/tmonth/month/week/nowyear/tyear/fyear/now/sharpeYear/maxDrawDownYear 等
        sort: DESC/ASC
        fund_type: 基金类型代码（如 282001001=股票型）
        fund_company: 基金公司 orgid
        min_scale: 最小规模（元），默认1000万
        strategy: 预设策略key（fund0001=年年正收益 等），会覆盖 sort_type/sort 并追加策略过滤条件
        extra_filters: 自定义 filterList，直接追加
        """
        # 策略筛选：查找本地映射，覆盖排序并追加过滤条件
        if strategy and strategy in self._STRATEGY_FILTERS:
            cfg = self._STRATEGY_FILTERS[strategy]
            sort_type = cfg["sort_type"]
            sort = cfg["sort"]
            extra_filters = list(extra_filters or []) + cfg["filters"]

        filter_list = list(self._DEFAULT_FILTERS)

        # 自定义最小规模
        if min_scale is not None:
            filter_list = [f for f in filter_list if f.get("filterField") != "fundScale"]
            filter_list.append({
                "filterField": "fundScale",
                "filterTypeList": [{"filterValue": str(int(min_scale)), "filterSymbol": "GREATER"}],
                "isRankConfig": True,
            })

        # 基金类型过滤
        if fund_type:
            filter_list.append({
                "filterField": "fundType",
                "innerJoinType": "OR",
                "filterTypeList": [{"filterValue": fund_type, "filterSymbol": "EQUAL"}],
            })

        # 基金公司过滤
        if fund_company:
            filter_list.append({
                "filterField": "orgid",
                "innerJoinType": "OR",
                "filterTypeList": [{"filterValue": fund_company, "filterSymbol": "EQUAL"}],
            })

        # 追加自定义过滤条件
        if extra_filters:
            filter_list.extend(extra_filters)

        ext = {
            "total": 0,
            "page": 0,
            "offset": offset,
            "limit": limit,
            "sort": sort,
            "sortType": sort_type,
            "outerJoinType": "AND",
            "filterList": filter_list,
            "fieldList": self._RANKING_FIELDS,
        }

        body = {
            "cardList": [{
                "cardModuleTypeEnum": "FUND",
                "cardEnum": "SORT_FILTER_V1",
                "ext": ext,
            }],
        }

        return await self._post_with_retry(
            f"{self.BASE_URL}/quotation/common/v1/list/card/info",
            json=body,
        )

    async def get_rank_board_config(self) -> dict:
        """获取排行榜配置（涨幅榜/反弹榜/人气榜/加仓榜/超额榜）"""
        import json
        resp = await self._get(
            f"{self.BASE_URL}/marketing/activity_redis/v1/get/fund_rank_list_v1"
        )
        # data 可能是 JSON 字符串，需要二次解析
        data = resp.get("data")
        if isinstance(data, str):
            resp["data"] = json.loads(data)
        return resp

    async def get_rank_filter_config(self) -> dict:
        """获取筛选策略配置（年年正收益/三年翻倍/机构偏爱/十年十倍等）"""
        import json
        resp = await self._get(
            f"{self.BASE_URL}/marketing/activity_redis/v1/get/fund_rank_filter_v1"
        )
        data = resp.get("data")
        if isinstance(data, str):
            resp["data"] = json.loads(data)
        return resp

    async def get_rank_distribution(self, indic_list: list = None) -> dict:
        """获取收益率分布统计（各周期的收益率分布：max/min/每个百分点的基金数量）"""
        if indic_list is None:
            indic_list = [
                "month", "tmonth", "hyear", "year", "tyear", "fyear",
                "nowyear", "maxDrawDownYear",
            ]
        return await self._post(
            f"{self.BASE_URL}/quotation/rank/filter/v1/count/info",
            json={"indicList": indic_list},
        )

    async def get_fund_company_list(self) -> list:
        """获取基金公司列表（使用独立请求避免 session 限流）"""
        async with httpx.AsyncClient(timeout=10.0) as tmp_client:
            resp = await tmp_client.get(
                f"{self.BASE_URL}/mInterface/jjgs.txt",
                headers={
                    "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
                    "Referer": "https://fund.10jqka.com.cn/",
                },
            )
            resp.raise_for_status()
            return resp.json()

    # ========== 交易账户数据（trade.5ifund.com） ==========

    TRADE_BASE_URL = "https://trade.5ifund.com"
    # 本地代理（通过 App 内的 OkHttpClient 转发 /rz/ 请求，解决 FundGW 认证问题）
    TRADE_PROXY_URL = "http://127.0.0.1:18900/proxy"
    # Hook 代理根地址（用于拉取最新 auth 参数）
    HOOK_PROXY_URL = "http://127.0.0.1:18900"

    # 交易认证参数（从 Hook 拦截获取，token 有过期时间）
    TRADE_AUTH = {
        "key1": "7246091a5f126b63",
        "key2": "2293a78f6581c12bbb334759458d4de3",
        "key3": "100113970166",  # custId
        "key4": "auth",
        "key5": "eyJjSWQiOiIxMDAxMTM5NzAxNjYiLCJleHAiOjE3NzUxODgyOTU5NzAsInNvdSI6IlNESyIsInR5cGUiOiJQV0QiLCJ2IjoiMS4wIn0=.MlJheGZFeXpTV2RMbVA2K2daNDZtcVloeVAxNmx1dFZ0dm1XSWVmRytEVUlpSldyRVdkblZMblVjbzl4RUVrcUc3WGpFQ1NzelpVZDFhSGIzUmxyQnc1UXdTcDBjYWEydVlMczFXdnV2WVZsWWdSUFk1aHJSTENkQ3JTNGtFYWs1cUw1dmI5Tk5xbjBTQU11SzBHcVJUR0RrSElwemRONjZjemdTdFNSdmdvPQ==",
        "userId": "690359103",
        "sessionId": "1a2de9ee7f75e42eb59cce41d4adafca0",
    }

    # 交易密码（MD5 哈希），通过 update_trade_password() 或 /api/trade/password 设置
    TRADE_PASSWORD_MD5 = ""

    TRADE_COOKIE = (
        "user_status=0; "
        "user=MDpteF82OTAzNTkxMDM6Ok5vbmU6NTAwOjcwMDM1OTEwMzo3LDExMTExMTExMTExLDQwOzQ0LDExLDQwOzYsMSw0MDs1LDEsNDA7MSwxMDEsNDA7MiwxLDQwOzMsMSw0MDs1LDEsNDA7OCwwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMSw0MDsxMDIsMSw0MDoxNjo6OjY5MDM1OTEwMzoxNzcyMzc0MjgxOjo6MTY5NDEzNDgwMDoyNjc4NDAwOjA6MTgzMWNjZTFhYTA1NDMzZjNjOTU2NmJjZTI1NWE5ZTZkOjow; "
        "userid=690359103; "
        "u_name=mx_690359103; "
        "sess_tk=eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzI1NiIsImtpZCI6InNlc3NfdGtfMSIsImJ0eSI6InNlc3NfdGsifQ"
        ".eyJqdGkiOiI2ZDllNWEyNWNlNmI1NmM5ZjMzMzU0YTAxYWNlMWM4MzEiLCJpYXQiOjE3NzI0Mzk1MTcsImV4cCI6MTc3MzkyODgwMCwic3ViIjoiNjkwMzU5MTAzIiwiaXNzIjoidXBhc3MuMTBqcWthLmNvbS5jbiIsImF1ZCI6IjIwMjMwODA0OTA3NTEyOTIiLCJhY3QiOiJvZmMiLCJjdWhzIjoiODgyZWUyNWI2OWQzNzJlOTY3MzEyZTQ0MWY1MmE1NDYzOTBiZDIxYzBmYzIzYzQyYWE4YjMzMmJkMGRjNWJlMyJ9"
        ".Z2vnV8SE6vcnRKCQZXZg9_iUOWSu5tgaD9J43mi3gWO5pziwMOLZNxfiKsSTQMQN6Il2K-qUMjoi8vshkeWgqA"
    )

    TRADE_HEADERS = {
        "User-Agent": "Hexin_Gphone/11.47.03 (Royal Flush) innerversion/G037.08.194.1.32 hxtheme/0 GphoneIjiJinSDK/V7.39.01 ifOperator/145",
        "Client-Referer": "",
    }

    def _trade_auth_params(self) -> dict:
        """构建交易 API 的 URL 认证参数"""
        return {
            "key1": self.TRADE_AUTH["key1"],
            "key2": self.TRADE_AUTH["key2"],
            "key5": self.TRADE_AUTH["key5"],
            "key3": self.TRADE_AUTH["key3"],
            "key4": self.TRADE_AUTH["key4"],
        }

    def _trade_cookie_header(self) -> dict:
        """构建交易 API 的 Cookie 头"""
        return {**self.TRADE_HEADERS, "cookie": self.TRADE_COOKIE}

    async def _trade_get(self, path: str, extra_params: dict = None) -> dict:
        """交易 API GET 请求（key1-key5 认证），LT99 自动刷新 token 重试"""
        result = await self._trade_get_raw(path, extra_params)
        if isinstance(result, dict) and result.get("code") in ("LT99",):
            if await self.refresh_auth_from_hook():
                result = await self._trade_get_raw(path, extra_params)
        return result

    async def _trade_get_raw(self, path: str, extra_params: dict = None) -> dict:
        """交易 API GET 请求（原始版本，不含重试）"""
        params = self._trade_auth_params()
        if extra_params:
            params.update(extra_params)
        resp = await self._client.get(
            f"{self.TRADE_BASE_URL}{path}",
            params=params,
            headers=self._trade_cookie_header(),
        )
        resp.raise_for_status()
        return resp.json()

    async def _trade_post_form(self, path: str, data: dict, extra_headers: dict = None) -> dict:
        """交易 API POST 请求（form-encoded body），LT99 自动刷新 token 重试"""
        result = await self._trade_post_form_raw(path, data, extra_headers)
        if isinstance(result, dict) and result.get("code") in ("LT99",):
            if await self.refresh_auth_from_hook():
                result = await self._trade_post_form_raw(path, data, extra_headers)
        return result

    async def _trade_post_form_raw(self, path: str, data: dict, extra_headers: dict = None) -> dict:
        """交易 API POST 请求 form-encoded（原始版本，不含重试）"""
        headers = self._trade_cookie_header()
        if extra_headers:
            headers.update(extra_headers)
        resp = await self._client.post(
            f"{self.TRADE_BASE_URL}{path}",
            data=data,
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()

    async def _trade_post_json(self, path: str, json_data: dict, referer: str = None) -> dict:
        """交易 API POST 请求（JSON body），LT99 自动刷新 token 重试"""
        result = await self._trade_post_json_raw(path, json_data, referer)
        if isinstance(result, dict) and result.get("code") in ("LT99",):
            if await self.refresh_auth_from_hook():
                result = await self._trade_post_json_raw(path, json_data, referer)
        return result

    async def _trade_post_json_raw(self, path: str, json_data: dict, referer: str = None) -> dict:
        """交易 API POST 请求 JSON（原始版本，不含重试）"""
        headers = self._trade_cookie_header()
        if referer:
            headers["referer"] = referer
        resp = await self._client.post(
            f"{self.TRADE_BASE_URL}{path}",
            json=json_data,
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()

    async def _call_jsbridge(self, data: dict, timeout: int = 35) -> dict:
        """通过 JSBridge 调用 API（用于基金交易等必须在 WebView 环境中的操作）

        Args:
            data: JSBridge请求数据，通常包含:
                - method: HTTP方法 (GET/POST)
                - url: 目标URL
                - params: 请求参数
                - needToken: 是否需要自动注入token
                - requestType: 请求类型 (如 "guomiSSL")
                - Header: 额外的请求头
            timeout: 超时时间（秒）

        Returns:
            API 响应数据
        """
        request_data = {
            "handler": "clientRequestHX",
            "data": data
        }

        try:
            response = await self._client.post(
                f"{self.HOOK_PROXY_URL}/jsbridge",
                json=request_data,
                timeout=timeout
            )
            response.raise_for_status()

            # 解析响应
            result = response.json()
            if isinstance(result, str):
                import json
                result = json.loads(result)

            if not result.get("success"):
                raise Exception(f"JSBridge调用失败: {result.get('error')}")

            return result.get("data", {})

        except Exception as e:
            raise Exception(f"JSBridge请求失败: {e}")

    async def _proxy_request(self, path: str, method: str = "POST",
                             body: str = None, content_type: str = None,
                             extra_headers: dict = None) -> dict:
        """通过 App 内代理转发请求（用于 /rz/ 路径的 API，需要 App 级认证）"""
        # 构建认证headers（key1-key5, userId, sessionId）
        auth_headers = {
            "key1": self.TRADE_AUTH["key1"],
            "key2": self.TRADE_AUTH["key2"],
            "key3": self.TRADE_AUTH["key3"],
            "key4": self.TRADE_AUTH["key4"],
            "key5": self.TRADE_AUTH["key5"],
            "userId": self.TRADE_AUTH["userId"],
            "sessionId": self.TRADE_AUTH["sessionId"],
        }
        # 合并用户自定义headers
        if extra_headers:
            auth_headers.update(extra_headers)

        payload = {
            "url": f"{self.TRADE_BASE_URL}{path}",
            "method": method,
            "extra_headers": auth_headers,  # 总是传递认证headers
        }
        if body is not None:
            payload["body"] = body
        if content_type:
            payload["content_type"] = content_type
        resp = await self._client.post(
            self.TRADE_PROXY_URL,
            json=payload,
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()

    def update_trade_auth(self, key1: str = None, key2: str = None, key3: str = None,
                          key5: str = None, user_id: str = None, session_id: str = None,
                          cookie: str = None):
        """更新交易认证参数（token 过期后需要重新从 Hook 捕获）"""
        if key1:
            self.TRADE_AUTH["key1"] = key1
        if key2:
            self.TRADE_AUTH["key2"] = key2
        if key3:
            self.TRADE_AUTH["key3"] = key3
        if key5:
            self.TRADE_AUTH["key5"] = key5
        if user_id:
            self.TRADE_AUTH["userId"] = user_id
        if session_id:
            self.TRADE_AUTH["sessionId"] = session_id
        if cookie:
            self.TRADE_COOKIE = cookie

    async def refresh_auth_from_hook(self) -> bool:
        """从 Hook 代理拉取最新的交易 token"""
        try:
            resp = await self._client.get(f"{self.HOOK_PROXY_URL}/auth", timeout=3.0)
            data = resp.json()
            if data.get("available") and data.get("key5"):
                # 更新所有认证参数
                if data.get("key1"):
                    self.TRADE_AUTH["key1"] = data["key1"]
                if data.get("key2"):
                    self.TRADE_AUTH["key2"] = data["key2"]
                if data.get("key3"):
                    self.TRADE_AUTH["key3"] = data["key3"]
                if data.get("key4"):
                    self.TRADE_AUTH["key4"] = data["key4"]
                self.TRADE_AUTH["key5"] = data["key5"]
                if data.get("userId"):
                    self.TRADE_AUTH["userId"] = data["userId"]
                if data.get("sessionId"):
                    self.TRADE_AUTH["sessionId"] = data["sessionId"]
                if data.get("cookie"):
                    self.TRADE_COOKIE = data["cookie"]
                return True
        except Exception:
            pass
        return False

    def update_trade_password(self, password_md5: str):
        """设置交易密码（MD5 哈希值）"""
        self.TRADE_PASSWORD_MD5 = password_md5.upper()

    @property
    def trade_cust_id(self) -> str:
        return self.TRADE_AUTH["key3"]

    async def get_buy_limits(self, fund_code: str) -> dict:
        """获取基金购买限制（最低/最高购买额、是否可购买等）

        fund_code: 基金代码
        返回: {
            "fund_code": "012922",
            "fund_name": "易方达全球成长精选混合C",
            "can_buy": True,
            "min_buy": 1.0,
            "max_buy": 50.0,  # None 表示无限制
            "confirm_date": "2026-03-09",
            "buy_status": "1",  # 1=可买 0=暂停
        }
        """
        init_resp = await self._proxy_request(
            "/rz/trade/dubbo/subscribe/init",
            body=f"fundCode={fund_code}",
            content_type="application/x-www-form-urlencoded",
        )
        init_data = init_resp.get("data", init_resp)
        fund_info = init_data.get("paramOpenFundAccBean", {})

        min_buy = float(init_data.get("minBuy", "1") or "1")
        max_buy_str = init_data.get("maxBuy")
        max_buy = float(max_buy_str) if max_buy_str else None
        buy_status = init_data.get("buyStatus", "1")

        return {
            "fund_code": fund_code,
            "fund_name": fund_info.get("fundName", ""),
            "can_buy": buy_status == "1",
            "min_buy": min_buy,
            "max_buy": max_buy,
            "confirm_date": fund_info.get("confirmDay", ""),
            "buy_status": buy_status,
        }

    async def buy_fund(self, fund_code: str, amount: float, use_wallet: bool = True,
                       password_md5: str = None) -> dict:
        """买入基金（完整6步流程 - 使用JSBridge）

        fund_code: 基金代码
        amount: 买入金额（元）
        use_wallet: 是否使用活期宝支付（默认True）
        password_md5: 交易密码MD5（优先级高于 TRADE_PASSWORD_MD5）
        返回: 订单信息（含 appSheetSerialNo）

        注意: 如果 amount 超过单日限购(maxBuy)，会自动调整为限购金额并在返回中标记
        """
        import json as json_module
        pwd = password_md5 or self.TRADE_PASSWORD_MD5
        if not pwd:
            raise ValueError("未设置交易密码，请通过 --password 参数传入或先调用 set_password")

        # Step 0: 刷新交易认证（Cookie 和 key5 可能已过期）
        await self.refresh_auth_from_hook()

        money = f"{amount:.2f}"
        cust_id = self.trade_cust_id

        # Step 1: 买入初始化 — 获取费率、基金信息、transactionAccountId
        init_data = {
            "method": "POST",
            "url": "https://trade.5ifund.com/rz/trade/dubbo/subscribe/init",
            "params": {"fundCode": fund_code},
            "Header": {
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/x-www-form-urlencoded",
                "custId": cust_id,
                "source": "SDK"
            },
            "needToken": True,
            "K5type": "none",
            "requestType": "guomiSSL"
        }
        init_resp = await self._call_jsbridge(init_data)

        # 提取transActionAccountId
        bank_cards = init_resp.get("result", {}).get("data", {}).get("bankCardSplitListResult", [])
        if not bank_cards:
            raise ValueError(f"买入初始化失败，未获取到银行卡信息: {init_resp}")

        transaction_account_id = bank_cards[0].get("transActionAccountId", "")
        if not transaction_account_id:
            raise ValueError(f"未获取到transActionAccountId")

        fund_info = init_resp.get("result", {}).get("data", {}).get("paramOpenFundAccBean", {})
        fund_name = fund_info.get("fundName", "")
        fund_risk_level = str(init_resp.get("result", {}).get("data", {}).get("fundRiskLevel", "4"))

        # 检查购买限制
        init_data_obj = init_resp.get("result", {}).get("data", {})
        min_buy = float(init_data_obj.get("minBuy", "1") or "1")
        max_buy_str = init_data_obj.get("maxBuy")
        max_buy = float(max_buy_str) if max_buy_str else None
        original_amount = amount
        amount_adjusted = False

        if amount < min_buy:
            raise ValueError(f"买入金额 {amount} 低于最低起购额 {min_buy}")

        if max_buy and amount > max_buy:
            amount = max_buy
            money = f"{amount:.2f}"
            amount_adjusted = True

        # Step 2: 账户状态检查
        acct_data = {
            "method": "POST",
            "url": "https://trade.5ifund.com/rz/account/dubbo/accountInfo/getCustAccoStatus",
            "params": {"version": "VOCATIONCODE_22"},
            "Header": {
                "Content-Type": "application/x-www-form-urlencoded",
                "custId": cust_id
            },
            "needToken": True,
            "K5type": "none",
            "requestType": "guomiSSL"
        }
        acct_resp = await self._call_jsbridge(acct_data)
        user_risk_level = str(acct_resp.get("result", {}).get("data", {}).get("riskLevel", "4"))

        # Step 3: 交易前签约检查
        check_data = {
            "method": "POST",
            "url": "https://trade.5ifund.com/rz/trade/dubbo/sign_contract/v1/check_before_trade",
            "params": {
                "fundCode": fund_code,
                "applicationAmount": money,
                "transactionAccountId": transaction_account_id
            },
            "Header": {
                "Content-Type": "application/x-www-form-urlencoded",
                "custId": cust_id
            },
            "needToken": True,
            "K5type": "none",
            "requestType": "guomiSSL"
        }
        await self._call_jsbridge(check_data)

        # Step 4: 获取短信验证状态
        auth = self.TRADE_AUTH
        sms_dto = json_module.dumps({
            "money": money,
            "fundCode": fund_code,
            "fundRiskLevel": fund_risk_level,
            "payType": "0",
            "userRiskLevel": user_risk_level,
        })

        getsms_url = f"https://trade.5ifund.com/rs/trade/shen/getsms/{cust_id}"
        getsms_params = {
            "key1": auth["key1"],
            "key2": auth["key2"],
            "key5": auth["key5"],
            "rsBuySmsDTO": sms_dto,
            "key3": auth["key3"],
            "key4": "auth",
        }
        getsms_jsdata = {
            "method": "POST",
            "url": getsms_url,
            "params": getsms_params,
            "K5type": "normal"
        }
        await self._call_jsbridge(getsms_jsdata)

        # Step 5: 校验短信并获取tradeInfoSeq
        checksms_dto = json_module.dumps({
            "money": money,
            "fundCode": fund_code,
            "fundRiskLevel": fund_risk_level,
            "payType": "0",
            "userRiskLevel": user_risk_level,
            "isCheckSmsCode": 0,
            "custId": cust_id,
        })

        checksms_url = f"https://trade.5ifund.com/rs/trade/shen/checksms/{cust_id}"
        checksms_params = {
            "key1": auth["key1"],
            "key2": auth["key2"],
            "key5": auth["key5"],
            "rsBuySmsDTO": checksms_dto,
            "key3": auth["key3"],
            "key4": "auth",
            "smsRandom": "",
        }
        checksms_jsdata = {
            "method": "POST",
            "url": checksms_url,
            "params": checksms_params,
            "K5type": "normal"
        }
        checksms_resp = await self._call_jsbridge(checksms_jsdata)

        # 提取tradeInfoSeq（多路径尝试）
        trade_info_seq = (checksms_resp.get("result", {}).get("singleData", {}).get("tradeInfoSeq") or
                         checksms_resp.get("singleData", {}).get("tradeInfoSeq") or
                         checksms_resp.get("tradeInfoSeq", ""))
        if not trade_info_seq:
            raise ValueError(f"未获取到tradeInfoSeq: {checksms_resp}")

        # Step 6: 提交买入订单
        agreement_str = json_module.dumps([{
            "protocalCode": "JJ_WDMCXY",
            "protocalVersion": "20230426",
        }])

        buy_data = {
            "method": "POST",
            "url": "https://trade.5ifund.com/rz/trade/dubbo/buy",
            "params": {
                "signFlag": "1",
                "useWallet": "1" if use_wallet else "0",
                "money": money,
                "tradeInfoSeq": trade_info_seq,
                "fundCode": fund_code,
                "tradePassword": pwd,
                "buyType": "1",
                "transactionAccountId": transaction_account_id,
                "operator": "145",
                "agreementStr": agreement_str
            },
            "needToken": True,
            "K5type": "none",
            "Header": {
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/x-www-form-urlencoded"
            },
            "requestType": "guomiSSL"
        }
        buy_resp = await self._call_jsbridge(buy_data, timeout=40)

        # 检查响应
        result_obj = buy_resp.get("result", {})
        error_code = result_obj.get("code", "")
        error_msg = result_obj.get("message", "")

        if error_code != "0000" and error_code != "":
            raise Exception(f"买入失败 [{error_code}]: {error_msg}")

        # 提取结果
        buy_data_result = result_obj.get("data", {})
        if not buy_data_result:
            raise Exception(f"买入响应无数据: {buy_resp}")

        app_sheet_serial_no = buy_data_result.get("appSheetSerialNo", "")

        result = {
            "fund_code": fund_code,
            "fund_name": fund_name,
            "amount": money,
            "app_sheet_serial_no": app_sheet_serial_no,
            "accept_time": buy_data_result.get("acceptTime", ""),
            "confirm_date": buy_data_result.get("confirmDate", "") or fund_info.get("confirmDay", ""),
            "charge": buy_data_result.get("charge", "0.00"),
            "min_buy": min_buy,
            "max_buy": max_buy,
            "raw_response": buy_resp,
        }

        # 如果金额被调整，记录原始金额和调整原因
        if amount_adjusted:
            result["amount_adjusted"] = True
            result["original_amount"] = f"{original_amount:.2f}"
            result["adjust_reason"] = f"超过单日限购 {max_buy:.2f} 元，已自动调整"

        return result

    async def buy_fund_via_webview(self, fund_code: str, amount: float,
                                   password_md5: str = None) -> dict:
        """通过 WebView 触发购买（利用 app 自身能力）

        fund_code: 基金代码
        amount: 买入金额（元）
        password_md5: 交易密码MD5（可选，如果提供会自动填充）
        返回: 触发结果
        """
        pwd = password_md5 or self.TRADE_PASSWORD_MD5

        payload = {
            "fundCode": fund_code,
            "amount": str(amount),
        }
        if pwd:
            payload["password"] = pwd

        resp = await self._client.post(
            f"{self.HOOK_PROXY_URL}/fund/buy",
            json=payload,
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()

    async def buy_fund_direct(self, fund_code: str, amount: float,
                             password_md5: str = None) -> dict:
        """直接调用 app 内部购买方法（不通过 UI 自动化）

        fund_code: 基金代码
        amount: 购买金额
        password_md5: 交易密码MD5
        返回: 购买结果
        """
        pwd = password_md5 or self.TRADE_PASSWORD_MD5

        payload = {
            "fundCode": fund_code,
            "amount": str(amount),
        }
        if pwd:
            payload["password"] = pwd

        resp = await self._client.post(
            f"{self.HOOK_PROXY_URL}/fund/buy_direct",
            json=payload,
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()

    async def open_fund_detail(self, fund_code: str) -> dict:
        """打开基金详情页面（触发WebView创建和JSBridge初始化）

        fund_code: 基金代码
        返回: 操作结果
        """
        payload = {"fundCode": fund_code}

        resp = await self._client.post(
            f"{self.HOOK_PROXY_URL}/fund/open_detail",
            json=payload,
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()

    async def sell_fund(self, fund_code: str, share_vol: str = None,
                        sell_all: bool = False, password_md5: str = None) -> dict:
        """赎回基金（2步流程 - 使用JSBridge）：render初始化 → 提交赎回

        fund_code: 基金代码
        share_vol: 赎回份额（字符串，如 "100.00"）
        sell_all: 是否全部赎回（使用全部可用份额）
        password_md5: 交易密码MD5（优先级高于 TRADE_PASSWORD_MD5）
        返回: 赎回结果
        """
        pwd = password_md5 or self.TRADE_PASSWORD_MD5
        if not pwd:
            raise ValueError("未设置交易密码，请通过 --password 参数传入或先调用 set_password")

        # Step 0: 刷新交易认证
        await self.refresh_auth_from_hook()

        cust_id = self.trade_cust_id

        # Step 1: 从持仓列表中找到目标基金
        pos_resp = await self.get_fund_positions()
        pos_data = pos_resp.get("singleData", {}).get("fundGeneral", {}).get("fundPositonCombinedList", [])
        target = None
        for p in pos_data:
            if p.get("fundCode") == fund_code:
                target = p
                break
        if not target:
            raise ValueError(f"未在持仓中找到基金 {fund_code}")

        available_vol = target.get("availableVol") or target.get("availableShare") or "0"
        available_vol_str = str(available_vol)
        try:
            available_vol_float = float(available_vol_str)
        except (ValueError, TypeError):
            available_vol_float = 0.0
        if available_vol_float <= 0:
            raise ValueError(f"基金 {fund_code} 可用份额为 {available_vol_str}，不足以赎回")

        # 持仓数据中的字段名是 transAccIdList，不是 transactionAccountId
        transaction_account_id = (target.get("transAccIdList") or
                                   target.get("transactionAccountId") or
                                   target.get("transActionAccountId") or "")
        fund_name = target.get("fundName") or ""
        share_type = target.get("shareType") or "0"

        # Step 2: 调用 render API 初始化赎回（使用JSBridge）
        render_data = {
            "method": "POST",
            "url": "https://trade.5ifund.com/rz/trade/dubbo/redemption/v1/render",
            "params": {
                "transactionAccountId": transaction_account_id,
                "fundCode": fund_code
            },
            "Header": {
                "Content-Type": "application/x-www-form-urlencoded",
                "custId": cust_id
            },
            "needToken": True,
            "K5type": "none",
            "requestType": "guomiSSL"
        }
        render_resp = await self._call_jsbridge(render_data)

        render_result = render_resp.get("result", {})
        render_code = render_result.get("code", "")
        if render_code not in ("0000", ""):
            raise ValueError(f"赎回初始化失败: {render_result.get('message', render_code)}")

        render_data_obj = render_result.get("data", {})
        # 从 render 中补充信息
        if not transaction_account_id:
            transaction_account_id = render_data_obj.get("transactionAccountId") or ""
        fund_info = render_data_obj.get("fundInfo", {})
        if not fund_name:
            fund_name = fund_info.get("fundName") or render_data_obj.get("fundName") or ""
        if not share_type:
            share_type = fund_info.get("shareType") or "0"

        # 获取 defenderToken（必需）
        defender_token = render_data_obj.get("defenderToken", {})
        dt = defender_token.get("dt", "")
        if not dt:
            raise ValueError("未获取到 defenderToken，无法提交赎回")

        # 获取钱包信息（用于超级转换到货币基金）
        wallet_info = render_data_obj.get("walletInfo", {})
        can_redeem_to_wallet = fund_info.get("canRedeemToWallet", "0")

        # Step 3: 确定赎回份额
        if sell_all:
            share_vol = f"{available_vol_float:.2f}"
        elif share_vol:
            try:
                vol = float(share_vol)
            except (ValueError, TypeError):
                raise ValueError(f"无效的赎回份额: {share_vol}")
            if vol > available_vol_float:
                raise ValueError(f"赎回份额 {vol:.2f} 超过可用份额 {available_vol_float:.2f}")
            share_vol = f"{vol:.2f}"
        else:
            raise ValueError("请指定赎回份额 (--shares) 或使用全部赎回 (--all)")

        # Step 4: 构建赎回参数
        redeem_params = {
            "fundCode": fund_code,
            "fundName": fund_name,
            "shareType": share_type,
            "shareVol": share_vol,
            "transActionAccountId": transaction_account_id,
            "tradePassword": pwd,
            "operator": "145",
            "largeRedemptionFlag": "0",
        }

        # 判断是否使用超级转换（赎回到货币基金）
        if can_redeem_to_wallet == "1" and wallet_info:
            # 超级转换模式：赎回到货币基金（更快到账）
            redeem_params["redemptionType"] = "1"
            redeem_params["targetFundCode"] = wallet_info.get("fundCode", "")
            redeem_params["targetFundName"] = wallet_info.get("fundName", "")
            redeem_params["targetTaCode"] = wallet_info.get("taCode", "")
            redeem_params["targetShareType"] = wallet_info.get("shareType", "0")
            redeem_params["targetFundType"] = wallet_info.get("fundType", "0")
        else:
            # 普通赎回模式
            redeem_params["redemptionType"] = "0"

        # Step 5: 提交赎回（使用JSBridge，带 dt header）
        redeem_jsdata = {
            "method": "POST",
            "url": "https://trade.5ifund.com/rz/trade/dubbo/redemption/v2/redeem",
            "params": redeem_params,
            "Header": {
                "Content-Type": "application/x-www-form-urlencoded",
                "custId": cust_id,
                "dt": dt
            },
            "needToken": True,
            "K5type": "none",
            "requestType": "guomiSSL"
        }
        redeem_full_resp = await self._call_jsbridge(redeem_jsdata, timeout=40)

        redeem_result = redeem_full_resp.get("result", {})
        redeem_code = redeem_result.get("code", "")
        if redeem_code != "0000" and redeem_code != "":
            raise ValueError(f"赎回失败: {redeem_result.get('message', redeem_code)}")

        redeem_data = redeem_result.get("data", {})
        return {
            "fund_code": fund_code,
            "fund_name": fund_name,
            "share_vol": share_vol,
            "available_vol": available_vol_str,
            "redemption_type": "超级转换" if redeem_params["redemptionType"] == "1" else "普通赎回",
            "app_sheet_serial_no": redeem_data.get("appSheetSerialNo", ""),
            "render_data": render_data_obj,
            "redeem_result": redeem_full_resp,
        }

    async def get_order_detail(self, app_sheet_serial_no: str) -> dict:
        """查询交易订单详情"""
        return await self._proxy_request(
            f"/rz/positionqryweb/dubbo/order/v2/detail?appSheetSerialNo={app_sheet_serial_no}",
            method="GET",
        )

    async def get_order_list(self, start_date: str = None, end_date: str = None,
                             op_type: str = "all", limit: int = 20, offset: int = 1) -> dict:
        """查询交易订单列表

        start_date: 开始日期 YYYYMMDD，默认近30天
        end_date: 结束日期 YYYYMMDD，默认今天
        op_type: 操作类型 all=全部
        limit: 每页条数
        offset: 页码（从1开始）
        """
        now = datetime.now()
        if not end_date:
            end_date = now.strftime("%Y%m%d")
        if not start_date:
            start_date = (now - timedelta(days=30)).strftime("%Y%m%d")
        return await self._trade_get(
            f"/rs/query/v1/currentHistoryInfo/{self.trade_cust_id}",
            extra_params={
                "opType": op_type,
                "productType": "all",
                "startDate": start_date,
                "endDate": end_date,
                "limit": str(limit),
                "offset": str(offset),
            },
        )

    async def cancel_order(self, app_sheet_serial_no: str, password_md5: str = None) -> dict:
        """撤销交易订单

        app_sheet_serial_no: 订单号
        password_md5: 交易密码MD5（优先级高于 TRADE_PASSWORD_MD5）
        """
        pwd = password_md5 or self.TRADE_PASSWORD_MD5
        if not pwd:
            raise ValueError("未设置交易密码，请通过 --password 参数传入或先调用 set_password")

        # 先从订单列表确认该订单是否可撤（endFlag=="0" && processStatus=="1"）
        list_resp = await self.get_order_list(limit=50)
        list_data = list_resp.get("singleData", list_resp).get("data", [])
        order_in_list = None
        for o in list_data:
            if o.get("appSheetSerialNo") == app_sheet_serial_no:
                order_in_list = o
                break
        if order_in_list:
            end_flag = order_in_list.get("endFlag", "")
            process_status = order_in_list.get("processStatus", "")
            if end_flag != "0" or process_status != "1":
                raise ValueError(f"该订单不可撤销（endFlag={end_flag}, processStatus={process_status}）")

        # 获取订单详情，拿到撤单所需的 realFundCode、transactionAccountId
        detail_resp = await self.get_order_detail(app_sheet_serial_no)
        detail_data = detail_resp.get("data", detail_resp)

        real_fund_code = detail_data.get("realFundCode", detail_data.get("fundCode", ""))
        transaction_account_id = detail_data.get("transactionAccountId", "")
        fund_name = detail_data.get("fundName", "")
        amount = detail_data.get("applicationAmount", "")

        # 执行撤单
        body_parts = [
            "revokeType=2",
            f"transActionAccountId={transaction_account_id}",
            f"revokeAppSheetNo={app_sheet_serial_no}",
            f"fundCode={real_fund_code}",
            f"tradePassword={pwd}",
            "shareType=0",
            "operator=145",
        ]
        revoke_resp = await self._proxy_request(
            "/rz/trade/dubbo/revoke",
            body="&".join(body_parts),
            content_type="application/x-www-form-urlencoded",
        )

        return {
            "fund_code": real_fund_code,
            "fund_name": fund_name,
            "amount": amount,
            "order_no": app_sheet_serial_no,
            "revoke_result": revoke_resp,
        }

    async def get_account_overview(self) -> dict:
        """账户总览（总资产、累计盈亏、当日盈亏、银行卡数、风险等级等）"""
        return await self._trade_get(
            f"/rs/incomequery/queryzcsharemobilehomenine/{self.trade_cust_id}"
        )

    async def get_fund_positions(self) -> dict:
        """基金持仓列表（持仓基金明细、市值、收益等）"""
        return await self._trade_get(
            f"/rs/fundpositionquery/fundpositionassemble/{self.trade_cust_id}"
        )

    async def get_wallet_info(self) -> dict:
        """活期宝/超级T+0（货币基金收益、可用余额等）"""
        return await self._trade_get(
            f"/rs/query/supertzeromobilehome3/{self.trade_cust_id}"
        )

    async def get_wallet_home(self) -> dict:
        """钱包首页（活期宝余额、冻结金额、累计收益等）"""
        return await self._proxy_request(
            "/rz/wallet/dubbo/v1/queryWalletHomePage",
            method="POST",
            body=f"custId={self.trade_cust_id}",
            content_type="application/x-www-form-urlencoded",
        )

    async def get_auto_invest_list(self, page_size: int = 20, status: str = "N") -> dict:
        """定投计划列表
        status: N=全部, 1=执行中, 2=暂停
        """
        body = json.dumps({
            "pageSize": page_size,
            "protocolStatusList": [],
            "productType": None,
            "lastProtocolNo": "",
            "fundCode": None,
            "transactionAccountIds": [None],
            "status": status,
            "type": None,
        })
        return await self._proxy_request(
            "/rz/positionqryweb/dubbo/protocol/v3/automatic_invest/list",
            method="POST",
            body=body,
            content_type="application/json",
        )

    async def get_auto_invest_summary(self) -> dict:
        """定投汇总（总金额、下次执行日期、正常/暂停计划数等）"""
        return await self._proxy_request(
            "/rz/positionqryweb/dubbo/protocol/v3/automatic_invest/summary",
            method="POST",
            body=f"custId={self.trade_cust_id}",
            content_type="application/x-www-form-urlencoded",
        )

    async def get_account_binding(self) -> dict:
        """账户绑定信息（客户ID、姓名、身份证号等）"""
        auth = self.TRADE_AUTH
        return await self._trade_post_form(
            "/rz/account/bind/query_bind/v1/result",
            data={
                "temporary": "0",
                "sessionId": auth["sessionId"],
                "userId": auth["userId"],
            },
            extra_headers={
                "custId": auth["key3"],
                "sessionId": auth["sessionId"],
                "userId": auth["userId"],
                "token": auth["key5"],
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

    async def get_account_open_status(self) -> dict:
        """开户状态查询"""
        auth = self.TRADE_AUTH
        return await self._trade_post_form(
            "/rz/account/open_account/getOpenAccountByUserId/v1/result",
            data={
                "custId": auth["key3"],
                "sessionId": auth["sessionId"],
                "userId": auth["userId"],
                "version": "1",
                "token": auth["key5"],
            },
            extra_headers={
                "custId": auth["key3"],
                "sessionId": auth["sessionId"],
                "userId": auth["userId"],
                "version": "1",
                "token": auth["key5"],
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

    async def get_trade_account_all(self) -> dict:
        """获取所有交易账户数据（一次性并发查询）"""
        overview_t = self.get_account_overview()
        positions_t = self.get_fund_positions()
        wallet_t = self.get_wallet_info()
        wallet_home_t = self.get_wallet_home()
        auto_invest_t = self.get_auto_invest_list()
        auto_summary_t = self.get_auto_invest_summary()
        binding_t = self.get_account_binding()

        results = await asyncio.gather(
            overview_t, positions_t, wallet_t, wallet_home_t,
            auto_invest_t, auto_summary_t, binding_t,
            return_exceptions=True,
        )

        def _safe(r):
            return r if not isinstance(r, Exception) else {"error": str(r)}

        return {
            "account_overview": _safe(results[0]),
            "fund_positions": _safe(results[1]),
            "wallet_info": _safe(results[2]),
            "wallet_home": _safe(results[3]),
            "auto_invest_list": _safe(results[4]),
            "auto_invest_summary": _safe(results[5]),
            "account_binding": _safe(results[6]),
        }

    @staticmethod
    def _cosine_similarity(vec_a: dict[str, float], vec_b: dict[str, float]) -> float:
        """计算两个行业分布向量的余弦相似度"""
        import math
        keys = set(vec_a) | set(vec_b)
        dot = sum(vec_a.get(k, 0) * vec_b.get(k, 0) for k in keys)
        mag_a = math.sqrt(sum(v ** 2 for v in vec_a.values()))
        mag_b = math.sqrt(sum(v ** 2 for v in vec_b.values()))
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)

    @staticmethod
    def _extract_industry_vector(style_data: dict) -> dict[str, float]:
        """从 style_preference 响应中提取最新一期的行业分布向量"""
        rate_list = style_data.get("data", {}).get("rateList", [])
        if not rate_list:
            return {}
        latest = rate_list[-1]  # 最新一期
        keys = ["kjRate", "zzRate", "xfRate", "zqRate", "jrRate", "ylRate", "jjRate"]
        return {k: latest.get(k, 0) for k in keys}

    async def find_similar_funds(self, fund_code: str, top_n: int = 5) -> list[dict]:
        """自动发现同赛道基金
        1. 获取目标基金的行业分布
        2. 从同花顺获取基金排行（按近一年收益，取前100）
        3. 逐批查询候选基金的行业分布，筛选相似度高的
        返回: [{"code", "name", "similarity", "return_1y"}, ...]
        """
        import asyncio
        # 1. 获取目标基金的行业向量
        target_style = await self.get_style_preference(fund_code)
        target_vec = self._extract_industry_vector(target_style)
        if not target_vec or all(v == 0 for v in target_vec.values()):
            return []

        # 2. 获取候选池（同花顺原生排行）
        ranking_resp = await self.get_fund_ranking(sort_type="year", sort="DESC", limit=100)
        # 从返回结构中提取基金列表: data[0].list
        data_list = ranking_resp.get("data", [])
        fund_list = data_list[0].get("list", []) if data_list else []
        # 排除目标基金自身
        candidates = [f for f in fund_list if f.get("tradeCode") != fund_code]

        # 3. 分批并发查询行业分布（每批10只，最多查30只）
        scored = []
        batch_size = 10
        max_query = 30
        for i in range(0, min(len(candidates), max_query), batch_size):
            batch = candidates[i:i + batch_size]
            tasks = [self.get_style_preference(f["tradeCode"]) for f in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for fund_info, style_result in zip(batch, results):
                if isinstance(style_result, Exception):
                    continue
                vec = self._extract_industry_vector(style_result)
                if not vec or all(v == 0 for v in vec.values()):
                    continue
                sim = self._cosine_similarity(target_vec, vec)
                if sim >= 0.5:  # 相似度阈值
                    year_return = fund_info.get("year")
                    scored.append({
                        "code": fund_info["tradeCode"],
                        "name": fund_info.get("simpleName", ""),
                        "similarity": round(sim, 4),
                        "return_1y": float(year_return) if year_return else None,
                    })

        # 4. 按相似度排序，取 top_n
        scored.sort(key=lambda x: x["similarity"], reverse=True)
        return scored[:top_n]

    async def get_fund_compare_data(self, fund_codes: list[str]) -> list[dict]:
        """并发获取多只基金的对比数据
        返回: [{"code", "name", "manager", "scale", "establish_date",
                "return_1y", "return_3y", "return_since", "annual_return",
                "max_drawdown_1y", "sharpe_1y",
                "top_industry", "industry_concentration", "org_ratio",
                "top10_stocks"}, ...]
        """
        import asyncio

        async def _fetch_one(code: str) -> dict:
            detail_t = self.get_fund_detail(code)
            base_t = self.get_fund_base(code)
            rank_t = self.get_performance_rank(code)
            drawdown_t = self.get_max_drawdown(code)
            style_t = self.get_style_preference(code)
            overview_t = self.get_holding_overview(code)
            holder_t = self.get_holder_ratio(code)
            holdings_t = self.get_top10_holdings(code)

            results = await asyncio.gather(
                detail_t, base_t, rank_t, drawdown_t,
                style_t, overview_t, holder_t, holdings_t,
                return_exceptions=True,
            )
            detail, base, rank, drawdown, style, overview, holder, holdings = results

            # 解析 detail
            info = detail.get("data", {}) if isinstance(detail, dict) else {}
            managers = info.get("managerInfo", [])
            manager_name = managers[0].get("name", "") if managers else ""

            # 解析 base
            hc = {}
            if isinstance(base, dict):
                hc = base.get("data", {}).get("handicap", {})
            scale_raw = hc.get("fundScale")
            scale = round(float(scale_raw) / 1e8, 2) if scale_raw else None
            establish = hc.get("establishmentDate", "")
            sharpe = round(float(hc["sharpeYear"]), 4) if hc.get("sharpeYear") else None
            max_dd_year = round(float(hc["maxDrawDownYear"]), 2) if hc.get("maxDrawDownYear") else None
            annual = round(float(hc["nowAnnual"]), 2) if hc.get("nowAnnual") else None

            # 解析 rank
            rank_map = {}
            if isinstance(rank, dict):
                for item in rank.get("data", []):
                    t = item.get("time", "")
                    y = item.get("yield")
                    if y is not None:
                        rank_map[t] = round(float(y), 2)

            # 解析 drawdown
            dd_map = {}
            if isinstance(drawdown, dict):
                for item in drawdown.get("data", []):
                    t = item.get("time", "")
                    d = item.get("drawdown")
                    if d is not None:
                        dd_map[t] = round(float(d), 2)

            # 解析 style（最新一期的主要行业）
            label_map = {"kjRate": "科技", "zzRate": "制造", "xfRate": "消费",
                         "zqRate": "周期", "jrRate": "金融", "ylRate": "医疗", "jjRate": "军工"}
            top_industry = ""
            industry_pct = 0
            if isinstance(style, dict):
                vec = self._extract_industry_vector(style)
                if vec:
                    top_key = max(vec, key=vec.get)
                    top_industry = label_map.get(top_key, top_key)
                    industry_pct = round(vec[top_key] * 100, 1)

            # 解析 overview（集中度）
            concentration = None
            if isinstance(overview, dict):
                ov = overview.get("data", {}).get(code, {})
                c = ov.get("fundStockConcentration")
                if c:
                    concentration = round(float(c) * 100, 1)

            # 解析 holder_ratio（最新一期机构占比）
            org_ratio = None
            if isinstance(holder, dict):
                items = holder.get("data", [])
                if items:
                    r = items[0].get("orgRate")
                    if r:
                        org_ratio = round(float(r), 1)

            # 解析 top10 holdings（股票代码列表）
            top10 = []
            if isinstance(holdings, dict):
                for s in holdings.get("data", {}).get("stock", []):
                    top10.append(s.get("secCode", ""))

            return {
                "code": code,
                "name": info.get("name", code),
                "manager": manager_name,
                "scale": scale,
                "establish_date": establish,
                "return_1y": rank_map.get("近一年"),
                "return_3y": rank_map.get("近三年"),
                "return_since": rank_map.get("成立以来"),
                "annual_return": annual,
                "max_drawdown_1y": max_dd_year,
                "sharpe_1y": sharpe,
                "top_industry": f"{top_industry}({industry_pct}%)" if top_industry else "",
                "concentration": concentration,
                "org_ratio": org_ratio,
                "top10_stocks": top10,
            }

        tasks = [_fetch_one(code) for code in fund_codes]
        return await asyncio.gather(*tasks)

    # ========== A 股大盘总览 ==========

    EASTMONEY_PUSH2 = "https://push2.eastmoney.com"
    THS_DATA = "https://data.10jqka.com.cn"

    # 东方财富指数代码映射
    INDEX_SECIDS = {
        "上证指数": "1.000001", "深证成指": "0.399001", "创业板指": "0.399006",
        "沪深300": "1.000300", "上证50": "1.000016", "中证500": "1.000905",
        "中证1000": "0.399852", "国证2000": "0.399303", "北证50": "0.899050",
    }

    async def get_market_overview(self) -> dict:
        """A 股大盘总览：指数行情 + 涨跌家数 + 成交额 + 资金流向 + 涨跌停 + 大小盘对比"""

        em_headers = {
            "Referer": "https://quote.eastmoney.com/",
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
        }
        ths_data_headers = {
            "Referer": "https://data.10jqka.com.cn/",
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
        }

        async def _fetch_indices():
            """获取主要指数行情（含涨跌家数），带重试"""
            secids = ",".join([
                self.INDEX_SECIDS["上证指数"], self.INDEX_SECIDS["深证成指"],
                self.INDEX_SECIDS["创业板指"], self.INDEX_SECIDS["沪深300"],
                self.INDEX_SECIDS["上证50"], self.INDEX_SECIDS["中证500"],
                self.INDEX_SECIDS["中证1000"], self.INDEX_SECIDS["国证2000"],
                self.INDEX_SECIDS["北证50"],
            ])
            for attempt in range(3):
                try:
                    resp = await self._client.get(
                        f"{self.EASTMONEY_PUSH2}/api/qt/ulist.np/get",
                        params={"fltt": 2, "secids": secids,
                                "fields": "f2,f3,f4,f5,f6,f7,f8,f12,f14,f104,f105,f106"},
                        headers=em_headers,
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

        async def _fetch_capital_flow(secid: str):
            """获取指数当日资金流向，带重试"""
            for attempt in range(3):
                try:
                    resp = await self._client.get(
                        f"{self.EASTMONEY_PUSH2}/api/qt/stock/fflow/kline/get",
                        params={"secid": secid, "fields1": "f1,f2,f3",
                                "fields2": "f51,f52,f53,f54,f55,f56", "lmt": 1, "klt": 101},
                        headers=em_headers,
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

        async def _fetch_limit_pool(pool_type: str):
            """获取涨停/跌停池"""
            endpoint = "limit_up_pool" if pool_type == "up" else "lower_limit_pool"
            resp = await self._client.get(
                f"{self.THS_DATA}/dataapi/limit_up/{endpoint}/",
                params={"page": 1, "limit": 15,
                        "field": "199112,10,9001,330323,330324,330325,9002,330329,133971,133970,1968584,3475914",
                        "order_field": "330324", "order_type": "0"},
                headers=ths_data_headers,
            )
            resp.raise_for_status()
            return resp.json()

        # 并发获取所有数据
        indices_raw, flow_sh_raw, flow_sz_raw, limit_up_raw, limit_down_raw = await asyncio.gather(
            _fetch_indices(),
            _fetch_capital_flow("1.000001"),   # 沪市资金流
            _fetch_capital_flow("0.399001"),   # 深市资金流
            _fetch_limit_pool("up"),
            _fetch_limit_pool("down"),
            return_exceptions=True,
        )

        result = {"indices": [], "market_stats": {}, "capital_flow": {},
                  "limit_up": {}, "limit_down": {}, "cap_comparison": {}, "signals": []}

        # ---------- 1. 指数行情 ----------
        if isinstance(indices_raw, dict):
            diff = indices_raw.get("data", {}).get("diff", [])
            total_rise = total_fall = total_flat = 0
            total_turnover = 0
            sh_stats = sz_stats = bj_stats = None

            for item in diff:
                code = item.get("f12", "")
                name = item.get("f14", "")
                close = item.get("f2")
                change_rate = item.get("f3")
                change_amt = item.get("f4")
                volume = item.get("f5")
                turnover = item.get("f6", 0)
                amplitude = item.get("f7")
                turnover_rate = item.get("f8")
                rise = item.get("f104", 0)
                fall = item.get("f105", 0)
                flat = item.get("f106", 0)

                idx_info = {
                    "code": code, "name": name, "close": close,
                    "changeRate": change_rate, "changeAmt": change_amt,
                    "volume": volume, "turnover": turnover,
                    "amplitude": amplitude, "turnoverRate": turnover_rate,
                    "rise": rise, "fall": fall, "flat": flat,
                }
                result["indices"].append(idx_info)

                # 统计全市场涨跌家数（上证=沪市全部, 深证成指≈深市全部, 北证50=北交所）
                if code == "000001":
                    sh_stats = (rise, fall, flat)
                    total_turnover += turnover
                elif code == "399001":
                    sz_stats = (rise, fall, flat)
                    total_turnover += turnover
                elif code == "899050":
                    bj_stats = (rise, fall, flat)
                    total_turnover += turnover

            # 合计全 A 涨跌家数
            for stats in [sh_stats, sz_stats, bj_stats]:
                if stats:
                    total_rise += stats[0]
                    total_fall += stats[1]
                    total_flat += stats[2]

            result["market_stats"] = {
                "totalRise": total_rise, "totalFall": total_fall, "totalFlat": total_flat,
                "totalStocks": total_rise + total_fall + total_flat,
                "totalTurnover": round(total_turnover / 1e8, 2),  # 亿元
            }

            # 大小盘对比：沪深300 vs 国证2000
            hs300_chg = next((i["changeRate"] for i in result["indices"] if i["code"] == "000300"), None)
            gz2000_chg = next((i["changeRate"] for i in result["indices"] if i["code"] == "399303"), None)
            if hs300_chg is not None and gz2000_chg is not None:
                result["cap_comparison"] = {
                    "largeCap": {"name": "沪深300", "changeRate": hs300_chg},
                    "smallCap": {"name": "国证2000", "changeRate": gz2000_chg},
                    "diff": round(gz2000_chg - hs300_chg, 2),
                    "stronger": "小盘股更强" if gz2000_chg > hs300_chg else "大盘股更强" if hs300_chg > gz2000_chg else "大小盘持平",
                }

        # ---------- 2. 资金流向 ----------
        total_main_flow = 0
        flow_details = []
        for raw, name in [(flow_sh_raw, "沪市"), (flow_sz_raw, "深市")]:
            if isinstance(raw, dict):
                klines = raw.get("data", {}).get("klines", [])
                if klines:
                    parts = klines[0].split(",")
                    if len(parts) >= 6:
                        main_flow = float(parts[5])
                        super_large = float(parts[1])
                        large = float(parts[2])
                        medium = float(parts[3])
                        small = float(parts[4])
                        total_main_flow += main_flow
                        flow_details.append({
                            "market": name, "date": parts[0],
                            "mainFlow": round(main_flow / 1e8, 2),
                            "superLarge": round(super_large / 1e8, 2),
                            "large": round(large / 1e8, 2),
                            "medium": round(medium / 1e8, 2),
                            "small": round(small / 1e8, 2),
                        })
        result["capital_flow"] = {
            "totalMainFlow": round(total_main_flow / 1e8, 2),
            "details": flow_details,
        }

        # ---------- 3. 涨跌停统计 ----------
        for raw, key in [(limit_up_raw, "limit_up"), (limit_down_raw, "limit_down")]:
            if isinstance(raw, dict) and raw.get("status_code") == 0:
                page_info = raw.get("data", {}).get("page", {})
                stocks = raw.get("data", {}).get("info", [])
                items = []
                for s in stocks[:15]:
                    items.append({
                        "code": s.get("code", ""),
                        "name": s.get("name", ""),
                        "changeRate": s.get("1968584"),
                        "tag": s.get("change_tag", ""),
                    })
                result[key] = {"total": page_info.get("total", 0), "stocks": items}

        # ---------- 4. 信号生成 ----------
        stats = result["market_stats"]
        if stats.get("totalRise") and stats.get("totalFall"):
            ratio = stats["totalRise"] / max(stats["totalFall"], 1)
            if ratio > 2:
                result["signals"].append(f"涨跌比 {ratio:.1f}:1，市场情绪偏多")
            elif ratio < 0.5:
                result["signals"].append(f"涨跌比 1:{1/ratio:.1f}，市场情绪偏空")

        flow_total = result["capital_flow"].get("totalMainFlow", 0)
        if flow_total > 50:
            result["signals"].append(f"主力资金净流入 {flow_total:+.0f} 亿，增量资金入场")
        elif flow_total < -50:
            result["signals"].append(f"主力资金净流出 {abs(flow_total):.0f} 亿，资金离场")

        limit_up_total = result.get("limit_up", {}).get("total", 0)
        limit_down_total = result.get("limit_down", {}).get("total", 0)
        if limit_up_total > 50:
            result["signals"].append(f"涨停 {limit_up_total} 家，市场赚钱效应强")
        if limit_down_total > 30:
            result["signals"].append(f"跌停 {limit_down_total} 家，注意风险")

        cap = result.get("cap_comparison", {})
        if cap.get("diff") is not None:
            if cap["diff"] > 1:
                result["signals"].append("小盘股明显强于大盘股，中小盘风格占优")
            elif cap["diff"] < -1:
                result["signals"].append("大盘股明显强于小盘股，蓝筹风格占优")

        return {"status_code": 0, "data": result}

    EASTMONEY_PUSH2EX = "https://push2ex.eastmoney.com"
    EM_UT = "7eea3edcaed734bea9cbfc24409ed989"

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

    # 异动类型编码 → 中文名
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

    SINA_STOCK_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    SINA_SECTOR_URL = "https://money.finance.sina.com.cn/q/view/newFLJK.php"

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

        import json
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

    # ========== 龙虎榜 ==========

    EM_DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get"

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

    # ========== 同花顺游资龙虎榜 ==========

    THS_LHB_URL = "http://data.10jqka.com.cn/market/longhu/"
    THS_LHB_AJAX = "http://data.10jqka.com.cn/ifmarket/lhbtable"

    async def get_ths_dragon_tiger(self, tab: str = "youzi", count: int = 30) -> dict:
        """同花顺龙虎榜 - 带游资/机构标签

        tab: "youzi"=游资(含一线/知名), "jigou"=机构专用, "all"=全部
        """
        import re

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "http://data.10jqka.com.cn/",
        }

        resp = await self._client.get(self.THS_LHB_URL, headers=headers, timeout=15)
        resp.raise_for_status()
        text = resp.content.decode("gbk", errors="replace")

        # 解析报告日期
        date_match = re.search(r'report="(\d{4}-\d{2}-\d{2})"', text)
        report_date = date_match.group(1) if date_match else ""

        # ---- 解析左侧股票列表 ----
        left_rows = re.findall(
            r'<tr[^>]*>\s*'
            r'<td[^>]*>\s*(?:<label[^>]*>(\d+日)</label>)?\s*</td>\s*'
            r'<td[^>]*>(\d{6})</td>\s*'
            r'<td[^>]*><a[^>]*stockcode="(\d+)"[^>]*rid="([^"]+)"[^>]*class="stock">([^<]+)</a></td>\s*'
            r'<td[^>]*class="[^"]*tr[^"]*">([^<]+)</td>\s*'
            r'<td[^>]*class="[^"]*tr[^"]*">([^<]+)</td>\s*'
            r'<td[^>]*class="[^"]*tr[^"]*">([^<]+)</td>\s*'
            r'<td[^>]*class="[^"]*tr[^"]*">([^<]+)</td>',
            text,
        )

        # ---- 解析右侧席位明细 ----
        stockcont_starts = [(m.start(), m.group(1))
                            for m in re.finditer(r"<div class=\"stockcont\"[^>]*rid='([^']+)'", text)]

        stock_details = {}
        for i, (start, rid) in enumerate(stockcont_starts):
            end = stockcont_starts[i + 1][0] if i + 1 < len(stockcont_starts) else len(text)
            section = text[start:end]

            # 汇总行
            summary = re.search(
                r'净额：<span[^>]*>([^<]+)</span>万元', section,
            )
            net_total = summary.group(1).replace(",", "") if summary else "0"

            # 买入/卖出席位
            buy_part = section.split("卖出金额最大的前5名")[0] if "卖出金额最大的前5名" in section else section
            sell_part = section[section.find("卖出金额最大的前5名"):] if "卖出金额最大的前5名" in section else ""

            def _parse_seats(html):
                seats = []
                for dept, label, buy, sell, net in re.findall(
                    r'title="([^"]+)">[^<]+</a>\s*'
                    r'(?:<label class="label[^"]*">([^<]+)</label>)?\s*</td>\s*'
                    r'<td[^>]*>([^<]*)</td>\s*'
                    r'<td[^>]*>([^<]*)</td>\s*'
                    r'<td[^>]*>([^<]*)</td>',
                    html,
                ):
                    # "机构专用" 没有 label 标签，直接作为 dept 名出现
                    effective_label = label
                    if not effective_label and dept == "机构专用":
                        effective_label = "机构专用"
                    seats.append({
                        "dept": dept,
                        "label": effective_label,
                        "buy": buy.strip(),
                        "sell": sell.strip(),
                        "net": net.strip(),
                    })
                return seats

            buy_seats = _parse_seats(buy_part)
            sell_seats = _parse_seats(sell_part)

            all_labels = set()
            for s in buy_seats + sell_seats:
                if s["label"]:
                    all_labels.add(s["label"])

            stock_details[rid] = {
                "netTotal": net_total,
                "buySeats": buy_seats,
                "sellSeats": sell_seats,
                "labels": all_labels,
            }

        # ---- 合并 & 分类 ----
        youzi_labels = {"一线游资", "知名游资"}
        jigou_label = "机构专用"
        all_items = []

        for row in left_rows:
            days, code, _, rid, name, price, chg, total_amt, net_amt = row
            detail = stock_details.get(rid, {})
            labels = detail.get("labels", set())

            # 判断类别
            has_youzi = bool(labels & youzi_labels)
            has_jigou = jigou_label in labels
            has_gansidui = "敢死队" in labels
            has_gfgs = "跟风高手" in labels

            if tab == "youzi" and not has_youzi:
                continue
            if tab == "jigou" and not has_jigou:
                continue
            if tab == "gansidui" and not has_gansidui:
                continue
            if tab == "gfgs" and not has_gfgs:
                continue

            # 收集参与的游资/机构席位信息
            tagged_seats = []
            for side, seats in [("买", detail.get("buySeats", [])), ("卖", detail.get("sellSeats", []))]:
                for s in seats:
                    if not s["label"]:
                        continue
                    if tab == "youzi" and s["label"] not in youzi_labels:
                        continue
                    if tab == "jigou" and s["label"] != jigou_label:
                        continue
                    tagged_seats.append({
                        "side": side,
                        "dept": s["dept"],
                        "label": s["label"],
                        "buy": s["buy"],
                        "sell": s["sell"],
                        "net": s["net"],
                    })

            all_items.append({
                "code": code,
                "name": name,
                "price": price,
                "chg": chg,
                "totalAmt": total_amt,
                "netAmt": net_amt,
                "days": days or "",
                "labels": sorted(labels),
                "seats": tagged_seats,
            })
            if len(all_items) >= count:
                break

        return {
            "status_code": 0,
            "data": {
                "tab": tab,
                "date": report_date,
                "total": len(all_items),
                "items": all_items,
            },
        }

    # ========== 资金流向 ==========

    PUSH2HIS = "https://push2his.eastmoney.com"
    EM_FFLOW_UT = "b2884a393a59ad64002292a3e90d46a5"

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

    # ========== 热点板块 ==========

    async def get_hot_board(self, board_type: str = "concept", sort: str = "rise", count: int = 10) -> dict:
        """热点板块排行（东方财富 push2 API）

        board_type: "concept"=概念板块, "industry"=行业板块
        sort: "rise"=今日涨幅最大, "flow"=资金流入最多, "5day"=5日涨幅最大
        """
        fs_map = {"concept": "m:90+t:3", "industry": "m:90+t:2"}
        fs = fs_map.get(board_type, "m:90+t:3")

        # 不同排序对应不同字段
        sort_map = {
            "rise": ("f3", "1"),     # 涨跌幅 降序
            "flow": ("f62", "1"),    # 主力净流入 降序
            "5day": ("f109", "1"),   # 5日涨跌幅 降序 (f109 = 5日涨跌幅)
        }
        fid, po = sort_map.get(sort, ("f3", "1"))

        fields = "f2,f3,f4,f8,f12,f14,f20,f62,f104,f105,f128,f140,f109"

        # 带重试的请求（push2 API 偶尔返回空）
        for attempt in range(3):
            url = f"{self.EASTMONEY_PUSH2}/api/qt/clist/get"
            try:
                resp = await self._client.get(url, params={
                    "pn": 1, "pz": min(count, 30), "po": po, "np": 1,
                    "fltt": 2, "fid": fid, "fs": fs, "fields": fields,
                })
                resp.raise_for_status()
                data = resp.json()
                if data.get("data") and data["data"].get("diff"):
                    break
            except Exception:
                if attempt == 2:
                    return {"status_code": -1, "data": {"boards": []}, "msg": "push2 API 请求失败"}
                continue
        else:
            return {"status_code": -1, "data": {"boards": []}, "msg": "push2 API 返回空数据"}

        diff = data["data"]["diff"]
        total = data["data"].get("total", 0)

        boards = []
        for item in diff:
            boards.append({
                "code": item.get("f12", ""),
                "name": item.get("f14", ""),
                "changeRate": item.get("f3"),           # 今日涨跌幅 %
                "changeAmt": item.get("f4"),            # 今日涨跌额
                "turnoverRate": item.get("f8"),         # 换手率 %
                "amount": item.get("f20"),              # 成交额（元）
                "netFlow": item.get("f62"),             # 主力净流入（元）
                "upCount": item.get("f104"),            # 上涨家数
                "downCount": item.get("f105"),          # 下跌家数
                "leadStock": item.get("f128", ""),      # 领涨股名称
                "leadCode": item.get("f140", ""),       # 领涨股代码
                "change5day": item.get("f109"),         # 5日涨跌幅 %
            })

        sort_names = {"rise": "今日涨幅最大", "flow": "资金流入最多", "5day": "5日涨幅最大"}
        return {"status_code": 0, "data": {
            "boardType": board_type,
            "sort": sort,
            "sortName": sort_names.get(sort, "今日涨幅最大"),
            "total": total,
            "boards": boards,
        }}

    # ========== 货币风向 ==========

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

    # ========== 热榜数据 ==========

    HOT_LIST_BASE = "https://eq.10jqka.com.cn/open"
    HOT_TOPIC_BASE = "https://t.10jqka.com.cn"
    HOT_BOND_BASE = "https://dq.10jqka.com.cn"

    async def get_hot_stocks(self, market: str = "a") -> dict:
        """个股热榜（A股/港股/美股）"""
        url = f"{self.HOT_LIST_BASE}/api/hot_list/v1/hot_stock/{market}/day/data.txt"
        return await self._get(url)

    async def get_hot_plate(self, plate_type: str = "concept") -> dict:
        """概念/行业热榜"""
        url = f"{self.HOT_LIST_BASE}/api/hot_list/v1/hot_plate/{plate_type}/data.txt"
        return await self._get(url)

    async def get_hot_etf(self) -> dict:
        """ETF 热榜"""
        url = f"{self.HOT_LIST_BASE}/api/etf_rank/v1/hot.txt"
        return await self._get(url)

    async def get_hot_futures(self) -> dict:
        """期货热榜"""
        url = f"{self.HOT_LIST_BASE}/api/hot_list/v1/futures/data.txt"
        return await self._get(url)

    async def get_hot_bond(self) -> dict:
        """可转债热榜"""
        url = f"{self.HOT_BOND_BASE}/fuyao/hot_list_data/out/hot_list/v1/bond"
        return await self._get(url)

    async def get_hot_topics(self) -> dict:
        """热榜话题"""
        url = f"{self.HOT_TOPIC_BASE}/lgt/topic/open/api/hot_topic/v1/hot_module_list"
        return await self._get(url)

    async def get_hot_posts(self, page: int = 1, page_size: int = 10) -> dict:
        """热门文章"""
        url = f"{self.HOT_TOPIC_BASE}/lgt/hotmodules/open/api/hot_module/v1/hot_post/list"
        return await self._get(url, params={"page": page, "pageSize": page_size})

    NEWS_BASE = "https://news.10jqka.com.cn"

    async def get_headlines(self) -> dict:
        """推荐头条（首页推荐tab头条模块）"""
        url = f"{self.NEWS_BASE}/tapp/news/headline/ths/client"
        return await self._get(url)

    @staticmethod
    def seq_to_encoded(seq: int) -> str:
        """将新闻 seq 数字 ID 转换为 encoded 格式（逆向自 zx-detail-fronted-container）"""
        import hashlib
        MAX = 100_000_000_000
        MULTIPLIER = 2147483647
        scrambled = (seq * MULTIPLIER) % MAX
        padded = str(scrambled).zfill(11)
        check_digit = sum(int(d) for d in str(seq)) % 10
        h = hashlib.md5(f"{seq}{check_digit}".encode()).hexdigest()
        return h[:4] + padded + str(check_digit) + h[-3:]

    async def get_article_detail(self, encoded_seq: str) -> dict:
        """获取新闻文章详情（type=1 新闻）
        encoded_seq: encoded 格式的文章ID，或纯数字 seq（会自动转换）
        """
        if encoded_seq.isdigit():
            encoded_seq = self.seq_to_encoded(int(encoded_seq))
        url = f"{self.NEWS_BASE}/mobile_api/news/article/v1/encoded/{encoded_seq}"
        return await self._get(url)

    async def get_news_themes(self) -> dict:
        """获取新闻主题分类列表（资讯→头条 tab 栏的主题标签）"""
        url = f"{self.NEWS_BASE}/app/headline/v1/hot-theme"
        return await self._get(url)

    async def get_theme_articles(self, theme_id: str, page: int = 1, size: int = 15) -> dict:
        """获取主题下的文章列表
        theme_id: 主题ID，如 TZ-11385
        需要先查询 theme info 获取内容流 ID，再查询文章列表
        """
        # 1. 获取主题模块配置，找到内容流 ID
        theme_url = f"{self.NEWS_BASE}/app/theme/v1/theme"
        theme_info = await self._get(theme_url, params={"themeId": theme_id})
        data = theme_info.get("data", {})
        stream_id = None
        for module in data.get("module", []):
            if module.get("type") == 2:
                items = module.get("items", [])
                if items:
                    stream_id = items[0].get("id")
                    break
        if stream_id is None:
            return {"status_code": -1, "data": [], "msg": "未找到内容流ID"}

        # 2. 获取文章列表
        content_url = f"{self.NEWS_BASE}/app/theme/v1/content"
        content = await self._get(content_url, params={"id": str(stream_id), "page": page, "size": size})
        return {
            "status_code": 0,
            "data": {
                "themeId": theme_id,
                "title": data.get("title", ""),
                "description": data.get("content", ""),
                "streamId": stream_id,
                "articles": content.get("data", []),
            },
        }

    async def get_flash_news_tabs(self) -> dict:
        """获取快讯分类标签列表（A股、重要、公告、期货、异动、港股、美股）"""
        url = f"{self.NEWS_BASE}/app/flash/flashnews/v2/tab"
        return await self._get(url)

    async def get_flash_news_list(self, tag_id: int = 21101, seq: int = 0) -> dict:
        """获取指定分类的快讯列表
        tag_id: 分类ID（从 get_flash_news_tabs 获取），默认21101=A股
        seq: 翻页游标，0=最新，传入上一页最后一条的 seq 加载更早的
        """
        url = f"{self.NEWS_BASE}/app/flash/flashnews/v1/list"
        return await self._get(url, params={"tagId": tag_id, "seq": seq})

    async def get_news_feed(self, page: int = 1) -> dict:
        """滚动快讯（财经要闻实时滚动，每页20条）"""
        url = f"{self.NEWS_BASE}/tapp/news/push/stock/"
        return await self._get(url, params={"page": page})

    async def get_topic_detail(self, code: str, page: int = 1, page_size: int = 10) -> dict:
        """话题详情（含推荐帖子列表）"""
        info_url = f"{self.HOT_TOPIC_BASE}/lgt/topic/open/api/topic_info/v1/topic?code={code}"
        feed_url = f"{self.HOT_TOPIC_BASE}/lgt/topic/open/api/topic_info/v3/recommend_list?code={code}&page={page}&pageSize={page_size}"
        info_resp, feed_resp = await asyncio.gather(
            self._get(info_url), self._get(feed_url)
        )
        return {"topic": info_resp.get("data", {}), "feeds": feed_resp.get("data", {})}

    async def get_special_detail(self, code: str) -> dict:
        """专题详情（从 HTML 解析组件内容）"""
        import json as _json, re
        url = f"https://mams.10jqka.com.cn/new/server/html/{code}.html"
        resp = await self._client.get(url)
        resp.raise_for_status()
        html = resp.text
        match = re.search(r'var\s+activity\s*=\s*', html)
        if not match:
            return {"error": "无法解析专题内容"}
        decoder = _json.JSONDecoder()
        activity, _ = decoder.raw_decode(html, match.end())
        components = activity.get("page", {}).get("components", [])
        result = {"title": "", "desc": "", "abstract": "", "tabs": [], "sections": []}
        for comp in components:
            d = comp.get("detail", {})
            name = d.get("name", "")
            if name == "hot-header-image":
                result["title"] = d.get("title", {}).get("value", "")
                result["desc"] = d.get("desc", {}).get("value", "")
                result["abstract"] = d.get("abstract", {}).get("value", "")
            elif name == "v-tab":
                result["tabs"] = [t.get("content", "") for t in d.get("title", [])]
                for sub in comp.get("components", []):
                    self._extract_special_section(sub, result["sections"])
            elif name == "futures-kyc-relatedinfo":
                result["sections"].append({
                    "type": "关联商品",
                    "title": d.get("title", ""),
                    "content": d.get("content", ""),
                })
            else:
                self._extract_special_section(comp, result["sections"])
        return result

    def _extract_special_section(self, comp: dict, sections: list):
        """递归提取专题组件中的有效内容"""
        import re
        d = comp.get("detail", {})
        name = d.get("name", "")
        if name == "ai-html-component":
            text = d.get("text", "")
            clean = re.sub(r"<[^>]+>", " ", text)
            clean = re.sub(r"\s+", " ", clean).strip()
            if clean and len(clean) > 20 and not clean.startswith(("AI摘要展示模块", ".ai-summary")):
                sections.append({"type": "内容", "content": clean})
        elif name == "event-timeline":
            title = d.get("title", "事件脉络")
            if isinstance(title, dict):
                title = title.get("value", "事件脉络")
            sections.append({"type": "事件脉络", "title": title})
        elif name == "news-content-flow-unify":
            title = d.get("title", "")
            if title:
                sections.append({"type": "资讯", "title": title})
        elif name == "event-deep-analysis":
            sections.append({"type": "深度分析"})
        elif name == "core-target-unify":
            title = d.get("title", "")
            if title:
                sections.append({"type": "相关标的", "title": title})
        elif name == "vue-title":
            title = d.get("title", "")
            if title:
                sections.append({"type": "标题", "title": title})
        elif name == "trends-read":
            content = d.get("content", "")
            if content:
                sections.append({"type": "内容", "content": content})
        elif name == "vue-text":
            text = d.get("textContent", "")
            if text:
                clean = re.sub(r"<[^>]+>", "", text).strip()
                if clean and len(clean) > 10:
                    sections.append({"type": "说明", "content": clean})
        for sub in comp.get("components", []):
            self._extract_special_section(sub, sections)

    # ========== 个股查询 ==========

    @staticmethod
    def _stock_secid(code: str) -> str:
        """股票代码转东财 secid 格式: 6开头/9开头→1.{code}(沪), 其他→0.{code}(深)"""
        code = code.strip()
        if code.startswith(("6", "9")):
            return f"1.{code}"
        return f"0.{code}"

    @staticmethod
    def _stock_tencent(code: str) -> str:
        """股票代码转腾讯行情格式: 6开头→sh{code}, 其他→sz{code}"""
        code = code.strip()
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

