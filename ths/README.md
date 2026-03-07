# 同花顺逆向记录

## App 信息
- 包名：`com.hexin.plat.android`
- 版本：11.47.03 (5191)
- 大小：~160MB (11 个 DEX 文件)
- 加固类型：**360 加固** (`libjiagu.so`)

## Zygisk Hook 状态
- 模块 ID：`thshook_zygisk`
- TAG：`THSHook`
- 360 加固绕过：✅ 成功
- OkHttp Interceptor 注入：✅ 成功
- App 正常运行：✅

## 核心发现：行情数据传输架构

### HTTP API（OkHttp 传输）
同花顺通过 OkHttp 传输的数据主要是：
- 首页 UI 配置 (`eq.10jqka.com.cn`)
- 用户状态 (`dq.10jqka.com.cn`)
- 埋点统计 (`stat.10jqka.com.cn`)
- APM 监控 (`apm.hexin.cn`)
- 自选股同步 (`cs.10jqka.com.cn`)

**行情数据不走 OkHttp！**

### 自定义 TCP 二进制协议（Native 层）
行情数据使用 **GMS (Gateway Market Service)** 协议：
- 核心库：`libhdp.so` (6MB) - Hexin Data Protocol
- 网络层：`libhgm.so` - TCP Session 管理
- 加密层：`libhcrypt-2.4.so` + `libhssl-2.1.so` - HSSL 加密
- 序列化：**Protocol Buffers** (`gms/gateway/rtp.pb.cc`)
- SM2 国密加密

### 行情页面
- 行情主页是 **WebView**：`eq.10jqka.com.cn/hxapp/hqMarket/index.html`
- 通过 JavaScript Bridge 与 Native 层通信
- Native 层通过 GMS 协议获取实时行情

## 公开 HTTP 行情 API（已验证 ✅）

### 域名
- `d.10jqka.com.cn` - 行情数据主域名

### API 端点

#### 1. 实时行情快照
```
GET http://d.10jqka.com.cn/v2/line/hs_{code}/01/today.js
```
- 返回格式：JSONP
- 字段说明：
  - `1`: 日期 (20260226)
  - `7`: 开盘价
  - `8`: 最高价
  - `9`: 最低价
  - `11`: 最新价/收盘价
  - `13`: 成交量（股）
  - `19`: 成交额（元）
  - `name`: 股票名称
  - `dt`: 数据时间戳
  - `marketType`: 市场类型

#### 2. 日K线（最近 140 根）
```
GET http://d.10jqka.com.cn/v4/line/hs_{code}/01/last.js
```
- 返回 JSONP，`data` 字段用分号分隔每根 K 线
- 格式：`日期,开盘,最高,最低,收盘,成交量,成交额,...`

#### 3. 全量历史日K
```
GET http://d.10jqka.com.cn/v6/line/hs_{code}/01/all.js
```
- 返回全量压缩数据（增量编码 + priceFactor）

#### 4. 周K线
```
GET http://d.10jqka.com.cn/v4/line/hs_{code}/11/last.js
```

#### 5. 月K线
```
GET http://d.10jqka.com.cn/v4/line/hs_{code}/21/last.js
```

#### 6. 5分钟K线
```
GET http://d.10jqka.com.cn/v4/line/hs_{code}/00/last.js
```

### 周期代码
| 代码 | 周期 |
|------|------|
| 00 | 5分钟 |
| 01 | 日K |
| 02 | 15分钟（待确认） |
| 11 | 周K |
| 21 | 月K |

### 股票代码格式
| 前缀 | 市场 | 示例 |
|------|------|------|
| `hs_` | A股（沪深） | `hs_600519`（茅台）、`hs_000001`（上证指数） |
| `hk_` | 港股 | `hk_00700`（腾讯） |

### 请求头要求
```
User-Agent: Mozilla/5.0 ...
Referer: http://stockpage.10jqka.com.cn/
```

### 反爬机制
- Web 端需要 `hexin-v` Cookie 参数（JS 加密生成）
- 移动端 API (`d.10jqka.com.cn`) 目前**不需要** hexin-v
- 需要 Referer 头
- 有速率限制

## 关键 SO 文件
| 文件 | 大小 | 用途 |
|------|------|------|
| `libjiagu.so` | 1.6MB | 360 加固 |
| `libhdp.so` | 6MB | 行情协议引擎 (GMS/HDP) |
| `libhgm.so` | 383KB | TCP Session 管理 |
| `libhcrypt-2.4.so` | 777KB | 加密库 |
| `libhssl-2.1.so` | 130KB | SSL 库 |
| `libhxsecurity.so` | 2.4MB | 安全库 |
| `libhtoken.so` | 129KB | Token 管理 |
| `libthsSign.so` | 48KB | 签名库 |
| `libflutter.so` | 8.4MB | Flutter 引擎 |
| `libapp.so` | 10MB | Flutter 业务代码 |
| `libxhook_lib.so` | 320KB | 内置 xhook 框架 |

## 结论

1. **行情数据获取推荐方案**：直接调用 `d.10jqka.com.cn` 公开 HTTP API
   - 无需 App Hook
   - 支持实时行情、历史K线、多周期
   - A股全覆盖

2. **不推荐 Hook 行情数据**：
   - 行情走 Native TCP 协议 (GMS/Protobuf/HSSL)
   - 逆向难度极高，需要分析 6MB 的 libhdp.so
   - 即使成功，也不如公开 API 稳定

3. **Hook 适用场景**：
   - 捕获 OkHttp 层的业务 API（搜索、自选股、资讯等）
   - 分析签名机制 (`libthsSign.so`)
   - 获取需要登录态的数据
