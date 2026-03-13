"""OCR 类 handler：纯 OCR 识别，5 个命令复用"""

from datetime import datetime

from services import task_db
from services.handlers import register


@register("", "ocr")
@register("", "table")
@register("", "search")
@register("", "full_page")
@register("", "manual_scroll")
@register("screenshot", "ocr")
@register("screenshot", "table")
@register("screenshot", "search")
@register("screenshot", "full_page")
@register("screenshot", "manual_scroll")
def handle_ocr(executor, task: dict):
    """纯 OCR：识别 → 返回文本"""
    task_id = task["id"]
    command_id = task.get("command_id") or task.get("task_type", "ocr")

    raw_text, markdown, ocr_id = executor._do_ocr(
        task_id, task["image_path"], command_id, task.get("client_id")
    )

    result_text = markdown or raw_text
    summary = result_text[:200] if result_text else "（空白页面）"

    task_db.update_task(task_id,
        status="completed", progress=100, progress_msg=None,
        summary=summary, result=result_text,
        completed_at=datetime.now()
    )
