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
15. 交易 SDK 只读查询逆向（查询目录/双观察者/进程内调用器/防崩铁律/真实数据/列主序/funds 受阻/滚轮 UI）
16. 登录链全图、跨设备 token 设备指纹移植与生产部署（14 级拦截器/五大阻断修复/互踢/自愈）

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
- Oplus Hans 冻结根因和调试命令参数定位；
- 交易只读查询 6 端点（持仓/资金/当日与历史委托成交）真实数据验证，含 38 条历史委托语义记录、dataTable 列主序结论与 funds 字段 ID 协议（§15.9）。

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

## 15. 交易 SDK 只读查询逆向（2026-08-16，11.58.03）

目标：不脱离 App 进程，复用同花顺自己的交易查询管道，为 Portfolio Agent 提供
持仓/委托/成交的只读数据端点。安全边界：日志只记结构不记值；只重放查询协议，
绝不触碰买入/卖出/撤单/转账。

### 15.1 关键类锚点（11.58.03 混淆名，升级会变）

| 类 | 角色 | 关键成员 |
|---|---|---|
| `MasterModuleBridge`（唯一非混淆锚点） | libweituo.so 唯一引用的 Java 类 | `jniRequest(Context,byte[])` 发请求；`receive(byte[])` 响应；`receiveWTModulePush` 推送；`receiveLog` GBK 日志 |
| `r9h`（WtModuleInitProcessor） | 交易模块初始化器 | `r9h.d` volatile boolean = **唯一安全就绪探针**（读它只触发平凡 clinit）；`r9h.o(byte[])` 请求入口；`r9h.c` context |
| `rpv`（抽象，uqv.e(true) 返回） | fluent 请求构建器 | `H(int pageId,int protocolId,imv observer,String params)`；`D(String key,Object value)`；`request()` |
| `uqv` | 构建器工厂 | `uqv.e(true)` 返回 A 股交易 builder |
| `pzr`/`fzr` | 账户对象（fzr extends pzr） | `d()` 券商账号 `q()` qsid `x()` wtid；`p0s.F(119)` 返回实例 |
| `ixm` | 抽象观察者基类（sxh/fyh/rtl 继承） | `receive(StuffBaseStruct)` → 抽象 `a()`；持仓响应走此体系 |
| `nxm` | 观察者基类（pxm/lxm/jxm 父类） | 字段 `c=pageId d=protocolId g=pzr`；自有 receive 分发 |
| `kmv` | 观察者注册表 | `c(imv)` 注册返回 ID；`l(imv)` 注销 |

### 15.2 数据流与加密结论

```
Cipher AES/ECB(Java 明文, 无 cmd= 字样) → r9h.o → jniRequest(Context,bytes)
  → libweituo.so（cmd=cmd_zijin_query&... 在 native 组装, Java hook 拿不到）
  → receive/receiveWTModulePush → CommunicationService.notifyDataReceived
  → hrv.b(SocketConnectionPool 帧解析) → ixm/nxm 观察者 → 页面
```

- cmd 明文映射在 native 侧，**不需要**破解——Java 层直接复用查询构建器即可绕开
- 交易首页批量查询 2276B（H(9001,1264)）；定时刷新 420B；持仓刷新 440B

### 15.3 响应双观察者体系（重要架构发现）

交易响应有两条独立投递路径，**必须都挂**才能覆盖全部页面：

1. **ixm 体系**：`receive(StuffBaseStruct)` → 子类实现抽象 `a()`。持仓页（sxh$b）走此
2. **nxm 体系**：自有 `receive()`，instanceof 分发——`StuffTableStruct` → Handler(msg=1) → `i()` → `dzh.Y1()` 解析 → `receiveTableData(String[][],...)`；`StuffTextStruct` → Handler(msg=2) → `j()` → `receiveTextData(id,content,caption)`。当日/历史委托成交页走此

