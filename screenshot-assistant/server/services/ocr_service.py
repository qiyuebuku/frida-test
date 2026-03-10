import base64
import os

import httpx

# WSL2 代理会干扰 httpx 请求
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)


class OCRService:
    def __init__(self, api_url: str = "http://127.0.0.1:5002/glmocr/parse"):
        self.api_url = api_url

    async def recognize(self, image_path: str) -> dict:
        """对图片进行 OCR 识别"""
        try:
            with open(image_path, "rb") as f:
                image_data = f.read()
            image_base64 = base64.b64encode(image_data).decode()
            return await self._call_ocr(image_base64)
        except Exception as e:
            print(f"[OCR] error: {e}", flush=True)
            return {"markdown_result": f"OCR 错误: {str(e)}"}

    async def recognize_base64(self, image_base64: str) -> dict:
        """直接对 base64 图片数据进行 OCR"""
        try:
            return await self._call_ocr(image_base64)
        except Exception as e:
            print(f"[OCR] error: {e}", flush=True)
            return {"markdown_result": f"OCR 错误: {str(e)}"}

    async def _call_ocr(self, image_base64: str) -> dict:
        data_url = f"data:image/jpeg;base64,{image_base64}"
        async with httpx.AsyncClient(timeout=600) as client:
            response = await client.post(
                self.api_url,
                json={"images": [data_url], "include_raw_ocr": True},
                headers={"Content-Type": "application/json"}
            )
            if response.status_code == 200:
                return response.json()
            else:
                print(f"[OCR] failed: {response.status_code} {response.text[:200]}", flush=True)
                return {"markdown_result": "", "raw_ocr_result": f"OCR 请求失败: {response.status_code}"}
