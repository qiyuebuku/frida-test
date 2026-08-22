# THS 生产部署

公开入口只有 [`deploy.sh`](./deploy.sh)，并且通常由工作区根目录的统一
`/home/yuyang/frida-test/deploy.sh` 调用。`internal/` 下均为运行时、systemd、配置、生产端实现
或维护工具，部署人员不应直接逐个执行。源代码通过 Git revision 同步到生产机，不使用 rsync
覆盖工作目录；APK、AVD、初始化模板、密钥和环境配置不进入 Git。

```bash
# 推荐：工作区统一自动增量部署
./deploy.sh production

# 只部署 THS Hook 或 THS 运行时
./deploy.sh production --component ths-hook
./deploy.sh production --component ths-runtime
```

该目录固化服务器上的同花顺采集运行时：

- `ths-android-emulator.service`：启动 Android 30 x86_64 AVD；
- `ths-android-watchdog.service`：探测 Android guest shell，并在半死状态下重建完整采集栈；
- `ths-trade-bridge.service`：维护 user 0 交易进程和宿主机 49500 直连转发；
- `ths-optimize-android.sh`：精简采集用户的无关 Google 后台组件、降低 Hook 日志并关闭无关系统扫描；
- `ths-collector-bridge@*.service`：在 Android users 10–17 中拉起八个采集进程、维护独立 ADB 转发并执行健康检查；
- `ths-collector-bridge.sh`：桥接进程实现；
- `ths-native-proxy.py`：串行转发原生请求，并在连接超时后重启 App、等待连接恢复和重放请求；
- `install-services.sh`：安装 systemd 单元。

生产拓扑为同一台 `emulator-5556` 上的 1+8 隔离：user 0 是交易专用实例，
users 10–17 是八个采集实例。交易直连 49500，不进入采集负载均衡；采集统一经
49350/49352 分发。

宿主机使用以下仅回环监听的端口：

- `127.0.0.1:49300`：primary 采集实例（user 17）ADB 直连设备端 `18980`；
- `127.0.0.1:49311`：期货独立用户（user 12）恢复代理；
- `127.0.0.1:49321`：美股排行独立用户（user 13）恢复代理；
- `127.0.0.1:49331`：美股 ETF 独立用户（user 14）恢复代理；
- `127.0.0.1:49301`：primary 采集恢复代理；正式采集入口为 `49350`。
- `127.0.0.1:49500`：交易实例（user 0）直连设备端 `18900`。

`smart-fund-server` 的采集配置必须使用 `49350/49352`，交易配置必须使用
`49500`。不要将 ADB 或这些端口直接暴露到公网。

### 弹性实例池实验

`ths-app-load-balancer.py --elastic-pool` 与
`ths-android-pool-manager.service` 提供按等待队列扩容、显式 drain 和空闲缩容能力。
该模式默认关闭，安装脚本不会启用 pool manager。生产实时服务会建立约 275 个长期订阅，
不能把这些订阅全部压到单个 App；2026-08-16 的生产验证出现 owner 健康 502 和流重连后
已回滚。再次启用前必须实现按实例订阅容量分片，并通过持续回放验证。默认网关仍连接并
均匀分配到全部后端。

桥接服务启动和进程恢复后会自动进入同花顺“行情”页，使该页面注册的
`UnifiedRequestBridge` 可用于股票排行协议；该状态不依赖人工操作。

首次安装或更新服务：

```bash
sudo ./install-services.sh
```

### 全新机器一键部署

正式环境不再从交易 user 0 复制 App 数据，也不依赖部署人员点击开户或新手引导页面。
部署制品包含 Android SDK、已配置 Magisk/LSPosed 作用域的 AVD 基线、同花顺 APK、Hook APK，
以及一个由“未登录过交易账号”的采集实例导出的脱敏黄金模板。大文件和 App 私有数据禁止提交
到 Git，流水线通过受保护的制品仓库分发并用 SHA-256 固定版本。

先在受控模板机上从已完成 UI 初始化、且从未登录交易账号的采集用户导出模板：

```bash
THS_TEMPLATE_SOURCE_USER_ID=12 \
  ./export-collector-template.sh /secure-artifacts/ths-collector-template.tar.gz
```

导出器拒绝 user 0，并在打包前删除数据库、WebView/Cookie、缓存、账号、委托和 Hook 密钥等
状态。将 `ths-deployment.env.example` 复制为目标机 `/etc/smart-fund/ths-deployment.env`，填写
五项制品路径及校验和后，目标机只需执行：

```bash
sudo ./internal/remote/provision-host.sh /etc/smart-fund/ths-deployment.env
```

