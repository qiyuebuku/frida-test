---
name: openwrt-manager
display_name: OpenWrt 路由器管理
icon: router
description: 管理 OpenWrt 路由器（10.168.1.3），包括 OpenClash/mihomo 代理、Passwall2、CA 证书、sing-box 等
category: infrastructure
commands:
  - id: status
    name: 查看路由器状态
    description: 查看 OpenClash 运行状态、磁盘空间、各服务状态
    input: none
    executor: claude
    estimated_time: 15

  - id: switch-node
    name: 切换代理节点
    description: 通过 mihomo API 切换 OpenClash 代理组的节点
    input: text
    executor: claude
    estimated_time: 10
    args:
      - name: group
        description: 代理组名（如 其他流量、能用就行）
        required: true
      - name: node
        description: 节点名
        required: true

  - id: update-subscription
    name: 更新订阅
    description: 重新下载 x-air/魔戒 订阅配置并重启 OpenClash
    input: text
    executor: claude
    estimated_time: 60

  - id: debug-proxy
    name: 诊断代理问题
    description: 测试代理连通性、检查日志、定位 TLS/DNS/路由问题
    input: text
    executor: claude
    estimated_time: 30

  - id: manage-ca-certs
    name: 管理 CA 证书
    description: 检查/更新路由器 CA 证书，解决 TLS 验证失败问题
    input: none
    executor: claude
    estimated_time: 30
---

# OpenWrt 路由器管理 Skill

## 路由器基本信息

| 项目 | 值 |
|------|-----|
| IP | `10.168.1.3` |
| 用户 | `root` |
| 密码 | 无 |
| SSH Key | `/mnt/c/Users/阮雨阳/.ssh/id_rsa` |
| 固件 | OpenWrt eSir Buddha V7 (2022) |
| 架构 | x86_64 |
| 内核 | 5.15.78 |
| WAN | `192.168.100.2` → 网关 `192.168.100.1`（天威视讯宽带）|
| LAN | `10.168.1.0/24` |

## SSH 连接方式

```bash
# 从 WSL2 连接（无需密码）
ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 root@10.168.1.3 "命令"

# 带 SSH Key（如果需要）
ssh -i /mnt/c/Users/阮雨阳/.ssh/id_rsa -o StrictHostKeyChecking=no root@10.168.1.3 "命令"
```

## 已安装的核心组件

| 组件 | 版本 | 路径 | 说明 |
|------|------|------|------|
| **OpenClash** | 0.47.088 | LuCI 插件 | 代理管理界面 |
| **mihomo** | 1.19.25 | `/etc/openclash/core/clash_meta` → `/usr/bin/mihomo` | Clash.Meta 内核（支持 AnyTLS） |
| **sing-box** | 1.13.12 | `/usr/bin/sing-box` | ⚠️ 路由引擎在本路由器上不可用 |
| **Xray** | 26.3.27 | Passwall2 使用 | Passwall2 的默认内核 |
| **Passwall2** | - | LuCI 插件 | 当前 disabled，sing-box 有问题 |

## 管理界面

- **LuCI**: `http://10.168.1.3`
- **OpenClash**: `http://10.168.1.3/luci/admin/services/openclash`
- **MetaCubeXD**: `http://10.168.1.3:9090/ui/metacubexd/`
- **Smart Selector 日志**: `http://10.168.1.3:9091/`（实时测速/决策日志，10s 自动刷新）
- **API 密钥**: `k1FuD3ty`

## OpenClash 常用操作

### 查看状态
```bash
ssh root@10.168.1.3 "/etc/init.d/openclash status"
ssh root@10.168.1.3 "tail -20 /tmp/openclash.log"
```

### 通过 API 切换节点
```bash
# 查看所有代理组
ssh root@10.168.1.3 "curl -s http://127.0.0.1:9090/proxies -H 'Authorization: Bearer k1FuD3ty'"

# 切换节点（group=代理组名，name=节点名）
ssh root@10.168.1.3 "curl -s -X PUT http://127.0.0.1:9090/proxies/其他流量 -H 'Content-Type: application/json' -H 'Authorization: Bearer k1FuD3ty' -d '{\"name\":\"日本1←A2·1倍·AnyTLS#13699\"}'"
```

### 重启 OpenClash
```bash
ssh root@10.168.1.3 "/etc/init.d/openclash restart"
```

### 更新订阅（重新下载配置）
```bash
ssh root@10.168.1.3 "curl -sL -H 'User-Agent: clash.meta' 'https://api.icp-verify.dynamic.hwsdn.com:666/v2/package/mwS6vQXef8kdTtugXHPh86S54X4weFNj/auto' > /etc/openclash/config/x-air.yaml && /etc/init.d/openclash restart"
```

