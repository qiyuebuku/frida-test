# 盐言故事 App 逆向工程记录

## 一、目标与背景

目标：Hook 盐言故事 Android App (`com.zhihu.vip.android`)，捕获网络 API 请求/响应数据和小说正文内容。

参考起点读书逆向的成功经验（详见 `hooker/docs/QIDIAN_REVERSE.md`），采用相同的 **Zygisk + Pine** 方案，但作为完全独立的项目，与起点读书代码零耦合。

---

## 二、App 技术分析

### 2.1 基本信息

| 项目 | 值 |
|------|-----|
| 包名 | `com.zhihu.vip.android` |
| 版本 | v1.93.0 (versionCode 10606) |
| 目标 SDK | 31 |
| 最低 SDK | 23 |
| 启动 Activity | `LauncherActivity` |
| 主 Activity | `com.zhihu.android.app.ui.activity.MainActivity` |
| URL Scheme | `zhvip://`、`story.zhihu.com`、`soia.zhihu.com` |

### 2.2 App 架构特征

- **React Native 应用**（Hermes JS 引擎，`libhermes.so`、`libreact_*` 系列 native 库）
- **多进程架构**：
  - 主进程：`com.zhihu.vip.android`
  - 文件下载进程：`com.zhihu.vip.android:filedownloader`
  - 频道进程：`com.zhihu.vip.android:channel`
- **知乎生态集成**：OAuth2 授权、推送、腾讯/微博 SDK 登录
- **Deep Link 支持**：`zhvip://`、`zhihuoauth2://`、`zhihupush://`

### 2.3 加固与保护分析

| 保护层 | 对应 SO |
|--------|---------|
| 梆梆加固 (Bangcle) | `libbangcle_crypto_tool.so` |
| 阿里安全 (MSAOA) | `libmsaoaidsec.so` |
| 知乎自研 | `libzxprotect.so` |

### 2.4 与起点读书 (360加固) 的对比

| 对比维度 | 起点读书 (360加固) | 盐言故事 (梆梆加固) |
|----------|-------------------|-------------------|
| 加固 SO | `libjiagu_vip.so` | `libbangcle_crypto_tool.so` |
| 代码完整性检测 | ✅ 延迟 40-50s 检测 ART 方法入口 | ❌ **未检测**（已验证） |
| ART 内部结构检测 | ✅ 检测 `disableHiddenApiPolicy` | ❓ 未测试（不需要） |
| LSPosed 检测 | ✅ 即时检测 bridge SO | ❓ 未测试 |
| Frida 检测 | ✅ .text 段 + maps + 线程名 | ❓ 未测试 |
| Pine SO 检测 | ✅ 检测名单含 `libpinesafecheck.so` | ❌ **未检测**（已验证） |

**实测结论**：梆梆加固的检测能力明显弱于 360加固。Pine hook App 类方法（OkHttpClient$Builder.build()）在 360加固下会触发延迟崩溃，但在梆梆加固下完全无反应。

---

## 三、项目结构

与起点读书项目 (`hooker/`) 完全独立，代码零耦合：

```
/home/yuyang/frida-test/yanyan/
├── app/
│   ├── build.gradle                          # Android 编译配置，依赖 Pine 0.3.0
│   ├── src/main/AndroidManifest.xml
│   └── src/main/java/com/yuyang/yyhook/
│       └── MainHook.java                     # Java 层 hook 逻辑
├── zygisk/
│   ├── jni/
│   │   ├── main.cpp                          # Zygisk 模块（TARGET_PKG = com.zhihu.vip.android）
│   │   └── zygisk.hpp                        # Zygisk API
│   ├── magisk/
│   │   ├── module.prop                       # id=yyhook_zygisk
│   │   ├── dex/                              # DEX + libpine.so
│   │   └── zygisk/
│   │       └── arm64-v8a.so                  # Zygisk 模块
│   └── extracted/                            # APK 提取临时目录
└── docs/
    └── YANYAN_REVERSE.md                     # 本文档
```

