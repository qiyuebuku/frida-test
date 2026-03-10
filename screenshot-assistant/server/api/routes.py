from fastapi import APIRouter, Request

from handlers.screenshot_handler import ScreenshotHandler

router = APIRouter()

screenshot_handler = ScreenshotHandler()


@router.post("/screenshot")
async def process_screenshot(request: Request):
    """接收截图并进行 OCR 处理"""
    data = await request.json()
    client_id = request.headers.get("X-Client-Id", "android")
    result = await screenshot_handler.process(data, client_id=client_id)
    return {"success": True, "data": result, "message": "处理完成"}
