/**
 * WebView XMLHttpRequest Hook - 注入 JS 代码捕获所有 AJAX 请求
 *
 * 使用方法：
 * 1. 在 hookWebViewRequests() 中，Hook WebView.setWebViewClient
 * 2. 当 WebViewClient.onPageStarted 触发时，注入这段 JS
 * 3. JS 会 Hook XMLHttpRequest.prototype.open/send，捕获所有请求
 */

// ===== 在 MainHook.java 中添加这个方法 =====

private static final String XHR_HOOK_SCRIPT =
    "(function() {" +
    "    var originalOpen = XMLHttpRequest.prototype.open;" +
    "    var originalSend = XMLHttpRequest.prototype.send;" +
    "    var originalSetRequestHeader = XMLHttpRequest.prototype.setRequestHeader;" +
    "    " +
    "    XMLHttpRequest.prototype.open = function(method, url, async, user, password) {" +
    "        this._method = method;" +
    "        this._url = url;" +
    "        this._requestHeaders = {};" +
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
    "        // 构建请求信息" +
    "        var requestInfo = {" +
    "            method: xhr._method," +
    "            url: xhr._url," +
    "            headers: xhr._requestHeaders," +
    "            body: body || null," +
    "            timestamp: Date.now()" +
    "        };" +
    "        " +
    "        // 过滤：只捕获 trade.5ifund.com 的请求" +
    "        if (xhr._url && xhr._url.indexOf('trade.5ifund.com') > -1) {" +
    "            // 通过 console.log 输出，会被 logcat 捕获" +
    "            console.log('[XHR_HOOK_REQUEST]', JSON.stringify(requestInfo));" +
    "        }" +
    "        " +
    "        // Hook 响应" +
    "        xhr.addEventListener('load', function() {" +
    "            if (xhr._url && xhr._url.indexOf('trade.5ifund.com') > -1) {" +
    "                var responseInfo = {" +
    "                    method: xhr._method," +
    "                    url: xhr._url," +
    "                    status: xhr.status," +
    "                    responseText: xhr.responseText," +
    "                    timestamp: Date.now()" +
    "                };" +
    "                console.log('[XHR_HOOK_RESPONSE]', JSON.stringify(responseInfo));" +
    "            }" +
    "        });" +
    "        " +
    "        return originalSend.apply(this, arguments);" +
    "    };" +
    "    " +
    "    console.log('[XHR_HOOK] XMLHttpRequest hooked successfully');" +
    "})();";

/**
 * 改进的 hookWebViewRequests - 注入 XHR Hook JS
 */
private static void hookWebViewRequests() throws Throwable {
    Class<?> webViewClass = Class.forName("android.webkit.WebView");

    // Hook setWebViewClient，在页面加载时注入 JS
    Method setWebViewClient = webViewClass.getDeclaredMethod("setWebViewClient",
            Class.forName("android.webkit.WebViewClient"));

    Pine.hook(setWebViewClient, new MethodHook() {
        @Override
        public void afterCall(Pine.CallFrame callFrame) {
            try {
                Object webView = callFrame.thisObject;
                Object client = callFrame.args[0];

                // Hook WebViewClient.onPageStarted
                Class<?> clientClass = client.getClass();
                Method onPageStarted = clientClass.getMethod("onPageStarted",
                        webViewClass, String.class, Class.forName("android.graphics.Bitmap"));

                Pine.hook(onPageStarted, new MethodHook() {
                    @Override
                    public void afterCall(Pine.CallFrame frame) {
                        try {
                            Object wv = frame.args[0];
                            String url = (String) frame.args[1];

                            // 只在 fund 相关页面注入
                            if (url != null && url.contains("trade.5ifund.com")) {
                                Log.i(TAG, "Injecting XHR Hook into: " + url);

                                // 注入 XHR Hook JS
                                Method evalJs = wv.getClass().getDeclaredMethod("evaluateJavascript",
                                        String.class, Class.forName("android.webkit.ValueCallback"));
                                evalJs.invoke(wv, XHR_HOOK_SCRIPT, null);

                                Log.i(TAG, "XHR Hook injected successfully");
                            }
                        } catch (Throwable e) {
                            Log.e(TAG, "Failed to inject XHR Hook", e);
                        }
                    }
                });

            } catch (Throwable e) {
                Log.e(TAG, "Failed to hook WebViewClient", e);
            }
        }
    });

    // Hook WebView.loadUrl（原有代码）
    Method loadUrl = webViewClass.getDeclaredMethod("loadUrl", String.class);
    Pine.hook(loadUrl, new MethodHook() {
        @Override
        public void beforeCall(Pine.CallFrame callFrame) {
            Object webView = callFrame.thisObject;
            String url = (String) callFrame.args[0];

            Log.i(TAG, "WebView.loadUrl → " + url);

            // 如果是 fund 页面，注入 XHR Hook
            if (url != null && url.contains("trade.5ifund.com")) {
                // 延迟注入（等待页面 DOM 加载）
                new Handler(Looper.getMainLooper()).postDelayed(new Runnable() {
                    @Override
                    public void run() {
                        try {
                            Log.i(TAG, "Injecting XHR Hook (delayed)...");
                            Method evalJs = webView.getClass().getDeclaredMethod("evaluateJavascript",
                                    String.class, Class.forName("android.webkit.ValueCallback"));
                            evalJs.invoke(webView, XHR_HOOK_SCRIPT, null);
                            Log.i(TAG, "XHR Hook injected successfully (delayed)");
                        } catch (Throwable e) {
                            Log.e(TAG, "Failed to inject XHR Hook (delayed)", e);
                        }
                    }
                }, 500); // 延迟 500ms
            }
        }
    });

    // ... 原有的其他 Hook 代码 ...
}

// ===== 在 capture_fund_requests.py 中添加日志解析 =====
"""
def extract_xhr_calls(self, logcat_output):
    \"\"\"
    从 logcat 中提取 XHR Hook 捕获的请求

    Returns:
        list of dict: XHR 请求列表
    \"\"\"
    requests = []
    responses = []

    # 匹配 XHR Hook 日志
    # 格式: [XHR_HOOK_REQUEST] {"method":"POST","url":"https://...","headers":{...},"body":"..."}
    request_pattern = r'\\[XHR_HOOK_REQUEST\\]\\s+(\\{.*?\\})'
    response_pattern = r'\\[XHR_HOOK_RESPONSE\\]\\s+(\\{.*?\\})'

    for match in re.finditer(request_pattern, logcat_output, re.DOTALL):
        try:
            data = json.loads(match.group(1))
            requests.append(data)
        except:
            pass

    for match in re.finditer(response_pattern, logcat_output, re.DOTALL):
        try:
            data = json.loads(match.group(1))
            responses.append(data)
        except:
            pass

    return requests, responses
"""
