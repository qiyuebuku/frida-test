# 加固特征识别库

## 通过 SO 文件名识别加固类型

### 360加固（奇虎）
- **特征文件**: `libjiagu.so`, `libjiagu_vip.so`, `libjiagu_x86.so`
- **检测强度**: ⭐⭐⭐⭐⭐（最强）
- **关键检测**: 延迟 ART 方法入口完整性检查（~40-50s）
- **推荐策略**: Zygisk + Pine + 动态代理拦截器（不能直接 Hook App 类）
- **已验证 App**: 起点读书(com.qidian.QDReader)、同花顺

### 梆梆加固
- **特征文件**: `libbangcle_crypto_tool.so`, `libDexHelper.so`, `libSecShell.so`
- **检测强度**: ⭐⭐（弱）
- **关键发现**: 对 Hook App 类完全无反应，无延迟代码完整性检测
- **推荐策略**: Zygisk + Pine（标准配置即可，无需特殊调优）
- **已验证 App**: 盐言故事(com.zhihu.vip.android)

### 腾讯乐固 / VMP
- **特征文件**: `libdexvmp.so`, `libshella-*.so`, `libshellx-*.so`, `libtxprotect.so`
- **检测强度**: ⭐⭐⭐
- **其他安全库**: libmsaoaidsec.so(OAID), libshadowhook.so(字节ShadowHook), libbytehook.so(字节ByteHook)
- **推荐策略**: Zygisk + Pine（Hook 框架类 + 动态代理）
- **已验证 App**: 小红书(com.xingin.xhs)

### 阿里聚安全
- **特征文件**: `libsgmain.so`, `libsgsecuritybody.so`, `libmobisec.so`
- **检测强度**: ⭐⭐⭐
- **推荐策略**: Zygisk + Pine（待验证）

### 爱加密
- **特征文件**: `libDexHelper*.so`, `libexec.so`, `libexecmain.so`
- **检测强度**: ⭐⭐⭐
- **推荐策略**: Zygisk + Pine（待验证）

### 网易易盾
- **特征文件**: `libnesec.so`
- **检测强度**: ⭐⭐⭐
- **推荐策略**: Zygisk + Pine（待验证）

### 未加固
- **特征**: 无上述任何 SO 文件
- **推荐策略**: 任何方案均可，优先 Frida 快速调试

## 自动检测脚本

```bash
# 从 APK 提取 SO 文件列表
unzip -l <app.apk> | grep -E '\.so$' | awk '{print $NF}'

# 特征匹配
# jiagu → 360加固
# bangcle → 梆梆
# dexvmp|shella|txprotect → 腾讯乐固
# sgmain|sgsecurity → 阿里
# DexHelper|execmain → 爱加密
# nesec → 网易
```