**与起点项目的差异**：

| 项目 | hooker (起点) | yanyan (盐言) |
|------|--------------|--------------|
| 包名 | `com.yuyang.qdhook` | `com.yuyang.yyhook` |
| 日志 TAG | `QDHook` / `QDCrypto` | `YYHook` |
| Magisk 模块 ID | `qdhook_zygisk` | `yyhook_zygisk` |
| 目标 App | `com.qidian.QDReader` | `com.zhihu.vip.android` |
| DEX 路径 | `/data/adb/modules/qdhook_zygisk/dex/` | `/data/adb/modules/yyhook_zygisk/dex/` |

---

## 四、测试计划（渐进式验证）

核心原则：**每一步只增加一个变量**，观察 App 是否正常运行 60+ 秒，逐步摸清梆梆加固的检测面。

### 测试 1：空注入

- **操作**：MainHook.entry() 只打一条日志，不加载 Pine，不做任何 hook
- **验证**：Zygisk 注入本身是否触发梆梆加固检测
- **结果**：✅ 通过 — App 稳定运行 60+ 秒，无崩溃

### 测试 2：加载 Pine SO（不初始化）

- **操作**：`System.load(pineSoPath)`，不调用 `Pine.ensureInitialized()`
- **验证**：maps 中出现 libpine.so 是否触发检测
- **结果**：✅ 通过 — `libpine.so loaded successfully`，App 稳定 60+ 秒

### 测试 3：Pine 初始化（安全配置）

- **操作**：PineConfig 安全配置 + `Pine.ensureInitialized()`
- **配置**：`antiChecks=true`, `disableHiddenApiPolicy=false`
- **验证**：Pine ART hook 引擎初始化是否触发检测
- **结果**：✅ 通过 — `Pine initialized`，App 稳定 60+ 秒

### 测试 4：Hook Application.onCreate（框架类）

- **操作**：Pine.hook(Application.onCreate)，回调中打日志
- **验证**：框架类 hook 是否触发检测
- **结果**：✅ 通过 — Hook 回调正常触发，App 稳定 60+ 秒
- **关键发现**：
  - Application 类名：`com.zhihu.android.app.ZhihuApplication`
  - ClassLoader：`dalvik.system.PathClassLoader`

### 测试 5：注入 OkHttp interceptor

- **操作**：动态代理拦截器 + Pine.hook(OkHttpClient$Builder.build())
- **验证**：(a) 盐言故事是否使用 OkHttp (b) 是否触发延迟检测
- **结果**：✅ 通过 — 成功捕获大量 API 请求/响应，App 稳定 60+ 秒
- **关键发现**：
  - **盐言故事确实使用 OkHttp**（React Native 底层网络层）
  - **API 响应为明文 JSON，未加密！** 推荐榜接口直接返回故事标题和正文摘要
  - **梆梆加固未检测到 OkHttp Builder.build() 的 Pine hook**
  - 无 360加固那样的延迟代码完整性检测

### 测试 6：安装 Crypto hooks + 内容加密方案分析

- **操作**：Hook `javax.crypto.Cipher`，捕获所有加密/解密操作
- **验证**：章节内容是否加密、用什么算法
- **结果**：✅ 已完成 — 发现完整加密架构

**Java Cipher 层捕获到的算法**：

| 算法 | 用途 | 备注 |
|------|------|------|
| RSA/ECB/PKCS1Padding | 加密 16字节随机密钥 K1 → `trans_key` | 客户端生成 K1，用 RSA 公钥加密后发送 |
| DESede/CBC | 固定密钥 `@#$!i89o23*&p9)-{qfv45,>` + IV `00000000` | 启动阶段使用，非内容解密 |
| AES/CBC/PKCS5Padding | 遥测/埋点数据加密 | 非内容解密 |

**关键发现：章节正文的 AES 解密不通过 Java `javax.crypto.Cipher`！**

