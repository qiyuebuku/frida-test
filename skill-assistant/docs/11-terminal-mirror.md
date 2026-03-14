# 终端镜像方案：将 Claude CLI 原样投影到手机端

## 问题背景

当前方案在服务端通过 tmux capture-pane 轮询捕获 Claude CLI 输出，然后解析/过滤后以事件流推送到手机端。存在根本性缺陷：

1. **信息丢失严重**：`_is_noise()` 和 `_extract_content()` 过滤掉了大量有用内容（工具参数、执行结果、Claude 推理文本）
2. **行刷新无法表达**：spinner 行（`✢ Osmosing… (1m 27s)`）在终端中原地刷新同一行，但 capture-pane 每秒都捕获为"新行"，导致日志重复
3. **语义解析脆弱**：Claude CLI 的 TUI 格式随版本更新不断变化，正则匹配维护成本极高
4. **丢失格式信息**：颜色、粗体、缩进、表格对齐等终端格式全部丢失

**核心思路**：不再解析 Claude CLI 输出，而是将终端内容原样投影到手机端——在 App 中嵌入一个终端模拟器，完美还原服务端 CLI 的显示效果。

## 方案对比

### 方案 A：WebView + xterm.js（推荐）

**架构**：
```
服务端 tmux session ──(WebSocket)──> App 内 WebView(xterm.js) 渲染
```

**原理**：
- 服务端新增 WebSocket 端点，attach 到 Claude CLI 的 tmux session
- 通过 `tmux -CC`（control mode）或 `capture-pane -p -e`（带 ANSI 转义）获取终端数据
- App 端用 WebView 加载 xterm.js，通过 WebSocket 接收终端数据并渲染

**优点**：
- xterm.js 是业界标准终端模拟器（VS Code、Tabby 等都在用），ANSI 支持完整
- 颜色、粗体、光标定位、行刷新（\r）等全部原生支持，spinner 不会重复显示
- 开发量最小：前端一个 HTML 页面 + WebSocket 连接
- 支持用户输入（追问消息可直接在终端中键入）

**缺点**：
- WebView 在 Android 上性能有限，列宽 >100 时可能卡顿（80 列够用）
- WebView 与原生 Compose UI 的交互（滚动、手势）需要适配
- 额外引入 JS 依赖

**实现要点**：
1. 服务端：FastAPI WebSocket 端点 → attach tmux session → 流式转发终端数据
2. 前端：WebView 加载本地 HTML（内嵌 xterm.js）→ WebSocket 连到服务端
3. 输入：用户在 xterm.js 中输入 → WebSocket 发回服务端 → tmux send-keys

### 方案 B：ttyd 直连

**架构**：
```
ttyd --attach tmux-session ──(HTTP/WS)──> App 内 WebView 访问 ttyd Web 页面
```

**原理**：
- 在服务端运行 ttyd，绑定到 Claude CLI 的 tmux session
- App 端 WebView 直接打开 ttyd 的 Web 页面（ttyd 自带 xterm.js 前端）

**优点**：
- 零开发量：ttyd 开箱即用，自带完整的 Web 终端前端
- 成熟稳定，C 实现性能好
- 内置 WebSocket 协议（二进制帧 + 命令字节前缀）

**缺点**：
- 需要额外安装 ttyd 进程
- ttyd 是独立进程，与 FastAPI 服务隔离，端口/认证需要额外配置
- 每个任务的 tmux session 需要动态绑定 ttyd 实例（端口管理复杂）
- 自定义能力弱（无法在终端流中注入自定义 UI 元素）

### 方案 C：原生终端组件（Termux terminal-view）

**架构**：
```
服务端 tmux ──(WebSocket 二进制流)──> App 原生 TerminalView 渲染
```

**原理**：
- 使用 Termux 的 `terminal-view` 组件（Maven: `com.termux:terminal-view`）
- 服务端通过 WebSocket 发送原始终端字节流
- App 端原生 View 渲染 ANSI 序列

**优点**：
- 纯原生，性能最好
- 与 Compose UI 集成更自然（AndroidView 包裹）
- 不依赖 WebView

**缺点**：
- Termux 组件文档少，集成复杂度高
- 需要处理终端尺寸协商（SIGWINCH）
- 维护负担：Termux 组件更新频率低

