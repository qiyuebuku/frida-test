# `/fund-trade portfolio-analyze` 执行流程

**通用持仓分析** - 基于 handler 预读的截图 OCR 持仓数据，采集市场行情，综合分析。

与 `run` 命令的区别：**只分析不交易**，不查经验知识库，不复盘，不执行买卖操作。

---

## 输入

handler 已完成 OCR 数据读取，以下数据已注入到你的上下文中：
- `structured_data`（结构化 JSON）：基金名称、代码、金额、收益等已提取
- `raw_text` / `markdown_text`：原始 OCR 文本（作为补充）

**优先使用 `structured_data`**，仅当其为空时回退到 `raw_text`/`markdown_text` 手动解析。

---

## Step 1: 市场环境采集

```bash
cd /home/yuyang/frida-test/.claude/skills/fund-trade

# 并行执行
python client.py market_overview
python client.py news_overview
python client.py hot_board
```

提取：
1. 大盘走势（上证/深证/创业板涨跌）
2. TOP 5 关键新闻事件
3. 热门板块涨跌

## Step 2: 基金级数据补充

对 OCR 中识别到的**有基金代码的基金**，批量查询详情（可并行，最多 10 只）：

```bash
python client.py <code> detail
python client.py <code> rank
```

如果 OCR 中的基金代码在同花顺基金池中也存在，额外获取量化信号：
```bash
python client.py evaluate
```

## Step 3: 综合分析

参考 `prompts/portfolio_analyze.md` 模板格式，结合所有数据输出分析报告。

分析维度：
1. **持仓全貌**：总资产、基金数量、收益情况
2. **配置分析**：按类型（QDII/A股/债券/商品）分组，计算占比
3. **风险提示**：集中度过高、单一市场风险、汇率风险
4. **市场关联**：当前市场热点与持仓的关联性
5. **操作建议**：哪些基金建议加仓/减仓/持有，附理由
