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

  - id: normalize-openapi
    name: 整理 OpenAPI 数据
    description: 导入 Apifox 前整理目录归属、tags、x-apifox-folder 和请求体示例
    input: text
    executor: claude
    estimated_time: 10
    args:
      - name: input
        description: OpenAPI JSON 文件路径
        required: true
      - name: output
        description: 整理后的 OpenAPI JSON 输出路径
        required: true

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

**立即用 Bash 执行 `apifox_client.py`**，根据参数中的 token、project_id、input 等构建命令行。涉及 Tamar Console 社区模块时，必须先整理和核验 OpenAPI，再导入。

```bash
# 基本格式
python apifox_client.py --token <token> <command> <project_id> [args...]

# 导出
python apifox_client.py --token TOKEN export PROJECT_ID -o ./output
python apifox_client.py --token TOKEN export 'APIFOX_URL' -o ./output  # URL 自动解析 projectId+moduleId

# 整理 OpenAPI（导入前建议固定执行）
python apifox_client.py normalize-openapi ./raw.json -o ./normalized.json

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
整理: `normalize-openapi <input> -o <output>`，默认使用 Tamar 社区模块规则
导入: `--endpoint-overwrite OVERWRITE_EXISTING|AUTO_MERGE|KEEP_EXISTING|CREATE_NEW` `--schema-overwrite` `--delete-unmatched` `--update-folder` `--module-id` `--branch-id`

## Tamar Console 社区模块导入流程

当目标是 Tamar Console 社区模块，例如 `http://127.0.0.1:4523/export/openapi/7141388/0?moduleId=7363228`，按下面流程执行：

1. 先导出当前 Apifox 全量 OpenAPI，保留已有接口和目录状态。
   ```bash
   python apifox_client.py --token TOKEN export 'APIFOX_URL' -o /tmp/apifox_export
   ```
2. 基于导出的全量文件整理目录和示例。
   ```bash
   python apifox_client.py normalize-openapi /tmp/apifox_export/openapi_export_7141388.json -o /tmp/apifox_normalized.json
   ```
3. 如果只新增少量接口，把新增接口合并进整理后的全量文件，再导入；不要拿局部文件配 `--delete-unmatched`。
4. 全量导入时才允许使用 `--delete-unmatched`，用于清理错误目录下的旧接口归属。
   ```bash
   python apifox_client.py --token TOKEN import-openapi 'APIFOX_URL' /tmp/apifox_normalized.json \
     --endpoint-overwrite OVERWRITE_EXISTING \
     --schema-overwrite OVERWRITE_EXISTING \
     --update-folder \
     --delete-unmatched
   ```
5. 导入后必须再次导出核验，检查接口目录和请求示例。
   ```bash
   python apifox_client.py --token TOKEN export 'APIFOX_URL' -o /tmp/apifox_verify
   ```

## Tamar Console 目录规范

社区模块只允许这些顶层目录：

- `用户端 (C端)`
- `管理后台`
- `内部接口`

常见接口归档：

- `/api/community/events*` -> `用户端 (C端)/社区活动`
- `/api/community/carousel*` -> `用户端 (C端)/轮播图`
- `/api/incentive/targeting*` -> `用户端 (C端)/投放管理`
- `/api/console/incentive*` -> `管理后台/激励管理`
- `/api/console/*` -> `管理后台` 或已有后台子目录

禁止创建这些根目录：

- `用户端（C端）`：中文括号是错的，必须用 `用户端 (C端)`
- `用户投放`：应使用 `用户端 (C端)/投放管理`
- `激励管理`：不能作为根目录；应使用 `管理后台/激励管理`
- `社区活动`：应使用 `用户端 (C端)/社区活动`
- `轮播图`：应使用 `用户端 (C端)/轮播图`

导入后根目录约束：

- `用户端 (C端)` 根目录下只允许少量没有更细分类的 C 端接口；新增模块应优先创建子目录
- `管理后台` 根目录下不应直接挂具体业务接口；如激励、视频、活动、预算等都必须进入对应子目录
- `内部接口` 可以作为顶层目录直接承载内部预算等接口，除非项目已有更细内部子目录

## 请求示例规则

POST/PUT/PATCH 请求示例必须写入 `requestBody.content.<media-type>.examples`，不要只写在 schema 的 `examples` 里；Apifox 可能不会把 schema-level examples 展示为请求 Body 示例。

投放决策接口必须保留这个 POST body 示例：

```json
{
  "placement": "bottom_right_card",
  "card_codes": [
    "m1_retention_offer",
    "minimax2.6",
    "测试卡片"
  ],
  "scene": "home",
  "client_context": {
    "route": "/home",
    "locale": "zh-CN",
    "client_version": "2.9.0",
    "device_type": "web"
  }
}
```

## 本次踩坑沉淀

- Apifox 会按 tag/x-apifox-folder 创建目录；扁平 tag 会污染根目录。
- `用户端（C端）` 和 `用户端 (C端)` 是两个不同目录，括号必须完全一致。
- `--update-folder` 需要和完整、正确的 `x-apifox-folder` 一起使用；导入后必须再导出核验。
- `--delete-unmatched` 很危险，只能用于“当前模块全量导出再整理后”的全量导入，不能用于局部 OpenAPI 文件。
- 空目录可能不会因为接口移动立刻消失；先保证导出结果中错误目录下没有接口，再让用户刷新 UI 或手动删除空目录。
