package com.yuyang.qdhook;

import android.app.Activity;
import android.app.Application;
import android.net.LocalServerSocket;
import android.net.LocalSocket;
import android.os.Bundle;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.io.PrintWriter;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.LinkedList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

import javax.crypto.Cipher;
import javax.crypto.spec.IvParameterSpec;

import top.canyie.pine.Pine;
import top.canyie.pine.PineConfig;
import top.canyie.pine.callback.MethodHook;

public class MainHook {

    private static final String TAG = "QDHook";
    private static volatile boolean hooksInstalled = false;
    private static final ConcurrentHashMap<String, Long> recentRequests = new ConcurrentHashMap<>();
    private static final long DEDUP_WINDOW_MS = 500;

    // 远程日志转发器
    private static final String REMOTE_LOG_HOST = "127.0.0.1";  // 通过 adb reverse tcp:8889 tcp:8889 映射到 PC
    private static final int REMOTE_LOG_PORT = 8889;
    private static volatile Socket logSocket = null;
    private static volatile PrintWriter logWriter = null;
    private static volatile boolean logForwarderRunning = false;
    private static final AtomicInteger logForwarderErrors = new AtomicInteger(0);
    private static final LinkedList<String> logBuffer = new LinkedList<>();
    private static final int MAX_LOG_BUFFER = 5000;

    // Crypto hook fields
    private static final String CRYPTO_TAG = "QDCrypto";
    private static final ConcurrentHashMap<Integer, CipherContext> trackedCiphers = new ConcurrentHashMap<>();
    private static final AtomicInteger cryptoLogCount = new AtomicInteger(0);
    private static volatile long cryptoLogWindowStart = 0;
    private static final int CRYPTO_LOG_LIMIT = 30;

    // Phase 0: 探测模式日志
    private static final String PROBE_TAG = "QDProbe";
    private static final int MAX_PROBE_LOGS = 500;
    private static final LinkedList<String> probeLogs = new LinkedList<>();

    // 方法调用记录（用于分析调用链）
    private static final int MAX_CALL_RECORDS = 200;
    private static final LinkedList<MethodCallRecord> callRecords = new LinkedList<>();

    // 已知的入口方法（从探测中收集）
    private static final ConcurrentHashMap<String, MethodEntry> knownEntries = new ConcurrentHashMap<>();

    // 保存的关键对象实例（用于主动调用）
    private static volatile Object bllVInstance = null;
    private static final ConcurrentHashMap<String, Object> chapterItems = new ConcurrentHashMap<>();
    private static final ConcurrentHashMap<String, Object> vObjects = new ConcurrentHashMap<>();  // 保存 v 对象（bll.v.N 的第三个参数）

    // Phase 1: 捕获的解密内容
    private static final int MAX_CAPTURED = 50;
    private static final LinkedList<CapturedContent> recentCaptured = new LinkedList<>();

    // LocalSocket RPC
    private static final String SOCKET_NAME = "qdhook_rpc";
    private static volatile LocalServerSocket serverSocket;
    private static volatile boolean rpcRunning = false;

    // Search RPC: 保存 OkHttpClient 和搜索请求模板
    private static volatile Object savedOkHttpClient = null;
    private static volatile boolean apiClientCaptured = false;  // API 客户端已从 druidv6 请求捕获
    private static volatile String cachedUserId = null;  // 缓存的用户 ID（从 book 目录获取）
    private static volatile String searchUrlTemplate = null;
    private static volatile String searchHttpMethod = null;
    private static volatile String searchBodyTemplate = null;
    private static volatile String lastSearchResponse = null;

    // HTTP 请求节流：记录上次请求时间，防止请求过于密集导致 App 无响应
    private static volatile long lastRequestTime = 0;
    private static final long MIN_REQUEST_INTERVAL_MS = 30;  // 每个请求之间最少间隔 30ms

    // 保存最近一次 pf.cihai.search 调用捕获的密钥（String 参数）
    private static volatile String lastCapturedDecryptKey = null;
    // 保存 pf.cihai.search 解密方法的引用（hook 初始化时获取，避免 ClassLoader 问题）
    private static volatile java.lang.reflect.Method pfCihaiSearchDecryptMethod = null;

    // 自动捕获的章节内容缓存（从 bll.v afterCall 中提取的 ChapterContentItem）
    private static final ConcurrentHashMap<String, String> capturedChapterContents = new ConcurrentHashMap<>();

    // 硬编码的搜索 fallback（已知的 API 格式，无需手动搜索即可使用）
    private static final String FALLBACK_SEARCH_URL = "https://druidv6.if.qidian.com/argus/api/v2/booksearch/searchbooks";
    private static final String FALLBACK_SEARCH_METHOD = "POST";
    private static final String FALLBACK_SEARCH_BODY = "keywordType=1&siteId=1&keyword={keyword}&pageSize=20&pageIndex={page}";

    private static class CapturedContent {
        String plaintext;
        String algorithm;
        String keyHex;
        long timestamp;

        CapturedContent(String plaintext, String algorithm, String keyHex) {
            this.plaintext = plaintext;
            this.algorithm = algorithm;
            this.keyHex = keyHex;
            this.timestamp = System.currentTimeMillis();
        }
    }

    private static class MethodCallRecord {
        String className;
        String methodName;
        String args;
        String returnValue;
        long timestamp;
        String threadName;

        MethodCallRecord(String className, String methodName, String args, String returnValue) {
            this.className = className;
            this.methodName = methodName;
            this.args = args;
            this.returnValue = returnValue;
            this.timestamp = System.currentTimeMillis();
            this.threadName = Thread.currentThread().getName();
        }

        @Override
        public String toString() {
            return timestamp + " | " + threadName + " | " + className + "." + methodName + "(" + args + ")" +
                   (returnValue != null ? " → " + returnValue : "");
        }
    }

    private static class MethodEntry {
        String className;
        String methodName;
        Class<?>[] paramTypes;
        Object instance;  // 单例实例
        boolean isStatic;

        MethodEntry(String className, String methodName, Class<?>[] paramTypes, Object instance, boolean isStatic) {
            this.className = className;
            this.methodName = methodName;
            this.paramTypes = paramTypes;
            this.instance = instance;
            this.isStatic = isStatic;
        }
    }

    private static class CipherContext {
        String algorithm;
        byte[] keyBytes;
        byte[] ivBytes;
        long initTimeMs;
        boolean stackPrinted;

        CipherContext(String algorithm, byte[] keyBytes, byte[] ivBytes) {
            this.algorithm = algorithm;
            this.keyBytes = keyBytes;
            this.ivBytes = ivBytes;
            this.initTimeMs = System.currentTimeMillis();
            this.stackPrinted = false;
        }
    }

    // Only hook framework classes (Application.onCreate + OkHttpClient$Builder.build)
    // 360加固 checks app class method integrity but NOT framework classes

