# 起点读书 App 逆向工程记录

## 一、背景与目标

目标：Hook 起点读书 Android App (`com.qidian.QDReader`)，捕获网络 API 请求/响应数据。

该 App 使用 **360加固** (`libjiagu_vip.so`) 保护，检测能力极强。在此之前，以下方案均已失败：

| 方案 | 失败原因 |
|------|----------|
| Frida (原版/hluda/strongR) | 360加固校验 `.text` 段代码完整性，Frida inline hook 写入 trampoline 必然触发检测 |
| Frida Gadget (sucsand Zygisk) | Gadget 注入成功但 frida 协议层 attach 挂起，且代码完整性矛盾依旧 |
| LSPosed / Xposed | 空模块（不 hook 任何方法）仅注入就触发 `exit_self`，360加固检测 LSPosed bridge SO |
| mitmproxy | 只能抓包，无法操作 App 内部逻辑 |

**最终成功方案：原生 Zygisk 模块 + Pine 框架**——完全绕开 LSPosed/Xposed，在 Zygote fork 时直接注入独立的 ART hook 框架。

---

## 二、方案架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Zygote 进程                               │
│  fork() → 子进程                                             │
│    ┌──────────────────────────────────────────────────────┐  │
│    │ preAppSpecialize (Zygisk 回调)                        │  │
│    │   - 检测目标包名 com.qidian.QDReader                   │  │
│    │   - 通过 companion 从 root 域读取 DEX + libpine.so     │  │
│    ├──────────────────────────────────────────────────────┤  │
│    │ postAppSpecialize (Zygisk 回调)                       │  │
│    │   - 写 libpine.so 到 app cache 目录                    │  │
│    │   - InMemoryDexClassLoader 加载 3 个 DEX（内存不落地）   │  │
│    │   - 反射调用 MainHook.entry()                          │  │
│    │   - 删除 libpine.so 文件                               │  │
│    ├──────────────────────────────────────────────────────┤  │
│    │ MainHook.entry() (Java 层)                            │  │
│    │   - System.load(pineSoPath) 加载 Pine native 库        │  │
│    │   - PineConfig 配置（antiChecks=true 等）               │  │
│    │   - Pine.hook(Application.onCreate)                    │  │
│    │     └→ 回调中用 app ClassLoader hook OkHttp 网络层      │  │
│    ├──────────────────────────────────────────────────────┤  │
│    │ 360加固初始化 (libjiagu_vip.so)                        │  │
│    │   - 解壳、环境检测、VMP 方法注册                         │  │
│    │   - ⚠️ 此时 Pine hook 已经就位，但加固未检测到           │  │
│    ├──────────────────────────────────────────────────────┤  │
│    │ Application.onCreate 触发                              │  │
│    │   - Pine 回调执行                                       │  │
│    │   - 通过 java.lang.reflect.Proxy 创建 OkHttp Interceptor│  │
│    │   - Pine.hook(OkHttpClient$Builder.build()) 注入拦截器   │  │
│    │   - App 正常运行，所有 API 请求被拦截记录                 │  │
│    └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘

companion 进程 (root 权限)
  - 从 /data/adb/modules/qdhook_zygisk/dex/ 读取文件
  - 通过 Unix socket 发送给目标进程
