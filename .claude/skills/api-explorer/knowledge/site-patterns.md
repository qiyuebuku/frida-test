# 网站 API 模式库

探索过的网站的 API 格式、认证方式、反爬特征汇总。下次探索同类网站时可以直接参考。

---

## 财经数据网站

### 东方财富 (eastmoney.com)

**API 特征：**
- 行情数据：`push2.eastmoney.com/api/qt/` — 无需认证，返回 JSON
- 数据中心：`datacenter-web.eastmoney.com/api/data/v1/get` — 通用查询接口，参数 `reportName` 决定数据类型
- 股吧：`guba.eastmoney.com` — API 已改版（只返回 security ID），**必须用 SSR HTML 解析**

**编码：** UTF-8
**反爬：** 无 WAF，无频率限制，最友好的财经数据源

### 新浪财经 (sina.com.cn)

**API 特征：**
- 实时行情：`hq.sinajs.cn/list=sh600519,...` — JSONP 格式（`var hq_str_xxx="..."`），需 `Referer: https://finance.sina.com.cn/`
- K线：`money.finance.sina.com.cn/.../CN_MarketData.getKLineData` — JSON 数组
- 新闻：`feed.mix.sina.com.cn/api/roll/get` — JSON
- 板块资金流：`vip.stock.finance.sina.com.cn/.../MoneyFlow.ssl_bkzj_bk` — JSON 数组

**编码：** GBK（行情数据），UTF-8（其他）
**反爬：** 需要 Referer header，无 WAF

### 腾讯证券 (gtimg.cn / qq.com)

**API 特征：**
- A股行情：`web.sqt.gtimg.cn/q=sh600519` — GBK 编码，`~` 分隔，88个字段
- 港股行情：`web.sqt.gtimg.cn/q=r_hk00700` — 同上，78个字段
- 美股行情：`web.sqt.gtimg.cn/q=usAAPL` — 同上（**不带 .OQ 后缀**）
- K线：`web.ifzq.gtimg.cn/appstock/app/fqkline/get` — JSON（A股部分失效，港美股正常）
- 资金流向：`proxy.finance.qq.com/cgi/cgi-bin/fundflow/hsfundtab` — JSON，需 `Referer: https://gu.qq.com/`
- 板块信息：`proxy.finance.qq.com/ifzqgtimg/appstock/app/stockinfo/plateNew` — 需 Referer
- 国际期货：`qt.gtimg.cn/q=hf_GC,hf_SI,...` — GBK，逗号分隔
- 指数简版：`qt.gtimg.cn/q=s_sh000001` — 精简格式，12个字段

**编码：** GBK（行情），UTF-8（`web.sqt.gtimg.cn/utf8/`）
**反爬：** 部分接口需 `Referer: https://gu.qq.com/`，无 WAF

**已下线的接口（不要浪费时间）：**
- `MoneyFlow`、`capitalflow`、`Stock`、`StockInfo`、`Finance`、`plate/getList` — 全部返回 "Controller not found"

### 雪球 (xueqiu.com)

**API 特征：**
- 热门话题：`/hot_event/list.json?count=10`
- 热股排行：`stock.xueqiu.com/v5/stock/hot_stock/list.json?size=10&_type=10&type=10`
- 7×24快讯：`/statuses/livenews/list.json?since_id=-1&max_id=-1&count=20`

**反爬：** 阿里云 WAF + httpOnly cookie，**最严格**
- 首页返回 WAF JS 挑战页（`aliyun_waf` meta 标签）
- 关键 cookie `xq_a_token` 为 httpOnly，`document.cookie` 拿不到
- 纯 HTTP + cookie 失败（TLS 指纹检测）
- **破解方案：** Playwright `context.cookies()` 获取 httpOnly cookie + `curl_cffi` 模拟 Chrome TLS
- 热帖 (`/statuses/hot/listV2.json`) 和个股讨论即使用上述方案也被 WAF 二次拦截

### 人民银行 (pbc.gov.cn)

**API 特征：**
- 纯 HTML 页面，无 JSON API
- 列表页：标准 `<a>` + `<span>` 日期格式，正则解析
- 统计数据：`attachDir/YYYY/MM/时间戳.htm` — GBK 编码 HTML 表格

**反爬：** 部分页面对 headless 浏览器返回 len=146 的空页面，需用 api-explorer 浏览器渲染
**编码：** 混合（页面 UTF-8，附件 htm GBK）

### 同花顺 (10jqka.com.cn)

**API 特征：**
- 基金/行情数据：`fund.10jqka.com.cn/` — JSON，无需认证
- 问财：`www.iwencai.com/customized/chart/get-robot-data` — POST JSON，需 `Cookie: v={hexin_v}`
- 新闻：`news.10jqka.com.cn/tapp/` — JSON

**反爬：** 问财有 Token 级验证码锁定（连续请求触发后 token 永久失效），需 ≥60 秒间隔

---

## 政府网站

### 通用特征
- 无 API，纯 HTML 解析
- 需要 `User-Agent` header
- 部分页面动态渲染需浏览器

### CFETS (chinamoney.com.cn)
- 汇率历史：`/ags/ms/cm-u-bk-ccpr/CcprHisNew` — JSON API
- 需要 `X-Requested-With: XMLHttpRequest` + `Referer`
- 单次查询限 90 天，超过需分段

---

## 通用模式识别

### 数据格式快速判断

| 响应特征 | 格式 | 处理方式 |
|---------|------|---------|
| `{"code":0,"data":...}` | JSON API | 直接解析 |
| `var xxx="..."` | JSONP/JS 变量 | 正则提取引号内容 |
| `~` 分隔的长字符串 | 腾讯行情格式 | split("~") |
| `,` 分隔 + GBK | 新浪行情/期货 | GBK decode + split(",") |
| HTML `<table>` | 政府/传统网站 | HTMLParser 或正则 |
| `callback(...)` | JSONP | 去掉函数名括号 |

### 编码检测
```python
# 通用编码处理
resp = await client.get(url)
try:
    text = resp.content.decode("utf-8")
except UnicodeDecodeError:
    text = resp.content.decode("gbk", errors="replace")
```