    public static void entry(ClassLoader classLoader, String pineSoPath) {
        Log.i(TAG, "Pine hook entry called");

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
            Log.i(TAG, "Pine initialized (antiChecks=true, no hiddenApi bypass)");

            // 方案 A: Hook Application.onCreate（作为备份）
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

            // 方案 B: 延迟线程直接安装（绕过 360 加固可能导致的 Application.onCreate 回调失效）
            new Thread(() -> {
                try {
                    // 等待 App 完全初始化，通过 ActivityThread.currentApplication() 获取 Application
                    for (int i = 0; i < 30; i++) {
                        Thread.sleep(500);
                        try {
                            Class<?> atClass = Class.forName("android.app.ActivityThread");
                            Method currentApp = atClass.getDeclaredMethod("currentApplication");
                            Application app = (Application) currentApp.invoke(null);
                            if (app != null) {
                                ClassLoader cl = app.getClassLoader();
                                if (cl != null) {
                                    // 验证能加载起点的类
                                    cl.loadClass("com.qidian.QDReader.QDApplication");
                                    Log.i(TAG, "App ready via ActivityThread after " + (i * 500) + "ms, app=" + app.getClass().getName());
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
            }, "QDHook-Delay").start();

        } catch (Throwable e) {
            Log.e(TAG, "Hook failed", e);
        }
    }

    /**
     * 安装所有 Hook（只执行一次）
     */
    private static synchronized void installAllHooks(ClassLoader cl) {
        if (hooksInstalled) return;
        hooksInstalled = true;
        appClassLoader = cl;

        Log.i(TAG, "=== installAllHooks start === cl=" + cl);

        try { injectInterceptor(cl); Log.i(TAG, "injectInterceptor done"); } catch (Throwable e) { Log.e(TAG, "injectInterceptor failed", e); }
        try { installCryptoHooks(); Log.i(TAG, "installCryptoHooks done"); } catch (Throwable e) { Log.e(TAG, "installCryptoHooks failed", e); }
        try { startRemoteLogForwarder(); Log.i(TAG, "startRemoteLogForwarder done"); } catch (Throwable e) { Log.e(TAG, "startRemoteLogForwarder failed", e); }
        try { installProbeHooks(cl); Log.i(TAG, "installProbeHooks done"); } catch (Throwable e) { Log.e(TAG, "installProbeHooks failed", e); }
        try { startRpcServer(); Log.i(TAG, "startRpcServer done"); } catch (Throwable e) { Log.e(TAG, "startRpcServer failed", e); }

        Log.i(TAG, "=== installAllHooks complete ===");
    }

    /**
     * Inject a logging interceptor into OkHttp using pure reflection + dynamic proxy.
     * No Pine hooks on OkHttp classes — avoids 360加固's code integrity checks.
     */
    private static void injectInterceptor(ClassLoader cl) {
        try {
            // Load OkHttp classes
            Class<?> interceptorClass;
            try {
                interceptorClass = cl.loadClass("okhttp3.Interceptor");
            } catch (ClassNotFoundException e) {
                Log.w(TAG, "okhttp3.Interceptor not found");
                return;
            }

            Class<?> chainClass = cl.loadClass("okhttp3.Interceptor$Chain");
            Class<?> clientBuilderClass = cl.loadClass("okhttp3.OkHttpClient$Builder");
            Class<?> clientClass = cl.loadClass("okhttp3.OkHttpClient");

            // Create our logging interceptor using java.lang.reflect.Proxy
            Object loggingInterceptor = Proxy.newProxyInstance(
                    cl,
                    new Class<?>[]{interceptorClass},
                    new InterceptorHandler(cl, chainClass)
            );
            Log.i(TAG, "Logging interceptor created via Proxy");

            // Hook OkHttpClient$Builder.build() to inject our interceptor into every client
            // NOTE: This hooks an app-loaded class. If this triggers 360加固, we'll need
            // an alternative approach (e.g., hooking OkHttpClient constructor or using mitmproxy).
            Method buildMethod = clientBuilderClass.getDeclaredMethod("build");
            Pine.hook(buildMethod, new MethodHook() {
                private final AtomicBoolean logged = new AtomicBoolean(false);

                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    try {
                        Object builder = callFrame.thisObject;
                        // Add our interceptor to the builder's interceptor list
                        Method addInterceptor = clientBuilderClass.getDeclaredMethod(
                                "addInterceptor", interceptorClass);
                        addInterceptor.invoke(builder, loggingInterceptor);
                        if (!logged.getAndSet(true)) {
                            Log.i(TAG, "Interceptor injected into OkHttpClient.Builder");
                        }
                    } catch (Throwable e) {
                        Log.e(TAG, "Failed to inject interceptor", e);
                    }
                }

                @Override
                public void afterCall(Pine.CallFrame callFrame) {
                    if (savedOkHttpClient == null) {
                        savedOkHttpClient = callFrame.getResult();
                        Log.i(TAG, "OkHttpClient saved for RPC use");
                    }
                }
            });
            Log.i(TAG, "OkHttpClient.Builder.build() hooked for interceptor injection");

            // 同时 hook OkHttpClient.newCall() 来捕获已存在的 client 实例
            // （App 可能在 hook 安装前就已经创建了 OkHttpClient）
            try {
                Class<?> requestClass = cl.loadClass("okhttp3.Request");
                Method newCallMethod = clientClass.getDeclaredMethod("newCall", requestClass);
                Pine.hook(newCallMethod, new MethodHook() {
                    private final AtomicBoolean captured = new AtomicBoolean(false);

                    @Override
                    public void beforeCall(Pine.CallFrame callFrame) {
                        if (savedOkHttpClient == null) {
                            savedOkHttpClient = callFrame.thisObject;
                            if (!captured.getAndSet(true)) {
                                Log.i(TAG, "OkHttpClient captured via newCall()");
                            }
                        }
                    }
                });
                Log.i(TAG, "OkHttpClient.newCall() hooked for client capture");
            } catch (Throwable e2) {
                Log.w(TAG, "Failed to hook OkHttpClient.newCall(): " + e2.getMessage());
            }

        } catch (Throwable e) {
            Log.e(TAG, "Failed to set up interceptor injection", e);
        }
    }

    /**
     * InvocationHandler for okhttp3.Interceptor proxy.
     * Implements intercept(Chain) by calling chain.proceed(request) and logging the data.
     */
    private static class InterceptorHandler implements InvocationHandler {
        private final ClassLoader cl;
        private final Class<?> chainClass;

        InterceptorHandler(ClassLoader cl, Class<?> chainClass) {
            this.cl = cl;
            this.chainClass = chainClass;
        }

        @Override
        public Object invoke(Object proxy, Method method, Object[] args) throws Throwable {
            // Handle toString, equals, hashCode
            if ("toString".equals(method.getName())) return "QDHookInterceptor";
            if ("equals".equals(method.getName())) return proxy == args[0];
            if ("hashCode".equals(method.getName())) return System.identityHashCode(proxy);

            // intercept(Chain chain)
            if (!"intercept".equals(method.getName()) || args == null || args.length != 1) {
                return method.invoke(proxy, args);
            }

            Object chain = args[0];

            // Get the request from chain
            Method requestMethod = chainClass.getDeclaredMethod("request");
            Object request = requestMethod.invoke(chain);

            // Get URL and method
            String url = "";
            String httpMethod = "";
            try {
                Method urlMethod = request.getClass().getDeclaredMethod("url");
                Object httpUrl = urlMethod.invoke(request);
                url = httpUrl != null ? httpUrl.toString() : "null";

                Method methodMethod = request.getClass().getDeclaredMethod("method");
                httpMethod = (String) methodMethod.invoke(request);
            } catch (Throwable ignored) {}

            if (isStaticResource(url)) {
                // Pass through without logging
                Method proceedMethod = chainClass.getDeclaredMethod("proceed",
                        cl.loadClass("okhttp3.Request"));
                return proceedMethod.invoke(chain, request);
            }

            // 特殊处理搜索请求
            boolean isSearchRequest = url.toLowerCase().contains("/search") || url.toLowerCase().contains("search");

            // Log request (with dedup)
            boolean shouldLog = false;
            long now = System.currentTimeMillis();
            Long lastSeen = recentRequests.get(url);
            if (lastSeen == null || (now - lastSeen) >= DEDUP_WINDOW_MS) {
                recentRequests.put(url, now);
                shouldLog = true;

                if (recentRequests.size() > 200) {
                    recentRequests.entrySet().removeIf(e -> (now - e.getValue()) > 10000);
                }
            }

            if (shouldLog) {
                StringBuilder sb = new StringBuilder();
                sb.append("→ ").append(httpMethod).append(" ").append(url);

                // Read request body for POST
                String bodyContent = "";
                try {
                    Method bodyMethod = request.getClass().getDeclaredMethod("body");
                    Object body = bodyMethod.invoke(request);
                    if (body != null) {
                        Method contentTypeMethod = body.getClass().getMethod("contentType");
                        Object contentType = contentTypeMethod.invoke(body);
                        String bodyInfo = contentType != null ? contentType.toString() : "";
                        if (!bodyInfo.isEmpty()) {
                            sb.append(" [").append(bodyInfo).append("]");
                        }
                        if (bodyInfo.contains("json") || bodyInfo.contains("form")
                                || bodyInfo.contains("text")) {
                            bodyContent = readRequestBody(body);
                        }
                    }
                } catch (Throwable ignored) {}

                // 特殊处理搜索请求
                if (isSearchRequest) {
                    logRemote(TAG, "=== 搜索请求 ===");
                    logRemote(TAG, "SEARCH_HTTP | URL: " + url);
                    logRemote(TAG, "SEARCH_HTTP | Method: " + httpMethod);
                    if (!bodyContent.isEmpty()) {
                        logRemote(TAG, "SEARCH_HTTP | Body: " + bodyContent);
                    }

                    // 捕获搜索请求模板（用于 RPC 搜索）
                    if (url.contains("searchbooks")) {
                        searchUrlTemplate = url;
                        searchHttpMethod = httpMethod;
                        searchBodyTemplate = bodyContent;
                        Log.i(TAG, "Search request template captured: " + url);
                    }

                    // 打印调用栈（合并为一条日志避免碎片化）
                    StringBuilder searchStack = new StringBuilder("SEARCH_HTTP | CallStack:");
                    for (StackTraceElement ste : Thread.currentThread().getStackTrace()) {
                        searchStack.append("\n    ").append(ste.toString());
                    }
                    logRemote(TAG, searchStack.toString());
                }

                Log.i(TAG, sb.toString());
                if (!bodyContent.isEmpty()) {
                    Log.i(TAG, "  ReqBody: " + truncate(bodyContent, 2000));
                }
            }

            // Proceed with the actual request
            Method proceedMethod = chainClass.getDeclaredMethod("proceed",
                    cl.loadClass("okhttp3.Request"));
            Object response = proceedMethod.invoke(chain, request);

            // Log response
            if (shouldLog && response != null) {
                try {
                    Method codeMethod = response.getClass().getDeclaredMethod("code");
                    int code = (int) codeMethod.invoke(response);

                    // Read response body via peekBody
                    String bodyStr = "";
                    try {
                        Method peekBodyMethod = response.getClass()
                                .getDeclaredMethod("peekBody", long.class);
                        Object peekBody = peekBodyMethod.invoke(response, 16384L);
                        if (peekBody != null) {
                            Method sourceMethod = peekBody.getClass().getMethod("source");
                            Object source = sourceMethod.invoke(peekBody);
                            if (source != null) {
                                Method readUtf8 = source.getClass().getMethod("readUtf8");
                                bodyStr = (String) readUtf8.invoke(source);
                            }
                            try {
                                peekBody.getClass().getMethod("close").invoke(peekBody);
                            } catch (Throwable ignored) {}
                        }
                    } catch (Throwable ignored) {}

                    Log.i(TAG, "← [" + code + "] " + url);

                    // 特殊处理搜索响应
                    if (isSearchRequest && !bodyStr.isEmpty()) {
                        logRemote(TAG, "SEARCH_HTTP | Response: " + code);
                        logRemote(TAG, "SEARCH_HTTP | ResponseBody: " + truncate(bodyStr, 4000));
                        if (url.contains("searchbooks")) {
                            lastSearchResponse = bodyStr;
                        }
                    }

                    if (!bodyStr.isEmpty() && bodyStr.length() > 2 && !looksLikeBinary(bodyStr)) {
                        logLong(TAG, "  Body: " + truncate(bodyStr, 4000));
                    }

                    // 对 bookcontent 请求记录详细响应到 logRemote
                    if (url.contains("bookcontent")) {
                        StringBuilder respDetail = new StringBuilder();
                        respDetail.append("BOOKCONTENT_RESP | code=").append(code);
                        respDetail.append(" | url=").append(url);
                        if (looksLikeBinary(bodyStr)) {
                            respDetail.append(" | binary, len=").append(bodyStr.length());
                        } else {
                            respDetail.append(" | body=").append(truncate(bodyStr, 2000));
                        }
                        logRemote(TAG, respDetail.toString());
                    }
                } catch (Throwable ignored) {}
            }

            return response;
        }
    }

    private static String readRequestBody(Object requestBody) {
        try {
            Method contentLengthMethod = requestBody.getClass().getMethod("contentLength");
            long contentLength = (long) contentLengthMethod.invoke(requestBody);
            if (contentLength > 50000 || contentLength == 0) return "";

            Method writeToMethod = null;
            for (Method m : requestBody.getClass().getMethods()) {
                if ("writeTo".equals(m.getName()) && m.getParameterTypes().length == 1) {
                    writeToMethod = m;
                    break;
                }
            }
            if (writeToMethod == null) return "";

            Class<?> sinkType = writeToMethod.getParameterTypes()[0];
            ClassLoader okioLoader = sinkType.getClassLoader();
            Class<?> bufferClass = okioLoader.loadClass(
                    sinkType.getName().replace("BufferedSink", "Buffer")
                            .replace("bufferedSink", "Buffer"));

            if (bufferClass.isInterface()) {
                String pkg = sinkType.getPackage() != null ? sinkType.getPackage().getName() : "okio";
                bufferClass = okioLoader.loadClass(pkg + ".Buffer");
            }

            Object buffer = bufferClass.getDeclaredConstructor().newInstance();
            writeToMethod.invoke(requestBody, buffer);

            Method readUtf8Method = bufferClass.getMethod("readUtf8");
            return (String) readUtf8Method.invoke(buffer);
        } catch (Throwable e) {
            return "";
        }
    }

    private static void logLong(String tag, String msg) {
        int chunkSize = 3800;
        for (int i = 0; i < msg.length(); i += chunkSize) {
            int end = Math.min(i + chunkSize, msg.length());
            if (i == 0) {
                Log.i(tag, msg.substring(0, end));
            } else {
                Log.i(tag, "  ..." + msg.substring(i, end));
            }
        }
    }

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

    private static boolean isStaticResource(String url) {
        return url.endsWith(".jpg") || url.endsWith(".png") || url.endsWith(".gif")
                || url.endsWith(".webp") || url.endsWith(".ico") || url.endsWith(".css")
                || url.endsWith(".js") || url.contains("/image/") || url.contains("/img/");
    }

    private static String truncate(String s, int maxLen) {
        if (s == null) return "null";
        if (s.length() <= maxLen) return s;
        return s.substring(0, maxLen) + "...(" + s.length() + " chars)";
    }

    // ==================== Crypto Hooks ====================

    private static void installCryptoHooks() {
        try {
            // Hook Cipher.init(int, Key, AlgorithmParameterSpec)
            Method initWithParams = Cipher.class.getDeclaredMethod("init",
                    int.class, java.security.Key.class, java.security.spec.AlgorithmParameterSpec.class);
            Pine.hook(initWithParams, new MethodHook() {
                @Override
                public void afterCall(Pine.CallFrame callFrame) {
                    try {
                        int opmode = (int) callFrame.args[0];
                        if (opmode != Cipher.DECRYPT_MODE) return;

                        Cipher cipher = (Cipher) callFrame.thisObject;
                        String algorithm = cipher.getAlgorithm();
                        if (isTlsAlgorithm(algorithm)) return;

                        java.security.Key key = (java.security.Key) callFrame.args[1];
                        byte[] keyBytes = key != null ? key.getEncoded() : null;

                        byte[] ivBytes = null;
                        Object paramSpec = callFrame.args[2];
                        if (paramSpec instanceof IvParameterSpec) {
                            ivBytes = ((IvParameterSpec) paramSpec).getIV();
                        }

                        int id = System.identityHashCode(cipher);
                        trackedCiphers.put(id, new CipherContext(algorithm, keyBytes, ivBytes));
                        cleanupTrackedCiphers();
                    } catch (Throwable e) {
                        Log.e(CRYPTO_TAG, "init(3) hook error", e);
                    }
                }
            });

            // Hook Cipher.init(int, Key)
            Method initSimple = Cipher.class.getDeclaredMethod("init",
                    int.class, java.security.Key.class);
            Pine.hook(initSimple, new MethodHook() {
                @Override
                public void afterCall(Pine.CallFrame callFrame) {
                    try {
                        int opmode = (int) callFrame.args[0];
                        if (opmode != Cipher.DECRYPT_MODE) return;

                        Cipher cipher = (Cipher) callFrame.thisObject;
                        String algorithm = cipher.getAlgorithm();
                        if (isTlsAlgorithm(algorithm)) return;

                        java.security.Key key = (java.security.Key) callFrame.args[1];
                        byte[] keyBytes = key != null ? key.getEncoded() : null;

                        int id = System.identityHashCode(cipher);
                        trackedCiphers.put(id, new CipherContext(algorithm, keyBytes, null));
                        cleanupTrackedCiphers();
                    } catch (Throwable e) {
                        Log.e(CRYPTO_TAG, "init(2) hook error", e);
                    }
                }
            });

            // Hook Cipher.doFinal(byte[])
            Method doFinalBytes = Cipher.class.getDeclaredMethod("doFinal", byte[].class);
            Pine.hook(doFinalBytes, new MethodHook() {
                @Override
                public void afterCall(Pine.CallFrame callFrame) {
                    handleDoFinalResult(callFrame, (byte[]) callFrame.args[0]);
                }
            });

            // Hook Cipher.doFinal()
            Method doFinalEmpty = Cipher.class.getDeclaredMethod("doFinal");
            Pine.hook(doFinalEmpty, new MethodHook() {
                @Override
                public void afterCall(Pine.CallFrame callFrame) {
                    handleDoFinalResult(callFrame, null);
                }
            });

            // Hook Cipher.doFinal(byte[], int, int)
            Method doFinalRange = Cipher.class.getDeclaredMethod("doFinal",
                    byte[].class, int.class, int.class);
            Pine.hook(doFinalRange, new MethodHook() {
                @Override
                public void afterCall(Pine.CallFrame callFrame) {
                    handleDoFinalResult(callFrame, (byte[]) callFrame.args[0]);
                }
            });

            Log.i(CRYPTO_TAG, "Crypto hooks installed (Cipher.init + doFinal)");
        } catch (Throwable e) {
            Log.e(CRYPTO_TAG, "Failed to install crypto hooks", e);
        }
    }

    private static void handleDoFinalResult(Pine.CallFrame callFrame, byte[] inputBytes) {
        try {
            Cipher cipher = (Cipher) callFrame.thisObject;
            int id = System.identityHashCode(cipher);
            CipherContext ctx = trackedCiphers.remove(id);
            if (ctx == null) return;

            Object result = callFrame.getResult();
            if (!(result instanceof byte[])) return;
            byte[] output = (byte[]) result;

            if (output.length < 64) return;
            if (!rateLimitCheck()) return;

            // 先检查是否是 gzip（在 UTF-8 转换之前）
            byte[] dataToCheck = output;
            if (output.length > 2 && output[0] == 0x1f && output[1] == (byte)0x8b) {
                try {
                    java.util.zip.GZIPInputStream gzipIn = new java.util.zip.GZIPInputStream(
                        new java.io.ByteArrayInputStream(output));
                    java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
                    byte[] buffer = new byte[4096];
                    int len;
                    while ((len = gzipIn.read(buffer)) > 0) {
                        out.write(buffer, 0, len);
                    }
                    gzipIn.close();
                    dataToCheck = out.toByteArray();
                    Log.i(CRYPTO_TAG, "Gzip decompressed: " + output.length + " -> " + dataToCheck.length + " bytes");
                } catch (Throwable e) {
                    Log.e(CRYPTO_TAG, "Gzip decompress failed", e);
                }
            }

            String plaintext = new String(dataToCheck, StandardCharsets.UTF_8);
            // 跳过明显不是内容的解密结果（非JSON且无中文）
            if (!plaintext.startsWith("{") && !looksLikeChineseText(dataToCheck)) return;

            StringBuilder sb = new StringBuilder();
            sb.append("DECRYPT | algo=").append(ctx.algorithm);
            sb.append(" | key=").append(bytesToHex(ctx.keyBytes));
            if (ctx.ivBytes != null) {
                sb.append(" | iv=").append(bytesToHex(ctx.ivBytes));
            }
            if (inputBytes != null) {
                sb.append(" | inLen=").append(inputBytes.length);
            }
            sb.append(" | outLen=").append(output.length);
            Log.i(CRYPTO_TAG, sb.toString());
            logLong(CRYPTO_TAG, "  PlainText: " + truncate(plaintext, 4000));

            // 保存到捕获缓冲区（供 RPC 获取）
            saveCapturedContent(plaintext, ctx.algorithm, bytesToHex(ctx.keyBytes));

            if (!ctx.stackPrinted) {
                ctx.stackPrinted = true;
                logStackTrace();
            }
        } catch (Throwable e) {
            Log.e(CRYPTO_TAG, "doFinal hook error", e);
        }
    }

    private static boolean isTlsAlgorithm(String algorithm) {
        if (algorithm == null) return false;
        String upper = algorithm.toUpperCase();
        return upper.contains("GCM") || upper.contains("CHACHA20") || upper.contains("POLY1305")
                || upper.contains("OAEP") || upper.contains("RSA");
    }

    private static boolean looksLikeChineseText(byte[] data) {
        try {
            String text = new String(data, StandardCharsets.UTF_8);
            int checkLen = Math.min(text.length(), 200);
            int cjkCount = 0;
            for (int i = 0; i < checkLen; i++) {
                char c = text.charAt(i);
                if (c >= '\u4E00' && c <= '\u9FFF') {
                    cjkCount++;
                    if (cjkCount >= 5) return true;
                }
            }
        } catch (Throwable ignored) {}
        return false;
    }

    private static String bytesToHex(byte[] bytes) {
        if (bytes == null) return "null";
        StringBuilder sb = new StringBuilder(bytes.length * 2);
        for (byte b : bytes) {
            sb.append(String.format("%02x", b & 0xff));
        }
        return sb.toString();
    }

    private static void logStackTrace() {
        StackTraceElement[] stack = Thread.currentThread().getStackTrace();
        StringBuilder sb = new StringBuilder("  CallStack:");
        int count = 0;
        for (StackTraceElement elem : stack) {
            String cls = elem.getClassName();
            if (cls.startsWith("com.yuyang.qdhook") || cls.startsWith("top.canyie.pine")
                    || cls.startsWith("dalvik.") || cls.startsWith("java.lang.Thread")) {
                continue;
            }
            sb.append("\n    ").append(elem.getClassName()).append(".")
                    .append(elem.getMethodName()).append("(").append(elem.getFileName())
                    .append(":").append(elem.getLineNumber()).append(")");
            if (++count >= 20) break;
        }
        Log.i(CRYPTO_TAG, sb.toString());
    }

    private static boolean rateLimitCheck() {
        long now = System.currentTimeMillis();
        if (now - cryptoLogWindowStart > 60000) {
            cryptoLogWindowStart = now;
            cryptoLogCount.set(0);
        }
        return cryptoLogCount.incrementAndGet() <= CRYPTO_LOG_LIMIT;
    }

    private static void cleanupTrackedCiphers() {
        if (trackedCiphers.size() <= 500) return;
        long now = System.currentTimeMillis();
        Iterator<Map.Entry<Integer, CipherContext>> it = trackedCiphers.entrySet().iterator();
        while (it.hasNext()) {
            if (now - it.next().getValue().initTimeMs > 60000) {
                it.remove();
            }
        }
    }

    // ==================== Phase 0: 探测模式 Hook ====================

    private static void installProbeHooks(ClassLoader cl) {
        try {
            // 1. Hook Activity 生命周期
            hookActivityLifecycle();

            // 2. Hook Fragment 生命周期
            hookFragmentLifecycle(cl);

            // 3. Hook 关键业务类
            hookKeyClasses(cl);

            // 4. Hook 搜索相关类和方法
            installSearchHooks(cl);

            Log.i(PROBE_TAG, "Probe hooks installed");
        } catch (Throwable e) {
            Log.e(PROBE_TAG, "Failed to install probe hooks", e);
        }
    }

    private static void hookActivityLifecycle() {
        try {
            // Hook onCreate
            Method onCreate = Activity.class.getDeclaredMethod("onCreate", Bundle.class);
            Pine.hook(onCreate, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    Activity activity = (Activity) callFrame.thisObject;
                    String className = activity.getClass().getName();
                    // 只记录起点 App 的 Activity
                    if (className.startsWith("com.qidian")) {
                        addProbeLog("ACTIVITY_CREATE | " + className);
                    }
                }
            });

            // Hook onResume
            Method onResume = Activity.class.getDeclaredMethod("onResume");
            Pine.hook(onResume, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    Activity activity = (Activity) callFrame.thisObject;
                    String className = activity.getClass().getName();
                    if (className.startsWith("com.qidian")) {
                        addProbeLog("ACTIVITY_RESUME | " + className);
                    }
                }
            });

            Log.i(PROBE_TAG, "Activity lifecycle hooks installed");
        } catch (Throwable e) {
            Log.e(PROBE_TAG, "Failed to hook Activity lifecycle", e);
        }
    }

    private static void hookFragmentLifecycle(ClassLoader cl) {
        String[] fragmentClasses = {
                "androidx.fragment.app.Fragment",
                "android.app.Fragment"
        };

        for (String fragmentClassName : fragmentClasses) {
            try {
                Class<?> fragmentClass = cl.loadClass(fragmentClassName);

                // Hook onResume
                Method onResume = fragmentClass.getDeclaredMethod("onResume");
                Pine.hook(onResume, new MethodHook() {
                    @Override
                    public void beforeCall(Pine.CallFrame callFrame) {
                        String className = callFrame.thisObject.getClass().getName();
                        if (className.startsWith("com.qidian")) {
                            addProbeLog("FRAGMENT_RESUME | " + className);
                        }
                    }
                });

                // Hook onCreateView (可以捕获 Fragment 的创建)
                try {
                    for (Method m : fragmentClass.getDeclaredMethods()) {
                        if ("onCreateView".equals(m.getName())) {
                            Pine.hook(m, new MethodHook() {
                                @Override
                                public void beforeCall(Pine.CallFrame callFrame) {
                                    String className = callFrame.thisObject.getClass().getName();
                                    if (className.startsWith("com.qidian")) {
                                        addProbeLog("FRAGMENT_CREATE_VIEW | " + className);
                                    }
                                }
                            });
                            break;
                        }
                    }
                } catch (Throwable ignored) {}

                Log.i(PROBE_TAG, "Fragment hooks installed for " + fragmentClassName);
            } catch (ClassNotFoundException ignored) {
            } catch (Throwable e) {
                Log.e(PROBE_TAG, "Failed to hook " + fragmentClassName, e);
            }
        }
    }

    private static void hookKeyClasses(ClassLoader cl) {
        // 根据已知信息，Hook 关键的业务类
        // 这些类名可能需要根据实际反编译结果调整

        // Hook pf.cihai.search（加解密相关）— 增强：捕获密钥参数
        try {
            Class<?> cihaiSearch = cl.loadClass("pf.cihai.search");
            for (Method m : cihaiSearch.getDeclaredMethods()) {
                if (java.lang.reflect.Modifier.isPublic(m.getModifiers())) {
                    final String methodName = m.getName();
                    final Class<?>[] paramTypes = m.getParameterTypes();
                    Pine.hook(m, new MethodHook() {
                        @Override
                        public void beforeCall(Pine.CallFrame callFrame) {
                            StringBuilder sb = new StringBuilder();
                            sb.append("METHOD_CALL | pf.cihai.search.").append(methodName).append("(");
                            if (callFrame.args != null) {
                                for (int i = 0; i < callFrame.args.length; i++) {
                                    if (i > 0) sb.append(", ");
                                    sb.append(summarizeArg(callFrame.args[i]));
                                }
                                // 捕获密钥：search(byte[], String) 的 String 参数
                                for (int i = 0; i < callFrame.args.length; i++) {
                                    if (callFrame.args[i] instanceof String) {
                                        String keyCandidate = (String) callFrame.args[i];
                                        if (keyCandidate.length() >= 16) {
                                            lastCapturedDecryptKey = keyCandidate;
                                            Log.i(TAG, "Captured decrypt key from pf.cihai.search: " + keyCandidate);
                                        }
                                    }
                                }
                            }
                            sb.append(")");
                            addProbeLog(sb.toString());
                        }

                        @Override
                        public void afterCall(Pine.CallFrame callFrame) {
                            Object result = callFrame.getResult();
                            if (result != null) {
                                addProbeLog("  → return " + summarizeArg(result));
                            }
                        }
                    });
                }
            }
            // 保存 search(byte[], String) 方法引用，供 RPC 解密调用
            for (Method m : cihaiSearch.getDeclaredMethods()) {
                Class<?>[] pt = m.getParameterTypes();
                if (pt.length == 2 && pt[0] == byte[].class && pt[1] == String.class) {
                    m.setAccessible(true);
                    pfCihaiSearchDecryptMethod = m;
                    Log.i(PROBE_TAG, "Saved pf.cihai.search decrypt method: " + m.getName() + "(byte[], String)");
                    break;
                }
            }
            Log.i(PROBE_TAG, "Hooked pf.cihai.search methods (with key capture)");
        } catch (ClassNotFoundException ignored) {
        } catch (Throwable e) {
            Log.e(PROBE_TAG, "Failed to hook pf.cihai.search", e);
        }

        // Hook com.qidian.QDReader.component.bll.v（章节内容处理）
        try {
            Class<?> bllV = cl.loadClass("com.qidian.QDReader.component.bll.v");
            // 使用 getMethods() 获取所有公共方法，包括继承的方法
            for (Method m : bllV.getMethods()) {
                final String methodName = m.getName();
                // 跳过 Object 类的方法
                if ("toString".equals(methodName) || "hashCode".equals(methodName) ||
                    "equals".equals(methodName) || "getClass".equals(methodName) ||
                    "notify".equals(methodName) || "notifyAll".equals(methodName) ||
                    "wait".equals(methodName)) {
                    continue;
                }

                Pine.hook(m, new MethodHook() {
                    @Override
                    public void beforeCall(Pine.CallFrame callFrame) {
                        // 保存 bll.v 实例
                        if (bllVInstance == null) {
                            bllVInstance = callFrame.thisObject;
                            Log.i(TAG, "Saved bll.v instance: " + bllVInstance.getClass().getName());
                        }

                        // 保存 ChapterItem 对象（第二个参数）和 v 对象（第三个参数）
                        if (callFrame.args != null && callFrame.args.length >= 2) {
                            Object arg1 = callFrame.args[1];
                            if (arg1 != null) {
                                String argClassName = arg1.getClass().getName();
                                // 识别 ChapterItem 对象
                                if (argClassName.contains("ChapterItem") ||
                                    argClassName.contains("Chapter") ||
                                    argClassName.startsWith("com.qidian.QDReader.component.data")) {

                                    // 提取 chapterId 用于索引
                                    String chapterId = extractChapterId(arg1);
                                    if (chapterId != null) {
                                        chapterItems.put(chapterId, arg1);

                                        // 对于 N 方法，保存第三个参数 v 对象
                                        if ("N".equals(methodName) && callFrame.args.length >= 3) {
                                            Object vObj = callFrame.args[2];
                                            if (vObj != null) {
                                                vObjects.put(chapterId, vObj);
                                                Log.i(TAG, "Saved vObject for " + chapterId + ": " + vObj.getClass().getName());
                                            }
                                        }

                                        Log.i(TAG, "Saved ChapterItem: " + chapterId);
                                    }
                                }
                            }
                        }

                        // 记录方法调用
                        StringBuilder sb = new StringBuilder();
                        sb.append("METHOD_CALL | bll.v.").append(methodName).append("(");
                        if (callFrame.args != null) {
                            for (int i = 0; i < callFrame.args.length; i++) {
                                if (i > 0) sb.append(", ");
                                sb.append(summarizeArg(callFrame.args[i]));
                            }
                        }
                        sb.append(")");
                        addProbeLog(sb.toString());
                        recordMethodCall("com.qidian.QDReader.component.bll.v", methodName, callFrame.args);
                    }

                    @Override
                    public void afterCall(Pine.CallFrame callFrame) {
                        Object result = callFrame.getResult();
                        if (result != null) {
                            String className = result.getClass().getName();
                            String simpleName = result.getClass().getSimpleName();
                            addProbeLog("  → return " + summarizeArg(result) + " [class=" + className + "]");
                            recordMethodReturn("com.qidian.QDReader.component.bll.v", methodName, result);

                            // 当返回 ChapterContentItem 时，自动提取并缓存内容
                            boolean isContentItem = className.contains("ChapterContentItem") || simpleName.contains("ChapterContentItem");
                            if (isContentItem) {
                                addProbeLog("CONTENT_ITEM_DETECTED | class=" + className + " | method=" + methodName);
                                try {
                                    extractAndCacheChapterContent(result, callFrame.args);
                                } catch (Throwable e) {
                                    Log.e(TAG, "Failed to extract ChapterContentItem content", e);
                                    addProbeLog("CONTENT_ITEM_EXTRACT_ERROR | " + e.getMessage());
                                }
                            }
                        }
                    }
                });
            }
            Log.i(PROBE_TAG, "Hooked com.qidian.QDReader.component.bll.v methods");
        } catch (ClassNotFoundException ignored) {
        } catch (Throwable e) {
            Log.e(PROBE_TAG, "Failed to hook bll.v", e);
        }

        // Phase 2: Hook ViewModel 和 Repository 层
        hookViewModelLayer(cl);
        hookRepositoryLayer(cl);
        hookNetworkLayer(cl);

        // Phase 3: 动态探测关键包下的类
        discoverAndHookClasses(cl);
    }

    // ==================== Phase 3: 动态类发现和 Hook ====================

    private static void discoverAndHookClasses(ClassLoader cl) {
        // 通过反射尝试获取已加载的类列表
        // 注意：这在 Android 上比较 tricky，我们尝试几种方法

        // 方法1：尝试从 BaseDexClassLoader 获取 dex 文件中的类
        try {
            // 获取当前 ClassLoader 的 pathList
            Class<?> basePathClassLoader = Class.forName("dalvik.system.BaseDexClassLoader");
            java.lang.reflect.Field pathListField = basePathClassLoader.getDeclaredField("pathList");
            pathListField.setAccessible(true);
            Object pathList = pathListField.get(cl);

            // 获取 dexElements
            Class<?> dexPathListClass = Class.forName("dalvik.system.DexPathList");
            java.lang.reflect.Field dexElementsField = dexPathListClass.getDeclaredField("dexElements");
            dexElementsField.setAccessible(true);
            Object[] dexElements = (Object[]) dexElementsField.get(pathList);

            Log.i(PROBE_TAG, "Found " + dexElements.length + " dex elements");

            // 遍历 dex 元素
            for (Object dexElement : dexElements) {
                try {
                    java.lang.reflect.Field dexField = dexElement.getClass().getDeclaredField("dexFile");
                    dexField.setAccessible(true);
                    Object dexFile = dexField.get(dexElement);

                    if (dexFile != null) {
                        // 获取类名列表
                        Method getClassNamesMethod = dexFile.getClass().getDeclaredMethod("getClassNames");
                        getClassNamesMethod.setAccessible(true);
                        String[] classNames = (String[]) getClassNamesMethod.invoke(dexFile);

                        Log.i(PROBE_TAG, "DexFile has " + classNames.length + " classes");

                        // 过滤并 Hook 相关的类
                        for (String className : classNames) {
                            if (shouldHookClass(className)) {
                                hookClassIfNotHooked(cl, className);
                            }
                        }
                    }
                } catch (Throwable e) {
                    // 继续尝试下一个 dex 元素
                }
            }
        } catch (Throwable e) {
            Log.e(PROBE_TAG, "Failed to discover classes from dex", e);
            // 降级方案：Hook 已知的关键类
            hookKnownClasses(cl);
        }
    }

    private static boolean shouldHookClass(String className) {
        // 只 Hook com.qidian 包下的特定类
        if (!className.startsWith("com.qidian")) {
            return false;
        }

        // 跳过一些明显不需要的类
        if (className.contains("$") ||  // 内部类
            className.contains("BuildConfig") ||
            className.contains("R.") ||  // R 类
            className.contains(".test.") ||
            className.contains(".android.")) {
            return false;
        }

        // 重点 Hook 包含以下关键词的类
        String lower = className.toLowerCase();
        return lower.contains("viewmodel") ||
               lower.contains("repository") ||
               lower.contains("usecase") ||
               lower.contains("interactor") ||
               lower.contains("manager") && !lower.contains("download") ||
               lower.contains("presenter") ||
               lower.contains("bll.") ||
               lower.contains("component.bll") ||
               lower.contains("data.chapter") ||
               lower.contains("data.book") ||
               lower.contains("reader.viewmodel") ||
               lower.contains("bookshelf.viewmodel");
    }

    private static final ConcurrentHashMap<String, Boolean> hookedClasses = new ConcurrentHashMap<>();

    private static void hookClassIfNotHooked(ClassLoader cl, String className) {
        if (hookedClasses.containsKey(className)) {
            return;  // 已 Hook 过
        }

        try {
            Class<?> clazz = cl.loadClass(className);
            int hookedMethods = 0;

            for (Method m : clazz.getDeclaredMethods()) {
                // 只 Hook 公共方法
                if (!java.lang.reflect.Modifier.isPublic(m.getModifiers())) {
                    continue;
                }

                // 跳过 Object 类的方法
                if ("toString".equals(m.getName()) ||
                    "hashCode".equals(m.getName()) ||
                    "equals".equals(m.getName()) ||
                    "getClass".equals(m.getName())) {
                    continue;
                }

                final String fullClassName = className;
                final String methodName = m.getName();
                final Class<?>[] paramTypes = m.getParameterTypes();

                try {
                    Pine.hook(m, new MethodHook() {
                        @Override
                        public void beforeCall(Pine.CallFrame callFrame) {
                            // 记录方法调用
                            String argsStr = formatArgs(callFrame.args);
                            addCallRecord(fullClassName, methodName, argsStr, null);

                            // 对于重要方法，输出详细日志
                            if (isImportantMethod(methodName, paramTypes)) {
                                addProbeLog("CALL | " + shortClassName(fullClassName) + "." + methodName + "(" + argsStr + ")");
                                // 保存为潜在入口
                                saveMethodEntry(fullClassName, methodName, paramTypes, callFrame.thisObject, false);
                            }
                        }

                        @Override
                        public void afterCall(Pine.CallFrame callFrame) {
                            Object result = callFrame.getResult();
                            if (isImportantMethod(methodName, paramTypes)) {
                                String resultStr = summarizeArg(result);
                                addProbeLog("  → " + resultStr);
                                addCallRecord(fullClassName, methodName, null, resultStr);
                            }
                        }
                    });
                    hookedMethods++;
                } catch (Throwable e) {
                    // Hook 单个方法失败，继续尝试其他方法
                }
            }

            if (hookedMethods > 0) {
                hookedClasses.put(className, true);
                Log.i(PROBE_TAG, "Hooked " + hookedMethods + " methods in " + className);
            }
        } catch (Throwable e) {
            // 加载类失败，跳过
        }
    }

    private static void hookKnownClasses(ClassLoader cl) {
        // 降级方案：Hook 已知的关键类
        String[] knownClasses = {
            "com.qidian.QDReader.repository.ChapterRepository",
            "com.qidian.QDReader.repository.BookRepository",
            "com.qidian.QDReader.repository.SearchRepository",
            "com.qidian.QDReader.ui.viewmodel.ReaderViewModel",
            "com.qidian.QDReader.ui.viewmodel.BookDetailViewModel",
            "com.qidian.QDReader.ui.viewmodel.SearchViewModel",
            "com.qidian.QDReader.ui.viewmodel.BookShelfViewModel",
            "com.qidian.QDReader.component.bll.v",
            "com.qidian.QDReader.component.bll.ChapterManager",
            "com.qidian.QDReader.component.bll.BookManager"
        };

        for (String className : knownClasses) {
            hookClassIfNotHooked(cl, className);
        }
    }

    private static String shortClassName(String fullClassName) {
        int lastDot = fullClassName.lastIndexOf('.');
        if (lastDot >= 0) {
            return fullClassName.substring(lastDot + 1);
        }
        return fullClassName;
    }

    private static String formatArgs(Object[] args) {
        if (args == null || args.length == 0) return "";
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < Math.min(args.length, 5); i++) {
            if (i > 0) sb.append(", ");
            sb.append(summarizeArg(args[i]));
        }
        if (args.length > 5) sb.append(", ...");
        return sb.toString();
    }

    private static boolean isImportantMethod(String methodName, Class<?>[] paramTypes) {
        // 判断是否是重要的业务方法
        String lower = methodName.toLowerCase();
        return lower.startsWith("load") ||
               lower.startsWith("get") ||
               lower.startsWith("fetch") ||
               lower.startsWith("request") ||
               lower.startsWith("query") ||
               lower.startsWith("search") ||
               lower.startsWith("refresh") ||
               lower.startsWith("read") ||
               (lower.contains("chapter") && !lower.contains("list")) ||
               lower.contains("content");
    }

    private static void addCallRecord(String className, String methodName, String args, String returnValue) {
        synchronized (callRecords) {
            callRecords.addLast(new MethodCallRecord(className, methodName, args, returnValue));
            while (callRecords.size() > MAX_CALL_RECORDS) {
                callRecords.removeFirst();
            }
        }
    }

    private static void recordMethodCall(String className, String methodName, Object[] args) {
        synchronized (callRecords) {
            callRecords.addLast(new MethodCallRecord(className, methodName, formatArgs(args), null));
            while (callRecords.size() > MAX_CALL_RECORDS) {
                callRecords.removeFirst();
            }
        }
    }

    private static void recordMethodReturn(String className, String methodName, Object result) {
        // 查找最近的调用记录并更新返回值
        synchronized (callRecords) {
            for (int i = callRecords.size() - 1; i >= 0; i--) {
                MethodCallRecord record = callRecords.get(i);
                if (record.className.equals(className) && record.methodName.equals(methodName) && record.returnValue == null) {
                    record.returnValue = summarizeArg(result);
                    break;
                }
            }
        }
    }

    private static void saveMethodEntry(String className, String methodName, Class<?>[] paramTypes, Object instance, boolean isStatic) {
        String key = className + "." + methodName;
        knownEntries.put(key, new MethodEntry(className, methodName, paramTypes, instance, isStatic));
    }

    // ==================== Phase 2: 业务层 Hook ====================

    private static void hookViewModelLayer(ClassLoader cl) {
        // 根据方案文档中的关键类，Hook ViewModel 层
        String[] viewModelClasses = {
                // BookDetailViewModel - 书籍详情
                "com.qidian.QDReader.ui.activity.b",
                // ReaderViewModel - 阅读器
                "com.qidian.QDReader.ui.activity.c",
                // SearchViewModel - 搜索
                "com.qidian.QDReader.ui.viewmodel.a"
        };

        for (String className : viewModelClasses) {
            try {
                Class<?> vmClass = cl.loadClass(className);
                for (Method m : vmClass.getDeclaredMethods()) {
                    if (java.lang.reflect.Modifier.isPublic(m.getModifiers())) {
                        final String fullClassName = className;
                        final String methodName = m.getName();
                        Pine.hook(m, new MethodHook() {
                            @Override
                            public void beforeCall(Pine.CallFrame callFrame) {
                                // 只记录重要的方法（包含 load, get, set, request 等关键词）
                                if (methodName.matches("(?i)(load|get|set|request|fetch|query|search).*")) {
                                    StringBuilder sb = new StringBuilder();
                                    sb.append("VIEWMODEL | ").append(fullClassName).append(".").append(methodName).append("(");
                                    if (callFrame.args != null && callFrame.args.length > 0) {
                                        for (int i = 0; i < Math.min(3, callFrame.args.length); i++) {
                                            if (i > 0) sb.append(", ");
                                            sb.append(summarizeArg(callFrame.args[i]));
                                        }
                                        if (callFrame.args.length > 3) sb.append(", ...");
                                    }
                                    sb.append(")");
                                    addProbeLog(sb.toString());
                                }
                            }

                            @Override
                            public void afterCall(Pine.CallFrame callFrame) {
                                Object result = callFrame.getResult();
                                if (result != null && methodName.matches("(?i)(load|get|request|fetch|query).*")) {
                                    String resultStr = summarizeArg(result);
                                    if (resultStr.length() < 200) {  // 只记录简短的返回值
                                        addProbeLog("  → " + resultStr);
                                    }
                                }
                            }
                        });
                    }
                }
                Log.i(PROBE_TAG, "Hooked ViewModel: " + className);
            } catch (ClassNotFoundException ignored) {
            } catch (Throwable e) {
                Log.e(PROBE_TAG, "Failed to hook ViewModel: " + className, e);
            }
        }
    }

    private static void hookRepositoryLayer(ClassLoader cl) {
        // Hook Repository 层（数据访问层）
        String[] repositoryPatterns = {
                "com.qidian.QDReader.repository",
                "com.qidian.QDReader.data.repository"
        };

        // 由于无法动态遍历包，我们 Hook 一些已知的 Repository 类
        String[] repositoryClasses = {
                // 章节相关 Repository
                "com.qidian.QDReader.repository.ChapterRepository",
                "com.qidian.QDReader.repository.a",
                // 书籍相关 Repository
                "com.qidian.QDReader.repository.BookRepository",
                "com.qidian.QDReader.repository.b"
        };

        for (String className : repositoryClasses) {
            try {
                Class<?> repoClass = cl.loadClass(className);
                for (Method m : repoClass.getDeclaredMethods()) {
                    if (java.lang.reflect.Modifier.isPublic(m.getModifiers())) {
                        final String fullClassName = className;
                        final String methodName = m.getName();
                        Pine.hook(m, new MethodHook() {
                            @Override
                            public void beforeCall(Pine.CallFrame callFrame) {
                                if (methodName.matches("(?i)(get|load|request|fetch|query|search).*")) {
                                    StringBuilder sb = new StringBuilder();
                                    sb.append("REPOSITORY | ").append(fullClassName).append(".").append(methodName).append("(");
                                    if (callFrame.args != null && callFrame.args.length > 0) {
                                        for (int i = 0; i < Math.min(3, callFrame.args.length); i++) {
                                            if (i > 0) sb.append(", ");
                                            sb.append(summarizeArg(callFrame.args[i]));
                                        }
                                        if (callFrame.args.length > 3) sb.append(", ...");
                                    }
                                    sb.append(")");
                                    addProbeLog(sb.toString());
                                }
                            }

                            @Override
                            public void afterCall(Pine.CallFrame callFrame) {
                                // 记录返回值
                                Object result = callFrame.getResult();
                                if (result != null) {
                                    String resultStr = summarizeArg(result);
                                    if (resultStr.length() < 300) {
                                        addProbeLog("  → " + resultStr);
                                    }
                                }
                            }
                        });
                    }
                }
                Log.i(PROBE_TAG, "Hooked Repository: " + className);
            } catch (ClassNotFoundException ignored) {
            } catch (Throwable e) {
                Log.e(PROBE_TAG, "Failed to hook Repository: " + className, e);
            }
        }
    }

    private static void hookNetworkLayer(ClassLoader cl) {
        // Hook 网络请求接口，找到发起网络调用的入口
        // OkHttp Call 相关类
        try {
            Class<?> callClass = cl.loadClass("okhttp3.Call");
            Class<?> realCallClass = cl.loadClass("okhttp3.RealCall");

            // Hook RealCall.execute() 和 enqueue()
            for (Method m : realCallClass.getDeclaredMethods()) {
                String methodName = m.getName();
                if ("execute".equals(methodName) || "enqueue".equals(methodName)) {
                    Pine.hook(m, new MethodHook() {
                        @Override
                        public void afterCall(Pine.CallFrame callFrame) {
                            // 对 execute() 的返回值（Response），捕获 bookcontent 响应
                            if (!"execute".equals(methodName)) return;
                            try {
                                Object request = null;
                                try {
                                    java.lang.reflect.Field rf = realCallClass.getDeclaredField("originalRequest");
                                    rf.setAccessible(true);
                                    request = rf.get(callFrame.thisObject);
                                } catch (Throwable ignored) {}
                                if (request == null) return;
                                Method urlMethod = request.getClass().getMethod("url");
                                String urlStr = String.valueOf(urlMethod.invoke(request));
                                if (!urlStr.contains("bookcontent")) return;

                                Object response = callFrame.getResult();
                                if (response == null) return;

                                int code = (int) response.getClass().getDeclaredMethod("code").invoke(response);
                                // 使用 peekBody 读响应（不消耗原始 body）
                                Method peekBodyMethod = response.getClass().getDeclaredMethod("peekBody", long.class);
                                Object peekBody = peekBodyMethod.invoke(response, 32768L);
                                String bodyStr = "";
                                if (peekBody != null) {
                                    Method sourceMethod = peekBody.getClass().getMethod("source");
                                    Object source = sourceMethod.invoke(peekBody);
                                    if (source != null) {
                                        bodyStr = (String) source.getClass().getMethod("readUtf8").invoke(source);
                                    }
                                    try { peekBody.getClass().getMethod("close").invoke(peekBody); } catch (Throwable ignored) {}
                                }

                                StringBuilder sb = new StringBuilder();
                                sb.append("BOOKCONTENT_RESP | code=").append(code).append(" | url=").append(urlStr);
                                if (bodyStr.isEmpty()) {
                                    sb.append(" | empty body");
                                } else if (looksLikeBinary(bodyStr)) {
                                    sb.append(" | encrypted_binary, len=").append(bodyStr.length());
                                } else {
                                    sb.append(" | body=").append(truncate(bodyStr, 4000));
                                }
                                addProbeLog(sb.toString());
                            } catch (Throwable e) {
                                addProbeLog("BOOKCONTENT_RESP_ERR | " + e.getClass().getSimpleName() + ": " + e.getMessage());
                            }
                        }

                        @Override
                        public void beforeCall(Pine.CallFrame callFrame) {
                            // 获取 Request 对象
                            Object request = null;
                            try {
                                // RealCall 有一个 originalRequest 字段
                                java.lang.reflect.Field requestField = realCallClass.getDeclaredField("originalRequest");
                                requestField.setAccessible(true);
                                request = requestField.get(callFrame.thisObject);
                            } catch (Throwable ignored) {}

                            if (request != null) {
                                try {
                                    Method urlMethod = request.getClass().getMethod("url");
                                    Object url = urlMethod.invoke(request);
                                    String urlStr = url != null ? url.toString() : "";
                                    addProbeLog("NETWORK_CALL | " + methodName + " | " + urlStr);

                                    // 对 bookcontent/subscription 相关请求，额外记录 HTTP 方法和请求体
                                    if (urlStr.contains("bookcontent") || urlStr.contains("subscription")) {
                                        try {
                                            Method httpMethodMethod = request.getClass().getDeclaredMethod("method");
                                            String httpMethod = (String) httpMethodMethod.invoke(request);
                                            StringBuilder detail = new StringBuilder();
                                            detail.append("BOOKCONTENT_DETAIL | method=").append(httpMethod).append(" | url=").append(urlStr);
                                            // 捕获请求体
                                            Method bodyMethod = request.getClass().getDeclaredMethod("body");
                                            Object body = bodyMethod.invoke(request);
                                            if (body != null) {
                                                String bodyStr = readRequestBody(body);
                                                if (bodyStr != null) {
                                                    detail.append(" | body=").append(bodyStr);
                                                }
                                            }
                                            addProbeLog(detail.toString());
                                        } catch (Throwable ex) {
                                            addProbeLog("BOOKCONTENT_DETAIL_ERR | " + ex.getMessage());
                                        }
                                    }

                                    // 从起点 API 请求捕获 OkHttpClient（带签名拦截器）
                                    // 优先使用 druidv6 API 请求的 client（必然带签名拦截器）
                                    if (urlStr.contains("druidv6.if.qidian.com/argus") && !apiClientCaptured) {
                                        try {
                                            Object realCall = callFrame.thisObject;
                                            java.lang.reflect.Field clientField = null;
                                            for (java.lang.reflect.Field f : realCall.getClass().getDeclaredFields()) {
                                                if (f.getType().getName().contains("OkHttpClient")) {
                                                    clientField = f;
                                                    break;
                                                }
                                            }
                                            if (clientField != null) {
                                                clientField.setAccessible(true);
                                                Object apiClient = clientField.get(realCall);
                                                if (apiClient != null) {
                                                    savedOkHttpClient = apiClient;
                                                    apiClientCaptured = true;
                                                    Log.i(TAG, "OkHttpClient captured from druidv6 API: " + urlStr.substring(0, Math.min(urlStr.length(), 80)));
                                                }
                                            }
                                        } catch (Throwable ignored2) {
                                            Log.w(TAG, "Failed to extract client from RealCall: " + ignored2.getMessage());
                                        }
                                    }

                                    // 捕获搜索请求模板（如果是 searchbooks 请求）
                                    if (urlStr.contains("searchbooks")) {
                                        searchUrlTemplate = urlStr;
                                        try {
                                            Method httpMethodMethod = request.getClass().getDeclaredMethod("method");
                                            searchHttpMethod = (String) httpMethodMethod.invoke(request);
                                        } catch (Throwable ignored2) {
                                            searchHttpMethod = "GET";
                                        }
                                        try {
                                            Method bodyMethod = request.getClass().getDeclaredMethod("body");
                                            Object body = bodyMethod.invoke(request);
                                            if (body != null) {
                                                searchBodyTemplate = readRequestBody(body);
                                            }
                                        } catch (Throwable ignored2) {}
                                        // 如果之前捕获的 client 不对，从 searchbooks 请求再更新一次
                                        try {
                                            Object realCall = callFrame.thisObject;
                                            java.lang.reflect.Field clientField = null;
                                            for (java.lang.reflect.Field f : realCall.getClass().getDeclaredFields()) {
                                                if (f.getType().getName().contains("OkHttpClient")) {
                                                    clientField = f;
                                                    break;
                                                }
                                            }
                                            if (clientField != null) {
                                                clientField.setAccessible(true);
                                                Object apiClient = clientField.get(realCall);
                                                if (apiClient != null) {
                                                    savedOkHttpClient = apiClient;
                                                }
                                            }
                                        } catch (Throwable ignored2) {}
                                        Log.i(TAG, "Search template captured via RealCall: " + urlStr);
                                    }
                                } catch (Throwable ignored) {}
                            }
                        }
                    });
                }
            }
            Log.i(PROBE_TAG, "Hooked OkHttp network layer");
        } catch (ClassNotFoundException ignored) {
        } catch (Throwable e) {
            Log.e(PROBE_TAG, "Failed to hook network layer", e);
        }
    }

    private static String summarizeArg(Object arg) {
        if (arg == null) return "null";
        if (arg instanceof String) {
            String s = (String) arg;
            if (s.length() > 100) {
                return "\"" + s.substring(0, 100) + "...(" + s.length() + ")\"";
            }
            return "\"" + s + "\"";
        }
        if (arg instanceof byte[]) {
            byte[] b = (byte[]) arg;
            return "byte[" + b.length + "]";
        }
        if (arg instanceof Number || arg instanceof Boolean) {
            return arg.toString();
        }
        return arg.getClass().getSimpleName() + "@" + Integer.toHexString(System.identityHashCode(arg));
    }

    private static void addProbeLog(String message) {
        String logLine = System.currentTimeMillis() + " | " + message;
        logRemote(PROBE_TAG, message);
        synchronized (probeLogs) {
            probeLogs.addLast(logLine);
            while (probeLogs.size() > MAX_PROBE_LOGS) {
                probeLogs.removeFirst();
            }
        }
    }

    // ==================== Phase 1: LocalSocket RPC ====================

    private static void startRpcServer() {
        Log.i(TAG, "startRpcServer called, rpcRunning=" + rpcRunning);
        if (rpcRunning) return;
        rpcRunning = true;

        new Thread(() -> {
            try {
                Log.i(TAG, "RPC thread starting...");
                // 先尝试关闭旧的 socket
                try {
                    if (serverSocket != null) {
                        serverSocket.close();
                        serverSocket = null;
                    }
                } catch (Throwable ignored) {}

                serverSocket = new LocalServerSocket(SOCKET_NAME);
                Log.i(TAG, "RPC server started on " + SOCKET_NAME);

                while (rpcRunning) {
                    try {
                        LocalSocket client = serverSocket.accept();
                        handleClient(client);
                    } catch (Throwable e) {
                        if (rpcRunning) {
                            Log.e(TAG, "RPC accept error", e);
                        }
                    }
                }
            } catch (Throwable e) {
                Log.e(TAG, "Failed to start RPC server", e);
                rpcRunning = false;
            }
        }, "QDHook-RPC").start();
    }

    private static void handleClient(LocalSocket client) {
        new Thread(() -> {
            try {
                InputStream is = client.getInputStream();
                OutputStream os = client.getOutputStream();
                BufferedReader reader = new BufferedReader(new InputStreamReader(is, StandardCharsets.UTF_8));

                String line;
                while ((line = reader.readLine()) != null) {
                    String response = handleRpcCommand(line);
                    // 确保响应是紧凑 JSON（无换行符），这样客户端可以用 \n 作为消息分隔符
                    try {
                        JSONObject compactJson = new JSONObject(response);
                        response = compactJson.toString();
                    } catch (Throwable ignored) {
                        // 不是 JSON，保持原样但去除换行
                        response = response.replace("\n", " ").replace("\r", "");
                    }
                    os.write((response + "\n").getBytes(StandardCharsets.UTF_8));
                    os.flush();
                }
            } catch (Throwable e) {
                Log.e(TAG, "RPC client error", e);
            } finally {
                try {
                    client.close();
                } catch (Throwable ignored) {}
            }
        }, "QDHook-RPC-Client").start();
    }

    private static String handleRpcCommand(String jsonStr) {
        try {
            JSONObject request = new JSONObject(jsonStr);
            String cmd = request.optString("cmd", "");

            switch (cmd) {
                case "ping":
                    return new JSONObject()
                            .put("success", true)
                            .put("data", "pong")
                            .put("timestamp", System.currentTimeMillis())
                            .toString();

                case "testChinese":
                    // 测试中文编码
                    String testContent = "这是一段测试中文内容，包含：简体中文、繁體中文、123数字、标点符号！";
                    String resultStr = new JSONObject()
                            .put("success", true)
                            .put("testText", testContent)
                            .put("chineseChars", "测试编码")
                            .put("mixed", "中文ABC123混合")
                            .toString(1);
                    // 记录日志以便验证
                    Log.i(TAG, "testChinese response: " + resultStr);
                    return resultStr.replace("\\/", "/");

                case "getRecentDecrypted":
                    int limit = request.optInt("limit", 10);
                    return handleGetRecentDecrypted(limit);

                case "getLogs":
                    int logLimit = request.optInt("limit", 200);
                    long since = request.optLong("since", 0);
                    String tagFilter = request.optString("tag", "");
                    return handleGetLogs(logLimit, since, tagFilter);

                case "dumpChapterItem": {
                    // 调试：dump 保存的 ChapterItem 字段
                    JSONObject dumpResult = new JSONObject();
                    dumpResult.put("savedCount", chapterItems.size());
                    dumpResult.put("bllVInstance", bllVInstance != null ? bllVInstance.getClass().getName() : "null");
                    JSONArray itemsList = new JSONArray();
                    for (java.util.Map.Entry<String, Object> entry : chapterItems.entrySet()) {
                        JSONObject itemInfo = new JSONObject();
                        itemInfo.put("chapterId", entry.getKey());
                        Object ci = entry.getValue();
                        itemInfo.put("className", ci.getClass().getName());
                        JSONObject fields = new JSONObject();
                        for (java.lang.reflect.Field f : ci.getClass().getDeclaredFields()) {
                            f.setAccessible(true);
                            Object val = f.get(ci);
                            if (val == null) {
                                fields.put(f.getName(), JSONObject.NULL);
                            } else if (val instanceof String) {
                                String s = (String) val;
                                fields.put(f.getName(), s.length() > 100 ? s.substring(0, 100) + "..." : s);
                            } else if (val instanceof Number || val instanceof Boolean) {
                                fields.put(f.getName(), val);
                            } else if (val instanceof byte[]) {
                                fields.put(f.getName(), "byte[" + ((byte[]) val).length + "]");
                            } else {
                                fields.put(f.getName(), val.getClass().getSimpleName() + "@" + Integer.toHexString(System.identityHashCode(val)));
                            }
                        }
                        itemInfo.put("fields", fields);
                        itemsList.put(itemInfo);
                        if (itemsList.length() >= 3) break;
                    }
                    dumpResult.put("items", itemsList);
                    dumpResult.put("success", true);
                    return dumpResult.toString(1);
                }

                case "getStatus":
                    return handleGetStatus();

                case "getCallRecords":
                    int callLimit = request.optInt("limit", 50);
                    return handleGetCallRecords(callLimit);

                case "getEntries":
                    return handleGetEntries();

                case "clearRecords":
                    synchronized (callRecords) {
                        callRecords.clear();
                    }
                    return new JSONObject()
                            .put("success", true)
                            .put("message", "Call records cleared")
                            .toString();

                case "fetchChapter":
                    // Phase 2: 主动获取章节内容
                    String bookId = request.optString("bookId");
                    String chapterId = request.optString("chapterId");
                    if (bookId == null || chapterId == null) {
                        return new JSONObject()
                                .put("success", false)
                                .put("error", "Missing bookId or chapterId")
                                .toString();
                    }
                    return handleFetchChapter(bookId, chapterId);

                case "testFetch":
                    // 测试命令：获取一个已知的章节
                    return handleFetchChapter("1209977", "23373921");

                case "search":
                    String keyword = request.optString("keyword", "");
                    if (keyword.isEmpty()) {
                        return new JSONObject()
                                .put("success", false)
                                .put("error", "Missing keyword")
                                .toString();
                    }
                    return handleSearch(keyword, request.optInt("page", 1));

                case "bookDetail":
                    String detailBookId = request.optString("bookId", "");
                    if (detailBookId.isEmpty()) {
                        return new JSONObject()
                                .put("success", false)
                                .put("error", "Missing bookId")
                                .toString();
                    }
                    return handleBookDetail(detailBookId);

                case "getSearchTemplate":
                    return new JSONObject()
                            .put("success", true)
                            .put("data", new JSONObject()
                                    .put("hasClient", savedOkHttpClient != null)
                                    .put("hasTemplate", searchUrlTemplate != null)
                                    .put("urlTemplate", searchUrlTemplate != null ? searchUrlTemplate : "not captured yet")
                                    .put("httpMethod", searchHttpMethod != null ? searchHttpMethod : "unknown")
                                    .put("bodyTemplate", searchBodyTemplate != null ? searchBodyTemplate : ""))
                            .toString();

                case "getChapterList": {
                    String clBookId = request.optString("bookId", "");
                    if (clBookId.isEmpty()) {
                        return new JSONObject()
                                .put("success", false)
                                .put("error", "Missing bookId")
                                .toString();
                    }
                    return handleGetChapterList(clBookId);
                }

                case "probeBllV": {
                    return handleProbeBllV();
                }

                case "testNoTemplate": {
                    // 测试完全免 UI 路径：强制不使用 chapterItems 模板
                    String ntBookId = request.optString("bookId", "");
                    String ntChapterId = request.optString("chapterId", "");
                    if (ntBookId.isEmpty() || ntChapterId.isEmpty()) {
                        return new JSONObject().put("success", false).put("error", "Missing bookId or chapterId").toString();
                    }
                    return handleTestNoTemplate(ntBookId, ntChapterId);
                }

                case "getChapterContent": {
                    String ccBookId = request.optString("bookId", "");
                    String ccChapterId = request.optString("chapterId", "");
                    if (ccBookId.isEmpty() || ccChapterId.isEmpty()) {
                        return new JSONObject()
                                .put("success", false)
                                .put("error", "Missing bookId or chapterId")
                                .toString();
                    }
                    return handleGetChapterContent(ccBookId, ccChapterId);
                }

                case "getVipContent": {
                    String vcBookId = request.optString("bookId", "");
                    String vcChapterId = request.optString("chapterId", "");
                    if (vcBookId.isEmpty() || vcChapterId.isEmpty()) {
                        return new JSONObject()
                                .put("success", false)
                                .put("error", "Missing bookId or chapterId")
                                .toString();
                    }
                    return handleGetVipContent(vcBookId, vcChapterId);
                }

                case "getChapterReviewSummary": {
                    String rsBookId = request.optString("bookId", "");
                    String rsChapterId = request.optString("chapterId", "");
                    if (rsBookId.isEmpty() || rsChapterId.isEmpty()) {
                        return new JSONObject()
                                .put("success", false)
                                .put("error", "Missing bookId or chapterId")
                                .toString();
                    }
                    return handleGetChapterReviewSummary(rsBookId, rsChapterId);
                }

                case "getCommentSummary": {
                    String csBookId = request.optString("bookId", "");
                    String csChapterId = request.optString("chapterId", "");
                    if (csBookId.isEmpty() || csChapterId.isEmpty()) {
                        return new JSONObject()
                                .put("success", false)
                                .put("error", "Missing bookId or chapterId")
                                .toString();
                    }
                    return handleGetCommentSummary(csBookId, csChapterId);
                }

                case "getParagraphComments": {
                    String pcBookId = request.optString("bookId", "");
                    String pcChapterId = request.optString("chapterId", "");
                    int pcParagraphId = request.optInt("paragraphId", Integer.MIN_VALUE);
                    if (pcBookId.isEmpty() || pcChapterId.isEmpty() || pcParagraphId == Integer.MIN_VALUE) {
                        return new JSONObject()
                                .put("success", false)
                                .put("error", "Missing bookId, chapterId, or paragraphId")
                                .toString();
                    }
                    int pcPage = request.optInt("page", 1);
                    int pcPageSize = request.optInt("pageSize", 20);
                    int pcType = request.optInt("type", 0);
                    return handleGetParagraphComments(pcBookId, pcChapterId, pcParagraphId, pcPage, pcPageSize, pcType);
                }

                case "getAllParagraphComments": {
                    String apBookId = request.optString("bookId", "");
                    String apChapterId = request.optString("chapterId", "");
                    if (apBookId.isEmpty() || apChapterId.isEmpty()) {
                        return new JSONObject()
                                .put("success", false)
                                .put("error", "Missing bookId or chapterId")
                                .toString();
                    }
                    int apPageSize = request.optInt("pageSize", 20);
                    int apType = request.optInt("type", 0);
                    boolean apIncludeChapterComments = request.optBoolean("includeChapterComments", false);
                    boolean apFetchReplies = request.optBoolean("fetchReplies", true);
                    JSONArray apOnlyParagraphs = request.optJSONArray("onlyParagraphs");
                    JSONObject apExistingReplies = request.optJSONObject("existingReplies");
                    return handleGetAllParagraphComments(apBookId, apChapterId, apPageSize, apType, apIncludeChapterComments, apFetchReplies, apOnlyParagraphs, apExistingReplies);
                }

                case "executePost": {
                    // 通用 POST 请求（FormBody）
                    String postUrl = request.optString("url", "");
                    JSONObject postParams = request.optJSONObject("params");
                    if (postUrl.isEmpty()) {
                        return new JSONObject().put("success", false).put("error", "Missing url").toString();
                    }
                    return handleExecutePost(postUrl, postParams);
                }

                case "executeGet": {
                    // 通用 GET 请求
                    String getUrl = request.optString("url", "");
                    if (getUrl.isEmpty()) {
                        return new JSONObject().put("success", false).put("error", "Missing url").toString();
                    }
                    return handleExecuteGet(getUrl);
                }

                default:
                    return new JSONObject()
                            .put("success", false)
                            .put("error", "Unknown command: " + cmd)
                            .toString();
            }
        } catch (Throwable e) {
            try {
                return new JSONObject()
                        .put("success", false)
                        .put("error", e.getMessage())
                        .toString();
            } catch (Throwable e2) {
                return "{\"success\":false,\"error\":\"JSON error\"}";
            }
        }
    }

    private static String handleGetRecentDecrypted(int limit) throws Exception {
        JSONArray arr = new JSONArray();
        synchronized (recentCaptured) {
            int count = 0;
            Iterator<CapturedContent> it = recentCaptured.descendingIterator();
            while (it.hasNext() && count < limit) {
                CapturedContent c = it.next();
                arr.put(new JSONObject()
                        .put("plaintext", c.plaintext)
                        .put("algorithm", c.algorithm)
                        .put("keyHex", c.keyHex)
                        .put("timestamp", c.timestamp));
                count++;
            }
        }
        // 使用 toString(1) 避免转义中文字符为 Unicode 格式
        String result = new JSONObject()
                .put("success", true)
                .put("data", arr)
                .toString(1);
        // 移除不必要的斜杠转义
        return result.replace("\\/", "/");
    }

    private static String handleGetLogs(int limit, long since, String tagFilter) throws Exception {
        long maxTimestamp = 0;
        int total = 0;

        try {
            // 拷贝快照
            ArrayList<String> snapshot = new ArrayList<>();
            synchronized (logBuffer) {
                total = logBuffer.size();
                snapshot.addAll(logBuffer);
            }

            // 从尾部向前查找匹配项，收集到临时列表
            ArrayList<JSONObject> matched = new ArrayList<>();
            for (int i = snapshot.size() - 1; i >= 0 && matched.size() < limit; i--) {
                String raw = snapshot.get(i);
                try {
                    JSONObject obj = new JSONObject(raw);
                    long ts = obj.optLong("timestamp", 0);
                    if (since > 0 && ts <= since) break;
                    if (!tagFilter.isEmpty()) {
                        String t = obj.optString("tag", "") + " " + obj.optString("message", "");
                        if (!t.toLowerCase().contains(tagFilter.toLowerCase())) continue;
                    }
                    matched.add(obj);
                    if (ts > maxTimestamp) maxTimestamp = ts;
                } catch (Throwable ignored) {}
            }

            // 反转为时间升序（最旧在前）
            JSONArray arr = new JSONArray();
            for (int i = matched.size() - 1; i >= 0; i--) {
                arr.put(matched.get(i));
            }

            return new JSONObject()
                    .put("success", true)
                    .put("count", arr.length())
                    .put("total", total)
                    .put("maxTimestamp", maxTimestamp)
                    .put("data", arr)
                    .toString();
        } catch (Throwable e) {
            Log.e(TAG, "handleGetLogs error: " + e.getMessage());
            return new JSONObject()
                    .put("success", false)
                    .put("error", "handleGetLogs: " + e.getMessage())
                    .toString();
        }
    }

    private static String handleGetStatus() throws Exception {
        return new JSONObject()
                .put("success", true)
                .put("data", new JSONObject()
                        .put("hooksInstalled", hooksInstalled)
                        .put("rpcRunning", rpcRunning)
                        .put("capturedCount", recentCaptured.size())
                        .put("logBufferCount", logBuffer.size())
                        .put("probeLogCount", probeLogs.size())
                        .put("callRecordCount", callRecords.size())
                        .put("trackedCiphersCount", trackedCiphers.size())
                        .put("hookedClassesCount", hookedClasses.size())
                        .put("knownEntriesCount", knownEntries.size())
                        .put("searchRpc", new JSONObject()
                                .put("okHttpClientSaved", savedOkHttpClient != null)
                                .put("searchTemplateCaptured", searchUrlTemplate != null)
                                .put("searchMethod", searchHttpMethod != null ? searchHttpMethod : "not captured")))
                .toString();
    }

    private static String handleGetCallRecords(int limit) throws Exception {
        JSONArray arr = new JSONArray();
        synchronized (callRecords) {
            int count = 0;
            for (MethodCallRecord record : callRecords) {
                arr.put(new JSONObject()
                        .put("className", record.className)
                        .put("methodName", record.methodName)
                        .put("args", record.args != null ? record.args : "")
                        .put("returnValue", record.returnValue)
                        .put("timestamp", record.timestamp)
                        .put("thread", record.threadName));
                if (++count >= limit) break;
            }
        }
        return new JSONObject()
                .put("success", true)
                .put("data", arr)
                .toString();
    }

    private static String handleGetEntries() throws Exception {
        JSONArray arr = new JSONArray();
        for (String key : knownEntries.keySet()) {
            MethodEntry entry = knownEntries.get(key);
            arr.put(new JSONObject()
                    .put("key", key)
                    .put("className", entry.className)
                    .put("methodName", entry.methodName)
                    .put("hasInstance", entry.instance != null)
                    .put("isStatic", entry.isStatic));
        }
        return new JSONObject()
                .put("success", true)
                .put("data", arr)
                .toString();
    }

    // 在 Cipher Hook 中保存捕获的内容
    private static void saveCapturedContent(String plaintext, String algorithm, String keyHex) {
        synchronized (recentCaptured) {
            recentCaptured.addLast(new CapturedContent(plaintext, algorithm, keyHex));
            while (recentCaptured.size() > MAX_CAPTURED) {
                recentCaptured.removeFirst();
            }
        }
    }

    /**
     * 通过 bll.v.N() 加载章节（与 App 完全相同的路径）
     * 优先使用已捕获的 v 对象和 ChapterItem
     */
    private static String tryFetchViaBllVN(String bookId, String chapterId) {
        try {
            // 如果 bllVInstance 为空，尝试创建
            if (bllVInstance == null) {
                Log.i(TAG, "tryFetchViaBllVN: bllVInstance is null, trying to create...");
                bllVInstance = createBllVInstance(Long.parseLong(bookId));
                if (bllVInstance == null) {
                    Log.i(TAG, "tryFetchViaBllVN: failed to create bllVInstance");
                    return null;
                }
                Log.i(TAG, "tryFetchViaBllVN: created bllVInstance: " + bllVInstance.getClass().getName());
            }

            // 清空捕获缓冲区
            synchronized (recentCaptured) {
                recentCaptured.clear();
            }

            Class<?> bllVClass = bllVInstance.getClass();
            long bookIdLong = Long.parseLong(bookId);

            // 查找 N 方法 (支持 5 或 6 个参数版本)
            // 5参数: long, ChapterItem, v, boolean, Object
            // 6参数: long, ChapterItem, v, boolean, Object, boolean
            Method methodN = null;
            int paramCount = 0;
            for (Method m : bllVClass.getDeclaredMethods()) {
                int pc = m.getParameterTypes().length;
                if ("N".equals(m.getName()) && (pc == 5 || pc == 6)) {
                    methodN = m;
                    paramCount = pc;
                    break;
                }
            }
            if (methodN == null) {
                Log.i(TAG, "tryFetchViaBllVN: method N(5 or 6 params) not found");
                return null;
            }
            methodN.setAccessible(true);
            Log.i(TAG, "tryFetchViaBllVN: found N method with " + paramCount + " parameters");

            // 获取或构造 ChapterItem
            Object chapterItem = chapterItems.get(chapterId);
            if (chapterItem == null) {
                chapterItem = cloneChapterItemWithNewId(chapterId);
            }
            if (chapterItem == null) {
                chapterItem = constructChapterItemFromApi(bookId, chapterId);
            }
            if (chapterItem == null) {
                Log.i(TAG, "tryFetchViaBllVN: cannot get ChapterItem");
                return null;
            }

            // 获取 v 对象：优先使用当前章节的，否则使用任意已捕获的，最后用 bllVInstance 自身
            Object vObj = vObjects.get(chapterId);
            if (vObj == null && !vObjects.isEmpty()) {
                vObj = vObjects.values().iterator().next();
                Log.i(TAG, "tryFetchViaBllVN: using v object from another chapter as template");
            }
            if (vObj == null) {
                // vObjects 为空时，bllVInstance 本身就是 bll.v 类实例，可作为 v 参数
                vObj = bllVInstance;
                Log.i(TAG, "tryFetchViaBllVN: using bllVInstance as v object");
            }

            Log.i(TAG, "tryFetchViaBllVN: calling bll.v.N(" + bookId + ", ChapterItem, " + vObj.getClass().getSimpleName() + ", true, null" + (paramCount == 6 ? ", true" : "") + ")");

            // 调用 N 方法（根据参数数量传递正确数量的参数）
            Object result;
            if (paramCount == 6) {
                result = methodN.invoke(bllVInstance, bookIdLong, chapterItem, vObj, true, null, true);
            } else {
                result = methodN.invoke(bllVInstance, bookIdLong, chapterItem, vObj, true, null);
            }
            Log.i(TAG, "tryFetchViaBllVN: N returned " + (result != null ? result.getClass().getSimpleName() : "null"));

            // 如果返回 ChapterContentItem，直接提取内容
            if (result != null && result.getClass().getName().contains("ChapterContentItem")) {
                String content = extractContentFromChapterContentItem(result);
                if (content != null && !content.isEmpty()) {
                    return content;
                }
            }

            // 等待 afterCall hook 捕获内容
            for (int retry = 0; retry < 10; retry++) {
                Thread.sleep(500);
                String captured = capturedChapterContents.get(chapterId);
                if (captured != null) {
                    Log.i(TAG, "tryFetchViaBllVN: captured content " + captured.length() + " chars");
                    return captured;
                }
            }

            // 兜底：从 recentCaptured 取
            String latest = getLatestCapturedContent();
            if (latest != null && latest.length() > 100) {
                return latest;
            }

            Log.i(TAG, "tryFetchViaBllVN: no content captured after N call");
            return null;
        } catch (Throwable e) {
            Log.e(TAG, "tryFetchViaBllVN failed: " + e.getMessage(), e);
            return null;
        }
    }

    // ==================== Phase 2: 主动获取章节内容 ====================

    private static volatile ClassLoader appClassLoader = null;  // 保存 App 的 ClassLoader
    private static volatile Class<?> chapterItemClass = null;  // ChapterItem 类

    /** 检查解密后的内容是否有效（非空、非纯空白） */
    private static boolean isContentMeaningful(String content) {
        if (content == null || content.isEmpty()) return false;
        // 去除全角/半角空白后检查是否还有实际内容
        String stripped = content.replaceAll("[\\s\u3000]+", "");
        return stripped.length() > 0;
    }

    private static String handleFetchChapter(String bookId, String chapterId) {
        try {
            Log.i(TAG, "RPC: fetchChapter - bookId=" + bookId + ", chapterId=" + chapterId);

            // ========== 步骤1: 尝试 bll.v.N（本地已有 .qd 文件时直接成功） ==========
            String contentViaN = tryFetchViaBllVN(bookId, chapterId);
            if (isContentMeaningful(contentViaN)) {
                return new JSONObject()
                        .put("success", true)
                        .put("data", new JSONObject()
                                .put("bookId", bookId)
                                .put("chapterId", chapterId)
                                .put("content", contentViaN)
                                .put("method", "bll.v.N"))
                        .toString();
            }

            // ========== 步骤2: 检查 .qd 是否已存在，不存在则下载 ==========
            // 先检查用户路径是否已有 .qd 文件（之前下载过的缓存）
            String existingQdPath = getQdPath(bookId, chapterId);
            if (existingQdPath != null) {
                java.io.File existingQd = new java.io.File(existingQdPath);
                if (existingQd.exists() && existingQd.length() > 500) {
                    addProbeLog("fetchChapter | .qd already exists: " + existingQdPath + " (" + existingQd.length() + " bytes), skip download");
                    // .qd 已存在但 N 没解密成功，直接用 L→K→R
                    capturedChapterContents.remove(chapterId);
                    String content = triggerAppDecrypt(bookId, chapterId, null);
                    if (isContentMeaningful(content)) {
                        return new JSONObject()
                                .put("success", true)
                                .put("data", new JSONObject()
                                        .put("bookId", bookId)
                                        .put("chapterId", chapterId)
                                        .put("content", content)
                                        .put("method", "cached+triggerAppDecrypt"))
                                .toString();
                    }
                }
            }

            if (savedOkHttpClient == null) {
                return new JSONObject()
                        .put("success", false)
                        .put("error", "OkHttpClient not available and bll.v.N failed")
                        .toString();
            }

            Class<?> requestBuilderClass = appClassLoader.loadClass("okhttp3.Request$Builder");
            Class<?> requestClass = appClassLoader.loadClass("okhttp3.Request");
            Method addHeaderMethod = requestBuilderClass.getDeclaredMethod("addHeader", String.class, String.class);

            byte[] encryptedBytes = null;
            String downloadSource = null;
            String safeError = "", vipError = "";

            // ---- 尝试 safegetcontent ----
            String url = SAFE_GET_CONTENT_URL + "?bookId=" + bookId + "&chapterId=" + chapterId;
            Object reqBuilder = requestBuilderClass.newInstance();
            requestBuilderClass.getDeclaredMethod("url", String.class).invoke(reqBuilder, url);
            requestBuilderClass.getDeclaredMethod("get").invoke(reqBuilder);
            Object httpRequest = requestBuilderClass.getDeclaredMethod("build").invoke(reqBuilder);
            Object call = savedOkHttpClient.getClass().getMethod("newCall", requestClass)
                    .invoke(savedOkHttpClient, httpRequest);
            Object response = call.getClass().getMethod("execute").invoke(call);

            int code = (int) response.getClass().getDeclaredMethod("code").invoke(response);
            Object responseBody = response.getClass().getDeclaredMethod("body").invoke(response);
            byte[] safeBytes = (byte[]) responseBody.getClass().getMethod("bytes").invoke(responseBody);

            addProbeLog("fetchChapter | safegetcontent code=" + code + " len=" + safeBytes.length);

            if (code == 200 && safeBytes.length > 500) {
                String preview = new String(safeBytes, 0, Math.min(safeBytes.length, 10), java.nio.charset.StandardCharsets.UTF_8);
                if (!preview.startsWith("{")) {
                    encryptedBytes = safeBytes;
                    downloadSource = "safegetcontent";
                }
            }
            if (encryptedBytes == null && code != 200) {
                try {
                    JSONObject safeJson = new JSONObject(new String(safeBytes, java.nio.charset.StandardCharsets.UTF_8));
                    safeError = safeJson.optString("Message", "HTTP " + code);
                } catch (Throwable ignored) { safeError = "HTTP " + code; }
            }

            // ---- 如果 safegetcontent 没拿到数据，先购买再下载 VIP 内容 ----
            int vipCode = 0;
            byte[] vipBytes = null;
            if (encryptedBytes == null) {
                Log.i(TAG, "fetchChapter: trying buyvipchapter + getvipcontent for " + bookId + "/" + chapterId);
                Class<?> formBodyBuilderClass = appClassLoader.loadClass("okhttp3.FormBody$Builder");
                Method addMethod = formBodyBuilderClass.getDeclaredMethod("add", String.class, String.class);
                Class<?> requestBodyClass = appClassLoader.loadClass("okhttp3.RequestBody");

                // ---- 步骤2a: 调用 buyvipchapter 购买/授权章节 ----
                try {
                    String buyUrl = "https://druidv6.if.qidian.com/argus/api/v3/subscription/buyvipchapter";
                    Object buyFormBuilder = formBodyBuilderClass.newInstance();
                    addMethod.invoke(buyFormBuilder, "bookId", bookId);
                    addMethod.invoke(buyFormBuilder, "consumeType", "0");
                    addMethod.invoke(buyFormBuilder, "sp", "");
                    addMethod.invoke(buyFormBuilder, "type", "3");
                    addMethod.invoke(buyFormBuilder, "confirmType", "0");
                    addMethod.invoke(buyFormBuilder, "chapterlist", chapterId);
                    Object buyFormBody = formBodyBuilderClass.getDeclaredMethod("build").invoke(buyFormBuilder);

                    Object buyReqBuilder = requestBuilderClass.newInstance();
                    requestBuilderClass.getDeclaredMethod("url", String.class).invoke(buyReqBuilder, buyUrl);
                    requestBuilderClass.getDeclaredMethod("post", requestBodyClass).invoke(buyReqBuilder, buyFormBody);
                    Object buyHttpRequest = requestBuilderClass.getDeclaredMethod("build").invoke(buyReqBuilder);

                    Object buyCall = savedOkHttpClient.getClass().getMethod("newCall", requestClass)
                            .invoke(savedOkHttpClient, buyHttpRequest);
                    Object buyResponse = buyCall.getClass().getMethod("execute").invoke(buyCall);

                    int buyCode = (int) buyResponse.getClass().getDeclaredMethod("code").invoke(buyResponse);
                    Object buyResponseBody = buyResponse.getClass().getDeclaredMethod("body").invoke(buyResponse);
                    String buyRespStr = (String) buyResponseBody.getClass().getMethod("string").invoke(buyResponseBody);

                    addProbeLog("fetchChapter | buyvipchapter code=" + buyCode + " response=" + buyRespStr.substring(0, Math.min(buyRespStr.length(), 200)));
                } catch (Throwable buyEx) {
                    addProbeLog("fetchChapter | buyvipchapter failed: " + buyEx.getMessage());
                }

                // ---- 步骤2b: 调用 getvipcontent 下载加密内容 ----
                String vipUrl = "https://druidv6.if.qidian.com/argus/api/v4/bookcontent/getvipcontent";
                Object formBuilder = formBodyBuilderClass.newInstance();
                addMethod.invoke(formBuilder, "b-string", "");
                addMethod.invoke(formBuilder, "b", bookId);
                addMethod.invoke(formBuilder, "c", chapterId);
                addMethod.invoke(formBuilder, "ui", "0");
                Object formBody = formBodyBuilderClass.getDeclaredMethod("build").invoke(formBuilder);

                Object vipReqBuilder = requestBuilderClass.newInstance();
                requestBuilderClass.getDeclaredMethod("url", String.class).invoke(vipReqBuilder, vipUrl);
                requestBuilderClass.getDeclaredMethod("post", requestBodyClass).invoke(vipReqBuilder, formBody);
                Object vipHttpRequest = requestBuilderClass.getDeclaredMethod("build").invoke(vipReqBuilder);

                Object vipCall = savedOkHttpClient.getClass().getMethod("newCall", requestClass)
                        .invoke(savedOkHttpClient, vipHttpRequest);
                Object vipResponse = vipCall.getClass().getMethod("execute").invoke(vipCall);

                vipCode = (int) vipResponse.getClass().getDeclaredMethod("code").invoke(vipResponse);
                Object vipResponseBody = vipResponse.getClass().getDeclaredMethod("body").invoke(vipResponse);
                vipBytes = (byte[]) vipResponseBody.getClass().getMethod("bytes").invoke(vipResponseBody);

                addProbeLog("fetchChapter | getvipcontent code=" + vipCode + " len=" + vipBytes.length);

                if (vipCode == 200 && vipBytes.length > 100) {
                    String vipPreview = new String(vipBytes, 0, Math.min(vipBytes.length, 10), java.nio.charset.StandardCharsets.UTF_8);
                    if (!vipPreview.startsWith("{")) {
                        encryptedBytes = vipBytes;
                        downloadSource = "getvipcontent";
                    } else {
                        // JSON 响应（如 "未购买"）
                        try {
                            JSONObject vipJson = new JSONObject(new String(vipBytes, java.nio.charset.StandardCharsets.UTF_8));
                            vipError = vipJson.optString("Message", "unknown");
                        } catch (Throwable ignored) { vipError = "JSON response"; }
                    }
                } else if (vipCode != 200) {
                    try {
                        JSONObject vipJson = new JSONObject(new String(vipBytes, java.nio.charset.StandardCharsets.UTF_8));
                        vipError = vipJson.optString("Message", "HTTP " + vipCode);
                    } catch (Throwable ignored) { vipError = "HTTP " + vipCode; }
                }
            }

            // 如果没下载到加密数据，尝试 L→K→R（App 自身的下载+解密链路）
            if (encryptedBytes == null) {
                addProbeLog("fetchChapter | API download failed, trying L→K→R fallback");
                capturedChapterContents.remove(chapterId);
                String contentViaLKR = triggerAppDecrypt(bookId, chapterId, null);
                if (isContentMeaningful(contentViaLKR)) {
                    return new JSONObject()
                            .put("success", true)
                            .put("data", new JSONObject()
                                    .put("bookId", bookId)
                                    .put("chapterId", chapterId)
                                    .put("content", contentViaLKR)
                                    .put("method", "LKR-fallback"))
                            .toString();
                }

                // L→K→R 失败后，再试一次 bll.v.N（L 可能已经内部下载了 .qd）
                String contentViaN2 = tryFetchViaBllVN(bookId, chapterId);
                if (isContentMeaningful(contentViaN2)) {
                    return new JSONObject()
                            .put("success", true)
                            .put("data", new JSONObject()
                                    .put("bookId", bookId)
                                    .put("chapterId", chapterId)
                                    .put("content", contentViaN2)
                                    .put("method", "LKR+bll.v.N"))
                            .toString();
                }

                return new JSONObject()
                        .put("success", false)
                        .put("error", "safegetcontent: " + (safeError.isEmpty() ? "no data" : safeError)
                                + " | getvipcontent: " + (vipError.isEmpty() ? "no data" : vipError)
                                + " | LKR-fallback: failed")
                        .toString();
            }

            // ========== 步骤3: 保存 .qd 到正确路径 + 用 bll.v.N 解密 ==========
            addProbeLog("fetchChapter | downloaded " + encryptedBytes.length + " bytes via " + downloadSource);
            String qdPath = saveQdToCorrectPath(bookId, chapterId, encryptedBytes);

            // 清除之前可能缓存的无效内容
            capturedChapterContents.remove(chapterId);

            // 用 bll.v.N 解密（.qd 文件已就位，N 应该能直接解密）
            String contentViaNRetry = tryFetchViaBllVN(bookId, chapterId);
            if (isContentMeaningful(contentViaNRetry)) {
                return new JSONObject()
                        .put("success", true)
                        .put("data", new JSONObject()
                                .put("bookId", bookId)
                                .put("chapterId", chapterId)
                                .put("content", contentViaNRetry)
                                .put("method", downloadSource + "+bll.v.N"))
                        .toString();
            }

            // ========== 步骤4: N 失败时用 L→K→R 兜底（免费章节可能走这条路） ==========
            String content = triggerAppDecrypt(bookId, chapterId, encryptedBytes);
            if (isContentMeaningful(content)) {
                return new JSONObject()
                        .put("success", true)
                        .put("data", new JSONObject()
                                .put("bookId", bookId)
                                .put("chapterId", chapterId)
                                .put("content", content)
                                .put("method", downloadSource + "+triggerAppDecrypt"))
                        .toString();
            }

            return new JSONObject()
                    .put("success", false)
                    .put("error", "downloaded via " + downloadSource + " but all decryption methods failed")
                    .toString();

        } catch (Throwable e) {
            Log.e(TAG, "handleFetchChapter failed", e);
            try {
                return new JSONObject()
                        .put("success", false)
                        .put("error", e.getClass().getSimpleName() + ": " + e.getMessage())
                        .toString();
            } catch (Throwable e2) {
                return "{\"success\":false,\"error\":\"Unknown error\"}";
            }
        }
    }

    /**
     * 从 ChapterItem 对象提取 chapterId
     */
    private static String extractChapterId(Object chapterItem) {
        if (chapterItem == null) return null;
        try {
            // 尝试通过反射获取 chapterId 字段
            for (java.lang.reflect.Field f : chapterItem.getClass().getDeclaredFields()) {
                String fieldName = f.getName();
                if (fieldName.contains("chapterId") || fieldName.contains("ChapterId") ||
                    fieldName.equals("id") || fieldName.equals("_id")) {
                    f.setAccessible(true);
                    Object value = f.get(chapterItem);
                    if (value != null) {
                        return String.valueOf(value);
                    }
                }
            }
            // 尝试调用 toString 方法
            return chapterItem.toString();
        } catch (Throwable e) {
            return null;
        }
    }

    /**
     * 使用已保存的 ChapterItem 对象加载章节
     */
    private static String loadChapterViaSavedItem(String bookId, String chapterId, Object chapterItem) throws Exception {
        // 清空捕获缓冲区
        synchronized (recentCaptured) {
            recentCaptured.clear();
        }

        if (bllVInstance == null) {
            return new JSONObject()
                    .put("success", false)
                    .put("error", "bll.v instance not available - need to open a chapter first")
                    .toString();
        }

        Class<?> bllVClass = bllVInstance.getClass();
        Object result = null;
        String methodNameUsed = null;

        // 首先尝试使用保存的 v 对象调用 bll.v.N()
        Object savedV = vObjects.get(chapterId);
        if (savedV != null) {
            try {
                Method methodN = findMethod(bllVClass, "N");
                if (methodN != null) {
                    Class<?>[] paramTypes = methodN.getParameterTypes();
                    int paramCount = paramTypes.length;
                    if (paramCount == 5 || paramCount == 6) {
                        methodN.setAccessible(true);
                        Log.i(TAG, "Calling bll.v.N with saved vObject (paramCount=" + paramCount + ")");
                        if (paramCount == 6) {
                            result = methodN.invoke(bllVInstance, Long.parseLong(bookId), chapterItem, savedV, true, null, true);
                        } else {
                            result = methodN.invoke(bllVInstance, Long.parseLong(bookId), chapterItem, savedV, true, null);
                        }
                        methodNameUsed = "N(with saved v)";
                        Log.i(TAG, "bll.v.N returned: " + (result != null ? result.getClass().getName() : "null"));
                    }
                }
            } catch (Throwable e) {
                Log.i(TAG, "bll.v.N with saved v failed: " + e.getMessage());
            }
        }

        // 如果没有保存的 v 对象或调用失败，走 L→K→R 调用链
        if (result == null) {
            long bookIdLong = Long.parseLong(bookId);
            Method methodL = null, methodK = null, methodR = null;
            for (Method m : bllVClass.getDeclaredMethods()) {
                Class<?>[] pt = m.getParameterTypes();
                if (pt.length == 2) {
                    if ("L".equals(m.getName())) methodL = m;
                    else if ("K".equals(m.getName())) methodK = m;
                    else if ("R".equals(m.getName())) methodR = m;
                }
            }

            if (methodL != null) {
                methodL.setAccessible(true);
                result = methodL.invoke(bllVInstance, bookIdLong, chapterItem);
                Log.i(TAG, "bll.v.L returned: " + (result != null ? result.getClass().getSimpleName() : "null"));
            }
            if (methodK != null) {
                methodK.setAccessible(true);
                Object kResult = methodK.invoke(bllVInstance, bookIdLong, chapterItem);
                Log.i(TAG, "bll.v.K returned: " + (kResult != null ? kResult.toString() : "null"));
            }
            if (methodR != null) {
                methodR.setAccessible(true);
                Object rResult = methodR.invoke(bllVInstance, bookIdLong, chapterItem);
                Log.i(TAG, "bll.v.R returned: " + (rResult != null ? rResult.toString() : "null"));
                methodNameUsed = "L→K→R";
            }
        }

        // 如果返回了 ChapterContentItem，尝试从中提取内容
        if (result != null && result.getClass().getName().contains("ChapterContentItem")) {
            String content = extractContentFromChapterContentItem(result);
            if (content != null && !content.isEmpty()) {
                String resultStr = new JSONObject()
                        .put("success", true)
                        .put("data", new JSONObject()
                                .put("bookId", bookId)
                                .put("chapterId", chapterId)
                                .put("content", content)
                                .put("method", "bll.v." + methodNameUsed + " + direct extraction"))
                        .toString(1);
                return resultStr.replace("\\/", "/");
            }
        }

        // 等待 afterCall hook 从 capturedChapterContents 捕获解密内容
        String content = null;
        for (int retry = 0; retry < 10; retry++) {
            Thread.sleep(500);
            content = capturedChapterContents.get(chapterId);
            if (content != null) break;
        }

        if (content != null) {
            String resultStr = new JSONObject()
                    .put("success", true)
                    .put("data", new JSONObject()
                            .put("bookId", bookId)
                            .put("chapterId", chapterId)
                            .put("content", content)
                            .put("method", "bll.v." + methodNameUsed))
                    .toString(1);
            return resultStr.replace("\\/", "/");
        }

        // 兜底：从 recentCaptured 取
        String decryptedContent = getLatestCapturedContent();
        if (decryptedContent != null) {
            String resultStr = new JSONObject()
                    .put("success", true)
                    .put("data", new JSONObject()
                            .put("bookId", bookId)
                            .put("chapterId", chapterId)
                            .put("content", decryptedContent)
                            .put("method", "bll.v." + methodNameUsed + " + recentCaptured"))
                    .toString(1);
            return resultStr.replace("\\/", "/");
        } else {
            return new JSONObject()
                    .put("success", false)
                    .put("error", "No decrypted content captured (method=" + methodNameUsed + ")")
                    .toString();
        }
    }

    /**
     * 从 bll.v afterCall 自动提取 ChapterContentItem 的内容并缓存
     */
    private static void extractAndCacheChapterContent(Object chapterContentItem, Object[] args) {
        try {
            // 提取 chapterId（从方法参数中获取）
            String chapterId = null;
            if (args != null) {
                for (Object arg : args) {
                    if (arg != null && arg.getClass().getName().contains("Chapter")) {
                        chapterId = extractChapterId(arg);
                        break;
                    }
                }
            }

            // 遍历 ChapterContentItem 的所有字段，记录信息并查找内容
            StringBuilder fieldDump = new StringBuilder();
            fieldDump.append("ChapterContentItem fields dump:");
            String bestContent = null;

            for (java.lang.reflect.Field f : chapterContentItem.getClass().getDeclaredFields()) {
                f.setAccessible(true);
                String fieldName = f.getName();
                Object value = f.get(chapterContentItem);

                if (value == null) {
                    fieldDump.append("\n  ").append(fieldName).append(" = null");
                    continue;
                }

                String typeName = value.getClass().getName();
                if (value instanceof String) {
                    String str = (String) value;
                    fieldDump.append("\n  ").append(fieldName).append(" [String(").append(str.length()).append(")] = ");
                    if (str.length() > 200) {
                        fieldDump.append(str.substring(0, 200)).append("...");
                    } else {
                        fieldDump.append(str);
                    }
                    // 章节内容字段（chapterContent）— 必须有非空白字符才算有效
                    if (fieldName.equals("chapterContent") && str.trim().length() > 0
                            && str.replaceAll("[\\s\u3000]+", "").length() > 0) {
                        bestContent = str;
                    } else if (str.length() > 50 && (bestContent == null || str.length() > bestContent.length())) {
                        bestContent = str;
                    }
                } else if (value instanceof byte[]) {
                    byte[] data = (byte[]) value;
                    fieldDump.append("\n  ").append(fieldName).append(" [byte[").append(data.length).append("]]");
                    // 尝试解码大的 byte[] 字段
                    if (data.length > 100) {
                        String decoded = decodeContent(data);
                        if (decoded != null && decoded.length() > 100) {
                            fieldDump.append(" → decoded ").append(decoded.length()).append(" chars");
                            if (bestContent == null || decoded.length() > bestContent.length()) {
                                bestContent = decoded;
                            }
                        }
                    }
                } else if (value instanceof Number || value instanceof Boolean) {
                    fieldDump.append("\n  ").append(fieldName).append(" [").append(typeName).append("] = ").append(value);
                } else if (value instanceof List) {
                    List<?> list = (List<?>) value;
                    fieldDump.append("\n  ").append(fieldName).append(" [List(").append(list.size()).append(")]");
                    // 遍历 List 中的元素，可能包含段落文本
                    if (!list.isEmpty()) {
                        StringBuilder listContent = new StringBuilder();
                        for (Object item : list) {
                            if (item instanceof String) {
                                listContent.append((String) item).append("\n");
                            } else if (item != null) {
                                // 尝试从子对象提取文本
                                String subText = extractTextFromObject(item);
                                if (subText != null) {
                                    listContent.append(subText).append("\n");
                                }
                            }
                        }
                        if (listContent.length() > 100) {
                            fieldDump.append(" → extracted ").append(listContent.length()).append(" chars");
                            if (bestContent == null || listContent.length() > bestContent.length()) {
                                bestContent = listContent.toString();
                            }
                        }
                    }
                } else {
                    fieldDump.append("\n  ").append(fieldName).append(" [").append(typeName).append("]");
                    // 尝试从嵌套对象提取文本
                    String subText = extractTextFromObject(value);
                    if (subText != null && subText.length() > 100) {
                        fieldDump.append(" → extracted ").append(subText.length()).append(" chars");
                        if (bestContent == null || subText.length() > bestContent.length()) {
                            bestContent = subText;
                        }
                    }
                }
            }

            Log.i(TAG, fieldDump.toString());
            addProbeLog("CONTENT_EXTRACT | chapterId=" + chapterId + " | bestContent=" +
                (bestContent != null ? bestContent.length() + " chars" : "null") +
                " | " + fieldDump.toString());

            if (bestContent != null) {
                Log.i(TAG, "Auto-captured chapter content: " + bestContent.length() + " chars" +
                    (chapterId != null ? " for chapterId=" + chapterId : ""));
                addProbeLog("CONTENT_CAPTURE | chapterId=" + chapterId + " | len=" + bestContent.length());
                if (chapterId != null) {
                    capturedChapterContents.put(chapterId, bestContent);
                }
                // 同时保存到 recentCaptured
                saveCapturedContent(bestContent, "bll.v.auto", chapterId != null ? chapterId : "unknown");
            } else {
                addProbeLog("CONTENT_EXTRACT_FAIL | chapterId=" + chapterId + " | no content found in ChapterContentItem");
            }
        } catch (Throwable e) {
            Log.e(TAG, "extractAndCacheChapterContent failed", e);
            addProbeLog("CONTENT_EXTRACT_ERROR | " + e.getMessage());
        }
    }

    /**
     * 从任意对象中尝试提取文本内容
     */
    private static String extractTextFromObject(Object obj) {
        if (obj == null) return null;
        try {
            StringBuilder sb = new StringBuilder();
            for (java.lang.reflect.Field f : obj.getClass().getDeclaredFields()) {
                f.setAccessible(true);
                Object value = f.get(obj);
                if (value instanceof String) {
                    String str = (String) value;
                    if (str.length() > 20) {
                        sb.append(str).append("\n");
                    }
                }
            }
            return sb.length() > 0 ? sb.toString() : null;
        } catch (Throwable e) {
            return null;
        }
    }

    /**
     * 直接从任意对象中提取文本内容（遍历所有字段，包括嵌套对象），并通过 probeLog 记录字段详情
     */
    private static String extractContentDirect(Object obj) {
        if (obj == null) return null;
        try {
            StringBuilder fieldInfo = new StringBuilder("extractContentDirect | class=" + obj.getClass().getName());
            String bestContent = null;

            for (java.lang.reflect.Field f : obj.getClass().getDeclaredFields()) {
                f.setAccessible(true);
                String fieldName = f.getName();
                Object value = f.get(obj);

                if (value == null) {
                    fieldInfo.append(" | ").append(fieldName).append("=null");
                    continue;
                }

                if (value instanceof String) {
                    String str = (String) value;
                    fieldInfo.append(" | ").append(fieldName).append("=String(").append(str.length()).append(")");
                    if (fieldName.equals("chapterContent") && str.replaceAll("[\\s\u3000]+", "").length() > 0) {
                        bestContent = str;
                    } else if (str.length() > 50 && (bestContent == null || str.length() > bestContent.length())) {
                        bestContent = str;
                    }
                } else if (value instanceof byte[]) {
                    byte[] data = (byte[]) value;
                    fieldInfo.append(" | ").append(fieldName).append("=byte[").append(data.length).append("]");
                    if (data.length > 100) {
                        String decoded = decodeContent(data);
                        if (decoded != null && decoded.length() > 100) {
                            fieldInfo.append("→decoded(").append(decoded.length()).append(")");
                            if (bestContent == null || decoded.length() > bestContent.length()) {
                                bestContent = decoded;
                            }
                        }
                    }
                } else if (value instanceof Number || value instanceof Boolean) {
                    fieldInfo.append(" | ").append(fieldName).append("=").append(value);
                } else if (value instanceof List) {
                    List<?> list = (List<?>) value;
                    fieldInfo.append(" | ").append(fieldName).append("=List(").append(list.size()).append(")");
                    if (!list.isEmpty()) {
                        StringBuilder listContent = new StringBuilder();
                        for (Object item : list) {
                            if (item instanceof String) {
                                listContent.append((String) item).append("\n");
                            } else if (item != null) {
                                String subText = extractTextFromObject(item);
                                if (subText != null) listContent.append(subText).append("\n");
                            }
                        }
                        if (listContent.length() > 100) {
                            fieldInfo.append("→text(").append(listContent.length()).append(")");
                            if (bestContent == null || listContent.length() > bestContent.length()) {
                                bestContent = listContent.toString();
                            }
                        }
                    }
                } else {
                    fieldInfo.append(" | ").append(fieldName).append("=").append(value.getClass().getSimpleName());
                    // 递归提取嵌套对象
                    String subText = extractTextFromObject(value);
                    if (subText != null && subText.length() > 100) {
                        fieldInfo.append("→text(").append(subText.length()).append(")");
                        if (bestContent == null || subText.length() > bestContent.length()) {
                            bestContent = subText;
                        }
                    }
                }
            }

            addProbeLog(fieldInfo.toString());
            return bestContent;
        } catch (Throwable e) {
            addProbeLog("extractContentDirect failed: " + e.getMessage());
            return null;
        }
    }

    /**
     * 从 ChapterContentItem 对象中提取内容
     */
    private static String extractContentFromChapterContentItem(Object item) {
        if (item == null) return null;
        try {
            // 先记录所有字段信息
            for (java.lang.reflect.Field f : item.getClass().getDeclaredFields()) {
                f.setAccessible(true);
                String fieldName = f.getName();
                Object value = f.get(item);

                if (value != null) {
                    String valueType = value.getClass().getName();
                    String valueInfo;
                    if (value instanceof byte[]) {
                        byte[] data = (byte[]) value;
                        valueInfo = "byte[" + data.length + "] first=" + Integer.toHexString(data[0] & 0xFF);
                        Log.i(TAG, "ChapterContentItem." + fieldName + " = " + valueInfo);

                        // 尝试解码
                        String decoded = decodeContent(data);
                        if (decoded != null && decoded.length() > 100) {
                            return decoded;
                        }
                    } else if (value instanceof String) {
                        String str = (String) value;
                        valueInfo = str.length() > 100 ? str.substring(0, 100) : str;
                        if (str.length() > 100) {
                            Log.i(TAG, "ChapterContentItem." + fieldName + " = " + valueInfo + "...");
                            if (fieldName.contains("content") || fieldName.contains("Content") ||
                                fieldName.contains("chapterContent")) {
                                return str;
                            }
                        } else {
                            Log.i(TAG, "ChapterContentItem." + fieldName + " = " + valueInfo);
                        }
                    } else {
                        Log.i(TAG, "ChapterContentItem." + fieldName + " = " + valueType + ": " + value.toString());
                    }
                } else {
                    Log.i(TAG, "ChapterContentItem." + fieldName + " = null");
                }
            }
        } catch (Throwable e) {
            Log.e(TAG, "Failed to extract content from ChapterContentItem", e);
        }
        return null;
    }

    /**
     * 解码内容（处理 gzip 压缩或 UTF-8 编码）
     */
    private static String decodeContent(byte[] data) {
        if (data == null || data.length == 0) return null;
        try {
            Log.i(TAG, "decodeContent: data.length=" + data.length + " first2=" + Integer.toHexString(data[0] & 0xFF) + " " + Integer.toHexString(data[1] & 0xFF));

            // 尝试 gzip 解压
            if (data.length > 2 && data[0] == 0x1f && data[1] == (byte)0x8b) {
                java.util.zip.GZIPInputStream gzipIn = new java.util.zip.GZIPInputStream(
                    new java.io.ByteArrayInputStream(data));
                java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
                byte[] buffer = new byte[4096];
                int len;
                while ((len = gzipIn.read(buffer)) > 0) {
                    out.write(buffer, 0, len);
                }
                gzipIn.close();
                String result = out.toString("UTF-8");
                Log.i(TAG, "Gzip decoded to " + result.length() + " chars");
                return result;
            }

            // 尝试 UTF-8 解码
            String result = new String(data, "UTF-8");
            Log.i(TAG, "UTF-8 decoded to " + result.length() + " chars");
            return result;
        } catch (Throwable e) {
            Log.e(TAG, "Failed to decode content", e);
            return null;
        }
    }

    /**
     * 通过 bll.v 类加载章节内容
     * 这是起点 App 内部的方法调用，会自动处理签名、请求、解密等
     */
    private static String loadChapterViaBllV(String bookId, String chapterId) throws Exception {
        // 清空捕获缓冲区，准备接收新的解密内容
        synchronized (recentCaptured) {
            recentCaptured.clear();
        }

        // 获取 ClassLoader
        if (appClassLoader == null) {
            appClassLoader = Thread.currentThread().getContextClassLoader();
        }

        // 加载 bll.v 类
        Class<?> bllVClass = appClassLoader.loadClass("com.qidian.QDReader.component.bll.v");

        // 加载 ChapterItem 类
        if (chapterItemClass == null) {
            String[] candidates = {
                "com.qidian.QDReader.repository.entity.ChapterItem",
                "com.qidian.QDReader.component.data.ChapterItem",
                "com.qidian.QDReader.data.bean.ChapterItem"
            };
            for (String className : candidates) {
                try {
                    chapterItemClass = appClassLoader.loadClass(className);
                    Log.i(TAG, "Loaded ChapterItem: " + className);
                    break;
                } catch (ClassNotFoundException ignored) {}
            }
            if (chapterItemClass == null) {
                chapterItemClass = findClassByName(appClassLoader, "ChapterItem");
                if (chapterItemClass != null) {
                    Log.i(TAG, "Found ChapterItem via search: " + chapterItemClass.getName());
                } else {
                    throw new Exception("ChapterItem class not found");
                }
            }
        }

        // 创建 ChapterItem 对象
        Object chapterItem = createChapterItem(chapterItemClass, bookId, chapterId);
        if (chapterItem == null) {
            throw new Exception("Failed to create ChapterItem");
        }

        // 调用 bll.v.R() 方法获取内容
        // 根据日志：bll.v.R(bookId, ChapterItem) → 返回 .cc 文件路径
        Method methodR = findMethod(bllVClass, "R", long.class, Object.class);
        if (methodR == null) {
            methodR = findMethod(bllVClass, "R", Object.class, Object.class);
        }

        if (methodR != null) {
            Log.i(TAG, "Calling bll.v.R() with bookId=" + bookId);

            // 尝试调用方法（可能是静态方法或实例方法）
            Object result = null;
            if (java.lang.reflect.Modifier.isStatic(methodR.getModifiers())) {
                // 静态方法
                if (methodR.getParameterTypes().length == 2) {
                    Class<?>[] paramTypes = methodR.getParameterTypes();
                    if (paramTypes[0] == long.class || paramTypes[0] == int.class) {
                        result = methodR.invoke(null, Long.parseLong(bookId), chapterItem);
                    } else {
                        result = methodR.invoke(null, bookId, chapterItem);
                    }
                } else {
                    result = methodR.invoke(null, chapterItem);
                }
            } else {
                // 实例方法 - 需要获取 bll.v 的实例
                Object instance = getBllVInstance(bllVClass);
                if (instance != null) {
                    Class<?>[] paramTypes = methodR.getParameterTypes();
                    if (paramTypes.length == 2) {
                        if (paramTypes[0] == long.class || paramTypes[0] == int.class) {
                            result = methodR.invoke(instance, Long.parseLong(bookId), chapterItem);
                        } else {
                            result = methodR.invoke(instance, bookId, chapterItem);
                        }
                    } else {
                        result = methodR.invoke(instance, chapterItem);
                    }
                }
            }

            Log.i(TAG, "bll.v.R() returned: " + (result != null ? result.toString() : "null"));

            // 等待解密（Cipher Hook 会捕获）
            Thread.sleep(3000);

            // 从捕获缓冲区获取解密后的内容
            String decryptedContent = getLatestCapturedContent();

            if (decryptedContent != null) {
                String resultStr = new JSONObject()
                        .put("success", true)
                        .put("data", new JSONObject()
                                .put("bookId", bookId)
                                .put("chapterId", chapterId)
                                .put("content", decryptedContent)
                                .put("method", "bll.v.R"))
                        .toString(1);
                return resultStr.replace("\\/", "/");
            } else {
                return new JSONObject()
                        .put("success", false)
                        .put("error", "No decrypted content captured via bll.v.R")
                        .put("note", "Try triggering safegetcontent API instead")
                        .toString();
            }
        } else {
            throw new Exception("Method bll.v.R not found");
        }
    }

    private static Object getBllVInstance(Class<?> bllVClass) throws Exception {
        if (bllVInstance != null) {
            return bllVInstance;
        }

        // 尝试找单例模式
        for (java.lang.reflect.Field f : bllVClass.getDeclaredFields()) {
            if (java.lang.reflect.Modifier.isStatic(f.getModifiers()) &&
                f.getType() == bllVClass) {
                f.setAccessible(true);
                bllVInstance = f.get(null);
                Log.i(TAG, "Found bll.v singleton instance");
                return bllVInstance;
            }
        }

        // 尝试创建实例
        try {
            bllVInstance = bllVClass.newInstance();
            Log.i(TAG, "Created new bll.v instance");
        } catch (Throwable e) {
            Log.e(TAG, "Failed to create bll.v instance", e);
        }
        return bllVInstance;
    }

    private static Object cloneChapterItemWithNewId(String targetChapterId) {
        try {
            if (chapterItems.isEmpty()) return null;

            // 取一个已保存的真实 ChapterItem 作为模板
            Object template = null;
            for (Object item : chapterItems.values()) {
                if (item != null) {
                    template = item;
                    break;
                }
            }
            if (template == null) return null;

            Class<?> clazz = template.getClass();

            // 创建新实例
            Object newItem = clazz.newInstance();

            // 复制所有字段
            for (java.lang.reflect.Field f : clazz.getDeclaredFields()) {
                if (java.lang.reflect.Modifier.isStatic(f.getModifiers())) continue;
                f.setAccessible(true);
                try {
                    f.set(newItem, f.get(template));
                } catch (Throwable ignored) {}
            }

            // 修改 ChapterId 为目标值
            for (java.lang.reflect.Field f : clazz.getDeclaredFields()) {
                if (java.lang.reflect.Modifier.isStatic(f.getModifiers())) continue;
                String name = f.getName();
                if (name.equals("ChapterId") || name.equals("chapterId")) {
                    f.setAccessible(true);
                    if (f.getType() == long.class || f.getType() == Long.class) {
                        f.set(newItem, Long.parseLong(targetChapterId));
                    } else if (f.getType() == int.class || f.getType() == Integer.class) {
                        f.set(newItem, Integer.parseInt(targetChapterId));
                    }
                    Log.i(TAG, "Cloned ChapterItem: set " + name + " = " + targetChapterId);
                }
            }

            return newItem;
        } catch (Throwable e) {
            Log.e(TAG, "Failed to clone ChapterItem", e);
            return null;
        }
    }

    private static Object createChapterItem(Class<?> chapterItemClass, String bookId, String chapterId) throws Exception {
        try {
            // 尝试找构造方法
            for (java.lang.reflect.Constructor<?> ctor : chapterItemClass.getDeclaredConstructors()) {
                try {
                    ctor.setAccessible(true);
                    Class<?>[] paramTypes = ctor.getParameterTypes();

                    if (paramTypes.length == 0) {
                        // 无参构造，然后设置属性
                        Object item = ctor.newInstance();
                        setChapterItemFields(item, bookId, chapterId);
                        return item;
                    } else if (paramTypes.length == 1 && paramTypes[0] == long.class) {
                        // 接受 chapterId (long)
                        Object item = ctor.newInstance(Long.parseLong(chapterId));
                        return item;
                    } else if (paramTypes.length == 1 && paramTypes[0] == int.class) {
                        Object item = ctor.newInstance(Integer.parseInt(chapterId));
                        return item;
                    }
                } catch (Throwable ignored) {}
            }

            // 如果找不到合适的构造方法，尝试创建默认实例
            Object item = chapterItemClass.newInstance();
            setChapterItemFields(item, bookId, chapterId);
            return item;

        } catch (Throwable e) {
            Log.e(TAG, "Failed to create ChapterItem", e);
            return null;
        }
    }

    private static void setChapterItemFields(Object item, String bookId, String chapterId) throws Exception {
        // 尝试设置 bookId 和 chapterId 字段
        for (java.lang.reflect.Field f : item.getClass().getDeclaredFields()) {
            String fieldName = f.getName();
            f.setAccessible(true);

            if (fieldName.contains("chapterId") || fieldName.contains("ChapterId") ||
                fieldName.equals("id") || fieldName.equals("_id")) {
                if (f.getType() == long.class || f.getType() == Long.class) {
                    f.set(item, Long.parseLong(chapterId));
                } else if (f.getType() == int.class || f.getType() == Integer.class) {
                    f.set(item, Integer.parseInt(chapterId));
                } else if (f.getType() == String.class) {
                    f.set(item, chapterId);
                }
            } else if (fieldName.contains("bookId") || fieldName.equals("book")) {
                if (f.getType() == long.class || f.getType() == Long.class) {
                    f.set(item, Long.parseLong(bookId));
                } else if (f.getType() == String.class) {
                    f.set(item, bookId);
                }
            }
        }
    }

    private static Method findMethod(Class<?> clazz, String methodName, Class<?>... paramTypes) {
        try {
            return clazz.getDeclaredMethod(methodName, paramTypes);
        } catch (NoSuchMethodException e) {
            // 尝试找同名方法
            for (Method m : clazz.getDeclaredMethods()) {
                if (m.getName().equals(methodName)) {
                    if (paramTypes == null || paramTypes.length == 0) {
                        return m;
                    }
                    Class<?>[] methodParamTypes = m.getParameterTypes();
                    if (methodParamTypes.length == paramTypes.length) {
                        boolean match = true;
                        for (int i = 0; i < paramTypes.length && match; i++) {
                            if (!paramTypes[i].isAssignableFrom(methodParamTypes[i])) {
                                match = false;
                            }
                        }
                        if (match) return m;
                    }
                }
            }
        }
        return null;
    }

    private static Class<?> findClassByName(ClassLoader cl, String simpleName) {
        // 通过已知的调用链查找类
        String[] possiblePackages = {
            "com.qidian.QDReader.component.data",
            "com.qidian.QDReader.data",
            "com.qidian.QDReader.data.bean",
            "com.qidian.QDReader.entity",
            "com.qidian.QDReader.model"
        };

        for (String pkg : possiblePackages) {
            try {
                String fullName = pkg + "." + simpleName;
                return cl.loadClass(fullName);
            } catch (ClassNotFoundException ignored) {}
        }
        return null;
    }

    private static String getLatestCapturedContent() {
        synchronized (recentCaptured) {
            // 查找最新的内容解密（时间戳最新的）
            long latestTime = 0;
            String latestContent = null;
            Log.i(TAG, "getLatestCapturedContent: checking " + recentCaptured.size() + " items");
            for (int i = recentCaptured.size() - 1; i >= 0; i--) {
                CapturedContent cc = recentCaptured.get(i);
                if (cc != null && cc.timestamp > latestTime && cc.plaintext != null && cc.plaintext.length() > 200) {
                    Log.i(TAG, "Checking item " + i + " timestamp=" + cc.timestamp + " containsContent=" + cc.plaintext.contains("Content"));
                    if (cc.plaintext.contains("Content")) {
                        // 提取 JSON 中的 Content 字段
                        try {
                            JSONObject json = new JSONObject(cc.plaintext);
                            if (json.has("Content")) {
                                String content = json.getString("Content");
                                if (content != null && content.length() > 100) {
                                    latestTime = cc.timestamp;
                                    latestContent = content;
                                    Log.i(TAG, "Found content with " + content.length() + " chars, starts with: " + content.substring(0, 20));
                                }
                            }
                        } catch (Throwable e) {
                            Log.e(TAG, "Failed to parse Content", e);
                        }
                    }
                }
            }
            if (latestContent != null) {
                Log.i(TAG, "Returning: " + latestContent.substring(0, Math.min(50, latestContent.length())));
            } else {
                Log.i(TAG, "Returning: null");
            }
            return latestContent;
        }
    }

    // ==================== Search RPC ====================

    private static String handleSearch(String keyword, int page) {
        try {
            Log.i(TAG, "RPC: search - keyword=" + keyword + ", page=" + page);

            if (savedOkHttpClient == null) {
                return new JSONObject()
                        .put("success", false)
                        .put("error", "OkHttpClient not saved yet - app may not have made any HTTP request")
                        .toString();
            }
            if (appClassLoader == null) {
                return new JSONObject()
                        .put("success", false)
                        .put("error", "AppClassLoader not available")
                        .toString();
            }

            // 决定使用捕获的模板还是 fallback
            boolean usingFallback = (searchUrlTemplate == null);
            String effectiveUrl;
            String effectiveMethod;
            String effectiveBody;

            if (usingFallback) {
                Log.i(TAG, "Using fallback search template (no captured template)");
                effectiveUrl = FALLBACK_SEARCH_URL;
                effectiveMethod = FALLBACK_SEARCH_METHOD;
                effectiveBody = FALLBACK_SEARCH_BODY
                        .replace("{keyword}", java.net.URLEncoder.encode(keyword, "UTF-8"))
                        .replace("{page}", String.valueOf(page));
            } else {
                effectiveUrl = buildSearchUrl(keyword, page);
                effectiveMethod = searchHttpMethod;
                effectiveBody = buildSearchBody(keyword, page);
            }

            if (effectiveUrl == null) {
                return new JSONObject()
                        .put("success", false)
                        .put("error", "Cannot build search URL")
                        .toString();
            }

            Log.i(TAG, "Search URL: " + effectiveUrl + (usingFallback ? " [fallback]" : " [captured]"));

            // 用反射构造 okhttp3.Request
            Class<?> requestBuilderClass = appClassLoader.loadClass("okhttp3.Request$Builder");
            Object reqBuilder = requestBuilderClass.newInstance();

            // builder.url(searchUrl)
            Method urlMethod = requestBuilderClass.getDeclaredMethod("url", String.class);
            urlMethod.invoke(reqBuilder, effectiveUrl);

            // 根据 HTTP 方法设置 GET 或 POST
            if ("POST".equalsIgnoreCase(effectiveMethod) && effectiveBody != null && !effectiveBody.isEmpty()) {
                // 使用 FormBody.Builder 构造 POST body（和 App 原生方式一致）
                String postBody = effectiveBody;
                Log.i(TAG, "Search POST body: " + postBody);

                Class<?> formBodyBuilderClass = appClassLoader.loadClass("okhttp3.FormBody$Builder");
                Object formBuilder = formBodyBuilderClass.newInstance();
                Method addMethod = formBodyBuilderClass.getDeclaredMethod("add", String.class, String.class);

                // 解析 key=value&key2=value2 格式
                for (String pair : postBody.split("&")) {
                    String[] kv = pair.split("=", 2);
                    if (kv.length == 2) {
                        // URL decode the value since buildSearchBody already encoded it
                        String value = java.net.URLDecoder.decode(kv[1], "UTF-8");
                        addMethod.invoke(formBuilder, kv[0], value);
                    }
                }

                Method formBuildMethod = formBodyBuilderClass.getDeclaredMethod("build");
                Object formBody = formBuildMethod.invoke(formBuilder);

                Class<?> requestBodyClass = appClassLoader.loadClass("okhttp3.RequestBody");
                Method postMethod = requestBuilderClass.getDeclaredMethod("post", requestBodyClass);
                postMethod.invoke(reqBuilder, formBody);
            } else {
                // GET request
                Method getMethod = requestBuilderClass.getDeclaredMethod("get");
                getMethod.invoke(reqBuilder);
            }

            // builder.build() → request
            Method buildMethod = requestBuilderClass.getDeclaredMethod("build");
            Object httpRequest = buildMethod.invoke(reqBuilder);

            // client.newCall(request).execute()
            Class<?> requestClass = appClassLoader.loadClass("okhttp3.Request");
            Method newCallMethod = savedOkHttpClient.getClass().getMethod("newCall", requestClass);
            Object call = newCallMethod.invoke(savedOkHttpClient, httpRequest);
            Method executeMethod = call.getClass().getMethod("execute");
            Object response = executeMethod.invoke(call);

            // 读取 response code
            Method codeMethod = response.getClass().getDeclaredMethod("code");
            int code = (int) codeMethod.invoke(response);

            // 读取 response body
            Method bodyMethod = response.getClass().getDeclaredMethod("body");
            Object responseBody = bodyMethod.invoke(response);
            String bodyStr = "";
            if (responseBody != null) {
                Method stringMethod = responseBody.getClass().getMethod("string");
                bodyStr = (String) stringMethod.invoke(responseBody);
            }

            Log.i(TAG, "Search response: code=" + code + ", bodyLen=" + bodyStr.length());

            // 解析并返回
            JSONObject result = new JSONObject();
            result.put("success", true);
            result.put("keyword", keyword);
            result.put("page", page);
            result.put("httpCode", code);

            if (!bodyStr.isEmpty()) {
                try {
                    result.put("response", new JSONObject(bodyStr));
                } catch (Throwable e) {
                    // 不是 JSON，原样返回
                    result.put("response", bodyStr);
                }
            }

            String resultStr = result.toString(1);
            return resultStr.replace("\\/", "/");

        } catch (Throwable e) {
            Log.e(TAG, "handleSearch failed", e);
            try {
                return new JSONObject()
                        .put("success", false)
                        .put("error", e.getClass().getSimpleName() + ": " + e.getMessage())
                        .toString();
            } catch (Throwable e2) {
                return "{\"success\":false,\"error\":\"Unknown error\"}";
            }
        }
    }

    // ==================== Book Detail RPC ====================

    private static final String BOOK_DETAIL_URL = "https://druidv6.if.qidian.com/argus/api/v3/bookdetail/get";

    private static String handleBookDetail(String bookId) {
        try {
            Log.i(TAG, "RPC: bookDetail - bookId=" + bookId);

            if (savedOkHttpClient == null) {
                return new JSONObject()
                        .put("success", false)
                        .put("error", "OkHttpClient not saved yet - app may not have made any HTTP request")
                        .toString();
            }
            if (appClassLoader == null) {
                return new JSONObject()
                        .put("success", false)
                        .put("error", "AppClassLoader not available")
                        .toString();
            }

            String url = BOOK_DETAIL_URL + "?bookId=" + bookId + "&isOutBook=0";
            Log.i(TAG, "BookDetail URL: " + url);

            // 用反射构造 okhttp3.Request (GET)
            Class<?> requestBuilderClass = appClassLoader.loadClass("okhttp3.Request$Builder");
            Object reqBuilder = requestBuilderClass.newInstance();

            java.lang.reflect.Method urlMethod = requestBuilderClass.getDeclaredMethod("url", String.class);
            urlMethod.invoke(reqBuilder, url);

            java.lang.reflect.Method getMethod = requestBuilderClass.getDeclaredMethod("get");
            getMethod.invoke(reqBuilder);

            java.lang.reflect.Method buildMethod = requestBuilderClass.getDeclaredMethod("build");
            Object httpRequest = buildMethod.invoke(reqBuilder);

            // client.newCall(request).execute()
            Class<?> requestClass = appClassLoader.loadClass("okhttp3.Request");
            java.lang.reflect.Method newCallMethod = savedOkHttpClient.getClass().getMethod("newCall", requestClass);
            Object call = newCallMethod.invoke(savedOkHttpClient, httpRequest);
            java.lang.reflect.Method executeMethod = call.getClass().getMethod("execute");
            Object response = executeMethod.invoke(call);

            // 读取 response code
            java.lang.reflect.Method codeMethod = response.getClass().getDeclaredMethod("code");
            int code = (int) codeMethod.invoke(response);

            // 读取 response body
            java.lang.reflect.Method bodyMethod = response.getClass().getDeclaredMethod("body");
            Object responseBody = bodyMethod.invoke(response);
            String bodyStr = "";
            if (responseBody != null) {
                java.lang.reflect.Method stringMethod = responseBody.getClass().getMethod("string");
                bodyStr = (String) stringMethod.invoke(responseBody);
            }

            Log.i(TAG, "BookDetail response: code=" + code + ", bodyLen=" + bodyStr.length());

            // 解析并返回
            JSONObject result = new JSONObject();
            result.put("success", true);
            result.put("bookId", bookId);
            result.put("httpCode", code);

            if (!bodyStr.isEmpty()) {
                try {
                    result.put("response", new JSONObject(bodyStr));
                } catch (Throwable e) {
                    result.put("response", bodyStr);
                }
            }

            String resultStr = result.toString(1);
            return resultStr.replace("\\/", "/");

        } catch (Throwable e) {
            Log.e(TAG, "handleBookDetail failed", e);
            try {
                return new JSONObject()
                        .put("success", false)
                        .put("error", e.getClass().getSimpleName() + ": " + e.getMessage())
                        .toString();
            } catch (Throwable e2) {
                return "{\"success\":false,\"error\":\"Unknown error\"}";
            }
        }
    }

    // ==================== Chapter List RPC ====================

    private static final String CHAPTER_LIST_URL = "https://druidv6.if.qidian.com/argus/api/v3/chapterlist/chapterlist";

    private static String handleGetChapterList(String bookId) {
        try {
            Log.i(TAG, "RPC: getChapterList - bookId=" + bookId);

            if (savedOkHttpClient == null) {
                return new JSONObject()
                        .put("success", false)
                        .put("error", "OkHttpClient not saved yet")
                        .toString();
            }
            if (appClassLoader == null) {
                return new JSONObject()
                        .put("success", false)
                        .put("error", "AppClassLoader not available")
                        .toString();
            }

            String url = CHAPTER_LIST_URL + "?bookId=" + bookId
                    + "&timeStamp=0&requestSource=0&md5Signature=&extendchapterIds=";
            Log.i(TAG, "ChapterList URL: " + url);

            // 反射构造 GET 请求
            Class<?> requestBuilderClass = appClassLoader.loadClass("okhttp3.Request$Builder");
            Object reqBuilder = requestBuilderClass.newInstance();

            java.lang.reflect.Method urlMethod = requestBuilderClass.getDeclaredMethod("url", String.class);
            urlMethod.invoke(reqBuilder, url);

            java.lang.reflect.Method getMethod = requestBuilderClass.getDeclaredMethod("get");
            getMethod.invoke(reqBuilder);

            java.lang.reflect.Method buildMethod = requestBuilderClass.getDeclaredMethod("build");
            Object httpRequest = buildMethod.invoke(reqBuilder);

            // client.newCall(request).execute()
            Class<?> requestClass = appClassLoader.loadClass("okhttp3.Request");
            java.lang.reflect.Method newCallMethod = savedOkHttpClient.getClass().getMethod("newCall", requestClass);
            Object call = newCallMethod.invoke(savedOkHttpClient, httpRequest);
            java.lang.reflect.Method executeMethod = call.getClass().getMethod("execute");
            Object response = executeMethod.invoke(call);

            int code = (int) response.getClass().getDeclaredMethod("code").invoke(response);

            Object responseBody = response.getClass().getDeclaredMethod("body").invoke(response);
            String bodyStr = "";
            if (responseBody != null) {
                bodyStr = (String) responseBody.getClass().getMethod("string").invoke(responseBody);
            }

            Log.i(TAG, "ChapterList response: code=" + code + ", bodyLen=" + bodyStr.length());

            JSONObject result = new JSONObject();
            result.put("success", true);
            result.put("bookId", bookId);
            result.put("httpCode", code);

            if (!bodyStr.isEmpty()) {
                try {
                    result.put("response", new JSONObject(bodyStr));
                } catch (Throwable e) {
                    result.put("response", bodyStr);
                }
            }

            String resultStr = result.toString(1);
            return resultStr.replace("\\/", "/");

        } catch (Throwable e) {
            Log.e(TAG, "handleGetChapterList failed", e);
            try {
                return new JSONObject()
                        .put("success", false)
                        .put("error", e.getClass().getSimpleName() + ": " + e.getMessage())
                        .toString();
            } catch (Throwable e2) {
                return "{\"success\":false,\"error\":\"Unknown error\"}";
            }
        }
    }

    // ==================== Test No-Template Path ====================

    /**
     * 严格测试完全免 UI 路径：
     * - 强制不使用 chapterItems 中的模板（模拟首次使用场景）
     * - 只通过 API 获取 JSON 构造 ChapterItem
     * - 如果 bllVInstance 为 null，尝试反射构造
     */
    private static String handleTestNoTemplate(String bookId, String chapterId) {
        try {
            JSONObject result = new JSONObject();
            result.put("testMode", "no-template (forced)");
            result.put("bookId", bookId);
            result.put("chapterId", chapterId);

            // 记录初始状态
            result.put("initialBllVInstance", bllVInstance != null);
            result.put("initialChapterItemsCount", chapterItems.size());

            // 步骤1：确保 bllVInstance（如果为 null，尝试构造）
            if (bllVInstance == null) {
                result.put("bllVSource", "constructed");
                bllVInstance = createBllVInstance(Long.parseLong(bookId));
                if (bllVInstance == null) {
                    result.put("success", false);
                    result.put("error", "Failed to create bllVInstance");
                    return result.toString(1);
                }
            } else {
                result.put("bllVSource", "auto-captured");
            }

            // 步骤2：通过 API 获取加密数据
            if (savedOkHttpClient == null) {
                result.put("success", false);
                result.put("error", "OkHttpClient not available");
                return result.toString(1);
            }

            String url = SAFE_GET_CONTENT_URL + "?bookId=" + bookId + "&chapterId=" + chapterId;
            Class<?> requestBuilderClass = appClassLoader.loadClass("okhttp3.Request$Builder");
            Object reqBuilder = requestBuilderClass.newInstance();
            requestBuilderClass.getDeclaredMethod("url", String.class).invoke(reqBuilder, url);
            requestBuilderClass.getDeclaredMethod("get").invoke(reqBuilder);
            Object httpRequest = requestBuilderClass.getDeclaredMethod("build").invoke(reqBuilder);
            Class<?> requestClass = appClassLoader.loadClass("okhttp3.Request");
            Object call = savedOkHttpClient.getClass().getMethod("newCall", requestClass)
                    .invoke(savedOkHttpClient, httpRequest);
            Object response = call.getClass().getMethod("execute").invoke(call);

            int code = (int) response.getClass().getDeclaredMethod("code").invoke(response);
            result.put("httpCode", code);

            Object responseBody = response.getClass().getDeclaredMethod("body").invoke(response);
            byte[] bodyBytes = (byte[]) responseBody.getClass().getMethod("bytes").invoke(responseBody);
            result.put("bodyLength", bodyBytes.length);

            if (bodyBytes.length == 0 || new String(bodyBytes, java.nio.charset.StandardCharsets.UTF_8).startsWith("{")) {
                result.put("success", false);
                result.put("error", "Response is not encrypted data");
                return result.toString(1);
            }

            // 步骤3：保存 .qd 文件
            String qdDir = "/storage/emulated/0/Android/data/com.qidian.QDReader/files/QDReader/book/0/" + bookId;
            new java.io.File(qdDir).mkdirs();
            String qdPath = qdDir + "/" + chapterId + ".qd";
            java.io.FileOutputStream fos = new java.io.FileOutputStream(qdPath);
            fos.write(bodyBytes);
            fos.close();

            // 步骤4：强制使用 API 构造的 ChapterItem（不使用 clone）
            Log.i(TAG, "testNoTemplate: constructing ChapterItem from API...");
            Object chapterItem = constructChapterItemFromApi(bookId, chapterId);
            if (chapterItem == null) {
                result.put("success", false);
                result.put("error", "Failed to construct ChapterItem from API");
                result.put("chapterItemSource", "api-failed");
                return result.toString(1);
            }
            result.put("chapterItemSource", "api-constructed");

            // 步骤5：调用 L → K → R
            Class<?> bllVClass = bllVInstance.getClass();
            Method methodL = null, methodK = null, methodR = null;
            for (Method m : bllVClass.getDeclaredMethods()) {
                Class<?>[] pt = m.getParameterTypes();
                if (pt.length == 2) {
                    if ("L".equals(m.getName())) methodL = m;
                    else if ("K".equals(m.getName())) methodK = m;
                    else if ("R".equals(m.getName())) methodR = m;
                }
            }

            long bookIdLong = Long.parseLong(bookId);

            if (methodL != null) {
                methodL.setAccessible(true);
                Object lResult = methodL.invoke(bllVInstance, bookIdLong, chapterItem);
                result.put("L_result", lResult != null ? lResult.getClass().getSimpleName() : "null");
            }
            if (methodK != null) {
                methodK.setAccessible(true);
                Object kResult = methodK.invoke(bllVInstance, bookIdLong, chapterItem);
                result.put("K_result", kResult != null ? kResult.toString() : "null");
            }
            if (methodR != null) {
                methodR.setAccessible(true);
                Object rResult = methodR.invoke(bllVInstance, bookIdLong, chapterItem);
                result.put("R_result", rResult != null ? rResult.toString() : "null");
            }

            // 步骤6：等待 afterCall hook 捕获
            String content = null;
            for (int retry = 0; retry < 10; retry++) {
                Thread.sleep(500);
                content = capturedChapterContents.get(chapterId);
                if (content != null) break;
            }

            if (content != null) {
                result.put("success", true);
                result.put("decrypted", true);
                result.put("contentLength", content.length());
                result.put("contentPreview", content.substring(0, Math.min(300, content.length())));
            } else {
                result.put("success", false);
                result.put("decrypted", false);
                result.put("error", "No content captured after L→K→R");
            }

            return result.toString(1);
        } catch (Throwable e) {
            Log.e(TAG, "testNoTemplate failed", e);
            try {
                return new JSONObject()
                        .put("success", false)
                        .put("error", e.getClass().getSimpleName() + ": " + e.getMessage())
                        .toString();
            } catch (Throwable e2) {
                return "{\"success\":false,\"error\":\"" + e.getMessage() + "\"}";
            }
        }
    }

    // ==================== Probe bll.v ====================

    private static String handleProbeBllV() {
        try {
            JSONObject result = new JSONObject();
            result.put("success", true);

            // 当前状态
            result.put("bllVInstance", bllVInstance != null ? bllVInstance.getClass().getName() : "null");
            result.put("chapterItemsCount", chapterItems.size());

            // 获取 ClassLoader
            android.app.Application app = (android.app.Application)
                    Class.forName("android.app.ActivityThread")
                            .getMethod("currentApplication")
                            .invoke(null);
            ClassLoader cl = app.getClassLoader();

            // 1. 分析 bll.v 类结构
            Class<?> bllVClass = cl.loadClass("com.qidian.QDReader.component.bll.v");
            JSONObject bllVInfo = new JSONObject();

            // 构造函数
            JSONArray ctors = new JSONArray();
            for (java.lang.reflect.Constructor<?> ctor : bllVClass.getDeclaredConstructors()) {
                StringBuilder sb = new StringBuilder("(");
                for (Class<?> p : ctor.getParameterTypes()) {
                    if (sb.length() > 1) sb.append(", ");
                    sb.append(p.getName());
                }
                sb.append(")");
                ctors.put(sb.toString());
            }
            bllVInfo.put("constructors", ctors);

            // 静态字段（寻找单例）
            JSONArray staticFields = new JSONArray();
            for (java.lang.reflect.Field f : bllVClass.getDeclaredFields()) {
                if (java.lang.reflect.Modifier.isStatic(f.getModifiers())) {
                    f.setAccessible(true);
                    JSONObject fi = new JSONObject();
                    fi.put("name", f.getName());
                    fi.put("type", f.getType().getName());
                    fi.put("isFinal", java.lang.reflect.Modifier.isFinal(f.getModifiers()));
                    try {
                        Object val = f.get(null);
                        if (val == null) {
                            fi.put("value", "null");
                        } else if (val instanceof Number || val instanceof Boolean || val instanceof String) {
                            fi.put("value", val.toString());
                        } else {
                            fi.put("value", val.getClass().getName() + "@" + Integer.toHexString(System.identityHashCode(val)));
                        }
                        // 如果是 bll.v 类型的静态字段，记录为单例候选
                        if (f.getType() == bllVClass || f.getType().getName().equals(bllVClass.getName())) {
                            fi.put("isSingletonCandidate", true);
                        }
                    } catch (Throwable e) {
                        fi.put("value", "ERROR: " + e.getMessage());
                    }
                    staticFields.put(fi);
                }
            }
            bllVInfo.put("staticFields", staticFields);

            // 超类
            bllVInfo.put("superClass", bllVClass.getSuperclass() != null ? bllVClass.getSuperclass().getName() : "none");

            // 接口
            JSONArray interfaces = new JSONArray();
            for (Class<?> iface : bllVClass.getInterfaces()) {
                interfaces.put(iface.getName());
            }
            bllVInfo.put("interfaces", interfaces);

            // 实例字段（非静态）
            JSONArray instanceFields = new JSONArray();
            for (java.lang.reflect.Field f : bllVClass.getDeclaredFields()) {
                if (!java.lang.reflect.Modifier.isStatic(f.getModifiers())) {
                    JSONObject fi = new JSONObject();
                    fi.put("name", f.getName());
                    fi.put("type", f.getType().getName());
                    instanceFields.put(fi);
                }
            }
            bllVInfo.put("instanceFields", instanceFields);

            result.put("bllV", bllVInfo);

            // 2. 如果 bllVInstance 已有，检查其内部状态
            if (bllVInstance != null) {
                JSONObject instanceState = new JSONObject();
                for (java.lang.reflect.Field f : bllVClass.getDeclaredFields()) {
                    if (java.lang.reflect.Modifier.isStatic(f.getModifiers())) continue;
                    f.setAccessible(true);
                    try {
                        Object val = f.get(bllVInstance);
                        if (val == null) {
                            instanceState.put(f.getName(), "null");
                        } else if (val instanceof Number || val instanceof Boolean || val instanceof String) {
                            instanceState.put(f.getName(), val.toString());
                        } else {
                            instanceState.put(f.getName(), val.getClass().getName() + "@" + Integer.toHexString(System.identityHashCode(val)));
                        }
                    } catch (Throwable e) {
                        instanceState.put(f.getName(), "ERROR");
                    }
                }
                result.put("bllVInstanceState", instanceState);
            }

            // 3. 尝试通过静态字段获取 bll.v 实例（方案 1）
            JSONObject singletonProbe = new JSONObject();
            for (java.lang.reflect.Field f : bllVClass.getDeclaredFields()) {
                if (java.lang.reflect.Modifier.isStatic(f.getModifiers())) {
                    f.setAccessible(true);
                    try {
                        Object val = f.get(null);
                        if (val != null && bllVClass.isInstance(val)) {
                            singletonProbe.put("found", true);
                            singletonProbe.put("fieldName", f.getName());
                            singletonProbe.put("sameAsCapture", val == bllVInstance);
                        }
                    } catch (Throwable ignored) {}
                }
            }
            if (!singletonProbe.has("found")) {
                singletonProbe.put("found", false);
                singletonProbe.put("note", "No static singleton field found in bll.v");
            }
            result.put("singletonProbe", singletonProbe);

            // 4. 深入探测静态 ConcurrentHashMap t（可能是 bll.v 实例缓存池）
            JSONObject hashMapProbe = new JSONObject();
            try {
                java.lang.reflect.Field tField = bllVClass.getDeclaredField("t");
                tField.setAccessible(true);
                Object hashMap = tField.get(null);
                if (hashMap != null) {
                    // 获取 size
                    Method sizeMethod = hashMap.getClass().getMethod("size");
                    int mapSize = (int) sizeMethod.invoke(hashMap);
                    hashMapProbe.put("size", mapSize);

                    // 遍历 entries
                    Method entrySetMethod = hashMap.getClass().getMethod("entrySet");
                    java.util.Set<?> entries = (java.util.Set<?>) entrySetMethod.invoke(hashMap);
                    JSONArray entriesArr = new JSONArray();
                    for (Object entry : entries) {
                        Method getKey = entry.getClass().getMethod("getKey");
                        Method getValue = entry.getClass().getMethod("getValue");
                        Object key = getKey.invoke(entry);
                        Object value = getValue.invoke(entry);
                        JSONObject e = new JSONObject();
                        e.put("key", key != null ? key.toString() : "null");
                        e.put("keyType", key != null ? key.getClass().getName() : "null");
                        e.put("valueType", value != null ? value.getClass().getName() : "null");
                        if (value != null && bllVClass.isInstance(value)) {
                            e.put("isBllVInstance", true);
                            // 获取这个实例的 bookId (judian 字段)
                            try {
                                java.lang.reflect.Field judianField = bllVClass.getDeclaredField("judian");
                                judianField.setAccessible(true);
                                e.put("bookId", judianField.get(value));
                            } catch (Throwable ignored) {}
                        }
                        entriesArr.put(e);
                        if (entriesArr.length() >= 10) break;
                    }
                    hashMapProbe.put("entries", entriesArr);
                }
            } catch (NoSuchFieldException e) {
                hashMapProbe.put("error", "field t not found");
            } catch (Throwable e) {
                hashMapProbe.put("error", e.getMessage());
            }
            result.put("staticHashMapT", hashMapProbe);

            // 5. 尝试无参构造 bll.v（方案 1 备选）
            JSONObject ctorProbe = new JSONObject();
            try {
                java.lang.reflect.Constructor<?> noArgCtor = bllVClass.getDeclaredConstructor();
                noArgCtor.setAccessible(true);
                ctorProbe.put("hasNoArgConstructor", true);
            } catch (NoSuchMethodException e) {
                ctorProbe.put("hasNoArgConstructor", false);
            }

            // 分析有参构造函数的参数
            for (java.lang.reflect.Constructor<?> ctor : bllVClass.getDeclaredConstructors()) {
                Class<?>[] params = ctor.getParameterTypes();
                JSONObject ctorInfo = new JSONObject();
                JSONArray paramList = new JSONArray();
                for (Class<?> p : params) {
                    paramList.put(p.getName());
                }
                ctorInfo.put("params", paramList);
                ctorInfo.put("paramCount", params.length);
                ctorProbe.put("ctor_" + params.length, ctorInfo);
            }
            result.put("constructorProbe", ctorProbe);

            // 6. 探测 callback.b 类（构造 bll.v 需要的回调参数）
            JSONObject callbackProbe = new JSONObject();
            try {
                Class<?> callbackClass = cl.loadClass("com.qidian.QDReader.component.bll.callback.b");
                callbackProbe.put("exists", true);
                callbackProbe.put("isInterface", callbackClass.isInterface());

                // 如果是接口，列出方法
                if (callbackClass.isInterface()) {
                    JSONArray methods = new JSONArray();
                    for (Method m : callbackClass.getDeclaredMethods()) {
                        StringBuilder sb = new StringBuilder(m.getName() + "(");
                        for (Class<?> p : m.getParameterTypes()) {
                            if (sb.charAt(sb.length() - 1) != '(') sb.append(", ");
                            sb.append(p.getSimpleName());
                        }
                        sb.append(") -> ").append(m.getReturnType().getSimpleName());
                        methods.put(sb.toString());
                    }
                    callbackProbe.put("methods", methods);
                }

                // 查看已有实例的 callback.b 实现类
                if (bllVInstance != null) {
                    java.lang.reflect.Field bField = bllVClass.getDeclaredField("b");
                    bField.setAccessible(true);
                    Object callback = bField.get(bllVInstance);
                    if (callback != null) {
                        callbackProbe.put("implementationClass", callback.getClass().getName());
                    }
                }
            } catch (Throwable e) {
                callbackProbe.put("error", e.getMessage());
            }
            result.put("callbackProbe", callbackProbe);

            // 7. 探索方案 3：搜索哪些类会创建 bll.v 实例
            JSONObject creatorProbe = new JSONObject();
            // 检查 bll.v 构造器的最后一个参数 callback.b 的实现类
            // 从那个实现类追踪到谁创建了 bll.v
            String[] managerCandidates = {
                "com.qidian.QDReader.component.bll.manager.v1",
                "com.qidian.QDReader.component.bll.manager.v1$judian",
                "com.qidian.QDReader.component.bll.manager.a",
                "com.qidian.QDReader.component.bll.manager.b",
                "com.qidian.QDReader.component.bll.ReadingManager",
                "com.qidian.QDReader.component.bll.BookContentManager",
                "com.qidian.QDReader.component.bll.ChapterManager",
                "com.qidian.QDReader.component.reader.ReaderManager",
                "com.qidian.QDReader.component.reader.ReadingController",
            };
            for (String className : managerCandidates) {
                try {
                    Class<?> c = cl.loadClass(className);
                    JSONObject info = new JSONObject();
                    info.put("exists", true);

                    // 查看是否有创建 bll.v 的方法或字段
                    boolean hasBllVRef = false;
                    for (java.lang.reflect.Field f : c.getDeclaredFields()) {
                        if (f.getType() == bllVClass) {
                            info.put("bllVField", f.getName());
                            hasBllVRef = true;
                            f.setAccessible(true);
                            if (java.lang.reflect.Modifier.isStatic(f.getModifiers())) {
                                Object val = f.get(null);
                                info.put("staticBllV", val != null ? "non-null" : "null");
                            }
                        }
                    }
                    for (Method m : c.getDeclaredMethods()) {
                        if (m.getReturnType() == bllVClass) {
                            info.put("returnsBllV", m.getName() + "(" + m.getParameterTypes().length + ")");
                            hasBllVRef = true;
                        }
                    }
                    if (hasBllVRef) {
                        creatorProbe.put(className, info);
                    }
                } catch (ClassNotFoundException ignored) {
                } catch (Throwable e) {
                    creatorProbe.put(className, "ERROR: " + e.getMessage());
                }
            }
            result.put("creatorProbe", creatorProbe);

            // 5. 分析 ChapterItem 类
            try {
                Class<?> chapterItemClass = cl.loadClass("com.qidian.QDReader.repository.entity.ChapterItem");
                JSONObject ciInfo = new JSONObject();

                // 构造函数
                JSONArray ciCtors = new JSONArray();
                for (java.lang.reflect.Constructor<?> ctor : chapterItemClass.getDeclaredConstructors()) {
                    StringBuilder sb = new StringBuilder("(");
                    for (Class<?> p : ctor.getParameterTypes()) {
                        if (sb.length() > 1) sb.append(", ");
                        sb.append(p.getSimpleName());
                    }
                    sb.append(")");
                    ciCtors.put(sb.toString());
                }
                ciInfo.put("constructors", ciCtors);
                ciInfo.put("hasNoArgConstructor", false);
                try {
                    chapterItemClass.getDeclaredConstructor();
                    ciInfo.put("hasNoArgConstructor", true);
                } catch (NoSuchMethodException ignored) {}

                // 测试创建空 ChapterItem
                try {
                    Object testItem = chapterItemClass.newInstance();
                    ciInfo.put("canCreateEmpty", true);
                } catch (Throwable e) {
                    ciInfo.put("canCreateEmpty", false);
                    ciInfo.put("createError", e.getMessage());
                }

                result.put("chapterItem", ciInfo);
            } catch (Throwable e) {
                result.put("chapterItem", "ERROR: " + e.getMessage());
            }

            // 6. 探索方案 3：寻找阅读模块初始化入口
            JSONObject initProbe = new JSONObject();
            // 搜索可能持有 bll.v 引用的类
            String[] candidateClasses = {
                "com.qidian.QDReader.ui.activity.QDReaderActivity",
                "com.qidian.QDReader.ui.activity.QDNewReaderActivity",
                "com.qidian.QDReader.component.bll.manager",
                "com.qidian.QDReader.component.bll.v$Companion",
                "com.qidian.QDReader.component.bll.v$a",
            };
            for (String className : candidateClasses) {
                try {
                    Class<?> c = cl.loadClass(className);
                    JSONObject classInfo = new JSONObject();
                    classInfo.put("exists", true);

                    // 查看该类中是否有 bll.v 类型的字段
                    for (java.lang.reflect.Field f : c.getDeclaredFields()) {
                        if (f.getType() == bllVClass || f.getType().getName().contains("bll.v")) {
                            f.setAccessible(true);
                            classInfo.put("hasBllVField", f.getName());
                            try {
                                if (java.lang.reflect.Modifier.isStatic(f.getModifiers())) {
                                    Object val = f.get(null);
                                    classInfo.put("staticBllVValue", val != null ? "non-null" : "null");
                                }
                            } catch (Throwable ignored) {}
                        }
                    }

                    // 查看是否有返回 bll.v 的方法
                    for (Method m : c.getDeclaredMethods()) {
                        if (m.getReturnType() == bllVClass) {
                            classInfo.put("hasBllVMethod", m.getName() + "(" + m.getParameterTypes().length + " params)");
                        }
                    }

                    initProbe.put(className, classInfo);
                } catch (ClassNotFoundException e) {
                    initProbe.put(className, "NOT_FOUND");
                } catch (Throwable e) {
                    initProbe.put(className, "ERROR: " + e.getMessage());
                }
            }

            // 搜索 bll 包下的所有相关类
            try {
                // 遍历 bll.v 的内部类
                Class<?>[] innerClasses = bllVClass.getDeclaredClasses();
                JSONArray innerClassList = new JSONArray();
                for (Class<?> inner : innerClasses) {
                    JSONObject innerInfo = new JSONObject();
                    innerInfo.put("name", inner.getName());
                    // 查看内部类是否有创建/获取外部类实例的方法
                    for (Method m : inner.getDeclaredMethods()) {
                        if (m.getReturnType() == bllVClass) {
                            innerInfo.put("returnsBllV", m.getName());
                        }
                    }
                    innerClassList.put(innerInfo);
                }
                initProbe.put("innerClasses", innerClassList);
            } catch (Throwable e) {
                initProbe.put("innerClasses", "ERROR: " + e.getMessage());
            }

            result.put("initProbe", initProbe);

            return result.toString(1);
        } catch (Throwable e) {
            try {
                return new JSONObject()
                        .put("success", false)
                        .put("error", e.getMessage())
                        .toString();
            } catch (Throwable e2) {
                return "{\"success\":false,\"error\":\"" + e.getMessage() + "\"}";
            }
        }
    }

    // ==================== VIP Content RPC ====================

    private static String handleGetVipContent(String bookId, String chapterId) {
        try {
            if (savedOkHttpClient == null) {
                return new JSONObject().put("success", false).put("error", "OkHttpClient not available").toString();
            }

            JSONObject result = new JSONObject();
            result.put("bookId", bookId);
            result.put("chapterId", chapterId);

            String vipUrl = "https://druidv6.if.qidian.com/argus/api/v4/bookcontent/getvipcontent";

            // 尝试 POST (FormBody: bookId + chapterId)
            try {
                Class<?> formBodyBuilderClass = appClassLoader.loadClass("okhttp3.FormBody$Builder");
                Object formBuilder = formBodyBuilderClass.newInstance();
                Method addMethod = formBodyBuilderClass.getDeclaredMethod("add", String.class, String.class);
                addMethod.invoke(formBuilder, "bookId", bookId);
                addMethod.invoke(formBuilder, "chapterId", chapterId);
                Object formBody = formBodyBuilderClass.getDeclaredMethod("build").invoke(formBuilder);

                Class<?> requestBuilderClass = appClassLoader.loadClass("okhttp3.Request$Builder");
                Object reqBuilder = requestBuilderClass.newInstance();
                requestBuilderClass.getDeclaredMethod("url", String.class).invoke(reqBuilder, vipUrl);

                Class<?> requestBodyClass = appClassLoader.loadClass("okhttp3.RequestBody");
                requestBuilderClass.getDeclaredMethod("post", requestBodyClass).invoke(reqBuilder, formBody);

                Object httpRequest = requestBuilderClass.getDeclaredMethod("build").invoke(reqBuilder);
                Class<?> requestClass = appClassLoader.loadClass("okhttp3.Request");
                Object call = savedOkHttpClient.getClass().getMethod("newCall", requestClass)
                        .invoke(savedOkHttpClient, httpRequest);
                Object response = call.getClass().getMethod("execute").invoke(call);

                int code = (int) response.getClass().getDeclaredMethod("code").invoke(response);
                Object responseBody = response.getClass().getDeclaredMethod("body").invoke(response);
                byte[] bodyBytes = (byte[]) responseBody.getClass().getMethod("bytes").invoke(responseBody);

                result.put("postHttpCode", code);
                result.put("postBodyLength", bodyBytes.length);

                String bodyStr = new String(bodyBytes, java.nio.charset.StandardCharsets.UTF_8);
                // 检查是否是 JSON
                if (bodyStr.startsWith("{")) {
                    result.put("postResponse", new JSONObject(bodyStr));
                    result.put("postEncrypted", false);
                } else {
                    result.put("postEncrypted", true);
                    result.put("postBodyPreview", bodyStr.substring(0, Math.min(100, bodyStr.length())));
                }
            } catch (Throwable e) {
                result.put("postError", e.getClass().getSimpleName() + ": " + e.getMessage());
            }

            // 尝试 POST (JSON body)
            try {
                String jsonBody = "{\"bookId\":\"" + bookId + "\",\"chapterId\":\"" + chapterId + "\"}";
                Class<?> mediaTypeClass = appClassLoader.loadClass("okhttp3.MediaType");
                Method parseMethod = null;
                for (Method m : mediaTypeClass.getDeclaredMethods()) {
                    if ("parse".equals(m.getName()) && m.getParameterTypes().length == 1
                            && m.getParameterTypes()[0] == String.class) {
                        parseMethod = m;
                        break;
                    }
                }
                Object jsonMediaType = parseMethod.invoke(null, "application/json; charset=utf-8");

                Class<?> requestBodyClass = appClassLoader.loadClass("okhttp3.RequestBody");
                Method createMethod = null;
                for (Method m : requestBodyClass.getDeclaredMethods()) {
                    if ("create".equals(m.getName()) && m.getParameterTypes().length == 2) {
                        Class<?>[] pts = m.getParameterTypes();
                        if (pts[0] == mediaTypeClass && pts[1] == String.class) {
                            createMethod = m;
                            break;
                        }
                    }
                }
                if (createMethod == null) {
                    // 尝试反序参数 (String, MediaType) 或其他签名
                    for (Method m : requestBodyClass.getDeclaredMethods()) {
                        if ("create".equals(m.getName()) && m.getParameterTypes().length == 2) {
                            Class<?>[] pts = m.getParameterTypes();
                            if (pts[0] == String.class && pts[1] == mediaTypeClass) {
                                createMethod = m;
                                Object jsonRequestBody = createMethod.invoke(null, jsonBody, jsonMediaType);
                                Class<?> reqBuilderClass = appClassLoader.loadClass("okhttp3.Request$Builder");
                                Object rb = reqBuilderClass.newInstance();
                                reqBuilderClass.getDeclaredMethod("url", String.class).invoke(rb, vipUrl);
                                reqBuilderClass.getDeclaredMethod("post", requestBodyClass).invoke(rb, jsonRequestBody);
                                Object req = reqBuilderClass.getDeclaredMethod("build").invoke(rb);
                                Class<?> reqClass = appClassLoader.loadClass("okhttp3.Request");
                                Object c = savedOkHttpClient.getClass().getMethod("newCall", reqClass).invoke(savedOkHttpClient, req);
                                Object resp = c.getClass().getMethod("execute").invoke(c);
                                int code = (int) resp.getClass().getDeclaredMethod("code").invoke(resp);
                                Object respBody = resp.getClass().getDeclaredMethod("body").invoke(resp);
                                byte[] bytes = (byte[]) respBody.getClass().getMethod("bytes").invoke(respBody);
                                String bodyStr = new String(bytes, java.nio.charset.StandardCharsets.UTF_8);
                                result.put("jsonPostHttpCode", code);
                                result.put("jsonPostBodyLength", bytes.length);
                                if (bodyStr.startsWith("{")) {
                                    result.put("jsonPostResponse", new JSONObject(bodyStr));
                                }
                                break;
                            }
                        }
                    }
                } else {
                    Object jsonRequestBody = createMethod.invoke(null, jsonMediaType, jsonBody);
                    Class<?> reqBuilderClass = appClassLoader.loadClass("okhttp3.Request$Builder");
                    Object rb = reqBuilderClass.newInstance();
                    reqBuilderClass.getDeclaredMethod("url", String.class).invoke(rb, vipUrl);
                    reqBuilderClass.getDeclaredMethod("post", requestBodyClass).invoke(rb, jsonRequestBody);
                    Object req = reqBuilderClass.getDeclaredMethod("build").invoke(rb);
                    Class<?> reqClass = appClassLoader.loadClass("okhttp3.Request");
                    Object c = savedOkHttpClient.getClass().getMethod("newCall", reqClass).invoke(savedOkHttpClient, req);
                    Object resp = c.getClass().getMethod("execute").invoke(c);
                    int code = (int) resp.getClass().getDeclaredMethod("code").invoke(resp);
                    Object respBody = resp.getClass().getDeclaredMethod("body").invoke(resp);
                    byte[] bytes = (byte[]) respBody.getClass().getMethod("bytes").invoke(respBody);
                    String bodyStr = new String(bytes, java.nio.charset.StandardCharsets.UTF_8);
                    result.put("jsonPostHttpCode", code);
                    result.put("jsonPostBodyLength", bytes.length);
                    if (bodyStr.startsWith("{")) {
                        result.put("jsonPostResponse", new JSONObject(bodyStr));
                    }
                }
            } catch (Throwable e) {
                result.put("jsonPostError", e.getClass().getSimpleName() + ": " + e.getMessage());
            }

            // 尝试 POST with more params (FormBody)
            try {
                Class<?> formBodyBuilderClass2 = appClassLoader.loadClass("okhttp3.FormBody$Builder");
                Object fb2 = formBodyBuilderClass2.newInstance();
                Method add2 = formBodyBuilderClass2.getDeclaredMethod("add", String.class, String.class);
                add2.invoke(fb2, "bookId", bookId);
                add2.invoke(fb2, "chapterId", chapterId);
                add2.invoke(fb2, "isPreview", "1");
                add2.invoke(fb2, "type", "0");
                Object formBody2 = formBodyBuilderClass2.getDeclaredMethod("build").invoke(fb2);

                Class<?> reqBuilderClass = appClassLoader.loadClass("okhttp3.Request$Builder");
                Object rb = reqBuilderClass.newInstance();
                reqBuilderClass.getDeclaredMethod("url", String.class).invoke(rb, vipUrl);
                Class<?> requestBodyClass = appClassLoader.loadClass("okhttp3.RequestBody");
                reqBuilderClass.getDeclaredMethod("post", requestBodyClass).invoke(rb, formBody2);
                Object req = reqBuilderClass.getDeclaredMethod("build").invoke(rb);
                Class<?> reqClass = appClassLoader.loadClass("okhttp3.Request");
                Object c = savedOkHttpClient.getClass().getMethod("newCall", reqClass).invoke(savedOkHttpClient, req);
                Object resp = c.getClass().getMethod("execute").invoke(c);
                int code = (int) resp.getClass().getDeclaredMethod("code").invoke(resp);
                Object respBody = resp.getClass().getDeclaredMethod("body").invoke(resp);
                byte[] bytes = (byte[]) respBody.getClass().getMethod("bytes").invoke(respBody);
                String bodyStr = new String(bytes, java.nio.charset.StandardCharsets.UTF_8);
                result.put("postPreviewHttpCode", code);
                result.put("postPreviewBodyLength", bytes.length);
                if (bodyStr.startsWith("{")) {
                    result.put("postPreviewResponse", new JSONObject(bodyStr));
                }
            } catch (Throwable e) {
                result.put("postPreviewError", e.getClass().getSimpleName() + ": " + e.getMessage());
            }

            result.put("success", true);
            return result.toString(1);
        } catch (Throwable e) {
            try {
                return new JSONObject().put("success", false).put("error", e.getMessage()).toString();
            } catch (Throwable e2) {
                return "{\"success\":false}";
            }
        }
    }

    // ==================== Chapter Content RPC ====================

    private static final String SAFE_GET_CONTENT_URL = "https://druidv6.if.qidian.com/argus/api/v2/bookcontent/safegetcontent";

    private static String handleGetChapterContent(String bookId, String chapterId) {
        try {
            Log.i(TAG, "RPC: getChapterContent - bookId=" + bookId + ", chapterId=" + chapterId);

            // 优先检查自动捕获的缓存
            String cachedContent = capturedChapterContents.get(chapterId);
            if (cachedContent != null) {
                Log.i(TAG, "Found auto-captured content for chapterId=" + chapterId);
                JSONObject result = new JSONObject()
                        .put("success", true)
                        .put("data", new JSONObject()
                                .put("bookId", bookId)
                                .put("chapterId", chapterId)
                                .put("content", cachedContent)
                                .put("method", "auto-captured from bll.v")
                                .put("contentLength", cachedContent.length()));
                String resultStr = result.toString(1);
                return resultStr.replace("\\/", "/");
            }

            if (savedOkHttpClient == null) {
                return new JSONObject()
                        .put("success", false)
                        .put("error", "OkHttpClient not saved yet")
                        .toString();
            }
            if (appClassLoader == null) {
                return new JSONObject()
                        .put("success", false)
                        .put("error", "AppClassLoader not available")
                        .toString();
            }

            String url = SAFE_GET_CONTENT_URL + "?bookId=" + bookId + "&chapterId=" + chapterId;
            Log.i(TAG, "ChapterContent URL: " + url);

            // 反射构造 GET 请求
            Class<?> requestBuilderClass = appClassLoader.loadClass("okhttp3.Request$Builder");
            Object reqBuilder = requestBuilderClass.newInstance();
            requestBuilderClass.getDeclaredMethod("url", String.class).invoke(reqBuilder, url);
            requestBuilderClass.getDeclaredMethod("get").invoke(reqBuilder);
            Object httpRequest = requestBuilderClass.getDeclaredMethod("build").invoke(reqBuilder);

            // client.newCall(request).execute()
            Class<?> requestClass = appClassLoader.loadClass("okhttp3.Request");
            Object call = savedOkHttpClient.getClass().getMethod("newCall", requestClass)
                    .invoke(savedOkHttpClient, httpRequest);
            Object response = call.getClass().getMethod("execute").invoke(call);

            int code = (int) response.getClass().getDeclaredMethod("code").invoke(response);

            // 读取 response body 为 bytes（因为可能是加密的二进制数据）
            Object responseBody = response.getClass().getDeclaredMethod("body").invoke(response);

            JSONObject result = new JSONObject();
            result.put("success", true);
            result.put("bookId", bookId);
            result.put("chapterId", chapterId);
            result.put("httpCode", code);

            if (responseBody != null) {
                // 先尝试读取为 bytes
                java.lang.reflect.Method bytesMethod = responseBody.getClass().getMethod("bytes");
                byte[] bodyBytes = (byte[]) bytesMethod.invoke(responseBody);
                result.put("bodyLength", bodyBytes.length);

                Log.i(TAG, "ChapterContent response: code=" + code + ", bodyLen=" + bodyBytes.length);

                if (bodyBytes.length > 0) {
                    // 检查是否是 JSON 文本（明文响应）
                    String bodyStr = new String(bodyBytes, java.nio.charset.StandardCharsets.UTF_8);
                    if (bodyStr.startsWith("{")) {
                        // JSON 响应（错误信息或未加密内容）
                        try {
                            result.put("response", new JSONObject(bodyStr));
                            result.put("encrypted", false);
                        } catch (Throwable e) {
                            result.put("response", bodyStr);
                            result.put("encrypted", false);
                        }
                    } else {
                        // 加密的二进制数据，尝试通过 App 内部方法解密
                        result.put("encrypted", true);

                        // 将加密数据保存为 .qd 文件，然后调用 bll.v 触发 App 内部解密
                        String content = triggerAppDecrypt(bookId, chapterId, bodyBytes);
                        if (content != null) {
                            result.put("decrypted", true);
                            result.put("content", content);
                            result.put("contentLength", content.length());
                            result.put("method", "bll.v internal decrypt");
                        } else {
                            result.put("decrypted", false);
                            result.put("error", "Got encrypted data but failed to decrypt. bodyLen=" + bodyBytes.length);
                            StringBuilder hexHead = new StringBuilder();
                            for (int i = 0; i < Math.min(32, bodyBytes.length); i++) {
                                hexHead.append(String.format("%02x", bodyBytes[i] & 0xff));
                            }
                            result.put("hexHead", hexHead.toString());
                        }
                    }
                }
            }

            String resultStr = result.toString(1);
            return resultStr.replace("\\/", "/");

        } catch (Throwable e) {
            Log.e(TAG, "handleGetChapterContent failed", e);
            try {
                return new JSONObject()
                        .put("success", false)
                        .put("error", e.getClass().getSimpleName() + ": " + e.getMessage())
                        .toString();
            } catch (Throwable e2) {
                return "{\"success\":false,\"error\":\"Unknown error\"}";
            }
        }
    }

    // ==================== Paragraph Comment (段评) RPC ====================

    private static final String CHAPTER_REVIEW_SUMMARY_URL = "https://druidv6.if.qidian.com/argus/api/v1/chapterreview/getchapterrepagesummary";
    private static final String PARAGRAPH_COMMENTS_URL = "https://druidv6.if.qidian.com/argus/api/v2/chapterreview/getparagraphscomments";
    private static final String COMMENT_REPLIES_URL = "https://druidv6.if.qidian.com/argus/api/v2/chapterreview/getparagraphscommentsreview";

    /**
     * 通用 GET 请求执行器（复用 savedOkHttpClient 带签名拦截器）
     */
    private static String executeGetRequest(String url) throws Exception {
        // 请求节流：如果距离上次请求时间太短，等待一段时间
        long now = System.currentTimeMillis();
        long timeSinceLastRequest = now - lastRequestTime;
        if (lastRequestTime > 0 && timeSinceLastRequest < MIN_REQUEST_INTERVAL_MS) {
            long sleepTime = MIN_REQUEST_INTERVAL_MS - timeSinceLastRequest;
            try {
                Thread.sleep(sleepTime);
            } catch (InterruptedException ie) {
                Thread.currentThread().interrupt();
            }
        }

        if (savedOkHttpClient == null) {
            throw new IllegalStateException("OkHttpClient not saved yet");
        }
        if (appClassLoader == null) {
            throw new IllegalStateException("AppClassLoader not available");
        }

        Class<?> requestBuilderClass = appClassLoader.loadClass("okhttp3.Request$Builder");
        Object reqBuilder = requestBuilderClass.newInstance();

        java.lang.reflect.Method urlMethod = requestBuilderClass.getDeclaredMethod("url", String.class);
        urlMethod.invoke(reqBuilder, url);

        java.lang.reflect.Method getMethod = requestBuilderClass.getDeclaredMethod("get");
        getMethod.invoke(reqBuilder);

        java.lang.reflect.Method buildMethod = requestBuilderClass.getDeclaredMethod("build");
        Object httpRequest = buildMethod.invoke(reqBuilder);

        Class<?> requestClass = appClassLoader.loadClass("okhttp3.Request");
        java.lang.reflect.Method newCallMethod = savedOkHttpClient.getClass().getMethod("newCall", requestClass);
        Object call = newCallMethod.invoke(savedOkHttpClient, httpRequest);
        java.lang.reflect.Method executeMethod = call.getClass().getMethod("execute");
        Object response = executeMethod.invoke(call);

        int code = (int) response.getClass().getDeclaredMethod("code").invoke(response);
        Object responseBody = response.getClass().getDeclaredMethod("body").invoke(response);
        String bodyStr = "";
        if (responseBody != null) {
            bodyStr = (String) responseBody.getClass().getMethod("string").invoke(responseBody);
        }

        // 更新上次请求时间（用于节流）
        lastRequestTime = System.currentTimeMillis();

        if (code != 200) {
            throw new RuntimeException("HTTP " + code + ": " + bodyStr);
        }
        return bodyStr;
    }

    /**
     * 获取章节段评摘要：返回各段落的 paragraphId 和评论数量
     */
    private static String handleGetChapterReviewSummary(String bookId, String chapterId) {
        try {
            Log.i(TAG, "RPC: getChapterReviewSummary - bookId=" + bookId + ", chapterId=" + chapterId);

            String url = CHAPTER_REVIEW_SUMMARY_URL + "?bookId=" + bookId
                    + "&chapterId=" + chapterId + "&useImei=0&strategy=0";

            String bodyStr = executeGetRequest(url);
            Log.i(TAG, "ChapterReviewSummary response bodyLen=" + bodyStr.length());

            JSONObject result = new JSONObject();
            result.put("success", true);
            result.put("bookId", bookId);
            result.put("chapterId", chapterId);

            if (!bodyStr.isEmpty()) {
                try {
                    result.put("response", new JSONObject(bodyStr));
                } catch (Throwable e) {
                    result.put("response", bodyStr);
                }
            }

            return result.toString(1).replace("\\/", "/");

        } catch (Throwable e) {
            Log.e(TAG, "handleGetChapterReviewSummary failed", e);
            try {
                return new JSONObject()
                        .put("success", false)
                        .put("error", e.getClass().getSimpleName() + ": " + e.getMessage())
                        .toString();
            } catch (Throwable e2) {
                return "{\"success\":false,\"error\":\"Unknown error\"}";
            }
        }
    }

    /**
     * 轻量评论摘要：1 次 API 请求，返回总评论数和各段落评论数
     */
    private static String handleGetCommentSummary(String bookId, String chapterId) {
        try {
            Log.i(TAG, "RPC: getCommentSummary - bookId=" + bookId + ", chapterId=" + chapterId);

            String url = CHAPTER_REVIEW_SUMMARY_URL + "?bookId=" + bookId
                    + "&chapterId=" + chapterId + "&useImei=0&strategy=0";
            String bodyStr = executeGetRequest(url);
            JSONObject summaryJson = new JSONObject(bodyStr);

            JSONObject summaryData = summaryJson.optJSONObject("Data");
            JSONArray paragraphs = new JSONArray();
            int totalComments = 0;

            if (summaryData != null) {
                JSONObject commentCounts = summaryData.optJSONObject("Getparagraphscommentcounts");
                JSONArray dataList = (commentCounts != null) ? commentCounts.optJSONArray("DataList") : null;
                if (dataList != null) {
                    for (int i = 0; i < dataList.length(); i++) {
                        JSONObject item = dataList.getJSONObject(i);
                        int pId = item.optInt("ParagraphId", -1);
                        int count = item.optInt("CommentCount", 0);
                        JSONObject p = new JSONObject();
                        p.put("paragraphId", pId);
                        p.put("commentCount", count);
                        paragraphs.put(p);
                        totalComments += count;
                    }
                }
            }

            JSONObject result = new JSONObject();
            result.put("success", true);
            result.put("bookId", bookId);
            result.put("chapterId", chapterId);
            result.put("totalComments", totalComments);
            result.put("paragraphs", paragraphs);

            return result.toString(1).replace("\\/", "/");

        } catch (Throwable e) {
            Log.e(TAG, "handleGetCommentSummary failed", e);
            try {
                return new JSONObject()
                        .put("success", false)
                        .put("error", e.getClass().getSimpleName() + ": " + e.getMessage())
                        .toString();
            } catch (Throwable e2) {
                return "{\"success\":false,\"error\":\"Unknown error\"}";
            }
        }
    }

    /**
     * 获取某段落的文字评论列表
     */
    private static String handleGetParagraphComments(String bookId, String chapterId, int paragraphId, int page, int pageSize, int type) {
        try {
            Log.i(TAG, "RPC: getParagraphComments - bookId=" + bookId + ", chapterId=" + chapterId
                    + ", paragraphId=" + paragraphId + ", page=" + page + ", type=" + type);

            String url = PARAGRAPH_COMMENTS_URL + "?bookId=" + bookId
                    + "&chapterId=" + chapterId
                    + "&paragraphId=" + paragraphId
                    + "&pg=" + page
                    + "&pz=" + pageSize
                    + "&type=" + type + "&anchorId=0&from=0";

            String bodyStr = executeGetRequest(url);
            Log.i(TAG, "ParagraphComments response bodyLen=" + bodyStr.length());

            JSONObject result = new JSONObject();
            result.put("success", true);
            result.put("bookId", bookId);
            result.put("chapterId", chapterId);
            result.put("paragraphId", paragraphId);
            result.put("page", page);

            if (!bodyStr.isEmpty()) {
                try {
                    result.put("response", new JSONObject(bodyStr));
                } catch (Throwable e) {
                    result.put("response", bodyStr);
                }
            }

            return result.toString(1).replace("\\/", "/");

        } catch (Throwable e) {
            Log.e(TAG, "handleGetParagraphComments failed", e);
            try {
                return new JSONObject()
                        .put("success", false)
                        .put("error", e.getClass().getSimpleName() + ": " + e.getMessage())
                        .toString();
            } catch (Throwable e2) {
                return "{\"success\":false,\"error\":\"Unknown error\"}";
            }
        }
    }

    /**
     * 通用 POST 请求（FormBody）
     */
    private static String handleExecutePost(String url, JSONObject params) {
        try {
            if (savedOkHttpClient == null) {
                return new JSONObject().put("success", false).put("error", "OkHttpClient not available").toString();
            }

            Class<?> formBodyBuilderClass = appClassLoader.loadClass("okhttp3.FormBody$Builder");
            Object formBuilder = formBodyBuilderClass.newInstance();
            Method addMethod = formBodyBuilderClass.getDeclaredMethod("add", String.class, String.class);

            if (params != null) {
                Iterator<String> keys = params.keys();
                while (keys.hasNext()) {
                    String key = keys.next();
                    addMethod.invoke(formBuilder, key, params.optString(key, ""));
                }
            }
            Object formBody = formBodyBuilderClass.getDeclaredMethod("build").invoke(formBuilder);

            Class<?> requestBuilderClass = appClassLoader.loadClass("okhttp3.Request$Builder");
            Class<?> requestClass = appClassLoader.loadClass("okhttp3.Request");
            Class<?> requestBodyClass = appClassLoader.loadClass("okhttp3.RequestBody");

            Object reqBuilder = requestBuilderClass.newInstance();
            requestBuilderClass.getDeclaredMethod("url", String.class).invoke(reqBuilder, url);
            requestBuilderClass.getDeclaredMethod("post", requestBodyClass).invoke(reqBuilder, formBody);
            Object httpRequest = requestBuilderClass.getDeclaredMethod("build").invoke(reqBuilder);

            Object call = savedOkHttpClient.getClass().getMethod("newCall", requestClass)
                    .invoke(savedOkHttpClient, httpRequest);
            Object response = call.getClass().getMethod("execute").invoke(call);

            int code = (int) response.getClass().getDeclaredMethod("code").invoke(response);
            Object responseBody = response.getClass().getDeclaredMethod("body").invoke(response);
            String bodyStr = (String) responseBody.getClass().getMethod("string").invoke(responseBody);

            return new JSONObject()
                    .put("success", true)
                    .put("httpCode", code)
                    .put("response", bodyStr)
                    .toString();

        } catch (Throwable e) {
            try {
                return new JSONObject().put("success", false).put("error", e.getMessage()).toString();
            } catch (Throwable e2) {
                return "{\"success\":false,\"error\":\"Unknown\"}";
            }
        }
    }

    /**
     * 通用 GET 请求
     */
    private static String handleExecuteGet(String url) {
        try {
            if (savedOkHttpClient == null) {
                return new JSONObject().put("success", false).put("error", "OkHttpClient not available").toString();
            }

            Class<?> requestBuilderClass = appClassLoader.loadClass("okhttp3.Request$Builder");
            Class<?> requestClass = appClassLoader.loadClass("okhttp3.Request");

            Object reqBuilder = requestBuilderClass.newInstance();
            requestBuilderClass.getDeclaredMethod("url", String.class).invoke(reqBuilder, url);
            requestBuilderClass.getDeclaredMethod("get").invoke(reqBuilder);
            Object httpRequest = requestBuilderClass.getDeclaredMethod("build").invoke(reqBuilder);

            Object call = savedOkHttpClient.getClass().getMethod("newCall", requestClass)
                    .invoke(savedOkHttpClient, httpRequest);
            Object response = call.getClass().getMethod("execute").invoke(call);

            int code = (int) response.getClass().getDeclaredMethod("code").invoke(response);
            Object responseBody = response.getClass().getDeclaredMethod("body").invoke(response);
            String bodyStr = (String) responseBody.getClass().getMethod("string").invoke(responseBody);

            return new JSONObject()
                    .put("success", true)
                    .put("httpCode", code)
                    .put("response", bodyStr)
                    .toString();

        } catch (Throwable e) {
            try {
                return new JSONObject().put("success", false).put("error", e.getMessage()).toString();
            } catch (Throwable e2) {
                return "{\"success\":false,\"error\":\"Unknown\"}";
            }
        }
    }

    /**
     * 一次性获取章节所有段评：先获取摘要，再逐段获取评论
     */
    private static String handleGetAllParagraphComments(String bookId, String chapterId, int pageSize, int type, boolean includeChapterComments, boolean fetchReplies, JSONArray onlyParagraphs, JSONObject existingReplies) {
        try {
            Log.i(TAG, "RPC: getAllParagraphComments - bookId=" + bookId + ", chapterId=" + chapterId
                    + ", type=" + type + ", includeChapterComments=" + includeChapterComments
                    + ", fetchReplies=" + fetchReplies
                    + ", onlyParagraphs=" + (onlyParagraphs != null ? onlyParagraphs.length() + " ids" : "null")
                    + ", existingReplies=" + (existingReplies != null ? existingReplies.length() + " entries" : "null"));

            // Step 1: 获取段评摘要
            String summaryUrl = CHAPTER_REVIEW_SUMMARY_URL + "?bookId=" + bookId
                    + "&chapterId=" + chapterId + "&useImei=0&strategy=0";
            String summaryBody = executeGetRequest(summaryUrl);
            JSONObject summaryJson = new JSONObject(summaryBody);

            // 从摘要中提取有评论的段落
            JSONObject summaryData = summaryJson.optJSONObject("Data");
            if (summaryData == null) {
                return new JSONObject()
                        .put("success", true)
                        .put("bookId", bookId)
                        .put("chapterId", chapterId)
                        .put("totalParagraphs", 0)
                        .put("paragraphs", new JSONArray())
                        .put("summary", summaryJson)
                        .toString(1).replace("\\/", "/");
            }

            // 构建 onlyParagraphs 过滤集合（null 表示不过滤）
            java.util.HashSet<Integer> filterPids = null;
            if (onlyParagraphs != null && onlyParagraphs.length() > 0) {
                filterPids = new java.util.HashSet<>();
                for (int i = 0; i < onlyParagraphs.length(); i++) {
                    filterPids.add(onlyParagraphs.getInt(i));
                }
            }

            // 收集有评论的段落（从 Getparagraphscommentcounts.DataList 中提取）
            JSONArray paragraphResults = new JSONArray();
            JSONObject commentCounts = summaryData.optJSONObject("Getparagraphscommentcounts");
            JSONArray dataList = (commentCounts != null) ? commentCounts.optJSONArray("DataList") : null;
            if (dataList == null) {
                dataList = new JSONArray();
            }

            int totalComments = 0;
            for (int i = 0; i < dataList.length(); i++) {
                JSONObject item = dataList.getJSONObject(i);
                int pId = item.optInt("ParagraphId", -1);
                int count = item.optInt("CommentCount", 0);
                if (count == 0) continue;
                // ParagraphId=-1 是章评，根据参数决定是否包含
                if (pId < 0 && !includeChapterComments) continue;

                // onlyParagraphs 过滤：非空时只获取指定段落
                if (filterPids != null && !filterPids.contains(pId)) continue;

                // Step 2: 获取该段落的全部评论（自动翻页）
                JSONArray allComments = new JSONArray();
                int currentPage = 1;
                boolean hasMore = true;

                while (hasMore) {
                    String commentsUrl = PARAGRAPH_COMMENTS_URL + "?bookId=" + bookId
                            + "&chapterId=" + chapterId
                            + "&paragraphId=" + pId
                            + "&pg=" + currentPage
                            + "&pz=" + pageSize
                            + "&type=" + type + "&anchorId=0&from=0";
                    String commentsBody = executeGetRequest(commentsUrl);
                    JSONObject commentsJson = new JSONObject(commentsBody);
                    JSONObject commentsData = commentsJson.optJSONObject("Data");

                    if (commentsData != null) {
                        JSONArray items = commentsData.optJSONArray("DataList");
                        if (items != null && items.length() > 0) {
                            for (int j = 0; j < items.length(); j++) {
                                JSONObject comment = items.getJSONObject(j);
                                // 获取评论回复
                                if (fetchReplies && comment.optInt("ReviewCount", 0) > 0) {
                                    long reviewId = comment.optLong("Id", 0);
                                    if (reviewId != 0) {
                                        // 检查 existingReplies：ReviewCount 一致则跳过回复获取
                                        boolean skipReplies = false;
                                        if (existingReplies != null) {
                                            String reviewIdStr = String.valueOf(reviewId);
                                            if (existingReplies.has(reviewIdStr)) {
                                                int oldReviewCount = existingReplies.optInt(reviewIdStr, -1);
                                                if (oldReviewCount == comment.optInt("ReviewCount", 0)) {
                                                    skipReplies = true;
                                                }
                                            }
                                        }
                                        if (!skipReplies) {
                                            JSONArray replies = fetchCommentReplies(bookId, chapterId, pId, reviewId, pageSize);
                                            if (replies.length() > 0) {
                                                comment.put("replies", replies);
                                            }
                                        }
                                        // skipReplies=true 时不带 replies 字段，Python 端从旧数据恢复
                                    }
                                }
                                allComments.put(comment);
                            }
                            // 判断是否还有下一页
                            if (items.length() < pageSize) {
                                hasMore = false;
                            } else {
                                currentPage++;
                            }
                        } else {
                            hasMore = false;
                        }
                    } else {
                        hasMore = false;
                    }

                    // 安全阀：最多 50 页
                    if (currentPage > 50) {
                        Log.w(TAG, "getAllParagraphComments: reached page limit for paragraphId=" + pId);
                        break;
                    }
                }

                JSONObject paragraphEntry = new JSONObject();
                paragraphEntry.put("paragraphId", pId);
                paragraphEntry.put("expectedCount", count);
                paragraphEntry.put("fetchedCount", allComments.length());
                paragraphEntry.put("comments", allComments);
                paragraphResults.put(paragraphEntry);

                totalComments += allComments.length();
                Log.i(TAG, "Paragraph " + pId + ": " + allComments.length() + "/" + count + " comments fetched");
            }

            JSONObject result = new JSONObject();
            result.put("success", true);
            result.put("bookId", bookId);
            result.put("chapterId", chapterId);
            result.put("totalParagraphs", paragraphResults.length());
            result.put("totalComments", totalComments);
            result.put("paragraphs", paragraphResults);

            Log.i(TAG, "getAllParagraphComments done: " + paragraphResults.length()
                    + " paragraphs, " + totalComments + " comments");

            return result.toString(1).replace("\\/", "/");

        } catch (Throwable e) {
            Log.e(TAG, "handleGetAllParagraphComments failed", e);
            try {
                return new JSONObject()
                        .put("success", false)
                        .put("error", e.getClass().getSimpleName() + ": " + e.getMessage())
                        .toString();
            } catch (Throwable e2) {
                return "{\"success\":false,\"error\":\"Unknown error\"}";
            }
        }
    }

    /**
     * 获取某条评论的所有回复（自动翻页）
     */
    private static JSONArray fetchCommentReplies(String bookId, String chapterId, int paragraphId, long rootReviewId, int pageSize) {
        JSONArray allReplies = new JSONArray();
        try {
            int page = 1;
            boolean hasMore = true;
            while (hasMore) {
                String url = COMMENT_REPLIES_URL + "?bookId=" + bookId
                        + "&chapterId=" + chapterId
                        + "&paragraphId=" + paragraphId
                        + "&pg=" + page
                        + "&pz=" + pageSize
                        + "&rootReviewId=" + rootReviewId
                        + "&type=0";
                String body = executeGetRequest(url);
                JSONObject json = new JSONObject(body);
                JSONObject data = json.optJSONObject("Data");
                if (data != null) {
                    JSONArray items = data.optJSONArray("DataList");
                    if (items != null && items.length() > 0) {
                        for (int i = 0; i < items.length(); i++) {
                            allReplies.put(items.getJSONObject(i));
                        }
                        if (items.length() < pageSize) {
                            hasMore = false;
                        } else {
                            page++;
                        }
                    } else {
                        hasMore = false;
                    }
                } else {
                    hasMore = false;
                }
                // 安全阀：最多 10 页
                if (page > 10) {
                    Log.w(TAG, "fetchCommentReplies: reached page limit for rootReviewId=" + rootReviewId);
                    break;
                }
            }
        } catch (Throwable e) {
            Log.e(TAG, "fetchCommentReplies failed for rootReviewId=" + rootReviewId, e);
        }
        return allReplies;
    }

    /**
     * 尝试解密章节内容
     * 先尝试通过 getkey API 获取密钥，再调用 App 的解密方法
     */
    /**
     * 通过 App 内部方法解密章节内容：
     * 1. 保存加密数据为 .qd 文件
     * 2. 调用 bll.v.R 触发 App 内部解密
     * 3. afterCall hook 自动捕获 ChapterContentItem 内容到 capturedChapterContents
     */
    /**
     * 方案 1：通过反射 + 动态代理创建 bll.v 实例
     * 当 App 启动后没有自动恢复阅读状态时使用
     */
    private static Object createBllVInstance(long bookId) {
        try {
            android.app.Application app = (android.app.Application)
                    Class.forName("android.app.ActivityThread")
                            .getMethod("currentApplication")
                            .invoke(null);
            ClassLoader cl = app.getClassLoader();

            Class<?> bllVClass = cl.loadClass("com.qidian.QDReader.component.bll.v");
            Class<?> callbackClass = cl.loadClass("com.qidian.QDReader.component.bll.callback.b");

            // 创建 callback.b 的动态代理（空实现）
            Object proxyCallback = java.lang.reflect.Proxy.newProxyInstance(
                    cl,
                    new Class[]{callbackClass},
                    new java.lang.reflect.InvocationHandler() {
                        @Override
                        public Object invoke(Object proxy, Method method, Object[] args) throws Throwable {
                            Log.i(TAG, "bll.v callback proxy: " + method.getName());
                            return null;
                        }
                    }
            );

            // 尝试 6 参数构造: (long bookId, long cihai, boolean, boolean, String mode, callback.b)
            try {
                java.lang.reflect.Constructor<?> ctor6 = null;
                for (java.lang.reflect.Constructor<?> ctor : bllVClass.getDeclaredConstructors()) {
                    if (ctor.getParameterTypes().length == 6) {
                        ctor6 = ctor;
                        break;
                    }
                }
                if (ctor6 != null) {
                    ctor6.setAccessible(true);
                    Object instance = ctor6.newInstance(bookId, -10000L, false, false, "reader", proxyCallback);
                    Log.i(TAG, "Created bll.v via 6-param constructor");
                    return instance;
                }
            } catch (Throwable e) {
                Log.e(TAG, "6-param constructor failed: " + e.getMessage());
            }

            // 尝试 7 参数构造: (int ?, long bookId, long cihai, boolean, boolean, String mode, callback.b)
            try {
                java.lang.reflect.Constructor<?> ctor7 = null;
                for (java.lang.reflect.Constructor<?> ctor : bllVClass.getDeclaredConstructors()) {
                    if (ctor.getParameterTypes().length == 7) {
                        ctor7 = ctor;
                        break;
                    }
                }
                if (ctor7 != null) {
                    ctor7.setAccessible(true);
                    Object instance = ctor7.newInstance(0, bookId, -10000L, false, false, "reader", proxyCallback);
                    Log.i(TAG, "Created bll.v via 7-param constructor");
                    return instance;
                }
            } catch (Throwable e) {
                Log.e(TAG, "7-param constructor failed: " + e.getMessage());
            }

            Log.e(TAG, "All bll.v construction attempts failed");
            return null;
        } catch (Throwable e) {
            Log.e(TAG, "createBllVInstance failed: " + e.getMessage(), e);
            return null;
        }
    }

    /**
     * 从章节列表 API 获取章节 JSON 数据，用 ChapterItem(JSONObject) 构造
     * 完全免 UI，不依赖已保存的 ChapterItem 模板
     */
    private static Object constructChapterItemFromApi(String bookId, String chapterId) {
        try {
            if (savedOkHttpClient == null || appClassLoader == null) {
                Log.e(TAG, "Cannot construct ChapterItem: no OkHttpClient or ClassLoader");
                return null;
            }

            // 调用章节列表 API 获取章节信息
            String chapterListUrl = "https://druidv6.if.qidian.com/argus/api/v3/chapterlist/chapterlist"
                    + "?bookId=" + bookId + "&timeStamp=0&requestSource=0&md5Signature=&extendchapterIds=";

            Class<?> requestBuilderClass = appClassLoader.loadClass("okhttp3.Request$Builder");
            Object reqBuilder = requestBuilderClass.newInstance();
            requestBuilderClass.getDeclaredMethod("url", String.class).invoke(reqBuilder, chapterListUrl);
            requestBuilderClass.getDeclaredMethod("get").invoke(reqBuilder);
            Object httpRequest = requestBuilderClass.getDeclaredMethod("build").invoke(reqBuilder);

            Class<?> requestClass = appClassLoader.loadClass("okhttp3.Request");
            Object call = savedOkHttpClient.getClass().getMethod("newCall", requestClass)
                    .invoke(savedOkHttpClient, httpRequest);
            Object response = call.getClass().getMethod("execute").invoke(call);

            Object body = response.getClass().getDeclaredMethod("body").invoke(response);
            if (body == null) return null;

            String bodyStr = (String) body.getClass().getMethod("string").invoke(body);
            JSONObject json = new JSONObject(bodyStr);

            if (json.optInt("Result", -1) != 0) {
                Log.e(TAG, "ChapterList API error: " + json.optString("Message"));
                return null;
            }

            // 查找目标 chapterId：支持两种 API 响应格式
            JSONObject dataObj = json.getJSONObject("Data");
            JSONObject targetChapterJson = null;

            // 格式1: Data.Vs[].Cs[]（卷-章节结构）
            JSONArray volumes = dataObj.optJSONArray("Vs");
            if (volumes != null) {
                for (int vi = 0; vi < volumes.length() && targetChapterJson == null; vi++) {
                    JSONArray chapters = volumes.getJSONObject(vi).optJSONArray("Cs");
                    if (chapters == null) continue;
                    for (int ci = 0; ci < chapters.length(); ci++) {
                        JSONObject ch = chapters.getJSONObject(ci);
                        long cid = ch.optLong("C", ch.optLong("I", 0));
                        if (String.valueOf(cid).equals(chapterId)) {
                            targetChapterJson = ch;
                            break;
                        }
                    }
                }
            }

            // 格式2: Data.Chapters[]（扁平章节列表）
            if (targetChapterJson == null) {
                JSONArray flatChapters = dataObj.optJSONArray("Chapters");
                if (flatChapters != null) {
                    for (int ci = 0; ci < flatChapters.length(); ci++) {
                        JSONObject ch = flatChapters.getJSONObject(ci);
                        long cid = ch.optLong("C", ch.optLong("ChapterId", ch.optLong("I", 0)));
                        if (String.valueOf(cid).equals(chapterId)) {
                            targetChapterJson = ch;
                            break;
                        }
                    }
                }
            }

            if (targetChapterJson == null) {
                Log.e(TAG, "Chapter " + chapterId + " not found in chapter list (Vs=" + (volumes != null ? volumes.length() : "null") + ", Chapters=" + (dataObj.optJSONArray("Chapters") != null ? dataObj.optJSONArray("Chapters").length() : "null") + ")");
                return null;
            }

            Log.i(TAG, "Found chapter JSON for " + chapterId + ": " + targetChapterJson.toString().substring(0, Math.min(200, targetChapterJson.toString().length())));

            // 使用 ChapterItem(JSONObject) 构造函数
            Class<?> chapterItemClass = appClassLoader.loadClass("com.qidian.QDReader.repository.entity.ChapterItem");
            java.lang.reflect.Constructor<?> jsonCtor = chapterItemClass.getDeclaredConstructor(JSONObject.class);
            jsonCtor.setAccessible(true);
            Object chapterItem = jsonCtor.newInstance(targetChapterJson);

            // 验证 ChapterId 是否正确设置
            String extractedId = extractChapterId(chapterItem);
            Log.i(TAG, "Constructed ChapterItem from API: chapterId=" + extractedId);

            // 如果 ChapterId 没有正确设置，手动设置
            if (extractedId == null || !extractedId.equals(chapterId)) {
                for (java.lang.reflect.Field f : chapterItemClass.getDeclaredFields()) {
                    if (f.getName().equals("ChapterId") || f.getName().equals("chapterId")) {
                        f.setAccessible(true);
                        if (f.getType() == long.class || f.getType() == Long.class) {
                            f.set(chapterItem, Long.parseLong(chapterId));
                        } else if (f.getType() == int.class || f.getType() == Integer.class) {
                            f.set(chapterItem, Integer.parseInt(chapterId));
                        }
                        Log.i(TAG, "Manually set ChapterId=" + chapterId);
                    }
                }
            }

            return chapterItem;
        } catch (Throwable e) {
            Log.e(TAG, "constructChapterItemFromApi failed: " + e.getMessage(), e);
            return null;
        }
    }

    /**
     * 获取当前登录用户 ID。
     * 优先从 book 目录获取（/storage/.../book/ 下非 "0" 的子目录名即为 userId）。
     */
    private static String getUserId() {
        if (cachedUserId != null) return cachedUserId;
        try {
            java.io.File bookDir = new java.io.File(
                    "/storage/emulated/0/Android/data/com.qidian.QDReader/files/QDReader/book");
            if (bookDir.exists() && bookDir.isDirectory()) {
                java.io.File[] children = bookDir.listFiles();
                if (children != null) {
                    for (java.io.File child : children) {
                        if (child.isDirectory() && !"0".equals(child.getName())) {
                            cachedUserId = child.getName();
                            Log.i(TAG, "getUserId: found userId=" + cachedUserId + " from book dir");
                            return cachedUserId;
                        }
                    }
                }
            }
        } catch (Throwable e) {
            Log.e(TAG, "getUserId failed: " + e.getMessage());
        }
        return "0";  // fallback
    }

    /** 通过 bll.v.K 获取 .qd 文件的期望路径（不下载/不保存） */
    private static String getQdPath(String bookId, String chapterId) {
        try {
            if (bllVInstance == null) {
                bllVInstance = createBllVInstance(Long.parseLong(bookId));
            }
            if (bllVInstance == null) return null;

            Object chapterItem = chapterItems.get(chapterId);
            if (chapterItem == null) chapterItem = cloneChapterItemWithNewId(chapterId);
            if (chapterItem == null) chapterItem = constructChapterItemFromApi(bookId, chapterId);
            if (chapterItem == null) return null;

            for (Method m : bllVInstance.getClass().getDeclaredMethods()) {
                if ("K".equals(m.getName()) && m.getParameterTypes().length == 2) {
                    m.setAccessible(true);
                    Object kResult = m.invoke(bllVInstance, Long.parseLong(bookId), chapterItem);
                    return kResult != null ? kResult.toString() : null;
                }
            }
        } catch (Throwable e) {
            Log.e(TAG, "getQdPath failed: " + e.getMessage());
        }
        return null;
    }

    /**
     * 保存 .qd 文件到 App 期望的用户路径（通过调用 K 获取正确路径）
     * 同时保存到匿名路径作为备用
     */
    private static String saveQdToCorrectPath(String bookId, String chapterId, byte[] encryptedBytes) {
        try {
            // 确保 bllVInstance 可用
            if (bllVInstance == null) {
                bllVInstance = createBllVInstance(Long.parseLong(bookId));
            }

            // 获取 ChapterItem
            Object chapterItem = chapterItems.get(chapterId);
            if (chapterItem == null) chapterItem = cloneChapterItemWithNewId(chapterId);
            if (chapterItem == null) chapterItem = constructChapterItemFromApi(bookId, chapterId);

            // 调用 K 获取 App 期望的路径
            String expectedPath = null;
            if (bllVInstance != null && chapterItem != null) {
                for (Method m : bllVInstance.getClass().getDeclaredMethods()) {
                    if ("K".equals(m.getName()) && m.getParameterTypes().length == 2) {
                        m.setAccessible(true);
                        Object kResult = m.invoke(bllVInstance, Long.parseLong(bookId), chapterItem);
                        if (kResult != null) {
                            expectedPath = kResult.toString();
                            addProbeLog("saveQdToCorrectPath | K path: " + expectedPath);
                        }
                        break;
                    }
                }
            }

            // 保存到用户路径
            if (expectedPath != null && !expectedPath.isEmpty()) {
                java.io.File file = new java.io.File(expectedPath);
                java.io.File dir = file.getParentFile();
                if (dir != null && !dir.exists()) dir.mkdirs();
                java.io.FileOutputStream fos = new java.io.FileOutputStream(expectedPath);
                fos.write(encryptedBytes);
                fos.close();
                Log.i(TAG, "Saved .qd to user path: " + expectedPath);
            }

            // 也保存到匿名路径
            String anonDir = "/storage/emulated/0/Android/data/com.qidian.QDReader/files/QDReader/book/0/" + bookId;
            new java.io.File(anonDir).mkdirs();
            String anonPath = anonDir + "/" + chapterId + ".qd";
            java.io.FileOutputStream fos2 = new java.io.FileOutputStream(anonPath);
            fos2.write(encryptedBytes);
            fos2.close();

            return expectedPath != null ? expectedPath : anonPath;
        } catch (Throwable e) {
            Log.e(TAG, "saveQdToCorrectPath failed: " + e.getMessage(), e);
            return null;
        }
    }

    private static String triggerAppDecrypt(String bookId, String chapterId, byte[] encryptedBytes) {
        try {
            Log.i(TAG, "triggerAppDecrypt: bookId=" + bookId + ", chapterId=" + chapterId + ", dataLen=" + (encryptedBytes != null ? encryptedBytes.length : 0));

            // 步骤1：确保 bllVInstance 可用（需要先有实例才能调用 K 获取正确路径）
            if (bllVInstance == null) {
                Log.i(TAG, "bllVInstance is null, attempting to create one...");
                bllVInstance = createBllVInstance(Long.parseLong(bookId));
                if (bllVInstance != null) {
                    Log.i(TAG, "Created bll.v instance via reflection: " + bllVInstance.getClass().getName());
                } else {
                    Log.i(TAG, "Failed to create bll.v instance");
                    return null;
                }
            }

            // 步骤2：获取或创建 ChapterItem
            Object chapterItem = chapterItems.get(chapterId);
            if (chapterItem == null) {
                chapterItem = cloneChapterItemWithNewId(chapterId);
                if (chapterItem != null) {
                    Log.i(TAG, "Cloned ChapterItem for chapterId=" + chapterId);
                }
            } else {
                Log.i(TAG, "Using saved ChapterItem for chapterId=" + chapterId);
            }

            if (chapterItem == null) {
                Log.i(TAG, "No ChapterItem template available, trying to construct from API...");
                chapterItem = constructChapterItemFromApi(bookId, chapterId);
                if (chapterItem != null) {
                    Log.i(TAG, "Constructed ChapterItem from API data: " + chapterItem.getClass().getName());
                } else {
                    Log.i(TAG, "Failed to construct ChapterItem");
                    return null;
                }
            }

            // 步骤3：查找 L, K, R 方法
            Class<?> bllVClass = bllVInstance.getClass();
            Method methodL = null, methodK = null, methodR = null;

            for (Method m : bllVClass.getDeclaredMethods()) {
                Class<?>[] pt = m.getParameterTypes();
                if (pt.length == 2) {
                    if ("L".equals(m.getName())) methodL = m;
                    else if ("K".equals(m.getName())) methodK = m;
                    else if ("R".equals(m.getName())) methodR = m;
                }
            }

            if (methodR == null) {
                Log.i(TAG, "Method bll.v.R not found");
                return null;
            }

            long bookIdLong = Long.parseLong(bookId);

            // 步骤4：先调用 K 获取 App 期望的 .qd 文件路径，然后保存到正确位置
            String expectedQdPath = null;
            if (methodK != null) {
                methodK.setAccessible(true);
                try {
                    Object kResult = methodK.invoke(bllVInstance, bookIdLong, chapterItem);
                    if (kResult != null) {
                        expectedQdPath = kResult.toString();
                        addProbeLog("triggerAppDecrypt | K returned expected path: " + expectedQdPath);
                    }
                } catch (Throwable e) {
                    Log.e(TAG, "bll.v.K (path query) failed: " + e.getMessage());
                }
            }

            // 保存 .qd 文件（encryptedBytes 为 null 时表示 .qd 已存在，跳过保存）
            if (encryptedBytes != null) {
                if (expectedQdPath != null && !expectedQdPath.isEmpty()) {
                    java.io.File expectedFile = new java.io.File(expectedQdPath);
                    java.io.File expectedDir = expectedFile.getParentFile();
                    if (expectedDir != null && !expectedDir.exists()) {
                        expectedDir.mkdirs();
                    }
                    java.io.FileOutputStream fos = new java.io.FileOutputStream(expectedQdPath);
                    fos.write(encryptedBytes);
                    fos.close();
                    Log.i(TAG, "Saved .qd file to user path: " + expectedQdPath + " (" + encryptedBytes.length + " bytes)");
                }

                // 也保存到匿名路径作为备用
                String anonDir = "/storage/emulated/0/Android/data/com.qidian.QDReader/files/QDReader/book/0/" + bookId;
                java.io.File dir = new java.io.File(anonDir);
                if (!dir.exists()) {
                    dir.mkdirs();
                }
                String anonPath = anonDir + "/" + chapterId + ".qd";
                java.io.FileOutputStream fos2 = new java.io.FileOutputStream(anonPath);
                fos2.write(encryptedBytes);
                fos2.close();
            } else {
                addProbeLog("triggerAppDecrypt | encryptedBytes=null, .qd already on disk, skipping save");
            }

            // 步骤5：调用 bll.v.L（内部会调用 K→R 完成解密）
            Object lResult = null;
            if (methodL != null) {
                methodL.setAccessible(true);
                try {
                    lResult = methodL.invoke(bllVInstance, bookIdLong, chapterItem);
                    addProbeLog("triggerAppDecrypt | L returned: " +
                        (lResult != null ? lResult.getClass().getName() + " hash=" + Integer.toHexString(System.identityHashCode(lResult)) : "null"));
                } catch (Throwable e) {
                    Log.e(TAG, "bll.v.L failed: " + e.getMessage());
                    addProbeLog("triggerAppDecrypt | L failed: " + e.getMessage());
                }
            }

            // L 内部会调用 K→R，afterCall hook 应已捕获内容
            String captured = capturedChapterContents.get(chapterId);
            if (captured != null) {
                addProbeLog("triggerAppDecrypt | content captured after L call: " + captured.length() + " chars");
                return captured;
            }

            // 如果 afterCall 没捕获到，尝试直接从 L 的返回值提取内容
            if (lResult != null) {
                addProbeLog("triggerAppDecrypt | trying direct extraction from L result: " + lResult.getClass().getName());
                String directContent = extractContentDirect(lResult);
                if (directContent != null && directContent.length() > 0) {
                    addProbeLog("triggerAppDecrypt | direct extraction from L: " + directContent.length() + " chars");
                    capturedChapterContents.put(chapterId, directContent);
                    return directContent;
                }
            }

            // 如果 L 调用后仍未获取到内容，尝试显式调用 K→R
            addProbeLog("triggerAppDecrypt | L didn't yield content, trying explicit K→R");
            if (methodK != null) {
                methodK.setAccessible(true);
                try {
                    Object kResult = methodK.invoke(bllVInstance, bookIdLong, chapterItem);
                    addProbeLog("triggerAppDecrypt | K returned: " + (kResult != null ? kResult.toString() : "null"));
                } catch (Throwable e) {
                    Log.e(TAG, "bll.v.K failed: " + e.getMessage());
                }
            }

            // 调用 bll.v.R（解密并返回 ChapterContentItem）
            methodR.setAccessible(true);
            Class<?>[] paramTypes = methodR.getParameterTypes();

            Object rResult;
            if (paramTypes[0] == long.class) {
                rResult = methodR.invoke(bllVInstance, bookIdLong, chapterItem);
            } else if (paramTypes[0] == int.class) {
                rResult = methodR.invoke(bllVInstance, (int) bookIdLong, chapterItem);
            } else {
                rResult = methodR.invoke(bllVInstance, bookId, chapterItem);
            }

            addProbeLog("triggerAppDecrypt | R returned: " +
                (rResult != null ? rResult.getClass().getName() + " hash=" + Integer.toHexString(System.identityHashCode(rResult)) : "null"));

            // 尝试从 R 的返回值直接提取
            if (rResult != null) {
                String directContent = extractContentDirect(rResult);
                if (directContent != null && directContent.length() > 100) {
                    addProbeLog("triggerAppDecrypt | direct extraction from R: " + directContent.length() + " chars");
                    capturedChapterContents.put(chapterId, directContent);
                    return directContent;
                }
            }

            // 步骤5：等待 afterCall hook 捕获解密内容
            for (int retry = 0; retry < 5; retry++) {
                Thread.sleep(500);
                captured = capturedChapterContents.get(chapterId);
                if (captured != null) {
                    addProbeLog("triggerAppDecrypt | content captured after wait: " + captured.length() + " chars");
                    return captured;
                }
            }

            // 步骤6：如果没有从缓存获取到，检查 recentCaptured
            String latest = getLatestCapturedContent();
            if (latest != null && latest.length() > 100) {
                addProbeLog("triggerAppDecrypt | got from recentCaptured: " + latest.length() + " chars");
                capturedChapterContents.put(chapterId, latest);
                return latest;
            }

            addProbeLog("triggerAppDecrypt | no content captured after L→K→R");
            return null;

        } catch (Throwable e) {
            Log.e(TAG, "triggerAppDecrypt failed: " + e.getMessage(), e);
            return null;
        }
    }

    private static String tryDecryptChapterContent(byte[] encryptedBytes) {
        // 方法 1：调用 pf.cihai.search(byte[], String key) 用已捕获的密钥
        if (lastCapturedDecryptKey != null) {
            String result = callPfCihaiSearch(encryptedBytes, lastCapturedDecryptKey);
            if (result != null) return result;
        }

        // 方法 2：调用 getkey API 获取密钥
        String getkeyResult = callGetkeyApi();
        if (getkeyResult != null) {
            Log.i(TAG, "getkey response: " + truncate(getkeyResult, 500));
            // 尝试从 getkey 响应中提取密钥
            try {
                JSONObject getkeyJson = new JSONObject(getkeyResult);
                // 遍历所有字段寻找密钥
                extractAndTryKeys(getkeyJson, encryptedBytes);
            } catch (Throwable e) {
                // 如果不是 JSON，可能密钥就是整个响应
                String result = callPfCihaiSearch(encryptedBytes, getkeyResult);
                if (result != null) return result;
            }
        }

        return null;
    }

    /**
     * 调用 getkey API
     */
    private static String callGetkeyApi() {
        try {
            if (savedOkHttpClient == null || appClassLoader == null) return null;

            String url = "https://druidv6.if.qidian.com/argus/api/v4/bookcontent/getkey?bookId=0&ui=0";
            Log.i(TAG, "Calling getkey API: " + url);

            Class<?> requestBuilderClass = appClassLoader.loadClass("okhttp3.Request$Builder");
            Object reqBuilder = requestBuilderClass.newInstance();
            requestBuilderClass.getDeclaredMethod("url", String.class).invoke(reqBuilder, url);
            requestBuilderClass.getDeclaredMethod("get").invoke(reqBuilder);
            Object httpRequest = requestBuilderClass.getDeclaredMethod("build").invoke(reqBuilder);

            Class<?> requestClass = appClassLoader.loadClass("okhttp3.Request");
            Object call = savedOkHttpClient.getClass().getMethod("newCall", requestClass)
                    .invoke(savedOkHttpClient, httpRequest);
            Object response = call.getClass().getMethod("execute").invoke(call);

            int code = (int) response.getClass().getDeclaredMethod("code").invoke(response);
            Object responseBody = response.getClass().getDeclaredMethod("body").invoke(response);
            if (responseBody != null) {
                String bodyStr = (String) responseBody.getClass().getMethod("string").invoke(responseBody);
                Log.i(TAG, "getkey response: code=" + code + ", body=" + truncate(bodyStr, 300));
                return bodyStr;
            }
        } catch (Throwable e) {
            Log.i(TAG, "getkey API failed: " + e.getMessage());
        }
        return null;
    }

    /**
     * 从 getkey JSON 中提取可能的密钥并尝试解密
     */
    private static String extractAndTryKeys(JSONObject json, byte[] encryptedBytes) {
        try {
            // 尝试常见的密钥字段名
            String[] keyFields = {"Key", "key", "DecryptKey", "decryptKey", "ContentKey", "contentKey", "Data"};
            for (String field : keyFields) {
                if (json.has(field)) {
                    Object val = json.get(field);
                    if (val instanceof String) {
                        String keyStr = (String) val;
                        if (keyStr.length() >= 16) {
                            Log.i(TAG, "Trying key from field '" + field + "': " + truncate(keyStr, 50));
                            String result = callPfCihaiSearch(encryptedBytes, keyStr);
                            if (result != null) return result;
                        }
                    } else if (val instanceof JSONObject) {
                        // 递归搜索
                        String result = extractAndTryKeys((JSONObject) val, encryptedBytes);
                        if (result != null) return result;
                    }
                }
            }
            // 遍历所有 String 字段尝试
            java.util.Iterator<String> keys = json.keys();
            while (keys.hasNext()) {
                String k = keys.next();
                Object val = json.opt(k);
                if (val instanceof String) {
                    String str = (String) val;
                    if (str.length() >= 32 && str.length() <= 128) {
                        Log.i(TAG, "Trying potential key from '" + k + "': " + truncate(str, 50));
                        String result = callPfCihaiSearch(encryptedBytes, str);
                        if (result != null) return result;
                    }
                }
            }
        } catch (Throwable e) {
            Log.i(TAG, "extractAndTryKeys failed: " + e.getMessage());
        }
        return null;
    }

    /**
     * 调用 pf.cihai.search(byte[], String) 进行解密
     * 使用 hook 初始化时保存的方法引用，避免 ClassLoader 问题
     */
    private static String callPfCihaiSearch(byte[] encryptedBytes, String key) {
        if (pfCihaiSearchDecryptMethod == null) {
            Log.i(TAG, "pf.cihai.search decrypt method not available (hook not initialized yet?)");
            return null;
        }
        try {
            Log.i(TAG, "Calling pf.cihai.search(byte[" + encryptedBytes.length + "], key=" + truncate(key, 30) + ")");
            Object decResult = pfCihaiSearchDecryptMethod.invoke(null, encryptedBytes, key);
            return processDecryptResult(decResult);
        } catch (Throwable e) {
            Log.i(TAG, "pf.cihai.search invoke failed: " + e.getMessage());
        }
        return null;
    }

    /**
     * 处理解密结果（byte[] 或 String），支持 gzip 解压
     */
    private static String processDecryptResult(Object decResult) {
        if (decResult == null) return null;
        try {
            String text = null;
            if (decResult instanceof byte[]) {
                byte[] decBytes = (byte[]) decResult;
                if (decBytes.length < 10) return null;
                // 检查 gzip
                if (decBytes.length > 2 && decBytes[0] == 0x1f && decBytes[1] == (byte) 0x8b) {
                    java.util.zip.GZIPInputStream gzipIn = new java.util.zip.GZIPInputStream(
                            new java.io.ByteArrayInputStream(decBytes));
                    java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
                    byte[] buffer = new byte[4096];
                    int len;
                    while ((len = gzipIn.read(buffer)) > 0) {
                        out.write(buffer, 0, len);
                    }
                    gzipIn.close();
                    text = out.toString("UTF-8");
                } else {
                    text = new String(decBytes, java.nio.charset.StandardCharsets.UTF_8);
                }
            } else if (decResult instanceof String) {
                text = (String) decResult;
            }

            if (text != null && (text.startsWith("{") || looksLikeChineseText(text.getBytes(java.nio.charset.StandardCharsets.UTF_8)))) {
                Log.i(TAG, "Decrypt success: " + text.length() + " chars");
                return text;
            }
        } catch (Throwable e) {
            Log.i(TAG, "processDecryptResult failed: " + e.getMessage());
        }
        return null;
    }

    private static String buildSearchUrl(String keyword, int page) {
        if (searchUrlTemplate == null) {
            return null;
        }

        try {
            String url = searchUrlTemplate;

            // POST 请求：keyword 在 body 中，URL 不需要修改
            if ("POST".equalsIgnoreCase(searchHttpMethod)) {
                return url;
            }

            // GET 请求：keyword 在 URL query params 中
            String encodedKeyword = java.net.URLEncoder.encode(keyword, "UTF-8");

            String[] keywordParams = {"kw", "keyword", "key", "query", "q", "wd", "word", "searchKey"};
            boolean replaced = false;

            for (String param : keywordParams) {
                String pattern = param + "=[^&]*";
                String replacement = param + "=" + encodedKeyword;
                String newUrl = url.replaceFirst(pattern, replacement);
                if (!newUrl.equals(url)) {
                    url = newUrl;
                    replaced = true;
                    break;
                }
            }

            if (!replaced) {
                if (url.contains("?")) {
                    url = url + "&kw=" + encodedKeyword;
                } else {
                    url = url + "?kw=" + encodedKeyword;
                }
            }

            // 替换 page 参数
            String[] pageParams = {"page", "pageIndex", "pageNum"};
            for (String param : pageParams) {
                String pattern = param + "=\\d+";
                String replacement = param + "=" + page;
                url = url.replaceFirst(pattern, replacement);
            }

            return url;
        } catch (Throwable e) {
            Log.e(TAG, "buildSearchUrl failed", e);
            return null;
        }
    }

    private static String buildSearchBody(String keyword, int page) {
        if (searchBodyTemplate == null || searchBodyTemplate.isEmpty()) {
            return "";
        }

        try {
            String body = searchBodyTemplate;
            String encodedKeyword = java.net.URLEncoder.encode(keyword, "UTF-8");

            // 替换 body 中的关键词参数
            String[] keywordParams = {"kw", "keyword", "key", "query", "q"};
            for (String param : keywordParams) {
                String pattern = param + "=[^&]*";
                String replacement = param + "=" + encodedKeyword;
                body = body.replaceFirst(pattern, replacement);
            }

            // 替换 page 参数
            String[] pageParams = {"page", "pageIndex", "pageNum"};
            for (String param : pageParams) {
                String pattern = param + "=\\d+";
                String replacement = param + "=" + page;
                body = body.replaceFirst(pattern, replacement);
            }

            return body;
        } catch (Throwable e) {
            Log.e(TAG, "buildSearchBody failed", e);
            return searchBodyTemplate;
        }
    }

    // ==================== 搜索 Hook ====================

    private static void installSearchHooks(ClassLoader cl) {
        logRemote(TAG, "=== 开始安装搜索 Hook ===");

        // 1. Hook 搜索相关的 Activity/Fragment
        hookSearchUI(cl);

        // 2. Hook 搜索相关的 Repository/ViewModel
        hookSearchRepositories(cl);

        logRemote(TAG, "搜索 Hook 安装完成");
    }

    private static void hookSearchUI(ClassLoader cl) {
        String[] searchClasses = {
            // Activity
            "com.qidian.QDReader.ui.activity.QDSearchActivity",
            "com.qidian.QDReader.ui.activity.SearchActivity",

            // Fragment
            "com.qidian.QDReader.ui.fragment.NewSearchHomePageFragment",
            "com.qidian.QDReader.ui.fragment.SearchAssociateFragment",
            "com.qidian.QDReader.ui.fragment.SearchResultFragment",
        };

        for (String className : searchClasses) {
            try {
                Class<?> clazz = cl.loadClass(className);
                logRemote(TAG, "SEARCH_SETUP | Found search UI class: " + className);

                // Hook 所有公共方法
                for (java.lang.reflect.Method method : clazz.getDeclaredMethods()) {
                    if (java.lang.reflect.Modifier.isPublic(method.getModifiers())) {
                        final String methodName = method.getName();
                        Pine.hook(method, new MethodHook() {
                            @Override
                            public void beforeCall(Pine.CallFrame callFrame) {
                                logRemote(TAG, String.format(
                                    "SEARCH_UI | %s | %s(%s)",
                                    className,
                                    methodName,
                                    formatParamsBrief(callFrame.args)
                                ));
                            }
                        });
                    }
                }
                logRemote(TAG, "SEARCH_SETUP | Hooked UI: " + className);
            } catch (ClassNotFoundException e) {
                // 类不存在，继续
            } catch (Throwable e) {
                Log.e(TAG, "Failed to hook " + className, e);
            }
        }
    }

    private static void hookSearchRepositories(ClassLoader cl) {
        // 扫描可能包含搜索的包
        String[] packages = {
            "com.qidian.QDReader.repository",
            "com.qidian.QDReader.data.repository",
            "com.qidian.QDReader.viewmodel",
            "com.qidian.QDReader.ui.viewmodel",
            "com.qidian.QDReader.component.bll",
        };

        int hookedCount = 0;

        for (String pkg : packages) {
            // 尝试加载可能包含搜索的类
            String[] possibleClasses = {
                pkg + ".SearchRepository",
                pkg + ".BookSearchRepository",
                pkg + ".SearchManager",
                pkg + ".SearchHelper",
                pkg + ".SearchViewModel",
                pkg + ".BookSearchViewModel",
            };

            for (String className : possibleClasses) {
                try {
                    Class<?> clazz = cl.loadClass(className);
                    logRemote(TAG, "SEARCH_SETUP | Found search repo class: " + className);

                    // Hook 包含 search 关键词的方法
                    for (java.lang.reflect.Method method : clazz.getDeclaredMethods()) {
                        String methodName = method.getName().toLowerCase();
                        if (methodName.contains("search") ||
                            methodName.contains("query") ||
                            methodName.contains("find")) {

                            method.setAccessible(true);
                            final String fullClassName = className;
                            Pine.hook(method, new MethodHook() {
                                @Override
                                public void beforeCall(Pine.CallFrame callFrame) {
                                    logRemote(TAG, String.format(
                                        "SEARCH_REPO | %s.%s(%s)",
                                        fullClassName,
                                        method.getName(),
                                        formatParamsDetailed(callFrame.args, method.getParameterTypes())
                                    ));

                                    // 打印完整调用栈（合并为一条日志）
                                    StringBuilder stackTrace = new StringBuilder();
                                    stackTrace.append("SEARCH_REPO_CALLSTACK | ").append(fullClassName).append(".").append(method.getName());
                                    for (StackTraceElement ste : Thread.currentThread().getStackTrace()) {
                                        stackTrace.append("\n    at ").append(ste.toString());
                                    }
                                    logRemote(TAG, stackTrace.toString());
                                }

                                @Override
                                public void afterCall(Pine.CallFrame callFrame) {
                                    Object result = callFrame.getResult();
                                    logRemote(TAG, String.format(
                                        "SEARCH_REPO_RESULT | %s.%s | → %s",
                                        fullClassName,
                                        method.getName(),
                                        formatResult(result)
                                    ));
                                }
                            });
                            hookedCount++;
                        }
                    }
                    logRemote(TAG, "SEARCH_SETUP | Hooked repo: " + className);
                } catch (ClassNotFoundException e) {
                    // 类不存在，继续
                } catch (Throwable e) {
                    Log.e(TAG, "Failed to hook " + className, e);
                }
            }
        }

        logRemote(TAG, "SEARCH_SETUP | 搜索 Repository Hook 完成，共 Hook " + hookedCount + " 个方法");
    }

    private static String formatParamsBrief(Object[] args) {
        if (args == null || args.length == 0) return "";
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < Math.min(args.length, 3); i++) {
            if (i > 0) sb.append(", ");
            Object arg = args[i];
            if (arg == null) {
                sb.append("null");
            } else if (arg instanceof String) {
                String s = (String) arg;
                if (s.length() > 30) {
                    sb.append("\"").append(s.substring(0, 30)).append("...\"");
                } else {
                    sb.append("\"").append(s).append("\"");
                }
            } else {
                sb.append(arg.getClass().getSimpleName());
            }
        }
        return sb.toString();
    }

    private static String formatParamsDetailed(Object[] args, Class<?>[] paramTypes) {
        if (args == null || args.length == 0) return "";
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < args.length; i++) {
            if (i > 0) sb.append(", ");
            sb.append(paramTypes[i].getSimpleName()).append("=");

            Object arg = args[i];
            if (arg == null) {
                sb.append("null");
            } else if (arg instanceof String) {
                sb.append("\"").append(arg).append("\"");
            } else if (arg instanceof Integer) {
                sb.append(arg);
            } else if (arg instanceof Long) {
                sb.append(arg);
            } else {
                sb.append(arg.getClass().getSimpleName());
            }
        }
        return sb.toString();
    }

    private static String formatResult(Object result) {
        if (result == null) return "null";
        if (result instanceof String) {
            String s = (String) result;
            if (s.length() > 100) {
                return "\"" + s.substring(0, 100) + "...\"";
            }
            return "\"" + s + "\"";
        }
        if (result instanceof java.util.Collection) {
            return "Collection[" + ((java.util.Collection<?>) result).size() + "]";
        }
        if (result instanceof java.util.Map) {
            return "Map[" + ((java.util.Map<?, ?>) result).size() + "]";
        }
        return result.getClass().getSimpleName();
    }

    // ==================== 远程日志转发器 ====================

    /**
     * 启动远程日志转发器
     * 将 Hook 日志通过 TCP Socket 发送到 PC 端
     */
    private static void startRemoteLogForwarder() {
        if (logForwarderRunning) {
            return;
        }

        new Thread(() -> {
            logForwarderRunning = true;
            Log.i(TAG, "Remote log forwarder started");

            while (logForwarderRunning) {
                try {
                    if (logSocket == null || logWriter == null || logSocket.isClosed()) {
                        try {
                            Log.i(TAG, "Connecting to remote log server: " + REMOTE_LOG_HOST + ":" + REMOTE_LOG_PORT);
                            logSocket = new Socket(REMOTE_LOG_HOST, REMOTE_LOG_PORT);
                            logSocket.setSoTimeout(5000);
                            logWriter = new PrintWriter(logSocket.getOutputStream(), true);
                            logWriter.flush();

                            logForwarderErrors.set(0);
                            Log.i(TAG, "Connected to remote log server");

                            // 发送缓冲区中的日志
                            synchronized (logBuffer) {
                                for (String log : logBuffer) {
                                    sendLogToRemote(log);
                                }
                                logBuffer.clear();
                            }
                        } catch (Exception e) {
                            Log.e(TAG, "Failed to connect to remote log server", e);
                            if (logWriter != null) {
                                logWriter.close();
                                logWriter = null;
                            }
                            if (logSocket != null) {
                                try {
                                    logSocket.close();
                                } catch (Exception ignored) {
                                }
                                logSocket = null;
                            }

                            // 等待后重试
                            try {
                                Thread.sleep(5000);
                            } catch (InterruptedException ignored) {
                            }
                            continue;
                        }
                    }

                    // 定期发送心跳
                    Thread.sleep(1000);
                } catch (InterruptedException e) {
                    break;
                } catch (Exception e) {
                    Log.e(TAG, "Remote log forwarder error", e);
                }
            }

            Log.i(TAG, "Remote log forwarder stopped");
        }, "RemoteLogForwarder").start();
    }

    /**
     * 发送日志到远程服务器
     */
    private static void sendLogToRemote(String logMessage) {
        try {
            if (logWriter != null && !logSocket.isClosed()) {
                logWriter.println(logMessage);
                logWriter.flush();
            }
            // 注意：不再在此处缓冲到 logBuffer，logRemote() 已统一处理
        } catch (Exception e) {
            Log.e(TAG, "Failed to send log to remote", e);
            logForwarderErrors.incrementAndGet();

            // 连续失败超过 10 次，关闭连接
            if (logForwarderErrors.get() > 10) {
                if (logWriter != null) {
                    logWriter.close();
                    logWriter = null;
                }
                if (logSocket != null) {
                    try {
                        logSocket.close();
                    } catch (Exception ignored) {
                    }
                    logSocket = null;
                }
                logForwarderErrors.set(0);
            }
        }
    }

    /**
     * 远程日志记录方法（替代 Log.i）
     */
    private static void logRemote(String tag, String message) {
        long timestamp = System.currentTimeMillis();
        String threadName = Thread.currentThread().getName();

        // JSON 格式
        JSONObject logJson = new JSONObject();
        try {
            logJson.put("timestamp", timestamp);
            logJson.put("thread", threadName);
            logJson.put("tag", tag);
            logJson.put("message", message);
        } catch (Exception e) {
            // JSON 构建失败，使用简单格式
        }

        String logMessage = logJson.toString();

        // 总是存到内存 buffer（供 RPC getLogs 读取）
        synchronized (logBuffer) {
            logBuffer.addLast(logMessage);
            while (logBuffer.size() > MAX_LOG_BUFFER) {
                logBuffer.removeFirst();
            }
        }

        // TCP 转发（可选，不影响 RPC 日志读取）
        sendLogToRemote(logMessage);

        // 同时也输出到 logcat（用于调试）
        Log.i(tag, message);
    }

    /**
     * 远程日志记录方法（带调用栈）
     */
    private static void logRemoteWithStack(String tag, String message) {
        long timestamp = System.currentTimeMillis();
        String threadName = Thread.currentThread().getName();

        // 获取调用栈
        StringBuilder stackTrace = new StringBuilder();
        for (StackTraceElement ste : Thread.currentThread().getStackTrace()) {
            stackTrace.append("    at ").append(ste.toString()).append("\n");
        }

        JSONObject logJson = new JSONObject();
        try {
            logJson.put("timestamp", timestamp);
            logJson.put("thread", threadName);
            logJson.put("tag", tag);
            logJson.put("message", message);
            logJson.put("stackTrace", stackTrace.toString());
        } catch (Exception e) {
            // JSON 构建失败，使用简单格式
        }

        String logMessage = logJson.toString();
        sendLogToRemote(logMessage);

        // 同时也输出到 logcat
        Log.i(tag, message);
    }
}
