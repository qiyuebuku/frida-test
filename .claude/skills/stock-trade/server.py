"""同花顺股票数据 FastAPI 服务器

将 Hook 代理服务器的股票数据封装为 RESTful API。
"""

import json
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ths_stock_client import THSStockClient


# ========== 数据模型 ==========

class PositionResponse(BaseModel):
    stock_code: str
    stock_name: str
    quantity: float
    available: float
    frozen: float
    cost_price: float
    current_price: float
    market_value: float
    profit: float
    profit_ratio: float
    market: str


class AssetResponse(BaseModel):
    total_asset: float       # 总资产
    money_remain: float      # 资金余额
    money_avl: float         # 可用资金
    money_freeze: float      # 冻结资金
    can_draw: float          # 可取资金


class OrderResponse(BaseModel):
    entrust_date: str
    entrust_time: str
    stock_code: str
    stock_name: str
    direction: str  # "买入" / "卖出"
    entrust_count: int
    entrust_price: float
    trans_count: int
    trans_price: float
    contract_no: str


class TradeResponse(BaseModel):
    trans_date: str
    trans_time: str
    stock_code: str
    stock_name: str
    direction: str
    trans_count: int
    trans_price: float
    stock_remain: int
    market: str


class PortfolioSummary(BaseModel):
    total_asset: float
    money_avl: float
    money_remain: float
    stock_market_value: float
    stock_profit: float
    position_count: int
    positions: list


class StatusResponse(BaseModel):
    database_available: bool
    fund_key: str
    tables: Optional[list] = None
    hint: Optional[str] = None


# ========== 全局客户端 ==========

client: Optional[THSStockClient] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global client
    # 从配置文件读取
    try:
        with open("config.json") as f:
            config = json.load(f)
        phone_ip = config.get("proxy", {}).get("phone_ip", "192.168.111.58")
        proxy_port = config.get("proxy", {}).get("proxy_port", 18900)
    except Exception:
        phone_ip = "192.168.111.58"
        proxy_port = 18900

    client = THSStockClient(phone_ip=phone_ip, proxy_port=proxy_port)
    yield
    await client.close()


app = FastAPI(
    title="同花顺股票数据 API",
    description="通过 Hook 代理服务器获取股票持仓、资产、委托、成交数据",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ========== API 端点 ==========

@app.get("/status", response_model=StatusResponse, tags=["系统"])
async def get_status():
    """获取数据库连接状态"""
    try:
        data = await client.get_status()
        return StatusResponse(
            database_available=data.get("database_available", False),
            fund_key=data.get("fund_key", ""),
            tables=data.get("tables"),
            hint=data.get("hint"),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/positions", response_model=list[PositionResponse], tags=["持仓"])
async def get_positions():
    """获取持仓列表"""
    try:
        positions = await client.get_positions()
        return [
            PositionResponse(
                stock_code=p.stock_code,
                stock_name=p.stock_name,
                quantity=p.stock_remain,
                available=p.stock_avl,
                frozen=p.stock_freeze,
                cost_price=p.price_buy_av,
                current_price=p.price_curr,
                market_value=p.stock_market_value,
                profit=p.stock_profit,
                profit_ratio=p.profit_loss_ratio,
                market=p.market,
            )
            for p in positions
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/assets", response_model=Optional[AssetResponse], tags=["资产"])
async def get_assets():
    """获取资产信息"""
    try:
        assets = await client.get_assets()
        if not assets:
            return None
        return AssetResponse(
            total_asset=assets.total_asset,
            money_remain=assets.money_remain,
            money_avl=assets.money_avl,
            money_freeze=assets.money_freeze,
            can_draw=assets.can_draw,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/orders", response_model=list[OrderResponse], tags=["委托"])
async def get_orders():
    """获取委托单列表"""
    try:
        orders = await client.get_orders()
        return [
            OrderResponse(
                entrust_date=o.entrust_date,
                entrust_time=o.entrust_time,
                stock_code=o.stock_code,
                stock_name=o.stock_name,
                direction="买入" if o.op == "4002" else "卖出" if o.op == "4001" else o.op,
                entrust_count=o.entrust_count,
                entrust_price=o.entrust_price,
                trans_count=o.trans_count,
                trans_price=o.trans_price,
                contract_no=o.contract_no,
            )
            for o in orders
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/history", response_model=list[TradeResponse], tags=["成交"])
async def get_history(limit: int = 100):
    """获取历史成交记录"""
    try:
        trades = await client.get_history(limit=limit)
        return [
            TradeResponse(
                trans_date=t.trans_date,
                trans_time=t.trans_time,
                stock_code=t.stock_code,
                stock_name=t.stock_name,
                direction=t.op_name,
                trans_count=t.trans_count,
                trans_price=t.price_trans,
                stock_remain=t.stock_remain,
                market=t.market,
            )
            for t in trades
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/daily", response_model=list[TradeResponse], tags=["成交"])
async def get_daily_trades():
    """获取当日成交记录"""
    try:
        trades = await client.get_daily_trades()
        return [
            TradeResponse(
                trans_date=t.trans_date,
                trans_time=t.trans_time,
                stock_code=t.stock_code,
                stock_name=t.stock_name,
                direction=t.op_name,
                trans_count=t.trans_count,
                trans_price=t.price_trans,
                stock_remain=t.stock_remain,
                market=t.market,
            )
            for t in trades
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/portfolio", response_model=PortfolioSummary, tags=["汇总"])
async def get_portfolio():
    """获取投资组合摘要"""
    try:
        summary = await client.get_portfolio_summary()
        return PortfolioSummary(**summary)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ========== 健康检查 ==========

@app.get("/health", tags=["系统"])
async def health_check():
    """健康检查"""
    try:
        status = await client.get_status()
        return {
            "status": "healthy" if status.get("database_available") else "degraded",
            "database": status.get("database_available", False),
        }
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