## 订阅信息

当前配置已合并四份订阅到 `/etc/openclash/config/x-air.yaml`（共 126 个代理节点）。

| 名称 | URL | 格式 | 节点数 |
|------|-----|------|--------|
| x-air | `https://api.icp-verify.dynamic.hwsdn.com:666/v2/package/mwS6vQXef8kdTtugXHPh86S54X4weFNj/auto` | ⚠️ UA 决定内容（见下方） | ~78 |
| eeox | `https://api.eeox.net/api/v1/client/subscribe?token=d707c3c05251a62d769b957e5f2dbb55` | clash YAML | 11 |
| 魔戒 | `https://msub.xn--m7r52rosihxm.com/api/v1/client/subscribe?token=10bdb377866f4d2c4b76b2577567330a` | clash YAML (vmess + hysteria2) | 32 |
| Tamaredge (2 号线路) | `https://lsg2.tamaredge.host:15438/s/clashMetaProfiles/654438183f8e9fc27cd878ba5dcf801f` | proxy-providers (vless/vmess/trojan) | 5 |

> **1 号线路 `lsg1.tamaredge.host:24114` 的 ChatGPT 证书报错**（`ERR_CERT_COMMON_NAME_INVALID`）——1 号线路的出口 IP 被 OpenAI 封锁。已切换到 2 号线路。
> **2 号线路测速结果**（2026-06-23）：
> - 能访问 ChatGPT：`VLESS_TCP/TLS_Vision` (875ms)、`vless_reality_vision` (1387ms)
> - 不能访问 ChatGPT：trojan_tcp、VLESS_WS、VMess_WS（TLS 握手失败）
> - 推荐：**VLESS_TCP/TLS_Vision**（访问 ChatGPT 最快）

**合并规则**：
- 过滤掉 info/dummy 节点（server: 0.0.0.0、剩余流量、套餐到期等）
- `能用就行` 自动选择组：所有节点（排除黑名单地区：香港、马来西亚、俄罗斯）
- `指定节点` 手动选择组：所有节点（含香港）
- 所有订阅均需使用 `clash.meta` UA 获取

### x-air 订阅的 UA 陷阱

**不同 User-Agent 返回完全不同的内容！**

| UA | 格式 | 协议 | AnyTLS |
|----|------|------|--------|
| `v2rayN/9.99` | Base64 分享链接 | vless + hysteria2 (26个) | ❌ 无 |
| `clash.meta` | YAML 配置 | vless + hysteria2 + **anytls** (78个) | ✅ 52个 |
| `sing-box` | JSON 配置 | vless + hysteria2 + **anytls** | ✅ 52个 |

**必须用 `clash.meta` UA 获取订阅才能拿到 AnyTLS 节点。**

## 节点命名规则

```
香港1←A2·0.3倍·AnyTLS#13699
│     │   │       │
│     │   │       └─ 协议: AnyTLS / HY2 / Vision(VLESS)
│     │   └─ 倍率: 0.3=省流量, 1=原价, 3=贵
│     └─ 线路: A1/A2/H1/P1 (不同入口)
└─ 地区
```

## 代理组结构

```
流量 → 规则匹配
 ├─ apple.com/icloud.com → 苹果服务（选国内服务即可）
 ├─ 国内域名/IP → 国内服务（保持 DIRECT）
 ├─ 内网 → 直连
 └─ 其他所有 → 其他流量 → 能用就行(自动) / 指定节点(手动)
```

- **能用就行**: `select` 类型，由 smart-selector.py 脚本控制切换（见下方）
- **指定节点**: 手动选择，包含所有节点（含香港）
- **其他流量**: 默认选「能用就行」

## 智能节点选择器 (smart-selector.py)

**位置**: `/root/smart-selector.py`（本地副本: `.claude/skills/openwrt-manager/smart-selector.py`）
**状态**: cron 每分钟运行一次，3 轮后决策

### 工作原理
1. 每分钟对所有节点并发测速（asyncio 协程，20 并发，~11 秒完成 64 节点）
2. 累积 3 轮测速数据后决策：
   - 计算每个节点 3 轮平均延迟
   - 过滤不稳定节点（标准差 > 均值 50%）
   - 最佳节点比当前节点延迟低 20%+ 才切换
3. 重置计数器，开始下一个 3 轮周期

### 关键配置
```python
MAX_ROUNDS = 3          # 3 轮后决策
IMPROVE_THRESHOLD = 0.20  # 20% 改善才切换
STABILITY_THRESHOLD = 0.50  # 标准差/均值 > 50% = 不稳定
MAX_CONCURRENT = 20     # 最大并发测速数
```

