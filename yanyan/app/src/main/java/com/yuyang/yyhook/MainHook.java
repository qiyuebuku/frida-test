package com.yuyang.yyhook;

import android.app.Application;
import android.util.Log;

import java.io.File;
import java.io.FileOutputStream;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.security.Key;
import java.security.spec.AlgorithmParameterSpec;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

import javax.crypto.Cipher;
import javax.crypto.spec.IvParameterSpec;
import javax.crypto.spec.SecretKeySpec;

import top.canyie.pine.Pine;
import top.canyie.pine.PineConfig;
import top.canyie.pine.callback.MethodHook;

public class MainHook {

    private static final String TAG = "YYHook";
    private static volatile boolean hooksInstalled = false;
    private static final ConcurrentHashMap<String, Long> recentRequests = new ConcurrentHashMap<>();
    private static final long DEDUP_WINDOW_MS = 500;
    private static final AtomicInteger dumpCounter = new AtomicInteger(0);

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

            Method onCreate = Application.class.getDeclaredMethod("onCreate");
            Pine.hook(onCreate, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    Application app = (Application) callFrame.thisObject;
                    Log.i(TAG, "Application.onCreate: " + app.getClass().getName());
                    if (!hooksInstalled) {
                        hooksInstalled = true;
                        try {
                            appCacheDir = app.getCacheDir().getAbsolutePath();
                            Log.i(TAG, "App cache dir: " + appCacheDir);
                        } catch (Throwable e) {
                            appCacheDir = "/data/data/com.zhihu.vip.android/cache";
                        }
                        injectInterceptor(app.getClassLoader());
                        installCryptoHooks();
                        installNativeTextHooks(app.getClassLoader());
                    }
                }
            });
            Log.i(TAG, "Hook installed on Application.onCreate");

        } catch (Throwable e) {
            Log.e(TAG, "Hook failed", e);
        }
    }

    private static void injectInterceptor(ClassLoader cl) {
        try {
            Class<?> interceptorClass;
            try {
                interceptorClass = cl.loadClass("okhttp3.Interceptor");
            } catch (ClassNotFoundException e) {
                Log.w(TAG, "okhttp3.Interceptor not found - app may not use OkHttp");
                return;
            }

            Class<?> chainClass = cl.loadClass("okhttp3.Interceptor$Chain");
            Class<?> clientBuilderClass = cl.loadClass("okhttp3.OkHttpClient$Builder");

            Object loggingInterceptor = Proxy.newProxyInstance(
                    cl,
                    new Class<?>[]{interceptorClass},
                    new InterceptorHandler(cl, chainClass)
            );
            Log.i(TAG, "Logging interceptor created via Proxy");

            Method buildMethod = clientBuilderClass.getDeclaredMethod("build");
            Pine.hook(buildMethod, new MethodHook() {
                private final AtomicBoolean logged = new AtomicBoolean(false);

                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    try {
                        Object builder = callFrame.thisObject;
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

    private static class InterceptorHandler implements InvocationHandler {
        private final ClassLoader cl;
        private final Class<?> chainClass;

        InterceptorHandler(ClassLoader cl, Class<?> chainClass) {
            this.cl = cl;
            this.chainClass = chainClass;
        }

        @Override
        public Object invoke(Object proxy, Method method, Object[] args) throws Throwable {
            if ("toString".equals(method.getName())) return "YYHookInterceptor";
            if ("equals".equals(method.getName())) return proxy == args[0];
            if ("hashCode".equals(method.getName())) return System.identityHashCode(proxy);

            if (!"intercept".equals(method.getName()) || args == null || args.length != 1) {
                return method.invoke(proxy, args);
            }

            Object chain = args[0];

            Method requestMethod = chainClass.getDeclaredMethod("request");
            Object request = requestMethod.invoke(chain);

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
                Method proceedMethod = chainClass.getDeclaredMethod("proceed",
                        cl.loadClass("okhttp3.Request"));
                return proceedMethod.invoke(chain, request);
            }

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
                    if (url.contains("manuscript/code")) {
                        dumpToFile("manuscript_code_request", bodyContent);
                    }
                }
            }

            Method proceedMethod = chainClass.getDeclaredMethod("proceed",
                    cl.loadClass("okhttp3.Request"));
            Object response = proceedMethod.invoke(chain, request);

            if (shouldLog && response != null) {
                try {
                    Method codeMethod = response.getClass().getDeclaredMethod("code");
                    int code = (int) codeMethod.invoke(response);

                    String bodyStr = "";
                    try {
                        Method peekBodyMethod = response.getClass()
                                .getDeclaredMethod("peekBody", long.class);
                        Object peekBody = peekBodyMethod.invoke(response, 65536L);
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
                    if (!bodyStr.isEmpty() && bodyStr.length() > 2 && !looksLikeBinary(bodyStr)) {
                        if (url.contains("manu_core") || url.contains("manuscript/code")) {
                            dumpToFile(url, bodyStr);
                        }
                        logLong(TAG, "  Body: " + truncate(bodyStr, 4000));
                    }
                } catch (Throwable ignored) {}
            }

            return response;
        }
    }

    private static void installCryptoHooks() {
        try {
            // Hook RSA encrypt to capture the random AES key (K1) used for trans_key
            for (Method m : Cipher.class.getDeclaredMethods()) {
                if ("doFinal".equals(m.getName())) {
                    Pine.hook(m, new MethodHook() {
                        @Override
                        public void beforeCall(Pine.CallFrame callFrame) {
                            try {
                                Cipher cipher = (Cipher) callFrame.thisObject;
                                String algo = cipher.getAlgorithm();
                                if (algo != null && algo.contains("RSA") && callFrame.args != null
                                        && callFrame.args.length > 0 && callFrame.args[0] instanceof byte[]) {
                                    byte[] input = (byte[]) callFrame.args[0];
                                    Log.i(TAG, "RSA_KEY K1[" + input.length + "]: " + bytesToHex(input));
                                    // Save K1 to file for offline decryption
                                    dumpToFile("rsa_k1_input", bytesToHex(input));
                                }
                            } catch (Throwable ignored) {}
                        }
                    });
                }
            }

            Log.i(TAG, "Crypto hooks installed (RSA K1 capture)");
        } catch (Throwable e) {
            Log.e(TAG, "Failed to install crypto hooks", e);
        }
    }

    private static void installNativeTextHooks(ClassLoader cl) {
        try {
            // Hook BaseJniWarp.getText() to capture decrypted chapter text
            Class<?> baseJniWarpClass = cl.loadClass("com.zhihu.android.app.nextebook.jni.BaseJniWarp");
            Class<?> ePageIndexClass = cl.loadClass("com.zhihu.android.app.nextebook.jni.BaseJniWarp$EPageIndex");

            for (Method m : baseJniWarpClass.getDeclaredMethods()) {
                if ("getText".equals(m.getName())) {
                    Pine.hook(m, new MethodHook() {
                        @Override
                        public void afterCall(Pine.CallFrame callFrame) {
                            try {
                                String text = (String) callFrame.getResult();
                                if (text != null && text.length() > 10) {
                                    Log.i(TAG, "NATIVE_TEXT[" + text.length() + "]: " + truncate(text, 500));
                                    if (text.length() > 100) {
                                        dumpToFile("native_getText", text);
                                    }
                                }
                            } catch (Throwable ignored) {}
                        }
                    });
                    Log.i(TAG, "Hooked BaseJniWarp.getText()");
                }
                // Also hook getTextWithPara for paragraph-level text
                if ("getTextWithPara".equals(m.getName())) {
                    Pine.hook(m, new MethodHook() {
                        @Override
                        public void afterCall(Pine.CallFrame callFrame) {
                            try {
                                String[] texts = (String[]) callFrame.getResult();
                                if (texts != null && texts.length > 0) {
                                    StringBuilder sb = new StringBuilder();
                                    for (String t : texts) {
                                        if (t != null && !t.isEmpty()) {
                                            sb.append(t).append("\n---\n");
                                        }
                                    }
                                    String all = sb.toString();
                                    if (all.length() > 10) {
                                        Log.i(TAG, "NATIVE_TEXT_PARA[" + texts.length + " paras, " + all.length() + " chars]");
                                        dumpToFile("native_getTextWithPara", all);
                                    }
                                }
                            } catch (Throwable ignored) {}
                        }
                    });
                    Log.i(TAG, "Hooked BaseJniWarp.getTextWithPara()");
                }
            }

            // Hook getParagraphText to capture text via ParagraphTextHandler
            Class<?> paragraphTextHandlerClass = cl.loadClass(
                    "com.zhihu.android.app.nextebook.jni.ParagraphTextHandler");
            for (Method m : baseJniWarpClass.getDeclaredMethods()) {
                if ("getParagraphText".equals(m.getName()) && m.getParameterTypes().length == 6) {
                    Pine.hook(m, new MethodHook() {
                        @Override
                        public void afterCall(Pine.CallFrame callFrame) {
                            try {
                                // The ParagraphTextHandler (last arg) holds the result
                                Object handler = callFrame.args[5];
                                Method getParagraphText = handler.getClass().getMethod("getParagraphText");
                                Object paragraphText = getParagraphText.invoke(handler);
                                if (paragraphText != null) {
                                    String text = paragraphText.toString();
                                    if (text.length() > 20) {
                                        Log.i(TAG, "PARAGRAPH_TEXT: " + truncate(text, 500));
                                    }
                                }
                            } catch (Throwable ignored) {}
                        }
                    });
                    Log.i(TAG, "Hooked BaseJniWarp.getParagraphText()");
                    break;
                }
            }

            // Find getText method reference for active extraction
            Method getTextMethod = null;
            Method getTextWithParaMethod = null;
            for (Method m : baseJniWarpClass.getDeclaredMethods()) {
                if ("getText".equals(m.getName())) {
                    getTextMethod = m;
                } else if ("getTextWithPara".equals(m.getName())) {
                    getTextWithParaMethod = m;
                }
            }
            final Method textMethod = getTextMethod;
            final Method textWithParaMethod = getTextWithParaMethod;

            // Hook getChapterItemHeightArray to extract text after chapter loads
            for (Method m : baseJniWarpClass.getDeclaredMethods()) {
                if ("getChapterItemHeightArray".equals(m.getName())) {
                    Pine.hook(m, new MethodHook() {
                        @Override
                        public void afterCall(Pine.CallFrame callFrame) {
                            try {
                                float[] heights = (float[]) callFrame.getResult();
                                if (heights == null) return;
                                Log.i(TAG, "CHAPTER_LOADED: " + heights.length + " pages");

                                // Get the EPageIndex and BaseJniWarp instance
                                Object ePageIndex = callFrame.args[0];
                                Object instance = callFrame.thisObject;

                                // Log EPageIndex fields for debugging
                                try {
                                    java.lang.reflect.Field fpField = ePageIndex.getClass().getField("filePath");
                                    java.lang.reflect.Field akField = ePageIndex.getClass().getField("aesKey");
                                    java.lang.reflect.Field rField = ePageIndex.getClass().getField("random");
                                    java.lang.reflect.Field rkField = ePageIndex.getClass().getField("randomKey");
                                    Log.i(TAG, "EPageIndex: filePath=" + truncate(String.valueOf(fpField.get(ePageIndex)), 100)
                                            + " aesKey=" + truncate(String.valueOf(akField.get(ePageIndex)), 60)
                                            + " random=" + String.valueOf(rField.get(ePageIndex))
                                            + " randomKey=" + String.valueOf(rkField.get(ePageIndex)));
                                } catch (Throwable e) {
                                    Log.w(TAG, "Failed to read EPageIndex fields: " + e);
                                }

                                // Actively call getText(ePageIndex, 0, Integer.MAX_VALUE) to extract full text
                                if (textMethod != null && instance != null) {
                                    try {
                                        String fullText = (String) textMethod.invoke(instance, ePageIndex, 0, Integer.MAX_VALUE);
                                        if (fullText != null && fullText.length() > 10) {
                                            Log.i(TAG, "EXTRACTED_TEXT[" + fullText.length() + "]: " + truncate(fullText, 500));
                                            dumpToFile("extracted_fulltext", fullText);
                                        } else {
                                            Log.i(TAG, "EXTRACTED_TEXT: null or empty");
                                        }
                                    } catch (Throwable e) {
                                        Log.w(TAG, "getText() failed: " + e.getMessage());
                                    }
                                }

                                // Also try getTextWithPara for structured text
                                if (textWithParaMethod != null && instance != null) {
                                    try {
                                        String[] paras = (String[]) textWithParaMethod.invoke(instance, ePageIndex, 0, Integer.MAX_VALUE);
                                        if (paras != null && paras.length > 0) {
                                            StringBuilder sb = new StringBuilder();
                                            for (String p : paras) {
                                                if (p != null && !p.isEmpty()) sb.append(p).append("\n");
                                            }
                                            String all = sb.toString();
                                            if (all.length() > 10) {
                                                Log.i(TAG, "EXTRACTED_PARA[" + paras.length + " paras, " + all.length() + " chars]");
                                                dumpToFile("extracted_paras", all);
                                            }
                                        }
                                    } catch (Throwable e) {
                                        Log.w(TAG, "getTextWithPara() failed: " + e.getMessage());
                                    }
                                }
                            } catch (Throwable e) {
                                Log.e(TAG, "getChapterItemHeightArray hook error", e);
                            }
                        }
                    });
                    Log.i(TAG, "Hooked BaseJniWarp.getChapterItemHeightArray()");
                    break;
                }
            }

            Log.i(TAG, "Native text hooks installed");
        } catch (Throwable e) {
            Log.e(TAG, "Failed to install native text hooks", e);
        }
    }

    private static String bytesToHex(byte[] bytes) {
        if (bytes == null) return "null";
        StringBuilder sb = new StringBuilder();
        int limit = Math.min(bytes.length, 32);
        for (int i = 0; i < limit; i++) {
            sb.append(String.format("%02x", bytes[i] & 0xff));
        }
        if (bytes.length > 32) sb.append("...(").append(bytes.length).append(" bytes)");
        return sb.toString();
    }

    private static String tryUtf8(byte[] bytes) {
        if (bytes == null) return "null";
        try {
            String s = new String(bytes, "UTF-8");
            // Check if it looks like text
            int nonPrintable = 0;
            int checkLen = Math.min(s.length(), 100);
            for (int i = 0; i < checkLen; i++) {
                char c = s.charAt(i);
                if (c < 0x20 && c != '\n' && c != '\r' && c != '\t') nonPrintable++;
            }
            if (nonPrintable > checkLen / 4) {
                return "[binary:" + bytesToHex(bytes) + "]";
            }
            return s;
        } catch (Throwable e) {
            return "[binary:" + bytesToHex(bytes) + "]";
        }
    }

    private static String appCacheDir = null;

    private static void dumpToFile(String url, String body) {
        try {
            if (appCacheDir == null) return;
            int idx = dumpCounter.incrementAndGet();
            String safeName = url.replaceAll("[^a-zA-Z0-9_]", "_");
            if (safeName.length() > 80) safeName = safeName.substring(safeName.length() - 80);
            String fileName = "yyhook_" + idx + "_" + safeName + ".json";
            File dir = new File(appCacheDir, "yyhook_dump");
            dir.mkdirs();
            File f = new File(dir, fileName);
            FileOutputStream fos = new FileOutputStream(f);
            fos.write(body.getBytes("UTF-8"));
            fos.close();
            Log.i(TAG, "  DUMP: " + body.length() + " chars -> " + f.getAbsolutePath());
        } catch (Throwable e) {
            Log.e(TAG, "Failed to dump body", e);
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
}