**章节内容 API 流程**：
1. `manu_core` API → 返回 `manuscript_content.data.script`（Base64 加密内容，~18-24KB）
2. `manuscript_content.data.script_type` = 1（加密标识）
3. `manuscript/code` API → 返回 `article_code`（32字节 Base64，实为 AES 密钥）

**RSA 密钥交换**：
- 客户端生成 16字节随机 AES 密钥 K1（`MarketEncryptUtils.j()`）
- 用 RSA 公钥加密 K1 → `trans_key`（`MarketEncryptUtils.h(pubKey, K1)`）
- 服务端用 K1 加密 article_code 后返回

---

## 五、构建与部署流程

### 5.1 构建步骤

```bash
# 1. 编译 Java 代码
cd /home/yuyang/frida-test/yanyan
JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
ANDROID_HOME=/home/yuyang/android-sdk \
/home/yuyang/.gradle-dist/gradle-8.9/bin/gradle :app:assembleDebug

# 2. 提取 DEX
cd zygisk/extracted
unzip -o ../../app/build/outputs/apk/debug/app-debug.apk 'classes*.dex'

# 3. 编译 Zygisk SO（仅修改 main.cpp 时需要）
export NDK=/home/yuyang/android-sdk/ndk/27.0.12077973
$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android26-clang++ \
    -shared -fPIC -std=c++17 -O2 -s \
    -o zygisk/magisk/zygisk/arm64-v8a.so zygisk/jni/main.cpp -llog -ldl
```

### 5.2 部署到手机

```bash
ADB="/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe -s 3B15BJ00GZL00000"

# DEX 更新（不需要重启手机）
for f in classes.dex classes2.dex classes3.dex; do
    $ADB push yanyan/zygisk/extracted/$f /data/local/tmp/yyhook/$f
done
# adb shell → su → cp /data/local/tmp/yyhook/classes*.dex /data/adb/modules/yyhook_zygisk/dex/

# 重启 App
$ADB shell am force-stop com.zhihu.vip.android
$ADB shell am start com.zhihu.vip.android

# 查看日志
$ADB logcat -d | grep YYHook
```

---

## 六、测试结果记录

### 测试 1-4 总结（2026-02-05）

| 步骤 | 操作 | 梆梆加固反应 | 结论 |
|------|------|-------------|------|
| ① 空注入 | 仅打日志，不加载 Pine | **正常** 60+ 秒 | Zygisk 注入不触发检测 |
| ② 加载 Pine SO | `System.load()`，不初始化 | **正常** 60+ 秒 | maps 中的 libpine.so 不触发检测 |
| ③ Pine 初始化 | 安全配置 + `ensureInitialized()` | **正常** 60+ 秒 | Pine ART hook 引擎不触发检测 |
| ④ Hook 框架类 | Pine.hook(Application.onCreate) | **正常** 60+ 秒 | 框架类 hook 不触发检测 |

**Application 类名**：`com.zhihu.android.app.ZhihuApplication`

### 测试 5 详细结果（2026-02-05）

**操作**：动态代理 OkHttp interceptor + Pine.hook(OkHttpClient$Builder.build())

**日志摘要**：
```
I YYHook: Application.onCreate: com.zhihu.android.app.ZhihuApplication
I YYHook: Logging interceptor created via Proxy
I YYHook: OkHttpClient.Builder.build() hooked for interceptor injection
I YYHook: Interceptor injected into OkHttpClient.Builder
I YYHook: → GET https://api.zhihu.com/km-indep-home/home/modules/v2?offset=0&channel_type=discovery...
I YYHook: ← [200] https://api.zhihu.com/km-indep-home/home/modules/v2?...
I YYHook:   Body: {"data":[{"module_type":"pin_top_list_v3","card_type":"FCT38C",...故事标题和正文摘要...}]}
```

**捕获到的 API 端点**：

