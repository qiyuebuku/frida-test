# 使用 Zygisk 替代 Frida 方案

## 🎯 为什么使用 Zygisk？

**Frida 的问题**：
- ❌ 启动速度慢（需要注入、加载脚本）
- ❌ 稳定性差（容易崩溃、断连）
- ❌ 需要手动启动 frida-server
- ❌ 需要运行 Python 脚本维持连接

**Zygisk 的优势**：
- ✅ **随 App 自动启动**：无需手动操作
- ✅ **速度快**：直接注入到 Zygote 进程，随 App 启动而启动
- ✅ **稳定性高**：基于 Magisk Zygisk 框架，不会断连
- ✅ **零维护**：一次安装，永久运行
- ✅ **更隐蔽**：比 Frida 更难被检测

---

## 📦 Zygisk 模块说明

### 已有的模块

位置：`/home/yuyang/frida-test/ths/zygisk/`

- **thshook_zygisk_new.zip** (202KB) - 最新版本（推荐）
- **thshook_zygisk.zip** (179KB) - 旧版本

### 功能

Zygisk 模块已实现完整功能：
1. **HTTP 服务器**：在 18900 端口监听
2. **GET /auth 端点**：返回最新捕获的交易认证参数（key1-key5）
3. **OkHttp Hook**：自动拦截同花顺 App 的 HTTP 请求
4. **POST /proxy 端点**：代理转发请求（复用 App 的 OkHttpClient）

### 源码位置

`/home/yuyang/frida-test/ths/app/src/main/java/com/yuyang/thshook/MainHook.java`

---

## 🚀 安装步骤

### 前提条件

1. ✅ 手机已 Root（Magisk）
2. ✅ 已启用 Zygisk（Magisk 设置 → Zygisk）
3. ✅ adb 已连接手机

### 安装模块

#### 方法 1: 通过 Magisk Manager（推荐）

```bash
# 1. 推送模块到手机
/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe -s 3B15BJ00GZL00000 push \
  /home/yuyang/frida-test/ths/zygisk/thshook_zygisk_new.zip \
  /sdcard/Download/

# 2. 在手机上操作：
# Magisk Manager → 模块 → 从本地安装 → 选择 thshook_zygisk_new.zip

# 3. 重启手机
/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe -s 3B15BJ00GZL00000 reboot
```

#### 方法 2: 通过 adb 安装（高级）

```bash
# 1. 进入手机 shell
/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe -s 3B15BJ00GZL00000 shell

# 2. 切换到 root
su

# 3. 解压模块到 Magisk 模块目录
unzip /sdcard/Download/thshook_zygisk_new.zip -d /data/adb/modules/thshook/

# 4. 退出并重启
exit
exit
/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe -s 3B15BJ00GZL00000 reboot
```

---

## ✅ 验证安装

### 1. 检查模块是否启用

```bash
# 查看 Magisk 模块列表
/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe -s 3B15BJ00GZL00000 shell ls /data/adb/modules/

# 应该看到 thshook 目录
```

### 2. 设置端口转发

```bash
# 转发 18900 端口
/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe -s 3B15BJ00GZL00000 forward tcp:18900 tcp:18900
```

### 3. 测试 HTTP 服务

```bash
# 测试服务是否运行
curl http://localhost:18900/auth

# 预期输出（token 为空是正常的，因为还没触发捕获）：
# {"key1":"","key2":"","key3":"","key4":"","key5":"","userId":"","sessionId":"","cookie":"","capture_time":0,"available":false}
```

### 4. 触发 token 捕获

**打开同花顺 App → 交易 → 基金**

App 会自动发起 HTTP 请求，Zygisk 模块会自动拦截并捕获 token。

```bash
# 再次查询（应该能看到 token）
curl http://localhost:18900/auth | jq .

# 预期输出：
# {
#   "key1": "3B15BJ00GZL00000...",
#   "key2": "xxxx",
#   "key3": "100113970166",
#   "key4": "10001...",
#   "key5": "eyJhbGciOiJIUzI1N...",
#   "userId": "100113970166",
#   "sessionId": "...",
#   "cookie": "...",
#   "capture_time": 1710001234567,
#   "available": true
# }
```