```

**核心优势：整个注入链不依赖 LSPosed/Xposed，不在 maps 中留下可识别的 bridge SO，Pine 的 ART hook 方式不修改 native .text 段。**

---

## 三、开发过程与关键决策

### 3.1 第一步：验证空 Zygisk 模块是否触发检测

**目标**：确认 360加固是否检测 Zygisk 模块本身。

编写了一个最小 Zygisk 模块，仅在 `preAppSpecialize` 中打日志，不做任何注入操作。

**结果：QDReader 正常启动，无 `exit_self`。**

**结论**：360加固不检测 Zygisk 模块注入行为本身（与 LSPosed 不同）。这是整个方案成立的前提。

### 3.2 第二步：集成 Pine 框架

[Pine](https://github.com/canyie/pine) (`top.canyie.pine:core:0.3.0`) 是一个独立的 ART hook 框架，不依赖 Xposed API。核心 API：

```java
Pine.hook(Method, new MethodHook() {
    @Override public void beforeCall(Pine.CallFrame callFrame) { ... }
    @Override public void afterCall(Pine.CallFrame callFrame) { ... }
});
```

将 Pine 集成到 Zygisk 模块的流程：

1. 在 `app/build.gradle` 中添加 `implementation 'top.canyie.pine:core:0.3.0'`
2. 编译 APK 得到 3 个 DEX 文件（Pine 库 + MainHook 代码）
3. 从 APK 中提取 `lib/arm64-v8a/libpine.so`
4. 将 DEX 和 SO 放入 Zygisk 模块的 `dex/` 目录

### 3.3 第三步：解决一系列集成问题（踩坑记录）

#### 坑 1：libpine.so 写入路径权限

**问题**：`postAppSpecialize` 运行在 App 沙箱中，无法写入 `/data/local/tmp/`。

**解决**：在 `preAppSpecialize` 中保存 `args->app_data_dir`，写入 `{app_data_dir}/cache/.pine.so`。

#### 坑 2：System.load() linker namespace 限制

**问题**：从 C++ JNI 上下文调用 `System.load()` 时，Android linker namespace 限制导致加载失败。

**尝试**：先用 `dlopen` 加载 SO，再手动调用 `JNI_OnLoad`。

**新问题**：`JNI_OnLoad` 返回 -1，因为 Pine 的 JNI_OnLoad 需要通过当前 ClassLoader 查找 Pine 的 Java 类，但此时 DEX ClassLoader 还没创建。

**最终解决**：调整加载顺序——先创建 `InMemoryDexClassLoader`，然后从 Java 层调用 `System.load()`。具体做法是在 `MainHook.entry(ClassLoader, String)` 的 Java 方法中执行 `System.load(pineSoPath)`，这样 Pine 的 JNI_OnLoad 能通过调用栈找到正确的 ClassLoader。

#### 坑 3：Pine.ensureInitialized() 内部调用 System.loadLibrary("pine")

**问题**：Pine 初始化时内部会再次尝试加载 `libpine.so`（通过 `System.loadLibrary`），但 `InMemoryDexClassLoader` 的 nativeLibraryDirectories 不包含我们的路径。

**解决**：设置 `PineConfig.libLoader` 为空实现（no-op），因为我们已经通过 `System.load()` 加载了 SO：

```java
PineConfig.libLoader = new Pine.LibLoader() {
    @Override public void loadLib() { /* already loaded */ }
};
```

#### 坑 4：Application 在 postAppSpecialize 时尚未创建

**问题**：最初尝试在 `postAppSpecialize` 中通过 `ActivityThread.getApplication()` 获取 Application 对象来拿 ClassLoader，但此时 Application 还没创建，返回 null。

**解决**：改为 hook `Application.onCreate()`，在回调中通过 `app.getClassLoader()` 获取 App 的 ClassLoader，再用它加载 OkHttp 类安装网络 hook。

### 3.4 第四步：360加固检测面逐步测试（最关键）

这是整个开发过程中最关键的环节——逐步增加功能，观察哪一步会触发 360加固检测。

| 测试步骤 | 操作 | 360加固反应 | 结论 |
|----------|------|-------------|------|
| ① 空 Zygisk 模块 | 仅日志，不注入任何东西 | **正常** | Zygisk 注入本身不触发检测 |
| ② 加载 libpine.so（不初始化） | `System.load()` 加载 SO，不调用 `Pine.ensureInitialized()` | **正常** | maps 中存在 libpine.so 不触发检测 |
| ③ Pine 初始化（默认配置） | `Pine.ensureInitialized()` 使用默认参数 | **崩溃** (`exit_self`) | 默认配置触发检测 |
| ④ Pine 初始化 + antiChecks | `antiChecks=true`，其余默认 | **崩溃** | 仍然触发检测 |
| ⑤ Pine 初始化 + 禁用 hiddenApi | `antiChecks=true` + `disableHiddenApiPolicy=false` | **正常** | **找到关键配置** |
| ⑥ Pine hook Application.onCreate | 步骤⑤ + 实际 hook 一个方法 | **正常，hook 生效** | Pine ART hook 不触发检测 |
| ⑦ Pine hook OkHttp 网络层 | 步骤⑥ + hook OkHttp Request/Response | **~40秒内正常，之后崩溃** | ⚠️ 延迟检测，见下文 |

#### ⚠️ 关键发现 1：`disableHiddenApiPolicy` 是触发检测的根源

Pine 的默认配置中 `PineConfig.disableHiddenApiPolicy = true`，这会修改 ART runtime 的内部结构来绕过 Android 的 hidden API 限制。**360加固会检测这种 ART 内部结构的修改**。

将其设为 `false` 后，Pine 不再修改 ART runtime internals，360加固的检测就不会触发。代价是无法调用 Android hidden API，但对于 hook 公开的 OkHttp API 来说完全够用。

**完整的安全配置：**

```java
PineConfig.libLoader = new Pine.LibLoader() {
    @Override public void loadLib() { }       // 已手动加载，跳过
};
PineConfig.debug = false;                      // 关闭调试日志
PineConfig.debuggable = false;                 // 关闭可调试标记
PineConfig.antiChecks = true;                  // 启用 Pine 自身的反检测
PineConfig.disableHiddenApiPolicy = false;     // ⚠️ 关键：不修改 ART 内部结构
PineConfig.disableHiddenApiPolicyForPlatformDomain = false;  // 同上
```

#### ⚠️ 关键发现 2：360加固对 App 类有延迟代码完整性检测

步骤⑦中的 OkHttp hook 看似成功，但在 App 运行约 40-50 秒后会触发崩溃。这是 360加固的 **延迟代码完整性检测** 机制。

**症状**：App 启动正常，hook 生效并成功捕获数据。但约 40-50 秒后：
1. 360加固的 `hi_signal` handler 开始疯狂触发，输出 `hi_signal: catch signal 11`（SIGSEGV）
2. 每秒产生 22.8 万+ 条日志
3. 触发 Android 系统 `LOGS OVER PROC QUOTA(300)` 限制
4. 进程被杀死：`Process exited due to signal 6 (Aborted)`

**控制变量实验**：

| 测试场景 | 结果 | 结论 |
|----------|------|------|
| 仅 Pine.ensureInitialized()，无任何 hook | **稳定 90+ 秒** ✅ | Pine 初始化本身不触发 |
| 仅 hook Application.onCreate（框架类） | **稳定 90+ 秒** ✅ | 框架类 hook 安全 |
| hook okhttp3.Request$Builder.build()（App 类） | **~40 秒后崩溃** ❌ | App 加载的类触发延迟检测 |

**根因**：360加固在 App 启动后约 40-50 秒启动延迟代码完整性校验，检测 **App 加载的类**（如 OkHttp）的方法入口是否被修改。Pine 的 ART method entry replacement 会被检出。但 **Android 框架类**（如 `Application`）不在检测范围内。

**解决方案**：放弃直接 Pine hook OkHttp 方法，改用 **动态代理拦截器** 方案（见第四节）。

---

## 四、最终代码结构

### 4.1 文件清单

```
hooker/
├── app/
│   ├── build.gradle                          # Android 编译配置，依赖 Pine 0.3.0
│   ├── src/main/AndroidManifest.xml          # 最小清单
│   └── src/main/java/com/yuyang/qdhook/
│       └── MainHook.java                     # Java 层 hook 逻辑（Pine API）
├── zygisk/
│   ├── jni/
│   │   ├── main.cpp                          # Zygisk 模块 C++ 代码
│   │   └── zygisk.hpp                        # Zygisk API 头文件
│   ├── magisk/
│   │   ├── module.prop                       # Magisk 模块元数据
│   │   ├── dex/
│   │   │   ├── classes.dex                   # 编译产物（Pine + MainHook）
│   │   │   ├── classes2.dex
│   │   │   ├── classes3.dex
│   │   │   └── libpine.so                    # Pine native 库 (arm64)
│   │   └── zygisk/
│   │       └── arm64-v8a.so                  # Zygisk 模块编译产物
│   └── extracted/                            # APK 解压临时目录
```

### 4.2 核心代码：main.cpp (Zygisk 模块)

**职责**：在 Zygote fork 时注入目标进程，加载 DEX 和 Pine SO，调用 Java 入口。

关键流程：

```cpp
// preAppSpecialize: 检测目标进程 + 从 companion 接收文件
void preAppSpecialize(AppSpecializeArgs *args) {
    // 1. 检查包名是否为 com.qidian.QDReader
    // 2. 非目标进程 → DLCLOSE_MODULE_LIBRARY（卸载自身 SO）
    // 3. 保存 app_data_dir
    // 4. connectCompanion() → 接收 4 个文件（3 DEX + 1 SO）
}

// postAppSpecialize: 在 App 进程空间中执行注入
void postAppSpecialize(const AppSpecializeArgs *args) {
    // 1. 写 libpine.so 到 {app_data_dir}/cache/.pine.so
    // 2. 创建 InMemoryDexClassLoader（3 个 DEX 内存加载）
    // 3. loadClass("com.yuyang.qdhook.MainHook")
    // 4. 调用 MainHook.entry(systemClassLoader, pineSoPath)
    // 5. unlink(.pine.so)  // 用完即删
}

// companion_handler: root 进程，读取模块目录文件并发送
static void companion_handler(int fd) {
    // 从 /data/adb/modules/qdhook_zygisk/dex/ 读取 4 个文件
    // 通过 socket 发送给目标进程（协议：file_count + [size + data]...）
}
```

**为什么需要 companion**：`postAppSpecialize` 运行在 App 沙箱中，无法读取 `/data/adb/` 目录。companion 运行在 Magisk 的 root 上下文中，可以读取模块文件并通过 Unix socket 传给子进程。

### 4.3 核心代码：MainHook.java (动态代理拦截器)

**职责**：初始化 Pine 框架，通过动态代理创建 OkHttp Interceptor，注入到每个 OkHttpClient 中，捕获完整的请求/响应数据（包括 Body 内容）。

**架构演进**：最初直接用 Pine hook OkHttp 的 `Request$Builder.build()`、`RealCall.execute()` 等方法，但触发了 360加固的延迟代码完整性检测（见 3.4 节），导致 ~40 秒后崩溃。**最终方案** 仅 hook 两个方法（均不触发检测），实际的网络数据捕获由标准 Java 代码完成。

```
entry()
  ├→ System.load(pineSoPath)                    // 加载 Pine native 库
  ├→ PineConfig 安全配置                          // antiChecks=true, disableHiddenApiPolicy=false
  ├→ Pine.ensureInitialized()                    // 初始化 Pine 框架
  └→ Pine.hook(Application.onCreate)             // ① hook 框架类（安全）
       └→ beforeCall 回调
            ├→ 获取 app ClassLoader
            └→ injectInterceptor(cl)
                 ├→ Proxy.newProxyInstance()       // 用 JDK 动态代理创建 okhttp3.Interceptor
                 │    └→ InterceptorHandler        // InvocationHandler 实现
                 │         └→ intercept(Chain)      // 标准 Java 代码，非 Pine hook
                 │              ├→ chain.request()   → 获取请求信息（URL/Method/Body）
                 │              ├→ chain.proceed()   → 执行实际请求
                 │              └→ response.peekBody() → 读取响应 Body
                 │
                 └→ Pine.hook(OkHttpClient$Builder.build())  // ② hook App 类（低频调用）
                      └→ beforeCall: addInterceptor(proxy)    // 将代理拦截器注入 Builder
