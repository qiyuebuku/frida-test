# 自动化 App 逆向 Skill 设计文档

## 一、目标与定位

### 1.1 核心目标

打造一个 **Claude Code Skill**，帮助用户**自动化逆向 Android App**，采集数据并分析加密方案。

类似于 `scrape-docs` skill（自动采集任意文档网站），这个 `reverse-app` skill 也是**通用方案**——不针对特定 App，而是提供一套标准化的逆向工程流程，让 AI 负责关键决策，程序负责重复性操作。

### 1.2 设计哲学

```
scrape-docs:  浏览器 + AI分析 → 采集任意文档网站
reverse-app:  Zygisk/Pine + AI分析 → 逆向任意 Android App
```

**分工模式**：
- **程序（工具层）**：封装重复、常规的操作（编译部署、截图、日志采集、DEX 反编译等）
- **AI（决策层）**：分析 App 架构、识别加固类型、选择 Hook 策略、定位关键类、设计解密方案

### 1.3 已验证的经验基础

| App | 加固方案 | 内容加密 | 突破方案 | 文档 |
|-----|---------|---------|---------|------|
| 起点读书 | 360加固 | 3DES-CBC | 动态代理 OkHttp Interceptor + Cipher Hook | `qidian/docs/QIDIAN_REVERSE.md` |
| 盐言故事 | 梆梆加固 | Native AES | 主动调用 JNI getText() | `yanyan/docs/YANYAN_REVERSE.md` |
| 小红书 | 腾讯乐固 VMP | AES-256-CTR | 动态代理 OkHttp Interceptor + Cipher Hook | `xhs/docs/README.md` |

三个项目共同验证了 **Zygisk + Pine** 方案的通用性：
- 360加固（最强）、梆梆加固、腾讯乐固 VMP 都无法检测
- Pine ART Hook 框架类安全
- 动态代理拦截器绕过代码完整性检测
- App 自带 Native Hook 框架（ShadowHook/ByteHook）不影响 Zygisk 注入

---

## 二、可行性分析

### 2.1 哪些可以自动化？

| 阶段 | 操作 | 自动化可行性 | 实现方式 |
|------|------|-------------|---------|
| **环境准备** | 检测设备 Root/Zygisk 状态 | ✅ 高 | `adb shell` 命令 |
| **环境准备** | 安装/更新 Zygisk 模块 | ✅ 高 | 模板化 Magisk 模块 + adb push |
| **信息收集** | 提取 APK 基本信息 | ✅ 高 | `aapt dump badging` |
| **信息收集** | 识别加固类型 | ✅ 高 | SO 文件特征匹配（360/梆梆/腾讯等） |
| **信息收集** | DEX 反编译 | ✅ 高 | jadx CLI |
| **信息收集** | 搜索关键类/方法 | ✅ 高 | grep + 正则 |
| **Hook 开发** | 生成基础 Hook 代码模板 | ✅ 高 | 代码模板 + 参数替换 |
| **Hook 开发** | 编译部署 DEX | ✅ 高 | gradle + adb |
| **测试验证** | 启动 App + 采集日志 | ✅ 高 | adb logcat |
| **测试验证** | 截图观察 App 状态 | ✅ 高 | adb screencap |
| **测试验证** | UI 自动化操作 | ⚠️ 中 | uiautomator + adb input |
| **数据采集** | 导出 Hook 捕获的数据 | ✅ 高 | adb pull |
| **分析决策** | 分析 API 结构 | 🤖 AI | Claude 分析 JSON |
| **分析决策** | 识别加密算法 | 🤖 AI | Claude 分析 Cipher 日志 |
| **分析决策** | 定位关键解密类 | 🤖 AI | Claude 分析反编译代码 + 调用栈 |
| **分析决策** | 设计 Hook 策略 | 🤖 AI | Claude 基于经验决策 |
| **分析决策** | 处理检测对抗 | 🤖 AI | Claude 分析崩溃日志 + 调整策略 |

### 2.2 为什么这个 Skill 可行？

1. **固定的技术栈**：Zygisk + Pine 已验证对多种加固有效
2. **标准化的流程**：逆向工程有明确的阶段和步骤
3. **AI 擅长的任务**：代码分析、模式识别、策略决策
4. **丰富的经验库**：两个成功案例提供了对抗经验

### 2.3 Skill 的边界

**能做的**：
- 自动化环境配置和部署
- 快速识别 App 技术栈和加固方案
- 生成并迭代 Hook 代码
- 采集和分析数据
- 提供逆向策略建议

**不能做的**（需要人工介入）：
- 复杂的登录流程（需要手机操作）
- 未知加固的深度对抗（需要专家分析）
- 法律合规判断（需要用户自行确认）

---

## 三、Skill 架构设计

### 3.1 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                     reverse-app Skill                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │   工具层     │    │   模板层     │    │   知识层     │       │
│  │  (Python)    │    │  (Templates) │    │  (Markdown)  │       │
│  ├──────────────┤    ├──────────────┤    ├──────────────┤       │
│  │ • adb_helper │    │ • main.cpp   │    │ • 加固特征库 │       │
│  │ • jadx_helper│    │ • MainHook   │    │ • Hook 策略库│       │
│  │ • gradle_cli │    │ • build.gradle│   │ • 解密方案库 │       │
│  │ • log_parser │    │ • module.prop│    │ • 对抗经验库 │       │
│  └──────────────┘    └──────────────┘    └──────────────┘       │
│                                                                  │
├─────────────────────────────────────────────────────────────────┤
│                         SKILL.md                                 │
│         (Claude Code 阅读的指令文档，定义流程和决策点)             │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 工具层（Python 脚本）

类似 `scrape-docs` 的 `browser_helper.py`，提供 HTTP API 封装常用操作：

