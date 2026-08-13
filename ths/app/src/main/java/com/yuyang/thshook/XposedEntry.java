package com.yuyang.thshook;

import android.util.Log;

import de.robv.android.xposed.IXposedHookLoadPackage;
import de.robv.android.xposed.callbacks.XC_LoadPackage;

public final class XposedEntry implements IXposedHookLoadPackage {
    private static final String TAG = "THSHook";
    private static final String TARGET_PACKAGE = "com.hexin.plat.android";
    private static volatile boolean initialized;

    @Override
    public void handleLoadPackage(XC_LoadPackage.LoadPackageParam loadPackageParam) {
        if (!TARGET_PACKAGE.equals(loadPackageParam.packageName)
                || !TARGET_PACKAGE.equals(loadPackageParam.processName)
                || initialized) {
            return;
        }

        synchronized (XposedEntry.class) {
            if (initialized) {
                return;
            }
            initialized = true;
            Log.i(TAG, "LSPosed attached to " + loadPackageParam.processName);
            MainHook.entry(loadPackageParam.classLoader, null);
        }
    }
}
