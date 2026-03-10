package com.yuyang.thshook;

import android.app.Application;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.lang.reflect.Method;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Proxy;
import java.net.ServerSocket;
import java.net.Socket;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;

import top.canyie.pine.Pine;
import top.canyie.pine.PineConfig;
import top.canyie.pine.callback.MethodHook;

public class MainHook {

    private static final String TAG = "THSHook";
    private static volatile boolean hooksInstalled = false;
    private static volatile ClassLoader appClassLoader = null;

    // OkHttpClient 捕获
    // 所有已创建的 OkHttpClient 实例
    private static final List<Object> allClients = java.util.Collections.synchronizedList(new ArrayList<>());
    // interceptor identity hash → OkHttpClient 映射
    private static final ConcurrentHashMap<Integer, Object> interceptorClientMap = new ConcurrentHashMap<>();
    // 域名 → OkHttpClient 映射（从实际请求中学习）
    private static final ConcurrentHashMap<String, Object> domainClients = new ConcurrentHashMap<>();
    // ThreadLocal 在 build() 的 beforeCall/afterCall 之间传递 interceptor ID
    private static final ThreadLocal<Integer> pendingInterceptorId = new ThreadLocal<>();

    // 最新捕获的交易认证参数（从 trade.5ifund.com 请求中提取）
    private static volatile String latestKey1 = null;
    private static volatile String latestKey2 = null;
    private static volatile String latestKey3 = null;
    private static volatile String latestKey4 = null;
    private static volatile String latestKey5 = null;
    private static volatile String latestUserId = null;
    private static volatile String latestSessionId = null;
    private static volatile String latestCookie = null;
    private static volatile long authCaptureTime = 0;

    // 保存最近的 WebView 引用（用于注入 JavaScript）
    private static volatile Object latestWebView = null;

    // 保存购买相关的方法引用和上下文（用于直接调用app的购买方法）
    private static volatile Object buyServiceInstance = null;
    private static volatile Method buyServiceMethod = null;
    private static volatile Object jsBridgeHandler = null;

    // 日志限流
    private static final AtomicInteger httpLogCount = new AtomicInteger(0);
    private static volatile long httpLogWindowStart = 0;
    private static final int HTTP_LOG_LIMIT = 100;

    // 股票数据：捕获的 SQLite 数据库引用
    private static volatile Object stockDatabase = null;
    // 股票账户 fund_key（AES加密）
    private static final String STOCK_FUND_KEY = "7qZ1IO8msos2SsiJgFqWREwrMVnGfXlV974+tulFq0k=";

    // XHR Hook JavaScript - 注入到 WebView 捕获所有 AJAX 请求
    private static final String XHR_HOOK_SCRIPT =
        "(function() {" +
        "    if (window.__XHR_HOOKED__) return;" +
        "    window.__XHR_HOOKED__ = true;" +
        "    " +
        "    var originalOpen = XMLHttpRequest.prototype.open;" +
        "    var originalSend = XMLHttpRequest.prototype.send;" +
        "    var originalSetRequestHeader = XMLHttpRequest.prototype.setRequestHeader;" +
        "    " +
        "    XMLHttpRequest.prototype.open = function(method, url, async, user, password) {" +
        "        this._method = method;" +
        "        this._url = url;" +
        "        this._requestHeaders = {};" +
        "        this._startTime = Date.now();" +
        "        return originalOpen.apply(this, arguments);" +
        "    };" +
        "    " +
        "    XMLHttpRequest.prototype.setRequestHeader = function(header, value) {" +
        "        this._requestHeaders[header] = value;" +
        "        return originalSetRequestHeader.apply(this, arguments);" +
        "    };" +
        "    " +
        "    XMLHttpRequest.prototype.send = function(body) {" +
        "        var xhr = this;" +
        "        " +
        "        var requestInfo = {" +
        "            method: xhr._method," +
        "            url: xhr._url," +
        "            headers: xhr._requestHeaders," +
        "            body: body || null," +
        "            timestamp: xhr._startTime" +
        "        };" +
        "        " +
        "        if (xhr._url && (xhr._url.indexOf('trade.5ifund.com') > -1 || xhr._url.indexOf('/fund/') > -1)) {" +
        "            console.log('[XHR_HOOK_REQUEST] ' + JSON.stringify(requestInfo));" +
        "        }" +
        "        " +
        "        xhr.addEventListener('load', function() {" +
        "            if (xhr._url && (xhr._url.indexOf('trade.5ifund.com') > -1 || xhr._url.indexOf('/fund/') > -1)) {" +
        "                var responseInfo = {" +
        "                    url: xhr._url," +
        "                    status: xhr.status," +
        "                    statusText: xhr.statusText," +
        "                    responseText: xhr.responseText.substring(0, 10000)," +
        "                    responseHeaders: xhr.getAllResponseHeaders()," +
        "                    duration: Date.now() - xhr._startTime" +
        "                };" +
        "                console.log('[XHR_HOOK_RESPONSE] ' + JSON.stringify(responseInfo));" +
        "            }" +
        "        });" +
        "        " +
        "        xhr.addEventListener('error', function() {" +
        "            console.log('[XHR_HOOK_ERROR] ' + xhr._url);" +
        "        });" +
        "        " +
        "        return originalSend.apply(this, arguments);" +
        "    };" +
        "    " +
        "    console.log('[XHR_HOOK] Initialized');" +
        "})();";

    public static void entry(ClassLoader classLoader, String pineSoPath) {
        Log.i(TAG, "MainHook.entry() called");

        try {
            System.load(pineSoPath);
            Log.i(TAG, "libpine.so loaded");

            PineConfig.libLoader = new Pine.LibLoader() {
                @Override public void loadLib() { }
            };
            PineConfig.debug = false;
            PineConfig.debuggable = false;
            PineConfig.antiChecks = true;
            PineConfig.disableHiddenApiPolicy = false;
            PineConfig.disableHiddenApiPolicyForPlatformDomain = false;

            Pine.ensureInitialized();
            Log.i(TAG, "Pine initialized");

            // 直接使用传入的classLoader安装hooks（不等待Application.onCreate）
            installAllHooks(classLoader);

            // Hook Application.onCreate
            Method onCreate = Application.class.getDeclaredMethod("onCreate");
            Pine.hook(onCreate, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    Application app = (Application) callFrame.thisObject;
                    Log.i(TAG, "Application.onCreate: " + app.getClass().getName());
                    installAllHooks(app.getClassLoader());
                }
            });
            Log.i(TAG, "Hook installed on Application.onCreate");