```python
# reverse_helper.py - 核心工具脚本

class ReverseHelper:
    """Android App 逆向工具集"""

    # === 设备管理 ===
    def check_device() -> dict:
        """检查设备状态：Root、Zygisk、ADB 连接"""

    def get_device_info() -> dict:
        """获取设备信息：型号、Android 版本、CPU 架构"""

    # === APK 分析 ===
    def analyze_apk(apk_path: str) -> dict:
        """分析 APK：包名、版本、权限、组件、SO 库"""

    def detect_protection(apk_path: str) -> dict:
        """检测加固类型：360/梆梆/腾讯/阿里/未加固"""

    def decompile_dex(apk_path: str, output_dir: str) -> dict:
        """反编译 DEX 到 Java 源码（jadx）"""

    # === Hook 模块管理 ===
    def create_project(pkg_name: str, project_dir: str) -> dict:
        """从模板创建新的 Hook 项目"""

    def build_project(project_dir: str) -> dict:
        """编译 Hook 项目（gradle assembleDebug）"""

    def deploy_dex(project_dir: str, module_id: str) -> dict:
        """部署 DEX 到手机"""

    def restart_app(pkg_name: str) -> dict:
        """重启目标 App"""

    # === 日志与数据 ===
    def collect_logs(tag: str, duration: int) -> dict:
        """采集指定 TAG 的 logcat 日志"""

    def pull_dumps(remote_dir: str, local_dir: str) -> dict:
        """拉取 Hook 导出的数据文件"""

    def parse_hook_logs(log_text: str) -> dict:
        """解析 Hook 日志，提取结构化数据"""

    # === UI 自动化 ===
    def screenshot(output_path: str) -> dict:
        """截图"""

    def tap(x: int, y: int) -> dict:
        """点击屏幕坐标"""

    def swipe(x1: int, y1: int, x2: int, y2: int, duration: int) -> dict:
        """滑动"""

    def dump_ui() -> dict:
        """导出 UI 层级结构"""

    def get_current_activity() -> dict:
        """获取当前前台 Activity"""
```

### 3.3 模板层（代码模板）

预置的代码模板，根据参数生成实际代码：

```
templates/
├── zygisk/
│   ├── main.cpp.template          # Zygisk 模块 C++ 模板
│   ├── module.prop.template       # Magisk 模块元数据
│   └── Android.mk.template
├── hook/
│   ├── MainHook.java.template     # Hook 入口模板
│   ├── OkHttpHook.java.template   # OkHttp 拦截器模板
│   ├── CipherHook.java.template   # Cipher Hook 模板
│   ├── JniHook.java.template      # JNI Hook 模板
│   └── build.gradle.template
└── scripts/
    ├── deploy.sh.template         # 部署脚本
    └── collect.sh.template        # 数据采集脚本
```

**模板变量示例**：

```java
// MainHook.java.template
package {{PACKAGE_NAME}};

public class MainHook {
    private static final String TAG = "{{LOG_TAG}}";
    private static final String TARGET_PKG = "{{TARGET_PKG}}";

    public static void entry(ClassLoader classLoader, String pineSoPath) {
        // ... 通用初始化代码 ...

        {{#if OKHTTP_HOOK}}
        // OkHttp 拦截器注入
        installOkHttpInterceptor(classLoader);
        {{/if}}

        {{#if CIPHER_HOOK}}
        // Cipher Hook
        installCipherHooks();
        {{/if}}

        {{#if JNI_HOOKS}}
        // JNI Hook
        {{#each JNI_HOOKS}}
        installJniHook(classLoader, "{{CLASS}}", "{{METHOD}}");
        {{/each}}
        {{/if}}
    }
}
```

### 3.4 知识层（Markdown 文档）

经验库，供 AI 决策时参考：

```
knowledge/
├── protections/
│   ├── 360_jiagu.md               # 360加固检测机制与绕过方案
│   ├── bangcle.md                 # 梆梆加固
│   ├── tencent_legu.md            # 腾讯乐固
│   └── alibaba.md                 # 阿里聚安全
├── strategies/
│   ├── okhttp_intercept.md        # OkHttp 拦截策略
│   ├── cipher_hook.md             # Cipher Hook 策略
│   ├── jni_hook.md                # JNI Hook 策略
│   └── native_text_extract.md     # Native 文本提取策略（盐言方案）
├── patterns/
│   ├── rsa_key_exchange.md        # RSA 密钥交换模式
│   ├── aes_content_encrypt.md     # AES 内容加密模式
│   └── 3des_content_encrypt.md    # 3DES 内容加密模式
└── troubleshooting/
    ├── delayed_crash.md           # 延迟崩溃排查
    ├── log_flooding.md            # 日志洪泛处理
    └── classloader_issues.md      # ClassLoader 问题
```

---

## 四、SKILL.md 设计（核心）

SKILL.md 是 Claude Code 阅读的指令文档，定义整个逆向流程：

```markdown
---
name: reverse-app
description: 自动化逆向 Android App，采集数据并分析加密方案。
---

# Android App 逆向 Skill

## 使用场景

当用户想要：
- 分析某个 Android App 的 API 接口
- 获取 App 中的加密内容（小说、视频等）
- 理解 App 的加密/签名机制
- 绕过 App 的安全检测

## 前置要求

1. 已 Root 的 Android 设备（KernelSU/Magisk）
2. 已安装 ZygiskNext
3. ADB 连接正常
4. 目标 App 已安装

## 标准流程

### 阶段一：环境检查

```bash
# 1. 检查设备状态
python .claude/skills/reverse-app/reverse_helper.py check_device

# 2. 获取目标 App 信息
python .claude/skills/reverse-app/reverse_helper.py analyze_apk <apk_path>

# 3. 检测加固类型
python .claude/skills/reverse-app/reverse_helper.py detect_protection <apk_path>
```

根据检测结果，参考 `knowledge/protections/` 中的对应文档选择策略。

### 阶段二：创建 Hook 项目

```bash
# 从模板创建项目
python .claude/skills/reverse-app/reverse_helper.py create_project \
  --pkg <target_package> \
  --name <project_name> \
  --output <project_dir>
```

### 阶段三：渐进式测试（关键）

**必须遵循渐进式测试原则**：每次只增加一个变量，观察 App 是否正常运行 60+ 秒。

| 测试步骤 | 操作 | 预期结果 |
|---------|------|---------|
| 1 | 空注入（只打日志） | App 正常 |
| 2 | 加载 Pine SO | App 正常 |
| 3 | Pine 初始化 | App 正常 |
| 4 | Hook Application.onCreate | App 正常，回调触发 |
| 5 | 注入 OkHttp Interceptor | 捕获到 API 请求 |
| 6 | 安装 Cipher Hook | 捕获到加密操作 |

每步之后执行：
```bash
# 编译部署
python .claude/skills/reverse-app/reverse_helper.py build_and_deploy <project_dir>

# 重启 App
python .claude/skills/reverse-app/reverse_helper.py restart_app <pkg>