### 常用操作
```bash
# 查看日志
ssh root@10.168.1.3 "tail -30 /tmp/smart-selector/selector.log"

# 查看当前状态
ssh root@10.168.1.3 "cat /tmp/smart-selector/history.json"

# 手动触发一轮
ssh root@10.168.1.3 "python3 /root/smart-selector.py"

# 重置状态（强制下一轮做决策）
ssh root@10.168.1.3 "rm -rf /tmp/smart-selector"

# 查看当前使用的节点
ssh root@10.168.1.3 "curl -s 'http://127.0.0.1:9090/proxies/%E8%83%BD%E7%94%A8%E5%B0%B1%E8%A1%8C' -H 'Authorization: Bearer k1FuD3ty' | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get(\"now\"))'"
```

### 注意事项
- `能用就行` 已改为 `select` 类型，mihomo 不再自动切换
- 脚本通过 mihomo API 控制，与 mihomo 不冲突
- 状态文件在 `/tmp/smart-selector/`（内存，重启后清空，脚本自动重建）
- 更新订阅后配置会被覆盖，需要重新将 `能用就行` 改为 `select` 类型

## 已知问题和踩过的坑

### 坑 1: CA 证书过期导致 TLS 验证失败（最重要！）

**现象**: 所有代理协议连接失败，mihomo 日志报 `x509: certificate signed by unknown authority`
**原因**: 路由器固件自带 CA 证书是 2021 年的（`ca-certificates 20210119`），缺少新 CA
**解决**: 已手动替换为 Mozilla 官方 2025 CA bundle
**文件**: `/etc/ssl/certs/ca-certificates.crt` 和 `/etc/ssl/cert.pem`
**注意**: 如果 `opkg upgrade ca-certificates` 会覆盖回旧版本！更新后需要重新替换。

### 坑 2: sing-box 路由引擎完全失效

**现象**: sing-box 的 socks/http 入站接受连接但不路由到任何出站，连 `block` 出站都无法阻止流量。所有流量直连穿透。
**原因**: sing-box 1.13.12 在此路由器（eSir Buddha V7 固件，内核 5.15.78）上路由引擎不工作。官方下载同版本同 hash，问题依旧。
**影响**: Passwall2 的 sing-box 节点（包括 AnyTLS）完全不可用。
**解决**: 使用 mihomo (Clash.Meta) 替代，通过 OpenClash 管理。

### 坑 3: curl --noproxy '*' 会覆盖 -x 代理设置

**现象**: 测试代理时 `curl --noproxy '*' -x http://proxy http://target` 请求不经过代理
**原因**: `--noproxy '*'` 优先级高于 `-x`，curl 会直连
**解决**: 测试代理时**不要加** `--noproxy '*'`

### 坑 4: x-air v2rayN 格式不含 AnyTLS

**现象**: Passwall2 订阅后没有 AnyTLS 节点
**原因**: x-air 机场对 v2rayN UA 只返回 vless+hysteria2，AnyTLS 只在 Clash/sing-box 格式中提供
**解决**: OpenClash 使用 `clash.meta` UA 获取完整节点列表

### 坑 5: 路由器存储空间紧张

**现象**: overlay 只剩几 MB，安装包失败
**原因**: 353MB overlay，sing-box(65MB) + mihomo(45MB) + 其他组件占用很多
**建议**: 安装/更新前先 `df -h /` 检查空间，清理 /tmp 下的测试文件和旧包

## 磁盘空间管理

```bash
# 检查空间
ssh root@10.168.1.3 "df -h /"

# 查看大文件
ssh root@10.168.1.3 "du -sh /usr/bin/* /etc/openclash/core/* 2>/dev/null | sort -rn | head -10"

# 清理 /tmp
ssh root@10.1.68.1.3 "rm -rf /tmp/*.log /tmp/*.json /tmp/*.gz /tmp/*.tar.*"
```

## CA 证书更新方法

如果证书被覆盖回旧版：
```bash
# 从本机下载最新 Mozilla CA bundle
curl -sL 'https://curl.se/ca/cacert-2025-02-25.pem' -o /tmp/cacert-new.pem
# 上传到路由器
scp -i /mnt/c/Users/阮雨阳/.ssh/id_rsa /tmp/cacert-new.pem root@10.168.1.3:/tmp/
# 替换
ssh root@10.168.1.3 "cp /tmp/cacert-new.pem /etc/ssl/certs/ca-certificates.crt && cp /tmp/cacert-new.pem /etc/ssl/cert.pem"
```
