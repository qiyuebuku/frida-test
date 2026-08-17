# 踩坑记录与避坑指南

## 🔴 致命级

### 0a. 增量 `adb install -r` 不杀进程——新代码根本没生效（2026-08-17 实测连坑两轮）
- **现象**: 改代码→部署"Success"（输出含 "Performing Incremental Install"，几十 ms 完成）→行为完全不变，日志还是旧逻辑
- **原因**: 增量安装只替换 APK 文件，运行中的进程继续用旧 dex，直到进程重启
- **方案**: **每次 install 后必 `am force-stop <pkg>` + 重新拉起**；用响应里的新版本指纹字段确认新代码在跑。详见 `debugging-methodology.md` §1

### 0b. logcat 切片（head/tail/grep 窗口）会制造"事件不存在"的假象（2026-08-17 多次误导）
- **现象**: 明明发生了的事件（请求/回调）在过滤结果里看不到，据此推翻正确假设
- **原因**: 环形缓冲被高频日志刷爆+grep 管道只取了头尾，事件在被裁掉的中段；或 `logcat -c` 清掉了安装期日志
- **方案**: 关键结论必须基于 `logcat -d > 本地文件` 全量分析；长观察用 `nohup adb logcat -s TAG > file &`。详见 `debugging-methodology.md` §5

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

### 35. Pine.hook 强制执行 clinit，毒化依赖 App 状态的类（2026-08-16 同花顺实测两次崩溃）
- **现象**: 启动期 Pine.hook 某类后 App 弹"运行异常"，logcat 显示 `ExceptionInInitializerError`，之后 App 自己使用该类时 `NoClassDefFoundError`
- **原因**: Pine.hook 内部 `Method.invoke` 会**强制执行目标类 `<clinit>`**。若 clinit 依赖未就绪的 App 状态（如需要 context、需要先调 init 的单例），抛异常后 ART 将类标记为 erroneous，**永久失效**——不是本次崩溃，而是后续所有使用都崩
- **方案**: ① 凡是 clinit 依赖 App 状态的类，绝不在其状态就绪前 hook；② 找该类体系内的"就绪标志"静态字段（如同花顺 `r9h.d` volatile boolean），延迟重试线程里先读标志再挂钩（读平凡静态字段只触发安全 clinit）；③ 延迟重试用 `Thread.sleep(15s/45s/90s)` 阶梯，成功后置标志不再重试
- **适用范围**: 所有 Pine/ART hook；单例初始化型 SDK（交易、推送、支付）

### 36. 动态代理观察者的 hashCode 等 primitive 方法返回 null（2026-08-16 同花顺实测）
- **现象**: `Proxy.newProxyInstance` 实现回调接口后，把代理对象传给 App 的注册方法抛 `NullPointerException: Expected to unbox a 'int' primitive type but was returned null`
- **原因**: InvocationHandler 里除目标方法外一律 `return null`。App 的观察者注册表（Vector/Hashtable/HashSet）会调用 `hashCode()`/`equals()`——它们返回 primitive int/boolean，null 无法 unbox
- **方案**: handler 显式处理 Object 方法：`hashCode → System.identityHashCode(proxy)`、`toString → 描述串`、`equals → proxy == args[0]`；其余未知方法返回 null 前先确认返回类型不是 primitive
- **适用范围**: 所有用动态代理实现 App 内部回调接口的场景（observer/listener/callback 注册）

### 37. postAppSpecialize 时机主线程 Looper 未创建
- **现象**: Zygisk 模块在 `postAppSpecialize` 里 `new Handler(Looper.getMainLooper())` 直接 NPE，后续初始化全部中断（连进程内 HTTP 服务器都起不来）
- **原因**: postAppSpecialize 执行时 App 的主线程 Looper 尚未 prepare，`Looper.getMainLooper()` 返回 null
- **方案**: 需要延迟执行的任务用后台线程 `Thread.sleep` 后直接执行（代码库内已有同款模式的 watcher 可复用）；不要依赖 Handler/Looper
- **适用范围**: 所有 Zygisk 模块的 postAppSpecialize 阶段

### 38. 把"某方法只在目标业务时被调用"当作安全触发器
- **现象**: 计划用 A 方法的 afterCall 触发 B 模块挂钩，实测 A 在启动期（context 未就绪）就被调用，连带触发 #35 的 clinit 毒化
- **原因**: 从反编译源码推断的调用时机不可靠——启动恢复流程（账户恢复、缓存回填）也会调用看似业务专属的方法
- **方案**: 任何"用 X 触发 Y"的钩子链，先只挂 X 打日志实测完整触发时间线（含冷启动、热启动、杀进程重启），确认无启动期调用后再挂 Y；更稳妥的是用就绪标志字段（见 #35）代替业务方法做触发器
- **适用范围**: 所有钩子链设计

