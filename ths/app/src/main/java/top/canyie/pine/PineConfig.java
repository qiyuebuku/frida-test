package top.canyie.pine;

public final class PineConfig {
    private PineConfig() {
    }

    public static Pine.LibLoader libLoader;
    public static boolean debug;
    public static boolean debuggable;
    public static boolean antiChecks;
    public static boolean disableHiddenApiPolicy;
    public static boolean disableHiddenApiPolicyForPlatformDomain;
}