```

**为什么这个方案安全**：
- **Application.onCreate** 是 Android 框架类，不在 360加固检测范围内
- **OkHttpClient$Builder.build()** 虽然是 App 加载的类，但它只在创建 OkHttpClient 时调用（整个 App 生命周期可能只调用几次），360加固的延迟检测尚未来得及校验或此方法不在检测名单中
- **实际的请求/响应捕获逻辑** 完全是标准 Java 代码（`InterceptorHandler.invoke()`），没有任何 Pine hook 参与

**去重机制**：通过 `ConcurrentHashMap<URL, timestamp>` + 500ms 时间窗口去重。静态资源（.jpg/.png/.gif/.css/.js 等）直接跳过不记录。

**Body 读取**：
- **请求 Body**：通过反射调用 `requestBody.writeTo(buffer)` + `buffer.readUtf8()` 读取 POST 内容
- **响应 Body**：通过 `response.peekBody(16384L).source().readUtf8()` 创建 Body 副本读取，不影响 App 正常消费响应流

---

## 五、构建与部署流程

### 5.1 环境要求

```
JDK 17:     /usr/lib/jvm/java-17-openjdk-amd64
Android SDK: /home/yuyang/android-sdk (platform 34, build-tools 34.0.0)
NDK:         27.0.12077973
Gradle:      8.9
```

### 5.2 构建步骤

```bash
# 1. 编译 Java 代码（生成包含 Pine 库 + MainHook 的 APK）
JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
ANDROID_HOME=/home/yuyang/android-sdk \
/home/yuyang/.gradle-dist/gradle-8.9/bin/gradle :app:assembleDebug

# 2. 从 APK 中提取 DEX 文件
cd zygisk/extracted
unzip -o ../../app/build/outputs/apk/debug/app-debug.apk 'classes*.dex'

# 3. 编译 Zygisk native 模块（仅修改 main.cpp 时需要）
export NDK=/home/yuyang/android-sdk/ndk/27.0.12077973
$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android26-clang++ \
    -shared -fPIC -std=c++17 -O2 -s \
    -o arm64-v8a.so main.cpp -llog -ldl
```

### 5.3 部署到手机

```bash
ADB="/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe -s 3B15BJ00GZL00000"

# DEX 更新（不需要重启）
for f in classes.dex classes2.dex classes3.dex; do
    $ADB push zygisk/extracted/$f /data/local/tmp/$f
done
$ADB shell "su -c 'cp /data/local/tmp/classes*.dex /data/adb/modules/qdhook_zygisk/dex/'"

# SO 更新（需要重启）
$ADB push arm64-v8a.so /data/local/tmp/
$ADB shell "su -c 'cp /data/local/tmp/arm64-v8a.so /data/adb/modules/qdhook_zygisk/zygisk/'"
$ADB reboot

# 重启 App（DEX 更新后只需重启 App）
$ADB shell am force-stop com.qidian.QDReader
$ADB shell am start com.qidian.QDReader/.ui.activity.SplashActivity

# 查看日志
$ADB logcat -d | grep QDHook
```

**重要**：DEX 文件的更改不需要重启手机，因为 companion 在每次 App 启动时都会重新读取文件。但 Zygisk 模块的 native SO (`arm64-v8a.so`) 更改需要重启，因为 SO 在 Zygote 启动时加载一次。

---

## 六、运行效果

### 6.1 启动日志

```
I QDHook: Target process detected, loading resources from companion
I QDHook: Companion sending 4 files
I QDHook: File 0: 49320 bytes loaded        (classes.dex)
I QDHook: File 1: 868 bytes loaded          (classes2.dex)
I QDHook: File 2: 9616 bytes loaded         (classes3.dex)
I QDHook: File 3: 72024 bytes loaded        (libpine.so)
I QDHook: postAppSpecialize: loading Pine and hook DEX
I QDHook: libpine.so written to /data/user/0/com.qidian.QDReader/cache/.pine.so
I QDHook: InMemoryDexClassLoader created
I QDHook: MainHook class loaded
I QDHook: Calling MainHook.entry()
I QDHook: Pine hook entry called
I QDHook: libpine.so loaded
I QDHook: Pine initialized (antiChecks=true, no hiddenApi bypass)
I QDHook: Hook installed on Application.onCreate
I QDHook: Application.onCreate: com.qidian.QDReader.QDApplication   ← hook 生效
I QDHook: Logging interceptor created via Proxy
I QDHook: OkHttpClient.Builder.build() hooked for interceptor injection
I QDHook: Interceptor injected into OkHttpClient.Builder              ← 首次注入成功
```

### 6.2 捕获的 API 请求与响应示例

```
I QDHook: → POST https://druidv6.if.qidian.com/argus/api/v1/freshman/bookshelfbtn [application/json; charset=utf-8]
I QDHook:   ReqBody: {"changeId":0}
I QDHook: ← [200] https://druidv6.if.qidian.com/argus/api/v1/freshman/bookshelfbtn
I QDHook:   Body: {"Result":0,"ReturnCode":0,"ReturnMessage":"","Data":{"BookShelfButton":[]}}