脚本恢复 SDK/AVD、安装 APK、创建并校验 users 10–17、恢复采集模板及 SELinux 标签、启动
1 个交易实例和 8 个采集实例，并以交易 runtime status、采集 LB status 和最终息屏作为验收
门禁。AVD 制品必须保持名称 `ths-futures`，且预先安装 Magisk/LSPosed 并为 Hook 配置目标 App
作用域；部署后的真实 Hook 健康检查会阻止作用域失效的制品进入服务。

CI/CD 使用的外层发布包固定包含 `android-sdk.tar.gz`、`ths-futures-avd.tar.gz`、`ths.apk`、
`ths-hook.apk`、`ths-collector-template.tar.gz` 和 `MANIFEST.sha256`。流水线先校验整个发布包，
`internal/remote/install-bundle.sh` 再校验包内每个文件，安装到不可变的 SHA-256 版本目录并自动生成
`/etc/smart-fund/ths-deployment.env`，随后调用同一个新机部署入口。这样迁移机器不再依赖服务器
上的历史文件或人工记忆。

发布系统使用 `build-deployment-bundle.sh` 生成该外层包；输入 AVD 必须来自已停机、已清除
运行锁文件的镜像构建任务，禁止直接归档正在运行的生产 AVD。

`ths-disable-bluetooth.sh` 是仓库内唯一的蓝牙优化实现。安装脚本会把它复制到
`/home/yuyangruan/android-runtime/bin/`，模拟器 systemd 单元在每次 AVD 启动完成后通过
`ExecStartPost` 自动执行，对全部 Android 用户关闭蓝牙及蓝牙 MIDI 服务。禁止只在生产服务器
手工修改运行时副本。

正式部署的最后一步固定执行 `ths-screen-off.sh`：先把前台用户恢复为交易 user 0，再发送幂等的
`KEYCODE_SLEEP`，并通过 `dumpsys power` 确认屏幕确实为 `OFF`。屏幕未关闭会使部署失败，避免
AVD 长期渲染造成额外 GPU/CPU 占用。流水线在服务和 LB 验收后还会再次执行该脚本作为最终门禁。

GitHub Actions 工作流 `.github/workflows/ths-android-production.yml` 会在所有目标为 `main`
的 PR 上执行校验；代码合并并推送到受保护的 `main` 后，同一工作流会在测试通过后自动部署
该次事件唯一对应的 `github.sha`。生产部署不提供本地入口或手动 workflow dispatch，回滚也
必须通过 revert PR 合并到 `main`。配置受保护的 `production` Environment 及
`THS_PRODUCTION_HOST`、`THS_PRODUCTION_SSH_PORT`、`THS_PRODUCTION_SSH_USER`、
`THS_PRODUCTION_SSH_PRIVATE_KEY`、`THS_PRODUCTION_SSH_KNOWN_HOSTS`、
`THS_PRODUCTION_SUDO_PASSWORD` 和 `DEPLOY_GIT_URL` 后，
由 GitHub 托管 Runner 自动执行同一个版本化部署入口。`THS_PRODUCTION_SSH_KNOWN_HOSTS`
必须由受信任渠道预先采集并核对，流水线禁止运行时接受未知主机密钥。生产 Environment 中的
SSH 用户只授予执行部署所需的 sudo 权限；个人运维账号仍可独立登录排障。

GitHub 仓库必须额外启用以下保护，工作流文件本身不能代替仓库权限配置：

- `main` 禁止直接 push 和 force push，只允许通过 PR 合并；
- PR 必须通过 `validate` 以及项目要求的其他测试；
- `production` Environment 只允许 `main` 部署，并保存全部生产 Secret；
- 开发机不保存 GitHub Actions 使用的生产 SSH 私钥；根部署入口在非
  `push main` 的 GitHub Actions 上下文中只允许 `--dry-run`；
- 回滚通过 revert PR 进入 `main`，不允许手动选择未进入 `main` 的 revision。

首次启用自动部署前，需要从可信终端核对生产服务器 SSH host key，并把完整
`known_hosts` 行写入 `THS_PRODUCTION_SSH_KNOWN_HOSTS`。流水线不会运行
`ssh-keyscan` 自动信任当次网络返回的主机身份。

安装脚本会复制运行脚本和 systemd 单元，并立即启用、启动对应服务。更新 Hook APK 后需要重新
安装 APK，并重启同花顺进程；只有修改 AVD、systemd 单元或运行脚本时才需要重启模拟器服务。

### Hook APK 签名约束

生产 Hook `com.yuyang.thshook` 的证书 SHA-256 固定为：

```text
9505d29aca6006eef0fe473b68e4eea03afd41019cf5435a5ee6963262559dbf
```

