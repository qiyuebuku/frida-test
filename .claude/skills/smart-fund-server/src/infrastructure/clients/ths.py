"""同花顺客户端 - 所有使用 *.10jqka.com.cn 域名的方法"""

import asyncio
import json
import math
import re
from typing import Optional

import httpx

from src.infrastructure.clients.base import BaseClient, cached


class THSClient(BaseClient):
    """同花顺数据客户端"""

    BASE_URL = "https://fund.10jqka.com.cn"
    DQ_BASE_URL = "https://dq.10jqka.com.cn"
    THS_DATA = "https://data.10jqka.com.cn"
    THS_LHB_URL = "http://data.10jqka.com.cn/market/longhu/"
    THS_LHB_AJAX = "http://data.10jqka.com.cn/ifmarket/lhbtable"
    HOT_LIST_BASE = "https://eq.10jqka.com.cn/open"
    HOT_TOPIC_BASE = "https://t.10jqka.com.cn"
    HOT_BOND_BASE = "https://dq.10jqka.com.cn"
    NEWS_BASE = "https://news.10jqka.com.cn"

    # 公告分类 catId 映射
    ANNOUNCEMENT_CATEGORIES = {
        "all": "0",
        "report": "003001",       # 业绩
        "dividend": "003004",     # 分红
        "change": "003007",       # 变更
        "operation": "003003,003002",  # 运作
        "other": "other",         # 其他
    }

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

    def __init__(self, timeout: float = 10.0):
        super().__init__(timeout)

    # ========== 基金详情 ==========

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_fund_detail(self, fund_code: str) -> dict:
        """基金综合详情（含净值、涨幅、基金经理、交易规则等）"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/fund/detail/data/{fund_code}/123"
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_fund_base(self, fund_code: str) -> dict:
        """基金基础信息（评分、风险等级、风格、基金经理）"""
        return await self._get(
            f"{self.BASE_URL}/quotation/fund_detail/v2/base/{fund_code}"
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_fund_info(self, fund_code: str) -> dict:
        """基金行情信息（净值、涨幅、规模、交易状态）"""
        return await self._get(
            f"{self.BASE_URL}/quotation/fund_detail/get",
            params={"fundCode": fund_code},
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
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

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_fund_flag(self, fund_code: str) -> dict:
        """基金标志（是否LOF/退市、二级分类）"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/static/detail/over/{fund_code}_flag"
        )

    # ========== 净值走势 ==========

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=3600)
    async def get_nav_trend(self, fund_code: str, period: str = "year") -> dict:
        """净值走势图数据
        period: year(近一年) / month(近一月) / nowyear(今年以来)
        """
        return await self._get(
            f"{self.BASE_URL}/hqapi/static/detail/flashnew/{fund_code}/{period}"
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=3600)
    async def get_realtime_trend(self, fund_code: str) -> dict:
        """实时估值分时走势（每分钟更新）"""
        return await self._get(
            f"{self.BASE_URL}/quotation/fund/detail/holder/v2/stock_trend",
            params={"fundCode": fund_code},
        )

    # ========== 业绩表现 ==========

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_performance_rank(self, fund_code: str) -> dict:
        """阶段涨幅及同类排名（近一周/月/季/半年/1-5年）"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/single_fund/detail/query_rate",
            params={"fundCode": fund_code, "type": "range"},
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_year_return(self, fund_code: str) -> dict:
        """年度收益率及同类排名"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/single_fund/detail/query_rate",
            params={"fundCode": fund_code, "type": "year"},
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_max_drawdown(self, fund_code: str) -> dict:
        """最大回撤（近半年/近一年/近三年/成立以来）"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/single_fund/detail/query_drawdown",
            params={"fundCode": fund_code, "type": "range"},
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_periodic_rate(self, fund_code: str, group_type: str = "dayPeriodicRate") -> dict:
        """定期收益率（收益稳定度）
        group_type: dayPeriodicRate / weekPeriodicRate / monthPeriodicRate / quarterPeriodicRate / yearPeriodicRate
        """
        return await self._post(
            f"{self.BASE_URL}/quotation/fund_detail/v2/periodic_rate",
            json={"groupType": group_type, "tradeCode": fund_code, "limit": 200},
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_profit_contribution(self, fund_code: str, time_type: str = "threeMonth") -> dict:
        """收益贡献分析
        time_type: threeMonth / halfYear / year
        """
        return await self._get(
            f"{self.BASE_URL}/hqapi/single_fund/detail/profit_contribution",
            params={"fundCode": fund_code, "timeType": time_type},
        )

    # ========== 持仓信息 ==========

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_top10_holdings(self, fund_code: str) -> dict:
        """前十大持仓"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/single_fund/detail/ten_asset_info",
            params={"fundCode": fund_code},
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_holding_overview(self, fund_code: str) -> dict:
        """持仓概览"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/single_fund/detail/query_hold_head",
            params={"fundCode": fund_code},
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_asset_allocation(self, fund_code: str, manager_id: str = "") -> dict:
        """资产配置"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/single_fund/detail/query_asset_config",
            params={"fundCode": fund_code, "managerId": manager_id},
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_style_preference(self, fund_code: str) -> dict:
        """投资风格偏好"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/single_fund/detail/query_type_prefer",
            params={"fundCode": fund_code},
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_position_dates(self, fund_code: str) -> dict:
        """持仓回顾 - 获取可用的季度日期列表及行业概要"""
        return await self._get(
            f"{self.BASE_URL}/bff-server/v1/fund/position_rank",
            params={"fund_code": fund_code},
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_position_detail(self, fund_code: str, end_date: str = "") -> dict:
        """持仓回顾 - 获取指定季度的前十大持仓明细"""
        return await self._get(
            f"{self.BASE_URL}/bff-server/v1/fund/position_detail",
            params={"fund_code": fund_code, "end_date": end_date},
        )

    # ========== 基金经理 ==========

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_manager_info(self, fund_code: str, manager_id: str) -> dict:
        """基金经理详细信息"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/single_fund/detail/manager_label_info",
            params={"fundManagerList": manager_id, "fundCode": fund_code},
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_manager_profile(self, manager_id: str) -> dict:
        """基金经理完整档案（个人简历、雷达图、管理基金列表等）"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/static/fundmanager/info/{manager_id}/0"
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_manager_invest_history(self, manager_id: str) -> dict:
        """基金经理投资历史（管理的所有基金业绩、重仓股）"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/static/fundmanager/investhistory/{manager_id}"
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_manager_diagnose(self, manager_id: str) -> dict:
        """基金经理诊断评分（历史规模、回撤、年化收益）"""
        return await self._get(
            f"{self.BASE_URL}/feQuotation/manager/diagnose/detail",
            params={"id": manager_id},
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_manager_industry_prefer(self, manager_id: str) -> dict:
        """基金经理行业偏好"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/fund/manager/investment/get_fund_manager_industry_prefer",
            params={"fundManagerId": manager_id},
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_manager_represent_fund(self, manager_id: str) -> dict:
        """基金经理代表基金"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/fund/manager/investment/get_represent_fund",
            params={"fundManagerId": manager_id},
        )

    # ========== 交易规则与费率 ==========

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_trade_rule(self, fund_code: str) -> dict:
        """交易规则与费率（申购/赎回费率、管理费、托管费、服务费、交易确认时间）"""
        return await self._get(
            f"{self.BASE_URL}/interface/fund/tradeRule/{fund_code}"
        )

    # ========== 规模与持有人 ==========

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_scale_change(self, fund_code: str) -> dict:
        """规模变动历史（季度净资产、申购赎回金额、份额变动、持有人结构）"""
        return await self._get(
            f"{self.BASE_URL}/interface/fund/detail/{fund_code}_gmbd"
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_holder_ratio(self, fund_code: str) -> dict:
        """机构持仓比例历史（半年度机构持有占比变化）"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/fund/detail/holder/const/{fund_code}"
        )

    # ========== 分红历史 ==========

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_dividend_history(self, fund_code: str) -> dict:
        """分红历史（从产品详情页 HTML 解析分红和拆分记录）"""
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

    # ========== 净值技术面 ==========

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=3600)
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

    # ========== 基金申赎资金流趋势 ==========

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
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

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_rsi_indicator(self, fund_code: str) -> dict:
        """RSI 买卖指标"""
        return await self._get(
            f"{self.DQ_BASE_URL}/fuyao/fund/default/v1/fund/indic",
            params={"tradeCodeList": fund_code, "typeList": "rsiBestLimitDown,rsiBestLimitUp"},
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_fund_track(self, fund_code: str) -> dict:
        """基金追踪"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/fund_track/query/{fund_code}"
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_announcements(self, fund_code: str, category: str = "all",
                                page: int = 1, page_size: int = 15) -> dict:
        """基金公告
        category: all/report/dividend/change/operation/other
        """
        cat_id = self.ANNOUNCEMENT_CATEGORIES.get(category, "0")
        return await self._get(
            f"{self.BASE_URL}/interface/net/pubnote2/{cat_id}_{fund_code}_{page}_{page_size}"
        )

    @cached(source="ths", source_name="同花顺", domain="news", frequency="realtime", market="a_share", ttl=300)
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

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
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

        # 基金类型过滤（使用 l2code 字段，支持多个代码逗号分隔）
        if fund_type:
            # 如果包含逗号，使用 IN 匹配多个类型
            if "," in fund_type:
                filter_list.append({
                    "filterField": "l2code",
                    "filterTypeList": [{"filterValue": fund_type, "filterSymbol": "IN"}],
                })
            else:
                filter_list.append({
                    "filterField": "l2code",
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

        return await self._post(
            f"{self.BASE_URL}/quotation/common/v1/list/card/info",
            json=body,
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_rank_board_config(self) -> dict:
        """获取排行榜配置（涨幅榜/反弹榜/人气榜/加仓榜/超额榜）"""
        resp = await self._get(
            f"{self.BASE_URL}/marketing/activity_redis/v1/get/fund_rank_list_v1"
        )
        # data 可能是 JSON 字符串，需要二次解析
        data = resp.get("data")
        if isinstance(data, str):
            resp["data"] = json.loads(data)
        return resp

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_rank_filter_config(self) -> dict:
        """获取筛选策略配置（年年正收益/三年翻倍/机构偏爱/十年十倍等）"""
        resp = await self._get(
            f"{self.BASE_URL}/marketing/activity_redis/v1/get/fund_rank_filter_v1"
        )
        data = resp.get("data")
        if isinstance(data, str):
            resp["data"] = json.loads(data)
        return resp

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
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

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
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

    # ========== 相似基金与对比 ==========

    @staticmethod
    def _cosine_similarity(vec_a: dict[str, float], vec_b: dict[str, float]) -> float:
        """计算两个行业分布向量的余弦相似度"""
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

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def find_similar_funds(self, fund_code: str, top_n: int = 5) -> list[dict]:
        """自动发现同赛道基金
        1. 获取目标基金的行业分布
        2. 从同花顺获取基金排行（按近一年收益，取前100）
        3. 逐批查询候选基金的行业分布，筛选相似度高的
        返回: [{"code", "name", "similarity", "return_1y"}, ...]
        """
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

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=14400)
    async def get_fund_compare_data(self, fund_codes: list[str]) -> list[dict]:
        """并发获取多只基金的对比数据
        返回: [{"code", "name", "manager", "scale", "establish_date",
                "return_1y", "return_3y", "return_since", "annual_return",
                "max_drawdown_1y", "sharpe_1y",
                "top_industry", "industry_concentration", "org_ratio",
                "top10_stocks"}, ...]
        """

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

    # ========== 同花顺游资龙虎榜 ==========

    @cached(source="ths", source_name="同花顺", domain="fund_flow", frequency="daily", market="a_share", ttl=3600)
    async def get_ths_dragon_tiger(self, tab: str = "youzi", count: int = 30) -> dict:
        """同花顺龙虎榜 - 带游资/机构标签

        tab: "youzi"=游资(含一线/知名), "jigou"=机构专用, "all"=全部
        """
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

    # ========== 市场热榜 (eq/t/dq.10jqka.com.cn) ==========

    @cached(source="ths", source_name="同花顺", domain="news", frequency="realtime", market="a_share", ttl=300)
    async def get_hot_stocks(self, market: str = "a") -> dict:
        """个股热榜（A股/港股/美股）"""
        url = f"{self.HOT_LIST_BASE}/api/hot_list/v1/hot_stock/{market}/day/data.txt"
        return await self._get(url)

    @cached(source="ths", source_name="同花顺", domain="news", frequency="realtime", market="a_share", ttl=300)
    async def get_hot_plate(self, plate_type: str = "concept") -> dict:
        """概念/行业热榜"""
        url = f"{self.HOT_LIST_BASE}/api/hot_list/v1/hot_plate/{plate_type}/data.txt"
        return await self._get(url)

    @cached(source="ths", source_name="同花顺", domain="news", frequency="realtime", market="a_share", ttl=300)
    async def get_hot_etf(self) -> dict:
        """ETF 热榜"""
        url = f"{self.HOT_LIST_BASE}/api/etf_rank/v1/hot.txt"
        return await self._get(url)

    @cached(source="ths", source_name="同花顺", domain="news", frequency="realtime", market="a_share", ttl=300)
    async def get_hot_futures(self) -> dict:
        """期货热榜"""
        url = f"{self.HOT_LIST_BASE}/api/hot_list/v1/futures/data.txt"
        return await self._get(url)

    @cached(source="ths", source_name="同花顺", domain="news", frequency="realtime", market="a_share", ttl=300)
    async def get_hot_bond(self) -> dict:
        """可转债热榜"""
        url = f"{self.HOT_BOND_BASE}/fuyao/hot_list_data/out/hot_list/v1/bond"
        return await self._get(url)

    @cached(source="ths", source_name="同花顺", domain="news", frequency="realtime", market="a_share", ttl=300)
    async def get_hot_topics(self) -> dict:
        """热榜话题"""
        url = f"{self.HOT_TOPIC_BASE}/lgt/topic/open/api/hot_topic/v1/hot_module_list"
        return await self._get(url)

    @cached(source="ths", source_name="同花顺", domain="news", frequency="realtime", market="a_share", ttl=300)
    async def get_hot_posts(self, page: int = 1, page_size: int = 10) -> dict:
        """热门文章"""
        url = f"{self.HOT_TOPIC_BASE}/lgt/hotmodules/open/api/hot_module/v1/hot_post/list"
        return await self._get(url, params={"page": page, "pageSize": page_size})

    # ========== 热点板块 (push2.eastmoney.com) ==========

    PUSH2_SUBDOMAINS = [2, 3, 12, 18, 42, 82]

    @cached(source="ths", source_name="同花顺", domain="news", frequency="realtime", market="a_share", ttl=300)
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
        import random
        for attempt in range(3):
            subdomain = random.choice(self.PUSH2_SUBDOMAINS)
            url = f"http://{subdomain}.push2.eastmoney.com/api/qt/clist/get"
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

    # ========== 新闻 (news.10jqka.com.cn) ==========

    @cached(source="ths", source_name="同花顺", domain="news", frequency="realtime", market="a_share", ttl=300)
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

    @cached(source="ths", source_name="同花顺", domain="news", frequency="realtime", market="a_share", ttl=300)
    async def get_article_detail(self, encoded_seq: str) -> dict:
        """获取新闻文章详情（type=1 新闻）
        encoded_seq: encoded 格式的文章ID，或纯数字 seq（会自动转换）
        """
        if encoded_seq.isdigit():
            encoded_seq = self.seq_to_encoded(int(encoded_seq))
        url = f"{self.NEWS_BASE}/mobile_api/news/article/v1/encoded/{encoded_seq}"
        return await self._get(url)

    @cached(source="ths", source_name="同花顺", domain="news", frequency="realtime", market="a_share", ttl=300)
    async def get_news_themes(self) -> dict:
        """获取新闻主题分类列表（资讯→头条 tab 栏的主题标签）"""
        url = f"{self.NEWS_BASE}/app/headline/v1/hot-theme"
        return await self._get(url)

    @cached(source="ths", source_name="同花顺", domain="news", frequency="realtime", market="a_share", ttl=300)
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

    @cached(source="ths", source_name="同花顺", domain="news", frequency="realtime", market="a_share", ttl=300)
    async def get_flash_news_tabs(self) -> dict:
        """获取快讯分类标签列表（A股、重要、公告、期货、异动、港股、美股）"""
        url = f"{self.NEWS_BASE}/app/flash/flashnews/v2/tab"
        return await self._get(url)

    @cached(source="ths", source_name="同花顺", domain="news", frequency="realtime", market="a_share", ttl=300)
    async def get_flash_news_list(self, tag_id: int = 21101, seq: int = 0) -> dict:
        """获取指定分类的快讯列表
        tag_id: 分类ID（从 get_flash_news_tabs 获取），默认21101=A股
        seq: 翻页游标，0=最新，传入上一页最后一条的 seq 加载更早的
        """
        url = f"{self.NEWS_BASE}/app/flash/flashnews/v1/list"
        return await self._get(url, params={"tagId": tag_id, "seq": seq})

    @cached(source="ths", source_name="同花顺", domain="news", frequency="realtime", market="a_share", ttl=300)
    async def get_news_feed(self, page: int = 1) -> dict:
        """滚动快讯（财经要闻实时滚动，每页20条）"""
        url = f"{self.NEWS_BASE}/tapp/news/push/stock/"
        return await self._get(url, params={"page": page})

    @cached(source="ths", source_name="同花顺", domain="news", frequency="realtime", market="a_share", ttl=300)
    async def get_topic_detail(self, code: str, page: int = 1, page_size: int = 10) -> dict:
        """话题详情（含推荐帖子列表）"""
        info_url = f"{self.HOT_TOPIC_BASE}/lgt/topic/open/api/topic_info/v1/topic?code={code}"
        feed_url = f"{self.HOT_TOPIC_BASE}/lgt/topic/open/api/topic_info/v3/recommend_list?code={code}&page={page}&pageSize={page_size}"
        info_resp, feed_resp = await asyncio.gather(
            self._get(info_url), self._get(feed_url)
        )
        return {"topic": info_resp.get("data", {}), "feeds": feed_resp.get("data", {})}

    @cached(source="ths", source_name="同花顺", domain="news", frequency="realtime", market="a_share", ttl=300)
    async def get_special_detail(self, code: str) -> dict:
        """专题详情（从 HTML 解析组件内容）"""
        import json as _json
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

    @cached(source="ths", source_name="同花顺", domain="sentiment", frequency="daily", market="a_share", ttl=3600)
    async def get_limit_pool(self, pool_type: str = "up") -> dict:
        """获取涨停/跌停池 (data.10jqka.com.cn)

        Args:
            pool_type: "up" 涨停池, "down" 跌停池
        Returns:
            同花顺原始 JSON
        """
        endpoint = "limit_up_pool" if pool_type == "up" else "lower_limit_pool"
        headers = {
            "Referer": "https://data.10jqka.com.cn/",
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
        }
        resp = await self._client.get(
            f"{self.THS_DATA}/dataapi/limit_up/{endpoint}/",
            params={"page": 1, "limit": 15,
                    "field": "199112,10,9001,330323,330324,330325,9002,330329,133971,133970,1968584,3475914",
                    "order_field": "330324", "order_type": "0"},
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()


    # ==================== 问财 (iwencai) ====================

    # 问财请求频率控制：最少间隔 60 秒，防止触发验证码
    _iwencai_last_request = 0.0
    _IWENCAI_MIN_INTERVAL = 60.0

    @cached(source="ths", source_name="同花顺", domain="sentiment", frequency="realtime", market="a_share", ttl=60)
    async def get_iwencai_query(self, question: str, perpage: int = 10, page: int = 1,
                                secondary_intent: str = "stock") -> dict:
        """问财自然语言选股查询（依赖数据库中的 hexin-v token）

        认证token由Zygisk Hook自动从同花顺App的WebView Cookie DB读取并上报到服务端数据库。
        每次请求实时从DB读取最新token，确保与Hook上报的token一致。
        服务端每次请求间隔≥60秒，防止触发验证码。

        Args:
            question: 自然语言问题（如"今日涨停的股票"、"AI概念股"）
            perpage: 每页条数
            page: 页码
            secondary_intent: 意图类型（stock/zhishu/fund）

        Returns:
            原始 iwencai API 响应，或包含 error 字段的 dict
        """
        import time as _time
        from src.infrastructure.db import fund_db

        # 频率限制
        now = _time.time()
        elapsed = now - THSClient._iwencai_last_request
        if elapsed < self._IWENCAI_MIN_INTERVAL:
            wait = self._IWENCAI_MIN_INTERVAL - elapsed
            return {"error": f"问财请求频率限制，请在 {wait:.0f} 秒后重试"}

        # 每次从 DB 读取最新 token（Hook 可能随时更新）
        cookies_data = fund_db.get_iwencai_cookies()
        hexin_v = cookies_data.get("hexin_v", "")
        if not hexin_v:
            return {"error": "hexin-v token 未配置，请先在手机端打开同花顺App触发上报"}

        # 构建完整 cookie（模拟真实浏览器会话）
        cookie_parts = [f"v={hexin_v}"]
        for key in ("userid", "cuc", "ticket", "sess_tk"):
            val = cookies_data.get(key, "")
            if val:
                cookie_parts.append(f"{key}={val}")

        add_info = (
            '{"urp":{"scene":1,"company":1,"business":1},'
            '"content_type":"' + secondary_intent + '",'
            '"search_cat":"' + secondary_intent + '"}'
        )

        THSClient._iwencai_last_request = _time.time()

        resp = await self._client.post(
            "https://www.iwencai.com/customized/chart/get-robot-data",
            json={
                "question": question,
                "perpage": perpage,
                "page": page,
                "source": "Ths_iwencai_Xuangu",
                "version": "2.0",
                "secondary_intent": secondary_intent,
                "add_info": add_info,
            },
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
                "Cookie": "; ".join(cookie_parts),
                "Referer": "https://www.iwencai.com/unifiedwap/home/index",
                "hexin-v": hexin_v,
            },
        )
        data = resp.json()

        # 检测验证码
        if data.get("data", {}).get("captcha_url") or data.get("code") == -2:
            return {"error": "问财触发验证码，hexin-v token 已失效，需要重新打开同花顺App刷新",
                    "captcha": True}

        if resp.status_code == 401:
            return {"error": "问财认证失败(401)，token可能已过期", "captcha": True}

        return data

    @cached(source="ths", source_name="同花顺", domain="sentiment", frequency="realtime", market="a_share", ttl=60)
    async def get_iwencai_stocks(self, question: str, limit: int = 10) -> list:
        """问财选股 — 提取结构化的股票列表

        Returns:
            [{"code": "605389.SH", "name": "长龄液压", ...}, ...]
        """
        raw = await self.get_iwencai_query(question, perpage=limit)
        if "error" in raw:
            return []

        results = []
        for answer in raw.get("data", {}).get("answer", []):
            for txt_item in answer.get("txt", []):
                content = txt_item.get("content", {})
                if not isinstance(content, dict):
                    continue
                for comp in content.get("components", []):
                    datas = comp.get("data", {}).get("datas", [])
                    for row in datas:
                        code = row.get("股票代码", "")
                        name = row.get("股票简称", "")
                        if code and name:
                            results.append(row)
        return results[:limit]


if __name__ == "__main__":
    import os
    import asyncio

    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        os.environ.pop(key, None)

    TEST_FUND = "110022"

    async def main():
        client = THSClient()
        try:
            # 基金数据
            print("=== 基金数据 ===")
            detail = await client.get_fund_detail(TEST_FUND)
            print(f"  基金详情: {bool(detail)}")

            holdings = await client.get_top10_holdings(TEST_FUND)
            stocks = holdings.get("data", {}).get("stock", [])
            print(f"  前十持仓: {len(stocks)}只")

            # 新闻
            print("\n=== 新闻资讯 ===")
            headlines = await client.get_headlines()
            print(f"  头条: {len(headlines.get('data', []))}条")

            flash = await client.get_flash_news_list()
            print(f"  快讯: {bool(flash)}")

            feed = await client.get_news_feed()
            print(f"  Feed: {bool(feed)}")

            # 热榜
            print("\n=== 热榜 ===")
            hot_stocks = await client.get_hot_stocks()
            print(f"  热股: {bool(hot_stocks)}")

            hot_plate = await client.get_hot_plate()
            print(f"  热板块: {bool(hot_plate)}")

            # 龙虎榜/涨停
            print("\n=== 市场数据 ===")
            limit_up = await client.get_limit_pool("up")
            count = limit_up.get("data", {}).get("page", {}).get("total", 0)
            print(f"  涨停池: {count}只")

            # 基金排行
            print("\n=== 基金排行 ===")
            ranking = await client.get_fund_ranking(sort_type="year")
            print(f"  基金排行: {bool(ranking)}")

        finally:
            await client.close()

    asyncio.run(main())
