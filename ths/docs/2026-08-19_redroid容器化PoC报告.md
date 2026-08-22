# redroid 容器化 PoC 报告（2026-08-19）

> 目标：验证用 Docker 跑 Android（redroid）替代生产 QEMU/AVD 承载同花顺采集栈的可行性。
> 执行位置：生产服务器（119.23.227.187）上的隔离目录 `~/redroid-poc`，容器名
> `ths-redroid-poc`，全程未触碰生产 AVD（emulator-5556）与 systemd 服务。

## 结论摘要

| 层 | 结果 | 说明 |
|---|---|---|
| 内核前置（binder） | ✅ | `binder_linux` 已加载并持久化，重启自动生效 |
| Docker + redroid 底座 | ✅ | Android 11 x86_64，boot 约 5 秒（AVD 为分钟级） |
| ARM 翻译（libndk） | ✅ | 与生产 AVD 同源翻译层，abilist 配置正确 |
| 同花顺 App 运行 | ✅ | arm64-only APK 正常启动，native 库经翻译层加载 |
| Magisk（redroid-script 集成） | ⚠️ 半工作 | magiskd + /sbin tmpfs 正常；模块系统/官方 Zygisk 未初始化 |
| Zygisk 注入 → LSPosed → ths-hook | ❌ | 三套实现均卡在 system_server 集成崩溃，见下文 |

**总评：底座可行，注入层是当前唯一阻塞项。**

## 已固化资产（服务器上可复用）

- 镜像 `redroid/redroid:11.0.0_ndk_magisk`（2.17GB）：官方 11.0.0-latest + Magisk Delta 30.6
  + libndk_translation，由 ayasa520/redroid-script 构建，可重复构建。
- 内核配置：`/etc/modules-load.d/redroid.conf` + `/etc/modprobe.d/redroid-binder.conf`。
- 容器启动参数（见本文末附录），`/data` 为 bind mount（named volume 会导致部分状态丢失，
  redroid-doc issue #379 有同类记录）。
- DNS 修复：resolvectl 上游改为 223.5.5.5/网关（原 8.8.8.8 被网络侧拦截，出网本身正常）。

## 关键验证记录

1. **boot 与隔离**：容器 5 秒 boot_completed；与生产 emulator-5556 在同一 adb server 下
   并存互不干扰（localhost:5555 vs emulator-5556）。
2. **同花顺 App**：生产制品 `ths.apk`（arm64-v8a only，183MB）`adb install` 成功，
   dex2oat 19.9s 后 `am start .LogoEmptyActivity` 正常拉起进程；`libyoga.so` 从
   `lib/arm64` 经翻译层加载，App 自导航至主界面 `.Hexin`。
   注意：install 返回 Success 时 dexopt 仍在后台，立即 monkey 启动会失败，需等编译完成。
3. **Magisk 集成链修复**（对 redroid-script 集成的必要修正，容器可写层 `/system` 内）：
   - bootanim.rc 中所有 `exec u:r:su:s0 ...` 的 seclabel 必须去掉——redroid SELinux 为
     Disabled，带 seclabel 的 exec/service 在 init 下静默失败；
   - 三条 `magiskpolicy --live` exec 必须删除——SELinux 关闭时该命令 abort
     （fopen /sys/fs/selinux/policy 失败），会卡死 rc 链；
   - 修复后 setup-sbin 正常：init 的 mount ns 中 `/sbin` 为 magisk tmpfs，`/sbin/magisk`
     可用，`magisk --sqlite` 可改 settings（zygisk=1 写入成功）。
   - 局限：magiskd 由容器入口进程（而非 Android init）拉起，模块系统从未初始化
     （`--install-module` 报 "Incomplete Magisk install"），官方 Zygisk 注入不会发生。

## Zygisk 注入层失败记录（三组实验）

| 实现 | 结果 | 证据 |
|---|---|---|
| Magisk Delta 30.6 官方 zygisk | 注入不发生 | zygote64 maps 无任何 magisk/zygisk 痕迹；magiskd 为容器 PID1 子进程，未执行 zygisk 初始化 |
| ReZygisk v1.0.0（手动部署 zygiskd/ptrace64） | 注入成功但 system_server 100% 崩 | `zygiskd64-zygisk_lsposed` companion 正常 fork（模块被识别加载）；system_server `ClassCastException: BinderProxy → PermissionManagerService`（PMS 本地 binder 变代理）crash loop |
| LSPosed 1.9.2 / 1.8.6 对照 | 1.9.2 叠加 native 崩；1.8.6 修复 native 但不救 ClassCast | 1.9.2：zygote64 内 `NewDirectByteBuffer` 容量参数为指针值（jint 溢出）SIGABRT；1.8.6：zygote64 正常启动，system_server 仍 ClassCast（隔离实验证实责任在 ReZygisk 层：移除 LSPosed 模块后仍崩） |
| ZygiskNext v1.4.5 | 未完成注入 | daemon 无法常驻：adb shell 会话退出即被清理；注册为 init service（去 seclabel）后 init 仍拒绝启动且无日志 |