I QDHook: → GET https://druidv6.if.qidian.com/argus/api/v4/client/getsplashscreen?localLabels=100
I QDHook: ← [200] https://druidv6.if.qidian.com/argus/api/v4/client/getsplashscreen?localLabels=100
I QDHook:   Body: {"Result":0,"ReturnCode":0,...,"Data":{"SplashData":[...]}}
```

### 6.3 已捕获的全部 API 端点（App 启动时）

**业务 API (`druidv6.if.qidian.com`)：**

| 分类 | 端点 | 方法 |
|------|------|------|
| 书架 | `/argus/api/v1/bookshelf/getad` | GET |
| 书架 | `/argus/api/v1/bookshelf/getHoverAdv` | GET |
| 书架 | `/argus/api/v1/bookshelf/getTopOperation` | GET |
| 书架 | `/argus/api/v1/bookshelf/getHighLevelBookReadInfo` | GET |
| 书城 | `/argus/api/v1/booksquare/getsquarepagepiece` | GET |
| 书城 | `/argus/api/v1/booksquare/getfloatcard` | GET |
| 搜索 | `/argus/api/v2/booksearch/shardWord` | POST |
| 推荐 | `/argus/api/v1/dailyrecommend/recommendBook` | GET |
| 推荐 | `/argus/api/v2/dailyrecommend/getdailyrecommend` | GET |
| 活动 | `/argus/api/v2/ploy/getactivitylist` | GET |
| 广告 | `/argus/api/v1/adv/getadvlistbatch` | GET |
| 广告 | `/argus/api/v2/adv/getadvlistbatch` | GET |
| 配置 | `/argus/api/v1/client/getconfSpecify` | GET |
| 配置 | `/argus/api/v1/young/getconf` | GET |
| 红点 | `/argus/api/v1/reddot/getdot` | GET |
| 弹窗 | `/argus/api/v1/popup/batchget` | GET |
| 新人 | `/argus/api/v1/freshman/bookshelfbtn` | GET |
| 新人 | `/argus/api/v1/freshman/freshmanGuidePopup` | GET |
| 用户 | `/argus/api/v1/user/getsimplediscover` | GET |

**其他服务：**

| 服务 | 端点 | 方法 |
|------|------|------|
| AB 测试 | `ywab.reader.qq.com/user/experiments/v2` | GET |
| 推送 | `upush.qidian.com/submit/deviceTokenV5` | POST |
| 推送 | `upush.qidian.com/submit/setTag` | POST |
| 日志 | `unitelogreport.reader.qq.com/ywslogcpt/QD/qdclientlog/access` | POST |

### 6.4 捕获的请求签名字段

| Header 字段 | 含义 | 示例 |
|------------|------|------|
| `QDSign` | 请求签名（Base64） | `R7TCs6Tou2Xs4Hrgv37zDe...` |
| `borgus` | 设备指纹 hash | `a7e27bbd8b5b172de0aa9e47aa0f7f7e` |
| `cecelia` | 会话标识 | `2_f9447a96667fab10dab281d5ce6bdab2...` |
| `tstamp` | 请求时间戳（毫秒） | `1770232767396` |
| `QDInfo` | 加密的设备/用户信息（Base64） | `7i/3Otdm4jrOlRni...` |
| `Cookie` | 包含 `qimei`, `ywkey`, `ywguid`, `appId` 等 | — |

---

## 七、360加固检测面分析总结

通过逐步测试，完整梳理了 360加固的检测范围：

### 会触发检测的操作

| 操作 | 检测机制 | 触发时机 |
|------|----------|----------|
| Frida attach/spawn | `.text` 段代码完整性校验（检测 inline hook trampoline） | 即时 |
| Frida Gadget inject | 同上 | 即时 |
| LSPosed 注入（即使空模块） | 检测 LSPosed bridge SO (`liblspd.so`) 或 ART 元数据变更 | 即时 |
| Xposed 框架注入 | 同上 | 即时 |
| `PineConfig.disableHiddenApiPolicy = true` | 检测 ART runtime 内部结构被修改 | 即时 |
| Pine hook App 加载的类方法 | **延迟 ART 方法入口完整性校验** | **~40-50 秒后** |
| `/proc/self/maps` 中的 frida 特征 | SVC 直接 syscall 读取 maps | 运行时 |
| 线程名含 frida 特征 | 遍历 `/proc/self/task/*/status` | 运行时 |

### 不会触发检测的操作

| 操作 | 原因 |
|------|------|
| 空 Zygisk 模块注入 | Zygisk 在 Zygote 层操作，加固不检测 |
| `System.load(libpine.so)` | maps 中出现 libpine.so 不在检测名单中 |
| `Pine.ensureInitialized()` (安全配置) | `antiChecks=true` + `disableHiddenApiPolicy=false` 不修改 ART internals |
| `Pine.hook(Application.onCreate)` | **框架类**的 ART 方法入口替换不在检测范围 |
| `Pine.hook(OkHttpClient$Builder.build())` | 低频调用方法，可能不在检测名单或检测尚未覆盖 |
| `java.lang.reflect.Proxy` 动态代理 | 标准 JDK 机制，不修改任何方法入口 |
| `InMemoryDexClassLoader` 加载自定义 DEX | 内存 DEX 加载不在检测范围 |
| 通过反射调用 App 内部方法 | 标准 Java 反射调用不触发任何检测 |

### 检测分层模型

```
Layer 0 (最早):   代码完整性 (.text CRC/hash) → 检测 Frida inline hook
Layer 1 (早期):   框架特征 (SO 名/类名/ART 元数据) → 检测 LSPosed/Xposed
Layer 2 (中期):   ART runtime 修改 → 检测 disableHiddenApiPolicy
Layer 3 (运行时): /proc/self/maps (SVC syscall) → 检测 Frida agent SO
Layer 4 (运行时): /proc/self/task/*/status → 检测 Frida 线程名
Layer 5 (延迟):   ART 方法入口完整性 → 检测 App 类的 method entry 被替换（~40-50s 延迟）

Pine + Zygisk + 动态代理方案绕过了全部 6 层：
  ✅ Layer 0: Pine 不修改 .text 段
  ✅ Layer 1: 不使用 LSPosed/Xposed 框架
  ✅ Layer 2: disableHiddenApiPolicy=false
  ✅ Layer 3: maps 中无 frida 特征
  ✅ Layer 4: 无 frida 线程
  ✅ Layer 5: 仅 hook 框架类 + 低频 App 类，核心网络捕获用动态代理而非 Pine hook