响应载体语义（实测确认）：
- `StuffTextStruct` = **纯提示文本**（caption=系统信息，如"没有成交数据。"），字段 reCode/id/type/caption/content。**不是账户数据**
- `StuffTableStruct` = 数据表：`tableHead`(String[] 列名)、`row/col`、`dataTable`(Hashtable<Integer,String[]>)、caption
- 持仓表 14 列：`[名称,盈亏,市值,盈亏率,成本,现价,数量,可卖,冻结,代码,刷新,交易市场,股东账号,实际数量]`

### 15.4 已验证查询目录（请求 → 响应配对，真机实测）

| 查询 | 请求 | 响应观察者 | 端点名 |
|---|---|---|---|
| 持仓（股票列表） | `H(2606或2624, 1891)` + `D("wt_account",pzr)` | ixm: sxh$b | positions |
| 当日委托 | `H(2683, 1811)` | nxm: pxm | today_order |
| 当日成交 | `H(2609, 1810)` ⚠️ | nxm: jxm | today_deal |
| 历史成交 | `G(2611, 1824)` | nxm: lxm | hist_deal |
| 历史委托 | `H(2612, 1825)` | nxm: pxm | hist_order |
| 资金（A股） | `H(2605, 1807)` + `D("wt_account",pzr)`，params 静态：`reqctrl=2012\nctrlid_0=36665\nctrlvalue_0=cc_capital\nctrlcount=1` | 自带 imv（字段 ID 取值） | funds |

**today_deal 陷阱**：App 新统一方案发 `H(2001,2031)`，但**响应帧 frameId=1810**
（旧协议号）。观察者按 protocolId 注册分发——按 2031 注册收不到（15s 超时）。
必须按 `H(2609,1810)` 发起注册；params 静态构造（源码 u2i HSTodayDealClient 模板）：
`ctrlid_0=36665\nctrlvalue_0=today_chaxun\nctrlcount=1`（eb6 格式：`a(key,value)`
生成 ctrlid_N/ctrlvalue_N 对，toString 补 ctrlcount）

**eb6 参数格式**：`ctrlid_0=值\nctrlvalue_0=值\nctrlcount=N`。新构造器可带初始串
（sxh 的 `new eb6(R.e())`），空构造器也可用（u2i 模式）

### 15.5 进程内只读调用器模板（已验证 139~275ms）

```java
// 1. 前置：thsAppClassLoader / tradeAccountManagerInstance(p0s.F(119)捕获)
//    / capturedQueryParams(protocolId→params, hook rpv.G/H 分存) / r9h.d==true
// 2. Proxy 实现 imv 接口（imv = xdv.receive(StuffBaseStruct) + request()）
//    ⚠️ hashCode/toString/equals 必须返回正确类型，见 pitfalls #36
Object observer = Proxy.newProxyInstance(cl, new Class[]{imvClass}, handler);
// 3. 反射链（H 内部自动注册观察者 eev.i().f()，无需手工 kmv.c）
Object rpv = uqv.e(true).H(pageId, protoId, observer, params);
rpv.D("wt_account", account);   // 绑定账户
rpv.request();                   // → mrv → k7r.K0 → r9h.o → jniRequest
// 4. CountDownLatch 等响应(15s)，receive 回调在通信线程 set stuff + countDown
// 5. 结束后 kmv.l(observer) 注销，防注册表泄漏
```

### 15.6 防崩铁律（两次真实崩溃换来）

- **clinit 毒化**：MasterModuleBridge 的 clinit 要 context，启动期 Pine.hook 它 →
  ExceptionInInitializerError → 类永久 erroneous → App 后续 NoClassDefFoundError
  （pitfalls #35）。解决：15/45/90s 延迟重试 + `r9h.d` 门控
- **F(119) 不是安全触发器**：启动期账户恢复流程就会调它（context 未就绪时），
  不能用它的 afterCall 触发 bridge 挂钩（pitfalls #38）
