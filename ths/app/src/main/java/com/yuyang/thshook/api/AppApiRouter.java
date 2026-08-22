package com.yuyang.thshook.api;

/**
 * API 入口层。
 *
 * <p>这里只负责把 HTTP 请求映射成稳定接口 ID 和结构化 query/body。它不引用
 * MainHook，也不允许出现登录、行情、下单等业务实现。业务层只根据 Endpoint.id
 * 选择处理器，因此 URL 变更不会渗透到逆向调用逻辑。</p>
 */
public final class AppApiRouter {
    private AppApiRouter() { }

    public static AppApiRequest route(String requestLine, String body) {
        return AppApiRequest.parse(requestLine, body);
    }
}
