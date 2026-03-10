import base64
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

        # 保存到数据库
        try:
            record_id = db.save_ocr_record(
                action=action, raw_text=raw_text, markdown_text=markdown,
                image_path=image_path, client_id=client_id
            )
            print(f"[Handler] OCR saved to DB, id={record_id}, action={action}", flush=True)
        except Exception as e:
            print(f"[Handler] DB save failed: {e}", flush=True)

        # 3. 根据 action 处理
        if action == "ocr":
            return {"text": text, "auto_copy": True}

        elif action == "chat_reply":
            reply = await self.llm.analyze_chat(text)
            return {"reply": reply, "auto_copy": True}

        elif action == "table":
            return {"text": text, "format": "markdown"}

        elif action == "search":
            return {"text": text, "auto_copy": True}

        elif action == "fund_holdings":
            return {"text": text, "auto_copy": True}

        elif action == "full_page":
            return {"text": text, "auto_copy": True}

        return {"text": text}

    def _save_image(self, image_base64: str) -> str:
        timestamp = int(time.time() * 1000)
        filename = f"screenshot_{timestamp}.jpg"
        filepath = IMAGES_DIR / filename

        image_data = base64.b64decode(image_base64)
        filepath.write_bytes(image_data)

        return str(filepath)


    async def _ocr_recognize(self, image_path: str) -> dict:
        return await self.ocr.recognize(image_path)
