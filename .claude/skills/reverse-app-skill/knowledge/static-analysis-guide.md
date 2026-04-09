# DEX 静态分析指南

## 定位：Hook 之前先看代码

动态 Hook 是"运行时观察"，静态分析是"提前摸底"。两者配合：
- **静态分析告诉你 Hook 什么**：哪些类/方法值得 Hook、加密逻辑在哪里、API 端点在哪里
- **动态 Hook 告诉你实际运行情况**：参数值、调用频率、执行时序

不做静态分析直接 Hook = 盲人摸象，不知道该 Hook 什么方法、不理解返回值含义。

## 工具链

### jadx — 主力反编译工具

```bash
# 基本反编译（推荐加 --show-bad-code 显示反编译失败的方法）
jadx -d <output_dir> <app.apk> --show-bad-code

# 常用参数
jadx -d output app.apk \
  --show-bad-code \          # 即使反编译失败也显示（方便定位 VMP/混淆代码）
  --threads-count 4 \        # 多线程加速
  --deobf                    # 自动重命名混淆类（a.b.c → 可读名）
```

**jadx-gui**：图形界面版本，支持交互式搜索、交叉引用、反编译。适合探索阶段。
**jadx CLI**：命令行版本，输出到目录后可以用 grep 批量搜索。适合自动化分析。

### apktool — 资源解包 + smali

```bash
# 解包 APK（获取 AndroidManifest.xml、资源文件、smali）
apktool d app.apk -o output_apktool

# 优势：能看到完整的 AndroidManifest.xml（jadx 有时解析不完整）
# 输出 smali 而非 Java，适合精确分析混淆代码
```

### 基本 APK 信息

```bash
# 包名、版本、权限、入口 Activity
aapt dump badging app.apk

# 列出 APK 内所有文件（识别加固 SO）
unzip -l app.apk | grep -E '\.so$|\.dex$'
```

## 分析流程

### 第一步：AndroidManifest.xml 分析

这是逆向的起点，告诉你 App 的骨架。

**关键信息**：

```bash
# 从 apktool 解包结果中查看
cat output_apktool/AndroidManifest.xml
```

| 关注点 | 找什么 | 为什么重要 |
|--------|--------|-----------|
| Application 类 | `android:name="..."` | 自定义 Application 是初始化入口，加固壳通常替换这里 |
| 主 Activity | `MAIN` + `LAUNCHER` intent-filter | App 启动入口 |
| Service/Receiver | 后台服务、广播接收器 | 可能包含数据同步、推送处理逻辑 |
| 权限 | `uses-permission` | 提示 App 功能范围（网络、存储、相机等） |
| ContentProvider | `android:authorities` | 数据库/数据共享接口 |
| meta-data | 各种配置键值对 | 第三方 SDK 配置（推送、统计、广告） |

**加固识别**：如果 `android:name` 指向加固壳的 Application 类（如 `com.qihoo.util.StubApplication`），说明真实 Application 被壳包裹，jadx 看到的代码可能是壳代码而非业务代码。

### 第二步：字符串搜索定位关键类

jadx 反编译后最高效的分析方式就是 **grep 搜索关键字符串**。

