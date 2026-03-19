# 案例：盐言故事 (com.zhihu.vip.android)

## 基本信息
- **包名**: com.zhihu.vip.android
- **版本**: v1.93.0
- **加固**: 梆梆加固（libbangcle_crypto_tool.so）
- **难度**: ⭐⭐（简单）
- **详细文档**: /home/yuyang/frida-test/yanyan/docs/YANYAN_REVERSE.md
- **核心代码**: /home/yuyang/frida-test/yanyan/app/src/main/java/com/yuyang/yyhook/MainHook.java（573 行）

## 逆向目标
- 捕获 API 流量（明文 JSON）
- RSA 密钥捕获 + AES 内容解密
- 获取章节解密后正文

## 关键发现
- **API 响应为明文 JSON**（不像起点读书加密），直接拦截即可
- **梆梆加固极弱**：Hook App 类完全无反应，无延迟检测
- **技术栈**: React Native (Hermes JS 引擎)
- **内容加密**: AES/CFB8/NoPadding，密钥通过 RSA 交换

## 成功方案
Zygisk + Pine（标准配置，无需特殊调优）

**三个 Hook 点**:
1. OkHttp 动态代理拦截器 — 网络流量
2. Cipher.doFinal() — RSA 密钥 K1
3. BaseJniWarp.getText() — 解密后章节正文（通过 Hook getChapterItemHeightArray 的 afterCall 触发）

## 加密流程
```
K1(16字节随机) → RSA加密 → trans_key → POST /manuscript/code
                                          ↓
                                   article_code(Base64)
                                          ↓
                        AES/CFB8/NoPadding(key=K1, iv=前16字节)
                                          ↓
                                     decryptKey
                                          ↓
                           Native JNI EpubWrap 解密章节
                                          ↓
                              getText() 返回明文
```

## 360加固 vs 梆梆加固对比（重要经验）
| 检测维度 | 360加固 | 梆梆加固 |
|---------|--------|---------|
| Hook 框架类 | ✅ 不检测 | ✅ 不检测 |
| Hook App 类 | ❌ 延迟崩溃 | ✅ **完全不检测** |
| ART 结构修改 | ❌ 检测 | ✅ 不检测 |
| 开发难度 | 高 | 低 |