# 等待 60 秒，采集日志
python .claude/skills/reverse-app/reverse_helper.py collect_logs --tag <LOG_TAG> --duration 60
```

如果某步失败（App 崩溃），回退到上一步，参考 `knowledge/troubleshooting/` 分析原因。

### 阶段四：数据分析

采集到数据后，AI 负责分析：

1. **API 分析**：识别关键端点、请求/响应格式
2. **加密分析**：识别算法、密钥来源、IV 模式
3. **内容定位**：找到目标数据（章节内容等）在哪个 API/类中

### 阶段五：深度 Hook（按需）

根据分析结果，可能需要：

- **JNI Hook**：如果解密在 Native 层（如盐言故事）
- **特定类 Hook**：针对业务逻辑类
- **主动调用**：在 Hook 回调中调用其他方法获取数据

参考 `knowledge/strategies/` 中的策略文档。

### 阶段六：自动化采集

数据提取方案验证后，可以：
1. 添加去重逻辑
2. 关联元数据（书名、章节名等）
3. 自动化翻页/导航
4. 批量导出数据

## 决策点（AI 判断）

以下情况需要 AI 分析决策：

1. **加固类型识别**：根据 SO 特征判断
2. **检测触发分析**：根据崩溃日志/行为判断是哪种检测
3. **Hook 策略选择**：根据目标数据位置选择 Hook 点
4. **加密方案分析**：根据 Cipher 日志识别算法和密钥来源
5. **对抗策略调整**：根据失败原因调整方案

## 输出规范

逆向过程中，将关键信息记录到 `<project_dir>/docs/REVERSE.md`：
- App 技术分析
- 加固检测结果
- 测试结果记录
- API 端点列表
- 加密方案分析
- Hook 策略说明

## 用户请求

$ARGUMENTS
```

---

## 五、与 scrape-docs 的对比

| 维度 | scrape-docs | reverse-app |
|------|-------------|-------------|
| **目标** | 任意文档网站 | 任意 Android App |
| **核心工具** | Camoufox 浏览器 | Zygisk + Pine |
| **数据获取** | HTTP 请求 | Hook 拦截 |
| **AI 决策点** | 选择器识别、登录检测 | 加固识别、Hook 策略、加密分析 |
| **知识库** | 站点选择器、展开按钮 | 加固特征、对抗策略、解密模式 |
| **自动化程度** | 高（浏览器全自动） | 中高（需要渐进测试） |
| **人工介入** | 登录操作 | 测试验证、策略调整 |

---

## 六、实现路线图

### Phase 1：基础框架（MVP）

1. **reverse_helper.py**：实现核心工具函数
   - 设备检查
   - APK 分析
   - 加固检测
   - 编译部署
   - 日志采集

2. **代码模板**：
   - Zygisk 模块模板
   - MainHook 基础模板
   - OkHttp Interceptor 模板

3. **SKILL.md**：
   - 基本流程定义
   - 渐进测试规范

4. **知识库**：
   - 360加固对抗经验
   - 梆梆加固对抗经验
   - OkHttp 拦截策略

### Phase 2：增强能力

1. **更多模板**：
   - Cipher Hook 模板
   - JNI Hook 模板
   - 主动调用模板

2. **更多知识**：
   - 更多加固类型
   - 更多加密模式
   - 更多对抗策略

3. **自动化增强**：
   - UI 自动化操作
   - 批量数据采集
   - 数据导出格式化

### Phase 3：智能化

1. **模式识别**：
   - 自动识别常见 App 架构（RN、Flutter 等）
   - 自动推荐 Hook 策略

2. **对抗进化**：
   - 根据失败自动调整策略
   - 学习新的检测绕过方案

3. **数据处理**：
   - 自动关联章节/书籍元数据
   - 结构化数据导出

---

## 七、风险与限制

### 7.1 技术限制

- **未知加固**：新型加固可能需要研究新的对抗方案
- **Native 层**：复杂的 Native 加密需要 IDA/Frida 分析
- **服务端验证**：某些数据需要有效登录态

### 7.2 合规风险

- 逆向工程可能违反 App 服务条款
- 采集的数据可能涉及版权
- **Skill 应提醒用户自行判断合规性**

### 7.3 维护成本

- 加固方案持续更新，需要跟进
- 不同 Android 版本可能有兼容性问题
- Pine 框架本身需要更新

---

## 八、日志方法论（核心经验）

这是逆向工程中最关键的部分之一。好的日志设计能让 AI 快速理解 App 行为、定位问题、验证方案。

### 8.1 日志设计原则

```
原则 1：分层 TAG
  - 主 TAG 用于过滤（如 YYHook、QDHook）
  - 子分类用前缀区分（CRYPTO_、HTTP_、JNI_、UI_）

原则 2：结构化输出
  - 固定格式便于 grep/解析
  - 关键字段用特定分隔符
  - 长内容截断 + 完整内容 dump 到文件

原则 3：防洪泛
  - 高频方法必须限流
  - 去重机制（URL/参数相同的请求）
  - 跳过无关数据（TLS 流量、静态资源）

原则 4：可追溯
  - 关键操作打印调用栈（首次触发时）
  - 记录时间戳便于关联
  - 阶段性标记（INIT、HOOK、CAPTURE、ERROR）
```

### 8.2 标准日志模板

#### 初始化阶段日志

```java
// 模板：初始化流程日志
Log.i(TAG, "========== INIT START ==========");
Log.i(TAG, "Target package: " + packageName);
Log.i(TAG, "Pine SO loaded: " + pineSoPath);
Log.i(TAG, "Pine initialized: antiChecks=" + PineConfig.antiChecks);
Log.i(TAG, "Hook installed on: Application.onCreate");
Log.i(TAG, "========== INIT COMPLETE ==========");

// 关键节点
Log.i(TAG, "Application.onCreate triggered: " + app.getClass().getName());
Log.i(TAG, "App ClassLoader: " + app.getClassLoader().getClass().getName());
Log.i(TAG, "Cache dir: " + app.getCacheDir().getAbsolutePath());
```

#### HTTP 请求日志

```java
// 模板：HTTP 请求/响应日志
private void logRequest(Request request) {
    String method = request.method();
    String url = request.url().toString();
    String contentType = request.body() != null ?
        request.body().contentType().toString() : "-";

    Log.i(TAG, "→ " + method + " " + url + " [" + contentType + "]");

    // POST 请求体（限制长度）
    if ("POST".equals(method) && request.body() != null) {
        String body = readRequestBody(request);
        if (body.length() > 500) {
            Log.i(TAG, "  ReqBody[" + body.length() + "]: " + body.substring(0, 500) + "...");
            dumpToFile("req_" + urlToFileName(url), body);
        } else {
            Log.i(TAG, "  ReqBody: " + body);
        }
    }
}

private void logResponse(Response response, String url) {
    int code = response.code();
    Log.i(TAG, "← [" + code + "] " + url);

    String body = peekResponseBody(response);
    if (body != null && !looksLikeBinary(body)) {
        if (body.length() > 500) {
            Log.i(TAG, "  Body[" + body.length() + "]: " + body.substring(0, 500) + "...");
            dumpToFile("resp_" + urlToFileName(url), body);
        } else {
            Log.i(TAG, "  Body: " + body);
        }
    } else {
        Log.i(TAG, "  Body: <binary, " + response.body().contentLength() + " bytes>");
    }
}
```