```

---

## 八、设备与环境信息

| 项目 | 详情 |
|------|------|
| 设备 | 一加 Ace6 |
| Android 版本 | 16 |
| 内核 | 6.6.66 |
| Root | SukiSU Ultra ksud 4.1.1 (KernelSU) |
| Zygisk | ZygiskNext v1.3.0 |
| ADB | `/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe -s 3B15BJ00GZL00000` |
| 目标 App | 起点读书 v7.9.440 (versionCode 1776) |
| App 保护 | 360加固 (libjiagu_vip.so) + 阿里安全 (libmsaoaidsec.so) |

---

## 九、网络数据包内容捕获（Body 读取）

### 9.1 方案演进过程

实现完整的请求/响应 Body 内容捕获经历了多次迭代，踩了不少坑：

#### 尝试 1：Hook ResponseBody.string() / bytes()（失败）

最初的思路是 hook `okhttp3.ResponseBody.string()` 和 `bytes()` 方法，在调用后读取返回值。

**问题**：需要通过 `bodyUrlMap` 建立 `ResponseBody identity → URL` 的映射关系，但 ResponseBody 对象的 identity hash 不稳定，且 `ResponseBody.string()` 只能调用一次（消耗流），hook 后 App 自身读取会失败。

#### 尝试 2：Hook Response.body() + peekBody()（日志洪泛灾难）

改为 hook `okhttp3.Response.body()` 方法，在返回时调用 `response.peekBody(32768L)` 创建 Body 副本。

**严重问题**：`Response.body()` 是一个 getter 方法，OkHttp 内部（拦截器链、缓存逻辑等）会频繁调用。单次 App 启动产生了 **27万+条日志**，触发 Android 系统的 `LOGS OVER PROC QUOTA(300)` 限制，导致：
- 所有 QDHook 日志被 logd 丢弃
- 手机弹出 **"三方应用异常：起点读书运行异常"** 系统警告
- App 本身可能因为日志 IO 阻塞而变慢

**教训**：永远不要 hook 高频 getter 方法。

#### 尝试 3：Hook RealCall 方法（短暂成功后崩溃）

改为 hook `okhttp3.RealCall` 的两个方法：
- `execute()` — 同步请求完成后捕获响应
- `getResponseWithInterceptorChain()` — 所有请求（同步+异步）完成后捕获响应

这解决了日志洪泛问题，但 **在 App 运行 40-50 秒后触发了 360加固的延迟代码完整性检测**（详见 3.4 节），导致进程被 SIGABRT 杀死。

#### 尝试 4：动态代理拦截器（最终方案 ✅）

完全放弃 Pine hook OkHttp 方法的思路，改用 **java.lang.reflect.Proxy** 创建标准的 OkHttp Interceptor：

1. 用 `Proxy.newProxyInstance()` 创建实现 `okhttp3.Interceptor` 接口的动态代理对象
2. 在代理的 `InvocationHandler.invoke()` 中实现 `intercept(Chain)` 方法逻辑
3. 仅 hook `OkHttpClient$Builder.build()`（这是低频方法），在 `beforeCall` 中调用 `addInterceptor()` 将代理拦截器注入

**优势**：
- 实际的请求/响应捕获逻辑是标准 Java 代码，不涉及任何 ART 方法修改
- `OkHttpClient$Builder.build()` 在整个 App 生命周期中调用次数极少，不触发延迟检测
- 拦截器模式是 OkHttp 的标准扩展方式，行为完全符合预期

**结果**：App 稳定运行 120+ 秒，成功捕获 76+ 条请求/响应日志，无任何崩溃。

### 9.2 关键技术难点与解决方案

#### 难点 1：Pine 无法解析匿名内部类的继承方法

`response.peekBody()` 返回的类型是 `okhttp3.ResponseBody$1`（匿名内部类）。调用其继承自 `ResponseBody` 的 `string()` 方法时，Pine 报错：

```
okhttp3.ResponseBody$1.string []
```

Pine 在匿名类上调用通过继承得到的方法时会解析失败。

**解决**：不调用 `peekBody.string()`，改为调用 `peekBody.source().readUtf8()`。`source()` 返回的是具体类（非匿名类），`readUtf8()` 方法可以正常解析。

```java
// 错误 ❌
Method stringMethod = peekBody.getClass().getMethod("string");
String body = (String) stringMethod.invoke(peekBody);

// 正确 ✅
Method sourceMethod = peekBody.getClass().getMethod("source");
Object source = sourceMethod.invoke(peekBody);
Method readUtf8 = source.getClass().getMethod("readUtf8");
String body = (String) readUtf8.invoke(source);
```

#### 难点 2：360加固重打包 okio 类导致 ClassNotFoundException

读取 POST 请求体需要使用 `okio.Buffer` 类，但直接通过 `appClassLoader.loadClass("okio.BufferedSink")` 会抛 `ClassNotFoundException`。360加固将 okio 类重打包到了不同的命名空间。

**解决**：通过反射从 `RequestBody.writeTo(BufferedSink)` 方法的参数类型上获取正确的 ClassLoader：

```java
// 1. 找到 writeTo 方法
Method writeToMethod = null;
for (Method m : requestBody.getClass().getMethods()) {
    if ("writeTo".equals(m.getName()) && m.getParameterTypes().length == 1) {
        writeToMethod = m;
        break;
    }
}

// 2. 从参数类型的 ClassLoader 加载 Buffer 类
Class<?> sinkType = writeToMethod.getParameterTypes()[0]; // BufferedSink
ClassLoader okioLoader = sinkType.getClassLoader();       // 360加固的 ClassLoader
Class<?> bufferClass = okioLoader.loadClass(pkg + ".Buffer");

// 3. 创建 Buffer 实例，写入并读取
Object buffer = bufferClass.getDeclaredConstructor().newInstance();
writeToMethod.invoke(requestBody, buffer);
String body = (String) bufferClass.getMethod("readUtf8").invoke(buffer);
```

**原理**：不管 360加固如何重打包，`writeTo` 方法的参数类型总是指向正确的 `BufferedSink` 类，其 ClassLoader 也能正确加载同包下的 `Buffer` 类。

#### 难点 3：RealCall 类名在不同 OkHttp 版本中不同

不同版本的 OkHttp 中，`RealCall` 的完整类名不同：
- 旧版：`okhttp3.RealCall`
- 新版：`okhttp3.internal.connection.RealCall`

**解决**：遍历尝试多个类名：

```java
for (String name : new String[]{
        "okhttp3.internal.connection.RealCall",
        "okhttp3.RealCall",
        "okhttp3.internal.http.RealInterceptorChain"}) {
    try {
        realCallClass = cl.loadClass(name);
        break;
    } catch (ClassNotFoundException ignored) {}
}
```

起点读书使用的是 `okhttp3.RealCall`。

### 9.3 捕获结果统计

App 启动（到达主界面）期间捕获的数据：

| 类型 | 数量 | 说明 |
|------|------|------|
| 请求 URL + Headers | 30+ | 去重后的唯一请求 |
| 响应 Body (JSON) | 38 | 通过 `peekBody(32768L)` 读取，不影响 App 正常运行 |
| 请求 Body (POST) | 8 | JSON/form 格式的 POST 请求体 |

### 9.4 捕获到 Body 的请求端点

以下是成功捕获到响应 Body 内容的 API 端点：

| 分类 | 端点 | Body 内容概述 |
|------|------|--------------|
| 配置 | `/argus/api/v1/client/getconf` | 客户端配置参数 |
| 配置 | `/argus/api/v1/client/getconfSpecify` | 指定配置项 |
| 配置 | `/argus/api/v3/client/r1` | 客户端上报配置 |
| 配置 | `/argus/api/v1/young/getconf` | 青少年模式配置 |
| 书架 | `/argus/api/v1/bookshelf/getad` | 书架广告位数据 |
| 书架 | `/argus/api/v1/bookshelf/getHoverAdv` | 书架悬浮广告 |
| 书架 | `/argus/api/v1/bookshelf/getTopOperation` | 书架顶部运营位 |
| 书架 | `/argus/api/v1/bookshelf/getHighLevelBookReadInfo` | 书籍阅读信息 |
| 书城 | `/argus/api/v1/booksquare/getsquarepagepiece` | 书城分页内容（书单、推荐） |
| 书城 | `/argus/api/v1/booksquare/getfloatcard` | 书城浮窗卡片 |
| 搜索 | `/argus/api/v2/booksearch/shardWord` | 搜索联想词/热搜 |
| 推荐 | `/argus/api/v1/dailyrecommend/recommendBook` | 每日推荐书籍列表 |
| 推荐 | `/argus/api/v2/dailyrecommend/getdailyrecommend` | 每日推荐详情 |
| 广告 | `/argus/api/v1/adv/getadvlistbatch` | 批量广告位数据 |
| 广告 | `/argus/api/v2/adv/getadvlistbatch` | 批量广告位数据 v2 |
| 活动 | `/argus/api/v2/ploy/getactivitylist` | 运营活动列表 |
| 弹窗 | `/argus/api/v1/popup/batchget` | 弹窗配置 |
| 红点 | `/argus/api/v1/reddot/getdot` | 红点提示状态 |
| 新人 | `/argus/api/v1/freshman/bookshelfbtn` | 新人书架按钮 |
| 新人 | `/argus/api/v1/freshman/freshmanGuidePopup` | 新人引导弹窗 |
| 用户 | `/argus/api/v1/user/getsimplediscover` | 用户发现页数据 |
| 音频 | `/argus/api/v1/audio/gettonegroup` | 音频音效分组 |
| 深链 | `/argus/api/v3/deeplink/geturl` | 深度链接 URL |
| 推送 | `upushv6.qidian.com/submit/deviceTokenV5` | 设备推送 Token 注册 |
| 推送 | `upushv6.qidian.com/submit/setTag` | 推送标签设置 |
| 闪屏 | `/argus/api/v4/client/getsplashscreen` | 开屏广告数据 |
| 推送上报 | `/argus/api/v1/push/report` | 推送状态上报 |

### 9.5 数据格式观察

所有 API 响应均为 JSON 格式，统一的外层结构：

```json
{
    "Result": 0,
    "ReturnCode": 0,
    "ReturnMessage": "",
    "Data": { ... }
}
```

POST 请求体示例：

```json
// POST /argus/api/v1/freshman/bookshelfbtn
{"changeId":0}

