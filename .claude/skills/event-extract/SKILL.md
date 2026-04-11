---
name: event-extract
display_name: 事件抽取
icon: auto_fix_high
description: 从财经新闻中抽取结构化事件，输出 JSON 用于事件流分析和交易决策
category: finance
user-invocable: true
commands:
  - id: extract
    name: 单条新闻抽取
    description: 输入新闻 title + content，输出结构化事件 JSON
    input: text
    executor: claude
    estimated_time: 30

  - id: extract_batch
    name: 批量新闻抽取
    description: 从 ft_news 表批量读取未抽取的新闻，调用 AI 抽取后写入 ft_events
    input: none
    executor: claude
    estimated_time: 300
---

# 事件抽取 Skill

## 概述

将原始财经新闻（ft_news）转换为结构化的金融事件（ft_events），用于：
- 事件流聚合（按行业 + 时间窗口检测发酵中的事件）
- 事件 → 基金/股票映射
- 决策打分模型的输入
- 历史类比交易（基于事件 embedding）

## 系统定位

```
ft_news (原始新闻)  ──────►  AI 事件抽取  ──────►  ft_events (结构化事件)
   ↑                              │                     │
   │                              │                     ▼
聚合层采集                    本 Skill            事件流聚合 / 决策引擎
```

---

## 事件抽取目标

把一条新闻转化为一个结构化事件 JSON，需要满足 4 个目标：

1. **可结构化**：方便 SQL 查询和过滤
2. **可量化**：有强度/情绪/置信度数值
3. **可关联**：能映射到行业/公司/基金
4. **可演化**：同一事件流可以聚合多条新闻

---

## 输出 Schema

每条新闻必须输出符合以下格式的 JSON（**只输出 JSON，不要解释文字**）：

```json
{
  "is_event": true,
  "event_type": "policy",
  "event_subtype": "support",
  "title": "事件标题（10-30 字，提炼自新闻标题）",
  "summary": "事件摘要（30-80 字，包含关键实体和影响）",

  "entities": {
    "organizations": ["发改委"],
    "industries": ["AI", "算力"],
    "companies": [],
    "regions": ["中国"]
  },

  "impact": {
    "direction": "positive",
    "strength": 0.85,
    "scope": "industry",
    "duration": "mid"
  },

  "sentiment": 0.7,
  "novelty": 0.6,
  "certainty": 0.9
}
```

如果新闻**不是可交易事件**（如纯播报、广告、八卦、个股财报数字罗列等），输出：
```json
{"is_event": false, "reason": "纯行情播报，无实质事件"}
```

---

## 字段定义

### `is_event` (bool)
- `true`：是值得抽取的事件
- `false`：噪音新闻，跳过

### `event_type` (string)
枚举值：
- `policy` — 政策（部委文件、法规、监管）
- `macro` — 宏观（CPI/PMI/利率/汇率/经济数据）
- `industry` — 行业（产业新闻、技术突破、市场动态）
- `company` — 公司（财报、并购、人事、产品发布）
- `capital` — 资金/资本（北向、机构调仓、IPO/再融资）
- `rumor` — 传闻（待验证消息）

### `event_subtype` (string, 可选)
具体细分，常见值：
- `support`(扶持) / `restrict`(限制) / `warning`(警示) / `launch`(发布)
- `merge`(并购) / `expand`(扩张) / `cooperate`(合作) / `dispute`(纠纷)

### `title` (string)
事件标题。**不要原样照搬新闻标题**，要提炼成 10-30 字的核心事件描述。

### `summary` (string)
事件摘要。30-80 字，必须包含：
- **谁**做了什么（主体 + 动作）
- **影响**了什么（行业/公司/市场）
- 关键数字（如果有）

### `entities` (object)
事件涉及的实体：
- `organizations`: 机构（如"发改委"、"证监会"、"美联储"）
- `industries`: 行业（如"AI"、"新能源"、"半导体"，使用通用名词）
- `companies`: 公司（股票代码或公司名）
- `regions`: 地区（如"中国"、"美国"、"欧盟"）

### `impact.direction` (enum)
- `positive` — 利好
- `negative` — 利空
- `neutral` — 中性

### `impact.strength` (float, 0-1)
影响强度：
- `0.9-1.0` — 重大政策（国务院文件、降息、突发事件）
- `0.7-0.9` — 部委文件、行业重大新闻
- `0.5-0.7` — 一般新闻
- `0.3-0.5` — 弱影响
- `< 0.3` — 几乎无影响（建议设为 is_event=false）

### `impact.scope` (enum)
- `market` — 影响整个市场（如降息）
- `industry` — 影响特定行业
- `company` — 仅影响个别公司

### `impact.duration` (enum)
- `short` — 短期（1-3 天）
- `mid` — 中期（1-2 周）
- `long` — 长期（数月以上）

### `sentiment` (float, 0-1)
市场情绪反应预估：
- `> 0.7` — 强烈正面
- `0.5` — 中性
- `< 0.3` — 负面

### `novelty` (float, 0-1)
**这条信息对市场是否新颖**：
- `> 0.7` — 新提法、新政策、首次出现
- `0.5` — 已有趋势的延续
- `< 0.3` — 老消息重复、市场已充分预期

### `certainty` (float, 0-1)
信息可信度：
- `0.9-1.0` — 官媒、官方文件（gov / pboc / csrc / 发改委）
- `0.8-0.9` — 财联社、东方财富、新华社
- `0.6-0.8` — 一般财经媒体
- `0.4-0.6` — 自媒体、雪球
- `< 0.4` — 传闻、未证实消息

---

