# 同花顺 App 个股行情页数据协议逆向记录

**日期**：2026-08-02  
**目标 App**：同花顺 `com.hexin.plat.android`  
**验证设备**：真机 `PLQ110`  
**验证版本**：同花顺 `11.51.03`  
**本阶段范围**：只完成协议定位、脱离页面调用和数据验收，不接数据库、调度任务与 Web UI。

## 1. 页面能力清单

截图中的个股页实际包含两套彼此独立的数据能力。

### 1.1 动态分组

当前配置返回五组：

| 分组 | 返回字段 | 排序 |
|---|---|---|
| 冲刺涨停的股票 | 名称、代码、最新价、涨速 | 涨速降序 |
| 同花顺热榜 | 排名、名称、代码、最新价、涨幅 | Prompt 自带热度顺序 |
| 筹码低位集中 | 名称、代码、涨幅、最新价 | 涨幅降序 |
| 集合竞价弱转强 | 名称、代码、最新价、涨幅 | 涨幅降序 |
| 低估值高成长 | 名称、代码、涨幅、最新价 | 涨幅降序 |

动态分组不是固定写死在 APK 中。页面先读取远端配置，再根据每组配置中的 `promptId` 调用原生指标引擎。

### 1.2 股票排行榜

已打通以下九类排序：

| 业务名 | `onlineId` | `sortid` | `sortorder` |
|---|---|---:|---:|
| 涨幅榜 | `zhangfu` | `34818` | `0` |
| 跌幅榜 | `diefu` | `34818` | `1` |
| 快速涨幅 | `zhangsu` | `48` | `0` |
| 成交额 | `chengjiaoe` | `19` | `0` |
| 大单净量 | `dadanjingliang` | `34370` | `0` |
| 量比榜 | `liangbi` | `34311` | `0` |
| 换手率 | `huanshoulv` | `34312` | `0` |
| 主力净流入 | `zhulijingliuru` | `34391` | `0` |
| 振幅榜 | `zhenfu` | `34819` | `0` |

这九类榜单使用同一张原生表格，只是切换排序字段和正倒序。一次响应已经带回页面所需的大部分列，不需要逐列请求。

## 2. 动态分组协议

### 2.1 远端配置

```http
GET https://eq.10jqka.com.cn/open/api/dynamic_configuration/v1/config_list?key=gegufeaturelist
```

配置响应中的关键字段：

| 字段 | 含义 |
|---|---|
| `title` / `subtitle` | 分组标题与说明 |
| `promptId` | 原生 Hurricane 查询使用的 Prompt 标识 |
| `headers` | 页面展示的指标及顺序 |
| `sortHeader` | 排序指标与方向 |
| `securities` | 首屏配置槽位数 |
| `isShowRanking` | 是否展示排名 |
| `jumpUrl` | “查看更多”跳转地址 |

该接口只返回分组定义，不返回最终股票列表。

### 2.2 股票列表查询

运行时捕获到的调用链：

```text
SecuritiesRankingSliderDsl
  -> IndicatorManager.obtainClient(frame=2312)
  -> QueryClient.query(QueryParam)
  -> HurricaneSecuritiesSource(type=PROMPT_CODE)
  -> QueryCallback.onNext(IndicatorTable)
```

查询参数：

```json
{
  "frame_id": 2312,
  "start": 0,
  "count": 4,
  "hurricane_type": "PROMPT_CODE",
  "hurricane_ids": ["<promptId>"],
  "mobile_indicator_ids": ["55", "10", "34818"],
  "http_source_id": "securities-ranking-slider"
}
```

指标 `55` 是名称，`10` 是最新价，`34818` 是涨幅，`48` 是涨速。实际指标集合由每组 `headers` 决定。

动态分组需要 App 进程中的原生指标引擎。远端配置接口可独立访问，但仅靠配置接口无法取得股票结果。

## 3. 排行榜协议

### 3.1 页面业务层

页面业务层调用 `HummerUnifiedRequestBridge`：

```json
{
  "onlineId": "zhangsu",
  "protocolId": 1208,
  "pageId": 2312,
  "requestDic": "startrow=0\r\nrowcount=24\r\nmarketId=0\r\nsortorder=0\r\nsortid=48",
  "requestType": 262144
}
```

该层响应与 App 表格最接近，并能返回行业字段 `36072`，因此独立验收工具使用这一层作为默认入口。

### 3.2 底层原生表格帧

Hook 在页面运行时捕获到最终底层请求：

```text
frame = 2312
page  = 1282

startrow=0
rowcount=24
marketId=0
sortorder=0
sortid=48
```

底层帧可直接调用并返回行情指标，但不保证包含业务层补充的行业字段。两者不是冲突关系，而是业务包装层与最终通信帧的上下层关系。

### 3.3 字段字典

