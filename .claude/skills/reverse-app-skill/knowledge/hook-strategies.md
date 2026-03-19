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
```
