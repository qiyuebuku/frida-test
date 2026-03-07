package com.yuyang.wxassistant;

import android.graphics.Rect;
import android.util.DisplayMetrics;
import android.util.Log;
import android.view.accessibility.AccessibilityNodeInfo;

import java.util.ArrayList;
import java.util.List;

public class WxMessageParser {

    private static final String TAG = "WxMessageParser";

    // WeChat known resource IDs (may change across versions)
    private static final String ID_CHAT_LIST = "com.tencent.mm:id/bn1";
    private static final String ID_CHAT_TITLE = "com.tencent.mm:id/kbb";

    public static class ParseResult {
        public final String chatTitle;
        public final List<ChatMessage> messages;
        public final String debugDump;

        public ParseResult(String chatTitle, List<ChatMessage> messages, String debugDump) {
            this.chatTitle = chatTitle;
            this.messages = messages;
            this.debugDump = debugDump;
        }
    }

    public static ParseResult parse(AccessibilityNodeInfo root, DisplayMetrics displayMetrics, boolean debugMode) {
        if (root == null) {
            return new ParseResult("", new ArrayList<>(), "root is null");
        }

        StringBuilder debugSb = debugMode ? new StringBuilder() : null;

        // 1. Extract chat title
        String chatTitle = extractChatTitle(root);

        // 2. Find the message list container
        AccessibilityNodeInfo listNode = findMessageList(root);

        if (debugMode && debugSb != null) {
            debugSb.append("=== 节点树 Dump ===\n");
            dumpNodeTree(root, 0, debugSb);
            debugSb.append("\n=== 解析结果 ===\n");
            debugSb.append("聊天标题: ").append(chatTitle).append("\n");
            debugSb.append("列表节点: ").append(listNode != null ? listNode.getClassName() : "未找到").append("\n");
        }

        List<ChatMessage> messages = new ArrayList<>();
        if (listNode != null) {
            int screenCenterX = displayMetrics.widthPixels / 2;
            extractMessages(listNode, messages, screenCenterX, displayMetrics.widthPixels, debugSb);
        }

        if (debugMode && debugSb != null) {
            debugSb.append("消息数量: ").append(messages.size()).append("\n");
        }

        return new ParseResult(
                chatTitle,
                messages,
                debugSb != null ? debugSb.toString() : null
        );
    }

    private static String extractChatTitle(AccessibilityNodeInfo root) {
        // Try known resource ID first
        List<AccessibilityNodeInfo> nodes = root.findAccessibilityNodeInfosByViewId(ID_CHAT_TITLE);
        if (nodes != null && !nodes.isEmpty()) {
            CharSequence text = nodes.get(0).getText();
            if (text != null) return text.toString();
        }

        // Fallback: find the first meaningful TextView near the top of the screen
        return findTopTitle(root);
    }