#### Cipher 加密日志

```java
// 模板：Cipher Hook 日志
private void logCipherInit(Cipher cipher, int opmode, Key key, AlgorithmParameterSpec params) {
    String mode = opmode == Cipher.DECRYPT_MODE ? "DECRYPT" :
                  opmode == Cipher.ENCRYPT_MODE ? "ENCRYPT" : "OTHER";
    String algo = cipher.getAlgorithm();
    String keyHex = bytesToHex(key.getEncoded());

    // 跳过 TLS 相关算法
    if (isTlsAlgorithm(algo)) return;

    Log.i(TAG, "CIPHER_INIT | mode=" + mode + " | algo=" + algo);
    Log.i(TAG, "  Key[" + key.getEncoded().length + "]: " + keyHex);

    if (params instanceof IvParameterSpec) {
        String ivHex = bytesToHex(((IvParameterSpec) params).getIV());
        Log.i(TAG, "  IV[" + ((IvParameterSpec) params).getIV().length + "]: " + ivHex);
    }
}

private void logCipherDoFinal(byte[] input, byte[] output, CipherContext ctx) {
    Log.i(TAG, "CIPHER_DOFINAL | algo=" + ctx.algorithm);
    Log.i(TAG, "  Input[" + input.length + "] → Output[" + output.length + "]");

    String plaintext = new String(output, StandardCharsets.UTF_8);
    if (plaintext.startsWith("{") || looksLikeChinese(plaintext)) {
        Log.i(TAG, "  Plaintext: " + truncate(plaintext, 500));
        dumpToFile("cipher_output", plaintext);

        // 首次命中时打印调用栈
        if (!ctx.stackPrinted) {
            logStackTrace("CIPHER_STACK", 20);
            ctx.stackPrinted = true;
        }
    }
}
```

#### JNI 方法日志

```java
// 模板：JNI Hook 日志
private void logJniCall(String className, String methodName, Object[] args, Object result) {
    StringBuilder sb = new StringBuilder();
    sb.append("JNI_CALL | ").append(className).append(".").append(methodName).append("(");

    // 参数摘要
    for (int i = 0; i < args.length; i++) {
        if (i > 0) sb.append(", ");
        sb.append(summarizeArg(args[i]));
    }
    sb.append(")");

    Log.i(TAG, sb.toString());

    // 返回值
    if (result != null) {
        if (result instanceof String) {
            String s = (String) result;
            Log.i(TAG, "  Return[" + s.length() + "]: " + truncate(s, 500));
            if (s.length() > 100) {
                dumpToFile("jni_" + methodName, s);
            }
        } else if (result instanceof byte[]) {
            Log.i(TAG, "  Return: byte[" + ((byte[]) result).length + "]");
        } else {
            Log.i(TAG, "  Return: " + result);
        }
    }
}

// 参数摘要（避免打印过长）
private String summarizeArg(Object arg) {
    if (arg == null) return "null";
    if (arg instanceof String) {
        String s = (String) arg;
        return "String[" + s.length() + "]" + (s.length() < 50 ? "=" + s : "");
    }
    if (arg instanceof byte[]) return "byte[" + ((byte[]) arg).length + "]";
    if (arg.getClass().isArray()) return arg.getClass().getSimpleName() + "[" + java.lang.reflect.Array.getLength(arg) + "]";
    return arg.getClass().getSimpleName();
}
```

#### 调用栈日志

```java
// 模板：打印调用栈（用于定位关键类）
private void logStackTrace(String label, int maxFrames) {
    StackTraceElement[] stack = Thread.currentThread().getStackTrace();
    Log.i(TAG, "=== " + label + " ===");
    int count = 0;
    for (StackTraceElement e : stack) {
        String className = e.getClassName();
        // 跳过系统类和 Hook 框架类
        if (className.startsWith("dalvik.") ||
            className.startsWith("java.lang.") ||
            className.startsWith("top.canyie.pine.") ||
            className.contains("MainHook")) {
            continue;
        }
        Log.i(TAG, "  " + className + "." + e.getMethodName() + ":" + e.getLineNumber());
        if (++count >= maxFrames) break;
    }
}
```

### 8.3 日志过滤与防洪泛

```java
// 模板：去重 + 限流
private static final ConcurrentHashMap<String, Long> recentLogs = new ConcurrentHashMap<>();
private static final long DEDUP_WINDOW_MS = 500;  // 500ms 内相同内容去重
private static final AtomicInteger logCounter = new AtomicInteger(0);
private static final int MAX_LOGS_PER_MINUTE = 100;
private static volatile long minuteStart = System.currentTimeMillis();

private boolean shouldLog(String key) {
    long now = System.currentTimeMillis();

    // 限流检查
    if (now - minuteStart > 60000) {
        minuteStart = now;
        logCounter.set(0);
    }
    if (logCounter.incrementAndGet() > MAX_LOGS_PER_MINUTE) {
        return false;
    }

    // 去重检查
    Long lastTime = recentLogs.get(key);
    if (lastTime != null && now - lastTime < DEDUP_WINDOW_MS) {
        return false;
    }
    recentLogs.put(key, now);
    return true;
}

// TLS 算法过滤（避免捕获 HTTPS 流量）
private boolean isTlsAlgorithm(String algo) {
    String upper = algo.toUpperCase();
    return upper.contains("GCM") ||
           upper.contains("CHACHA") ||
           upper.contains("POLY1305") ||
           upper.contains("RSA/ECB/OAEPWITH") ||
           upper.contains("ECDH");
}

// 静态资源过滤
private boolean isStaticResource(String url) {
    String lower = url.toLowerCase();
    return lower.endsWith(".jpg") || lower.endsWith(".png") ||
           lower.endsWith(".gif") || lower.endsWith(".css") ||
           lower.endsWith(".js") || lower.endsWith(".woff") ||
           lower.contains("/static/") || lower.contains("/assets/");
}
```

### 8.4 数据 Dump 机制

