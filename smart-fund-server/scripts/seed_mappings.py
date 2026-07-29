"""填充 3 张映射表（行业 → 指数 → 基金）的种子数据

可重复执行（ON CONFLICT DO UPDATE）。

数据来源：
- 行业关键词：参考申万/中证一级行业分类 + 主题概念
- 行业指数：选择最广为人知的代表性指数
- 跟踪基金：优先选择 ETF（流动性好），费率低 + 规模大 + 跟踪误差小

用法：
    python scripts/seed_mappings.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json
import psycopg2
from src.infrastructure.config.settings import DB_CONFIG


# ==================== 数据 ====================

# Industry → Keywords + Aliases
# (industry_id, industry_name, keywords[], aliases[], priority)
INDUSTRIES = [
    # ── 科技成长 ──────────────────────────────
    ("semiconductor", "半导体",
     ["半导体", "芯片", "集成电路", "晶圆", "封测", "光刻", "EDA", "存储芯片", "GPU", "MCU"],
     ["IC", "chip"], 0.9),
    ("ai", "人工智能",
     ["人工智能", "AI", "大模型", "AIGC", "ChatGPT", "Transformer", "机器学习", "深度学习", "算力"],
     ["LLM", "生成式AI"], 0.95),
    ("computing_power", "算力",
     ["算力", "数据中心", "IDC", "服务器", "液冷", "光模块", "CPO", "光通信"],
     ["AIDC"], 0.85),
    ("comm_5g", "5G通信",
     ["5G", "通信设备", "基站", "运营商", "光纤光缆", "天线", "RF", "射频"],
     ["6G"], 0.6),
    ("software", "软件",
     ["软件", "SaaS", "工业软件", "操作系统", "数据库", "中间件", "信创"],
     [], 0.7),

    # ── 新能源 ──────────────────────────────
    ("new_energy_vehicle", "新能源车",
     ["新能源车", "电动车", "新能源汽车", "智能驾驶", "自动驾驶", "蔚来", "理想", "小鹏", "比亚迪", "特斯拉"],
     ["NEV", "EV", "电车"], 0.85),
    ("power_battery", "动力电池",
     ["动力电池", "锂电池", "宁德时代", "电解液", "正极材料", "负极材料", "隔膜", "钠电池", "固态电池"],
     ["电池"], 0.85),
    ("photovoltaic", "光伏",
     ["光伏", "硅料", "硅片", "电池片", "组件", "逆变器", "钙钛矿", "TOPCon", "HJT"],
     ["太阳能"], 0.8),
    ("wind_power", "风电",
     ["风电", "风机", "海上风电", "陆上风电", "风塔", "叶片"],
     [], 0.7),

    # ── 消费 ──────────────────────────────
    ("liquor", "白酒",
     ["白酒", "茅台", "五粮液", "泸州老窖", "汾酒", "次高端", "高端白酒"],
     ["酒"], 0.7),
    ("medicine", "医药",
     ["医药", "创新药", "CXO", "生物医药", "中药", "化学药", "原料药", "医疗器械", "疫苗", "GLP-1"],
     ["医疗", "biotech"], 0.85),
    ("food_beverage", "食品饮料",
     ["食品饮料", "调味品", "啤酒", "乳制品", "肉制品", "速冻食品", "零食"],
     [], 0.6),

    # ── 金融周期 ──────────────────────────────
    ("bank", "银行",
     ["银行", "国有大行", "股份制银行", "城商行", "工商银行", "建设银行", "招商银行", "宁波银行"],
     [], 0.7),
    ("securities", "券商",
     ["券商", "证券", "投行", "中信证券", "华泰证券", "国泰君安"],
     ["证券公司"], 0.75),
    ("insurance", "保险",
     ["保险", "中国平安", "中国人寿", "新华保险", "保险股"],
     [], 0.6),
    ("real_estate", "房地产",
     ["房地产", "地产", "万科", "保利", "新房", "二手房", "REITs", "保障房"],
     ["地产股"], 0.65),

    # ── 资源 ──────────────────────────────
    ("nonferrous", "有色金属",
     ["有色金属", "铜", "铝", "铅锌", "黄金", "白银", "稀土", "锂矿", "钴", "镍"],
     ["金属"], 0.75),
    ("oil_gas", "油气",
     ["原油", "石油", "天然气", "中石油", "中石化", "OPEC", "页岩气"],
     ["油价"], 0.7),
    ("coal", "煤炭",
     ["煤炭", "动力煤", "焦煤", "煤价", "中国神华"],
     [], 0.6),

    # ── 国防军工 ──────────────────────────────
    ("defense", "军工",
     ["军工", "国防", "航天", "航空", "导弹", "战机", "卫星", "北斗", "无人机"],
     ["国防工业"], 0.7),

    # ── 农业/化工 ──────────────────────────────
    ("agriculture", "农业",
     ["农业", "种业", "种子", "化肥", "农药", "猪肉", "养殖", "饲料"],
     ["三农"], 0.5),
    ("chemical", "化工",
     ["化工", "基础化工", "化学原料", "纯碱", "塑料", "钛白粉", "聚氨酯", "MDI"],
     ["chemicals"], 0.6),

    # ── 宽基（事件无明确行业时打到大盘） ──────────────────────────────
    ("broad_csi300", "沪深300",
     ["沪深300", "蓝筹", "大盘", "权重股"],
     [], 0.4),
    ("broad_csi500", "中证500",
     ["中证500", "中盘"],
     [], 0.3),
    ("broad_chinext", "创业板",
     ["创业板", "成长股"],
     [], 0.4),
    ("broad_sci_tech", "科创板",
     ["科创板", "硬科技"],
     [], 0.5),
]


# Industry → Index
# (industry_id, index_code, index_name, weight)
INDUSTRY_INDEX = [
    # 科技
    ("semiconductor", "980017", "国证半导体", 1.0),
    ("ai", "930713", "中证人工智能AI", 1.0),
    ("computing_power", "931007", "中证云计算与大数据主题", 1.0),
    ("comm_5g", "931079", "中证5G通信主题", 1.0),
    ("software", "931476", "中证软件服务", 1.0),

    # 新能源
    ("new_energy_vehicle", "399976", "中证新能源汽车", 1.0),
    ("power_battery", "931719", "中证电池主题", 1.0),
    ("photovoltaic", "931151", "中证光伏产业", 1.0),
    ("wind_power", "931625", "中证风电产业", 1.0),

    # 消费
    ("liquor", "399997", "中证白酒", 1.0),
    ("medicine", "399933", "中证医药", 1.0),
    ("food_beverage", "000932", "中证消费", 1.0),

    # 金融
    ("bank", "399986", "中证银行", 1.0),
    ("securities", "399975", "中证全指证券公司", 1.0),
    ("insurance", "399809", "中证全指保险", 1.0),
    ("real_estate", "399393", "国证地产", 1.0),

    # 资源
    ("nonferrous", "399395", "国证有色", 1.0),
    ("oil_gas", "000067", "中证石油天然气", 1.0),
    ("coal", "399998", "中证煤炭", 1.0),

    # 国防
    ("defense", "399967", "中证军工", 1.0),

    # 农化
    ("agriculture", "930707", "中证主要消费", 0.5),
    ("chemical", "000813", "中证细分化工", 1.0),

    # 宽基
    ("broad_csi300", "000300", "沪深300", 1.0),
    ("broad_csi500", "000905", "中证500", 1.0),
    ("broad_chinext", "399006", "创业板指", 1.0),
    ("broad_sci_tech", "000688", "科创50", 1.0),
]


# Index → Fund
# (index_code, fund_code, fund_name, fund_type, fee_rate, liquidity_score)
INDEX_FUND = [
    # 半导体
    ("980017", "159995", "华夏国证半导体ETF", "ETF", 0.005, 0.95),
    ("980017", "512480", "国泰CES半导体芯片ETF", "ETF", 0.005, 0.85),

    # AI
    ("930713", "515980", "华夏中证人工智能ETF", "ETF", 0.005, 0.85),
    ("930713", "515070", "华泰柏瑞中证人工智能ETF", "ETF", 0.005, 0.80),

    # 算力/云计算
    ("931007", "159739", "华夏中证云50ETF", "ETF", 0.005, 0.70),

    # 5G通信
    ("931079", "515050", "华夏中证5G通信主题ETF", "ETF", 0.005, 0.85),

    # 软件服务
    ("931476", "515230", "国联安中证软件ETF", "ETF", 0.005, 0.65),

    # 新能源车
    ("399976", "515030", "华夏国证新能源车ETF", "ETF", 0.005, 0.85),
    ("399976", "159806", "国泰中证新能源汽车ETF", "ETF", 0.005, 0.80),

    # 锂电池
    ("931719", "159755", "广发中证电池主题ETF", "ETF", 0.005, 0.80),

    # 光伏
    ("931151", "516880", "华夏中证光伏产业ETF", "ETF", 0.005, 0.80),
    ("931151", "515790", "华泰柏瑞中证光伏产业ETF", "ETF", 0.005, 0.85),

    # 风电
    ("931625", "159864", "华泰柏瑞中证风电产业ETF", "ETF", 0.005, 0.65),

    # 白酒
    ("399997", "512690", "鹏华中证酒ETF", "ETF", 0.005, 0.85),
    ("399997", "161725", "招商中证白酒指数LOF", "LOF", 0.012, 0.80),

    # 医药
    ("399933", "159929", "汇添富中证医药ETF", "ETF", 0.005, 0.80),
    ("399933", "512010", "易方达沪深300医药ETF", "ETF", 0.005, 0.75),

    # 食品消费
    ("000932", "159928", "汇添富中证主要消费ETF", "ETF", 0.005, 0.80),

    # 银行
    ("399986", "512800", "华宝中证银行ETF", "ETF", 0.005, 0.85),
    ("399986", "515020", "华夏中证银行ETF", "ETF", 0.005, 0.75),

    # 券商
    ("399975", "512000", "华宝中证全指证券公司ETF", "ETF", 0.005, 0.95),
    ("399975", "512880", "国泰中证全指证券公司ETF", "ETF", 0.005, 0.85),

    # 保险
    ("399809", "512070", "易方达中证全指保险ETF", "ETF", 0.005, 0.55),

    # 地产
    ("399393", "512200", "南方中证全指房地产ETF", "ETF", 0.005, 0.60),

    # 有色
    ("399395", "512400", "南方中证申万有色金属ETF", "ETF", 0.005, 0.85),

    # 油气
    ("000067", "159699", "广发中证全指能源ETF", "ETF", 0.005, 0.55),

    # 煤炭
    ("399998", "515220", "国泰中证煤炭ETF", "ETF", 0.005, 0.80),

    # 军工
    ("399967", "512660", "国泰中证军工ETF", "ETF", 0.005, 0.85),
    ("399967", "512680", "广发中证军工ETF", "ETF", 0.005, 0.80),

    # 化工
    ("000813", "159870", "鹏华国证细分化工产业ETF", "ETF", 0.005, 0.70),

    # 宽基
    ("000300", "510330", "华夏沪深300ETF", "ETF", 0.005, 0.95),
    ("000300", "510300", "华泰柏瑞沪深300ETF", "ETF", 0.005, 0.95),
    ("000905", "510500", "南方中证500ETF", "ETF", 0.005, 0.95),
    ("000905", "159922", "嘉实中证500ETF", "ETF", 0.005, 0.85),
    ("399006", "159915", "易方达创业板ETF", "ETF", 0.005, 0.95),
    ("000688", "588000", "华夏科创50ETF", "ETF", 0.005, 0.95),
    ("000688", "588080", "易方达科创板50ETF", "ETF", 0.005, 0.85),
]


# ==================== 主流程 ====================


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    cur = conn.cursor()

    print(f"== 开始填充 {len(INDUSTRIES)} 个行业 / {len(INDUSTRY_INDEX)} 条 industry-index / {len(INDEX_FUND)} 条 index-fund ==\n")

    # 1. ft_industry_mapping
    for industry_id, name, keywords, aliases, priority in INDUSTRIES:
        cur.execute(
            """
            INSERT INTO ft_industry_mapping (industry_id, industry_name, keywords, aliases, priority)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (industry_id) DO UPDATE SET
                industry_name = EXCLUDED.industry_name,
                keywords = EXCLUDED.keywords,
                aliases = EXCLUDED.aliases,
                priority = EXCLUDED.priority
            """,
            (industry_id, name, json.dumps(keywords, ensure_ascii=False),
             json.dumps(aliases, ensure_ascii=False), priority),
        )
    print(f"✅ ft_industry_mapping: {len(INDUSTRIES)} 条")

    # 2. ft_industry_index
    for industry_id, index_code, index_name, weight in INDUSTRY_INDEX:
        cur.execute(
            """
            INSERT INTO ft_industry_index (industry_id, index_code, index_name, weight)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (industry_id, index_code) DO UPDATE SET
                index_name = EXCLUDED.index_name,
                weight = EXCLUDED.weight
            """,
            (industry_id, index_code, index_name, weight),
        )
    print(f"✅ ft_industry_index: {len(INDUSTRY_INDEX)} 条")

    # 3. ft_index_fund
    for index_code, fund_code, fund_name, fund_type, fee_rate, liquidity in INDEX_FUND:
        cur.execute(
            """
            INSERT INTO ft_index_fund (index_code, fund_code, fund_name, fund_type, fee_rate, liquidity_score)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (index_code, fund_code) DO UPDATE SET
                fund_name = EXCLUDED.fund_name,
                fund_type = EXCLUDED.fund_type,
                fee_rate = EXCLUDED.fee_rate,
                liquidity_score = EXCLUDED.liquidity_score
            """,
            (index_code, fund_code, fund_name, fund_type, fee_rate, liquidity),
        )
    print(f"✅ ft_index_fund: {len(INDEX_FUND)} 条")

    # 校验：每个 industry 都有指数
    cur.execute("""
        SELECT m.industry_id, m.industry_name
        FROM ft_industry_mapping m
        LEFT JOIN ft_industry_index i USING (industry_id)
        WHERE i.industry_id IS NULL
    """)
    orphans = cur.fetchall()
    if orphans:
        print(f"\n⚠️  {len(orphans)} 个 industry 缺指数:")
        for r in orphans:
            print(f"   - {r[0]} ({r[1]})")

    # 校验：每个 index 都有基金
    cur.execute("""
        SELECT i.industry_id, i.index_code, i.index_name
        FROM ft_industry_index i
        LEFT JOIN ft_index_fund f USING (index_code)
        WHERE f.index_code IS NULL
    """)
    no_funds = cur.fetchall()
    if no_funds:
        print(f"\n⚠️  {len(no_funds)} 个 index 缺基金:")
        for r in no_funds:
            print(f"   - {r[1]} {r[2]} ({r[0]})")

    cur.close()
    conn.close()
    print("\n== 完成 ==")


if __name__ == "__main__":
    main()
