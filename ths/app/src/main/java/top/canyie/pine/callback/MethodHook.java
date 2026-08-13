package top.canyie.pine.callback;

import top.canyie.pine.Pine;

public abstract class MethodHook {
    public static final class Unhook {
        private Unhook() {
        }
    }

    public void beforeCall(Pine.CallFrame callFrame) throws Throwable {
    }

    public void afterCall(Pine.CallFrame callFrame) throws Throwable {
    }
}
