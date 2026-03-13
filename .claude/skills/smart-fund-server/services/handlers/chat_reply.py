"""智能回复 handler：OCR → LLM 生成回复建议"""

from datetime import datetime

from services import task_db
from services.handlers import register


@register("", "chat_reply")
@register("screenshot", "chat_reply")
def handle_chat_reply(executor, task: dict):
    """OCR → LLM 回复建议"""
    task_id = task["id"]

    raw_text, markdown, ocr_id = executor._do_ocr(
        task_id, task["image_path"], "chat_reply", task.get("client_id")
    )

    executor._progress(task_id, 40, "正在生成回复建议...")

    prompt = f"""分析以下聊天截图内容，给出 3 个回复建议。

聊天内容：
{markdown or raw_text}

输出格式：
# 智能回复建议

## 对话摘要
（一句话总结对方说了什么）

## 推荐回复
1. **正式回复**：...
2. **轻松回复**：...
3. **简短回复**：...

只输出 Markdown 格式的回复建议。"""
    prompt = executor._apply_custom_prompt(prompt, task)

    result = executor._run_claude(prompt, timeout=120)
    if not result:
        task_db.update_task(task_id, status="failed", error_msg="生成回复失败")
        return

    summary = executor._extract_summary(result, max_len=100)
    task_db.update_task(task_id,
        status="completed", progress=100, progress_msg=None,
        summary=summary, result=result,
        completed_at=datetime.now()
    )
