"""智能回复 handler：OCR → LLM 生成回复建议"""

from datetime import datetime

from services.db import task_db
from services.handlers import register


@register("", "chat_reply")
@register("screenshot", "chat_reply")
def handle_chat_reply(executor, task: dict):
    """OCR → LLM 回复建议"""
    task_id = task["id"]

    raw_text, markdown, ocr_id = executor._do_ocr(
        task_id, task["image_path"], "chat_reply", task.get("client_id")
    )

    # 构建 prompt，将用户自定义提示词作为回复风格要求
    system_prompt, rules = executor._get_custom_config(task)
    style_hint = ""
    if system_prompt:
        style_hint = f"\n\n回复风格要求：{system_prompt}"
    if rules:
        style_hint += f"\n额外规则：{rules}"

    prompt = f"""分析以下聊天截图内容，给出 3 个回复建议。{style_hint}

聊天内容：
{markdown or raw_text}

输出格式：
# 智能回复建议

## 对话摘要
（一句话总结对方说了什么）

## 推荐回复
1. **回复1**：...
2. **回复2**：...
3. **回复3**：...

只输出 Markdown 格式的回复建议。"""

    result = executor._run_claude_streaming(task_id, prompt,
        timeout=120, progress_range=(25, 90), estimated_tools=2,
        model="haiku", emit_done=False)
    if not result:
        task_db.update_task(task_id, status="failed", error_msg="生成回复失败")
        return

    summary = executor._extract_summary(result, max_len=100)
    task_db.update_task(task_id,
        status="completed", progress=100, progress_msg=None,
        summary=summary, result=result,
        completed_at=datetime.now()
    )
