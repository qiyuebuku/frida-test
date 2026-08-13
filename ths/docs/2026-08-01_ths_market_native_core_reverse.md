# 同花顺客观市场数据原生核心逆向记录

**日期**：2026-08-01  
**目标 App**：同花顺 `com.hexin.plat.android`  
**验证版本**：11.51.03  
**核心代码**：`app/src/main/java/com/yuyang/thshook/MainHook.java`  
**当前结论**：`realDataRequest` 已脱离 Activity、WebView 和 JSBridge。服务器通过 Android
x86_64 虚拟机、Native Bridge、Magisk、LSPosed 和进程内探针运行同花顺原生核心；它已经能够
作为无人值守的服务器侧数据桥接服务运行，但仍不是脱离 APK 的纯 Linux Client。

## 1. 本次目标

同花顺“五维分析”页面展示了市场广度、盘中异动、集合竞价、ETF 资金、市场情绪、全球市场、
货币、估值和债市等客观数据。本次逆向目标分为三个层级：

1. 找到各页面模块实际使用的数据源；
2. 将依赖页面的原生指标调用改造成无页面的 App 进程内调用；
3. 评估能否进一步独立为服务器侧 Client。

本次不涉及基金交易，不记录账户、Cookie、Token、设备身份或其他认证值。

## 2. 页面不是单一 HTTP 数据源

目标页面是 App 原生壳与 WebView 混合实现，实际存在三类数据通道：

| 通道 | 特征 | 代表能力 | 当前状态 |
|---|---|---|---|
| 公开 HTTPS | 标准 GET/POST JSON | ETF 估算、估值阈值、部分历史数据 | 已独立重放 |
| `realDataRequest` | JSBridge 进入原生实时指标通道 | 大盘资金、市场情绪、A50、逆回购、汇率 | 已脱离页面 |
| `UnifiedRequestBridge` | 帧号、页面号、请求字典和订阅 Key | 大盘异动、集合竞价、排行、行情曲线 | 已实现无 WebView 的进程内直接调用 |

页面 Network 只能覆盖公开 HTTPS。只抓浏览器网络会遗漏原生行情协议；只停留在 JSBridge 又会
继续依赖页面，因此需要从 JavaScript Handler 继续追踪到底层请求对象。

## 3. 逆向过程

### 3.1 页面与请求清单

先通过截图、UI 层级、WebView 页面资源和 Hook 日志建立映射：

```text
页面卡片
  -> JavaScript Handler / onlineId
  -> Bridge 参数
  -> Java 接口
  -> 请求模型
  -> 原生通信协议或公开 HTTPS
```

该步骤确认同一个页面同时混用了公开 API、实时指标通道和原生订阅协议，避免将所有模块错误地
归入同一种 Client。

### 3.2 从 JSBridge 继续下钻

`realDataRequest` 的页面参数只有：

- `key`；
- `requestParam`；
- `requestChannel`。

静态搜索 JavaScript 接口与运行时低频 Hook 共同确认底层调用链：

```text
RealTimeDataRequestJsInterface
  -> ro9
  -> qo9
  -> uo9（全量请求）
  -> to9（订阅请求）
  -> CommunicationService
```

混淆类的定位依据不是类名语义，而是构造参数、父类、方法签名、请求常量、帧号和真实调用顺序。

### 3.3 DEX 指令辅助分析

jadx 无法稳定还原部分系统或混淆方法时，直接解析 DEX 指令。该方式用于确认：

- 请求和响应方法；
- 调试命令分支；
- 参数数量；
- 状态切换；
- Oplus Hans 命令格式。

经验是只打印目标字符串附近的小段指令，不要一次反汇编整个大方法，否则噪声会掩盖控制流。

## 4. `realDataRequest` 协议细节

### 4.1 请求模型

页面通常按以下规则生成参数：

```text
key            = <indicator_key>
requestParam   = <indicator_key> data
requestChannel = <indicator_key>_channel
```