---

## 🔧 与现有代码集成

好消息：**无需修改任何代码！**

现有的 `auth_manager.py` 已经兼容 Zygisk：
- JSBridge URL: `http://localhost:18900/auth`（Frida 和 Zygisk 都用这个端点）
- 端口转发：`adb forward tcp:18900 tcp:18900`
- API 接口完全一致

### 测试自动刷新

```bash
cd /home/yuyang/frida-test/ths/api

# 测试从手机获取 token（无需启动 Frida）
python auth_manager.py auto-refresh

# 预期输出：
# 🔄 策略: 优先从手机获取token（避免单点登录冲突）
# 尝试方式 1: 从手机App自动获取token
# ✅ 手机已连接
# ✅ App已在运行
# ✅ 完整认证参数已捕获 (key1-key5)
# ✅ 自动刷新成功
```

---

## 📊 Frida vs Zygisk 对比

| 特性 | Frida | Zygisk |
|------|-------|--------|
| **启动方式** | 手动启动 frida-server + Python 脚本 | 随 App 自动启动 |
| **速度** | 慢（需要注入、编译脚本） | 快（直接注入） |
| **稳定性** | 中等（容易断连） | 高（基于 Magisk） |
| **维护成本** | 高（需要保持 Python 进程运行） | 零（一次安装） |
| **端口** | 18900（手动转发） | 18900（自动监听） |
| **检测风险** | 高（frida 特征明显） | 低（更隐蔽） |
| **是否需要手机连接** | 是（需要 adb） | 否（但端口转发需要） |
| **推荐场景** | 临时调试、动态分析 | **生产环境、长期运行** |

---

## 🎉 迁移步骤总结

### 从 Frida 迁移到 Zygisk

**只需 3 步**：

1. **安装 Zygisk 模块**
   ```bash
   # 推送模块到手机
   adb push /home/yuyang/frida-test/ths/zygisk/thshook_zygisk_new.zip /sdcard/Download/
   # 在 Magisk Manager 中安装并重启
   ```

2. **设置端口转发**
   ```bash
   adb forward tcp:18900 tcp:18900
   ```

3. **测试**
   ```bash
   curl http://localhost:18900/auth
   ```

**无需修改任何 Python 代码！**

### 停止使用 Frida

不再需要：
- ❌ `python start_persistent_hook.py`
- ❌ frida-server 进程
- ❌ hook_login.js 脚本
- ❌ check_jsbridge.sh（可选保留）

---

## 🛠️ 故障排查

### Q1: curl http://localhost:18900/auth 连接失败

**检查清单**：
```bash
# 1. 检查端口转发
adb forward --list | grep 18900
# 如果没有，重新设置：
adb forward tcp:18900 tcp:18900

# 2. 检查 Zygisk 模块是否启用
adb shell ls /data/adb/modules/ | grep thshook

# 3. 检查 App 是否运行
adb shell ps -A | grep com.hexin.plat.android

# 4. 查看日志
adb logcat -s THSHook:V
```

### Q2: 返回 "available": false

**原因**：App 还没有发起 HTTP 请求，OkHttp Interceptor 未捕获到 token

**解决**：
```bash
# 方法 1: 手动打开 App → 交易 → 基金
# 方法 2: 用 adb 启动 App
adb shell monkey -p com.hexin.plat.android -c android.intent.category.LAUNCHER 1

# 等待 3-5 秒后再查询
curl http://localhost:18900/auth | jq .
```

### Q3: 模块安装后不生效

**检查**：
```bash
# 1. 确认 Magisk 已启用 Zygisk
adb shell su -c 'cat /data/adb/magisk/config'
# 应该看到 zygisk=true

# 2. 确认模块已启用
adb shell su -c 'ls /data/adb/modules/thshook/'
# 应该看到模块文件，且没有 disable 文件

# 3. 查看日志
adb logcat -s Magisk:V Zygisk:V THSHook:V
```

### Q4: 端口转发不稳定

**持久化端口转发**：