生产签名私钥保存在服务器 `/home/yuyangruan/.android/debug.keystore`，alias 为 `androiddebugkey`。本机默认 debug keystore 与生产证书不同，禁止直接使用 `./gradlew installDebug` 或未经重新签名的 `app-debug.apk` 覆盖生产。

覆盖前必须分别检查生产私钥和待安装 APK 的证书指纹；两者都与上述指纹一致后才能执行 `adb install -r`。遇到 `INSTALL_FAILED_UPDATE_INCOMPATIBLE` 必须停止，禁止自动卸载 Hook，以免破坏 LSPosed 模块启用状态和作用域。完整流程见：

```text
/home/yuyang/frida-test/.claude/skills/reverse-app-skill/knowledge/apk-signing-deployment.md
```

运行状态：

```bash
sudo systemctl status ths-android-emulator ths-collector-bridge
curl http://127.0.0.1:49300/health
curl http://127.0.0.1:49301/health
curl http://127.0.0.1:49311/health
curl http://127.0.0.1:49321/health
curl http://127.0.0.1:49331/health
```

原生指标调用示例：

```bash
curl -H 'Content-Type: application/json' \
  --data '{"key":"sjdp_temperature_hs","requestParam":"sjdp_temperature_hs data","requestChannel":"sjdp_temperature_hs_channel"}' \
  http://127.0.0.1:49301/native/realtime
```

原生 UnifiedRequest 调用示例：

```bash
curl -H 'Content-Type: application/json' \
  --data '{"onlineId":"marketLabel","protocolId":1002,"pageId":6000,"requestType":262144,"requestDic":"marketcode=16\r\naction=subscribe\r\nkey=mobiledpyd\r\nstockcode=1A0001","cancelRequestDic":"marketcode=16\r\naction=unsubscribe\r\nkey=mobiledpyd\r\nstockcode=1A0001"}' \
  http://127.0.0.1:49301/native/unified
```

`/native/unified` 直接构造同花顺 App 内的 `HummerUnifiedRequestBridge` 请求模型，不创建
WebView。请求在 App 主进程中串行执行，回调完成后会取消订阅并释放资源。服务端
`THSClient.get_native_market_anomalies()` 和 `THSClient.get_native_call_auction()` 负责业务参数及
Base64 正文解析。

取消订阅会异步占用共享 frame/page 路由，因此 Hook 在清理后保留 800ms 静默期。代理还会为
每次 Unified 调用生成唯一 `onlineId`。若原生层返回 `errorCode=-131` 或连接直接断开，代理会重启
同花顺进程、等待行情连接初始化，并重放一次原请求。

`ths-collector-bridge` 每 15 秒检查一次接口。连续三次失败时会重启同花顺并等待 Hook 恢复；
虚拟机进程退出时则由 `ths-android-emulator` 自动重启。

仅检查 QEMU 进程或 `adb get-state` 无法识别“transport 在线但 guest shell
卡死”的状态。watchdog 每 30 秒执行一次最长 15 秒的
`adb shell echo ths-watchdog-ok`；连续 3 次失败后停止实时流、负载均衡和全部
bridge，重启模拟器，等待 boot completed，再串行恢复 8 个 Android 用户。
完整恢复后进入 15 分钟冷却，避免重启风暴。普通业务 ADB 命令使用 60 秒超时，
不与短探针共用超时配置。

模拟器在 8 个 Android 用户中仅保留同花顺采集所需能力。启动完成后会禁用 GMS、GSF、
Google 搜索和输入法，关闭定位与后台 Wi-Fi/BLE 扫描，并将 `THSHook` 的生产 logcat 等级
设为 `W`。所有 bridge 恢复后 watchdog 会让显示器进入休眠，避免 SwiftShader 持续绘制。
模拟器 unit 使用 `CPUQuota=300%` 限制异常峰值；正常负载低于该值时不会限速。

临时恢复 Hook 详细日志：

```bash
adb -s emulator-5556 shell setprop log.tag.THSHook V
```

恢复某个用户的 Google 服务时，将 `<user>` 替换为 Android user id：

```bash
adb -s emulator-5556 shell pm enable --user <user> com.google.android.gms
adb -s emulator-5556 shell pm enable --user <user> com.google.android.gsf
```

日志：

```text
/home/yuyangruan/android-runtime/logs/emulator.log
/home/yuyangruan/android-runtime/logs/collector-bridge.log
```

调试环境还在 Android 内安装了 `/data/local/tmp/frida-server` 17.6.2 x86_64。它不是采集运行时
依赖，默认不启动；仅在受控调试期间按需启动并通过 ADB loopback 转发，避免长期开放调试端口。
