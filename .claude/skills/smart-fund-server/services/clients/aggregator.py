"""聚合接口客户端 - 调用多个数据源组合数据"""

import asyncio
from datetime import datetime, timedelta

from services.clients.ths import THSClient
from services.clients.eastmoney import EastmoneyClient
from services.clients.sina import SinaClient
from services.clients.tencent import TencentClient


class AggregatorClient:
    """聚合接口 - 调用多个数据源组合数据"""

    def __init__(self, ths: THSClient, eastmoney: EastmoneyClient, sina: SinaClient, tencent: TencentClient):
        self.ths = ths
        self.eastmoney = eastmoney
        self.sina = sina
        self.tencent = tencent

    async def get_holdings_valuation(self, fund_code: str) -> dict:
        """获取前十大持仓股的估值数据（PE/PB/市值）
        数据来源: 同花顺持仓 + 腾讯证券 web.sqt.gtimg.cn 公开行情 API
        """
        holdings = await self.ths.get_top10_holdings(fund_code)
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
            raw = await self.tencent.get_stock_batch_quote(query_codes)
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

    async def get_holdings_valuation_percentile(self, fund_code: str, years: int = 3) -> dict:
        """获取前十大持仓的估值百分位
        1. 获取持仓列表 + 当前 PE/PB
        2. 并发获取每只股票的历史估值
        3. 计算百分位并返回汇总
        """
        # 并发获取持仓和当前估值
        holdings_task = self.ths.get_top10_holdings(fund_code)
        valuation_task = self.get_holdings_valuation(fund_code)
        holdings_data, valuation_data = await asyncio.gather(holdings_task, valuation_task)

        stocks = holdings_data.get("data", {}).get("stock", [])
        val_list = valuation_data.get("data", [])
        val_map = {v["secCode"]: v for v in val_list if v.get("secCode")}

        if not stocks:
            return {"status_code": 0, "data": {"stocks": [], "summary": {}}}

        # 并发获取历史估值
        stock_codes = [s.get("secCode", "") for s in stocks if s.get("secCode")]
        history_tasks = [self.eastmoney.get_stock_valuation_history(code, years) for code in stock_codes]
        history_results = await asyncio.gather(*history_tasks, return_exceptions=True)
        history_map = {}
        for code, result in zip(stock_codes, history_results):
            if isinstance(result, list):
                history_map[code] = result

        def _percentile(current_val, history, field):
            """计算百分位: 历史中 <= 当前值的比例"""
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

    async def get_market_environment(self) -> dict:
        """获取沪深300/创业板指趋势 + 北向资金 + 融资融券 + 国债收益率
        数据来源: 东方财富 push2his + datacenter
        """

        beg_date = (datetime.now() - timedelta(days=400)).strftime("%Y%m%d")

        # 并发获取所有数据
        hs300_raw, cyb_raw, nb_raw, margin_raw, bond_raw = await asyncio.gather(
            self.eastmoney.get_index_kline("1.000300", beg_date),   # 沪深300
            self.eastmoney.get_index_kline("0.399006", beg_date),   # 创业板指
            self.eastmoney.get_northbound_recent(),
            self.eastmoney.get_margin_recent(),
            self.eastmoney.get_index_kline("1.511260", beg_date),   # 十年国债ETF
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

    async def get_market_overview(self) -> dict:
        """A 股大盘总览：指数行情 + 涨跌家数 + 成交额 + 资金流向 + 涨跌停 + 大小盘对比
        数据来源: 东方财富 push2 + 同花顺 data
        """

        INDEX_SECIDS = {
            "上证指数": "1.000001", "深证成指": "0.399001", "创业板指": "0.399006",
            "沪深300": "1.000300", "上证50": "1.000016", "中证500": "1.000905",
            "中证1000": "0.399852", "国证2000": "0.399303", "北证50": "0.899050",
        }

        secids = ",".join([
            INDEX_SECIDS["上证指数"], INDEX_SECIDS["深证成指"],
            INDEX_SECIDS["创业板指"], INDEX_SECIDS["沪深300"],
            INDEX_SECIDS["上证50"], INDEX_SECIDS["中证500"],
            INDEX_SECIDS["中证1000"], INDEX_SECIDS["国证2000"],
            INDEX_SECIDS["北证50"],
        ])

        # 并发获取所有数据
        indices_raw, flow_sh_raw, flow_sz_raw, limit_up_raw, limit_down_raw = await asyncio.gather(
            self.eastmoney.get_indices_realtime(secids),
            self.eastmoney.get_index_capital_flow_daily("1.000001"),   # 沪市资金流
            self.eastmoney.get_index_capital_flow_daily("0.399001"),   # 深市资金流
            self.ths.get_limit_pool("up"),
            self.ths.get_limit_pool("down"),
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
