import asyncio
import base64
import json
import logging
import os
import time
from pathlib import Path

from services.ocr_service import OCRService
from services.llm_service import LLMService
from services import db

logger = logging.getLogger(__name__)

IMAGES_DIR = Path(__file__).parent.parent / "images"
IMAGES_DIR.mkdir(exist_ok=True)


class ScreenshotHandler:
    def __init__(self):
        self.ocr = OCRService(os.getenv("OCR_URL", "http://119.23.227.187:8675/glmocr/parse"))
        self.llm = LLMService()

    async def process(self, data: dict, client_id: str = None) -> dict:
        action = data.get("action", "ocr")
        image_base64 = data.get("imageBase64", "")

        # 1. 保存图片
        image_path = self._save_image(image_base64)
        print(f"[Handler] Image saved: {image_path}", flush=True)

        # 2. OCR 识别
        print(f"[Handler] Starting OCR for action={action}...", flush=True)
        ocr_result = await self._ocr_recognize(image_path)
        markdown = ocr_result.get("markdown_result", "")
        raw_text = ocr_result.get("raw_ocr_result", "")
        print(f"[Handler] OCR done, markdown={len(markdown)} chars, raw={len(raw_text)} chars", flush=True)

        # 同时输出两种格式，用标注区分
        text = f"=== 纯文本 ===\n{raw_text}\n\n=== 格式化 ===\n{markdown}"

        # 3. Claude 结构化处理（同步）
        structured_data = None
        flat_json = None
        if raw_text or markdown:
            print(f"[Handler] Starting Claude structuring...", flush=True)
            structured_data = await self._structure_with_claude(action, raw_text, markdown)
            if structured_data:
                flat_data = self._flatten_structured_data(action, structured_data)
                flat_json = json.dumps(flat_data, ensure_ascii=False) if flat_data else None
                print(f"[Handler] Claude structured: {len(structured_data)} chars", flush=True)
            else:
                print(f"[Handler] Claude structuring returned empty/failed", flush=True)

        # 4. 保存到数据库（原始 + 结构化都保存）
        try:
            record_id = db.save_ocr_record(
                action=action, raw_text=raw_text, markdown_text=markdown,
                structured_data=structured_data,
                image_path=image_path, client_id=client_id
            )
            if flat_json:
                db.update_ocr_structured_data(record_id, structured_data, flat_json)
            print(f"[Handler] OCR saved to DB, id={record_id}, action={action}", flush=True)
        except Exception as e:
            print(f"[Handler] DB save failed: {e}", flush=True)

        # 5. 根据 action 构建结果
        result_text = structured_data if structured_data else text

        if action == "ocr":
            return {"text": result_text, "auto_copy": True}

        elif action == "chat_reply":
            reply = await self.llm.analyze_chat(text)
            return {"reply": reply, "auto_copy": True}

        elif action == "table":
            return {"text": result_text, "format": "markdown"}

        elif action == "search":
            return {"text": result_text, "auto_copy": True}

        elif action == "fund_holdings":
            return {"text": result_text, "auto_copy": True}

        elif action == "full_page":
            return {"text": result_text, "auto_copy": True}

        return {"text": result_text}

    async def process_stream(self, data: dict, client_id: str = None):
        """SSE 流式处理：逐步推送进度给客户端"""
        action = data.get("action", "ocr")
        image_base64 = data.get("imageBase64", "")

        # 1. 保存图片
        image_path = self._save_image(image_base64)
        yield {"event": "progress", "data": {"step": "image_saved", "message": "图片已保存"}}

        # 2. OCR 识别
        yield {"event": "progress", "data": {"step": "ocr_start", "message": "OCR 识别中..."}}
        ocr_result = await self._ocr_recognize(image_path)
        markdown = ocr_result.get("markdown_result", "")
        raw_text = ocr_result.get("raw_ocr_result", "")
        text = f"=== 纯文本 ===\n{raw_text}\n\n=== 格式化 ===\n{markdown}"
        yield {"event": "progress", "data": {"step": "ocr_done", "message": f"OCR 完成，识别 {len(raw_text)} 字符"}}

        # 3. Claude 结构化
        structured_data = None
        flat_json = None
        if raw_text or markdown:
            yield {"event": "progress", "data": {"step": "claude_start", "message": "AI 结构化处理中..."}}
            structured_data = await self._structure_with_claude(action, raw_text, markdown)
            if structured_data:
                flat_data = self._flatten_structured_data(action, structured_data)
                flat_json = json.dumps(flat_data, ensure_ascii=False) if flat_data else None
                yield {"event": "progress", "data": {"step": "claude_done", "message": "AI 处理完成"}}
            else:
                yield {"event": "progress", "data": {"step": "claude_failed", "message": "AI 处理跳过"}}

        # 4. 保存到数据库
        try:
            record_id = db.save_ocr_record(
                action=action, raw_text=raw_text, markdown_text=markdown,
                structured_data=structured_data,
                image_path=image_path, client_id=client_id
            )
            if flat_json:
                db.update_ocr_structured_data(record_id, structured_data, flat_json)
        except Exception as e:
            print(f"[Handler] DB save failed: {e}", flush=True)

        # 5. 最终结果
        result_text = structured_data if structured_data else text
        if action == "chat_reply":
            reply = await self.llm.analyze_chat(text)
            result_data = {"reply": reply, "auto_copy": True}
        elif action == "table":
            result_data = {"text": result_text, "format": "markdown"}
        else:
            result_data = {"text": result_text, "auto_copy": True}

        yield {"event": "result", "data": result_data}

    def _save_image(self, image_base64: str) -> str:
        timestamp = int(time.time() * 1000)
        filename = f"screenshot_{timestamp}.jpg"
        filepath = IMAGES_DIR / filename

        image_data = base64.b64decode(image_base64)
        filepath.write_bytes(image_data)

        return str(filepath)


    async def _ocr_recognize(self, image_path: str) -> dict:
        return await self.ocr.recognize(image_path)

    def _extract_json_from_text(self, text: str) -> dict | None:
        """从 Claude 输出中提取 JSON（可能被 ```json 包裹）"""
        import re
        # 尝试提取 ```json ... ``` 中的内容
        m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        json_str = m.group(1).strip() if m else text.strip()
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse JSON from Claude output: {json_str[:200]}")
            return None

    def _flatten_structured_data(self, action: str, structured_data: str) -> dict | None:
        """将 Claude 结构化输出扁平化为一层 JSON"""
        parsed = self._extract_json_from_text(structured_data)
        if not parsed:
            return None

        if action == "fund_holdings":
            return self._flatten_fund_holdings(parsed)
        else:
            return self._flatten_generic(parsed)

    def _flatten_fund_holdings(self, data: dict) -> dict:
        """扁平化基金持仓数据"""
        flat = {}

        # 提取摘要数值（递归查找常见 key）
        flat["total_assets"] = self._find_numeric(data, ["total_assets", "total_amount"])
        flat["yesterday_profit"] = self._find_numeric(data, ["yesterday_profit", "yesterday"])
        flat["holding_profit"] = self._find_numeric(data, ["holding_profit", "holding"])
        flat["cumulative_profit"] = self._find_numeric(data, ["cumulative_profit", "cumulative"])
        flat["pending_purchase"] = self._find_numeric(data, ["pending_purchase", "pending"])

        # 提取持仓列表，每只基金扁平化
        holdings = self._find_list(data, ["holdings", "funds", "fund_list"])
        flat_holdings = []
        for h in (holdings or []):
            item = {}
            for key in ["fund_name", "fund_code", "amount", "daily_profit", "total_profit", "profit_rate"]:
                val = h.get(key)
                if isinstance(val, dict):
                    val = val.get("value", val.get("formatted"))
                item[key] = val
            flat_holdings.append(item)
        flat["holdings"] = flat_holdings
        flat["holdings_count"] = len(flat_holdings)

        return flat

    def _flatten_generic(self, data: dict) -> dict:
        """通用扁平化：递归展平嵌套 dict 为 dot-notation key"""
        flat = {}
        self._flatten_dict(data, "", flat)
        return flat

    def _flatten_dict(self, obj, prefix: str, result: dict):
        if isinstance(obj, dict):
            for k, v in obj.items():
                new_key = f"{prefix}.{k}" if prefix else k
                if isinstance(v, (dict,)):
                    self._flatten_dict(v, new_key, result)
                elif isinstance(v, list) and all(not isinstance(i, (dict, list)) for i in v):
                    result[new_key] = v
                elif isinstance(v, list):
                    result[new_key] = v  # 保留复杂数组不再展开
                else:
                    result[new_key] = v

    def _find_numeric(self, data: dict, keys: list) -> float | None:
        """在嵌套 dict 中查找数值字段"""
        for key in keys:
            val = self._deep_find(data, key)
            if val is not None:
                if isinstance(val, dict):
                    val = val.get("value", val.get("formatted"))
                if isinstance(val, (int, float)):
                    return val
                if isinstance(val, str):
                    try:
                        return float(val.replace(",", "").replace("元", "").replace("+", ""))
                    except ValueError:
                        pass
        return None

    def _find_list(self, data: dict, keys: list) -> list | None:
        for key in keys:
            val = self._deep_find(data, key)
            if isinstance(val, list):
                return val
        return None

    def _deep_find(self, data, target_key):
        """递归在嵌套 dict 中查找 key"""
        if isinstance(data, dict):
            if target_key in data:
                return data[target_key]
            for v in data.values():
                result = self._deep_find(v, target_key)
                if result is not None:
                    return result
        return None

    async def _structure_with_claude(self, action: str, raw_text: str, markdown_text: str) -> str:
        """用 claude -p 将 OCR 文本处理为结构化 JSON"""
        prompt = self._build_structure_prompt(action, raw_text, markdown_text)
        try:
            proc = await asyncio.create_subprocess_exec(
                "claude", "-p", prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            result = stdout.decode("utf-8").strip()
            if proc.returncode != 0:
                logger.warning(f"claude -p failed: {stderr.decode()}")
                return None
            return result
        except asyncio.TimeoutError:
            logger.warning("claude -p timed out (60s)")
            return None
        except FileNotFoundError:
            logger.warning("claude command not found")
            return None
        except Exception as e:
            logger.warning(f"claude -p error: {e}")
            return None

    def _build_structure_prompt(self, action: str, raw_text: str, markdown_text: str) -> str:
        """根据 action 类型构建不同的结构化 prompt"""
        if action == "fund_holdings":
            instruction = """请将以下 OCR 识别的基金持仓截图文本解析为结构化 JSON。
提取每只基金的信息，输出格式：
```json
{
  "total_assets": "总资产金额（如有）",
  "total_profit": "总收益（如有）",
  "holdings": [
    {
      "fund_name": "基金名称",
      "fund_code": "基金代码（6位数字，如有）",
      "amount": "持有金额",
      "daily_profit": "当日收益（如有）",
      "total_profit": "持有收益（如有）",
      "profit_rate": "收益率（如有）"
    }
  ]
}
```
如果某个字段无法识别，设为 null。只输出 JSON，不要其他文字。"""
        elif action == "table":
            instruction = """请将以下 OCR 识别的表格文本解析为结构化 JSON。
输出格式为一个数组，每个元素代表一行，字段名取自表头。
只输出 JSON，不要其他文字。"""
        else:
            instruction = """请将以下 OCR 识别的文本整理为结构化 JSON。
提取关键信息（人名、数字、日期、金额等），按合理的结构组织。
只输出 JSON，不要其他文字。"""

        return f"""{instruction}

=== 纯文本 ===
{raw_text}

=== 格式化文本 ===
{markdown_text}"""
