"""智能基金服务 - 合并同花顺基金 API + 截屏助手 OCR"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.ths_fund_client import THSFundClient
from services import fund_db
from services import db as ocr_db
from services import task_db
from services import skill_registry as sr
from services.scheduler import scheduler
from routers import router, set_client, start_auth_auto_refresh

client: THSFundClient = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global client

    # 初始化数据库表
    try:
        fund_db.init_tables()
        ocr_db.init_ocr_tables()
        task_db.init_task_tables()
        print("✅ 数据库表已初始化")
    except Exception as e:
        print(f"⚠️ 数据库表初始化失败: {e}")

    # 初始化同花顺客户端
    client = THSFundClient()
    set_client(client)

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
    await client.close()


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8900, reload=True)
