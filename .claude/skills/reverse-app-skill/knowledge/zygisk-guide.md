# Zygisk 深度指南 — 我们的核心注入框架

## 为什么 Zygisk 是最佳方案

**一句话**：Zygisk 在 Zygote fork 时注入，比 App 的任何安全检测代码都先执行，且不留下 Frida/Xposed 的特征。

| 对比维度 | Frida | LSPosed/Xposed | **Zygisk + Pine** |
|---------|-------|----------------|-------------------|
| 注入时机 | App 启动后 attach | App 启动时 bridge 注入 | **Zygote fork 时（最早）** |
| 独立进程 | frida-server 常驻 | 无 | **无** |
| 特征文件 | frida-agent.so in maps | lspd bridge SO | **无（InMemoryDex + 删除 SO）** |
| 特征线程 | gmain/gdbus/gum-js-loop | 无 | **无** |
| 特征端口 | 27042 | 无 | **无** |
| .text 段修改 | inline hook 修改 | 不修改 | **不修改（Pine ART Hook）** |
| 360加固检测 | ❌ 必被检出 | ❌ 检测 bridge SO | **✅ 完全绕过** |
| 梆梆加固检测 | ❌ | ⚠️ | **✅ 完全绕过** |
| 腾讯乐固检测 | ❌ | ⚠️ | **✅ 完全绕过** |

**核心优势**：大部分安全加固厂商（360/梆梆/腾讯乐固）的检测目标是 Frida 和 Xposed/LSPosed，
**几乎没有 App 会检测 Zygisk 注入**，因为：
1. Zygisk 工作在 Zygote 进程级别，App 没有能力感知 fork 前发生了什么
2. DEX 完全在内存中加载（InMemoryDexClassLoader），不写入 odex/vdex
3. libpine.so 写入后立即删除，加载后仅存于内存
4. Pine ART Hook 从 ART 内部替换方法入口，不修改 .text 段代码

## Zygisk 工作原理