```java
// 模板：数据 Dump 到文件
private static final AtomicInteger dumpCounter = new AtomicInteger(0);
private static String dumpDir = null;

private void initDumpDir(Context context) {
    dumpDir = context.getCacheDir().getAbsolutePath() + "/hook_dump";
    new File(dumpDir).mkdirs();
}

private void dumpToFile(String label, String content) {
    if (dumpDir == null) return;
    try {
        int seq = dumpCounter.incrementAndGet();
        String filename = String.format("%s_%03d_%s.json", TAG.toLowerCase(), seq, sanitizeFileName(label));
        File file = new File(dumpDir, filename);
        FileOutputStream fos = new FileOutputStream(file);
        fos.write(content.getBytes(StandardCharsets.UTF_8));
        fos.close();
        Log.i(TAG, "  DUMP: " + content.length() + " chars -> " + file.getAbsolutePath());
    } catch (Exception e) {
        Log.w(TAG, "Dump failed: " + e.getMessage());
    }
}

private String sanitizeFileName(String name) {
    return name.replaceAll("[^a-zA-Z0-9_-]", "_").substring(0, Math.min(name.length(), 50));
}
```

### 8.5 日志查看命令速查

```bash
# 查看所有 Hook 日志
adb logcat -d | grep -a "YYHook\|QDHook"

# 只看 HTTP 请求
adb logcat -d | grep -a "YYHook" | grep -a "→\|←"

# 只看 Cipher 操作
adb logcat -d | grep -a "CIPHER_"

# 只看 JNI 调用
adb logcat -d | grep -a "JNI_CALL"

# 只看初始化
adb logcat -d | grep -a "INIT\|Hooked\|installed"

# 只看错误
adb logcat -d | grep -a "YYHook" | grep -ai "error\|fail\|exception"

# 只看提取的内容
adb logcat -d | grep -a "EXTRACTED\|Plaintext\|Return\["

# 实时监控（过滤噪音）
adb logcat -v brief | grep -a "YYHook" | grep -av "check_health\|mqtt\|datahub"

# 拉取 dump 文件
adb shell "su -c 'ls /data/user/0/<pkg>/cache/hook_dump/'"
adb shell "su -c 'cp /data/user/0/<pkg>/cache/hook_dump/* /data/local/tmp/'"
adb pull /data/local/tmp/*.json ./dumps/
```

### 8.6 日志分析 Checklist

AI 分析日志时应该检查的关键点：

```markdown
## 初始化阶段
- [ ] Pine 是否成功加载和初始化？
- [ ] 所有 Hook 是否安装成功？
- [ ] Application 类名是什么？
- [ ] 有没有异常/错误日志？

## HTTP 层
- [ ] 是否捕获到 API 请求？
- [ ] 响应是明文 JSON 还是加密数据？
- [ ] 关键 API 端点有哪些？
- [ ] 有没有 POST 请求体？

## 加密层
- [ ] 使用了什么加密算法？
- [ ] 密钥长度和格式？
- [ ] IV 是固定的还是动态的？
- [ ] 解密后的内容是什么格式？
- [ ] 调用栈指向哪个类？

## 稳定性
- [ ] App 是否稳定运行 60+ 秒？
- [ ] 有没有日志洪泛（每秒超过 100 条）？
- [ ] 有没有崩溃/ANR？
- [ ] 内存使用是否正常？

## 数据完整性
- [ ] 目标数据（章节内容等）是否完整提取？
- [ ] Dump 文件是否成功生成？
- [ ] 数据编码是否正确（UTF-8）？
```

---

## 九、工具层简化设计

经过思考，不需要封装复杂的 Python HTTP 服务。大多数操作可以直接用 Bash 命令完成，Skill 只需要指导 AI 如何调用这些命令。

### 9.1 核心命令清单

```bash
# === 设备检查 ===
# 检查 ADB 连接
adb devices

# 检查 Root
adb shell "su -c 'id'"

# 检查 Zygisk
adb shell "ls /data/adb/modules/zygisksu 2>/dev/null || ls /data/adb/modules/zygisknext 2>/dev/null"

# === APK 分析 ===
# 提取 APK 基本信息
aapt dump badging /path/to/app.apk | grep -E "package:|application-label:|launchable-activity:"

# 列出 APK 中的 SO 文件（检测加固）
unzip -l /path/to/app.apk | grep "\.so$"

# 提取 APK 到手机（用于分析）
adb shell pm path <pkg>

# === DEX 反编译 ===
# 用 jadx 反编译
jadx --no-res -d /tmp/jadx_out /path/to/classes.dex

# 搜索关键类
grep -r "Cipher\|encrypt\|decrypt" /tmp/jadx_out/sources/

# === Hook 项目 ===
# 编译
cd <project_dir>
JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
ANDROID_HOME=/home/yuyang/android-sdk \
./gradlew :app:assembleDebug

# 提取 DEX
cd zygisk/extracted
unzip -o ../../app/build/outputs/apk/debug/app-debug.apk 'classes*.dex'

# 部署 DEX
for f in classes.dex classes2.dex classes3.dex; do
    adb push $f /data/local/tmp/hook/$f
done
adb shell "su -c 'cp /data/local/tmp/hook/classes*.dex /data/adb/modules/<module_id>/dex/'"

# 重启 App
adb shell am force-stop <pkg>
adb shell monkey -p <pkg> -c android.intent.category.LAUNCHER 1

# === 日志与数据 ===
# 清空日志
adb logcat -c

# 采集日志
adb logcat -d | grep -a "<TAG>"

# 拉取 dump 文件
adb shell "su -c 'cp /data/user/0/<pkg>/cache/hook_dump/* /data/local/tmp/'"
adb pull /data/local/tmp/*.json ./

# === UI 自动化 ===
# 截图
adb exec-out screencap -p > screenshot.png

# 点击
adb shell input tap <x> <y>

# 滑动
adb shell input swipe <x1> <y1> <x2> <y2> <duration_ms>

# UI 层级
adb shell uiautomator dump /data/local/tmp/ui.xml
adb shell cat /data/local/tmp/ui.xml

# 当前 Activity
adb shell dumpsys activity activities | grep "topResumedActivity"
```

### 9.2 辅助脚本（可选）

如果某些操作太复杂，可以封装为简单的 Shell 脚本：

```bash
# scripts/detect_protection.sh - 检测加固类型
#!/bin/bash
APK="$1"
SO_LIST=$(unzip -l "$APK" 2>/dev/null | grep "\.so$" | awk '{print $4}')

if echo "$SO_LIST" | grep -q "libjiagu"; then
    echo "360加固"
elif echo "$SO_LIST" | grep -q "libbangcle"; then
    echo "梆梆加固"
elif echo "$SO_LIST" | grep -q "libshell"; then
    echo "腾讯乐固"
elif echo "$SO_LIST" | grep -q "libsgmain\|libsgsecurit"; then
    echo "阿里聚安全"
else
    echo "未识别/无加固"
fi
```

