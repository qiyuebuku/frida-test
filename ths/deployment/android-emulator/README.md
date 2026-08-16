# 同花顺 Android 虚拟机运行服务

该目录固化服务器上的同花顺采集运行时：

- `ths-android-emulator.service`：启动 Android 30 x86_64 AVD；
- `ths-android-watchdog.service`：探测 Android guest shell，并在半死状态下重建完整采集栈；
- `ths-optimize-android.sh`：精简采集用户的无关 Google 后台组件、降低 Hook 日志并关闭无关系统扫描；
- `ths-collector-bridge.service`：等待 Android 启动、拉起同花顺、维护 ADB 端口转发并执行健康检查；
- `ths-collector-bridge.sh`：桥接进程实现；
- `ths-native-proxy.py`：串行转发原生请求，并在连接超时后重启 App、等待连接恢复和重放请求；
- `install-services.sh`：安装 systemd 单元。

宿主机使用两个仅回环监听的端口：

- `127.0.0.1:49300`：ADB 直连 Android 进程内 `18900`，仅供健康检查和底层调试；
- `127.0.0.1:49311`：期货独立用户（user 12）恢复代理；
- `127.0.0.1:49321`：美股排行独立用户（user 13）恢复代理；
- `127.0.0.1:49331`：美股 ETF 独立用户（user 14）恢复代理；
- `127.0.0.1:49301`：带串行控制与进程恢复能力的正式业务入口。

`smart-fund-server` 必须使用 `49301`。不要将 ADB 或这两个端口直接暴露到公网。

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

安装脚本会复制运行脚本和 systemd 单元，并立即启用、启动两个服务。更新 Hook APK 后需要重新
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
adb -s emulator-5554 shell setprop log.tag.THSHook V
```

恢复某个用户的 Google 服务时，将 `<user>` 替换为 Android user id：

```bash
adb -s emulator-5554 shell pm enable --user <user> com.google.android.gms
adb -s emulator-5554 shell pm enable --user <user> com.google.android.gsf
```

日志：

```text
/home/yuyangruan/android-runtime/logs/emulator.log
/home/yuyangruan/android-runtime/logs/collector-bridge.log
```

调试环境还在 Android 内安装了 `/data/local/tmp/frida-server` 17.6.2 x86_64。它不是采集运行时
依赖，默认不启动；仅在受控调试期间按需启动并通过 ADB loopback 转发，避免长期开放调试端口。
