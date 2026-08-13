# 案例：同花顺（com.hexin.plat.android）

## 目录

1. 基本信息与目标
2. 已确认的数据通道
3. `realDataRequest` 原生调用链
4. 无页面运行所需状态
5. 回调、并发和清理规则
6. 已验证结果
7. 息屏冻结与保活
8. 本次少走弯路的顺序
9. 历史认证与问财经验
10. 短线精灵事件流
11. 服务器运行与正式采集
12. 当前边界
13. Hook APK 生产签名
14. 板块 QueryClient 路由与性能优化

## 1. 基本信息与目标

- **包名**: `com.hexin.plat.android`
- **加固**: 360 加固
- **核心代码**: `/home/yuyang/frida-test/ths/app/src/main/java/com/yuyang/thshook/MainHook.java`
- **详细市场数据调研**: `/home/yuyang/frida-test/smart-fund-server/docs/3. 实施方案/1. 数据源对接/12. 同花顺App客观市场数据接口逆向调研记录.md`

本案例先后覆盖基金接口、问财认证和客观市场数据。最新阶段的目标不是页面自动化，而是：

1. 确认五维分析页面实际使用的数据源；
2. 区分公开 HTTPS、`realDataRequest` 和 `UnifiedRequestBridge`；
3. 脱离 Activity、WebView 和 JavaScript Bridge 调用实时指标；
4. 评估设备侧无页面 Sidecar 是否具备生产稳定性。

## 2. 已确认的数据通道

同一个页面同时使用三类通道：

| 通道 | 能力示例 | 当前完成度 |
|---|---|---|
| 公开 HTTPS | ETF 估算、估值阈值、部分情绪和历史数据 | 可独立重放，仍需稳定性和授权验收 |
| `realDataRequest` | 大盘资金、情绪、A50、逆回购、汇率等时间序列 | 已脱离页面，仍依赖 App 原生运行时 |
| `UnifiedRequestBridge` | 大盘异动、集合竞价、排行和短线精灵事件流 | 已实现无 WebView 的进程内直接调用 |

重要结论：浏览器 Network 看不到全部数据；找到 JSBridge 也不代表已经找到业务核心。

## 3. `realDataRequest` 原生调用链

页面参数由以下三项组成：

- `key`：指标 Key；
- `requestParam`：通常为 `{key} data`；
- `requestChannel`：通常为 `{key}_channel`。

静态与运行时分析确认调用链：

```text
RealTimeDataRequestJsInterface
  -> ro9（请求模型）
  -> qo9（协调器与回调）
  -> uo9（全量请求）
  -> to9（订阅请求）
  -> CommunicationService
```

协议参数：

| 请求 | frame | page | method |
|---|---:|---:|---|
| 全量 | `6004` | `1006` | `hget` |
| 订阅 | `6005` | `1007` | `sub` / `unsub` |

请求类型为 `262144`，文本包含 `method`、App 内用户标识和 `param`。认证与用户标识必须由
App 自身运行时生成，案例文档不记录实际值。

`qo9` 回调结果对象可读取：

- 状态；
- 响应类型；
- Key；
- 数据正文。

## 4. 无页面运行所需状态

最初只反射构造请求对象时没有结果，根因不是协议参数错误，而是缺少页面正常启动时建立的运行态。

已确认的最小依赖：

1. 目标 Application 已完成初始化；
2. 使用 App 自身 Context 启动旧版 `CommunicationService`；
3. 将 App 阶段切换到活跃状态；
4. 在主 Looper 创建并启动原生 Client；
5. App 内部身份和行情长连接可用。

当前版本实际使用旧通信服务路径。判断新旧通信路径时应先检查运行时开关，不要只根据 APK 中同时
存在的两个实现进行猜测。

## 5. 回调、并发和清理规则

### 5.1 回调完成条件

一次调用可能先收到订阅确认，再收到全量数据。`response_type=1` 的订阅成功只表示通道注册完成，
不能作为指标结果；一次性接口只接受 `response_type=0` 的全量正文。

### 5.2 主线程要求

Client 创建、启动和清理均通过主 Looper 执行。早期版本在工作线程清理后，首次请求成功但后续请求
不稳定；把完整生命周期放回主线程后连续调用恢复正常。

### 5.3 串行要求

全量和订阅请求使用固定帧号、页面号及内部路由。未证明底层支持并发前，全局串行执行请求；每次
获得全量结果后立即注销并等待清理结束。

