# 踩坑记录与避坑指南

## 🔴 致命级

### 1. PineConfig.disableHiddenApiPolicy 必须设为 false
- **现象**: App 启动 ~40-50s 后闪退，日志显示 `exit_self`、`catch signal 11`
- **原因**: 360加固检测 ART runtime 内部结构是否被修改。Pine 默认设置 `disableHiddenApiPolicy=true` 会修改 ART
- **方案**: `PineConfig.disableHiddenApiPolicy = false`
- **适用范围**: 所有使用 360加固的 App

### 2. 不能直接 Hook App 加载类的高频方法（360加固）
- **现象**: App 运行约 40-50s 后被 SIGABRT 杀死，日志爆炸（每秒 22.8 万+ 条）
- **原因**: 360加固的延迟 ART 方法入口完整性检查
- **方案**: 用动态代理拦截器代替直接 Pine Hook OkHttp 方法
- **适用范围**: 360加固 App。梆梆加固不受影响

### 3. Hook Response.body() 等高频 getter 导致日志洪泛
- **现象**: 27 万+ 条日志，触发 Android `LOGS_OVER_PROC_QUOTA(300)` 限制，手机弹出系统警告
- **原因**: getter 方法调用频率极高
- **方案**: 永远不要 Hook 高频 getter 方法。用 OkHttp Interceptor 获取请求/响应数据
- **适用范围**: 所有 App

## 🟡 严重级

### 4. OkHttpClient 捕获了错误的实例
- **现象**: 所有 RPC 请求返回 HTTP 402 签名错误
- **原因**: App 中有多个 OkHttpClient（Glide 图片加载、广告 SDK、业务 API 等），捕获到了不带签名拦截器的客户端
- **方案**: 从特定业务 API 域名的 RealCall 中提取，使用 `apiClientCaptured` 一次性标志
- **适用范围**: 所有使用 OkHttp 的 App

### 5. 认证参数放错位置（Header vs Query）
- **现象**: 直接 HTTP 调用返回 403 "非法访问"
- **原因**: 同花顺的 key1-key5 认证参数应放在 URL Query 中，而非 Header
- **方案**: 通过 Hook OkHttp 捕获完整 URL（包含 Query String），精确复制参数位置
- **教训**: 永远先观察真实请求的参数位置，不要假设

### 6. ChapterItem 必须完整克隆（起点读书）
- **现象**: bll.v 解密方法返回 null 或错误类型
- **原因**: 创建了最小化的 fake ChapterItem（仅设置 chapterId），但解密逻辑依赖多个字段
- **方案**: 通过反射复制全部字段，仅修改 chapterId
- **适用范围**: 起点读书 App

### 7. bll.v 方法必须按 L→K→R 顺序调用
- **现象**: 仅调用 R 返回 null
- **原因**: L 是真正的解密触发点，K 获取路径，R 获取缓存。顺序不可打乱
- **方案**: 严格按 L→K→R 顺序反射调用
- **适用范围**: 起点读书 App

## 🟢 一般级

### 8. Cipher Hook 必须过滤 TLS 流量
- **现象**: 大量无关的 TLS 加密日志（GCM/ChaCha20），淹没业务加密日志
- **方案**: 过滤算法名：跳过 GCM, CHACHA20, POLY1305, OAEP, RSA；仅跟踪 DECRYPT_MODE
- **适用范围**: 所有 App

### 9. WSL2 下 adb forward 可能失效
- **现象**: 端口转发建立后无法连接
- **方案**: 使用 `adb_relay.py` 创建 TCP 中继，或让目标服务监听 `0.0.0.0` 并通过 WiFi IP 直连
- **适用范围**: WSL2 环境

### 10. 起点读书章节列表 API 双格式
- **现象**: 某些书籍无法获取章节列表
- **原因**: API 返回两种 JSON 结构：`Data.Vs[].Cs[]`（卷-章节）和 `Data.Chapters[]`（扁平列表）
- **方案**: 同时支持两种格式
- **适用范围**: 起点读书 App

### 11. 微信 MicroMsg 目录每个账号不同
- **现象**: 硬编码路径找不到数据库文件
- **原因**: 每个微信账号有不同的 hash 目录
- **方案**: 从 `SQLiteDatabase.getPath()` 动态解析目录
- **适用范围**: 微信 App

### 12. libpine.so 必须先写入再删除
- **现象**: 在文件系统中留下 Pine 库特征
- **方案**: 写入 `cache/.pine.so` → `System.load()` → 立即 `unlink()`
- **适用范围**: 所有 Zygisk 模块

