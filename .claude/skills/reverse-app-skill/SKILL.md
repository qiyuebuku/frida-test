# Reverse App Skill — Android 逆向工程通用 Skill

## 概述

本 Skill 用于 Android App 的逆向工程，核心方案是 **SuKiSU Ultra + Zygisk + Pine ART Hook**。
它能对任意 App 进行渐进式探索，找到注入点并实现数据捕获、API 逆向、内容解密等目标。

**核心能力**：
- 自动识别 App 加固类型（360/梆梆/腾讯乐固/阿里/未加固）
- 基于加固类型自动选择 Hook 策略
- 渐进式探索发现注入点（广泛 Hook → 日志分析 → 精准注入）
- 每次逆向后自动积累经验到知识库，实现自我迭代

**为什么选 Zygisk？** 大部分安全加固（360/梆梆/腾讯乐固）的检测目标是 Frida 和 Xposed，
几乎没有 App 会检测 Zygisk 注入——它在 Zygote fork 时注入，比 App 的任何代码都先执行，
且 DEX 完全内存加载、SO 加载后立即删除，不留特征痕迹。
详见 **knowledge/zygisk-guide.md**。

## 环境信息

- **Root 方案**: SuKiSU Ultra + Zygisk
- **Hook 框架**: Pine ART Hook（top.canyie.pine:core:0.3.0）
- **开发环境**: WSL2 (Linux)
- **手机**: 一加 Ace6（3B15BJ00GZL00000）
- **ADB**: `/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe -s 3B15BJ00GZL00000`
- **Android SDK**: `/home/yuyang/android-sdk`
- **NDK**: `/home/yuyang/android-sdk/ndk/27.0.12077973`
- **Gradle**: `/home/yuyang/.gradle-dist/gradle-8.9/bin/gradle`
- **JAVA_HOME**: `/usr/lib/jvm/java-17-openjdk-amd64`
- **逆向项目根目录**: `/home/yuyang/frida-test`
- **已完成案例目录**: qidian/, yanyan/, xhs/, ths/, wechat/

## WSL2 环境与 ADB 连接

我们的 Hook 代码编译和分析都在 WSL2 中完成，但 ADB 是 Windows 侧的 `.exe`。
WSL2 与 Windows 的网络隔离导致端口转发、shell 引号等问题，必须注意以下规则：

1. **ADB 复合命令不可用**：不要用 `adb shell "su -c '...'"` 等单行命令（引号被 WSL shell 提前解释）。先 `adb shell` 进入手机终端，再 `su`，然后在手机 shell 内直接执行
2. **adb forward 在 WSL2 下不可靠**：端口转发可能不生效。替代方案：
   - 使用 `adb_relay.py` 创建 TCP 中继（WSL 本地端口 → 手机端口）
   - 让目标服务监听 `0.0.0.0`，通过手机 WiFi IP 直连
3. **RPC/HTTP 代理端口转发**：Hook 模块内嵌的 HTTP 代理（如 18900 端口）或 LocalSocket RPC，从 WSL2 访问时需要 `adb forward` 或 `adb_relay.py`：
   ```bash
   # 方案 A：adb forward（可能不生效）
   $ADB forward tcp:18900 tcp:18900
   # 方案 B：adb_relay.py（更可靠）
   python3 adb_relay.py  # 配置 LOCAL_PORT=18900, REMOTE_PORT=18900
   ```
4. **Frida USB 模式不可用**：frida-server 需监听 `0.0.0.0:27042`，通过 `-H <手机WiFi IP>:27042` 连接
5. **截屏辅助分析**：`adb exec-out screencap -p > /tmp/screenshot.png`，然后用 Read 工具查看

## 日志驱动的逆向分析

**日志是逆向的眼睛。** 在不了解 App 内部行为时，唯一的信息来源就是 Hook 输出的日志。
日志设计的好坏直接决定逆向效率。

### 日志设计原则