### 39. zygisksu（ZygiskNext/KernelSU）对模块 .so 有 ~356KB 预留地址空间限制（2026-08-16 实测）
- **现象**: 换一版 main.cpp 后模块完全不加载，logcat 无任何模块日志；降级回旧 .so 立即恢复。magisk 日志有 `reserved address space ... smaller than ... bytes needed`
- **原因**: ZygiskNext 在 zygote 启动时给模块 dlopen 预留的地址窗口有限。`std::vector<std::string>`、`std::sort` 等 STL 模板实例化会显著增大 .so，轻松超限
- **方案**: companion/native 侧用纯 C 结构（定长二维数组 + 插入排序）替代 STL 容器；`-fvisibility=hidden -Os`；提交前检查 .so 体积（实测 346432B 可载、355680B 不可载）
- **适用范围**: zygisksu/ZygiskNext 环境的所有原生模块；Magisk 官方 zygisk 无此限制

### 40. zygisk 原生 .so 只在 zygote 启动时加载，DEX 不是
- **现象**: 更新模块目录里的 .so 后 force-stop + 重启 App 不生效；更新 dex 立即生效
- **原因**: zygisk 模块 .so 在 zygote 进程启动时 dlopen 并常驻；App 重启不重新 dlopen。DEX 是每次 fork 后经 companion 传输注入，随 App 重启刷新
- **方案**: 改 .so 后必须 `su -c 'setprop ctl.restart zygote'`（会杀掉所有 App，开发机可接受）；只改 DEX 则 force-stop + monkey 启动即可。注意 zygote 重启后部分设备会弹 USB 模式选择框挡住屏幕，`input keyevent KEYCODE_BACK` 关闭
- **适用范围**: 所有 zygisk 模块迭代

### 41. 增量 dexing 使 MainHook 所在 classesN.dex 漂移，companion 硬编码文件名失效
- **现象**: 一次编译后 MainHook 从 classes3.dex 移到 classes4.dex，硬编码"发送 classes/classes2/classes3/libpine.so"的 companion 把不含 MainHook 的 dex 注入，Hook 全部失效
- **原因**: gradle 增量 dexing 会把类重新分布到任意数量的 dex 文件，文件数量和编号不保证稳定
- **方案**: ① companion 侧动态枚举模块目录（排序后的 *.dex 全部发送、libpine.so 最后），协议带文件数；② 部署脚本每次用 `unzip -p apk classesN.dex | strings | grep <标志字符串>` 确认 MainHook 实际所在 dex 再推送
- **适用范围**: 所有经 companion 传输 DEX 的 zygisk 模块

### 42. 源码树里的桩类会让 so 的 JNI_OnLoad 失败（真库/桩库双源陷阱）
- **现象**: `System.load(libpine.so)` 抛 `UnsatisfiedLinkError: JNI_ERR returned from JNI_OnLoad`，全部 Pine hook 失效；直接 dlopen 该 so 正常
- **原因**: 项目源码里存在一个为 LSPosed 桥接写的同名桩类（只有三个普通方法，无 native 方法）。JNI_OnLoad 里 `RegisterNatives` 要注册真类的 `native` 方法（如 `init0(IZZZZZ)V`），FindClass 解析到桩类 → 注册失败 → JNI_ERR。曾因"模块目录恰好残留旧版真 dex"而掩盖数日
- **方案**: ① 保留一份真库 dex（如 `zygisk/vendor/pine-classes.dex`）作为唯一部署源，写进部署流程；② 排查 UnsatisfiedLinkError 时先 `strings libX.so | grep -E "native方法签名"` 比对 Java 类是否有对应 native 声明；③ 部署清单固定"真库 dex 不动、只增量替换业务 dex"，防止全量推送覆盖
- **适用范围**: 所有携带 JNI 库 + 源码树存在同名桥接桩的项目（Pine/lsplant 等）

### 43. Windows adb 经 shell cat 拉二进制会 \n→\r\n 损坏
- **现象**: `adb shell "su -c 'cat file'"` 拉出的 DEX/SO 反编译失败或 md5 与设备上不同
- **原因**: Windows 版 adb 的 shell 输出做文本模式转换，0x0A 被展开成 0x0D0A
- **方案**: 设备上先 `su -c 'cp <src> /data/local/tmp/ && chmod 644'`，再 `adb pull`。或统一用 `adb exec-out`（二进制安全）
- **适用范围**: 所有从 Windows adb 拉取二进制的场景（DEX/SO/DB）