```bash
# scripts/quick_deploy.sh - 快速部署
#!/bin/bash
PROJECT_DIR="$1"
MODULE_ID="$2"
ADB="${ADB:-adb}"

cd "$PROJECT_DIR"
./gradlew :app:assembleDebug || exit 1

cd zygisk/extracted
unzip -o ../../app/build/outputs/apk/debug/app-debug.apk 'classes*.dex'

for f in classes*.dex; do
    $ADB push "$f" /data/local/tmp/hook/
done

$ADB shell "su -c 'cp /data/local/tmp/hook/classes*.dex /data/adb/modules/$MODULE_ID/dex/'"
echo "Deploy complete!"
```

### 9.3 Skill 调用方式

SKILL.md 中直接指导 AI 使用 Bash 工具：

```markdown
## 工具调用方式

本 Skill 不需要额外的 Python 服务，直接使用 Bash 工具执行命令。

### 示例：检测加固类型

使用 Bash 工具执行：
```bash
unzip -l /path/to/app.apk | grep "\.so$" | grep -E "jiagu|bangcle|shell|sgmain"
```

根据输出判断：
- 包含 `libjiagu` → 360加固
- 包含 `libbangcle` → 梆梆加固
- 包含 `libshell` → 腾讯乐固
- 无特征 SO → 可能未加固

### 示例：采集日志并分析

```bash
# 1. 清空日志
adb logcat -c

# 2. 等待 App 运行一段时间...

# 3. 采集日志
adb logcat -d | grep -a "YYHook" > /tmp/hook_logs.txt

# 4. 用 Read 工具读取日志，AI 分析
```
```

---

## 十、知识层自动沉淀机制

### 10.1 问题

随着逆向更多 App，会产生大量新知识：
- 新的加固类型及其绕过方案
- 新的加密模式及其破解方法
- 新的 Hook 技巧和最佳实践
- 新的检测机制和对抗策略

如果只靠人工整理，容易遗漏或拖延。需要一个**半自动化**的知识沉淀机制。

### 10.2 方案：AI 辅助知识提取

```
逆向过程 → AI 识别关键发现 → 结构化记录 → 人工 Review → 合并到知识库
```

#### 触发条件（AI 判断）

AI 在以下情况应主动提取知识：

| 触发场景 | 知识类型 | 目标文件 |
|---------|---------|---------|
| 识别到新的加固 SO 特征 | 加固特征 | `knowledge/protections/<name>.md` |
| 发现新的检测机制 | 对抗策略 | `knowledge/troubleshooting/<name>.md` |
| 成功绕过某种检测 | 绕过方案 | 追加到对应加固文档 |
| 发现新的加密模式 | 加密模式 | `knowledge/patterns/<name>.md` |
| 某个 Hook 技巧在多个 App 验证有效 | Hook 策略 | `knowledge/strategies/<name>.md` |

#### 知识条目格式

```markdown
# [知识标题]

## 来源
- App: [App 名称]
- 日期: [日期]
- 验证状态: 单 App 验证 / 多 App 验证

## 问题描述
[遇到了什么问题]

## 解决方案
[具体的解决步骤/代码]

## 适用场景
[什么情况下可以使用这个方案]

## 注意事项
[使用时的注意点]
```

#### 自动化程度

| 步骤 | 自动化程度 | 说明 |
|------|-----------|------|
| 识别关键发现 | AI 判断 | AI 在逆向过程中主动识别 |
| 结构化记录 | AI 生成 | AI 按模板生成 Markdown |
| 写入暂存区 | 自动 | 写入 `knowledge/_pending/` 目录 |
| Review & 合并 | 人工 | 用户确认后移动到正式目录 |
| 去重/更新 | AI 辅助 | AI 检查是否已有类似知识 |

#### SKILL.md 中的指令

```markdown
## 知识沉淀规范

在逆向过程中，当你发现以下情况时，必须主动记录：

1. **新加固特征**：发现 SO 文件名与已知加固不匹配
2. **新检测机制**：App 崩溃/异常，且原因不在已有文档中
3. **成功绕过**：通过非标准方法解决了某个问题
4. **新加密模式**：Cipher 日志显示未见过的算法组合

记录方式：
```bash
# 写入待审核目录
cat > knowledge/_pending/$(date +%Y%m%d)_<简短描述>.md << 'EOF'
# [知识标题]
## 来源
- App: [当前 App]
- 日期: $(date +%Y-%m-%d)
...
EOF
```

用户会定期 Review `_pending/` 目录并合并到正式知识库。
```

---

## 十一、代码继承与分层架构

### 11.1 问题

为特定 App 开发的代码可能：
1. **过于特化**：只适用于该 App，直接放入基础代码会污染通用性
2. **有潜在价值**：经过多 App 验证后，可以提升为通用方案
3. **相互冲突**：不同 App 可能需要不同的实现细节

用户的建议是正确的：**用 App 名为文件夹，继承基础代码库**。

### 11.2 三层架构方案

```
reverse-app/
├── base/                           # 第一层：基础框架（稳定，很少改动）
│   ├── MainHook.java               # 入口模板，定义生命周期
│   ├── HookContext.java            # Hook 上下文
│   └── Utils.java                  # 工具方法
│
├── plugins/                        # 第二层：通用插件（经过验证的方案）
│   ├── okhttp/
│   │   └── OkHttpInterceptor.java  # OkHttp 拦截器（已在多 App 验证）
│   ├── cipher/
│   │   └── CipherHook.java         # Cipher Hook（通用加密监控）
│   └── jni/
│       └── JniHook.java            # JNI 通用 Hook
│
└── apps/                           # 第三层：App 特定代码（实验性，可能不通用）
    ├── qidian/
    │   ├── config.json             # 配置：启用哪些插件
    │   ├── QDContentDecryptor.java # 起点特定：3DES 章节解密
    │   └── README.md               # 该 App 的逆向记录
    ├── yanyan/
    │   ├── config.json
    │   ├── YYTextExtractor.java    # 盐言特定：JNI getText 主动调用
    │   └── README.md
    └── _template/                  # 新 App 项目模板
        ├── config.json
        └── CustomHook.java
```

### 11.3 代码组织方式

由于 Java 不支持真正的"运行时继承"（编译时确定），使用**组合 + 配置**模式：

#### config.json（每个 App 的配置）