## 6. 已验证结果

在不保留前台 Activity、不创建目标 WebView 的情况下，已连续获取：

- 大盘情绪；
- 逆回购；
- 富时 A50；
- 大盘资金。

这证明 `realDataRequest` 已经脱离页面和 JSBridge。但它仍使用同花顺 App 包内类、身份和通信
服务，因此完成度是“App 进程内直接调用”，不是“独立协议 Client”。

## 7. 息屏冻结与保活

### 7.1 现象

ColorOS 息屏后，目标 UID 被 Oplus Hans 冻结，原生请求进入发送阶段但没有接收回调。前台
Service 和 `PARTIAL_WAKE_LOCK` 都不能阻止该冻结。

### 7.2 定位方法

同时检查：

- `dumpsys activity hans checkfreeze <uid>`；
- App UID cgroup 的冻结状态；
- 目标进程和通信 Service；
- 原生请求的 send/receive Hook；
- 行情 TCP 连接是否仍存在。

系统帮助没有给出完整 Hans 参数时，拉取 Oplus system service JAR，直接反汇编命令分发方法。
由此确认 `addwl` 不是只传包名，而是要求 UID、包名和模式参数。UID 必须运行时查询，不能写死。

### 7.3 关键结论

将 UID 移出 Hans 管理集合可以阻止它继续被冻结，但不能恢复冻结期间已经失效的行情长连接。
白名单和断线重连是两个独立问题：

```text
允许后台运行 != 长连接仍然有效
```

当前仍需补齐连接状态检测、通信服务重建、首次请求预热和长时间息屏验收。

## 8. 本次少走弯路的顺序

以后继续逆向同花顺原生数据时，使用以下顺序：

1. 先列出页面模块并判断三类通道，不要一开始只抓 HTTP；
2. 对公开 HTTPS 先做独立重放；
3. 对 JSBridge 记录 Handler、输入和回调；
4. 沿 Java 接口追到混淆请求类和通信 Service；
5. Hook 最小请求/回调点，确认帧号、页号和响应类型；
6. 在页面打开状态下测试直接调用；
7. 退到后台测试；
8. 去掉 Activity 和 WebView，只启动 Service；
9. 连续执行多个 Key，修复线程、清理和路由冲突；
10. 最后测试息屏、冻结、网络切换、进程重启和设备重启。

以下假设已经证伪，不要重复尝试：

- “WebView Bridge 就是最底层接口”；
- “第一个成功回调就是指标数据”；
- “Client 在任意线程清理都一样”；
- “固定协议页天然支持并发”；
- “前台 Service + WakeLock 就能解决 ColorOS 息屏”；
- “解除冻结后旧长连接会自动恢复”；
- “厂商 `dumpsys` 命令参数可以根据名称猜出来”。

## 9. 历史认证与问财经验

### 9.1 认证参数位置

历史基金接口的设备和账户认证参数位于 URL Query，Cookie 和会话 Token 位于 Header。直接重放时
必须复制真实请求中的参数位置，不能按常见习惯自行改放 Header。

### 9.2 WebView Cookie

问财关键 Cookie 存在 WebView Cookie 数据库中。数据库路径随 WebView 版本、多进程和 App 更新
变化，必须动态探测；只读打开，并控制请求频率。关键 Cookie 值不得写入日志、案例或仓库。

### 9.3 Token 级风控

问财的验证码状态可能绑定 Token，而不只是 IP。逆向调试阶段连续重放会污染当前会话，应该先低频
验证，并保留更换测试会话的能力。

## 10. 短线精灵事件流

### 10.1 已确认协议

个股异动、板块异动和大笔委托都通过 Unified 请求的 `protocol_id=1004`、`page_id=6002` 获取，
但使用不同订阅 Key：

| 数据 | Key | data_id_list | 当前单次上限 |
|---|---|---|---:|
| 个股成交异动 | `dxjl_free` | `1074269398,1074269399,1074269404,1074269405,592572,592574,527739,527735,1073744628,1073744629` | 500 |
| 板块异动 | `block_dxjl` | `1,2,3,4` | 500 |
| 大笔委托 | `dbwt` | `133990,133991` | 500 |