            // 延迟线程直接安装（绕过 360 加固）
            new Thread(() -> {
                try {
                    for (int i = 0; i < 30; i++) {
                        Thread.sleep(500);
                        try {
                            Class<?> atClass = Class.forName("android.app.ActivityThread");
                            Method currentApp = atClass.getDeclaredMethod("currentApplication");
                            Application app = (Application) currentApp.invoke(null);
                            if (app != null) {
                                ClassLoader cl = app.getClassLoader();
                                if (cl != null) {
                                    // 等待 THS 的实际 Application 类或 OkHttp 加载
                                    try {
                                        cl.loadClass("com.hexin.plat.android.App");
                                        Log.i(TAG, "App ready (App class) via ActivityThread after " + (i * 500) + "ms");
                                    } catch (ClassNotFoundException e1) {
                                        // 备选：等待 OkHttp 类加载（因为我们实际需要的是 OkHttp）
                                        cl.loadClass("okhttp3.OkHttpClient");
                                        Log.i(TAG, "App ready (OkHttp class) via ActivityThread after " + (i * 500) + "ms");
                                    }
                                    installAllHooks(cl);
                                    return;
                                }
                            }
                        } catch (ClassNotFoundException e) {
                            // App 类还没加载，继续等
                        } catch (Throwable e) {
                            Log.w(TAG, "Waiting... (" + e.getClass().getSimpleName() + ": " + e.getMessage() + ")");
                        }
                    }
                    Log.e(TAG, "Timeout waiting for App ClassLoader (15s)");
                } catch (Throwable e) {
                    Log.e(TAG, "Delayed hook install failed", e);
                }
            }, "THSHook-Delay").start();

        } catch (Throwable e) {
            Log.e(TAG, "Hook failed", e);
        }
    }

    private static synchronized void installAllHooks(ClassLoader cl) {
        if (hooksInstalled) return;
        hooksInstalled = true;
        appClassLoader = cl;

        Log.i(TAG, "=== installAllHooks start === cl=" + cl);

        // 启用 WebView 调试
        try {
            Class<?> webViewClass = Class.forName("android.webkit.WebView");
            Method setDebug = webViewClass.getDeclaredMethod("setWebContentsDebuggingEnabled", boolean.class);
            setDebug.invoke(null, true);
            Log.i(TAG, "WebView debugging enabled");
        } catch (Throwable e) {
            Log.w(TAG, "WebView debug enable failed: " + e.getMessage());
        }

        try { injectInterceptor(cl); Log.i(TAG, "injectInterceptor done"); }
        catch (Throwable e) { Log.e(TAG, "injectInterceptor failed", e); }

        try { hookHttpURLConnection(); Log.i(TAG, "hookHttpURLConnection done"); }
        catch (Throwable e) { Log.e(TAG, "hookHttpURLConnection failed", e); }

        try { hookWebViewRequests(); Log.i(TAG, "hookWebViewRequests done"); }
        catch (Throwable e) { Log.e(TAG, "hookWebViewRequests failed", e); }

        try { hookJSBridgeNative(); Log.i(TAG, "hookJSBridgeNative done"); }
        catch (Throwable e) { Log.e(TAG, "hookJSBridgeNative failed", e); }

        try { hookSQLiteDatabase(); Log.i(TAG, "hookSQLiteDatabase done"); }
        catch (Throwable e) { Log.e(TAG, "hookSQLiteDatabase failed", e); }

        try { hookTradingSDK(cl); Log.i(TAG, "hookTradingSDK done"); }
        catch (Throwable e) { Log.e(TAG, "hookTradingSDK failed", e); }

        try { hookCipher(); Log.i(TAG, "hookCipher done"); }
        catch (Throwable e) { Log.e(TAG, "hookCipher failed", e); }

        try { hookWTBuyConfirmClient(cl); Log.i(TAG, "hookWTBuyConfirmClient done"); }
        catch (Throwable e) { Log.e(TAG, "hookWTBuyConfirmClient failed", e); }

        try { hookClientRequestHX(cl); Log.i(TAG, "hookClientRequestHX done"); }
        catch (Throwable e) { Log.e(TAG, "hookClientRequestHX failed", e); }

        try { hookActivityAndClicks(); Log.i(TAG, "hookActivityAndClicks done"); }
        catch (Throwable e) { Log.e(TAG, "hookActivityAndClicks failed", e); }

        // 启动代理服务器
        startProxyServer(cl);

        Log.i(TAG, "=== installAllHooks complete ===");
    }

    /**
     * 本地 HTTP 代理服务器，通过 app 的 OkHttpClient 转发请求
     * 端口 18900
     * POST /proxy  {"url":"...","method":"GET|POST","body":"...","content_type":"..."}
     * GET /domains  列出已捕获的 OkHttpClient 域名
     * GET /clients  列出所有已捕获的 OkHttpClient 数量
     */
    private static void startProxyServer(ClassLoader cl) {
        new Thread(() -> {
            try {
                ServerSocket server = new ServerSocket(18900);
                Log.i(TAG, "Proxy server started on port 18900");

                while (true) {
                    try {
                        Socket client = server.accept();
                        new Thread(() -> handleProxyRequest(client, cl)).start();
                    } catch (Throwable e) {
                        Log.e(TAG, "Proxy accept error: " + e.getMessage());
                    }
                }
            } catch (Throwable e) {
                Log.e(TAG, "Proxy server failed to start: " + e.getMessage());
            }
        }, "THSHook-Proxy").start();
    }

    private static void handleProxyRequest(Socket client, ClassLoader cl) {
        try {
            client.setSoTimeout(30000);
            BufferedReader reader = new BufferedReader(new InputStreamReader(client.getInputStream()));
            OutputStream out = client.getOutputStream();

            // 读取 HTTP 请求行
            String requestLine = reader.readLine();
            if (requestLine == null) { client.close(); return; }

            // 读取 headers
            int contentLength = 0;
            String line;
            while ((line = reader.readLine()) != null && !line.isEmpty()) {
                if (line.toLowerCase().startsWith("content-length:")) {
                    contentLength = Integer.parseInt(line.substring(15).trim());
                }
            }

            // 读取 body
            String body = "";
            if (contentLength > 0) {
                char[] buf = new char[contentLength];
                int read = 0;
                while (read < contentLength) {
                    int n = reader.read(buf, read, contentLength - read);
                    if (n == -1) break;
                    read += n;
                }
                body = new String(buf, 0, read);
            }

            Log.i(TAG, "Proxy request: " + requestLine + " body=" + body.substring(0, Math.min(200, body.length())));

            // 调试端点: GET /domains
            if (requestLine.startsWith("GET /domains")) {
                StringBuilder sb = new StringBuilder("{\"domains\":[");
                boolean first = true;
                for (String d : domainClients.keySet()) {
                    if (!first) sb.append(",");
                    sb.append("\"").append(d).append("\"");
                    first = false;
                }
                sb.append("],\"total_clients\":").append(allClients.size());
                sb.append(",\"interceptor_map_size\":").append(interceptorClientMap.size()).append("}");
                sendResponse(out, 200, sb.toString());
                client.close();
                return;
            }

            // 认证端点: GET /auth — 返回最新捕获的交易认证参数
            if (requestLine.startsWith("GET /auth")) {
                StringBuilder sb = new StringBuilder("{");
                sb.append("\"key1\":\"").append(latestKey1 != null ? latestKey1 : "").append("\"");
                sb.append(",\"key2\":\"").append(latestKey2 != null ? latestKey2 : "").append("\"");
                sb.append(",\"key3\":\"").append(latestKey3 != null ? latestKey3 : "").append("\"");
                sb.append(",\"key4\":\"").append(latestKey4 != null ? latestKey4 : "").append("\"");
                sb.append(",\"key5\":\"").append(latestKey5 != null ? latestKey5 : "").append("\"");
                sb.append(",\"userId\":\"").append(latestUserId != null ? latestUserId : "").append("\"");
                sb.append(",\"sessionId\":\"").append(latestSessionId != null ? latestSessionId : "").append("\"");
                sb.append(",\"cookie\":\"").append(latestCookie != null ? latestCookie.replace("\"", "\\\"") : "").append("\"");
                sb.append(",\"capture_time\":").append(authCaptureTime);
                sb.append(",\"available\":").append(latestKey5 != null);
                sb.append("}");
                sendResponse(out, 200, sb.toString());
                client.close();
                return;
            }

            // 基金购买端点: POST /fund/buy_direct — 直接调用app内部购买方法（优先匹配，避免被 /fund/buy 误匹配）
            if (requestLine.startsWith("POST /fund/buy_direct")) {
                String result = directBuyFund(body);
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // 基金购买端点: POST /fund/buy — 通过 WebView 触发购买（已废弃，用户不接受UI自动化）
            if (requestLine.startsWith("POST /fund/buy")) {
                String result = triggerFundBuy(body);
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // 打开基金详情页面: POST /fund/open_detail — 触发WebView创建和JSBridge初始化
            if (requestLine.startsWith("POST /fund/open_detail")) {
                String result = openFundDetail(body);
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // ========== 股票数据查询端点 ==========

            // GET /stock/positions — 查询持仓
            if (requestLine.startsWith("GET /stock/positions")) {
                String result = queryStockPositions();
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // GET /stock/assets — 查询资产
            if (requestLine.startsWith("GET /stock/assets")) {
                String result = queryStockAssets();
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // GET /stock/orders — 查询委托
            if (requestLine.startsWith("GET /stock/orders")) {
                String result = queryStockOrders();
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // GET /stock/history — 查询历史成交
            if (requestLine.startsWith("GET /stock/history")) {
                String result = queryStockHistory();
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // GET /stock/daily — 查询当日成交
            if (requestLine.startsWith("GET /stock/daily")) {
                String result = queryStockDaily();
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // GET /stock/status — 检查数据库状态
            if (requestLine.startsWith("GET /stock/status")) {
                String result = getStockDbStatus();
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // GET /stock/schema?table=xxx — 查询表结构
            if (requestLine.startsWith("GET /stock/schema")) {
                String table = null;
                int qIdx = requestLine.indexOf("?table=");
                if (qIdx != -1) {
                    int endIdx = requestLine.indexOf(" ", qIdx);
                    if (endIdx == -1) endIdx = requestLine.length();
                    table = requestLine.substring(qIdx + 7, endIdx);
                }
                String result = queryTableSchema(table);
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // ========== JSBridge 转发端点 ==========

            // POST /jsbridge — 通过 WebView 调用 JSBridge
            if (requestLine.startsWith("POST /jsbridge")) {
                String result = callJSBridge(body);
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // GET /stock/query?sql=xxx — 执行任意查询（调试用）
            if (requestLine.startsWith("GET /stock/query")) {
                int qIdx = requestLine.indexOf("?sql=");
                if (qIdx == -1) {
                    sendResponse(out, 400, "{\"error\":\"missing sql parameter\"}");
                    client.close();
                    return;
                }
                int endIdx = requestLine.indexOf(" ", qIdx);
                if (endIdx == -1) endIdx = requestLine.length();
                String sql = java.net.URLDecoder.decode(requestLine.substring(qIdx + 5, endIdx), "UTF-8");
                String result = executeStockQuery(sql, null);
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // GET /stock/databases — 列出所有数据库文件
            if (requestLine.startsWith("GET /stock/databases")) {
                String result = listDatabases();
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // GET /stock/opendb?path=xxx — 打开指定数据库并列出表
            if (requestLine.startsWith("GET /stock/opendb")) {
                int qIdx = requestLine.indexOf("?path=");
                if (qIdx == -1) {
                    sendResponse(out, 400, "{\"error\":\"missing path parameter\"}");
                    client.close();
                    return;
                }
                int endIdx = requestLine.indexOf(" ", qIdx);
                if (endIdx == -1) endIdx = requestLine.length();
                String dbPath = java.net.URLDecoder.decode(requestLine.substring(qIdx + 6, endIdx), "UTF-8");
                String result = openAndListTables(dbPath);
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // GET /stock/trade/status — 获取交易 SDK 状态
            if (requestLine.startsWith("GET /stock/trade/status")) {
                String result = getTradeStatus();
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // GET /stock/trade/logs — 获取最近的交易日志
            if (requestLine.startsWith("GET /stock/trade/logs")) {
                String result = getRecentTradeLogs();
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // 调试端点: GET /clients — 列出所有 client 及其 CookieJar 信息
            if (requestLine.startsWith("GET /clients")) {
                StringBuilder sb = new StringBuilder("{\"clients\":[");
                synchronized (allClients) {
                    for (int i = 0; i < allClients.size(); i++) {
                        if (i > 0) sb.append(",");
                        Object c = allClients.get(i);
                        sb.append("{\"index\":").append(i);
                        sb.append(",\"class\":\"").append(c.getClass().getName()).append("\"");
                        // 尝试获取 cookieJar 信息
                        try {
                            Method cookieJarM = c.getClass().getDeclaredMethod("cookieJar");
                            if (!cookieJarM.isAccessible()) cookieJarM.setAccessible(true);
                            Object cj = cookieJarM.invoke(c);
                            sb.append(",\"cookieJar\":\"").append(cj.getClass().getName()).append("\"");
                        } catch (Throwable e) {
                            sb.append(",\"cookieJar\":\"unknown\"");
                        }
                        sb.append("}");
                    }
                }
                sb.append("]}");
                sendResponse(out, 200, sb.toString());
                client.close();
                return;
            }

            // 解析 JSON body
            String targetUrl = extractJsonString(body, "url");
            String method = extractJsonString(body, "method");
            String reqBody = extractJsonString(body, "body");
            String contentType = extractJsonString(body, "content_type");
            String clientIdxStr = extractJsonString(body, "client_index");
            String extraHeadersJson = extractJsonObject(body, "extra_headers");

            if (targetUrl == null || targetUrl.isEmpty()) {
                sendResponse(out, 400, "{\"error\":\"missing url field\"}");
                client.close();
                return;
            }
            if (method == null) method = "GET";

            // 选择 OkHttpClient
            Object okClient = null;

            // 1. 如果指定了 client_index，使用该索引的客户端
            if (clientIdxStr != null) {
                try {
                    int idx = Integer.parseInt(clientIdxStr);
                    if (idx >= 0 && idx < allClients.size()) {
                        okClient = allClients.get(idx);
                        Log.i(TAG, "Proxy using client[" + idx + "]");
                    }
                } catch (Throwable ignored) {}
            }

            // 2. 按域名匹配
            if (okClient == null) {
                String targetDomain = extractDomain(targetUrl);
                if (targetDomain != null) {
                    okClient = domainClients.get(targetDomain);
                    if (okClient != null) {
                        Log.i(TAG, "Proxy using domain-matched client for: " + targetDomain);
                    }
                }
            }

            // 3. 使用第一个捕获的客户端
            if (okClient == null && !allClients.isEmpty()) {
                okClient = allClients.get(0);
                Log.i(TAG, "Proxy using first available client");
            }

            if (okClient == null) {
                sendResponse(out, 503, "{\"error\":\"No OkHttpClient captured yet\"}");
                client.close();
                return;
            }

            // 使用选中的 OkHttpClient 发起请求
            try {
                ClassLoader bcl = okClient.getClass().getClassLoader();
                Class<?> requestClass = bcl.loadClass("okhttp3.Request");
                Class<?> builderClass = bcl.loadClass("okhttp3.Request$Builder");
                Class<?> requestBodyClass = bcl.loadClass("okhttp3.RequestBody");
                Class<?> mediaTypeClass = bcl.loadClass("okhttp3.MediaType");

                // 构建 Request
                Object builder = builderClass.getDeclaredConstructor().newInstance();
                Method urlMethod = builderClass.getDeclaredMethod("url", String.class);
                builder = urlMethod.invoke(builder, targetUrl);

                // 添加额外的 headers
                if (extraHeadersJson != null && !extraHeadersJson.isEmpty()) {
                    Method addHeaderMethod = builderClass.getDeclaredMethod("addHeader", String.class, String.class);
                    // 简单解析 JSON object: {"key1":"value1","key2":"value2"}
                    String inner = extraHeadersJson.trim();
                    if (inner.startsWith("{")) inner = inner.substring(1);
                    if (inner.endsWith("}")) inner = inner.substring(0, inner.length() - 1);
                    String[] pairs = inner.split(",");
                    for (String pair : pairs) {
                        int colonIdx = pair.indexOf(":");
                        if (colonIdx > 0) {
                            String key = pair.substring(0, colonIdx).trim();
                            String value = pair.substring(colonIdx + 1).trim();
                            // 去掉引号
                            if (key.startsWith("\"") && key.endsWith("\"")) key = key.substring(1, key.length() - 1);
                            if (value.startsWith("\"") && value.endsWith("\"")) value = value.substring(1, value.length() - 1);
                            if (!key.isEmpty()) {
                                builder = addHeaderMethod.invoke(builder, key, value);
                                Log.i(TAG, "Proxy added header: " + key + "=" + value);
                            }
                        }
                    }
                }

                // 设置请求体
                if ("POST".equalsIgnoreCase(method) || "PUT".equalsIgnoreCase(method)) {
                    if (contentType == null || contentType.isEmpty()) {
                        contentType = "application/x-www-form-urlencoded";
                    }
                    Object mediaType = mediaTypeClass.getDeclaredMethod("parse", String.class)
                            .invoke(null, contentType);
                    Object requestBodyObj = requestBodyClass.getDeclaredMethod("create", mediaTypeClass, String.class)
                            .invoke(null, mediaType, reqBody != null ? reqBody : "");
                    Method methodM = builderClass.getDeclaredMethod("method", String.class, requestBodyClass);
                    builder = methodM.invoke(builder, method.toUpperCase(), requestBodyObj);
                }

                Method buildMethod = builderClass.getDeclaredMethod("build");
                Object request = buildMethod.invoke(builder);

                // 执行请求
                Method newCallMethod = okClient.getClass().getDeclaredMethod("newCall",
                        bcl.loadClass("okhttp3.Request"));
                Object call = newCallMethod.invoke(okClient, request);
                Method executeMethod = call.getClass().getDeclaredMethod("execute");
                Object response = executeMethod.invoke(call);

                // 读取响应
                int code = (int) response.getClass().getDeclaredMethod("code").invoke(response);
                Object responseBody = response.getClass().getDeclaredMethod("body").invoke(response);
                String responseStr = "";
                if (responseBody != null) {
                    try {
                        responseStr = (String) responseBody.getClass().getDeclaredMethod("string").invoke(responseBody);
                    } catch (Throwable e1) {
                        try {
                            Object source = responseBody.getClass().getMethod("source").invoke(responseBody);
                            Object buffer = bcl.loadClass("okio.Buffer").getDeclaredConstructor().newInstance();
                            Method readAll = source.getClass().getMethod("readAll", bcl.loadClass("okio.Sink"));
                            readAll.invoke(source, buffer);
                            responseStr = (String) buffer.getClass().getMethod("readUtf8").invoke(buffer);
                        } catch (Throwable e2) {
                            try {
                                byte[] bytes = (byte[]) responseBody.getClass().getMethod("bytes").invoke(responseBody);
                                responseStr = new String(bytes, "UTF-8");
                            } catch (Throwable e3) {
                                responseStr = "{\"error\":\"Cannot read response body: " + e3.getMessage() + "\"}";
                            }
                        }
                    }
                }

                Log.i(TAG, "Proxy response: " + code + " len=" + responseStr.length());
                sendResponse(out, code, responseStr);

            } catch (Throwable e) {
                Log.e(TAG, "Proxy request failed: " + e.getMessage());
                sendResponse(out, 500, "{\"error\":\"" + e.getMessage().replace("\"", "'") + "\"}");
            }

            client.close();
        } catch (Throwable e) {
            try { client.close(); } catch (Throwable ignored) {}
        }
    }

    private static void sendResponse(OutputStream out, int code, String body) throws java.io.IOException {
        String status = code == 200 ? "OK" : (code == 503 ? "Service Unavailable" : "Error");
        byte[] bodyBytes = body.getBytes("UTF-8");
        String header = "HTTP/1.1 " + code + " " + status + "\r\n"
                + "Content-Type: application/json; charset=utf-8\r\n"
                + "Content-Length: " + bodyBytes.length + "\r\n"
                + "Access-Control-Allow-Origin: *\r\n"
                + "Connection: close\r\n"
                + "\r\n";
        out.write(header.getBytes("UTF-8"));
        out.write(bodyBytes);
        out.flush();
    }

    private static String extractDomain(String url) {
        try {
            int start = url.indexOf("://");
            if (start == -1) return null;
            start += 3;
            int end = url.indexOf("/", start);
            if (end == -1) end = url.length();
            int portIdx = url.indexOf(":", start);
            if (portIdx != -1 && portIdx < end) end = portIdx;
            return url.substring(start, end);
        } catch (Throwable e) {
            return null;
        }
    }

    private static String extractJsonString(String json, String key) {
        String pattern = "\"" + key + "\"";
        int idx = json.indexOf(pattern);
        if (idx == -1) return null;
        idx = json.indexOf(":", idx + pattern.length());
        if (idx == -1) return null;
        idx++;
        while (idx < json.length() && (json.charAt(idx) == ' ' || json.charAt(idx) == '\t')) idx++;
        if (idx >= json.length()) return null;
        if (json.charAt(idx) == '"') {
            idx++;
            StringBuilder sb = new StringBuilder();
            while (idx < json.length() && json.charAt(idx) != '"') {
                if (json.charAt(idx) == '\\' && idx + 1 < json.length()) {
                    idx++;
                    char c = json.charAt(idx);
                    if (c == 'n') sb.append('\n');
                    else if (c == 't') sb.append('\t');
                    else sb.append(c);
                } else {
                    sb.append(json.charAt(idx));
                }
                idx++;
            }
            return sb.toString();
        }
        return null;
    }

    /**
     * 从 JSON 中提取对象值（用于 extra_headers）
     */
    private static String extractJsonObject(String json, String key) {
        String pattern = "\"" + key + "\"";
        int idx = json.indexOf(pattern);
        if (idx == -1) return null;
        idx = json.indexOf(":", idx + pattern.length());
        if (idx == -1) return null;
        idx++;
        while (idx < json.length() && (json.charAt(idx) == ' ' || json.charAt(idx) == '\t')) idx++;
        if (idx >= json.length()) return null;
        if (json.charAt(idx) == '{') {
            int braceCount = 1;
            int start = idx;
            idx++;
            while (idx < json.length() && braceCount > 0) {
                char c = json.charAt(idx);
                if (c == '{') braceCount++;
                else if (c == '}') braceCount--;
                idx++;
            }
            return json.substring(start, idx);
        }
        return null;
    }

    // ========== 股票数据查询方法 ==========

    /**
     * 获取股票数据库实例
     */
    private static Object getStockDatabase() {
        // 首选数据库路径
        String XCS2_DB_PATH = "/data/data/com.hexin.plat.android/databases/xcs2.db";

        // 检查现有数据库是否正确
        if (stockDatabase != null) {
            try {
                Method isOpen = stockDatabase.getClass().getMethod("isOpen");
                if ((boolean) isOpen.invoke(stockDatabase)) {
                    // 检查是否是 xcs2.db
                    Method getPath = stockDatabase.getClass().getMethod("getPath");
                    String path = (String) getPath.invoke(stockDatabase);
                    if (path != null && path.contains("xcs2")) {
                        return stockDatabase;
                    }
                    // 不是 xcs2.db，关闭它并重新打开正确的数据库
                    Log.i(TAG, "Current database is not xcs2.db, reopening...");
                }
            } catch (Throwable ignored) {}
        }

        // 尝试打开 xcs2.db
        try {
            Class<?> sqliteClass = Class.forName("android.database.sqlite.SQLiteDatabase");
            Method openDatabase = sqliteClass.getMethod("openDatabase",
                String.class,
                Class.forName("android.database.sqlite.SQLiteDatabase$CursorFactory"),
                int.class
            );

            java.io.File dbFile = new java.io.File(XCS2_DB_PATH);
            if (dbFile.exists()) {
                // OPEN_READONLY = 1
                Object db = openDatabase.invoke(null, XCS2_DB_PATH, null, 1);
                if (db != null) {
                    Log.i(TAG, "Stock database opened: " + XCS2_DB_PATH);
                    stockDatabase = db;
                    return db;
                }
            } else {
                Log.w(TAG, "xcs2.db does not exist yet");
            }
        } catch (Throwable e) {
            Log.e(TAG, "getStockDatabase failed: " + e.getMessage());
        }

        return null;
    }

    /**
     * 执行 SQL 查询并返回 JSON 结果
     */
    private static String executeStockQuery(String sql, String[] args) {
        Object db = getStockDatabase();
        if (db == null) {
            return "{\"error\":\"Database not available\",\"hint\":\"请先在App中打开持仓页面\"}";
        }

        try {
            Method rawQuery = db.getClass().getMethod("rawQuery", String.class, String[].class);
            Object cursor = rawQuery.invoke(db, sql, args);

            if (cursor == null) {
                return "{\"error\":\"Query returned null cursor\"}";
            }

            // 获取列名
            Method getColumnNames = cursor.getClass().getMethod("getColumnNames");
            String[] columns = (String[]) getColumnNames.invoke(cursor);

            // 遍历结果
            Method moveToNext = cursor.getClass().getMethod("moveToNext");
            Method getString = cursor.getClass().getMethod("getString", int.class);
            Method getColumnIndex = cursor.getClass().getMethod("getColumnIndex", String.class);
            Method getType = cursor.getClass().getMethod("getType", int.class);
            Method getDouble = cursor.getClass().getMethod("getDouble", int.class);
            Method getLong = cursor.getClass().getMethod("getLong", int.class);

            StringBuilder sb = new StringBuilder();
            sb.append("{\"columns\":").append(toJsonArray(columns));
            sb.append(",\"data\":[");

            boolean first = true;
            int rowCount = 0;
            while ((boolean) moveToNext.invoke(cursor) && rowCount < 500) {
                if (!first) sb.append(",");
                first = false;
                sb.append("{");

                boolean firstCol = true;
                for (String col : columns) {
                    if (!firstCol) sb.append(",");
                    firstCol = false;

                    int idx = (int) getColumnIndex.invoke(cursor, col);
                    int type = (int) getType.invoke(cursor, idx);

                    sb.append("\"").append(escapeJson(col)).append("\":");

                    // type: 0=NULL, 1=INTEGER, 2=FLOAT, 3=STRING, 4=BLOB
                    if (type == 0) {
                        sb.append("null");
                    } else if (type == 1) {
                        sb.append(getLong.invoke(cursor, idx));
                    } else if (type == 2) {
                        sb.append(getDouble.invoke(cursor, idx));
                    } else {
                        String val = (String) getString.invoke(cursor, idx);
                        if (val == null) {
                            sb.append("null");
                        } else {
                            sb.append("\"").append(escapeJson(val)).append("\"");
                        }
                    }
                }
                sb.append("}");
                rowCount++;
            }

            sb.append("],\"row_count\":").append(rowCount).append("}");

            // 关闭 cursor
            Method close = cursor.getClass().getMethod("close");
            close.invoke(cursor);

            return sb.toString();
        } catch (Throwable e) {
            Log.e(TAG, "executeStockQuery failed: " + e.getMessage());
            return "{\"error\":\"" + escapeJson(e.getMessage()) + "\"}";
        }
    }

    /**
     * 查询持仓
     */
    private static String queryStockPositions() {
        String sql = "SELECT * FROM stock_position WHERE fund_key = ?";
        return executeStockQuery(sql, new String[]{ STOCK_FUND_KEY });
    }

    /**
     * 查询资产
     */
    private static String queryStockAssets() {
        // account_d 表包含资金信息
        String sql = "SELECT * FROM account_d WHERE fund_key = ?";
        return executeStockQuery(sql, new String[]{ STOCK_FUND_KEY });
    }

    /**
     * 查询委托
     */
    private static String queryStockOrders() {
        String sql = "SELECT * FROM stock_entrust WHERE fund_key = ? ORDER BY entrust_date DESC, entrust_time DESC";
        return executeStockQuery(sql, new String[]{ STOCK_FUND_KEY });
    }

    /**
     * 查询历史成交
     */
    private static String queryStockHistory() {
        String sql = "SELECT * FROM stock_history WHERE fund_key = ? ORDER BY trans_date DESC";
        return executeStockQuery(sql, new String[]{ STOCK_FUND_KEY });
    }

    /**
     * 查询当日成交
     */
    private static String queryStockDaily() {
        String sql = "SELECT * FROM daily_trans WHERE fund_key = ?";
        return executeStockQuery(sql, new String[]{ STOCK_FUND_KEY });
    }

    /**
     * 获取数据库状态
     */
    private static String getStockDbStatus() {
        StringBuilder sb = new StringBuilder("{");
        sb.append("\"fund_key\":\"").append(STOCK_FUND_KEY).append("\"");

        Object db = getStockDatabase();
        if (db == null) {
            sb.append(",\"database_available\":false");
            sb.append(",\"hint\":\"请在App中打开持仓页面后重试\"");

            // 列出数据库目录内容
            try {
                java.io.File dbDir = new java.io.File("/data/data/com.hexin.plat.android/databases/");
                if (dbDir.exists() && dbDir.isDirectory()) {
                    String[] files = dbDir.list();
                    if (files != null) {
                        sb.append(",\"database_files\":").append(toJsonArray(files));
                    }
                }
            } catch (Throwable ignored) {}
        } else {
            sb.append(",\"database_available\":true");

            // 列出表
            try {
                String tablesSql = "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name";
                Method rawQuery = db.getClass().getMethod("rawQuery", String.class, String[].class);
                Object cursor = rawQuery.invoke(db, tablesSql, null);

                if (cursor != null) {
                    Method moveToNext = cursor.getClass().getMethod("moveToNext");
                    Method getString = cursor.getClass().getMethod("getString", int.class);

                    sb.append(",\"tables\":[");
                    boolean first = true;
                    while ((boolean) moveToNext.invoke(cursor)) {
                        if (!first) sb.append(",");
                        first = false;
                        String tableName = (String) getString.invoke(cursor, 0);
                        sb.append("\"").append(escapeJson(tableName)).append("\"");
                    }
                    sb.append("]");

                    Method close = cursor.getClass().getMethod("close");
                    close.invoke(cursor);
                }
            } catch (Throwable e) {
                sb.append(",\"tables_error\":\"").append(escapeJson(e.getMessage())).append("\"");
            }
        }

        sb.append("}");
        return sb.toString();
    }

    /**
     * 转换字符串数组为 JSON 数组
     */
    private static String toJsonArray(String[] arr) {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < arr.length; i++) {
            if (i > 0) sb.append(",");
            sb.append("\"").append(escapeJson(arr[i])).append("\"");
        }
        sb.append("]");
        return sb.toString();
    }

    /**
     * 转义 JSON 字符串
     */
    private static String escapeJson(String s) {
        if (s == null) return "";
        return s.replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
                .replace("\t", "\\t");
    }

    /**
     * 查询表结构
     */
    private static String queryTableSchema(String tableName) {
        Object db = getStockDatabase();
        if (db == null) {
            return "{\"error\":\"Database not available\"}";
        }

        try {
            String sql;
            if (tableName == null || tableName.isEmpty()) {
                // 列出所有表
                sql = "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name";
            } else {
                // 查询特定表结构
                sql = "PRAGMA table_info(" + tableName + ")";
            }

            return executeStockQuery(sql, null);
        } catch (Throwable e) {
            return "{\"error\":\"" + escapeJson(e.getMessage()) + "\"}";
        }
    }

    /**
     * 列出所有数据库文件
     */
    private static String listDatabases() {
        StringBuilder sb = new StringBuilder("{\"databases\":[");

        try {
            java.io.File dbDir = new java.io.File("/data/data/com.hexin.plat.android/databases/");
            if (dbDir.exists() && dbDir.isDirectory()) {
                java.io.File[] files = dbDir.listFiles();
                boolean first = true;
                if (files != null) {
                    for (java.io.File f : files) {
                        if (!first) sb.append(",");
                        first = false;
                        sb.append("{");
                        sb.append("\"name\":\"").append(escapeJson(f.getName())).append("\"");
                        sb.append(",\"size\":").append(f.length());
                        sb.append(",\"path\":\"").append(escapeJson(f.getAbsolutePath())).append("\"");
                        sb.append("}");
                    }
                }
            }
        } catch (Throwable e) {
            sb.append("{\"error\":\"").append(escapeJson(e.getMessage())).append("\"}");
        }

        sb.append("]}");
        return sb.toString();
    }

    /**
     * 打开指定数据库并列出表
     */
    private static String openAndListTables(String dbPath) {
        StringBuilder sb = new StringBuilder("{");
        sb.append("\"path\":\"").append(escapeJson(dbPath)).append("\"");

        try {
            Class<?> sqliteClass = Class.forName("android.database.sqlite.SQLiteDatabase");
            Method openDatabase = sqliteClass.getMethod("openDatabase",
                String.class,
                Class.forName("android.database.sqlite.SQLiteDatabase$CursorFactory"),
                int.class
            );

            // OPEN_READONLY = 1
            Object db = openDatabase.invoke(null, dbPath, null, 1);
            if (db == null) {
                sb.append(",\"error\":\"Failed to open database\"");
                sb.append("}");
                return sb.toString();
            }

            // 设置为当前股票数据库
            stockDatabase = db;
            sb.append(",\"database_set\":true");

            // 查询表列表
            Method rawQuery = db.getClass().getMethod("rawQuery", String.class, String[].class);
            Object cursor = rawQuery.invoke(db, "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name", null);

            if (cursor != null) {
                Method moveToNext = cursor.getClass().getMethod("moveToNext");
                Method getString = cursor.getClass().getMethod("getString", int.class);

                sb.append(",\"tables\":[");
                boolean first = true;
                while ((boolean) moveToNext.invoke(cursor)) {
                    if (!first) sb.append(",");
                    first = false;
                    String tableName = (String) getString.invoke(cursor, 0);
                    String createSql = (String) getString.invoke(cursor, 1);
                    sb.append("{");
                    sb.append("\"name\":\"").append(escapeJson(tableName)).append("\"");
                    if (createSql != null) {
                        sb.append(",\"sql\":\"").append(escapeJson(createSql)).append("\"");
                    }
                    sb.append("}");
                }
                sb.append("]");

                Method close = cursor.getClass().getMethod("close");
                close.invoke(cursor);
            }
        } catch (Throwable e) {
            sb.append(",\"error\":\"").append(escapeJson(e.getMessage())).append("\"");
        }

        sb.append("}");
        return sb.toString();
    }

    /**
     * Hook HttpURLConnection 捕获非OkHttp的HTTP请求（券商SDK可能使用）
     */
    private static void hookHttpURLConnection() throws Throwable {
        // Hook URL.openConnection()
        Class<?> urlClass = Class.forName("java.net.URL");
        Method openConn = urlClass.getDeclaredMethod("openConnection");
        Pine.hook(openConn, new MethodHook() {
            @Override
            public void afterCall(Pine.CallFrame callFrame) {
                try {
                    Object conn = callFrame.getResult();
                    if (conn != null) {
                        java.net.URL url = (java.net.URL) callFrame.thisObject;
                        String urlStr = url.toString();
                        // 过滤掉静态资源
                        if (!urlStr.endsWith(".js") && !urlStr.endsWith(".css") && !urlStr.endsWith(".png")
                            && !urlStr.endsWith(".jpg") && !urlStr.endsWith(".gif") && !urlStr.endsWith(".ico")
                            && !urlStr.contains("apm.") && !urlStr.contains("stat.")) {
                            Log.i(TAG, "URLConnection.open → " + urlStr);
                        }
                    }
                } catch (Throwable e) { /* ignore */ }
            }
        });

        // Hook HttpURLConnection.getInputStream() - 捕获响应
        try {
            Class<?> httpConnClass = Class.forName("java.net.HttpURLConnection");
            Method getInput = httpConnClass.getDeclaredMethod("getInputStream");
            Pine.hook(getInput, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    try {
                        java.net.HttpURLConnection conn = (java.net.HttpURLConnection) callFrame.thisObject;
                        String urlStr = conn.getURL().toString();
                        int code = conn.getResponseCode();
                        // 只记录交易相关的请求
                        if (urlStr.contains("trade") || urlStr.contains("stock") || urlStr.contains("broker")
                            || urlStr.contains("order") || urlStr.contains("position") || urlStr.contains("asset")
                            || urlStr.contains("ccsc") || urlStr.contains("川财") || urlStr.contains("hexin")) {
                            Log.i(TAG, "HttpURLConnection ← " + code + " " + urlStr);
                        }
                    } catch (Throwable e) { /* ignore */ }
                }
            });
        } catch (Throwable e) {
            Log.w(TAG, "HttpURLConnection.getInputStream hook failed: " + e.getMessage());
        }

        // Hook Socket.connect() - 捕获TCP连接（股票交易可能用私有协议）
        try {
            Class<?> socketClass = Class.forName("java.net.Socket");
            for (Method m : socketClass.getDeclaredMethods()) {
                if ("connect".equals(m.getName())) {
                    Pine.hook(m, new MethodHook() {
                        @Override
                        public void beforeCall(Pine.CallFrame callFrame) {
                            try {
                                Object addr = callFrame.args[0];
                                if (addr != null) {
                                    String addrStr = addr.toString();
                                    // 过滤掉已知的行情端口，只记录可能的交易端口
                                    if (!addrStr.contains(":443") && !addrStr.contains(":80")
                                        && !addrStr.contains(":8080") && !addrStr.contains(":27042")) {
                                        Log.i(TAG, "Socket.connect → " + addrStr);
                                    }
                                }
                            } catch (Throwable e) { /* ignore */ }
                        }
                    });
                    break;
                }
            }
        } catch (Throwable e) {
            Log.w(TAG, "Socket.connect hook failed: " + e.getMessage());
        }

        Log.i(TAG, "HttpURLConnection/Socket hooks installed");
    }

    /**
     * Hook WebViewClient.shouldInterceptRequest 捕获 WebView 内所有网络请求 URL
     */
    private static void hookWebViewRequests() throws Throwable {
        Class<?> webViewClass = Class.forName("android.webkit.WebView");

        Method loadUrl = webViewClass.getDeclaredMethod("loadUrl", String.class);
        Pine.hook(loadUrl, new MethodHook() {
            @Override
            public void beforeCall(Pine.CallFrame callFrame) {
                final Object webView = callFrame.thisObject;
                String url = (String) callFrame.args[0];
                Log.i(TAG, "WebView.loadUrl → " + url);

                // 保存 WebView 引用
                if (webView != null) {
                    latestWebView = webView;
                }

                // 如果是 fund 页面，延迟注入 XHR Hook
                if (url != null && (url.contains("trade.5ifund.com") || url.contains("/fund/"))) {
                    new Handler(Looper.getMainLooper()).postDelayed(new Runnable() {
                        @Override
                        public void run() {
                            try {
                                Log.i(TAG, "Injecting XHR Hook into fund page...");
                                Method evalJs = webView.getClass().getDeclaredMethod("evaluateJavascript",
                                        String.class, Class.forName("android.webkit.ValueCallback"));
                                evalJs.invoke(webView, XHR_HOOK_SCRIPT, null);
                                Log.i(TAG, "✓ XHR Hook injected");
                            } catch (Throwable e) {
                                Log.e(TAG, "Failed to inject XHR Hook", e);
                            }
                        }
                    }, 1000); // 延迟 1 秒等待页面加载
                }
            }
        });

        // Hook postUrl
        try {
            Method postUrl = webViewClass.getDeclaredMethod("postUrl", String.class, byte[].class);
            Pine.hook(postUrl, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    String url = (String) callFrame.args[0];
                    Log.i(TAG, "WebView.postUrl → " + url);
                }
            });
        } catch (Throwable e) { /* ignore */ }

        // Hook loadData - 可能基金页面用这个加载
        try {
            Method loadData = webViewClass.getDeclaredMethod("loadData", String.class, String.class, String.class);
            Pine.hook(loadData, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    final Object webView = callFrame.thisObject;
                    String data = (String) callFrame.args[0];
                    Log.i(TAG, "WebView.loadData → [" + data.length() + " chars]");

                    if (webView != null) {
                        latestWebView = webView;
                    }

                    // 延迟注入 XHR Hook（基金页面可能在这里加载）
                    new Handler(Looper.getMainLooper()).postDelayed(new Runnable() {
                        @Override
                        public void run() {
                            try {
                                Log.i(TAG, "Injecting XHR Hook (after loadData)...");
                                Method evalJs = webView.getClass().getDeclaredMethod("evaluateJavascript",
                                        String.class, Class.forName("android.webkit.ValueCallback"));
                                evalJs.invoke(webView, XHR_HOOK_SCRIPT, null);
                                Log.i(TAG, "✓ XHR Hook injected (loadData)");
                            } catch (Throwable e) {
                                Log.e(TAG, "Failed to inject XHR Hook (loadData)", e);
                            }
                        }
                    }, 2000); // 延迟 2 秒
                }
            });
        } catch (Throwable e) { /* ignore */ }

        // Hook loadDataWithBaseURL - 更常用的方法
        try {
            Method loadDataWithBaseURL = webViewClass.getDeclaredMethod("loadDataWithBaseURL",
                    String.class, String.class, String.class, String.class, String.class);
            Pine.hook(loadDataWithBaseURL, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    final Object webView = callFrame.thisObject;
                    String baseUrl = (String) callFrame.args[0];
                    String data = (String) callFrame.args[1];
                    Log.i(TAG, "WebView.loadDataWithBaseURL → baseUrl=" + baseUrl + " [" + data.length() + " chars]");

                    if (webView != null) {
                        latestWebView = webView;
                    }

                    // 延迟注入 XHR Hook
                    new Handler(Looper.getMainLooper()).postDelayed(new Runnable() {
                        @Override
                        public void run() {
                            try {
                                Log.i(TAG, "Injecting XHR Hook (after loadDataWithBaseURL)...");
                                Method evalJs = webView.getClass().getDeclaredMethod("evaluateJavascript",
                                        String.class, Class.forName("android.webkit.ValueCallback"));
                                evalJs.invoke(webView, XHR_HOOK_SCRIPT, null);
                                Log.i(TAG, "✓ XHR Hook injected (loadDataWithBaseURL)");
                            } catch (Throwable e) {
                                Log.e(TAG, "Failed to inject XHR Hook (loadDataWithBaseURL)", e);
                            }
                        }
                    }, 2000); // 延迟 2 秒
                }
            });
        } catch (Throwable e) { /* ignore */ }

        try {
            Method evalJs = webViewClass.getDeclaredMethod("evaluateJavascript", String.class,
                    Class.forName("android.webkit.ValueCallback"));
            Pine.hook(evalJs, new MethodHook() {
                private final AtomicInteger evalCounter = new AtomicInteger(0);
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    // 保存 WebView 引用
                    Object webView = callFrame.thisObject;
                    if (webView != null) {
                        latestWebView = webView;
                    }

                    String js = (String) callFrame.args[0];
                    if (js.length() < 500) {
                        Log.i(TAG, "WebView.evalJS → " + js);
                    } else {
                        int idx = evalCounter.incrementAndGet();
                        Log.i(TAG, "WebView.evalJS #" + idx + " [" + js.length() + " chars] START");
                        for (int i = 0; i < js.length(); i += 3000) {
                            int end = Math.min(i + 3000, js.length());
                            Log.i(TAG, "WebView.evalJS #" + idx + " [" + i + "-" + end + "] " + js.substring(i, end));
                        }
                        Log.i(TAG, "WebView.evalJS #" + idx + " END");
                    }
                }
            });
        } catch (Throwable e) { /* ignore */ }

        Class<?> webViewClientClass = Class.forName("android.webkit.WebViewClient");
        Class<?> webResourceRequestClass = Class.forName("android.webkit.WebResourceRequest");
        Method shouldIntercept = webViewClientClass.getDeclaredMethod(
                "shouldInterceptRequest", webViewClass, webResourceRequestClass);
        Pine.hook(shouldIntercept, new MethodHook() {
            @Override
            public void beforeCall(Pine.CallFrame callFrame) {
                try {
                    Object request = callFrame.args[1];
                    Method getUrl = request.getClass().getMethod("getUrl");
                    Object uri = getUrl.invoke(request);
                    String url = uri.toString();
                    if (!url.endsWith(".js") && !url.endsWith(".css") && !url.endsWith(".png")
                            && !url.endsWith(".jpg") && !url.endsWith(".gif") && !url.endsWith(".woff")
                            && !url.endsWith(".ttf") && !url.contains(".ico")
                            && !url.startsWith("data:")) {
                        Log.i(TAG, "WebView.intercept → " + url);
                    }
                } catch (Throwable e) { /* ignore */ }
            }
        });
    }

    /**
     * 注入 OkHttp Interceptor，捕获所有 HTTP 请求和响应
     */
    private static void injectInterceptor(ClassLoader cl) throws Throwable {
        Class<?> builderClass = cl.loadClass("okhttp3.OkHttpClient$Builder");
        Method buildMethod = builderClass.getDeclaredMethod("build");

        Pine.hook(buildMethod, new MethodHook() {
            @Override
            public void beforeCall(Pine.CallFrame callFrame) {
                try {
                    Object builder = callFrame.thisObject;
                    ClassLoader bcl = builder.getClass().getClassLoader();

                    Class<?> interceptorClass = bcl.loadClass("okhttp3.Interceptor");
                    Class<?> chainClass = bcl.loadClass("okhttp3.Interceptor$Chain");

                    // 创建代理 Interceptor
                    Object interceptor = Proxy.newProxyInstance(
                        bcl,
                        new Class<?>[]{ interceptorClass },
                        new InterceptorHandler(chainClass)
                    );

                    // 保存 interceptor ID 到 ThreadLocal
                    pendingInterceptorId.set(System.identityHashCode(interceptor));

                    Method addInterceptor = builder.getClass().getDeclaredMethod("addInterceptor", interceptorClass);
                    addInterceptor.invoke(builder, interceptor);

                    Log.i(TAG, "Interceptor injected into OkHttpClient.Builder");
                } catch (Throwable e) {
                    Log.e(TAG, "Failed to inject interceptor: " + e.getMessage());
                }
            }

            @Override
            public void afterCall(Pine.CallFrame callFrame) {
                // 捕获 OkHttpClient 实例并关联到 interceptor
                Object client = callFrame.getResult();
                if (client != null) {
                    allClients.add(client);
                    Integer interceptorId = pendingInterceptorId.get();
                    if (interceptorId != null) {
                        interceptorClientMap.put(interceptorId, client);
                        pendingInterceptorId.remove();
                    }
                    Log.i(TAG, "OkHttpClient captured #" + allClients.size());
                }
            }
        });

        Log.i(TAG, "OkHttp interceptor hook installed");
    }

    /**
     * OkHttp Interceptor 处理器
     */
    static class InterceptorHandler implements InvocationHandler {
        private final Class<?> chainClass;

        InterceptorHandler(Class<?> chainClass) {
            this.chainClass = chainClass;
        }

        @Override
        public Object invoke(Object proxy, Method method, Object[] args) throws Throwable {
            if (!"intercept".equals(method.getName())) {
                if ("toString".equals(method.getName())) return "THSHook-Interceptor";
                if ("hashCode".equals(method.getName())) return System.identityHashCode(proxy);
                if ("equals".equals(method.getName())) return proxy == args[0];
                return null;
            }

            Object chain = args[0];

            // 获取 request
            Method requestMethod = chainClass.getDeclaredMethod("request");
            Object request = requestMethod.invoke(chain);

            Object url = request.getClass().getDeclaredMethod("url").invoke(request);
            String urlStr = url.toString();
            String httpMethod = (String) request.getClass().getDeclaredMethod("method").invoke(request);

            // 捕获域名特定的 OkHttpClient
            // 当 interceptor 处理请求时，通过 interceptor identity → client 映射找到对应的 OkHttpClient
            String domain = extractDomain(urlStr);
            if (domain != null && !domainClients.containsKey(domain)) {
                int myId = System.identityHashCode(proxy);
                Object myClient = interceptorClientMap.get(myId);
                if (myClient != null) {
                    domainClients.put(domain, myClient);
                    Log.i(TAG, "Domain client mapped: " + domain + " → client#" + allClients.indexOf(myClient));
                }
            }

            // 从 trade.5ifund.com 请求中提取 auth 参数
            if (urlStr.contains("trade.5ifund.com")) {
                try {
                    // 如果URL包含 key5 参数，提取它
                    if (urlStr.contains("key5=")) {
                        int k5Start = urlStr.indexOf("key5=") + 5;
                        int k5End = urlStr.indexOf("&", k5Start);
                        if (k5End == -1) k5End = urlStr.length();
                        String key5Value = urlStr.substring(k5Start, k5End);
                        if (key5Value.length() > 10) {
                            latestKey5 = key5Value;
                            authCaptureTime = System.currentTimeMillis();
                            Log.i(TAG, "Auth captured from URL: key5=" + key5Value.substring(0, 20) + "...");
                        }
                    }

                    // 无论是否有 key5，都尝试提取 headers（因为 key5 可能通过 cipher 捕获）
                    Object headers = request.getClass().getDeclaredMethod("headers").invoke(request);
                    String headerStr = headers.toString();

                    // 提取 Cookie
                    int cookieIdx = headerStr.toLowerCase().indexOf("cookie:");
                    if (cookieIdx != -1) {
                        int valStart = cookieIdx + 7;
                        int valEnd = headerStr.indexOf("\n", valStart);
                        if (valEnd == -1) valEnd = headerStr.length();
                        String newCookie = headerStr.substring(valStart, valEnd).trim();
                        if (newCookie.length() > 0) {
                            latestCookie = newCookie;
                            Log.i(TAG, "Cookie captured from trade.5ifund.com request, len=" + latestCookie.length());
                        }
                    }

                    // 提取 key1-key4, userId, sessionId
                    String[] authKeys = {"key1", "key2", "key3", "key4", "userId", "sessionId"};
                    for (String key : authKeys) {
                        int keyIdx = headerStr.indexOf(key + ":");
                        if (keyIdx != -1) {
                            int valStart = keyIdx + key.length() + 1;
                            int valEnd = headerStr.indexOf("\n", valStart);
                            if (valEnd == -1) valEnd = headerStr.length();
                            String value = headerStr.substring(valStart, valEnd).trim();
                            if (value.length() > 0) {
                                switch (key) {
                                    case "key1": latestKey1 = value; break;
                                    case "key2": latestKey2 = value; break;
                                    case "key3": latestKey3 = value; break;
                                    case "key4": latestKey4 = value; break;
                                    case "userId": latestUserId = value; break;
                                    case "sessionId": latestSessionId = value; break;
                                }
                                Log.i(TAG, key + " captured: " + (value.length() > 20 ? value.substring(0, 20) + "..." : value));
                            }
                        }
                    }
                } catch (Throwable e) {
                    Log.w(TAG, "Auth capture failed: " + e.getMessage());
                }
            }

            // 日志限流
            long now = System.currentTimeMillis();
            if (now - httpLogWindowStart > 10000) {
                httpLogCount.set(0);
                httpLogWindowStart = now;
            }
            boolean shouldLog = httpLogCount.incrementAndGet() <= HTTP_LOG_LIMIT;

            if (shouldLog) {
                Log.i(TAG, "→ " + httpMethod + " " + urlStr);

                // 记录请求头
                try {
                    Object headers = request.getClass().getDeclaredMethod("headers").invoke(request);
                    String headerStr = headers.toString();
                    if (urlStr.contains("trade.5ifund.com")) {
                        for (int si = 0; si < headerStr.length(); si += 3000) {
                            int end = Math.min(si + 3000, headerStr.length());
                            Log.i(TAG, "  ReqHeaders[" + si + "-" + end + "]: " + headerStr.substring(si, end));
                        }
                    } else {
                        if (headerStr.length() > 500) headerStr = headerStr.substring(0, 500) + "...";
                        Log.i(TAG, "  ReqHeaders: " + headerStr);
                    }
                } catch (Throwable ignored) {}

                // 记录请求体
                try {
                    Object body = request.getClass().getDeclaredMethod("body").invoke(request);
                    if (body != null) {
                        String bodyType = body.getClass().getName();
                        Log.i(TAG, "  ReqBodyType: " + bodyType);
                        try {
                            if (bodyType.contains("FormBody")) {
                                Method sizeM = body.getClass().getDeclaredMethod("size");
                                int size = (int) sizeM.invoke(body);
                                StringBuilder sb = new StringBuilder();
                                Method nameM = body.getClass().getDeclaredMethod("name", int.class);
                                Method valueM = body.getClass().getDeclaredMethod("value", int.class);
                                for (int i = 0; i < Math.min(size, 20); i++) {
                                    if (sb.length() > 0) sb.append("&");
                                    sb.append(nameM.invoke(body, i)).append("=").append(valueM.invoke(body, i));
                                }
                                Log.i(TAG, "  ReqBody: " + sb.toString());
                            } else {
                                ClassLoader bcl = body.getClass().getClassLoader();
                                Class<?> bufferClass = bcl.loadClass("okio.Buffer");
                                Object buffer = bufferClass.getDeclaredConstructor().newInstance();
                                Method writeTo = body.getClass().getMethod("writeTo", bcl.loadClass("okio.BufferedSink"));
                                writeTo.invoke(body, buffer);
                                String bodyStr = (String) buffer.getClass().getMethod("readUtf8").invoke(buffer);
                                if (bodyStr != null && bodyStr.length() > 0) {
                                    if (bodyStr.length() > 1000) bodyStr = bodyStr.substring(0, 1000) + "...";
                                    Log.i(TAG, "  ReqBody: " + bodyStr);
                                }
                            }
                        } catch (Throwable ignored) {}
                    }
                } catch (Throwable ignored) {}
            }

            // 执行请求
            Method proceedMethod = chainClass.getDeclaredMethod("proceed", request.getClass().getClassLoader().loadClass("okhttp3.Request"));
            Object response = proceedMethod.invoke(chain, request);

            if (shouldLog) {
                try {
                    int code = (int) response.getClass().getDeclaredMethod("code").invoke(response);
                    Log.i(TAG, "← " + code + " " + urlStr);

                    Object responseBody = response.getClass().getDeclaredMethod("body").invoke(response);
                    if (responseBody != null) {
                        Object contentType = responseBody.getClass().getDeclaredMethod("contentType").invoke(responseBody);
                        String ctStr = contentType != null ? contentType.toString() : "";

                        if (ctStr.contains("json") || ctStr.contains("text") || ctStr.contains("html") || ctStr.contains("xml")) {
                            try {
                                Object source = responseBody.getClass().getMethod("source").invoke(responseBody);
                                if (source != null) {
                                    try {
                                        Method reqBytes = source.getClass().getMethod("request", long.class);
                                        reqBytes.invoke(source, 65536L);
                                    } catch (Throwable ignored) {}

                                    Method bufferMethod = null;
                                    try { bufferMethod = source.getClass().getMethod("getBuffer"); }
                                    catch (Throwable e1) {
                                        try { bufferMethod = source.getClass().getMethod("buffer"); }
                                        catch (Throwable e2) {}
                                    }
                                    if (bufferMethod != null) {
                                        Object buffer = bufferMethod.invoke(source);
                                        Object cloned = buffer.getClass().getMethod("clone").invoke(buffer);
                                        String bodyStr = (String) cloned.getClass().getMethod("readUtf8").invoke(cloned);
                                        if (bodyStr != null && bodyStr.length() > 0) {
                                            if (bodyStr.length() > 2000) bodyStr = bodyStr.substring(0, 2000) + "...[truncated]";
                                            Log.i(TAG, "  RespBody: " + bodyStr);
                                        }
                                    }
                                }
                            } catch (Throwable e) {
                                Log.w(TAG, "  RespBody read failed: " + e.getMessage());
                            }
                        } else {
                            Log.i(TAG, "  RespType: " + ctStr + " (binary, skipped)");
                        }
                    }
                } catch (Throwable e) {
                    Log.w(TAG, "  Response log failed: " + e.getMessage());
                }
            }

            return response;
        }
    }

    /**
     * Hook WebView.addJavascriptInterface 发现 JS-Java 桥接对象
     * 并监控 @JavascriptInterface 方法调用
     */
    private static void hookJSBridgeNative() throws Throwable {
        Class<?> webViewClass = Class.forName("android.webkit.WebView");
        Method addJsInterface = webViewClass.getDeclaredMethod("addJavascriptInterface", Object.class, String.class);

        Pine.hook(addJsInterface, new MethodHook() {
            @Override
            public void beforeCall(Pine.CallFrame callFrame) {
                try {
                    Object jsInterface = callFrame.args[0];
                    String name = (String) callFrame.args[1];
                    String className = jsInterface.getClass().getName();
                    Log.i(TAG, "WebView.addJavascriptInterface: name=" + name + " class=" + className);

                    // 如果是 ClientRequestHX，深度 Hook 所有方法
                    if (className.contains("ClientRequestHX")) {
                        Log.i(TAG, "=== Detected ClientRequestHX! Deep hooking all methods ===");
                        deepHookClientRequestHX(jsInterface);
                    }

                    // 枚举所有带 @JavascriptInterface 注解的方法
                    for (Method m : jsInterface.getClass().getDeclaredMethods()) {
                        try {
                            if (m.isAnnotationPresent(Class.forName("android.webkit.JavascriptInterface").asSubclass(java.lang.annotation.Annotation.class))) {
                                Log.i(TAG, "  @JavascriptInterface: " + m.getName() + " " + java.util.Arrays.toString(m.getParameterTypes()));

                                // Hook 该方法以监控调用
                                Pine.hook(m, new MethodHook() {
                                    @Override
                                    public void beforeCall(Pine.CallFrame frame) {
                                        StringBuilder sb = new StringBuilder();
                                        sb.append("JSBridge.").append(name).append(".").append(m.getName()).append("(");
                                        if (frame.args != null) {
                                            for (int i = 0; i < frame.args.length; i++) {
                                                if (i > 0) sb.append(", ");
                                                Object arg = frame.args[i];
                                                if (arg == null) {
                                                    sb.append("null");
                                                } else if (arg instanceof String) {
                                                    String s = (String) arg;

                                                    // 特殊处理 onActionEvent 中的 clientRequestHX
                                                    if (m.getName().equals("onActionEvent") && s.contains("clientRequestHX")) {
                                                        // 尝试拦截并用 Native HTTP 处理
                                                        String result = interceptClientRequestHX(s, frame.thisObject);
                                                        if (result != null) {
                                                            // 成功拦截，阻止原始方法执行
                                                            Log.i(TAG, ">>> INTERCEPTED clientRequestHX, handled natively");
                                                            frame.setResult(null);
                                                            return;
                                                        }
                                                        // 提取认证参数（如果没有拦截）
                                                        if (s.contains("trade.5ifund.com")) {
                                                            extractAuthFromJSBridge(s);
                                                        }
                                                    }

                                                    // 捕获购买相关的JSBridge调用
                                                    if (s.contains("fundBuy") || s.contains("subscribe") || s.contains("purchase")) {
                                                        Log.i(TAG, ">>> CAPTURE: Detected buy-related JSBridge call");
                                                        // 保存当前的JSBridge handler实例
                                                        jsBridgeHandler = frame.thisObject;
                                                    }

                                                    if (s.length() > 500) {
                                                        sb.append("\"").append(s.substring(0, 500)).append("...[").append(s.length()).append("]\"");
                                                    } else {
                                                        sb.append("\"").append(s).append("\"");
                                                    }
                                                } else {
                                                    sb.append(arg);
                                                }
                                            }
                                        }
                                        sb.append(")");
                                        Log.i(TAG, sb.toString());
                                    }

                                    @Override
                                    public void afterCall(Pine.CallFrame frame) {
                                        Object result = frame.getResult();
                                        if (result != null) {
                                            String resultStr = result.toString();
                                            if (resultStr.length() > 1000) {
                                                resultStr = resultStr.substring(0, 1000) + "...[" + resultStr.length() + "]";
                                            }
                                            Log.i(TAG, "  → " + resultStr);
                                        }
                                    }
                                });
                            }
                        } catch (Throwable ignored) {}
                    }
                } catch (Throwable e) {
                    Log.w(TAG, "addJavascriptInterface hook error: " + e.getMessage());
                }
            }
        });

        Log.i(TAG, "JSBridge native hook installed");
    }

    /**
     * 从 JSBridge clientRequestHX 请求中提取认证参数
     */
    private static void extractAuthFromJSBridge(String json) {
        try {
            // 提取 Header 对象中的认证参数
            String[] authKeys = {"key1", "key2", "key3", "key4", "key5", "userId", "sessionId"};
            for (String key : authKeys) {
                // 查找 "key":"value" 模式
                String pattern = "\"" + key + "\":\"";
                int keyIdx = json.indexOf(pattern);
                if (keyIdx != -1) {
                    int valStart = keyIdx + pattern.length();
                    int valEnd = json.indexOf("\"", valStart);
                    if (valEnd > valStart) {
                        String value = json.substring(valStart, valEnd);
                        // 处理 JSON 转义
                        value = value.replace("\\u003d", "=");
                        if (value.length() > 0) {
                            switch (key) {
                                case "key1": latestKey1 = value; break;
                                case "key2": latestKey2 = value; break;
                                case "key3": latestKey3 = value; break;
                                case "key4": latestKey4 = value; break;
                                case "key5":
                                    latestKey5 = value;
                                    authCaptureTime = System.currentTimeMillis();
                                    break;
                                case "userId": latestUserId = value; break;
                                case "sessionId": latestSessionId = value; break;
                            }
                            Log.i(TAG, "Auth from JSBridge: " + key + "=" + (value.length() > 30 ? value.substring(0, 30) + "..." : value));
                        }
                    }
                }
            }
        } catch (Throwable e) {
            Log.w(TAG, "extractAuthFromJSBridge failed: " + e.getMessage());
        }
    }

    /**
     * 通过 WebView 触发基金购买
     */
    private static String triggerFundBuy(String body) {
        try {
            // 解析请求参数
            String fundCode = extractJsonString(body, "fundCode");
            String amountStr = extractJsonString(body, "amount");
            String password = extractJsonString(body, "password");

            if (fundCode == null || fundCode.isEmpty()) {
                return "{\"success\":false,\"error\":\"Missing fundCode\"}";
            }
            if (amountStr == null || amountStr.isEmpty()) {
                return "{\"success\":false,\"error\":\"Missing amount\"}";
            }

            Log.i(TAG, "Triggering fund buy: " + fundCode + " amount=" + amountStr);

            if (latestWebView == null) {
                return "{\"success\":false,\"error\":\"No WebView available\"}";
            }

            // 构造购买页面 URL
            String buyUrl = "https://trade.5ifund.com/hxapp/ifundBuyInit/dist/index.html?fundCode="
                + fundCode + "&amount=" + amountStr + "&transActionAccountId=600113970167";

            // 在主线程中打开购买页面
            final Object webView = latestWebView;
            new android.os.Handler(android.os.Looper.getMainLooper()).post(new Runnable() {
                @Override
                public void run() {
                    try {
                        // 方法1：直接加载 URL
                        webView.getClass().getMethod("loadUrl", String.class)
                            .invoke(webView, buyUrl);
                        Log.i(TAG, "Opened buy page: " + buyUrl);

                        // 方法2：如果提供了密码，延迟2秒后自动填充密码并提交
                        if (password != null && !password.isEmpty()) {
                            new android.os.Handler().postDelayed(new Runnable() {
                                @Override
                                public void run() {
                                    try {
                                        String js = "javascript:(function(){" +
                                            "var pwd=document.querySelector('input[type=password]');" +
                                            "if(pwd){pwd.value='" + password + "';}" +
                                            "var btn=document.querySelector('button.confirm');" +
                                            "if(btn){btn.click();}" +
                                            "})()";
                                        webView.getClass().getMethod("loadUrl", String.class)
                                            .invoke(webView, js);
                                        Log.i(TAG, "Auto-filled password and clicked confirm");
                                    } catch (Throwable e) {
                                        Log.e(TAG, "Auto-fill failed: " + e.getMessage());
                                    }
                                }
                            }, 2000);
                        }
                    } catch (Throwable e) {
                        Log.e(TAG, "loadUrl failed: " + e.getMessage());
                    }
                }
            });

            return "{\"success\":true,\"fundCode\":\"" + fundCode + "\",\"amount\":\"" + amountStr + "\"}";

        } catch (Throwable e) {
            Log.e(TAG, "triggerFundBuy failed", e);
            return "{\"success\":false,\"error\":\"" + e.getMessage() + "\"}";
        }
    }

    /**
     * 直接调用 App 内部的购买方法（不通过 UI 自动化）
     */
    private static String directBuyFund(String body) {
        Log.i(TAG, ">>> directBuyFund called");

        try {
            // 解析请求参数
            String fundCode = extractJsonString(body, "fundCode");
            String amountStr = extractJsonString(body, "amount");
            String password = extractJsonString(body, "password");

            if (fundCode == null || fundCode.isEmpty()) {
                return "{\"success\":false,\"error\":\"Missing fundCode\"}";
            }
            if (amountStr == null || amountStr.isEmpty()) {
                return "{\"success\":false,\"error\":\"Missing amount\"}";
            }

            Log.i(TAG, "Attempting direct buy: fundCode=" + fundCode + ", amount=" + amountStr);

            // 策略1: 尝试通过反射查找购买相关的类
            // 常见的包名模式: com.hexin.plat.android.fund.*, com.hexin.*.trade.*, 等
            String[] possiblePackages = {
                "com.hexin.plat.android.fund",
                "com.hexin.plat.android.trade",
                "com.hexin.fund",
                "com.hexin.trade",
                "com.ths.fund",
                "com.ths.trade"
            };

            // 常见的类名模式
            String[] possibleClassSuffixes = {
                ".FundBuyService",
                ".FundTradeService",
                ".TradeService",
                ".BuyService",
                ".SubscribeService",
                ".FundSubscribeService",
                ".manager.FundManager",
                ".manager.TradeManager",
                ".presenter.FundBuyPresenter",
                ".presenter.TradePresenter"
            };

            Class<?> targetClass = null;
            String foundClassName = null;

            // 尝试查找类
            for (String pkg : possiblePackages) {
                for (String suffix : possibleClassSuffixes) {
                    try {
                        String className = pkg + suffix;
                        targetClass = Class.forName(className);
                        foundClassName = className;
                        Log.i(TAG, ">>> Found candidate class: " + className);
                        break;
                    } catch (ClassNotFoundException ignored) {}
                }
                if (targetClass != null) break;
            }

            if (targetClass == null) {
                // 策略2: 如果有保存的JSBridge handler，尝试从中查找购买方法
                if (jsBridgeHandler != null) {
                    Log.i(TAG, ">>> Using captured JSBridge handler: " + jsBridgeHandler.getClass().getName());

                    // 枚举handler的所有方法，查找购买相关的方法
                    Method[] methods = jsBridgeHandler.getClass().getDeclaredMethods();
                    for (Method m : methods) {
                        String methodName = m.getName().toLowerCase();
                        if (methodName.contains("buy") || methodName.contains("subscribe") ||
                            methodName.contains("purchase") || methodName.contains("trade")) {
                            Log.i(TAG, ">>> Found potential buy method: " + m.getName() +
                                " params: " + java.util.Arrays.toString(m.getParameterTypes()));
                        }
                    }

                    return "{\"success\":false,\"error\":\"Found JSBridge handler but need manual method identification\",\"handler\":\"" +
                        jsBridgeHandler.getClass().getName() + "\"}";
                }

                return "{\"success\":false,\"error\":\"No suitable class or handler found. Need to capture JSBridge handler first by making a real purchase.\"}";
            }

            // 如果找到了类，枚举其方法
            Log.i(TAG, ">>> Enumerating methods of class: " + foundClassName);
            Method[] methods = targetClass.getDeclaredMethods();
            for (Method m : methods) {
                Log.i(TAG, "  Method: " + m.getName() + " params: " + java.util.Arrays.toString(m.getParameterTypes()));
            }

            return "{\"success\":false,\"error\":\"Class found but method invocation not yet implemented\",\"class\":\"" + foundClassName + "\"}";

        } catch (Throwable e) {
            Log.e(TAG, "directBuyFund failed", e);
            return "{\"success\":false,\"error\":\"" + e.getMessage() + "\",\"stack\":\"" + Log.getStackTraceString(e) + "\"}";
        }
    }

    /**
     * 打开基金详情页面（触发WebView创建和JSBridge初始化）
     */
    private static String openFundDetail(String body) {
        Log.i(TAG, ">>> openFundDetail called");

        try {
            String fundCode = extractJsonString(body, "fundCode");
            if (fundCode == null || fundCode.isEmpty()) {
                return "{\"success\":false,\"error\":\"Missing fundCode\"}";
            }

            Log.i(TAG, "Opening fund detail page: " + fundCode);

            // 通过Intent打开基金详情页面
            // 同花顺的基金详情页面通常使用以下URI scheme:
            // "thshexin://web?url=https://trade.5ifund.com/hxapp/fund/detail?fundCode=xxx"
            // 或者直接通过Activity打开

            // 方法1: 尝试通过Intent打开
            new android.os.Handler(android.os.Looper.getMainLooper()).post(new Runnable() {
                @Override
                public void run() {
                    try {
                        // 获取当前的Application context
                        Class<?> atClass = Class.forName("android.app.ActivityThread");
                        Method currentApp = atClass.getDeclaredMethod("currentApplication");
                        android.app.Application app = (android.app.Application) currentApp.invoke(null);

                        if (app != null) {
                            // 创建Intent打开基金详情页面
                            android.content.Intent intent = new android.content.Intent();
                            intent.setAction(android.content.Intent.ACTION_VIEW);
                            intent.setData(android.net.Uri.parse("thshexin://web?url=https://trade.5ifund.com/hxapp/fund/detail?fundCode=" + fundCode));
                            intent.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK);
                            app.startActivity(intent);
                            Log.i(TAG, "Started fund detail activity via Intent");
                        }
                    } catch (Throwable e) {
                        Log.e(TAG, "Failed to open fund detail via Intent: " + e.getMessage());
                        e.printStackTrace();
                    }
                }
            });

            return "{\"success\":true,\"fundCode\":\"" + fundCode + "\",\"message\":\"Opening fund detail page\"}";

        } catch (Throwable e) {
            Log.e(TAG, "openFundDetail failed", e);
            return "{\"success\":false,\"error\":\"" + e.getMessage() + "\"}";
        }
    }

    /**
     * Hook SQLiteDatabase 操作，捕获股票交易数据存取
     */
    private static void hookSQLiteDatabase() throws Throwable {
        Class<?> dbClass = Class.forName("android.database.sqlite.SQLiteDatabase");

        // Hook openDatabase 捕获数据库实例
        try {
            for (Method m : dbClass.getDeclaredMethods()) {
                if ("openDatabase".equals(m.getName()) || "openOrCreateDatabase".equals(m.getName())) {
                    Pine.hook(m, new MethodHook() {
                        @Override
                        public void afterCall(Pine.CallFrame callFrame) {
                            Object db = callFrame.getResult();
                            if (db != null) {
                                try {
                                    Method getPath = db.getClass().getMethod("getPath");
                                    String path = (String) getPath.invoke(db);
                                    Log.i(TAG, "SQLite.open: " + path);
                                    // 捕获股票相关数据库
                                    // 优先 xcs2.db
                                    if (path != null && path.contains("xcs2")) {
                                        stockDatabase = db;
                                        Log.i(TAG, "Stock database captured (xcs2): " + path);
                                    } else if (stockDatabase == null && path != null && (path.contains("weituo")
                                        || path.contains("xcs") || path.contains("stock") || path.contains("trade"))) {
                                        stockDatabase = db;
                                        Log.i(TAG, "Stock database captured: " + path);
                                    }
                                } catch (Throwable e) {
                                    Log.w(TAG, "getPath failed: " + e.getMessage());
                                }
                            }
                        }
                    });
                }
            }
        } catch (Throwable e) {
            Log.w(TAG, "SQLite openDatabase hook failed: " + e.getMessage());
        }

        // Hook execSQL - 用于 INSERT/UPDATE/DELETE
        try {
            Method execSQL = dbClass.getDeclaredMethod("execSQL", String.class);
            Pine.hook(execSQL, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    // 捕获数据库实例
                    Object db = callFrame.thisObject;
                    if (stockDatabase == null && db != null) {
                        try {
                            Method getPath = db.getClass().getMethod("getPath");
                            String path = (String) getPath.invoke(db);
                            if (path != null && (path.contains("weituo") || path.contains("xcs")
                                || path.contains("stock") || path.contains("trade"))) {
                                stockDatabase = db;
                                Log.i(TAG, "Stock database captured from execSQL: " + path);
                            }
                        } catch (Throwable ignored) {}
                    }

                    String sql = (String) callFrame.args[0];
                    // 只记录交易相关的表操作
                    if (sql != null && (sql.contains("stock_") || sql.contains("position") || sql.contains("entrust")
                        || sql.contains("trans") || sql.contains("asset") || sql.contains("money") || sql.contains("order"))) {
                        Log.i(TAG, "SQLite.execSQL: " + sql.substring(0, Math.min(500, sql.length())));
                    }
                }
            });
        } catch (Throwable e) {
            Log.w(TAG, "SQLite execSQL hook failed: " + e.getMessage());
        }

        // Hook execSQL with bind args
        try {
            Method execSQLArgs = dbClass.getDeclaredMethod("execSQL", String.class, Object[].class);
            Pine.hook(execSQLArgs, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    String sql = (String) callFrame.args[0];
                    Object[] args = (Object[]) callFrame.args[1];
                    if (sql != null && (sql.contains("stock_") || sql.contains("position") || sql.contains("entrust")
                        || sql.contains("trans") || sql.contains("asset") || sql.contains("money") || sql.contains("order"))) {
                        Log.i(TAG, "SQLite.execSQL: " + sql.substring(0, Math.min(300, sql.length()))
                            + " args=" + java.util.Arrays.toString(args));
                    }
                }
            });
        } catch (Throwable e) {
            Log.w(TAG, "SQLite execSQL(args) hook failed: " + e.getMessage());
        }

        // Hook rawQuery - 用于 SELECT
        try {
            Method rawQuery = dbClass.getDeclaredMethod("rawQuery", String.class, String[].class);
            Pine.hook(rawQuery, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    String sql = (String) callFrame.args[0];
                    String[] args = (String[]) callFrame.args[1];
                    if (sql != null && (sql.contains("stock_") || sql.contains("position") || sql.contains("entrust")
                        || sql.contains("trans") || sql.contains("asset") || sql.contains("money") || sql.contains("order"))) {
                        Log.i(TAG, "SQLite.rawQuery: " + sql.substring(0, Math.min(300, sql.length()))
                            + " args=" + java.util.Arrays.toString(args));
                    }
                }
            });
        } catch (Throwable e) {
            Log.w(TAG, "SQLite rawQuery hook failed: " + e.getMessage());
        }

        // Hook insert
        try {
            Method insert = dbClass.getDeclaredMethod("insert", String.class, String.class,
                Class.forName("android.content.ContentValues"));
            Pine.hook(insert, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    String table = (String) callFrame.args[0];
                    if (table != null && (table.contains("stock") || table.contains("position") || table.contains("entrust")
                        || table.contains("trans") || table.contains("asset") || table.contains("money") || table.contains("order"))) {
                        Object values = callFrame.args[2];
                        String valStr = values != null ? values.toString() : "null";
                        if (valStr.length() > 500) valStr = valStr.substring(0, 500) + "...";
                        Log.i(TAG, "SQLite.insert: table=" + table + " values=" + valStr);
                    }
                }
            });
        } catch (Throwable e) {
            Log.w(TAG, "SQLite insert hook failed: " + e.getMessage());
        }

        // Hook update
        try {
            Method update = dbClass.getDeclaredMethod("update", String.class,
                Class.forName("android.content.ContentValues"), String.class, String[].class);
            Pine.hook(update, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    String table = (String) callFrame.args[0];
                    if (table != null && (table.contains("stock") || table.contains("position") || table.contains("entrust")
                        || table.contains("trans") || table.contains("asset") || table.contains("money") || table.contains("order"))) {
                        Object values = callFrame.args[1];
                        String where = (String) callFrame.args[2];
                        String valStr = values != null ? values.toString() : "null";
                        if (valStr.length() > 300) valStr = valStr.substring(0, 300) + "...";
                        Log.i(TAG, "SQLite.update: table=" + table + " values=" + valStr + " where=" + where);
                    }
                }
            });
        } catch (Throwable e) {
            Log.w(TAG, "SQLite update hook failed: " + e.getMessage());
        }

        Log.i(TAG, "SQLite hooks installed");
    }

    // 捕获的 MasterModuleBridge 实例，用于发起交易
    private static volatile Object masterModuleBridgeInstance = null;
    private static volatile Method masterModuleBridgeRequestMethod = null;

    // WTBuyConfirmClient: 买入入口类
    private static volatile Object wtBuyConfirmClientInstance = null;
    private static volatile Class<?> wtBuyConfirmClientClass = null;
    private static volatile Method wtBuyRequestDirectMethod = null;
    // 存储创建 WTBuyConfirmClient 需要的依赖（从 Hook 中捕获）
    private static volatile Object capturedWTContext = null;  // 可能需要的 Context 或其他依赖

    // 交易日志缓存（最近 100 条）
    private static final List<String> recentTradeLogs = java.util.Collections.synchronizedList(new java.util.LinkedList<String>() {
        @Override
        public boolean add(String e) {
            if (size() >= 100) {
                removeFirst();
            }
            return super.add(e);
        }
    });

    private static void addTradeLog(String log) {
        String timestamp = new java.text.SimpleDateFormat("HH:mm:ss.SSS").format(new java.util.Date());
        recentTradeLogs.add(timestamp + " " + log);
    }

    /**
     * Hook 股票交易 SDK，捕获买入/卖出操作
     * 目标包：com.hexin.android.weituo.hstrade.*
     */
    private static void hookTradingSDK(ClassLoader cl) throws Throwable {
        // 核心：Hook MasterModuleBridge (libweituo.so 的 Java 入口)
        try {
            Class<?> bridgeClass = cl.loadClass("com.hexin.android.mastermodule.MasterModuleBridge");
            Log.i(TAG, "Found MasterModuleBridge class!");

            // Hook 所有方法来分析接口
            for (Method m : bridgeClass.getDeclaredMethods()) {
                String methodName = m.getName();
                Log.i(TAG, "MasterModuleBridge method: " + methodName + " params=" + java.util.Arrays.toString(m.getParameterTypes()));

                // 跳过常见的无关方法
                if (methodName.equals("hashCode") || methodName.equals("equals")
                    || methodName.equals("toString") || methodName.equals("getClass")) {
                    continue;
                }

                Pine.hook(m, new MethodHook() {
                    @Override
                    public void beforeCall(Pine.CallFrame callFrame) {
                        // 捕获实例
                        if (masterModuleBridgeInstance == null && callFrame.thisObject != null) {
                            masterModuleBridgeInstance = callFrame.thisObject;
                            Log.i(TAG, "MasterModuleBridge instance captured!");
                        }

                        StringBuilder sb = new StringBuilder();
                        sb.append("MasterBridge.").append(methodName).append("(");
                        if (callFrame.args != null && callFrame.args.length > 0) {
                            for (int i = 0; i < callFrame.args.length; i++) {
                                if (i > 0) sb.append(", ");
                                Object arg = callFrame.args[i];
                                if (arg == null) {
                                    sb.append("null");
                                } else if (arg instanceof String) {
                                    String s = (String) arg;
                                    sb.append("\"").append(s.length() > 500 ? s.substring(0, 500) + "..." : s).append("\"");
                                } else if (arg instanceof byte[]) {
                                    byte[] bytes = (byte[]) arg;
                                    sb.append("byte[").append(bytes.length).append("]");
                                    // 解码 byte[] 内容（最多显示 2000 字节）
                                    if (bytes.length > 0) {
                                        try {
                                            int len = Math.min(bytes.length, 2000);
                                            String content = new String(bytes, 0, len, "UTF-8");
                                            // 替换不可打印字符
                                            content = content.replaceAll("[\\x00-\\x1F\\x7F]", ".");
                                            sb.append("=\"").append(content);
                                            if (bytes.length > 2000) sb.append("...");
                                            sb.append("\"");
                                        } catch (Throwable ignored) {}
                                    }
                                } else {
                                    sb.append(arg.getClass().getSimpleName()).append("@").append(Integer.toHexString(System.identityHashCode(arg)));
                                }
                            }
                        }
                        sb.append(")");
                        String logMsg = sb.toString();
                        Log.i(TAG, logMsg);
                        addTradeLog("REQ: " + logMsg);

                        // 打印调用栈（直接使用 Throwable 获取完整调用栈）
                        if (methodName.equals("jniRequest")) {
                            Log.i(TAG, "=== jniRequest CALL STACK ===");
                            Throwable t = new Throwable();
                            for (StackTraceElement e : t.getStackTrace()) {
                                String cn = e.getClassName();
                                // 只打印同花顺的类
                                if (cn.contains("hexin") || cn.contains("weituo")) {
                                    Log.i(TAG, "  -> " + cn + "." + e.getMethodName() + ":" + e.getLineNumber());
                                }
                            }
                            Log.i(TAG, "=== END STACK ===");
                        }
                    }

                    @Override
                    public void afterCall(Pine.CallFrame callFrame) {
                        Object result = callFrame.getResult();
                        if (result != null && !(result instanceof Void)) {
                            String resultStr;
                            if (result instanceof byte[]) {
                                byte[] bytes = (byte[]) result;
                                resultStr = "byte[" + bytes.length + "]";
                                if (bytes.length < 500) {
                                    try {
                                        resultStr += "=\"" + new String(bytes, "UTF-8") + "\"";
                                    } catch (Throwable ignored) {}
                                }
                            } else {
                                resultStr = result.toString();
                                if (resultStr.length() > 500) {
                                    resultStr = resultStr.substring(0, 500) + "...";
                                }
                            }
                            Log.i(TAG, "  → " + resultStr);
                            addTradeLog("RSP: " + methodName + " → " + resultStr);
                        }
                    }
                });

                // 保存请求方法引用（用于发起交易）
                if (methodName.contains("request") || methodName.contains("Request")
                    || methodName.contains("send") || methodName.contains("Send")
                    || methodName.contains("call") || methodName.contains("Call")) {
                    if (masterModuleBridgeRequestMethod == null) {
                        masterModuleBridgeRequestMethod = m;
                        Log.i(TAG, "MasterModuleBridge request method captured: " + methodName);
                    }
                }
            }
            Log.i(TAG, "MasterModuleBridge hooks installed");
        } catch (ClassNotFoundException e) {
            Log.w(TAG, "MasterModuleBridge not found");
        }

        // 尝试 Hook 常见的交易接口类
        String[] tradeClassNames = {
            "com.hexin.android.weituo.hstrade.base.api.TradeApi",
            "com.hexin.android.weituo.hstrade.base.api.TradeApiImpl",
            "com.hexin.android.weituo.hstrade.base.manager.TradeManager",
            "com.hexin.android.weituo.trade.core.TradeCore",
            "com.hexin.android.weituo.trade.api.ITradeApi",
            "com.hexin.android.weituo.common.TradeCallback",
            "com.hexin.android.weituo.hstrade.base.xcs.local.manager.XcsLocalManager",
            "com.hexin.android.weituo.hstrade.base.xcs.local.api.XcsLocalApi",
            // 新增：更多可能的交易类
            "com.hexin.android.weituo.hstrade.base.request.TradeRequest",
            "com.hexin.android.weituo.hstrade.base.request.RequestManager",
            "com.hexin.android.weituo.core.WeiTuoCore",
            "com.hexin.android.weituo.WeiTuoManager"
        };

        for (String className : tradeClassNames) {
            try {
                Class<?> tradeClass = cl.loadClass(className);
                Log.i(TAG, "Found trade class: " + className);
                hookAllMethods(tradeClass, className);
            } catch (ClassNotFoundException e) {
                // 类不存在，继续尝试下一个
            }
        }

        // Hook Dialog 显示，捕获错误消息
        try {
            Class<?> alertDialogClass = Class.forName("android.app.AlertDialog$Builder");
            Method setMessage = alertDialogClass.getDeclaredMethod("setMessage", CharSequence.class);
            Pine.hook(setMessage, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    CharSequence msg = (CharSequence) callFrame.args[0];
                    if (msg != null) {
                        String msgStr = msg.toString();
                        // 捕获交易相关的对话框消息
                        if (msgStr.contains("251067") || msgStr.contains("股东") || msgStr.contains("限制")
                            || msgStr.contains("委托") || msgStr.contains("买入") || msgStr.contains("卖出")
                            || msgStr.contains("交易") || msgStr.contains("下单")) {
                            Log.i(TAG, "TradeDialog: " + msgStr);
                            // 打印调用堆栈以找到交易代码
                            StringBuilder sb = new StringBuilder();
                            for (StackTraceElement e : Thread.currentThread().getStackTrace()) {
                                if (e.getClassName().contains("weituo") || e.getClassName().contains("trade")
                                    || e.getClassName().contains("hexin")) {
                                    sb.append("  at ").append(e.toString()).append("\n");
                                }
                            }
                            if (sb.length() > 0) {
                                Log.i(TAG, "TradeStack:\n" + sb);
                            }
                        }
                    }
                }
            });
            Log.i(TAG, "AlertDialog.Builder.setMessage hooked");
        } catch (Throwable e) {
            Log.w(TAG, "AlertDialog hook failed: " + e.getMessage());
        }

        // Hook Toast 显示
        try {
            Class<?> toastClass = Class.forName("android.widget.Toast");
            Method makeText = toastClass.getDeclaredMethod("makeText",
                Class.forName("android.content.Context"), CharSequence.class, int.class);
            Pine.hook(makeText, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    CharSequence msg = (CharSequence) callFrame.args[1];
                    if (msg != null) {
                        String msgStr = msg.toString();
                        if (msgStr.contains("251067") || msgStr.contains("股东") || msgStr.contains("委托")
                            || msgStr.contains("买入") || msgStr.contains("卖出") || msgStr.contains("下单")
                            || msgStr.contains("交易") || msgStr.contains("成功") || msgStr.contains("失败")) {
                            Log.i(TAG, "TradeToast: " + msgStr);
                        }
                    }
                }
            });
            Log.i(TAG, "Toast.makeText hooked");
        } catch (Throwable e) {
            Log.w(TAG, "Toast hook failed: " + e.getMessage());
        }

        Log.i(TAG, "Trading SDK hooks installed");
    }

    /**
     * Hook 一个类的所有公共方法
     */
    private static void hookAllMethods(Class<?> clazz, String className) {
        for (Method m : clazz.getDeclaredMethods()) {
            try {
                String methodName = m.getName();
                // 跳过常见的无关方法
                if (methodName.equals("hashCode") || methodName.equals("equals")
                    || methodName.equals("toString") || methodName.equals("getClass")
                    || methodName.startsWith("access$")) {
                    continue;
                }

                Pine.hook(m, new MethodHook() {
                    @Override
                    public void beforeCall(Pine.CallFrame callFrame) {
                        StringBuilder sb = new StringBuilder();
                        sb.append("Trade.").append(clazz.getSimpleName()).append(".")
                          .append(methodName).append("(");
                        if (callFrame.args != null && callFrame.args.length > 0) {
                            for (int i = 0; i < callFrame.args.length; i++) {
                                if (i > 0) sb.append(", ");
                                Object arg = callFrame.args[i];
                                if (arg == null) {
                                    sb.append("null");
                                } else if (arg instanceof String) {
                                    String s = (String) arg;
                                    sb.append("\"").append(s.length() > 200 ? s.substring(0, 200) + "..." : s).append("\"");
                                } else {
                                    sb.append(arg.getClass().getSimpleName()).append("@").append(Integer.toHexString(System.identityHashCode(arg)));
                                }
                            }
                        }
                        sb.append(")");
                        Log.i(TAG, sb.toString());
                    }

                    @Override
                    public void afterCall(Pine.CallFrame callFrame) {
                        Object result = callFrame.getResult();
                        if (result != null && !(result instanceof Void)) {
                            String resultStr = result.toString();
                            if (resultStr.length() > 300) {
                                resultStr = resultStr.substring(0, 300) + "...";
                            }
                            Log.i(TAG, "  → " + resultStr);
                        }
                    }
                });
            } catch (Throwable e) {
                // 忽略无法 Hook 的方法
            }
        }
        Log.i(TAG, "Hooked all methods in: " + className);
    }

    // ========== Activity 和点击追踪 ==========

    // 当前 Activity 名称
    private static volatile String currentActivityName = "";

    /**
     * Hook Activity 生命周期和 View 点击事件，追踪买入/卖出按钮
     */
    private static void hookActivityAndClicks() throws Throwable {
        // Hook Activity.onCreate
        Class<?> activityClass = Class.forName("android.app.Activity");
        Method onCreate = activityClass.getDeclaredMethod("onCreate", Class.forName("android.os.Bundle"));
        Pine.hook(onCreate, new MethodHook() {
            @Override
            public void afterCall(Pine.CallFrame callFrame) {
                try {
                    Object activity = callFrame.thisObject;
                    String name = activity.getClass().getName();
                    currentActivityName = name;
                    // 只记录同花顺相关的 Activity
                    if (name.contains("hexin") || name.contains("weituo") || name.contains("trade")
                        || name.contains("order") || name.contains("buy") || name.contains("sell")) {
                        Log.i(TAG, "Activity.onCreate: " + name);
                        addTradeLog("ACTIVITY: " + name);
                    }
                } catch (Throwable ignored) {}
            }
        });

        // Hook Activity.onResume
        Method onResume = activityClass.getDeclaredMethod("onResume");
        Pine.hook(onResume, new MethodHook() {
            @Override
            public void afterCall(Pine.CallFrame callFrame) {
                try {
                    Object activity = callFrame.thisObject;
                    String name = activity.getClass().getName();
                    currentActivityName = name;
                } catch (Throwable ignored) {}
            }
        });

        // Hook View.setOnClickListener - 捕获所有点击监听器设置
        Class<?> viewClass = Class.forName("android.view.View");
        Class<?> listenerClass = Class.forName("android.view.View$OnClickListener");
        Method setOnClickListener = viewClass.getDeclaredMethod("setOnClickListener", listenerClass);
        Pine.hook(setOnClickListener, new MethodHook() {
            @Override
            public void beforeCall(Pine.CallFrame callFrame) {
                try {
                    Object view = callFrame.thisObject;
                    Object listener = callFrame.args[0];
                    if (listener == null) return;

                    String listenerClass = listener.getClass().getName();
                    // 只关注交易相关的监听器
                    if (listenerClass.contains("weituo") || listenerClass.contains("trade")
                        || listenerClass.contains("order") || listenerClass.contains("buy")
                        || listenerClass.contains("sell") || listenerClass.contains("mairu")
                        || listenerClass.contains("maichu") || listenerClass.contains("entrust")) {

                        // 获取 view ID
                        int viewId = (int) view.getClass().getMethod("getId").invoke(view);
                        String viewClassName = view.getClass().getName();

                        Log.i(TAG, "TradeButton detected: view=" + viewClassName + " id=" + viewId
                            + " listener=" + listenerClass);
                        addTradeLog("BUTTON: " + listenerClass + " viewId=" + viewId);

                        // Hook 这个监听器的 onClick 方法
                        try {
                            Method onClick = listener.getClass().getMethod("onClick", viewClass);
                            Pine.hook(onClick, new MethodHook() {
                                @Override
                                public void beforeCall(Pine.CallFrame frame) {
                                    Log.i(TAG, ">>> TRADE CLICK: " + listenerClass + " in " + currentActivityName);
                                    addTradeLog("CLICK: " + listenerClass);

                                    // 打印调用栈
                                    Throwable t = new Throwable();
                                    StringBuilder stack = new StringBuilder();
                                    for (StackTraceElement e : t.getStackTrace()) {
                                        String cn = e.getClassName();
                                        if (cn.contains("hexin") || cn.contains("weituo")) {
                                            stack.append("  -> ").append(cn).append(".").append(e.getMethodName())
                                                 .append(":").append(e.getLineNumber()).append("\n");
                                        }
                                    }
                                    if (stack.length() > 0) {
                                        Log.i(TAG, "Click stack:\n" + stack.toString());
                                    }
                                }
                            });
                            Log.i(TAG, "Hooked onClick for: " + listenerClass);
                        } catch (Throwable e) {
                            Log.w(TAG, "Failed to hook onClick: " + e.getMessage());
                        }
                    }
                } catch (Throwable e) {
                    // ignore
                }
            }
        });

        // Hook View.performClick - 捕获所有点击事件
        try {
            Method performClick = viewClass.getDeclaredMethod("performClick");
            Pine.hook(performClick, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    try {
                        Object view = callFrame.thisObject;
                        String viewClassName = view.getClass().getName();

                        // 只记录交易相关的点击
                        if (viewClassName.contains("weituo") || viewClassName.contains("trade")
                            || viewClassName.contains("buy") || viewClassName.contains("sell")
                            || viewClassName.contains("order") || viewClassName.contains("Button")) {

                            int viewId = (int) view.getClass().getMethod("getId").invoke(view);

                            // 尝试获取 view 的文本
                            String text = "";
                            try {
                                Method getText = view.getClass().getMethod("getText");
                                Object textObj = getText.invoke(view);
                                if (textObj != null) text = textObj.toString();
                            } catch (Throwable ignored) {}

                            // 只记录有文本或特定类型的点击
                            if (text.contains("买") || text.contains("卖") || text.contains("下单")
                                || text.contains("委托") || text.contains("确认")
                                || viewClassName.contains("weituo")) {
                                Log.i(TAG, ">>> performClick: " + viewClassName + " id=" + viewId
                                    + " text=\"" + text + "\" activity=" + currentActivityName);
                                addTradeLog("PERFORM_CLICK: " + text + " in " + currentActivityName);
                            }
                        }
                    } catch (Throwable ignored) {}
                }
            });
        } catch (Throwable e) {
            Log.w(TAG, "performClick hook failed: " + e.getMessage());
        }

        Log.i(TAG, "Activity and click hooks installed");
    }

    // ========== Cipher Hook - 捕获加密操作 ==========

    // Cipher 上下文追踪
    private static class CipherContext {
        String algorithm;
        byte[] keyBytes;
        byte[] ivBytes;
        int opmode;
        long initTimeMs;
        boolean stackPrinted;

        CipherContext(String algorithm, byte[] keyBytes, byte[] ivBytes, int opmode) {
            this.algorithm = algorithm;
            this.keyBytes = keyBytes;
            this.ivBytes = ivBytes;
            this.opmode = opmode;
            this.initTimeMs = System.currentTimeMillis();
            this.stackPrinted = false;
        }
    }

    private static final ConcurrentHashMap<Integer, CipherContext> trackedCiphers = new ConcurrentHashMap<>();
    private static final AtomicInteger cipherLogCount = new AtomicInteger(0);
    private static volatile long cipherLogWindowStart = 0;
    private static final int CIPHER_LOG_LIMIT = 50;

    /**
     * Hook javax.crypto.Cipher 捕获加密/解密操作
     * 用于追踪交易请求在哪里被加密
     */
    private static void hookCipher() throws Throwable {
        Class<?> cipherClass = Class.forName("javax.crypto.Cipher");

        // Hook Cipher.init(int opmode, Key key, AlgorithmParameterSpec params)
        try {
            Method initWithParams = cipherClass.getDeclaredMethod("init",
                int.class, java.security.Key.class, java.security.spec.AlgorithmParameterSpec.class);

            Pine.hook(initWithParams, new MethodHook() {
                @Override
                public void afterCall(Pine.CallFrame callFrame) {
                    try {
                        int opmode = (int) callFrame.args[0];
                        Object cipher = callFrame.thisObject;
                        String algorithm = (String) cipher.getClass().getMethod("getAlgorithm").invoke(cipher);

                        // 跳过 TLS 算法
                        if (isTlsAlgorithm(algorithm)) return;

                        java.security.Key key = (java.security.Key) callFrame.args[1];
                        byte[] keyBytes = key != null ? key.getEncoded() : null;

                        byte[] ivBytes = null;
                        Object paramSpec = callFrame.args[2];
                        if (paramSpec != null && paramSpec.getClass().getName().contains("IvParameterSpec")) {
                            try {
                                ivBytes = (byte[]) paramSpec.getClass().getMethod("getIV").invoke(paramSpec);
                            } catch (Throwable ignored) {}
                        }

                        int id = System.identityHashCode(cipher);
                        trackedCiphers.put(id, new CipherContext(algorithm, keyBytes, ivBytes, opmode));

                        // 限制日志
                        if (cipherRateLimitCheck()) {
                            String mode = opmode == 1 ? "ENCRYPT" : opmode == 2 ? "DECRYPT" : "MODE_" + opmode;
                            Log.i(TAG, "CIPHER_INIT: " + mode + " algo=" + algorithm +
                                " keyLen=" + (keyBytes != null ? keyBytes.length : 0) +
                                " ivLen=" + (ivBytes != null ? ivBytes.length : 0));
                        }
                    } catch (Throwable e) {
                        // ignore
                    }
                }
            });
            Log.i(TAG, "Hooked Cipher.init(int, Key, AlgorithmParameterSpec)");
        } catch (Throwable e) {
            Log.w(TAG, "Cipher.init with params hook failed: " + e.getMessage());
        }

        // Hook Cipher.init(int opmode, Key key)
        try {
            Method initSimple = cipherClass.getDeclaredMethod("init", int.class, java.security.Key.class);
            Pine.hook(initSimple, new MethodHook() {
                @Override
                public void afterCall(Pine.CallFrame callFrame) {
                    try {
                        int opmode = (int) callFrame.args[0];
                        Object cipher = callFrame.thisObject;
                        String algorithm = (String) cipher.getClass().getMethod("getAlgorithm").invoke(cipher);

                        if (isTlsAlgorithm(algorithm)) return;

                        java.security.Key key = (java.security.Key) callFrame.args[1];
                        byte[] keyBytes = key != null ? key.getEncoded() : null;

                        int id = System.identityHashCode(cipher);
                        trackedCiphers.put(id, new CipherContext(algorithm, keyBytes, null, opmode));
                    } catch (Throwable ignored) {}
                }
            });
            Log.i(TAG, "Hooked Cipher.init(int, Key)");
        } catch (Throwable e) {
            Log.w(TAG, "Cipher.init simple hook failed: " + e.getMessage());
        }

        // Hook Cipher.doFinal(byte[] input)
        try {
            Method doFinalBytes = cipherClass.getDeclaredMethod("doFinal", byte[].class);
            Pine.hook(doFinalBytes, new MethodHook() {
                @Override
                public void afterCall(Pine.CallFrame callFrame) {
                    try {
                        Object cipher = callFrame.thisObject;
                        int id = System.identityHashCode(cipher);
                        CipherContext ctx = trackedCiphers.get(id);
                        if (ctx == null) return;

                        byte[] input = (byte[]) callFrame.args[0];
                        Object result = callFrame.getResult();
                        if (!(result instanceof byte[])) return;
                        byte[] output = (byte[]) result;

                        // 过滤小数据
                        if (input == null || input.length < 32) return;

                        // 检查是否是加密操作（opmode == 1）
                        boolean isEncrypt = ctx.opmode == 1;

                        String inputStr = "";
                        String outputStr = "";
                        try {
                            inputStr = new String(input, "UTF-8");
                            outputStr = new String(output, "UTF-8");
                        } catch (Throwable ignored) {}

                        // 检查是否包含交易相关关键字
                        boolean isTradeRelated = inputStr.contains("mairu") || inputStr.contains("maichu")
                            || inputStr.contains("weituo") || inputStr.contains("stock")
                            || inputStr.contains("price") || inputStr.contains("volume")
                            || inputStr.contains("order") || inputStr.contains("trade")
                            || inputStr.contains("cmd_wt") || inputStr.contains("cmd_qu");

                        // 如果是加密操作，记录明文输入
                        if (isEncrypt && cipherRateLimitCheck()) {
                            Log.i(TAG, "CIPHER_ENCRYPT: algo=" + ctx.algorithm +
                                " inputLen=" + input.length +
                                " outputLen=" + output.length);

                            // 打印明文输入（可能是交易参数）
                            String preview = inputStr.length() > 500 ? inputStr.substring(0, 500) + "..." : inputStr;
                            Log.i(TAG, "  PlainText: " + preview);

                            // 尝试从明文中提取 key5（用于认证）
                            if (inputStr.contains("\"key5\"")) {
                                try {
                                    int k5Start = inputStr.indexOf("\"key5\":\"") + 8;
                                    if (k5Start > 8) {
                                        int k5End = inputStr.indexOf("\"", k5Start);
                                        if (k5End > k5Start) {
                                            String key5Value = inputStr.substring(k5Start, k5End);
                                            // 处理 JSON 转义字符 \u003d
                                            key5Value = key5Value.replace("\\u003d", "=");
                                            if (key5Value.length() > 50) {
                                                latestKey5 = key5Value;
                                                authCaptureTime = System.currentTimeMillis();
                                                Log.i(TAG, "Auth captured from cipher: key5=" + key5Value.substring(0, Math.min(50, key5Value.length())) + "... (len=" + key5Value.length() + ")");
                                            }
                                        }
                                    }
                                } catch (Throwable e) {
                                    Log.w(TAG, "Failed to extract key5 from cipher plaintext: " + e.getMessage());
                                }
                            }

                            // 如果是交易相关的，打印调用栈
                            if (isTradeRelated && !ctx.stackPrinted) {
                                ctx.stackPrinted = true;
                                logCipherStack("ENCRYPT");
                            }
                        }

                        // 清理
                        if (System.currentTimeMillis() - ctx.initTimeMs > 60000) {
                            trackedCiphers.remove(id);
                        }
                    } catch (Throwable e) {
                        // ignore
                    }
                }
            });
            Log.i(TAG, "Hooked Cipher.doFinal(byte[])");
        } catch (Throwable e) {
            Log.w(TAG, "Cipher.doFinal hook failed: " + e.getMessage());
        }

        // Hook Cipher.doFinal(byte[], int, int)
        try {
            Method doFinalPartial = cipherClass.getDeclaredMethod("doFinal", byte[].class, int.class, int.class);
            Pine.hook(doFinalPartial, new MethodHook() {
                @Override
                public void afterCall(Pine.CallFrame callFrame) {
                    try {
                        Object cipher = callFrame.thisObject;
                        int id = System.identityHashCode(cipher);
                        CipherContext ctx = trackedCiphers.get(id);
                        if (ctx == null) return;

                        byte[] input = (byte[]) callFrame.args[0];
                        int offset = (int) callFrame.args[1];
                        int len = (int) callFrame.args[2];

                        if (input == null || len < 32) return;

                        boolean isEncrypt = ctx.opmode == 1;
                        if (isEncrypt && cipherRateLimitCheck()) {
                            String plaintext = new String(input, offset, len, "UTF-8");
                            Log.i(TAG, "CIPHER_ENCRYPT_PARTIAL: algo=" + ctx.algorithm + " len=" + len);
                            String preview = plaintext.length() > 500 ? plaintext.substring(0, 500) + "..." : plaintext;
                            Log.i(TAG, "  PlainText: " + preview);

                            // 尝试从明文中提取 key5（用于认证）
                            if (plaintext.contains("\"key5\"")) {
                                try {
                                    int k5Start = plaintext.indexOf("\"key5\":\"") + 8;
                                    if (k5Start > 8) {
                                        int k5End = plaintext.indexOf("\"", k5Start);
                                        if (k5End > k5Start) {
                                            String key5Value = plaintext.substring(k5Start, k5End);
                                            // 处理 JSON 转义字符 \u003d
                                            key5Value = key5Value.replace("\\u003d", "=");
                                            if (key5Value.length() > 50) {
                                                latestKey5 = key5Value;
                                                authCaptureTime = System.currentTimeMillis();
                                                Log.i(TAG, "Auth captured from cipher (partial): key5=" + key5Value.substring(0, Math.min(50, key5Value.length())) + "... (len=" + key5Value.length() + ")");
                                            }
                                        }
                                    }
                                } catch (Throwable e) {
                                    Log.w(TAG, "Failed to extract key5 from cipher plaintext: " + e.getMessage());
                                }
                            }

                            if (!ctx.stackPrinted) {
                                ctx.stackPrinted = true;
                                logCipherStack("ENCRYPT");
                            }
                        }
                    } catch (Throwable ignored) {}
                }
            });
            Log.i(TAG, "Hooked Cipher.doFinal(byte[], int, int)");
        } catch (Throwable e) {
            Log.w(TAG, "Cipher.doFinal partial hook failed: " + e.getMessage());
        }

        Log.i(TAG, "Cipher hooks installed");
    }

    private static boolean isTlsAlgorithm(String algorithm) {
        if (algorithm == null) return false;
        String upper = algorithm.toUpperCase();
        return upper.contains("GCM") ||
               upper.contains("CHACHA20") ||
               upper.contains("POLY1305") ||
               upper.contains("OAEP") ||
               (upper.contains("RSA") && upper.contains("ECB"));
    }

    private static boolean cipherRateLimitCheck() {
        long now = System.currentTimeMillis();
        if (now - cipherLogWindowStart > 60000) {
            cipherLogWindowStart = now;
            cipherLogCount.set(0);
        }
        return cipherLogCount.incrementAndGet() <= CIPHER_LOG_LIMIT;
    }

    private static void logCipherStack(String tag) {
        Throwable t = new Throwable();
        StringBuilder stack = new StringBuilder();
        stack.append("=== ").append(tag).append(" CALL STACK ===\n");
        for (StackTraceElement e : t.getStackTrace()) {
            String cn = e.getClassName();
            // 打印同花顺相关的类
            if (cn.contains("hexin") || cn.contains("weituo") || cn.contains("trade")
                || cn.contains("stock") || cn.contains("order")) {
                stack.append("  -> ").append(cn).append(".")
                     .append(e.getMethodName()).append(":").append(e.getLineNumber()).append("\n");
            }
        }
        stack.append("=== END STACK ===");
        Log.i(TAG, stack.toString());
        addTradeLog("STACK: " + stack.toString());
    }

    // ========== WTBuyConfirmClient Hook ==========

    /**
     * Hook 买入入口类 WTBuyConfirmClient
     * 目标：捕获构造参数、requestDirect() 调用方式，并存储实例
     */
    private static void hookWTBuyConfirmClient(ClassLoader cl) throws Throwable {
        String buyClientClassName = "com.hexin.android.weituo.trading.alltradetype.basictrade.buysell.common.wtbuysellreq.client.WTBuyConfirmClient";

        try {
            wtBuyConfirmClientClass = cl.loadClass(buyClientClassName);
            Log.i(TAG, "=== Found WTBuyConfirmClient class! ===");

            // 列出所有构造函数
            java.lang.reflect.Constructor<?>[] ctors = wtBuyConfirmClientClass.getDeclaredConstructors();
            Log.i(TAG, "WTBuyConfirmClient has " + ctors.length + " constructor(s):");
            for (java.lang.reflect.Constructor<?> ctor : ctors) {
                Class<?>[] paramTypes = ctor.getParameterTypes();
                StringBuilder paramStr = new StringBuilder();
                for (int i = 0; i < paramTypes.length; i++) {
                    if (i > 0) paramStr.append(", ");
                    paramStr.append(paramTypes[i].getName());
                }
                Log.i(TAG, "  Constructor(" + paramStr.toString() + ")");

                // Hook 每个构造函数
                ctor.setAccessible(true);
                Pine.hook(ctor, new MethodHook() {
                    @Override
                    public void afterCall(Pine.CallFrame callFrame) {
                        try {
                            Object instance = callFrame.thisObject;
                            Log.i(TAG, "=== WTBuyConfirmClient CONSTRUCTED ===");

                            // 记录构造参数
                            if (callFrame.args != null && callFrame.args.length > 0) {
                                StringBuilder sb = new StringBuilder("Constructor args:\n");
                                for (int i = 0; i < callFrame.args.length; i++) {
                                    Object arg = callFrame.args[i];
                                    String argInfo = arg == null ? "null" :
                                        arg.getClass().getName() + "@" + Integer.toHexString(System.identityHashCode(arg));
                                    sb.append("  [").append(i).append("] ").append(argInfo);

                                    // 尝试提取更多信息
                                    if (arg != null) {
                                        try {
                                            String str = arg.toString();
                                            if (str.length() < 500) {
                                                sb.append(" = ").append(str);
                                            }
                                        } catch (Throwable ignored) {}
                                    }
                                    sb.append("\n");

                                    // 保存可能的 Context 依赖
                                    if (arg != null && arg.getClass().getName().contains("Context")) {
                                        capturedWTContext = arg;
                                        Log.i(TAG, "Captured WT Context: " + arg.getClass().getName());
                                    }
                                }
                                Log.i(TAG, sb.toString());
                            }

                            // 保存实例
                            wtBuyConfirmClientInstance = instance;
                            Log.i(TAG, "WTBuyConfirmClient instance captured!");

                            // 列出实例的所有字段（通过反射）
                            dumpObjectFields(instance, "WTBuyConfirmClient");

                        } catch (Throwable e) {
                            Log.e(TAG, "WTBuyConfirmClient constructor hook error: " + e.getMessage());
                        }
                    }
                });
            }

            // 列出所有方法并 Hook 关键方法
            Method[] methods = wtBuyConfirmClientClass.getDeclaredMethods();
            Log.i(TAG, "WTBuyConfirmClient has " + methods.length + " method(s):");

            for (Method m : methods) {
                String methodName = m.getName();
                Class<?>[] paramTypes = m.getParameterTypes();
                StringBuilder paramStr = new StringBuilder();
                for (int i = 0; i < paramTypes.length; i++) {
                    if (i > 0) paramStr.append(", ");
                    paramStr.append(paramTypes[i].getSimpleName());
                }
                Log.i(TAG, "  " + methodName + "(" + paramStr.toString() + ") -> " + m.getReturnType().getSimpleName());

                // 保存 requestDirect 方法引用
                if (methodName.equals("requestDirect") || methodName.contains("request")) {
                    wtBuyRequestDirectMethod = m;
                    Log.i(TAG, "Found request method: " + methodName);
                }

                // Hook 所有公开方法来观察调用
                m.setAccessible(true);
                final String mName = methodName;
                Pine.hook(m, new MethodHook() {
                    @Override
                    public void beforeCall(Pine.CallFrame callFrame) {
                        try {
                            StringBuilder sb = new StringBuilder();
                            sb.append("WTBuyClient.").append(mName).append("(");

                            if (callFrame.args != null) {
                                for (int i = 0; i < callFrame.args.length; i++) {
                                    if (i > 0) sb.append(", ");
                                    Object arg = callFrame.args[i];
                                    if (arg == null) {
                                        sb.append("null");
                                    } else if (arg instanceof String) {
                                        String s = (String) arg;
                                        sb.append("\"").append(s.length() > 200 ? s.substring(0, 200) + "..." : s).append("\"");
                                    } else if (arg instanceof Number || arg instanceof Boolean) {
                                        sb.append(arg);
                                    } else {
                                        sb.append(arg.getClass().getSimpleName()).append("@").append(Integer.toHexString(System.identityHashCode(arg)));
                                    }
                                }
                            }
                            sb.append(")");
                            Log.i(TAG, sb.toString());
                            addTradeLog("BUY: " + sb.toString());

                            // 如果是关键方法，打印详细调用栈
                            if (mName.contains("request") || mName.contains("submit") || mName.contains("confirm") || mName.contains("send")) {
                                logBuyCallStack();
                            }
                        } catch (Throwable e) {
                            Log.w(TAG, "WTBuyClient method hook error: " + e.getMessage());
                        }
                    }

                    @Override
                    public void afterCall(Pine.CallFrame callFrame) {
                        try {
                            Object result = callFrame.getResult();
                            if (result != null) {
                                String resultStr = result.toString();
                                if (resultStr.length() > 500) resultStr = resultStr.substring(0, 500) + "...";
                                Log.i(TAG, "  WTBuyClient." + mName + " → " + resultStr);
                            }
                        } catch (Throwable ignored) {}
                    }
                });
            }

            Log.i(TAG, "WTBuyConfirmClient hooks installed");

        } catch (ClassNotFoundException e) {
            Log.w(TAG, "WTBuyConfirmClient class not found: " + buyClientClassName);
            // 尝试其他可能的类名
            tryAlternativeBuyClasses(cl);
        }

        // 同时尝试 Hook 卖出类
        hookWTSellConfirmClient(cl);
    }

    /**
     * 尝试其他可能的买入类
     */
    private static void tryAlternativeBuyClasses(ClassLoader cl) {
        String[] alternatives = {
            "com.hexin.android.weituo.trading.buysell.BuyConfirmClient",
            "com.hexin.android.weituo.hstrade.buy.BuyClient",
            "com.hexin.android.weituo.trading.buy.WTBuyClient",
            "com.hexin.android.weituo.trade.buy.BuyRequestClient"
        };

        for (String className : alternatives) {
            try {
                Class<?> clazz = cl.loadClass(className);
                Log.i(TAG, "Found alternative buy class: " + className);
                wtBuyConfirmClientClass = clazz;
                // 可以在这里添加类似的 Hook 逻辑
            } catch (ClassNotFoundException ignored) {}
        }
    }

    /**
     * Hook 卖出入口类（类似买入）
     */
    private static void hookWTSellConfirmClient(ClassLoader cl) {
        String sellClientClassName = "com.hexin.android.weituo.trading.alltradetype.basictrade.buysell.common.wtbuysellreq.client.WTSellConfirmClient";
        try {
            Class<?> sellClass = cl.loadClass(sellClientClassName);
            Log.i(TAG, "=== Found WTSellConfirmClient class! ===");

            // Hook 构造函数
            for (java.lang.reflect.Constructor<?> ctor : sellClass.getDeclaredConstructors()) {
                ctor.setAccessible(true);
                Pine.hook(ctor, new MethodHook() {
                    @Override
                    public void afterCall(Pine.CallFrame callFrame) {
                        Log.i(TAG, "=== WTSellConfirmClient CONSTRUCTED ===");
                        if (callFrame.args != null) {
                            for (int i = 0; i < callFrame.args.length; i++) {
                                Object arg = callFrame.args[i];
                                Log.i(TAG, "  [" + i + "] " + (arg == null ? "null" : arg.getClass().getName()));
                            }
                        }
                    }
                });
            }

            // Hook 所有方法
            for (Method m : sellClass.getDeclaredMethods()) {
                m.setAccessible(true);
                final String mName = m.getName();
                Pine.hook(m, new MethodHook() {
                    @Override
                    public void beforeCall(Pine.CallFrame callFrame) {
                        Log.i(TAG, "WTSellClient." + mName + "() called");
                        addTradeLog("SELL: " + mName);
                    }
                });
            }
            Log.i(TAG, "WTSellConfirmClient hooks installed");
        } catch (ClassNotFoundException e) {
            Log.w(TAG, "WTSellConfirmClient not found");
        } catch (Throwable e) {
            Log.e(TAG, "WTSellConfirmClient hook failed: " + e.getMessage());
        }
    }

    /**
     * Dump 对象的所有字段（用于分析对象结构）
     */
    private static void dumpObjectFields(Object obj, String tag) {
        try {
            Class<?> clazz = obj.getClass();
            StringBuilder sb = new StringBuilder();
            sb.append("=== ").append(tag).append(" Fields ===\n");

            // 遍历所有字段（包括父类）
            while (clazz != null && !clazz.getName().startsWith("java.")) {
                for (java.lang.reflect.Field f : clazz.getDeclaredFields()) {
                    f.setAccessible(true);
                    String fieldName = f.getName();
                    Object value = null;
                    try {
                        value = f.get(obj);
                    } catch (Throwable ignored) {}

                    String valueStr = value == null ? "null" :
                        (value instanceof String) ? "\"" + value + "\"" :
                        (value instanceof Number || value instanceof Boolean) ? value.toString() :
                        value.getClass().getSimpleName() + "@" + Integer.toHexString(System.identityHashCode(value));

                    if (valueStr.length() > 200) {
                        valueStr = valueStr.substring(0, 200) + "...";
                    }

                    sb.append("  ").append(f.getType().getSimpleName()).append(" ").append(fieldName)
                      .append(" = ").append(valueStr).append("\n");
                }
                clazz = clazz.getSuperclass();
            }
            sb.append("=== End Fields ===");
            Log.i(TAG, sb.toString());
        } catch (Throwable e) {
            Log.w(TAG, "dumpObjectFields failed: " + e.getMessage());
        }
    }

    /**
     * 打印买入调用栈
     */
    private static void logBuyCallStack() {
        Throwable t = new Throwable();
        StringBuilder stack = new StringBuilder();
        stack.append("=== BUY CALL STACK ===\n");
        for (StackTraceElement e : t.getStackTrace()) {
            String cn = e.getClassName();
            if (cn.contains("hexin") || cn.contains("weituo") || cn.contains("trade")
                || cn.contains("buy") || cn.contains("sell")) {
                stack.append("  -> ").append(cn).append(".")
                     .append(e.getMethodName()).append(":").append(e.getLineNumber()).append("\n");
            }
        }
        stack.append("=== END STACK ===");
        Log.i(TAG, stack.toString());
        addTradeLog(stack.toString());
    }

    // ========== 交易状态和日志查询 ==========

    /**
     * 获取交易 SDK 状态
     */
    private static String getTradeStatus() {
        StringBuilder sb = new StringBuilder("{");
        sb.append("\"master_bridge_captured\":").append(masterModuleBridgeInstance != null);
        sb.append(",\"request_method_captured\":").append(masterModuleBridgeRequestMethod != null);

        if (masterModuleBridgeRequestMethod != null) {
            sb.append(",\"request_method\":\"").append(masterModuleBridgeRequestMethod.getName()).append("\"");
            sb.append(",\"request_params\":").append(toJsonArray(
                java.util.Arrays.stream(masterModuleBridgeRequestMethod.getParameterTypes())
                    .map(Class::getSimpleName)
                    .toArray(String[]::new)
            ));
        }

        sb.append(",\"fund_key\":\"").append(STOCK_FUND_KEY).append("\"");
        sb.append(",\"stock_account\":\"0926764077\"");
        sb.append(",\"log_count\":").append(recentTradeLogs.size());
        sb.append("}");
        return sb.toString();
    }

    /**
     * 获取最近的交易日志
     */
    private static String getRecentTradeLogs() {
        StringBuilder sb = new StringBuilder("{\"logs\":[");
        synchronized (recentTradeLogs) {
            boolean first = true;
            for (String log : recentTradeLogs) {
                if (!first) sb.append(",");
                first = false;
                sb.append("\"").append(escapeJson(log)).append("\"");
            }
        }
        sb.append("]}");
        return sb.toString();
    }

    /**
     * 通过 WebView 调用 JSBridge（同步等待响应）
     * POST /jsbridge
     * Body: {"handler": "clientRequestHX", "data": {...}}
     */
    private static String callJSBridge(String body) {
        try {
            // 检查 WebView 是否可用
            if (latestWebView == null) {
                return "{\"success\":false,\"error\":\"No WebView available. Please open a fund page first.\"}";
            }

            // 解析请求参数
            String handler = extractJsonString(body, "handler");
            String dataJson = extractJsonObject(body, "data");

            if (handler == null || handler.isEmpty()) {
                return "{\"success\":false,\"error\":\"Missing 'handler' field\"}";
            }
            if (dataJson == null) {
                return "{\"success\":false,\"error\":\"Missing 'data' field\"}";
            }

            Log.i(TAG, "callJSBridge: handler=" + handler + " data=" + dataJson.substring(0, Math.min(200, dataJson.length())));

            // 生成唯一的 callback ID
            final String callbackId = "proxy_" + System.currentTimeMillis();

            // 构造 JavaScript 代码
            String jsCode = "(function() {" +
                    "  if (typeof window.WebViewJavascriptBridge === 'undefined') {" +
                    "    return JSON.stringify({success: false, error: 'WebViewJavascriptBridge not initialized'});" +
                    "  }" +
                    "  var result = {pending: true, callbackId: '" + callbackId + "'};" +
                    "  window.WebViewJavascriptBridge.callHandler('" + handler + "', " + dataJson + ", function(response) {" +
                    "    window._jsbridgeResponse_" + callbackId + " = {success: true, data: response};" +
                    "  });" +
                    "  return JSON.stringify(result);" +
                    "})();";

            // 使用 CountDownLatch 等待响应
            final String[] resultHolder = new String[1];
            final java.util.concurrent.CountDownLatch latch = new java.util.concurrent.CountDownLatch(1);

            // 执行 JavaScript（获取初始状态）
            try {
                // 获取主线程 Handler (声明为 final 以便在内部类中使用)
                final Class<?> looperClass = Class.forName("android.os.Looper");
                final Class<?> handlerClass = Class.forName("android.os.Handler");
                java.lang.reflect.Method getMainLooper = looperClass.getDeclaredMethod("getMainLooper");
                Object mainLooper = getMainLooper.invoke(null);
                java.lang.reflect.Constructor<?> handlerCtor = handlerClass.getConstructor(looperClass);
                final Object mainHandler = handlerCtor.newInstance(mainLooper);
                final java.lang.reflect.Method postMethod = handlerClass.getMethod("post", Runnable.class);

                Class<?> valueCallbackClass = Class.forName("android.webkit.ValueCallback");
                Object callback = java.lang.reflect.Proxy.newProxyInstance(
                        appClassLoader,
                        new Class<?>[]{valueCallbackClass},
                        new java.lang.reflect.InvocationHandler() {
                            @Override
                            public Object invoke(Object proxy, java.lang.reflect.Method method, Object[] args) {
                                if (method.getName().equals("onReceiveValue") && args != null && args.length > 0) {
                                    String initResult = (String) args[0];
                                    Log.i(TAG, "JSBridge initial result: " + initResult);

                                    // 启动轮询获取最终结果
                                    new Thread(() -> {
                                        try {
                                            // 轮询等待 JavaScript callback 完成（最多 30 秒）
                                            for (int i = 0; i < 60; i++) {
                                                Thread.sleep(500);

                                                // 检查响应是否已准备好
                                                String checkJs = "(function() {" +
                                                        "  if (typeof window._jsbridgeResponse_" + callbackId + " !== 'undefined') {" +
                                                        "    var r = window._jsbridgeResponse_" + callbackId + ";" +
                                                        "    delete window._jsbridgeResponse_" + callbackId + ";" +
                                                        "    return JSON.stringify(r);" +
                                                        "  }" +
                                                        "  return null;" +
                                                        "})();";

                                                final String[] checkResult = new String[1];
                                                final java.util.concurrent.CountDownLatch checkLatch = new java.util.concurrent.CountDownLatch(1);

                                                Object checkCallback = java.lang.reflect.Proxy.newProxyInstance(
                                                        appClassLoader,
                                                        new Class<?>[]{valueCallbackClass},
                                                        new java.lang.reflect.InvocationHandler() {
                                                            @Override
                                                            public Object invoke(Object proxy, java.lang.reflect.Method method, Object[] args) {
                                                                if (method.getName().equals("onReceiveValue") && args != null && args.length > 0) {
                                                                    checkResult[0] = (String) args[0];
                                                                    checkLatch.countDown();
                                                                }
                                                                return null;
                                                            }
                                                        });

                                                // 在主线程执行轮询检查
                                                postMethod.invoke(mainHandler, new Runnable() {
                                                    @Override
                                                    public void run() {
                                                        try {
                                                            java.lang.reflect.Method evalMethod = latestWebView.getClass().getMethod(
                                                                    "evaluateJavascript", String.class, valueCallbackClass);
                                                            evalMethod.invoke(latestWebView, checkJs, checkCallback);
                                                        } catch (Exception e) {
                                                            Log.e(TAG, "Failed to execute check JS on main thread", e);
                                                        }
                                                    }
                                                });

                                                checkLatch.await(1, java.util.concurrent.TimeUnit.SECONDS);

                                                if (checkResult[0] != null && !checkResult[0].equals("null")) {
                                                    resultHolder[0] = checkResult[0];
                                                    latch.countDown();
                                                    Log.i(TAG, "JSBridge response received: " + checkResult[0].substring(0, Math.min(500, checkResult[0].length())));
                                                    break;
                                                }
                                            }

                                            // 超时
                                            if (resultHolder[0] == null) {
                                                resultHolder[0] = "{\"success\":false,\"error\":\"Timeout waiting for JSBridge response\"}";
                                                latch.countDown();
                                            }

                                        } catch (Exception e) {
                                            Log.e(TAG, "JSBridge polling error", e);
                                            resultHolder[0] = "{\"success\":false,\"error\":\"Polling error: " + e.getMessage() + "\"}";
                                            latch.countDown();
                                        }
                                    }).start();
                                }
                                return null;
                            }
                        });

                // 在主线程执行 evaluateJavascript
                postMethod.invoke(mainHandler, new Runnable() {
                    @Override
                    public void run() {
                        try {
                            java.lang.reflect.Method evalMethod = latestWebView.getClass().getMethod(
                                    "evaluateJavascript", String.class, valueCallbackClass);
                            evalMethod.invoke(latestWebView, jsCode, callback);
                        } catch (Exception e) {
                            Log.e(TAG, "Failed to invoke evaluateJavascript on main thread", e);
                            resultHolder[0] = "{\"success\":false,\"error\":\"Failed to execute JavaScript: " + e.getMessage() + "\"}";
                            latch.countDown();
                        }
                    }
                });

            } catch (Exception e) {
                Log.e(TAG, "Failed to setup main thread execution", e);
                return "{\"success\":false,\"error\":\"Failed to setup JavaScript execution: " + e.getMessage() + "\"}";
            }

            // 等待响应（最多 35 秒）
            boolean completed = latch.await(35, java.util.concurrent.TimeUnit.SECONDS);
            if (!completed || resultHolder[0] == null) {
                return "{\"success\":false,\"error\":\"Timeout: no response after 35 seconds\"}";
            }

            return resultHolder[0];

        } catch (Throwable e) {
            Log.e(TAG, "callJSBridge failed", e);
            return "{\"success\":false,\"error\":\"" + e.getMessage() + "\",\"stack\":\"" + Log.getStackTraceString(e) + "\"}";
        }
    }

    /**
     * 深度 Hook ClientRequestHX 实例的所有方法
     * 目标：追踪它如何发起 HTTP 请求，找到真实的请求方法
     */
    private static void deepHookClientRequestHX(Object clientRequestInstance) {
        try {
            Class<?> clientRequestClass = clientRequestInstance.getClass();
            Log.i(TAG, "=== Deep hooking ClientRequestHX instance ===");
            Log.i(TAG, "Class: " + clientRequestClass.getName());

            // 列出所有方法
            Method[] allMethods = clientRequestClass.getDeclaredMethods();
            Log.i(TAG, "Total methods: " + allMethods.length);

            for (Method method : allMethods) {
                Class<?>[] paramTypes = method.getParameterTypes();
                StringBuilder paramStr = new StringBuilder();
                for (int i = 0; i < paramTypes.length; i++) {
                    if (i > 0) paramStr.append(", ");
                    paramStr.append(paramTypes[i].getSimpleName());
                }
                Log.i(TAG, "  " + method.getName() + "(" + paramStr.toString() + ") -> " + method.getReturnType().getSimpleName());

                // Hook 所有方法
                try {
                    method.setAccessible(true);
                    Pine.hook(method, new MethodHook() {
                        @Override
                        public void beforeCall(Pine.CallFrame callFrame) {
                            StringBuilder log = new StringBuilder();
                            log.append("ClientRequestHX.").append(method.getName()).append("(");

                            if (callFrame.args != null && callFrame.args.length > 0) {
                                for (int i = 0; i < callFrame.args.length; i++) {
                                    if (i > 0) log.append(", ");
                                    Object arg = callFrame.args[i];
                                    if (arg == null) {
                                        log.append("null");
                                    } else {
                                        String argStr = arg.toString();
                                        if (argStr.length() > 200) {
                                            argStr = argStr.substring(0, 200) + "...[" + argStr.length() + "]";
                                        }
                                        log.append(argStr);
                                    }
                                }
                            }
                            log.append(")");
                            Log.i(TAG, log.toString());

                            // 打印调用堆栈，找出调用链
                            if (method.getName().contains("request") || method.getName().contains("execute")
                                || method.getName().contains("send") || method.getName().contains("call")) {
                                Log.i(TAG, "  [STACK TRACE]:");
                                StackTraceElement[] stack = Thread.currentThread().getStackTrace();
                                for (int i = 0; i < Math.min(15, stack.length); i++) {
                                    StackTraceElement e = stack[i];
                                    if (e.getClassName().contains("hexin") || e.getClassName().contains("okhttp")
                                        || e.getClassName().contains("http")) {
                                        Log.i(TAG, "    -> " + e.getClassName() + "." + e.getMethodName() + ":" + e.getLineNumber());
                                    }
                                }
                            }
                        }

                        @Override
                        public void afterCall(Pine.CallFrame callFrame) {
                            Object result = callFrame.getResult();
                            if (result != null) {
                                String resultStr = result.toString();
                                if (resultStr.length() > 500) {
                                    resultStr = resultStr.substring(0, 500) + "...[" + resultStr.length() + "]";
                                }
                                Log.i(TAG, "  ClientRequestHX." + method.getName() + " -> " + resultStr);
                            }
                        }
                    });
                } catch (Throwable e) {
                    Log.w(TAG, "Failed to hook method: " + method.getName() + " - " + e.getMessage());
                }
            }

            Log.i(TAG, "=== ClientRequestHX deep hooks installed ===");

        } catch (Throwable e) {
            Log.e(TAG, "Failed to deep hook ClientRequestHX", e);
        }
    }

    /**
     * Hook ClientRequestHX 类（从 ClassLoader 加载）
     * 这个方法在 installAllHooks() 中调用，但通常会失败，因为 ClientRequestHX 在 WebView ClassLoader 中
     * 真正的 Hook 发生在 deepHookClientRequestHX() 中
     */
    private static void hookClientRequestHX(ClassLoader cl) throws Throwable {
        Log.i(TAG, "Attempting to hook ClientRequestHX from main ClassLoader (may fail)");
        try {
            Class<?> clientRequestClass = cl.loadClass("com.hexin.android.bank.hxminiapp.js.ClientRequestHX");
            Log.i(TAG, "Found ClientRequestHX in main ClassLoader!");
            // 如果找到了，也可以 Hook，但这种情况很少
        } catch (ClassNotFoundException e) {
            Log.i(TAG, "ClientRequestHX not in main ClassLoader (expected, will hook when WebView loads)");
        }
    }

    /**
     * 拦截 clientRequestHX 调用，用 Native HTTP 库处理请求
     * 返回 non-null 表示成功拦截，null 表示继续原流程
     */
    private static String interceptClientRequestHX(String jsonStr, Object jsBridgeInstance) {
        try {
            // 记录完整的 JSBridge 调用数据
            Log.i(TAG, ">>> clientRequestHX called with: " + jsonStr.substring(0, Math.min(500, jsonStr.length())));

            // 暂时禁用拦截，让原始 JSBridge 处理
            return null;

            /* DISABLED FOR DEBUGGING - START
            // 必须先检查 WebView 是否可用，否则响应无法返回
            if (latestWebView == null) {
                Log.i(TAG, ">>> latestWebView is null, skip interception");
                return null; // 不拦截，让原始 JSBridge 处理
            }

            Log.i(TAG, ">>> Attempting to intercept clientRequestHX");

            // 解析 JSON：[{"handlerName":"clientRequestHX","data":{...},"callbackId":"cb_..."}]
            if (!jsonStr.startsWith("[")) return null;

            // 提取第一个对象
            int firstBrace = jsonStr.indexOf('{');
            int lastBrace = jsonStr.lastIndexOf('}');
            if (firstBrace < 0 || lastBrace < 0) return null;

            String requestJson = jsonStr.substring(firstBrace, lastBrace + 1);

            // 提取 handlerName
            String handlerName = extractJsonString(requestJson, "handlerName");
            if (!"clientRequestHX".equals(handlerName)) return null;

            // 提取 callbackId
            String callbackId = extractJsonString(requestJson, "callbackId");
            if (callbackId == null || callbackId.isEmpty()) {
                Log.w(TAG, "No callbackId found");
                return null;
            }

            // 提取 data 对象
            String dataJson = extractJsonObject(requestJson, "data");
            if (dataJson == null) {
                Log.w(TAG, "No data object found");
                return null;
            }

            // 从 data 中提取请求参数
            String method = extractJsonString(dataJson, "method");
            String url = extractJsonString(dataJson, "url");

            if (url == null || url.isEmpty()) {
                Log.w(TAG, "No URL found");
                return null;
            }

            Log.i(TAG, ">>> Intercepted: " + method + " " + url);

            // 在新线程中发起 Native HTTP 请求
            final Object webView = latestWebView;
            new Thread(() -> {
                try {
                    String responseData = executeNativeHttpRequest(method, url, dataJson);

                    // 构造响应 JSON
                    String response = "{\"responseId\":\"" + callbackId + "\",\"responseData\":" + responseData + "}";

                    // 通过 evaluateJavascript 返回给 H5
                    if (webView != null) {
                        String jsCode = "javascript:(function(){" +
                                "if(window.WebViewJavascriptBridge && " +
                                "typeof window.WebViewJavascriptBridge._handleMessageFromObjC === 'function'){" +
                                "window.WebViewJavascriptBridge._handleMessageFromObjC('" +
                                response.replace("'", "\\'").replace("\n", "\\n") + "');" +
                                "}})();";

                        // 在主线程执行
                        new android.os.Handler(android.os.Looper.getMainLooper()).post(() -> {
                            try {
                                java.lang.reflect.Method evalMethod = webView.getClass().getMethod(
                                        "evaluateJavascript", String.class,
                                        Class.forName("android.webkit.ValueCallback"));
                                evalMethod.invoke(webView, jsCode, null);
                                Log.i(TAG, ">>> Response delivered via evaluateJavascript");
                            } catch (Exception e) {
                                Log.e(TAG, "Failed to deliver response", e);
                            }
                        });
                    }
                } catch (Exception e) {
                    Log.e(TAG, "Native HTTP request failed", e);
                }
            }).start();

            return "intercepted"; // 返回 non-null 表示已拦截
            DISABLED FOR DEBUGGING - END */
        } catch (Exception e) {
            Log.e(TAG, "interceptClientRequestHX failed", e);
            return null;
        }
    }

    /**
     * 使用 Native HTTP 库执行请求
     */
    private static String executeNativeHttpRequest(String method, String url, String dataJson) throws Exception {
        Log.i(TAG, ">>> Executing native HTTP: " + method + " " + url);

        // 提取 params
        String paramsJson = extractJsonObject(dataJson, "params");

        // 提取 Header
        String headerJson = extractJsonObject(dataJson, "Header");

        // 优先使用 OkHttp
        if (domainClients.size() > 0) {
            return executeWithOkHttp(method, url, paramsJson, headerJson);
        } else {
            return executeWithHttpURLConnection(method, url, paramsJson, headerJson);
        }
    }

    /**
     * 使用 OkHttp 执行请求
     */
    private static String executeWithOkHttp(String method, String url, String paramsJson, String headerJson) throws Exception {
        // 获取任意一个 OkHttpClient（优先 trade.5ifund.com 域名的）
        Object client = null;
        for (Object c : domainClients.values()) {
            client = c;
            break;
        }
        if (client == null) {
            throw new Exception("No OkHttpClient available");
        }

        ClassLoader cl = client.getClass().getClassLoader();
        Class<?> requestBuilderClass = cl.loadClass("okhttp3.Request$Builder");
        Class<?> requestClass = cl.loadClass("okhttp3.Request");
        Class<?> responseClass = cl.loadClass("okhttp3.Response");
        Class<?> requestBodyClass = cl.loadClass("okhttp3.RequestBody");
        Class<?> mediaTypeClass = cl.loadClass("okhttp3.MediaType");

        // 创建 Request.Builder
        Object builder = requestBuilderClass.getDeclaredConstructor().newInstance();
        java.lang.reflect.Method urlMethod = requestBuilderClass.getMethod("url", String.class);
        builder = urlMethod.invoke(builder, url);

        // 添加 Headers
        if (headerJson != null && !headerJson.isEmpty()) {
            // TODO: 解析 Header JSON 并添加
        }

        // 构造请求
        if ("POST".equalsIgnoreCase(method) && paramsJson != null && !paramsJson.isEmpty()) {
            // 创建 RequestBody
            java.lang.reflect.Method parseMethod = mediaTypeClass.getMethod("parse", String.class);
            Object mediaType = parseMethod.invoke(null, "application/json; charset=utf-8");

            java.lang.reflect.Method createMethod = requestBodyClass.getMethod("create",
                    mediaTypeClass, String.class);
            Object body = createMethod.invoke(null, mediaType, paramsJson);

            java.lang.reflect.Method postMethod = requestBuilderClass.getMethod("post", requestBodyClass);
            builder = postMethod.invoke(builder, body);
        } else {
            java.lang.reflect.Method getMethod = requestBuilderClass.getMethod("get");
            builder = getMethod.invoke(builder);
        }

        // 构建 Request
        java.lang.reflect.Method buildMethod = requestBuilderClass.getMethod("build");
        Object request = buildMethod.invoke(builder);

        // 执行请求
        java.lang.reflect.Method newCallMethod = client.getClass().getMethod("newCall", requestClass);
        Object call = newCallMethod.invoke(client, request);

        java.lang.reflect.Method executeMethod = call.getClass().getMethod("execute");
        Object response = executeMethod.invoke(call);

        // 读取响应
        java.lang.reflect.Method bodyMethod = responseClass.getMethod("body");
        Object responseBody = bodyMethod.invoke(response);

        java.lang.reflect.Method stringMethod = responseBody.getClass().getMethod("string");
        String responseStr = (String) stringMethod.invoke(responseBody);

        Log.i(TAG, ">>> Native HTTP response: " + responseStr.substring(0, Math.min(500, responseStr.length())));

        return responseStr;
    }

    /**
     * 使用 HttpURLConnection 执行请求
     */
    private static String executeWithHttpURLConnection(String method, String url, String paramsJson, String headerJson) throws Exception {
        Log.i(TAG, ">>> Using HttpURLConnection");

        java.net.URL urlObj = new java.net.URL(url);
        java.net.HttpURLConnection conn = (java.net.HttpURLConnection) urlObj.openConnection();

        conn.setRequestMethod(method);
        conn.setConnectTimeout(30000);
        conn.setReadTimeout(30000);

        // 添加默认 Headers
        conn.setRequestProperty("User-Agent", "Mozilla/5.0");
        conn.setRequestProperty("Accept", "application/json");

        // 从 WebView CookieManager 中读取 Cookie
        try {
            Class<?> cookieManagerClass = Class.forName("android.webkit.CookieManager");
            java.lang.reflect.Method getInstance = cookieManagerClass.getMethod("getInstance");
            Object cookieManager = getInstance.invoke(null);
            java.lang.reflect.Method getCookie = cookieManagerClass.getMethod("getCookie", String.class);
            String cookies = (String) getCookie.invoke(cookieManager, url);
            if (cookies != null && !cookies.isEmpty()) {
                conn.setRequestProperty("Cookie", cookies);
                Log.i(TAG, ">>> Added Cookie: " + cookies.substring(0, Math.min(100, cookies.length())));
            } else {
                Log.w(TAG, ">>> No cookies found for: " + url);
            }
        } catch (Exception e) {
            Log.e(TAG, ">>> Failed to get cookies: " + e.getMessage());
        }

        // 解析并添加自定义 Headers
        if (headerJson != null && !headerJson.isEmpty()) {
            Log.i(TAG, ">>> Adding custom headers from: " + headerJson);
            String[] headerPairs = headerJson.replace("{", "").replace("}", "").split(",");
            for (String pair : headerPairs) {
                int colonIdx = pair.indexOf(":");
                if (colonIdx > 0) {
                    String key = pair.substring(0, colonIdx).trim();
                    String value = pair.substring(colonIdx + 1).trim();
                    // 去掉引号
                    if (key.startsWith("\"") && key.endsWith("\"")) key = key.substring(1, key.length() - 1);
                    if (value.startsWith("\"") && value.endsWith("\"")) value = value.substring(1, value.length() - 1);
                    if (!key.isEmpty() && !value.isEmpty()) {
                        conn.setRequestProperty(key, value);
                        Log.i(TAG, ">>> Added header: " + key + " = " + value.substring(0, Math.min(50, value.length())));
                    }
                }
            }
        }

        if ("POST".equalsIgnoreCase(method) && paramsJson != null && !paramsJson.isEmpty()) {
            conn.setDoOutput(true);
            conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");

            java.io.OutputStream os = conn.getOutputStream();
            os.write(paramsJson.getBytes("UTF-8"));
            os.flush();
            os.close();
        }

        int responseCode = conn.getResponseCode();
        Log.i(TAG, ">>> Response code: " + responseCode);

        java.io.InputStream is = (responseCode < 400) ? conn.getInputStream() : conn.getErrorStream();
        java.io.BufferedReader reader = new java.io.BufferedReader(new java.io.InputStreamReader(is, "UTF-8"));
        StringBuilder sb = new StringBuilder();
        String line;
        while ((line = reader.readLine()) != null) {
            sb.append(line);
        }
        reader.close();
        conn.disconnect();

        String responseStr = sb.toString();
        Log.i(TAG, ">>> HttpURLConnection response: " + responseStr.substring(0, Math.min(500, responseStr.length())));

        return responseStr;
    }
}