    private static String findTopTitle(AccessibilityNodeInfo node) {
        if (node == null) return "";

        Rect bounds = new Rect();
        node.getBoundsInScreen(bounds);

        // Title is typically in the top area (within first 200px)
        if (bounds.top < 200 && bounds.bottom < 300) {
            if (isTextView(node) && node.getText() != null) {
                String text = node.getText().toString().trim();
                // Filter out time/status bar text, keep meaningful titles
                if (!text.isEmpty() && text.length() < 30 && !text.contains(":") && !text.matches("\\d+")) {
                    return text;
                }
            }
        }

        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) {
                String result = findTopTitle(child);
                if (!result.isEmpty()) return result;
            }
        }
        return "";
    }

    private static AccessibilityNodeInfo findMessageList(AccessibilityNodeInfo root) {
        // Try known resource ID
        List<AccessibilityNodeInfo> nodes = root.findAccessibilityNodeInfosByViewId(ID_CHAT_LIST);
        if (nodes != null && !nodes.isEmpty()) {
            return nodes.get(0);
        }

        // Fallback: find ListView or RecyclerView that takes significant screen space
        return findListNode(root);
    }

    private static AccessibilityNodeInfo findListNode(AccessibilityNodeInfo node) {
        if (node == null) return null;

        String className = node.getClassName() != null ? node.getClassName().toString() : "";

        if (className.contains("ListView") || className.contains("RecyclerView")) {
            Rect bounds = new Rect();
            node.getBoundsInScreen(bounds);
            int height = bounds.height();
            // The main chat list should be tall (at least 500px)
            if (height > 500) {
                return node;
            }
        }

        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) {
                AccessibilityNodeInfo result = findListNode(child);
                if (result != null) return result;
            }
        }
        return null;
    }

    private static void extractMessages(AccessibilityNodeInfo listNode, List<ChatMessage> messages,
                                         int screenCenterX, int screenWidth, StringBuilder debugSb) {
        for (int i = 0; i < listNode.getChildCount(); i++) {
            AccessibilityNodeInfo child = listNode.getChild(i);
            if (child == null) continue;

            // Extract all TextViews from this message item
            List<TextNodeInfo> textNodes = new ArrayList<>();
            collectTextNodes(child, textNodes);

            if (textNodes.isEmpty()) continue;

            // Find the main message text (usually the largest or most prominent one)
            TextNodeInfo mainText = findMainMessageText(textNodes, screenCenterX, screenWidth);
            if (mainText == null) continue;

            // Determine direction based on the main text's horizontal position
            Rect bounds = mainText.bounds;
            int centerX = bounds.centerX();

            // Check if it's a system message (centered and narrow)
            int textWidth = bounds.width();
            boolean isCentered = Math.abs(centerX - screenCenterX) < screenWidth * 0.1;
            boolean isNarrow = textWidth < screenWidth * 0.5;

            if (isCentered && isNarrow) {
                // System message, skip
                if (debugSb != null) {
                    debugSb.append("[系统消息] ").append(mainText.text).append("\n");
                }
                continue;
            }

            ChatMessage.Direction direction = centerX > screenCenterX
                    ? ChatMessage.Direction.SENT
                    : ChatMessage.Direction.RECEIVED;

            // Try to find sender name (for group chats)
            // Sender name is typically a small text above the message bubble
            String sender = findSenderName(textNodes, mainText, direction);

            ChatMessage msg = new ChatMessage(sender, mainText.text, direction);
            messages.add(msg);

            if (debugSb != null) {
                debugSb.append("[").append(direction == ChatMessage.Direction.SENT ? "发送" : "接收").append("] ");
                if (sender != null) debugSb.append(sender).append(": ");
                debugSb.append(mainText.text).append("\n");
            }
        }
    }

    private static TextNodeInfo findMainMessageText(List<TextNodeInfo> textNodes, int screenCenterX, int screenWidth) {
        TextNodeInfo best = null;
        int bestScore = -1;

        for (TextNodeInfo tn : textNodes) {
            if (tn.text.isEmpty()) continue;

            // Skip very short text that might be time stamps
            if (tn.text.length() <= 2 && tn.text.matches("[\\d:]+")) continue;

            // Score: prefer longer text, avoid tiny bounds
            int score = tn.text.length();
            if (tn.bounds.width() > 50 && tn.bounds.height() > 30) {
                score += 100;
            }

            if (score > bestScore) {
                bestScore = score;
                best = tn;
            }
        }
        return best;
    }

    private static String findSenderName(List<TextNodeInfo> textNodes, TextNodeInfo mainText, ChatMessage.Direction direction) {
        if (direction == ChatMessage.Direction.SENT) return null;

        for (TextNodeInfo tn : textNodes) {
            if (tn == mainText) continue;
            if (tn.text.isEmpty()) continue;

            // Sender name is above the main message and relatively short
            if (tn.bounds.bottom <= mainText.bounds.top + 10
                    && tn.text.length() < 20
                    && !tn.text.matches("[\\d:]+")) {
                return tn.text;
            }
        }
        return null;
    }

    private static void collectTextNodes(AccessibilityNodeInfo node, List<TextNodeInfo> result) {
        if (node == null) return;

        if (isTextView(node) && node.getText() != null) {
            String text = node.getText().toString().trim();
            if (!text.isEmpty()) {
                Rect bounds = new Rect();
                node.getBoundsInScreen(bounds);
                result.add(new TextNodeInfo(text, bounds));
            }
        }

        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) {
                collectTextNodes(child, result);
            }
        }
    }

    private static boolean isTextView(AccessibilityNodeInfo node) {
        String className = node.getClassName() != null ? node.getClassName().toString() : "";
        return className.contains("TextView");
    }

    private static void dumpNodeTree(AccessibilityNodeInfo node, int depth, StringBuilder sb) {
        if (node == null) return;
        if (depth > 15) return; // Prevent too deep recursion

        String indent = "  ".repeat(depth);
        String className = node.getClassName() != null ? node.getClassName().toString() : "null";
        String shortClass = className.contains(".") ? className.substring(className.lastIndexOf('.') + 1) : className;

        Rect bounds = new Rect();
        node.getBoundsInScreen(bounds);

        sb.append(indent).append(shortClass);

        String viewId = node.getViewIdResourceName();
        if (viewId != null) {
            sb.append(" [").append(viewId).append("]");
        }

        if (node.getText() != null) {
            String text = node.getText().toString();
            if (text.length() > 50) text = text.substring(0, 50) + "...";
            sb.append(" text=\"").append(text).append("\"");
        }

        if (node.getContentDescription() != null) {
            sb.append(" desc=\"").append(node.getContentDescription()).append("\"");
        }

        sb.append(" ").append(bounds.toShortString());
        sb.append("\n");

        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) {
                dumpNodeTree(child, depth + 1, sb);
            }
        }
    }

    private static class TextNodeInfo {
        final String text;
        final Rect bounds;

        TextNodeInfo(String text, Rect bounds) {
            this.text = text;
            this.bounds = bounds;
        }
    }
}
