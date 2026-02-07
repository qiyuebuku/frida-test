package com.yuyang.qdhook;

import android.app.Application;
import android.util.Log;

import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Iterator;
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

    private static final String TAG = "XHSHook";
    private static volatile boolean hooksInstalled = false;
    private static final ConcurrentHashMap<String, Long> recentRequests = new ConcurrentHashMap<>();
    private static final long DEDUP_WINDOW_MS = 500;

    // Crypto hook fields
    private static final String CRYPTO_TAG = "XHSCrypto";
    private static final ConcurrentHashMap<Integer, CipherContext> trackedCiphers = new ConcurrentHashMap<>();
    private static final AtomicInteger cryptoLogCount = new AtomicInteger(0);
    private static volatile long cryptoLogWindowStart = 0;
    private static final int CRYPTO_LOG_LIMIT = 30;

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

            // Only hook Application.onCreate — this is a framework class, safe from 360加固 checks
            Method onCreate = Application.class.getDeclaredMethod("onCreate");
            Pine.hook(onCreate, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    Application app = (Application) callFrame.thisObject;
                    Log.i(TAG, "Application.onCreate: " + app.getClass().getName());
                    if (!hooksInstalled) {
                        hooksInstalled = true;
                        injectInterceptor(app.getClassLoader());
                        installCryptoHooks();
                    }
                }
            });
            Log.i(TAG, "Hook installed on Application.onCreate");

        } catch (Throwable e) {
            Log.e(TAG, "Hook failed", e);
        }
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
            });
            Log.i(TAG, "OkHttpClient.Builder.build() hooked for interceptor injection");

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

                    // Check if this is an API response (not static resource)
                    boolean isApiResponse = url.contains("/api/") || url.contains("xiaohongshu.com");

                    // Read response body - try multiple methods for compatibility
                    String bodyStr = "";
                    try {
                        Method bodyMethod = response.getClass().getDeclaredMethod("body");
                        Object body = bodyMethod.invoke(response);
                        if (body != null) {
                            // Try to get source and peek bytes
                            Method sourceMethod = body.getClass().getMethod("source");
                            Object source = sourceMethod.invoke(body);
                            if (source != null) {
                                // Request to buffer some bytes without consuming
                                try {
                                    Method reqBytesMethod = source.getClass().getMethod("request", long.class);
                                    reqBytesMethod.invoke(source, 65536L);
                                } catch (Throwable ignored) {}

                                // Get buffer and read as string
                                try {
                                    Method bufferMethod = source.getClass().getMethod("getBuffer");
                                    Object buffer = bufferMethod.invoke(source);
                                    if (buffer != null) {
                                        Method cloneMethod = buffer.getClass().getMethod("clone");
                                        Object clonedBuffer = cloneMethod.invoke(buffer);
                                        Method readUtf8 = clonedBuffer.getClass().getMethod("readUtf8");
                                        bodyStr = (String) readUtf8.invoke(clonedBuffer);
                                    }
                                } catch (Throwable e2) {
                                    // Fallback: try buffer() method
                                    try {
                                        Method bufferMethod = source.getClass().getMethod("buffer");
                                        Object buffer = bufferMethod.invoke(source);
                                        if (buffer != null) {
                                            Method cloneMethod = buffer.getClass().getMethod("clone");
                                            Object clonedBuffer = cloneMethod.invoke(buffer);
                                            Method readUtf8 = clonedBuffer.getClass().getMethod("readUtf8");
                                            bodyStr = (String) readUtf8.invoke(clonedBuffer);
                                        }
                                    } catch (Throwable ignored) {}
                                }
                            }
                        }
                    } catch (Throwable e) {
                        // Silent fail for non-API responses
                    }

                    Log.i(TAG, "← [" + code + "] " + url);
                    // Always log API response bodies (JSON data)
                    if (isApiResponse && !bodyStr.isEmpty() && bodyStr.length() > 2) {
                        // For API responses, log even if it looks like binary (might be compressed JSON)
                        if (bodyStr.startsWith("{") || bodyStr.startsWith("[")) {
                            logLong(TAG, "  RespBody: " + truncate(bodyStr, 8000));
                        } else if (!looksLikeBinary(bodyStr)) {
                            logLong(TAG, "  RespBody: " + truncate(bodyStr, 4000));
                        }
                    }
                } catch (Throwable e) {
                    Log.w(TAG, "Response logging failed: " + e.getMessage());
                }
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

            String plaintext = new String(output, StandardCharsets.UTF_8);
            // 跳过明显不是内容的解密结果（非JSON且无中文）
            if (!plaintext.startsWith("{") && !looksLikeChineseText(output)) return;

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
}
