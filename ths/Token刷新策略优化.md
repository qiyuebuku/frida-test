# Token刷新策略优化 - 避免单点登录冲突

## 问题

**单点登录限制**：使用密码MD5登录会导致手机App被踢下线

## 解决方案

### 优先级策略

```
┌─────────────────────────────────────────┐
│  检测到token即将过期                     │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│  方案1: 从手机App获取（优先）✅           │
│  ─────────────────────────────────      │
│  • 检查手机连接                          │
│  • 检测App是否运行                       │
│  • 等待OkHttp Interceptor捕获token      │
│  • 不会踢掉手机端                        │
└──────────────┬──────────────────────────┘
               │
               │ 如果失败
               ▼
┌─────────────────────────────────────────┐
│  方案2: 使用密码MD5登录（备用）⚠️         │
│  ─────────────────────────────────      │
│  • 调用基金账户登录API                   │
│  • 获取新token                           │
│  • ⚠️ 会踢掉手机端                       │
└─────────────────────────────────────────┘
```

---

## 方案1: 从手机获取（推荐）

### 前提条件

**必须运行JSBridge服务**（OkHttp Interceptor）

有两种方式：

#### 方式A: 手动运行一次（简单）

在手机上打开同花顺App，进入 **交易 → 基金** 页面，确保JSBridge服务在运行。

```bash
# 检查JSBridge是否可用
curl http://localhost:18900/auth | jq .available
# 输出 true 表示可用
```

#### 方式B: 持久化Hook服务（高级）

后台运行Frida Hook，自动捕获所有HTTP请求：

```bash
# 启动持久化Hook服务（后台运行）
cd /home/yuyang/frida-test/ths/api
nohup python start_persistent_hook.py > /tmp/frida_persistent.log 2>&1 &

# 查看日志
tail -f /tmp/frida_persistent.log
```

**功能**：
- 自动检测App运行状态
- App运行时自动注入OkHttp Hook
- 后台持续监听HTTP请求
- 自动提取认证参数

---

## 方案2: 密码登录（备用）

**仅在以下情况使用**：
- 手机未连接
- JSBridge服务未运行
- 从手机获取失败
- token已完全过期

**⚠️ 注意**：使用密码登录会导致手机App被踢下线（单点登录限制）

---

## 使用方法

### 1. 自动刷新（推荐配置）

```python
from auth_manager import AuthManager

# 初始化（优先从手机获取）
manager = AuthManager(enable_auto_refresh=True)

# 获取认证参数（自动检测过期并刷新）
auth = manager.get_auth()

# 刷新策略：
# 1. 先尝试从手机获取（不踢手机）
# 2. 失败后才使用密码登录（踢手机）
```

### 2. 手动刷新

```bash
# 自动刷新（优先手机）
python auth_manager.py auto-refresh

# 输出示例：
# 🔄 策略: 优先从手机获取token（避免单点登录冲突）
# 尝试方式 1: 从手机App自动获取token
# ✅ 手机已连接
# ✅ App已在运行
# ✅ 完整认证参数已捕获 (key1-key5)
# ✅ 自动刷新成功
```

### 3. 强制使用密码登录

如果确实需要踢掉手机端（例如手机丢失、长时间无法连接手机等）：

```python
manager = AuthManager()

# 跳过手机获取，直接使用密码登录
if manager.refresh_by_password():
    print("✅ 密码登录成功（手机端已被踢下线）")
```

---

## 配置文件

### config.json

```json
{
  "adb_device": "3B15BJ00GZL00000",
  "adb_path": "/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe",
  "app_package": "com.hexin.plat.android",
  "enable_auto_refresh": true
}
```

### auth_cache.json

```json
{
  "auth": {
    "key1": "...",
    "key2": "...",
    "key3": "100113970166",
    "key4": "auth",
    "key5": "...",
    "userId": "690359103",
    "account": "100113970166"
  },
  "cached_at": 1772940313,
  "expires_at": 1775532303,
  "password_md5": "DB53EF5F124897EA9DD2C33DE1566592"  ← 备用登录
}
```

---

## 最佳实践

### 日常使用（手机可用）

