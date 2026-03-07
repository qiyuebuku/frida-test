package com.yuyang.wxassistant;

import java.util.ArrayList;
import java.util.List;

/**
 * 收集并合并来自多次屏幕捕获的聊天消息，处理滚动产生的重叠去重。
 */
public class MessageCollector {

    private final List<ChatMessage> messages = new ArrayList<>();
    private String chatTitle = "";

    public synchronized void merge(List<ChatMessage> newMessages) {
        if (newMessages.isEmpty()) return;

        if (messages.isEmpty()) {
            messages.addAll(newMessages);
            return;
        }

        List<String> existingFps = buildFingerprints(messages);
        List<String> newFps = buildFingerprints(newMessages);

        // Case 1: scroll down — existing tail overlaps with new head
        int appendOverlap = findTailHeadOverlap(existingFps, newFps);
        if (appendOverlap > 0) {
            for (int i = appendOverlap; i < newMessages.size(); i++) {
                messages.add(newMessages.get(i));
            }
            return;
        }

        // Case 2: scroll up — new tail overlaps with existing head
        int prependOverlap = findTailHeadOverlap(newFps, existingFps);
        if (prependOverlap > 0) {
            List<ChatMessage> merged = new ArrayList<>(newMessages.subList(0, newMessages.size() - prependOverlap));
            merged.addAll(messages);
            messages.clear();
            messages.addAll(merged);
            return;
        }

        // Case 3: new messages entirely contained in existing — skip
        if (isSubsequence(newFps, existingFps)) {
            return;
        }

        // Case 4: no overlap found — append (may have gap)
        messages.addAll(newMessages);
    }

    /**
     * Find the longest overlap where the tail of `a` matches the head of `b`.
     */
    private int findTailHeadOverlap(List<String> a, List<String> b) {
        int maxOverlap = Math.min(a.size(), b.size());
        for (int len = maxOverlap; len >= 1; len--) {
            boolean match = true;
            for (int i = 0; i < len; i++) {
                if (!a.get(a.size() - len + i).equals(b.get(i))) {
                    match = false;
                    break;
                }
            }
            if (match) return len;
        }
        return 0;
    }

    private boolean isSubsequence(List<String> sub, List<String> sup) {
        if (sub.size() > sup.size()) return false;
        int j = 0;
        for (int i = 0; i < sup.size() && j < sub.size(); i++) {
            if (sup.get(i).equals(sub.get(j))) {
                j++;
            }
        }
        return j == sub.size();
    }

    private List<String> buildFingerprints(List<ChatMessage> msgs) {
        List<String> fps = new ArrayList<>(msgs.size());
        for (ChatMessage m : msgs) {
            fps.add(m.getDirection().name() + "|" + m.getContent());
        }
        return fps;
    }

    public synchronized List<ChatMessage> getMessages() {
        return new ArrayList<>(messages);
    }

    public synchronized int getCount() {
        return messages.size();
    }

    public synchronized String getChatTitle() {
        return chatTitle;
    }

    public synchronized void setChatTitle(String title) {
        if (title != null && !title.isEmpty()) {
            this.chatTitle = title;
        }
    }

    public synchronized void clear() {
        messages.clear();
        chatTitle = "";
    }

    public synchronized String formatForDisplay() {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < messages.size(); i++) {
            ChatMessage m = messages.get(i);
            if (m.getDirection() == ChatMessage.Direction.SENT) {
                sb.append("→ ");
            } else {
                sb.append("← ");
            }
            sb.append(m.toDisplayString());
            if (i < messages.size() - 1) sb.append("\n");
        }
        return sb.toString();
    }
}