请求使用 `action=subscribe`，读取结果后必须使用相同 Key 和参数执行 `unsubscribe`。当前已确认的类型
包括特大主动买卖、急速拉升、猛烈打压、挂单拉升和挂单打压。

### 10.2 返回结构与身份

单条事件核心字段为：

```text
color, dataid, marketcode, selfstock, stockcode, stockname, time, value
```

`time` 是业务事件时间，不能使用抓取时间替代。下游以交易日、流类型、市场、标的、事件类型和规范化
载荷指纹生成稳定事件 ID，同一事件重复轮询幂等写入。

### 10.3 缓冲区行为

`max_msg_num` 是返回上限，不是分页。连续请求可能得到数量相近但成员不同的集合，尤其是大笔委托。
这不是接口随机制造重复，而是订阅缓冲区返回了不同子集。正确做法是定时轮询并保存集合并集，而不是：

- 只取首页展示的两条；
- 假设单次 500 条就是完整历史；
- 用整份响应 JSON 的哈希判断是否重复；
- 因第二轮新增记录就认定幂等失败。

## 11. 服务器运行与正式采集

### 11.1 运行架构

当前服务器链路为：

```text
systemd -> Android 30 x86_64 AVD -> Native Bridge -> Magisk/LSPosed
-> 同花顺进程内探针 -> ADB forward -> 宿主机恢复代理 -> THSClient
```

真机用于确认 UI 口径和发现协议；服务器模拟器用于运行已经确认的调用。生产采集不依赖前台页面，也
不要求 Frida 常驻。

### 11.2 恢复代理

宿主机代理负责串行调用、生成唯一 `onlineId`、识别 HTTP/传输错误和 `errorCode=-131`，必要时重启
App、等待通信初始化并只重放一次。探针和代理只监听 loopback。

### 11.3 数据库接入

正式采集任务每 30 秒扫描一次有效交易时段，将三类事件分别保存为：

- `ths_stock_anomaly`；
- `ths_sector_anomaly`；
- `ths_large_order`。

页面级 `market_anomaly` 快照只保留曲线、计数和少量最近事件，完整事件全部拆成事件级记录，避免每
30 秒重复保存数百条大 JSON。首次接入可强制 bootstrap，正常休市时跳过。

生产验收曾一次写入 407 条个股异动、408 条板块异动和 253 条大笔委托；第二轮补充了接口首次未
返回的独立缓冲区成员。按稳定业务字段检查未发现同一事件重复行。

## 12. 当前边界

已完成：

- 页面客观数据源分类；
- 多项公开接口重放；
- `realDataRequest` 原生核心调用链；
- `UnifiedRequestBridge` 无 WebView 进程内调用；
- 短线精灵个股、板块和大笔委托事件流；
- 无 Activity、无 WebView 的连续指标调用；
- Linux 虚拟机、故障恢复代理与 systemd 无人值守运行；
- 事件级定时采集和数据库幂等入库；
- Oplus Hans 冻结根因和调试命令参数定位。

未完成：

- 独立于同花顺 App 的协议 Client；
- App 或 Android 镜像升级后的自动兼容检测；
- 长时间稳定性和多实例容灾验收；
- 授权与生产使用边界确认。

当前实现可描述为内部使用的服务器侧 Android 数据桥接服务，不能描述为脱离 APK 的独立行情 Client。

## 13. Hook APK 生产签名

同花顺 Hook 包为 `com.yuyang.thshook`。生产虚拟机上已安装 Hook 使用的证书 SHA-256 为：

```text
9505d29aca6006eef0fe473b68e4eea03afd41019cf5435a5ee6963262559dbf
```

生产签名私钥的持久来源是：

```text
服务器 119.23.227.187:1113
/home/yuyangruan/.android/debug.keystore
alias: androiddebugkey
```

本机 `/home/yuyang/.android/debug.keystore` 当前证书指纹为：

```text
ae47c19e090dce17729490521a4e8bd4ede9c8a0ca8d984d67cc673f608fce69
```

两者不同。因此以下操作禁止用于生产覆盖：

- `./gradlew installDebug`；
- 直接安装未经重新签名的 `app-debug.apk`；
- 使用本机默认 `~/.android/debug.keystore` 签名；
- 签名冲突后直接卸载 `com.yuyang.thshook`。