### 44. UI 自动化导航前不确认前台 App，坐标全部打偏
- **现象**: 依次 tap"交易Tab→持仓→查询"后日志毫无反应，OCR 发现屏幕在另一个 App（如快手）
- **原因**: monkey 启动可能被首页弹窗/快捷方式切换打断，脚本盲打坐标在错误窗口执行
- **方案**: ① 每轮导航前 `dumpsys window | grep mCurrentFocus` 确认目标包名在前台；② 导航后立即截图 OCR 验证预期元素出现再继续；③ OCR 坐标先确认截图是否全尺寸（screencap 直出的 1080x2378 不需要乘缩放系数；先打印图片尺寸再定坐标变换）；④ 底部 Tab 栏小字 OCR 识别不到时，裁剪该区域放大 2 倍再识别
- **适用范围**: 所有基于 input tap 坐标的 UI 自动化

### 45. 响应帧协议号 ≠ 请求协议号，按请求号注册的观察者收不到响应
- **现象**: 进程内重放查询 `H(2001,2031,observer,...)`，请求发出成功但 15 秒超时无回调；App 自己同样的请求有响应（响应帧 frameId=1810）
- **原因**: SDK 版本切换过渡期，新版用新协议号发请求，native 侧响应仍按旧协议号回帧；观察者注册表按协议号做分发键——按 2031 注册的观察者永远匹配不到 1810 的响应帧
- **方案**: ① 先 hook 响应观察者的 receive（记录 query(pageId,protocolId) 与 stuff.frameId），建立"请求协议号 → 响应协议号"映射；② 重放一律用**响应侧的协议号**注册+发起（往往就是旧协议号）；③ 旧协议的 params 若 App 已不再发送，可从反编译源码找模板静态构造
- **适用范围**: 所有带版本演进的请求/响应分发体系（协议号路由型 SDK）

### 46. 高频方法的捕获日志刷屏，挤掉低频关键事件
- **现象**: "manager captured"类 afterCall 日志每秒多条，100 条环形缓冲里全是它，TradeQuery/TradeResp 等真正要观察的事件全被冲掉，logcat 也难 grep
- **原因**: 高频回调（账户刷新、轮询）持续命中同一日志点，没有静默机制
- **方案**: ① 捕获成功后置 volatile 标志，后续调用静默或降为 Log.v；② 关键低频事件（请求/响应）单独 `Log.i` 直出 logcat，不只进内存缓冲；③ 观察时 `grep -v` 过滤噪音源
- **适用范围**: 所有高频 hook + 事件缓冲的探针设计

### 47. 把"没有符合条件的数据"当成账户无数据（2026-08-17 同花顺实测）
- **现象**: 历史委托页默认"近7日/近30日"查询返回"没有符合条件的历史委托数据。"，据此在文档里记录"账户全空无法采集 schema"
- **原因**: 查询页默认日期窗口很短；账户的真实数据（2026-03~05 共 38 条委托）在窗口之外。UI 的空结果提示分不清"无数据"和"窗口内无数据"
- **方案**: 下"账户无数据"结论前，先用自定义日期把窗口拉满（如当年 1 月 1 日或若干年前）查一次；UI 有日期筛选的历史类查询，一律先怀疑窗口
- **适用范围**: 所有带日期区间参数的查询类逆向（成交/委托/流水/账单）

### 48. 响应表格是列主序，且列键序 ≠ 表头序（2026-08-17 同花顺实测）
- **现象**: 把 dataTable 外层 key 当行号输出，得到的"第一行"是 38 个股票代码、第二行是 38 个名称；转置后按 tableHead 命名，代码列被标成"名称"
- **原因**: ① 表格结构是列主序——外层 key=列号，`String[]` 是该列全部行的值；② 列的物理键序与 tableHead 展示序不一致（实测 key0=代码、key1=名称，tableHead[0]=名称），键→列映射在各观察者实例私有的数组里（`dzh.Y1(stuff, iArr)` 的 iArr），stuff 本身不携带
- **方案**: ① 有真实多行数据后先验证方向（一行全是代码 = 列主序）；② 转置成行主序再输出；③ 语义列名必须用真实数据逐列人工校准，输出 `key_columns` 说明映射来源；④ 空表只有 tableHead 时不要臆测键序，等有行数据再校准
- **适用范围**: 所有表格型响应协议（Hashtable<Integer,String[]> 类载体）

