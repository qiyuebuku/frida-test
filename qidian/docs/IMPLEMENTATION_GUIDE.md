# 起点读书 Hook 实施完整指南

> 本文档整合了数据提取方案、实施进展和探测结果，为起点读书 App 的 Hook 数据提取提供完整的技术指导。

## 目录

1. [需求背景](#需求背景)
2. [技术分析](#技术分析)
3. [探测结果](#探测结果)
4. [实施阶段](#实施阶段)
5. [当前进展](#当前进展)
6. [下一步行动](#下一步行动)

---

## 需求背景

### 核心目标

在不打开起点 App 界面、不模拟人工操作的情况下，通过 Hook 技术主动获取指定小说的章节数据。

### 技术挑战

根据逆向分析，起点读书的数据获取涉及：

1. **API 请求签名**：每个请求需要 `QDSign`、`borgus`、`cecelia` 等签名字段
2. **章节加密**：由 App 内部 `bll.v` 类完成解密（非 javax.crypto.Cipher，具体算法封装在内部，详见"加密方案纠正"章节）
3. **登录态**：VIP 章节需要登录后的 Cookie（`ywkey`、`ywguid` 等）
4. **360加固检测**：存在延迟代码完整性检测（40-50秒后触发），以及可能的行为异常检测

---

## 技术分析

### 核心难点

#### 难点一：业务流程的完整性

正常用户操作流程：
```
搜索小说 → 点击搜索结果 → 进入详情页 → 点击开始阅读 → 阅读第一章 → 翻页/切换章节
```

每一步都涉及：
- **API 调用**：不同的接口、不同的参数
- **状态管理**：App 内部维护阅读进度、书架状态等
- **UI 状态**：Activity 栈、Fragment 状态

**风险**：直接调用底层网络 API 绕过正常业务流程可能触发服务端风控或客户端状态异常。

#### 难点二：检测机制的不可预测性

360加固的检测是多层的、延迟的：
- 立即触发：LSPosed 检测
- 延迟触发：40-50 秒后的方法入口完整性检测
- 行为模式：访问模式、请求频率等

**风险**：难以定位具体触发点，调试困难。

#### 难点三：操作层级的选择

```
┌─────────────────────────────────────────────────────────────┐
│  UI 层 (Activity/Fragment)                                   │  ← 最安全，但最复杂
│    ↓                                                         │
│  ViewModel/Presenter 层                                       │  ← 相对安全
│    ↓                                                         │
│  Repository/UseCase 层                                        │  ← 中等风险
│    ↓                                                         │
│  网络层 (OkHttp)                                              │  ← 高风险（绕过业务逻辑）
│    ↓                                                         │
│  加密层 (Cipher)                                              │  ← 只能被动捕获
└─────────────────────────────────────────────────────────────┘
```

---

## 探测结果

### 执行时间
2026-02-05 ~ 2026-02-06

### 已完成的阶段
✅ Phase 0: 广泛 Hook 探测模式
✅ Phase 1: LocalSocket RPC 通信
✅ Phase 2: 部分完成（RPC 命令框架）

### 关键发现

#### 1. 加密方案（重要纠正）

> **早期误判**：最初通过 Cipher Hook 捕获到 `AES/CBC/PKCS5Padding` 和 `DESede/CBC/PKCS5Padding`，误以为这是章节内容的解密算法。实际上这些 Cipher 操作用于**设备指纹/安全检测等辅助功能**，与章节内容解密无关。

**章节内容的实际解密方式**：
- App **不使用** `javax.crypto.Cipher` 进行章节内容解密（`trackedCiphersCount` 始终为 0）
- 解密完全由 App 内部 `com.qidian.QDReader.component.bll.v` 类的方法链完成（L → K → R）
- 密钥获取和派生逻辑封装在 `bll.v` 内部，外部无法直接复制
- 不需要调用 `getkey` API

**Cipher Hook 实际捕获的内容**（非章节内容）：
- 安全检测数据：`suspiciousDylibs` 列表（检测逆向工具 so）
- 设备标识数据：`gee_id`、时间戳等
- 这些加密使用 `AES/CBC/PKCS5Padding`，固定密钥 `abcEncDyetonFeyedxadcDyetonqwy`

#### 2. 关键类

| 类名 | 作用 | 说明 |
|------|------|------|
| **`com.qidian.QDReader.component.bll.v`** | **章节解密核心类** | L→K→R 方法链执行解密，是获取章节明文的唯一途径 |
| `si.cihai.search` | 辅助加解密（设备指纹等） | 与章节内容无关 |
| `mi.d.search` | 辅助加密工具类 | 与章节内容无关 |
| `pf.cihai.search` | 旧版加密类 | **已不存在于当前版本**，旧文档中的引用均已失效 |
| `com.qidian.QDReader.qmethod.pandoraex.monitor.c` | 安全检测 | 检测逆向工具 |

#### 3. 关键 API 端点

| 端点 | 方法 | 用途 | 请求参数 | 已实现 |
|------|------|------|---------|--------|
| `/argus/api/v2/booksearch/searchbooks` | POST | 搜索书籍 | keyword, pageSize, pageIndex | ✅ |
| `/argus/api/v3/bookdetail/get` | GET | 获取书籍详情 | bookId, isOutBook=0 | ✅ |
| `/argus/api/v3/bookdetail/lookfor` | GET | 相关书籍推荐 | bookId, isOutBook=0 | - |
| `/argus/api/v2/bookcontent/safegetcontent` | GET | 获取免费章节内容（加密） | bookId, chapterId | ✅ |
| `/argus/api/v3/chapterlist/chapterlist` | GET | 获取章节列表 | bookId, timeStamp=0, requestSource=0, md5Signature=, extendchapterIds= | ✅ |
| `/argus/api/v3/subscription/buyvipchapter` | POST | 购买 VIP 章节 | bookId, consumeType=0, sp=, type=3, confirmType=0, chapterlist={chapterId} | ✅ |
| `/argus/api/v4/bookcontent/getvipcontent` | POST | 获取 VIP 章节内容（加密） | b-string=, b={bookId}, c={chapterId}, ui=0 | ✅ |
| `/argus/api/v2/subscription/getunboughtchapterlist` | GET | 查询未购买章节列表 | bookId | ✅ |
| `/argus/api/v2/subscription/getvipprice` | POST | 查询 VIP 价格 | ChapterCount=-1, bookId, withoutLimitFree=0, UnitCount=1, chapterId | ✅ |
| `/argus/api/v4/bookcontent/getkey` | GET | 获取解密密钥 | bookId | ❌ 不需要，解密由 bll.v 内部完成 |

#### 4. UI 调用链

**搜索流程**:
```
QDSearchActivity (搜索页)
  → NewSearchHomePageFragment (搜索首页)
  → SearchAssociateFragment (搜索联想)
  → SearchResultFragment (搜索结果)
```

**阅读流程**:
```
QDBookDetailActivity (书籍详情)
  → 目录页
  → QDReaderActivity (阅读器)
```

#### 5. 已捕获的 Cipher 解密事件（辅助功能，非章节内容）

> 以下 Cipher 操作与章节内容解密**无关**，仅用于安全检测和设备指纹等辅助功能。

**安全检测类**（检测逆向工具的 so 库列表）:
```json
{
  "suspiciousDylibs": "libSignatureKiller.so|libAPKFxxxxx.so|libfxdcc.so|libpinesafecheck.so|libyyds.so|libtweakjar.so|libsotweak.so"
}
```

**设备标识类**:
```json
{
  "gee_id": "99d4cbb0-d8c4-4375-a64e-0677cbeac110",
  "ts": 1770119636920,
  "ver": "1.0.0"
}
```

#### 6. 章节内容解密的实际流程（核心发现）

章节内容的解密**不经过** `javax.crypto.Cipher`，而是由 `bll.v` 类内部完成：

```
safegetcontent API → 返回加密的 byte[] → 保存为 .qd 文件
    ↓
bll.v.L(bookId, ChapterItem) → 返回 ChapterContentItem（准备本地文件，触发内部解密）
    ↓
bll.v.K(bookId, ChapterItem) → 返回 .qd 文件路径
    ↓
bll.v.R(bookId, ChapterItem) → 返回 .cc 文件路径（解密后的缓存文件）
    ↓
afterCall hook 从返回的 ChapterContentItem 中提取 Content 字段 → 章节明文
```

> **关键发现**：实际上是 `bll.v.L` 内部就触发了解密，afterCall hook 在 L 返回前就已经捕获了明文。R 方法只是返回解密后的缓存文件路径。但三个方法必须按 L→K→R 顺序调用，单独调用 R 返回 null。

---

## 实施阶段

### 总体策略

**核心原则**：
1. **每次只增加一个变量**：每步都验证 App 稳定运行 60+ 秒
2. **优先使用高层 API**：尽量在 ViewModel/Repository 层操作，而非直接调用网络层
3. **保守起步**：先实现被动监听，再逐步增加主动调用能力
4. **详细日志**：每个操作都记录，便于定位问题

### 架构图

```
PC 端 (Python)                     手机端 (App + Hook)
     │                                    │
     ├──[ADB forward]──────────────────────┤
     │                                    │
     │   {"cmd":"search",                 │
     │    "keyword":"斗破苍穹"}            │
     ├──────────────────────────►         │
     │                    LocalServerSocket│
     │                         ↓          │
     │                    MainHook.java   │
     │                         ↓          │
     │                    【关键】复用 App  │
     │                    OkHttpClient    │
     │                    发送 API 请求   │
     │                         ↓          │
     │                    搜索/详情/目录：│
     │                    直接返回 JSON   │
     │                    章节内容：      │
     │                    加密数据→bll.v  │
     │                    L→K→R 解密     │
     │                         ↓          │
     │                    afterCall hook  │
     │                    捕获解密明文    │
     │                         ↓          │
     │   {"success":true, "data":{...}}  │
     ◄────────────────────────────────────│
```

### Phase 0：广泛 Hook + 自动化探测

**目标**：通过广泛的 Hook 打印日志，配合自动化点击触发操作，分析出关键的调用入口。

**核心思路**：
```
广泛 Hook（打印大量日志）
        ↓
自动化点击（截图 + 识别 + 模拟点击）
        ↓
触发关键操作（搜索、打开书、阅读章节）
        ↓
分析日志（找出调用链）
        ↓
定位 Hook 入口
```

**实现**：
- ✅ Hook Activity/Fragment 生命周期
- ✅ Hook ViewModel/Repository 方法调用
- ✅ Hook OkHttp 网络请求
- ✅ Hook Cipher 解密操作
- ✅ 记录完整调用栈

### Phase 1：LocalSocket 通信 + 被动监听

**目标**：建立 PC ↔ 手机的通信通道，实现被动数据获取。

**实现**：
- ✅ LocalSocket 服务端监听 `qdhook_rpc`
- ✅ RPC 命令解析和分发
- ✅ 环形缓冲区保存最近 50 条解密内容
- ✅ 中文编码支持（使用 `toString(1)` 避免转义）

**已实现命令**：
- ✅ `ping` - 测试连接
- ✅ `getStatus` - 获取 Hook 状态
- ✅ `getRecentDecrypted` - 获取最近解密内容
- ✅ `getLogs` - 获取探测日志
- ✅ `testChinese` - 测试中文编码
- ⚠️ `fetchChapter` - 主动获取章节（部分完成）

### Phase 2：根据探测结果定位入口

**目标**：根据 Phase 0 的日志分析结果，确定要 Hook 的关键入口。

**关键入口类**：
```
// 章节解密核心（已确认，最终方案的基础）
com.qidian.QDReader.component.bll.v      ← 章节内容解密核心类（L→K→R 方法链）

// 辅助加解密层（设备指纹/安全检测，与章节内容无关）
si.cihai.search                           ← 辅助加解密入口
mi.d.search                               ← 辅助加密工具类
pf.cihai.search                           ← 已不存在于当前版本

// UI 层（已确认）
com.qidian.QDReader.ui.activity.QDReaderActivity
com.qidian.QDReader.ui.activity.QDBookDetailActivity
```

### Phase 3：单点调用测试

**目标**：选择一个最安全的入口方法，进行单次调用测试。

**策略**：选择"只读"性质的方法：
- 获取书架列表（用户已有的书）
- 获取某本书的章节目录

### Phase 4：模拟完整业务流程

**目标**：实现搜索 → 详情 → 阅读的完整流程，每步之间加入合理延迟。

**关键设计**：模拟真实用户行为的时间间隔

```python
def download_chapter(book_id, chapter_id):
    # 1. 先"访问"书籍详情（触发详情页 API）
    client.visit_book_detail(book_id)
    time.sleep(random.uniform(1.0, 2.0))

    # 2. 获取章节目录
    chapters = client.get_chapter_list(book_id)
    time.sleep(random.uniform(0.5, 1.0))

    # 3. "开始阅读"（触发阅读相关初始化）
    client.start_reading(book_id, chapter_id)
    time.sleep(random.uniform(0.3, 0.5))

    # 4. 获取章节内容
    content = client.get_chapter_content(book_id, chapter_id)
    return content
```

### Phase 5：批量下载与优化

**目标**：实现稳定的批量下载能力。

**优化点**：
1. 请求间隔：章节之间 2-5 秒随机延迟
2. 断点续传：记录已下载章节，支持中断恢复
3. 错误重试：失败时等待更长时间后重试
4. 保活机制：定期发送心跳，检测 App 状态
5. 导出格式：TXT/EPUB 格式

---

## 当前进展

> 最后更新：2026-02-07

### 已完成

| 能力 | 说明 | 详细文档 |
|------|------|----------|
| 广泛 Hook 探测 | Activity/Fragment/ViewModel/Repository/Network/Cipher 全层 Hook | `QIDIAN_REVERSE.md` |
| LocalSocket RPC | PC ↔ 手机双向通信，支持 10+ 命令 | 见下方命令列表 |
| 搜索入口定位 | `QDSearchActivity.search()` → `searchbooks` API 完整链路 | `1. 搜索功能实现.md` |
| 100% 免 UI 搜索 | 复用 App 的 OkHttpClient + 签名拦截器，直接发 HTTP 搜索 | `1. 搜索功能实现.md` |
| RPC 日志同步 | 内存缓冲区 5000 条 + 增量轮询拉取到 PC | `2. 日志同步与一键启动.md` |
| 一键启动脚本 | `start.sh`：停止→启动→forward→日志同步，一条命令搞定 | `2. 日志同步与一键启动.md` |
| 书籍详情获取 | GET 请求 bookdetail/get API，支持任意 bookId 查询完整详情 | `3. 书籍详情功能方案.md` |
| **章节目录获取** | GET 请求 chapterlist API，返回全部章节（含 VIP 标记） | `4. 章节阅读功能方案.md` |
| **章节内容解密** | safegetcontent API + bll.v L→K→R 内部解密，100% 免 UI | `4. 章节阅读功能方案.md` |
| **VIP 章节购买与解密** | buyvipchapter 购买 + getvipcontent 下载 + bll.v.N 解密，100% 免 UI | `7. VIP 章节购买与解密方案.md` |
| **Python 工具链** | QidianAPI 封装 + batch_fetch.py 三步工作流 + 断点续传 + 增量更新 | `6. Python 工具链使用说明.md` |

### 关键决策回顾

**决策 1：OkHttpClient 复用 vs 调用 Java 搜索方法**
- 选择复用 OkHttpClient：绕过混淆类名问题，App 拦截器自动签名
- 放弃调用 `QDSearchActivity.search()`：需要 Activity 上下文，依赖 UI 线程

**决策 2：Fallback 硬编码模板 vs 必须先手动搜索**
- 选择硬编码 fallback：`POST searchbooks` + `keyword/pageSize/pageIndex` 参数
- 实现 100% 免 UI：不再需要首次手动搜索来捕获模板

**决策 3：RPC 轮询日志 vs TCP 推送日志**
- 选择 RPC 轮询：复用已有通道，无需 `adb reverse` 和额外 TCP 服务
- 弃用 `log_receiver.py`：TCP 推送方案依赖 adb reverse + 地址配置复杂

**决策 4：OkHttpClient 捕获策略（重要，经历过严重 Bug）**
- **最终方案**：从 `druidv6.if.qidian.com/argus` 域名的实际 API 请求中，通过 RealCall 对象反射取其 OkHttpClient 字段
- **关键教训**：`OkHttpClient.Builder.build()` 捕获的第一个客户端往往是非 API 客户端（如 Glide 图片加载），**不带签名拦截器**，导致所有 RPC 请求返回 HTTP 402 签名错误。此 Bug 导致搜索和书籍详情均无法使用
- **修复标志**：使用 `apiClientCaptured` 布尔标志，确保只从 druidv6 API 请求捕获一次，既保证正确性又避免频繁更新
- **捕获路径**：Hook `RealCall.enqueue()` / `RealCall.execute()` → 检查请求 URL 含 `druidv6.if.qidian.com/argus` → 反射遍历 RealCall 的字段找到 OkHttpClient 类型 → 取出并保存到 `savedOkHttpClient`
- **依赖顺序**：OkHttpClient 只有在 App 自身发起 API 请求后才能被捕获。如果 App 启动后还未请求任何 API，RPC 命令会返回 `OkHttpClient not saved yet` 错误
- 代码位置：`MainHook.java:1498-1522`（`installProbeHooks()` 中的 RealCall Hook）

**决策 5：书籍详情 API — GET 请求 vs POST 请求**
- 通过手动打开详情页捕获日志，确认 API 为 **GET** 请求（与搜索的 POST 不同）
- GET 请求同样能通过 OkHttpClient 拦截器自动签名，无需额外处理
- `isOutBook=0` 为固定参数，含义为"非站外书"

**决策 6：章节内容解密 — 自行解密 vs 触发 App 内部解密（重要）**
- **方案 A（自行解密）被否决**：App 不使用 `javax.crypto.Cipher` 进行章节解密，Cipher Hook 的 `trackedCiphersCount` 始终为 0。之前文档中记录的 AES/DESede 加密信息用于安全检测等辅助功能，与章节内容无关
- **方案 B（App 内部解密）被采用**：通过反射调用 `bll.v` 类的 L→K→R 方法链，让 App 自己完成解密，afterCall hook 捕获解密结果
- `pf.cihai.search` 类在当前版本已不存在，旧文档中对该类的引用均已失效
- `getkey` API 不需要调用，解密密钥获取和派生完全由 `bll.v` 内部处理
- **bll.v Hook 范围**：Hook 了 bll.v 类的**全部公共方法**（排除 Object 基类方法），而非仅 L/K/R。任何返回 `ChapterContentItem` 类型的方法都会触发 `extractAndCacheChapterContent()` 自动提取明文。这意味着如果 App 版本更新后改变了方法名，只要返回类型不变仍能正常捕获
- 代码位置：`MainHook.java:1000-1082`（bll.v 全方法 Hook）

**决策 7：ChapterItem 必须完整克隆（踩坑教训）**
- 不能创建最小化的 fake ChapterItem（只设 chapterId），会导致 `bll.v` 方法返回错误类型或 null
- 必须从已捕获的真实 ChapterItem 克隆全部字段（通过反射复制所有 field），仅修改 ChapterId
- 这意味着需要用户至少进入一次阅读页面，以捕获一个真实的 ChapterItem 作为模板

**决策 8：bll.v.L→K→R 调用序列不可省略（核心发现）**
- 观察 App 正常读取章节时的 probe 日志，发现 L→K→R 的固定调用模式
- L：准备本地文件状态，实际触发解密（afterCall hook 在此阶段就能捕获明文）
- K：确认加密文件路径（.qd 文件）
- R：生成解密后的缓存文件路径（.cc 文件）
- 仅调用 R 返回 null；必须先调用 L 和 K 才能使 R 正常工作

**决策 9：完全免 UI — bllVInstance 自动捕获 + ChapterItem API 构造（重要突破）**
- **旧方案**：需要用户至少进入一次阅读页面，以捕获 `bllVInstance` 和 `ChapterItem` 模板
- **新方案**：两个关键发现消除了此前置条件：
  1. **bllVInstance 自动捕获**：App 启动时恢复上次阅读状态，自动触发 `bll.v` 实例化和 hook 回调。无阅读记录时可通过 `createBllVInstance()` 反射创建
  2. **ChapterItem API 构造**：`ChapterItem` 类有 `ChapterItem(JSONObject)` 构造函数，可用章节列表 API 的 JSON 数据直接构造，不需要模板克隆
- **验证**：用从未打开过的书（斗破苍穹 bookId=1004608738）成功完成 搜索→章节列表→解密 全链路
- 实现位置：`MainHook.java` → `constructChapterItemFromApi()`, `createBllVInstance()`, `handleTestNoTemplate()`

**决策 10：RPC JSON 紧凑格式修复（重要 Bug 修复）**
- **问题**：`handleRpcCommand` 返回的 JSON 使用 `toString(1)` 格式化（含换行符），但 RPC 协议以 `\n` 为消息分隔符，导致客户端在第一个换行处就截断读取，章节列表等大 JSON 响应被截断
- **解决**：在 `handleClient` 中发送响应前，用 `new JSONObject(response).toString()` 转换为紧凑 JSON（无换行符）
- 实现位置：`MainHook.java` → `handleClient()` 方法

**决策 11：章节列表 API 双格式支持**
- 不同书籍的章节列表 API 返回两种 JSON 结构：`Data.Vs[].Cs[]`（卷-章节结构）和 `Data.Chapters[]`（扁平列表）
- `constructChapterItemFromApi()` 必须同时支持两种格式，否则某些书籍的 ChapterItem 构造会失败
- 实现位置：`MainHook.java` → `constructChapterItemFromApi()` 方法

**决策 12：段评功能实现 — 通用 GET 请求执行器**
- 将重复的 OkHttp 反射调用代码（构造 Request.Builder → 设置 URL → get() → build() → newCall → execute）提取为通用 `executeGetRequest(url)` 方法
- 新增 API 只需构造 URL 字符串，调用 `executeGetRequest()` 即可
- 段评摘要 API 响应结构：`Data.Getparagraphscommentcounts.DataList[]`（含 ParagraphId, CommentCount）
- 段评详情 API 响应结构：`Data.DataList[]`（含 Content, RefferContent, AgreeAmount, CreateTime 等）
- `ParagraphId=-1` 表示章评，`getAllParagraphComments` 自动跳过
- 实现位置：`MainHook.java` → `executeGetRequest()`, `handleGetChapterReviewSummary()`, `handleGetParagraphComments()`, `handleGetAllParagraphComments()`

**决策 13：VIP 章节购买 — buyvipchapter API 参数格式（重要发现）**
- **发现过程**：多次尝试不同参数组合均返回 -100001，最终通过修改 OkHttp 拦截器（增加 `subscription` 路径拦截）+ 用户手动打开 VIP 章节，从日志中捕获 App 真实请求格式
- **正确参数**：`bookId`, `consumeType=0`, `sp=`(空), `type=3`, `confirmType=0`, `chapterlist={chapterId}`
- **关键字段名**：`chapterlist`（不是 `chapterId`），这是无法猜测的关键差异
- **Result -100010 不是错误**：表示"要购买的vip章节不存在"，实际含义是章节已购买，应继续下载流程而非报错
- 详见：`docs/7. VIP 章节购买与解密方案.md`

**决策 14：VIP 章节解密 — bll.v.N 而非 L→K→R（关键区分）**
- L→K→R 方法链**只能解密免费章节**，VIP 章节解密后 chapterContent 仅含空白字符
- bll.v.N 是 VIP 章节的正确解密入口，参数和返回类型与 L→K→R 类似
- VIP .qd 文件必须保存到用户路径（`book/{userId}/{bookId}/`），不能用匿名路径（`book/0/{bookId}/`）
- 保存路径错误会导致 bll.v.N 返回 null
- 详见：`docs/7. VIP 章节购买与解密方案.md`

**决策 15：批量采集修复模式 — 粒度跳过（正文/评论独立判断）**
- 旧方案：整章跳过（文件存在就跳过），导致正文成功但评论失败的章节无法修复评论
- 新方案：`--fix` 模式下独立检查正文和评论的有效性，只重新采集缺失/失败的部分
- 已有的有效数据直接复用（通过 `existing` 参数传给 `fetch_one()`），不重新请求
- 详见：`docs/6. Python 工具链使用说明.md`

### 下一步

**全链路已完成**。搜索 → 书籍详情 → 章节目录 → 免费/VIP 章节内容 → 段评的完整链路已全部实现，**100% 免 UI**。批量采集工具已包含断点续传（--fix 模式）和粒度修复能力。

可选优化方向：
1. **导出格式**：TXT/EPUB 格式输出
2. **并发优化**：多线程采集（需评估风控风险）
3. **余额监控**：VIP 采集时实时监控余额变化

### 阶段进展

| 阶段 | 内容 | 状态 |
|------|------|------|
| Phase 0 | 广泛 Hook + 自动化探测 + 日志分析 | ✅ 完成 |
| Phase 1 | LocalSocket + 被动监听 | ✅ 完成 |
| Phase 2 | 根据探测结果定位入口 | ✅ 完成 |
| Phase 3 | 搜索入口分析 | ✅ 完成 — 见 `1. 搜索功能实现.md` |
| Phase 4 | 搜索 RPC（100% 免 UI） | ✅ 完成 — 见 `1. 搜索功能实现.md` |
| Phase 5 | RPC 日志同步 + 一键启动 | ✅ 完成 — 见 `2. 日志同步与一键启动.md` |
| Phase 6 | 书籍详情获取（100% 免 UI） | ✅ 完成 — 见 `3. 书籍详情功能方案.md` |
| Phase 7 | 章节目录 + 内容解密（100% 免 UI） | ✅ 完成 — 见 `4. 章节阅读功能方案.md` |
| Phase 8 | 段落评论获取（100% 免 UI） | ✅ 完成 — 见 `5. 段评获取功能方案.md` |
| Phase 9 | Python 工具链（API 封装 + 批量采集） | ✅ 完成 — 见 `6. Python 工具链使用说明.md` |
| Phase 10 | VIP 章节购买与解密（100% 免 UI） | ✅ 完成 — 见 `7. VIP 章节购买与解密方案.md` |

---

## 检测风险缓解策略

### 策略一：操作层级选择

| 操作类型 | 推荐层级 | 风险 | 说明 |
|---------|---------|------|------|
| 获取书架 | ViewModel | 低 | 只读操作，用户常做 |
| 获取章节目录 | ViewModel | 低 | 只读操作 |
| 获取章节内容 | Repository | 中 | 需要先"进入"书籍 |
| 搜索 | ViewModel | 中 | 需要模拟输入行为 |
| 翻页 | ViewModel | 低 | 常规操作 |

### 策略二：行为模式拟真

```
❌ 错误模式：
   直接请求 chapter1 → 直接请求 chapter2 → 直接请求 chapter3
   （无间隔、无状态）

✅ 正确模式：
   访问详情页 → [1.5s] → 获取目录 → [0.5s] → 开始阅读 → [2s] → 请求 chapter1
   → [3s] → 翻页 → [2.5s] → 请求 chapter2 → ...
```

### 策略三：渐进式调试

```
每增加一个新操作：
1. 单独测试该操作，观察 60 秒
2. 与前序操作组合测试，观察 120 秒
3. 加入完整流程测试，观察 300 秒
4. 批量测试（10+ 章节），观察全程

如果任何阶段出现异常：
- 记录详细日志
- 回退到上一个稳定版本
- 分析可能的检测点
```

---

## 通信协议

### 请求格式

```json
{"cmd":"命令名","参数1":"值1","参数2":"值2"}
```

### 响应格式

```json
{
  "success": true|false,
  "data": {...},
  "error": "错误信息（失败时）"
}
```

### 命令列表

**核心业务命令**

| 阶段 | 命令 | 参数 | 说明 |
|------|------|------|------|
| Phase 4 | `search` | `keyword`, `page` | 100% 免 UI 搜索，优先用捕获的模板，fallback 硬编码 |
| Phase 6 | `bookDetail` | `bookId` | 获取书籍完整详情（书名/作者/简介/字数等） |
| Phase 7 | `getChapterList` | `bookId` | 获取全部章节目录（ID、名称、VIP 标记、字数等） |
| Phase 7 | `getChapterContent` | `bookId`, `chapterId` | 获取章节明文（自动下载加密数据 + bll.v 解密） |
| Phase 8 | `getChapterReviewSummary` | `bookId`, `chapterId` | 获取章节各段落评论数量和 paragraphId |
| Phase 8 | `getParagraphComments` | `bookId`, `chapterId`, `paragraphId`, `page`, `pageSize` | 获取某段落的文字评论列表（支持分页） |
| Phase 8 | `getAllParagraphComments` | `bookId`, `chapterId`, `pageSize` | 一次性获取章节所有段评（自动翻页，跳过章评） |

**基础设施命令**

| 阶段 | 命令 | 参数 | 说明 |
|------|------|------|------|
| Phase 1 | `ping` | - | 测试 RPC 连接 |
| Phase 1 | `getStatus` | - | 获取 Hook 状态（包括 OkHttpClient/bllVInstance 是否已捕获） |
| Phase 1 | `getLogs` | `limit`, `since`, `tag` | 增量获取日志缓冲区内容（支持时间戳过滤和关键词过滤） |
| Phase 1 | `getRecentDecrypted` | `limit` | 获取最近被 Cipher Hook 捕获的解密内容 |
| Phase 1 | `testChinese` | - | 测试 RPC 通道的中文编码是否正确 |

**调试命令**

| 阶段 | 命令 | 参数 | 说明 |
|------|------|------|------|
| Phase 7 | `dumpChapterItem` | - | 查看已保存的 ChapterItem 模板字段和 bllVInstance 状态 |
| Phase 7 | `testNoTemplate` | `bookId`, `chapterId` | 测试免模板路径（强制走 API 构造 ChapterItem） |
| Phase 7 | `probeBllV` | - | 探测 bll.v 类所有方法签名，用于版本适配 |
| - | `getSearchTemplate` | - | 查看捕获的搜索 API URL/Method/Body 模板 |
| - | `getCallRecords` | `limit` | 获取最近的方法调用记录（bll.v 等被 Hook 方法的调用日志） |
| - | `getEntries` | - | 获取已知的方法入口列表 |
| - | `clearRecords` | - | 清空方法调用记录 |
| Phase 10 | `fetchChapter` | `bookId`, `chapterId` | 获取章节内容（自动判断免费/VIP，VIP 自动购买+解密） |
| Phase 10 | `executeGet` | `url` | 通用 GET 请求（复用 OkHttpClient 签名） |
| Phase 10 | `executePost` | `url`, `body` | 通用 POST 请求（复用 OkHttpClient 签名） |
| - | `testFetch` | - | 硬编码测试命令（bookId=1209977, chapterId=23373921） |

---

## 关键常量与架构说明

### 核心常量

| 常量 | 值 | 含义 | 代码位置 |
|------|------|------|----------|
| `DEDUP_WINDOW_MS` | 500ms | 网络请求日志去重窗口，同 URL 在 500ms 内仅记录一次 | `MainHook.java:44` |
| `MAX_LOG_BUFFER` | 5000 | 内存日志缓冲区容量（超出时移除最旧的） | `MainHook.java:54` |
| `CRYPTO_LOG_LIMIT` | 30 | Cipher Hook 每分钟最多记录的解密日志条数（防止 HTTPS 流量淹没日志） | `MainHook.java:61` |
| `SOCKET_NAME` | `qdhook_rpc` | LocalSocket RPC 通道名称 | `MainHook.java:85` |
| `MAX_CALL_RECORDS` | 200 | 保留的方法调用记录数 | `MainHook.java:69` |

### Cipher Hook 六层过滤（重要架构设计）

Cipher Hook 用于监控 App 的 `javax.crypto.Cipher` 解密操作，但 HTTPS 流量每分钟会产生数千次 Cipher 调用，如果不过滤会淹没日志和缓冲区。设计了六层逐步过滤：

| 层级 | 过滤条件 | 作用 | 代码位置 |
|------|----------|------|----------|
| 第 1 层 | `opmode != DECRYPT_MODE` | 只追踪解密操作，跳过加密 | `MainHook.java:613` |
| 第 2 层 | `isTlsAlgorithm()` | 排除 GCM/CHACHA20/POLY1305/OAEP/RSA（TLS 算法） | `MainHook.java:617, 760-765` |
| 第 3 层 | `output.length < 64` | 跳过过短的解密结果（非内容数据） | `MainHook.java:708` |
| 第 4 层 | `rateLimitCheck()` | 每分钟最多 30 条日志 | `MainHook.java:709, 810-817` |
| 第 5 层 | Gzip 检测与解压 | 检查 `0x1f 0x8b` 魔数，解压后再分析 | `MainHook.java:713-729` |
| 第 6 层 | 内容识别 | 必须是 JSON（以 `{` 开头）或包含 5 个以上中文字符 | `MainHook.java:733` |

> **最终结论**：章节内容解密不走 `javax.crypto.Cipher`，但 Cipher Hook 仍保留用于监控安全检测、设备指纹等辅助解密操作。

### 二进制响应检测

`safegetcontent` API 返回的是加密二进制数据（非 JSON），OkHttp Interceptor 需要识别并跳过日志记录，否则 logcat 会打印大量乱码。

- **检测方法**：检查响应前 200 个字符中不可打印字符的比例，超过 20% 则判为二进制
- **阈值 20%**：经验值，加密数据的不可打印字符比例远超此阈值，正常 JSON/文本远低于此值
- 代码位置：`MainHook.java:577-586`（`looksLikeBinary()` 方法）

### 360 加固对 okio 包的重打包

读取 POST 请求体（如搜索请求）时，需要调用 okio 的 `BufferedSink` / `Buffer` 类。但 360 加固会将第三方库重打包到不同的包路径下，导致直接用 `okio.Buffer` 类名会找不到类。

- **解决方案**：通过 `writeTo(BufferedSink)` 方法签名中 `BufferedSink` 参数的 ClassLoader 动态加载 `Buffer` 类，而不是硬编码包名
- **静默失败**：如果读取失败，返回空字符串而不是抛异常，避免影响主流程
- 代码位置：`MainHook.java:529-563`（`readRequestBody()` 方法）

### RPC 协议

- **传输层**：`LocalSocket`（Android Unix 域套接字），名称 `qdhook_rpc`
- **消息分隔**：`\n` 换行符
- **请求格式**：单行紧凑 JSON，如 `{"cmd":"search","keyword":"斗破苍穹"}\n`
- **响应格式**：单行紧凑 JSON + `\n`
- **重要约束**：响应 JSON **绝对不能包含换行符**，否则客户端会在第一个 `\n` 处截断。`handleClient()` 在发送前统一通过 `new JSONObject(response).toString()` 去除格式化缩进
- 代码位置：`MainHook.java:1641-1670`（`handleClient()` 方法）

---

## 风险与注意事项

1. **最大风险**：未知的检测机制
   - 缓解：渐进式测试，每步观察 60+ 秒

2. **登录态过期**：Cookie 可能失效
   - 缓解：定期手动打开 App 刷新

3. **App 被杀**：系统清理后台 App
   - 缓解：使用保活策略或定期通过 ADB 唤醒

4. **API 变更**：起点更新后接口改变
   - 缓解：保持 Hook 代码的灵活性，便于快速适配

5. **VIP 内容访问限制**：VIP 章节需要账户余额
   - 缓解：已实现 `buyvipchapter` 自动购买流程，需确保账户有足够余额

---

## 技术限制

### WSL2 环境限制

| 限制 | 解决方案 |
|------|----------|
| WSL2 无法访问 ADB forward 端口 | 用 Windows Python 运行 poller/forwarder，或使用 `rpc_call.py` 桥接脚本 |
| `adb logcat -d` 管道输出为空 | dump 到设备文件再 pull |
| `am start` 返回 -92 | 改用 `monkey` 命令启动 |
| `ps -A \| grep` 不可靠 | dump 到文件后本地 grep |

### 360 加固对抗

| 问题 | 解决方案 |
|------|----------|
| `Application.onCreate` Hook 回调不触发 | 延迟线程 + `ActivityThread.currentApplication()` 获取 ClassLoader |
| `Thread.getContextClassLoader()` 返回 null | 必须用 `ActivityThread.currentApplication().getClassLoader()` |
| Pine `antiChecks` 必须为 true | 否则 360 检测到 Hook 框架直接闪退 |

### OkHttpClient 捕获陷阱（重要）

| 问题 | 解决方案 |
|------|----------|
| `Builder.build()` 首次捕获到非 API 客户端（如 Glide） | 改为从 `druidv6.if.qidian.com/argus` API 请求的 RealCall 反射获取 |
| `savedOkHttpClient == null` 条件导致 API 客户端无法覆盖先占位的非 API 客户端 | 使用独立的 `apiClientCaptured` 标志控制捕获时机 |
| 所有 RPC 请求返回 HTTP 402 签名错误 | 根因就是 OkHttpClient 没带签名拦截器，优先排查此问题 |

---

## 项目文件索引

| 文件 | 作用 |
|------|------|
| `app/src/main/java/com/example/qdhook/MainHook.java` | 核心 Hook 代码（RPC 服务、网络拦截、搜索、详情、章节列表、章节解密） |
| `start.sh` | 一键启动脚本 |
| `scripts/log_poller.py` | RPC 日志轮询器（Windows Python 运行） |
| `scripts/test_rpc_windows.py` | RPC 测试脚本 |
| `scripts/rpc_forwarder.py` | HTTP→RPC 转发器（可选） |
| `scripts/qidian_api.py` | RPC API Python 封装模块（QidianAPI 类 + CLI） |
| `scripts/batch_fetch.py` | 批量采集工具（search/list/fetch + --fix 修复模式） |
| `scripts/log_receiver.py` | TCP 日志接收器（已弃用） |
| `logs/qdhook.log` | 日志输出文件 |

## 文档索引

| 文档 | 内容 |
|------|------|
| `docs/IMPLEMENTATION_GUIDE.md` | 项目总览、架构、进展、决策汇总（本文件） |
| `docs/QIDIAN_REVERSE.md` | 逆向分析记录（加密方案、类结构、调用栈） |
| `docs/1. 搜索功能实现.md` | 搜索功能的完整实现过程和技术决策 |
| `docs/2. 日志同步与一键启动.md` | RPC 轮询日志方案和 start.sh 脚本说明 |
| `docs/3. 书籍详情功能方案.md` | 书籍详情 API 探测结果、响应结构、RPC 实现 |
| `docs/4. 章节阅读功能方案.md` | 章节目录 + 内容解密方案、bll.v L→K→R 调用链、测试结果 |
| `docs/5. 段评获取功能方案.md` | 段落评论 API 探测结果、响应格式、RPC 实现、测试验证 |
| `docs/6. Python 工具链使用说明.md` | QidianAPI 封装、batch_fetch.py 三步工作流、断点续传、增量更新 |
| `docs/7. VIP 章节购买与解密方案.md` | buyvipchapter API 发现过程、VIP 完整流程、bll.v.N 解密、.qd 路径陷阱 |

---

## 结论

**搜索 → 书籍详情 → 章节目录 → 免费/VIP 章节内容解密 → 段落评论 → 批量采集** 的完整链路已全部实现 100% 免 UI 自动化。

核心技术路线：
1. **API 请求**：复用 App 的 OkHttpClient（含签名拦截器）+ 反射构造请求，支持 GET/POST
2. **免费章节解密**：触发 App 内部 `bll.v` 类的 L→K→R 方法链，afterCall hook 自动捕获解密后的明文
3. **VIP 章节解密**：`buyvipchapter` 购买 → `getvipcontent` 下载 → `bll.v.N` 解密（与免费章节走不同的解密路径）
4. **段落评论**：`getchapterrepagesummary` 获取摘要 + `getparagraphscomments` 逐段获取评论（自动翻页）
5. **批量采集**：`batch_fetch.py` 提供 search/list/fetch CLI + `--fix` 粒度修复（正文/评论独立判断）

关键前置条件（**全部自动完成，无需任何手动操作**）：
- `savedOkHttpClient`：App 启动后自动从 API 请求中捕获
- `bllVInstance`：App 启动时恢复阅读状态自动捕获，无阅读记录时可反射创建
- `ChapterItem`：通过章节列表 API 的 JSON 数据自动构造，不需要模板

已验证书籍：从十二形拳开始肉身成圣、雾都狩魔笔记、诡秘之主、**斗破苍穹**（从未打开过的书，完全免 UI 验证通过），多章解密均成功。VIP 章节测试：连续 3 个未购买 VIP 章节全部购买+解密成功。段评测试：第1章获取到 44 个有评论段落共 251 条评论。
