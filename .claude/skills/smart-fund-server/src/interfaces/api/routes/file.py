"""文件路由：上传、读取、列出、搜索项目文件"""

import os
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter()


def _project_root() -> Path:
    return Path(os.getenv("SKILLS_DIR", str(Path(__file__).parent.parent.parent))).resolve()


def _safe_path(rel_path: str) -> Path:
    """解析相对路径并做安全检查，防止目录穿越"""
    root = _project_root()
    abs_path = Path(root, rel_path).resolve()
    if not str(abs_path).startswith(str(root)):
        raise HTTPException(403, "路径不在项目目录内")
    return abs_path


@router.post("/api/files/upload", summary="上传文件", tags=["文件"])
async def upload_file(request: Request):
    """上传文件到服务端，返回文件路径供 Skill 命令使用。

    支持两种方式：
    1. multipart/form-data: 字段名 file
    2. application/json: {"filename": "xxx.json", "content": "文件内容字符串"}
    """
    root = _project_root()
    upload_dir = root / "uploads"
    upload_dir.mkdir(exist_ok=True)

    content_type = request.headers.get("content-type", "")

    if "multipart/form-data" in content_type:
        form = await request.form()
        uploaded = form.get("file")
        if not uploaded:
            raise HTTPException(400, "缺少 file 字段")
        filename = getattr(uploaded, "filename", None) or f"upload_{int(time.time())}"
        data = await uploaded.read()
    elif "application/json" in content_type:
        body = await request.json()
        filename = body.get("filename", f"upload_{int(time.time())}.json")
        content = body.get("content", "")
        if not content:
            raise HTTPException(400, "缺少 content 字段")
        data = content.encode("utf-8") if isinstance(content, str) else content
    else:
        raise HTTPException(400, "不支持的 Content-Type，请用 multipart/form-data 或 application/json")

    ts = int(time.time())
    safe_name = Path(filename).name
    dest = upload_dir / f"{ts}_{safe_name}"
    dest.write_bytes(data)

    return {
        "status": "success",
        "data": {
            "path": str(dest.relative_to(root)),
            "abs_path": str(dest),
            "size": len(data),
            "filename": safe_name,
        }
    }


@router.get("/api/files/read", summary="读取项目文件", tags=["文件"])
async def read_project_file(
    path: str = Query(..., description="相对于项目根目录的文件路径"),
):
    """读取文件内容，路径限制在项目根目录内。"""
    abs_path = _safe_path(path)
    if not abs_path.is_file():
        raise HTTPException(404, f"文件不存在: {path}")

    content = abs_path.read_text(encoding="utf-8", errors="replace")
    return {
        "status": "success",
        "data": {
            "path": path,
            "size": abs_path.stat().st_size,
            "content": content,
        }
    }


@router.get("/api/files/list", summary="列出目录文件", tags=["文件"])
async def list_project_files(
    path: str = Query("scraped_docs", description="相对于项目根目录的目录路径"),
    pattern: str = Query("*", description="glob 模式，如 *.md"),
    recursive: bool = Query(False, description="是否递归搜索子目录"),
):
    """列出项目目录下的文件。"""
    abs_path = _safe_path(path)
    if not abs_path.is_dir():
        raise HTTPException(404, f"目录不存在: {path}")

    root = _project_root()
    glob_method = abs_path.rglob if recursive else abs_path.glob
    files = []
    for f in sorted(glob_method(pattern)):
        if f.is_file():
            files.append({
                "path": str(f.relative_to(root)),
                "size": f.stat().st_size,
            })
            if len(files) >= 200:
                break

    return {"status": "success", "data": files, "total": len(files)}


@router.get("/api/files/search", summary="搜索项目文件", tags=["文件"])
async def search_project_files(
    name: str = Query(..., description="文件名关键词"),
    pattern: str = Query("*", description="额外 glob 模式过滤"),
):
    """递归搜索文件名包含关键词的文件。"""
    root = _project_root()
    files = []
    for f in sorted(root.rglob(pattern)):
        if f.is_file() and name.lower() in f.name.lower():
            files.append({
                "path": str(f.relative_to(root)),
                "size": f.stat().st_size,
            })
            if len(files) >= 100:
                break

    return {"status": "success", "data": files, "total": len(files)}