### 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        Zygote 进程                               │
│                                                                   │
│  ┌─────────────────┐       ┌──────────────────────────┐         │
│  │ Zygisk 框架      │──────→│ 加载所有模块的 SO         │         │
│  │ (Magisk/KernelSU)│       │ arm64-v8a.so              │         │
│  └─────────────────┘       └──────────────────────────┘         │
│                                       │                           │
│                              onLoad(api, env)                     │
│                                       │                           │
│                          preAppSpecialize(args)                   │
│                           ├─ 检查包名是否为目标                    │
│                           ├─ 非目标 → DLCLOSE 卸载                │
│                           ├─ 是目标 → connectCompanion()          │
│                           └─ 从 Companion 读取 DEX + SO 到内存    │
│                                       │                           │
│                              fork() ──┤                           │
│                                       │                           │
│  ┌────────────────────────────────────┤                           │
│  │ 子进程 (目标 App)                    │                           │
│  │                                     │                           │
│  │  postAppSpecialize()                │                           │
│  │   ├─ 写 .pine.so 到 cache/         │                           │
│  │   ├─ InMemoryDexClassLoader 加载 DEX│                           │
│  │   ├─ loadClass("MainHook")          │                           │
│  │   ├─ MainHook.entry(cl, soPath)     │                           │
│  │   │   ├─ System.load(soPath)        │                           │
│  │   │   ├─ Pine.ensureInitialized()   │                           │
│  │   │   └─ 安装各种 Hook              │                           │
│  │   └─ unlink(.pine.so)  ← 删除痕迹  │                           │
│  │                                     │                           │
│  │  App 正常启动，Hook 已生效           │                           │
│  └─────────────────────────────────────┘                           │
│                                                                   │
│  ┌─────────────────────────────────────┐                          │
│  │ Companion 进程 (root 权限)           │                          │
│  │  companion_handler(fd)               │                          │
│  │   ├─ 读取 /data/adb/modules/*/dex/  │                          │
│  │   │   ├─ classes.dex                 │                          │
│  │   │   ├─ classes2.dex                │                          │
│  │   │   ├─ classes3.dex                │                          │
│  │   │   └─ libpine.so                  │                          │
│  │   └─ 通过 socket 发送给子进程        │                          │
│  └─────────────────────────────────────┘                          │
└─────────────────────────────────────────────────────────────────┘
```

### 三个生命周期阶段

#### 阶段 1: onLoad — 初始化

```cpp
void onLoad(zygisk::Api *api, JNIEnv *env) override {
    this->api = api;
    this->env = env;
}
```

每个 App 启动时都会触发。仅保存指针，不做任何判断。

#### 阶段 2: preAppSpecialize — fork 前（决策 + 读取文件）

这是最关键的阶段，在 Zygote fork 出子进程**之前**执行：

```cpp
void preAppSpecialize(zygisk::AppSpecializeArgs *args) override {
    // 1. 检查包名
    const char *name = env->GetStringUTFChars(args->nice_name, nullptr);
    bool is_target = (strcmp(name, "com.target.package") == 0);
    env->ReleaseStringUTFChars(args->nice_name, name);

    if (!is_target) {
        // 非目标 App：立即卸载模块 SO，零开销
        api->setOption(zygisk::DLCLOSE_MODULE_LIBRARY);
        return;
    }

    // 2. 保存 App 数据目录（用于写 .pine.so）
    const char *dir = env->GetStringUTFChars(args->app_data_dir, nullptr);
    app_data_dir = dir;  // 例：/data/data/com.target.package
    env->ReleaseStringUTFChars(args->app_data_dir, dir);

    // 3. 连接 Companion 进程（root 权限）
    int companion_fd = api->connectCompanion();

    // 4. 从 Companion 读取文件到内存
    int32_t file_count = 0;
    read_full(companion_fd, &file_count, 4);
    files.resize(file_count);
    for (int i = 0; i < file_count; i++) {
        int32_t size = 0;
        read_full(companion_fd, &size, 4);
        files[i].data.resize(size);
        read_full(companion_fd, files[i].data.data(), size);
    }
    close(companion_fd);
}
```

**为什么在 pre 阶段读取文件？**
- 此时还在 Zygote 进程中，可以调用 `connectCompanion()`
- fork 之后（post 阶段）就无法再连接 Companion 了

#### 阶段 3: postAppSpecialize — fork 后（加载代码 + 安装 Hook）

fork 之后在子进程中执行，此时已经是目标 App 的进程：

```cpp
void postAppSpecialize(const zygisk::AppSpecializeArgs *args) override {
    if (!is_target) return;

    // 1. 写 libpine.so 到 App 私有缓存目录
    std::string pine_path = app_data_dir + "/cache/.pine.so";
    int fd = open(pine_path.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0755);
    write_full(fd, files[3].data.data(), files[3].data.size());
    close(fd);

    // 2. 创建 InMemoryDexClassLoader（DEX 完全在内存中！）
    jclass bbClass = env->FindClass("java/nio/ByteBuffer");
    jobjectArray dexBuffers = env->NewObjectArray(3, bbClass, nullptr);
    for (int i = 0; i < 3; i++) {
        jobject buf = env->NewDirectByteBuffer(
            files[i].data.data(), files[i].data.size());
        env->SetObjectArrayElement(dexBuffers, i, buf);
    }

    jobject systemCL = env->CallStaticObjectMethod(
        env->FindClass("java/lang/ClassLoader"),
        env->GetStaticMethodID(
            env->FindClass("java/lang/ClassLoader"),
            "getSystemClassLoader", "()Ljava/lang/ClassLoader;"));

    jclass imClsLoader = env->FindClass("dalvik/system/InMemoryDexClassLoader");
    jobject dexCL = env->NewObject(imClsLoader,
        env->GetMethodID(imClsLoader, "<init>",
            "([Ljava/nio/ByteBuffer;Ljava/lang/ClassLoader;)V"),
        dexBuffers, systemCL);

    // 3. 加载 MainHook 类并调用 entry()
    jclass hookClass = (jclass) env->CallObjectMethod(dexCL,
        env->GetMethodID(env->FindClass("java/lang/ClassLoader"),
            "loadClass", "(Ljava/lang/String;)Ljava/lang/Class;"),
        env->NewStringUTF("com.yuyang.hook.MainHook"));

    env->CallStaticVoidMethod(hookClass,
        env->GetStaticMethodID(hookClass, "entry",
            "(Ljava/lang/ClassLoader;Ljava/lang/String;)V"),
        systemCL,
        env->NewStringUTF(pine_path.c_str()));

    // 4. 立即删除 .pine.so（已加载到内存，不再需要文件）
    unlink(pine_path.c_str());
}
```

### Companion 进程 — root 权限文件读取

```cpp
static void companion_handler(int fd) {
    // 以 root 权限运行！
    const char *dex_dir = "/data/adb/modules/hook_zygisk/dex";
    const char *names[] = {"classes.dex", "classes2.dex", "classes3.dex", "libpine.so"};
    int32_t count = 4;

    write_full(fd, &count, 4);
    for (int i = 0; i < count; i++) {
        char path[256];
        snprintf(path, sizeof(path), "%s/%s", dex_dir, names[i]);

        int file_fd = open(path, O_RDONLY);
        struct stat st;
        fstat(file_fd, &st);
        int32_t size = (int32_t)st.st_size;

        write_full(fd, &size, 4);
        std::vector<uint8_t> buf(size);
        read_full(file_fd, buf.data(), size);
        write_full(fd, buf.data(), size);
        close(file_fd);
    }
}

// 注册入口
REGISTER_ZYGISK_MODULE(HookModule)
REGISTER_ZYGISK_COMPANION(companion_handler)
```

**为什么需要 Companion？**
- DEX 和 SO 文件存放在 `/data/adb/modules/`（需要 root 权限）
- App 进程是普通权限，无法读取这些文件
- Companion 由 Magisk/KernelSU 框架保证以 root 运行

## 设备上的模块目录结构

```
/data/adb/modules/<module_id>/
├── module.prop              # 模块元数据（id/name/version）
├── dex/
│   ├── classes.dex          # MainHook + 业务逻辑
│   ├── classes2.dex         # Pine 框架 Java 层
│   ├── classes3.dex         # Pine 框架额外类
│   └── libpine.so           # Pine 框架 native 库
└── zygisk/
    └── arm64-v8a.so         # Zygisk C++ 模块（含 Module + Companion）
```

## 编译流程

### 1. 编译 Java Hook 模块 → DEX

```bash
cd /home/yuyang/frida-test/<app_name>

# Gradle 编译
JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
ANDROID_HOME=/home/yuyang/android-sdk \
/home/yuyang/.gradle-dist/gradle-8.9/bin/gradle :app:assembleDebug

# 从 APK 提取 DEX
cd zygisk/extracted
unzip -o ../../app/build/outputs/apk/debug/app-debug.apk 'classes*.dex'
```

### 2. 编译 Zygisk C++ 模块 → SO

```bash
NDK=/home/yuyang/android-sdk/ndk/27.0.12077973
$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android26-clang++ \
    -shared -fPIC -std=c++17 -O2 -s \
    -o zygisk/magisk/zygisk/arm64-v8a.so \
    zygisk/jni/main.cpp -llog -ldl
```

### 3. 部署到手机

```bash
ADB="${ADB:-adb}"
# 多设备时另行设置 ANDROID_SERIAL，并由 adb 自动使用该环境变量。
MODULE_ID="hook_zygisk"

# 推送文件到临时目录
$ADB push zygisk/magisk/ /data/local/tmp/$MODULE_ID/

# 进入手机 shell（WSL2 下不要用单行 su -c）
$ADB shell
su

# 复制到 Magisk 模块目录
cp -r /data/local/tmp/$MODULE_ID/* /data/adb/modules/$MODULE_ID/
chmod -R 755 /data/adb/modules/$MODULE_ID/

# 重启 App（不需要重启手机）
am force-stop <package_name>
am start <package_name>
```

**注意**：修改 DEX 后不需要重启手机，只需 force-stop 再启动 App。
修改 Zygisk SO（main.cpp）后也不需要重启手机（Zygisk 每次 fork 时重新加载 SO）。

## 隐蔽性设计要点

### 我们做了什么来避免被检测

| 措施 | 原因 |
|------|------|
| InMemoryDexClassLoader | DEX 不写入文件系统，不生成 odex/vdex |
| .pine.so 写入后立即 unlink | 加载到内存后删除文件，/proc/maps 中路径显示为 (deleted) |
| 写入 App 私有 cache/ 目录 | 避免写入 /data/local/tmp/（该目录被多数加固厂商监控） |
| 文件名以 `.` 开头 | 普通 ls 不显示 |
| 非目标 App 立即 DLCLOSE | 不影响其他 App 的性能和安全检测 |
| Pine 不修改 .text 段 | 不触发代码完整性 CRC 校验 |
| PineConfig.disableHiddenApiPolicy=false | 不修改 ART runtime 内部结构 |

### 已知的残留痕迹（理论上可被检测但实际无人检测）

1. `/proc/self/maps` 中可能存在 `(deleted)` 的 .pine.so 映射
2. InMemoryDexClassLoader 创建的 ClassLoader 可被枚举
3. Pine Hook 的 ART 方法入口指针被替换（但不修改代码）

## 与 SuKiSU Ultra 的配合

SuKiSU Ultra 是 KernelSU 的一个分支，内置 Zygisk 支持：
- **不需要 Magisk**：直接在 KernelSU 中启用 Zygisk 功能
- **更好的隐藏**：KernelSU 工作在内核层，比 Magisk 更难被检测
- **模块兼容**：与 Magisk Zygisk 模块完全兼容，同样的目录结构和 API

```
SuKiSU Ultra (内核层 Root)
    └─ 内置 Zygisk 支持
        └─ 加载 /data/adb/modules/*/zygisk/arm64-v8a.so
            └─ 我们的 Hook 模块
                └─ Pine ART Hook
```

## 常见问题排查

### App 启动后没有日志输出
1. 检查包名是否正确：`adb shell pm list packages | grep <keyword>`
2. 检查模块是否启用：`ls /data/adb/modules/<id>/disable`（如果存在 disable 文件则被禁用）
3. 检查 DEX 文件是否存在：`ls -la /data/adb/modules/<id>/dex/`
4. 检查 SO 文件架构：`file zygisk/magisk/zygisk/arm64-v8a.so`

### App 启动即崩溃（0-5s）
- Zygisk SO 编译错误或 main.cpp 有 bug
- DEX 文件损坏或缺失
- libpine.so 版本不匹配
- MainHook.java 编译错误

### App 延迟崩溃（40-50s）
- 360加固延迟检测被触发
- 检查 PineConfig.disableHiddenApiPolicy 是否为 false
- 检查是否 Hook 了 App 加载类的高频方法（参见 pitfalls.md）