1. **启动JSBridge服务**
   ```bash
   # 方式1: 在手机上打开App进入基金页面
   # 方式2: 运行持久化Hook服务
   nohup python api/start_persistent_hook.py > /tmp/frida_persistent.log 2>&1 &
   ```

2. **定时检查刷新**
   ```bash
   # cron任务（每天凌晨3点）
   0 3 * * * cd /home/yuyang/frida-test/ths/api && python auth_manager.py auto-refresh
   ```

3. **程序自动刷新**
   ```python
   # 每次API调用前自动检测
   manager = AuthManager(enable_auto_refresh=True)
   auth = manager.get_auth()  # 自动从手机获取
   ```

### 应急使用（手机不可用）

```bash
# 强制使用密码登录（踢掉手机）
python -c "
from auth_manager import AuthManager
m = AuthManager()
m.refresh_by_password()
"
```

---

## 工作流程

### 场景1: 手机连接 + App运行

```
1. 检测到token即将过期
2. 检查手机连接 ✅
3. 检测App运行 ✅
4. 等待OkHttp捕获token
5. 从JSBridge获取 ✅
6. 更新缓存
7. ✅ 完成（手机端不受影响）
```

### 场景2: 手机连接 + App未运行

```
1. 检测到token即将过期
2. 检查手机连接 ✅
3. 检测App运行 ❌
4. 启动App
5. 等待OkHttp捕获token
6. 从JSBridge获取 ✅
7. ✅ 完成（手机端不受影响）
```

### 场景3: 手机未连接

```
1. 检测到token即将过期
2. 检查手机连接 ❌
3. 使用密码MD5登录
4. 获取新token ✅
5. ⚠️ 手机端被踢下线
6. ✅ 完成（但手机需要重新登录）
```

---

## 监控和告警

### 检查刷新策略

```bash
# 查看当前token状态
python auth_manager.py status

# 输出：
# {
#   "status": "valid",
#   "expires_at": "2026-04-07 11:34:27",
#   "remaining_days": 29
# }
```

### 监控持久化Hook服务

```bash
# 检查服务是否运行
ps aux | grep start_persistent_hook

# 查看实时日志
tail -f /tmp/frida_persistent.log
```

### 监控JSBridge状态

```bash
# 检查JSBridge是否可用
curl -s http://localhost:18900/auth | jq '{available, has_key5: (.key5 != null)}'

# 输出示例：
# {
#   "available": true,
#   "has_key5": true
# }
```

---

## 故障排查

### Q1: auto-refresh总是使用密码登录（踢掉手机）

**可能原因**：
- JSBridge服务未运行
- OkHttp Interceptor未捕获到请求
- 手机未连接

**解决方法**：
```bash
# 1. 检查手机连接
adb devices

# 2. 检查JSBridge
curl http://localhost:18900/auth

# 3. 启动持久化Hook服务
python api/start_persistent_hook.py &

# 4. 或手动在手机上进入基金页面
```

### Q2: 从手机获取token超时

**可能原因**：
- App未发起API请求
- OkHttp Interceptor未监听正确的请求

**解决方法**：
```bash
# 手动在手机上操作
# 1. 打开同花顺App
# 2. 进入"交易 → 基金"
# 3. 等待页面加载完成
# 4. 重新运行 auto-refresh
```

### Q3: 持久化Hook服务频繁重启

**可能原因**：
- App频繁重启
- Frida版本不兼容
- Hook脚本错误

**解决方法**：
```bash
# 查看详细日志
tail -100 /tmp/frida_persistent.log

# 检查frida版本
frida --version  # 应该是 17.6.2

# 测试Hook脚本
frida -H localhost:27042 -n com.hexin.plat.android -l /tmp/hook_login.js
```

---

## 总结

**核心改进**：
1. ✅ 避免踢掉手机端（优先从手机获取）
2. ✅ 双重保障（手机失败才用密码）
3. ✅ 自动检测（手机连接状态）
4. ✅ 持久化Hook（可选，完全自动化）

**推荐配置**：
- 日常使用：启动持久化Hook + 定时自动刷新
- 应急使用：手动密码登录（踢掉手机）
- 最佳体验：手机App保持运行 + OkHttp实时监听

**下一步优化**：
- [ ] 添加token刷新失败的告警
- [ ] 持久化Hook服务自动重启机制
- [ ] Web界面监控token状态和刷新策略