| 指标 ID | 字段 |
|---:|---|
| `4` / `5` | 股票代码 |
| `55` | 股票名称 |
| `10` | 最新价 |
| `34818` | 涨跌幅 |
| `48` | 涨速 |
| `19` | 成交额 |
| `34311` | 量比 |
| `34312` | 换手率 |
| `34370` | 大单净量 |
| `34391` | 主力净流入 |
| `34819` | 振幅 |
| `36072` | 行业板块 |
| `36103` | 市场标识 |

### 3.4 分页

分页由 `startrow` 和 `rowcount` 控制，不是只能读取 App 首屏。

已验证：

```text
startrow=24
rowcount=12
```

能够正常返回涨幅榜第二页 12 条数据。

`rowcount` 在当前验证工具中限制为 `1..50`，用于控制单次真机请求规模，不代表上游协议的理论最大值。

### 3.5 市场与页面筛选

截图展示的是默认 A 股全市场榜单，对应 `marketId=0`。页面上的“市场”和“过滤”属于用户筛选控件；当前 App 会将部分自定义筛选导向登录流程。截图没有给出这些控件展开后的筛选集合，因此本阶段只验收默认 A 股榜单，不推测账号相关筛选参数。

## 4. 独立验证工具

脚本：

```text
ths/scripts/validate_stock_market_protocols.py
```

完整验证：

```bash
python ths/scripts/validate_stock_market_protocols.py --count 8
```

只验证动态分组：

```bash
python ths/scripts/validate_stock_market_protocols.py --only dynamic
```

只验证指定排行榜：

```bash
python ths/scripts/validate_stock_market_protocols.py \
  --only ranking \
  --ranking quick \
  --ranking large_order \
  --count 20
```

验证第二页：

```bash
python ths/scripts/validate_stock_market_protocols.py \
  --only ranking \
  --ranking rise \
  --start 24 \
  --count 12
```

保存原始验收结果：

```bash
python ths/scripts/validate_stock_market_protocols.py \
  --output /tmp/ths-stock-protocol-validation.json
```

脚本只调用远端配置和真机进程内桥接，不导入 `smart-fund-server`，也不会写数据库或投递任务。

## 5. 2026-08-02 真机验收结果

| 项目 | 结果 |
|---|---|
| 远端动态分组配置 | 通过，取得 5 组 |
| 五组原生股票查询 | 通过 |
| 涨幅、跌幅、涨速、成交额 | 通过 |
| 大单净量、量比、换手率 | 通过 |
| 主力净流入、振幅 | 通过 |
| 行业字段 | 通过业务层响应取得 |
| 第二页分页 | 通过 |
| 完整矩阵 | 5 个动态分组 + 9 类排行榜，失败 0 |

“筹码低位集中”在本次验证时返回总数 `0`，App 同时显示“暂无数据”，属于上游当前结果为空，不是协议或解析失败。

## 6. 能力边界

当前结论按运行依赖分级：

| 能力 | 独立程度 |
|---|---|
| 动态分组配置 | 可脱离 App 的公开 HTTPS |
| 动态分组股票列表 | 已脱离页面，但仍依赖 App 进程原生指标引擎 |
| 股票排行榜 | 已脱离页面，但仍依赖 App 进程原生 UnifiedRequest 核心 |
| 数据库存储、定时调度、服务 API | 本阶段明确未实现 |

下一阶段若正式接入采集系统，应以本脚本的协议结果为输入，再单独设计快照表、幂等键、交易时段调度和查询 API，不能把逆向探针直接当作数据库写入任务。

## 7. 页面交互与账号边界

2026-08-02 在真机匿名状态下继续验证了个股页的三个辅助入口：

| 页面入口 | 真机行为 | 协议结论 |
|---|---|---|
| 市场 | 跳转手机验证码登录 | 属于账号筛选配置；默认 A 股全市场仍可用 `marketId=0` 匿名查询 |
| 过滤 | 跳转手机验证码登录 | 属于账号自定义条件，不作为匿名行情协议的前置依赖 |
| 榜单编辑 | 跳转手机验证码登录 | 用于配置榜单展示项，不影响底层 9 类排行榜直接调用 |

榜单标签区域本身不是横向滚动容器。在该区域执行横向手势会切换 A 股、港股等顶层市场页面，不会显示隐藏榜单。因此不能通过 UI 手势枚举榜单，协议验收以页面运行日志、业务层 `onlineId` 和最终原生帧参数为准。

动态分组配置中的跳转信息也属于协议交付的一部分。验收脚本现会保留：

- `jump_url` 与 `subtitle_jump_url`：卡片和副标题的跳转地址；
- `data_code` 与 `key`：远端配置身份；
- `is_show_ranking`：是否展示排名；
- `highlight_tag`：页面热点标签。

至此，截图涉及的动态分组、同花顺热榜和股票排行榜数据协议均已打通。账号登录只限制页面自定义筛选与榜单编辑，不限制本阶段已经确认的默认榜单数据调用。
