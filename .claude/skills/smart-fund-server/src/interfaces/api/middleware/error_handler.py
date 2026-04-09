from fastapi import Request
from fastapi.responses import JSONResponse

from src.common.exceptions import AppException


async def app_exception_handler(request: Request, exc: AppException):
    return JSONResponse(status_code=exc.code, content={"error": exc.message})
