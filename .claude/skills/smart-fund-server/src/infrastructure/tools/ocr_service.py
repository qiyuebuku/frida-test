import base64
import os

import httpx

# WSL2 代理会干扰 httpx 请求
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)


class OCRService:
    def __init__(self, api_url: str = None):
        from src.infrastructure.config.settings import OCR_URL
        self.api_url = api_url or OCR_URL

    async def recognize(self, image_path: str) -> dict:
        """对图片进行 OCR 识别"""
        try:
            with open(image_path, "rb") as f:
                image_data = f.read()
            image_base64 = base64.b64encode(image_data).decode()
            return await self._call_ocr(image_base64)
        except Exception as e:
            import traceback
            print(f"[OCR] error: {e}\n{traceback.format_exc()}", flush=True)
            return {"markdown_result": "", "raw_ocr_result": f"OCR 异常: {type(e).__name__}: {e}"}

    async def recognize_base64(self, image_base64: str) -> dict:
        """直接对 base64 图片数据进行 OCR"""
        try:
            return await self._call_ocr(image_base64)
        except Exception as e:
            import traceback
            print(f"[OCR] error: {e}\n{traceback.format_exc()}", flush=True)
            return {"markdown_result": "", "raw_ocr_result": f"OCR 异常: {type(e).__name__}: {e}"}

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
                # 提取完整错误信息，包括 request_id 等调试字段
                error_body = response.text
                request_id = response.headers.get("x-request-id", "N/A")
                fal_request_id = response.headers.get("x-fal-request-id", request_id)
                error_detail = (
                    f"[OCR] FAILED\n"
                    f"  Status: {response.status_code}\n"
                    f"  URL: {self.api_url}\n"
                    f"  Request-ID: {fal_request_id}\n"
                    f"  Headers: {dict(response.headers)}\n"
                    f"  Body: {error_body[:2000]}"
                )
                print(error_detail, flush=True)
                return {
                    "markdown_result": "",
                    "raw_ocr_result": f"OCR 请求失败: HTTP {response.status_code}, request_id={fal_request_id}, body={error_body[:500]}"
                }
