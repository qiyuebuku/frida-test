# Hook 策略模式库

## 策略 1: OkHttp 动态代理拦截器（最通用）

**适用场景**: 所有使用 OkHttp 的 App（95%+ 的现代 Android App）
**安全等级**: 极高（标准 Java 代码，不触发任何检测）

```java
// 1. 创建动态代理 Interceptor
Object interceptor = Proxy.newProxyInstance(cl,
    new Class[]{interceptorClass}, handler);

// 2. Hook OkHttpClient$Builder.build()
Pine.hook(builderClass, "build", new MethodHook() {
    public void beforeCall(CallFrame f) {
        addInterceptor.invoke(f.thisObject, interceptor);
    }
});
```

**关键注意**:
- 必须从业务 API 域名的请求中精确捕获 OkHttpClient
- 避免捕获 Glide/Picasso 等图片加载的客户端
- 使用 `apiClientCaptured` 一次性标志防止覆盖
- 使用 `peekBody()` 读取响应 Body，不消费原 Body
- 添加 500ms 去重窗口 + 静态资源过滤

## 策略 2: Cipher 加密 Hook（通用加密捕获）

**适用场景**: 需要捕获加密密钥和算法
**安全等级**: 高（框架类 Hook）

```java
// Hook Cipher.init — 捕获算法、密钥、IV
Pine.hook(Cipher.class, "init", ...);

// Hook Cipher.doFinal — 捕获输入输出
Pine.hook(Cipher.class, "doFinal", ...);
```

**关键注意**:
- 必须过滤 TLS 相关算法（GCM, CHACHA20, POLY1305, OAEP, RSA）
- 仅跟踪 DECRYPT_MODE，跳过 ENCRYPT_MODE（减少噪音）
- 速率限制：每 60 秒最多记录 30 条
- 通过栈追踪定位业务调用方

## 策略 3: WCDB/SQLite 数据库 Hook

**适用场景**: 微信等使用加密数据库的 App
**安全等级**: 高

```java
// Hook SQLiteDatabase.openDatabase — 捕获密钥
Pine.hook(sqliteDbClass, "openDatabase", new MethodHook() {
    public void beforeCall(CallFrame f) {
        byte[] pwd = (byte[]) f.args[pwdIndex];
        String path = (String) f.args[pathIndex];
        saveKeyToFile(path, pwd);
    }
});
```

**关键注意**:
- 通过参数类型动态查找方法重载（不硬编码参数位置）
- 延迟 3 秒等待数据库完全初始化
- 通过 getPath() 反射获取数据库路径，动态推导 MicroMsg 目录

## 策略 4: JNI Native 方法 Hook（深层解密）

**适用场景**: App 使用 Native C++ 层解密内容（如盐言故事 BaseJniWarp.getText()）

```java
// Hook getChapterItemHeightArray 的 afterCall
// 然后主动调用 getText() 获取解密文本
Pine.hook(heightMethod, new MethodHook() {
    public void afterCall(CallFrame f) {
        String text = (String) textMethod.invoke(f.thisObject, pageIndex, 0, MAX_VALUE);
        dumpToFile("extracted_fulltext", text);
    }
});
```

## 策略 5: App 内部方法反射调用（起点读书解密）

**适用场景**: 解密逻辑完全在 App 内部，无标准 Cipher 调用

```java
// Hook bll.v 全公共方法，通过返回类型匹配
for (Method m : bllVClass.getDeclaredMethods()) {
    if (isPublic(m) && !isObjectMethod(m)) {
        Pine.hook(m, new MethodHook() {
            public void afterCall(CallFrame f) {
                if (f.getResult() instanceof ChapterContentItem) {
                    extractContent(f.getResult());
                }
            }
        });
    }
}

// 主动触发解密：反射调用 L→K→R 方法链
bllV.L(bookId, chapterItem);  // 触发解密
bllV.K(bookId, chapterItem);  // 获取路径
bllV.R(bookId, chapterItem);  // 获取缓存
```

