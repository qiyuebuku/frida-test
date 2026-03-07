package com.yuyang.wxassistant;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.AccessibilityServiceInfo;
import android.os.Handler;
import android.os.Looper;
import android.util.DisplayMetrics;
import android.util.Log;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;

import java.util.List;

public class WxAccessibilityService extends AccessibilityService {

    private static final String TAG = "WxAccessibilityService";
    private static final String WX_PACKAGE = "com.tencent.mm";
    private static final long SCROLL_DEBOUNCE_MS = 400;
    private static final long INITIAL_CAPTURE_DELAY_MS = 600;

    private static final String[] CHAT_ACTIVITIES = {
            "com.tencent.mm.ui.LauncherUI",
            "com.tencent.mm.ui.chatting.ChattingUI"
    };

    private FloatingWindowManager floatingManager;
    private AppPreferences prefs;
    private AiApiClient aiClient;
    private MessageCollector collector;
    private Handler handler;

    private boolean inChatPage = false;
    private Runnable pendingCapture;

    private static WxAccessibilityService instance;

    public static WxAccessibilityService getInstance() {
        return instance;
    }

    @Override
    public void onServiceConnected() {
        super.onServiceConnected();
        instance = this;

        // 程序化设置 flag，确保 getWindows() 可用
        AccessibilityServiceInfo info = getServiceInfo();
        if (info != null) {
            info.flags |= AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS
                    | AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS
                    | AccessibilityServiceInfo.FLAG_INCLUDE_NOT_IMPORTANT_VIEWS;
            setServiceInfo(info);
        }

        prefs = new AppPreferences(this);
        aiClient = new AiApiClient(prefs);
        collector = new MessageCollector();
        handler = new Handler(Looper.getMainLooper());

        floatingManager = new FloatingWindowManager(this);
        floatingManager.setOnButtonClicked(this::onButtonClicked);
        floatingManager.setOnSendToAi(this::onSendToAi);
        floatingManager.setOnClearCollection(this::onClearCollection);

        Log.i(TAG, "WxAccessibilityService connected");
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        if (event == null) return;
        String packageName = event.getPackageName() != null ? event.getPackageName().toString() : "";

        if (!WX_PACKAGE.equals(packageName)) {
            if (inChatPage) {
                exitChatPage();
            }
            return;
        }

        int eventType = event.getEventType();

        if (eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
            String className = event.getClassName() != null ? event.getClassName().toString() : "";
            boolean isChatActivity = false;
            for (String act : CHAT_ACTIVITIES) {
                if (act.equals(className)) {
                    isChatActivity = true;
                    break;
                }
            }

            if (isChatActivity) {
                if (!inChatPage) {
                    enterChatPage();
                }
            } else if (className.startsWith("com.tencent.mm")) {
                if (inChatPage) {
                    exitChatPage();
                }
            }
        } else if (eventType == AccessibilityEvent.TYPE_VIEW_SCROLLED && inChatPage) {
            // Debounce scroll captures
            scheduleCaptureDebounced();
        }
    }

    private void enterChatPage() {
        inChatPage = true;
        collector.clear();
        floatingManager.showButton();
        floatingManager.updateButtonBadge(0);

        // Initial capture after a short delay to let the UI settle
        handler.postDelayed(this::captureAndMerge, INITIAL_CAPTURE_DELAY_MS);
        Log.d(TAG, "Entered WeChat chat page");
    }

    private void exitChatPage() {
        inChatPage = false;
        cancelPendingCapture();
        collector.clear();
        floatingManager.hideButton();
        floatingManager.hidePanel();
        Log.d(TAG, "Left WeChat chat page");
    }

    private void scheduleCaptureDebounced() {
        cancelPendingCapture();
        pendingCapture = this::captureAndMerge;
        handler.postDelayed(pendingCapture, SCROLL_DEBOUNCE_MS);
    }

    private void cancelPendingCapture() {
        if (pendingCapture != null) {
            handler.removeCallbacks(pendingCapture);
            pendingCapture = null;
        }
    }

