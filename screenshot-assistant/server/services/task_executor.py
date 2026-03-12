import subprocess
import threading
import time
import json

from services.db import update_task, get_task
from services.skill_registry import SkillInfo, CommandInfo


class TaskExecutor:
    """异步任务执行器 — 在后台线程中执行 Skill 命令"""

    def __init__(self):
        self._pool: dict[int, threading.Thread] = {}

    def submit(self, task_id: int, skill: SkillInfo, command: CommandInfo,
               args: dict | None = None, input_data: str | None = None,
               image_path: str | None = None):
        """提交任务到后台线程"""
        t = threading.Thread(
            target=self._run,
            args=(task_id, skill, command, args, input_data, image_path),
            daemon=True,
        )
        self._pool[task_id] = t
        t.start()

    def _run(self, task_id: int, skill: SkillInfo, command: CommandInfo,
             args: dict | None, input_data: str | None, image_path: str | None):
        start = time.time()
        try:
            update_task(task_id, status="processing",
                        started_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                        progress_msg="正在执行...")

            if command.executor == "pipeline":
                result = self._execute_pipeline(task_id, skill, command, input_data, image_path)
            else:
                result = self._execute_claude(task_id, skill, command, args, input_data)

            elapsed = int(time.time() - start)
            update_task(
                task_id,
                status="completed",
                result=result,
                progress=100,
                progress_msg="完成",
                completed_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                duration_sec=elapsed,
            )
            print(f"[TaskExecutor] task {task_id} completed in {elapsed}s", flush=True)
        except Exception as e:
            elapsed = int(time.time() - start)
            update_task(
                task_id,
                status="failed",
                error_msg=str(e),
                completed_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                duration_sec=elapsed,
            )
            print(f"[TaskExecutor] task {task_id} failed: {e}", flush=True)
        finally:
            self._pool.pop(task_id, None)

    def _execute_pipeline(self, task_id: int, skill: SkillInfo, command: CommandInfo,
                          input_data: str | None, image_path: str | None) -> str:
        """按 pipeline 步骤依次执行"""
        context = {
            "image_path": image_path,
            "input_data": input_data,
            "skill_path": skill.path,
        }

        for i, step in enumerate(command.pipeline):
            update_task(task_id,
                        progress=int((i / len(command.pipeline)) * 100),
                        progress_msg=step.description or f"步骤 {i+1}/{len(command.pipeline)}")

            match step.handler:
                case "ocr_service":
                    if not image_path:
                        raise RuntimeError("ocr_service 需要图片输入")
                    from services.ocr_service import OCRService
                    ocr = OCRService()
                    import asyncio
                    loop = asyncio.new_event_loop()
                    ocr_result = loop.run_until_complete(ocr.recognize(image_path))
                    loop.close()
                    context["ocr_text"] = ocr_result.get("markdown_result", "")

                case "llm_call":
                    from services.llm_service import LLMService
                    llm = LLMService()
                    prompt_parts = [step.prompt_template]
                    if input_data:
                        prompt_parts.append(f"\n输入内容:\n{input_data}")
                    if context.get("ocr_text"):
                        prompt_parts.append(f"\nOCR识别结果:\n{context['ocr_text']}")
                    import asyncio
                    loop = asyncio.new_event_loop()
                    result = loop.run_until_complete(llm.chat("\n".join(prompt_parts)))
                    loop.close()
                    context["analysis"] = result

                case _:
                    print(f"[TaskExecutor] unknown handler: {step.handler}, skipping", flush=True)

        return context.get("analysis") or json.dumps(
            {k: v for k, v in context.items() if k not in ("image_path", "input_data", "skill_path")},
            ensure_ascii=False
        )

    def _execute_claude(self, task_id: int, skill: SkillInfo, command: CommandInfo,
                        args: dict | None, input_data: str | None) -> str:
        """通过 claude CLI 执行"""
        prompt_parts = [f"执行 Skill '{skill.display_name}' 的命令 '{command.name}'"]
        prompt_parts.append(f"命令描述: {command.description}")
        if input_data:
            prompt_parts.append(f"用户输入: {input_data}")
        if args:
            prompt_parts.append(f"参数: {json.dumps(args, ensure_ascii=False)}")

        prompt = "\n".join(prompt_parts)

        update_task(task_id, progress_msg="正在调用 Claude...")

        cmd = [
            "claude", "-p", prompt,
            "--allowedTools", "Bash,Read,Glob,Grep,Write,Edit",
        ]

        result = subprocess.run(
            cmd,
            cwd=skill.path,
            capture_output=True,
            text=True,
            timeout=command.estimated_time * 2,
        )

        if result.returncode != 0:
            error = result.stderr.strip() or "Claude 执行失败"
            raise RuntimeError(error)

        return result.stdout.strip()


# 全局单例
task_executor = TaskExecutor()