```bash
# 在 jadx 输出目录中搜索
cd <jadx_output_dir>

# 1. 搜 API 域名/路径 → 定位网络请求代码
grep -rn "api.example.com" --include="*.java"
grep -rn "/api/v" --include="*.java"
grep -rn "https://" --include="*.java" | head -30

# 2. 搜加密相关关键词 → 定位加密逻辑
grep -rn "AES\|DES\|RSA\|Cipher\|SecretKey\|encrypt\|decrypt" --include="*.java"
grep -rn "MD5\|SHA\|HMAC\|digest" --include="*.java"
grep -rn "Base64\|encode\|decode" --include="*.java"

# 3. 搜网络框架关键词 → 确认网络库
grep -rn "OkHttpClient\|Retrofit\|Interceptor\|HttpUrl" --include="*.java"
grep -rn "Volley\|HttpURLConnection\|WebView" --include="*.java"

# 4. 搜签名/Token 关键词 → 定位认证机制
grep -rn "token\|sign\|signature\|auth\|session" --include="*.java" -i
grep -rn "Cookie\|Set-Cookie\|Bearer" --include="*.java"

# 5. 搜本地存储关键词 → 定位敏感数据存储
grep -rn "SharedPreferences\|getSharedPreferences" --include="*.java"
grep -rn "SQLiteDatabase\|openOrCreateDatabase\|Room" --include="*.java"

# 6. 搜 WebView/JSBridge → 定位 Hybrid 通信
grep -rn "addJavascriptInterface\|@JavascriptInterface\|evaluateJavascript" --include="*.java"
grep -rn "JsBridge\|WebViewJavascriptBridge\|callHandler" --include="*.java"

# 7. 搜错误信息/日志 TAG → 反向定位业务代码
grep -rn "\"error\"\|\"success\"\|\"failed\"" --include="*.java"
grep -rn "Log\.\|TAG\s*=" --include="*.java" | head -20
```

### 第三步：理解代码结构与调用链

找到关键类后，需要理解调用关系。

**从上到下追踪（正向）**：
```
入口 Activity → onClick 事件 → 调用业务方法 → 网络请求 → 解析响应
```

**从下到上追踪（反向，更常用）**：
```
搜到加密方法 → 谁调用了它（jadx 交叉引用 / grep） → 调用方是什么业务 → 理解加密上下文
```

**jadx-gui 交叉引用**：右键方法 → "Find Usage" 查看所有调用者。这是最高效的追踪方式。

**命令行追踪**：
```bash
# 找到某个类/方法的所有调用者
grep -rn "ClassName\.methodName\|new ClassName" --include="*.java"

# 找到某个接口的所有实现
grep -rn "implements InterfaceName" --include="*.java"

# 找到某个类的子类
grep -rn "extends ClassName" --include="*.java"
```

### 第四步：混淆代码分析

大部分 App 使用 ProGuard/R8 混淆，类名和方法名变成 a、b、c 等单字母。

**应对策略**：

1. **字符串不会被混淆**：混淆改的是标识符，不改字符串常量。所以 grep 字符串仍然有效
2. **框架类不混淆**：`android.*`、`okhttp3.*`、`retrofit2.*` 等保持原名
3. **jadx --deobf**：自动为混淆类生成可读名（基于使用上下文）
4. **看方法签名而非名字**：混淆后 `void a(String, int)` 的参数类型仍然可读
5. **看常量和字符串**：方法内的字符串常量暴露真实用途

```java
// 混淆前
public class CryptoHelper {
    public String encrypt(String data, String key) { ... }
}

// 混淆后
public class a {
    public String a(String str, String str2) {
        // 但内部字符串不变：
        Cipher cipher = Cipher.getInstance("AES/CBC/PKCS5Padding");
        // → 一看就知道这是 AES 加密
    }
}
```

### 第五步：识别第三方 SDK 和框架

App 中大量代码来自第三方库，识别它们可以快速过滤噪音：

| 包名模式 | 库 | 关注度 |
|----------|-----|--------|
| `okhttp3.*` | OkHttp | ⭐⭐⭐ 网络层，Hook 重点 |
| `retrofit2.*` | Retrofit | ⭐⭐⭐ API 定义层 |
| `com.google.gson.*` | Gson | ⭐⭐ JSON 解析 |
| `com.bumptech.glide.*` | Glide | ⭐ 图片加载，可忽略 |
| `com.squareup.picasso.*` | Picasso | ⭐ 图片加载，可忽略 |
| `com.tencent.mm.opensdk.*` | 微信 SDK | ⭐ 分享/登录 |
| `com.alipay.sdk.*` | 支付宝 SDK | ⭐ 支付 |
| `cn.jpush.*` / `com.xiaomi.push.*` | 推送 SDK | 无关 |
| `com.umeng.*` / `com.sensorsdata.*` | 统计 SDK | 无关 |