- postAppSpecialize 无主线程 Looper（pitfalls #37）
- installAllHooks 会被多 classLoader 多次调用；App 类挂钩放 firstRun 外可重试区

### 15.7 部署与环境备忘

- 解壳 DEX（360 加壳运行时解密）：`/data/user/0/com.hexin.plat.android/files/dex/classes{1-7}.dex`，
  拉取方法见 pitfalls #43；反编译到 /tmp/ths-jadx-rt{1,2,4,5,6}/runtime
- Zygisk 模块目录三文件：classes.dex(真 Pine 49320B，vendor 固化) + classes3.dex(MainHook，
  增量替换) + libpine.so。**真 Pine dex 被 APK 桩覆盖 = 全部 hook 失效**（pitfalls #42）
- 交易页 UI 坐标（1080x2378 原始分辨率直接用，OCR 坐标不缩放）：买入(108,1073) 卖出(323,1073)
  撤单(538,1073) 持仓(755,1073) 查询(971,1072)；Tab 栏 y≈2290：交易(629) 资讯(810)；
  查询页项：当日成交(144,457) 当日委托(145,602) 历史成交(145,776) 历史委托(144,920)
- Hook HTTP 端点：`GET /stock/trade/query?name=positions|today_order|today_deal|
  hist_order|hist_deal`（各查询需 App 内进过对应页面一次以捕获 params；today_deal 除外）

### 15.8 真实数据验证与 dataTable 列主序（第七轮，2026-08-16）

测试账户（川财证券模拟 **3857）有 2026-03~05 真实交易。历史委托页默认"近7日/近30日"
为空，自定义日期 2025-01-01 起返回 38 条委托记录——先拉满日期窗口再下"无数据"结论。

**dataTable 结构（重要架构发现）**：

- **列主序**：`StuffTableStruct.dataTable` 外层 key=列号（0..col-1），`String[]` 为该列
  全部行的值；`row=38, col=12` 时是 12 个 entry、每个 38 元素
- **键序 ≠ tableHead 展示序**：1825 实测 key0=代码、key1=名称、key2=状态、key3=操作、
  key4=委托数量、key5=价格、key6=成交数量、key7=成交均价、key8=交易市场、key9=合同编号、
  key10=日期、key11=时间；而 tableHead[0]=名称。键→列映射在各观察者实例私有的 iArr
  （`dzh.Y1(stuff, iArr)` 第二参数，即 nxm 实例字段 e），stuff 本身不携带
- MainHook 端点输出：转置后行主序 `rows` + 人工校准的 `TRADE_TABLE_KEY_COLUMNS`
  （当前仅 1825）→ `key_columns` + `records`（语义键对象数组，下游直接可用）；
  其余协议按位置消费，待有真实行数据再校准

**各端点真实数据状态**：

| 端点 | 实测 |
|---|---|
| hist_order | 38 条完整委托（买/卖、已成/已报/废单/已撤全状态），171~178ms |
| hist_deal | App 内本就无成交流水（默认/按股票模式均空），非重放问题 |
| positions | 空仓（总资产 3,139.80、仓位 0%），14 列表头完整，出持仓后自动有行 |
| today_order/deal | 无当日数据，端点行为与 App 一致 |

**params 生命周期**：内存暂存随 App 重启清空；重启后各端点需重新进对应页面捕获一次
（today_deal 用静态 params 免疫）。捕获后 curl 可长期重放。

**部署时序铁律**：重启 → **等满 90s 挂钩梯** → 再首次进交易页。交易首页批量查询
`H(9001,1264)` 只在首次进入时发出（之后走缓存）；hook 未就绪时错过，只能 force-stop 重来。

### 15.9 funds 端点（9001 容器受阻 → 1807 专用协议打通，2026-08-16 第八轮）

