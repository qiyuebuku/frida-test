package com.yuyang.thshook.api;

import java.net.URLDecoder;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

/** 接口层解析后的请求，只负责 method/path/query/body，不承载业务逻辑。 */
public final class AppApiRequest {
    public final AppApiCatalog.Endpoint endpoint;
    public final String body;
    private final Map<String, String> query;

    private AppApiRequest(AppApiCatalog.Endpoint endpoint, String body,
            Map<String, String> query) {
        this.endpoint = endpoint;
        this.body = body == null ? "" : body;
        this.query = Collections.unmodifiableMap(query);
    }

    public static AppApiRequest parse(String requestLine, String body) {
        AppApiCatalog.Endpoint endpoint = AppApiCatalog.resolve(requestLine);
        if (endpoint == null) return null;
        Map<String, String> query = new LinkedHashMap<>();
        int firstSpace = requestLine.indexOf(' ');
        int secondSpace = requestLine.indexOf(' ', firstSpace + 1);
        String target = secondSpace < 0
                ? requestLine.substring(firstSpace + 1)
                : requestLine.substring(firstSpace + 1, secondSpace);
        int q = target.indexOf('?');
        if (q >= 0 && q + 1 < target.length()) {
            for (String pair : target.substring(q + 1).split("&")) {
                int equals = pair.indexOf('=');
                String key = equals < 0 ? pair : pair.substring(0, equals);
                String value = equals < 0 ? "" : pair.substring(equals + 1);
                query.put(decode(key), decode(value));
            }
        }
        return new AppApiRequest(endpoint, body, query);
    }

    private static String decode(String value) {
        try {
            return URLDecoder.decode(value, "UTF-8");
        } catch (java.io.UnsupportedEncodingException impossible) {
            throw new IllegalStateException(impossible);
        }
    }

    public String query(String name) {
        return query.get(name);
    }

    public Map<String, String> query() {
        return query;
    }
}
