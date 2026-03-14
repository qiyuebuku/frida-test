# 截屏助手 App 功能升级方案

> 从「截屏工具」升级为「智能分析助手」— 截图提交即走，后台自动分析，随时查看结果。

## 设计文档

| 文档 | 内容 |
|------|------|
| [01-overview.md](01-overview.md) | 整体架构与核心思路 |
| [02-async-task-system.md](02-async-task-system.md) | 异步任务系统（数据库 + API + 进度追踪） |
| [03-app-ui-design.md](03-app-ui-design.md) | App UI 改造（任务列表 + 详情页 + 进度展示） |
| [04-server-processing-pipeline.md](04-server-processing-pipeline.md) | 服务端处理流水线（OCR → 结构化 → Claude 分析） |
| [05-skill-deployment.md](05-skill-deployment.md) | fund-trade Skill 远端部署方案 |
| [06-implementation-plan.md](06-implementation-plan.md) | 分阶段实施计划 |

## 参考文档（历史）

| 文档 | 内容 |
|------|------|
| [参考-截屏助手初版技术方案.md](参考-截屏助手初版技术方案.md) | App 初版设计（悬浮窗、长截图、OCR） |
| [参考-基金交易架构重构方案.md](参考-基金交易架构重构方案.md) | fund-trade 从本地到 C/S 架构的重构 |
| [参考-同花顺API逆向分析.md](参考-同花顺API逆向分析.md) | 同花顺 Zygisk Hook + API 认证逆向 |