1. **每个 Hook 必须有日志**：哪怕只是一行 `Log.i(TAG, "method called")`，无日志 = 盲人摸象
2. **日志必须包含上下文**：不只是"被调用了"，还要记录参数、返回值、调用栈
3. **使用统一 TAG 前缀**：方便 `adb logcat | grep TAG` 过滤，建议格式 `<APP>Hook`
4. **分级输出**：
   - `Log.i`：正常流程日志（方法调用、参数、返回值）
   - `Log.w`：值得关注的异常（空值、未预期的类型）
   - `Log.e`：Hook 失败、异常捕获
5. **避免日志洪泛**：高频方法必须加速率限制或采样，否则会触发 Android 的 `LOGS_OVER_PROC_QUOTA` 限制

### 关键日志模式

```java
// 模式 1：方法调用 + 参数记录
Pine.hook(method, new MethodHook() {
    public void beforeCall(CallFrame f) {
        Log.i(TAG, "→ ClassName.methodName("
            + "arg0=" + f.args[0]
            + ", arg1=" + f.args[1] + ")");
    }
    public void afterCall(CallFrame f) {
        Log.i(TAG, "← ClassName.methodName → " + f.getResult());
    }
});

// 模式 2：调用栈追踪（定位业务调用方）
Log.i(TAG, "Stack: " + Log.getStackTraceString(new Throwable()));

// 模式 3：二进制数据转 Hex（密钥、加密数据）
private static String bytesToHex(byte[] bytes) {
    StringBuilder sb = new StringBuilder();
    for (byte b : bytes) sb.append(String.format("%02x", b));
    return sb.toString();
}
Log.i(TAG, "Key: " + bytesToHex(keyBytes));

// 模式 4：JSON 格式化（API 响应）
Log.i(TAG, "Response[" + url + "]: " + body.substring(0, Math.min(500, body.length())));

// 模式 5：速率限制（高频方法）
private static final AtomicInteger logCount = new AtomicInteger(0);
private static volatile long logWindowStart = System.currentTimeMillis();

private static boolean shouldLog() {
    long now = System.currentTimeMillis();
    if (now - logWindowStart > 60000) {
        logCount.set(0);
        logWindowStart = now;
    }
    return logCount.incrementAndGet() <= 30;  // 每分钟最多 30 条
}
```

### 日志分析工作流

```bash
# 1. 清空旧日志
$ADB logcat -c

# 2. 启动 App
$ADB shell am start <package>/<activity>

# 3. 实时过滤日志
$ADB logcat -v threadtime | grep "YourTAG"

# 4. 保存日志到文件（用于离线分析）
$ADB logcat -v threadtime | grep "YourTAG" > /tmp/hook.log

# 5. 从日志中提取关键信息
grep "→ " /tmp/hook.log          # 所有方法调用
grep "← " /tmp/hook.log          # 所有返回值
grep "Key:" /tmp/hook.log         # 加密密钥
grep "Response\[" /tmp/hook.log   # API 响应
grep "Stack:" /tmp/hook.log       # 调用栈
```

**根据日志分析 App 行为的典型思路**：
1. 从 URL 日志看 App 调用了哪些 API → 确定目标接口
2. 从 Cipher 日志看使用了什么加密算法 → 确定解密方案
3. 从调用栈看哪个类发起了请求 → 定位业务代码
4. 从参数/返回值看数据结构 → 设计 RPC 接口
5. 从崩溃日志看触发了什么检测 → 调整 Hook 策略

---

## ⚠️ 核心原则：渐进式探索，最小步骤推进

**这是本 Skill 最重要的原则。** 面对一个全新的 App，绝对不能一次性写大量 Hook 代码。
加固检测的触发往往是累积效应——单个 Hook 可能没事，但多个 Hook 同时存在就会被检出。
一旦触发防御，App 可能直接闪退、静默上报、甚至封禁设备，后续排查极其困难。

### 渐进式方法论的 5 条铁律

1. **每次只改一个东西**：新增一个 Hook、修改一个配置、调整一个参数——每次只动一处
2. **改完立刻验证**：编译 → 部署 → 启动 App → 观察日志/截屏 → 确认 App 存活
3. **存活时间是关键指标**：App 启动后必须观察至少 60 秒（360加固的延迟检测在 40-50s）
4. **崩溃就回退**：如果 App 闪退，立刻回退到上一个可用版本，分析崩溃原因后再尝试
5. **先探测边界，再扩展功能**：先搞清楚哪些类/方法可以安全 Hook，再逐步增加功能

