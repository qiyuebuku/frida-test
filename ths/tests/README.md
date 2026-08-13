# 同花顺逆向本地单测

本目录统一保存 `ths` 项目的本地单元测试。部署前必须从项目根目录执行：

```bash
python scripts/ths_dev.py predeploy
```

`predeploy` 会运行本目录测试、`smart-fund-server` 中与 `THSSTREAM/1` 契约直接相关的测试，并执行 Hook APK 增量编译。任一步失败都会阻止后续 `deploy-device`。

测试范围包括：

- `THSSTREAM/1` 握手、订阅、事件和 `request_id` 响应关联；
- 真实响应 Fixture 回放；
- Android 宿主代理的超时、恢复和单通道约束；
- 服务端常驻流 Client 与 THS Client 路由契约。

Fixture 和模拟器只用于快速反馈，不能替代真机与生产虚拟机验收。