### 4.2 全量与订阅请求

| 类型 | 实现类 | frame | page | method | requestType |
|---|---|---:|---:|---|---:|
| 全量 | `uo9` | `6004` | `1006` | `hget` | `262144` |
| 订阅 | `to9` | `6005` | `1007` | `sub` / `unsub` | `262144` |

请求文本由以下字段组成：

```text
method=<method>
userid=<App 运行时用户标识>
param=<requestParam 或 requestChannel>
```

实际用户标识由 App 运行时提供，不进入文档或日志。

### 4.3 回调结构

`qo9` 的回调结果对象可读取：

- 请求状态；
- 响应类型；
- 指标 Key；
- 数据正文。

同一调用可能先收到订阅确认，再收到全量数据：

| 响应类型 | 含义 | 一次性查询处理 |
|---|---|---|
| `0` | 全量业务数据 | 作为最终结果 |
| `1` | 订阅确认或增量通道数据 | 不能提前结束请求 |

早期实现把第一个成功回调当成结果，只获得订阅成功 JSON。修正后仅在目标全量数据到达时结束。

## 5. 进程内 HTTP 探针

`MainHook.java` 内建立了实验性 HTTP 探针：

| 端点 | 用途 |
|---|---|
| `GET /health` | 检查注入进程和探针是否存活 |
| `POST /native/realtime` | 传入 Key、请求参数和 Channel，调用原生实时指标核心 |
| `POST /native/unified` | 传入协议号、页面号和请求字典，调用原生 UnifiedRequest 核心 |

探针分别直接构造 `ro9` / `qo9` 与 `HummerUnifiedRequestBridge.RequestModel`，不调用目标页面的
JavaScript，也不要求目标 WebView 存在。Unified 请求通过 `ld0.call(Object[])` 接收解析后的
`head/body`，一次性请求完成后调用原生移除逻辑取消订阅。

当前探针仅用于逆向验证，尚未完成正式鉴权和接口治理。生产化前应限制为 loopback、增加操作
白名单与鉴权，并确保日志不包含身份参数。

## 6. 无页面运行依赖

### 6.1 只构造 Client 为什么没有结果

最初直接调用请求对象没有异常，但也没有数据。对比正常打开页面与纯后台启动日志后，确认缺少的
不是参数，而是 App 生命周期状态：

1. `CommunicationService` 未进入可处理行情请求的状态；
2. App 阶段仍处于非活跃值；
3. Client 生命周期依赖主 Looper。

### 6.2 最小初始化

已验证的最小流程：

1. 等待目标 Application 初始化；
2. 使用 App 自身 Context 启动 `CommunicationService`；
3. 调用 App 已有阶段切换方法进入活跃状态；
4. 在主线程创建 Client、注册回调并发起请求；
5. 收到全量数据后在主线程注销和清理 Client。

运行时开关表明当前版本使用旧通信服务路径。APK 中同时存在新旧实现不能证明实际运行的是新路径，
必须以运行时配置和调用日志为准。

## 7. 线程、串行和资源清理

### 7.1 主线程清理

首次请求成功、后续请求异常的根因之一，是 Client 在工作线程清理。将创建、启动和清理都投递到
主 Looper，并等待清理完成后，连续请求恢复正常。

### 7.2 固定路由不能盲目并发

全量和订阅使用固定 frame/page 及内部路由。并发请求可能产生覆盖、串包或回调丢失，因此当前
通过全局锁串行执行。只有底层路由能力被证明支持并发后，才能取消该限制。

### 7.3 一次性调用必须注销

一次性指标查询不能留下持续订阅。获得全量结果后立即调用原生清理方法，并等待主线程清理结束，
再处理下一请求。

### 7.4 Unified 取消订阅存在异步竞态

