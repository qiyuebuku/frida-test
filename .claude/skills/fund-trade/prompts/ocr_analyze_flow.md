# `/fund-trade ocr-analyze [action]` 执行流程

**截屏持仓分析** - 从截屏助手的 `sa_ocr_records` 表读取 OCR 识别数据，结合市场行情和基金信息进行综合分析。

与 `run` 命令的区别：**只分析不交易**，不查经验知识库，不复盘，不执行买卖操作。

支持的 action 类型（对应截屏助手的功能按钮）：
| action | 含义 | 分析方向 |
|--------|------|----------|
| `fund_holdings` | 持仓分析（长截屏） | 基金持仓全貌、配置建议 |
| `ocr` | 识别文字 | 通用文字内容分析 |
| `table` | 表格识别 | 表格数据解读 |
| `full_page` | 完整页面 | 页面内容综合分析 |

默认 action 为 `fund_holdings`。

---

## Step 1: 从服务端读取 OCR 数据

通过 client.py 命令读取最新 OCR 记录：

```bash
cd /home/yuyang/frida-test/.claude/skills/fund-trade

# 获取最新一条指定 action 的 OCR 记录（输出全文）
python client.py ocr-latest fund_holdings

# 获取最新多条
python client.py ocr-latest fund_holdings --count 3

# 查看 OCR 记录列表
python client.py ocr-records --count 10

# 按 action 筛选列表
python client.py ocr-records fund_holdings --count 5
```

对应 API 接口：
- `GET /api/ocr/latest?action=fund_holdings&count=1` — 最新记录
- `GET /api/ocr/records?action=fund_holdings&limit=10` — 记录列表

**检查数据有效性**：
- 如果没有记录 → 提示用户先在截屏助手中触发持仓截图
- 如果记录时间超过 24 小时 → 提示数据可能过时，建议重新截图

**优先使用结构化数据**：
- 记录中的 `structured_data` 字段包含 AI 预处理后的结构化 JSON（基金名称、代码、金额、收益等已提取完毕）
- 如果 `structured_data` 存在且有效，**直接使用它**，不需要再解析 `raw_text` 或 `markdown_text`
- 仅当 `structured_data` 为空时，才回退到用 `raw_text`/`markdown_text` 手动解析持仓列表

## Step 2: 市场环境采集

```bash
cd /home/yuyang/frida-test/.claude/skills/fund-trade

# 并行执行
python client.py market_overview > /tmp/ft_ocr_market.json
python client.py news_overview > /tmp/ft_ocr_news.json
python client.py hot_board > /tmp/ft_ocr_hotboard.json
```

读取这 3 个文件，提取：
1. 大盘走势（上证/深证/创业板涨跌）
2. TOP 5 关键新闻事件
3. 热门板块涨跌

## Step 3: 基金级数据补充

对 OCR 中识别到的**有基金代码的基金**，批量查询详情（可并行，最多 10 只）：

```bash
python client.py <code> detail
python client.py <code> rank
```

如果 OCR 中的基金代码在同花顺基金池中也存在，额外获取量化信号：
```bash
python client.py evaluate
```

## Step 4: 综合分析

读取 `prompts/ocr_analyze.md` 模板，填入数据，由 Claude 输出分析报告。

分析维度：
1. **持仓全貌**：总资产、基金数量、收益情况
2. **配置分析**：按类型（QDII/A股/债券/商品）分组，计算占比
3. **风险提示**：集中度过高、单一市场风险、汇率风险
4. **市场关联**：当前市场热点与持仓的关联性
5. **操作建议**：哪些基金建议加仓/减仓/持有，附理由

## Step 5: 保存分析结果（可选）

如果识别到的持仓数据质量足够（基金代码 + 金额齐全），保存到 `ft_alipay_positions` 表复用：
```bash
echo '<JSON>' | python fund_db.py alipay-save
```