| 分类 | 端点 | 方法 | 响应格式 |
|------|------|------|---------|
| 首页推荐 | `api.zhihu.com/km-indep-home/home/modules/v2` | GET | 明文 JSON（含故事标题、简介、作者） |
| App 配置 | `m-cloud.zhihu.com/api/cloud/vip/config/all` | POST | 明文 JSON |
| 用户引导 | `api.zhihu.com/me/guides` | GET | 明文 JSON |
| 未读消息 | `api.zhihu.com/km-indep-home/message/un_read` | GET | 明文 JSON |
| 用户信息 | `api.zhihu.com/people/self` | GET | 明文 JSON |
| AB 实验 | `api.zhihu.com/ab/api/v1/products/zhihu/platforms/android/config` | POST | protobuf |
| SDK 查询 | `zeus-api.vemarsdev.com/zeus/client/v2/query` | POST | 明文 JSON |
| 健康检查 | `appcloud.zhihu.com/check_health` | GET | - |
| UGC | `api.zhihu.com/km-indep-home/ugc/user/white_list` | GET | 明文 JSON |
| 日志上报 | `duga.zhihu.com/action/zhihu_vip/log` | POST | protobuf |
| 微信 | `api.zhihu.com/km-wechat/public_account/*/subscribe_info` | GET | 明文 JSON |
| 资源 | `appcloud2.zhihu.com/v3/resource` | GET | - |
| 页面模板 | `page-modular.zhihu.com/templates` | GET | - |
| 登录配置 | `api.zhihu.com/sku/km_resource` | GET | - |
| 屏幕推广 | `api.zhihu.com/km-indep-home/screen/promotion` | GET | - |

**重大发现**：

1. **API 响应为明文 JSON，未加密** — 与起点读书（3DES-CBC 加密）完全不同
2. **首页推荐接口直接返回故事标题和正文摘要**，包含完整的推荐榜数据
3. **盐言故事确实使用 OkHttp** 作为网络层（React Native 默认）
4. **梆梆加固未检测到 OkHttpClient$Builder.build() 的 Pine hook** — 比 360加固宽松得多
5. **App 稳定运行 60+ 秒，无延迟崩溃** — 无 360加固那样的延迟代码完整性检测

---

## 七、梆梆加固检测面总结

### 与 360加固的对比（基于实测）

| 对比维度 | 起点读书 (360加固) | 盐言故事 (梆梆加固) |
|----------|-------------------|-------------------|
| Zygisk 空注入 | ✅ 不检测 | ✅ 不检测 |
| libpine.so 加载 | ✅ 不检测 | ✅ 不检测 |
| Pine 初始化（安全配置） | ✅ 不检测 | ✅ 不检测 |
| Hook 框架类 (Application) | ✅ 不检测 | ✅ 不检测 |
| Hook App 类 (OkHttp Builder) | ⚠️ 延迟 40-50s 检测 | ✅ 不检测 |
| `disableHiddenApiPolicy=true` | ❌ 即时检测 | ❓ 未测试（不需要） |

**结论**：梆梆加固对 Zygisk + Pine 方案的检测能力**明显弱于** 360加固。Pine hook 各层级均未触发任何检测，包括 hook App 加载的类（这在 360加固中会触发延迟检测）。

---

## 八、风险评估（已更新）

| 风险 | 级别 | 实测结果 |
|------|------|---------|
| ~~梆梆加固检测 Pine hook~~ | ~~高~~ | ✅ 已验证：全部通过，检测能力弱于 360加固 |
| ~~React Native JS 层加密~~ | ~~中~~ | ✅ 已验证：API 响应为明文 JSON，无需额外处理 |
| ~~多进程干扰~~ | ~~低~~ | ✅ 仅注入主进程，无干扰 |
| ~~网络层不走 OkHttp~~ | ~~低~~ | ✅ 已验证：RN 确实使用 OkHttp |
| ~~章节正文是否加密~~ | ~~中~~ | ✅ 已验证：内容加密，使用 AES/CFB8/NoPadding |
| ~~未登录状态下的内容限制~~ | ~~中~~ | ✅ 免费章节可正常获取 |
| 从 Native 层提取解密后明文 | 中 | ⏳ 需 Hook JNI 层 |

