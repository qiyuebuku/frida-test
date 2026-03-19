# 加密/解密模式库

## 模式 1: OkHttp 签名拦截器复用（起点读书）

**场景**: API 请求需要 QDSign、borgus、cecelia 等签名字段
**方案**: 复用 App 内已配置签名拦截器的 OkHttpClient

```java
// 从 RealCall 反射获取 OkHttpClient
savedOkHttpClient = reflectGetClient(realCall);
// 直接用它发请求，签名拦截器自动添加签名
Response response = savedOkHttpClient.newCall(request).execute();
```

## 模式 2: App 内部解密方法反射调用（起点读书）

**场景**: 解密逻辑完全封装在 App 内部，不使用标准 Cipher
**方案**: 反射调用 App 的解密方法链

```
safegetcontent API → 加密数据 → 保存为 .qd 文件
                                        ↓
反射调用 bll.v.L(bookId, chapterItem)  → 触发解密
反射调用 bll.v.K(bookId, chapterItem)  → 获取文件路径
反射调用 bll.v.R(bookId, chapterItem)  → 获取缓存路径
                                        ↓
afterCall Hook 自动捕获 ChapterContentItem.Content → 明文
```

## 模式 3: RSA 密钥交换 + AES 解密（盐言故事）

**场景**: 内容使用 AES 加密，密钥通过 RSA 交换
**流程**:

```
1. 客户端生成 16 字节随机 AES 密钥 K1
2. RSA 公钥加密 K1 → trans_key
3. POST /manuscript/code {section_id, trans_key}
4. 服务端返回 article_code（AES 加密的解密密钥）
5. AES/CFB8/NoPadding 解密：
   - Key: K1
   - IV: article_code 前 16 字节
   - 密文: article_code 第 16 字节之后
   - 明文: decryptKey（真正的内容密钥）
6. Native JNI 层用 decryptKey 解密章节内容
```

**Hook 点**: Cipher.doFinal() 捕获 K1 + BaseJniWarp.getText() 获取解密文本

## 模式 4: AES-256-CTR 加密（小红书）

**场景**: API 数据使用 AES/CTR/NoPadding 加密
**参数**:
- 密钥: 32 字节（256 位）
- IV: 16 字节
- 算法: AES/CTR/NoPadding

**Hook 点**: Cipher.init() 捕获密钥和 IV + Cipher.doFinal() 捕获明文

## 模式 5: WCDB 数据库密钥捕获（微信）

**场景**: 微信使用 WCDB 加密数据库
**方案**: Hook `SQLiteDatabase.openDatabase()` 在 beforeCall 中提取 byte[] 密钥参数

```java
// 密钥通常在 byte[] 类型的参数中
byte[] pwd = (byte[]) callFrame.args[pwdIndex];
String path = (String) callFrame.args[pathIndex];
saveKeyToFile(path, pwd);
```

## 模式 6: JWT Token 自动刷新（同花顺）

**场景**: 认证 Token 有有效期（如 28 天），需要自动续期
**方案**:

```python
# 1. Hook Cipher.doFinal() 捕获初始 Token
# 2. 解析 JWT payload 获取过期时间
# 3. 保存密码 MD5 用于自动登录
# 4. Token 过期前 3 天自动调用登录接口刷新
if token_expires_in < 3_days:
    response = login(password_md5=saved_md5)
    save_new_token(response["key5"])
```

## 通用解密发现方法论

```
步骤 1: 广泛 Hook Cipher
  → 记录所有 getInstance/init/doFinal 调用
  → 过滤 TLS 流量（GCM/ChaCha20/RSA）
  → 识别业务加密算法

步骤 2: 栈追踪定位
  → 在 Cipher.init 的 Hook 中打印调用栈
  → 找到业务调用方（通常在 App 的 util/crypto 包中）

步骤 3: 反编译分析
  → jadx 反编译目标类
  → 理解加密流程和密钥来源

步骤 4: 选择 Hook 点
  → 如果标准 Cipher: 直接 Hook doFinal
  → 如果自定义加密: 反射调用 App 的解密方法
  → 如果 Native 加密: Hook JNI 方法获取解密结果
```
