# 反检测通用知识库

## 8 大检测机制与绕过方案

### 1. 进程/端口检测
- **检测方式**：App 扫描 frida-server 进程名或默认端口 27042
- **绕过**：重命名 frida-server 二进制 + 使用非默认端口（`-l 0.0.0.0:8888`）
- **Zygisk 方案**：天然绕过，无独立进程

### 2. ptrace 占坑检测
- **检测方式**：App 先 `ptrace(PTRACE_TRACEME)` 占坑，阻止调试器 attach
- **绕过（Frida）**：必须用 spawn 模式 + `Interceptor.replace`（不是 attach）替换 ptrace 返回值
- **Zygisk 方案**：天然绕过，不使用 ptrace

### 3. D-Bus 协议检测
- **检测方式**：App 向所有端口发送 D-Bus 认证消息 `\x00AUTH\r\n`，检测 REJECT 回复
- **绕过**：Hook `strcmp`/`strstr` 拦截含 "REJECT" 的回复
- **Zygisk 方案**：天然绕过，无 D-Bus 通信

### 4. /proc/self/maps 检测（最常见）
- **检测方式**：读取 maps 文件检测 frida-agent 内存映射
- **libc 层绕过**：Hook `open`/`openat` + `read`，过滤包含 frida/tmp 特征的行
- **IO 重定向绕过**（更彻底）：Hook `open`，将 maps 内容过滤后写入新文件，让 `open` 返回新文件 fd
- **直接 SVC syscall 检测**：App 用内联汇编直接发 syscall 绕过 libc，此时 libc 层 Hook 全部失效。**只能源码编译修改版 Frida**
- **Zygisk 方案**：Pine 不在 maps 中留下 frida 特征

### 5. /proc/self/fd 检测
- **检测方式**：遍历 fd 目录，检测 fd 链接是否指向 frida 相关文件
- **绕过**：Hook `readlinkat`，过滤包含 frida 特征的链接路径
- **Zygisk 方案**：天然绕过

### 6. 线程名检测
- **检测方式**：遍历 `/proc/self/task/*/comm`，检测 `gmain`、`gdbus`、`gum-js-loop` 等 Frida 特征线程名
- **绕过**：源码编译时修改线程名，或 Hook open/read 过滤 comm 文件内容
- **Zygisk 方案**：Pine 线程名无 Frida 特征，使用标准命名如 `XXHook-RPC`

### 7. /data/local/tmp 目录检测
- **检测方式**：检测 maps/fd 中是否出现 `/data/local/tmp/` 路径
- **绕过**：用 `-d` 参数指定其他目录存放 frida 临时 SO 文件
- **Zygisk 方案**：libpine.so 写入 `cache/` 后立即删除

### 8. Inline Hook 检测 / 代码完整性校验
- **检测方式**：校验关键函数的前几条指令是否被 Hook 修改（检查跳转指令）
- **绕过**：使用 `Interceptor.replace` 代替 `Interceptor.attach`
- **Zygisk 方案**：Pine ART Hook 不修改 .text 段，从 ART 层面替换方法入口

### 9. 服务端 Token 级验证码锁定（API 反爬）
- **检测方式**：服务端监控同一 token 的请求频率，触发后将验证码状态**绑定到 token**（而非 IP/设备）
- **特征**：API 返回 captcha_url 或 401，换 IP 无效，换设备无效，只有完成验证码或换新 token 才能恢复
- **绕过**：(1) 严格控制请求频率（≥60 秒间隔），预防而非恢复；(2) token 被锁后删除 App 的 Cookie DB 强制重新生成全新 token
- **Zygisk 方案**：通过 Cookie DB 直读（策略 8）定期获取最新 token，配合服务端频率控制

### 10. TLS 指纹检测（Web 反爬）
- **检测方式**：服务端分析 TLS 握手中的 cipher suite 顺序、扩展列表、ALPN 协议，识别客户端是浏览器还是脚本
- **特征**：同样的 URL + Cookie + Headers，浏览器成功，curl/httpx/requests 返回 403/400
- **绕过**：使用 `curl_cffi`（`impersonate="chrome120"`）模拟 Chrome 的 TLS 指纹。详见 `knowledge/web-anti-crawl.md`

### 11. httpOnly Cookie + WAF JS 挑战（Web 反爬）
- **检测方式**：首页返回 WAF JS 挑战页（如阿里云 WAF 的 `aliyun_waf` meta 标签），浏览器执行 JS 后获得 httpOnly cookie
- **特征**：`document.cookie` 拿不到关键认证 token，复制 cookie 发请求仍 401
- **绕过**：用 Playwright `context.cookies()` 获取完整 cookie（含 httpOnly），再用 `curl_cffi` 复用。详见 `knowledge/web-anti-crawl.md`

## 关键原则

### App 逆向
1. **Zygisk + Pine 是最通用的方案**：绕过上述 1-9 检测
2. **Frida 仅用于快速调试**：正式方案必须用 Zygisk
3. **优先使用 spawn 模式**（`frida -f`）
4. **避免 Hook 高频函数**（strstr/strcmp/getter 等）不加过滤
5. **Frida 17 API 变更**：`Module.findExportByName` 已移除，改用 `Process.getModuleByName("libc.so").getExportByName("open")`
6. **Native 层 Hook 必须在全局作用域**，不能放在 `Java.perform` 内部
7. **当所有方案都失败时**：放弃注入，改用 mitmproxy + Magisk SSL Pinning Bypass 抓包

### Web 反爬
8. **逆向调试阶段切忌连续请求**：先确认单次请求成功，再验证频率边界，避免把 token 搞废
9. **优先 curl_cffi**：大部分 TLS 指纹检测可以绕过，不需要上浏览器
10. **浏览器只用于获取 cookie**：通过 WAF 挑战后导出 cookie，业务请求用 curl_cffi
11. **httpOnly cookie 是常见壁垒**：`document.cookie` 拿不到，必须用 `context.cookies()`

## 实战工具

- **HLuda-server / strongR-frida**：去特征的 Frida server
- **FridaContainer**（GitHub）：社区维护的反检测脚本集合
- **mitmproxy + MagiskTrustUserCerts + TrustMeAlready**：不注入进程的抓包替代方案