正确流程是临时从生产服务器读取上述 keystore，校验其 SHA-256，签名构建产物，再校验 APK 指纹后执行 `adb install -r`。临时私钥副本只能放在权限为 `0600` 的临时文件中，并通过 `trap` 或部署流程在结束时删除。私钥不存在或指纹不匹配时必须停止部署。

当前环境的可执行命令为：

```bash
set -euo pipefail
EXPECTED_CERT="9505d29aca6006eef0fe473b68e4eea03afd41019cf5435a5ee6963262559dbf"
KEYSTORE=/tmp/thshook-production-debug.keystore
SIGNED_APK=/tmp/thshook-production.apk
trap 'rm -f "$KEYSTORE" "$SIGNED_APK"' EXIT

scp -P 1113 -i /tmp/deploy_key_smart_fund_113 \
  yuyangruan@119.23.227.187:/home/yuyangruan/.android/debug.keystore \
  "$KEYSTORE"
chmod 600 "$KEYSTORE"

keytool -list -v -keystore "$KEYSTORE" \
  -storepass android -alias androiddebugkey

/home/yuyang/android-sdk/build-tools/34.0.0/apksigner sign \
  --ks "$KEYSTORE" \
  --ks-key-alias androiddebugkey \
  --ks-pass pass:android \
  --key-pass pass:android \
  --out "$SIGNED_APK" \
  /home/yuyang/frida-test/ths/app/build/outputs/apk/debug/app-debug.apk

ACTUAL_CERT="$(/home/yuyang/android-sdk/build-tools/34.0.0/apksigner \
  verify --print-certs "$SIGNED_APK" \
  | sed -n 's/^Signer #1 certificate SHA-256 digest: //p')"
test "$ACTUAL_CERT" = "$EXPECTED_CERT" || {
  echo "Hook APK certificate mismatch: $ACTUAL_CERT" >&2
  exit 1
}

# 自动校验通过后，才允许上传并执行 adb install -r。
```

`/home/yuyangruan/.android/debug.keystore` 是生产签名私钥的长期来源，禁止删除或覆盖。仅删除下载到本机 `/tmp` 的临时副本。

通用检查和私钥丢失后的受控重装流程见 `knowledge/apk-signing-deployment.md`。

## 14. 板块 QueryClient 路由与性能优化

### 14.1 问题现象与错误方向

旧的板块核心任务将热门板块、五类板块排行、四种指标和资金流包装成 26 组高层调用，再依次经过
App Bridge。一次全量采集约 501.8 秒，超过调度周期。排查期间以下方向均被实验证伪：

- 单纯增加 HTTP/TCP 连接即可获得更多 Native 并发；
- 所有 `QueryClient` 查询都能使用随机 frame ID；
- Legacy Ranking 是稳定的通用板块入口；
- 一次提交 618 个显式证券一定比分类请求更快；
- 只要扩大代理超时，就能解决 30/40 秒长尾；
- 为每个数据类型增加 App 或模拟器是首选扩容方案。

Legacy Ranking 连续 20 次调用只成功 7 至 11 次，存在回调丢失，不能继续承载板块核心行情。

### 14.2 原生 App 的真实刷新模型

板块页面不是每刷新一个榜单就创建一套新页面请求，而是在页面生命周期内复用 QueryClient：

```text
进入页面
  -> IndicatorManager.obtainClient(frameId=2312)
  -> query / 切分类 / 切排序
  -> 回调更新表格
离开页面
  -> QueryClient.cancel()
```

这解释了原生 App 为什么能频繁刷新：它复用 App 内部查询对象和通信运行态，页面层只改变证券源、字段和排序条件。

### 14.3 两类 QueryClient 的路由边界

实测确认同一套 Indicator SDK 存在两种不同边界：

| 请求族 | frame ID | 并发结论 |
|---|---|---|
| 纯 Hurricane 板块全集 | 可使用随机 frame | 8 路并发已验证 |
| 显式 `Security` 的 MobileHQ 行情 | 必须使用 `2312` | 单 App 内容量 1 |

显式证券查询使用随机 frame 时，请求对象能够构造并进入调用，但 30 秒内没有业务回调。只有原生页面已注册的
`frameId=2312` 能稳定收到结果。因此 `2312` 是 App 进程内 `IndicatorManager` 的路由身份，不是 HTTP
连接或 Bridge Socket 的身份。

```text
多个 HTTP/TCP 连接
  -> 同一个同花顺 App 进程
  -> 同一个 IndicatorManager
  -> 同一个 frameId=2312
```