### 渐进式探索的心态

```
❌ 错误心态：
   "我要一次性 Hook OkHttp + Cipher + WebView + 自定义类，把所有数据都抓到"
   → 结果：App 启动 30 秒后闪退，不知道是哪个 Hook 触发了检测

✅ 正确心态：
   "先只 Hook Application.onCreate，确认注入成功且 App 正常"
   → App 存活 → "再加一个 OkHttp 拦截器"
   → App 存活 → "再加 Cipher Hook"
   → App 崩溃 → "Cipher Hook 有问题，回退分析"
```

---

## 逆向工作流（7 个 Phase）

### Phase 0: 纯观察（不注入任何代码）

**目标**：了解 App 基本信息，不碰 App 进程。

```bash
# 1. 获取 APK 基本信息
aapt dump badging <app.apk>

# 2. 解压 APK，检查 SO 文件识别加固
unzip -l <app.apk> | grep '\.so$'
```

**加固特征库**（参见 knowledge/protection-signatures.md）：
| SO 文件特征 | 加固厂商 | 难度 |
|------------|---------|------|
| libjiagu.so / libjiagu_vip.so | 360加固 | ⭐⭐⭐⭐⭐ |
| libbangcle_crypto_tool.so | 梆梆加固 | ⭐⭐ |
| libdexvmp.so / libshella-*.so | 腾讯乐固 | ⭐⭐⭐ |
| libsgmain.so / libsgsecuritybody.so | 阿里聚安全 | ⭐⭐⭐ |
| libDexHelper*.so | 爱加密 | ⭐⭐⭐ |
| 无特征 SO | 未加固 | ⭐ |

```bash
# 3. DEX 反编译（jadx）— 只是看代码，不修改任何东西
jadx -d <output_dir> <app.apk> --show-bad-code

# 4. 正常使用 App，观察行为
# 截屏记录 App 界面和流程
adb exec-out screencap -p > /tmp/screenshot.png
```

**Phase 0 产出**：
- 包名、版本、加固类型
- App 主要功能和页面流程
- 初步判断目标数据在哪里（API/本地数据库/WebView）

### Phase 1: 最小注入测试（只验证注入通道）

**目标**：确认 Zygisk 模块能成功加载，App 不会崩溃。
**代码量**：仅 1 行日志输出。

从模板创建项目（参见 templates/ 目录）：

```
<app_name>/
├── app/
│   ├── build.gradle
│   └── src/main/java/com/yuyang/<tag>hook/
│       └── MainHook.java
├── zygisk/
│   ├── jni/
│   │   ├── main.cpp        # 仅匹配包名 + 加载 DEX
│   │   ├── zygisk.hpp
│   │   ├── Android.mk
│   │   └── Application.mk
│   └── magisk/
│       ├── module.prop
│       ├── zygisk/
│       │   └── arm64-v8a.so
│       └── dex/
│           ├── classes.dex
│           └── libpine.so
├── scripts/
└── docs/
```

**第一版 MainHook.java（极简）**：
```java
public static void entry(ClassLoader classLoader, String pineSoPath) {
    Log.i(TAG, "=== Zygisk module loaded ===");  // 仅这一行

    // Pine 安全配置
    System.load(pineSoPath);
    PineConfig.libLoader = () -> {};
    PineConfig.debug = false;
    PineConfig.debuggable = false;
    PineConfig.antiChecks = true;
    PineConfig.disableHiddenApiPolicy = false;      // ⚠️ 最关键
    PineConfig.disableHiddenApiPolicyForPlatformDomain = false;
    Pine.ensureInitialized();
    Log.i(TAG, "Pine initialized, waiting...");

    // 不做任何 Hook！只验证注入和 Pine 初始化
}
```

**验证步骤**：
1. 编译部署 → 启动 App
2. `adb logcat | grep TAG` 看到 "Zygisk module loaded" + "Pine initialized"
3. **等待 60 秒以上**，确认 App 不崩溃
4. 正常使用 App 的各项功能，确认无异常

