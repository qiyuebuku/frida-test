# Hook APK 签名与安全覆盖部署

## 1. 为什么必须先检查签名

Android 只允许使用相同签名证书的新 APK 覆盖已安装包。即使 `applicationId`、版本号和代码完全相同，只要签名不同，`adb install -r` 就会返回 `INSTALL_FAILED_UPDATE_INCOMPATIBLE`。

Gradle 的 `assembleDebug` 默认使用当前机器的 `~/.android/debug.keystore`。每台开发机、服务器或重新初始化的 Android 环境都可能生成不同的 debug 私钥，因此“都是 debug 签名”并不代表证书相同。

Hook APK 覆盖失败时禁止直接卸载。卸载可能导致 LSPosed 模块启用状态、作用域和相关运行配置丢失，随后即使 APK 安装成功，Hook 也可能没有真正注入目标 App。

## 2. 每次覆盖部署前的强制检查

部署脚本或人工操作必须依次完成以下步骤，任何一步不满足都应停止：

1. 确认待更新的是 Hook 包，不是目标业务 App；
2. 获取设备上已安装 Hook APK 的证书 SHA-256；
3. 获取计划使用的签名私钥对应证书 SHA-256；
4. 两个指纹必须完全一致；
5. 使用正确私钥签名构建产物；
6. 再次验证已签名 APK 的证书指纹；
7. 只执行 `adb install -r`，不得在失败后自动降级为卸载重装；
8. 安装后重启目标 App，并验证 Hook 日志、健康接口和 LSPosed 作用域。

推荐的证书检查方式：

```bash
APK_SIGNER="$ANDROID_HOME/build-tools/34.0.0/apksigner"

# 已知 APK
"$APK_SIGNER" verify --print-certs hook.apk

# 已知私钥
keytool -list -v \
  -keystore "$HOOK_KEYSTORE" \
  -alias "$HOOK_KEY_ALIAS"
```

如果只能访问设备上的已安装包：

```bash
HOOK_PACKAGE=com.example.hook
APK_PATH="$(adb shell pm path "$HOOK_PACKAGE" | sed 's/^package://')"
adb pull "$APK_PATH" /tmp/installed-hook.apk
"$APK_SIGNER" verify --print-certs /tmp/installed-hook.apk
```

证书是公开信息，只能用于比对，不能从证书恢复签名私钥。

## 3. 推荐的生产签名流程

生产签名私钥必须有一个明确、持久、受权限保护的来源。不要把私钥提交到 Git，也不要把 `/tmp` 作为唯一存储位置。

如果私钥只保存在生产服务器，部署时可以临时拉取：

```bash
set -euo pipefail
TEMP_KEYSTORE="$(mktemp /tmp/hook-production.XXXXXX.keystore)"
trap 'rm -f "$TEMP_KEYSTORE" /tmp/hook-production.apk' EXIT

scp production-host:/protected/path/hook.keystore "$TEMP_KEYSTORE"
chmod 600 "$TEMP_KEYSTORE"

keytool -list -v -keystore "$TEMP_KEYSTORE" -alias "$HOOK_KEY_ALIAS"

"$APK_SIGNER" sign \
  --ks "$TEMP_KEYSTORE" \
  --ks-key-alias "$HOOK_KEY_ALIAS" \
  --ks-pass "env:HOOK_KEYSTORE_PASSWORD" \
  --key-pass "env:HOOK_KEY_PASSWORD" \
  --out /tmp/hook-production.apk \
  app/build/outputs/apk/debug/app-debug.apk

"$APK_SIGNER" verify --print-certs /tmp/hook-production.apk
adb install -r /tmp/hook-production.apk
```

正式脚本应保存预期 SHA-256，并在签名前后自动比较。不要只依赖人工观察输出。

## 4. 私钥确实丢失时

先检查原构建机、生产服务器、CI Secret、加密备份和部署脚本引用路径。已安装 APK 只能提供证书，不能恢复私钥。

确认私钥不可恢复后，才允许进行可控重装：

1. 导出或截图记录 LSPosed 中该 Hook 模块的启用状态和完整作用域；
2. 备份 Hook 自身配置、服务端口和模块文件；
3. 只卸载 Hook 包，不卸载目标业务 App；
4. 使用新的固定生产私钥安装 Hook；
5. 在 LSPosed 中重新启用模块并恢复全部作用域；
6. 按框架要求重启目标 App、Zygote 或设备；
7. 验证注入日志、Hook 健康接口和至少一条真实业务链路；
8. 将新证书指纹和私钥来源更新到对应案例文档。

禁止在没有作用域备份和恢复步骤时自动执行 `adb uninstall`。

## 5. 验收清单

- [ ] 已安装 APK 与待安装 APK 的 SHA-256 证书指纹一致；
- [ ] 签名私钥来自项目明确记录的持久位置；
- [ ] 私钥未写入 Git，临时副本退出时自动删除；
- [ ] `adb install -r` 成功，未执行卸载；
- [ ] Hook 包仍被 LSPosed 启用；
- [ ] 目标 App 仍在完整作用域中；
- [ ] Hook 注入日志、健康接口和真实调用均通过。