`HummerUnifiedRequestBridge.removeRequest()` 不只清理本地监听器，还会异步发送取消订阅帧。若释放
全局锁后立即复用同一 frame/page 路由，上一请求的取消帧可能与下一请求竞争，表现为下一请求固定
等待约 20 秒并返回 `errorCode=-131`。当前在主线程清理完成后额外保留 800ms 静默期，再释放
Unified 全局锁。真实连续调用中，该处理消除了大多数第二次请求超时。

该等待不是业务限流，而是当前版本原生协议的资源释放边界。App 升级后必须重新验证等待时长，不能
假定 800ms 永久有效。

## 8. 已验证结果

在不保留前台 Activity、不创建目标 WebView 的情况下，已连续验证：

| 指标 Key | 结果 |
|---|---|
| `sjdp_temperature_hs` | 成功返回市场情绪数据 |
| `sjdp_reverse_repurchase` | 成功返回逆回购数据 |
| `sjdp_ftse_a50` | 成功返回 A50 数据 |
| `sjdp_market_capital` | 成功返回大盘资金分钟序列 |

结论：`realDataRequest` 已完成“无页面的 App 进程内调用”。它仍依赖同花顺 App 进程、类加载器、
身份和通信服务，因此不能描述为完全脱离 App。

## 9. ColorOS/Oplus Hans 息屏问题

### 9.1 现象

设备息屏后，请求已经进入全量和订阅发送方法，但没有 receive 回调，最终超时。检查发现目标 UID
被 Oplus Hans 冻结，对应 cgroup 进入冻结状态。

### 9.2 已证伪方案

以下措施不能单独解决问题：

- 将 `CommunicationService` 提升为前台 Service；
- 请求期间持有 `PARTIAL_WAKE_LOCK`；
- 使用 Android 标准进程 unfreeze 命令；
- 只执行一次 Hans unfreeze；
- 解冻后继续复用冻结期间的旧长连接。

### 9.3 Hans 命令定位

系统帮助没有完整说明参数。拉取 Oplus system service JAR 并反汇编 `OplusHansManager` 的命令
分发逻辑后，确认：

```text
dumpsys activity hans addwl <uid> <package> <mode>
```

该命令不是只传包名。UID 必须运行时查询，模式含义可能随系统版本变化，不能写死为通用规则。
实测将目标 UID 移出 Hans 管理集合后不会立即再次冻结。

### 9.4 仍未解决的问题

白名单只解决后续冻结。冻结期间已经失效的行情 TCP/认证会话不会自动恢复，因此请求仍可能超时。
下一步必须增加：

1. 原生连接状态检查；
2. 超时后的 Client 与通信 Service 重建；
3. 首次请求预热；
4. 白名单持久性验证；
5. 多小时息屏与网络切换测试。

## 10. 本次错误路径与经验

| 错误假设 | 实际情况 |
|---|---|
| 页面 Network 能覆盖全部数据 | 多项核心指标走原生通道 |
| JSBridge 已是最底层能力 | Bridge 只是参数适配和回调路由 |
| 能构造请求对象就能调用 | 还依赖 Service、App 阶段和主线程 |
| 第一个成功回调就是结果 | 可能只是订阅 ACK |
| 清理线程不重要 | 错误线程会污染后续请求 |
| 固定协议页可以直接并发 | 未验证前必须串行 |
| 前台 Service 和 WakeLock 足够保活 | OEM freezer 仍会冻结 UID |
| 加白名单等于连接恢复 | 旧长连接必须独立重建 |
| `dumpsys` 参数可猜 | 应反汇编系统服务确认参数分支 |

## 11. 当前代码状态

`MainHook.java` 当前原型包含：

- Application 与目标 ClassLoader 捕获；
- 原生实时指标核心 Hook；
- `realDataRequest` 进程内调用；
- 全量/订阅响应区分；
- 全局串行；
- 主线程创建和清理；
- `CommunicationService` 启动和阶段初始化；
- 实验性前台 Service 与请求 WakeLock；
- `/health`、`/native/realtime` 和 `/native/unified` 探针。