    /**
     * 遍历所有窗口，找到微信的窗口根节点，避免获取到悬浮窗自身的节点。
     */
    private AccessibilityNodeInfo findWeChatRoot() {
        try {
            List<AccessibilityWindowInfo> windows = getWindows();
            for (AccessibilityWindowInfo window : windows) {
                AccessibilityNodeInfo root = window.getRoot();
                if (root != null) {
                    CharSequence pkg = root.getPackageName();
                    if (pkg != null && WX_PACKAGE.equals(pkg.toString())) {
                        return root;
                    }
                }
            }
        } catch (Exception e) {
            Log.w(TAG, "getWindows failed, fallback to getRootInActiveWindow", e);
        }
        return getRootInActiveWindow();
    }

    private void captureAndMerge() {
        AccessibilityNodeInfo root = findWeChatRoot();
        if (root == null) return;

        DisplayMetrics dm = getResources().getDisplayMetrics();
        WxMessageParser.ParseResult result = WxMessageParser.parse(root, dm, false);

        if (result.messages.isEmpty()) return;

        collector.setChatTitle(result.chatTitle);
        collector.merge(result.messages);
        floatingManager.updateButtonBadge(collector.getCount());

        // If panel is showing collection view, update it
        if (floatingManager.isPanelShowing()) {
            floatingManager.showCollectionView(collector.formatForDisplay(), collector.getCount());
        }
    }

    private void onButtonClicked() {
        // Capture current screen first
        captureAndMerge();

        if (prefs.isDebugMode()) {
            StringBuilder debug = new StringBuilder("=== 调试模式 ===\n\n");
            AccessibilityNodeInfo wxRoot = findWeChatRoot();

            if (wxRoot == null) {
                debug.append("findWeChatRoot() 返回 null\n");
                floatingManager.showResult(debug.toString());
                return;
            }

            // 从 bn1 节点开始 dump 子树
            debug.append("=== 从 bn1 开始遍历 ===\n");
            java.util.List<AccessibilityNodeInfo> bn1List =
                    wxRoot.findAccessibilityNodeInfosByViewId("com.tencent.mm:id/bn1");
            if (bn1List != null && !bn1List.isEmpty()) {
                AccessibilityNodeInfo bn1 = bn1List.get(0);
                debug.append("bn1: cls=").append(bn1.getClassName());
                android.graphics.Rect bb = new android.graphics.Rect();
                bn1.getBoundsInScreen(bb);
                debug.append(" bounds=").append(bb.toShortString())
                        .append(" children=").append(bn1.getChildCount()).append("\n");
                dumpSubtree(bn1, 1, debug, 8);
            } else {
                debug.append("bn1 未找到\n");
            }

            // 搜索聊天界面可见文字
            debug.append("\n=== 搜索屏幕可见文本 ===\n");
            String[] keywords = {"你好", "我是雨阳", "西柚", "聊天"};
            for (String kw : keywords) {
                java.util.List<AccessibilityNodeInfo> found = wxRoot.findAccessibilityNodeInfosByText(kw);
                int count = found != null ? found.size() : 0;
                debug.append("\"").append(kw).append("\": ").append(count).append("个");
                if (found != null) {
                    for (AccessibilityNodeInfo f : found) {
                        debug.append("\n  text=").append(f.getText())
                                .append(" cls=").append(f.getClassName())
                                .append(" desc=").append(f.getContentDescription());
                        android.graphics.Rect fb = new android.graphics.Rect();
                        f.getBoundsInScreen(fb);
                        debug.append(" bounds=").append(fb.toShortString());
                        debug.append(" parent=");
                        AccessibilityNodeInfo parent = f.getParent();
                        if (parent != null) {
                            debug.append(parent.getClassName());
                            android.graphics.Rect pb = new android.graphics.Rect();
                            parent.getBoundsInScreen(pb);
                            debug.append(pb.toShortString());
                        } else {
                            debug.append("null");
                        }
                    }
                }
                debug.append("\n");
            }

            // 暴力搜索一批常见微信 resource-id
            debug.append("\n=== 批量 ID 搜索 ===\n");
            String[] ids = {
                "com.tencent.mm:id/bn1",   // 可能是消息列表容器
                "com.tencent.mm:id/obn",   // 常见聊天气泡
                "com.tencent.mm:id/b4h",   // 消息文本
                "com.tencent.mm:id/b6o",   // 消息文本 (另一个版本)
                "com.tencent.mm:id/kbb",   // 标题
                "com.tencent.mm:id/bkz",   // 标题 (另一版本)
                "com.tencent.mm:id/khj",   // 聊天列表
                "com.tencent.mm:id/b79",   // RecyclerView
            };
            for (String id : ids) {
                java.util.List<AccessibilityNodeInfo> found = wxRoot.findAccessibilityNodeInfosByViewId(id);
                int count = found != null ? found.size() : 0;
                if (count > 0) {
                    AccessibilityNodeInfo f = found.get(0);
                    android.graphics.Rect fb = new android.graphics.Rect();
                    f.getBoundsInScreen(fb);
                    debug.append(id).append(": ").append(count).append("个")
                            .append(" cls=").append(f.getClassName())
                            .append(" bounds=").append(fb.toShortString())
                            .append(" children=").append(f.getChildCount())
                            .append(" text=").append(f.getText())
                            .append("\n");
                }
            }

            floatingManager.showResult(debug.toString());
            return;
        }

        // Show collection panel
        floatingManager.showCollectionView(collector.formatForDisplay(), collector.getCount());
    }

