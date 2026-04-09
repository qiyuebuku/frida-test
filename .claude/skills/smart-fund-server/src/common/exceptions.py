class AppException(Exception):
    """应用异常基类"""
    def __init__(self, message: str, code: int = 400):
        self.message = message
        self.code = code


class NotFoundError(AppException):
    def __init__(self, message: str = "资源不存在"):
        super().__init__(message, 404)


class ExternalAPIError(AppException):
    def __init__(self, source: str, message: str):
        super().__init__(f"[{source}] {message}", 502)