**Phase 1 验证通过后再继续。如果这一步就崩溃，说明基础配置有问题，必须先排查。**

### Phase 2: Hook Application.onCreate（获取 ClassLoader）

**目标**：仅 Hook 一个框架类方法，获取 App 的 ClassLoader。
**新增代码**：约 10 行。

```java
// 在 entry() 末尾新增
Method onCreate = Application.class.getDeclaredMethod("onCreate");
Pine.hook(onCreate, new MethodHook() {
    public void beforeCall(Pine.CallFrame f) {
        Application app = (Application) f.thisObject;
        ClassLoader cl = app.getClassLoader();
        Log.i(TAG, "Application.onCreate, ClassLoader: " + cl);
        Log.i(TAG, "Package: " + app.getPackageName());
        // 不做其他任何事情
    }
});
```

**验证步骤**：
1. 编译部署 → 启动 App
2. 确认日志输出 ClassLoader 信息
3. **等待 60 秒**，确认 App 存活
4. 操作 App 各页面，确认功能正常

### Phase 3: 探测 OkHttp 是否存在

**目标**：确认 App 使用 OkHttp，尝试注入最简单的拦截器。
**新增代码**：约 20 行。

```java
// 在 onCreate Hook 的 beforeCall 中新增
try {
    Class<?> builderClass = cl.loadClass("okhttp3.OkHttpClient$Builder");
    Log.i(TAG, "OkHttp found: " + builderClass);
    // 此时只探测，不注入拦截器
} catch (ClassNotFoundException e) {
    Log.i(TAG, "OkHttp NOT found, need alternative approach");
}
```

**如果 OkHttp 存在且 App 存活 → 下一步注入空拦截器**：
```java
// 空拦截器 — 只转发请求，不记录任何内容
Object emptyInterceptor = Proxy.newProxyInstance(cl,
    new Class[]{interceptorClass},
    (proxy, method, args) -> {
        if ("intercept".equals(method.getName())) {
            Object chain = args[0];
            Object request = chainClass.getMethod("request").invoke(chain);
            return chainClass.getMethod("proceed", requestClass).invoke(chain, request);
        }
        return method.invoke(proxy, args);
    });
```

**验证**：空拦截器注入后 App 是否存活 60 秒？是 → 拦截器通道安全。

### Phase 4: 逐步开启数据捕获

**目标**：在已验证安全的拦截器中，逐步增加日志记录。
**每次只增加一种数据的捕获**，顺序：

1. **第一轮**：仅记录 URL（一行日志）
   ```java
   Log.i(TAG, "→ " + url.toString());
   ```
   验证 → App 存活？继续。

2. **第二轮**：增加 HTTP Method
   ```java
   Log.i(TAG, "→ " + httpMethod + " " + url);
   ```
   验证 → App 存活？继续。

3. **第三轮**：增加响应码
   ```java
   Log.i(TAG, "← [" + code + "] " + url);
   ```
   验证 → App 存活？继续。

4. **第四轮**：增加 peekBody 响应内容
   ```java
   Object body = peekBody.invoke(response, 1024 * 100L);
   String bodyStr = (String) stringMethod.invoke(body);
   Log.i(TAG, "Body: " + bodyStr.substring(0, Math.min(500, bodyStr.length())));
   ```
   验证 → App 存活？继续。

5. **第五轮**（可选）：增加 Cipher Hook
   - 先只 Hook `Cipher.getInstance` 记录算法名
   - 再 Hook `Cipher.init` 记录密钥
   - 最后 Hook `Cipher.doFinal` 记录加解密数据

### Phase 5: 精准定位与功能实现

**前置条件**：Phase 4 全部通过，已经掌握了：
- App 的 API 端点和数据格式
- 加密算法和密钥（如果有）
- App 的安全边界（哪些 Hook 安全，哪些会触发检测）

根据 Phase 4 的数据分析结果，逐步实现业务功能：
1. 识别目标 API 端点和签名机制
2. 定位加密/解密的关键类和方法
3. 建立 RPC 通道（LocalSocket 或 HTTP 代理端口）
4. 实现特定的业务功能（数据提取、自动化操作等）

