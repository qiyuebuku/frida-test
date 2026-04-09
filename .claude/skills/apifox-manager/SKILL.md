---
name: apifox-manager
display_name: Apifox 文档管理
icon: api
description: 通过 Apifox 开放 API 管理项目文档，支持导出、修改、导入 OpenAPI/Postman 数据
category: tools
commands:
  - id: export
    name: 导出 OpenAPI 数据
    description: 从 Apifox 项目导出 OpenAPI/Swagger 格式的 API 文档数据
    input: text
    executor: claude
    estimated_time: 30
    args:
      - name: token
        description: Apifox API 访问令牌
        required: true
      - name: project_id
        description: Apifox 项目 ID 或 URL（如 http://127.0.0.1:4523/export/openapi/7141388/0?moduleId=7363228，自动解析 projectId 和 moduleId）
        required: true
      - name: oas_version
        description: OAS 版本（2.0 / 3.0 / 3.1），默认 3.1
        required: false
      - name: format
        description: 导出格式（JSON / YAML），默认 JSON
        required: false
      - name: scope
        description: 导出范围（ALL / SELECTED_TAGS / SELECTED_FOLDERS），默认 ALL
        required: false
      - name: tag_names
        description: 指定导出的标签名列表（逗号分隔），scope=SELECTED_TAGS 时使用
        required: false
      - name: exclude_tags
        description: 排除的标签名列表（逗号分隔）
        required: false

  - id: import-openapi
    name: 导入 OpenAPI 数据
    description: 将 OpenAPI/Swagger 格式数据导入到 Apifox 项目，支持文件路径或 URL
    input: text
    executor: claude
    estimated_time: 30
    args:
      - name: token
        description: Apifox API 访问令牌
        required: true
      - name: project_id
        description: Apifox 项目 ID 或 URL（自动解析 projectId 和 moduleId）
        required: true
      - name: input
        description: OpenAPI 文件路径或 URL
        required: true
      - name: endpoint_overwrite
        description: 接口覆盖策略（OVERWRITE_EXISTING / AUTO_MERGE / KEEP_EXISTING / CREATE_NEW），默认 OVERWRITE_EXISTING
        required: false
      - name: schema_overwrite
        description: 模型覆盖策略（同上），默认 OVERWRITE_EXISTING
        required: false
      - name: delete_unmatched
        description: 是否删除未匹配的资源（true/false），默认 false
        required: false

  - id: import-postman
    name: 导入 Postman Collection
    description: 将 Postman Collection v2 格式数据导入到 Apifox 项目
    input: text
    executor: claude
    estimated_time: 30
    args:
      - name: token
        description: Apifox API 访问令牌
        required: true
      - name: project_id
        description: Apifox 项目 ID 或 URL（自动解析 projectId 和 moduleId）
        required: true
      - name: input
        description: Postman Collection JSON 文件路径
        required: true
      - name: endpoint_overwrite
        description: 接口覆盖策略，默认 OVERWRITE_EXISTING
        required: false
---

# Apifox 文档管理 Skill

## 执行方式

**立即用 Bash 执行 `apifox_client.py`**，根据参数中的 token、project_id、input 等构建命令行。

```bash
# 基本格式
python apifox_client.py --token <token> <command> <project_id> [args...]

# 导出
python apifox_client.py --token TOKEN export PROJECT_ID -o ./output
python apifox_client.py --token TOKEN export 'APIFOX_URL' -o ./output  # URL 自动解析 projectId+moduleId

# 导入 OpenAPI（文件路径或 URL）
python apifox_client.py --token TOKEN import-openapi PROJECT_ID ./file.json
python apifox_client.py --token TOKEN import-openapi PROJECT_ID ./file.json --endpoint-overwrite AUTO_MERGE

# 导入 Postman
python apifox_client.py --token TOKEN import-postman PROJECT_ID ./collection.json
```

**关键**：
- project_id 支持纯数字 ID 或 Apifox URL（如 `http://127.0.0.1:4523/export/openapi/7141388/0?moduleId=7363228`）
- input 参数如果是 `uploads/xxx` 相对路径，直接使用即可（相对于当前工作目录）
- 收到参数后立即构建并执行命令，不要输出文档

## 可用参数

导出: `--oas-version 2.0|3.0|3.1` `--format JSON|YAML` `--scope ALL|SELECTED_TAGS|SELECTED_FOLDERS` `--tag-names` `--exclude-tags` `--folder-ids` `--module-id` `--branch-id`
导入: `--endpoint-overwrite OVERWRITE_EXISTING|AUTO_MERGE|KEEP_EXISTING|CREATE_NEW` `--schema-overwrite` `--delete-unmatched` `--module-id` `--branch-id`