其中前台 Service、WakeLock 和 Hans 调试均属于实验性稳定性探索，不代表息屏问题已经解决。

## 12. 完成度与后续工作

### 已完成

- 页面客观数据模块与三类数据通道映射；
- 多项公开 HTTPS 独立重放；
- `realDataRequest` 的原生请求链路与协议常量；
- `UnifiedRequestBridge` 的无 WebView 原生调用；
- 无 Activity、无 WebView 的连续指标调用；
- 回调类型、主线程清理和串行问题修复；
- Oplus Hans 冻结根因及命令参数定位。

### 未完成

- 面向公网或跨主机调用的探针鉴权；
- 独立于 App 的纯 Linux 协议 Client；
- App、Android 系统镜像升级后的兼容性自动检测；
- 授权和生产使用边界确认。

## 13. Linux 服务器虚拟化运行

### 13.1 运行架构

服务器使用以下链路运行目标 App：

```text
systemd
  -> Android 30 x86_64 AVD
  -> Native Bridge 执行同花顺 ARM64 so
  -> Magisk Zygisk
  -> LSPosed 注入 com.hexin.plat.android
  -> THSHook 进程内原生调用
  -> ADB forward 127.0.0.1:49300 -> Android 18900
  -> 宿主机恢复代理 127.0.0.1:49301
```

Pine 的 ARM 原生库不能加载到 x86_64 ART 进程，因此服务器版本不再直接加载 `libpine.so`。
项目保留 Pine 风格的最小兼容接口，底层委托给 LSPosed/XposedBridge 完成 Java Method Hook。

同花顺 11.51.03 在当前 AVD 中没有触发虚拟机拒绝运行，不需要修改 App 的环境检测逻辑，也没有
加入伪造设备环境的绕过代码。

### 13.2 systemd 服务

部署文件位于 `deployment/android-emulator/`：

| 服务 | 职责 |
|---|---|
| `ths-android-emulator.service` | 冷启动 AVD，进程退出后自动重启 |
| `ths-collector-bridge.service` | 等待开机、维护端口转发、拉起 App、执行健康检查和故障恢复 |

`49300` 是面向调试的原始 ADB 转发，`49301` 是 `THSClient` 使用的正式宿主机入口。恢复代理负责：

1. 串行转发 `/native/*` 请求；
2. 为每个 Unified 请求生成唯一 `onlineId`；
3. 识别 HTTP/传输错误、`errorCode=-131` 和原生超时；
4. 故障时重启目标 App，等待 Hook 健康并预留 20 秒通信初始化时间；
5. 对原请求只重放一次，避免无界重试。

桥接服务连续三次健康检查失败后也会重启目标 App。实测主动停止同花顺进程后，单次业务请求可自动
拉起 App 并恢复，不需要人工进入页面。探针与恢复代理都只监听宿主机 loopback，不映射到公网。

虚拟机内另安装了 Frida Server 17.6.2 x86_64 作为按需动态分析工具。生产采集不依赖 Frida，
该进程默认不启动，也不配置公网监听。

### 13.3 服务器验收结果

冷启动后已验证：

| 检查项 | 结果 |
|---|---|
| Android 冷启动与开机完成检测 | 通过 |
| LSPosed 目标进程注入 | 通过 |
| `GET /health` | 通过 |
| 市场情绪 `sjdp_temperature_hs` | 通过 |
| 逆回购 `sjdp_reverse_repurchase` | 通过 |
| A50 `sjdp_ftse_a50` | 通过 |
| 大盘资金 `sjdp_market_capital` | 通过 |
| 大盘异动 `marketLabel` | 通道通过，非交易日返回空事件集合 |
| 集合竞价 `jjData` | 通过，返回上一交易日热点股、涨停股和板块 |
| 大盘异动曲线 `dpydLine` | 通过，返回上一交易日分钟曲线 |
| Unified 连续三次调用与资源清理 | 通过 |
| 目标 App 被停止后的自动恢复 | 通过 |
| 请求头、正文、响应和明文认证日志脱敏 | 通过 |

