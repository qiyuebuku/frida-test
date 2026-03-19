# 案例：小红书 (com.xingin.xhs)

## 基本信息
- **包名**: com.xingin.xhs
- **版本**: 9.19.3
- **加固**: 腾讯乐固 VMP（libdexvmp.so）
- **难度**: ⭐⭐⭐
- **核心代码**: /home/yuyang/frida-test/xhs/app/src/main/java/com/yuyang/qdhook/MainHook.java（2600+ 行）

## 安全防护层
| 安全库 | 功能 |
|--------|------|
| libdexvmp.so | 腾讯乐固 VMP 加固 |
| libmsaoaidsec.so | OAID 安全 + ptrace 反调试 |
| libshadowhook.so | 字节跳动 ShadowHook |
| libbytehook.so | 字节跳动 ByteHook |
| libopen_hook.so | open 系统调用 Hook（检测 /data/local/tmp） |

## 逆向目标
- HTTP 流量拦截与分析
- AES-256-CTR 加密数据解密
- 推荐 feed、笔记详情、评论等内容获取

## 成功方案
Zygisk + Pine + OkHttp 动态代理拦截器

**Hook 点**:
1. Application.onCreate — 注入入口
2. OkHttpClient$Builder.build() — 注入拦截器
3. Cipher.init/doFinal — 捕获 AES-256-CTR 密钥和明文

**性能优化**:
- HTTP 请求去重：ConcurrentHashMap + 500ms 时间窗口
- 静态资源过滤：.jpg/.png/.gif/.webp
- Cipher 速率限制：每 60s 最多 30 条
- Cipher 上下文自动清理（超 60s 过期）

## 关键 API 域名
| 域名 | 用途 |
|------|------|
| edith.xiaohongshu.com | 主要业务 API |
| rec.xiaohongshu.com | 推荐系统 |
| as.xiaohongshu.com | 安全/设备验证 |
| t2.xiaohongshu.com | 数据采集/埋点 |
