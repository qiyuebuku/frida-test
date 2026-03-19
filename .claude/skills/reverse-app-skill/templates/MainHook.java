package com.yuyang.hook;

// Zygisk + Pine ART Hook 模板
// 使用方法：
// 1. 替换 TAG 为你的标识
// 2. 在 installHooks() 中添加具体的 Hook 逻辑
// 3. 根据加固类型调整 Pine 配置

import android.app.Application;
import android.util.Log;

import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;

import javax.crypto.Cipher;

import top.canyie.pine.Pine;
import top.canyie.pine.PineConfig;
import top.canyie.pine.callback.MethodHook;

public class MainHook {
    private static final String TAG = "AppHook";  // 替换为你的标识
    private static final AtomicBoolean hooksInstalled = new AtomicBoolean(false);

    // OkHttp 去重：URL → 上次记录时间
    private static final ConcurrentHashMap<String, Long> recentRequests = new ConcurrentHashMap<>();
    private static final long DEDUP_WINDOW_MS = 500;

    // 捕获的 OkHttpClient
    private static volatile Object savedOkHttpClient = null;
    private static volatile boolean apiClientCaptured = false;

    /**
     * 入口方法 — 由 Zygisk C++ 层调用
     */
    public static void entry(ClassLoader classLoader, String pineSoPath) {
        try {
            // 1. 加载 Pine native 库
            System.load(pineSoPath);
            Log.i(TAG, "Pine SO loaded: " + pineSoPath);

            // 2. Pine 安全配置（适用所有加固）
            PineConfig.libLoader = new Pine.LibLoader() {
                @Override public void loadLib() { }  // 手动加载，跳过默认
            };
            PineConfig.debug = false;
            PineConfig.debuggable = false;
            PineConfig.antiChecks = true;              // 启用反检测
            PineConfig.disableHiddenApiPolicy = false;  // ⚠️ 关键：不修改 ART
            PineConfig.disableHiddenApiPolicyForPlatformDomain = false;

            Pine.ensureInitialized();
            Log.i(TAG, "Pine initialized");

            // 3. Hook Application.onCreate（安全的框架类 Hook）
            Method onCreate = Application.class.getDeclaredMethod("onCreate");
            Pine.hook(onCreate, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    if (!hooksInstalled.compareAndSet(false, true)) return;

                    Application app = (Application) callFrame.thisObject;
                    ClassLoader cl = app.getClassLoader();
                    Log.i(TAG, "Application.onCreate intercepted, ClassLoader: " + cl);

                    try {
                        installHooks(cl);
                    } catch (Exception e) {
                        Log.e(TAG, "Failed to install hooks", e);
                    }
                }
            });