进一步以 `THSClient` 连续执行大盘异动与集合竞价，四条不同 Unified 路由均成功。后续两轮调用中
出现一次 `ggList` 原生超时，恢复代理完成 App 重启和原请求重放，总耗时约 46 秒；恢复后连续七条
Unified 请求均在约 0.8-2.2 秒内完成。由此可确认无人值守恢复链路有效，但不能把原生通道描述为
零故障或低延迟强保证。

### 13.4 运行边界

- 当前服务依赖同花顺 APK、Android 用户数据、Native Bridge 和 LSPosed，不是纯 Linux 协议实现；
- 进程内 HTTP 探针尚未设计面向公网的鉴权协议，因此必须保持 loopback；
- LSPosed Manager 显示系统级 SEPolicy 未完全激活，但目标 App 作用域在 SELinux Enforcing 下已经
  完成冷启动、注入和连续原生调用；当前不依赖 Permissive 模式；
- App 或系统镜像升级后，需要重新执行冷启动、Hook 注入和指标回归测试。

## 14. 后续验收矩阵

| 用例 | 通过条件 |
|---|---|
| Service-only 冷启动 | 已通过：不创建目标页面和 WebView，首次请求返回完整数据 |
| 连续不同 Key | 无串包、无残留订阅、无第二次请求超时 |
| 相同 Key 重复调用 | 结果独立且 Client 均被清理 |
| 并发调用 | 明确串行排队，或证明底层并发安全 |
| 息屏一小时 | 进程、服务和请求可用，断线可自动恢复 |
| 网络切换 | Wi-Fi/移动网络切换后自动重连 |
| App 重启 | 已通过：Hook 和探针自动恢复；仍需长期重复测试 |
| 虚拟机重启 | 已通过：systemd 冷启动后模块与采集链路自动恢复 |
| App 升级 | 类、方法、帧号或字段变化被明确检测 |
| 安全检查 | RPC 不暴露身份信息，不监听不受控公网地址 |

当前链路已经具备服务器无人值守运行和进程故障恢复能力，可作为内部数据桥接服务持续验收。
完成长时间稳定性、升级兼容性、安全和授权边界验证前，不能把它描述为正式授权的独立行情源。

## 15. 短线精灵事件流与正式采集接入

### 15.1 从首页摘要追到完整事件流

首页“大盘异动”卡片只展示少量最新个股事件。进入“短线精灵”后可以看到完整的成交异动与大笔
委托列表，说明首页数据不是完整数据集，而是同一原生事件源的展示截断。

真机验证采用以下顺序：

1. 记录首页两条事件及其时间、名称、类型和值；
2. 点击进入短线精灵，确认完整列表和筛选 Tab；
3. 清空 Hook 日志后分别切换成交异动、大笔委托和板块；
4. 对比 `HummerUnifiedRequestBridge.RequestModel` 的 `protocolId`、`pageId` 和请求字典；
5. 将页面请求字典原样交给 `/native/unified`，用页面值核对响应字段；
6. 增大 `max_msg_num`，连续调用并比较集合交集与差集。

该方法避免了两个错误方向：根据首页两条记录反推接口上限，以及在服务器模拟器中盲猜页面点击路径。
真机负责确认 UI 口径和发现请求，模拟器只负责复现已经确认的调用。

### 15.2 Unified 协议

三条完整事件流使用相同的 Unified 路由：

```text
protocolId = 1004
pageId     = 6002
action     = subscribe / unsubscribe
stock_list = all
```

不同流的请求参数如下：

