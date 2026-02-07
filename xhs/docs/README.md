# 小红书（XHS）逆向分析报告

## App 信息

| 项目 | 值 |
|------|-----|
| 包名 | com.xingin.xhs |
| 版本 | 9.19.3 |
| 加固类型 | 腾讯乐固 VMP (libdexvmp.so) |
| Hook 状态 | 成功 |

## 安全特征

### Native 安全库
- `libdexvmp.so` - 腾讯 VMP 加固
- `libmsaoaidsec.so` - OAID 安全模块（包含 ptrace 反调试）
- `libsecurebase.so` - 安全基础库
- `libshadowhook.so` - 字节跳动 ShadowHook
- `libbytehook.so` - 字节跳动 ByteHook
- `libsentry-hook.so` - Sentry Hook
- `libopen_hook.so` - open 系统调用 Hook

### 绕过方案
使用 **Zygisk + Pine ART Hook** 方案成功绕过检测：
- 通过 Zygisk 在 App 启动前注入
- 使用 Pine 框架 Hook Java 层
- 避免触发 Native 层检测

## API 端点

### 核心域名
| 域名 | 用途 |
|------|------|
| edith.xiaohongshu.com | 主要业务 API |
| rec.xiaohongshu.com | 推荐系统 |
| as.xiaohongshu.com | 安全/设备验证 |
| t2.xiaohongshu.com | 数据采集 |
| sns-na-i*.xhscdn.com | 图片 CDN |
| sns-avatar-qc.xhscdn.com | 头像 CDN |

### 关键接口

#### 1. 首页推荐 Feed
```
GET https://rec.xiaohongshu.com/api/sns/v6/homefeed
参数:
  - oid: homefeed_recommend
  - cursor_score: 分页游标
  - geo: Base64 编码的位置信息
  - trace_id: 追踪 ID
  - note_index: 笔记索引
  - refresh_type: 刷新类型
  - known_signal: JSON 编码的设备/用户信号
```

#### 2. 笔记预加载
```
POST https://edith.xiaohongshu.com/api/sns/v1/note/detailfeed/preload
Content-Type: application/x-www-form-urlencoded
Body:
  - source: main
  - data: JSON 数组，包含笔记 ID、类型和 xsec_token
```

#### 3. 设备验证
```
POST https://as.xiaohongshu.com/api/v1/dvf/gch/android
POST https://as.xiaohongshu.com/api/v1/dvf/vat/android
Content-Type: application/json
Body: 包含设备指纹、签名等信息
```

#### 4. App 配置
```
POST https://as.xiaohongshu.com/api/v1/cfg/android
Body: {
  "appid": "ECFAAF01",
  "channel": "OppoPreload2025",
  "did": "设备 ID",
  "gid": "组 ID",
  "magic": 随机数,
  "model": "设备型号",
  "os": "android",
  "os_version": API 级别,
  "sdk_version": "2.9.55"
}
```

## 加密方案

### AES-256-CTR 加密
用于加密某些敏感数据（如用户行为统计）。

| 项目 | 值 |
|------|-----|
| 算法 | AES/CTR/NoPadding |
| 密钥长度 | 256 位 (32 字节) |
| IV 长度 | 128 位 (16 字节) |
| 密钥 | `ecccd9d088350ed66b1c38b2b2db8d80de6a1fa749b03099063d680c4976117d` |

### 解密示例
```java
// Java 代码示例
Cipher cipher = Cipher.getInstance("AES/CTR/NoPadding");
SecretKeySpec keySpec = new SecretKeySpec(hexToBytes(KEY), "AES");
IvParameterSpec ivSpec = new IvParameterSpec(iv);
cipher.init(Cipher.DECRYPT_MODE, keySpec, ivSpec);
byte[] plaintext = cipher.doFinal(ciphertext);
```

### 加密数据示例
```json
{
  "d_11h": 0.0,  // 某小时的指标
  "d_01h": 0.0,
  "l_00h": 0.0,
  // ... 24 小时的数据分布
}
```

## 请求签名

设备验证接口使用复杂的签名机制：
- `s`: 64 字节十六进制签名
- `k`: 32 字节十六进制密钥
- `d`: Base64 编码的动态数据
- `g`: 设备组 ID

具体签名算法需要进一步分析 Native 层代码。

## 调用栈

加密操作的 Java 调用栈：
```
x57.a.a (解密入口)
  └── l57.e.c
      └── l57.h$c.onSuccess
          └── u9b.j.invoke
              └── u9b.h.accept
                  └── c5c.q.onNext
                      └── (RxJava 异步链)
```

## 测试结果

| 测试项 | 结果 | 备注 |
|--------|------|------|
| Zygisk 注入 | 成功 | 重启后生效 |
| Pine 初始化 | 成功 | antiChecks=true |
| Application.onCreate Hook | 成功 | com.xingin.xhs.app.XhsApplication |
| OkHttp 拦截器注入 | 成功 | 捕获所有 HTTP 请求 |
| Cipher Hook | 成功 | 捕获 AES 解密操作 |
| App 稳定运行 | 成功 | 无崩溃，无检测 |

## 文件结构

```
/home/yuyang/frida-test/xhs/
├── app/                          # Hook 代码
│   └── src/main/java/.../MainHook.java
├── zygisk/
│   ├── jni/main.cpp             # Zygisk native 模块
│   ├── magisk/                   # Magisk 模块
│   └── extracted/                # DEX 和 SO 文件
└── docs/
    └── README.md                 # 本文档
```

## 后续工作

1. [ ] 分析 Native 层签名算法
2. [ ] 研究 xsec_token 生成机制
3. [ ] 深入分析笔记内容 API
4. [ ] 研究评论/点赞等互动接口