**快速定位业务代码**：排除第三方包名后，剩下的就是 App 自身业务代码。

```bash
# 列出所有顶级包名
ls <jadx_output>/sources/

# 排除常见第三方库，看业务包
ls <jadx_output>/sources/com/<app_company>/
```

## 静态分析如何指导 Hook 策略

### 场景 1：找到 API 端点后

```
静态分析发现：
  - Retrofit 接口定义 `@GET("/api/v2/user/info")`
  - 方法签名 `Observable<UserInfo> getUserInfo(@Query("uid") String uid)`

→ Hook 策略：
  - Hook OkHttp 拦截器，过滤 `/api/v2/user/info` 路径
  - 或直接 Hook 这个 Retrofit 方法的返回值
```

### 场景 2：发现加密逻辑后

```
静态分析发现：
  - 类 `com.app.security.RequestSigner` 中有 `sign(Map params)` 方法
  - 内部使用 HMAC-SHA256 + 时间戳 + nonce
  - 密钥从 SharedPreferences 读取

→ Hook 策略：
  - Hook `RequestSigner.sign()` 的 afterCall，直接拿签名结果
  - 或 Hook SharedPreferences 获取密钥，自己实现签名算法
```

### 场景 3：发现 WebView/JSBridge 后

```
静态分析发现：
  - `addJavascriptInterface(bridge, "NativeBridge")`
  - bridge 类有 `@JavascriptInterface void callNative(String json)`
  - JSON 格式 {"cmd": "xxx", "data": {...}}

→ Hook 策略：
  - Hook `callNative` 方法，记录所有 JS → Native 通信
  - 分析 cmd 枚举值，定位感兴趣的功能
```

## 加固 App 的静态分析特殊处理

加固 App 的 DEX 是被壳加密的，jadx 直接反编译看到的是壳代码而非业务代码。

### 获取脱壳后的 DEX

**方法 1：通过 Hook dump 内存中的 DEX**
```java
// Hook ClassLoader.loadClass，在 App 运行时 dump DEX
// 360 加固在运行时解密 DEX 到内存，此时可以 dump
```

**方法 2：使用现成脱壳工具**
- **FART**（ART 环境下的脱壳机）
- **DexDump**（Frida 脚本 dump 内存 DEX）
- **BlackDex**（免 Root 脱壳工具，但对高版本加固可能失效）

**方法 3：先动态 Hook 确定关键类名，再针对性分析**
```
Phase 1-4 的日志输出 → 获得类名和方法名
→ 从脱壳 DEX 中找到这些类的完整代码
→ 理解内部逻辑 → 优化 Hook 策略
```

### 加固 App 的分析顺序

对于加固 App，静态分析和动态 Hook 交替进行：

```
1. 初步静态分析（壳代码 + AndroidManifest）
   → 识别加固类型、入口 Activity

2. Phase 1-3 动态 Hook（注入 + 基础探测）
   → 获得 ClassLoader、确认 OkHttp 等框架

3. 运行时 dump 脱壳 DEX
   → 用 jadx 反编译脱壳后的 DEX

4. 深度静态分析（脱壳后代码）
   → 定位加密类、签名逻辑、业务方法

5. Phase 4-5 精准 Hook
   → 基于静态分析结果，精确 Hook 目标方法
```

## 实战 Checklist

在完成静态分析后，你应该能回答以下问题：

- [ ] App 使用什么网络库？（OkHttp / Retrofit / Volley / 自定义）
- [ ] App 有哪些关键 API 域名和端点？
- [ ] App 使用什么加密算法？密钥从哪来？
- [ ] App 是否有 WebView/JSBridge 通信？
- [ ] App 使用什么本地存储？（SharedPreferences / SQLite / 加密数据库）
- [ ] App 的认证机制是什么？（Token / Cookie / 自定义签名）
- [ ] 哪些类/方法是 Hook 的候选目标？
- [ ] App 使用了哪些第三方 SDK？哪些可以忽略？