// POST /argus/api/v3/client/r1
{"EnvironmentId":"...","Reports":[...]}

// POST /argus/api/v4/client/getsplashscreen
{"localLabels":"100"}
```

### 9.6 "三方应用异常"警告事件

在开发 Body 捕获功能的过程中，由于错误地 hook 了 `Response.body()` 高频 getter 方法，产生了约 27 万条日志，触发 Android 系统的日志配额限制（`LOGS OVER PROC QUOTA(300)`）。手机弹出 **"三方应用异常：起点读书运行异常，建议前往软件商店更新至最新版本"** 的系统警告。

**根因**：日志洪泛导致 logd 守护进程检测到异常日志量，触发系统级告警。这不是 360加固的检测，而是 Android 系统自身的应用异常监控机制。

**解决**：切换到 hook `RealCall` 方法后，日志量恢复正常（单次启动约 50-80 条），该警告不再出现。

---

## 十、书籍内容解密（javax.crypto.Cipher Hook）

### 10.1 背景与问题

进入书籍阅读页后，OkHttp interceptor 捕获到的 `safegetcontent` 响应为加密的二进制数据（乱码）。相关 API：

| API | 说明 |
|-----|------|
| `/argus/api/v2/bookcontent/safegetcontent?bookId=xxx&chapterId=xxx` | 获取章节内容（加密密文） |
| `/argus/api/v4/bookcontent/getkey?bookId=0&ui=0` | 获取解密密钥 |

**核心思路**：既然 App 端必须在本地解密才能显示给用户，那就 hook `javax.crypto.Cipher` 类捕获解密后的明文。`javax.crypto.Cipher` 是 Android 框架类（boot classpath），与 `Application.onCreate` 同属框架层，Pine hook 安全，不会触发 360加固的延迟代码完整性检测。

### 10.2 实现方案

在 `MainHook.java` 中新增 `installCryptoHooks()` 方法，在 `Application.onCreate` 的回调中（`injectInterceptor()` 之后）调用。

#### Hook 点设计

```
Cipher.init(int opmode, Key key, AlgorithmParameterSpec params)  ← afterCall
Cipher.init(int opmode, Key key)                                  ← afterCall
Cipher.doFinal(byte[])                                            ← afterCall
Cipher.doFinal()                                                  ← afterCall
Cipher.doFinal(byte[], int, int)                                  ← afterCall
```

#### 数据结构

```java
// 每个被追踪的 Cipher 实例的上下文
class CipherContext {
    String algorithm;    // 如 "DESede/CBC/PKCS5Padding"
    byte[] keyBytes;     // 密钥原始字节
    byte[] ivBytes;      // 初始化向量（可能为 null）
    long initTimeMs;     // init 时间，用于过期清理
    boolean stackPrinted; // 是否已打印调用栈（每个 context 只打印一次）
}

// 以 Cipher 对象的 identityHashCode 为 key 追踪实例
ConcurrentHashMap<Integer, CipherContext> trackedCiphers;
```

#### 处理流程

```
Cipher.init() 触发：
  ├→ opmode != DECRYPT_MODE？跳过（只关心解密）
  ├→ isTlsAlgorithm(algorithm)？跳过（GCM/ChaCha20/RSA/OAEP 是 TLS 流量）
  └→ 提取 algorithm、key.getEncoded()、ivParameterSpec.getIV()
     → 存入 trackedCiphers[identityHashCode(cipher)]

Cipher.doFinal() 触发：
  ├→ trackedCiphers 中无此实例？跳过（不是我们追踪的 DECRYPT 操作）
  ├→ 从 trackedCiphers 中移除（一次性消费）
  ├→ output.length < 64 字节？跳过（太短，不是章节内容）
  ├→ 限流检查（每分钟最多 30 条日志）
  ├→ 解码为 UTF-8 字符串
  ├→ 非 JSON 且无中文？跳过
  └→ 输出：算法、Key(hex)、IV(hex)、输入/输出长度、明文内容
     └→ 首次命中时打印调用栈（20 层，用于定位解密类）
```

#### 多层过滤机制（防止日志洪泛）

这是设计中最关键的部分。TLS/HTTPS 也通过 `javax.crypto.Cipher` 进行加解密，每个网络请求都会产生大量 Cipher 操作。如果不做过滤，日志量会爆炸（参见 9.6 节的教训）。

```
Layer 1 - init 阶段过滤：
  ├ 只追踪 DECRYPT_MODE（opmode==2）
  └ 排除 TLS 算法（GCM/CHACHA20/POLY1305/OAEP/RSA）
    → TLS 流量的额外开销：仅一次 HashMap.get()，几乎可忽略