## 抽取规则

### 必须遵守

1. **只输出 JSON**，不要任何解释文字、markdown 代码块标记
2. **JSON 必须合法**，可以被 `json.loads()` 直接解析
3. **不要捏造**：如果新闻没有提到具体公司/数字，对应字段留空数组或 0
4. **行业名词标准化**：
   - "人工智能" / "AI" / "AIGC" → 都用 `"AI"`
   - "新能源汽车" / "新能源车" / "电动车" → 都用 `"新能源车"`
   - "半导体" / "芯片" → 都用 `"半导体"`
5. **政策来源 → certainty 高**：source=gov/csrc/pboc 时 certainty 至少 0.9
6. **快讯类（cls）→ certainty 0.85**

### 必须过滤（is_event=false）

- 纯行情播报："xx 涨 5%" 而无原因
- 广告软文：基金推广、券商广告
- 八卦娱乐：明星新闻、不相关社会新闻
- 个股财报数字罗列：除非有重大变化（业绩翻倍、重大并购等）
- 重复新闻：已经被 fingerprint 去重过的不会进来，但还要警惕措辞略不同的重复

---

## 示例

### 示例 1：政策利好（典型）

**输入**：
```
title: 发改委：加快推进算力基础设施建设，支持AI产业发展
content: 国家发改委发布通知，将加快推进算力基础设施建设，重点支持人工智能产业发展...到2027年要建成全国统一的算力网络。
source: gov
```

**输出**：
```json
{
  "is_event": true,
  "event_type": "policy",
  "event_subtype": "support",
  "title": "发改委加快算力基础设施建设支持AI产业",
  "summary": "发改委发布通知加快算力基础设施建设，重点支持AI产业，2027年建成全国统一算力网络。",
  "entities": {
    "organizations": ["发改委"],
    "industries": ["AI", "算力", "数据中心"],
    "companies": [],
    "regions": ["中国"]
  },
  "impact": {
    "direction": "positive",
    "strength": 0.85,
    "scope": "industry",
    "duration": "long"
  },
  "sentiment": 0.8,
  "novelty": 0.7,
  "certainty": 0.95
}
```

### 示例 2：纯播报（应过滤）

**输入**：
```
title: 锂电池板块涨 3.25%
content: 截至下午收盘，锂电池板块上涨 3.25%，领涨股欣旺达涨停。
source: sina
```

**输出**：
```json
{"is_event": false, "reason": "纯行情播报，无实质事件驱动"}
```

### 示例 3：公司新闻

**输入**：
```
title: 宁德时代宣布与特斯拉续签长期供货协议
content: 宁德时代今日公告，与特斯拉签署2026-2030年动力电池供货协议，预计金额超千亿元。
source: cls
```

**输出**：
```json
{
  "is_event": true,
  "event_type": "company",
  "event_subtype": "cooperate",
  "title": "宁德时代与特斯拉续签千亿动力电池长约",
  "summary": "宁德时代与特斯拉续签2026-2030年动力电池供货协议，金额超千亿元。",
  "entities": {
    "organizations": [],
    "industries": ["新能源车", "动力电池"],
    "companies": ["宁德时代", "特斯拉"],
    "regions": ["中国", "美国"]
  },
  "impact": {
    "direction": "positive",
    "strength": 0.75,
    "scope": "industry",
    "duration": "long"
  },
  "sentiment": 0.85,
  "novelty": 0.7,
  "certainty": 0.85
}
```

### 示例 4：宏观数据

**输入**：
```
title: 国家统计局：3月CPI同比上涨1.0%
content: 国家统计局发布数据，3月份全国居民消费价格指数（CPI）同比上涨1.0%，环比上涨0.3%。
source: gov
```

**输出**：
```json
{
  "is_event": true,
  "event_type": "macro",
  "event_subtype": "data_release",
  "title": "3月CPI同比上涨1.0%通胀温和",
  "summary": "统计局发布3月CPI数据，同比上涨1.0%，环比上涨0.3%，通胀温和。",
  "entities": {
    "organizations": ["国家统计局"],
    "industries": [],
    "companies": [],
    "regions": ["中国"]
  },
  "impact": {
    "direction": "neutral",
    "strength": 0.5,
    "scope": "market",
    "duration": "short"
  },
  "sentiment": 0.5,
  "novelty": 0.4,
  "certainty": 0.95
}
```

---

## 使用方式

### 单条抽取（手动测试）

```bash
echo '{
  "title": "发改委：加快算力基础设施建设",
  "content": "...",
  "source": "gov"
}' | claude -p "$(cat .claude/skills/event-extract/SKILL.md)" --dangerously-skip-permissions
```

### 批量抽取（系统调用）

由 `agg_event_extraction` task 自动调用：
1. 从 ft_news 读取 `event_extracted=false` 的新闻
2. 预筛选过滤明显无关的（按 source/length/keywords）
3. 批量传给 claude（每次 1-5 条）
4. 解析返回的 JSON
5. 写入 ft_events
6. 标记 ft_news.event_extracted=true

---

## 调用约定

**输入格式**（user message）：
```
请从下面的新闻中抽取事件：

[新闻 1]
title: ...
content: ...
source: ...
published_at: ...

[新闻 2]
...
```

**输出格式**：
- 如果只有 1 条新闻：直接输出 JSON
- 如果有多条新闻：输出 JSON 数组，每条对应一个事件 JSON

**严格要求**：
- 只输出 JSON，不要 markdown 代码块（```json）
- 不要加任何解释文字
- 不能失败时输出空字符串，至少输出 `{"is_event": false, "reason": "..."}`
