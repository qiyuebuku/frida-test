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

## 关键原则

1. **Zygisk + Pine 是最通用的方案**：绕过上述全部 8 种检测
2. **Frida 仅用于快速调试**：正式方案必须用 Zygisk
3. **优先使用 spawn 模式**（`frida -f`）
4. **避免 Hook 高频函数**（strstr/strcmp/getter 等）不加过滤
5. **Frida 17 API 变更**：`Module.findExportByName` 已移除，改用 `Process.getModuleByName("libc.so").getExportByName("open")`
6. **Native 层 Hook 必须在全局作用域**，不能放在 `Java.perform` 内部
7. **当所有方案都失败时**：放弃注入，改用 mitmproxy + Magisk SSL Pinning Bypass 抓包

## 实战工具

- **HLuda-server / strongR-frida**：去特征的 Frida server
- **FridaContainer**（GitHub）：社区维护的反检测脚本集合
- **mitmproxy + MagiskTrustUserCerts + TrustMeAlready**：不注入进程的抓包替代方案