Layer 2 - doFinal 阶段过滤：
  ├ 只处理 trackedCiphers 中的实例
  ├ 输出 >= 64 字节
  ├ JSON 格式（以 { 开头）或含中文字符
  └ 限流 30 条/分钟
```

#### 内存管理

`trackedCiphers` 超过 500 条时，自动清理 60 秒前的旧条目（Cipher 对象 init 后未调用 doFinal 的情况）。

### 10.3 开发过程中遇到的问题与解决

#### 问题 1：OkHttp interceptor 输出加密二进制数据为乱码

**现象**：`safegetcontent` API 的响应是加密数据，OkHttp interceptor 的 `peekBody().source().readUtf8()` 将二进制数据强行解码为 UTF-8，输出大量乱码到 logcat：

```
I QDHook  : ← [200] https://druidv6.if.qidian.com/argus/api/v2/bookcontent/safegetcontent?bookId=1042659444&chapterId=816953054
I QDHook  : �H%��@W�4�.�ĬyT4}��BJl�/ʺrD��s(|d�
             %a�∱0hs�A�q���%���r�.s�'��s�ə,�"�t...
```

**解决**：在 OkHttp interceptor 的响应 Body 输出前增加 `looksLikeBinary()` 检测。检查前 200 个字符中不可打印字符（`< 0x20` 且非换行/回车/制表符）的比例是否超过 20%，超过则跳过输出：

```java
private static boolean looksLikeBinary(String s) {
    int checkLen = Math.min(s.length(), 200);
    int nonPrintable = 0;
    for (int i = 0; i < checkLen; i++) {
        char c = s.charAt(i);
        if (c < 0x20 && c != '\n' && c != '\r' && c != '\t') {
            nonPrintable++;
        }
    }
    return nonPrintable > checkLen / 5;
}
```

**注意**：这并非"过滤掉"解密内容——OkHttp interceptor 层拿到的就是加密密文（App 尚未解密），无论如何都不可读。真正的解密明文由 `QDCrypto` tag 的 Cipher hook 捕获。两个 tag 的职责划分：

| Log Tag | 职责 | 数据状态 |
|---------|------|---------|
| `QDHook` | OkHttp 网络层请求/响应 | URL、Headers、明文 JSON 响应、**加密的章节密文（跳过不输出）** |
| `QDCrypto` | javax.crypto.Cipher 解密结果 | **解密后的章节明文 JSON** |

#### 问题 2：`looksLikeChineseText` 过滤器导致章节正文被漏掉

**现象**：最初设计中，doFinal 输出必须在前 200 字符中含 5 个以上 CJK 汉字（U+4E00-U+9FFF）才会记录。但部分解密结果虽然是有效的章节数据（JSON 格式），前 200 字符主要是 JSON 键名和元数据结构，CJK 字符不足 5 个，导致被过滤掉。

**解决**：放宽判断条件——如果解密输出以 `{` 开头（JSON 格式），直接放行；只对非 JSON 格式才检查中文字符：

```java
// 修改前（过于严格）
if (!looksLikeChineseText(output)) return;

// 修改后
String plaintext = new String(output, StandardCharsets.UTF_8);
if (!plaintext.startsWith("{") && !looksLikeChineseText(output)) return;
```

**原理**：起点读书的加密内容解密后全部是 JSON 格式，以 `{` 开头是可靠的判断条件。而 TLS 解密的内容已经在 init 阶段通过算法过滤被排除了。

#### 问题 3：App 安全检测解密也被捕获

**现象**：除了书籍内容解密外，还捕获到 App 的安全检测解密操作：

```
DECRYPT | algo=AES/CBC/PKCS5PADDING | key=6162634563447965... | iv=616e6372637074786f6e...
PlainText: {"suspiciousDylibs":"libSignatureKiller.so|libAPKFxxxxx.so|libfxdcc.so|libpinesafecheck.so|libyyds.so|libtweakjar.so|libsotweak.so"}
```

这是 App 解密本地配置文件获取"可疑 SO 名称列表"，然后检查设备上是否存在这些文件。这个解密通过 `AES/CBC` 进行（不属于 TLS 算法），因此未被过滤。

**处理**：目前不过滤，作为有价值的安全情报保留。可以看到 App 会检测以下逆向工具的 SO：

| SO 名称 | 对应工具 |
|---------|---------|
| `libSignatureKiller.so` | 签名校验绕过 |
| `libAPKFxxxxx.so` | APK 修改工具 |
| `libfxdcc.so` | 未知检测绕过 |
| `libpinesafecheck.so` | Pine 安全检查（⚠️ 与本项目直接相关） |
| `libyyds.so` | 未知 Hook 框架 |
| `libtweakjar.so` | Jar 修改工具 |
| `libsotweak.so` | SO 修改工具 |

**重要发现**：App 明确检测 `libpinesafecheck.so`，说明 Pine 框架已在加固厂商的检测名单中。但我们的方案中 Pine 的 SO 命名为 `.pine.so`（临时写入后立即删除），不在检测列表内。

### 10.4 解密结果验证

#### 早期观察到的 Cipher 加密方案

> **⚠️ 重要纠正（2026-02-06）**：以下通过 Cipher Hook 捕获的加密信息用于**安全检测/设备指纹等辅助功能**，与章节内容解密**无关**。章节内容的解密不经过 `javax.crypto.Cipher`，而是由 `bll.v` 类内部方法链（L→K→R）完成。详见 `4. 章节阅读功能方案.md`。

| 参数 | 值 | 说明 |
|------|-----|------|
| 算法 | `DESede/CBC/PKCS5Padding` | 3DES-CBC 模式（辅助功能用，非章节解密） |
| IV | `0000000000000000`（8 字节全零） | 固定 IV |
| Key | 每章不同的 24 字节密钥 | hex 编码存储 |
| 密文来源 | `safegetcontent` API 响应 | HTTP 响应 Body 即为密文 |

#### 解密调用栈（定位解密入口）

> **⚠️ 纠正**：下方第一条调用栈（`pf.cihai.search` 相关）用于辅助加密功能，`pf.cihai.search` 在当前版本已不存在。第二条调用栈（`bll.v` 相关）才是章节内容解密的实际路径。

```
[辅助加密 — 非章节内容，且 pf.cihai.search 已不存在于当前版本]
com.qidian.QDReader.qmethod.pandoraex.monitor.c.search   ← 入口（混淆名）
  └→ pf.cihai.search                                      ← 已不存在
       └→ a.b.d → a.b.b                                    ← 底层 Cipher 调用
            └→ javax.crypto.Cipher.doFinal                  ← JCE API

[章节内容解密 — 实际使用的路径]
com.qidian.QDReader.component.bll.v                       ← 章节解密核心类
  └→ v.L(bookId, ChapterItem)                              ← 准备文件 + 触发解密
  └→ v.K(bookId, ChapterItem)                              ← 获取 .qd 文件路径
  └→ v.R(bookId, ChapterItem)                              ← 返回 .cc 缓存路径
```

**关键类**：`com.qidian.QDReader.component.bll.v` 是章节内容解密的**唯一核心类**。`pf.cihai.search` 在当前版本已不存在。

#### 完全免 UI 方案的关键发现（2026-02-06）

以下发现消除了"需要用户手动进入阅读页面"的前置条件，实现了 100% 免 UI 自动化：

1. **bllVInstance 自动捕获**：App 启动时会恢复上次阅读状态（即使用户没有手动操作），此过程自动触发 `bll.v` 类的实例化，hook 自动捕获 `bllVInstance`。即使 App 没有阅读记录（全新安装），也可通过反射创建 `bll.v` 实例（`createBllVInstance()` 方法）

2. **ChapterItem(JSONObject) 构造函数**：`ChapterItem` 类存在一个接收 `JSONObject` 参数的构造函数，可以直接用章节列表 API（`chapterlist`）返回的章节 JSON 数据构造有效的 `ChapterItem` 实例，不再需要从已捕获的真实模板克隆字段。这是通过 `constructChapterItemFromApi()` 方法实现的

3. **章节列表 API 双格式**：章节列表 API 返回的 JSON 有两种结构——`Data.Vs[].Cs[]`（卷-章节结构，如诡秘之主）和 `Data.Chapters[]`（扁平列表，如斗破苍穹），代码中必须同时支持

#### bll.v Hook 的实际覆盖范围（重要）

bll.v 的 Hook **并非仅针对 L/K/R 三个方法**，而是 Hook 了该类所有公共方法（排除 Object 基类方法如 toString/hashCode/equals）。这样设计有两个关键优势：

- **自动适配版本更新**：如果 App 更新后改变了方法名（如 L 改为 M），只要方法仍然返回 `ChapterContentItem` 类型，afterCall hook 仍能自动捕获解密明文
- **bllVInstance 尽早捕获**：App 可能在 L/K/R 之前调用 bll.v 的其他方法（如初始化方法），全方法 Hook 确保 `bllVInstance` 在第一次任何方法调用时就被保存

afterCall hook 的提取逻辑（`MainHook.java:1064-1079`）：检查返回值类名是否包含 `ChapterContentItem`，命中则调用 `extractAndCacheChapterContent()` 提取 `Content` 字段并存入 `capturedChapterContents` 缓存。这个提取是**同步的**——在 hook 回调中立即执行，不需要等待后续方法调用

#### 解密后的数据格式

**书籍详情**（进入书详情页时解密）：

```json
{
  "Content": {
    "Author": "大脑被掏空",
    "BookId": 1041829708,
    "BookName": "野夫提刀录",
    "Description": "日出扶桑一丈高，人间万事细如毛...",
    "FansList": [...],
    "TotalChapterCount": 549,
    "WordsCnt": 2271688,
    "Tags": [{"Name":"志怪"}, {"Name":"古典修仙"}, ...]
  }
}
```

**章节正文**（进入阅读页时解密）：

```json
{
  "AuthorComments": {
    "AuthorComments": "新书大家收藏一下...",
    "AuthorName": "大脑被掏空"
  },
  "Blocks": [],
  "Content": "　　昏黄的火把照亮了夜，一个个村民捏着火把，似乎围着什么东西。\r\n　　喧喧嚷嚷的人群，繁星点点的夜空。\r\n　　还有……疼痛的脑壳。\r\n　　怎么回事，头怎么这么疼？..."
}
```

**内容字段说明**：

| 字段 | 说明 |
|------|------|
| `Content` | 章节正文，`\r\n` 分段，中文全角空格 `　　` 缩进 |
| `AuthorComments` | 作者本章说（部分章节有） |
| `Blocks` | 段落级标记信息（如插图位置） |

### 10.5 日志查看方式

```bash
ADB="/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe -s 3B15BJ00GZL00000"

# 查看解密明文（章节正文、书籍详情）
$ADB logcat -d | grep QDCrypto

# 查看网络请求/响应（URL、JSON API 数据）
$ADB logcat -d | grep QDHook

# 同时查看两者
$ADB logcat -d | grep -E "QDCrypto|QDHook"
```

---

## 十一、已知问题与观察

### 11.1 安全检测 SO 检查

App 通过 AES/CBC 解密本地配置文件获取可疑 SO 名称列表（见 10.3 问题 3），定期扫描设备。当前方案中 Pine SO 命名为 `.pine.so`（点号开头的隐藏文件），不在检测列表内，且使用后立即删除（`unlink`），目前安全。

### 11.2 章节内容获取依赖登录状态

未登录状态下只能阅读免费章节（通常是前几章），VIP 章节需要登录后才能请求。当前测试均在未登录状态下进行，成功解密了免费章节内容。

---

## 十二、后续扩展方向

1. ~~**Hook OkHttp Response Body**~~：✅ 已实现，使用动态代理拦截器 + `peekBody()` + `source().readUtf8()` 读取响应体
2. ~~**解决 360加固延迟检测导致的崩溃**~~：✅ 已解决，从 Pine hook OkHttp 方法迁移到动态代理拦截器方案
3. ~~**分析书籍内容解密逻辑**~~：✅ 已实现。**纠正**：章节解密不经过 `javax.crypto.Cipher`，而是由 `bll.v` 类的 L→K→R 方法链完成。Cipher Hook 捕获的是辅助功能的加密操作
4. ~~**Hook 业务逻辑层**~~：✅ 已实现，通过 `bll.v` 类的 L→K→R 方法链实现章节解密
5. ~~**数据外传**~~：✅ 已实现，通过 LocalSocket RPC 通道 + ADB forward 实时传输数据到 PC
6. ~~**特定 API 深度分析**~~：✅ 已实现搜索、详情、章节列表、章节内容四个核心 API
7. ~~**密钥来源追踪**~~：✅ 已确认密钥获取封装在 `bll.v` 内部，不需要外部调用 `getkey` API，不需要自行派生密钥
8. **Hook 请求签名生成**：定位 `QDSign`/`borgus`/`cecelia` 的生成逻辑（当前通过复用 OkHttpClient 绕过，暂不需要）
9. ~~**段落评论获取**~~：✅ 已实现。通过 `getchapterrepagesummary` + `getparagraphscomments` API，支持自动翻页和批量获取

---

## 十三、OkHttpClient 多客户端陷阱深度分析

### 问题描述

起点读书 App 内部存在**多个 OkHttpClient 实例**，并非所有实例都带有 API 签名拦截器。如果捕获到错误的客户端，所有通过该客户端发出的请求都会因签名缺失而返回 HTTP 402 错误。

### 客户端分类

| 客户端来源 | 用途 | 是否带签名拦截器 | 捕获时序 |
|-----------|------|-----------------|----------|
| Glide 图片加载 | 加载封面图等 CDN 资源 | 否 | App 启动后很早就创建 |
| WebView/广告 SDK | 加载广告和 Web 页面 | 否 | 不确定 |
| 起点业务 API 客户端 | 调用 `druidv6.if.qidian.com/argus` 域名的 API | **是** | App 主界面加载后 |

### 演进过程

1. **初始方案**：Hook `OkHttpClient.Builder.build()`，保存第一个被创建的 OkHttpClient → 结果是 Glide 客户端，HTTP 402
2. **改进方案**：在 `build()` 中检查 Builder 的 interceptors 列表是否包含签名拦截器 → 签名拦截器类名被混淆，无法可靠识别
3. **最终方案**：不从 Builder 捕获，改为从实际发起 API 请求的 `RealCall` 对象中反射获取：
   - Hook `RealCall.enqueue()` 和 `RealCall.execute()`
   - 检查请求 URL 是否包含 `druidv6.if.qidian.com/argus`（起点 API 域名）
   - 如果是，反射遍历 `RealCall` 的所有字段，找到类型包含 `OkHttpClient` 的字段
   - 取出该字段值保存为 `savedOkHttpClient`
   - 用 `apiClientCaptured` 布尔标志防止后续覆盖

### 关键教训

- **不能假设第一个 OkHttpClient 就是 API 客户端**
- **不能通过 Builder 的拦截器列表判断**（混淆后类名不可预测）
- **必须从实际 API 请求的调用链中反向提取客户端**
- 代码位置：`MainHook.java:1498-1522`

---

## 十四、段落评论（段评）API 逆向发现

### 发现过程

段评 API 不在 App 启动时调用，需要用户在阅读页面**点击段落评论气泡**才会触发。气泡是自定义 View 绘制的（非标准 Android 控件），无法通过 uiautomator 定位，只能手动点击。

### API 发现方法

1. 手动点击评论气泡（如显示 `<22` 的气泡）
2. 观察 logcat 中 `QDProbe` tag 的 `NETWORK_CALL` 日志
3. 发现 App 同时调用三个 API：文字评论、配音评论、图片评论

### 发现的评论系统 Activity

- `NewParagraphCommentListActivity`：段评列表页面，包含"全部"和"配音"两个 Tab

### paragraphId 的映射规则

- paragraphId **不是** 从 1 开始的简单递增序号
- 实际值是非连续的整数（如 1, 2, 3, 4, 64, 71, 128, 129, 135...），与 Web 端的 `segmentId` 等价
- `paragraphId = -1` 表示**章评**（对整个章节的评论），不是段评
- 必须先调用 `getchapterrepagesummary` 获取章节中有评论的段落及其 paragraphId
- `RefferContent` 字段包含被评论段落的原文，可用于关联评论与章节正文

### API 端点清单

详见 `docs/5. 段评获取功能方案.md`