`H(9001,1264)` 的 params 可捕获可构造，但按 1264 注册的 proxy 15s 收不到任何响应帧；
受控实验（清 logcat → 单次重放）中连 `r9h.o`（原生发送入口）日志都不出现——请求在
Java 层容器内部被处理，或响应按子项拆帧后分发到交易首页各组件自己的观察者，不走
protocolId 注册路径。结论：**可捕获 ≠ 可重放**，9001 容器本身放弃重放。

**破局**：反编译持仓页资产页卡组件（`rcm` / `WeiTuoChiCangPersonalCapitalItemView`）
找到专门资金子协议：

```java
uqv.e(true).H(2605, 1807, observer,
    new eb6().b("reqctrl=2012").a("36665", "cc_capital").toString())
  .D("wt_account", pzr).request();
```

- `cc_capital` = `CapitalQuerySource.ACapital`（A股）；B股 `cc_b_capital`；reqctrl 变体
  2012/2013/2014 见 rcm/kqi/iqi
- 响应 StuffTableStruct：**dataTable 键=字段 ID**（非列号），`getData(fieldId)` 取
  firstOrNull 即金额。字段：36628 总资产 / 36629 浮动盈亏 / 36625 可用资金 / 36626
  总市值 / 36623 可取资金；36622/36624/36627/36630 无命名引用（field_366XX 输出），
  36631-36633 仅港美股
- 真机与 App 显示逐项一致（3139.80 / 0.00 / 0.00；空仓可用=可取=全额）

**通用教训**：页面容器协议重放受阻时，不要硬啃容器——反编译页面里消费该数据的
**具体组件**，往往存在可独立调用的专用子协议客户端，且 params 可从源码模板静态构造。

### 15.10 滚轮日期选择器 UI 自动化（自定义日期采集用）

- 三列滚轮（年/月/日）x=288/552/792，中心行 y≈2014，相邻项 y≈1833；**点击相邻项即滚动
  一格居中，重复点同一位置连续递减**（月 7→1 点 6 次、日 17→1 点 16 次，每次间隔 ≥0.7s
  等动画），批量执行后截图验收三列终值
- 滚轮按钮行在滚轮**上方** y≈1635：取消(88,1635)、清除(858,1635)、确定(989,1635)；
  外层日期行确定(932,590)
- App 记住上次自定义起始值——**操作前先截图确认滚轮初始值再定步数**（本次初始
  2026-07-17，步数=年×1/月×6/日×16）
- 历史成交页布局不同：起始日期(253,574)、确定(942,574)，"默认/按股票"分组 Tab y≈447
- 裁剪放大 OCR 的坐标必须换算回原始坐标系（real = 裁剪原点 + 放大坐标/2），tap 前做
  屏幕尺寸 sanity check（x<1080；曾把 1344 当真实 x 点了空处）
- 交易页可能弹"资产分析权限开通"推广页拦截导航——BACK 退出后重新 `dumpsys window`
  确认再继续（pitfalls #44）
- 详细交接文档：`smart-fund-server/docs/6. 使用说明/5. Portfolio Agent第一阶段同花顺交易SDK逆向交接说明.md`

### 15.11 写交易执行器：买/卖/撤单（2026-08-16 第九~十轮，真机验证 ✅）

三个写端点全部经真实券商系统验证（模拟盘 **3857）：

| 操作 | 协议 | 验证结果 |
|---|---|---|
| 买入 | `H(2682,1820)` reqctrl=2001 | ✅ 真实下单 6 单（合同号 1384~1410） |
| 卖出 | `H(2604,1821)` reqctrl=2002 | ✅ 券商拒绝 `[251005][证券可用数量不足]`——参数直达券商 |
| 撤单 | `P(2683).U(25102).R().c0().a0()` | ✅ 三单已撤（136ms），business_ok 判定正确 |