### 13. Pine 的 libLoader 必须设为空实现
- **现象**: Pine 尝试重复加载 SO 导致崩溃
- **原因**: 我们已手动 System.load()，Pine 默认的 libLoader 会再次加载
- **方案**: `PineConfig.libLoader = () -> {};`
- **适用范围**: 所有 Zygisk + Pine 项目

### 14. installAllHooks 的 hooksInstalled 标志导致重试被跳过
- **现象**: OkHttp interceptor 注入失败后，延迟线程获取到正确的 ClassLoader 也不再尝试
- **原因**: `installAllHooks` 首次调用时 `hooksInstalled=true` 被立即设置，但 OkHttp 类还不存在（360加固延迟解壳）。后续调用被 `if (hooksInstalled) return` 跳过
- **方案**: 将关键 hook 的成功状态单独追踪（如 `okHttpHooked`），条件改为 `if (hooksInstalled && okHttpHooked) return`，非关键 hook 用 `firstRun` 标志只执行一次
- **适用范围**: 所有使用 360 加固的 App（ClassLoader 在 Zygote fork 阶段不包含 App 类）

### 15. WebView Cookie DB 路径不固定
- **现象**: 从固定路径 `app_webview/Default/Cookies` 读取 Cookie DB 失败
- **原因**: Android WebView 的存储路径会因为 App 更新、WebView 版本变化、多进程模式等原因改变。常见路径有 `app_webview/Default/Cookies` 和 `app_webview_<包名>/Default/Cookies`
- **方案**: 遍历所有可能路径，或用 `find` 命令动态查找。在 Hook 中实现路径探测逻辑
- **适用范围**: 所有需要读取 WebView Cookie 的逆向场景

### 16. 反爬 Token 一旦触发验证码就永久失效
- **现象**: 短时间内连续请求某个 API，第一次成功，后续全部返回验证码(captcha)。换 IP 仍然需要验证码
- **原因**: 服务端将验证码状态绑定到 token（而非 IP），一旦触发就标记该 token 需要完成验证码才能继续使用
- **方案**: (1) 严格控制请求频率（至少 60 秒间隔）；(2) 预防而非恢复——token 被锁后只能重新生成；(3) 清除 App 的 Cookie DB 并重启 App 可触发全新 token 生成
- **适用范围**: 问财(iwencai)等有 token 级验证码锁定机制的服务。**逆向调试阶段切忌连续请求测试**

### 17. document.cookie 拿不到 httpOnly cookie 导致请求失败
- **现象**: 从浏览器复制 `document.cookie` 发 HTTP 请求，返回 401/403，但浏览器本身请求正常
- **原因**: 关键认证 cookie（如 `xq_a_token`）标记为 `httpOnly=true`，浏览器 JS 无法访问，但浏览器发请求时会自动携带
- **方案**: 使用 Playwright `page.context.cookies()` 或 Selenium `driver.get_cookies()` 获取完整 cookie 列表（包含 httpOnly）
- **适用范围**: 所有使用 httpOnly cookie 做认证的网站（雪球、部分银行/证券网站）

### 18. TLS 指纹被检测导致同样的 cookie + headers 仍然失败
- **现象**: 完整复制了浏览器的 cookie 和 headers，curl/httpx/requests 发请求仍返回 403 或 WAF 挑战页
- **原因**: 服务端检测 TLS 握手指纹（cipher suite 顺序、扩展列表等），标准 HTTP 库的指纹与浏览器不同
- **方案**: 使用 `curl_cffi`（`Session(impersonate="chrome120")`）模拟 Chrome 的 TLS 指纹
- **适用范围**: 使用 WAF（阿里云/Cloudflare）的网站。**httpx/requests/标准curl 全部有独特指纹会被识别**

### 19. WAF JS 挑战页被误认为"网站不可访问"
- **现象**: HTTP 请求返回 200 但内容是 HTML 而非预期的 JSON，或返回一小段 JS 代码
- **原因**: 返回的是 WAF JS 挑战页面（含 `<script>` 和 `<meta name="aliyun_waf_*">`），需要浏览器执行 JS 后才能获得真正的响应
- **方案**: 识别特征（`aliyun_waf`/`cf-ray`/`challenge.js`），用真实浏览器通过挑战，导出 cookie 后复用
- **适用范围**: 阿里云 WAF（国内常见）、Cloudflare（国际常见）
