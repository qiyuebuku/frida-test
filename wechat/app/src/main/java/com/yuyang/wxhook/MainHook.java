package com.yuyang.wxhook;

import android.app.Application;
import android.database.Cursor;
import android.net.LocalServerSocket;
import android.net.LocalSocket;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.io.BufferedWriter;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;

import android.util.Base64;

import top.canyie.pine.Pine;
import top.canyie.pine.PineConfig;
import top.canyie.pine.callback.MethodHook;

public class MainHook {

    private static final String TAG = "WXHook";
    private static final String SOCKET_NAME = "wxhook_rpc";
    private static final String EXPORT_DIR = "/data/user/0/com.tencent.mm/cache/wxhook";
    private static final long MAX_MEDIA_SIZE = 50 * 1024 * 1024; // 50MB

    // 微信媒体基础目录 — 在 DB 打开时动态确定
    private static volatile String microMsgDir = null;

    private static volatile boolean hooksInstalled = false;
    private static volatile boolean rpcRunning = false;
    private static volatile LocalServerSocket serverSocket;
    private static volatile Object dbInstance = null;
    private static volatile Method rawQueryMethod = null;
    private static final Map<String, String> contactMap = new HashMap<>();
    private static final SimpleDateFormat sdf = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.CHINA);

    public static void entry(ClassLoader systemClassLoader, String pineSoPath) {
        Log.i(TAG, "MainHook.entry() called");
        try {
            System.load(pineSoPath);
            Log.i(TAG, "Pine SO loaded");
            PineConfig.debug = false;
            PineConfig.debuggable = false;
            PineConfig.libLoader = new Pine.LibLoader() {
                @Override public void loadLib() { }
            };
            Pine.ensureInitialized();
            Log.i(TAG, "Pine initialized");
            hookApplicationOnCreate(systemClassLoader);
        } catch (Throwable t) {
            Log.e(TAG, "entry() error: " + t.getMessage(), t);
        }
    }

    private static void hookApplicationOnCreate(ClassLoader systemClassLoader) {
        try {
            Method onCreateMethod = Application.class.getDeclaredMethod("onCreate");
            Pine.hook(onCreateMethod, new MethodHook() {
                @Override
                public void afterCall(Pine.CallFrame callFrame) {
                    if (hooksInstalled) return;
                    hooksInstalled = true;
                    Application app = (Application) callFrame.thisObject;
                    ClassLoader appClassLoader = app.getClassLoader();
                    Log.i(TAG, "Application.onCreate() triggered");
                    try {
                        installWCDBHooks(appClassLoader);
                    } catch (Throwable t) {
                        Log.e(TAG, "Failed to install WCDB hooks: " + t.getMessage(), t);
                    }
                }
            });
            Log.i(TAG, "Application.onCreate hook installed");
        } catch (Throwable t) {
            Log.e(TAG, "hookApplicationOnCreate error: " + t.getMessage(), t);
        }
    }

    private static void installWCDBHooks(ClassLoader cl) {
        Log.i(TAG, "=== Installing WCDB hooks ===");
        hookWCDB1(cl);
        Log.i(TAG, "=== WCDB hooks installation complete ===");
    }

    private static void hookWCDB1(ClassLoader cl) {
        try {
            Class<?> sqliteDbClass = cl.loadClass("com.tencent.wcdb.database.SQLiteDatabase");
            Method[] methods = sqliteDbClass.getDeclaredMethods();
            for (Method m : methods) {
                if (!m.getName().equals("openDatabase")) continue;
                Class<?>[] params = m.getParameterTypes();
                int pwdIndex = -1, pathIndex = -1;
                for (int i = 0; i < params.length; i++) {
                    if (params[i] == byte[].class) pwdIndex = i;
                    if (params[i] == String.class && pathIndex < 0) pathIndex = i;
                }
                if (pwdIndex < 0) continue;
                if (params.length < 7) continue;

                final int fPwd = pwdIndex, fPath = pathIndex;
                Pine.hook(m, new MethodHook() {
                    @Override
                    public void beforeCall(Pine.CallFrame callFrame) {
                        try {
                            String path = fPath >= 0 ? (String) callFrame.args[fPath] : "unknown";
                            byte[] pwd = (byte[]) callFrame.args[fPwd];
                            if (pwd != null && pwd.length > 0) {
                                String keyStr = new String(pwd, StandardCharsets.UTF_8);
                                Log.i(TAG, "DB KEY: " + keyStr + " -> " + path);
                                saveKeyToFile(path, keyStr, bytesToHex(pwd));
                            }
                        } catch (Throwable t) {
                            Log.e(TAG, "beforeCall error: " + t.getMessage());
                        }
                    }

                    @Override
                    public void afterCall(Pine.CallFrame callFrame) {
                        try {
                            String path = fPath >= 0 ? (String) callFrame.args[fPath] : "";
                            if (path != null && path.contains("EnMicroMsg.db") && dbInstance == null) {
                                Object db = callFrame.getResult();
                                if (db != null) {
                                    Log.i(TAG, "EnMicroMsg.db opened, initializing RPC...");
                                    final Object dbRef = db;
                                    new Thread(() -> {
                                        try {
                                            Thread.sleep(3000);
                                            initializeDb(dbRef);
                                        } catch (Throwable t) {
                                            Log.e(TAG, "Init thread error: " + t.getMessage(), t);
                                        }
                                    }, "WXHook-Init").start();
                                }
                            }
                        } catch (Throwable t) {
                            Log.e(TAG, "afterCall error: " + t.getMessage());
                        }
                    }
                });
                Log.i(TAG, "Hooked openDatabase (7-param)");
                break;
            }
        } catch (Throwable t) {
            Log.e(TAG, "hookWCDB1 error: " + t.getMessage(), t);
        }
    }

    // ==================== DB Initialization ====================

    private static void initializeDb(Object db) {
        dbInstance = db;

        // Find rawQuery method
        for (Method m : db.getClass().getMethods()) {
            if (m.getName().equals("rawQuery")) {
                Class<?>[] params = m.getParameterTypes();
                if (params.length == 2 && params[0] == String.class) {
                    rawQueryMethod = m;
                }
            }
        }
        if (rawQueryMethod == null) {
            Log.e(TAG, "rawQuery method not found!");
            return;
        }

        // Detect MicroMsg directory from db path
        try {
            Method getPathMethod = db.getClass().getMethod("getPath");
            String dbPath = (String) getPathMethod.invoke(db);
            if (dbPath != null && dbPath.contains("MicroMsg/")) {
                // e.g. /data/user/0/com.tencent.mm/MicroMsg/0874d2bb.../EnMicroMsg.db
                int idx = dbPath.indexOf("MicroMsg/");
                String afterMicroMsg = dbPath.substring(idx + "MicroMsg/".length());
                String hash = afterMicroMsg.split("/")[0];
                microMsgDir = dbPath.substring(0, idx + "MicroMsg/".length()) + hash;
                Log.i(TAG, "MicroMsg dir: " + microMsgDir);
            }
        } catch (Throwable t) {
            Log.w(TAG, "Failed to detect MicroMsg dir: " + t.getMessage());
        }

        // Load contacts
        loadContacts();
        Log.i(TAG, "DB initialized, contacts: " + contactMap.size());

        // Start RPC server
        startRpcServer();
    }

    private static void loadContacts() {
        try {
            contactMap.clear();
            Cursor c = (Cursor) rawQueryMethod.invoke(dbInstance,
                "SELECT username, alias, nickname, conRemark FROM rcontact", null);
            while (c.moveToNext()) {
                String username = c.getString(0);
                String alias = c.getString(1);
                String nickname = c.getString(2);
                String remark = c.getString(3);
                String displayName = remark != null && !remark.isEmpty() ? remark
                    : nickname != null && !nickname.isEmpty() ? nickname
                    : alias != null && !alias.isEmpty() ? alias
                    : username;
                contactMap.put(username, displayName);
            }
            c.close();
            Log.i(TAG, "Contacts loaded: " + contactMap.size());
        } catch (Throwable t) {
            Log.e(TAG, "loadContacts error: " + t.getMessage(), t);
        }
    }

    // ==================== RPC Server ====================

    private static void startRpcServer() {
        if (rpcRunning) return;
        rpcRunning = true;

        new Thread(() -> {
            try {
                try {
                    if (serverSocket != null) {
                        serverSocket.close();
                        serverSocket = null;
                    }
                } catch (Throwable ignored) {}

                serverSocket = new LocalServerSocket(SOCKET_NAME);
                Log.i(TAG, "RPC server started on " + SOCKET_NAME);

                while (rpcRunning) {
                    try {
                        LocalSocket client = serverSocket.accept();
                        handleClient(client);
                    } catch (Throwable e) {
                        if (rpcRunning) {
                            Log.e(TAG, "RPC accept error", e);
                        }
                    }
                }
            } catch (Throwable e) {
                Log.e(TAG, "Failed to start RPC server", e);
                rpcRunning = false;
            }
        }, "WXHook-RPC").start();
    }

    private static void handleClient(LocalSocket client) {
        new Thread(() -> {
            try {
                InputStream is = client.getInputStream();
                OutputStream os = client.getOutputStream();
                BufferedReader reader = new BufferedReader(new InputStreamReader(is, StandardCharsets.UTF_8));

                String line;
                while ((line = reader.readLine()) != null) {
                    String response = handleRpcCommand(line);
                    try {
                        JSONObject compactJson = new JSONObject(response);
                        response = compactJson.toString();
                    } catch (Throwable ignored) {
                        response = response.replace("\n", " ").replace("\r", "");
                    }
                    os.write((response + "\n").getBytes(StandardCharsets.UTF_8));
                    os.flush();
                }
            } catch (Throwable e) {
                Log.e(TAG, "RPC client error", e);
            } finally {
                try { client.close(); } catch (Throwable ignored) {}
            }
        }, "WXHook-RPC-Client").start();
    }

    // ==================== RPC Command Dispatch ====================

    private static String handleRpcCommand(String jsonStr) {
        try {
            JSONObject request = new JSONObject(jsonStr);
            String cmd = request.optString("cmd", "");

            switch (cmd) {
                case "ping":
                    return new JSONObject()
                        .put("success", true)
                        .put("data", "pong")
                        .put("dbReady", dbInstance != null)
                        .put("contacts", contactMap.size())
                        .toString();

                case "get_contacts":
                    return handleGetContacts(request);

                case "search_contacts":
                    return handleSearchContacts(request);

                case "get_conversations":
                    return handleGetConversations(request);

                case "get_history":
                    return handleGetHistory(request);

                case "get_new_messages":
                    return handleGetNewMessages(request);

                case "refresh_contacts":
                    loadContacts();
                    return new JSONObject()
                        .put("success", true)
                        .put("contacts", contactMap.size())
                        .toString();

                case "resolve_media":
                    return handleResolveMedia(request);

                case "get_media":
                    return handleGetMedia(request);

                default:
                    return new JSONObject()
                        .put("success", false)
                        .put("error", "Unknown command: " + cmd)
                        .toString();
            }
        } catch (Throwable e) {
            try {
                return new JSONObject()
                    .put("success", false)
                    .put("error", e.getMessage())
                    .toString();
            } catch (Throwable e2) {
                return "{\"success\":false,\"error\":\"JSON error\"}";
            }
        }
    }

    // ==================== RPC Handlers ====================

    private static String handleGetContacts(JSONObject request) throws Exception {
        String filter = request.optString("filter", "");
        boolean realOnly = request.optBoolean("real_only", true);

        JSONArray arr = new JSONArray();
        String sql = realOnly
            ? "SELECT username, alias, nickname, conRemark, type FROM rcontact WHERE type IN (1, 3, 4) ORDER BY nickname"
            : "SELECT username, alias, nickname, conRemark, type FROM rcontact ORDER BY nickname";

        Cursor c = (Cursor) rawQueryMethod.invoke(dbInstance, sql, null);
        while (c.moveToNext()) {
            String username = c.getString(0);
            String alias = c.getString(1);
            String nickname = c.getString(2);
            String remark = c.getString(3);
            int type = c.getInt(4);

            // Apply keyword filter
            if (!filter.isEmpty()) {
                boolean match = (username != null && username.contains(filter))
                    || (alias != null && alias.contains(filter))
                    || (nickname != null && nickname.contains(filter))
                    || (remark != null && remark.contains(filter));
                if (!match) continue;
            }

            JSONObject cj = new JSONObject();
            cj.put("username", username);
            if (alias != null && !alias.isEmpty()) cj.put("alias", alias);
            if (nickname != null && !nickname.isEmpty()) cj.put("nickname", nickname);
            if (remark != null && !remark.isEmpty()) cj.put("remark", remark);
            cj.put("type", type);
            arr.put(cj);
        }
        c.close();

        return new JSONObject()
            .put("success", true)
            .put("data", arr)
            .toString();
    }

    private static String handleSearchContacts(JSONObject request) throws Exception {
        String keyword = request.optString("keyword", "");
        if (keyword.isEmpty()) {
            return new JSONObject().put("success", false).put("error", "Missing keyword").toString();
        }
        // Reuse get_contacts with filter
        request.put("filter", keyword);
        request.put("real_only", true);
        return handleGetContacts(request);
    }

    private static String handleGetConversations(JSONObject request) throws Exception {
        int limit = request.optInt("limit", 50);

        Cursor c = (Cursor) rawQueryMethod.invoke(dbInstance,
            "SELECT talker, COUNT(*) as cnt, MIN(createTime) as first, MAX(createTime) as last " +
            "FROM message GROUP BY talker ORDER BY last DESC LIMIT " + limit, null);

        JSONArray arr = new JSONArray();
        while (c.moveToNext()) {
            String talker = c.getString(0);
            long cnt = c.getLong(1);
            long first = c.getLong(2);
            long last = c.getLong(3);

            JSONObject conv = new JSONObject();
            conv.put("talker", talker != null ? talker : "");
            String name = contactMap.get(talker);
            if (name != null) conv.put("name", name);
            conv.put("count", cnt);
            conv.put("firstMessage", formatTime(first));
            conv.put("lastMessage", formatTime(last));
            arr.put(conv);
        }
        c.close();

        return new JSONObject()
            .put("success", true)
            .put("data", arr)
            .toString();
    }

    private static String handleGetHistory(JSONObject request) throws Exception {
        String talker = request.optString("talker", "");
        int limit = request.optInt("limit", 50);
        long beforeId = request.optLong("before_id", Long.MAX_VALUE);

        // Resolve name to username if needed
        talker = resolveToUsername(talker);

        String sql;
        if (!talker.isEmpty()) {
            sql = "SELECT msgId, type, isSend, createTime, talker, content, imgPath FROM message " +
                "WHERE talker = '" + escapeSql(talker) + "' AND msgId < " + beforeId +
                " ORDER BY msgId DESC LIMIT " + limit;
        } else {
            sql = "SELECT msgId, type, isSend, createTime, talker, content, imgPath FROM message " +
                "WHERE msgId < " + beforeId + " ORDER BY msgId DESC LIMIT " + limit;
        }

        Cursor c = (Cursor) rawQueryMethod.invoke(dbInstance, sql, null);
        JSONArray arr = new JSONArray();
        while (c.moveToNext()) {
            arr.put(buildMessageJson(c));
        }
        c.close();

        return new JSONObject()
            .put("success", true)
            .put("data", arr)
            .toString();
    }

    private static String handleGetNewMessages(JSONObject request) throws Exception {
        long afterId = request.optLong("after_id", 0);
        String talker = request.optString("talker", "");
        int limit = request.optInt("limit", 200);

        // Resolve name to username if needed
        talker = resolveToUsername(talker);

        String sql;
        if (!talker.isEmpty()) {
            sql = "SELECT msgId, type, isSend, createTime, talker, content, imgPath FROM message " +
                "WHERE talker = '" + escapeSql(talker) + "' AND msgId > " + afterId +
                " ORDER BY msgId ASC LIMIT " + limit;
        } else {
            sql = "SELECT msgId, type, isSend, createTime, talker, content, imgPath FROM message " +
                "WHERE msgId > " + afterId + " ORDER BY msgId ASC LIMIT " + limit;
        }

        Cursor c = (Cursor) rawQueryMethod.invoke(dbInstance, sql, null);
        JSONArray arr = new JSONArray();
        long maxId = afterId;
        while (c.moveToNext()) {
            JSONObject msg = buildMessageJson(c);
            arr.put(msg);
            long msgId = c.getLong(0);
            if (msgId > maxId) maxId = msgId;
        }
        c.close();

        return new JSONObject()
            .put("success", true)
            .put("data", new JSONObject()
                .put("messages", arr)
                .put("last_id", maxId))
            .toString();
    }

    // ==================== Media Handlers ====================

    /**
     * 解析消息对应的媒体文件路径
     * 输入: {msgId}  输出: 文件列表(path, type, size, exists)
     */
    private static String handleResolveMedia(JSONObject request) throws Exception {
        long msgId = request.optLong("msgId", 0);
        if (msgId <= 0) {
            return new JSONObject().put("success", false).put("error", "Missing msgId").toString();
        }
        if (microMsgDir == null) {
            return new JSONObject().put("success", false).put("error", "MicroMsg dir not detected").toString();
        }

        // 查询消息基本信息
        Cursor mc = (Cursor) rawQueryMethod.invoke(dbInstance,
            "SELECT msgId, type, isSend, content, imgPath, msgSvrId FROM message WHERE msgId = " + msgId, null);
        if (!mc.moveToFirst()) {
            mc.close();
            return new JSONObject().put("success", false).put("error", "Message not found: " + msgId).toString();
        }
        int type = mc.getInt(1);
        String content = mc.getString(3);
        String imgPath = mc.getString(4);
        long msgSvrId = mc.getLong(5);
        mc.close();

        JSONArray files = new JSONArray();

        switch (type) {
            case 3: // 图片
                resolveImageMedia(msgId, msgSvrId, imgPath, files);
                break;
            case 34: // 语音
                resolveVoiceMedia(msgId, files);
                break;
            case 43: // 视频
                resolveVideoMedia(msgId, files);
                break;
            case 47: // 表情(自定义表情)
                resolveEmojiMedia(content, files);
                break;
        }

        return new JSONObject()
            .put("success", true)
            .put("msgId", msgId)
            .put("type", type)
            .put("typeName", getMessageTypeName(type))
            .put("files", files)
            .toString();
    }

    private static void resolveImageMedia(long msgId, long msgSvrId, String imgPath, JSONArray files) throws Exception {
        // 方案1: 查 ImgInfo2 表（通过 msgSvrId 关联）
        if (msgSvrId > 0) {
            try {
                Cursor ic = (Cursor) rawQueryMethod.invoke(dbInstance,
                    "SELECT bigImgPath, thumbImgPath, midImgPath FROM ImgInfo2 WHERE msgSvrId = " + msgSvrId, null);
                while (ic.moveToNext()) {
                    String bigImg = ic.getString(0);
                    String thumbImg = ic.getString(1);
                    String midImg = ic.getString(2);

                    if (bigImg != null && !bigImg.isEmpty()) {
                        String resolved = resolveImagePath(bigImg);
                        if (resolved != null) addFileInfo(files, resolved, "original");
                    }
                    if (midImg != null && !midImg.isEmpty()) {
                        String resolved = resolveImagePath(midImg);
                        if (resolved != null) addFileInfo(files, resolved, "medium");
                    }
                    if (thumbImg != null && !thumbImg.isEmpty()) {
                        String resolved = resolveImagePath(thumbImg);
                        if (resolved != null) addFileInfo(files, resolved, "thumbnail");
                    }
                }
                ic.close();
            } catch (Throwable t) {
                Log.w(TAG, "ImgInfo2 query failed: " + t.getMessage());
            }
        }

        // 方案2: 从 imgPath 字段解析（回退方案）
        if (files.length() == 0 && imgPath != null && !imgPath.isEmpty()) {
            String resolved = resolveImagePath(imgPath);
            if (resolved != null) {
                String filename = new File(resolved).getName();
                String label = filename.startsWith("th_") ? "thumbnail" : "original";
                addFileInfo(files, resolved, label);
            }
        }
    }

    /**
     * 解析微信图片路径：
     * - THUMBNAIL_DIRPATH://th_{hash} → image2/{hash[0:2]}/{hash[2:4]}/th_{hash}
     * - SERVERID://... → 服务器 ID，跳过（原图未下载到本地）
     * - 普通路径 → 直接使用
     */
    private static String resolveImagePath(String path) {
        if (path == null || path.isEmpty()) return null;
        if (path.startsWith("SERVERID://")) {
            // 服务器原图 ID，非本地文件
            return null;
        }
        // 从路径中提取文件名（去掉协议前缀）
        String filename = path;
        if (path.contains("://")) {
            filename = path.substring(path.indexOf("://") + 3);
        }
        // 提取 hash（去掉 th_ 前缀）
        String hash = filename.startsWith("th_") ? filename.substring(3) : filename;
        if (hash.length() >= 4) {
            // image2/{hash[0:2]}/{hash[2:4]}/{filename}
            return microMsgDir + "/image2/" + hash.substring(0, 2) + "/" + hash.substring(2, 4) + "/" + filename;
        }
        // 兜底
        return resolveMediaPath(filename);
    }

    private static void resolveVoiceMedia(long msgId, JSONArray files) throws Exception {
        // 查 voiceinfo 表
        try {
            Cursor vc = (Cursor) rawQueryMethod.invoke(dbInstance,
                "SELECT clientMsgId FROM voiceinfo WHERE msgId = " + msgId, null);
            if (vc.moveToFirst()) {
                String clientMsgId = vc.getString(0);
                if (clientMsgId != null && !clientMsgId.isEmpty()) {
                    String md5 = md5Hex(clientMsgId);
                    String voicePath = microMsgDir + "/voice2/" + md5.substring(0, 2) + "/"
                        + md5.substring(2, 4) + "/msg_" + clientMsgId + ".amr";
                    addFileInfo(files, voicePath, "voice");
                }
            }
            vc.close();
        } catch (Throwable t) {
            Log.w(TAG, "voiceinfo query failed: " + t.getMessage());
        }
    }

    private static void resolveVideoMedia(long msgId, JSONArray files) throws Exception {
        // 查 videoinfo2 表
        try {
            Cursor vc = (Cursor) rawQueryMethod.invoke(dbInstance,
                "SELECT filename FROM videoinfo2 WHERE msgId = " + msgId, null);
            if (vc.moveToFirst()) {
                String filename = vc.getString(0);
                if (filename != null && !filename.isEmpty()) {
                    // 视频文件
                    String videoPath = microMsgDir + "/video/" + filename + ".mp4";
                    addFileInfo(files, videoPath, "video");

                    // 视频封面
                    String thumbPath = microMsgDir + "/video/" + filename + ".jpg";
                    addFileInfo(files, thumbPath, "video_thumb");
                }
            }
            vc.close();
        } catch (Throwable t) {
            Log.w(TAG, "videoinfo2 query failed: " + t.getMessage());
        }
    }

    private static void resolveEmojiMedia(String content, JSONArray files) throws Exception {
        // 表情包：从 content XML 中解析 cdnurl
        if (content == null || content.isEmpty()) return;
        // 解析 <emoji ... cdnurl="..." ...> 或 md5 属性
        String cdnurl = extractXmlAttr(content, "cdnurl");
        String emojiMd5 = extractXmlAttr(content, "md5");
        if (cdnurl != null && !cdnurl.isEmpty()) {
            try {
                JSONObject fj = new JSONObject();
                fj.put("url", cdnurl);
                fj.put("label", "emoji");
                if (emojiMd5 != null) fj.put("md5", emojiMd5);
                fj.put("type", "url");
                files.put(fj);
            } catch (Throwable ignored) {}
        }
    }

    private static String extractXmlAttr(String xml, String attr) {
        // 简单提取 attr="value" 或 attr='value'
        String pattern1 = attr + "=\"";
        int idx = xml.indexOf(pattern1);
        if (idx >= 0) {
            int start = idx + pattern1.length();
            int end = xml.indexOf("\"", start);
            if (end > start) return xml.substring(start, end);
        }
        String pattern2 = attr + "='";
        idx = xml.indexOf(pattern2);
        if (idx >= 0) {
            int start = idx + pattern2.length();
            int end = xml.indexOf("'", start);
            if (end > start) return xml.substring(start, end);
        }
        return null;
    }

    private static String resolveMediaPath(String path) {
        if (path == null) return "";
        // 已是绝对路径
        if (path.startsWith("/")) return path;
        // 相对路径，拼接 microMsgDir
        return microMsgDir + "/" + path;
    }

    private static void addFileInfo(JSONArray files, String path, String label) throws Exception {
        File f = new File(path);
        JSONObject fj = new JSONObject();
        fj.put("path", path);
        fj.put("label", label);
        fj.put("exists", f.exists());
        fj.put("type", "file");
        if (f.exists()) {
            fj.put("size", f.length());
        }
        files.put(fj);
    }

    /**
     * 读取文件内容并返回 base64
     * 输入: {path}  输出: {base64, size, md5}
     */
    private static String handleGetMedia(JSONObject request) throws Exception {
        String path = request.optString("path", "");
        if (path.isEmpty()) {
            return new JSONObject().put("success", false).put("error", "Missing path").toString();
        }

        File file = new File(path);
        if (!file.exists()) {
            return new JSONObject().put("success", false).put("error", "File not found: " + path).toString();
        }
        if (!file.isFile()) {
            return new JSONObject().put("success", false).put("error", "Not a file: " + path).toString();
        }
        if (file.length() > MAX_MEDIA_SIZE) {
            return new JSONObject().put("success", false)
                .put("error", "File too large: " + file.length() + " bytes (max " + MAX_MEDIA_SIZE + ")")
                .toString();
        }

        // 读取文件
        byte[] data = readFileBytes(file);

        // 计算 MD5
        String md5 = md5Hex(data);

        // Base64 编码 (NO_WRAP 确保没有换行)
        String base64 = Base64.encodeToString(data, Base64.NO_WRAP);

        return new JSONObject()
            .put("success", true)
            .put("path", path)
            .put("size", data.length)
            .put("md5", md5)
            .put("base64", base64)
            .toString();
    }

    private static byte[] readFileBytes(File file) throws Exception {
        FileInputStream fis = new FileInputStream(file);
        ByteArrayOutputStream bos = new ByteArrayOutputStream((int) file.length());
        byte[] buf = new byte[8192];
        int n;
        while ((n = fis.read(buf)) != -1) {
            bos.write(buf, 0, n);
        }
        fis.close();
        return bos.toByteArray();
    }

    private static String md5Hex(String input) {
        try {
            return md5Hex(input.getBytes(StandardCharsets.UTF_8));
        } catch (Throwable t) {
            return "";
        }
    }

    private static String md5Hex(byte[] data) {
        try {
            MessageDigest md = MessageDigest.getInstance("MD5");
            byte[] digest = md.digest(data);
            StringBuilder sb = new StringBuilder();
            for (byte b : digest) {
                sb.append(String.format("%02x", b & 0xFF));
            }
            return sb.toString();
        } catch (Throwable t) {
            return "";
        }
    }

    // ==================== Helpers ====================

    /**
     * Resolve a display name or nickname to username (wxid).
     * If already a username (starts with wxid_ or doesn't match any contact name), return as-is.
     */
    private static String resolveToUsername(String input) {
        if (input == null || input.isEmpty()) return "";
        // Already a username
        if (input.startsWith("wxid_") || input.startsWith("gh_") || input.equals("weixin")
                || input.equals("filehelper")) {
            return input;
        }
        // Try to find by nickname/remark
        for (Map.Entry<String, String> entry : contactMap.entrySet()) {
            if (entry.getValue().equals(input)) {
                return entry.getKey();
            }
        }
        // Could be a group chat id or other format, return as-is
        return input;
    }

    private static String escapeSql(String s) {
        return s.replace("'", "''");
    }

    private static JSONObject buildMessageJson(Cursor c) throws Exception {
        long msgId = c.getLong(0);
        int type = c.getInt(1);
        int isSend = c.getInt(2);
        long createTime = c.getLong(3);
        String talker = c.getString(4);
        String content = c.getString(5);
        String imgPath = c.getString(6);

        JSONObject msg = new JSONObject();
        msg.put("msgId", msgId);
        msg.put("type", type);
        msg.put("isSend", isSend);
        msg.put("createTime", createTime);
        msg.put("time", formatTime(createTime));
        msg.put("talker", talker != null ? talker : "");

        String displayName = contactMap.get(talker);
        if (displayName != null) msg.put("talkerName", displayName);

        if (content != null && !content.isEmpty()) msg.put("content", content);
        if (imgPath != null && !imgPath.isEmpty()) msg.put("imgPath", imgPath);
        msg.put("typeName", getMessageTypeName(type));

        return msg;
    }

    private static String formatTime(long ts) {
        return sdf.format(new Date(ts > 9999999999L ? ts : ts * 1000));
    }

    private static String getMessageTypeName(int type) {
        switch (type) {
            case 1: return "text";
            case 3: return "image";
            case 34: return "voice";
            case 42: return "contact_card";
            case 43: return "video";
            case 47: return "emoji";
            case 48: return "location";
            case 49: return "app_message";
            case 50: return "voip";
            case 10000: return "system";
            case 10002: return "revoke";
            case 1048625: return "photo";
            default: return "type_" + type;
        }
    }

    // ==================== File Utils ====================

    private static void saveKeyToFile(String dbPath, String keyStr, String keyHex) {
        try {
            String content = "DB: " + dbPath + "\nKey (string): " + keyStr + "\nKey (hex): " + keyHex + "\n\n";
            File dir = new File(EXPORT_DIR);
            dir.mkdirs();
            java.io.FileWriter fw = new java.io.FileWriter(new File(dir, "db_keys.txt"), true);
            fw.write(content);
            fw.close();
        } catch (Throwable t) {
            Log.e(TAG, "saveKeyToFile error: " + t.getMessage());
        }
    }

    private static String bytesToHex(byte[] bytes) {
        if (bytes == null) return "null";
        StringBuilder sb = new StringBuilder();
        for (byte b : bytes) {
            sb.append(String.format("%02x", b & 0xFF));
        }
        return sb.toString();
    }
}
