package top.canyie.pine;

import java.lang.reflect.Member;

import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;
import top.canyie.pine.callback.MethodHook;

public final class Pine {
    private Pine() {
    }

    public interface LibLoader {
        void loadLib();
    }

    public static final class CallFrame {
        private final XC_MethodHook.MethodHookParam param;
        public final Object thisObject;
        public final Object[] args;

        private CallFrame(XC_MethodHook.MethodHookParam param) {
            this.param = param;
            this.thisObject = param.thisObject;
            this.args = param.args;
        }

        public Object getResult() {
            return param.getResult();
        }

        public void setResult(Object result) {
            param.setResult(result);
        }
    }

    public static void ensureInitialized() {
        // LSPosed initializes its bridge before module callbacks are invoked.
    }

    public static MethodHook.Unhook hook(Member member, MethodHook callback) {
        XposedBridge.hookMethod(member, new XC_MethodHook() {
            @Override
            protected void beforeHookedMethod(MethodHookParam param) throws Throwable {
                callback.beforeCall(new CallFrame(param));
            }

            @Override
            protected void afterHookedMethod(MethodHookParam param) throws Throwable {
                callback.afterCall(new CallFrame(param));
            }
        });
        return null;
    }
}