| 流 | key | data_id_list | 响应字段 |
|---|---|---|---|
| 个股成交异动 | `dxjl_free` | `1074269398,1074269399,1074269404,1074269405,592572,592574,527739,527735,1073744628,1073744629` | `dxjl` 或 `dxjl_free` |
| 板块异动 | `block_dxjl` | `1,2,3,4` | `block_dxjl` |
| 大笔委托 | `dbwt` | `133990,133991` | `dbwt` |

当前请求统一使用 `max_msg_num=500`。一次性调用在拿到业务结果后，必须使用相同 Key、事件类型和标的
范围执行 unsubscribe，并等待 Unified 原生清理完成。

### 15.3 字段和类型

单条事件原始字段为：

```json
{
  "color": "-65536",
  "dataid": "133990",
  "marketcode": "17",
  "selfstock": "0",
  "stockcode": "688336",
  "stockname": "三生国健",
  "time": "1785481020",
  "value": "141手"
}
```

已由页面与响应共同确认的编码包括：

| dataid | 含义 |
|---:|---|
| `592572` | 特大主动买 |
| `592574` | 特大主动卖 |
| `1074269404` | 急速拉升 |
| `1074269405` | 猛烈打压 |
| `133990` | 挂单拉升 |
| `133991` | 挂单打压 |

其余编码保留原始 ID，只有在页面口径被实际验证后才补中文枚举，不能根据颜色或数值方向猜测。

### 15.4 `max_msg_num` 与缓冲区行为

实测 `max_msg_num` 从 40 提高到 100、200、500 后，单次返回量随之增加，但 500 并不会构成稳定分页。
连续两次调用可能出现：

```text
count 接近
overlap 很高
only_first > 0
only_second > 0
```

例如大笔委托连续请求曾分别返回不同的 253 条子集；个股异动也可能在两次调用中交换部分成员。这表明
上游返回的是订阅缓冲区的当前子集，而不是具有 offset/cursor 的静态历史页。

因此完整性策略是周期轮询并持久化集合并集。单次响应、首页展示数量或某次返回的 500 上限都不能被
当成完整历史边界。

### 15.5 正式采集模型

`smart-fund-server` 已将该能力接入 `collect_ths_market_events`：

- Scheduler 每 30 秒投递一次；
- 仅在有效交易窗口常态运行；
- 首次无数据或人工验收可强制 bootstrap；
- 数据写入现有 `ft_market_snapshots`；
- 每条原生事件独立保存，不把数百条事件重复塞进聚合快照。

对应数据类型：

| data_type | 内容 |
|---|---|
| `ths_stock_anomaly` | 个股成交异动 |
| `ths_sector_anomaly` | 板块异动 |
| `ths_large_order` | 大笔委托 |

事件 ID 包含交易日、流类型、市场、标的、事件类型和规范化载荷指纹。业务事件时间来自原始 `time`，
抓取时间只用于计算延迟。数据库唯一约束使任务重试和重复轮询保持幂等；缓冲区后续返回的新事件则
自然补充入库。

页面级 `market_anomaly` 快照只保留大盘曲线、事件计数和少量最近事件，用于总览展示；完整事件历史
必须查询上述事件级类型。

### 15.6 生产验收

生产环境强制 bootstrap 的一轮结果为：

| 类型 | 事件级记录 |
|---|---:|
| 个股异动 | 407 |
| 板块异动 | 408 |
| 大笔委托 | 253 |

第二轮轮询后板块和大笔委托数量增加，进一步检查发现新增记录是上游首次未返回的不同事件，不是同一
业务事件重复建行。按市场、代码、事件类型、时间和值分组未发现重复。

验收时必须分别回答三个问题：

1. 同一业务事件是否拥有稳定身份；
2. 重复轮询是否产生业务重复；
3. 上游缓冲区每次返回的集合是否完全相同。

前两项当前通过，第三项明确不成立，因此需要持续轮询而不能依赖单次全量。

## 16. A 股板块页逆向补充

### 16.1 页面配置是首要入口

板块页运行时配置文件为：