**买卖 params**（eb6 格式）：数量键 buy=36615/sell=36621、代码=2102、价格=2127，附
36641=1、36670=24（时效）、36669=1；尾部 `\r\nsource=` Base64(DES) 签名——**cmd 命令名
编码在 source 内**（`gyh.a(params, hyh, protoId)` 拼接，hyh.p() 返回如 ".cmd_mairu_confirm"）。
DES 确定性使签名跨进程重启一致；签名只覆盖页面标签不含订单参数，**改 code/price/qty
后复用有效**。静态模板兜底（TRADE_ORDER_STATIC_TEMPLATES），App 重启后无需重新捕获。

**撤单四链并存（核心教训——反编译源码协议 ≠ 券商实际部署协议）**：

- `22157`：b8p 源码链，重放被判 `[250001][未知请求]`——**反编译看到的协议不一定部署**
- `1823`：v1p 闪电撤单 `G(2683,1823)`，撤单 Tab 实际不走此链
- `25102`：**v3p QuanCheClient 撤买/撤卖批量撤单，实际生效**。params：
  `2103=撤单条数`、`2102="code_名称_委托号_市场码_股东账号_可撤数量"`（多单 `|` 分隔）；
  实测 `159740_恒生科技ETF大成_1404_1_0926764077_0`（marketCode=1 深A）
- `25106`：全撤（v3p `x(list,true)` 分支）

发送链（与 G/H 不同的 fluent 风格）：
```java
uqv.e(true).P(2683).U(25102).R(kmv.c(observer)).c0(params).a0();
```
响应 StuffResourceStruct：type=5、buffer=GBK JSON `{count, stockArr:[{code,message,htbh}]}，
成功=stockArr[0].code=="0"；假委托号返回明确的业务错误 `[250001][订单表记录不存在]`。

**绕过 hook 点的发送路径定位法（可复用）**：App 撤单请求不经过 rpv.G/H → 在
PERFORM_CLICK hook 检测"撤单"按钮文本更新 lastWithdrawClickMs → r9h.o hook 在 3 秒
窗口内打印业务调用栈（WithdrawSendStack）→ 栈显示 `v3p.x → mrv.a0 → rpv.a0 → mrv.O →
k7r.K0 → r9h.o` → 反编译 v3p 得 25102。

**MhvDump（hook mrv.O）= 写协议自动捕获**：过滤集 {1820,1821,1823,22157,25102,25106}，
命中时全量输出 params 并自动存入 capturedQueryParams；App 端任何写操作发生后即可从
`/stock/trade/write-captures` 导出重放。

**安全边界**：写端点必须 `confirm:"true"`；转账 1826 已实现未验证（需银行密码，禁止
自动化）；写端点测试用 Python raw socket（WSL curl 对 cancel 假性挂起 25s 超时，服务端
实际 84ms 完成）。

### 15.12 零 UI 依赖闭环：账户恢复 + 静默重登 + 静态 params（2026-08-17 第十一轮）

用户明确要求"不能接受需要 UI 驱动的功能"。本轮消除全部 UI 依赖后验收：force-stop 重启 →
monkey 拉起（只落首页）→ 等 ~3.5 分钟预热 → **6/6 只读端点全部返回真实数据**
（today_deal 166ms / funds 1798ms / positions 1865ms / today_order 1766ms /
hist_order 1785ms / hist_deal 128ms）。

**三层依赖解法**（`ensureTradeRuntimeReady` + `TRADE_QUERY_STATIC_PARAMS`）：

1. **params 层**：4 个捕获端点全部静态模板化（源码推导）——positions
   `addAccount=1\n36665=zjcc_home`；today_order `addGaiDan=1\n36716=1\n36665=today_chaxun`；
   hist_order `reqctrl=2026\n36633={start}\n36634={end}\n36665=his_chaxun`（t4m.a）；
   hist_deal `reqctrl=4223\n...\n36665=his_dryk\ntotalMoney=1\nrowcount=40`（sxh.d）。
   `{start}=20250101`、`{end}=yyyyMMdd 今天`
2. **账户对象层**：`n0s.s().B()` 填充列表 → `izr.a.x(fzr)` 置激活打分（`w5s.v(119)`
   按 `izr.a.j(pzr)>0` 选账户；`x()` 是交易页激活账户的标准调用，mnq.s 同款）
3. **登录态层**（mrv.k0 门控反编译实锤，mrv.java:149-168）：请求带 wt_account 且
   `izr.a.l()`=false 时被**静默丢弃**（正在重登）或触发重登后丢弃。解法：
   `x0s.F(false,false,14)` 触发 WeituoReloginManager 静默 token 重登 + 轮询 `izr.a.l(fzr)`

**预热**：MasterModuleBridge 挂钩成功后 `trade-warmup` 线程循环 ensure（最多 6 次）。
静默重登实测 2~3.5 分钟（token 链路慢，非故障）；预热期 HTTP 调用阻塞在登录门（45s/次）
返回明确错误。诊断口诀：**构建点有日志、r9h.o 无日志 = mrv 层登录门丢弃**。

**陷阱**：`n0s.A(false)` 幂等检查（2ms 提前返回不加载）→ 用 B()；`c1s.m().G()` 强制重载
YYB 仓库会把账户数据清掉，禁用；激活 ≠ 登录，激活后立即发的首请求仍被吞。

遗留（9. 待优化.md）：多账户场景取"第一个 fzr"未验证；重登窗口偏长（P2）。

---

## 16. 交易登录链全图、跨设备 token 与生产部署（第十六轮，2026-08-18）

### 16.1 登录 14 级拦截器链（f2s.q → s9m 全图，逐点实测）

```
f2s.q(tokenInfo, q3s, g8m)
 └ s8m.c → t9m(SixPassportDecrypt) ——外层：解密六位护照/建上下文 r8m
    └ 完成回调 p8m → s8m.f 建主链（r8m.e=智联分支决定 z9m 或 r9m）：
    j9m → m9m → v9m → o9m → i9m(AppUpdate) → x9m(WTModule 等 r9h.d=true)
    → [e=true] z9m(ZLBindWTPhone) → p9m(RZRQ) → w9m(VIPStation 异步HTTP) 
      → u9m(SslCert) → y9m(YYbNature) → n9m
      [e=false] → p9m → w9m → y9m → r9m(Session)
    └ 链完成 m1s → e2s.a0 → s9m(SilentLogin) → t1s → mrv → r9h.o → 券商
