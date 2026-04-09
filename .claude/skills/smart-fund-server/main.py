"""智能基金服务 - 合并同花顺基金 API + 截屏助手 OCR"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from services.db import fund_db
from services.db import ocr_db
from services.db import task_db
from services.db import raw_data
from services.tools import skill_registry as sr
from services.tools.scheduler import scheduler
from routers import router, start_auth_auto_refresh
from routers._utils import init_clients, close_clients


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    import os
    skills_dir = os.getenv("SKILLS_DIR", str(Path(__file__).parent.parent))
    sr.skill_registry = sr.SkillRegistry(skills_dir)
    print(f"✅ SkillRegistry 已初始化 (skills_dir={skills_dir})")

    # 启动 auth token 自动刷新后台任务
    await start_auth_auto_refresh()

    yield

    scheduler.stop()
    await close_clients()


app = FastAPI(
    title="智能基金服务",
    description="同花顺基金 API + 截屏助手 OCR",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", summary="健康检查", tags=["系统"])
async def health_check():
    return {"status": "ok"}


app.include_router(router)

# 浏览器探索服务（camoufox 可选，未安装不影响启动）
from routers.spy import router as spy_router
app.include_router(spy_router)

# 静态文件（xterm.js 等）
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

# WebSocket 路由需要直接注册到 app（子 router 中 WebSocket 可能 404）
from routers.terminal import router as terminal_router
app.include_router(terminal_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8900, reload=True)