```text
/data/user/0/com.hexin.plat.android/files/hexinApp/hx_native_pkg/
  AStockSector/2.0/AStockSector/AStockSector.json
```

该文件完整声明了页面模块、数据源、参数、字段、排序和跳转目标。逆向此类 Kamis/Hummer 页面时，
应先提取运行时配置，再通过 Hook 日志确认真实请求，不应只根据 UI 文案猜协议页号。

### 16.2 旧行情直接回调

原生请求工厂 `uzu.a()` 的 `H(frame, page, ivu, requestText)` 支持为单次请求注册 `ivu` 回调。
`qmu` 是请求派发器而不是响应回调，早期把它当成回调目标会导致请求已发送但结果无法归属。

当前 Hook 探针 `/native/ranking-debug` 已改为：

1. 调用 `uzu.a().H(...)` 创建请求；
2. 使用动态代理实现 `ivu`；
3. 在回调中接收 `StuffBaseStruct`；
4. 复用 `HummerUnifiedRequestBridge.getResponseJsonObj` 转换响应；
5. 递归序列化整数键 Map、Collection 和数组；
6. 请求结束后释放一次性回调资源。

该路径已经重放：

| 模块 | frame | page | 关键参数 |
|---|---:|---:|---|
| 板块统计 | 2312 | 1358 | `sortid=34818` |
| 板块景气度 | 2312 | 4104 | `sortname=sector_prosperity_all` |
| 商品联动 | 2312 | 4104 | `sortname=sector_goods_futures` |

### 16.3 热门板块是复合数据源

热门板块不是单个旧行情请求。页面配置使用 `SIFHurricane` 生成板块列表和热度，再通过
`SIFMobihq1264` 补充行情涨幅：

```text
概念: hurricaneId=cn_concept
行业: hurricaneId=industry_l1
排序: ths-hot-data-minute-attention-rate
Source-Id: AStockSector
```

因此旧行情表中只出现名称、代码、市场和涨幅是正常结果，热度必须从 Hurricane 通道获取。任何用
涨幅、成交额或本地加权分数伪造热度的实现都不符合页面口径。

### 16.4 当前生产状态

- `QueryClient` / `HurricaneSecuritiesSource` 已支持热门板块无页面查询；
- `SectorMainFlow` 已确认行业、概念、地域三类固定页面与资金字段；
- `mobileweb_PlateChangeChart` 热点轮动已通过 App HTTP 通道独立重放；
- `EtfIndustryOpportunityCard` 行业机会榜已通过 App HTTP 通道独立重放；
- 板块统计、资金、景气度和商品联动均已迁移到服务器虚拟机；
- Bridge 保持 loopback，仅由服务端 Client 通过宿主机恢复代理访问；
- Client 对原生请求进行串行化，成功后等待订阅清理，并对回调超时重试一次。

LSPosed 调用 `MainHook.entry` 时本地 so 路径为空。该运行方式复用 Xposed Bridge，不能执行
`System.load(null)`；入口已经按空路径跳过本地库加载。修复后虚拟机冷启动日志能够看到
`Using LSPosed hook bridge` 和 `Pine initialized`，`/probe` 返回
`mode=injected_core_probe`。

### 16.5 板块分类页与涨停数分类

真机 UI 操作和 Hook 捕获确认，`frame=2312` 下的板块分类页面不是共用 `1358`：

```text
all=1358
industry=1209
concept=1297
style=4046
region=1337
```

涨停数不是旧行情页本地计算值，而是 `SIFHurricane` 的 `up_down_limit_up_num`。分类参数为：

```text
all=[cn_concept, industry_l1, region, tszs]
industry=[industry_l1]
concept=[cn_concept]
style=[tszs]
region=[region]
```

请求使用 `Source-Id=sif-quoter-dataapi-sector-statistics`。这些值均来自同一 App 版本的实际页面请求，已经在服务器虚拟机完成无人值守重放。