```

- **基类 h9m 是模板方法**：`intercept(){ a() ? e() : c() }`——多数子类只覆写
  条件 a()/名字 b()，**不覆写 intercept**！hook intercept 打点时这些子类不命中，
  链上"凭空消失"的下一节点往往就是它们（z9m 即如此，卡了数小时）。
  `c()`=继续（next.intercept() 或完成回调）；`e()`=停链等本环节异步流程。
- **诊断探针**（hookTradeLoginPathDiagnostics）：每节点 enter + h9m.c 的
  proceed（含 this.a/this.b 反射+线程名）。真机完整链 6ms 走完可作对照。

### 16.2 五大阻断点与修复（AVD 无头环境实测定位）

| 阻断点 | 机制 | 修复 |
| --- | --- | --- |
| lzr.e=false | native 路径标志仅登录成功回调（n2s.b）置位、内存不持久化 → k7r.Q→q9r.l 判走 CBAS socket 死路（AVD 无 CBAS 地址，PushConnect 缺位） | 登录前 `mzr.a.b(mgr).p(true)` 强制（=成功回调效果） |
| z9m 绑定门禁 | a() 查**本设备**智联绑定手机号（pzh.g().e()），新设备无记录 → 停链等绑定 UI | hook z9m.a() 强制 false |
| isWeituoLogining 僵尸 | r0s.u().j 非静默登录置位、官方复位在 r0s.o()/1h 超时 TimerTask；卡住后 k2s.b 静默丢所有 f2s.q（"could not login wt"） | 等待 10s 后 `i0(false)` 强清 |
| 模块初始化竞态 | x9m 等 r9h.d；AVD 慢初始化（分钟级）+原重试仅 3 档错过即永久放弃 | 失败路径自续排（6 档共 ~15 分钟） |
| fail(null)="null stuff" | App 内部重登与主动登录并发，竞争超时方回调 fail(null) | fail 后轮询 izr.a.l 5s 收编 |

### 16.3 跨设备 token：设备指纹移植（券商设备绑定的构成）

直接 import 真机 token → 券商拒："非交易密码方式登录失败，请输入交易密码
登录并重新绑定"。**移植设备标识后接受**（登录 498ms，六端点真实数据）：

| 层 | 键/字段 | 说明 |
| --- | --- | --- |
| SP `hardwareinfo.dat.xml` | `sp_key_wt_hardware_unique`（WT 层设备 ID，头号因子）、`sp_key_hardware_unique`、`sp_key_hardware_unique_server`（服务端签发）、`sp_key_hardware_mac` | 真机值直接写入（属主改回 app uid） |
| Build | MODEL/BRAND | 反射改写；端点 `POST /stock/trade/device-spoof`（filesDir/thshook_spoof.json 持久化+启动恢复） |
| getConfigInfo | native→Java 配置回调（26 键：IMEI/IMSI/Mac/UDID/Userid/MobileType/SessionId/...） | MobileType 随 Build 自动；**Userid 不可伪造**（须与本机 THS 会话一致，实测伪造后登录失败） |

观测端点 `GET /stock/trade/device-info`（UDID/机型/最近 getConfigInfo 原文）。
**token 单活会话**：同 token 双端并发登录互踢（1Hz 重登风暴实测）——
真机/AVD 只能一端在线，切换时先停另一端。

### 16.4 会话生命周期与自愈

- **90B 帧 = 会话失效推送**：查询发在失效会话上→响应是 90B 错误帧（观察者
  不识别→超时）→App 自动重登。被动等不可靠（重登 ~18s 且有竞态）。
- **自愈方案**：查询超时 → `doActiveTradeLogin(force=true)`（跳过
  already_logged_in 短路强制重发 f2s.q 重建服务端会话）→ 3s 稳定窗 →
  重试一次。冷启动首查 ~20s，稳定窗 0~3s。客户端 GET 超时 ≥75s。
- **token 读取坑**：z7m.i 反序列化后 x7m.i(pzr) 重算 livetime（ehi.m().n()
  开关），AVD 恒 0 → isAvailable 恒 false（文件有效但"token unavailable"）。
  解法：seed 文件（thshook_trade_seed.json={qsid,json,broker,token,token_time}）
  登录门自动重播官方 import 链 + livetime 1440 修复；一次性标志成功才消耗。

### 16.5 生产形态（8 实例 AVD，LSPosed 注入）

- **单登录三层**：trade role 门禁（owner/user0 唯一启用，filesDir 持久化，
  未配置实例 /stock/trade/* 全 403；`injectedViaLsposed` 判据：LSPosed 默认
  禁用、zygisksu 默认启用）+ `THS_TRADE_BASE_URL=http://127.0.0.1:49301`
  （owner 专用 proxy）+ LB 49350 OWNER_AFFINITY_PREFIXES 加 /stock/trade。
