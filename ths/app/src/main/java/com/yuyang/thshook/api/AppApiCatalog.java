package com.yuyang.thshook.api;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 同花顺 Hook 的接口层，也是 App 侧能力的唯一公开清单。
 *
 * <p>业务处理代码不应在这里出现。每条声明只回答六件事：接口 ID、HTTP 方法、
 * 路径、用途、输入和输出。处理逻辑仍由 MainHook 中对应 ID 的 handler 完成。
 * 新增接口必须先在此登记，再实现处理器；禁止在 socket 代码里直接硬编码新路径。</p>
 *
 * <p>request/response 使用面向维护者的紧凑结构描述，不包含真实账号、密码、
 * token 或业务数据示例。</p>
 */
public final class AppApiCatalog {
    public enum Maturity { VERIFIED, EXPERIMENTAL, DEBUG_ONLY }
    public enum Risk { READ_ONLY, SECRET, LOCAL_STATE_WRITE, REAL_MONEY_WRITE, DEBUG }

    public static final class Endpoint {
        public final String id;
        public final String method;
        public final String path;
        public final String summary;
        public final String request;
        public final String response;
        public final Maturity maturity;
        public final Risk risk;

        private Endpoint(String id, String method, String path, String summary,
                String request, String response, Maturity maturity, Risk risk) {
            this.id = id;
            this.method = method;
            this.path = path;
            this.summary = summary;
            this.request = request;
            this.response = response;
            this.maturity = maturity;
            this.risk = risk;
        }
    }

    private static final List<Endpoint> ENDPOINTS;
    private static final Map<String, Endpoint> BY_ID;