---

## 九、内容加密架构（DEX 反编译分析，2026-02-05）

### 9.1 总体架构

盐言故事有**两条内容处理路径**，由 `script_type` 决定：

```
script_type == 1 → 加密内容（大多数章节）
script_type != 1 → Base64 编码明文（免费预览等）
```

### 9.2 加密内容处理流程（script_type == 1）

```
┌─ 客户端 ─────────────────────────────────────────────────────────┐
│                                                                    │
│  1. MarketEncryptUtils.j()                                        │
│     → 生成 16字节随机 AES-128 密钥 K1                              │
│                                                                    │
│  2. MarketEncryptUtils.h(RSA_PUB_KEY, K1)                         │
│     → RSA 加密 K1 → Base64(trans_key)                             │
│                                                                    │
│  3. 请求 manuscript/code API                                       │
│     → 参数: section_id, trans_key, timestamp, signature            │
│     → 响应: { article_code: "Base64...", log: "F" }               │
│                                                                    │
│  4. MarketEncryptUtils.a(article_code, K1)                        │
│     → Base64 解码 article_code                                     │
│     → AES/CFB8/NoPadding 解密: IV=前16字节, Key=K1               │
│     → 得到 decryptKey (byte[])                                    │
│                                                                    │
│  5. 构建 HTML                                                      │
│     → script 内容包裹在 <Encryption>...</Encryption> 标签中        │
│     → HTML 文件写入磁盘                                            │
│                                                                    │
│  6. EBookChapter 对象                                              │
│     → aesKey = article_code (解密前)                               │
│     → randomKey = log                                              │
│     → random = randomS (K1 的某种表示)                             │
│     → path = HTML 文件路径                                         │
│                                                                    │
│  7. Native C++ 层 (EpubWrap JNI)                                  │
│     → 读取 HTML 文件                                               │
│     → 提取 <Encryption> 标签中的加密内容                           │
│     → 使用 aesKey/randomKey 解密                                   │
│     → 渲染为页面                                                   │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

### 9.3 关键类与方法

| 类 | DEX | 功能 |
|----|-----|------|
| `com.zhihu.android.app.market.utils.h` (MarketEncryptUtils) | classes6 | AES/CFB8/NoPadding 解密、RSA 加密、随机密钥生成 |
| `com.zhihu.android.app.w0.q.a` (EBookDecryptUtil) | classes6 | ePub 文件解密（用于电子书，非盐言） |
| `com.zhihu.android.vip.manuscript.manuscript.x6.b0` | classes10 | 盐言故事页面构建（HTML 模板 + 加密内容） |
| `com.zhihu.android.vip.reader.common.r.p` (EBookChapterInfoPrepareManager) | classes10 | 电子书章节准备（密钥解密 + 下载） |
| `com.zhihu.android.vip.reader.common.r.u` (EBookEpubHtmlDecryptManager) | classes10 | ePub HTML 解密管理 |
| `com.zhihu.android.service.reader_sdk.ManuscriptFactoryImpl$b` | classes9 | IManuscriptRender 实现，调用 JNI 层 |
| `com.zhihu.android.app.w0.l` (EpubProcessor) | classes6 | ePub JNI 处理器封装 |
| `com.zhihu.android.app.nextebook.jni.EpubWrap` | classes6 | JNI native 方法 |
| `com.zhihu.android.app.nextebook.model.EBookChapter` | classes6 | 章节数据模型（aesKey, randomKey, random） |
| `com.zhihu.android.vip.manuscript.model.CodeResult` | classes10 | manuscript/code API 响应模型 |

### 9.4 AES 解密细节（MarketEncryptUtils.b()）

```java
// AES/CFB8/NoPadding 解密
// 输入: data = Base64.decode(article_code), key = K1 (16 bytes)
byte[] aesKey = Arrays.copyOfRange(key, 0, 16);     // AES-128
byte[] iv     = Arrays.copyOfRange(data, 0, 16);     // 前16字节为IV
byte[] cipher = Arrays.copyOfRange(data, 16, data.length); // 剩余为密文
Cipher c = Cipher.getInstance("AES/CFB8/NoPadding");
c.init(Cipher.DECRYPT_MODE, new SecretKeySpec(aesKey, "AES"), new IvParameterSpec(iv));
return c.doFinal(cipher);
```

**注意**：还有一个 `d()` 方法使用 **AES/CFB/NoPadding**（非 CFB8），两者区别在于反馈块大小。

### 9.5 RSA 公钥

```
-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDgkB5vTONXv15SukpyFKKbkO3m
MbZ8z4u8HwtV14qEoOJaOhh6pu75o6bojX3RFnWm3wHxFjmdJu1+JurChFiY2fxD
Q+SZWXKzNvfK/fvi3JNMfgVfp0HcuCzKDWE+vPeactLeTNnjFRYlnaUygiwm0KNE
hDDHw2/41xjcPLmPpQIDAQAB
-----END PUBLIC KEY-----
```

SHA-256 hash（用于签名）: `E48FBEA6AACC8177E10AF1190421E92B`

---

## 十、Hook 策略

### 10.1 已验证的可行方案

由于内容最终解密在 **Native C++ 层**（通过 `EpubWrap` JNI），直接 hook Java Cipher 无法获取解密后的明文。

**推荐方案：Hook `EpubWrap.getText()` 或 `ChapterInfoHandler`**

```
获取明文的 Hook 点（优先级从高到低）：
1. EpubWrap.getText() → 返回解密后的文本内容
2. ChapterInfoHandler → getPageInfos() 后包含页面文本
3. ManuscriptFactoryImpl$b.J0() → 解析完成后 pageInfos 中有内容
4. 直接 Hook AES/CFB8 解密 → 但需要先解密 article_code（两步操作）
```

### 10.2 备选方案：纯 Java 层解密

如果能同时捕获 K1（RSA 输入）和 article_code（API 响应），可以在 Java 层复现解密：
1. Hook RSA doFinal → 捕获 K1
2. Hook OkHttp → 捕获 article_code 和 script
3. AES/CFB8 解密 article_code → 得到 decryptKey
4. 用 decryptKey 解密 script → 得到明文

---

## 十一、后续方向

1. **Hook EpubWrap JNI 层**：捕获解密后的渲染文本
2. **或实现纯 Java 解密**：同时捕获 K1 + article_code + script，在 Java 层复现解密
3. **数据外传**：将捕获的数据通过 LocalSocket/BroadcastReceiver 实时发送到 PC 端
4. **完善自动化**：自动翻页 + 批量捕获章节内容

---

## 十、设备与环境信息

| 项目 | 详情 |
|------|------|
| 设备 | 一加 Ace6 |
| Android 版本 | 16 |
| Root | SukiSU Ultra ksud 4.1.1 (KernelSU) |
| Zygisk | ZygiskNext v1.3.0 |
| ADB | `/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe -s 3B15BJ00GZL00000` |
| 目标 App | 盐言故事 v1.93.0 |
| App 保护 | 梆梆加固 + 阿里安全 + 知乎自研 |
| Application 类 | `com.zhihu.android.app.ZhihuApplication` |
| ClassLoader | `dalvik.system.PathClassLoader` |

---

## 十二、测试 7：主动调用 getText() 提取解密文本（2026-02-05 成功）

### 12.1 关键发现

**getText() 在正常阅读时不被调用**。Native 渲染引擎直接在 Canvas/SurfaceView 上绘制文本，不经过 getText()。截屏 (screencap) 也无法捕获阅读页面内容（显示全黑），说明使用了硬件加速的 Surface 渲染。

但 `getChapterItemHeightArray()` 在每次章节加载时会被调用，且接收 `EPageIndex` 参数。`EPageIndex` 包含解密所需的所有信息。

### 12.2 解决方案

在 `getChapterItemHeightArray()` 的 afterCall hook 中：
1. 捕获 `EPageIndex` 参数和 `BaseJniWarp` 实例
2. 主动调用 `getText(ePageIndex, 0, Integer.MAX_VALUE)` 提取全文
3. 同时调用 `getTextWithPara()` 获取分段文本

### 12.3 实测结果

```
CHAPTER_LOADED: 20 pages
EPageIndex: filePath=/data/user/0/.../manuscript/1811826762830757888/.../1.3.0_20...
  aesKey=F6Zns+tJkHI4shtZ116Xpy0BiazSlpOPy8rMbbWKkxQ=
  random=TzSesXpIpwyzJIMO
  randomKey=
