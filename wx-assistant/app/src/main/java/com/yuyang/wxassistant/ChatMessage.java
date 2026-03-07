package com.yuyang.wxassistant;

public class ChatMessage {

    public enum Direction {
        SENT,
        RECEIVED
    }

    private final String sender;
    private final String content;
    private final Direction direction;

    public ChatMessage(String sender, String content, Direction direction) {
        this.sender = sender;
        this.content = content;
        this.direction = direction;
    }

    public String getSender() {
        return sender;
    }

    public String getContent() {
        return content;
    }

    public Direction getDirection() {
        return direction;
    }

    public String toDisplayString() {
        if (sender != null && !sender.isEmpty()) {
            return sender + ": " + content;
        }
        return (direction == Direction.SENT ? "我" : "对方") + ": " + content;
    }
}