### 49. 可捕获 ≠ 可重放：页面容器协议按 protocolId 注册收不到响应（2026-08-17 同花顺实测）
- **现象**: hook 捕获了 `H(9001,1264)` 的完整 params，用同一套"注册 proxy → 重放"模式调用，15s 超时；受控实验（清 logcat → 单次重放）中连原生发送入口 `r9h.o` 的日志都不出现
- **原因**: 交易首页批量查询是页面级**容器协议**：请求是多个子项打包，响应按子项拆帧后分发到页面各组件自己的观察者，不走按 protocolId 注册的通用观察者路径；重放请求在容器内部被吞
- **方案**: ① 端点上线前必须做受控重放实验（清日志→单次触发→查请求是否真到达原生入口、响应帧回到哪）；② 容器协议要走另一条路：**反编译页面里消费该数据的具体组件，找可独立调用的专用子协议客户端**——同花顺资金查询即由容器 9001 改走持仓页组件的 `H(2605,1807)` 专用协议（params 从源码 eb6 模板静态构造），一次打通；③ 单协议查询可重放 ≠ 同 SDK 的容器协议可重放
- **适用范围**: 所有"批量/聚合/页面容器"型查询与单协议查询共存的 SDK

### 50. 只在首次进入触发的查询 + 延迟挂钩 = 永远捕获不到（2026-08-17 同花顺实测）
- **现象**: 重启后进了两次交易页，目标批量查询 params 始终没捕获到；再次进入页面也不发请求
- **原因**: 该查询只在 App 启动后**首次进入页面**时发出，之后走缓存；而延迟挂钩梯（15/45/90s + 就绪标志门控）在首次触发时还没装好
- **方案**: 部署重启后**等满整个挂钩重试梯（≥90s）再首次进入目标页面**；错过就只能 force-stop 重来一次；判断依据：进页面后 logcat 无该协议的请求日志 ≠ hook 坏了，可能是"这个查询根本没第二次"
- **适用范围**: 延迟挂钩 + 一次性/首次触发型请求的组合

### 51. 滚轮选择器盲目 swipe 过冲，点击相邻项可逐格精确滚动（2026-08-17 同花顺实测）
- **现象**: 日期滚轮要跨多格（月 7→1、日 17→1），长距离 swipe 不可控、fling 过冲难修正
- **原因**: 滚轮步进约 180px/格，屏幕内可滑动距离有限，惯性滚动无准头
- **方案**: ① 多数滚轮**点击相邻项即滚一格居中**：反复点"中心行上方一格"的同一屏幕位置可连续递减（每次间隔 ≥0.7s 等动画）；② 操作前先截图确认滚轮初始值（App 常记住上次选择），按初始值算步数；③ 批量执行后仍要截图验收三列终值
- **适用范围**: 所有日期/选项滚轮的 UI 自动化

### 52. 裁剪放大 OCR 的坐标未换算回原坐标系，点击无声落空（2026-08-17 同花顺实测）
- **现象**: 按记录的坐标点"确定"按钮毫无反应，对话框不关；把 1344 当 x 坐标使用，而屏幕宽度只有 1080
- **原因**: 为识别小字把裁剪区域放大了 2 倍，OCR 输出的是放大图坐标系；坐标记进了笔记后忘了来源
- **方案**: ① 裁剪放大 OCR 的坐标必须换算：`真实 = 裁剪原点 + 放大坐标/倍数`；② tap 前做屏幕尺寸 sanity check（x<1080、y<2378），超界即坐标换算错误；③ 记录坐标时标注是否换算过
- **适用范围**: 所有裁剪/缩放后 OCR 驱动的 tap 自动化

### 53. 反编译源码里的协议 ≠ 券商实际部署协议，必须真机捕获对照（2026-08-17 同花顺撤单实测）
- **现象**: 按反编译 b8p 类源码重放撤单协议 22157，券商返回 `[250001][未知请求(L-0-jjyw_chedan)]`；同一 SDK 里并存四条撤单链（22157/1823/25102/25106），只有真机捕获的 25102 实际生效
- **原因**: 大型金融 App 的交易 SDK 是多版本协议共存的堆积层——源码里的类和协议号是"客户端支持过的"，券商侧只部署当前生效链路；静态分析无法判断哪条在生产路径上
- **方案**: ① 写操作协议一律先真机触发一次，在发送必经点（如同花顺 mrv.O / r9h.o）捕获实际协议号与 params，再决定实现哪条链；② 判别标准：重放返回"未知请求"= 券商不认识该协议（链路错误）；返回业务错误（如"订单表记录不存在"）= 协议对、参数错
- **适用范围**: 所有写操作/交易类协议逆向；任何"源码里看到协议就实现"的场景