## 推荐方案：A（WebView + xterm.js）

选择理由：
1. **开发量最小**，核心只需一个 WebSocket 端点 + 一个 HTML 页面
2. xterm.js 的 ANSI 渲染能力完全覆盖 Claude CLI 的输出格式
3. 用户输入可直接通过终端发送，不需要单独的消息 API
4. 后续可平滑升级为方案 B（ttyd）或方案 C（原生）

## 详细设计

### 1. 服务端：WebSocket 终端中继

在 FastAPI 中新增 WebSocket 端点，连接到 tmux session 并双向转发数据。

**端点**：`/ws/terminal/{task_id}`

**协议**：
- 服务端 → 客户端：原始终端字节（含 ANSI 转义序列）
- 客户端 → 服务端：用户键入的字符（直接 send-keys 到 tmux）
- 控制消息：JSON 格式，`{"type": "resize", "cols": 80, "rows": 24}` 等

**数据获取方式 — tmux control mode（`tmux -CC`）**：

tmux control mode 是专为程序化集成设计的协议：
- 文本协议，所有事件以 `%` 前缀通知
- `%output %pane-id content`：窗格输出（含八进制转义的控制字符）
- 内置流控：`pause-after` 防止缓冲区溢出
- 与直接轮询 `capture-pane` 相比，是**事件驱动**的，不会漏掉也不会重复

```python
# 伪代码
async def ws_terminal(websocket, task_id):
    session_name = f"sa_claude_{task_id}"

    # 启动 tmux control mode 客户端
    proc = await asyncio.create_subprocess_exec(
        "tmux", "-CC", "attach-session", "-t", session_name,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )

    # 双向转发
    async def forward_output():
        async for line in proc.stdout:
            if line.startswith(b"%output"):
                # 解析 %output %pane-id content，提取 content
                data = parse_control_output(line)
                await websocket.send_bytes(data)

    async def forward_input():
        async for msg in websocket.iter_text():
            if msg.startswith("{"):
                # JSON 控制消息（resize 等）
                handle_control(proc, msg)
            else:
                # 用户输入 → tmux send-keys
                _tmux("send-keys", "-t", session_name, "-l", msg)

    await asyncio.gather(forward_output(), forward_input())
```

**备选数据源 — `capture-pane -p -e`（更简单）**：

如果 control mode 解析复杂度过高，可退回轮询模式，但使用 `-e` 保留 ANSI：
```python
async def ws_terminal_polling(websocket, task_id):
    session_name = f"sa_claude_{task_id}"
    last_screen = ""
    while True:
        screen = subprocess.run(
            ["tmux", "capture-pane", "-t", session_name, "-p", "-e", "-S", "-50"],
            capture_output=True, timeout=5
        ).stdout
        if screen != last_screen:
            # 发送完整屏幕快照（xterm.js 用 \033[2J 清屏 + 重绘）
            await websocket.send_bytes(b"\033[2J\033[H" + screen)
            last_screen = screen
        await asyncio.sleep(0.5)
```

注意：轮询方式会导致 xterm.js 闪烁（每次清屏重绘）。control mode 是更优解。

### 2. 前端：WebView + xterm.js

**HTML 页面**（打包在 App assets 中）：

```html
<!DOCTYPE html>
<html>
<head>
  <link rel="stylesheet" href="xterm.css" />
  <script src="xterm.js"></script>
  <script src="xterm-addon-fit.js"></script>
  <style>
    body { margin: 0; background: #1e1e1e; overflow: hidden; }
    #terminal { width: 100%; height: 100%; }
  </style>
</head>
<body>
  <div id="terminal"></div>
  <script>
    const term = new Terminal({
      fontSize: 13,
      fontFamily: 'monospace',
      theme: { background: '#1e1e1e', foreground: '#d4d4d4' },
      cursorBlink: false,
      disableStdin: false,
    });
    const fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);
    term.open(document.getElementById('terminal'));
    fitAddon.fit();

    // WebSocket 连接
    const taskId = new URLSearchParams(location.search).get('task_id');
    const ws = new WebSocket(`ws://${location.hostname}:8900/ws/terminal/${taskId}`);
    ws.binaryType = 'arraybuffer';

    ws.onmessage = (event) => {
      term.write(new Uint8Array(event.data));
    };

    // 用户输入转发
    term.onData((data) => {
      ws.send(data);
    });

    // 窗口大小变化
    window.addEventListener('resize', () => {
      fitAddon.fit();
      ws.send(JSON.stringify({
        type: 'resize', cols: term.cols, rows: term.rows
      }));
    });
  </script>
