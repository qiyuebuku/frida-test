"""路由汇总：将各模块的 router 合并为统一 router"""

from fastapi import APIRouter

from src.interfaces.api.routes.trade import start_auth_auto_refresh
from src.interfaces.api.routes import (
    file,
    fund_query,
    llm_proxy,
    market,
    skill,
    strategy,
    task,
    trade,
    watchlist,
)

router = APIRouter()
router.include_router(fund_query.router)
router.include_router(market.router)
router.include_router(trade.router)
router.include_router(strategy.router)
router.include_router(task.router)
router.include_router(skill.router)
router.include_router(file.router)
router.include_router(watchlist.router)
router.include_router(llm_proxy.router)