    private void onSendToAi() {
        java.util.List<ChatMessage> messages = collector.getMessages();

        if (messages.isEmpty()) {
            floatingManager.showError("没有收集到消息，请先在微信聊天界面滚动");
            return;
        }

        String apiKey = prefs.getApiKey();
        if (apiKey == null || apiKey.isEmpty()) {
            floatingManager.showError("请先在 WX Assistant 中配置 API Key");
            return;
        }

        floatingManager.showLoading();

        aiClient.requestReply(messages, collector.getChatTitle(), new AiApiClient.Callback() {
            @Override
            public void onSuccess(String reply) {
                floatingManager.showResult(reply);
            }

            @Override
            public void onError(String error) {
                floatingManager.showError(error);
            }
        });
    }

    private void onClearCollection() {
        collector.clear();
        floatingManager.updateButtonBadge(0);
        floatingManager.showCollectionView("", 0);

        // Re-capture current screen after clearing
        handler.postDelayed(this::captureAndMerge, 300);
    }

    private void dumpSubtree(AccessibilityNodeInfo node, int depth, StringBuilder sb, int maxDepth) {
        if (node == null || depth > maxDepth) return;
        String indent = "  ".repeat(depth);
        String cls = node.getClassName() != null ? node.getClassName().toString() : "null";
        if (cls.contains(".")) cls = cls.substring(cls.lastIndexOf('.') + 1);

        android.graphics.Rect bounds = new android.graphics.Rect();
        node.getBoundsInScreen(bounds);

        sb.append(indent).append(cls);
        String vid = node.getViewIdResourceName();
        if (vid != null) sb.append(" [").append(vid).append("]");
        if (node.getText() != null) {
            String t = node.getText().toString();
            if (t.length() > 40) t = t.substring(0, 40) + "...";
            sb.append(" \"").append(t).append("\"");
        }
        if (node.getContentDescription() != null) {
            sb.append(" desc=\"").append(node.getContentDescription()).append("\"");
        }
        sb.append(" ").append(bounds.toShortString());
        sb.append(" ch=").append(node.getChildCount());
        sb.append("\n");

        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) {
                dumpSubtree(child, depth + 1, sb, maxDepth);
            } else {
                sb.append(indent).append("  [").append(i).append("] = null\n");
            }
        }
    }

    @Override
    public void onInterrupt() {
        Log.w(TAG, "WxAccessibilityService interrupted");
    }

    @Override
    public void onDestroy() {
        super.onDestroy();
        instance = null;
        cancelPendingCapture();
        if (floatingManager != null) {
            floatingManager.destroy();
            floatingManager = null;
        }
        Log.i(TAG, "WxAccessibilityService destroyed");
    }
}
