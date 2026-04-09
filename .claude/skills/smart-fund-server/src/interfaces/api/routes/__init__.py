"""路由汇总：将各模块的 router 合并为统一 router"""

from fastapi import APIRouter

from src.interfaces.api.routes.trade import start_auth_auto_refresh
from src.interfaces.api.routes import fund_query, market, trade, strategy, task, skill, file

router = APIRouter()
router.include_router(fund_query.router)
router.include_router(market.router)
router.include_router(trade.router)
router.include_router(strategy.router)
router.include_router(task.router)
router.include_router(skill.router)
router.include_router(file.router)