```json
{
  "target_package": "com.qidian.QDReader",
  "log_tag": "QDHook",
  "module_id": "qdhook_zygisk",

  "plugins": {
    "okhttp": {
      "enabled": true,
      "config": {
        "log_binary": false,
        "max_body_length": 500
      }
    },
    "cipher": {
      "enabled": true,
      "config": {
        "skip_tls": true,
        "algorithms": ["DESede", "AES"]
      }
    }
  },

  "custom_hooks": [
    {
      "class": "com.qidian.QDReader.component.bll.v",
      "methods": ["*"],
      "handler": "QDContentDecryptor"
    }
  ]
}
```

#### MainHook.java（基础框架）

```java
// base/MainHook.java - 不直接修改，通过配置和插件扩展

public class MainHook {
    public static void entry(ClassLoader classLoader, String pineSoPath) {
        // 1. 加载配置
        HookConfig config = loadConfig();

        // 2. 初始化 Pine
        initPine(pineSoPath, config);

        // 3. 安装通用插件
        if (config.isPluginEnabled("okhttp")) {
            PluginManager.install(new OkHttpPlugin(config.getPluginConfig("okhttp")));
        }
        if (config.isPluginEnabled("cipher")) {
            PluginManager.install(new CipherPlugin(config.getPluginConfig("cipher")));
        }

        // 4. 安装 App 特定 Hook（通过反射加载）
        for (CustomHookConfig hookConfig : config.getCustomHooks()) {
            PluginManager.installCustom(classLoader, hookConfig);
        }
    }
}
```

#### App 特定代码（继承插件接口）

```java
// apps/qidian/QDContentDecryptor.java

public class QDContentDecryptor implements CustomHookHandler {

    @Override
    public void onMethodCall(Pine.CallFrame frame) {
        // 起点特定的解密逻辑
        // ...
    }

    // 如果发现这个方法在其他 App 也有用，
    // 可以提取到 plugins/cipher/ 中
}
```

### 11.4 代码提升流程

```
实验阶段                    验证阶段                    通用阶段
apps/yanyan/             → 在其他 App 测试            → plugins/
YYTextExtractor.java       是否有效                     NativeTextExtractor.java

apps/qidian/             → 发现盐言也用类似方案       → plugins/cipher/
QDContentDecryptor.java    抽象出通用接口               ContentDecryptor.java
```

#### 提升标准

代码从 `apps/` 提升到 `plugins/` 需要满足：

| 条件 | 说明 |
|------|------|
| **多 App 验证** | 至少在 2 个不同 App 中验证有效 |
| **接口抽象** | 提取出通用接口，App 特定部分通过配置或子类实现 |
| **文档完善** | 有清晰的使用说明和适用场景 |
| **无副作用** | 不会影响其他插件或基础框架 |

#### 提升操作

```bash
# 1. 将 App 特定代码复制到 plugins
cp apps/yanyan/YYTextExtractor.java plugins/jni/NativeTextExtractor.java

# 2. 重构为通用接口
# - 移除硬编码的类名/方法名
# - 添加配置项
# - 编写文档

# 3. 更新原 App 配置，使用新插件
# apps/yanyan/config.json:
# "plugins": { "native_text": { "enabled": true, ... } }

# 4. 删除原 App 特定代码
rm apps/yanyan/YYTextExtractor.java

# 5. 记录到知识库
# knowledge/strategies/native_text_extract.md
```

### 11.5 潜在问题与解决方案

| 问题 | 风险 | 解决方案 |
|------|------|---------|
| **配置复杂度** | 配置项太多难以管理 | 提供合理默认值，只暴露常用配置 |
| **插件依赖** | 插件之间可能有依赖关系 | 明确依赖声明，PluginManager 处理加载顺序 |
| **版本兼容** | 基础框架更新可能破坏 App 特定代码 | 语义化版本，Breaking Change 需要迁移指南 |
| **编译复杂度** | 每个 App 需要独立编译 | 统一 Gradle 配置，通过 flavor 区分 |
| **ClassLoader** | 动态加载 App 特定类可能有问题 | 编译时合并，运行时通过反射调用 |

### 11.6 简化方案（推荐起步）

完整的插件架构可能过于复杂。建议先用简化方案：

```
frida-test/
├── base/                    # 基础模板（复制使用，不直接修改）
│   ├── MainHook.java        # 入口模板
│   ├── OkHttpInterceptor.java
│   └── CipherHook.java
│
├── qidian/                  # 起点读书项目
│   └── app/src/.../MainHook.java  # 从 base 复制，添加特定逻辑
│
├── yanyan/                  # 盐言故事项目
│   └── app/src/.../MainHook.java  # 从 base 复制，添加特定逻辑
│
└── knowledge/               # 知识库（独立于代码）
    └── ...
```

**工作流**：
1. 新 App：从 `base/` 复制模板到新目录
2. 开发：在 App 目录中修改，添加特定逻辑
3. 提升：发现通用代码后，**手动**更新 `base/` 模板
4. 同步：其他 App 项目可选择性合并 base 的更新

**优点**：
- 简单直接，无需复杂的插件系统
- 每个 App 项目完全独立，不会相互影响
- 适合当前 2-3 个 App 的规模

**缺点**：
- 代码重复
- 手动同步可能遗漏

**建议**：先用简化方案，等 App 数量超过 5 个再考虑完整插件架构。

---

## 十二、结论

### 可行性评估：高

| 维度 | 评估 | 说明 |
|------|------|------|
| **技术基础** | ✅ 扎实 | Zygisk + Pine 已在 360/梆梆加固上验证 |
| **流程标准化** | ✅ 可行 | 逆向有明确阶段，适合 Skill 封装 |
| **AI 决策能力** | ✅ 强项 | 代码分析、模式识别、策略选择 |
| **知识沉淀** | ✅ 可实现 | 半自动化机制，AI 识别 + 人工 Review |
| **代码复用** | ✅ 可行 | 三层架构或简化方案均可 |

### 核心设计要点

1. **日志方法论是关键**（第八章）
   - 好的日志设计让 AI 能快速理解 App 行为
   - 分层 TAG、结构化输出、防洪泛、可追溯

2. **工具层保持简单**（第九章）
   - 直接用 Bash 命令，不需要 Python HTTP 服务
   - Skill 的价值在于流程指导和知识积累，不在于工具封装

3. **知识层自动沉淀**（第十章）
   - AI 主动识别关键发现
   - 写入 `_pending/` 暂存区
   - 人工 Review 后合并到正式知识库

4. **代码分层架构**（第十一章）
   - **简化方案（推荐起步）**：每个 App 独立项目，手动同步通用代码
   - **完整方案（规模化后）**：base → plugins → apps 三层架构

### 实施建议

