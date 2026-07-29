"""智能基金服务 - 入口"""

from src.interfaces.api import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn
    from src.infrastructure.config.settings import SERVER_HOST, SERVER_PORT

    uvicorn.run("main:app", host=SERVER_HOST, port=SERVER_PORT, reload=True)
