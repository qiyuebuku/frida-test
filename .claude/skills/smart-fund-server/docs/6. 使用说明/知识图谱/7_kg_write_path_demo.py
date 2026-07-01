#!/usr/bin/env python3
"""演示：Milvus 语义检索与 PG 关系展开写入链路。

本脚本对应实施方案：

    docs/3. 实施方案/4. 知识图谱/11. Milvus语义检索与PG关系展开写入链路实施方案.md

它演示的不是“检索效果”，而是写入链路是否按目标架构落地：

    Source Record
      -> Evidence
      -> Evidence Chunk manifest
      -> Milvus chunk target
      -> Cognitive Card
      -> Community Assignment
      -> Community Card / Milvus community target

每一步的作用：

- Step 0：打印本次演示配置，并检查数据库 schema 是否已经是最新写入链路结构。
  所有参数都写死在脚本顶部，不使用命令行参数。要切换 target、dry-run、是否全量重建，直接改常量。

- Step 0.6：清理上一次同一受控 source 前缀写入的数据。
  这一步只清理 `usage_demo_write_path` 前缀相关 PG 事实、Graph Index 派生行和 Milvus target，
  保证每次执行都能验证本次新写入结果。

- Step 1：编译并写入受控新闻 Source Record。
  这一步会走真实 KnowledgeService.compile_kg()，不是 mock。当前项目尚未上线，演示脚本直接写入 prod target。

- Step 2：检查 PG 事实层。
  重点看 evidence、chunk manifest、Cognitive Card、Community Assignment 和 Community Card 是否存在。
  PG 只保存事实和指针；chunk 可读全文主要由 Milvus target 承担。

- Step 3：检查 Graph Index 状态。
  重点看 Cognitive Card 驱动的 community 是否已经写入，并保留 chunk/evidence refs。

- Step 4：按 target_id 从 Milvus 精准取回写入的 chunk / community target。
  这一步证明 Milvus 不只是语义搜索，也能按 PG refs 精准取回可读 target。

- Step 5：用语义查询检查 Milvus 多集合入口。
  这一步同时输出全局召回和本次 demo scope 召回，避免历史数据污染写入链路质量判断。

- Step 6：可选全量重建语义索引和 Graph Index。
  正常写入已经在 compile 阶段刷新 Cognitive Community Index。
  旧 Graph Index 显式构建仅作为手动回归入口，默认关闭，避免覆盖 Cognitive Community 结果。
  如果要演示全量语义索引重建，把 RUN_FULL_SEMANTIC_REBUILD 改为 True。

- Step 7：写出 generated_write_path_demo.json。
  终端输出只展示摘要，完整结构化结果写入 JSON，方便后续对比。

运行方式：

    python "docs/6. 使用说明/知识图谱/7_kg_write_path_demo.py"

不需要命令行参数。需要调整演示行为，修改下面的常量即可。
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path
from pprint import pprint
from typing import Any

import redis
from sqlalchemy import delete, func, inspect, or_, select

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)


def _project_root() -> Path:
    root = Path(__file__).resolve()
    while root.name != "smart-fund-server" and root.parent != root:
        root = root.parent
    if root.name != "smart-fund-server":
        raise RuntimeError("cannot locate smart-fund-server project root")
    return root


PROJECT_ROOT = _project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.application.dto.knowledge_dto import (  # noqa: E402
    KnowledgeCompileCommand,
    KnowledgeRebuildIndexesCommand,
)
from src.application.services.knowledge_llm_config import kg_llm_config_summary  # noqa: E402
from src.application.services.knowledge_service import create_knowledge_service  # noqa: E402
from src.domain.knowledge.retrieval import RetrievalOptions  # noqa: E402
from src.domain.knowledge.semantic_index_materials import (  # noqa: E402
    SEMANTIC_COLLECTION_ENTITY,
    SEMANTIC_COLLECTION_RELATION,
)
from src.domain.knowledge_adapters.financial.source_projection import project_ft_news_row  # noqa: E402
from src.infrastructure.connections import get_session  # noqa: E402
from src.infrastructure.config.settings import REDIS_URL  # noqa: E402
from src.infrastructure.llm_proxy.service import get_llm_gateway_service  # noqa: E402
from src.infrastructure.observability.langfuse_tracing import (  # noqa: E402
    langfuse_flush,
    langfuse_observation,
    langfuse_propagation_context,
    langfuse_update_span,
)
from src.infrastructure.persistence.models.knowledge import (  # noqa: E402
    KnowledgeAssignmentCandidateOrder,
    KnowledgeCognitiveCard,
    KnowledgeCommunityAssignment,
    KnowledgeEdge,
    KnowledgeEdgeEvidence,
    KnowledgeEdgeEvidenceChunk,
    KnowledgeEvidence,
    KnowledgeEvidenceChunk,
    KnowledgeGraphAdjacency,
    KnowledgeGraphCommunity,
    KnowledgeGraphDelta,
    KnowledgeGraphFinding,
    KnowledgeGraphUnassignedSignal,
    KnowledgeNode,
    KnowledgeNormalizationRule,
)
from src.infrastructure.persistence.models.collection import News  # noqa: E402
from src.infrastructure.vector_store.semantic_hybrid_retriever import (  # noqa: E402
    MilvusSemanticHybridRetriever,
)


OUTPUT_FILE = Path(__file__).with_name("generated_write_path_demo.json")

TARGET = "prod"
ADAPTER = "financial"
CONCURRENCY = 1
DRY_RUN = False

# 默认不做全量重建。全量重建会重新处理当前 target 的语义索引，可能调用 embedding 服务较久。
RUN_FULL_SEMANTIC_REBUILD = False

# compile_kg 已刷新 Cognitive Community Index。旧 Graph Index 显式构建只保留为手动回归入口。
RUN_GRAPH_INDEX_BUILD = False

DEMO_PREFIX = "usage_demo_write_path"
DEMO_SCENARIO = "real_ft_news_community_80_20260628"
DEMO_TS = "2026-05-20T09:30:00+08:00"
RUN_SESSION_ID = f"kg-write-demo:{DEMO_SCENARIO}:{int(time.time())}"

USE_REAL_FT_NEWS = True
USE_HARDCODED_FT_NEWS = True
FT_NEWS_CANDIDATE_LIMIT = 800
FT_NEWS_RECORD_LIMIT = 80
FT_NEWS_MIN_TEXT_CHARS = 120

VERIFY_QUERY = "最近市场对AI算力链、半导体、新能源和并购重组的主要叙事、机会与风险分别是什么？"
SEMANTIC_LIMIT = 12
MILVUS_GET_LIMIT = 12
STEP_HEARTBEAT_SECONDS = 15

FT_NEWS_REPRESENTATIVE_BUCKETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("merger_restructuring", ("并购", "重组", "收购", "资产注入", "控制权", "重大资产")),
    ("ai_compute_chain", ("AI", "算力", "数据中心", "光模块", "GPU", "液冷", "服务器")),
    ("semiconductor_policy", ("半导体", "芯片", "科创板", "国产替代", "先进封装", "晶圆")),
    ("new_energy_capacity", ("新能源", "锂电", "固态电池", "光伏", "储能", "产能", "电池")),
    ("overseas_supply_chain", ("出海", "海外", "关税", "制裁", "出口管制", "供应链")),
    ("policy_regulation", ("政策", "监管", "证监会", "发改委", "工信部", "央行", "国务院")),
    ("market_risk", ("风险", "下跌", "减持", "商誉", "亏损", "违约", "退市", "承压")),
    ("broker_capital_market", ("券商", "投行", "融资", "估值", "资本市场", "IPO", "再融资")),
    ("macro_liquidity", ("利率", "汇率", "央行", "流动性", "社融", "存贷款", "通胀", "美联储")),
    ("commodity_resource", ("黄金", "铜", "原油", "煤炭", "有色", "稀土", "大宗商品", "硫酸")),
    ("regional_industry", ("广东", "福建", "上海", "深圳", "厦门", "区域", "园区", "自贸")),
    ("company_earnings", ("业绩", "净利润", "营收", "订单", "财报", "一季报", "增长")),
)

# 固定真实 ft_news 样本。运行时按 ID 从 ft_news 读取正文，避免把大段新闻硬编码进脚本，
# 同时保证每次 demo 使用同一批样本来观察 community 聚合、分裂和晋升效果。
HARDCODED_FT_NEWS_IDS: tuple[int, ...] = (
    83904,  # A股并购重组市场结构变化
    59604,  # AI 应用 / 创业板人工智能 ETF
    74425,  # 特斯拉 / AI 芯片短缺 / 自建产能
    66308,  # 海辰储能西班牙工厂 / 储能出海
    76578,  # 福建支持厦门建设金砖产业合作暨出海综合服务港
    68104,  # 国务院生产性服务业贷款贴息
    74339,  # A股公司被证监会立案
    59597,  # 国海证券业务表现
    59605,  # 美债 / 美联储政策路径
    74419,  # 硫酸短缺 / 铜价 / 美伊风险
    72799,  # 广东区域股权市场培育企业
    73304,  # 拓斯达业绩 / 工业机器人
    77551,  # 美股午盘 / 市场风险
    77549,  # Pimco / 海湾地区借款
    77547,  # 霍尔木兹海峡 / 美伊风险
    77546,  # 欧盟对乌贷款 / 对俄制裁
    76836,  # 横店影视 IP 全链路运营
    76835,  # 新能源发电项目备案
    76834,  # 油气供应中断影响
    76581,  # 算力硬件股回暖
    76580,  # 工业气体概念
    76579,  # 雷诺一季度营收
    76575,  # 印尼央行干预汇率
    76220,  # 绿电 / 算电协同
    75817,  # 全球电信连接 / 5G
    75815,  # 海南外卖骑手食品安全奖励
    75814,  # 数字虚拟宇宙 / 超算
    75461,  # 华勤技术港股上市 / AI 算力
    75456,  # 日本反对 MBK 收购牧野铣床
    75455,  # 知识产权海关备案
)

HARDCODED_FT_NEWS_ROWS: list[dict[str, Any]] = [
    {
        "id": 83904,
        "selection_buckets": ["merger_restructuring"],
        "title": "A股并购重组市场呈现三方面新变化",
        "content": (
            "Wind资讯数据显示，截至4月23日晚间，年内A股上市公司首次披露的并购重组交易已达1104起，"
            "合计交易金额约3634.65亿元，其中重大资产重组案例达33起。结合近两年数据来看，并购重组市场"
            "整体呈现出数量稳步增长、交易规模持续放量的良好态势。更为关键的是，并购重组市场生态也在发生"
            "深刻变革。第一，新兴领域产业链深度整合，新旧动能协同推进。本轮并购重组最鲜明的特征之一，是"
            "资源要素加速向新质生产力领域集聚。在“并购六条”和“科创板八条”等政策引导下，创业板与科创板"
            "上市公司并购重组案例呈现量质齐升态势。机构研究数据显示，科创板与创业板并购重组案例数量占A股"
            "总案例的比重由2020年的26%逐步提升至2025年的37%。其中，半导体、软件、生物医药、高端装备制造"
            "等新兴产业占比超过九成。此外，跨境并购、多工具组合的复杂交易案例频繁出现，反映出市场对新质"
            "生产力整合的迫切需求以及交易能力的显著提升。\n\n"
            "与此同时，传统行业的横向整合加快、产业集中度提升。汽车零部件、化学制品、电力等传统行业通过"
            "横向整合、战略合作优化产业结构，提升资源配置效率。这种“新兴聚势、传统提质”的双轮驱动格局，"
            "正推动并购重组市场步入更成熟、更高效的发展新阶段。第二，并购重组回归产业本源，助力资本市场"
            "生态优化。在各类政策推动下，A股并购市场进一步锚定价值投资与产业估值。数据显示，2025年A股"
            "横向整合并购事件达463起，交易金额4410亿元，同比增长27%；垂直整合案例从2023年的4起、2024年的"
            "5起大幅增至2025年的20起。这些数据清晰表明，并购市场正加速转向追求产业协同与长期价值。\n\n"
            "并购重组不仅有利于推动产业结构优化，还为一些上市公司提供了主动退市的新选择，这也契合“畅通退市"
            "渠道”的政策导向。从更宏观的视角看，通过市场化并购实现优胜劣汰，既能保护投资者利益，又能引导"
            "资源向优质企业集中，推动资本市场生态持续优化。第三，机构角色升级，从交易通道转向“价值合伙人”。"
            "当前，A股并购重组市场在变革中展现出前所未有的活力，这也为中介机构带来更多机遇与挑战。作为独立"
            "财务顾问，券商深度参与上市公司并购重组的交易方案设计、标的估值定价、交易撮合谈判及融资安排等"
            "关键环节。2025年，券商共服务82家上市公司完成重大资产重组，交易金额超6000亿元，通过参与产业链"
            "整合、跨区域或跨境并购等重大交易，助力上市公司实现外延式发展，为打造具有国际影响力的产业集群"
            "提供金融支撑。"
        ),
        "summary": "政策推动下，A股并购重组市场数量增长、交易放量，并向新质生产力、产业协同和价值投资回归。",
        "source": "eastmoney",
        "source_name": "证券日报",
        "source_reliability": 0.75,
        "category": "",
        "url": "http://finance.eastmoney.com/a/202604243716747262.html",
        "tags": [],
        "related_stocks": [],
        "published_at": "2026-04-23T23:38:00+00:00",
        "fingerprint": "b3197a1d7c4f1490909953000dd6186af115234bd6ee35ef37fe5dc7b0d27e82",
        "created_at": "2026-04-23T23:52:32.085587+00:00",
    },
    {
        "id": 74416,
        "selection_buckets": ["ai_compute_chain"],
        "title": "市值突破万亿！中际旭创最近1年飙升超10倍！重仓“易中天”的创业板人工智能ETF华宝（159363）再创新高！",
        "content": (
            "4月23日盘中，光模块龙头中际旭创盘中上涨近3%，成为继工业富联之后第二只市值突破万亿的算力硬件股，"
            "该股自去年4月22日以来累计上涨超1000%。根据其2026年第一季度财报，中际旭创净利润达到57.35亿元，"
            "同比和环比分别增长262.28%和56.48%。热门ETF方面，重仓光模块龙头“易中天”的创业板人工智能ETF华宝"
            "（159363）继续放量突破，场内价格再创新高。\n\n"
            "中际旭创在2026年一季度表现亮眼，其总市值突破1万亿主要得益于业绩高速增长。公司净利润同比增长"
            "262.28%，创下单季度历史最高纪录，这主要源于终端客户对算力基础设施的强劲投入导致产品出货量持续"
            "增长。预付款项同比增长1009.48%，反映出公司为应对市场需求大幅增加原材料和设备采购，显示出积极的"
            "产能扩张信号。股价累计上涨超900%的市场表现，与公司在光模块领域的龙头地位密切相关，特别是800G和"
            "1.6T等高速光模块的市场需求占据主导地位，推动了公司的长期增长预期。\n\n"
            "光通信行业龙头中际旭创2026年第一季度业绩表现亮眼，单季度实现扣非归母净利润57.18亿元，同比增长"
            "264.56%，环比增长近60%，主要得益于AI算力需求强劲带动产品出货持续增长。公司毛利率持续稳步提升至"
            "约46%，受益于1.6T和800G等高端产品比重提升、硅光模块进一步渗透和良率提升等因素。海外需求同样高"
            "景气，主流云厂商2026年资本开支规模持续超预期，GPU性能提升推动光模块速率升级明确，高速率高价值量"
            "产品放量节奏加快。\n\n"
            "投资逻辑上，中际旭创作为光模块行业龙头，市值突破万亿标志着行业领先地位及强劲增长潜力。AI算力需求"
            "爆发、终端客户基础设施投入、800G/1.6T高速率产品升级和海外云厂商资本开支超预期，构成光模块产业链"
            "的核心叙事。但市场也需要关注估值消化、客户集中、上游器件供给、汇率波动和海外采购风险。"
        ),
        "summary": "中际旭创业绩与市值快速增长，反映 AI 算力需求、光模块升级和云厂商资本开支推动的产业链叙事。",
        "source": "sina",
        "source_name": "新浪基金",
        "source_reliability": 0.7,
        "category": "",
        "url": "https://finance.sina.com.cn/money/fund/jjh/2026-04-23/doc-inhvnain3802919.shtml",
        "tags": [],
        "related_stocks": [],
        "published_at": "2026-04-23T02:14:44+00:00",
        "fingerprint": "4466db4ef524ea8ac86737c8dd7beb9e7517aef306eafaea09a3c409c6dd7003",
        "created_at": "2026-04-23T02:45:43.644832+00:00",
    },
    {
        "id": 74425,
        "selection_buckets": ["semiconductor_policy"],
        "title": "特斯拉CEO马斯克：预计未来AI芯片将严重不足",
        "content": (
            "特斯拉CEO马斯克在财报电话会上表示，特斯拉之所以启动Terafab芯片工厂项目，是因为公司预计未来AI芯片"
            "将严重不足。他表示，就行业增长速度而言，逻辑芯片，甚至更多的是存储芯片，如果不自己制造芯片，"
            "就会遇到瓶颈。这就是Terafab的诞生原因。该表态反映出AI应用扩张对逻辑芯片、存储芯片和先进制造能力"
            "的压力，也会强化市场对半导体产能、芯片供应链安全和自建产能路线的关注。"
        ),
        "summary": "马斯克预计未来 AI 芯片将严重不足，特斯拉启动 Terafab 芯片工厂以应对供应瓶颈。",
        "source": "xueqiu",
        "source_name": "雪球",
        "source_reliability": 0.5,
        "category": "company",
        "url": "",
        "tags": [],
        "related_stocks": [],
        "published_at": "2026-04-23T02:44:15+00:00",
        "fingerprint": "f35667b29dc6e8067be3b14850906c924553347ff95c74e4596465b7328ebe58",
        "created_at": "2026-04-23T02:45:44.392835+00:00",
    },
    {
        "id": 76835,
        "selection_buckets": ["new_energy_capacity"],
        "title": "3月全国新增建档立卡新能源发电项目共6228个",
        "content": (
            "国家能源局发布数据，2026年3月，全国新增建档立卡新能源发电（不含户用光伏）项目共6228个，其中风电"
            "项目44个，光伏发电项目6179个，生物质发电项目5个。光伏项目中，集中式光伏发电项目34个，工商业分布式"
            "光伏发电项目6145个。该数据说明新能源装机和分布式光伏项目仍保持高频备案，可能影响光伏组件、逆变器、"
            "储能、电网消纳和新能源运营商的需求预期。"
        ),
        "summary": "3月全国新增建档立卡新能源发电项目6228个，光伏项目占绝大多数。",
        "source": "xueqiu",
        "source_name": "雪球",
        "source_reliability": 0.5,
        "category": "industry",
        "url": "",
        "tags": [],
        "related_stocks": [],
        "published_at": "2026-04-23T06:50:29+00:00",
        "fingerprint": "9aa54f5487b463337a6ea3c8f06a6ca26b43152ec5a984924118bc3338ba4241",
        "created_at": "2026-04-23T07:00:46.465048+00:00",
    },
    {
        "id": 76578,
        "selection_buckets": ["overseas_supply_chain"],
        "title": "福建：支持厦门建设金砖产业合作暨出海综合服务港",
        "content": (
            "福建省人民政府发布关于支持金砖国家新工业革命伙伴关系创新基地高质量发展的意见。意见指出，支持厦门建设"
            "金砖产业合作暨出海综合服务港。鼓励企业在金砖国家布局建设供应链后备基地和资源保障渠道，推动金砖创新"
            "基地联动企业在海外建设工业园区、联络点，提升海外中资园区的承载能力和服务功能。在符合国家政策要求的"
            "前提下，分批分行业遴选匹配金砖国家发展需求的先进工业能力、技术解决方案，依托金砖创新基地和金砖国家"
            "工业能力中国中心，向金砖国家精准输出。"
        ),
        "summary": "福建支持厦门建设金砖产业合作暨出海综合服务港，鼓励企业建设海外供应链和工业园区。",
        "source": "xueqiu",
        "source_name": "雪球",
        "source_reliability": 0.5,
        "category": "policy",
        "url": "",
        "tags": [],
        "related_stocks": [],
        "published_at": "2026-04-23T05:55:30+00:00",
        "fingerprint": "569943e7a8b11a2b94be2ba862b7496965aea57d4d14eda1687b500b468c6aba",
        "created_at": "2026-04-23T06:02:47.575850+00:00",
    },
    {
        "id": 76575,
        "selection_buckets": ["policy_regulation"],
        "title": "印尼央行宣布继续加大干预力度 以维持印尼盾汇率稳定",
        "content": (
            "印尼央行高级副行长Destry Damayanti表示，央行继续加大干预力度，以维持印尼盾汇率稳定。央行将继续通过"
            "离岸和国内无本金交割远期合约、现汇和政府债券对市场进行干预，并强化支持市场的货币工具利率结构，以保持"
            "国内资产吸引力。由于全球不确定性加剧，印尼盾及其他地区货币均面临压力。该事件体现出新兴市场汇率、美元"
            "流动性、跨境资本和央行干预对风险偏好的影响。"
        ),
        "summary": "印尼央行继续加大干预力度维持印尼盾稳定，反映全球不确定性下的新兴市场汇率压力。",
        "source": "xueqiu",
        "source_name": "雪球",
        "source_reliability": 0.5,
        "category": "macro",
        "url": "",
        "tags": [],
        "related_stocks": [],
        "published_at": "2026-04-23T06:01:11+00:00",
        "fingerprint": "0431727eb136e90eacd608b3ba91212fd67305ee94407592cef328906daada80",
        "created_at": "2026-04-23T06:02:47.575850+00:00",
    },
    {
        "id": 74419,
        "selection_buckets": ["market_risk"],
        "title": "长江有色：硫酸短缺令全球铜企承压 23日铜价或大涨",
        "content": (
            "长江铜价短评称，特朗普意外同意延长与伊停火协议改善风险情绪且伦铜库存下降，隔夜伦铜涨2.4%；硫酸短缺"
            "令全球铜企承压，国内旺季消费尚可，现铜或大涨。宏观局势方面，美伊陷入高风险博弈，随着和谈搁浅及双向"
            "海上封锁升级，油价重返升势。尽管特朗普表态将无限期延长停火，暗示其不愿重启武装冲突，但缺乏明确退场"
            "路径，极限施压正将全球经济拖入持久消耗战。\n\n"
            "国内方面，工信部推进智能网联新能源汽车等“十五五”规划，落实机械、汽车等行业稳增长方案，推进设备更新与"
            "以旧换新，为地缘政治阴云下的市场注入支撑。供应端，硫酸短缺成为核心变量。受中东局势影响，硫酸供应趋紧，"
            "价格创历史新高。海关数据显示，3月中国对智利硫酸出口降至零，加剧全球最大铜产国的原料危机。叠加不可抗力"
            "及罢工等因素，智利铜产量承压，全球铜精矿加工费持续走低。\n\n"
            "需求端呈现传统平淡、新兴强劲的分化格局。下游加工企业因高价多采取刚需备货，现货成交平淡。但光伏、电网、"
            "电动汽车、锂电池、太阳能电池、PCB扩产及AI算力中心建设，为铜消费提供了广阔空间。硫酸短缺加剧全球铜企供应"
            "忧虑，国内基本面持续修复，社会库存持续去化，行业预期向好。"
        ),
        "summary": "硫酸短缺令全球铜企供应承压，地缘风险和 AI 算力、电网、新能源需求共同影响铜价。",
        "source": "sina",
        "source_name": "市场资讯",
        "source_reliability": 0.7,
        "category": "",
        "url": "https://finance.sina.com.cn/money/future/indu/2026-04-23/doc-inhvmvzh5131311.shtml",
        "tags": [],
        "related_stocks": [],
        "published_at": "2026-04-23T02:09:52+00:00",
        "fingerprint": "4842b0085fe003b7d0af4fed96a8473cc80fd344893d6c6733c088f9fbb6c422",
        "created_at": "2026-04-23T02:45:43.644832+00:00",
    },
    {
        "id": 74738,
        "selection_buckets": ["broker_capital_market"],
        "title": "广东：一季度末存贷款余额分别突破40万亿元和30万亿元",
        "content": (
            "广东省人民政府新闻办公室举行2026年一季度广东省金融运行形势新闻发布会。中国人民银行广东省分行副行长"
            "张双长表示，今年以来，广东社会融资规模稳步扩大，存款和贷款余额分别突破40万亿元和30万亿元，金融总量"
            "增长与经济发展的需要基本匹配。一季度末，广东社会融资规模存量同比增长6.9%；一季度社会融资规模增量"
            "1.1万亿元。一季度末，广东本外币贷款余额30.7万亿元、同比增长4.8%，存款余额40.2万亿元、同比增长7.7%。"
            "该数据可用于观察区域信用扩张、实体融资、金融支持制造业和资本市场流动性环境。"
        ),
        "summary": "广东一季度末存贷款余额分别突破40万亿元和30万亿元，社会融资规模稳步扩大。",
        "source": "xueqiu",
        "source_name": "雪球",
        "source_reliability": 0.5,
        "category": "",
        "url": "",
        "tags": [],
        "related_stocks": [],
        "published_at": "2026-04-23T03:11:24+00:00",
        "fingerprint": "6eddfc32eda8395c503d32862b81a38e0d94d157c8c9a207a85e6759329db3fc",
        "created_at": "2026-04-23T03:17:46.076969+00:00",
    },
]


async def main() -> None:
    outputs: dict[str, Any] = {}
    run_metadata = {
        "target": TARGET,
        "adapter": ADAPTER,
        "dry_run": DRY_RUN,
        "concurrency": CONCURRENCY,
        "run_full_semantic_rebuild": RUN_FULL_SEMANTIC_REBUILD,
        "demo_scenario": DEMO_SCENARIO,
        "session_id": RUN_SESSION_ID,
        "use_real_ft_news": USE_REAL_FT_NEWS,
        "use_hardcoded_ft_news": USE_HARDCODED_FT_NEWS,
        "hardcoded_ft_news_count": len(HARDCODED_FT_NEWS_ROWS),
        "ft_news_candidate_limit": FT_NEWS_CANDIDATE_LIMIT,
        "ft_news_record_limit": FT_NEWS_RECORD_LIMIT,
        "verify_query": VERIFY_QUERY,
    }

    with langfuse_propagation_context(
        trace_name="kg.write_path_demo",
        session_id=RUN_SESSION_ID,
        tags=["kg", "write-path", "demo"],
        metadata=run_metadata,
    ):
        with langfuse_observation(
            name="kg.write_path_demo",
            as_type="chain",
            input=run_metadata,
            metadata=run_metadata,
        ):
            try:
                step_0_print_config()
                outputs["schema_check"] = _observed_sync_step(
                    "demo.step0_5.schema_check",
                    step_0_5_validate_write_path_schema,
                )
                outputs["cleanup"] = await _observed_async_step(
                    "demo.step0_6.cleanup_previous_demo",
                    step_0_6_cleanup_previous_demo_data,
                )
                outputs["compile"] = await _observed_async_step(
                    "demo.step1.compile",
                    step_1_compile_controlled_news,
                )
                if RUN_GRAPH_INDEX_BUILD:
                    outputs["graph_index_build"] = await _observed_async_step(
                        "demo.step1_5.build_graph_index",
                        step_1_5_build_graph_index,
                    )
                outputs["pg_state"] = _observed_sync_step(
                    "demo.step2.inspect_pg",
                    lambda: step_2_inspect_pg_state(outputs["compile"]),
                )
                outputs["graph_index"] = _observed_sync_step(
                    "demo.step3.inspect_graph_index",
                    lambda: step_3_inspect_graph_index(
                        outputs["compile"],
                        outputs["pg_state"],
                        outputs.get("graph_index_build"),
                    ),
                )
                outputs["milvus_get_by_ids"] = await _observed_async_step(
                    "demo.step4.milvus_get_by_ids",
                    lambda: step_4_get_milvus_targets_by_id(outputs["pg_state"], outputs["graph_index"]),
                )
                outputs["milvus_semantic_search"] = await _observed_async_step(
                    "demo.step5.semantic_search",
                    lambda: step_5_semantic_search(outputs["pg_state"], outputs["graph_index"]),
                )
                if RUN_FULL_SEMANTIC_REBUILD:
                    outputs["full_semantic_rebuild"] = await _observed_async_step(
                        "demo.step6.full_semantic_rebuild",
                        step_6_full_semantic_rebuild,
                    )
                    outputs["graph_index_after_rebuild"] = _observed_sync_step(
                        "demo.step3.inspect_graph_index_after_rebuild",
                        lambda: step_3_inspect_graph_index(
                            outputs["compile"],
                            outputs["pg_state"],
                            outputs.get("full_semantic_rebuild"),
                        ),
                    )
                    outputs["milvus_semantic_search_after_rebuild"] = await _observed_async_step(
                        "demo.step5.semantic_search_after_rebuild",
                        lambda: step_5_semantic_search(outputs["pg_state"], outputs["graph_index"]),
                    )
                _observed_sync_step("demo.step7.write_output", lambda: step_7_write_output(outputs))
                langfuse_update_span(
                    output={
                        "output_file": str(OUTPUT_FILE),
                        "compiled": _compact_compile_output(outputs.get("compile")),
                        "pg_state": _compact_pg_state(outputs.get("pg_state")),
                    },
                    status_message="completed",
                )
            except Exception as exc:
                langfuse_update_span(
                    metadata={"error_type": exc.__class__.__name__},
                    level="ERROR",
                    status_message=str(exc),
                )
                raise
            finally:
                langfuse_flush()


def _observed_sync_step(name: str, fn):
    started_at = time.perf_counter()
    print(f"[{name}] START")
    with langfuse_observation(name=name, as_type="span"):
        try:
            result = fn()
            duration_ms = round((time.perf_counter() - started_at) * 1000, 1)
            print(f"[{name}] DONE duration={duration_ms / 1000:.1f}s")
            langfuse_update_span(
                output=_compact_observation_output(result),
                metadata={"duration_ms": duration_ms},
                status_message="completed",
            )
            return result
        except Exception as exc:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 1)
            print(f"[{name}] FAILED duration={duration_ms / 1000:.1f}s error={exc.__class__.__name__}: {exc}")
            langfuse_update_span(
                metadata={"error_type": exc.__class__.__name__, "duration_ms": duration_ms},
                level="ERROR",
                status_message=str(exc),
            )
            raise


async def _observed_async_step(name: str, fn):
    started_at = time.perf_counter()
    print(f"[{name}] START")
    with langfuse_observation(name=name, as_type="span"):
        task = asyncio.create_task(fn())
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=STEP_HEARTBEAT_SECONDS)
                if not task.done():
                    elapsed = time.perf_counter() - started_at
                    print(f"[{name}] still running elapsed={elapsed:.1f}s")
            result = await task
            duration_ms = round((time.perf_counter() - started_at) * 1000, 1)
            print(f"[{name}] DONE duration={duration_ms / 1000:.1f}s")
            langfuse_update_span(
                output=_compact_observation_output(result),
                metadata={"duration_ms": duration_ms},
                status_message="completed",
            )
            return result
        except Exception as exc:
            if not task.done():
                task.cancel()
            duration_ms = round((time.perf_counter() - started_at) * 1000, 1)
            print(f"[{name}] FAILED duration={duration_ms / 1000:.1f}s error={exc.__class__.__name__}: {exc}")
            langfuse_update_span(
                metadata={"error_type": exc.__class__.__name__, "duration_ms": duration_ms},
                level="ERROR",
                status_message=str(exc),
            )
            raise


def _compact_observation_output(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "__dict__"):
        return _compact_compile_output(value)
    if isinstance(value, dict):
        return {
            key: value[key]
            for key in list(value.keys())[:20]
            if key not in {"records", "payload", "content"}
        }
    return value


def _compact_compile_output(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return {
            "run_id": value.get("run_id"),
            "nodes": value.get("nodes"),
            "edges": value.get("edges"),
            "evidence": value.get("evidence"),
            "failed_records": value.get("failed_records"),
            "dry_run": value.get("dry_run"),
        }
    return {
        "run_id": getattr(value, "run_id", None),
        "nodes": getattr(value, "nodes", None),
        "edges": getattr(value, "edges", None),
        "evidence": getattr(value, "evidence", None),
        "failed_records": getattr(value, "failed_records", None),
        "dry_run": getattr(value, "dry_run", None),
    }


def _compact_pg_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "evidence": len(value.get("evidence") or []),
        "chunks": len(value.get("chunks") or []),
        "nodes": len(value.get("nodes") or []),
        "edges": len(value.get("edges") or []),
        "edge_evidence_refs": len(value.get("edge_evidence_refs") or []),
        "edge_chunk_refs": len(value.get("edge_chunk_refs") or []),
    }


def step_0_print_config() -> None:
    _section("Step 0", "运行配置")
    pprint(
        {
            "target": TARGET,
            "adapter": ADAPTER,
            "dry_run": DRY_RUN,
            "concurrency": CONCURRENCY,
            "run_full_semantic_rebuild": RUN_FULL_SEMANTIC_REBUILD,
            "demo_scenario": DEMO_SCENARIO,
            "use_real_ft_news": USE_REAL_FT_NEWS,
            "use_hardcoded_ft_news": USE_HARDCODED_FT_NEWS,
            "hardcoded_ft_news_count": len(HARDCODED_FT_NEWS_ROWS),
            "ft_news_candidate_limit": FT_NEWS_CANDIDATE_LIMIT,
            "ft_news_record_limit": FT_NEWS_RECORD_LIMIT,
            "verify_query": VERIFY_QUERY,
            "output_file": str(OUTPUT_FILE),
            "kg_llm": kg_llm_config_summary(),
            "llm_proxy": _llm_proxy_summary(),
        },
        sort_dicts=False,
    )


def step_0_5_validate_write_path_schema() -> dict[str, Any]:
    """校验演示库已经升级到当前写入链路 schema。

    这个 demo 不做自动 DDL 修补。原因是脚本要验证当前架构，而不是在运行时悄悄把旧库
    兼容过去。若这里失败，应先使用 schema/06_knowledge.sql 和后续 upgrade 脚本升级库。
    """

    _section("Step 0.5", "校验写入链路 schema")
    with get_session(TARGET) as session:
        inspector = inspect(session.bind)
        required_tables = {
            KnowledgeAssignmentCandidateOrder.__tablename__,
            KnowledgeEvidence.__tablename__,
            KnowledgeEvidenceChunk.__tablename__,
            KnowledgeGraphCommunity.__tablename__,
            KnowledgeCognitiveCard.__tablename__,
            KnowledgeCommunityAssignment.__tablename__,
        }
        missing_tables = sorted(table for table in required_tables if not inspector.has_table(table))
        table_columns = {
            table: _table_column_names(inspector, table)
            for table in required_tables
            if table not in missing_tables
        }
        missing_columns = {
            KnowledgeEvidenceChunk.__tablename__: sorted(
                _required_chunk_manifest_columns() - table_columns.get(KnowledgeEvidenceChunk.__tablename__, set())
            )
        }
        missing_columns = {table: columns for table, columns in missing_columns.items() if columns}
        forbidden_columns = {
            KnowledgeEvidenceChunk.__tablename__: sorted(
                table_columns.get(KnowledgeEvidenceChunk.__tablename__, set())
                & _forbidden_chunk_manifest_columns()
            )
        }
        forbidden_columns = {table: columns for table, columns in forbidden_columns.items() if columns}
    result = {
        "status": "ok" if not missing_tables and not missing_columns and not forbidden_columns else "invalid",
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
        "forbidden_columns": forbidden_columns,
        "schema_files": [
            "schema/06_knowledge.sql",
            "schema/08_drop_physical_foreign_keys.sql",
            "schema/09_graph_index_lineage_delta_upgrade.sql",
            "schema/10_drop_kg_evidence_chunks_content.sql",
            "schema/11_drop_graph_index_version_tables.sql",
            "schema/12_graph_unassigned_signals.sql",
            "schema/13_cognitive_index.sql",
        ],
        "kg_evidence_chunks_columns": sorted(table_columns.get(KnowledgeEvidenceChunk.__tablename__, set())),
        "kg_cognitive_cards_columns": sorted(table_columns.get(KnowledgeCognitiveCard.__tablename__, set())),
        "kg_community_assignments_columns": sorted(
            table_columns.get(KnowledgeCommunityAssignment.__tablename__, set())
        ),
    }
    pprint(result, sort_dicts=False)
    if result["status"] != "ok":
        raise RuntimeError(
            "写入链路 schema 未升级到最新结构；请先应用 schema/06_knowledge.sql "
            "以及 graph index upgrade / unassigned signal 脚本，再运行本 demo。"
        )
    return result


async def step_0_6_cleanup_previous_demo_data() -> dict[str, Any]:
    """清理上一次受控 demo 写入的数据，保证每次运行都验证本次结果。"""

    _section("Step 0.6", "清理上一次受控 demo 数据")
    if DRY_RUN:
        result = {"skipped": True, "reason": "DRY_RUN=True 时不会清理 PG/Milvus"}
        pprint(result, sort_dicts=False)
        return result

    with get_session(TARGET) as session:
        evidence_rows = session.scalars(
            select(KnowledgeEvidence).where(
                KnowledgeEvidence.adapter_name == ADAPTER,
                KnowledgeEvidence.source_id.like(f"{DEMO_PREFIX}:%"),
            )
        ).all()
        evidence_ids = [row.evidence_id for row in evidence_rows]
        chunk_ids = session.scalars(
            select(KnowledgeEvidenceChunk.chunk_id).where(
                KnowledgeEvidenceChunk.adapter_name == ADAPTER,
                KnowledgeEvidenceChunk.evidence_id.in_(evidence_ids),
            )
        ).all() if evidence_ids else []
        cognitive_card_ids = session.scalars(
            select(KnowledgeCognitiveCard.cognitive_card_id).where(
                KnowledgeCognitiveCard.adapter_name == ADAPTER,
                KnowledgeCognitiveCard.source_id.like(f"{DEMO_PREFIX}:%"),
            )
        ).all()
        edge_ids = session.scalars(
            select(KnowledgeEdgeEvidence.edge_id).where(KnowledgeEdgeEvidence.evidence_id.in_(evidence_ids))
        ).all() if evidence_ids else []
        edge_ids = list(dict.fromkeys(str(edge_id) for edge_id in edge_ids if edge_id))
        edge_rows = session.scalars(
            select(KnowledgeEdge).where(KnowledgeEdge.edge_id.in_(edge_ids))
        ).all() if edge_ids else []
        legacy_edge_ids = session.scalars(
            select(KnowledgeEdge.edge_id).where(KnowledgeEdge.adapter_name == ADAPTER)
        ).all()
        legacy_edge_ids = list(dict.fromkeys([*edge_ids, *(str(edge_id) for edge_id in legacy_edge_ids if edge_id)]))
        candidate_node_ids = list(
            dict.fromkeys(
                node_id
                for edge in edge_rows
                for node_id in (edge.source_node_id, edge.target_node_id)
                if node_id
            )
        )
        graph_ids = _find_related_graph_index_ids(
            session,
            evidence_ids=set(evidence_ids),
            chunk_ids=set(chunk_ids),
            node_ids=set(candidate_node_ids),
            edge_ids=set(edge_ids),
        )
        protected_seed_community_ids = _strip_demo_refs_from_seed_communities(
            session,
            community_ids=graph_ids["community_ids"],
            evidence_ids=set(evidence_ids),
            chunk_ids=set(chunk_ids),
            cognitive_card_ids=set(cognitive_card_ids),
        )
        graph_ids["community_ids"] = [
            community_id
            for community_id in graph_ids["community_ids"]
            if community_id not in protected_seed_community_ids
        ]

        deleted: dict[str, int] = {}
        deleted["community_assignments"] = _delete_count(
            session,
            delete(KnowledgeCommunityAssignment).where(
                KnowledgeCommunityAssignment.adapter_name == ADAPTER,
                KnowledgeCommunityAssignment.cognitive_card_id.in_(cognitive_card_ids),
            ),
        )
        deleted["cognitive_cards"] = _delete_count(
            session,
            delete(KnowledgeCognitiveCard).where(
                KnowledgeCognitiveCard.adapter_name == ADAPTER,
                KnowledgeCognitiveCard.cognitive_card_id.in_(cognitive_card_ids),
            ),
        )
        deleted["graph_unassigned_signals"] = _delete_count(
            session,
            delete(KnowledgeGraphUnassignedSignal).where(
                KnowledgeGraphUnassignedSignal.adapter_name == ADAPTER,
                KnowledgeGraphUnassignedSignal.signal_id.in_(graph_ids["unassigned_signal_ids"]),
            ),
        )
        deleted["graph_deltas"] = _delete_count(
            session,
            delete(KnowledgeGraphDelta).where(
                KnowledgeGraphDelta.adapter_name == ADAPTER,
                KnowledgeGraphDelta.delta_id.in_(graph_ids["delta_ids"]),
            ),
        )
        deleted["graph_findings"] = _delete_count(
            session,
            delete(KnowledgeGraphFinding).where(
                KnowledgeGraphFinding.adapter_name == ADAPTER,
                KnowledgeGraphFinding.finding_id.in_(graph_ids["finding_ids"]),
            ),
        )
        deleted["graph_communities"] = _delete_count(
            session,
            delete(KnowledgeGraphCommunity).where(
                KnowledgeGraphCommunity.adapter_name == ADAPTER,
                KnowledgeGraphCommunity.community_id.in_(graph_ids["community_ids"]),
            ),
        )
        deleted["edge_chunk_refs"] = _delete_count(
            session,
            delete(KnowledgeEdgeEvidenceChunk).where(
                or_(
                    KnowledgeEdgeEvidenceChunk.evidence_id.in_(evidence_ids),
                    KnowledgeEdgeEvidenceChunk.edge_id.in_(legacy_edge_ids),
                )
            ),
        )
        deleted["evidence_chunks"] = _delete_count(
            session,
            delete(KnowledgeEvidenceChunk).where(KnowledgeEvidenceChunk.evidence_id.in_(evidence_ids)),
        )
        deleted["edge_evidence_refs"] = _delete_count(
            session,
            delete(KnowledgeEdgeEvidence).where(
                or_(
                    KnowledgeEdgeEvidence.evidence_id.in_(evidence_ids),
                    KnowledgeEdgeEvidence.edge_id.in_(legacy_edge_ids),
                )
            ),
        )
        deleted["graph_adjacency"] = _delete_count(
            session,
            delete(KnowledgeGraphAdjacency).where(
                KnowledgeGraphAdjacency.adapter_name == ADAPTER,
            ),
        )
        deleted["edges"] = _delete_count(
            session,
            delete(KnowledgeEdge).where(KnowledgeEdge.adapter_name == ADAPTER),
        )
        deleted["evidence"] = _delete_count(
            session,
            delete(KnowledgeEvidence).where(KnowledgeEvidence.evidence_id.in_(evidence_ids)),
        )
        deleted["legacy_nodes"] = _delete_count(
            session,
            delete(KnowledgeNode).where(KnowledgeNode.adapter_name == ADAPTER),
        )

    milvus_target_ids = _cleanup_milvus_target_ids(
        chunk_ids=list(chunk_ids),
        node_ids=candidate_node_ids,
        edge_ids=legacy_edge_ids,
        graph_ids=graph_ids,
    )
    retriever = MilvusSemanticHybridRetriever()
    milvus_deleted = await retriever.delete_documents(
        adapter_name=ADAPTER,
        target=TARGET,
        chunk_ids=milvus_target_ids,
    )
    legacy_entity_target_ids = await retriever.list_target_ids_by_role(
        collection_role=SEMANTIC_COLLECTION_ENTITY,
        adapter_name=ADAPTER,
        target=TARGET,
    )
    legacy_relation_target_ids = await retriever.list_target_ids_by_role(
        collection_role=SEMANTIC_COLLECTION_RELATION,
        adapter_name=ADAPTER,
        target=TARGET,
    )
    legacy_entity_deleted = await retriever.delete_documents_by_role(
        collection_role=SEMANTIC_COLLECTION_ENTITY,
        adapter_name=ADAPTER,
        target=TARGET,
        target_ids=legacy_entity_target_ids,
    )
    legacy_relation_deleted = await retriever.delete_documents_by_role(
        collection_role=SEMANTIC_COLLECTION_RELATION,
        adapter_name=ADAPTER,
        target=TARGET,
        target_ids=legacy_relation_target_ids,
    )
    candidate_ledger_key = f"kg:assignment_candidate_ledger:{TARGET}:{ADAPTER}"
    candidate_ledger_deleted = redis.from_url(REDIS_URL, decode_responses=True).delete(candidate_ledger_key)
    result = {
        "source_prefix": DEMO_PREFIX,
        "evidence_ids": evidence_ids,
        "chunk_ids": list(chunk_ids),
        "cognitive_card_ids": list(cognitive_card_ids),
        "edge_ids": edge_ids,
        "candidate_node_ids": candidate_node_ids,
        "graph_target_ids": graph_ids,
        "protected_seed_community_ids": protected_seed_community_ids,
        "milvus_target_ids": milvus_target_ids,
        "deleted": deleted,
        "milvus": {
            "deleted_target_ids": milvus_deleted,
            "legacy_entity_target_ids": len(legacy_entity_target_ids),
            "legacy_relation_target_ids": len(legacy_relation_target_ids),
            "legacy_entity_deleted": legacy_entity_deleted,
            "legacy_relation_deleted": legacy_relation_deleted,
        },
        "redis": {
            "candidate_ledger_key": candidate_ledger_key,
            "candidate_ledger_deleted": int(candidate_ledger_deleted or 0),
        },
    }
    pprint(
        {
            "source_prefix": DEMO_PREFIX,
            "evidence": len(evidence_ids),
            "chunks": len(chunk_ids),
            "edges": len(edge_ids),
            "candidate_nodes": len(candidate_node_ids),
            "graph_targets": {
                "communities": len(graph_ids["community_ids"]),
                "findings": len(graph_ids["finding_ids"]),
                "deltas": len(graph_ids["delta_ids"]),
            },
            "protected_seed_communities": len(protected_seed_community_ids),
            "milvus_targets": len(milvus_target_ids),
            "deleted": deleted,
            "milvus": result["milvus"],
            "redis": result["redis"],
        },
        sort_dicts=False,
    )
    return result


async def step_1_compile_controlled_news() -> dict[str, Any]:
    _section("Step 1", "编译并写入受控新闻")
    service = create_knowledge_service(target=TARGET)
    result = await service.compile_kg(
        KnowledgeCompileCommand(
            adapter_name=ADAPTER,
            records=controlled_news_records(),
            target=TARGET,
            dry_run=DRY_RUN,
            request_id=f"{DEMO_PREFIX}:{DEMO_SCENARIO}:compile",
            concurrency=CONCURRENCY,
        )
    )
    data = result.to_dict()
    pprint(
        {
            "run_id": data.get("run_id"),
            "dry_run": data.get("dry_run"),
            "nodes": data.get("nodes"),
            "edges": data.get("edges"),
            "evidence": data.get("evidence"),
            "failed_records": data.get("failed_records"),
            "warnings": data.get("warnings"),
            "sample_node_ids": data.get("node_ids", [])[:8],
            "sample_edge_ids": data.get("edge_ids", [])[:8],
            "sample_evidence_ids": data.get("evidence_ids", [])[:8],
            "index_refresh": _compact_index_refresh(data.get("index_refresh") or {}),
            "graph_index": _compact_graph_index_refresh((data.get("index_refresh") or {}).get("graph_index") or {}),
        },
        sort_dicts=False,
    )
    return data


async def step_1_5_build_graph_index() -> dict[str, Any]:
    _section("Step 1.5", "显式构建 Graph Index 高级认知索引")
    service = create_knowledge_service(target=TARGET)
    result = await service.rebuild_indexes_for(
        KnowledgeRebuildIndexesCommand(
            adapter_name=ADAPTER,
            target=TARGET,
            index_types=["graph_index"],
            scope="all",
        )
    )
    data = result.to_dict()
    graph_index = _compact_graph_index_refresh(data.get("graph_index") or {})
    pprint(
        {
            "run_id": data.get("run_id"),
            "graph_adjacency": data.get("graph_adjacency"),
            "evidence_chunks": data.get("evidence_chunks"),
            "hybrid_chunks": data.get("hybrid_chunks"),
            "graph_index": graph_index,
        },
        sort_dicts=False,
    )
    return data


def step_2_inspect_pg_state(compile_result: dict[str, Any]) -> dict[str, Any]:
    _section("Step 2", "检查 PG 事实和指针")
    evidence_ids = [str(item) for item in compile_result.get("evidence_ids", []) if item]
    node_ids = [str(item) for item in compile_result.get("node_ids", []) if item]
    edge_ids = [str(item) for item in compile_result.get("edge_ids", []) if item]

    if DRY_RUN:
        result = {
            "skipped": True,
            "reason": "DRY_RUN=True 时不会写入 PG/Milvus",
            "compile_ids": {
                "evidence_ids": evidence_ids,
                "node_ids": node_ids,
                "edge_ids": edge_ids,
            },
        }
        pprint(result, sort_dicts=False)
        return result

    with get_session(TARGET) as session:
        evidence_rows = _fetch_evidence_rows(session, evidence_ids)
        chunk_rows = _fetch_chunk_rows(session, evidence_ids)
        node_rows = _fetch_node_rows(session, node_ids)
        edge_rows = _fetch_edge_rows(session, edge_ids)
        edge_evidence_rows = _fetch_edge_evidence_rows(session, edge_ids)
        edge_chunk_rows = _fetch_edge_chunk_rows(session, edge_ids)
        normalization_rows = _fetch_recent_normalization_rules(session)

    result = {
        "evidence": evidence_rows,
        "chunks": chunk_rows,
        "nodes": node_rows,
        "edges": edge_rows,
        "edge_evidence_refs": edge_evidence_rows,
        "edge_chunk_refs": edge_chunk_rows,
        "normalization_rules": normalization_rows,
        "ids": {
            "evidence_ids": evidence_ids,
            "chunk_ids": [item["chunk_id"] for item in chunk_rows],
            "node_ids": node_ids,
            "edge_ids": edge_ids,
            "entity_target_ids": _entity_target_ids(node_rows),
            "relation_target_ids": [f"kg_card:edge:{edge_id}" for edge_id in edge_ids],
        },
    }
    pprint(
        {
            "evidence_count": len(evidence_rows),
            "chunk_count": len(chunk_rows),
            "node_count": len(node_rows),
            "edge_count": len(edge_rows),
            "edge_evidence_refs": len(edge_evidence_rows),
            "edge_chunk_refs": len(edge_chunk_rows),
            "chunk_split_ok": len(chunk_rows) >= 2,
            "node_type_counts": dict(Counter(item["node_type"] for item in node_rows)),
            "relation_type_counts": dict(Counter(item["relation_type"] for item in edge_rows)),
            "sample_chunks": chunk_rows[:3],
            "sample_edges": edge_rows[:5],
            "normalization_rule_samples": normalization_rows[:5],
        },
        sort_dicts=False,
    )
    if evidence_rows and len(chunk_rows) < 2:
        raise RuntimeError(
            "受控新闻未切出多个 chunk，无法验证 chunk manifest、previous/next 指针和 edge->chunk refs。"
        )
    return result


def step_3_inspect_graph_index(
    compile_result: dict[str, Any],
    pg_state: dict[str, Any],
    graph_index_build: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _section("Step 3", "检查 Graph Index community / finding / delta")
    if pg_state.get("skipped"):
        return {"skipped": True, "reason": pg_state.get("reason")}

    ids = pg_state.get("ids") or {}
    evidence_ids = set(ids.get("evidence_ids") or [])
    chunk_ids = set(ids.get("chunk_ids") or [])
    node_ids = set(ids.get("node_ids") or [])
    edge_ids = set(ids.get("edge_ids") or [])

    with get_session(TARGET) as session:
        communities = session.scalars(
            select(KnowledgeGraphCommunity)
            .where(KnowledgeGraphCommunity.adapter_name == ADAPTER)
            .order_by(
                KnowledgeGraphCommunity.projection,
                KnowledgeGraphCommunity.level,
                KnowledgeGraphCommunity.community_id,
            )
        ).all()
        findings = session.scalars(
            select(KnowledgeGraphFinding)
            .where(KnowledgeGraphFinding.adapter_name == ADAPTER)
            .order_by(
                KnowledgeGraphFinding.projection,
                KnowledgeGraphFinding.community_id,
                KnowledgeGraphFinding.finding_id,
            )
        ).all()
        deltas = session.scalars(
            select(KnowledgeGraphDelta)
            .where(KnowledgeGraphDelta.adapter_name == ADAPTER)
            .order_by(
                KnowledgeGraphDelta.projection,
                KnowledgeGraphDelta.window_name,
                KnowledgeGraphDelta.delta_id,
            )
        ).all()
        unassigned_signals = session.scalars(
            select(KnowledgeGraphUnassignedSignal)
            .where(KnowledgeGraphUnassignedSignal.adapter_name == ADAPTER)
            .order_by(
                KnowledgeGraphUnassignedSignal.projection,
                KnowledgeGraphUnassignedSignal.signal_id,
            )
        ).all()
        cognitive_cards = session.scalars(
            select(KnowledgeCognitiveCard)
            .where(
                KnowledgeCognitiveCard.adapter_name == ADAPTER,
                KnowledgeCognitiveCard.evidence_id.in_(evidence_ids),
            )
            .order_by(KnowledgeCognitiveCard.source_id, KnowledgeCognitiveCard.chunk_index)
        ).all()
        community_assignments = session.scalars(
            select(KnowledgeCommunityAssignment)
            .where(
                KnowledgeCommunityAssignment.adapter_name == ADAPTER,
                KnowledgeCommunityAssignment.cognitive_card_id.in_(
                    [row.cognitive_card_id for row in cognitive_cards]
                ),
            )
            .order_by(KnowledgeCommunityAssignment.cognitive_card_id, KnowledgeCommunityAssignment.intent_index)
        ).all()

    related_communities = [
        _community_row(row)
        for row in communities
        if _intersects(row.evidence_ids, evidence_ids)
        or _intersects(row.chunk_ids, chunk_ids)
        or _intersects(row.member_node_ids, node_ids)
        or _intersects(row.member_edge_ids, edge_ids)
    ]
    related_findings = [
        _finding_row(row)
        for row in findings
        if row.community_id in {item["community_id"] for item in related_communities}
        or _intersects(row.cited_chunk_ids, chunk_ids)
        or _intersects(row.cited_evidence_ids, evidence_ids)
        or _intersects(row.supporting_edge_ids, edge_ids)
        or _intersects(row.node_ids, node_ids)
    ]
    related_deltas = [
        _delta_row(row)
        for row in deltas
        if _intersects(row.community_ids, {item["community_id"] for item in related_communities})
        or _intersects(row.finding_ids, {item["finding_id"] for item in related_findings})
        or _intersects(row.cited_chunk_ids, chunk_ids)
    ]
    related_unassigned_signals = [
        _unassigned_signal_row(row)
        for row in unassigned_signals
        if _intersects(row.evidence_ids, evidence_ids)
        or _intersects(row.chunk_ids, chunk_ids)
        or _intersects(row.node_ids, node_ids)
        or _intersects(row.edge_ids, edge_ids)
    ]
    result = {
        "compile_graph_index": _compact_graph_index_refresh((compile_result.get("index_refresh") or {}).get("graph_index") or {}),
        "compile_cognitive_index": _compact_cognitive_index_refresh(
            (compile_result.get("index_refresh") or {}).get("cognitive_index") or {}
        ),
        "manual_graph_index_build": _compact_graph_index_refresh((graph_index_build or {}).get("graph_index") or {}),
        "related_cognitive_cards": [_cognitive_card_row(row) for row in cognitive_cards],
        "related_community_assignments": [_community_assignment_row(row) for row in community_assignments],
        "related_communities": related_communities,
        "related_findings": related_findings,
        "related_deltas": related_deltas,
        "related_unassigned_signals": related_unassigned_signals,
        "ids": {
            "community_target_ids": [item["community_id"] for item in related_communities],
            "finding_target_ids": [item["finding_id"] for item in related_findings],
            "delta_target_ids": [item["delta_id"] for item in related_deltas],
        },
    }
    pprint(
        {
            "compile_graph_index": result["compile_graph_index"],
            "compile_cognitive_index": result["compile_cognitive_index"],
            "manual_graph_index_build": result["manual_graph_index_build"],
            "related_cognitive_cards": len(result["related_cognitive_cards"]),
            "related_community_assignments": len(result["related_community_assignments"]),
            "related_communities": len(related_communities),
            "related_findings": len(related_findings),
            "related_deltas": len(related_deltas),
            "related_unassigned_signals": len(related_unassigned_signals),
            "sample_cognitive_cards": result["related_cognitive_cards"][:5],
            "sample_community_assignments": result["related_community_assignments"][:5],
            "sample_communities": related_communities[:5],
            "sample_findings": related_findings[:5],
            "sample_deltas": related_deltas[:5],
            "sample_unassigned_signals": related_unassigned_signals[:5],
        },
        sort_dicts=False,
    )
    return result


async def step_4_get_milvus_targets_by_id(pg_state: dict[str, Any], graph_index_state: dict[str, Any]) -> dict[str, Any]:
    _section("Step 4", "按 target_id 从 Milvus 精准取回")
    if pg_state.get("skipped"):
        return {"skipped": True, "reason": pg_state.get("reason")}

    ids = pg_state.get("ids") or {}
    graph_ids = graph_index_state.get("ids") or {}
    target_groups = {
        "chunk": ids.get("chunk_ids", [])[:MILVUS_GET_LIMIT],
        "community": graph_ids.get("community_target_ids", [])[:MILVUS_GET_LIMIT],
    }
    target_id_to_group = {
        target_id: group_name
        for group_name, group_ids in target_groups.items()
        for target_id in group_ids
    }
    target_ids = list(target_id_to_group)
    retriever = MilvusSemanticHybridRetriever()
    hits = await retriever.get_by_ids(
        target_ids,
        RetrievalOptions(
            adapter_name=ADAPTER,
            target=TARGET,
            semantic_hybrid_limit=SEMANTIC_LIMIT,
            limit=SEMANTIC_LIMIT,
            max_hits=SEMANTIC_LIMIT,
        ),
    )
    result = {
        "requested_groups": target_groups,
        "requested_target_ids": target_ids,
        "requested_counts": {name: len(values) for name, values in target_groups.items()},
        "hit_count": len(hits),
        "hit_counts": _milvus_hit_counts_by_group(hits, target_id_to_group),
        "hits": [_compact_hit(hit) for hit in hits],
    }
    pprint(
        {
            "requested": len(target_ids),
            "requested_counts": result["requested_counts"],
            "hit_count": len(hits),
            "hit_counts": result["hit_counts"],
            "sample_hits": result["hits"][:8],
        },
        sort_dicts=False,
    )
    return result


def _milvus_hit_counts_by_group(hits: list[Any], target_id_to_group: dict[str, str]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for hit in hits:
        group_name = target_id_to_group.get(str(getattr(hit, "hit_id", "")), "unknown")
        counts[group_name] += 1
    return dict(counts)


async def step_5_semantic_search(pg_state: dict[str, Any], graph_index_state: dict[str, Any]) -> dict[str, Any]:
    _section("Step 5", "Milvus 多集合语义搜索检查")
    retriever = MilvusSemanticHybridRetriever()
    hits = await retriever.search(
        VERIFY_QUERY,
        RetrievalOptions(
            adapter_name=ADAPTER,
            target=TARGET,
            semantic_hybrid_limit=SEMANTIC_LIMIT,
            limit=SEMANTIC_LIMIT,
            max_hits=SEMANTIC_LIMIT,
        ),
    )
    scoped_refs = _demo_scope_refs(pg_state, graph_index_state)
    scoped_hits = [hit for hit in hits if _hit_in_demo_scope(hit, scoped_refs)]
    result = {
        "query": VERIFY_QUERY,
        "hit_count": len(hits),
        "demo_scope_hit_count": len(scoped_hits),
        "diagnostics": retriever.last_search_diagnostics,
        "hits": [_compact_hit(hit) for hit in hits],
        "demo_scope_hits": [_compact_hit(hit) for hit in scoped_hits],
        "demo_scope_refs": {key: sorted(value) for key, value in scoped_refs.items()},
    }
    pprint(
        {
            "hit_count": result["hit_count"],
            "demo_scope_hit_count": result["demo_scope_hit_count"],
            "diagnostics": result["diagnostics"],
            "sample_hits": result["hits"][:8],
            "demo_scope_sample_hits": result["demo_scope_hits"][:8],
        },
        sort_dicts=False,
    )
    return result


async def step_6_full_semantic_rebuild() -> dict[str, Any]:
    _section("Step 6", "可选全量语义索引和 Graph Index 重建")
    service = create_knowledge_service(target=TARGET)
    result = await service.rebuild_indexes_for(
        KnowledgeRebuildIndexesCommand(
            adapter_name=ADAPTER,
            target=TARGET,
            index_types=["graph_adjacency", "evidence_chunks", "hybrid_chunks", "graph_index"],
            scope="all",
        )
    )
    data = result.to_dict()
    pprint(data, sort_dicts=False)
    return data


def step_7_write_output(outputs: dict[str, Any]) -> None:
    _section("Step 7", "写出演示结果")
    OUTPUT_FILE.write_text(json.dumps(outputs, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"[output] written: {OUTPUT_FILE}")


def controlled_news_records() -> list[dict[str, Any]]:
    """受控新闻样例：用长文本覆盖 chunk split、chunk refs 和边证据追溯。"""

    if USE_HARDCODED_FT_NEWS:
        records = hardcoded_ft_news_records()
        if records:
            return records
        print("[warn] 硬编码 ft_news 样本不可用，尝试实时 ft_news 查询。")

    if USE_REAL_FT_NEWS:
        records = real_ft_news_records()
        if records:
            return records
        print("[warn] ft_news 没有可用代表性样本，回退到内置受控新闻。")

    return fallback_controlled_news_records()


def hardcoded_ft_news_records() -> list[dict[str, Any]]:
    """固定代表性 ft_news 样本，避免 demo 每次受数据库最新数据影响。"""

    if HARDCODED_FT_NEWS_IDS:
        requested_ids = HARDCODED_FT_NEWS_IDS[:FT_NEWS_RECORD_LIMIT]
        rows = _fetch_ft_news_rows_by_ids(requested_ids)
        rows_by_id = {int(row["id"]): row for row in rows}
        missing_ids = [row_id for row_id in requested_ids if row_id not in rows_by_id]
        selected_rows = [rows_by_id[row_id] for row_id in requested_ids if row_id in rows_by_id]
        records: list[dict[str, Any]] = []
        for row in selected_rows:
            projected = project_ft_news_row(row)
            if projected is None:
                continue
            records.append(
                _demo_scoped_projected_record(
                    projected,
                    row=row,
                    bucket_hits=_ft_news_bucket_hits(row),
                )
            )
        if len(records) < FT_NEWS_RECORD_LIMIT and USE_REAL_FT_NEWS:
            supplement = real_ft_news_records(
                limit=FT_NEWS_RECORD_LIMIT - len(records),
                excluded_ids={int(row_id) for row_id in requested_ids},
            )
            records.extend(supplement)
        if records:
            print(
                "[demo.ft_news] using fixed ft_news ids; "
                f"requested={len(requested_ids)} records={len(records)} "
                f"missing_ids={missing_ids}"
            )
            for record in records:
                print(
                    "[demo.ft_news] "
                    f"{record['metadata'].get('selection_buckets')} "
                    f"{record['metadata'].get('original_source_id')} "
                    f"{record['payload'].get('title')}"
                )
        return records

    records: list[dict[str, Any]] = []
    for row in HARDCODED_FT_NEWS_ROWS:
        projected = project_ft_news_row(row)
        if projected is None:
            continue
        records.append(
            _demo_scoped_projected_record(
                projected,
                row=row,
                bucket_hits=[str(item) for item in row.get("selection_buckets") or []],
            )
        )
    if records:
        print(
            "[demo.ft_news] using hardcoded representative samples; "
            f"records={len(records)} ids={[record['metadata'].get('original_source_id') for record in records]}"
        )
        for record in records:
            print(
                "[demo.ft_news] "
                f"{record['metadata'].get('selection_buckets')} "
                f"{record['metadata'].get('original_source_id')} "
                f"{record['payload'].get('title')}"
            )
    return records


def real_ft_news_records(
    *,
    limit: int = FT_NEWS_RECORD_LIMIT,
    excluded_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """从 ft_news 选取代表性新闻，覆盖真实写入和 Graph Index 分裂场景。"""

    rows = _fetch_recent_ft_news_rows(limit=FT_NEWS_CANDIDATE_LIMIT)
    if not rows:
        return []
    if excluded_ids:
        rows = [row for row in rows if int(row["id"]) not in excluded_ids]
    selected_rows, bucket_hits = _select_representative_ft_news_rows(
        rows,
        limit=limit,
        min_text_chars=FT_NEWS_MIN_TEXT_CHARS,
    )
    records: list[dict[str, Any]] = []
    for row in selected_rows:
        projected = project_ft_news_row(row)
        if projected is None:
            continue
        records.append(_demo_scoped_projected_record(projected, row=row, bucket_hits=bucket_hits.get(int(row["id"]), [])))
    if records:
        print(
            "[demo.ft_news] selected "
            f"{len(records)} records from {len(rows)} candidates; "
            f"ids={[record['metadata'].get('original_source_id') for record in records]}"
        )
        for record in records:
            print(
                "[demo.ft_news] "
                f"{record['metadata'].get('selection_buckets')} "
                f"{record['metadata'].get('original_source_id')} "
                f"{record['payload'].get('title')}"
            )
    return records


def _fetch_recent_ft_news_rows(*, limit: int) -> list[dict[str, Any]]:
    with get_session(TARGET) as session:
        inspector = inspect(session.bind)
        if not inspector.has_table(News.__tablename__):
            return []
        rows = session.scalars(
            select(News)
            .order_by(News.created_at.desc().nullslast(), News.id.desc())
            .limit(max(1, int(limit)))
        ).all()
        return [_news_model_row(row) for row in rows]


def _fetch_ft_news_rows_by_ids(row_ids: tuple[int, ...]) -> list[dict[str, Any]]:
    if not row_ids:
        return []
    with get_session(TARGET) as session:
        inspector = inspect(session.bind)
        if not inspector.has_table(News.__tablename__):
            return []
        rows = session.scalars(
            select(News).where(News.id.in_(list(row_ids)))
        ).all()
        return [_news_model_row(row) for row in rows]


def _select_representative_ft_news_rows(
    rows: list[dict[str, Any]],
    *,
    limit: int,
    min_text_chars: int,
) -> tuple[list[dict[str, Any]], dict[int, list[str]]]:
    usable = [row for row in rows if len(_ft_news_search_text(row)) >= min_text_chars]
    selected: list[dict[str, Any]] = []
    selected_ids: set[int] = set()
    bucket_hits: dict[int, list[str]] = {}

    for bucket_name, keywords in FT_NEWS_REPRESENTATIVE_BUCKETS:
        best = _best_row_for_bucket(usable, keywords=keywords, excluded_ids=selected_ids)
        if best is None:
            continue
        row_id = int(best["id"])
        selected.append(best)
        selected_ids.add(row_id)
        bucket_hits.setdefault(row_id, []).append(bucket_name)
        if len(selected) >= limit:
            return selected, bucket_hits

    for row in usable:
        if len(selected) >= limit:
            break
        row_id = int(row["id"])
        if row_id in selected_ids:
            continue
        selected.append(row)
        selected_ids.add(row_id)
        bucket_hits.setdefault(row_id, []).append("recent_long_text")
    return selected, bucket_hits


def _best_row_for_bucket(
    rows: list[dict[str, Any]],
    *,
    keywords: tuple[str, ...],
    excluded_ids: set[int],
) -> dict[str, Any] | None:
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    for index, row in enumerate(rows):
        row_id = int(row["id"])
        if row_id in excluded_ids:
            continue
        text = _ft_news_search_text(row).lower()
        score = sum(text.count(keyword.lower()) for keyword in keywords)
        if score <= 0:
            continue
        candidates.append((score, -index, row))
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def _ft_news_bucket_hits(row: dict[str, Any]) -> list[str]:
    text = _ft_news_search_text(row).lower()
    hits = [
        bucket_name
        for bucket_name, keywords in FT_NEWS_REPRESENTATIVE_BUCKETS
        if any(keyword.lower() in text for keyword in keywords)
    ]
    return hits or ["fixed_ft_news_sample"]


def _demo_scoped_projected_record(
    record: dict[str, Any],
    *,
    row: dict[str, Any],
    bucket_hits: list[str],
) -> dict[str, Any]:
    original_source_id = str(record.get("source_id") or f"ft_news:{row.get('id')}")
    scoped_source_id = f"{DEMO_PREFIX}:ft_news:{row.get('id')}"
    payload = dict(record.get("payload") or {})
    payload["source_id"] = scoped_source_id
    payload["document_id"] = scoped_source_id
    payload["original_source_id"] = original_source_id
    metadata = {
        **(record.get("metadata") or {}),
        "source_table": "ft_news",
        "source_pk": row.get("id"),
        "original_source_id": original_source_id,
        "demo_scenario": DEMO_SCENARIO,
        "selection_buckets": bucket_hits,
        "source_origin": "real_ft_news_demo",
    }
    return {
        **record,
        "source_id": scoped_source_id,
        "payload": payload,
        "metadata": metadata,
    }


def _news_model_row(row: News) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "content": row.content,
        "summary": row.summary,
        "source": row.source,
        "source_name": row.source_name,
        "source_reliability": row.source_reliability,
        "category": row.category,
        "url": row.url,
        "tags": row.tags,
        "related_stocks": row.related_stocks,
        "published_at": row.published_at,
        "fingerprint": row.fingerprint,
        "created_at": row.created_at,
    }


def _ft_news_search_text(row: dict[str, Any]) -> str:
    parts = [row.get("title"), row.get("summary"), row.get("content")]
    return "\n".join(str(item or "").strip() for item in parts if str(item or "").strip())


def fallback_controlled_news_records() -> list[dict[str, Any]]:
    """内置兜底新闻：仅在 ft_news 不可用时使用。"""

    text = (
        "并购重组政策窗口下，A股产业整合机会与风险同步升温。"
        "在并购六条和科创板八条等政策引导下，并购重组市场进一步锚定价值投资与产业估值。"
        "监管层鼓励上市公司围绕主业开展产业并购，强调估值约束、信息披露质量和中小投资者保护。"
        "市场参与者认为，本轮并购重组不再只是控制权交易，而是更多服务于产业链补短板、强链条、补技术短板和提升上市公司质量。"
        "Wind 口径显示，年内首次披露的并购重组交易数量持续增加，重大资产重组案例也明显活跃。"
        "从交易结构看，横向整合仍是主线，垂直整合案例开始增多，部分上市公司通过收购上游材料、核心零部件、研发平台或下游渠道来提升协同效率。"
        "但交易对价、商誉减值、业绩承诺兑现和整合执行风险也开始被投资者重新定价。"
        "\n\n"
        "第一，半导体、生物医药和高端装备制造成为机会与风险并存的重点方向。"
        "半导体、生物医药、高端装备制造和汽车零部件等行业成为产业并购的重要方向，"
        "上市公司通过横向整合和垂直整合补齐供应链能力。"
        "在半导体领域，部分公司关注先进封装、功率器件、设备材料和国产替代环节，机会在于提升供应链安全和客户交付能力，风险在于周期波动、资本开支压力和技术路线变化。"
        "在生物医药领域，企业更关注创新药管线、CXO 服务、医疗器械平台和商业化渠道，机会在于补齐研发体系和销售网络，风险在于临床失败、集采降价和研发资产估值过高。"
        "在高端装备制造领域，智能制造、工业机器人、数控系统和航空航天配套资产受到关注，交易目的包括获取核心技术、提高产能利用率和进入高壁垒客户体系，风险则来自订单兑现、客户认证周期和整合管理难度。"
        "在汽车零部件领域，电动化和智能化仍是并购主线，热管理、轻量化、智能座舱和线控制动等细分资产被认为具有较高产业协同价值。"
        "\n\n"
        "第二，券商角色从交易撮合者升级为价值合伙人，开始围绕产业链梳理标的、估值和并购节奏。"
        "过去券商更多承担财务顾问、估值定价、合规核查和交易执行角色，当前则需要提前参与产业研究、资产筛选和长期资本运作方案设计。"
        "部分券商投行团队会从上市公司战略出发，梳理同业可比公司、上下游潜在标的、技术路线差异、客户重合度和商誉压力。"
        "在政策窗口、市场估值和融资环境变化时，券商还需要协助企业判断交易支付方式、股份锁定安排、业绩承诺风险和整合节奏。"
        "这意味着券商在并购重组生态中的角色更接近价值合伙人，而不是单纯项目承揽方。"
        "\n\n"
        "第三，资产影响从单一控制权交易扩展到优质资产注入、研发能力整合、新质生产力布局和风险重估。"
        "受影响的资产主要包括上市公司股权、产业链上下游资产、研发平台和制造产能。"
        "对于上市公司而言，优质资产注入可能改善收入结构和盈利质量，也可能带来商誉减值、整合不及预期和管理半径扩张等风险。"
        "对于产业链而言，并购重组有助于提高关键环节集中度，但如果交易价格过高、融资成本上升或下游需求放缓，也会削弱协同效果。"
        "对于投资者而言，需要区分政策鼓励的产业整合和单纯题材炒作，重点观察标的资产质量、现金流、客户稳定性、技术壁垒和交易对价合理性。"
        "\n\n"
        "后续影响还体现在市场叙事变化上。"
        "一方面，政策支持会强化并购重组、科创板八条、新质生产力和价值投资等主题之间的联系，推动资金重新评估硬科技资产的长期价值。"
        "另一方面，监管对高估值并购、跨界收购和忽悠式重组仍会保持约束，市场会更关注交易是否真正改善主业竞争力。"
        "因此，本轮并购重组对半导体、高端装备制造、生物医药和汽车零部件等行业偏正面，但最终影响仍取决于交易落地质量、整合执行能力和资本市场环境。"
    )
    source_id = f"{DEMO_PREFIX}:news:{DEMO_SCENARIO}"
    return [
        {
            "source_type": "news_articles",
            "source_id": source_id,
            "record_kind": "text_document",
            "observed_at": DEMO_TS,
            "payload": {
                "source_id": source_id,
                "published_at": DEMO_TS,
                "title": "并购重组政策窗口下的产业整合机会与风险",
                "summary": "并购重组政策窗口推动半导体、生物医药、高端装备制造等方向整合，同时带来估值、商誉和整合执行风险。",
                "text": text,
                "source_name": "usage_demo",
                "url": "https://example.local/kg-write-path-demo",
            },
            "metadata": {
                "source_table": "usage_demo_news",
                "source_pk": DEMO_SCENARIO,
                "external_source": "kg_write_path_demo",
                "source_origin": "controlled_demo",
                "projection_rule_version": "demo-v1",
            },
        }
    ]


def _find_related_graph_index_ids(
    session: Any,
    *,
    evidence_ids: set[str],
    chunk_ids: set[str],
    node_ids: set[str],
    edge_ids: set[str],
) -> dict[str, list[str]]:
    communities = session.scalars(
        select(KnowledgeGraphCommunity).where(KnowledgeGraphCommunity.adapter_name == ADAPTER)
    ).all()
    related_community_ids = {
        row.community_id
        for row in communities
        if _intersects(row.evidence_ids, evidence_ids)
        or _intersects(row.chunk_ids, chunk_ids)
        or _intersects(row.member_node_ids, node_ids)
        or _intersects(row.member_edge_ids, edge_ids)
    }
    findings = session.scalars(
        select(KnowledgeGraphFinding).where(KnowledgeGraphFinding.adapter_name == ADAPTER)
    ).all()
    related_finding_ids = {
        row.finding_id
        for row in findings
        if row.community_id in related_community_ids
        or _intersects(row.cited_evidence_ids, evidence_ids)
        or _intersects(row.cited_chunk_ids, chunk_ids)
        or _intersects(row.supporting_edge_ids, edge_ids)
        or _intersects(row.node_ids, node_ids)
    }
    deltas = session.scalars(
        select(KnowledgeGraphDelta).where(KnowledgeGraphDelta.adapter_name == ADAPTER)
    ).all()
    related_delta_ids = {
        row.delta_id
        for row in deltas
        if _intersects(row.community_ids, related_community_ids)
        or _intersects(row.finding_ids, related_finding_ids)
        or _intersects(row.cited_evidence_ids, evidence_ids)
        or _intersects(row.cited_chunk_ids, chunk_ids)
        or _intersects(row.supporting_edge_ids, edge_ids)
        or _intersects(row.node_ids, node_ids)
    }
    unassigned_signals = session.scalars(
        select(KnowledgeGraphUnassignedSignal).where(KnowledgeGraphUnassignedSignal.adapter_name == ADAPTER)
    ).all()
    related_unassigned_signal_ids = {
        row.signal_id
        for row in unassigned_signals
        if _intersects(row.evidence_ids, evidence_ids)
        or _intersects(row.chunk_ids, chunk_ids)
        or _intersects(row.node_ids, node_ids)
        or _intersects(row.edge_ids, edge_ids)
    }
    return {
        "community_ids": sorted(related_community_ids),
        "finding_ids": sorted(related_finding_ids),
        "delta_ids": sorted(related_delta_ids),
        "unassigned_signal_ids": sorted(related_unassigned_signal_ids),
    }


def _cleanup_milvus_target_ids(
    *,
    chunk_ids: list[str],
    node_ids: list[str],
    edge_ids: list[str],
    graph_ids: dict[str, list[str]],
) -> list[str]:
    target_ids = [
        *chunk_ids,
        *[f"kg_card:node_card:{node_id}" for node_id in node_ids],
        *[f"kg_card:event_card:{node_id}" for node_id in node_ids],
        *[f"kg_card:edge:{edge_id}" for edge_id in edge_ids],
        *graph_ids.get("community_ids", []),
        *graph_ids.get("finding_ids", []),
        *graph_ids.get("delta_ids", []),
    ]
    return [target_id for target_id in dict.fromkeys(target_ids) if target_id]


def _strip_demo_refs_from_seed_communities(
    session: Any,
    *,
    community_ids: list[str],
    evidence_ids: set[str],
    chunk_ids: set[str],
    cognitive_card_ids: set[str],
) -> list[str]:
    if not community_ids:
        return []
    rows = session.scalars(
        select(KnowledgeGraphCommunity).where(
            KnowledgeGraphCommunity.adapter_name == ADAPTER,
            KnowledgeGraphCommunity.community_id.in_(community_ids),
        )
    ).all()
    protected: list[str] = []
    for row in rows:
        metrics = dict(row.metrics or {})
        if metrics.get("origin") != "seed":
            continue
        protected.append(row.community_id)
        row.evidence_ids = [item for item in row.evidence_ids or [] if item not in evidence_ids]
        row.chunk_ids = [item for item in row.chunk_ids or [] if item not in chunk_ids]
        metrics["source_ids"] = [
            item
            for item in metrics.get("source_ids") or []
            if not str(item).startswith(f"{DEMO_PREFIX}:")
        ]
        metrics["source_count"] = len(set(metrics["source_ids"]))
        metrics["cognitive_card_ids"] = [
            item
            for item in metrics.get("cognitive_card_ids") or []
            if item not in cognitive_card_ids
        ]
        metrics["assigned_intents"] = [
            item
            for item in metrics.get("assigned_intents") or []
            if not _is_demo_assignment_payload(item, evidence_ids=evidence_ids, cognitive_card_ids=cognitive_card_ids)
        ]
        metrics["assignments"] = [
            item
            for item in metrics.get("assignments") or []
            if not _is_demo_assignment_payload(item, evidence_ids=evidence_ids, cognitive_card_ids=cognitive_card_ids)
        ]
        if not metrics["assigned_intents"]:
            row.summary = str(metrics.get("scope") or row.summary or "")
            metrics["source_count"] = 0
        row.metrics = metrics
    return protected


def _is_demo_assignment_payload(
    item: Any,
    *,
    evidence_ids: set[str],
    cognitive_card_ids: set[str],
) -> bool:
    if not isinstance(item, dict):
        return False
    source_id = str(item.get("source_id") or "")
    evidence_id = str(item.get("evidence_id") or "")
    cognitive_card_id = str(item.get("cognitive_card_id") or "")
    return (
        source_id.startswith(f"{DEMO_PREFIX}:")
        or evidence_id in evidence_ids
        or cognitive_card_id in cognitive_card_ids
    )


def _delete_count(session: Any, statement: Any) -> int:
    result = session.execute(statement)
    return int(result.rowcount or 0)


def _delete_orphan_nodes(session: Any, node_ids: list[str]) -> int:
    deleted = 0
    for node_id in dict.fromkeys(node_ids):
        remaining_edges = int(
            session.scalar(
                select(func.count())
                .select_from(KnowledgeEdge)
                .where(
                    KnowledgeEdge.adapter_name == ADAPTER,
                    or_(
                        KnowledgeEdge.source_node_id == node_id,
                        KnowledgeEdge.target_node_id == node_id,
                    ),
                )
            )
            or 0
        )
        if remaining_edges:
            continue
        deleted += _delete_count(
            session,
            delete(KnowledgeNode).where(
                KnowledgeNode.adapter_name == ADAPTER,
                KnowledgeNode.node_id == node_id,
            ),
        )
    return deleted


def _fetch_evidence_rows(session: Any, evidence_ids: list[str]) -> list[dict[str, Any]]:
    if not evidence_ids:
        return []
    rows = session.scalars(
        select(KnowledgeEvidence).where(KnowledgeEvidence.evidence_id.in_(evidence_ids))
    ).all()
    return [
        {
            "evidence_id": row.evidence_id,
            "source_type": row.source_type,
            "source_id": row.source_id,
            "evidence_type": row.evidence_type,
            "status": row.status,
            "content_length": len(row.content or ""),
            "content_preview": _clip(row.content or "", 180),
            "payload_keys": sorted((row.payload or {}).keys()),
        }
        for row in rows
    ]


def _fetch_chunk_rows(session: Any, evidence_ids: list[str]) -> list[dict[str, Any]]:
    if not evidence_ids:
        return []
    rows = session.scalars(
        select(KnowledgeEvidenceChunk)
        .where(KnowledgeEvidenceChunk.evidence_id.in_(evidence_ids))
        .order_by(KnowledgeEvidenceChunk.evidence_id, KnowledgeEvidenceChunk.chunk_index)
    ).all()
    return [
        {
            "chunk_id": row.chunk_id,
            "evidence_id": row.evidence_id,
            "chunk_index": row.chunk_index,
            "start_offset": row.start_offset,
            "end_offset": row.end_offset,
            "previous_chunk_id": row.previous_chunk_id,
            "next_chunk_id": row.next_chunk_id,
            "text_hash": row.text_hash,
            "chunker_version": row.chunker_version,
        }
        for row in rows
    ]


def _fetch_node_rows(session: Any, node_ids: list[str]) -> list[dict[str, Any]]:
    if not node_ids:
        return []
    rows = session.scalars(
        select(KnowledgeNode).where(KnowledgeNode.node_id.in_(node_ids)).order_by(KnowledgeNode.node_type)
    ).all()
    return [
        {
            "node_id": row.node_id,
            "node_type": row.node_type,
            "stable_key": row.stable_key,
            "canonical_name": row.canonical_name,
            "aliases": row.aliases,
            "status": row.status,
            "properties": _compact_properties(row.properties),
        }
        for row in rows
    ]


def _fetch_edge_rows(session: Any, edge_ids: list[str]) -> list[dict[str, Any]]:
    if not edge_ids:
        return []
    rows = session.scalars(
        select(KnowledgeEdge).where(KnowledgeEdge.edge_id.in_(edge_ids)).order_by(KnowledgeEdge.relation_type)
    ).all()
    return [
        {
            "edge_id": row.edge_id,
            "source_node_id": row.source_node_id,
            "target_node_id": row.target_node_id,
            "relation_type": row.relation_type,
            "confidence_label": row.confidence_label,
            "confidence_score": round(float(row.confidence_score), 3),
            "status": row.status,
            "properties": _compact_properties(row.properties),
        }
        for row in rows
    ]


def _fetch_edge_evidence_rows(session: Any, edge_ids: list[str]) -> list[dict[str, Any]]:
    if not edge_ids:
        return []
    rows = session.scalars(
        select(KnowledgeEdgeEvidence).where(KnowledgeEdgeEvidence.edge_id.in_(edge_ids))
    ).all()
    return [{"edge_id": row.edge_id, "evidence_id": row.evidence_id} for row in rows]


def _fetch_edge_chunk_rows(session: Any, edge_ids: list[str]) -> list[dict[str, Any]]:
    if not edge_ids:
        return []
    rows = session.scalars(
        select(KnowledgeEdgeEvidenceChunk).where(KnowledgeEdgeEvidenceChunk.edge_id.in_(edge_ids))
    ).all()
    return [
        {
            "edge_id": row.edge_id,
            "evidence_id": row.evidence_id,
            "chunk_id": row.chunk_id,
        }
        for row in rows
    ]


def _fetch_recent_normalization_rules(session: Any) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(KnowledgeNormalizationRule)
        .where(KnowledgeNormalizationRule.adapter_name == ADAPTER)
        .order_by(KnowledgeNormalizationRule.updated_at.desc())
        .limit(12)
    ).all()
    return [
        {
            "rule_type": row.rule_type,
            "raw_value": row.raw_value,
            "canonical_value": row.canonical_value,
            "status": row.status,
            "confidence": round(float(row.confidence), 3),
            "source": row.source,
        }
        for row in rows
    ]


def _table_column_names(inspector: Any, table_name: str) -> set[str]:
    return {str(column["name"]) for column in inspector.get_columns(table_name)}


def _required_chunk_manifest_columns() -> set[str]:
    return {
        "chunk_index",
        "start_offset",
        "end_offset",
        "previous_chunk_id",
        "next_chunk_id",
        "text_hash",
        "chunker_version",
    }


def _forbidden_chunk_manifest_columns() -> set[str]:
    return {"content", "payload"}


def _intersects(left: list[Any] | tuple[Any, ...] | set[Any] | None, right: set[str]) -> bool:
    if not left or not right:
        return False
    return bool({str(item) for item in left if item}.intersection(right))


def _cognitive_card_row(row: KnowledgeCognitiveCard) -> dict[str, Any]:
    return {
        "cognitive_card_id": row.cognitive_card_id,
        "source_id": row.source_id,
        "evidence_id": row.evidence_id,
        "primary_chunk_id": row.primary_chunk_id,
        "chunk_index": row.chunk_index,
        "summary": _clip(row.summary or "", 140),
        "title_candidates": (row.title_candidates or [])[:5],
        "topic_intent_count": len(row.topic_intents or []),
        "risk_signal_count": len(row.risk_signals or []),
        "local_impact_signal_count": len(row.local_impact_signals or []),
        "supporting_text": (row.supporting_text or [])[:3],
        "schema_version": row.schema_version,
        "status": row.status,
    }


def _community_assignment_row(row: KnowledgeCommunityAssignment) -> dict[str, Any]:
    return {
        "assignment_id": row.assignment_id,
        "cognitive_card_id": row.cognitive_card_id,
        "intent_index": row.intent_index,
        "community_id": row.community_id,
        "action": row.action,
        "weight": round(float(row.weight or 0), 4),
        "confidence": round(float(row.confidence or 0), 4),
        "update_mode": row.update_mode,
        "matched_reason": _clip(row.matched_reason or "", 120),
        "reason": _clip(row.reason or "", 120),
        "status": row.status,
    }


def _community_row(row: KnowledgeGraphCommunity) -> dict[str, Any]:
    return {
        "community_id": row.community_id,
        "version_id": row.version_id,
        "projection": row.projection,
        "level": row.level,
        "parent_community_id": row.parent_community_id,
        "title": row.title,
        "summary": _clip(row.summary or "", 240),
        "member_nodes": len(row.member_node_ids or []),
        "member_edges": len(row.member_edge_ids or []),
        "evidence": len(row.evidence_ids or []),
        "chunks": len(row.chunk_ids or []),
        "status": row.status,
        "lineage_id": row.lineage_id,
        "previous_version_id": row.previous_version_id,
        "change_reason": row.change_reason,
        "metrics": _compact_properties(row.metrics),
    }


def _finding_row(row: KnowledgeGraphFinding) -> dict[str, Any]:
    return {
        "finding_id": row.finding_id,
        "community_id": row.community_id,
        "projection": row.projection,
        "finding_type": row.finding_type,
        "title": row.title,
        "statement": _clip(row.statement or "", 260),
        "cited_chunks": len(row.cited_chunk_ids or []),
        "cited_evidence": len(row.cited_evidence_ids or []),
        "supporting_edges": len(row.supporting_edge_ids or []),
        "nodes": len(row.node_ids or []),
        "confidence": round(float(row.confidence or 0.0), 3),
        "status": row.status,
        "version": row.version,
    }


def _delta_row(row: KnowledgeGraphDelta) -> dict[str, Any]:
    return {
        "delta_id": row.delta_id,
        "projection": row.projection,
        "window_name": row.window_name,
        "title": row.title,
        "summary": _clip(row.summary or "", 260),
        "community_count": len(row.community_ids or []),
        "finding_count": len(row.finding_ids or []),
        "cited_chunks": len(row.cited_chunk_ids or []),
        "cited_evidence": len(row.cited_evidence_ids or []),
        "supporting_edges": len(row.supporting_edge_ids or []),
        "nodes": len(row.node_ids or []),
        "status": row.status,
        "version": row.version,
    }


def _unassigned_signal_row(row: KnowledgeGraphUnassignedSignal) -> dict[str, Any]:
    return {
        "signal_id": row.signal_id,
        "projection": row.projection,
        "title": row.title,
        "reason": row.reason,
        "nodes": len(row.node_ids or []),
        "edges": len(row.edge_ids or []),
        "evidence": len(row.evidence_ids or []),
        "chunks": len(row.chunk_ids or []),
        "topic_tags": row.topic_tags or [],
        "impact_tags": row.impact_tags or [],
        "event_type_tags": row.event_type_tags or [],
        "relation_types": row.relation_types or [],
        "support_score": round(float(row.support_score or 0.0), 3),
        "status": row.status,
        "promoted_community_id": row.promoted_community_id,
        "promotion_attempts": row.promotion_attempts,
        "metrics": _compact_properties(row.metrics),
    }


def _entity_target_ids(node_rows: list[dict[str, Any]]) -> list[str]:
    target_ids: list[str] = []
    for row in node_rows:
        node_id = row["node_id"]
        if row["node_type"] == "event":
            target_ids.append(f"kg_card:event_card:{node_id}")
        else:
            target_ids.append(f"kg_card:node_card:{node_id}")
    return target_ids


def _demo_scope_refs(pg_state: dict[str, Any], graph_index_state: dict[str, Any]) -> dict[str, set[str]]:
    pg_ids = pg_state.get("ids") or {}
    graph_ids = graph_index_state.get("ids") or {}
    return {
        "evidence_ids": set(pg_ids.get("evidence_ids") or []),
        "chunk_ids": set(pg_ids.get("chunk_ids") or []),
        "node_ids": set(pg_ids.get("node_ids") or []),
        "edge_ids": set(pg_ids.get("edge_ids") or []),
        "target_ids": set(
            [
                *(pg_ids.get("chunk_ids") or []),
                *(pg_ids.get("entity_target_ids") or []),
                *(pg_ids.get("relation_target_ids") or []),
                *(graph_ids.get("community_target_ids") or []),
                *(graph_ids.get("finding_target_ids") or []),
                *(graph_ids.get("delta_target_ids") or []),
            ]
        ),
    }


def _hit_in_demo_scope(hit: Any, refs: dict[str, set[str]]) -> bool:
    data = hit.model_dump() if hasattr(hit, "model_dump") else dict(hit)
    hit_id = str(data.get("hit_id") or "")
    if hit_id and hit_id in refs.get("target_ids", set()):
        return True
    if _intersects(data.get("evidence_refs") or [], refs.get("evidence_ids", set())):
        return True
    if _intersects(data.get("node_refs") or [], refs.get("node_ids", set())):
        return True
    if _intersects(data.get("edge_refs") or [], refs.get("edge_ids", set())):
        return True
    return False


def _compact_hit(hit: Any) -> dict[str, Any]:
    data = hit.model_dump() if hasattr(hit, "model_dump") else dict(hit)
    return {
        "hit_id": data.get("hit_id"),
        "hit_type": data.get("hit_type"),
        "title": data.get("title"),
        "score": round(float(data.get("score") or 0.0), 4),
        "source": data.get("source"),
        "source_channels": data.get("source_channels"),
        "node_refs": data.get("node_refs"),
        "edge_refs": data.get("edge_refs"),
        "evidence_refs": data.get("evidence_refs"),
        "snippet": _clip(data.get("snippet") or "", 240),
    }


def _compact_index_refresh(index_refresh: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": index_refresh.get("mode"),
        "graph_adjacency": index_refresh.get("graph_adjacency"),
        "evidence_chunks": index_refresh.get("evidence_chunks"),
        "hybrid_chunks": index_refresh.get("hybrid_chunks"),
        "graph_index": _compact_graph_index_refresh(index_refresh.get("graph_index") or {}),
        "cognitive_index": _compact_cognitive_index_refresh(index_refresh.get("cognitive_index") or {}),
        "semantic_materials": index_refresh.get("semantic_materials"),
        "stale_hybrid_vectors_deleted": index_refresh.get("stale_hybrid_vectors_deleted"),
        "stale_semantic_documents_deleted": index_refresh.get("stale_semantic_documents_deleted"),
    }


def _compact_cognitive_index_refresh(cognitive_index: dict[str, Any]) -> dict[str, Any]:
    if not cognitive_index:
        return {}
    diagnostics = cognitive_index.get("diagnostics") or {}
    return {
        "status": cognitive_index.get("status"),
        "changed_chunks": cognitive_index.get("changed_chunks"),
        "changed_evidence": cognitive_index.get("changed_evidence"),
        "cards": cognitive_index.get("cards"),
        "all_cards": cognitive_index.get("all_cards"),
        "assignments": cognitive_index.get("assignments"),
        "communities": cognitive_index.get("communities"),
        "documents_written": cognitive_index.get("documents_written"),
        "stale_documents_deleted": cognitive_index.get("stale_documents_deleted"),
        "community_builder": diagnostics.get("community_builder"),
        "assignment_validation_errors": diagnostics.get("assignment_validation_errors"),
        "candidate_ledger": diagnostics.get("candidate_ledger"),
    }


def _compact_graph_index_refresh(graph_index: dict[str, Any]) -> dict[str, Any]:
    if not graph_index:
        return {}
    refresh_plan = graph_index.get("refresh_plan") or {}
    replacement_scope = graph_index.get("replacement_scope") or {}
    diagnostics = graph_index.get("diagnostics") or {}
    return {
        "communities": graph_index.get("communities"),
        "findings": graph_index.get("findings"),
        "deltas": graph_index.get("deltas"),
        "built_communities": graph_index.get("built_communities"),
        "built_findings": graph_index.get("built_findings"),
        "built_deltas": graph_index.get("built_deltas"),
        "documents_written": graph_index.get("documents_written"),
        "stale_documents_deleted": graph_index.get("stale_documents_deleted"),
        "refresh_action": refresh_plan.get("action"),
        "refresh_score": refresh_plan.get("score"),
        "refresh_reasons": refresh_plan.get("reasons"),
        "actual_refresh_strategy": graph_index.get("actual_refresh_strategy"),
        "replacement_scope": replacement_scope,
        "community_algorithm": diagnostics.get("community_algorithm"),
        "community_report_generator": diagnostics.get("community_report_generator"),
        "rolling_delta_count": diagnostics.get("rolling_delta_count"),
    }


def _compact_properties(value: dict[str, Any] | None) -> dict[str, Any]:
    if not value:
        return {}
    result: dict[str, Any] = {}
    for key in sorted(value.keys())[:12]:
        item = value[key]
        if isinstance(item, str):
            result[key] = _clip(item, 120)
        elif isinstance(item, (list, tuple)):
            result[key] = list(item[:6])
        else:
            result[key] = item
    return result


def _llm_proxy_summary() -> dict[str, Any]:
    health = get_llm_gateway_service().health()
    return {
        "default_provider": health.get("default_provider"),
        "default_model": health.get("default_model"),
        "model_routes": health.get("model_routes"),
        "providers": health.get("providers"),
    }


def _section(step: str, title: str) -> None:
    print(f"\n============ {step}. {title} ============")


def _clip(value: str, limit: int) -> str:
    value = " ".join(str(value).split())
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


if __name__ == "__main__":
    asyncio.run(main())