| 阶段 | 目标 | 工作内容 |
|------|------|---------|
| **Phase 0** | 整理现有资产 | 将 qidian/yanyan 的通用代码提取到 base/ |
| **Phase 1** | MVP | 创建 SKILL.md + 基础知识库 + 代码模板 |
| **Phase 2** | 验证 | 用 Skill 逆向第 3 个 App，验证流程 |
| **Phase 3** | 迭代 | 根据实践反馈优化 Skill 和知识库 |

### 决策记录（2026-02-05）

- [x] **知识库目录结构**：需要重新组织（见下方独立仓库结构）
- [x] **简化方案 vs 完整插件架构**：**先用简化方案**，App 数量超过 5 个再考虑插件架构
- [x] **独立仓库**：**需要**，创建专门的 reverse-app-skill 仓库

---

## 十三、独立仓库设计

### 13.1 仓库结构

```
reverse-app-skill/                       # 独立仓库
├── SKILL.md                             # Claude Code Skill 入口文件
├── README.md                            # 仓库说明
│
├── base/                                # 基础代码模板
│   ├── zygisk/
│   │   ├── main.cpp                     # Zygisk 模块模板
│   │   ├── zygisk.hpp
│   │   └── Android.mk
│   ├── hook/
│   │   ├── MainHook.java                # Hook 入口模板
│   │   ├── OkHttpInterceptor.java       # OkHttp 拦截器
│   │   ├── CipherHook.java              # Cipher Hook
│   │   └── Utils.java                   # 工具类
│   └── gradle/
│       ├── build.gradle                 # 项目 build.gradle
│       └── settings.gradle
│
├── knowledge/                           # 知识库（核心资产）
│   ├── protections/                     # 加固方案
│   │   ├── 360_jiagu.md                 # 360加固特征 + 绕过方案
│   │   ├── bangcle.md                   # 梆梆加固
│   │   ├── tencent_legu.md              # 腾讯乐固
│   │   └── _detection_features.md       # SO 特征速查表
│   ├── strategies/                      # Hook 策略
│   │   ├── okhttp_intercept.md          # OkHttp 拦截
│   │   ├── cipher_hook.md               # Cipher Hook
│   │   ├── jni_hook.md                  # JNI Hook
│   │   └── native_text_extract.md       # Native 文本提取（盐言方案）
│   ├── patterns/                        # 加密模式
│   │   ├── 3des_cbc.md                  # 3DES-CBC（起点方案）
│   │   ├── aes_cbc.md                   # AES-CBC
│   │   └── rsa_key_exchange.md          # RSA 密钥交换
│   ├── troubleshooting/                 # 问题排查
│   │   ├── delayed_crash.md             # 延迟崩溃
│   │   ├── log_flooding.md              # 日志洪泛
│   │   └── classloader_issues.md        # ClassLoader 问题
│   └── _pending/                        # 待审核知识（AI 生成）
│       └── .gitkeep
│
├── apps/                                # App 特定项目（简化方案）
│   ├── _template/                       # 新项目模板
│   │   ├── config.json
│   │   ├── MainHook.java
│   │   └── README.md
│   ├── qidian/                          # 起点读书
│   │   ├── config.json                  # 配置
│   │   ├── MainHook.java                # 从 base 复制 + 特定逻辑
│   │   ├── QDContentDecryptor.java      # 特定代码
│   │   └── README.md                    # 逆向记录
│   ├── yanyan/                          # 盐言故事
│   │   ├── config.json
│   │   ├── MainHook.java
│   │   ├── YYTextExtractor.java
│   │   └── README.md
│   └── xhs/                             # 小红书
│       ├── config.json
│       ├── MainHook.java
│       └── README.md                    # 逆向记录
│
├── scripts/                             # 辅助脚本
│   ├── detect_protection.sh             # 检测加固类型
│   ├── quick_deploy.sh                  # 快速部署
│   └── pull_dumps.sh                    # 拉取数据
│
└── docs/                                # 设计文档
    ├── DESIGN.md                        # 本设计文档
    └── LOGGING_GUIDE.md                 # 日志方法论指南
```

### 13.2 与现有项目的关系

```
现有项目（frida-test/）                新仓库（reverse-app-skill/）
──────────────────────────             ─────────────────────────────
qidian/                        →       apps/qidian/（代码迁移）
  └── docs/QIDIAN_REVERSE.md   →       apps/qidian/README.md

yanyan/                        →       apps/yanyan/（代码迁移）
  └── docs/YANYAN_REVERSE.md   →       apps/yanyan/README.md

xhs/                           →       apps/xhs/（代码迁移）
  └── docs/README.md           →       apps/xhs/README.md

scraped_docs/                  →       knowledge/（精华提取）

docs/AUTO_REVERSE_SKILL_DESIGN.md →    docs/DESIGN.md
```

### 13.3 迁移步骤

```bash
# 1. 创建独立仓库
mkdir reverse-app-skill
cd reverse-app-skill
git init

# 2. 创建目录结构
mkdir -p base/{zygisk,hook,gradle}
mkdir -p knowledge/{protections,strategies,patterns,troubleshooting,_pending}
mkdir -p apps/{_template,qidian,yanyan}
mkdir -p scripts docs

# 3. 迁移基础代码
cp frida-test/qidian/zygisk/jni/* base/zygisk/
cp frida-test/qidian/app/src/.../MainHook.java base/hook/

# 4. 迁移知识文档
# 从 QIDIAN_REVERSE.md 和 YANYAN_REVERSE.md 提取通用知识
# 整理到 knowledge/ 各子目录

# 5. 迁移 App 特定代码
cp -r frida-test/qidian/app/src/... apps/qidian/
cp -r frida-test/yanyan/app/src/... apps/yanyan/
cp -r frida-test/xhs/app/src/... apps/xhs/

# 6. 创建 SKILL.md
# （基于本设计文档第四章）
```

### 13.4 使用方式

用户在 Claude Code 中：

```bash
# 方式 1：克隆仓库到 .claude/skills/
git clone <repo> ~/.claude/skills/reverse-app

# 方式 2：直接在项目中使用
git clone <repo> ./reverse-app-skill
# 然后告诉 Claude：参考 reverse-app-skill/SKILL.md 逆向某 App
```

### 13.5 知识库维护流程

```
日常使用                          定期维护
────────                          ────────
1. AI 逆向过程中发现新知识         1. Review _pending/ 目录
2. AI 写入 knowledge/_pending/    2. 验证知识准确性
3. 继续逆向工作                   3. 移动到正式目录
                                  4. 更新相关文档索引
                                  5. git commit & push
```
