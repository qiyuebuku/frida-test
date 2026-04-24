"""各事件类型的 system prompt 后缀 — 与 base.SYSTEM_PREFIX 拼接使用"""
from src.domain.extraction.services.l1a.prompts.base import SYSTEM_PREFIX

# 各 event_type 的专属 prompt 后缀
TYPE_SUFFIXES: dict[str, str] = {
    "policy": """

## 当前类别：政策事件
聚焦：央行/证监会/发改委/财政部等监管机构的政策发布、法规变更、行政命令。
特别注意：政策方向、影响行业、受益/受损方、实施时间线。
示例 event_subtype: rate_cut, reserve_ratio_change, subsidy, regulation_relax, regulation_tighten
""",
    "earnings": """

## 当前类别：业绩事件
聚焦：上市公司财报、业绩预告、盈利预警、分红方案。
特别注意：实际值 vs 预期值差异、同比/环比变化、行业对比。
示例 event_subtype: earnings_beat, earnings_miss, earnings_warning, dividend_increase
""",
    "m&a": """

## 当前类别：并购重组事件
聚焦：收购、合并、资产重组、控股权变更。
特别注意：交易金额、标的资产、参与方、对行业格局的影响。
示例 event_subtype: acquisition, merger, restructuring, control_change, buyout
""",
    "announcement": """

## 当前类别：公司公告事件
聚焦：股东增减持、回购、股权激励、诉讼、质押、定增、解禁。
特别注意：公告对股价的直接影响、大股东动向、流通盘变化。
示例 event_subtype: insider_buy, insider_sell, buyback, lockup_expiry, litigation, private_placement
""",
    "shock": """

## 当前类别：突发事件
聚焦：自然灾害、事故、制裁、地缘冲突、疫情等意外事件。
特别注意：受影响的供应链/产能、持续时间、替代方案。
示例 event_subtype: natural_disaster, accident, sanctions, geopolitics, pandemic, supply_disruption
""",
    "industry": """

## 当前类别：行业动态事件
聚焦：行业层面的价格变动、产能变化、技术突破、新产品发布、竞争格局变化。
特别注意：对行业内上市公司的影响范围、持续时间。
示例 event_subtype: price_change, capacity_change, tech_breakthrough, new_product, competition_shift
""",
    "macro_data": """

## 当前类别：宏观经济数据事件
聚焦：CPI/PPI/PMI/GDP/M2/社融/进出口等宏观数据发布。
特别注意：实际值 vs 预期值、趋势方向、对货币政策和市场的影响。
示例 event_subtype: cpi_release, ppi_release, pmi_release, gdp_release, credit_data, trade_data
""",
    "other": """

## 当前类别：其他事件
不属于以上 7 类但有交易相关性的财经事件。
尽量给出明确的 event_subtype。
""",
}


def get_system_prompt(event_type: str) -> str:
    """获取完整的 system prompt（前缀 + 类型后缀）"""
    suffix = TYPE_SUFFIXES.get(event_type, TYPE_SUFFIXES["other"])
    return SYSTEM_PREFIX + suffix
