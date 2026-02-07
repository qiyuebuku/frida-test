# 起点读书搜索 Hook - 完整实施指南

## 快速开始

### 1. 在 Windows 宿主机上启动日志接收器

```bash
# 方法 1: 双击运行
start_log_receiver.bat

# 方法 2: 命令行运行
cd D:/123pan
python scripts/log_receiver.py
```

**日志接收器会**：
- 监听 `0.0.0.0:8889`
- 显示本机 IP 地址（如 `172.28.192.1`）
- 接收来自 Android 的日志
- 保存到 `./logs/YYYY-MM-DD/YYYY-MM-DD_HH-MM-SS_hook.log`

### 2. 修改 Android 端的 IP 地址

如果 Windows 的 IP 不是 `172.28.192.1`，需要修改代码：

```java
// MainHook.java 第 57 行
private static final String REMOTE_LOG_HOST = "你的Windows IP";  // 修改这里
```

然后重新编译部署。

### 3. 部署到手机

```bash
cd /home/yuyang/frida-test/qidian

# 编译
./gradlew assembleDebug

# 部署
./deploy.sh
```

### 4. 触发搜索操作

```bash
# 等待 App 启动完成后
adb -s 3B15BJ00GZL00000 shell input tap 540 100  # 点击搜索框
sleep 1
adb -s 3B15BJ00GZL00000 shell input text "test"  # 输入关键词
sleep 1
adb -s 3B15BJ00GZL00000 shell input keyevent KEYCODE_ENTER  # 触发搜索
```

### 5. 在 WSL2 分析日志

```bash
cd /home/yuyang/frida-test/qidian

# 分析日志
python3 scripts/analyze_logs.py
```

---

## 工作流程图

```
┌─────────────────┐
│  Windows 宿主机  │
│  运行日志接收器  │
│  :8889          │
└────────┬────────┘
         │ TCP Socket
         ▼
┌─────────────────┐
│  Android 手机   │
│  起点 App + Hook│
│  发送日志       │
└─────────────────┘

日志保存：
./logs/2026-02-06/2026-02-06_17-30-00_hook.log
         ↓
┌─────────────────┐
│  WSL2 分析工具  │
│  读取日志分析   │
└─────────────────┘
```

---

## 日志格式

### JSON 格式（推荐）

```json
{
  "timestamp": 1707208234567,
  "thread": "main",
  "tag": "SEARCH_REPO",
  "message": "SEARCH_REPO | com.qidian.QDReader.repository.SearchRepository.searchBooks(keyword=\"test\", pageNum=1)"
}
```

### 文本格式（后备）

```
[2026-02-06 17:30:45.123] [main] [SEARCH_REPO] SEARCH_REPO | com.qidian.QDReader.repository.SearchRepository.searchBooks(keyword="test", pageNum=1)
```

---

## 高级用法

### 只收集搜索相关的日志

修改 `hookSearchRepositories()` 方法，使用 `logRemote()` 替代 `Log.i()`：

```java
Log.i(TAG, String.format(...));  // 原来
logRemote("SEARCH_REPO", String.format(...));  // 改为这样
```

### 过滤特定类

只 Hook 感兴趣的类：

```java
private static void hookSearchRepositories(ClassLoader cl) {
    String[] targetClasses = {
        "com.qidian.QDReader.repository.SearchRepository",
        "com.qidian.QDReader.data.SearchManager",
        // 只添加你确认存在的类
    };
    // ...
}
```

### 实时查看日志

在 Windows 上，日志接收器会实时打印重要日志：

```python
def print_log(self, log_entry):
    if any(keyword in tag.lower() for keyword in ['search', 'hook', 'qdhook']):
        print(f"[{time_str}] [{tag}] {message}")
```

---

## 故障排查

### 问题 1: 日志接收器无法启动

**原因**：端口 8889 被占用

**解决**：
```bash
# Windows 查看端口占用
netstat -ano | findstr :8889

# 杀死占用进程
taskkill /PID <进程ID> /F
```

### 问题 2: Android 无法连接到 Windows

**原因**：防火墙阻止

**解决**：
1. Windows 防火墙允许 Python 入站连接
2. 或临时关闭防火墙测试

### 问题 3: 日志文件过大

**原因**：Hook 太多

**解决**：
- 减少 Hook 的类和方法
- 增加日志轮转间隔（`log_rotation_interval`）

---

## 下一步

1. **确认 Windows IP**：运行日志接收器查看
2. **测试连接**：启动 App 后查看 Windows 是否显示连接
3. **触发搜索**：手动搜索并观察日志
4. **分析结果**：运行分析工具提取入口方法

开始实施！