            Log.i(TAG, "Entry completed successfully");
        } catch (Exception e) {
            Log.e(TAG, "Entry failed", e);
        }
    }

    /**
     * 安装所有 Hook — 在此添加你的具体 Hook 逻辑
     */
    private static void installHooks(ClassLoader cl) throws Exception {
        // === Hook 1: OkHttp 动态代理拦截器 ===
        injectOkHttpInterceptor(cl);

        // === Hook 2: Cipher 加密 Hook（可选） ===
        // installCipherHooks();

        // === Hook 3: 你的自定义 Hook ===
        // installCustomHooks(cl);

        Log.i(TAG, "All hooks installed");
    }

    /**
     * OkHttp 动态代理拦截器注入
     */
    private static void injectOkHttpInterceptor(ClassLoader cl) {
        try {
            Class<?> interceptorClass = cl.loadClass("okhttp3.Interceptor");
            Class<?> chainClass = cl.loadClass("okhttp3.Interceptor$Chain");
            Class<?> clientBuilderClass = cl.loadClass("okhttp3.OkHttpClient$Builder");
            Class<?> requestClass = cl.loadClass("okhttp3.Request");
            Class<?> responseClass = cl.loadClass("okhttp3.Response");

            Method addInterceptor = clientBuilderClass.getDeclaredMethod("addInterceptor", interceptorClass);
            Method buildMethod = clientBuilderClass.getDeclaredMethod("build");

            // 创建动态代理拦截器
            Object loggingInterceptor = Proxy.newProxyInstance(cl,
                new Class<?>[]{interceptorClass},
                new InvocationHandler() {
                    @Override
                    public Object invoke(Object proxy, Method method, Object[] args) throws Throwable {
                        if (!"intercept".equals(method.getName())) {
                            return method.invoke(proxy, args);
                        }

                        Object chain = args[0];
                        Method requestMethod = chainClass.getDeclaredMethod("request");
                        Method proceedMethod = chainClass.getDeclaredMethod("proceed", requestClass);

                        Object request = requestMethod.invoke(chain);

                        // 获取 URL
                        Method urlMethod = requestClass.getDeclaredMethod("url");
                        Object url = urlMethod.invoke(request);
                        String urlStr = url.toString();

                        // 去重 + 静态资源过滤
                        if (!shouldLog(urlStr)) {
                            return proceedMethod.invoke(chain, request);
                        }

                        // 获取 Method
                        Method methodMethod = requestClass.getDeclaredMethod("method");
                        String httpMethod = (String) methodMethod.invoke(request);

                        Log.i(TAG, "→ " + httpMethod + " " + urlStr);

                        // 执行请求
                        Object response = proceedMethod.invoke(chain, request);

                        // 获取响应码
                        Method codeMethod = responseClass.getDeclaredMethod("code");
                        int code = (int) codeMethod.invoke(response);

                        Log.i(TAG, "← [" + code + "] " + urlStr);

                        // 获取响应 Body（通过 peekBody，不消费原 Body）
                        try {
                            Method peekBody = responseClass.getDeclaredMethod("peekBody", long.class);
                            Object body = peekBody.invoke(response, 1024 * 100L); // 100KB
                            if (body != null) {
                                Method string = body.getClass().getDeclaredMethod("string");
                                String bodyStr = (String) string.invoke(body);
                                if (bodyStr != null && bodyStr.length() > 0) {
                                    Log.i(TAG, "RespBody: " + bodyStr.substring(0, Math.min(bodyStr.length(), 500)));
                                }
                            }
                        } catch (Exception e) {
                            // peekBody 可能失败，忽略
                        }

                        return response;
                    }
                });

            // Hook OkHttpClient$Builder.build()
            Pine.hook(buildMethod, new MethodHook() {
                @Override
                public void beforeCall(Pine.CallFrame callFrame) {
                    try {
                        addInterceptor.invoke(callFrame.thisObject, loggingInterceptor);
                    } catch (Exception e) {
                        Log.e(TAG, "Failed to inject interceptor", e);
                    }
                }
            });

            Log.i(TAG, "OkHttp interceptor installed");
        } catch (ClassNotFoundException e) {
            Log.w(TAG, "OkHttp not found in this app, skipping interceptor");
        } catch (Exception e) {
            Log.e(TAG, "Failed to install OkHttp interceptor", e);
        }
    }

    /**
     * Cipher 加密 Hook（可选）
     */
    private static void installCipherHooks() {
        try {
            // Hook Cipher.doFinal(byte[])
            Method doFinal = Cipher.class.getDeclaredMethod("doFinal", byte[].class);
            Pine.hook(doFinal, new MethodHook() {
                @Override
                public void afterCall(Pine.CallFrame callFrame) {
                    try {
                        Cipher cipher = (Cipher) callFrame.thisObject;
                        String algo = cipher.getAlgorithm();

                        // 过滤 TLS 流量
                        if (algo == null) return;
                        String upper = algo.toUpperCase();
                        if (upper.contains("GCM") || upper.contains("CHACHA")
                            || upper.contains("POLY1305") || upper.contains("OAEP")) {
                            return;
                        }

                        byte[] input = (byte[]) callFrame.args[0];
                        byte[] output = (byte[]) callFrame.getResult();
                        if (output == null) return;

                        Log.i(TAG, "Cipher " + algo
                            + " inLen=" + (input != null ? input.length : 0)
                            + " outLen=" + output.length);
                    } catch (Exception e) {
                        // ignore
                    }
                }
            });

            Log.i(TAG, "Cipher hooks installed");
        } catch (Exception e) {
            Log.e(TAG, "Failed to install Cipher hooks", e);
        }
    }

    /**
     * URL 去重 + 静态资源过滤
     */
    private static boolean shouldLog(String url) {
        if (url == null) return false;

        // 过滤静态资源
        String lower = url.toLowerCase();
        if (lower.endsWith(".jpg") || lower.endsWith(".jpeg") || lower.endsWith(".png")
            || lower.endsWith(".gif") || lower.endsWith(".webp") || lower.endsWith(".svg")
            || lower.endsWith(".css") || lower.endsWith(".woff") || lower.endsWith(".woff2")) {
            return false;
        }

        // 去重
        String key = url.split("\\?")[0]; // 不含 query 的 URL
        long now = System.currentTimeMillis();
        Long last = recentRequests.get(key);
        if (last != null && now - last < DEDUP_WINDOW_MS) {
            return false;
        }
        recentRequests.put(key, now);

        // 清理过期条目
        if (recentRequests.size() > 1000) {
            recentRequests.entrySet().removeIf(e -> now - e.getValue() > 60000);
        }

        return true;
    }
}
