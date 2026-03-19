# 案例：起点读书 (com.qidian.QDReader)

## 基本信息
- **包名**: com.qidian.QDReader
- **加固**: 360加固（libjiagu_vip.so）
- **难度**: ⭐⭐⭐⭐⭐（最高）
- **详细文档**: /home/yuyang/frida-test/qidian/docs/QIDIAN_REVERSE.md
- **核心代码**: /home/yuyang/frida-test/qidian/app/src/main/java/com/yuyang/qdhook/MainHook.java（3800+ 行）

## 逆向目标
- 100% 免 UI 程序化获取搜索、书籍详情、章节目录、章节内容、段落评论
- 章节解密（bll.v 方法链触发）
- VIP 章节购买 + 解密

## 失败方案记录
| 方案 | 失败原因 |
|------|---------|
| Frida (原版/HLuda/strongR) | 360加固检测 .text 段完整性 |
| Frida Gadget + Zygisk | Gadget 注入但 attach 挂起 |
| LSPosed/Xposed | 空模块就触发 exit_self |
| 直接 Pine Hook OkHttp 方法 | 40-50s 延迟检测崩溃 |

## 成功方案
Zygisk + Pine + OkHttp 动态代理拦截器

**关键突破**:
1. `PineConfig.disableHiddenApiPolicy = false` — 不修改 ART 结构
2. 动态代理替代 Pine Hook OkHttp — 规避延迟代码完整性检测
3. 从 `druidv6.if.qidian.com/argus` 域名精确捕获 OkHttpClient
4. bll.v L→K→R 反射调用触发章节解密

## 核心 API 端点
- 搜索: POST `/argus/api/v2/booksearch/searchbooks`
- 书籍详情: GET `/argus/api/v3/bookdetail/get`
- 章节目录: GET `/argus/api/v3/chapterlist/chapterlist`（双格式）
- 免费章节: GET `/argus/api/v2/bookcontent/safegetcontent`
- VIP 章节: POST `/argus/api/v4/bookcontent/getvipcontent`
- 段评: GET `/argus/api/v3/comments/getchapterrepagesummary`

## RPC 通道
- 协议: LocalSocket (`qdhook_rpc`)
- 转发: `adb forward tcp:12345 localabstract:qdhook_rpc`
- 命令: search, bookDetail, getChapterList, getChapterContent, fetchChapter

## Python 工具
- `scripts/qidian_api.py` — RPC 客户端封装
- `scripts/batch_fetch.py` — 批量采集 + 断点续传 + --fix 修复