社区同类问题佐证：redroid-doc issue #10（LSPosed works but Zygisk No，closed 无解）、
issue #379、JingMatrix/Vector discussions #395（ZygiskNext 系与 LSPosed 系注入崩溃为
已知类别问题）。

容器行为提示：zygote crash loop 后 init 有重启上限，容器会停在 boot_completed=1 但
zygote/system_server 僵死的半死态——生产 watchdog 的"adb shell 探活 + 重建"策略在
redroid 上同样必要（探活命令可复用）。

## 后续路径（按性价比排序）

1. **NeoZygisk + Vector（JingMatrix 系）**：Vector 官方推荐组合，社区活跃。这是下次
   实验的首选；部署方式可复用本次的模块目录手动部署 + zygote 重启验证法。
2. **redroid 12/13 基座重试**：ayasa520 脚本支持到 13；更高版本 Android 的 zygisk 生态
   修复更多。需要重验同花顺兼容性（libndk 翻译质量随版本变化）。
3. **定制 system 镜像直嵌 LSPosed**（无 zygisk 依赖）：把 lspd 注入点直接打进镜像
   /system，绕开运行时注入。工程量最大但确定性最高，且与生产"AVD 基线内置作用域"
   的制品化思路同构。
4. **混合路线**：底座换 redroid（拿密度/启动速度收益），注入层暂留 AVD——短期不现实
   （同一套 Hook 必须同环境），仅作为架构演进参考。

## 环境差异备忘（迁移时必须逐项核对）

- SELinux：redroid 默认 Disabled；生产 AVD 内核策略环境不同。所有依赖 seclabel/SELinux
  的 rc 组件需适配（本次已趟坑，修法见上）。
- Magisk 状态判定：`which magisk` 不可靠（不在 PATH），用 `/sbin/magisk`；模块目录
  `/data/adb/modules` 对 root adb 可见。
- zygote 控制：`stop/start zygote` 在 redroid 里无效，需 `kill -9 $(pidof zygote64)`
  交由 init 重启。
- 多用户：本次未验证 redroid 的 `pm create-user` 多实例拓扑（1+8 生产形态的对应物），
  属于后续验证项。
- App 制品：同花顺 APK 仅 arm64-v8a，redroid 必须配置 native bridge（libndk 已验证）。

## 附录：容器启动命令（已验证）

```bash
docker run -itd --privileged --name ths-redroid-poc \
  -v /home/yuyangruan/redroid-poc/data11:/data \
  --memory 6g --cpus 4 -p 127.0.0.1:5555:5555 \
  redroid/redroid:11.0.0_ndk_magisk \
  ro.product.cpu.abilist=x86_64,arm64-v8a,x86,armeabi-v7a,armeabi \
  ro.product.cpu.abilist64=x86_64,arm64-v8a \
  ro.product.cpu.abilist32=x86,armeabi-v7a,armeabi \
  ro.dalvik.vm.isa.arm=x86 \
  ro.dalvik.vm.isa.arm64=x86_64 \
  ro.enable.native.bridge.exec=1 \
  ro.vendor.enable.native.bridge.exec=1 \
  ro.vendor.enable.native.bridge.exec64=1 \
  ro.dalvik.vm.native.bridge=libndk_translation.so \
  ro.ndk_translation.version=0.2.3 \
  androidboot.redroid_gpu_mode=guest \
  androidboot.use_memfd=1
```

注：容器可写层的 bootanim.rc 修正（去 seclabel/删 magiskpolicy）在 `docker restart`
后保留，`docker rm` 后丢失；固化为镜像时需 `docker commit` 或在镜像构建层修改。

---

# 最终成果（2026-08-19 下午更新）：完整链路打通 + 1+8 容器拓扑上线

## 注入层最终方案：Riru 直嵌（放弃 ZygiskNext 系与 Magisk 完整集成）

`ro.dalvik.vm.native.bridge=libriruloader.so` 让 ART 在 zygote 启动时标准加载
Riru loader，不依赖 ptrace/SELinux/Magisk 模块系统。redroid（SELinux Disabled）
需要以下四项定制，缺一不可：

1. **rirud.apk 补丁**（apktool 反编译 → smali 修改 → 重打包）：
   `DaemonSocketServerThread` 的连接鉴权原本强制对端 SELinux 上下文
   `u:r:zygote:s0`，无 SELinux 环境永远被拒。补丁改为 uid=0 即放行。