## 策略 6: LocalSocket RPC 通道

**适用场景**: 需要 PC 端远程控制手机端 Hook 模块

```java
// 手机端
LocalServerSocket server = new LocalServerSocket("hook_rpc");
while (true) {
    LocalSocket client = server.accept();
    new Thread(() -> handleClient(client)).start();
}

// PC 端
adb forward tcp:12345 localabstract:hook_rpc
socket.connect("127.0.0.1", 12345);
socket.send('{"cmd":"ping"}\n');
```

## 策略 7: HTTP 代理服务器（内嵌）

**适用场景**: 需要通过 App 的 OkHttpClient 转发请求（复用签名/Cookie）

```java
// 在 App 进程内启动 ServerSocket 监听 18900
ServerSocket server = new ServerSocket(18900);
// 接收 JSON 请求，通过 savedOkHttpClient 发送
// 返回 JSON 响应
```

## 策略 8: WebView Cookie DB 直读（认证参数提取）

**适用场景**: App 使用 WebView 访问 Web 服务，认证 Token 存储在 WebView 的 Cookie 数据库中（SQLite）
**安全等级**: 极高（只读数据库，不 Hook 任何方法）

```java
// Cookie DB 路径探测（路径因 WebView 版本/设备而异）
String[] candidatePaths = {
    "/data/data/<pkg>/app_webview/Default/Cookies",
    "/data/data/<pkg>/app_webview_<pkg>/Default/Cookies",
};
String dbPath = null;
for (String p : candidatePaths) {
    if (new java.io.File(p).exists()) { dbPath = p; break; }
}

// 只读打开 + SQL 查询
SQLiteDatabase db = SQLiteDatabase.openDatabase(dbPath, null, OPEN_READONLY);
Cursor c = db.rawQuery(
    "SELECT value FROM cookies WHERE host_key=? AND name=?",
    new String[]{".target-domain.com", "token_name"});
if (c.moveToFirst()) {
    String tokenValue = c.getString(0);
    // 上报到服务端
}
c.close();
db.close();
```

**关键注意**:
- 必须用 `OPEN_READONLY` 打开，避免锁死 WebView 的数据库
- Cookie DB 路径不固定，必须做路径探测（见 pitfalls #15）
- 延迟读取：App 启动后 WebView 需要时间初始化和写入 Cookie，建议延迟 15-30 秒
- 定期轮询：Token 可能会被 App 更新，建议每 30 分钟重读一次
- 适合提取 session token、hexin-v、CSRF token 等 WebView 级别的认证参数
- **不适用于 HttpOnly cookie**：WebView Cookie DB 包含所有 cookie（含 HttpOnly）

**对比 OkHttp Interceptor 方式的优势**:
- 不依赖特定的 HTTP 请求被触发（DB 中始终有值）
- 不需要 OkHttp interceptor hook 成功（360加固下常失败）
- 可以一次读取所有 cookie 字段（而非逐个从请求 header 提取）

## 选择决策树

```
需要捕获 HTTP 流量？
├─ YES → 策略 1: OkHttp 动态代理拦截器
│
需要捕获加密密钥？
├─ YES → 策略 2: Cipher Hook
│
需要访问加密数据库？
├─ YES → 策略 3: WCDB/SQLite Hook
│
需要获取 Native 层解密数据？
├─ YES → 策略 4: JNI Native 方法 Hook
│
解密逻辑在 App Java 层？
├─ YES → 策略 5: App 内部方法反射调用
│
需要 PC 端远程控制？
├─ YES → 策略 6: LocalSocket RPC
│        或 策略 7: HTTP 代理服务器
│
需要提取 WebView 的 session/token？
├─ YES → 策略 8: WebView Cookie DB 直读
│        （OkHttp interceptor 失败或 token 不经过 OkHttp 时的首选方案）
```