**同样遵循最小步骤原则**：每次只新增一个功能点，验证后再继续。

### Phase 6: 崩溃排查流程（当 App 闪退时）

当某一步导致 App 崩溃时，按以下流程排查：

```
1. 立刻回退到上一个可用版本
   → 确认回退后 App 正常

2. 查看崩溃日志
   adb logcat | grep -E "FATAL|signal|crash|exit_self|SIGABRT"

3. 分析崩溃原因
   ┌──────────────────────────────────────────────┐
   │ 崩溃时间点         可能原因                    │
   ├──────────────────────────────────────────────┤
   │ 启动即崩溃         Zygisk/Pine 初始化问题      │
   │ 5-10s 后崩溃       Hook 的方法有问题            │
   │ 40-50s 后崩溃      360加固延迟代码完整性检测    │
   │ 随机崩溃           Hook 高频方法导致性能问题    │
   │ 特定操作后崩溃     Hook 影响了 App 正常逻辑     │
   └──────────────────────────────────────────────┘

4. 根据原因调整策略
   - 代码完整性检测 → 改用动态代理拦截器
   - Hook 高频方法 → 改为 Hook 低频方法或使用拦截器
   - 性能问题 → 添加速率限制和过滤

5. 最小化修改后重新尝试
```

### Phase 7: 经验回写

**每次成功逆向后，必须更新知识库**：
1. 在 `knowledge/` 下新增或更新案例文件
2. 将新发现的加固特征添加到 `protection-signatures.md`
3. 将新的 Hook 策略添加到 `hook-strategies.md`
4. 将踩坑记录添加到 `pitfalls.md`

---

## 核心技术原理

### 为什么选择 Zygisk + Pine 而非 Frida/LSPosed

| 方案 | 360加固 | 梆梆加固 | 腾讯乐固 | 隐蔽性 |
|------|--------|---------|---------|--------|
| Frida (原版) | ❌ | ❌ | ❌ | 低 |
| Frida (HLuda/strongR) | ❌ | ⚠️ | ⚠️ | 中 |
| LSPosed/Xposed | ❌ | ⚠️ | ⚠️ | 低 |
| **Zygisk + Pine** | ✅ | ✅ | ✅ | **高** |

**原因**：
1. Zygisk 在 Zygote fork 时注入，App 无法检测
2. Pine ART Hook 不修改 .text 段代码，不触发代码完整性检测
3. 动态代理拦截器是标准 Java 代码，不被识别为 Hook
4. `disableHiddenApiPolicy=false` 避免修改 ART runtime 内部结构

### 360加固的 6 层检测（已验证）

| 层 | 检测机制 | Zygisk+Pine 是否绕过 |
|----|---------|---------------------|
| 0 | .text 段代码完整性（CRC） | ✅ Pine 不修改 .text 段 |
| 1 | 框架特征（LSPosed bridge SO） | ✅ 不依赖 LSPosed |
| 2 | ART runtime 修改（disableHiddenApiPolicy） | ✅ 设为 false |
| 3 | /proc/self/maps 中 frida 特征 | ✅ 无 frida 进程 |
| 4 | /proc/self/task 中 frida 线程名 | ✅ Pine 无特征线程名 |
| 5 | **延迟 ART 方法入口检查**（~40-50s） | ⚠️ 只能 Hook 框架类 + 低频 App 类 |

**第 5 层是最关键的**：360加固会延迟检查 App 加载类的方法入口是否被修改。因此：
- ✅ 可以 Hook：`Application.onCreate`、`Activity.onCreate`（框架类）
- ✅ 可以 Hook：`OkHttpClient$Builder.build()`（低频，调用 <10 次）
- ❌ 不能 Hook：`Response.body()`、`Request.url()`（高频 getter）
- ❌ 不能 Hook：应用自定义类的高频方法

**解决方案**：用动态代理拦截器（Proxy.newProxyInstance）替代直接 Hook OkHttp 方法。拦截器是标准 Java 代码，不触发检测。

### OkHttp 动态代理拦截器（核心创新）