### 54. 发送路径绕过已知 hook 点：调用栈时间窗诊断法（2026-08-17 同花顺实测）
- **现象**: App 撤单请求发出后，已 hook 的 rpv.G/H 查询构建器毫无日志，无法定位协议
- **原因**: 存在多条发送链（fluent 风格 `P().U().R().c0().a0()` 不经 G/H）；hook 点覆盖不全时请求"凭空消失"
- **方案**: ① 在 UI 事件层（PERFORM_CLICK/按钮文本）标记时间戳 `lastXxxClickMs`；② 在已知发送必经点（原生入口 r9h.o）检查 `now - lastXxxClickMs < 3000` 即打印业务调用栈（过滤 java./android. 前缀，取约 18 帧）；③ 栈顶的业务类（如 v3p.x）即真实发送链，反编译它得协议号；④ 事后在更上游的必经点（mrv.O）补常驻 hook 做协议过滤自动捕获
- **适用范围**: 所有"请求发出去了但 hook 没看到"的多链路发送架构

### 55. WSL curl 对特定端点假性挂起，服务端实际早已完成（2026-08-17 同花顺实测）
- **现象**: curl POST 撤单端点 25 秒超时（EXIT 28），但服务端日志显示 handler 84ms 完整执行并返回；同端点 Python raw socket 0.34s 收全响应
- **原因**: WSL curl 与 hook 服务器（自研 LightweightHTTP）的 keep-alive/传输编码交互问题——响应已发出，curl 不认为连接结束
- **方案**: ① 判别：服务端执行日志正常 + curl 超时 = 疑似假性挂起，换 Python raw socket（`socket.create_connection` + 手写 HTTP 头 + `Connection: close`）复测；② 写操作类端点测试一律用 Python socket 客户端，避免"curl 超时"误判为下单失败而重复提交
- **适用范围**: WSL 环境 + 自研简易 HTTP 服务器的组合；尤其写端点（超时误判会造成重复下单）

### 56. 请求签名只覆盖页面标签不含业务参数，改参数后签名可复用（2026-08-17 同花顺下单实测）
- **现象**: 下单 params 尾部 `\r\nsource=` Base64(DES) 签名，担心改 code/price/qty 后签名失效需重新捕获
- **原因**: 同花顺 `gyh.a(params, hyh, protoId)` 的签名输入只有页面标签与 cmd 名（如 ".cmd_mairu_confirm"），不含订单参数；DES 确定性（无随机 IV）使签名跨进程重启一致
- **方案**: ① 逆向时先确认签名的输入域：dump 被签名字符串与业务参数对比；② 若签名不含业务参数，静态模板保存一份签名即可长期复用（App 重启后无需重新捕获）；③ 若含业务参数则必须每次调用签名函数（反射调用或复现算法）
- **适用范围**: 所有带请求签名/防篡改校验的协议逆向

### 57. 请求发送层按登录态静默丢弃请求 + 账户对象零 UI 恢复三件套（2026-08-17 同花顺实测）
- **现象**: ① App 冷启动不进交易页面时，进程内重放查询"构建成功但无响应"——请求构建点（TradeQuery.H）有日志、原生发送入口（r9h.o）无日志，请求凭空消失；② 账户选择器 F(119) 返回 null，但运行时账户列表里明明有正确类的账户对象
- **原因**: 发送层（同花顺 mrv.k0）有登录门控：请求携带账户对象且 `izr.a.l(account)`（登录态）为 false 时——若正在静默重登（isWeituoLogining）直接**静默丢弃**（无异常无日志），否则触发 `x0s.F(false,false,14)` 静默 token 重登后丢弃当前请求。账户选择按"激活打分"`izr.a.j(pzr)>0`，不进交易页面时打分恒 0，列表有账户也选不出
- **方案**: 零 UI 恢复三件套（全部反射）：① `n0s.s().B()` 从仓库填充账户列表；② `izr.a.x(fzr)` 激活打分（交易页激活账户的标准调用）；③ `x0s.F(false,false,14)` 触发静默重登 + 轮询 `izr.a.l(fzr)` 直至 true 才放行请求。诊断口诀："构建点有日志、发送入口无日志 = 发送层门控丢弃，查登录态"
- **注意**: 静默重登（token 链路）可能耗时 2~3.5 分钟，预热需循环重试而非单次等待；激活（打分置位）≠ 登录（登录态置位），激活后立即发的请求仍会被吞
- **适用范围**: 所有带账户/会话状态的 SDK 逆向；"请求无声消失"类问题的排查路径