EXTRACTED_TEXT[3202]: 我和裴霄、祝琳的友谊跨越了整整 70 年...
EXTRACTED_PARA[1 paras, 3203 chars]

CHAPTER_LOADED: 23 pages
EPageIndex: filePath=/data/user/0/.../manuscript/1905219472874274899/.../1.3.0_20...
  aesKey=gEFu7cpIUh+eIQ/+31twXnYaZRDe87mGpO9kI2y1KZY=
  random=VqVCDzYBAlwyqlIU
  randomKey=ra
EXTRACTED_TEXT[3907]: 拍床戏时，男女主假戏真做...
```

### 12.4 完整解密流程（确认）

```
客户端生成 K1 (16字符随机串)
  → RSA加密(K1) → trans_key
  → POST /manuscript/code {section_id, trans_key}
  ← {article_code (Base64加密), log}

Native 引擎接收:
  EPageIndex.aesKey = article_code
  EPageIndex.random = K1
  EPageIndex.randomKey = log
  EPageIndex.filePath = 本地 epub 缓存路径

Native C++ 内部解密:
  1. 用 K1 解密 article_code → 真实 AES 密钥
  2. 用 AES 密钥解密 epub 文件中的加密内容
  3. 渲染到 Surface

getText(EPageIndex, 0, MAX_INT) → 返回完整解密文本
```

### 12.5 screencap 黑屏说明

阅读页面 `ManuscriptHostActivity` 使用 SurfaceView/硬件加速渲染，`adb exec-out screencap -p` 无法捕获该层内容。实际手机屏幕正常显示文本。这不影响 hook 数据提取。

### 12.6 核心 Hook 代码实现

```java
// 在 getChapterItemHeightArray 的 afterCall 中主动调用 getText
for (Method m : baseJniWarpClass.getDeclaredMethods()) {
    if ("getChapterItemHeightArray".equals(m.getName())) {
        Pine.hook(m, new MethodHook() {
            @Override
            public void afterCall(Pine.CallFrame callFrame) {
                try {
                    float[] heights = (float[]) callFrame.getResult();
                    if (heights == null) return;

                    Object ePageIndex = callFrame.args[0];
                    Object instance = callFrame.thisObject;

                    // 主动调用 getText 提取全文
                    String fullText = (String) textMethod.invoke(
                        instance, ePageIndex, 0, Integer.MAX_VALUE);
                    if (fullText != null && fullText.length() > 10) {
                        dumpToFile("extracted_fulltext", fullText);
                    }
                } catch (Throwable e) { /* ... */ }
            }
        });
    }
}
```

### 12.7 数据导出位置

提取的文本保存在：`/data/user/0/com.zhihu.vip.android/cache/yyhook_dump/`

拉取命令：
```bash
adb shell "su -c 'cp /data/user/0/com.zhihu.vip.android/cache/yyhook_dump/*.json /data/local/tmp/'"
adb pull /data/local/tmp/extracted_fulltext.json
```

---

## 十三、后续优化方向

1. **章节 ID 关联**：在导出文件名中包含 section_id，便于批量处理
2. **去重逻辑**：同一章节可能多次触发 getChapterItemHeightArray，需要去重
3. **自动化翻页**：通过模拟滑动实现整本书的批量提取
4. **元数据关联**：从 manu_core API 响应中提取书名、章节名等元数据
