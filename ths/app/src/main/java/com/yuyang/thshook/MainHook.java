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
    private static volatile long lastWithdrawClickMs = 0; // 撤单诊断：确认撤单点击时刻
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
    // 启动期行情线报捕获（2026-08-19）：逆 CBAS hangqing 会话建立用。进程起来
    // 即 arm（不清文件、不被请求级 capture 重置），上限 4MB；POST
    // /native/wire-capture/boot {"armed":false} 关闭。
    private static volatile boolean marketWireBootArmed = false;
    private static final java.util.concurrent.atomic.AtomicLong marketWireBootBytes =
            new java.util.concurrent.atomic.AtomicLong(0);
    private static final long MARKET_WIRE_BOOT_CAP_BYTES = 4L * 1024 * 1024;
    private static final ConcurrentHashMap<String, Boolean> hookedBridgeMethods = new ConcurrentHashMap<>();
    private static final AtomicBoolean tradeAccountRegistryHooked = new AtomicBoolean(false);
    private static final AtomicBoolean tradingSdkBridgeHooked = new AtomicBoolean(false);
    private static final AtomicBoolean tradeLoginDialogHooked = new AtomicBoolean(false);
    private static final AtomicInteger bridgeRetryAttempts = new AtomicInteger(0);
    // hrv.r 在请求风暴期间会被并发频繁调用。必须跨 Hook 回调实例限频，不能在
    // MethodHook 的实例字段中计时（Pine 可能为每次调用创建独立回调状态）。
    private static final AtomicLong lastTradeSessionCheckLogMs = new AtomicLong(0L);
    // 最近一次 resetCbasServer 记录（host:port），/stock/trade/cbas/status 回显
    private static volatile String lastCbasServerReset = "never";
    // 写端点响应旁路捕获（2026-08-18 压测发现：写协议响应帧被 App 常驻观察者
    // 竞争消费，自注册 observer 大概率收不到——操作执行成功但响应丢失）。
    // 写请求发出前按 protocolId 登记 pending，ixm/nxm receive hook 检测到
    // frameId 匹配的帧时旁路复制一份，等待期结束后以旁路结果兜底。
    private static final ConcurrentHashMap<Integer, java.util.List<Object>> pendingWriteStuff =
            new ConcurrentHashMap<>();
    private static final ConcurrentHashMap<Integer, CountDownLatch> pendingWriteLatch =
            new ConcurrentHashMap<>();
    // WT 模块推送事件缓冲（receiveWTModulePush→GBK 明文，kbr.d 已证实）：
    // 委托/成交状态变化的被动通知，写确认从轮询变事件驱动的数据源。
    // 环形 100 条，/stock/trade/push-events 读取。
    private static final java.util.ArrayDeque<String> wtPushEvents = new java.util.ArrayDeque<>();
    private static final Object wtPushLock = new Object();
    private static volatile long wtPushCount = 0;
    // 最近一次成功查询的时间戳（会话新鲜度信号）：写前 60s 内有成功查询
    // 则跳过强制登录（写时长从 ~35s 压到 ~0.2s）；写失败的重试轮仍会强制
    // 登录兜底，跳过判断错误也能自愈
    private static volatile long lastSuccessfulQueryMs = 0L;
    // 最近一次成功写响应的时间戳（2026-08-18 连续写实测）：写后服务端会话即
    // 失效并进入恢复期——期内再写（如 BUY 后撤单）被 onChannelBad(code=1)
    // 静默丢弃，期内强制登录也持续 fail(null)（竞争）。写前等待冷却窗
    // （45s，实测 round2 在 ~77s 发出即成功）避开恢复期。
    private static volatile long lastSuccessfulWriteMs = 0L;
    // CBAS 通道事件状态（2026-08-18 事件驱动重试）：App 原生每 7~8s 检查通道、
    // 断开即重连。此前查询撞上"断开→重连"窗口会白等 15s 再误判强制登录。
    // Socket.connect hook（端口 9528）维护 reconnecting/ready 时间戳，
    // onChannelBad（请求被丢）记录丢帧时刻；查询据此实现：发送前等就绪、
    // 在途被丢等重连完成立即重发（不登录不持锁）。
    private static volatile boolean cbasReconnecting = false;
    private static volatile long lastCbasReadyMs = 0L;
    // 采集就绪追踪（2026-08-19）：hook 起来 ≠ 行情可用。真实判据是 unified 请求
    // 实际成功过（新用户首启卡开户页时 CBAS TCP 能连但无会话，请求全超时）。
    private static volatile long unifiedLastOkMs = 0L;
    private static volatile long unifiedOkCount = 0L;
    private static volatile long unifiedFailCount = 0L;
    private static volatile String unifiedLastFailReason = "";
    private static final java.util.concurrent.atomic.AtomicBoolean faceSdkGuardHooked =
            new java.util.concurrent.atomic.AtomicBoolean(false);
    private static volatile long lastCbasBadMs = 0L;
    private static final Object cbasSignal = new Object();
    // 最近一次 MasterBridge.getConfigInfo 的 query/result 原文（native→Java 要
    // 设备配置的唯一通道，设备指纹观测与伪造的核心数据源）
    private static volatile String lastGetConfigInfoQuery = "";
    private static volatile String lastGetConfigInfoResult = "";
    // 设备指纹伪造状态（filesDir/thshook_spoof.json 持久化）：
    // udids=绑定设备ID(n6m.l)、udidKey=加密key(n6m.s)、getconfiginfo=整段替换
    private static volatile String spoofUdidL = "";
    private static volatile String spoofUdidS = "";
    private static volatile String spoofGetConfigInfo = "";
    private static volatile boolean deviceSpoofInstalled = false;
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
    // 写交易协议（买入/卖出/撤单/转账）的完整请求捕获。key = "pageId_protocolId"，
    // value = 完整 params（含 source 签名后缀）。股票代码/价格/数量 + 固定签名，
    // 无账户凭据；仅供逆向分析与端点构造参考。
    private static final ConcurrentHashMap<String, String> capturedWriteRequests =
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
                injectedViaLsposed = true;
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

        // 无人值守加固：阻断交易登录失败弹窗（x0s.S → TradeFeedback 一键反馈框）。
        // 实测 App 自身静默重登失败会循环弹"登录失败/登录超时"挡屏；登录态由
        // POST /stock/trade/login 主动端点管理（2026-08-17 真机验证 success），
        // UI 反馈在无人值守场景无价值。
        if (!tradeLoginDialogHooked.get()) {
            try {
                hookTradeLoginDialogSuppress(cl);
                tradeLoginDialogHooked.set(true);
                Log.i(TAG, "hookTradeLoginDialogSuppress done");
            } catch (Throwable e) {
                Log.e(TAG, "hookTradeLoginDialogSuppress failed (will retry with next classLoader)", e);
            }
        }

        // 无人值守加固：开户页退出路径 FS_Init 主线程卡死防护（模拟器人脸引擎
        // native 永久阻塞 → ANR，2026-08-19 实测 ANR 栈定位）。
        if (!faceSdkGuardHooked.get()) {
            try {
                hookFaceSdkAnrGuard(cl);
                faceSdkGuardHooked.set(true);
                Log.i(TAG, "hookFaceSdkAnrGuard done");
            } catch (Throwable e) {
                Log.e(TAG, "hookFaceSdkAnrGuard failed (will retry with next classLoader)", e);
            }
        }

        // 以下 hooks 只在首次运行时安装
        if (firstRun) {
            try { armMarketWireBootCapture(); }
            catch (Throwable e) { Log.e(TAG, "boot wire capture arm failed", e); }

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

    /** 无人值守加固（2026-08-19）：开户页退出路径会在主线程同步调
     *  FS_SDKEngine.FS_Init（人脸引擎 native 初始化）。模拟器无人脸硬件，
     *  native 层永久阻塞且持 SynchronizedLazyImpl 锁 → 主线程 ANR 卡死
     *  （实测 ANR 栈：H5KaihuBrowserActi.finish → s.b lazy → FS_Init Native）。
     *  返回 0 伪装成功，跳过 native 调用。 */
    private static void hookFaceSdkAnrGuard(ClassLoader cl) throws Exception {
        Class<?> engineClass = cl.loadClass("com.hexin.facestate.FS_SDKEngine");
        Method target = null;
        for (Method m : engineClass.getDeclaredMethods()) {
            if ("FS_Init".equals(m.getName())) {
                target = m;
                break;
            }
        }
        if (target == null) {
            throw new NoSuchMethodException("FS_Init not found on FS_SDKEngine");
        }
        Pine.hook(target, new MethodHook() {
            @Override
            public void beforeCall(Pine.CallFrame callFrame) {
                callFrame.setResult(0);
                Log.i(TAG, "FS_Init skipped (emulator face-engine ANR guard)");
            }
        });
        Log.i(TAG, "FS_SDKEngine.FS_Init ANR guard installed");
    }

    /** 无人值守加固：阻断交易登录失败弹窗（x0s.S，@MainThread）。 */
    private static void hookTradeLoginDialogSuppress(ClassLoader cl) throws Exception {        Class<?> x0sClass = cl.loadClass("x0s");
        Method sM = x0sClass.getDeclaredMethod("S", int.class);
        Pine.hook(sM, new MethodHook() {
            @Override
            public void beforeCall(Pine.CallFrame callFrame) {
                callFrame.setResult(null);
                Log.i(TAG, "trade login dialog suppressed (recode=" + callFrame.args[0] + ")");
            }
        });
        Log.i(TAG, "x0s.S login-dialog suppress hook installed");
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

    /** 逐字节读一行（去 \r\n；EOF 且无内容返回 null）。字节层面读取，
     *  与后续 body 的字节精确读保持同一流位置（不走 Reader 的预读）。 */
    private static String readRawLine(java.io.InputStream in) throws java.io.IOException {
        StringBuilder sb = new StringBuilder(96);
        int c;
        while ((c = in.read()) != -1) {
            if (c == '\n') break;
            sb.append((char) c);
        }
        if (c == -1 && sb.length() == 0) return null;
        int len = sb.length();
        if (len > 0 && sb.charAt(len - 1) == '\r') sb.setLength(len - 1);
        return sb.toString();
    }

    /** 等待 CBAS 通道重连完成（事件驱动：Socket.connect success 的 notifyAll
     *  唤醒；上限 maxMs）。返回时若 lastCbasReadyMs 更新过即视为就绪。 */
    private static void waitForCbasReady(long maxMs) {
        long deadline = System.currentTimeMillis() + maxMs;
        while (System.currentTimeMillis() < deadline) {
            if (!cbasReconnecting && lastCbasReadyMs > 0) return;
            long left = deadline - System.currentTimeMillis();
            if (left <= 0) break;
            synchronized (cbasSignal) {
                try { cbasSignal.wait(Math.min(500, left)); } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    return;
                }
            }
        }
    }

    /** 发送前检查：App 正在重连 CBAS 时等待就绪再发。返回是否真的等待过。 */
    private static boolean waitForCbasReadyIfReconnecting(long maxMs) {
        if (!cbasReconnecting) return false;
        waitForCbasReady(maxMs);
        return true;
    }

    private static void handleProxyRequest(Socket client, ClassLoader cl) {
        try {
            // 180s：写端点全流程（pre-login+写+状态确认）实测可达 ~90s，
            // 30s 读超时会在慢 handler 期间因缓冲读的隐蔽阻塞掐断连接
            // （客户端表现为 30.0s "Server disconnected"）
            client.setSoTimeout(300000);
            // 2026-08-18 关键修复：body 必须按字节流读。此前用 BufferedReader
            // 的 char[] 按 Content-Length（字节数）读——body 含中文（如撤单的
            // stock_name，UTF-8 3字节/字）时 char 数 < 字节数，read 永久阻塞
            // 等待不存在的数据（实测含中文的 POST 全部挂起、纯 ASCII 的 BUY
            // 正常）。请求行/headers 逐字节读避免 BufferedReader 预读越界。
            java.io.BufferedInputStream rawIn = new java.io.BufferedInputStream(
                    client.getInputStream());
            OutputStream out = client.getOutputStream();

            // 读取 HTTP 请求行
            String requestLine = readRawLine(rawIn);
            if (requestLine == null) { client.close(); return; }

            if ("THSSTREAM/1".equals(requestLine)) {
                client.setSoTimeout(0);
                handleNativeRealtimeStream(
                        client,
                        new BufferedReader(new InputStreamReader(rawIn, "UTF-8")),
                        out,
                        resolveAppClassLoader(cl));
                return;
            }

            // 读取 headers
            int contentLength = 0;
            String line;
            while ((line = readRawLine(rawIn)) != null && !line.isEmpty()) {
                if (line.toLowerCase().startsWith("content-length:")) {
                    contentLength = Integer.parseInt(line.substring(15).trim());
                }
            }

            // 读取 body（字节精确读 + UTF-8 解码）
            String body = "";
            if (contentLength > 0) {
                byte[] buf = new byte[contentLength];
                int read = 0;
                while (read < contentLength) {
                    int n = rawIn.read(buf, read, contentLength - read);
                    if (n == -1) break;
                    read += n;
                }
                body = new String(buf, 0, read, "UTF-8");
            }

            Log.i(TAG, "Proxy request: " + requestLine + " body=" + body.substring(0, Math.min(200, body.length())));

            if (requestLine.startsWith("GET /health")) {
                long unifiedLastOkAge = unifiedLastOkMs > 0
                        ? System.currentTimeMillis() - unifiedLastOkMs : -1L;
                // collector_ready：unified 请求 10 分钟内真实成功过（LB/CI 门禁用）。
                // hook_ready 只代表注入 HTTP 服务存活，不代表行情可用。
                boolean collectorReady = unifiedLastOkAge >= 0 && unifiedLastOkAge < 600_000L;
                sendResponse(out, 200, "{\"ok\":true,\"mode\":\"injected_core_probe\""
                        + ",\"build\":\"20260819-write-ready-gate-v14-wirecap\""
                        + ",\"android_user_id\":" + androidUserId()
                        + ",\"pid\":" + android.os.Process.myPid()
                        + ",\"listen_port\":" + proxyPortForCurrentUser()
                        + ",\"hook_ready\":true"
                        + ",\"collector_ready\":" + collectorReady
                        + ",\"unified_ok_count\":" + unifiedOkCount
                        + ",\"unified_fail_count\":" + unifiedFailCount
                        + ",\"unified_last_ok_age_ms\":" + unifiedLastOkAge
                        + ",\"unified_last_fail_reason\":\"" + esc(unifiedLastFailReason) + "\""
                        + ",\"trade_hook_ready\":" + tradingSdkBridgeHooked.get()
                        + ",\"cbas_reconnecting\":" + cbasReconnecting
                        + ",\"cbas_ready_age_ms\":" + (lastCbasReadyMs > 0
                            ? System.currentTimeMillis() - lastCbasReadyMs : -1)
                        + ",\"realtime_stream_sessions\":"
                        + realtimeStreamSessions.get() + "}");
                client.close();
                return;
            }

            if (requestLine.startsWith("POST /native/wire-capture/boot")) {
                // 启动期捕获开关：body {"armed":false} 停止（常规采集实例稳定后
                // 关闭以省 IO；逆向分析实例保持开启）。
                boolean armOn = !"false".equals(extractJsonString(body, "armed"));
                if (armOn) {
                    armMarketWireBootCapture();
                } else {
                    marketWireBootArmed = false;
                    marketWireCaptureActive.set(0);
                    Log.i(TAG, "RT_WIRE boot capture disarmed");
                }
                sendResponse(out, 200, "{\"success\":true,\"boot_armed\":" + marketWireBootArmed
                        + ",\"boot_bytes\":" + marketWireBootBytes.get() + "}");
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

            // 交易主实例门禁：未配置 thshook_trade_role.json 的实例拒绝全部交易
            // 端点（登录/查询/下单/token），防止多实例并发登录互顶会话。
            // /stock/trade/role 自身豁免，否则无法在本实例开启。
            if (requestLine.contains("/stock/trade/")
                    && !requestLine.contains("/stock/trade/role")
                    && !isTradeRoleEnabled()) {
                sendResponse(out, 403,
                        "{\"ok\":false,\"error\":\"trade disabled on this instance\"}");
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

            // App 内交易运行时状态机。服务端只负责调用，不再用 UI 或固定
            // systemd sleep 猜测交易模块是否完成初始化。
            if (requestLine.startsWith("GET /stock/trade/runtime/status")) {
                sendResponse(out, 200, handleTradeRuntimeStatus());
                client.close();
                return;
            }

            // 幂等初始化：启动 CommunicationService、恢复交易模块/账户/登录，
            // 最后执行只读持仓探针。绝不调用下单、撤单或转账协议。
            if (requestLine.startsWith("POST /stock/trade/runtime/ensure")) {
                sendResponse(out, 200, handleTradeRuntimeEnsure(body));
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

            // GET /stock/trade/write-captures — 导出写协议（买/卖/撤单/转账）捕获的
            // 完整请求参数（含 source 签名），以及各查询协议当前缓存的 params。
            if (requestLine.startsWith("GET /stock/trade/write-captures")) {
                String result = getTradeWriteCaptures();
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // POST /stock/trade/login — 主动登录执行器（token 方式，替代盲等 App
            // 静默重登；2026-08-17 开盘实测 App 自身重登也会超时弹"登录失败"）
            if (requestLine.startsWith("POST /stock/trade/login")) {
                String result = handleTradeLogin(body);
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // POST /stock/trade/pwd — 设置/清除交易密码（token 过期时自动密码登录）；
            // GET 查询是否已配置。密码不回显不进日志。
            if (requestLine.startsWith("POST /stock/trade/pwd")
                    || requestLine.startsWith("GET /stock/trade/pwd")) {
                String result = handleTradePassword(body);
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // GET /stock/trade/token/export — 导出当前交易 token 明文（跨设备共享
            // 实验：生产 VM 复用手机端刷新的 token，避免 VM 内人工登录）
            if (requestLine.startsWith("GET /stock/trade/token/export")) {
                String result = handleTradeTokenExport();
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // POST /stock/trade/token/import — 写回明文 token 并验证登录（body
            // {"token","time","login":true}，走官方 z7m.o 入口本机重加密入库）
            if (requestLine.startsWith("POST /stock/trade/token/import")) {
                String result = handleTradeTokenImport(body);
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // GET /stock/trade/account/export — 导出交易账户对象（官方 B() 序列化
            // + 元数据），供另一台设备 seed。⚠ 含 compwd/资金账号，受控通道专用。
            if (requestLine.startsWith("GET /stock/trade/account/export")) {
                String result = handleTradeAccountExport();
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // POST /stock/trade/account/seed — 写入导出的交易账户（C() 反序列化 →
            // n0s.b 仓库 → izr.a.x 激活 → 轮询 F(119)），供 token import 前置。
            if (requestLine.startsWith("POST /stock/trade/account/seed")) {
                String result = handleTradeAccountSeed(body);
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // GET /stock/trade/token/report — token 自动上报状态与当前配置（打码）
            if (requestLine.startsWith("GET /stock/trade/token/report")) {
                String result = handleTokenReportStatus();
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // POST /stock/trade/token/report — 配置自动上报（url/api_key/enabled，
            // force=true 立即触发一次导出上报；持久化到 filesDir）
            if (requestLine.startsWith("POST /stock/trade/token/report")) {
                String result = handleTokenReportConfig(body);
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // GET /stock/trade/role — 交易主实例角色状态（本实例是否启用交易）
            if (requestLine.startsWith("GET /stock/trade/role")) {
                String result = handleTradeRoleStatus();
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // POST /stock/trade/role — 启用/停用本实例交易能力（body
            // {"enabled":bool}，持久化 filesDir/thshook_trade_role.json）。
            // 主实例运维端点：只有配置过的实例可登录/查询/下单。
            if (requestLine.startsWith("POST /stock/trade/role")) {
                String result = handleTradeRoleConfig(body);
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // GET /stock/trade/cbas — CBAS（交易通道）诊断：地址列表与最近设置记录
            if (requestLine.startsWith("GET /stock/trade/cbas")) {
                String result = handleTradeCbasStatus();
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // POST /stock/trade/cbas — 手动设置 CBAS 服务器（body {"host","port"}）。
            // AVD 无头环境推送组件可能不设置 CBAS 地址（o2u.p()=null → hrv.C no-op），
            // 交易通道永远建立不了；从真机抓地址后由此注入（调用官方
            // CommunicationService.resetCbasServer，与 PushConnect 同一路径）。
            if (requestLine.startsWith("POST /stock/trade/cbas")) {
                String result = handleTradeCbasSet(body);
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // GET /stock/trade/device-info — 设备指纹观测（UDID/机型/最近一次
            // getConfigInfo query+result 原文），跨设备 diff 用
            if (requestLine.startsWith("GET /stock/trade/device-info")) {
                String result = handleTradeDeviceInfo();
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // GET /stock/trade/push-events — WT 模块推送事件（委托/成交状态变化
            // 的被动通知，GBK 明文环形缓冲 100 条）
            if (requestLine.startsWith("GET /stock/trade/push-events")) {
                String result = handleTradePushEvents();
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // POST /stock/trade/device-spoof — 设备指纹伪造（body {udid_l, udid_s,
            // getconfiginfo, model, brand, enabled}，持久化 filesDir/thshook_spoof.json）。
            // 用途：让 AVD 向券商呈现真机的设备标识（跨设备 token 绑定校验实验）。
            // 注意 udid 同时是 token 加密 key——必须先 spoof 再 import token。
            if (requestLine.startsWith("POST /stock/trade/device-spoof")) {
                String result = handleTradeDeviceSpoof(body);
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // POST /stock/trade/order — 买卖委托执行器（真实下单，confirm=true 必填）
            if (requestLine.startsWith("POST /stock/trade/order")) {
                String result = handleTradeOrder(body);
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // POST /stock/trade/cancel — 撤单执行器（v3p 协议 25102，真机已验证：
            // 撤 1401/1404 成功、假单号返回券商业务错误 250001）
            if (requestLine.startsWith("POST /stock/trade/cancel")) {
                String result = handleTradeCancel(body);
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // GET /stock/trade/transfer/banks — 存管银行列表（只读）
            if (requestLine.startsWith("GET /stock/trade/transfer/banks")) {
                String result = handleTransferBanks();
                sendResponse(out, 200, result);
                client.close();
                return;
            }

            // POST /stock/trade/transfer — 银证转账转入（源码参数，未重放验证）
            if (requestLine.startsWith("POST /stock/trade/transfer")) {
                String result = handleTradeTransfer(body);
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
                unifiedFailCount++;
                unifiedLastFailReason = "timeout";
                return "{\"success\":false,\"error\":\"unified request timed out\"}";
            }

            Object value = response.get();
            if (value == null) {
                unifiedFailCount++;
                unifiedLastFailReason = "missing response";
                return "{\"success\":false,\"error\":\"unified response missing\"}";
            }
            boolean success = unifiedResponseSucceeded(value);
            if (success) {
                unifiedLastOkMs = System.currentTimeMillis();
                unifiedOkCount++;
            } else {
                unifiedFailCount++;
                unifiedLastFailReason = "business error";
            }
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
        if (marketWireBootArmed) return; // 启动期捕获进行中，保留 boot 全量线报
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
        if (marketWireBootArmed) return;
        marketWireCaptureActive.set(0);
        Log.i(TAG, "RT_WIRE capture finished id=" + marketWireCaptureId);
    }

    /** 启动期线报捕获：进程启动即调用（installAllHooks firstRun），记录 App
     *  自身（主页面渲染）建立 CBAS hangqing 会话的完整握手，供逆向复刻。 */
    private static void armMarketWireBootCapture() {
        marketWireBootArmed = true;
        marketWireBootBytes.set(0);
        marketWireCaptureId = System.currentTimeMillis() + "-boot";
        marketWireCaptureActive.set(1);
        Log.i(TAG, "RT_WIRE boot capture armed id=" + marketWireCaptureId
                + " (cap " + MARKET_WIRE_BOOT_CAP_BYTES + " bytes)");
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
        if (marketWireBootArmed
                && marketWireBootBytes.addAndGet(length) > MARKET_WIRE_BOOT_CAP_BYTES) {
            return; // 启动期捕获超限，静默丢弃（boot 样本早已覆盖握手阶段）
        }
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
            String meta = ",\"boot_armed\":" + marketWireBootArmed
                    + ",\"boot_bytes\":" + marketWireBootBytes.get();
            if (file == null || !file.exists()) {
                return "{\"success\":true,\"capture_id\":" + jsonValue(marketWireCaptureId)
                        + meta + ",\"records\":[]}";
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
                        + meta + ",\"records\":[" + records + "]}";
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
        // 非引号值（true/false/数字）：读原始 token（支持 "confirm":true 裸布尔）
        int end = idx;
        while (end < json.length() && json.charAt(end) != ',' && json.charAt(end) != '}'
                && json.charAt(end) != '\n' && json.charAt(end) != '\r') end++;
        String raw = json.substring(idx, end).trim();
        return raw.isEmpty() ? null : raw;
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
            // 失败路径自续重试（installAllHooks 每个ClassLoader只调度一次，
            // 若不加此链，真机 2 次调用都错过 r9h.d=true 即永久放弃）
            scheduleTradingSdkBridgeRetry(cl);
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
                scheduleTradingSdkBridgeRetry(cl);
                return;
            }
            Log.i(TAG, "MasterModule initialized (r9h.d=true), safe to hook");
        } catch (Throwable e) {
            Log.w(TAG, "r9h ready-check unavailable: " + e + " — skip hook attempt");
            scheduleTradingSdkBridgeRetry(cl);
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
                    // 撤单诊断：确认撤单点击后 3s 内的请求打印调用栈（撤单 Tab 的提交
                    // 不经过 rpv.G/mrv.O，路径未知——栈是唯一定位手段）
                    if (System.currentTimeMillis() - lastWithdrawClickMs < 3000) {
                        StackTraceElement[] st = new Throwable().getStackTrace();
                        StringBuilder sb = new StringBuilder("WithdrawSendStack:");
                        int n = 0;
                        for (StackTraceElement e : st) {
                            String cn = e.getClassName();
                            if (cn.startsWith("java.") || cn.startsWith("android.")
                                    || cn.startsWith("com.yuyang.")) continue;
                            sb.append(' ').append(cn).append('.').append(e.getMethodName());
                            if (++n >= 18) break;
                        }
                        Log.i(TAG, sb.toString());
                        addTradeLog(sb.toString());
                    }
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

        // 登录路径诊断（AVD 排查用，W 级保证 logcat 可见）：登录请求在
        // mrv 会话检查层被静默吞（无 jniRequest 无回调），需要定位卡点
        try {
            hookTradeLoginPathDiagnostics(cl);
        } catch (Throwable e) {
            Log.w(TAG, "login-path diagnostics hook failed: " + e);
        }

        // 2026-08-18 crash 防护：rpv.X/Y/C 对 this.d（mqv 通信服务）裸调用，
        // 冷启动窗口 uqv.e() 从 Map<Integer,mqv> 取不到服务注入 null →
        // request() → this.d.a() 主线程 NPE，App 每分钟 crash 循环
        // （dropbox: rpv.X(SourceFile:4) ← mrv.request ← sov.run）。
        // 防护两件套：① X/Y/C beforeCall 检查 d==null 时丢弃请求防崩；
        // ② uqv.e afterCall 对创建出的 rpv 补注入（从注册表重取非空 mqv）。
        try {
            hookTradeCommServiceGuard(cl);
        } catch (Throwable e) {
            Log.w(TAG, "comm-service guard hook failed: " + e);
        }

        // 2026-08-18 token 时间源防护：TokenInfo.isAvailable 用 ghi.m().p()
        // （yb6.a.b() App 时间源）校验过期——模拟器环境该时间源异常（未同步/
        // 返回 0）时 token 永远判过期，z7m.i 恒 null，登录死锁。改用系统时钟
        // 等价校验：mLiveTime>0 且按 System.currentTimeMillis 计算未过期则
        // 强制返回 true。真过期场景由券商登录回调明确拒绝，无安全风险。
        try {
            Class<?> tokenInfoClass = cl.loadClass(
                    "com.hexin.android.weituo.hstrade.feature.login.bindlogin.model.TokenInfo");
            Method isAvail = tokenInfoClass.getDeclaredMethod("isAvailable");
            isAvail.setAccessible(true);
            Pine.hook(isAvail, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    try {
                        Object ti = callFrame.thisObject;
                        int liveTime = ti.getClass().getField("mLiveTime").getInt(ti);
                        String lbt = (String) ti.getClass().getField("lastBindingTime").get(ti);
                        if (liveTime <= 0 || lbt == null || lbt.isEmpty()) return;
                        long ageMs = System.currentTimeMillis()
                                - Long.parseLong(lbt) * 1000L;
                        if (ageMs >= 0 && ageMs < liveTime * 60000L) {
                            callFrame.setResult(Boolean.TRUE);
                        }
                    } catch (Throwable ignored) {
                    }
                }
            });
            Log.w(TAG, "token isAvailable clock-guard installed");
        } catch (Throwable e) {
            Log.w(TAG, "token isAvailable guard failed: " + e.getMessage());
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

        // 撤单差异诊断：mrv.O() 是写请求发送前最后一站（栈：request→X→mrv.O→mrv.k0→
        // r9h.o）。dump 写协议 mhv 全字段，用于对比 App 端请求与端点重放请求的完整差异
        // （params 逐字节一致但服务端结果不同 → 差异必在 mhv 其他字段/信封）
        try {
            Class<?> mrvClass = cl.loadClass("mrv");
            Class<?> mhvClassD = cl.loadClass("mhv");
            java.lang.reflect.Field fMhvOfMrv = mrvClass.getField("a"); // mhv 字段声明在父类 fhv，getField 走继承链
            fMhvOfMrv.setAccessible(true);
            java.lang.reflect.Field fProtoD = mhvClassD.getDeclaredField("c");
            fProtoD.setAccessible(true);
            Method oMethod = mrvClass.getDeclaredMethod("O");
            oMethod.setAccessible(true);
            Pine.hook(oMethod, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    try {
                        Object mhv = fMhvOfMrv.get(callFrame.thisObject);
                        if (mhv == null) return;
                        int proto = fProtoD.getInt(mhv);
                        // 25102=撤买/撤卖批量撤单（v3p QuanCheClient）, 25106=全撤,
                        // 1823=闪电撤单（v1p）, 22157=b8p 旧链路（当前券商不走）
                        if (proto != 22157 && proto != 1820 && proto != 1821
                                && proto != 1823 && proto != 25102 && proto != 25106) return;
                        StringBuilder sb = new StringBuilder("MhvDump proto=").append(proto).append(' ');
                        String fullParams = null;
                        int pageIdDump = -1;
                        for (java.lang.reflect.Field f : mhvClassD.getFields()) {
                            Object v;
                            try { v = f.get(mhv); } catch (Throwable ignored) { continue; }
                            String vs;
                            if (v == null) vs = "null";
                            else if (v instanceof byte[]) vs = "bytes(" + ((byte[]) v).length + ")";
                            else vs = String.valueOf(v);
                            if ("f".equals(f.getName()) && v instanceof String) {
                                fullParams = (String) v; // params 全量留档
                            }
                            if ("b".equals(f.getName()) && v instanceof Integer) {
                                pageIdDump = (Integer) v;
                            }
                            if (vs.length() > 500) vs = vs.substring(0, 500) + "..";
                            sb.append(f.getName()).append('=').append(vs).append('|');
                        }
                        Log.i(TAG, sb.toString());
                        addTradeLog(sb.toString());
                        // 写协议 params 自动捕获（含 App 侧 G() 链路——G hook 实测拦不到
                        // v1p 的调用，此处 O() 是发送必经点，更可靠）
                        if (fullParams != null) {
                            capturedQueryParams.put(proto, fullParams);
                            capturedQueryPageIds.put(proto, new int[]{pageIdDump, proto});
                        }
                    } catch (Throwable t) {
                        Log.i(TAG, "MhvDump fail: " + t);
                    }
                }
            });
            Log.i(TAG, "mrv.O mhv-dump hook installed");
        } catch (Throwable e) {
            Log.w(TAG, "mrv.O mhv-dump hook failed: " + e.getMessage());
        }

        // 通用发送点 aqv.a(mhv)：所有 rpv 构建的请求（含流式 P(pageId).U(protoId)
        // .R(observer).c0(params).request() 链，如撤单 b8p 不经过 G/H）最终都汇入
        // communicationManager.a(mhv)。mhv 字段：b=pageId c=protocolId d=observerId
        // f=params（终态，含 gyh.a() 拼好的 source 签名）。
        // 写协议全集在此完整记录；带 source 签名的未知交易区协议（如转账）也会命中，
        // 用于发现尚未定位的写协议。查询协议静默兜底存储。
        try {
            Class<?> aqvClass = cl.loadClass("aqv");
            Class<?> mhvClass = cl.loadClass("mhv");
            java.lang.reflect.Field fPage = mhvClass.getDeclaredField("b");
            java.lang.reflect.Field fProto = mhvClass.getDeclaredField("c");
            java.lang.reflect.Field fParams = mhvClass.getDeclaredField("f");
            fPage.setAccessible(true);
            fProto.setAccessible(true);
            fParams.setAccessible(true);
            Method sendMhv = aqvClass.getDeclaredMethod("a", mhvClass);
            sendMhv.setAccessible(true);
            Pine.hook(sendMhv, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    try {
                        Object mhv = callFrame.args[0];
                        if (mhv == null) return;
                        int pageId = fPage.getInt(mhv);
                        int protoId = fProto.getInt(mhv);
                        String params = (String) fParams.get(mhv);
                        boolean isWrite = TRADE_WRITE_PROTOCOLS.contains(protoId);
                        // source 签名目前只在写请求上出现（gyh.a 由买/卖/撤单客户端调用）
                        boolean signedUnknown = !isWrite && params != null
                                && params.contains("source=")
                                && pageId >= 2600 && pageId <= 2860;
                        if (isWrite || signedUnknown) {
                            String safeParams = params == null ? "null"
                                    : params.replace("\r", "\\r").replace("\n", "\\n");
                            String logMsg = "TradeWrite.Send pageId=" + pageId
                                    + " protocolId=" + protoId + " params=" + safeParams;
                            Log.i(TAG, logMsg);
                            addTradeLog(logMsg);
                            capturedWriteRequests.put(pageId + "_" + protoId, params);
                        } else if (params != null && protoId != 0) {
                            boolean knownQuery = capturedQueryPageIds.containsKey(protoId);
                            if (!knownQuery) {
                                for (int[] pair : TRADE_QUERY_PROTOCOLS.values()) {
                                    if (pair[0] == protoId) {
                                        knownQuery = true;
                                        break;
                                    }
                                }
                            }
                            if (knownQuery) {
                                // 查询协议兜底：H/G 钩未就绪时错过的流式查询也能补上
                                capturedQueryParams.put(protoId, params);
                                capturedQueryPageIds.put(protoId, new int[]{pageId, protoId});
                            }
                        }
                    } catch (Throwable ignored) {
                    }
                }
            });
            Log.i(TAG, "aqv.a universal send hook installed");
        } catch (Throwable e) {
            Log.w(TAG, "aqv.a hook failed: " + e.getMessage());
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
                    capturePendingWrite(callFrame.args[0], "ixm");
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
                    capturePendingWrite(callFrame.args[0], "nxm");
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
                        // WT 推送（GBK 明文：委托/成交状态变化通知）进环形缓冲，
                        // /stock/trade/push-events 读取——写确认事件驱动的数据源
                        if ("receiveWTModulePush".equals(methodName)
                                && callFrame.args != null && callFrame.args.length > 0
                                && callFrame.args[0] instanceof byte[]) {
                            try {
                                String push = new String((byte[]) callFrame.args[0], "GBK");
                                synchronized (wtPushLock) {
                                    wtPushCount++;
                                    wtPushEvents.addLast(
                                            new java.text.SimpleDateFormat("HH:mm:ss.SSS")
                                                    .format(new java.util.Date()) + " " + push);
                                    while (wtPushEvents.size() > 100) wtPushEvents.pollFirst();
                                }
                                Log.w(TAG, "WTPush[" + wtPushCount + "]: "
                                        + push.substring(0, Math.min(push.length(), 120)));
                            } catch (Throwable pushEx) {
                                Log.w(TAG, "WTPush decode failed: " + pushEx);
                            }
                        }

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
                        // getConfigInfo：native 向 Java 要设备配置——存原文供
                        // /stock/trade/device-info 观测；spoofGetConfigInfo 为
                        // 字段级覆盖 JSON（{"Userid":"...","MobileType":"..."}），
                        // 动态字段（SysBootTime/SessionId 等）保持本机值
                        if ("getConfigInfo".equals(methodName)) {
                            Object q = callFrame.args != null && callFrame.args.length > 0
                                    ? callFrame.args[0] : null;
                            if (q instanceof String) lastGetConfigInfoQuery = (String) q;
                            Object r = callFrame.getResult();
                            if (r instanceof String) lastGetConfigInfoResult = (String) r;
                            if (!spoofGetConfigInfo.isEmpty()
                                    && r instanceof String) {
                                try {
                                    org.json.JSONObject resp =
                                            new org.json.JSONObject((String) r);
                                    org.json.JSONObject data = resp.optJSONObject("data");
                                    if (data != null) {
                                        org.json.JSONObject patch =
                                                new org.json.JSONObject(spoofGetConfigInfo);
                                        java.util.Iterator<String> it = patch.keys();
                                        int applied = 0;
                                        while (it.hasNext()) {
                                            String k = it.next();
                                            data.put(k, patch.get(k));
                                            applied++;
                                        }
                                        callFrame.setResult(resp.toString());
                                        Log.w(TAG, "DeviceSpoof.getConfigInfo patched fields="
                                                + applied);
                                    }
                                } catch (Throwable patchEx) {
                                    Log.w(TAG, "DeviceSpoof.getConfigInfo patch failed: "
                                            + patchEx);
                                }
                            }
                        }
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
            // z7m.w(pzr, WtToken, Time) — token 明文唯一经过点（持久化前会被
            // 本机密钥加密成 encryptedPwd），捕获供 token 导出端点兜底
            try {
                Class<?> z7mClass = cl.loadClass("z7m");
                Pine.hook(z7mClass.getDeclaredMethod("w",
                                cl.loadClass("pzr"), String.class, String.class),
                        new MethodHook() {
                            @Override
                            public void beforeCall(Pine.CallFrame callFrame) {
                                Object t = callFrame.args[1];
                                Object tm = callFrame.args[2];
                                if (t instanceof String && tm instanceof String
                                        && !((String) t).isEmpty()) {
                                    capturedWtToken = (String) t;
                                    capturedWtTokenTime = (String) tm;
                                    Log.i(TAG, "z7m.w token captured (len="
                                            + ((String) t).length() + ")");
                                    // 触发点1：token 明文唯一经过点，直接上报
                                    reportTokenAsync((String) t, (String) tm,
                                            "z7m_w_capture");
                                }
                            }
                        });
                Log.i(TAG, "z7m.w hook installed");
            } catch (Throwable e) {
                Log.w(TAG, "z7m.w hook failed: " + e);
            }
            // 登录报文探针（2026-08-19 密码预处理排查）：t3s.b 是密码登录报文
            // 构造终点（返回 t3s.d = 线上字节）。捕获 App 实际构造的 g6m 全部
            // 字段（含明文密码 tag4 来源 g6m.c）与完整线上 hex，写
            // filesDir/thshook_login_probe.log，供与 hook 自建 g6m 对比。
            try {
                Class<?> t3sClass = cl.loadClass("t3s");
                Class<?> v3sClass = cl.loadClass("v3s");
                Class<?> jmvClass = cl.loadClass("jmv");
                Pine.hook(t3sClass.getDeclaredMethod("b", v3sClass, jmvClass),
                        new MethodHook() {
                            @Override
                            public void afterCall(Pine.CallFrame callFrame) {
                                try {
                                    StringBuilder sb = new StringBuilder();
                                    sb.append("=== t3s.b pwd login probe ===\n");
                                    Object v3s = callFrame.args[0];
                                    Object g6m = v3sClass.getField("d").get(v3s);
                                    Object r8m = v3sClass.getField("c").get(v3s);
                                    if (g6m != null) {
                                        for (Field f : g6m.getClass().getFields()) {
                                            Class<?> ft = f.getType();
                                            if (ft == String.class || ft == boolean.class
                                                    || ft == int.class || ft == long.class) {
                                                Object v = f.get(g6m);
                                                if (v != null) {
                                                    sb.append("g6m.").append(f.getName())
                                                            .append('=').append(v).append('\n');
                                                }
                                            }
                                        }
                                    }
                                    if (r8m != null) {
                                        for (Field f : r8m.getClass().getFields()) {
                                            Class<?> ft = f.getType();
                                            if (ft == String.class || ft == boolean.class
                                                    || ft == int.class || ft == long.class) {
                                                Object v = f.get(r8m);
                                                if (v != null) {
                                                    sb.append("r8m.").append(f.getName())
                                                            .append('=').append(v).append('\n');
                                                }
                                            }
                                        }
                                    }
                                    Object ret = callFrame.getResult();
                                    byte[] buf = ret != null
                                            ? (byte[]) t3sClass.getField("d").get(ret) : null;
                                    sb.append("buf_len=").append(buf == null ? -1 : buf.length).append('\n');
                                    if (buf != null) {
                                        StringBuilder hex = new StringBuilder(buf.length * 2);
                                        for (byte b : buf) hex.append(String.format("%02x", b));
                                        sb.append("buf_hex=").append(hex).append('\n');
                                    }
                                    sb.append("=== end ===\n");
                                    Log.i(TAG, sb.toString());
                                    appendLoginProbeLog(sb.toString());
                                } catch (Throwable t) {
                                    Log.w(TAG, "t3s.b probe failed: " + t);
                                }
                            }
                        });
                Log.i(TAG, "t3s.b login probe installed");
            } catch (Throwable e) {
                Log.w(TAG, "t3s.b probe hook failed: " + e);
            }
            // 设备指纹伪造恢复（filesDir/thshook_spoof.json）：必须在 token
            // import/登录前生效（udid 同时是 token 加密 key）
            try {
                loadDeviceSpoofConfig(cl);
            } catch (Throwable e) {
                Log.w(TAG, "loadDeviceSpoofConfig failed: " + e);
            }
            // 后台预热交易运行态（激活账户 + 等静默重登），首个 HTTP 请求无需
            // 再等 ensure 流程。静默重登实测可能耗时 ~2 分钟，失败后循环重试。
            // 仅主实例（trade role 已启用）预热，其余实例禁止向券商发登录。
            if (isTradeRoleEnabled()) {
                new Thread(() -> {
                    for (int attempt = 1; attempt <= 6; attempt++) {
                        try {
                            Thread.sleep(3000);
                            boolean ok = ensureTradeRuntimeReady(cl);
                            Log.i(TAG, "trade runtime warmup attempt=" + attempt + ": " + ok);
                            if (ok) return;
                        } catch (Throwable e) {
                            Log.w(TAG, "trade runtime warmup failed: " + e);
                        }
                    }
                    Log.w(TAG, "trade runtime warmup exhausted");
                }, "trade-warmup").start();
            } else {
                Log.i(TAG, "trade role disabled, skip trade-warmup");
            }
        }

        // 猜测性交易接口类只尝试一次（当前版本均不存在）
        if (speculativeTradeClassesHooked.compareAndSet(false, true)) {
            hookSpeculativeTradeClasses(cl);
        }
    }

    /** crash 防护（2026-08-18）：rpv.X/Y/C 裸调 this.d（mqv）——uqv.e() 在
     *  通信服务注册表 Map<Integer,mqv> 未填充的冷启动窗口创建出的 rpv 带
     *  null d，request() 即主线程 NPE crash（每分钟循环）。防护：
     *  ① X/Y/C beforeCall d==null → setResult 丢弃请求（防崩）；
     *  ② uqv.e afterCall → d==null 的 rpv 从注册表补注入非空 mqv。 */
    private static void hookTradeCommServiceGuard(ClassLoader cl) throws Exception {
        Class<?> rpvClass = cl.loadClass("rpv");
        Class<?> mqvClass = cl.loadClass("mqv");
        java.lang.reflect.Field dField = rpvClass.getField("d");
        dField.setAccessible(true);

        // ① 三个裸调用 this.d 的方法全部防护（参数均为 Context）
        Class<?> ctxClass = android.content.Context.class;
        String[] guardMethods = {"X", "Y", "C"};
        int guarded = 0;
        for (String mn : guardMethods) {
            final String methodName = mn;
            try {
                Method m = rpvClass.getDeclaredMethod(mn, ctxClass);
                m.setAccessible(true);
                Pine.hook(m, new MethodHook() {
                    @Override
                    public void beforeCall(Pine.CallFrame callFrame) {
                        try {
                            Object d = dField.get(callFrame.thisObject);
                            if (d == null) {
                                callFrame.setResult(null);
                                Log.w(TAG, "rpv guard: dropped " + methodName
                                        + " request, comm service(mqv) null (pre-init window)");
                            }
                        } catch (Throwable ignored) {
                        }
                    }
                });
                guarded++;
            } catch (NoSuchMethodException ignored) {
            }
        }
        Log.w(TAG, "rpv crash guard installed on " + guarded + " methods");

        // ② uqv.e(boolean) 创建 rpv 后补注入：d==null 时从注册表重取
        try {
            Class<?> uqvClass = cl.loadClass("uqv");
            java.lang.reflect.Field registryField = uqvClass.getDeclaredField("a");
            registryField.setAccessible(true);
            Method eMethod = uqvClass.getDeclaredMethod("e", boolean.class);
            eMethod.setAccessible(true);
            java.lang.reflect.Method e0Method = rpvClass.getDeclaredMethod("e0", mqvClass);
            e0Method.setAccessible(true);
            Pine.hook(eMethod, new MethodHook() {
                @Override
                public void afterCall(Pine.CallFrame callFrame) {
                    try {
                        Object rpvInst = callFrame.getResult();
                        if (rpvInst == null || dField.get(rpvInst) != null) return;
                        Object registry = registryField.get(null);
                        if (!(registry instanceof java.util.Map)) return;
                        for (Object v : ((java.util.Map<?, ?>) registry).values()) {
                            if (v != null) {
                                e0Method.invoke(rpvInst, v);
                                Log.w(TAG, "rpv guard: backfilled comm service via e0");
                                return;
                            }
                        }
                    } catch (Throwable ignored) {
                    }
                }
            });
            Log.w(TAG, "uqv.e backfill hook installed");
        } catch (Throwable t) {
            Log.w(TAG, "uqv.e backfill hook failed: " + t.getMessage());
        }
    }

    /** 登录路径诊断（AVD 排查）：登录请求在 mrv 会话检查层被静默吞时定位卡点。
     *  全部 W 级日志（AVD logcat 只显示 W/E）+ recentTradeLogs 缓冲。 */
    private static void hookTradeLoginPathDiagnostics(ClassLoader cl) {
        // 1. r9h.r = notifyUserChanged 实际入口：定位高频刷屏源（栈每 10s 一次）
        try {
            Class<?> r9hClass = cl.loadClass("r9h");
            Method rMethod = r9hClass.getDeclaredMethod("r");
            rMethod.setAccessible(true);
            Pine.hook(rMethod, new MethodHook() {
                private long lastStackMs;

                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    long now = System.currentTimeMillis();
                    if (now - lastStackMs > 10000) {
                        lastStackMs = now;
                        String msg = "TradeUserChange.r9h.r called" + tradeStackSnippet("UserChangeStack");
                        Log.w(TAG, msg);
                        addTradeLog(msg);
                    }
                }
            });
            Log.i(TAG, "r9h.r user-change hook installed");
        } catch (Throwable e) {
            Log.w(TAG, "r9h.r hook failed: " + e);
        }
        // 2. f2s.q 登录入口：确认登录调用到达
        try {
            Class<?> f2sClass = cl.loadClass("f2s");
            Class<?> tokenCls = cl.loadClass(
                    "com.hexin.android.weituo.hstrade.feature.login.bindlogin.model.TokenInfo");
            Class<?> q3sClass = cl.loadClass("q3s");
            Class<?> g8mClass = cl.loadClass("g8m");
            Method qMethod = f2sClass.getDeclaredMethod("q", tokenCls, q3sClass, g8mClass);
            qMethod.setAccessible(true);
            Pine.hook(qMethod, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    String msg = "TradeLogin.f2s.q entry tokenInfo="
                            + (callFrame.args[0] == null ? "null" : "present");
                    Log.w(TAG, msg);
                    addTradeLog(msg);
                }
            });
            Log.i(TAG, "f2s.q login-entry hook installed");
        } catch (Throwable e) {
            Log.w(TAG, "f2s.q hook failed: " + e);
        }
        // 2.5 k2s.b：isWeituoLogining 判定入口，true=f2s.q 将被静默丢弃
        try {
            Class<?> k2sClass = cl.loadClass("k2s");
            Class<?> q3sClass2 = cl.loadClass("q3s");
            Method bMethod = k2sClass.getDeclaredMethod("b", q3sClass2);
            bMethod.setAccessible(true);
            Pine.hook(bMethod, new MethodHook() {
                @Override
                public void afterCall(Pine.CallFrame callFrame) {
                    String msg = "TradeLogin.k2s.b drop-will-happen=" + callFrame.getResult();
                    Log.w(TAG, msg);
                    addTradeLog(msg);
                }
            });
            Log.i(TAG, "k2s.b drop-check hook installed");
        } catch (Throwable e) {
            Log.w(TAG, "k2s.b hook failed: " + e);
        }
        // 3. k7r.Q：true=走 jniRequest 直发，false=走 socket 会话检查（可能被吞）
        try {
            Class<?> k7rClass = cl.loadClass("k7r");
            Class<?> mhvClass = cl.loadClass("mhv");
            Method qMethod = k7rClass.getDeclaredMethod("Q", mhvClass);
            qMethod.setAccessible(true);
            Pine.hook(qMethod, new MethodHook() {
                @Override
                public void afterCall(Pine.CallFrame callFrame) {
                    String msg = "TradeLogin.k7r.Q jniPath=" + callFrame.getResult()
                            + " (false=socket session-check path)";
                    Log.w(TAG, msg);
                    addTradeLog(msg);
                }
            });
            Log.i(TAG, "k7r.Q path hook installed");
        } catch (Throwable e) {
            Log.w(TAG, "k7r.Q hook failed: " + e);
        }
        // 4. mrv$b.onChannelBad：请求在此被静默丢弃（"donot executeRequest"）
        try {
            Class<?> bClass = cl.loadClass("mrv$b");
            Method badMethod = bClass.getDeclaredMethod("onChannelBad", int.class);
            badMethod.setAccessible(true);
            Pine.hook(badMethod, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    // 事件驱动重试信号：在途请求被通道丢弃——等待中的查询
                    // 据此立即放弃本轮等待，走"等重连→重发"快速路径
                    lastCbasBadMs = System.currentTimeMillis();
                    synchronized (cbasSignal) { cbasSignal.notifyAll(); }
                    String msg = "TradeChannel.onChannelBad code=" + callFrame.args[0]
                            + " REQUEST DROPPED";
                    Log.w(TAG, msg);
                    addTradeLog(msg);
                }
            });
            Log.i(TAG, "onChannelBad hook installed");
        } catch (Throwable e) {
            Log.w(TAG, "onChannelBad hook failed: " + e);
        }
        // 5. mrv$a.onChannelOk：通道就绪，请求继续执行
        try {
            Class<?> aClass = cl.loadClass("mrv$a");
            Method okMethod = aClass.getDeclaredMethod("onChannelOk");
            okMethod.setAccessible(true);
            Pine.hook(okMethod, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    Log.w(TAG, "TradeChannel.onChannelOk executing request");
                    addTradeLog("TradeChannel.onChannelOk executing request");
                }
            });
            Log.i(TAG, "onChannelOk hook installed");
        } catch (Throwable e) {
            Log.w(TAG, "onChannelOk hook failed: " + e);
        }
        // 6. hrv.r：会话检查入口（sessionType 0=行情通道 1=交易通道）
        try {
            Class<?> hrvClass = cl.loadClass("hrv");
            Class<?> hClass = cl.loadClass("hrv$h");
            Method rMethod = hrvClass.getDeclaredMethod("r", hClass);
            rMethod.setAccessible(true);
            Pine.hook(rMethod, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    String st;
                    try {
                        Object cb = callFrame.args[0];
                        st = String.valueOf(cb.getClass().getMethod("sessionType")
                                .invoke(cb));
                    } catch (Throwable t) {
                        st = "?" + t.getClass().getSimpleName();
                    }
                    long now = System.currentTimeMillis();
                    // hrv.r 位于请求热路径；诊断不能因高频 Log.w 放大 ANR/重启风暴。
                    long previous = lastTradeSessionCheckLogMs.get();
                    if (now - previous < 5000L
                            || !lastTradeSessionCheckLogMs.compareAndSet(previous, now)) return;
                    String msg = "TradeChannel.hrv.r check sessionType=" + st;
                    Log.w(TAG, msg);
                    addTradeLog(msg);
                }
            });
            Log.i(TAG, "hrv.r session-check hook installed");
        } catch (Throwable e) {
            Log.w(TAG, "hrv.r hook failed: " + e);
        }
        // 7. Socket.connect：验证 CBAS(9528) 是否实际发起连接，以及连接是否抛错。
        // 仅记录目标端口，且所有日志均不包含认证报文或账户信息。
        try {
            Method connectMethod = Socket.class.getDeclaredMethod(
                    "connect", java.net.SocketAddress.class, int.class);
            Pine.hook(connectMethod, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    Object endpoint = callFrame.args[0];
                    if (endpoint instanceof java.net.InetSocketAddress
                            && ((java.net.InetSocketAddress) endpoint).getPort() == 9528) {
                        cbasReconnecting = true;
                        String msg = "TradeChannel.Socket.connect start endpoint=" + endpoint;
                        Log.w(TAG, msg);
                        addTradeLog(msg);
                    }
                }

                @Override
                public void afterCall(Pine.CallFrame callFrame) {
                    Object endpoint = callFrame.args[0];
                    if (endpoint instanceof java.net.InetSocketAddress
                            && ((java.net.InetSocketAddress) endpoint).getPort() == 9528) {
                        // 桩 CallFrame 不暴露异常：失败时 Socket.isConnected()=false
                        boolean ok = false;
                        try {
                            ok = callFrame.thisObject instanceof Socket
                                    && ((Socket) callFrame.thisObject).isConnected();
                        } catch (Throwable ignored) { }
                        if (ok) {
                            cbasReconnecting = false;
                            lastCbasReadyMs = System.currentTimeMillis();
                            synchronized (cbasSignal) { cbasSignal.notifyAll(); }
                        }
                        String msg = "TradeChannel.Socket.connect "
                                + (ok ? "success" : "FAILED(endpoint refused/timeout)")
                                + " endpoint=" + endpoint;
                        Log.w(TAG, msg);
                        addTradeLog(msg);
                    }
                }
            });
            Log.i(TAG, "Socket.connect CBAS hook installed");
        } catch (Throwable e) {
            Log.w(TAG, "Socket.connect CBAS hook failed: " + e);
        }
        // 8. hrv.u：CBAS（交易通道 sessionType=1）同步检查——无 Ok/Bad 之外的状态
        try {
            Class<?> hrvCls = cl.loadClass("hrv");
            Class<?> hCls = cl.loadClass("hrv$h");
            Method uMethod = hrvCls.getDeclaredMethod("u", hCls);
            uMethod.setAccessible(true);
            Pine.hook(uMethod, new MethodHook() {
                private long lastLogMs;

                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    long now = System.currentTimeMillis();
                    if (now - lastLogMs < 5000) return;
                    lastLogMs = now;
                    String cb = callFrame.args[0] == null ? "null"
                            : callFrame.args[0].getClass().getName();
                    String msg = "TradeChannel.hrv.u CBAS-check cb=" + cb;
                    Log.w(TAG, msg);
                    addTradeLog(msg);
                }
            });
            Log.i(TAG, "hrv.u CBAS-check hook installed");
        } catch (Throwable e) {
            Log.w(TAG, "hrv.u hook failed: " + e);
        }
        // 9. hrv.C：CBAS 连接动作——o2u.p() 为 null 时静默 no-op（AVD 疑似病灶）。
        //    读 hrv.d(o2u).p() 的返回即可判断地址列表是否为空。
        try {
            Class<?> hrvCls = cl.loadClass("hrv");
            Method cMethod = hrvCls.getDeclaredMethod("C");
            cMethod.setAccessible(true);
            Pine.hook(cMethod, new MethodHook() {
                private long lastLogMs;

                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    long now = System.currentTimeMillis();
                    if (now - lastLogMs < 5000) return;
                    lastLogMs = now;
                    String addr;
                    try {
                        Object hrvInst = callFrame.thisObject;
                        java.lang.reflect.Field dField = hrvCls.getDeclaredField("d");
                        dField.setAccessible(true);
                        Object o2u = dField.get(hrvInst);
                        Object n2u = o2u.getClass().getMethod("p").invoke(o2u);
                        addr = n2u == null ? "NULL(no-cbas-list)" : String.valueOf(n2u);
                    } catch (Throwable t) {
                        addr = "?" + t.getClass().getSimpleName();
                    }
                    String msg = "TradeChannel.hrv.C connect-cbas addr=" + addr;
                    Log.w(TAG, msg);
                    addTradeLog(msg);
                }
            });
            Log.i(TAG, "hrv.C cbas-connect hook installed");
        } catch (Throwable e) {
            Log.w(TAG, "hrv.C hook failed: " + e);
        }
        // 10. CommunicationService.resetCbasServer(host,port)：CBAS 地址设置入口。
        //     真机由此抓真实 CBAS 地址；AVD 上若从不出现即为推送组件缺位的实锤
        try {
            Class<?> commSvc = cl.loadClass("com.hexin.plat.android.CommunicationService");
            Method reset = commSvc.getDeclaredMethod("resetCbasServer", String.class, int.class);
            reset.setAccessible(true);
            Pine.hook(reset, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    String msg = "TradeChannel.resetCbasServer host=" + callFrame.args[0]
                            + " port=" + callFrame.args[1];
                    Log.w(TAG, msg);
                    addTradeLog(msg);
                    lastCbasServerReset = msg;
                }
            });
            Log.i(TAG, "resetCbasServer hook installed");
        } catch (Throwable e) {
            Log.w(TAG, "resetCbasServer hook failed: " + e);
        }
        // 11. 登录拦截器链（s8m：t9m→j9m→m9m→v9m→o9m→i9m→x9m→…异步责任链）。
        //     任一环节不回调即"无发送无失败"地挂起（AVD 病灶定位关键）。
        //     h9m.c() 是链的"继续"调用；每个 intercept() 入口打点。
        String[] chainClasses = {"t9m", "j9m", "m9m", "v9m", "o9m", "i9m", "x9m",
                "z9m", "p9m", "w9m", "u9m", "y9m", "n9m", "r9m", "s9m"};
        for (String chainName : chainClasses) {
            try {
                Class<?> chainCls = cl.loadClass(chainName);
                Method intercept = chainCls.getDeclaredMethod("intercept");
                intercept.setAccessible(true);
                Pine.hook(intercept, new MethodHook() {
                    @Override
                    public void beforeCall(Pine.CallFrame callFrame) {
                        String b = "?";
                        try {
                            b = String.valueOf(chainCls.getMethod("b")
                                    .invoke(callFrame.thisObject));
                        } catch (Throwable ignored) { }
                        String msg = "TradeLoginChain." + chainName + " enter (" + b + ")";
                        Log.w(TAG, msg);
                        addTradeLog(msg);
                    }
                });
            } catch (Throwable e) {
                Log.w(TAG, "chain hook " + chainName + " failed: " + e.getMessage());
            }
        }
        try {
            Class<?> h9mClass = cl.loadClass("h9m");
            Method proceed = h9mClass.getDeclaredMethod("c");
            proceed.setAccessible(true);
            Pine.hook(proceed, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    String name = callFrame.thisObject == null ? "?"
                            : callFrame.thisObject.getClass().getSimpleName();
                    String next = "?", cb = "?";
                    try {
                        java.lang.reflect.Field aField = h9mClass.getDeclaredField("a");
                        aField.setAccessible(true);
                        Object aVal = aField.get(callFrame.thisObject);
                        next = aVal == null ? "null" : aVal.getClass().getSimpleName();
                        java.lang.reflect.Field bField = h9mClass.getDeclaredField("b");
                        bField.setAccessible(true);
                        Object bVal = bField.get(callFrame.thisObject);
                        cb = bVal == null ? "null" : bVal.getClass().getSimpleName();
                    } catch (Throwable t) {
                        next = "?" + t.getClass().getSimpleName();
                    }
                    String msg = "TradeLoginChain.h9m.c proceed from=" + name
                            + " next=" + next + " cb=" + cb
                            + " thread=" + Thread.currentThread().getName();
                    Log.w(TAG, msg);
                    addTradeLog(msg);
                }
            });
            Log.i(TAG, "login chain hooks installed");
        } catch (Throwable e) {
            Log.w(TAG, "h9m.c hook failed: " + e);
        }
        // 12. e2s.a0：拦截器链完成后的实际登录发送（s9m 链 + 超时任务）。
        //     若 a0 未运行=链未完成；运行了=卡点在 s9m 发送阶段。
        try {
            Class<?> e2sClass = cl.loadClass("e2s");
            Class<?> v3sClass = cl.loadClass("v3s");
            Method a0Method = e2sClass.getDeclaredMethod("a0", v3sClass);
            a0Method.setAccessible(true);
            Pine.hook(a0Method, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    Log.w(TAG, "TradeLoginChain.e2s.a0 send-phase start (s9m chain)");
                    addTradeLog("TradeLoginChain.e2s.a0 send-phase start (s9m chain)");
                }
            });
            Log.i(TAG, "e2s.a0 send-phase hook installed");
        } catch (Throwable e) {
            Log.w(TAG, "e2s.a0 hook failed: " + e);
        }
        // 13. z9m（ZLBindWTPhoneInterceptor，r8m.e=智联分支时进入）：基类模板
        //     intercept() 里 a()=true 即停链等"绑定手机号"流程。a() 判定依据是
        //     **本设备**的智联绑定手机号（pzh.g().e()）——真机有历史绑定记录
        //     判 false 放行；AVD/新设备无记录判 true，无头环境绑定流程永不
        //     完成 → 登录链无限挂起（本次 AVD 登录超时的最终根因）。
        //     账户实际已在手机端绑定过，此处强制 a()=false 等价"设备已绑定"。
        try {
            Class<?> z9mClass = cl.loadClass("z9m");
            Method aMethod = z9mClass.getDeclaredMethod("a");
            aMethod.setAccessible(true);
            Pine.hook(aMethod, new MethodHook() {
                @Override
                public void afterCall(Pine.CallFrame callFrame) {
                    if (Boolean.TRUE.equals(callFrame.getResult())) {
                        callFrame.setResult(false);
                        Log.w(TAG, "TradeLoginChain.z9m phone-bind gate bypassed");
                        addTradeLog("TradeLoginChain.z9m phone-bind gate bypassed");
                    }
                }
            });
            Log.i(TAG, "z9m phone-bind bypass hook installed");
        } catch (Throwable e) {
            Log.w(TAG, "z9m hook failed: " + e);
        }
    }

    /** 交易事件栈摘录：过滤 java/android 框架帧，最多 12 帧（诊断刷屏源用） */
    private static String tradeStackSnippet(String label) {
        StackTraceElement[] st = new Throwable().getStackTrace();
        StringBuilder sb = new StringBuilder(" ").append(label).append(":");
        int n = 0;
        for (StackTraceElement e : st) {
            String cn = e.getClassName();
            if (cn.startsWith("java.") || cn.startsWith("android.")
                    || cn.startsWith("com.yuyang.")) continue;
            sb.append(' ').append(cn).append('.').append(e.getMethodName());
            if (++n >= 12) break;
        }
        return sb.toString();
    }

    /** 延迟重试：等 App 完全启动、上下文就绪后再触发 bridge 挂钩。
     *  注意不能用 Handler——postAppSpecialize 时机主线程 Looper 尚未创建。 */
    private static void scheduleTradingSdkBridgeRetry(final ClassLoader cl) {
        final int attempt = bridgeRetryAttempts.incrementAndGet();
        // AVD x86 转译下 r9h.d 就绪可能远慢于真机（负载高时 >90s），
        // 重试窗口放宽到 6 档共 ~15 分钟，避免错过即永久放弃
        if (attempt > 6) return;
        final long delayMs = attempt <= 3 ? (attempt == 1 ? 15000L : attempt == 2 ? 45000L : 90000L)
                : (attempt == 4 ? 180000L : 300000L);
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
                                if (text.contains("撤单")) {
                                    lastWithdrawClickMs = System.currentTimeMillis();
                                }
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
        sb.append(",\"ensure_error\":\"").append(lastEnsureTradeError)
                .append("\",\"trade_role\":").append(isTradeRoleEnabled());
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
        // 资金（A股）：持仓页资产页卡专用协议。源码 rcm.request()/WeiTuoChiCangPersonalCapitalItemView.request()：
        //   uqv.e(true).H(2605, 1807, obs, new eb6().b("reqctrl=2012").a("36665", "cc_capital").toString())
        // 不走 9001 页面容器（容器协议按 1264 注册收不到响应，见待优化历史记录）。
        m.put("funds", new int[]{1807, 2605});
        TRADE_QUERY_PROTOCOLS = java.util.Collections.unmodifiableMap(m);
        java.util.Map<String, String> sp = new java.util.LinkedHashMap<>();
        // new eb6().a("36665", TodayDealSource.Query.getSource()).toString()
        sp.put("today_deal", "ctrlid_0=36665\nctrlvalue_0=today_chaxun\nctrlcount=1");
        // new eb6().b("reqctrl=2012").a("36665", CapitalQuerySource.ACapital.getSource()).toString()
        sp.put("funds", "reqctrl=2012\nctrlid_0=36665\nctrlvalue_0=cc_capital\nctrlcount=1");
        // 以下四条 2026-08-16 静态化（消除 App 重启后需 UI 进页面捕获的依赖）。
        // positions：真机捕获（write-captures 导出），zjcc_home=交易首页资金持仓入口
        sp.put("positions", "addAccount=1\nctrlid_0=36665\nctrlvalue_0=zjcc_home\nctrlcount=1");
        // today_order：源码推导 l6p → t6p.g(false,true) + pxm.o() 附加 36665；
        // UnifiedHsTodayOrderPage: ct9 第三段 = TodayOrderSource.Query("today_chaxun")
        sp.put("today_order", "addGaiDan=1\nctrlid_0=36716\nctrlvalue_0=1\nctrlid_1=36665\nctrlvalue_1=today_chaxun\nctrlcount=2");
        // hist_order：t4m.a() 源码模板——new eb6("reqctrl=2026").a(36633,start).a(36634,end)
        //   .a("36665", HistoryOrderSource.Query)；日期 yyyyMMdd（y6n.e 格式），占位符调用时展开
        sp.put("hist_order", "reqctrl=2026\nctrlid_0=36633\nctrlvalue_0={start}\nctrlid_1=36634\nctrlvalue_1={end}\nctrlid_2=36665\nctrlvalue_2=his_chaxun\nctrlcount=3");
        // hist_deal：sxh.d() 源码模板——new eb6("reqctrl=4223").a("36633",s).a("36634",e)
        //   .a("36665", HistoryDealSource.Dryk).b("totalMoney=1").b("rowcount=40")
        sp.put("hist_deal", "reqctrl=4223\nctrlid_0=36633\nctrlvalue_0={start}\nctrlid_1=36634\nctrlvalue_1={end}\nctrlid_2=36665\nctrlvalue_2=his_dryk\ntotalMoney=1\nrowcount=40\nctrlcount=3");
        TRADE_QUERY_STATIC_PARAMS = java.util.Collections.unmodifiableMap(sp);
    }

    /** 写交易协议号：买入 1820/1804/22915(闪电)，卖出 1821/1805/22917(闪电)，
     *  撤单提交 22157，撤单列表 2015、撤单确认 2013（1820/1821 真机实发捕获，
     *  1804/1805 为源码静态逆向的持仓刷新变体，22915/22917 闪电下单未实测） */
    private static final java.util.Set<Integer> TRADE_WRITE_PROTOCOLS =
            java.util.Collections.unmodifiableSet(
                    new java.util.HashSet<>(java.util.Arrays.asList(
                            1820, 1821, 1804, 1805, 22157, 22915, 22917, 2015, 2013)));

    private static String invokeZjccQuery() {
        return invokeTradeQuery(1891, 2624, null);
    }

    /** ensureTradeRuntimeReady 最近一次失败原因（仅结构信息，无业务值） */
    private static volatile String lastEnsureTradeError = "not run";
    private static volatile boolean tradeRuntimeReadyOnce = false;
    private static volatile String tradeRuntimeEnsureState = "NOT_RUN";
    private static volatile long tradeRuntimeEnsureStartedMs = 0L;
    private static volatile long tradeRuntimeEnsureCompletedMs = 0L;
    private static volatile String tradeRuntimeProbe = "not run";
    /** 交易运行时/主动登录专用锁：登录等待可达 ~60s，与类锁分离——主线程 hook
     *  回调（captureTradeAccountManager 等 synchronized 方法）不能被它阻塞（ANR）。 */
    private static final Object tradeRuntimeLock = new Object();
    /** z7m.w（TokenManager 写入）捕获的最近一次明文 WtToken/Time（登录响应
     *  经过点；跨设备共享实验的导出双保险，App 重启即清空）。 */
    private static volatile String capturedWtToken;
    private static volatile String capturedWtTokenTime;

    // ---- token 自动上报（2026-08-17）：真机打开 App / 主动登录刷新 token 后，
    // 自动 POST 到服务端 /api/ths/token 入库，服务端自愈取用。用户免数据线操作。
    private static volatile boolean tokenReportConfigLoaded = false;
    private static volatile boolean tokenReportEnabled = false;
    private static volatile String tokenReportUrl;        // 完整 URL（含 /api/ths/token）
    private static volatile String tokenReportApiKey;
    private static volatile String lastReportedTokenHash; // sha256 去重：同 token 不重发
    private static volatile String lastTokenReportStatus = "not run"; // 结构信息，无 token
    private static volatile String deviceIdCache;
    private static final java.util.concurrent.ExecutorService tokenReportExecutor =
            java.util.concurrent.Executors.newSingleThreadExecutor(r -> {
                Thread t = new Thread(r, "ths-token-report");
                t.setDaemon(true);
                return t;
            });

    // ---- 交易主实例门禁（2026-08-17）：生产同一 AVD 多 user 各跑一份 App，
    // 只允许主实例登录券商（并发登录会互顶会话）。filesDir 按 user 隔离，
    // thshook_trade_role.json 写 {"enabled":true} 的实例才启用交易功能：
    // 预热线程不启动、/stock/trade/* 端点全 403（role 端点自身豁免）。
    // 无配置文件时按 user 数推断：单 user 设备（真机）默认启用，
    // 多 user 环境（生产 AVD）默认禁用、必须显式配置主实例。
    private static volatile boolean tradeRoleEnabled = false;
    private static volatile boolean tradeRoleLoaded = false;
    private static final Object tradeRoleLock = new Object();
    // 注入路线标记：true=LSPosed 桥（生产 AVD 多实例环境），false=zygisksu
    // 原生 Pine（真机单实例）。UserManager.getUserCount() 在 ColorOS 上不可靠
    // （实测单 user 设备返回失败），改按注入路线推断默认角色。
    private static volatile boolean injectedViaLsposed = false;
    // 交易密码（密码登录用）：pendingTradePassword 为单次请求传入（用后即清），
    // 持久化在 filesDir/thshook_pwd.json。绝不写日志/绝不出现在任何响应中。
    private static volatile String pendingTradePassword = null;
    // 账户 seed 自动重播（每进程一次）：AVD 的 yyb 券商库未初始化时 App 账户
    // 仓库不落盘，重启即失；ensure 阶段从 filesDir/thshook_trade_seed.json 重播。
    private static volatile boolean tradeSeedAutoPlayed = false;

    private static boolean r9hReady(ClassLoader cl) {
        try {
            Class<?> r9hClass = cl.loadClass("r9h");
            java.lang.reflect.Field initFlag = r9hClass.getDeclaredField("d");
            initFlag.setAccessible(true);
            return (boolean) initFlag.get(null);
        } catch (Throwable e) {
            return false;
        }
    }

    /**
     * 零 UI 交易运行时恢复：App 冷启动后交易模块懒加载未激活时（未进过交易 Tab），
     * 主动走 App 官方初始化入口补齐两前置条件，替代此前"必须进一次交易页"的依赖：
     *   1. master module：k7r.m().A(null) → s9r.w()（notifyInitWTModule，内部幂等——
     *      hasInitWtModule 分支直接返回）→ r9h.g().m(...) → initMasterModule → r9h.d=true
     *   2. 账户列表：n0s.s().A(false)（WeituoAccountManager "init account"，官方入口）
     *      → c1s.m().G() 加密仓库加载 → B() 填充 → p0s.F(119) 可返回 pzr
     * 恢复后 F(119) 捕获 manager，并轮询等待两个标志就绪。
     */
    private static boolean ensureTradeRuntimeBase(ClassLoader cl) {
        synchronized (tradeRuntimeLock) {
            return ensureTradeRuntimeBaseLocked(cl);
        }
    }

    /** seed 前置：只保证 master module 就绪（账户恢复正是 seed 的目标，不能作为前置）。 */
    private static boolean ensureTradeMasterModule(ClassLoader cl) {
        synchronized (tradeRuntimeLock) {
            try {
                return ensureTradeMasterModuleLocked(cl);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                lastEnsureTradeError = "interrupted";
                return false;
            }
        }
    }

    private static boolean ensureTradeMasterModuleLocked(ClassLoader cl) throws InterruptedException {
        // 1) 激活交易模块（libweituo.so 的 initMasterModule）
        if (!r9hReady(cl)) {
                try {
                    Class<?> k7rClass = cl.loadClass("k7r");
                    Object k7rInst = k7rClass.getMethod("m").invoke(null);
                    k7rClass.getMethod("A", cl.loadClass("x6r")).invoke(k7rInst, new Object[]{null});
                    Log.i(TAG, "ensure: k7r.A(null) invoked");
                } catch (Throwable e) {
                    Log.w(TAG, "trade module activate failed: " + e);
                }
                for (int i = 0; i < 20 && !r9hReady(cl); i++) Thread.sleep(500);
                if (!r9hReady(cl)) {
                    // 兜底（AVD 首次启用交易）：s9r.w() 在 E()=true（WT lua 脚本
                    // 未提取或版本记录不匹配）时静默跳过初始化——filesDir 无
                    // resources/scripts/wt_handleclientreq.lua 即此症状。调官方
                    // 修复入口 s9r.x()（initWTModuleConfigFile，异步解压 lua），
                    // 等 E() 翻转后重触发激活。
                    try {
                        Class<?> k7rClass = cl.loadClass("k7r");
                        Object k7rInst = k7rClass.getMethod("m").invoke(null);
                        Object s9rInst = k7rClass.getField("p").get(k7rInst);
                        Class<?> s9rClass = cl.loadClass("s9r");
                        boolean needRepair = (boolean) s9rClass.getMethod("E")
                                .invoke(s9rInst);
                        Log.i(TAG, "ensure: s9r.E()=" + needRepair);
                        if (needRepair) {
                            s9rClass.getMethod("x").invoke(s9rInst);
                            // 解压在后台线程，转译环境慢，最多等 120s
                            for (int i = 0; i < 120
                                    && (boolean) s9rClass.getMethod("E").invoke(s9rInst); i++) {
                                Thread.sleep(1000);
                            }
                            Log.i(TAG, "ensure: after s9r.x() E()=" + s9rClass
                                    .getMethod("E").invoke(s9rInst));
                            // 版本 sp 修复（z() 分支）：解压成功回调会写版本；
                            // 仍不匹配时手动写当前版本再重触发
                            if ((boolean) s9rClass.getMethod("E").invoke(s9rInst)) {
                                try {
                                    s9rClass.getMethod("N").invoke(s9rInst);
                                    Log.i(TAG, "ensure: s9r.N() version fix invoked");
                                } catch (Throwable e2) {
                                    Log.w(TAG, "ensure: s9r.N() failed: " + e2);
                                }
                            }
                            k7rClass.getMethod("A", cl.loadClass("x6r"))
                                    .invoke(k7rInst, new Object[]{null});
                            Log.i(TAG, "ensure: k7r.A(null) re-invoked after repair");
                        }
                    } catch (Throwable e) {
                        Log.w(TAG, "wt resource repair failed: " + e);
                    }
                    // initMasterModule native 转译执行慢，等待窗口放大到 60s
                    for (int i = 0; i < 60 && !r9hReady(cl); i++) Thread.sleep(1000);
                }
                if (!r9hReady(cl)) {
                    lastEnsureTradeError = "master module not ready after activate (r9h.d=false)";
                    Log.w(TAG, "ensure: fail r9h not ready");
                    return false;
                }
            }
            return true;
        }

    private static boolean ensureTradeRuntimeBaseLocked(ClassLoader cl) {
        // 运行时基础：模块激活 + 账户列表恢复 + manager 捕获（不含登录门）。
        // 供 ensureTradeRuntimeReady 与主动登录端点 handleTradeLogin 共用。
        if (tradeAccountManagerInstance != null && r9hReady(cl)) return true;
        try {
            if (!r9hReady(cl) && !ensureTradeMasterModuleLocked(cl)) return false;
            // 2) 恢复账户列表并捕获 manager
            if (tradeAccountManagerInstance == null) {
                Class<?> p0sClass = cl.loadClass("p0s");
                java.lang.reflect.Method f119 = p0sClass.getDeclaredMethod("F", int.class);
                f119.setAccessible(true);
                Object mgr = f119.invoke(null, 119);
                Log.i(TAG, "ensure: F(119) first=" + (mgr != null));
                // 诊断：当前主账号用户与仓库账户数（区分"仓库无账户"与"列表未填充"）
                try {
                    Object userId = cl.loadClass("ulm").getMethod("e").invoke(null);
                    Object c1sInst2 = cl.loadClass("c1s").getMethod("m").invoke(null);
                    Object b1sInst = cl.loadClass("c1s").getMethod("p").invoke(c1sInst2);
                    java.util.List<?> accts = (java.util.List<?>) b1sInst.getClass()
                            .getMethod("H").invoke(b1sInst);
                    Log.i(TAG, "ensure: diag user=" + (userId == null ? "null"
                            : (String.valueOf(userId).isEmpty() ? "empty" : "set"))
                            + " repoAccounts=" + (accts == null ? -1 : accts.size()));
                    lastEnsureTradeError = "F119=null user=" + (userId == null
                            ? "null" : (String.valueOf(userId).isEmpty() ? "empty" : "set"))
                            + " repo=" + (accts == null ? -1 : accts.size());
                } catch (Throwable e) {
                    Log.w(TAG, "ensure: diag failed: " + e);
                    lastEnsureTradeError = "F119=null diagFailed=" + e;
                }
                if (mgr == null) {
                    // n0s.A(false) 有幂等检查（I() 判断端口/用户未变即提前 return，实测 2ms
                    // 返回且不加载）。仓库（b1s.H()）实测有账户，直接调 n0s.s().B()
                    // （initCurrentUserAccounts：重读 c1s.m().l() 填充 n0s.c 账户列表）。
                    // 注意不能先调 c1s.m().G()——它会强制重载 yyb 仓库，实测反而把数据清掉。
                    try {
                        Class<?> n0sClass = cl.loadClass("n0s");
                        Object n0sInst = n0sClass.getMethod("s").invoke(null);
                        n0sClass.getMethod("B").invoke(n0sInst);
                        Log.i(TAG, "ensure: n0s.s().B() invoked");
                        java.util.List<?> after = (java.util.List<?>) n0sClass
                                .getMethod("i").invoke(n0sInst);
                        StringBuilder cn = new StringBuilder();
                        if (after != null) {
                            for (int k = 0; k < after.size() && k < 5; k++) {
                                cn.append(after.get(k) == null ? "null"
                                        : after.get(k).getClass().getSimpleName()).append(',');
                            }
                        }
                        Log.i(TAG, "ensure: n0s.i() size=" + (after == null ? -1 : after.size())
                                + " classes=" + cn);
                        lastEnsureTradeError = lastEnsureTradeError
                                + " afterB=" + (after == null ? -1 : after.size());
                    } catch (Throwable e) {
                        Log.w(TAG, "account list refresh failed: " + e);
                    }
                    for (int i = 0; i < 20 && mgr == null; i++) {
                        Thread.sleep(500);
                        try {
                            mgr = f119.invoke(null, 119);
                        } catch (Throwable e) {
                            Log.w(TAG, "ensure: poll F(119) throw: " + e.getCause());
                            break;
                        }
                    }
                    Log.i(TAG, "ensure: after poll mgr=" + (mgr != null));
                }
                // AVD 环境账户仓库不持久化（yyb 券商库未初始化）：从
                // filesDir/thshook_trade_seed.json 自动重播 seed（成功才消耗
                // 一次性标志——模块未就绪等失败场景下允许下次 ensure 重试，
                // 否则进程会永久卡在 F119=null 无自愈路径）。
                if (mgr == null && !tradeSeedAutoPlayed) {
                    try {
                        File seedFile = appInstance == null ? null : new File(
                                appInstance.getFilesDir(), "thshook_trade_seed.json");
                        if (seedFile != null && seedFile.exists() && seedFile.canRead()) {
                            byte[] buf = new byte[(int) seedFile.length()];
                            int r;
                            try (FileInputStream fis = new FileInputStream(seedFile)) {
                                r = fis.read(buf);
                            }
                            JSONObject seedCfg = new JSONObject(new String(buf, 0,
                                    Math.max(r, 0), java.nio.charset.StandardCharsets.UTF_8));
                            Log.i(TAG, "ensure: auto reseed trade account from seed file");
                            lastEnsureTradeError = lastEnsureTradeError + " reseed";
                            mgr = applyTradeAccountSeed(cl, seedCfg.optString("qsid"),
                                    seedCfg.getJSONObject("json"),
                                    seedCfg.optJSONObject("broker"), null);
                            if (mgr != null) {
                                tradeSeedAutoPlayed = true;
                                Thread.sleep(5000);
                            }
                            Log.i(TAG, "ensure: auto reseed mgr=" + (mgr != null)
                                    + (mgr == null ? " (will retry next ensure)" : ""));
                        }
                    } catch (Throwable e) {
                        Log.w(TAG, "ensure: auto reseed failed: " + e);
                    }
                }
                // F(119)=w5s.v(119) 按 izr.a.j(pzr)（账户被激活时间打分）>0 选账户；
                // 不进交易页时打分恒 0，列表里有 fzr 也选不出。镜像 mnq.s 的标准激活
                // 链：izr.a.x(fzr) —— 设打分 + 发账户活跃事件 + 更新当前账户。
                if (mgr == null) {
                    try {
                        Class<?> n0sClass = cl.loadClass("n0s");
                        Object n0sInst = n0sClass.getMethod("s").invoke(null);
                        java.util.List<?> accounts = (java.util.List<?>) n0sClass
                                .getMethod("i").invoke(n0sInst);
                        Class<?> fzrClass = cl.loadClass("fzr");
                        Object target = null;
                        if (accounts != null) {
                            for (int k = 0; k < accounts.size(); k++) {
                                Object cand = accounts.get(k);
                                if (cand != null && fzrClass.isInstance(cand)) {
                                    target = cand;
                                    break;
                                }
                            }
                        }
                        Log.i(TAG, "ensure: activate target="
                                + (target == null ? "null" : target.getClass().getSimpleName()));
                        if (target == null) {
                            lastEnsureTradeError = lastEnsureTradeError
                                    + " noFzrInList=" + (accounts == null ? -1 : accounts.size());
                        }
                        if (target != null) {
                            Class<?> izrClass = cl.loadClass("izr");
                            Object izrInst = izrClass.getField("a").get(null);
                            izrClass.getMethod("x",
                                    cl.loadClass("pzr")).invoke(izrInst, target);
                            Log.i(TAG, "ensure: izr.a.x(fzr) invoked");
                            for (int i = 0; i < 6 && mgr == null; i++) {
                                Thread.sleep(500);
                                mgr = f119.invoke(null, 119);
                            }
                            Log.i(TAG, "ensure: after activate mgr=" + (mgr != null));
                            if (mgr == null) {
                                lastEnsureTradeError = lastEnsureTradeError
                                        + " afterActivate=null";
                            }
                            if (mgr != null) {
                                // 激活事件触发的 WT 会话建立是异步的；激活后立即发出的
                                // 首个交易请求会被吞（today_deal 实测 4ms 后超时）。
                                Thread.sleep(5000);
                                Log.i(TAG, "ensure: post-activation grace done");
                            }
                        }
                    } catch (Throwable e) {
                        Log.w(TAG, "account activate failed: " + e);
                    }
                }
                if (mgr == null) {
                    lastEnsureTradeError = "trade account not restored (F(119)=null after init)";
                    return false;
                }
                captureTradeAccountManager(mgr);
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            lastEnsureTradeError = "interrupted";
            return false;
        } catch (Throwable e) {
            lastEnsureTradeError = "ensure failed: " + e;
            return false;
        }
        // ensure 已把 r9h 拉到 ready 后，可立即安全安装 MasterModuleBridge 旁路
        // Hook；不再等待冷启动的 15/45/90 秒延迟重试档。
        if (!tradingSdkBridgeHooked.get() && r9hReady(cl)) {
            try {
                hookTradingSdkBridge(cl);
            } catch (Throwable e) {
                lastEnsureTradeError = "trade bridge hook install failed: " + e;
                return false;
            }
        }
        return true;
    }

    private static boolean ensureTradeRuntimeReady(ClassLoader cl) {
        synchronized (tradeRuntimeLock) {
            return ensureTradeRuntimeReadyLocked(cl);
        }
    }

    private static boolean ensureTradeRuntimeReadyLocked(ClassLoader cl) {
        Log.i(TAG, "ensure: enter mgr=" + (tradeAccountManagerInstance != null)
                + " r9h=" + r9hReady(cl));
        if (tradeRuntimeReadyOnce && tradeAccountManagerInstance != null && r9hReady(cl)) return true;
        if (!ensureTradeRuntimeBase(cl)) return false;
        // 登录门：统一走主动登录执行器（唯一登录发起链，同步等回调结果）。
        // 旧实现（x0s.F 触发 + 45s 盲轮询）已移除——2026-08-17 实测预热/App/端点
        // 三链并发登录会在券商服务器侧互顶会话（"正在登录中，请稍后再试!"），
        // App 链因此反复失败循环弹"登录失败"。
        JSONObject loginReport = new JSONObject();
        boolean loggedIn = doActiveTradeLogin(cl, loginReport);
        Log.i(TAG, "ensure: login-ready=" + loggedIn + " report=" + loginReport);
        if (!loggedIn) return false;
        tradeRuntimeReadyOnce = true;
        return true;
    }

    private static boolean isTradeSessionReady(ClassLoader cl) {
        try {
            Object mgr = tradeAccountManagerInstance;
            if (cl == null || mgr == null) return false;
            Class<?> izrClass = cl.loadClass("izr");
            Object izrInst = izrClass.getField("a").get(null);
            return Boolean.TRUE.equals(izrClass.getMethod("l", cl.loadClass("pzr"))
                    .invoke(izrInst, mgr));
        } catch (Throwable ignored) {
            return false;
        }
    }

    private static String handleTradeRuntimeStatus() {
        JSONObject out = new JSONObject();
        try {
            ClassLoader cl = resolveAppClassLoader(null);
            boolean classloaderReady = cl != null;
            boolean moduleReady = classloaderReady && r9hReady(cl);
            boolean hookReady = tradingSdkBridgeHooked.get();
            boolean accountReady = tradeAccountManagerInstance != null;
            boolean sessionReady = isTradeSessionReady(cl);
            long probeAgeMs = lastSuccessfulQueryMs > 0
                    ? System.currentTimeMillis() - lastSuccessfulQueryMs : -1L;
            boolean probeReady = probeAgeMs >= 0 && probeAgeMs <= 300000L;
            boolean writeReady = classloaderReady && moduleReady && hookReady
                    && accountReady && sessionReady && probeReady;
            String state = "READY";
            if ("INITIALIZING".equals(tradeRuntimeEnsureState)) state = "INITIALIZING";
            else if (!classloaderReady) state = "PROCESS_STARTING";
            else if (!moduleReady || !hookReady) state = "HOOK_NOT_READY";
            else if (!accountReady) state = "ACCOUNT_NOT_READY";
            else if (!sessionReady) state = "SESSION_NOT_READY";
            else if (!probeReady) state = "PROBE_REQUIRED";

            out.put("ok", writeReady);
            out.put("state", state);
            out.put("android_user_id", androidUserId());
            out.put("pid", android.os.Process.myPid());
            out.put("process_ready", classloaderReady);
            out.put("trade_module_ready", moduleReady);
            out.put("trade_hook_ready", hookReady);
            out.put("account_ready", accountReady);
            out.put("session_ready", sessionReady);
            out.put("readonly_probe_ready", probeReady);
            out.put("readonly_probe_age_ms", probeAgeMs);
            out.put("write_ready", writeReady);
            out.put("ensure_state", tradeRuntimeEnsureState);
            out.put("ensure_started_at_ms", tradeRuntimeEnsureStartedMs);
            out.put("ensure_completed_at_ms", tradeRuntimeEnsureCompletedMs);
            out.put("probe", tradeRuntimeProbe);
            out.put("last_error", lastEnsureTradeError == null
                    ? JSONObject.NULL : lastEnsureTradeError);
        } catch (Throwable e) {
            try { out.put("ok", false); out.put("state", "ERROR");
                out.put("last_error", String.valueOf(e)); } catch (Throwable ignored) { }
        }
        return out.toString();
    }

    /** 写端点最终门禁。必须在 App 内重新计算，不能信任调用方曾经调用过 ensure。 */
    private static String requireTradeWriteReady(JSONObject out) {
        try {
            JSONObject runtime = new JSONObject(handleTradeRuntimeStatus());
            if (runtime.optBoolean("write_ready", false)) return null;
            out.put("runtime", runtime);
            return "trade runtime not write-ready: " + runtime.optString("state", "UNKNOWN")
                    + "; call POST /stock/trade/runtime/ensure first";
        } catch (Throwable e) {
            return "trade runtime readiness check failed: " + e;
        }
    }

    private static String handleTradeRuntimeEnsure(String body) {
        synchronized (tradeRuntimeLock) {
            JSONObject out = new JSONObject();
            org.json.JSONArray actions = new org.json.JSONArray();
            long started = System.currentTimeMillis();
            try {
                JSONObject current = new JSONObject(handleTradeRuntimeStatus());
                if (current.optBoolean("write_ready", false)) {
                    actions.put("already_ready");
                    out.put("ok", true);
                    out.put("state", "READY");
                    out.put("actions", actions);
                    out.put("elapsed_ms", System.currentTimeMillis() - started);
                    out.put("runtime", current);
                    return out.toString();
                }
            } catch (Throwable ignored) { }
            tradeRuntimeEnsureStartedMs = started;
            tradeRuntimeEnsureState = "INITIALIZING";
            tradeRuntimeProbe = "pending";
            try {
                ClassLoader cl = resolveAppClassLoader(null);
                if (cl == null) throw new IllegalStateException("classloader not ready");

                // Context.startService is performed inside the App process and does not
                // require an Activity, foreground user, unlocked screen, or coordinate tap.
                startLegacyCommunicationService(appInstance, cl);
                actions.put("communication_service_started");

                if (!ensureTradeRuntimeReadyLocked(cl)) {
                    throw new IllegalStateException(lastEnsureTradeError);
                }
                actions.put("trade_runtime_initialized");
                actions.put("trade_session_ready");
                if (tradingSdkBridgeHooked.get()) actions.put("trade_hooks_installed");

                String probeResult = invokeTradeQueryByName("positions");
                JSONObject probe = new JSONObject(probeResult);
                if (!probe.optBoolean("ok", false)) {
                    throw new IllegalStateException("readonly positions probe failed: "
                            + probe.optString("error", "unknown"));
                }
                tradeRuntimeProbe = "positions:ok";
                actions.put("readonly_positions_probe_passed");
                tradeRuntimeEnsureState = "READY";
                tradeRuntimeEnsureCompletedMs = System.currentTimeMillis();
                lastEnsureTradeError = null;
                out.put("ok", true);
                out.put("state", "READY");
            } catch (Throwable e) {
                lastEnsureTradeError = String.valueOf(e.getMessage() == null ? e : e.getMessage());
                tradeRuntimeEnsureState = "FAILED";
                tradeRuntimeEnsureCompletedMs = System.currentTimeMillis();
                tradeRuntimeProbe = "failed";
                try {
                    out.put("ok", false);
                    out.put("state", "FAILED");
                    out.put("error", lastEnsureTradeError);
                } catch (Throwable ignored) { }
            }
            try {
                out.put("actions", actions);
                out.put("elapsed_ms", System.currentTimeMillis() - started);
                out.put("runtime", new JSONObject(handleTradeRuntimeStatus()));
            } catch (Throwable ignored) { }
            return out.toString();
        }
    }

    /**
     * 主动登录执行器（POST /stock/trade/login）：不依赖 App 自身静默重登的盲轮询，
     * 直接调用登录执行器并同步等待回调结果（2026-08-17 用户指示：登录要可控）。
     * 链路（rt5/f2s.java、rt7/z7m.java、runtime/x0s.java 反编译）：
     *   z7m.k().i(userId, mgr) 取 TokenInfo（内部 isAvailable 检查，过期返回 null）
     *   → q3s.a().s(1).q(2).v(0).n(2).p(false).u(true)（镜像 x0s.i 的 token 方式参数）
     *   → f2s.d().q(tokenInfo, q3s, g8m回调) → e2s TCP 客户端发送登录
     * 回调：onWeituoLoginSuccess(String,String,g6m) / onWeituoLoginFail(StuffBaseStruct,g6m)。
     * 拦截规则（f2s.q + k2s.b）：isWeituoLogining（r0s.u().H()）时新登录被静默丢弃，
     * 发送前先等 App 的在途登录尝试结束。
     */
    private static String handleTradeLogin(String body) throws org.json.JSONException {
        JSONObject out = new JSONObject();
        out.put("endpoint", "login");
        long t0 = System.currentTimeMillis();
        ClassLoader cl = resolveAppClassLoader(null);
        if (cl == null) return errorJson(out, "classloader not ready");
        // 可选 body {"password": "..."}：本次登录用密码（一次性，不落盘）；
        // {"force": true}：绕过 already_logged_in 短路强制重登（会话被 90B 踢/
        //   真机互踢后本地登录标志仍在的场景），token 优先、过期自动密码 fallback；
        // {"method": "pwd"}：跳过 token 路径直接密码登录（最强重建：无论 token
        //   状态如何都用交易密码拿全新会话+新 token）。
        String pendingPwd = null;
        boolean force = false;
        boolean preferPwd = false;
        try {
            if (body != null && !body.trim().isEmpty()) {
                JSONObject req = new JSONObject(body);
                String p = req.optString("password", "");
                if (!p.isEmpty()) pendingPwd = p;
                force = req.optBoolean("force", false);
                preferPwd = "pwd".equals(req.optString("method", ""));
                out.put("force", force);
                out.put("method", preferPwd ? "pwd" : "auto");
            }
        } catch (Throwable ignored) { }
        if (pendingPwd != null) pendingTradePassword = pendingPwd;
        if (!ensureTradeRuntimeBase(cl)) {
            pendingTradePassword = null;
            return errorJson(out, lastEnsureTradeError);
        }
        boolean ok;
        if (preferPwd) {
            ok = doPwdLogin(cl, out);
        } else {
            ok = doActiveTradeLogin(cl, out, force);
        }
        pendingTradePassword = null;
        out.put("ok", ok);
        out.put("elapsed_ms", System.currentTimeMillis() - t0);
        return out.toString();
    }

    /** method=pwd：密码直登入口。复用 doActiveTradeLoginLocked 的全部前置
     *  （在途登录等待、僵尸 isWeituoLogining 清除、账户就绪），仅跳过 token
     *  读取/seed 重播，直接走密码登录分支拿全新会话。 */
    private static boolean doPwdLogin(ClassLoader cl, JSONObject report) {
        synchronized (tradeRuntimeLock) {
            boolean ok = doActiveTradeLoginLocked(cl, report, true, true);
            if (ok) schedulePostLoginTokenReport();
            return ok;
        }
    }

    /** POST /stock/trade/pwd — 设置/清除交易密码（body {"password":...} 或
     *  {"clear":true}）。密码持久化 filesDir/thshook_pwd.json（不回显、不进日志），
     *  供 token 过期时自动密码登录。GET 返回是否已配置。 */
    private static String handleTradePassword(String body) throws org.json.JSONException {
        JSONObject out = new JSONObject();
        out.put("endpoint", "pwd");
        ClassLoader cl = resolveAppClassLoader(null);
        if (cl == null) return errorJson(out, "classloader not ready");
        try {
            if (body != null && !body.trim().isEmpty()) {
                if (body.contains("\"clear\"")) {
                    File f = new File(appInstance.getFilesDir(), "thshook_pwd.json");
                    if (f.exists()) f.delete();
                    out.put("cleared", true);
                } else {
                    String p = extractJsonString(body, "password");
                    if (p == null || p.isEmpty()) return errorJson(out, "password required");
                    JSONObject o = new JSONObject();
                    o.put("password", p);
                    writeFileString(new File(appInstance.getFilesDir(), "thshook_pwd.json"), o.toString());
                    out.put("stored", true);
                }
            }
        } catch (Throwable t) {
            return errorJson(out, "pwd store failed: " + t.getMessage());
        }
        out.put("ok", true);
        out.put("configured", loadTradePassword() != null);
        return out.toString();
    }

    /**
     * GET /stock/trade/token/export — 导出当前交易 token 明文（跨设备共享实验）。
     * token 持久化形态：z7m.c 把登录响应的 WtToken 当密码材料，用本机密钥
     * n6m.K(n6m.r().s()) 加密成 encryptedPwd 存 authorization.bat（明文不落盘）。
     * 这里进程内手工解密回明文，并用 n6m.a(明文)==mediumPwdId 自校验；解密
     * 校验失败 fallback 用 z7m.w hook 暂存的最近一次登录响应明文。
     * ⚠ 输出含登录凭证，仅限受控局域网实验通道，禁止入库/提交/记日志。
     */
    private static String handleTradeTokenExport() throws org.json.JSONException {
        JSONObject out = new JSONObject();
        out.put("endpoint", "token_export");
        ClassLoader cl = resolveAppClassLoader(null);
        if (cl == null) return errorJson(out, "classloader not ready");
        if (!ensureTradeRuntimeBase(cl)) return errorJson(out, lastEnsureTradeError);
        try {
            String userId = (String) cl.loadClass("ulm").getMethod("e").invoke(null);
            Class<?> z7mClass = cl.loadClass("z7m");
            Object z7mInst = z7mClass.getMethod("k").invoke(null);
            Object tokenInfo = z7mClass
                    .getMethod("i", String.class, cl.loadClass("pzr"))
                    .invoke(z7mInst, userId, tradeAccountManagerInstance);
            if (tokenInfo == null) {
                return errorJson(out, "token unavailable (expired or not stored)");
            }
            Class<?> bindingCls = tokenInfo.getClass().getSuperclass();
            byte[] encPwd = (byte[]) bindingCls.getField("encryptedPwd").get(tokenInfo);
            String mediumPwdId = (String) bindingCls.getField("mediumPwdId").get(tokenInfo);
            String lastBindingTime = (String) bindingCls.getField("lastBindingTime").get(tokenInfo);
            String plain = null;
            boolean verified = false;
            String verifyKeyPath = null;
            Class<?> n6mClass = cl.loadClass("n6m");
            if (encPwd != null && encPwd.length > 0) {
                Class<?> hexinUtils = cl.loadClass(
                        "com.hexin.android.base_common_utils.HexinUtils");
                java.lang.reflect.Method decipher = hexinUtils.getMethod(
                        "getRunDecipheringString", byte[].class, byte[].class);
                java.lang.reflect.Method hashOf = n6mClass.getMethod("a", String.class);
                // key 路径 A：TokenInfo.getBindingMediumPwd 覆写版（UDID 解密）
                try {
                    String cand = (String) tokenInfo.getClass()
                            .getMethod("getBindingMediumPwd", int.class)
                            .invoke(tokenInfo, 0);
                    String expect = (String) hashOf.invoke(null, cand);
                    if (cand != null && !cand.isEmpty()
                            && expect != null && expect.equals(mediumPwdId)) {
                        plain = cand;
                        verified = true;
                        verifyKeyPath = "udid(TokenInfo.getBindingMediumPwd)";
                    }
                } catch (Throwable e) {
                    Log.w(TAG, "token export try-A failed: " + e);
                }
                // key 路径 B：BindingWTInfo 标准链（K(n6m.r().s()) 解密）
                if (!verified) {
                    try {
                        Object n6mInst = n6mClass.getMethod("r").invoke(null);
                        String quickKey = (String) n6mClass.getMethod("s").invoke(n6mInst);
                        String key = (String) n6mClass.getMethod("K", String.class)
                                .invoke(null, quickKey);
                        if (key != null && !key.isEmpty()) {
                            String cand = (String) decipher.invoke(null, encPwd, key.getBytes());
                            String expect = (String) hashOf.invoke(null, cand);
                            if (cand != null && !cand.isEmpty()
                                    && expect != null && expect.equals(mediumPwdId)) {
                                plain = cand;
                                verified = true;
                                verifyKeyPath = "K(n6m.r().s())";
                            }
                        }
                    } catch (Throwable e) {
                        Log.w(TAG, "token export try-B failed: " + e);
                    }
                }
            }
            String source = null;
            if (verified) {
                source = "decrypt";
            } else if (capturedWtToken != null) {
                // fallback：z7m.w hook 暂存的最近一次登录响应明文
                plain = capturedWtToken;
                if (capturedWtTokenTime != null) lastBindingTime = capturedWtTokenTime;
                source = "hook_capture";
            } else {
                return errorJson(out, "decrypt verify failed and no hook capture"
                        + " (plain=" + (plain == null ? "null" : "len" + plain.length())
                        + " mediumPwdId=" + (mediumPwdId == null ? "null"
                                : "len" + mediumPwdId.length()) + ")");
            }
            JSONObject info = new JSONObject();
            info.put("qsid", bindingCls.getField("qsId").get(tokenInfo));
            info.put("account", bindingCls.getField("accountStr").get(tokenInfo));
            info.put("wtid", bindingCls.getField("wtId").get(tokenInfo));
            info.put("accounttype", bindingCls.getField("accountType").get(tokenInfo));
            info.put("accountNatureType",
                    bindingCls.getField("accountNatureType").get(tokenInfo));
            info.put("livetime", tokenInfo.getClass().getField("mLiveTime").get(tokenInfo));
            info.put("lastBindingTime", lastBindingTime);
            out.put("ok", true);
            out.put("token", plain);
            out.put("time", lastBindingTime);
            out.put("verify", verified);
            out.put("source", source);
            out.put("key_path", verifyKeyPath);
            out.put("info", info);
            return out.toString();
        } catch (Throwable e) {
            return errorJson(out, "token export failed: " + e);
        }
    }

    /**
     * POST /stock/trade/token/import body={"token","time","login":true}
     * 跨设备共享实验：把（手机端导出的）明文 WtToken 写回本机 token 仓库——
     * 走官方入口 z7m.o(mgr, {"WtToken","Time"})，用本机密钥重新加密入库并
     * 持久化；login=true（默认）随即走 doActiveTradeLogin 验证可用性。
     */
    private static String handleTradeTokenImport(String body) throws org.json.JSONException {
        JSONObject out = new JSONObject();
        out.put("endpoint", "token_import");
        long t0 = System.currentTimeMillis();
        ClassLoader cl = resolveAppClassLoader(null);
        if (cl == null) return errorJson(out, "classloader not ready");
        if (!ensureTradeRuntimeBase(cl)) return errorJson(out, lastEnsureTradeError);
        JSONObject req;
        try {
            req = new JSONObject(body);
        } catch (Throwable e) {
            return errorJson(out, "bad json body: " + e.getMessage());
        }
        String token = req.optString("token", "");
        String time = req.optString("time", "");
        if (token.isEmpty() || time.isEmpty()) {
            return errorJson(out, "token and time required");
        }
        try {
            Class<?> z7mClass = cl.loadClass("z7m");
            Object z7mInst = z7mClass.getMethod("k").invoke(null);
            JSONObject userInfo = new JSONObject();
            userInfo.put("WtToken", token);
            userInfo.put("Time", time);
            z7mClass.getMethod("o",
                            cl.loadClass("pzr"), org.json.JSONObject.class)
                    .invoke(z7mInst, tradeAccountManagerInstance, userInfo);
            // token 明文并入 seed 文件（thshook_trade_seed.json 增 token/time 字段）：
            // AVD 读取路径 x7m livetime 重算使 z7m.i 恒 null（重启后 token 不可用），
            // 登录门在 token 不可用时自动重播（applyTokenSeedToken，见 doActiveTradeLogin）
            try {
                File seedFile = new File(appInstance.getFilesDir(),
                        "thshook_trade_seed.json");
                JSONObject persist = seedFile.exists()
                        ? new JSONObject(readFileToString(seedFile)) : new JSONObject();
                persist.put("token", token);
                persist.put("token_time", time);
                java.io.FileWriter sw = new java.io.FileWriter(seedFile, false);
                sw.write(persist.toString());
                sw.close();
            } catch (Throwable persistEx) {
                Log.w(TAG, "token seed persist failed: " + persistEx);
            }
            // AVD 实测 x7m.i(pzr)=0（keep-login 全局开关 ehi.m().n() 未开）→
            // TokenInfo.mLiveTime=0 → isAvailable 恒 false；对齐真机行为补
            // 1440 分钟并回写 authorization.bat（livetime 随 TokenInfo 序列化）。
            try {
                String uid2 = (String) cl.loadClass("ulm").getMethod("e").invoke(null);
                java.util.List<?> tis = (java.util.List<?>) z7mClass
                        .getMethod("m", String.class).invoke(z7mInst, uid2);
                String expQsid = String.valueOf(tradeAccountManagerInstance.getClass()
                        .getMethod("q").invoke(tradeAccountManagerInstance));
                String expAcc = String.valueOf(tradeAccountManagerInstance.getClass()
                        .getMethod("d").invoke(tradeAccountManagerInstance));
                if (tis != null) {
                    for (Object ti : tis) {
                        Class<?> tiC = ti.getClass();
                        if (expQsid.equals(String.valueOf(tiC.getField("qsId").get(ti)))
                                && expAcc.equals(String.valueOf(
                                        tiC.getField("accountStr").get(ti)))) {
                            java.lang.reflect.Field ltF = tiC.getField("mLiveTime");
                            if (((Integer) ltF.get(ti)) <= 0) {
                                ltF.setInt(ti, 1440);
                                z7mClass.getMethod("t", String.class)
                                        .invoke(z7mInst, uid2);
                                out.put("livetime_fixed", true);
                            }
                            break;
                        }
                    }
                }
            } catch (Throwable e) {
                Log.w(TAG, "token import livetime fix failed: " + e);
            }
            out.put("stored", true);
            String userId = (String) cl.loadClass("ulm").getMethod("e").invoke(null);
            Object tokenInfo = z7mClass
                    .getMethod("i", String.class, cl.loadClass("pzr"))
                    .invoke(z7mInst, userId, tradeAccountManagerInstance);
            out.put("readback_available", tokenInfo != null);
            if (tokenInfo == null) {
                // 诊断（结构信息，无 token 值）：列表是否为空、匹配字段、livetime
                try {
                    java.util.List<?> list = (java.util.List<?>) z7mClass
                            .getMethod("m", String.class).invoke(z7mInst, userId);
                    JSONArray arr = new JSONArray();
                    if (list != null) {
                        for (Object ti : list) {
                            JSONObject o = new JSONObject();
                            Class<?> tiC = ti.getClass();
                            o.put("qsId", tiC.getField("qsId").get(ti));
                            o.put("accountStr", tiC.getField("accountStr").get(ti));
                            o.put("natureType", tiC.getField("accountNatureType").get(ti));
                            o.put("accountType", tiC.getField("accountType").get(ti));
                            try {
                                o.put("mLiveTime", tiC.getField("mLiveTime").get(ti));
                            } catch (Throwable ignore) { }
                            try {
                                o.put("available", tiC.getMethod("isAvailable").invoke(ti));
                            } catch (Throwable ignore) { }
                            arr.put(o);
                        }
                    }
                    out.put("token_list_size", list == null ? -1 : list.size());
                    out.put("token_list_diag", arr);
                } catch (Throwable e) {
                    out.put("diag_error", String.valueOf(e));
                }
            }
            if (req.optBoolean("login", true)) {
                boolean ok = doActiveTradeLogin(cl, out);
                out.put("ok", ok);
            } else {
                out.put("ok", true);
            }
        } catch (Throwable e) {
            return errorJson(out, "token import failed: " + e);
        }
        out.put("elapsed_ms", System.currentTimeMillis() - t0);
        return out.toString();
    }

    /**
     * GET /stock/trade/account/export — 导出当前交易账户对象（跨设备 seed 用）。
     * 用官方序列化 pzr.B(JSONObject, true)（与仓库持久化 C() 对称），附带
     * q()/d()/w()/e()/f()/x() 运行时元数据供 seed 后比对校准。
     * ⚠ 输出含 compwd（通讯密码材料）与资金账号，敏感度与 token 同级：
     * 仅限受控通道，禁止入库/提交/记日志。
     */
    private static String handleTradeAccountExport() throws org.json.JSONException {
        JSONObject out = new JSONObject();
        out.put("endpoint", "account_export");
        ClassLoader cl = resolveAppClassLoader(null);
        if (cl == null) return errorJson(out, "classloader not ready");
        if (!ensureTradeRuntimeBase(cl)) return errorJson(out, lastEnsureTradeError);
        Object mgr = tradeAccountManagerInstance;
        if (mgr == null) return errorJson(out, "trade account manager not captured (F(119)=null)");
        try {
            JSONObject json = new JSONObject();
            mgr.getClass().getMethod("B", org.json.JSONObject.class, boolean.class)
                    .invoke(mgr, json, true);
            JSONObject meta = new JSONObject();
            Method[] getters = {
                    mgr.getClass().getMethod("q"),   // qsid
                    mgr.getClass().getMethod("d"),   // 券商账号
                    mgr.getClass().getMethod("w"),   // 营业部/类别串
                    mgr.getClass().getMethod("x"),   // 资金账号 zjzh
                    mgr.getClass().getMethod("f"),   // 字符串元数据
            };
            String[] keys = {"qsid", "account", "w", "zjzh", "f"};
            for (int i = 0; i < getters.length; i++) {
                meta.put(keys[i], String.valueOf(getters[i].invoke(mgr)));
            }
            meta.put("e", mgr.getClass().getMethod("e").invoke(mgr)); // int 类型
            // a1s 券商信息对象（n0s.E 入库前置：v()!=null）。按 a1s.y() 解析键序列化，
            // AVD 端本地 yyb 库缺该券商时用 y(json, false, Proxy回调) 重建。
            Object broker = mgr.getClass().getMethod("v").invoke(mgr);
            if (broker != null) {
                JSONObject brokerJson = new JSONObject();
                Class<?> a1sC = broker.getClass();
                String[] fieldToKey = {
                        "a", "yybname", "b", "accounttype", "f", "wtid", "g", "qsid",
                        "h", "area", "i", "qsname", "j", "pinyin", "k", "dtkltype",
                        "l", "getzb", "m", "zztype", "q", "yybfunc",
                        "s", "pluginurlandroid", "t", "pluginverandroid",
                };
                for (int i = 0; i < fieldToKey.length; i += 2) {
                    try {
                        Object v = a1sC.getField(fieldToKey[i]).get(broker);
                        brokerJson.put(fieldToKey[i + 1], v == null ? "" : String.valueOf(v));
                    } catch (Throwable ignore) { }
                }
                try {
                    brokerJson.put("last_select", a1sC.getField("u").get(broker));
                } catch (Throwable ignore) { }
                JSONObject extra = new JSONObject();
                for (String fn : new String[]{"c", "d", "e", "n"}) {
                    try {
                        Object v = a1sC.getField(fn).get(broker);
                        extra.put(fn, v == null ? "" : String.valueOf(v));
                    } catch (Throwable ignore) { }
                }
                brokerJson.put("_extra", extra);
                out.put("broker", brokerJson);
            }
            out.put("ok", true);
            out.put("qsid", meta.optString("qsid"));
            out.put("json", json);
            out.put("meta", meta);
        } catch (Throwable e) {
            return errorJson(out, "account export failed: " + e);
        }
        return out.toString();
    }

    /**
     * POST /stock/trade/account/seed body={"qsid","json"}
     * 把（真机导出的）交易账户写进本机账户仓库：new fzr(0) → C(qsid,json)
     * 官方反序列化 → n0s.s().b(pzr) 官方添加入口（内存+加密仓库持久化）→
     * izr.a.x(fzr) 激活打分 → 轮询 F(119) 捕获。响应回显 seed 后实际
     * q()/d()/w()/e()/f()/x() 供与真机 meta 对比校准。
     */
    private static String handleTradeAccountSeed(String body) throws org.json.JSONException {
        JSONObject out = new JSONObject();
        out.put("endpoint", "account_seed");
        long t0 = System.currentTimeMillis();
        ClassLoader cl = resolveAppClassLoader(null);
        if (cl == null) return errorJson(out, "classloader not ready");
        if (!ensureTradeMasterModule(cl)) return errorJson(out, lastEnsureTradeError);
        JSONObject req;
        try {
            req = new JSONObject(body);
        } catch (Throwable e) {
            return errorJson(out, "bad json body: " + e.getMessage());
        }
        String qsid = req.optString("qsid", "");
        JSONObject json = req.optJSONObject("json");
        if (qsid.isEmpty() || json == null) {
            return errorJson(out, "qsid and json required");
        }
        try {
            JSONObject brokerJson = req.optJSONObject("broker");
            Object captured = applyTradeAccountSeed(cl, qsid, json, brokerJson, out);
            JSONObject echo = new JSONObject();
            if (captured != null) {
                echo.put("qsid", captured.getClass().getMethod("q").invoke(captured));
                echo.put("account", captured.getClass().getMethod("d").invoke(captured));
                echo.put("w", captured.getClass().getMethod("w").invoke(captured));
                echo.put("zjzh", captured.getClass().getMethod("x").invoke(captured));
                echo.put("f", captured.getClass().getMethod("f").invoke(captured));
                echo.put("e", captured.getClass().getMethod("e").invoke(captured));
            }
            out.put("ok", captured != null);
            out.put("captured", captured != null);
            out.put("echo", echo);
            if (captured == null) {
                out.put("error", String.valueOf(out.opt("seed_error")));
            } else {
                // App 自己的账户仓库在 yyb 券商库未初始化的环境（AVD 实测）不会
                // 落盘，重启即失；seed body 存 filesDir，ensure 阶段自动重播。
                JSONObject persist = new JSONObject();
                persist.put("qsid", qsid);
                persist.put("json", json);
                if (brokerJson != null) persist.put("broker", brokerJson);
                File seedFile = new File(appInstance.getFilesDir(),
                        "thshook_trade_seed.json");
                try (FileOutputStream fos = new FileOutputStream(seedFile, false)) {
                    fos.write(persist.toString()
                            .getBytes(java.nio.charset.StandardCharsets.UTF_8));
                }
                out.put("persisted", true);
            }
        } catch (Throwable e) {
            return errorJson(out, "account seed failed: " + e);
        }
        out.put("elapsed_ms", System.currentTimeMillis() - t0);
        return out.toString();
    }

    /**
     * seed 核心（端点与 ensure 自动重播共用）：new fzr(0) → C(qsid,json) 官方
     * 反序列化 → a1s 解析（本地 yyb 库 b1s.W(qsid)，缺则用导出 JSON 经
     * a1s.y(json,false,Proxy回调) 重建）→ V(a1s) 填充 → n0s.s().b() 官方
     * 添加入口 → izr.a.x() 激活 → 轮询 F(119)。返回捕获的 manager（null=失败）。
     */
    private static Object applyTradeAccountSeed(ClassLoader cl, String qsid,
            JSONObject json, JSONObject brokerJson, JSONObject out) throws Exception {
        Class<?> fzrClass = cl.loadClass("fzr");
        Object f = fzrClass.getConstructor(int.class).newInstance(0);
        fzrClass.getMethod("C", String.class, org.json.JSONObject.class)
                .invoke(f, qsid, json);
        // n0s.E(pzr) 要求 v()!=null（a1s 券商信息对象）才入库；V(a1s) 同时填
        // s/g(qsid)/d(券商名)/h(wtid)。先查本机 yyb 券商库（b1s.W）。
        Object broker = null;
        try {
            Object c1sInst = cl.loadClass("c1s").getMethod("m").invoke(null);
            Object b1sInst = cl.loadClass("c1s").getMethod("p").invoke(c1sInst);
            Class<?> b1sClass = b1sInst.getClass();
            try {
                b1sClass.getMethod("K0").invoke(b1sInst);
            } catch (Throwable e) {
                Log.w(TAG, "seed: b1s.K0() load yyb db failed: " + e);
            }
            broker = b1sClass.getMethod("W", String.class).invoke(b1sInst, qsid);
        } catch (Throwable e) {
            Log.w(TAG, "seed: local yyb db lookup failed: " + e);
        }
        if (out != null) out.put("broker_found", broker != null);
        if (broker == null) {
            // 本地 yyb 库无该券商（未下载）：用真机导出的 a1s JSON 重建。
            // a1s.y(json, false, cb) 的 cb（a1s$a 接口）在 accountlist 缺省时
            // 只需 a/c/d 三个回调；Proxy 返回空实现。
            if (brokerJson == null || !brokerJson.has("qsid")) {
                if (out != null) out.put("seed_error",
                        "broker (a1s) unavailable: local yyb db empty for qsid="
                                + qsid + " and no broker json");
                return null;
            }
            Class<?> cbClass = cl.loadClass("a1s$a");
            Object cb = Proxy.newProxyInstance(cl, new Class[]{cbClass},
                    (p, m, args) -> {
                        switch (m.getName()) {
                            case "a": case "h": return null;
                            case "e": return 0;
                            case "g": return false;
                            case "b": case "i": return "";
                            case "getContext": return appInstance;
                            case "hashCode": return System.identityHashCode(p);
                            case "toString": return p.getClass().getName();
                            case "equals": return p == args[0];
                            default: return null; // c/d/f void no-op
                        }
                    });
            broker = cl.loadClass("a1s")
                    .getMethod("y", org.json.JSONObject.class, boolean.class, cbClass)
                    .invoke(null, brokerJson, false, cb);
            JSONObject extra = brokerJson.optJSONObject("_extra");
            if (extra != null && broker != null) {
                Class<?> a1sC = broker.getClass();
                for (String fn : new String[]{"c", "d", "e", "n"}) {
                    try {
                        a1sC.getField(fn).set(broker, extra.optString(fn, ""));
                    } catch (Throwable ignore) { }
                }
            }
            if (out != null) out.put("broker_rebuilt", broker != null);
            Log.i(TAG, "seed: a1s rebuilt from export json, ok=" + (broker != null));
        }
        if (broker == null) {
            if (out != null) out.put("seed_error", "broker (a1s) rebuild failed for qsid=" + qsid);
            return null;
        }
        fzrClass.getMethod("V", cl.loadClass("a1s")).invoke(f, broker);
        cl.loadClass("n0s").getMethod("b", cl.loadClass("pzr"))
                .invoke(cl.loadClass("n0s").getMethod("s").invoke(null), f);
        Class<?> izrClass = cl.loadClass("izr");
        Object izrInst = izrClass.getField("a").get(null);
        izrClass.getMethod("x", cl.loadClass("pzr")).invoke(izrInst, f);
        // 主动轮询 F(119)（w5s.v(119) 按 izr 打分选择），不能只读静态捕获字段
        Class<?> p0sClass = cl.loadClass("p0s");
        java.lang.reflect.Method f119 = p0sClass.getDeclaredMethod("F", int.class);
        f119.setAccessible(true);
        Object captured = null;
        for (int i = 0; i < 30 && captured == null; i++) {
            Thread.sleep(1000);
            captured = f119.invoke(null, 119);
        }
        if (captured != null) captureTradeAccountManager(captured);
        return captured;
    }

    // ==================================================================
    // token 自动上报（2026-08-17）：真机打开 App / 主动登录后自动把有效 token
    // POST 到服务端 /api/ths/token（X-Api-Key 认证），服务端入库 ft_ths_tokens，
    // 过期自愈时 import 回设备。配置来源：filesDir/thshook_report.json（端点可写）
    // 或 /data/local/tmp/thshook_report.json（adb push 只读）。token 明文仅进
    // HTTPS/受控通道请求体，禁止写日志/文件。
    // ==================================================================

    private static File tokenReportConfigFileApp() {
        Context app = appInstance;
        if (app == null) return null;
        return new File(app.getFilesDir(), "thshook_report.json");
    }

    private static File tokenReportConfigFileTmp() {
        return new File("/data/local/tmp/thshook_report.json");
    }

    /** 懒加载上报配置：filesDir 优先（端点写入的持久配置），tmp 兜底（adb push）。 */
    private static void loadTokenReportConfig() {
        if (tokenReportConfigLoaded) return;
        synchronized (tokenReportExecutor) {
            if (tokenReportConfigLoaded) return;
            for (File f : new File[]{tokenReportConfigFileApp(), tokenReportConfigFileTmp()}) {
                if (f == null || !f.exists() || !f.canRead()) continue;
                try (FileInputStream fis = new FileInputStream(f)) {
                    byte[] buf = new byte[(int) f.length()];
                    int read = fis.read(buf);
                    JSONObject cfg = new JSONObject(new String(buf, 0, Math.max(read, 0),
                            java.nio.charset.StandardCharsets.UTF_8));
                    String url = cfg.optString("url", "").trim();
                    String key = cfg.optString("api_key", "").trim();
                    if (!url.isEmpty() && !key.isEmpty()) {
                        tokenReportUrl = url;
                        tokenReportApiKey = key;
                        tokenReportEnabled = cfg.optBoolean("enabled", true);
                        Log.i(TAG, "token report config loaded from "
                                + f.getName() + " url host="
                                + java.net.URI.create(url).getHost());
                        break;
                    }
                } catch (Throwable e) {
                    Log.w(TAG, "token report config read failed (" + f + "): " + e);
                }
            }
            tokenReportConfigLoaded = true;
        }
    }

    /** 端点写入配置：更新内存 + 持久化到 filesDir（重启仍生效）。 */
    private static void saveTokenReportConfig(String url, String apiKey, Boolean enabled)
            throws JSONException, java.io.IOException {
        if (url != null) tokenReportUrl = url.trim();
        if (apiKey != null && !apiKey.trim().isEmpty()) tokenReportApiKey = apiKey.trim();
        if (enabled != null) tokenReportEnabled = enabled;
        tokenReportConfigLoaded = true;
        File f = tokenReportConfigFileApp();
        if (f != null) {
            JSONObject cfg = new JSONObject();
            cfg.put("url", tokenReportUrl == null ? "" : tokenReportUrl);
            cfg.put("api_key", tokenReportApiKey == null ? "" : tokenReportApiKey);
            cfg.put("enabled", tokenReportEnabled);
            try (FileOutputStream fos = new FileOutputStream(f, false)) {
                fos.write(cfg.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8));
            }
        }
    }

    private static String getDeviceId() {
        if (deviceIdCache != null) return deviceIdCache;
        try {
            Context app = appInstance;
            if (app != null) {
                String id = android.provider.Settings.Secure.getString(
                        app.getContentResolver(),
                        android.provider.Settings.Secure.ANDROID_ID);
                if (id != null && !id.isEmpty()) {
                    deviceIdCache = android.os.Build.MODEL + "-" + id.substring(
                            Math.max(0, id.length() - 6));
                    return deviceIdCache;
                }
            }
        } catch (Throwable e) {
            Log.w(TAG, "getDeviceId failed: " + e);
        }
        deviceIdCache = android.os.Build.MODEL + "-unknown";
        return deviceIdCache;
    }

    private static String sha256Hex(String s) {
        try {
            java.security.MessageDigest md = java.security.MessageDigest.getInstance("SHA-256");
            byte[] d = md.digest(s.getBytes(java.nio.charset.StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder();
            for (byte b : d) sb.append(String.format(Locale.US, "%02x", b));
            return sb.toString();
        } catch (Throwable e) {
            return null;
        }
    }

    /** 补齐 payload 元数据（executor 线程内执行，反射失败静默缺省）。 */
    private static void enrichTokenReportPayload(JSONObject payload) {
        try {
            ClassLoader cl = resolveAppClassLoader(null);
            if (cl != null) {
                payload.put("user_id", cl.loadClass("ulm").getMethod("e").invoke(null));
                Class<?> z7mClass = cl.loadClass("z7m");
                Object z7mInst = z7mClass.getMethod("k").invoke(null);
                Object mgr = tradeAccountManagerInstance;
                if (z7mInst != null && mgr != null) {
                    Object userId = payload.opt("user_id");
                    Object tokenInfo = z7mClass
                            .getMethod("i", String.class, cl.loadClass("pzr"))
                            .invoke(z7mInst, userId, mgr);
                    if (tokenInfo != null) {
                        Class<?> bindingCls = tokenInfo.getClass().getSuperclass();
                        payload.put("qsid", String.valueOf(
                                bindingCls.getField("qsId").get(tokenInfo)));
                        payload.put("account", String.valueOf(
                                bindingCls.getField("accountStr").get(tokenInfo)));
                        payload.put("wtid", String.valueOf(
                                bindingCls.getField("wtId").get(tokenInfo)));
                        Object acctType = bindingCls.getField("accountType").get(tokenInfo);
                        if (acctType instanceof Integer) payload.put("accounttype", acctType);
                        Object natureType = bindingCls.getField("accountNatureType").get(tokenInfo);
                        if (natureType instanceof Integer) {
                            payload.put("accountNatureType", natureType);
                        }
                        Object live = tokenInfo.getClass().getField("mLiveTime").get(tokenInfo);
                        if (live instanceof Integer) payload.put("livetime", live);
                    }
                }
            }
        } catch (Throwable e) {
            Log.w(TAG, "token report enrich failed: " + e);
        }
        try {
            payload.put("device_id", getDeviceId());
        } catch (JSONException ignored) {
        }
    }

    /**
     * 异步上报 token（sha256 去重 + 单线程串行 + 3 次退避重试 5s/15s）。
     * 401/503（认证/未配置）不重试——重试也不会成功，等配置修复。
     * 永不抛出、永不把 token 写进日志。
     */
    private static void reportTokenAsync(String token, String time, String source) {
        if (token == null || token.isEmpty() || time == null || time.isEmpty()) return;
        try {
            loadTokenReportConfig();
            if (!tokenReportEnabled || tokenReportUrl == null || tokenReportUrl.isEmpty()) {
                lastTokenReportStatus = "disabled (no config)";
                return;
            }
            String hash = sha256Hex(token);
            if (hash != null && hash.equals(lastReportedTokenHash)) return;
            lastTokenReportStatus = "pending";
            final String fToken = token, fTime = time, fSource = source;
            tokenReportExecutor.execute(() -> {
                JSONObject payload = new JSONObject();
                try {
                    payload.put("token", fToken);
                    payload.put("time", fTime);
                    payload.put("source", fSource == null ? "device" : fSource);
                    enrichTokenReportPayload(payload);
                } catch (JSONException e) {
                    lastTokenReportStatus = "payload build failed";
                    return;
                }
                byte[] body = payload.toString()
                        .getBytes(java.nio.charset.StandardCharsets.UTF_8);
                String hash2 = sha256Hex(fToken);
                for (int attempt = 1; attempt <= 3; attempt++) {
                    HttpURLConnection conn = null;
                    try {
                        conn = (HttpURLConnection) new URL(tokenReportUrl).openConnection();
                        conn.setRequestMethod("POST");
                        conn.setConnectTimeout(10000);
                        conn.setReadTimeout(20000);
                        conn.setDoOutput(true);
                        conn.setInstanceFollowRedirects(false);
                        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                        conn.setRequestProperty("X-Api-Key",
                                tokenReportApiKey == null ? "" : tokenReportApiKey);
                        conn.setRequestProperty("Connection", "close");
                        try (OutputStream os = conn.getOutputStream()) {
                            os.write(body);
                        }
                        int code = conn.getResponseCode();
                        if (code >= 200 && code < 300) {
                            lastReportedTokenHash = hash2;
                            lastTokenReportStatus = "ok http=" + code
                                    + " attempt=" + attempt + " t=" + System.currentTimeMillis();
                            Log.i(TAG, "token report ok (http=" + code
                                    + " attempt=" + attempt + ")");
                            return;
                        }
                        if (code == 401 || code == 503) {
                            lastTokenReportStatus = "rejected http=" + code
                                    + " (check api key / server config)";
                            Log.w(TAG, "token report rejected http=" + code);
                            return;
                        }
                        lastTokenReportStatus = "http=" + code + " attempt=" + attempt;
                        Log.w(TAG, "token report http=" + code + " attempt=" + attempt);
                    } catch (Throwable e) {
                        lastTokenReportStatus = "error attempt=" + attempt + ": " + e;
                        Log.w(TAG, "token report attempt=" + attempt + " failed: " + e);
                    } finally {
                        if (conn != null) try {
                            conn.disconnect();
                        } catch (Throwable ignored) {
                        }
                    }
                    if (attempt < 3) {
                        try {
                            Thread.sleep(attempt == 1 ? 5000L : 15000L);
                        } catch (InterruptedException e) {
                            Thread.currentThread().interrupt();
                            return;
                        }
                    }
                }
                lastTokenReportStatus = "failed after 3 attempts"
                        + " t=" + System.currentTimeMillis();
            });
        } catch (Throwable e) {
            Log.w(TAG, "reportTokenAsync submit failed: " + e);
        }
    }

    /** 登录成功后的兜底上报：延迟 3s（等 z7m.w 先报，同 token 去重跳过）再导出上报。 */
    private static void schedulePostLoginTokenReport() {
        tokenReportExecutor.execute(() -> {
            try {
                Thread.sleep(3000);
                String result = handleTradeTokenExport();
                JSONObject jo = new JSONObject(result);
                if (jo.optBoolean("ok")) {
                    reportTokenAsync(jo.optString("token"), jo.optString("time"),
                            "post_login_export:" + jo.optString("source", "unknown"));
                } else {
                    Log.i(TAG, "post-login token export not available: "
                            + jo.optString("error"));
                }
            } catch (Throwable e) {
                Log.w(TAG, "post-login token report failed: " + e);
            }
        });
    }

    /** GET /stock/trade/token/report — 上报状态 + 当前配置（api_key 打码）。 */
    private static String handleTokenReportStatus() throws JSONException {
        loadTokenReportConfig();
        JSONObject out = new JSONObject();
        out.put("endpoint", "token_report");
        out.put("enabled", tokenReportEnabled);
        out.put("url", tokenReportUrl == null ? "" : tokenReportUrl);
        String key = tokenReportApiKey;
        out.put("api_key_masked", key == null || key.isEmpty() ? ""
                : key.substring(0, Math.min(4, key.length())) + "****");
        out.put("last_status", lastTokenReportStatus);
        out.put("last_token_hash_prefix", lastReportedTokenHash == null ? null
                : lastReportedTokenHash.substring(0, 8));
        return out.toString();
    }

    /**
     * POST /stock/trade/token/report body={"url","api_key","enabled","force"}
     * 配置并（可选 force）立即触发一次导出上报。url/api_key 留空表示不改。
     */
    private static String handleTokenReportConfig(String body) throws JSONException {
        JSONObject out = new JSONObject();
        out.put("endpoint", "token_report_config");
        try {
            JSONObject req = new JSONObject(body == null || body.isEmpty() ? "{}" : body);
            String url = req.optString("url", "");
            String apiKey = req.optString("api_key", "");
            Boolean enabled = req.has("enabled") ? req.optBoolean("enabled") : null;
            saveTokenReportConfig(url.isEmpty() ? null : url,
                    apiKey.isEmpty() ? null : apiKey, enabled);
            if (req.optBoolean("force", false)) {
                schedulePostLoginTokenReport();
                out.put("force_triggered", true);
            }
            out.put("ok", true);
            out.put("status", handleTokenReportStatus());
            return out.toString();
        } catch (Throwable e) {
            return errorJson(out, "token report config failed: " + e);
        }
    }

    // ==================================================================
    // 交易主实例角色（2026-08-17）：filesDir/thshook_trade_role.json
    // {"enabled":true}。filesDir 按 Android user 隔离，生产同一 AVD 8 实例
    // 只有配置过的主实例启用交易功能（预热/登录/查询/下单/token 端点）。
    // ==================================================================

    private static File tradeRoleConfigFile() {
        Context app = appInstance;
        if (app == null) return null;
        return new File(app.getFilesDir(), "thshook_trade_role.json");
    }

    private static void loadTradeRoleConfig() {
        if (tradeRoleLoaded) return;
        synchronized (tradeRoleLock) {
            if (tradeRoleLoaded) return;
            File f = tradeRoleConfigFile();
            if (f != null && f.exists() && f.canRead()) {
                try (FileInputStream fis = new FileInputStream(f)) {
                    byte[] buf = new byte[(int) f.length()];
                    int read = fis.read(buf);
                    JSONObject cfg = new JSONObject(new String(buf, 0,
                            Math.max(read, 0), java.nio.charset.StandardCharsets.UTF_8));
                    tradeRoleEnabled = cfg.optBoolean("enabled", false);
                    tradeRoleLoaded = true;
                    Log.i(TAG, "trade role loaded: enabled=" + tradeRoleEnabled);
                    return;
                } catch (Throwable e) {
                    Log.w(TAG, "trade role config read failed: " + e);
                }
            }
            // 无配置文件：zygisksu 真机（单实例）默认启用；LSPosed 桥环境
            // （生产 AVD 多实例）默认禁用，防止并发登录互顶。
            tradeRoleEnabled = !injectedViaLsposed;
            tradeRoleLoaded = true;
            Log.i(TAG, "trade role default (no config): enabled=" + tradeRoleEnabled
                    + " lsposed=" + injectedViaLsposed);
        }
    }

    private static boolean isTradeRoleEnabled() {
        loadTradeRoleConfig();
        return tradeRoleEnabled;
    }

    /** GET /stock/trade/push-events — WT 推送事件缓冲。 */
    private static String handleTradePushEvents() throws JSONException {
        JSONObject out = new JSONObject();
        out.put("endpoint", "push_events");
        out.put("ok", true);
        out.put("total_received", wtPushCount);
        java.util.List<String> events = new ArrayList<>();
        synchronized (wtPushLock) {
            events.addAll(wtPushEvents);
        }
        out.put("count", events.size());
        out.put("events", new org.json.JSONArray(events));
        return out.toString();
    }

    /** GET /stock/trade/device-info — 设备指纹观测（真机/AVD diff 数据源）。 */
    private static String handleTradeDeviceInfo() throws JSONException {
        JSONObject out = new JSONObject();
        out.put("endpoint", "device_info");
        out.put("ok", true);
        out.put("model", android.os.Build.MODEL);
        out.put("brand", android.os.Build.BRAND);
        out.put("device", android.os.Build.DEVICE);
        out.put("manufacturer", android.os.Build.MANUFACTURER);
        try {
            out.put("android_id", android.provider.Settings.Secure.getString(
                    appInstance.getContentResolver(),
                    android.provider.Settings.Secure.ANDROID_ID));
        } catch (Throwable ignored) { }
        ClassLoader cl = thsAppClassLoader;
        if (cl != null) {
            try {
                Class<?> n6mClass = cl.loadClass("n6m");
                Object l = n6mClass.getMethod("l").invoke(null);
                out.put("udid_l", l == null ? "" : l.toString());
                Object n6mInst = n6mClass.getMethod("r").invoke(null);
                Object s = n6mClass.getMethod("s").invoke(n6mInst);
                out.put("udid_s", s == null ? "" : s.toString());
            } catch (Throwable t) {
                out.put("udid_error", String.valueOf(t));
            }
        }
        out.put("getconfiginfo_query", lastGetConfigInfoQuery);
        out.put("getconfiginfo_result", lastGetConfigInfoResult);
        out.put("spoof_active", !spoofUdidL.isEmpty() || !spoofUdidS.isEmpty()
                || !spoofGetConfigInfo.isEmpty());
        return out.toString();
    }

    /** POST /stock/trade/device-spoof — 安装/更新设备指纹伪造。 */
    private static String handleTradeDeviceSpoof(String body) throws JSONException {
        JSONObject out = new JSONObject();
        out.put("endpoint", "device_spoof");
        JSONObject req = body == null || body.isEmpty() ? new JSONObject() : new JSONObject(body);
        String udidL = req.optString("udid_l", "");
        String udidS = req.optString("udid_s", "");
        String gcInfo = req.optString("getconfiginfo", "");
        String model = req.optString("model", "");
        String brand = req.optString("brand", "");
        boolean enabled = req.optBoolean("enabled", true);
        if (!enabled) {
            spoofUdidL = "";
            spoofUdidS = "";
            spoofGetConfigInfo = "";
            persistSpoofConfig("", "", "", "", "");
            out.put("ok", true);
            out.put("result", "spoof cleared (restart app to fully reset)");
            return out.toString();
        }
        ClassLoader cl = thsAppClassLoader;
        if (cl == null) {
            out.put("ok", false);
            out.put("error", "app classloader not captured");
            return out.toString();
        }
        try {
            spoofUdidL = udidL;
            spoofUdidS = udidS;
            spoofGetConfigInfo = gcInfo;
            if (!model.isEmpty()) setStaticStringField(android.os.Build.class, "MODEL", model);
            if (!brand.isEmpty()) setStaticStringField(android.os.Build.class, "BRAND", brand);
            boolean hooked = installDeviceSpoofHooks(cl);
            persistSpoofConfig(udidL, udidS, gcInfo, model, brand);
            out.put("ok", hooked);
            out.put("udid_l_len", udidL.length());
            out.put("udid_s_len", udidS.length());
            out.put("getconfiginfo_len", gcInfo.length());
            out.put("note", "udid spoof must be active BEFORE token import "
                    + "(udid is also the token encryption key)");
            return out.toString();
        } catch (Throwable e) {
            out.put("ok", false);
            out.put("error", String.valueOf(e));
            return out.toString();
        }
    }

    /** 启动期从 filesDir 恢复 spoof 配置并安装 hooks（bridge hook 安装后调用）。 */
    private static void loadDeviceSpoofConfig(ClassLoader cl) {
        try {
            if (appInstance == null) return;
            File f = new File(appInstance.getFilesDir(), "thshook_spoof.json");
            if (!f.exists()) return;
            JSONObject cfg = new JSONObject(readFileToString(f));
            if (!cfg.optBoolean("enabled", false)) return;
            spoofUdidL = cfg.optString("udid_l", "");
            spoofUdidS = cfg.optString("udid_s", "");
            spoofGetConfigInfo = cfg.optString("getconfiginfo", "");
            String model = cfg.optString("model", "");
            String brand = cfg.optString("brand", "");
            if (!model.isEmpty()) setStaticStringField(android.os.Build.class, "MODEL", model);
            if (!brand.isEmpty()) setStaticStringField(android.os.Build.class, "BRAND", brand);
            installDeviceSpoofHooks(cl);
            Log.w(TAG, "DeviceSpoof restored from config (udid_l_len="
                    + spoofUdidL.length() + ")");
        } catch (Throwable e) {
            Log.w(TAG, "loadDeviceSpoofConfig failed: " + e);
        }
    }

    private static void persistSpoofConfig(String udidL, String udidS, String gcInfo,
                                           String model, String brand) {
        try {
            if (appInstance == null) return;
            JSONObject cfg = new JSONObject();
            boolean any = !udidL.isEmpty() || !udidS.isEmpty() || !gcInfo.isEmpty();
            cfg.put("enabled", any);
            cfg.put("udid_l", udidL);
            cfg.put("udid_s", udidS);
            cfg.put("getconfiginfo", gcInfo);
            cfg.put("model", model);
            cfg.put("brand", brand);
            java.io.FileWriter w = new java.io.FileWriter(
                    new File(appInstance.getFilesDir(), "thshook_spoof.json"));
            w.write(cfg.toString());
            w.close();
        } catch (Throwable e) {
            Log.w(TAG, "persistSpoofConfig failed: " + e);
        }
    }

    /** n6m.l()（绑定设备SUID）/n6m.s()（加密key）返回值伪造。 */
    private static boolean installDeviceSpoofHooks(ClassLoader cl) {
        if (deviceSpoofInstalled) {
            // 已装：只更新静态值即可（hook 读的是 volatile 字段）
            return true;
        }
        boolean any = false;
        try {
            Class<?> n6mClass = cl.loadClass("n6m");
            Method lM = n6mClass.getDeclaredMethod("l");
            lM.setAccessible(true);
            Pine.hook(lM, new MethodHook() {
                @Override
                public void afterCall(Pine.CallFrame callFrame) {
                    if (!spoofUdidL.isEmpty()) callFrame.setResult(spoofUdidL);
                }
            });
            any = true;
            Method sM = n6mClass.getDeclaredMethod("s");
            sM.setAccessible(true);
            Pine.hook(sM, new MethodHook() {
                @Override
                public void afterCall(Pine.CallFrame callFrame) {
                    if (!spoofUdidS.isEmpty()) callFrame.setResult(spoofUdidS);
                }
            });
            deviceSpoofInstalled = true;
            Log.w(TAG, "DeviceSpoof n6m.l/s hooks installed");
        } catch (Throwable e) {
            Log.w(TAG, "DeviceSpoof n6m hooks failed: " + e);
        }
        return any;
    }

    private static void setStaticStringField(Class<?> cls, String name, String value) {
        try {
            java.lang.reflect.Field f = cls.getDeclaredField(name);
            f.setAccessible(true);
            f.set(null, value);
        } catch (Throwable e) {
            Log.w(TAG, "setStaticStringField " + name + " failed: " + e);
        }
    }

    private static String readFileToString(File f) throws Exception {
        java.io.FileInputStream fis = new java.io.FileInputStream(f);
        try {
            java.io.ByteArrayOutputStream bos = new java.io.ByteArrayOutputStream();
            byte[] buf = new byte[4096];
            int n;
            while ((n = fis.read(buf)) > 0) bos.write(buf, 0, n);
            return new String(bos.toByteArray(), "UTF-8");
        } finally {
            fis.close();
        }
    }

    /** 登录探针日志追加（filesDir/thshook_login_probe.log），失败静默 */
    private static void appendLoginProbeLog(String text) {
        try {
            if (appInstance == null) return;
            File f = new File(appInstance.getFilesDir(), "thshook_login_probe.log");
            java.io.FileOutputStream fos = new java.io.FileOutputStream(f, true);
            try {
                fos.write((text + "\n").getBytes("UTF-8"));
            } finally {
                fos.close();
            }
        } catch (Throwable ignored) {
        }
    }

    private static void writeFileString(File f, String content) throws Exception {
        java.io.FileOutputStream fos = new java.io.FileOutputStream(f);
        try {
            fos.write(content.getBytes("UTF-8"));
            fos.getFD().sync();
        } finally {
            fos.close();
        }
    }

    private static String handleTradeCbasStatus() throws JSONException {
        JSONObject out = new JSONObject();
        out.put("endpoint", "cbas_status");
        out.put("ok", true);
        out.put("last_reset", lastCbasServerReset);
        ClassLoader cl = thsAppClassLoader;
        if (cl != null) {
            try {
                Class<?> commSvcClass = cl.loadClass("com.hexin.plat.android.CommunicationService");
                Object commSvc = commSvcClass.getMethod("getCommunicationService")
                        .invoke(null);
                out.put("comm_service", commSvc != null ? "running" : "null");
            } catch (Throwable t) {
                out.put("comm_service", "?" + t.getClass().getSimpleName());
            }
        }
        return out.toString();
    }

    /** POST /stock/trade/cbas {"host","port"} — 手动注入 CBAS 服务器地址。
     *  走官方 CommunicationService.resetCbasServer（PushConnect 同款路径）。 */
    private static String handleTradeCbasSet(String body) throws JSONException {
        JSONObject out = new JSONObject();
        out.put("endpoint", "cbas_set");
        JSONObject req = body == null || body.isEmpty() ? new JSONObject() : new JSONObject(body);
        String host = req.optString("host", "");
        int port = req.optInt("port", 0);
        if (host.isEmpty() || port <= 0) {
            out.put("ok", false);
            out.put("error", "body requires host(string) and port(int)");
            return out.toString();
        }
        ClassLoader cl = thsAppClassLoader;
        if (cl == null) {
            out.put("ok", false);
            out.put("error", "app classloader not captured");
            return out.toString();
        }
        try {
            Class<?> commSvcClass = cl.loadClass("com.hexin.plat.android.CommunicationService");
            Object commSvc = commSvcClass.getMethod("getCommunicationService").invoke(null);
            if (commSvc == null) {
                out.put("ok", false);
                out.put("error", "CommunicationService instance null (not started)");
                return out.toString();
            }
            commSvcClass.getMethod("resetCbasServer", String.class, int.class)
                    .invoke(commSvc, host, port);
            lastCbasServerReset = "manual host=" + host + " port=" + port;
            out.put("ok", true);
            out.put("host", host);
            out.put("port", port);
            out.put("result", "resetCbasServer invoked; CBAS connect will be "
                    + "triggered by next session-check (hrv.C)");
            return out.toString();
        } catch (Throwable e) {
            out.put("ok", false);
            out.put("error", String.valueOf(e));
            return out.toString();
        }
    }

    /** GET /stock/trade/role — 本实例交易角色状态。 */
    private static String handleTradeRoleStatus() throws JSONException {
        JSONObject out = new JSONObject();
        out.put("endpoint", "trade_role_status");
        out.put("ok", true);
        out.put("enabled", isTradeRoleEnabled());
        out.put("config_file", "filesDir/thshook_trade_role.json");
        return out.toString();
    }

    /** POST /stock/trade/role body={"enabled":bool} — 启用/停用本实例交易能力。 */
    private static String handleTradeRoleConfig(String body) throws JSONException {
        JSONObject out = new JSONObject();
        out.put("endpoint", "trade_role_config");
        try {
            JSONObject req = new JSONObject(body == null || body.isEmpty() ? "{}" : body);
            if (!req.has("enabled")) {
                return errorJson(out, "missing field: enabled");
            }
            boolean enabled = req.optBoolean("enabled", false);
            File f = tradeRoleConfigFile();
            if (f == null) {
                return errorJson(out, "app context not ready");
            }
            JSONObject cfg = new JSONObject();
            cfg.put("enabled", enabled);
            try (java.io.FileOutputStream fos = new java.io.FileOutputStream(f)) {
                fos.write(cfg.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8));
            }
            tradeRoleEnabled = enabled;
            tradeRoleLoaded = true;
            Log.i(TAG, "trade role set: enabled=" + enabled);
            out.put("ok", true);
            out.put("enabled", tradeRoleEnabled);
            return out.toString();
        } catch (Throwable e) {
            return errorJson(out, "trade role config failed: " + e);
        }
    }

    /**
     * 主动登录核心（唯一登录发起链，预热与 HTTP 端点共用）。
     * 全进程只从这里发 f2s.q 登录：预热线程的 x0s.F 触发已移除——2026-08-17 实测
     * 三链并发（预热 F + 本端点 + App 组件自发重登）会在券商服务器侧互顶会话
     * （"正在登录中，请稍后再试!"），App 链因此反复失败循环弹"登录失败"。
     * 返回 true=已登录（含 already_logged_in / success-after-timeout）。
     * 细节（result/error/via）写入 report。
     */
    private static boolean doActiveTradeLogin(ClassLoader cl, JSONObject report) {
        return doActiveTradeLogin(cl, report, false);
    }

    /** force=true：跳过 already_logged_in 短路，强制重发 f2s.q 重建服务端会话。
     *  用于查询超时重试前——本地 izr.a.l=true 但服务端会话已失效（90B 踢）的
     *  场景，被动等 App 自愈不可靠，主动登录实测 18s 内恢复。 */
    private static boolean doActiveTradeLogin(ClassLoader cl, JSONObject report, boolean force) {
        synchronized (tradeRuntimeLock) {
            boolean ok = doActiveTradeLoginLocked(cl, report, force, false);
            if (ok) {
                // 触发点2：登录成功后异步导出 token 上报（锁外调度，不阻塞登录链）。
                // z7m.w 若已在本链触发过则由 sha256 去重跳过；此处兜住"重启后
                // already_logged_in（持久化 token）无新登录响应"的场景。
                schedulePostLoginTokenReport();
            }
            return ok;
        }
    }

    /** 交易密码来源：本次请求（pendingTradePassword，一次性）或
     *  filesDir/thshook_pwd.json（持久化 {"password":...}）。绝不写日志。 */
    private static String loadTradePassword() {
        try {
            String p = pendingTradePassword;
            if (p != null && !p.isEmpty()) return p;
            if (appInstance == null) return null;
            File f = new File(appInstance.getFilesDir(), "thshook_pwd.json");
            if (!f.exists()) return null;
            JSONObject o = new JSONObject(readFileToString(f));
            return o.optString("password", "");
        } catch (Throwable t) {
            return null;
        }
    }

    /** 密码登录（2026-08-18，8/19 真机探针修正）：镜像 SimpleWeituoLogin 链——
     *  g6m.a() builder：N(资金账号)/R(密码→g6m.b→线上tag3)/S(空)/T("0")/
     *  O("0")/Q(空)/U(yyb串=g6m.a(a1s))/M(false)/P("1")/X(true)/H(acctype)/G()
     *  → f2s.d().o(g6m, null, q3s.a().s(0).q(1).v(1).o(true), g8m回调)。
     *  keepLogin=true 使券商下发新 token（z7m.w hook 自动捕获上报）。 */
    private static boolean doPasswordTradeLoginLocked(ClassLoader cl,
            JSONObject report, String password) {
        try {
            Object mgr = tradeAccountManagerInstance;
            if (mgr == null) {
                report.put("pwd_error", "no account");
                return false;
            }
            Object broker = mgr.getClass().getMethod("v").invoke(mgr);
            if (broker == null) {
                report.put("pwd_error", "no broker info");
                return false;
            }
            Class<?> g6mClass = cl.loadClass("g6m");
            String yybStr = (String) g6mClass
                    .getMethod("a", cl.loadClass("a1s")).invoke(null, broker);
            if (yybStr == null || yybStr.isEmpty()) {
                report.put("pwd_error", "yyb string empty");
                return false;
            }
            Class<?> builderClass = cl.loadClass("g6m$a");
            Object b = builderClass.newInstance();
            String account = (String) mgr.getClass().getMethod("d").invoke(mgr);
            int accType = (Integer) mgr.getClass().getMethod("e").invoke(mgr);
            b.getClass().getMethod("N", String.class).invoke(b, account);
            // 2026-08-19 真机探针捕获（thshook_login_probe.log）修正：App 实际构造
            // g6m.b=R(密码)（线上 tag3，券商密码校验位）、g6m.c=S("")（tag4 恒空）。
            // 此前 S(密码)/R("0") 导致券商把 "0" 当密码 → "[120047]客户交易密码错误"。
            b.getClass().getMethod("R", String.class).invoke(b, password);
            b.getClass().getMethod("S", String.class).invoke(b, "");
            b.getClass().getMethod("T", String.class).invoke(b, "0");
            b.getClass().getMethod("O", String.class).invoke(b, "0");
            b.getClass().getMethod("Q", String.class).invoke(b, "");
            b.getClass().getMethod("U", String.class).invoke(b, yybStr);
            b.getClass().getMethod("M", boolean.class).invoke(b, Boolean.FALSE);
            b.getClass().getMethod("P", String.class).invoke(b, "1");
            b.getClass().getMethod("X", boolean.class).invoke(b, Boolean.TRUE);
            b.getClass().getMethod("H", int.class).invoke(b, accType);
            Object info = b.getClass().getMethod("G").invoke(b);

            Class<?> q3sClass = cl.loadClass("q3s");
            Object q = q3sClass.getMethod("a").invoke(null);
            q = q3sClass.getMethod("s", int.class).invoke(q, 0);
            q = q3sClass.getMethod("q", int.class).invoke(q, 1);
            q = q3sClass.getMethod("v", int.class).invoke(q, 1);
            q = q3sClass.getMethod("o", boolean.class).invoke(q, Boolean.TRUE);

            CountDownLatch latch = new CountDownLatch(1);
            AtomicReference<String> result = new AtomicReference<>("pending");
            AtomicReference<String> failDetail = new AtomicReference<>();
            Object callback = Proxy.newProxyInstance(cl,
                    new Class[]{cl.loadClass("g8m")},
                    (proxy, method, mArgs) -> {
                        String name = method.getName();
                        if ("onWeituoLoginSuccess".equals(name)) {
                            result.set("success");
                            latch.countDown();
                        } else if ("onWeituoLoginFail".equals(name)) {
                            result.set("fail");
                            failDetail.set(describeStuffStruct(mArgs != null && mArgs.length > 0 ? mArgs[0] : null));
                            latch.countDown();
                        } else if ("interceptTimeout".equals(name) || "onlyMeHandleReceiveData".equals(name)) {
                            return Boolean.FALSE;
                        }
                        if ("hashCode".equals(name)) return System.identityHashCode(proxy);
                        if ("toString".equals(name)) return "THSHook.pwdLogin@" + Integer.toHexString(System.identityHashCode(proxy));
                        if ("equals".equals(name)) return proxy == (mArgs != null && mArgs.length > 0 ? mArgs[0] : null);
                        return null;
                    });
            Object f2sInst = cl.loadClass("f2s").getMethod("d").invoke(null);
            cl.loadClass("f2s").getMethod("o",
                            cl.loadClass("g6m"), cl.loadClass("a1s"),
                            cl.loadClass("q3s"), cl.loadClass("g8m"))
                    .invoke(f2sInst, info, null, q, callback);
            boolean done = latch.await(40, TimeUnit.SECONDS);
            if (!done) {
                report.put("pwd_result", "timeout");
                lastEnsureTradeError = "trade pwd login: no response in 40s";
                return false;
            }
            String r = result.get();
            report.put("pwd_result", r);
            if (!"success".equals(r)) {
                if (failDetail.get() != null) report.put("pwd_fail_detail", failDetail.get());
                lastEnsureTradeError = "trade pwd login fail: " + failDetail.get();
                return false;
            }
            lastEnsureTradeError = null;
            return true;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return false;
        } catch (Throwable t) {
            Throwable cause = t.getCause() != null ? t.getCause() : t;
            try {
                report.put("pwd_error", String.valueOf(cause).substring(0, Math.min(160, String.valueOf(cause).length())));
            } catch (Throwable ignored) { }
            lastEnsureTradeError = "trade pwd login error: " + cause;
            return false;
        }
    }

    private static boolean doActiveTradeLoginLocked(ClassLoader cl, JSONObject report, boolean force,
                                                    boolean preferPwd) {
        Object mgr = tradeAccountManagerInstance;
        try {
            Class<?> izrClass = cl.loadClass("izr");
            Object izrInst = izrClass.getField("a").get(null);
            java.lang.reflect.Method lM = izrClass.getMethod("l", cl.loadClass("pzr"));
            if (!force && Boolean.TRUE.equals(lM.invoke(izrInst, mgr))) {
                report.put("result", "already_logged_in");
                // 陈旧错误清除：会话漂移期残留的 ensure_error 在登录态确认后失效
                lastEnsureTradeError = null;
                return true;
            }
            // isWeituoLogining 时 f2s.q 被 k2s.b 静默丢弃（"could not login wt"），
            // 先等 App 的在途登录结束；期间登录态置位则直接复用
            Class<?> r0sClass = cl.loadClass("r0s");
            Object r0sInst = r0sClass.getMethod("u").invoke(null);
            java.lang.reflect.Method hM = r0sClass.getMethod("H");
            for (int i = 0; i < 20 && Boolean.TRUE.equals(hM.invoke(r0sInst)); i++) {
                Thread.sleep(500);
                if (Boolean.TRUE.equals(lM.invoke(izrInst, mgr))) {
                    report.put("result", "already_logged_in");
                    report.put("via", "app-concurrent-login");
                    return true;
                }
            }
            // 僵尸登录标志清除：AVD 冷环境非静默登录走上 CBAS 死路后 H() 永不
            // 复位（官方复位在 r0s.o()/1小时超时 TimerTask），等待超时后用 App
            // 同款 i0(false) 强制清除，否则本次 f2s.q 仍被 k2s.b 静默丢弃
            if (Boolean.TRUE.equals(hM.invoke(r0sInst))) {
                r0sClass.getMethod("i0", boolean.class).invoke(r0sInst, false);
                report.put("stuck_logining_cleared", true);
                Thread.sleep(200);
            }
            String userId = (String) cl.loadClass("ulm").getMethod("e").invoke(null);
            Class<?> z7mClass = cl.loadClass("z7m");
            Object z7mInst = z7mClass.getMethod("k").invoke(null);
            // preferPwd（POST /stock/trade/login {"method":"pwd"}）：跳过 token
            // 读取与 seed 重播，直接密码登录拿全新会话（最强重建，用户显式要求）
            Object tokenInfo = preferPwd ? null : z7mClass
                    .getMethod("i", String.class, cl.loadClass("pzr"))
                    .invoke(z7mInst, userId, mgr);
            if (preferPwd) {
                report.put("skip_token", true);
            }
            if (tokenInfo == null && !preferPwd) {
                // AVD 读取路径缺陷兜底：x7m.i(pzr) 的 livetime 重算使 z7m.i 恒
                // null（重启后 token 不可用，文件实际有效）。从 seed 文件
                // （thshook_trade_seed.json 的 token/token_time）自动重播官方
                // 导入链 z7m.o + livetime 修复，再重读一次。
                try {
                    File seedFile = new File(appInstance.getFilesDir(),
                            "thshook_trade_seed.json");
                    if (seedFile.exists()) {
                        JSONObject persist = new JSONObject(readFileToString(seedFile));
                        String tok = persist.optString("token", "");
                        String tokTime = persist.optString("token_time", "");
                        long tokAgeMin = tokTime.isEmpty() ? Long.MAX_VALUE
                                : (System.currentTimeMillis() / 1000
                                        - Long.parseLong(tokTime)) / 60;
                        if (!tok.isEmpty() && tokAgeMin < 1440) {
                            JSONObject userInfo = new JSONObject();
                            userInfo.put("WtToken", tok);
                            userInfo.put("Time", tokTime);
                            z7mClass.getMethod("o",
                                            cl.loadClass("pzr"), org.json.JSONObject.class)
                                    .invoke(z7mInst, mgr, userInfo);
                            java.util.List<?> tis = (java.util.List<?>) z7mClass
                                    .getMethod("m", String.class).invoke(z7mInst, userId);
                            if (tis != null) {
                                for (Object ti : tis) {
                                    try {
                                        java.lang.reflect.Field lt = ti.getClass()
                                                .getField("mLiveTime");
                                        if (((Integer) lt.get(ti)) <= 0) {
                                            lt.setInt(ti, 1440);
                                        }
                                    } catch (Throwable ignore) { }
                                }
                            }
                            z7mClass.getMethod("t", String.class).invoke(z7mInst, userId);
                            tokenInfo = z7mClass
                                    .getMethod("i", String.class, cl.loadClass("pzr"))
                                    .invoke(z7mInst, userId, mgr);
                            report.put("token_replayed_from_seed",
                                    tokenInfo != null);
                        }
                    }
                } catch (Throwable t) {
                    Log.w(TAG, "token seed replay failed: " + t);
                }
            }
            if (tokenInfo == null) {
                // 2026-08-18 密码登录：token 过期/缺失时用交易密码直接登录
                // （设备指纹已伪装成真机，券商侧视为同一设备）。密码来自本次
                // 请求 body 或 filesDir/thshook_pwd.json，成功后 z7m.w hook
                // 自动捕获新 token 并上报+并入 seed。
                String pwd = loadTradePassword();
                if (pwd != null && !pwd.isEmpty()) {
                    boolean pwdOk = doPasswordTradeLoginLocked(cl, report, pwd);
                    report.put("pwd_login", pwdOk);
                    if (pwdOk) {
                        // 密码登录成功=会话已建立。z7m.i 读回仍可能因 x7m.i 的
                        // livetime 重算坑返回 null（AVD 冷启动 ehi.m().n() 未
                        // 就绪）——先用 seed 重播同款 livetime 修复再读；仍 null
                        // 时以 izr.l 登录态为准判成功，不得误报 token unavailable
                        try {
                            java.util.List<?> tis = (java.util.List<?>) z7mClass
                                    .getMethod("m", String.class).invoke(z7mInst, userId);
                            if (tis != null) {
                                for (Object ti : tis) {
                                    try {
                                        java.lang.reflect.Field lt = ti.getClass()
                                                .getField("mLiveTime");
                                        if (((Integer) lt.get(ti)) <= 0) {
                                            lt.setInt(ti, 1440);
                                        }
                                    } catch (Throwable ignore) { }
                                }
                            }
                        } catch (Throwable ignore) { }
                        tokenInfo = z7mClass
                                .getMethod("i", String.class, cl.loadClass("pzr"))
                                .invoke(z7mInst, userId, mgr);
                        if (tokenInfo != null) {
                            schedulePostLoginTokenReport();
                        } else {
                            // z7m.i 仍 null（livetime 坑）：onWeituoLoginSuccess 回调
                            // 本身就是券商确认（2026-08-19 实测：此分支下 funds
                            // 2s 成功，会话已建立，izr.l 置位有异步延迟）——轮询
                            // izr.l 最多 5s 作参考，无论真假都判成功，绝不再误报
                            // token unavailable
                            boolean loggedInState = false;
                            try {
                                for (int i = 0; i < 10 && !loggedInState; i++) {
                                    loggedInState = Boolean.TRUE.equals(
                                            izrInst.getClass()
                                                    .getMethod("l", cl.loadClass("pzr"))
                                                    .invoke(izrInst, mgr));
                                    if (!loggedInState) Thread.sleep(500);
                                }
                            } catch (Throwable ignore) { }
                            report.put("logged_in_state", loggedInState);
                            report.put("result", "success");
                            report.put("via", "pwd_login_callback_confirmed");
                            lastEnsureTradeError = null;
                            schedulePostLoginTokenReport();
                            return true;
                        }
                    }
                }
            }
            if (tokenInfo == null) {
                report.put("result", "fail");
                report.put("error", "token unavailable (expired or not stored); "
                        + "set trade password via POST /stock/trade/pwd to enable password login");
                lastEnsureTradeError = "trade login: token unavailable";
                return false;
            }
            Class<?> q3sClass = cl.loadClass("q3s");
            Object params = q3sClass.getMethod("a").invoke(null);
            params = q3sClass.getMethod("s", int.class).invoke(params, 1);
            params = q3sClass.getMethod("q", int.class).invoke(params, 2);
            params = q3sClass.getMethod("v", int.class).invoke(params, 0);
            params = q3sClass.getMethod("n", int.class).invoke(params, 2);
            params = q3sClass.getMethod("p", boolean.class).invoke(params, false);
            params = q3sClass.getMethod("u", boolean.class).invoke(params, true);
            CountDownLatch latch = new CountDownLatch(1);
            AtomicReference<String> result = new AtomicReference<>("pending");
            AtomicReference<String> failDetail = new AtomicReference<>();
            Object callback = Proxy.newProxyInstance(cl,
                    new Class[]{cl.loadClass("g8m")},
                    (proxy, method, mArgs) -> {
                        String name = method.getName();
                        if ("onWeituoLoginSuccess".equals(name)) {
                            result.set("success");
                            latch.countDown();
                            return null;
                        }
                        if ("onWeituoLoginFail".equals(name)) {
                            result.set("fail");
                            failDetail.set(mArgs[0] == null ? "null stuff"
                                    : describeStuffStruct(mArgs[0]));
                            latch.countDown();
                            return null;
                        }
                        if ("interceptTimeout".equals(name)
                                || "onlyMeHandleReceiveData".equals(name)) {
                            return Boolean.FALSE;
                        }
                        if ("hashCode".equals(name)) return System.identityHashCode(proxy);
                        if ("equals".equals(name)) return proxy == mArgs[0];
                        if ("toString".equals(name)) return "TradeLoginCallback";
                        return null;
                    });
            Class<?> tokenCls = cl.loadClass(
                    "com.hexin.android.weituo.hstrade.feature.login.bindlogin.model.TokenInfo");
            // AVD 冷环境（seed 账户从未成功登录）：lzr.e（native 路径支持标志）
            // 默认 false 且不持久化（只在登录成功回调 n2s.b 里置位），此时
            // k7r.Q→q9r.l 判定走 CBAS socket 会话检查路径——AVD 上 CBAS 地址
            // 未被 PushConnect 设置，请求排队至死（35s 无回调）。主动置位
            // lzr.e=true 等价于登录成功回调的效果，登录请求即路由到
            // jniRequest native 路径（真机同款，warmup 实测 attempt=1 成功）。
            try {
                Class<?> mzrClass = cl.loadClass("mzr");
                Object mzrA = mzrClass.getField("a").get(null);
                Object lzr = mzrClass.getMethod("b", cl.loadClass("pzr"))
                        .invoke(mzrA, mgr);
                lzr.getClass().getMethod("p", boolean.class).invoke(lzr, true);
                report.put("native_path_forced", true);
            } catch (Throwable t) {
                Log.w(TAG, "force lzr.e=true failed: " + t);
            }
            Class<?> f2sClass = cl.loadClass("f2s");
            Object f2sInst = f2sClass.getMethod("d").invoke(null);
            f2sClass.getMethod("q", tokenCls, q3sClass, cl.loadClass("g8m"))
                    .invoke(f2sInst, tokenInfo, params, callback);
            boolean done = latch.await(35, TimeUnit.SECONDS);
            if (!done) {
                // 超时但登录可能已生效（同写端点超时铁律：先查状态再定论）。
                // 2026-08-19 实测：35s 无回调但会话实际建立，izr.l 在超时后
                // 数秒才异步置位（随后 funds 2s 成功）——轮询 5s 再定论
                boolean loggedIn = false;
                try {
                    for (int i = 0; i < 10 && !loggedIn; i++) {
                        loggedIn = Boolean.TRUE.equals(lM.invoke(izrInst, mgr));
                        if (!loggedIn) Thread.sleep(500);
                    }
                } catch (Throwable ignore) { }
                report.put("result", loggedIn ? "success-after-timeout" : "timeout");
                try {
                    report.put("weituo_logining_stuck",
                            Boolean.TRUE.equals(hM.invoke(r0sInst)));
                } catch (Throwable ignore) { }
                if (loggedIn) tradeRuntimeReadyOnce = true;
                else lastEnsureTradeError = "trade login: no response in 35s and not logged in";
                return loggedIn;
            }
            String r = result.get();
            report.put("result", r);
            if (!"success".equals(r)) {
                // fail 回调 ≠ 登录失败：App 内部重登链与本次登录并发竞争时，
                // 竞争中超时一方会以 fail(null)（"null stuff"）回调，而 App 侧
                // 的登录可能已实际成功（实测 fail 后 15s funds 查询直接可用）。
                // 先轮询 izr.a.l 确认真实登录态，置位则按成功收编。
                boolean loggedInAfterFail = false;
                for (int i = 0; i < 10 && !loggedInAfterFail; i++) {
                    loggedInAfterFail = Boolean.TRUE.equals(lM.invoke(izrInst, mgr));
                    if (!loggedInAfterFail) Thread.sleep(500);
                }
                if (loggedInAfterFail) {
                    report.put("result", "success-via-concurrent-login");
                    tradeRuntimeReadyOnce = true;
                    return true;
                }
                String err = failDetail.get() == null ? "onWeituoLoginFail" : failDetail.get();
                report.put("error", err);
                lastEnsureTradeError = "trade login failed: " + err;
                return false;
            }
            // 登录标志（izr.a.l）由 receive 处理链异步置位，短暂确认；
            // 未置位则补一发官方单账户静默重登（x0s$b/c 回调无弹窗）
            boolean loggedIn = false;
            for (int i = 0; i < 10 && !loggedIn; i++) {
                loggedIn = Boolean.TRUE.equals(lM.invoke(izrInst, mgr));
                if (!loggedIn) Thread.sleep(500);
            }
            if (!loggedIn) {
                try {
                    cl.loadClass("x0s")
                            .getMethod("w", cl.loadClass("pzr")).invoke(null, mgr);
                    for (int i = 0; i < 10 && !loggedIn; i++) {
                        Thread.sleep(500);
                        loggedIn = Boolean.TRUE.equals(lM.invoke(izrInst, mgr));
                    }
                    report.put("fallback_relogin_sent", true);
                } catch (Throwable e) {
                    Log.w(TAG, "fallback x0s.w failed: " + e);
                }
            }
            report.put("logged_in_state", loggedIn);
            if (loggedIn) tradeRuntimeReadyOnce = true;
            else lastEnsureTradeError = "trade login: callback success but izr.a.l not set";
            return loggedIn;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            lastEnsureTradeError = "trade login interrupted";
            return false;
        } catch (Throwable e) {
            lastEnsureTradeError = "trade login invoke failed: " + e;
            return false;
        }
    }

    private static String invokeTradeQueryByName(String name) {
        int[] proto = TRADE_QUERY_PROTOCOLS.get(name);
        if (proto == null) {
            JSONObject resp = new JSONObject();
            return errorJson(resp, "unknown query name '" + name
                    + "', supported: " + TRADE_QUERY_PROTOCOLS.keySet());
        }
        String result = invokeTradeQuery(proto[0], proto[1],
                expandStaticParams(TRADE_QUERY_STATIC_PARAMS.get(name)));
        // 超时分级自愈（2026-08-18 重做）：此前"超时→强制登录→sleep 3s→重试"
        // 恒定 ~20s，且收盘后实测登录多为误判（already_logged_in，会话没坏，
        // 只是 CBAS socket 断开重连窗口内请求丢失、无 onChannelBad 事件）。
        // 分级：①等通道就绪（事件驱动 ≤5s）直接重试——App 原生 7~8s 周期
        // 重连完成后重发即成功；②仍超时才强制登录重建服务端会话（真会话
        // 过期场景，90B 踢）+3s 就绪窗 + 重试。只读查询幂等，重试安全。
        if (result != null && result.contains("timeout waiting response")) {
            try {
                waitForCbasReady(5000);
                result = invokeTradeQuery(proto[0], proto[1],
                        expandStaticParams(TRADE_QUERY_STATIC_PARAMS.get(name)));
            } catch (Throwable retryEx) {
                Log.w(TAG, "query fast-retry failed: " + retryEx);
            }
        }
        if (result != null && result.contains("timeout waiting response")) {
            try {
                ClassLoader cl = resolveAppClassLoader(null);
                if (cl != null) {
                    JSONObject loginReport = new JSONObject();
                    doActiveTradeLogin(cl, loginReport, true);
                    // 登录回调确认后会话分发仍需短暂就绪窗口（实测紧随其后的
                    // 重试偶发落空、下一个查询成功——即本重试修的会话被后续
                    // 查询吃到），等 3s 消竞态
                    Thread.sleep(3000);
                }
            } catch (Throwable loginEx) {
                Log.w(TAG, "query-retry force login failed: " + loginEx);
            }
            result = invokeTradeQuery(proto[0], proto[1],
                    expandStaticParams(TRADE_QUERY_STATIC_PARAMS.get(name)));
        }
        return result;
    }

    /** 历史类模板占位符展开：{start}=20250101（账户全历史起点，见交接说明 §3.9 日期窗口教训），
     *  {end}=今天（yyyyMMdd，与 App y6n.e 格式一致）。每次调用动态生成，无需 UI 捕获。 */
    private static String expandStaticParams(String tpl) {
        if (tpl == null || tpl.indexOf("{start}") < 0) return tpl;
        String today = new java.text.SimpleDateFormat("yyyyMMdd").format(new java.util.Date());
        return tpl.replace("{start}", "20250101").replace("{end}", today);
    }

    /**
     * 通用交易查询调用器：name 对应协议见 TRADE_QUERY_PROTOCOLS。
     * pageId/params 取 hook 捕获的最近一次真实值（App 自己发起过该查询才有），
     * fallbackPageId 仅在无捕获时兜底。
     */
    private static String invokeTradeQuery(int protocolId, int fallbackPageId, String fallbackParams) {
        return invokeTradeQuery(protocolId, fallbackPageId, fallbackParams, true);
    }

    /**
     * bindAccount=false 用于写交易（买/卖/撤单）：App 原始写请求链不带
     * D("wt_account")（账户由交易模块会话态绑定），镜像原始链路以免改变路由。
     * explicitParams 非空时直接使用（写执行器已在其中改写 code/price/qty），
     * 跳过捕获缓存查找——否则 1820 模板缓存会覆盖改写后的参数。
     */
    private static String invokeTradeQuery(int protocolId, int fallbackPageId, String fallbackParams,
                                           boolean bindAccount) {
        return invokeTradeQuery(protocolId, fallbackPageId, fallbackParams, bindAccount, false);
    }

    private static String invokeTradeQuery(int protocolId, int fallbackPageId, String fallbackParams,
                                           boolean bindAccount, boolean forceParams) {
        JSONObject resp = new JSONObject();
        try {
            resp.put("query", "proto_" + protocolId);
        } catch (JSONException ignored) { }
        ClassLoader cl = thsAppClassLoader;
        if (cl == null) {
            return errorJson(resp, "ths classloader not ready (wait for delayed hooks)");
        }
        if (!ensureTradeRuntimeReady(cl)) {
            return errorJson(resp, lastEnsureTradeError);
        }
        int[] pageIds = capturedQueryPageIds.get(protocolId);
        String params = forceParams ? fallbackParams : capturedQueryParams.get(protocolId);
        if (params == null) params = fallbackParams;
        if (params == null) {
            return errorJson(resp, "params for protocol " + protocolId
                    + " not captured (open the corresponding page in app once)");
        }
        int pageId = pageIds != null ? pageIds[0] : fallbackPageId;
        try {
            resp.put("pageId", pageId);
        } catch (JSONException ignored) { }

        final CountDownLatch latch = new CountDownLatch(1);
        // 响应可能拆多帧（zjcc 实测 58B+954B 两帧），收集全部 stuff；
        // lastAddMs 支撑首帧后的静默宽限窗，避免只取第一帧丢后续行
        final java.util.List<Object> stuffList = java.util.Collections.synchronizedList(new ArrayList<>());
        final java.util.concurrent.atomic.AtomicLong lastAddMs =
                new java.util.concurrent.atomic.AtomicLong(0);
        Object observer;
        try {
            Class<?> imvClass = cl.loadClass("imv");
            observer = java.lang.reflect.Proxy.newProxyInstance(cl,
                    new Class[]{imvClass},
                    (proxy, method, args) -> {
                        String name = method.getName();
                        if ("receive".equals(name)) {
                            // xdv.receive(StuffBaseStruct)：响应回调，可能在通信线程
                            if (args != null && args.length > 0 && args[0] != null) {
                                stuffList.add(args[0]);
                                lastAddMs.set(System.currentTimeMillis());
                                latch.countDown();
                            }
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
        // 写协议（bindAccount=false 路径=写执行器）登记旁路捕获：响应帧被 App
        // 常驻观察者竞争消费时，ixm/nxm hook 旁路兜底（frameId 匹配 protocolId）
        final boolean isWritePath = !bindAccount;
        final CountDownLatch bypassLatch;
        final java.util.List<Object> bypassStuff;
        if (isWritePath) {
            // lzr.e（native 路径标志）不持久化且会被 App 内部重置（实测 BUY 后
            // 20s 的 CANCEL 时 k7r.Q 已从 true 翻回 false）→ 请求走 socket
            // session-check 死路被 onChannelBad(code=1) 静默丢弃（无响应无失败
            // 回调）。写路径每次发送前强制置位，等价登录成功回调的效果。
            try {
                Class<?> mzrClass = cl.loadClass("mzr");
                Object mzrA = mzrClass.getField("a").get(null);
                Object lzr = mzrClass.getMethod("b", cl.loadClass("pzr"))
                        .invoke(mzrA, tradeAccountManagerInstance);
                lzr.getClass().getMethod("p", boolean.class).invoke(lzr, true);
                Log.w(TAG, "WritePath forced lzr.e=true (native jniRequest)");
            } catch (Throwable t) {
                Log.w(TAG, "WritePath force lzr.e failed: " + t);
            }
            bypassLatch = new CountDownLatch(1);
            bypassStuff = java.util.Collections.synchronizedList(new ArrayList<>());
            pendingWriteLatch.put(protocolId, bypassLatch);
            pendingWriteStuff.put(protocolId, bypassStuff);
        } else {
            bypassLatch = null;
            bypassStuff = null;
        }
        boolean arrived = false;
        try {
            // 发送前通道就绪检查（2026-08-18 事件驱动重试）：App 正在重连 CBAS
            // （7~8s 周期检查触发）时请求会发到死连接上石沉大海——等重连完成
            // 再发（事件触发，Socket.connect success 的 notifyAll 唤醒，上限 6s）
            if (waitForCbasReadyIfReconnecting(6000)) {
                try { resp.put("waited_cbas_reconnect", true); } catch (JSONException ignore) { }
            }
            Class<?> uqvClass = cl.loadClass("uqv");
            Object builder = uqvClass.getMethod("e", boolean.class).invoke(null, Boolean.TRUE);
            Class<?> imvClass = cl.loadClass("imv");
            java.lang.reflect.Method hMethod = builder.getClass()
                    .getMethod("H", int.class, int.class, imvClass, String.class);
            final String sendParams = params;
            java.util.concurrent.Callable<Object> sendOnce = () -> {
                Object rpv = hMethod.invoke(builder, pageId, protocolId, observer, sendParams);
                if (bindAccount) {
                    java.lang.reflect.Method dMethod = rpv.getClass()
                            .getMethod("D", String.class, Object.class);
                    dMethod.invoke(rpv, "wt_account", tradeAccountManagerInstance);
                }
                rpv.getClass().getMethod("request").invoke(rpv);
                return rpv;
            };
            sendOnce.call();
            // 事件驱动等待（2026-08-18）：不再对 15s 傻等——本请求在途期间
            // onChannelBad（请求被丢）触发后：等 CBAS 重连完成（事件）→ 立即
            // 重发（observer 仍注册，响应照常回调）。最多重发 2 次；全部无
            // 响应才走外层强制登录自愈（真会话问题）。写路径保持原 15s 直等
            // （fire-and-forget 的撤单不依赖此等待；BUY 响应本来就快）。
            long attemptStart = System.currentTimeMillis();
            int resend = 0;
            boolean done = false;
            while (!done) {
                if (isWritePath) {
                    arrived = latch.await(15, TimeUnit.SECONDS);
                    done = true;
                    break;
                }
                long waited = 0;
                while (waited < 15000 && latch.getCount() > 0) {
                    long badAt = lastCbasBadMs;
                    long readyAt = lastCbasReadyMs;
                    if (resend < 2) {
                        // 信号一（onChannelBad 事件）：在途请求被通道显式丢弃
                        if (badAt > attemptStart && badAt >= readyAt) {
                            Log.w(TAG, "QueryDropped protocol=" + protocolId
                                    + " resend#" + (resend + 1) + " after channelBad");
                            waitForCbasReady(6000);
                            if (System.currentTimeMillis() - lastCbasBadMs > 500
                                    && lastCbasReadyMs >= badAt) {
                                attemptStart = System.currentTimeMillis();
                                sendOnce.call();
                                resend++;
                                try { resp.put("event_resends", resend); } catch (JSONException ignore) { }
                                waited = 0;
                                continue;
                            }
                        }
                        // 信号二（通道重建推断）：本请求发出 2s 后通道发生过重建
                        // （发出时撞上断开窗口，请求发到死连接石沉大海且无丢帧
                        // 回调）——原响应永不到达，通道已就绪，立即重发
                        if (readyAt > attemptStart + 2000) {
                            Log.w(TAG, "QueryStaleSend protocol=" + protocolId
                                    + " resend#" + (resend + 1)
                                    + " after cbas rebuilt (no drop event)");
                            attemptStart = System.currentTimeMillis();
                            sendOnce.call();
                            resend++;
                            try { resp.put("event_resends", resend); } catch (JSONException ignore) { }
                            waited = 0;
                            continue;
                        }
                    }
                    // 无事件 → 短轮询等待（cbasSignal 的通知会提前唤醒检查）
                    long slice = Math.min(1000, 15000 - waited);
                    synchronized (cbasSignal) {
                        cbasSignal.wait(slice);
                    }
                    waited += slice;
                    if (latch.getCount() == 0) break;
                }
                done = true;
            }
            arrived = latch.getCount() == 0;
        } catch (Throwable e) {
            if (isWritePath) { pendingWriteLatch.remove(protocolId); pendingWriteStuff.remove(protocolId); }
            unregisterObserverQuietly(cl, observer);
            return errorJson(resp, "query invoke failed: " + describeThrowableChain(e));
        }
        // 自注册 observer 超时 → 旁路等 2s；旁路有帧=App 观察者抢走了响应，
        // 用旁路结果继续（写响应丢失问题的修复）
        if (!arrived && isWritePath) {
            try { arrived = bypassLatch.await(2, TimeUnit.SECONDS); } catch (InterruptedException ignore) { }
            if (arrived && !bypassStuff.isEmpty()) {
                stuffList.addAll(bypassStuff);
                lastAddMs.set(System.currentTimeMillis());
                try { resp.put("via_bypass", true); } catch (JSONException ignore) { }
                Log.w(TAG, "WriteBypass rescued protocol=" + protocolId
                        + " frames=" + bypassStuff.size());
            }
        }
        if (isWritePath) { pendingWriteLatch.remove(protocolId); pendingWriteStuff.remove(protocolId); }
        if (arrived) {
            // 会话新鲜度信号：成功收到响应=会话可用，写前据此跳过强制登录
            lastSuccessfulQueryMs = System.currentTimeMillis();
            if (isWritePath) lastSuccessfulWriteMs = lastSuccessfulQueryMs;
            // 静默宽限窗：末帧后 1.5s 内无新帧即认为响应结束（总上限仍受 request 发起后 15s 约束）。
            // 文本响应（StuffTextStruct）是终态单帧，直接跳过宽限窗
            boolean firstIsText = stuffList.get(0).getClass().getName()
                    .endsWith("StuffTextStruct");
            if (!firstIsText) {
                long hardDeadline = startMs + 15000;
                while (System.currentTimeMillis() - lastAddMs.get() < 1500
                        && System.currentTimeMillis() < hardDeadline) {
                    try { Thread.sleep(200); } catch (InterruptedException ie) {
                        Thread.currentThread().interrupt();
                        break;
                    }
                }
            }
        }
        unregisterObserverQuietly(cl, observer);
        long elapsed = System.currentTimeMillis() - startMs;
        try {
            resp.put("elapsed_ms", elapsed);
        } catch (JSONException ignored) { }
        if (stuffList.isEmpty()) {
            return errorJson(resp, "timeout waiting response (15s)");
        }
        try {
            JSONObject parsed = stuffTableToJson(stuffList, protocolId);
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
    // dataTable 键序 -> 语义列名（按真机数据人工校准）。
    // App 侧 dzh.Y1 用各观察者私有的 iArr 重排列序，stuff 本身不携带映射；
    // 协议升级或列变化后需重新校准。无映射的协议输出 key_columns=null。
    private static final java.util.Map<Integer, String[]> TRADE_TABLE_KEY_COLUMNS;
    static {
        java.util.Map<Integer, String[]> k = new java.util.LinkedHashMap<>();
        k.put(1825, new String[]{"代码", "名称", "状态", "操作", "委托数量", "价格",
                "成交数量", "成交均价", "交易市场", "合同编号", "日期", "时间"});
        // 2026-08-17 开盘实测（买 0.596 成交/卖 0.594 成交/挂单撤单闭环）校准：
        k.put(1891, new String[]{"代码", "名称", "交易市场", "刷新", "股东账号", "数量",
                "冻结", "可卖", "成本", "现价", "市值", "盈亏", "实际数量", "盈亏率"});
        k.put(1811, new String[]{"代码", "名称", "状态", "股东账号", "操作", "委托数量",
                "价格", "成交数量", "成交均价", "交易市场", "合同编号", "委托日期",
                "委托时间", "备注", "委托状态码"});
        k.put(1810, new String[]{"代码", "名称", "unknown_2", "股东账号", "操作",
                "unknown_5", "成交数量", "成交价", "交易市场", "成交额", "unknown_10",
                "成交日期", "成交时间", "合同编号", "成交编号", "unknown_15"});
        TRADE_TABLE_KEY_COLUMNS = java.util.Collections.unmodifiableMap(k);
    }

    // 按字段 ID 取值的协议（dataTable 键=字段 ID 而非列号）：字段 ID → 语义名。
    // 1807 资金协议源码 gqi.receiveData：getData(fieldId) 取 firstOrNull 即金额。
    // 语义名与 App 显示对应：36628 总资产 / 36629 浮动盈亏 / 36625 可用资金 /
    // 36626 总市值 / 36623 可取资金；36631/36632/36633 仅港美股资金卡使用（A股为空）。
    // 2026-08-17 持仓实测补充：36622 持仓期间恒为 3139.80（=昨日收盘总资产）而
    // 36628 变为 3138.90 → 36622=期初总资产；36630 与 36625 同值（空仓 3139.80/
    // 持仓 3079.50）→ 36630=可用资金镜像。36624/36627 持仓期间仍为 0.00，语义未知。
    private static final java.util.Map<Integer, java.util.Map<Integer, String>> TRADE_FIELD_ID_PROTOCOLS;
    static {
        java.util.Map<Integer, String> f1807 = new java.util.LinkedHashMap<>();
        f1807.put(36628, "total_assets");
        f1807.put(36629, "float_profit");
        f1807.put(36625, "available_amount");
        f1807.put(36626, "total_market_value");
        f1807.put(36623, "withdrawable_amount");
        f1807.put(36622, "open_total_assets");
        f1807.put(36630, "available_amount_alt");
        f1807.put(36624, "field_36624");
        f1807.put(36627, "field_36627");
        f1807.put(36631, "field_36631");
        f1807.put(36632, "field_36632");
        f1807.put(36633, "field_36633");
        java.util.Map<Integer, java.util.Map<Integer, String>> fp = new java.util.LinkedHashMap<>();
        fp.put(1807, java.util.Collections.unmodifiableMap(f1807));
        TRADE_FIELD_ID_PROTOCOLS = java.util.Collections.unmodifiableMap(fp);
    }

    /** 多帧 stuff 合并解析：全部 StuffTableStruct 的 dataTable 按列拼接；
     *  文本帧保留首个；1807 等字段 ID 协议额外输出 fields 单记录。 */
    private static JSONObject stuffTableToJson(java.util.List<Object> stuffs, int protocolId) throws Throwable {
        JSONObject out = new JSONObject();
        Object first = stuffs.get(0);
        Class<?> c = first.getClass();
        out.put("struct", c.getSimpleName());
        out.put("frames", stuffs.size());
        try {
            out.put("pageId", c.getField("pageId").getInt(first));
            out.put("frameId", c.getField("frameId").getInt(first));
            out.put("real", c.getField("isRealData").getBoolean(first));
        } catch (Throwable ignored) { }
        boolean anyTable = false;
        for (Object s : stuffs) {
            if ("com.hexin.middleware.data.mobile.StuffTableStruct".equals(s.getClass().getName())) {
                anyTable = true;
                break;
            }
        }
        if (anyTable) {
            // 合并所有表格帧：tableHead/caption 取首个非空；dataTable 按列键拼接（多帧行延续）
            String[] head = null;
            Object caption = null;
            java.util.Map<Integer, java.util.List<String>> merged = new java.util.TreeMap<>();
            for (Object s : stuffs) {
                if (!"com.hexin.middleware.data.mobile.StuffTableStruct".equals(s.getClass().getName())) {
                    continue;
                }
                Class<?> sc = s.getClass();
                if (head == null) {
                    String[] h = (String[]) sc.getField("tableHead").get(s);
                    if (h != null && h.length > 0) head = h;
                }
                if (caption == null) {
                    Object cap = sc.getField("caption").get(s);
                    if (cap != null) caption = cap;
                }
                java.util.Hashtable<?, ?> dataTable =
                        (java.util.Hashtable<?, ?>) sc.getField("dataTable").get(s);
                if (dataTable == null) continue;
                for (java.util.Map.Entry<?, ?> e : dataTable.entrySet()) {
                    Integer key = (Integer) e.getKey();
                    String[] vals = (String[]) e.getValue();
                    java.util.List<String> list = merged.get(key);
                    if (list == null) {
                        list = new ArrayList<>();
                        merged.put(key, list);
                    }
                    if (vals != null) for (String v : vals) list.add(v);
                }
            }
            out.put("caption", caption == null ? "" : caption);
            out.put("col", merged.size());
            java.util.List<String> headList = new ArrayList<>();
            if (head != null) for (String h : head) headList.add(h);
            out.put("columns", new JSONArray(headList));
            // dataTable 为列主序：外层 key=列号/字段 ID，String[] 为该列全部行的值。
            // 转置为行主序 rows[i][j]=dataTable[j][i]，与 columns 对齐。
            JSONArray rows = new JSONArray();
            int rowCount = 0;
            for (java.util.List<String> colVals : merged.values()) {
                if (colVals.size() > rowCount) rowCount = colVals.size();
            }
            out.put("row", rowCount);
            for (int r = 0; r < rowCount; r++) {
                JSONArray rowArr = new JSONArray();
                for (java.util.List<String> colVals : merged.values()) {
                    rowArr.put(r < colVals.size() ? colVals.get(r) : "");
                }
                rows.put(rowArr);
            }
            out.put("rows", rows);
            String[] keyCols = TRADE_TABLE_KEY_COLUMNS.get(protocolId);
            out.put("key_columns", keyCols == null ? JSONObject.NULL : new JSONArray(java.util.Arrays.asList(keyCols)));
            if (keyCols != null) {
                JSONArray records = new JSONArray();
                for (int r = 0; r < rows.length(); r++) {
                    JSONArray rowArr = rows.getJSONArray(r);
                    JSONObject rec = new JSONObject();
                    for (int j = 0; j < keyCols.length && j < rowArr.length(); j++) {
                        rec.put(keyCols[j], rowArr.optString(j, ""));
                    }
                    records.put(rec);
                }
                out.put("records", records);
            }
            // 字段 ID 协议：键=字段 ID，值数组 firstOrNull 即该字段值（单记录）
            java.util.Map<Integer, String> fieldNames = TRADE_FIELD_ID_PROTOCOLS.get(protocolId);
            if (fieldNames != null) {
                JSONObject fields = new JSONObject();
                for (java.util.Map.Entry<Integer, java.util.List<String>> e : merged.entrySet()) {
                    String name = fieldNames.getOrDefault(e.getKey(), "field_" + e.getKey());
                    java.util.List<String> vals = e.getValue();
                    fields.put(name, vals == null || vals.isEmpty() ? "" : vals.get(0));
                }
                out.put("fields", fields);
            }
        } else if ("com.hexin.middleware.data.mobile.StuffTextStruct".equals(c.getName())) {
            out.put("reCode", c.getField("reCode").getInt(first));
            out.put("textId", c.getField("id").getInt(first));
            Object caption = c.getField("caption").get(first);
            Object content = c.getField("content").get(first);
            out.put("caption", caption == null ? "" : caption);
            out.put("content", content == null ? "" : content);
        } else if ("com.hexin.middleware.data.mobile.StuffResourceStruct".equals(c.getName())) {
            // 写响应（撤单等）业务结果在 buffer：GBK 编码 JSON，retcode=="0" 成功、
            // retmsg 为失败原因（源码 b8p.s 解析逻辑）
            JSONObject generic = new JSONObject();
            for (java.lang.reflect.Field f : c.getFields()) {
                try {
                    Object v = f.get(first);
                    if (v instanceof byte[] && ((byte[]) v).length > 0
                            && (f.getName().equals("buffer") || f.getName().equals("cacheBuffer"))) {
                        byte[] bv = (byte[]) v;
                        generic.put(f.getName(), "bytes(len=" + bv.length + ")");
                        try {
                            String dec = new String(bv, "GBK").trim();
                            generic.put(f.getName() + "_gbk", dec);
                            try {
                                org.json.JSONObject j = new org.json.JSONObject(dec);
                                if (j.has("retcode")) out.put("retcode", j.optString("retcode"));
                                if (j.has("retmsg")) out.put("retmsg", j.optString("retmsg"));
                                out.put("business_ok", "0".equals(j.optString("retcode")));
                            } catch (Throwable ignored) { }
                        } catch (Throwable ignored) { }
                        continue;
                    }
                    String vs;
                    if (v == null) vs = "";
                    else if (v instanceof byte[]) vs = "bytes(len=" + ((byte[]) v).length + ")";
                    else {
                        vs = String.valueOf(v);
                        if (vs.length() > 800) vs = vs.substring(0, 800) + "...(truncated)";
                    }
                    generic.put(f.getName(), vs);
                } catch (Throwable ignored) { }
            }
            out.put("fields_dump", generic);
        } else {
            // 其余写响应载体（StuffInteractStruct/StuffCtrlStruct 等）：
            // 通用反射导出 public 字段。byte[] 显示长度，String 截断 800 字符
            JSONObject generic = new JSONObject();
            for (java.lang.reflect.Field f : c.getFields()) {
                try {
                    Object v = f.get(first);
                    String vs;
                    if (v == null) vs = "";
                    else if (v instanceof byte[]) vs = "bytes(len=" + ((byte[]) v).length + ")";
                    else {
                        vs = String.valueOf(v);
                        if (vs.length() > 800) vs = vs.substring(0, 800) + "...(truncated)";
                    }
                    generic.put(f.getName(), vs);
                } catch (Throwable ignored) { }
            }
            out.put("fields_dump", generic);
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
     * 导出写协议捕获（买/卖/撤单/转账完整 params）与查询协议缓存 params。
     * params 内换行转义为 \r\n 字面量，便于直接阅读与重放构造。
     */
    private static String getTradeWriteCaptures() throws org.json.JSONException {
        org.json.JSONObject root = new org.json.JSONObject();
        org.json.JSONObject writes = new org.json.JSONObject();
        for (java.util.Map.Entry<String, String> e : capturedWriteRequests.entrySet()) {
            String v = e.getValue() == null ? "null"
                    : e.getValue().replace("\r", "\\r").replace("\n", "\\n");
            writes.put(e.getKey(), v);
        }
        root.put("write_captures", writes);
        org.json.JSONObject queries = new org.json.JSONObject();
        for (java.util.Map.Entry<Integer, String> e : capturedQueryParams.entrySet()) {
            int[] pageId = capturedQueryPageIds.get(e.getKey());
            org.json.JSONObject q = new org.json.JSONObject();
            q.put("pageId", pageId == null ? -1 : pageId[0]);
            String v = e.getValue() == null ? "null"
                    : e.getValue().replace("\r", "\\r").replace("\n", "\\n");
            q.put("params", v);
            queries.put(String.valueOf(e.getKey()), q);
        }
        root.put("query_captures", queries);
        return root.toString();
    }

    // ============ 写交易执行器（买/卖/撤单/转账） ============

    /** 委托方向 → {protocolId, pageId, 数量 ctrl 键}。协议号来自真机捕获
     *  （App 实发 1820/1821，与反编译源码的 1804/1805 不同——以捕获为准） */
    private static final java.util.Map<String, int[]> TRADE_ORDER_SPECS;
    static {
        java.util.Map<String, int[]> os = new java.util.LinkedHashMap<>();
        os.put("buy",  new int[]{1820, 2682, 36615});
        os.put("sell", new int[]{1821, 2604, 36621});
        TRADE_ORDER_SPECS = java.util.Collections.unmodifiableMap(os);
    }

    /** 买卖委托静态模板兜底（2026-08-16 真机捕获）。source 签名经 DES 确定性验证
     *  跨进程重启一致，且签名只覆盖页面标签串、不含订单参数——改 code/price/qty 后仍有效。
     *  数量键：buy=36615 / sell=36621；代码=2102；价格=2127。 */
    private static final java.util.Map<Integer, String> TRADE_ORDER_STATIC_TEMPLATES;
    static {
        java.util.Map<Integer, String> ts = new java.util.HashMap<>();
        ts.put(1820, "reqctrl=2001\nctrlid_0=36615\nctrlvalue_0=100\nctrlid_1=2102\nctrlvalue_1=159740"
                + "\nctrlid_2=2127\nctrlvalue_2=0.588\nctrlid_3=36641\nctrlvalue_3=1"
                + "\nctrlid_4=36670\nctrlvalue_4=24\nctrlid_5=36669\nctrlvalue_5=1\nctrlcount=6"
                + "\r\nsource=QFYIth3VsFsID1yEp9kVEgmmL3RO5g02nt7wZi7zPj2Y/fnztbRWFlZORElQS6dI");
        ts.put(1821, "reqctrl=2002\nctrlid_0=36621\nctrlvalue_0=100\nctrlid_1=2102\nctrlvalue_1=159740"
                + "\nctrlid_2=2127\nctrlvalue_2=0.587\nctrlid_3=36670\nctrlvalue_3=24"
                + "\nctrlid_4=36669\nctrlvalue_4=1\nctrlcount=5"
                + "\r\nsource=QFYIth3VsFtvnh6uA6/cclsubXPvwC7jwqEqNiO3NzMt/I1K4Sj1uJCw7WvfQ9OD");
        TRADE_ORDER_STATIC_TEMPLATES = java.util.Collections.unmodifiableMap(ts);
    }

    /**
     * eb6 ctrl 参数串中替换 ctrlid_N=key 对应的 ctrlvalue_N 值。
     * 换行符原样保留（App 的 params 混用 \n 与 \r\n）。
     */
    private static String replaceCtrlValue(String params, String key, String newValue) {
        if (params == null || key == null || newValue == null) return params;
        String[] lines = params.split("\n", -1);
        String idx = null;
        for (String line : lines) {
            int eq = line.indexOf('=');
            if (eq <= 0 || !line.startsWith("ctrlid_")) continue;
            if (key.equals(line.substring(eq + 1).trim())) {
                idx = line.substring("ctrlid_".length(), eq);
                break;
            }
        }
        if (idx == null) return params;
        StringBuilder sb = new StringBuilder();
        boolean first = true;
        for (String line : lines) {
            if (!first) sb.append('\n');
            first = false;
            String trimmed = line.endsWith("\r") ? line.substring(0, line.length() - 1) : line;
            if (trimmed.startsWith("ctrlvalue_" + idx + "=")) {
                sb.append("ctrlvalue_").append(idx).append('=').append(newValue);
            } else {
                sb.append(line);
            }
        }
        return sb.toString();
    }

    /**
     * POST /stock/trade/order — 买卖委托执行器（真实下单！）。
     * body: {"action":"buy|sell","code":"159740","price":"0.588","qty":"100","confirm":true}
     * 模板取自真机捕获的 1820/1821 请求（含 source 签名，签名只覆盖页面标签串、
     * 不含订单参数，改 code/price/qty 后签名仍有效）。confirm!=true 一律拒绝执行。
     */
    private static String handleTradeOrder(String body) throws org.json.JSONException {
        JSONObject resp = new JSONObject();
        resp.put("endpoint", "order");
        String action = extractJsonString(body, "action");
        String code = extractJsonString(body, "code");
        String price = extractJsonString(body, "price");
        String qty = extractJsonString(body, "qty");
        String confirm = extractJsonString(body, "confirm");
        if (action == null || code == null || price == null || qty == null) {
            return errorJson(resp, "missing action/code/price/qty");
        }
        if (!"true".equals(confirm)) {
            return errorJson(resp, "confirm!=true: write endpoints require explicit confirmation");
        }
        int[] spec = TRADE_ORDER_SPECS.get(action.trim().toLowerCase());
        if (spec == null) {
            return errorJson(resp, "unknown action '" + action + "', supported: " + TRADE_ORDER_SPECS.keySet());
        }
        int protoId = spec[0], pageId = spec[1], qtyKey = spec[2];
        String template = capturedQueryParams.get(protoId);
        if (template == null) template = TRADE_ORDER_STATIC_TEMPLATES.get(protoId);
        if (template == null) {
            return errorJson(resp, "order template (protocol " + protoId + ") unavailable");
        }
        String params = replaceCtrlValue(replaceCtrlValue(replaceCtrlValue(
                template, "2102", code.trim()), "2127", price.trim()), String.valueOf(qtyKey), qty.trim());
        resp.put("protocolId", protoId);
        resp.put("pageId", pageId);
        resp.put("action", action);
        return executeWriteWithConfirm(protoId, pageId, params,
                "buy".equals(action.trim().toLowerCase()), code.trim(), price.trim());
    }

    /**
     * 有状态确认的写执行器（2026-08-18 写不稳定根因修复）：
     * 写落在会话死窗口时既无响应也无执行（实测 3 单 1 丢失），而写不能盲目
     * 重发。解法：超时后查当日委托定论——
     *   buy：新委托已出现 = 已执行（不重发）；未出现 = 确认未执行 = 安全重发一次
     *   cancel：委托状态已变（已撤*）= 已执行；状态未变 = 安全重发
     * 两轮后仍无定论则返回未确认状态+最新委托列表（客户端/人工定论）。
     */
    private static String executeWriteWithConfirm(int protoId, int pageId, String params,
                                                  boolean isBuy, String code, String price) {
        return executeWriteWithConfirm(protoId, pageId, params, isBuy, code, price, null);
    }

    private static String executeWriteWithConfirm(int protoId, int pageId, String params,
                                                  boolean isBuy, String code, String price,
                                                  String entrustNo) {
        long t0 = System.currentTimeMillis();
        JSONObject out = new JSONObject();
        try {
            out.put("query", "proto_" + protoId);
            out.put("pageId", pageId);
            out.put("write", true);
        } catch (JSONException ignored) { }

        // 写响应的确定性确认依赖 ixm/nxm 旁路 hook。启动窗口内不允许降级发送：
        // 否则请求可能已经执行但响应被 App 常驻观察者抢走，调用方无法定论。
        if (!tradingSdkBridgeHooked.get()) {
            return errorJson(out, "trade hook not ready; retry after /health trade_hook_ready=true");
        }
        // 设备端是最终安全边界：即使调用方绕过 THSTradeClient 直连 49500，
        // 也必须具备账户、会话、旁路 Hook 和 5 分钟内真实只读探针。
        String readinessError = requireTradeWriteReady(out);
        if (readinessError != null) {
            return errorJson(out, readinessError);
        }

        // BUY 超时后的安全重发必须与“发送前”的委托集合比较。只按 code+price
        // 搜索会命中当天旧委托（回归固定价格时必现），错误地把本次暗丢判成已执行。
        // 基线查询失败时 fail closed，不执行真实写操作。
        final java.util.Set<String> buyBaselineEntrustNos = new java.util.HashSet<>();
        if (isBuy) {
            JSONObject baseline = queryTodayOrdersBestEffort();
            org.json.JSONArray baselineRecords = tradeOrderRecords(baseline);
            if (baselineRecords == null) {
                return errorJson(out, "pre-write today_order baseline unavailable; write not sent");
            }
            collectEntrustNos(baselineRecords, buyBaselineEntrustNos);
            try { out.put("baseline_order_count", buyBaselineEntrustNos.size()); }
            catch (JSONException ignored) { }
        }

        // 写前强制刷新登录（2026-08-18 根因修复）：券商对写操作要求新鲜会话，
        // 老化会话上的写会触发服务端安全重置（实测：查询恒稳、写间歇引发
        // 会话失效后全超时再自愈；真机写测试均在刚登录后成功）。force=true
        // 重发 f2s.q 拿新鲜会话后再写（实测登录 4~18s）。
        // 会话新鲜度优化：60s 内有成功查询（lastSuccessfulQueryMs）则跳过——
        // 写时长从 ~35s 压到 ~0.2s；跳过判断错误时由重试轮的强制登录兜底。
        // 写后恢复期（2026-08-18 连续写实测）：写成功后服务端会话即失效，
        // 紧随的写被 onChannelBad(code=1) 静默丢弃——恢复期"冷却等待"实测
        // 无效（App 自动重登在无 UI 态恢复不了会话）；有效路径是 round1 快
        // 试 → 超时确认 → round2 前强制登录重发（~60-90s 全链），依赖
        // 客户端超时 ≥200s 与设备端 SoTimeout 300s 兜住。
        try {
            long sinceOk = System.currentTimeMillis() - lastSuccessfulQueryMs;
            if (lastSuccessfulQueryMs > 0 && sinceOk < 60000) {
                out.put("pre_write_login", "skipped-fresh(" + (sinceOk / 1000) + "s)");
            } else {
                ClassLoader cl = resolveAppClassLoader(null);
                if (cl != null) {
                    JSONObject lr = new JSONObject();
                    boolean fresh = doActiveTradeLogin(cl, lr, true);
                    out.put("pre_write_login", lr.opt("result"));
                    if (!fresh) {
                        return errorJson(out, "pre-write login failed: " + lr.opt("error"));
                    }
                    Thread.sleep(1500);
                }
            }
        } catch (Throwable loginEx) {
            Log.w(TAG, "pre-write login failed: " + loginEx);
        }

        for (int round = 1; round <= 2; round++) {
            String result = invokeTradeQuery(protoId, pageId, params, false, true);
            boolean timeout = result != null && result.contains("timeout waiting response");
            if (!timeout) {
                // 有响应只证明通信完成，不等于券商受理。StuffTextStruct 常用
                // “[错误码][原因]”同步返回业务拒绝（如收盘后禁止交易）；旧逻辑
                // 一律写 executed=true，会把拒单伪装成成功。
                try {
                    JSONObject r = new JSONObject(result);
                    JSONObject data = r.optJSONObject("data");
                    String rejection = writeBusinessRejection(data);
                    if (rejection != null) {
                        r.put("transport_ok", true);
                        r.put("business_ok", false);
                        r.put("executed", false);
                        r.put("ok", false);
                        r.put("error", rejection);
                        r.put("confirmed_via", round == 1
                                ? "response-rejected" : "retry-response-rejected");
                        return r.toString();
                    }
                    r.put("transport_ok", true);
                    r.put("business_ok", true);
                    r.put("executed", true);
                    r.put("confirmed_via", round == 1 ? "response" : "retry-response");
                    return r.toString();
                } catch (Throwable e) { return result; }
            }
            // 超时 → 查当日委托定论
            try { Thread.sleep(2500); } catch (InterruptedException ie) {
                Thread.currentThread().interrupt(); }
            JSONObject orders = queryTodayOrdersBestEffort();
            Object recs = orders != null
                    ? orders.optJSONObject("data") != null
                        ? orders.optJSONObject("data").opt("records") : null : null;
            if (recs == null && orders != null && orders.optJSONObject("data") != null) {
                recs = orders.optJSONObject("data").opt("rows");
            }
            boolean queryOk = orders != null && Boolean.TRUE.equals(orders.opt("ok"));
            if (queryOk && recs instanceof org.json.JSONArray) {
                org.json.JSONArray arr = (org.json.JSONArray) recs;
                boolean executedViaState;
                if (isBuy) {
                    executedViaState = orderListContainsNewEntrust(
                            arr, code, price, buyBaselineEntrustNos);
                } else {
                    // cancel：目标委托状态含"已撤"即已执行
                    executedViaState = entrustNo != null
                            && orderStatusCancelled(arr, entrustNo);
                }
                if (executedViaState) {
                    return writeConfirmed(out, true, "order-state", arr, round, t0);
                }
                // BUY 也做延迟二次确认。首次查询未见新合同号并不能证明请求未到
                // 券商；给状态落库留出窗口，避免原请求迟到后又安全重发成重复单。
                if (isBuy && round < 2) {
                    try { Thread.sleep(4000); } catch (InterruptedException ie) {
                        Thread.currentThread().interrupt(); }
                    JSONObject orders2 = queryTodayOrdersBestEffort();
                    org.json.JSONArray recs2 = tradeOrderRecords(orders2);
                    if (recs2 != null && orderListContainsNewEntrust(
                            recs2, code, price, buyBaselineEntrustNos)) {
                        return writeConfirmed(out, true, "order-state-2nd",
                                recs2, round, t0);
                    }
                }
                // 撤单在途二次确认（2026-08-18 user17 实测）：撤单请求被券商执行
                // 但响应帧丢失（旁路亦未捕获）时，首查（超时后 2.5s）常仍"已报"，
                // 判未执行进入重发轮 + 强制登录（最坏 40s）→ 总时长超客户端上限。
                // 撤单落库通常在数秒内：等 4s 重查一次即"已撤"，典型时长从
                // ~60-120s 压到 ~26s；仍未见"已撤"才进 round 2 重发。
                if (!isBuy && entrustNo != null && round < 2) {
                    try { Thread.sleep(4000); } catch (InterruptedException ie) {
                        Thread.currentThread().interrupt(); }
                    JSONObject orders2 = queryTodayOrdersBestEffort();
                    Object recs2 = orders2 != null && orders2.optJSONObject("data") != null
                            ? orders2.optJSONObject("data").opt("records") : null;
                    if (recs2 == null && orders2 != null
                            && orders2.optJSONObject("data") != null) {
                        recs2 = orders2.optJSONObject("data").opt("rows");
                    }
                    if (recs2 instanceof org.json.JSONArray
                            && orderStatusCancelled((org.json.JSONArray) recs2, entrustNo)) {
                        return writeConfirmed(out, true, "order-state-2nd",
                                recs2, round, t0);
                    }
                }
                if (round == 2) {
                    return writeUnconfirmed(out, arr, orders, t0);
                }
                // 未执行确认 → 会话恢复后重发（第 2 轮）
                try {
                    ClassLoader cl = resolveAppClassLoader(null);
                    if (cl != null) doActiveTradeLogin(cl, new JSONObject(), true);
                    Thread.sleep(2000);
                } catch (Throwable ignore) { }
            } else {
                // 委托查询也失败（会话死透）→ 强制登录后重试
                try {
                    ClassLoader cl = resolveAppClassLoader(null);
                    if (cl != null) doActiveTradeLogin(cl, new JSONObject(), true);
                    Thread.sleep(2000);
                } catch (Throwable ignore) { }
                if (round == 2) {
                    return writeUnconfirmed(out, null, orders, t0);
                }
            }
        }
        return errorJson(out, "write rounds exhausted");
    }

    /** 返回券商同步拒绝原因；null 表示响应中没有发现拒绝信号。 */
    private static String writeBusinessRejection(JSONObject data) {
        if (data == null) return null;
        if (data.has("business_ok") && !data.optBoolean("business_ok", false)) {
            String message = data.optString("retmsg", "").trim();
            return message.isEmpty() ? "broker rejected write request" : message;
        }
        if (!"StuffTextStruct".equals(data.optString("struct", ""))) return null;
        String content = data.optString("content", "").replace("\u0000", "").trim();
        if (content.isEmpty()) return null;
        if (content.matches(".*\\[[0-9]{5,}\\].*")
                || content.contains("错误") || content.contains("失败")
                || content.contains("不允许") || content.contains("禁止")
                || content.contains("不足") || content.contains("不存在")) {
            return content;
        }
        return null;
    }

    private static JSONObject queryTodayOrdersBestEffort() {
        try {
            String r = invokeTradeQueryByName("today_order");
            return r == null ? null : new JSONObject(r);
        } catch (Throwable t) {
            return null;
        }
    }

    private static org.json.JSONArray tradeOrderRecords(JSONObject orders) {
        if (orders == null || !Boolean.TRUE.equals(orders.opt("ok"))) return null;
        JSONObject data = orders.optJSONObject("data");
        if (data == null) return null;
        Object records = data.opt("records");
        if (!(records instanceof org.json.JSONArray)) records = data.opt("rows");
        return records instanceof org.json.JSONArray ? (org.json.JSONArray) records : null;
    }

    private static void collectEntrustNos(org.json.JSONArray arr,
                                          java.util.Set<String> target) {
        for (int i = 0; i < arr.length(); i++) {
            JSONObject o = arr.optJSONObject(i);
            if (o == null) continue;
            String no = o.optString("合同编号", "");
            if (!no.isEmpty()) target.add(no);
        }
    }

    private static boolean orderListContainsNewEntrust(org.json.JSONArray arr, String code,
                                                        String price,
                                                        java.util.Set<String> baselineEntrustNos) {
        int n = arr.length();
        for (int i = 0; i < n; i++) {
            org.json.JSONObject o = arr.optJSONObject(i);
            if (o == null) continue;
            String c = o.optString("代码", "");
            String p = o.optString("价格", "");
            String no = o.optString("合同编号", "");
            if (code.equals(c) && price.equals(p) && !no.isEmpty()
                    && !baselineEntrustNos.contains(no)) return true;
        }
        return false;
    }

    private static boolean orderStatusCancelled(org.json.JSONArray arr, String entrustNo) {
        int n = arr.length();
        for (int i = 0; i < n; i++) {
            org.json.JSONObject o = arr.optJSONObject(i);
            if (o == null) continue;
            if (entrustNo.equals(o.optString("合同编号", ""))) {
                String st = o.optString("状态", "");
                if (st.contains("已撤")) return true;
            }
        }
        return false;
    }

    private static String writeConfirmed(JSONObject out, boolean executed, String via,
                                         Object orders, int round, long t0) {
        try {
            out.put("ok", true);
            out.put("executed", executed);
            out.put("confirmed_via", via);
            out.put("rounds", round);
            if (orders != null) out.put("orders", orders);
            out.put("elapsed_ms", System.currentTimeMillis() - t0);
        } catch (JSONException ignored) { }
        return out.toString();
    }

    private static String writeUnconfirmed(JSONObject out, Object orders,
                                           JSONObject ordersResp, long t0) {
        try {
            out.put("ok", false);
            out.put("executed", false);
            out.put("confirmed_via", "none");
            out.put("error", "write unconfirmed after 2 rounds (session window); "
                    + "treat orders list as ground truth");
            if (orders != null) out.put("orders", orders);
            if (ordersResp != null) out.put("followup_ok", ordersResp.opt("ok"));
            out.put("elapsed_ms", System.currentTimeMillis() - t0);
        } catch (JSONException ignored) { }
        return out.toString();
    }

    /** 写响应旁路捕获：响应帧经任一观察者体系分发时，frameId 匹配 pending
     *  登记的写协议即复制一份（不参与 App 观察者的消费竞争）。 */
    private static void capturePendingWrite(Object stuff, String via) {
        if (stuff == null || pendingWriteLatch.isEmpty()) return;
        try {
            int frameId = stuff.getClass().getField("frameId").getInt(stuff);
            java.util.List<Object> sink = pendingWriteStuff.get(frameId);
            if (sink != null) {
                sink.add(stuff);
                CountDownLatch l = pendingWriteLatch.get(frameId);
                if (l != null) l.countDown();
                Log.w(TAG, "WriteBypass captured frameId=" + frameId
                        + " via=" + via + " stuff=" + stuff.getClass().getSimpleName());
            }
        } catch (Throwable ignored) { }
    }

    /** 写端点兜底确认（2026-08-18 压测发现：写操作 100% 执行但响应观察者
     *  2/3 概率收不到帧，客户端拿到超时无法定论）。响应超时时自动查当日委托
     *  附最新状态——"操作已执行与否"以委托列表为准（写超时铁律的设备端实现）。 */
    private static String appendWriteConfirmation(String result, String code) {
        if (result == null || !result.contains("timeout waiting response")) return result;
        try {
            Thread.sleep(3000);
            String orders = invokeTradeQueryByName("today_order");
            JSONObject out = new JSONObject(result);
            out.put("write_unconfirmed", true);
            out.put("note", "write likely executed but response frame lost; "
                    + "today_order + push events attached as ground truth");
            try {
                JSONObject od = new JSONObject(orders);
                JSONObject dd = od.optJSONObject("data");
                if (dd != null) {
                    Object recs = dd.opt("records");
                    if (recs == null) recs = dd.opt("rows");
                    if (recs != null) out.put("followup_orders", recs);
                }
                out.put("followup_ok", od.opt("ok"));
            } catch (Throwable ignore) { }
            // 推送事件（事件驱动确认）：写后到达的委托/成交状态推送
            try {
                java.util.List<String> recent = new ArrayList<>();
                synchronized (wtPushLock) {
                    java.util.Iterator<String> it = wtPushEvents.descendingIterator();
                    int n = 0;
                    while (it.hasNext() && n < 5) { recent.add(it.next()); n++; }
                }
                if (!recent.isEmpty()) out.put("push_events", new org.json.JSONArray(recent));
            } catch (Throwable ignore) { }
            return out.toString();
        } catch (Throwable t) {
            return result;
        }
    }

    /**
     * POST /stock/trade/cancel — 撤单执行器。
     * body: {"entrust_no":"...","stock_code":"...","confirm":true}
     * 协议源码逆向自 b8p：P(2684).U(22157).c0(eb6: 2135=委托号, 2167=第二键)。
     * 参数与 App 真机撤单捕获逐字节一致（2026-08-16 验证）。
     * 业务结果在 StuffResourceStruct.buffer（GBK JSON retcode/retmsg，响应已解码）。
     * 注意：夜市委托（未报状态）撤单曾两次收到响应但订单未撤——retcode 待解码复测。
     */
    private static String handleTradeCancel(String body) throws org.json.JSONException {
        JSONObject resp = new JSONObject();
        resp.put("endpoint", "cancel");
        String entrustNo = extractJsonString(body, "entrust_no");
        String stockCode = extractJsonString(body, "stock_code");
        String stockName = extractJsonString(body, "stock_name");
        String marketCode = extractJsonString(body, "market_code");
        String shareholderAccount = extractJsonString(body, "shareholder_account");
        String withdrawableQty = extractJsonString(body, "withdrawable_qty");
        String confirm = extractJsonString(body, "confirm");
        if (!"true".equals(confirm)) {
            return errorJson(resp, "confirm!=true: write endpoints require explicit confirmation");
        }
        // App 撤单 Tab 真实协议（v3p QuanCheClient 源码 + 真机捕获）：
        //   uqv.e(true).P(2683).U(25102).R(observerId).c0(params).a0()
        // params 为 eb6 格式：2103=撤单条数, 2102="code_名称_委托号_市场码_股东账号_可撤数量"
        // （多单用 | 分隔）。全撤走 25106（格式不同）。此前 22157（b8p）被券商判"未知请求"。
        String params;
        if (entrustNo != null && stockCode != null && stockName != null
                && marketCode != null && shareholderAccount != null) {
            String qty = withdrawableQty == null ? "0" : withdrawableQty.trim();
            String entry = stockCode.trim() + "_" + stockName.trim() + "_" + entrustNo.trim()
                    + "_" + marketCode.trim() + "_" + shareholderAccount.trim() + "_" + qty;
            params = "ctrlid_0=2103\nctrlvalue_0=1\nctrlid_1=2102\nctrlvalue_1="
                    + entry + "\nctrlcount=2";
        } else {
            // 兜底：App UI 撤过一单后（MhvDump 自动捕获），复用捕获 params 原样重放
            params = capturedQueryParams.get(25102);
            if (params == null) {
                return errorJson(resp, "missing trade fields (entrust_no/stock_code/stock_name/"
                        + "market_code/shareholder_account) and no captured 25102 params "
                        + "(cancel once in app UI after this deploy, then retry)");
            }
            resp.put("params_source", "captured_25102");
        }
        resp.put("protocolId", 25102);
        resp.put("pageId", 2683);
        // 2026-08-18 终版协议（fire-and-forget）：撤单链路上任何同步等待都会
        // 拖死 handler——连续写场景同步响应帧被 onChannelBad 丢、ensure 链
        // 按档自续（15/45/90s...）、强制登录在锁上竞争，实测单轮同步也要
        // >200s。而撤单请求本身 100% 能到达券商（压测 8/8 实际执行成功，
        // 唯一可靠信源是 today_order 终态）。因此本端点只负责"发出"（异步
        // 线程，<1s 返回 fired），执行确认完全由调用方（服务端 cancel_order）
        // 轮询 today_order 完成；未确认时由服务端再次 fire。
        Thread fireThread = new Thread(() -> {
            try {
                String r = invokeTradeQuery(25102, 2683, params, false, true);
                Log.w(TAG, "cancel-fire done: " + (r == null ? "null"
                        : r.substring(0, Math.min(120, r.length()))));
            } catch (Throwable t) {
                Log.w(TAG, "cancel-fire failed: " + t);
            }
        }, "cancel-fire");
        fireThread.setDaemon(true);
        fireThread.start();
        try {
            resp.put("ok", true);
            resp.put("fired", true);
            resp.put("async_confirm", true);
            resp.put("executed", (Object) null);
            resp.put("confirm_hint", "poll today_order for 已撤");
            resp.put("elapsed_ms", 0);
        } catch (Throwable ignore) { }
        return resp.toString();
    }

    /** v3p 撤单发送链：uqv.e(true).P(2683).U(25102).R(kmv.c(observer)).c0(params).a0()。
     *  R 需要 int 观察者 id（先 kmv.c(proxy) 注册）。响应 StuffResourceStruct：type=5、
     *  buffer=GBK JSON（retcode/retmsg，v3p.t/w 解析；stuffTableToJson 已解码输出）。 */
    private static String invokeWithdrawV3p(String params, JSONObject resp) {
        ClassLoader cl = thsAppClassLoader;
        if (cl == null) {
            return errorJson(resp, "ths classloader not ready (wait for delayed hooks)");
        }
        if (!ensureTradeRuntimeReady(cl)) {
            return errorJson(resp, lastEnsureTradeError);
        }
        final CountDownLatch latch = new CountDownLatch(1);
        final java.util.List<Object> stuffList = java.util.Collections.synchronizedList(new ArrayList<>());
        Object observer;
        try {
            Class<?> imvClass = cl.loadClass("imv");
            observer = java.lang.reflect.Proxy.newProxyInstance(cl,
                    new Class[]{imvClass},
                    (proxy, method, args) -> {
                        String name = method.getName();
                        if ("receive".equals(name)) {
                            if (args != null && args.length > 0 && args[0] != null) {
                                stuffList.add(args[0]);
                                latch.countDown();
                            }
                            return null;
                        }
                        if ("hashCode".equals(name)) return System.identityHashCode(proxy);
                        if ("toString".equals(name)) return "THSHook.cancelObserver@" + Integer.toHexString(System.identityHashCode(proxy));
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
            Class<?> kmvClass = cl.loadClass("kmv");
            Integer observerId = (Integer) kmvClass.getMethod("c", imvClass).invoke(null, observer);
            Object rpv = builder.getClass().getMethod("P", int.class).invoke(builder, 2683);
            rpv = rpv.getClass().getMethod("U", int.class).invoke(rpv, 25102);
            rpv = rpv.getClass().getMethod("R", int.class).invoke(rpv, observerId);
            rpv = rpv.getClass().getMethod("c0", String.class).invoke(rpv, params);
            rpv.getClass().getMethod("a0").invoke(rpv);
        } catch (Throwable e) {
            unregisterObserverQuietly(cl, observer);
            return errorJson(resp, "cancel invoke failed: " + describeThrowableChain(e));
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
        if (stuffList.isEmpty()) {
            return errorJson(resp, "timeout waiting response (15s)");
        }
        try {
            JSONObject parsed = stuffTableToJson(stuffList, 25102);
            resp.put("ok", true);
            resp.put("data", parsed);
            // 25102 响应结构：{count, stockArr:[{code, message, htbh}]}，
            // 成功 = stockArr[0].code=="0"（真机验证：撤 1404 → code=0 message=合同编号）
            try {
                String gbk = parsed.optJSONObject("fields_dump") == null ? null
                        : parsed.optJSONObject("fields_dump").optString("buffer_gbk", "");
                if (!gbk.isEmpty()) {
                    org.json.JSONObject j = new org.json.JSONObject(gbk);
                    org.json.JSONArray arr = j.optJSONArray("stockArr");
                    if (arr != null && arr.length() > 0) {
                        resp.put("business_ok", "0".equals(arr.optJSONObject(0).optString("code")));
                        resp.put("business_message", arr.optJSONObject(0).optString("message", ""));
                    }
                }
            } catch (Throwable ignored) { }
            return resp.toString();
        } catch (Throwable e) {
            return errorJson(resp, "parse response failed: " + e);
        }
    }

    /**
     * GET /stock/trade/transfer/banks — 存管银行列表（只读）。
     * 协议 1830 reqctrl=6013（源码 d1m 内部类 d，真机页面打开时已捕获空参数版）。
     */
    private static String handleTransferBanks() throws org.json.JSONException {
        JSONObject resp = new JSONObject();
        resp.put("endpoint", "transfer_banks");
        return invokeTradeQuery(1830, 1830, "reqctrl=6013", true);
    }

    /**
     * POST /stock/trade/transfer — 银证转账（转入证券账户）。
     * body: {"direction":"in","amount":"100","bank_password":"...","bank_index":"0","confirm":true}
     * 协议 1826 reqctrl=6015：116=银行索引, 103=金额, 120=w0s.b(银行密码)（客户端加密）。
     * 源码逆向自 c1m.J → d1m.a。⚠️ 未真机验证（需银行密码+交易日窗口）。
     */
    private static String handleTradeTransfer(String body) throws org.json.JSONException {
        JSONObject resp = new JSONObject();
        resp.put("endpoint", "transfer");
        String direction = extractJsonString(body, "direction");
        String amount = extractJsonString(body, "amount");
        String bankPassword = extractJsonString(body, "bank_password");
        String bankIndex = extractJsonString(body, "bank_index");
        String confirm = extractJsonString(body, "confirm");
        if (amount == null || bankPassword == null) {
            return errorJson(resp, "missing amount/bank_password");
        }
        if (!"true".equals(confirm)) {
            return errorJson(resp, "confirm!=true: write endpoints require explicit confirmation");
        }
        if (direction == null || !"in".equals(direction.trim())) {
            return errorJson(resp, "only direction=in implemented (transfer-out protocol not yet reversed)");
        }
        String readinessError = requireTradeWriteReady(resp);
        if (readinessError != null) {
            return errorJson(resp, readinessError);
        }
        String encPwd;
        try {
            Class<?> w0s = thsAppClassLoader.loadClass("w0s");
            java.lang.reflect.Method b = w0s.getDeclaredMethod("b", String.class);
            b.setAccessible(true);
            Object r = b.invoke(null, bankPassword);
            encPwd = r == null ? null : String.valueOf(r);
        } catch (Throwable e) {
            return errorJson(resp, "w0s.b password encrypt failed: " + e);
        }
        if (encPwd == null) {
            return errorJson(resp, "w0s.b returned null");
        }
        String params = "ctrlcount=3\r\nctrlid_0=116\r\nctrlvalue_0=" + (bankIndex == null ? "0" : bankIndex.trim())
                + "\r\nctrlid_1=103\r\nctrlvalue_1=" + amount.trim()
                + "\r\nctrlid_2=120\r\nctrlvalue_2=" + encPwd
                + "\r\nreqctrl=6015";
        resp.put("protocolId", 1826);
        resp.put("pageId", 1826);
        resp.put("unverified", "source-derived params (d1m.a), not yet replay-verified");
        return invokeTradeQuery(1826, 1826, params, true, true);
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
