# 踩坑记录与避坑指南

## 🔴 致命级

### 0. 不同机器的默认 debug 签名不能覆盖同一个 Hook APK
- **现象**: `adb install -r` 返回 `INSTALL_FAILED_UPDATE_INCOMPATIBLE`，或者部署人员准备通过卸载解决签名冲突
- **原因**: 每台机器的 `~/.android/debug.keystore` 可能不同；“debug APK”不是一个统一签名身份
- **方案**: 部署前比较已安装 APK、生产私钥和待安装 APK 的证书 SHA-256；始终使用项目记录的生产私钥签名。签名不一致时立即停止，不能自动卸载
- **风险**: 卸载 Hook 可能破坏 LSPosed 模块启用状态和作用域，造成 APK 已安装但 Hook 没有注入
- **详细流程**: `knowledge/apk-signing-deployment.md`
- **适用范围**: 所有需要覆盖安装并由 LSPosed/Xposed 管理作用域的 Hook APK

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

### 20. 把 JSBridge 当成真正业务核心
- **现象**: 页面内调用已重放，但无法脱离 WebView，也无法在后台运行
- **原因**: JSBridge 通常只是参数适配和回调路由，真正请求由更下层的模型、协调器和通信类完成
- **方案**: 从 `@JavascriptInterface` 继续追踪到请求对象、回调和 Service，再直接反射调用底层对象
- **适用范围**: Hybrid App、行情 App、音视频 App 和设备控制 App

### 21. 只构造请求对象，忽略隐藏生命周期状态
- **现象**: 方法执行且没有异常，但服务端没有响应
- **原因**: 正常页面打开时还设置了 App 活跃阶段、启动 Service、认证长连接或注册观察者
- **方案**: 对比正常页面与 Service-only 冷启动日志，每次只补一个缺失状态并验证
- **适用范围**: 依赖长连接、Binder Service 或前后台状态的 App

### 22. 将订阅确认回包误当成业务数据
- **现象**: 请求立即“成功”，结果只有订阅成功状态，没有指标正文
- **原因**: 同一个回调混合全量数据、订阅 ACK 和增量推送
- **方案**: 依据响应类型、Key 和状态码确定完成条件；一次性请求只在目标全量数据到达后结束
- **适用范围**: 行情、即时通信、推送和流式协议

### 23. 在错误线程创建或清理原生 Client
- **现象**: 第一次请求成功，后续请求卡住、串包或回调丢失
- **原因**: App 内部 Client 依赖主 Looper，清理在工作线程执行导致注册状态未完全释放
- **方案**: 创建、启动、注销和清理都投递到主线程，并等待清理完成后再发下一次请求
- **适用范围**: Android Handler、LiveData、EventBus 或页面协议 Client

### 24. 默认并发调用固定协议页
- **现象**: 多请求之间结果互串、后发请求覆盖先发请求或随机超时
- **原因**: 原生协议使用固定帧号、页面号、Channel 或全局路由槽
- **方案**: 在证明支持并发前全局串行；完成后立即注销一次性 Client
- **适用范围**: 旧行情协议、页面号路由和单连接订阅协议

### 25. 认为前台 Service 和 WakeLock 可以阻止所有息屏冻结
- **现象**: 前台通知存在且 WakeLock 已持有，息屏数秒后进程仍停止收包
- **原因**: ColorOS 等厂商 freezer 可直接冻结 App UID/cgroup，与标准 Doze 不是同一机制
- **方案**: 同时检查厂商管理器、UID cgroup 和连接状态；建立受控白名单或使用独立协议 Client
- **适用范围**: ColorOS/Oplus Hans 及其他厂商后台冻结机制

### 26. 解冻或加入白名单后继续复用旧长连接
- **现象**: 进程不再被冻结，但原生请求仍持续超时
- **原因**: 白名单只阻止后续冻结，冻结期间已断开的 TCP/认证会话不会自动恢复
- **方案**: 解冻后检测连接状态并重建通信 Service/会话；将重连与白名单视为两项独立工作
- **适用范围**: 所有长连接、行情订阅和即时通信协议