    static {
        List<Endpoint> e = new ArrayList<>();

        // 系统与 App 初始化
        add(e, "system.health", "GET", "/health", "Hook、行情和交易模块健康状态",
                "-", "HealthStatus", Maturity.VERIFIED, Risk.READ_ONLY);
        add(e, "admin.bootstrap", "POST", "/admin/bootstrap", "完成隐私页和首次启动初始化",
                "{}", "BootstrapResult", Maturity.VERIFIED, Risk.LOCAL_STATE_WRITE);
        add(e, "admin.viewClick", "POST", "/admin/view-click", "调用当前页面指定 View 的真实监听器",
                "{view_id:int}", "{success:boolean,...}", Maturity.DEBUG_ONLY, Risk.DEBUG);

        // Native 行情
        add(e, "market.runtime.ensure", "POST", "/native/runtime/ensure", "启动 App Native 行情运行时",
                "{}", "MarketRuntimeStatus", Maturity.VERIFIED, Risk.LOCAL_STATE_WRITE);
        add(e, "market.runtime.status", "GET", "/native/runtime/status", "查询 Native 行情运行时状态",
                "-", "MarketRuntimeStatus", Maturity.VERIFIED, Risk.READ_ONLY);
        add(e, "market.hurricane", "POST", "/native/hurricane", "Hurricane 指标查询",
                "HurricaneQuery", "IndicatorRows", Maturity.VERIFIED, Risk.READ_ONLY);
        add(e, "market.realtime", "POST", "/native/realtime", "直接调用 App 实时行情核心",
                "RealtimeQuery", "RealtimeResult", Maturity.VERIFIED, Risk.READ_ONLY);
        add(e, "market.unified", "POST", "/native/unified", "直接调用 App UnifiedRequest 核心",
                "UnifiedQuery", "UnifiedResult", Maturity.VERIFIED, Risk.READ_ONLY);
        add(e, "market.rankingDebug", "POST", "/native/ranking-debug", "排行榜协议诊断",
                "RankingQuery", "DebugResult", Maturity.DEBUG_ONLY, Risk.DEBUG);

        // 交易运行时、登录和账户
        add(e, "trade.runtime.status", "GET", "/stock/trade/runtime/status", "权威交易运行时状态",
                "-", "TradeRuntimeStatus", Maturity.VERIFIED, Risk.READ_ONLY);
        add(e, "trade.runtime.ensure", "POST", "/stock/trade/runtime/ensure", "重建账户、登录并执行只读探针",
                "{}", "TradeRuntimeEnsureResult", Maturity.VERIFIED, Risk.LOCAL_STATE_WRITE);
        add(e, "trade.login", "POST", "/stock/trade/login", "主动 token 或账号密码登录",
                "{method?:'pwd'|'auto',force?:boolean,password?:string(secret)}",
                "TradeLoginResult", Maturity.VERIFIED, Risk.SECRET);
        add(e, "trade.password.get", "GET", "/stock/trade/pwd", "查询交易密码是否已配置（不回显）",
                "-", "{configured:boolean}", Maturity.VERIFIED, Risk.SECRET);
        add(e, "trade.password.set", "POST", "/stock/trade/pwd", "设置或清除本机交易密码",
                "{password?:string(secret),clear?:boolean}", "{stored?:boolean,cleared?:boolean}",
                Maturity.VERIFIED, Risk.SECRET);
        add(e, "trade.account.configure", "POST", "/stock/trade/account/configure", "由资金账号和券商模板重建账户",
                "{account:string(secret),qsid:string,broker?:object}", "AccountConfigureResult",
                Maturity.VERIFIED, Risk.SECRET);
        add(e, "trade.account.export", "GET", "/stock/trade/account/export", "导出账户 seed（受控诊断）",
                "-", "AccountSeed(secret)", Maturity.DEBUG_ONLY, Risk.SECRET);
        add(e, "trade.account.seed", "POST", "/stock/trade/account/seed", "导入账户 seed",
                "AccountSeed(secret)", "AccountSeedResult", Maturity.DEBUG_ONLY, Risk.SECRET);

        // 交易 token、实例角色、通道和设备
        add(e, "trade.token.export", "GET", "/stock/trade/token/export", "导出交易 token（受控诊断）",
                "-", "TradeToken(secret)", Maturity.DEBUG_ONLY, Risk.SECRET);
        add(e, "trade.token.import", "POST", "/stock/trade/token/import", "导入 token 并可选登录",
                "{token:string(secret),time:string,login?:boolean}", "TokenImportResult",
                Maturity.EXPERIMENTAL, Risk.SECRET);
        add(e, "trade.tokenReport.status", "GET", "/stock/trade/token/report", "token 上报配置和状态（打码）",
                "-", "TokenReportStatus", Maturity.EXPERIMENTAL, Risk.READ_ONLY);
        add(e, "trade.tokenReport.configure", "POST", "/stock/trade/token/report", "配置 token 受控上报",
                "{url?:string,api_key?:string(secret),enabled?:boolean,force?:boolean}",
                "TokenReportStatus", Maturity.EXPERIMENTAL, Risk.SECRET);
        add(e, "trade.role.status", "GET", "/stock/trade/role", "查询本实例是否允许交易",
                "-", "{enabled:boolean}", Maturity.VERIFIED, Risk.READ_ONLY);
        add(e, "trade.role.configure", "POST", "/stock/trade/role", "启停本实例交易角色",
                "{enabled:boolean}", "{enabled:boolean}", Maturity.VERIFIED, Risk.LOCAL_STATE_WRITE);
        add(e, "trade.cbas.status", "GET", "/stock/trade/cbas", "CBAS 通道诊断",
                "-", "CbasStatus", Maturity.DEBUG_ONLY, Risk.DEBUG);
        add(e, "trade.cbas.configure", "POST", "/stock/trade/cbas", "设置 CBAS 地址",
                "{host:string,port:int}", "CbasStatus", Maturity.DEBUG_ONLY, Risk.LOCAL_STATE_WRITE);
        add(e, "trade.device.info", "GET", "/stock/trade/device-info", "设备指纹观测",
                "-", "DeviceInfo(secret)", Maturity.DEBUG_ONLY, Risk.SECRET);
        add(e, "trade.device.spoof", "POST", "/stock/trade/device-spoof", "配置受控设备指纹",
                "DeviceSpoofConfig(secret)", "DeviceInfo", Maturity.DEBUG_ONLY, Risk.SECRET);

        // 权威只读交易查询
        add(e, "trade.query", "GET", "/stock/trade/query", "资金、持仓、委托和成交查询",
                "query{name:funds|positions|today_order|today_deal|hist_order|hist_deal}",
                "TradeTableResult", Maturity.VERIFIED, Risk.READ_ONLY);
        add(e, "trade.marketRoute", "GET", "/stock/trade/market-route", "查询股票的券商市场路由",
                "query{code:string}", "MarketRouteResult", Maturity.VERIFIED, Risk.READ_ONLY);
        add(e, "trade.pushEvents", "GET", "/stock/trade/push-events", "委托和成交推送事件",
                "-", "PushEvent[]", Maturity.VERIFIED, Risk.READ_ONLY);
        add(e, "trade.transfer.banks", "GET", "/stock/trade/transfer/banks", "存管银行列表",
                "-", "Bank[]", Maturity.EXPERIMENTAL, Risk.READ_ONLY);

        // 真实资金写操作
        add(e, "trade.order", "POST", "/stock/trade/order", "股票买入或卖出",
                "{action:'buy'|'sell',code:string,price:string,qty:string,confirm:true}",
                "TradeOrderResult", Maturity.VERIFIED, Risk.REAL_MONEY_WRITE);
        add(e, "trade.cancel", "POST", "/stock/trade/cancel", "撤销股票委托",
                "{entrust_no:string,stock_code:string,stock_name:string,market_code:string,"
                        + "shareholder_account:string,withdrawable_qty?:string,confirm:true}",
                "TradeCancelResult",
                Maturity.VERIFIED, Risk.REAL_MONEY_WRITE);

        // WebView 桥仍承载少量尚未迁入 Native Core 的行情能力。
        add(e, "web.jsBridge", "POST", "/jsbridge", "调用 App WebView JSBridge",
                "JsBridgeRequest", "JsBridgeResult", Maturity.DEBUG_ONLY, Risk.DEBUG);

        // App 本地 SQLite 仅供逆向诊断；券商权威数据统一走 trade.query。
        add(e, "local.status", "GET", "/stock/status", "本地交易数据库状态",
                "-", "LocalDbStatus", Maturity.DEBUG_ONLY, Risk.DEBUG);
        add(e, "local.schema", "GET", "/stock/schema", "读取本地表结构",
                "query{table:string}", "TableSchema", Maturity.DEBUG_ONLY, Risk.DEBUG);
        add(e, "local.query", "GET", "/stock/query", "执行本地调试 SQL",
                "query{sql:string}", "LocalRows", Maturity.DEBUG_ONLY, Risk.DEBUG);
        add(e, "local.databases", "GET", "/stock/databases", "列出 App 数据库文件",
                "-", "Database[]", Maturity.DEBUG_ONLY, Risk.DEBUG);
        add(e, "local.openDb", "GET", "/stock/opendb", "打开数据库并列出表",
                "query{path:string}", "DatabaseSchema", Maturity.DEBUG_ONLY, Risk.DEBUG);

        // 逆向和协议诊断
        add(e, "debug.wire.boot", "POST", "/native/wire-capture/boot", "启停启动期 Native 报文捕获",
                "{armed:boolean}", "CaptureStatus", Maturity.DEBUG_ONLY, Risk.DEBUG);
        add(e, "debug.wire.read", "GET", "/native/wire-capture", "读取 Native 报文捕获",
                "-", "WireCapture", Maturity.DEBUG_ONLY, Risk.DEBUG);
        add(e, "debug.table.reset", "POST", "/native/table-capture/reset", "清空表格结构捕获",
                "{}", "{success:boolean}", Maturity.DEBUG_ONLY, Risk.DEBUG);
        add(e, "debug.table.read", "GET", "/native/table-capture", "读取表格结构捕获",
                "-", "TableCapture", Maturity.DEBUG_ONLY, Risk.DEBUG);
        add(e, "debug.indicator.reset", "POST", "/native/indicator-capture/reset", "清空指标捕获",
                "{}", "{success:boolean}", Maturity.DEBUG_ONLY, Risk.DEBUG);
        add(e, "debug.indicator.read", "GET", "/native/indicator-capture", "读取指标捕获",
                "-", "IndicatorCapture", Maturity.DEBUG_ONLY, Risk.DEBUG);
        add(e, "debug.trade.logs", "GET", "/stock/trade/logs", "读取交易诊断日志",
                "-", "TradeLog[]", Maturity.DEBUG_ONLY, Risk.DEBUG);
        add(e, "debug.trade.writeCaptures", "GET", "/stock/trade/write-captures", "读取交易写协议捕获",
                "-", "WriteCapture(secret)", Maturity.DEBUG_ONLY, Risk.SECRET);
        add(e, "debug.trade.sdkSchema", "GET", "/stock/trade/sdk-schema", "交易 SDK 类型和方法签名",
                "-", "SdkSchema", Maturity.DEBUG_ONLY, Risk.DEBUG);
        add(e, "debug.domains", "GET", "/domains", "认证参数和网络 Client 诊断",
                "-", "DomainDiagnostics(secret)", Maturity.DEBUG_ONLY, Risk.SECRET);
        add(e, "debug.auth", "GET", "/auth", "读取已捕获认证状态（受控诊断）",
                "-", "AuthDiagnostics(secret)", Maturity.DEBUG_ONLY, Risk.SECRET);
        add(e, "debug.clients", "GET", "/clients", "列出 App 内网络 Client",
                "-", "ClientDiagnostics", Maturity.DEBUG_ONLY, Risk.DEBUG);

        ENDPOINTS = Collections.unmodifiableList(e);
        Map<String, Endpoint> byId = new LinkedHashMap<>();
        for (Endpoint endpoint : e) {
            if (byId.put(endpoint.id, endpoint) != null) {
                throw new IllegalStateException("duplicate API id: " + endpoint.id);
            }
        }
        BY_ID = Collections.unmodifiableMap(byId);
    }

    private AppApiCatalog() { }

    private static void add(List<Endpoint> target, String id, String method, String path,
            String summary, String request, String response, Maturity maturity, Risk risk) {
        target.add(new Endpoint(id, method, path, summary, request, response, maturity, risk));
    }

    public static List<Endpoint> endpoints() {
        return ENDPOINTS;
    }

    public static Endpoint byId(String id) {
        return BY_ID.get(id);
    }

    /** 从原始 HTTP 请求行解析唯一接口；query string 不参与路由匹配。 */
    public static Endpoint resolve(String requestLine) {
        if (requestLine == null) return null;
        int firstSpace = requestLine.indexOf(' ');
        if (firstSpace <= 0) return null;
        int secondSpace = requestLine.indexOf(' ', firstSpace + 1);
        String method = requestLine.substring(0, firstSpace);
        String target = secondSpace < 0
                ? requestLine.substring(firstSpace + 1)
                : requestLine.substring(firstSpace + 1, secondSpace);
        int query = target.indexOf('?');
        String path = query < 0 ? target : target.substring(0, query);
        for (Endpoint endpoint : ENDPOINTS) {
            if (endpoint.method.equals(method) && endpoint.path.equals(path)) return endpoint;
        }
        return null;
    }
}
