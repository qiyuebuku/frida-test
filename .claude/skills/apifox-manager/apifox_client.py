#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Apifox Open API 客户端

封装 Apifox 开放 API 的导入/导出操作，供 AI Skill 调用。

用法:
    python apifox_client.py export <project_id> [选项]
    python apifox_client.py normalize-openapi <input> -o <output>
    python apifox_client.py import-openapi <project_id> [选项]
    python apifox_client.py import-postman <project_id> [选项]
    python apifox_client.py list-projects
"""

import os
import re
import sys
import json
import argparse
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# 清理代理（WSL2 环境）
for k in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"]:
    os.environ.pop(k, None)

import httpx

# ========== 配置 ==========

BASE_URL = "https://api.apifox.com/v1"
API_VERSION = "2024-03-28"
ACCESS_TOKEN = ""  # 由 CLI --token 或 APIFOX_TOKEN 环境变量设置

# 默认输出目录
DEFAULT_OUTPUT_DIR = Path(__file__).parent / "output"

TAMAR_FOLDER_ALIASES = {
    "社区活动": "用户端 (C端)/社区活动",
    "轮播图": "用户端 (C端)/轮播图",
    "用户投放": "用户端 (C端)/投放管理",
    "用户端（C端）": "用户端 (C端)",
    "激励管理": "管理后台",
    "视频管理": "管理后台/视频管理",
    "创作者管理": "管理后台/创作者管理",
    "活动管理": "管理后台/活动管理",
}

TAMAR_FORBIDDEN_ROOT_FOLDERS = set(TAMAR_FOLDER_ALIASES)

TARGETING_DECISION_EXAMPLE = {
    "placement": "bottom_right_card",
    "card_codes": [
        "m1_retention_offer",
        "minimax2.6",
        "测试卡片",
    ],
    "scene": "home",
    "client_context": {
        "route": "/home",
        "locale": "zh-CN",
        "client_version": "2.9.0",
        "device_type": "web",
    },
}


def parse_apifox_url(url: str) -> dict:
    """解析 Apifox URL，提取 project_id 和 module_id

    支持格式:
      http://127.0.0.1:4523/export/openapi/7141388/0?moduleId=7363228
      https://app.apifox.com/project/7141388
    """
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    result = {}

    # /export/openapi/{projectId}/... 格式
    m = re.search(r'/(?:export/openapi|project)/(\d+)', parsed.path)
    if m:
        result["project_id"] = m.group(1)

    if "moduleId" in params:
        result["module_id"] = int(params["moduleId"][0])

    return result


def get_headers():
    """构建公共请求头"""
    if not ACCESS_TOKEN:
        print("错误: 未设置 APIFOX_TOKEN 环境变量", file=sys.stderr)
        print("用法: export APIFOX_TOKEN='your_token_here'", file=sys.stderr)
        sys.exit(1)
    return {
        "X-Apifox-Api-Version": API_VERSION,
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


def _infer_tamar_folder(path: str) -> str:
    """Infer the top-level folder when exported data has no Apifox folder."""
    if path.startswith("/api/console/"):
        return "管理后台"
    if path.startswith("/api/community/events"):
        return "用户端 (C端)/社区活动"
    if path.startswith("/api/community/carousel"):
        return "用户端 (C端)/轮播图"
    if path.startswith("/api/incentive/targeting"):
        return "用户端 (C端)/投放管理"
    if path.startswith("/api/console/incentive"):
        return "管理后台/激励管理"
    if path.startswith("/api/community/") or path.startswith("/api/incentive/"):
        return "用户端 (C端)"
    return "内部接口"


def _rebuild_tags_from_operations(data: dict) -> None:
    tag_names = []
    seen = set()
    for item in data.get("paths", {}).values():
        for operation in item.values():
            if not isinstance(operation, dict):
                continue
            for tag in operation.get("tags") or []:
                parts = tag.split("/")
                for idx in range(1, len(parts) + 1):
                    name = "/".join(parts[:idx])
                    if name not in seen:
                        seen.add(name)
                        tag_names.append(name)
    data["tags"] = [{"name": name} for name in tag_names]


def _promote_request_examples(operation: dict) -> bool:
    """Move schema-level examples to requestBody.content examples for Apifox UI."""
    changed = False
    request_body = operation.get("requestBody")
    if not isinstance(request_body, dict):
        return changed

    content = request_body.get("content")
    if not isinstance(content, dict):
        return changed

    for media in ("application/json", "multipart/form-data", "application/x-www-form-urlencoded"):
        media_obj = content.get(media)
        if not isinstance(media_obj, dict):
            continue
        if media_obj.get("examples") or media_obj.get("example"):
            continue
        schema = media_obj.get("schema")
        if not isinstance(schema, dict):
            continue
        schema_examples = schema.get("examples")
        if isinstance(schema_examples, list) and schema_examples:
            media_obj["examples"] = {
                "default": {
                    "summary": "请求示例",
                    "value": schema_examples[0],
                }
            }
            schema.pop("examples", None)
            changed = True
    return changed


def normalize_openapi(input_file: str, output_file: str, *, profile: str = "tamar-community") -> dict:
    """Normalize OpenAPI before importing to Apifox."""
    src = Path(input_file)
    if not src.exists():
        print(f"错误: 文件不存在: {input_file}", file=sys.stderr)
        sys.exit(1)

    data = json.loads(src.read_text(encoding="utf-8"))
    paths = data.get("paths", {})
    changed_folders = 0
    promoted_examples = 0
    targeting_example_added = False
    forbidden_after = []

    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        for method, operation in item.items():
            if not isinstance(operation, dict):
                continue

            folder = operation.get("x-apifox-folder")
            if not folder:
                tags = operation.get("tags") or []
                folder = tags[0] if tags else _infer_tamar_folder(path)

            if profile == "tamar-community" and path.startswith("/api/incentive/targeting"):
                new_folder = "用户端 (C端)/投放管理"
            elif profile == "tamar-community" and path.startswith("/api/console/incentive"):
                new_folder = "管理后台/激励管理"
            else:
                new_folder = TAMAR_FOLDER_ALIASES.get(folder, folder)
            if profile == "tamar-community" and not new_folder:
                new_folder = _infer_tamar_folder(path)

            if new_folder != folder:
                changed_folders += 1

            if new_folder:
                operation["x-apifox-folder"] = new_folder
                operation["tags"] = [new_folder]

            if _promote_request_examples(operation):
                promoted_examples += 1

            if path == "/api/incentive/targeting/decision" and method.lower() == "post":
                media_obj = (
                    operation
                    .setdefault("requestBody", {})
                    .setdefault("content", {})
                    .setdefault("application/json", {})
                )
                media_obj["examples"] = {
                    "default": {
                        "summary": "批量查询卡片投放展示决策",
                        "value": TARGETING_DECISION_EXAMPLE,
                    }
                }
                schema = media_obj.get("schema")
                if isinstance(schema, dict):
                    schema.pop("examples", None)
                targeting_example_added = True

            final_folder = operation.get("x-apifox-folder")
            if final_folder in TAMAR_FORBIDDEN_ROOT_FOLDERS:
                forbidden_after.append({
                    "method": method.upper(),
                    "path": path,
                    "folder": final_folder,
                })

    _rebuild_tags_from_operations(data)

    out = Path(output_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "status": "ok" if not forbidden_after else "warning",
        "file": str(out),
        "paths": len(paths),
        "endpoints": sum(len(item) for item in paths.values() if isinstance(item, dict)),
        "changedFolders": changed_folders,
        "promotedRequestExamples": promoted_examples,
        "targetingDecisionExample": targeting_example_added,
        "forbiddenRootFolderEndpoints": forbidden_after,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


# ========== 导出 ==========

def export_openapi(project_id: str, output_dir: str = None, *,
                   oas_version: str = "3.1",
                   export_format: str = "JSON",
                   scope_type: str = "ALL",
                   excluded_tags: list = None,
                   include_apifox_ext: bool = True,
                   add_folders_to_tags: bool = True,
                   branch_id: int = None,
                   module_id: int = None,
                   endpoint_ids: list = None,
                   tag_names: list = None,
                   folder_ids: list = None):
    """
    导出项目的 OpenAPI 数据

    scope_type: ALL | SELECTED_ENDPOINTS | SELECTED_TAGS | SELECTED_FOLDERS
    """
    url = f"{BASE_URL}/projects/{project_id}/export-openapi"

    # 构建 scope
    if scope_type == "ALL":
        scope = {"type": "ALL"}
        if excluded_tags:
            scope["excludedByTags"] = excluded_tags
    elif scope_type == "SELECTED_ENDPOINTS":
        scope = {"type": "SELECTED_ENDPOINTS", "selectedEndpointIds": endpoint_ids or []}
    elif scope_type == "SELECTED_TAGS":
        scope = {"type": "SELECTED_TAGS", "selectedTagNames": tag_names or []}
    elif scope_type == "SELECTED_FOLDERS":
        scope = {"type": "SELECTED_FOLDERS", "selectedFolderIds": folder_ids or []}
    else:
        scope = {"type": "ALL"}

    body = {
        "scope": scope,
        "options": {
            "includeApifoxExtensionProperties": include_apifox_ext,
            "addFoldersToTags": add_folders_to_tags,
        },
        "oasVersion": oas_version,
        "exportFormat": export_format,
    }

    if branch_id is not None:
        body["branchId"] = branch_id
    if module_id is not None:
        body["moduleId"] = module_id

    params = {"locale": "zh-CN"}

    with httpx.Client(timeout=60) as client:
        resp = client.post(url, headers=get_headers(), params=params, json=body)

    if resp.status_code != 200:
        print(f"导出失败: HTTP {resp.status_code}", file=sys.stderr)
        print(resp.text, file=sys.stderr)
        sys.exit(1)

    data = resp.json()

    # 保存到文件
    out_dir = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    ext = "json" if export_format == "JSON" else "yaml"
    out_file = out_dir / f"openapi_export_{project_id}.{ext}"

    with open(out_file, "w", encoding="utf-8") as f:
        if export_format == "JSON":
            json.dump(data, f, ensure_ascii=False, indent=2)
        else:
            f.write(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2))

    # 统计信息
    paths = data.get("paths", {})
    schemas = data.get("components", {}).get("schemas", {})
    tags = data.get("tags", [])

    print(json.dumps({
        "status": "ok",
        "file": str(out_file),
        "stats": {
            "endpoints": sum(len(methods) for methods in paths.values()),
            "paths": len(paths),
            "schemas": len(schemas),
            "tags": len(tags),
        },
        "title": data.get("info", {}).get("title", ""),
        "version": data.get("info", {}).get("version", ""),
    }, ensure_ascii=False, indent=2))

    return data


# ========== 导入 OpenAPI ==========

def import_openapi(project_id: str, input_source: str, *,
                   endpoint_overwrite: str = "OVERWRITE_EXISTING",
                   schema_overwrite: str = "OVERWRITE_EXISTING",
                   target_endpoint_folder_id: int = None,
                   target_schema_folder_id: int = None,
                   update_folder: bool = False,
                   prepend_base_path: bool = False,
                   branch_id: int = None,
                   module_id: int = None,
                   delete_unmatched: bool = False):
    """
    导入 OpenAPI/Swagger 数据到项目

    input_source: 文件路径或 URL
    """
    url = f"{BASE_URL}/projects/{project_id}/import-openapi"

    # 判断是 URL 还是文件
    if input_source.startswith("http://") or input_source.startswith("https://"):
        input_data = {"url": input_source}
    else:
        # 从文件读取
        file_path = Path(input_source)
        if not file_path.exists():
            print(f"错误: 文件不存在: {input_source}", file=sys.stderr)
            sys.exit(1)
        content = file_path.read_text(encoding="utf-8")
        # 字符串方式导入
        input_data = content

    body = {
        "input": input_data,
        "inputType": "openapi",
        "options": {
            "endpointOverwriteBehavior": endpoint_overwrite,
            "schemaOverwriteBehavior": schema_overwrite,
            "updateFolderOfChangedEndpoint": update_folder,
            "prependBasePath": prepend_base_path,
        },
    }

    if target_endpoint_folder_id is not None:
        body["options"]["targetEndpointFolderId"] = target_endpoint_folder_id
    if target_schema_folder_id is not None:
        body["options"]["targetSchemaFolderId"] = target_schema_folder_id
    if delete_unmatched:
        body["options"]["deleteUnmatchedResources"] = True
    if branch_id is not None:
        body["options"]["targetBranchId"] = branch_id
    if module_id is not None:
        body["options"]["moduleId"] = module_id

    params = {"locale": "zh-CN"}

    with httpx.Client(timeout=60) as client:
        resp = client.post(url, headers=get_headers(), params=params, json=body)

    if resp.status_code != 200:
        print(f"导入失败: HTTP {resp.status_code}", file=sys.stderr)
        print(resp.text, file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    counters = data.get("data", {}).get("counters", {})
    errors = data.get("data", {}).get("errors", [])

    print(json.dumps({
        "status": "ok",
        "counters": counters,
        "errors": errors,
    }, ensure_ascii=False, indent=2))

    return data


# ========== 导入 Postman ==========

def import_postman(project_id: str, input_file: str, *,
                   endpoint_overwrite: str = "OVERWRITE_EXISTING",
                   endpoint_case_overwrite: str = "OVERWRITE_EXISTING",
                   target_endpoint_folder_id: int = None,
                   update_folder: bool = False,
                   branch_id: int = None,
                   module_id: int = None):
    """导入 Postman Collection 数据到项目"""
    url = f"{BASE_URL}/projects/{project_id}/import-postman-collection"

    file_path = Path(input_file)
    if not file_path.exists():
        print(f"错误: 文件不存在: {input_file}", file=sys.stderr)
        sys.exit(1)

    content = file_path.read_text(encoding="utf-8")

    body = {
        "input": content,
        "inputType": "postman",
        "options": {
            "endpointOverwriteBehavior": endpoint_overwrite,
            "endpointCaseOverwriteBehavior": endpoint_case_overwrite,
            "updateFolderOfChangedEndpoint": update_folder,
        },
    }

    if target_endpoint_folder_id is not None:
        body["options"]["targetEndpointFolderId"] = target_endpoint_folder_id
    if branch_id is not None:
        body["options"]["targetBranchId"] = branch_id
    if module_id is not None:
        body["options"]["moduleId"] = module_id

    params = {"locale": "zh-CN"}

    with httpx.Client(timeout=60) as client:
        resp = client.post(url, headers=get_headers(), params=params, json=body)

    if resp.status_code != 200:
        print(f"导入失败: HTTP {resp.status_code}", file=sys.stderr)
        print(resp.text, file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    counters = data.get("data", {}).get("counters", {})
    errors = data.get("data", {}).get("errors", [])

    print(json.dumps({
        "status": "ok",
        "counters": counters,
        "errors": errors,
    }, ensure_ascii=False, indent=2))

    return data


# ========== CLI ==========

def main():
    parser = argparse.ArgumentParser(description="Apifox Open API 客户端")
    parser.add_argument("--token", help="Apifox API 访问令牌（优先于 APIFOX_TOKEN 环境变量）")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # export
    p_export = subparsers.add_parser("export", help="导出 OpenAPI 数据")
    p_export.add_argument("project_id", help="项目 ID 或 Apifox URL（自动解析 projectId 和 moduleId）")
    p_export.add_argument("-o", "--output", help="输出目录")
    p_export.add_argument("--oas-version", default="3.1", choices=["2.0", "3.0", "3.1"], help="OAS 版本")
    p_export.add_argument("--format", default="JSON", choices=["JSON", "YAML"], help="导出格式")
    p_export.add_argument("--scope", default="ALL",
                         choices=["ALL", "SELECTED_ENDPOINTS", "SELECTED_TAGS", "SELECTED_FOLDERS"],
                         help="导出范围")
    p_export.add_argument("--exclude-tags", nargs="*", help="排除的标签")
    p_export.add_argument("--endpoint-ids", nargs="*", type=int, help="指定接口 ID 列表")
    p_export.add_argument("--tag-names", nargs="*", help="指定标签名列表")
    p_export.add_argument("--folder-ids", nargs="*", type=int, help="指定目录 ID 列表")
    p_export.add_argument("--no-ext", action="store_true", help="不包含 Apifox 扩展字段（默认包含）")
    p_export.add_argument("--no-folders-to-tags", action="store_true", help="标签中不包含目录名（默认包含）")
    p_export.add_argument("--branch-id", type=int, help="分支 ID")
    p_export.add_argument("--module-id", type=int, help="模块 ID（URL 中有 moduleId 时可省略）")

    # normalize-openapi
    p_normalize = subparsers.add_parser("normalize-openapi", help="导入前整理 OpenAPI 目录和示例")
    p_normalize.add_argument("input", help="OpenAPI JSON 文件路径")
    p_normalize.add_argument("-o", "--output", required=True, help="输出文件路径")
    p_normalize.add_argument("--profile", default="tamar-community",
                             choices=["tamar-community"],
                             help="整理规则配置")

    # import-openapi
    p_import = subparsers.add_parser("import-openapi", help="导入 OpenAPI 数据")
    p_import.add_argument("project_id", help="项目 ID 或 Apifox URL")
    p_import.add_argument("input", help="OpenAPI 文件路径或 URL")
    p_import.add_argument("--endpoint-overwrite", default="OVERWRITE_EXISTING",
                         choices=["OVERWRITE_EXISTING", "AUTO_MERGE", "KEEP_EXISTING", "CREATE_NEW"],
                         help="接口覆盖策略")
    p_import.add_argument("--schema-overwrite", default="OVERWRITE_EXISTING",
                         choices=["OVERWRITE_EXISTING", "AUTO_MERGE", "KEEP_EXISTING", "CREATE_NEW"],
                         help="模型覆盖策略")
    p_import.add_argument("--target-endpoint-folder", type=int, help="目标接口目录 ID")
    p_import.add_argument("--target-schema-folder", type=int, help="目标模型目录 ID")
    p_import.add_argument("--update-folder", action="store_true", help="更新接口目录 ID")
    p_import.add_argument("--prepend-base-path", action="store_true", help="添加基础路径前缀")
    p_import.add_argument("--branch-id", type=int, help="分支 ID")
    p_import.add_argument("--module-id", type=int, help="模块 ID（URL 中有 moduleId 时可省略）")
    p_import.add_argument("--delete-unmatched", action="store_true", help="删除未匹配的资源")

    # import-postman
    p_postman = subparsers.add_parser("import-postman", help="导入 Postman Collection 数据")
    p_postman.add_argument("project_id", help="项目 ID 或 Apifox URL")
    p_postman.add_argument("input", help="Postman Collection JSON 文件路径")
    p_postman.add_argument("--endpoint-overwrite", default="OVERWRITE_EXISTING",
                          choices=["OVERWRITE_EXISTING", "AUTO_MERGE", "KEEP_EXISTING", "CREATE_NEW"])
    p_postman.add_argument("--endpoint-case-overwrite", default="OVERWRITE_EXISTING",
                          choices=["OVERWRITE_EXISTING", "KEEP_EXISTING", "CREATE_NEW"])
    p_postman.add_argument("--target-endpoint-folder", type=int, help="目标接口目录 ID")
    p_postman.add_argument("--update-folder", action="store_true")
    p_postman.add_argument("--branch-id", type=int)
    p_postman.add_argument("--module-id", type=int)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "normalize-openapi":
        normalize_openapi(args.input, args.output, profile=args.profile)
        return

    # 初始化 token: --token > APIFOX_TOKEN 环境变量
    global ACCESS_TOKEN
    ACCESS_TOKEN = args.token or os.environ.get("APIFOX_TOKEN", "")

    # 解析 project_id：支持 URL 或纯 ID
    raw_project = args.project_id
    if raw_project.startswith("http://") or raw_project.startswith("https://"):
        parsed = parse_apifox_url(raw_project)
        if "project_id" not in parsed:
            print(f"错误: 无法从 URL 中解析 project_id: {raw_project}", file=sys.stderr)
            sys.exit(1)
        project_id = parsed["project_id"]
        # URL 中的 moduleId 作为默认值，--module-id 参数优先
        if args.module_id is None and "module_id" in parsed:
            args.module_id = parsed["module_id"]
    else:
        project_id = raw_project

    if args.command == "export":
        export_openapi(
            project_id,
            output_dir=args.output,
            oas_version=args.oas_version,
            export_format=args.format,
            scope_type=args.scope,
            excluded_tags=args.exclude_tags,
            include_apifox_ext=not args.no_ext,
            add_folders_to_tags=not args.no_folders_to_tags,
            branch_id=args.branch_id,
            module_id=args.module_id,
            endpoint_ids=args.endpoint_ids,
            tag_names=args.tag_names,
            folder_ids=args.folder_ids,
        )
    elif args.command == "import-openapi":
        import_openapi(
            project_id,
            args.input,
            endpoint_overwrite=args.endpoint_overwrite,
            schema_overwrite=args.schema_overwrite,
            target_endpoint_folder_id=args.target_endpoint_folder,
            target_schema_folder_id=args.target_schema_folder,
            update_folder=args.update_folder,
            prepend_base_path=args.prepend_base_path,
            branch_id=args.branch_id,
            module_id=args.module_id,
            delete_unmatched=args.delete_unmatched,
        )
    elif args.command == "import-postman":
        import_postman(
            project_id,
            args.input,
            endpoint_overwrite=args.endpoint_overwrite,
            endpoint_case_overwrite=args.endpoint_case_overwrite,
            target_endpoint_folder_id=args.target_endpoint_folder,
            update_folder=args.update_folder,
            branch_id=args.branch_id,
            module_id=args.module_id,
        )


if __name__ == "__main__":
    main()