多建连接不会生成多个 `2312`。只有独立 App 进程才会拥有独立 Manager，但当前吞吐已满足要求，没有必要用
多 Android 用户或多模拟器增加运行和认证成本。

### 14.4 回调完成和可选字段

MobileHQ 会分多帧返回字段。收盘后涨速指标 `36251` 合法为空，但名称、指数、涨幅和量比仍完整。如果完成条件
要求所有请求字段出现，请求就会持续收到其他更新并最终卡满总超时。

当前规则为：

1. 所有请求字段到齐时立即完成；
2. 否则在全部证券及必需字段到齐后，只给可选字段一个有界收尾窗口；
3. 结果返回后在主 Looper 调用 `QueryClient.cancel()`；
4. 不用零值、旧值或推测值伪造收盘后涨速。

通用实现应进一步优先观察“证券 Key 集合是否停止增长”，避免同一证券重复字段更新不断重置静默计时。详见
`knowledge/native-query-routing-and-concurrency.md`。

### 14.5 最终采集架构

```text
并发 Hurricane：行业 / 概念 / 风格 / 地域全集
  -> 四个显式证券列表
  -> MobileHQ frame 2312 单 Lane 依次补齐核心行情
  -> Client 合并 618 个板块
  -> 短时共享同一原始快照
  -> 本地派生 all/industry/concept/style/region 与多个排序指标
```

限流必须按请求族而不是只按 HTTP 路径：

- Legacy Ranking：1；
- 显式证券 MobileHQ：1；
- 纯 Hurricane：8；
- Unified/JSBridge：独立容量；
- App HTTP 和可独立重放的 Direct HTTP：不占 Native 锁。

代理通过请求体是否包含非空 `securities` 区分同一路径中的纯 Hurricane 和 MobileHQ 查询。

### 14.6 关键实验与最终结果

- 行业 90、概念 390、风格 105、地域 33，共 618 个板块；
- 名称、指数、涨幅、量比覆盖率 100%；
- 收盘后涨速为空，符合业务语义；
- 618 个板块核心行情墙钟 13.393 秒；
- 五类行情榜单、五类涨停数、三类资金流和三类热门板块共 16 个能力单元；
- 完整生产隔离测试 16/16 成功，总墙钟 14.787 秒；
- 旧链路约 501.8 秒，最终链路不需要多 App 实例。

一次提交 618 个显式证券曾导致 Hook 连接被关闭，因此最终保留四个类别大小的 MobileHQ 请求。批量越大不一定越
快，必须以成功率、字段覆盖率和墙钟共同验收。

### 14.7 调试过程保护

容量测试会暂停正式 Scheduler 和 Worker，测试脚本必须使用 `trap` 恢复服务，并给远端 Python 设置总 timeout。
诊断输出只打印行数、字段覆盖率和耗时，不打印完整板块行，避免数 MB 输出撑爆本地会话。测试完成后检查：

- 正式服务均为 active；
- 没有残留 benchmark/probe 进程；
- 数据库最新快照继续推进；
- WSL 中没有堆积超时的 `wsl.exe`/SSH 包装进程。

这次故障说明：先定位 Native 身份域和回调路由，再讨论线程数、连接数和实例数。否则工程层的“并发优化”只会
把稳定串行请求变成随机超时。
# 2026-08-04 行情页对比卡与股票排行实机验证