- **实例端口**：18900+userId×10（owner=18900，proxy 493X1→forward 493X0）。
- **owner 上报**：`http://10.0.2.2:8900/api/ths/token`（emulator→宿主）。
- **AVD 特有环境**：yyb 券商库未初始化 → App 账户仓库不落盘（seed 是唯一
  持久层）；模块初始化分钟级；8 实例 x86 高负载 ANR 风暴（load 39 实测，
  AudioUploadService/CommunicationService/广播饥饿）——但 **owner（user 0）
  几乎不 ANR**（前台用户优先）。
- 验收：六端点 6/6；E2E api→49301→owner→券商 funds 3138.60 与真机一致；
  投影 exposure_summary=available；重启 already_logged_in。

详细文档：`smart-fund-server/docs/3. 实施方案/7. Agent工具/7. 生产环境交易能力
部署与主实例单登录实施方案.md`；交接说明 §3.15。

## 17. 多用户交易专属实例（第十七轮，2026-08-18）

**场景**：交易会话与采集实例解耦——同 emulator 增第 9 个 user（17/trade）专管交易。

### 全新 user 的"三层状态移植"（核心经验，缺一层一种死法）

| 层 | 文件 | 缺失症状 |
| --- | --- | --- |
| ①设备指纹 | shared_prefs/hardwareinfo.dat.xml | token 被券商拒（非交易密码方式登录失败） |
| ②THS 身份 | user_md5.xml、_sp_last_username.xml、sp_key_newuser.xml、sp_default_group.xml、sp_wt_expected_login_account.xml + files/{userId}_authorization.bat | z7m.i 读回恒 null → "token unavailable"（authorization.bat 文件名含 ulm.e() 的 userId，身份 SP 缺则 userId 空、文件名不匹配） |
| ③交易通道 | files/cbas_mt_*.txt、cbas_temp_username.txt、_weituo_yybinfo.dat、_weituonew_third_new.dat、mt_*_weituo_*.dat、yybnature_cached.dat、user_info.dat、files/{userId}/ + sp_weituo_login.xml、sp_user_sid.xml、push_setting.dat.xml | 登录链 14 级拦截器卡死 y9m(YYbNature)、hrv 检查 sessionType=0 后无 Socket.connect（CBAS 地址 null→hrv.C no-op） |