2. **启动包装脚本** `/system/bin/start-rirud.sh`（由 bootanim.rc 的
   `ths-rirud` service 在 post-fs-data 拉起）：
   - rirud 的 `addAssetPath("rirud.apk")` 与 `module.prop` 都是 cwd 相对路径，
     必须先 `cd /data/adb/modules/riru-core`（官方 Magisk 安装即如此，
     init 服务的 cwd 是 `/`）；
   - 把 `libriru.so` 镜像到 `/sbin/.magisk/modules/riru-core/lib64/`（loader
     通过 rirud socket 拿 magisk tmpfs 路径后从该镜像路径 dlopen）；
   - 把 LSPosed 模块 so 放 `/sbin/.magisk/modules/riru_lsposed/riru/lib64/liblspd.so`
     （带 .so 后缀；rirud 的 collectModules 扫描该布局）；
   - 以绝对路径启动 LSPosed daemon（`${0%/*}` 相对路径会让
     `System.load("./daemon.apk!/lib/...")` 抛 UnsatisfiedLinkError）；
   - 启动参数 `riru.Daemon 0 /sbin libndk_translation.so`——第二参必须是
     magisk tmpfs（/sbin），第三参是真 native bridge（不能 getprop，因为本环境
     prop 从 boot 起就是 libriruloader.so）。
3. **LSPosed 模块手工安装**（无 Magisk 模块系统，等价 customize.sh）：
   `lib/x86_64/liblspd.so → system/lib64/`、`riru/lib64/liblspd` 占位、
   `bin/dex2oat{32,64}` 解出、**daemon.apk 与 dex2oat 的
   `placeholder_/dev/..............`（14 字符占位）等长替换为 14 位 hex 路径**
   ——长度不等会整体平移 zip 偏移导致 ClassNotFoundException。
4. **干净 /data**：经历过 zygote crash 风暴的 data 卷会让 arm64 App 50ms 级
   静默死亡（无 Java 栈无 tombstone），换新卷即愈；同镜像同参数下新旧卷
   表现不同，排查时优先怀疑数据卷状态。

镜像链：`redroid:11.0.0_ndk_magisk → ndk_riru → riru2..riru6`（当前
`redroid:11.0.0_riru6` = riru2 的 rc + 修复版 wrapper + 无 /system 直写依赖）。

## 验证通过的完整链路

```
redroid 容器 → libriruloader(ART native bridge) → rirud(socket 鉴权补丁)
→ libriru.so 注入 zygote64 → NativeBridgeItf 转发 libndk_translation
→ LSPosed riru 模块加载 → lspd daemon → THSHook 注入 arm64 同花顺
→ OkHttp/UnifiedRequest Hook 生效 → 18900 /auth HTTP 200
```

关键日志：`Riru v26.1.7 in zygote64` → `original native bridge:
libndk_translation.so` → `module loaded: riru_lsposed@lspd` →
`LSPosed attached to com.hexin.plat.android` → `Runtime architecture: aarch64`。

## 生产拓扑：一容器一实例（1 交易 + 8 采集）

**放弃单容器多用户**：redroid 镜像 `Max users: 1`（`fw.show_multiuserui` 锁死），
且多用户丢失容器化弹性价值。实测单实例容器 headless 仅 ~1.05GiB，一容器一实例
的 9 容器拓扑总内存 ~9.7GiB（服务器 94GiB），扩缩容 = docker 层面操作。

| 实例 | 容器 | adb | App 端口(宿主) | 数据卷 |
|---|---|---|---|---|
| 交易 | ths-trade | 5560 | 127.0.0.1:49600 | data-trade |
| 采集×8 | ths-collector1..8 | 5561-5568 | 127.0.0.1:49610-49617 | data-collectorN |

- 每容器 `--memory 2g --cpus 2`，实测 ~1.05GiB/容器。
- 数据卷由验证完备的基线卷（THS+hook+LSPosed scope 已配）`cp -a` 克隆。
- 验收：9/9 容器 THS 进程存活、18900 监听、宿主端口 /auth 全部 HTTP 200。
- PoC 端口用 49600 段，与生产 49300/49500 完全隔离；生产服务未动。

## 固化脚本（服务器 `~/redroid-poc/stack/`）

- `deploy-redroid-stack.sh`：克隆卷 + 起 9 容器
- `install-lspd-module.sh`：LSPosed 模块手工安装（含 14 位等长 patch）
- `enable-scope.py`：LSPosed modules_config.db 写 scope（thshook → hexin, user 0）
- `start-rirud.sh`：注入引导包装脚本（镜像内 /system/bin/start-rirud.sh 源）
- `start-all-ths.sh`：批量启动 + 端口验收
