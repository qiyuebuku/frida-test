# THS redroid 运行约束

redroid 11 的 Ethernet LinkProperties 会使用 `8.8.8.8`。当前生产服务器无法稳定
直连该解析器，因此由 Docker 中的 `ths-gateway` 在启动时幂等维护 Docker bridge
的 TCP/UDP 53 DNAT 规则，并将请求转发到服务器实际 DNS。THS 链路不再安装或依赖
`ths-redroid-dns-forward.service`。

容器启动后还必须执行：

```sh
settings put global captive_portal_mode 0
settings put global private_dns_mode off
```

trade 与 collector1-8 必须使用不同且可重复生成的 `android_id`。同 ID 的容器会
在行情服务器侧互相挤占会话，表现为 unified 可偶发成功但 realtime 长期超时。

同花顺首轮 unified 用真实响应确认行情路由，realtime 则完成 total client 的原生
dispatch。部分实时指标只在产生新快照时回调，不能把“当前没有回调”误判为容器
未初始化；`realtime_verified` 只记录是否已收到 callback，不作为冷启动阻塞门禁。
禁止通过 force-stop 或解锁 swipe 恢复。

## 制品化架构

`image/` 把固定版本的 Android 系统补丁、THS APK、本次提交构建并签名的 Hook APK、
启动管理器和 Docker HEALTHCHECK 内置进镜像。Dockerfile 仅负责静态制品；新数据卷
从空状态创建，只展开 Riru/LSPosed 模块。Android init 服务负责安装并校验两个 APK，
再显式启动 App、配置 LSPosed 作用域、角色门禁并主动初始化业务运行时。
创建命令只有在 Docker HEALTHCHECK 验证行情 `runtime_ready=true`（交易实例还要求
`write_ready=true`）后才返回成功，外部编排不需要补做初始化动作。

## Docker Gate

行情采集统一访问宿主机 `127.0.0.1:49350`，该端口由 `ths-gateway` 容器提供，
不再由 systemd Python 进程提供。Gate 使用 host 网络访问 collector1-8 的
`49610-49617`，负责健康检查、最少在途调度、排空和失败重试。交易端口 `49600`
仍固定连接独立的 `ths-trade` 容器，不进入无状态采集池。

部署或升级 Gate：

```bash
docker compose -f compose.gateway.yml -p ths-gateway up -d --build
docker inspect -f '{{.State.Health.Status}}' ths-gateway
curl -fsS http://127.0.0.1:49350/lb/status
```

`ths-gateway` 使用 `restart: unless-stopped`，同时通过 `NET_ADMIN` 仅维护上述 DNS
DNAT 规则。宿主机不得再启用 `ths-app-load-balancer.service`、
`ths-redroid-dns-forward.service` 或 `ths-redroid-stack.service`。

容器状态约定：

- `starting`：Android、Hook 或行情/交易运行时仍在初始化，禁止接流量。
- `healthy`：THSHook 主动初始化 CommunicationService、unified bridge 与 realtime
  total client 后返回 `runtime_ready=true`。不等待业务请求超时；交易时段有 realtime
  回调时状态中的 `realtime_verified=true`。trade 模式还要求 `write_ready=true`。
- `unhealthy`：守护器会继续执行无破坏恢复，不使用 `force-stop` 或 swipe。

## 可重复构建与生产发布

生产镜像不再包含或依赖黄金 `/data`。公开仓库保存 Dockerfile、启动初始化逻辑、
Hook 源码和 `artifacts.lock`；私有仓库
`qiyuebuku/smart-fund-deploy-artifacts` 的 `bootstrap-v1` Release 保存无法从本项目
源码生成的 THS APK、Native Bridge、Magisk 和 Riru/LSPosed 静态输入。构建前必须
同时校验文件长度和 SHA-256。

新数据卷仅展开 Riru/LSPosed 静态模块。Android 启动后由
`ths-runtime-manager.sh` 安装固定 THS APK和本次 Git SHA 构建、签名的 Hook APK，
写入 LSPosed module/scope 后执行一次确定性重启。App 数据、设备身份、缓存、Cookie
和账户状态均由空卷生成，不从其他实例克隆。

`.github/workflows/ths-redroid-production.yml` 是唯一生产发布入口：

1. PR 只执行源码构建、shell 和部署契约测试；
2. 合并 `main` 后下载并校验私有制品，从该 SHA 构建、签名 Hook；
3. 构建镜像并推送 Docker Hub，取得 registry 返回的不可变 digest；
4. 生产服务器直接从 Docker Hub 拉取该不可变 digest；公网代理由路由器基础设施维护，
   发布流程不传输镜像 tar、不维护第二份可变镜像副本；
5. 生产先用全新卷启动隔离 canary，只有达到 `healthy` 才逐个替换 collector；
6. trade 最后替换，任何实例失败立即恢复该实例原镜像；
7. 镜像的 OCI revision label 必须等于触发部署的 `github.sha`。

禁止从开发机运行生产 rollout，禁止生产使用可变 tag。交易密码、账户 seed 和 token
只通过生产服务器只读 Secret 注入，绝不进入 Git、私有制品或镜像层。

交易凭据统一在 GitHub `production` Environment Secrets 的 Web UI 维护：
`THS_TRADE_ACCOUNT`、`THS_TRADE_BROKER`、`THS_TRADE_QSID`、
`THS_TRADE_PASSWORD`。`main` 的自建 Runner 在构建前将它们原子同步为权限 0600 的
服务器运行时文件；容器只读挂载。Hook 先按 qsid 从固定 APK 的券商库校验券商名称，
再用资金账号创建/复用官方 `fzr` 账户对象，最后只执行一次密码登录。任何 Secret
缺失、券商与 qsid 不匹配、账户未激活或 `write_ready=false` 都会阻止部署成功。

新增采集实例：

```bash
./add-instance.sh --name ths-collector9 --mode collector \
  --adb-port 5569 --http-port 49618
./inspect-instance.sh ths-collector9
```

`add-instance.sh` 默认最多等待 300 秒。只有容器达到 `healthy` 才以 0 退出；初始化
失败会输出容器状态、运行阶段和末尾日志并以非零退出，因此调用方不得在命令成功前
把实例加入负载均衡。

交易实例必须显式使用 `--mode trade`。默认 `--trade-init secrets` 要求传入
`--account-seed-secret`；可选 token 与交易密码分别通过 `--token-secret`、
`--password-secret` 只读挂载。`--trade-init existing` 仅允许数据卷已经有有效交易
会话的恢复场景。交易凭据不进入 Dockerfile、镜像层或命令行参数。

`--password-levels 1|2` 是显式策略声明：一级表示交易密码；二级表示账户 seed 中
还包含券商通讯密码材料。当前 Hook 的账户 seed 与交易密码接口分别承载这两层，
容器不会输出密码内容。

旧 redroid 交易数据目录只允许通过显式迁移参数接管：

```bash
./add-instance.sh --name ths-trade --mode trade \
  --adb-port 5560 --http-port 49600 --image yuyangruan/ths-redroid:1.2.47 \
  --trade-init existing --data-dir /home/yuyangruan/redroid-poc/data-trade
```

脚本会先验证目录中同时存在同花顺和 Hook 的应用数据；普通 collector 或 secrets
初始化模式不能挂载未知非空目录，避免把生产账户数据误制成采集模板。