```java
// 创建标准的 okhttp3.Interceptor 实现
InvocationHandler handler = (proxy, method, args) -> {
    if ("intercept".equals(method.getName())) {
        Chain chain = (Chain) args[0];
        Request request = chain.request();
        // 捕获请求
        logRequest(request);
        Response response = chain.proceed(request);
        // 通过 peekBody 捕获响应（不消费原 Body）
        logResponse(response);
        return response;
    }
    return method.invoke(proxy, args);
};

Object interceptor = Proxy.newProxyInstance(
    classLoader, new Class[]{interceptorClass}, handler);

// 通过 Hook Builder.build() 在构建时注入
Pine.hook(builderClass, "build", new MethodHook() {
    public void beforeCall(CallFrame f) {
        addInterceptor.invoke(f.thisObject, interceptor);
    }
});
```

### OkHttpClient 捕获的关键陷阱

App 中通常存在多个 OkHttpClient 实例（图片加载、广告、业务 API 等）。
**必须从业务 API 请求中精确提取 OkHttpClient**：

```java
// ❌ 错误：捕获第一个 OkHttpClient（可能是 Glide 图片加载）
// ✅ 正确：从特定域名的 RealCall 中提取
if (url.contains("target.api.domain") && !apiClientCaptured) {
    savedOkHttpClient = reflectGetClient(realCall);
    apiClientCaptured = true;  // 一次性标志
}
```

---

## 编译部署流程

### 编译 Java Hook 模块

```bash
cd /home/yuyang/frida-test/<app_name>

JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
ANDROID_HOME=/home/yuyang/android-sdk \
/home/yuyang/.gradle-dist/gradle-8.9/bin/gradle :app:assembleDebug

# 提取 DEX
cd zygisk/extracted
unzip -o ../../app/build/outputs/apk/debug/app-debug.apk 'classes*.dex'
```

### 编译 Zygisk C++ 模块

```bash
NDK=/home/yuyang/android-sdk/ndk/27.0.12077973
$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android26-clang++ \
    -shared -fPIC -std=c++17 -O2 -s \
    -o zygisk/magisk/zygisk/arm64-v8a.so zygisk/jni/main.cpp -llog -ldl
```

### 部署到手机

```bash
ADB="/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe -s 3B15BJ00GZL00000"

# 推送 DEX 和 SO 到 Magisk 模块目录（需要 root）
# 先 adb shell，再 su，再执行 cp 命令
$ADB shell
su
cp /data/local/tmp/<module>/dex/* /data/adb/modules/<module_id>/dex/
cp /data/local/tmp/<module>/zygisk/* /data/adb/modules/<module_id>/zygisk/

# 重启 App
am force-stop <package_name>
am start <package_name>

# 查看日志
$ADB logcat -v threadtime | grep <TAG>
```

---

## 知识库结构

知识库是本 Skill 自我迭代的核心。每次逆向后都应更新。

```
knowledge/
├── anti-detection.md         # 反检测通用知识（8大检测机制+绕过方案）
├── protection-signatures.md  # 加固特征识别库
├── hook-strategies.md        # Hook 策略模式库
├── pitfalls.md               # 踩坑记录与避坑指南
├── decryption-patterns.md    # 加密/解密模式库
├── case-qidian.md            # 案例：起点读书（360加固，最复杂）
├── case-yanyan.md            # 案例：盐言故事（梆梆加固）
├── case-xhs.md               # 案例：小红书（腾讯乐固）
├── case-ths.md               # 案例：同花顺（360加固+基金交易）
└── case-wechat.md            # 案例：微信（WCDB数据库解密）
```

## 自我迭代规则

当完成一次新的逆向工程后，执行以下步骤：

1. **更新加固特征库**：如果遇到新的加固类型或新版本特征
2. **更新 Hook 策略**：如果发现了新的注入点或 Hook 模式
3. **记录踩坑**：任何花费超过 10 分钟排查的问题
4. **新增案例**：创建 `case-<app_name>.md`，记录完整的逆向过程
5. **更新反检测知识**：如果遇到新的检测机制或绕过方法

格式要求：每个知识条目必须包含：
- **现象**：遇到了什么问题/发现
- **原因**：根因分析
- **方案**：如何解决
- **适用范围**：在什么场景下适用
