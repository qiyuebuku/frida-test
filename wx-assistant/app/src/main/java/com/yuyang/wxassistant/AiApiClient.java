package com.yuyang.wxassistant;

import android.os.Handler;
import android.os.Looper;
import android.util.Base64;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class AiApiClient {

    public interface Callback {
        void onSuccess(String reply);
        void onError(String error);
    }

    private final AppPreferences prefs;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    public AiApiClient(AppPreferences prefs) {
        this.prefs = prefs;
    }

    /**
     * Send screenshots to a vision-capable AI model for chat reply suggestions.
     */
    public void requestReplyWithScreenshots(List<byte[]> screenshots, Callback callback) {
        executor.execute(() -> {
            try {
                String result = doScreenshotRequest(screenshots);
                mainHandler.post(() -> callback.onSuccess(result));
            } catch (Exception e) {
                String error = e.getMessage() != null ? e.getMessage() : e.toString();
                mainHandler.post(() -> callback.onError(error));
            }
        });
    }

    public void testConnection(Callback callback) {
        executor.execute(() -> {
            try {
                String baseUrl = prefs.getApiBaseUrl().replaceAll("/+$", "");
                String url = baseUrl + "/v1/models";

                HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
                conn.setRequestMethod("GET");
                conn.setRequestProperty("Authorization", "Bearer " + prefs.getApiKey());
                conn.setConnectTimeout(10000);
                conn.setReadTimeout(10000);

                int code = conn.getResponseCode();
                conn.disconnect();

                if (code == 200) {
                    mainHandler.post(() -> callback.onSuccess("连接成功"));
                } else {
                    mainHandler.post(() -> callback.onError("HTTP " + code));
                }
            } catch (Exception e) {
                String error = e.getMessage() != null ? e.getMessage() : e.toString();
                mainHandler.post(() -> callback.onError(error));
            }
        });
    }

    private String doScreenshotRequest(List<byte[]> screenshots) throws Exception {
        String baseUrl = prefs.getApiBaseUrl().replaceAll("/+$", "");
        String url = baseUrl + "/v1/chat/completions";

        // Build messages array
        JSONArray messagesArray = new JSONArray();

        // System message
        JSONObject systemMsg = new JSONObject();
        systemMsg.put("role", "system");
        systemMsg.put("content", prefs.getSystemPrompt());
        messagesArray.put(systemMsg);

        // User message with images
        JSONArray contentArray = new JSONArray();

        JSONObject textPart = new JSONObject();
        textPart.put("type", "text");
        if (screenshots.size() == 1) {
            textPart.put("text", "这是当前微信聊天界面的截图，请根据聊天上下文给出合适的回复建议。");
        } else {
            textPart.put("text", "以下是微信聊天界面的 " + screenshots.size()
                    + " 张截图（从不同滚动位置截取，按时间顺序排列），请综合所有截图理解完整对话上下文，给出合适的回复建议。");
        }
        contentArray.put(textPart);

        for (byte[] jpeg : screenshots) {
            String base64 = Base64.encodeToString(jpeg, Base64.NO_WRAP);
            JSONObject imagePart = new JSONObject();
            imagePart.put("type", "image_url");
            JSONObject imageUrl = new JSONObject();
            imageUrl.put("url", "data:image/jpeg;base64," + base64);
            imagePart.put("image_url", imageUrl);
            contentArray.put(imagePart);
        }

        JSONObject userMsg = new JSONObject();
        userMsg.put("role", "user");
        userMsg.put("content", contentArray);
        messagesArray.put(userMsg);

        JSONObject body = new JSONObject();
        body.put("model", prefs.getModel());
        body.put("messages", messagesArray);
        body.put("temperature", 0.7);
        body.put("max_tokens", 2048);

        // Send request
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setRequestMethod("POST");
        conn.setRequestProperty("Content-Type", "application/json");
        conn.setRequestProperty("Authorization", "Bearer " + prefs.getApiKey());
        conn.setConnectTimeout(15000);
        conn.setReadTimeout(120000); // Vision requests may take longer
        conn.setDoOutput(true);

        try (OutputStream os = conn.getOutputStream()) {
            os.write(body.toString().getBytes(StandardCharsets.UTF_8));
        }

        int code = conn.getResponseCode();
        BufferedReader reader;
        if (code >= 200 && code < 300) {
            reader = new BufferedReader(new InputStreamReader(conn.getInputStream(), StandardCharsets.UTF_8));
        } else {
            reader = new BufferedReader(new InputStreamReader(conn.getErrorStream(), StandardCharsets.UTF_8));
        }

        StringBuilder response = new StringBuilder();
        String line;
        while ((line = reader.readLine()) != null) {
            response.append(line);
        }
        reader.close();
        conn.disconnect();

        if (code < 200 || code >= 300) {
            throw new RuntimeException("API 错误 (HTTP " + code + "): " + response);
        }

        JSONObject responseJson = new JSONObject(response.toString());
        return responseJson.getJSONArray("choices")
                .getJSONObject(0)
                .getJSONObject("message")
                .getString("content")
                .trim();
    }
}
