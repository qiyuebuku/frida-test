# 案例：微信 (com.tencent.mm)

## 基本信息
- **包名**: com.tencent.mm
- **难度**: ⭐⭐⭐
- **核心代码**: /home/yuyang/frida-test/wechat/app/src/main/java/com/yuyang/qdhook/MainHook.java

## 逆向目标
- 实时消息监听
- 联系人与会话管理
- 媒体文件提取（图片/语音/视频/表情包）
- 数据库解密密钥获取

## 成功方案
Zygisk + Pine（Hook WCDB 数据库）

**关键 Hook 点**:
1. Application.onCreate — 注入入口
2. SQLiteDatabase.openDatabase — 捕获数据库解密密钥
3. rawQuery 反射 — SQL 查询消息/联系人

## 数据库结构
| 表名 | 用途 |
|------|------|
| message | 消息（msgId, type, isSend, createTime, talker, content） |
| rcontact | 联系人（username, alias, nickname, conRemark） |
| ImgInfo2 | 图片元数据（msgSvrId, bigImgPath, thumbImgPath） |
| voiceinfo | 语音消息 |
| videoinfo2 | 视频消息 |

## 关键技术
### 数据库密钥捕获
```java
// Hook SQLiteDatabase.openDatabase()
// 通过参数类型动态查找 byte[] 密钥参数
Pine.hook(openDbMethod, new MethodHook() {
    public void beforeCall(CallFrame f) {
        byte[] pwd = (byte[]) f.args[pwdIndex];
        saveKeyToFile(path, pwd);
    }
});
```

### MicroMsg 目录动态定位
每个微信账号有不同的 hash 目录，必须从 `getPath()` 动态解析：
```java
String dbPath = db.getPath();
// /data/user/0/com.tencent.mm/MicroMsg/0874d2bb.../EnMicroMsg.db
// 解析出 hash 目录
```

### 媒体文件路径解析
- **图片**: `THUMBNAIL_DIRPATH://th_{hash}` → `image2/{hash[0:2]}/{hash[2:4]}/th_{hash}`
- **语音**: MD5(clientMsgId) → `voice2/{md5[0:2]}/{md5[2:4]}/msg_{clientMsgId}.amr`

## RPC 通道
- 协议: LocalSocket (`wxhook_rpc`)
- 命令: ping, get_contacts, get_history, get_new_messages, resolve_media, get_media

## Python 工具
- `wxmonitor.py` — RPC 客户端 + 消息监控 + 媒体导出