</body>
</html>
```

**Android 端集成**：

```kotlin
// TaskDetailScreen.kt 中
@Composable
fun TerminalView(taskId: Int, serverHost: String) {
    AndroidView(factory = { context ->
        WebView(context).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            loadUrl("file:///android_asset/terminal.html?task_id=$taskId&host=$serverHost")
        }
    })
}
```

### 3. 与现有系统的兼容

**渐进迁移**，不破坏现有功能：

| 功能 | 现有方案（保留） | 终端镜像（新增） |
|------|----------------|-----------------|
| 任务列表 | `/api/tasks` REST | 不变 |
| 任务创建 | `/api/skills/{name}/run` | 不变 |
| 进度/状态 | SSE event stream | 保留，终端镜像作为**补充视图** |
| 结果展示 | Markdown 渲染 | 终端原样 + Markdown 双视图切换 |
| 追问消息 | `/api/tasks/{id}/message` | 直接在终端中键入（更自然） |
| 工具调用步骤 | 解析 tool_call 事件 | 终端中直接可见（无需解析） |

**UI 切换**：TaskDetail 页面顶部增加 Tab 切换：
- **结果** Tab：现有 Markdown 渲染（最终报告）
- **终端** Tab：xterm.js 实时终端（完整过程）

### 4. 输入方案

终端镜像后，用户可以：
1. **直接在终端输入追问**：xterm.js 捕获键盘 → WebSocket → tmux send-keys
2. **保留消息输入框**：对于手机端，虚拟键盘+终端体验较差，可保留底部输入框，输入后通过 WebSocket 发送
3. **建议**：默认使用底部输入框（更适合手机），终端本身设为只读（`disableStdin: true`），输入框发送时调用 `ws.send(message + '\n')`

### 5. 服务端改动范围

| 文件 | 改动 |
|------|------|
| `routers/terminal.py`（新建） | WebSocket 端点 `/ws/terminal/{task_id}` |
| `routers/__init__.py` | 注册 terminal router |
| `services/task_executor.py` | 无需改动（tmux session 已存在） |
| App assets/ | 新增 `terminal.html`, `xterm.js`, `xterm.css`, `xterm-addon-fit.js` |
| `TaskDetailScreen.kt` | 新增 TerminalView + Tab 切换 |

### 6. 关键技术决策

**Q: tmux control mode 还是 capture-pane 轮询？**

推荐 **control mode**：
- 事件驱动，不漏不重
- 原生支持行刷新（\r 回车覆盖同一行），spinner 不会重复
- 内置流控（pause-after）

capture-pane 轮询的问题：
- 需要清屏重绘，xterm.js 会闪烁
- 轮询间隔太大漏内容，太小浪费 CPU
- 仍然无法正确处理行刷新

**Q: xterm.js 版本/大小？**

- 核心库 ~300KB（gzip 后 ~90KB）
- 打包到 App assets 中，无需网络加载
- 使用 v5.x 最新稳定版

**Q: 多任务终端如何管理？**

- 每个 task_id 对应一个 tmux session（现有机制）
- WebSocket 连接时 attach 到对应 session
- 多个客户端可同时 attach 同一 session（只读）
- 断线重连：WebSocket 断开后重新连接，tmux session 仍在，内容不丢

## 实施步骤

1. **服务端 WebSocket 端点**（~2h）
   - 新建 `routers/terminal.py`
   - 实现 tmux control mode attach + WebSocket 双向转发
   - 处理 resize、断线重连

2. **前端 HTML + xterm.js**（~1h）
   - 打包 xterm.js 到 assets
   - 编写 terminal.html（WebSocket 连接 + 渲染）

3. **Android 端集成**（~2h）
   - TaskDetailScreen 新增终端 Tab
   - WebView 加载 terminal.html
   - 底部输入框通过 WebSocket 发送消息

4. **测试与调优**（~1h）
   - 验证 ANSI 颜色、spinner、表格渲染
   - WebView 性能测试（80 列 vs 更宽）
   - 断线重连验证