### 27. 猜测厂商调试命令参数
- **现象**: `dumpsys` 命令无输出或看似成功但状态未改变
- **原因**: 厂商命令常要求 UID、包名、模式等固定参数，帮助文本不完整
- **方案**: 拉取对应 system service JAR，检查 DEX 中命令分发方法的参数数量和分支逻辑
- **适用范围**: OEM 电源管理、冻结、网络和权限调试接口

### 28. 进程内 HTTP 探针监听所有网卡
- **现象**: 调试接口可能被同一局域网或设备上的其他进程访问
- **原因**: `new ServerSocket(port)` 默认不限制为 loopback，且原型通常没有鉴权
- **方案**: 默认绑定 loopback，通过 ADB forward 或受控中继访问；正式使用时增加鉴权和操作白名单
- **适用范围**: 所有 App 进程内 RPC/HTTP Sidecar

### 29. 把外层连接数当成 Native 并发数
- **现象**: 增加 HTTP/TCP 连接后吞吐没有提高，反而出现串包、覆盖和随机超时
- **原因**: 所有连接最终进入同一 App 进程和全局 Manager，仍共享同一个 frame/page/route slot
- **方案**: 追踪 `obtainClient` 和回调路由的身份域；用同 frame、多 frame、串行和并发矩阵验证，不能根据连接数推断
- **适用范围**: App 内 HTTP Bridge、LocalSocket、Binder 和原生行情 SDK

### 30. 因一个请求族支持随机 frame 就推广到全部请求
- **现象**: 列表查询并发正常，显式证券查询换随机 frame 后只发送、不回调
- **原因**: 同一 SDK 的不同数据源可能分别使用动态路由和页面预注册固定路由
- **方案**: 按列表、显式证券、排行、订阅等请求族分别验证 frame；无业务回调的随机 frame 不能用于扩容
- **适用范围**: 混合 Hurricane/MobileHQ、全量/订阅或多数据源 SDK

### 31. 等待所有可选指标导致请求卡满总超时
- **现象**: 数据主体早已完整，某个收盘后无值的涨速/盘口字段使请求仍等待 30 或 40 秒
- **原因**: 完成条件把市场状态下合法为空的可选字段当成必需字段
- **方案**: 区分必需与可选字段；必需字段到齐且证券集合稳定一个收尾窗口后结束，不伪造缺失值
- **适用范围**: 多帧行情、盘口、实时速度和状态型指标

### 32. 用任意回调更新重置静默计时
- **现象**: 证券已经全部返回，但同一证券字段重复更新使 quiet timer 永远不能触发
- **原因**: 完成判定观察 callback 次数，而不是结果集合是否继续增长
- **方案**: 单独记录证券 Key 集合版本；只有新证券出现才重置集合稳定计时
- **适用范围**: 增量表格、流式快照和分字段回调

### 33. 为减少请求次数构造过大的原生批次
- **现象**: 小分类请求稳定，合并成数百对象的大请求后 Hook 连接关闭或内存/序列化长尾增加
- **原因**: Native SDK、Binder、回调表格或 Bridge 响应存在未公开的批量边界
- **方案**: 用成功率、字段覆盖率和墙钟共同选择批量；业务分类通常是更安全的自然分片
- **适用范围**: 显式证券列表、批量指标、Binder/RPC 大响应

### 34. 诊断脚本本身拖垮 WSL 或生产服务
- **现象**: WSL 出现 `E_UNEXPECTED`、积压多个包装进程，正式 Worker 测试后未恢复
- **原因**: 多个长时间工具会话、SSH、远端 Python 和大体积输出没有统一生命周期
- **方案**: 一次只保留一个长任务；远端命令使用总 timeout；暂停服务必须配套 `trap` 恢复；输出计数和覆盖率，不打印完整大表；结束后检查残留进程
- **适用范围**: WSL 到远端 Android/Hook 的容量测试和批量协议探针