创建脚本 `/home/yuyang/frida-test/ths/api/setup_port_forward.sh`：
```bash
#!/bin/bash
ADB_PATH="/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe"
DEVICE_ID="3B15BJ00GZL00000"

echo "设置adb端口转发..."
$ADB_PATH -s $DEVICE_ID forward tcp:18900 tcp:18900

if [ $? -eq 0 ]; then
    echo "✅ 端口转发设置成功"
    echo ""
    echo "当前端口转发列表："
    $ADB_PATH forward --list | grep 18900
    echo ""
    echo "💡 测试连接："
    echo "   curl http://localhost:18900/auth"
else
    echo "❌ 端口转发设置失败"
    exit 1
fi
```

---

## 📝 Zygisk 模块工作原理

### 架构

```
┌─────────────────────────────────────────────────┐
│  Android App (同花顺)                            │
│  ┌─────────────────────────────────┐            │
│  │  OkHttpClient                    │            │
│  │  ↓ (Pine Hook 拦截)              │            │
│  │  【Zygisk Module: MainHook.java】│            │
│  │    ├─ 捕获 HTTP 请求/响应         │            │
│  │    ├─ 提取 key1-key5             │            │
│  │    └─ HTTP Server (18900端口)    │            │
│  └─────────────────────────────────┘            │
└─────────────────────────────────────────────────┘
                      ↕ (端口转发)
┌─────────────────────────────────────────────────┐
│  WSL2 (Linux)                                    │
│  ┌─────────────────────────────────┐            │
│  │  Python (auth_manager.py)       │            │
│  │  ↓ curl http://localhost:18900  │            │
│  │  ✅ 获取 token                   │            │
│  └─────────────────────────────────┘            │
└─────────────────────────────────────────────────┘
```

### 关键技术

1. **Zygisk 注入**：模块在 App 启动时自动注入
2. **Pine Hook**：Hook OkHttp 的 Request.Builder.build() 方法
3. **ServerSocket**：在 18900 端口监听 HTTP 请求
4. **adb forward**：将手机的 18900 端口转发到本地

---

## ✅ 最佳实践

### 推荐配置（生产环境）

1. **安装 Zygisk 模块**（一次性）
   ```bash
   # 推送并安装模块
   adb push thshook_zygisk_new.zip /sdcard/Download/
   # Magisk Manager → 安装 → 重启
   ```

2. **自动设置端口转发**（开机脚本）
   ```bash
   # 添加到 ~/.bashrc 或 crontab
   alias ths-forward='/home/yuyang/frida-test/ths/api/setup_port_forward.sh'
   ```

3. **程序自动刷新**
   ```python
   # auth_manager.py 已配置自动刷新
   manager = AuthManager(enable_auto_refresh=True)
   auth = manager.get_auth()  # 自动检测过期并刷新
   ```

4. **定时检查**（可选）
   ```bash
   # crontab -e
   # 每天凌晨 3 点检查并刷新 token
   0 3 * * * cd /home/yuyang/frida-test/ths/api && python auth_manager.py auto-refresh
   ```

### 应急方案

如果 Zygisk 服务异常：
```bash
# 查看日志
adb logcat -s THSHook:V

# 重启 App（触发 Zygisk 重新注入）
adb shell am force-stop com.hexin.plat.android
adb shell monkey -p com.hexin.plat.android -c android.intent.category.LAUNCHER 1

# 重新设置端口转发
adb forward tcp:18900 tcp:18900
```

---

## 🎉 总结

### 升级收益

- ✅ **无需维护 Frida**：不用再启动 frida-server、运行 Python hook 脚本
- ✅ **更快速**：Zygisk 随 App 启动，无延迟
- ✅ **更稳定**：基于 Magisk 框架，不会断连
- ✅ **更隐蔽**：比 Frida 更难被检测
- ✅ **零成本迁移**：现有代码无需修改

### 推荐使用场景

- ✅ **日常自动刷新 token**：Zygisk（推荐）
- ✅ **生产环境运行**：Zygisk（推荐）
- ⚠️ **临时调试分析**：Frida（灵活性高）
- ⚠️ **动态修改请求**：Frida（实时编辑脚本）

---

**结论**：对于长期运行的 token 自动刷新场景，**强烈推荐使用 Zygisk**！
