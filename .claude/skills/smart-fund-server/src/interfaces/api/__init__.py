"""应用工厂 - 创建并配置 FastAPI 实例"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.common.exceptions import AppException
from src.infrastructure.db import fund_db, ocr_db, task_db, raw_data
from src.infrastructure.tools import skill_registry as sr
from src.infrastructure.tools.scheduler import scheduler
from src.interfaces.api.middleware.error_handler import app_exception_handler
from src.interfaces.api.routes import router, start_auth_auto_refresh
from src.interfaces.api.routes._utils import init_clients, close_clients

_PROJECT_ROOT = Path(__file__).resolve().parents[3]  # smart-fund-server/

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # 初始化数据库表
    try:
        fund_db.init_tables()
        ocr_db.init_ocr_tables()
        task_db.init_task_tables()
        raw_data.init_raw_data_tables()
        print("✅ 数据库表已初始化")
    except Exception as e:
        print(f"⚠️ 数据库表初始化失败: {e}")

    # 初始化所有数据源客户端
    init_clients()

    # 启动定时任务调度器
    scheduler.start()
    print("✅ 定时任务调度器已启动")

    # 初始化 SkillRegistry
    skills_dir = os.getenv("SKILLS_DIR", str(_PROJECT_ROOT.parent))
    sr.skill_registry = sr.SkillRegistry(skills_dir)
    print(f"✅ SkillRegistry 已初始化 (skills_dir={skills_dir})")

    # 启动 auth token 自动刷新后台任务
    await start_auth_auto_refresh()

    yield

    scheduler.stop()
    await close_clients()


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用"""
    app = FastAPI(
        title="智能基金服务",
        description="同花顺基金 API + 截屏助手 OCR",
        version="2.0.0",
        lifespan=_lifespan,
    )

    # --- middleware ---
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- exception handlers ---
    app.add_exception_handler(AppException, app_exception_handler)

    # --- routes ---
    @app.get("/health", summary="健康检查", tags=["系统"])
    async def health_check():
        return {"status": "ok"}

    app.include_router(router)

    # 浏览器探索服务（camoufox 可选，未安装不影响启动）
    from src.interfaces.api.routes.spy import router as spy_router
    app.include_router(spy_router)

    # WebSocket 路由需要直接注册到 app
    from src.interfaces.api.routes.terminal import router as terminal_router
    app.include_router(terminal_router)

    # 静态文件（xterm.js 等）
    app.mount(
        "/static",
        StaticFiles(directory=str(_PROJECT_ROOT / "static")),
        name="static",
    )

    return app