另配 files/thshook_trade_role.json {"enabled":true} + thshook_trade_seed.json；**不复制** thshook_report.json（上报防双跑）。

### 多用户启动坑（Android 11 实测）

1. 新建 user 默认 `user_setup_complete=0` → am start **静默无效**（dumpsys 可见 task 建立、ActivityRecord 存在但 app=null，crash buffer 空）。修：`settings put --user N secure user_setup_complete 1`
2. **后台 user 无法启动 activity**（START 后无 Start proc）：必须 `am switch-user N`（等 get-current-user=N）→ `wm dismiss-keyguard` + `input keyevent 82` → `am start -n pkg/.Hexin` → 等 /health → 切回原前台 user
3. am start 报 "its current task has been brought to the front" = 残留 task 无进程，先 force-stop
4. `.LogoEmptyActivity` 是 launcher 入口但主 activity 是 `.Hexin`

### LSPosed scope 变更铁律（事故换来的）

scope 表 INSERT（mid,app_pkg_name,user_id）后 **daemon 内存缓存不重读**。手动 `kill lspd` + service.sh 拉起 = **system_server 崩溃软重启**（全部 App 被杀），且手动拉起的 lspd **注入功能失效**（App 起来零 hook 日志）。唯一正解：**重启 emulator**（Magisk service.sh 正常序拉起 lspd，采集框架 systemd lane 自动恢复全部实例，实测 8/8 全回）。

### 写操作实测结论（user17）

- BUY 挂单：response 确认 120-170ms 稳定（confirmed_via=response）
- CANCEL：实际执行成功（today_order 终态"已撤"）但响应链最坏 120s——写响应帧丢失（App 观察者竞争消费，ixm/nxm 旁路兜不住）+ 两轮×(15s 写等待+登录 40s)。优化=撤单在途二次确认（首查"已报"后等 4s 重查"已撤"即返回，~26s）
- 废单边界：撤"废单"类终态委托无意义（券商不响应撤单请求）→ 确认逻辑只认"已撤"