- `profile_yester`: `protocolId=4052`, `pageId=2312`, `requestDic=startrow=0\r\nrowcount=1\r\nadddata=1`。字段 `34818` 为“昨日涨停表现”，`35284/35286` 为领涨股名称/涨幅。实机返回 `4.60% / 欣天科技 / 20.04%`。
- `profile_dxp`: `protocolId=1264`, `pageId=2312`，请求证券固定为 `marketlist=16|16`、`stocklist=1B0300|1B0852`，即沪深300与中证1000，不是国证2000。字段 `55/4/34818` 分别为名称、代码、涨幅。实机返回 `1.27% / 2.82%`。
- `hqMarketZdt`: 行情页 WebView 调用 `callNativeHandler("hqMarketZdt", "")`；返回 `time/zt/dt` 分钟数组及 `all.data` 汇总。页面优先取最后一个分钟点，缺失时回退 `all.data`。实机 15:00 返回 `140/1`。
- 股票排行使用 `protocolId=1208`, `pageId=2312`，每个 Tab 单独请求：`startrow=0`, `rowcount=N`, `sortorder`, `sortid`, `newrealtime=0`, `selfstockcustom=1`。不要抓全市场总表后本地排序，该做法会命中不同缓存快照。涨幅榜为 `onlineId=zhangfu`, `sortid=34818`, `sortorder=0`；字段 `4/55/10/34818/36072` 为代码/名称/最新/涨幅/行业。
- 动态选股分组的 Hurricane `PROMPT_CODE` 返回值可能只有 `code/market`，即便传了 `mobile_indicator_ids` 也可能得到空指标；不能把它当完整行情。应收集首页精选证券后，用 `protocolId=1264` 批量补全，`columnorder=55|4|34338|10|34818|48`，并按 Hurricane 返回的 `marketlist/stocklist` 原序请求。实机对 `002364/002217/600818/002361` 成功补出名称、最新、涨幅、涨速。
- 大盘异动由两条数据共同构成：曲线为 `dpydLine/protocolId=1229/pageId=2312`，参数 `fstrend=1, stockcode=1A0001, marketcode=16`；事件点为 `marketLabel/protocolId=1002/pageId=6000` 订阅 `mobiledpyd`。`mobiledpyd` 常返回 `[{value: "{\"data\":[...]}"}]`，必须继续解开 `value -> data -> info`，过滤外层和明细的 `isdraw`，并用 `ctime/time` 映射到 09:30–11:30、13:00–15:00 的 241 点位置。否则只能得到曲线，无法复现同花顺事件标记。
- `dpydLine` 的纵轴不是按曲线 min/max 自适应。App 读取 `extDataDict[6]` 作为昨收中轴，以 `max(abs(extDataDict[9]-昨收), abs(extDataDict[8]-昨收))` 生成上下对称区间；左轴显示指数点位，右轴显示相对昨收百分比。忽略该规则会导致原始241点相同但图形视觉走势与 App 不同。

## 2026-08-04 热门板块与板块统计补充验证

- 热门概念/行业 Hurricane 可能只返回 `code/market/heat`，名称和涨幅为空。应按原序使用 `protocolId=1264`、`pageId=2312` 和 `columnorder=55|4|34338|10|34818|48` 补齐板块名称与涨幅；板块市场号为 `48`。
- 热门榜展示必须选择每个 `sector_type` 最新一次采集的完整批次，不能把 `list_latest` 返回的不同批次按证券代码直接混排，否则会出现旧交易日残留、重复排名和同榜多个第一名。
- 显式证券 MobileHQ/Hurricane 桥单次最多接受 100 行。概念、风格等全集必须按最多 100 个证券顺序分片，再合并结果；超过限制会返回 `row count too large, exceed 100 limit`。
- App“板块统计-全部”口径是行业板块与概念板块的并集，不包含风格和地域板块。把四类全部合并会使“昨日表现”等风格指数挤占涨幅榜，无法与 App 对齐。
- 收盘后生产验证：热门概念名称/涨幅覆盖完整；板块统计“全部-涨幅”前九依次为元件、共封装光学(CPO)、光刻机、电子化学品、先进封装、半导体、存储芯片、玻璃基板、中芯国际概念，与 App 截图口径一致。
- `hot_block_list` 每次返回多个历史交易日的轮动榜，但某日掉出新榜单的旧板块快照仍会留在数据库。读取时必须按 `sector_type + metric + source_date` 选择最新 `fetched_at` 的完整批次，再按 `rank` 排序；不能把各板块代码的 latest 行直接拼接。App 首页一屏为最近 3 个交易日。
- 热门板块的热度是用户关注度信号，不是收盘即冻结的价格指标。实机在 00:21 后仍持续改变概念、行业、指数热度，因此 `hot` 三个任务不能套用 A 股开盘窗口；应保持独立的 60 秒调度。涨幅字段可以维持收盘值，热度与排名继续更新。
- 热度目前来自主动 Hurricane 查询而非稳定推送。生产采用自适应轮询：交易时段 60 秒、普通闭市和午休 5 分钟、01:00–07:00 深夜 30 分钟；分钟调度未到期时仅检查本地最新 `fetched_at` 后跳过，不占用 Native QueryClient。真正的实时订阅流则全天保持连接，仅对回调内容按快照键去重，不使用交易窗口拦截。
