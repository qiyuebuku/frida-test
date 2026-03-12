"""交易路由：认证管理/交易账户/基金交易"""

import asyncio
import base64
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Body

from routers._utils import safe_call, client
from routers._models import (
    BuyFundRequest, SellFundRequest, CancelOrderRequest,
    TradeAuthUpdate, TradePasswordUpdate,
)
from services import fund_db

router = APIRouter()
logger = logging.getLogger("auth")


# ==================== 认证辅助函数 ====================

def _decode_jwt_exp(jwt_token: str):
    """解析同花顺 JWT 获取过期时间（秒级时间戳）"""
    if not jwt_token:
        return None
    for part_idx in (1, 0):
        try:
            parts = jwt_token.split(".")
            if len(parts) <= part_idx:
                continue
            segment = parts[part_idx]
            pad = 4 - len(segment) % 4
            if pad != 4:
                segment += "=" * pad
            decoded = base64.urlsafe_b64decode(segment)
            data = json.loads(decoded)
            exp = data.get("exp")
            if exp is not None:
                if exp > 10000000000:
                    exp = exp // 1000
                return exp
        except Exception:
            continue
    return None


def _get_auth_expires_at():
    """读取 auth_cache.json 中的过期时间，返回 (expires_at, is_expired)"""
    cache_file = Path(__file__).parent.parent / "auth_cache.json"
    if not cache_file.exists():
        return None, True
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        expires_at = data.get("expires_at")
        if not expires_at:
            return None, False  # 无过期信息，假设有效
        return expires_at, int(time.time()) >= expires_at
    except Exception:
        return None, True


async def _login_by_password():
    """使用密码登录获取 token（会踢掉手机端登录）

    Returns:
        成功返回 auth 数据 dict，失败返回 None
    """
    import httpx

    config_file = Path(__file__).parent.parent / "config.json"
    if not config_file.exists():
        logger.warning("config.json 不存在，无法使用密码登录")
        return None

    with open(config_file, "r", encoding="utf-8") as f:
        config = json.load(f)

    account = config.get("trade_account")
    password = config.get("trade_password")
    if not account or not password:
        logger.warning("config.json 中未设置 trade_account 或 trade_password")
        return None

    password_md5 = hashlib.md5(password.encode()).hexdigest().upper()
    device_id = "7246091a5f126b63"
    device_sign = "2293a78f6581c12bbb334759458d4de3"

    url = "https://trade.5ifund.com/rz/account/login/noauth/v1/result/safe/check"
    headers = {
        "token": "-1",
        "custId": "-1",
        "source": "SDK",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Hexin_Gphone/11.48.03 (Royal Flush) innerversion/G037.08.194.1.32 hxtheme/0 GphoneIjiJinSDK/V7.39.01 ifOperator/145",
        "Client-Referer": "",
        "Host": "trade.5ifund.com",
        "Connection": "Keep-Alive",
        "Accept-Encoding": "gzip",
    }
    form_data = {
        "key1": device_id,
        "uId": device_id,
        "key2": device_sign,
        "password": password_md5,
        "ipAddress": "null",
        "thsUserId": "690359103",
        "device": "OnePlus PLQ110",
        "deviceName": "OnePlus ",
        "account": account,
        "loginSource": "SDK",
        "operator": "145",
    }

    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.post(url, headers=headers, data=form_data)
            resp.raise_for_status()
            result = resp.json()

        if result.get("code") != "0000":
            logger.warning(f"密码登录失败: {result.get('message', '未知错误')}")
            return None

        data_field = result.get("data", {})
        key3 = data_field.get("key3") or data_field.get("custId") or account
        key5 = data_field.get("key5")
        if not key5:
            logger.warning("登录响应中未找到 key5")
            return None

        cookie = ""
        if "set-cookie" in resp.headers:
            cookie = resp.headers["set-cookie"]

        expires_at = _decode_jwt_exp(key5)

        return {
            "auth": {
                "key1": device_id,
                "key2": device_sign,
                "key3": key3,
                "key4": data_field.get("key4", "auth"),
                "key5": key5,
                "userId": key3,
                "sessionId": "",
                "cookie": cookie,
                "account": key3,
            },
            "expires_at": expires_at,
            "sync_source": "server_password_login",
        }
    except Exception as e:
        logger.error(f"密码登录异常: {e}")
        return None


def _save_auth_cache(cache_data: dict):
    """保存认证数据到 auth_cache.json 并刷新内存"""
    cache_file = Path(__file__).parent.parent / "auth_cache.json"
    cache_data.setdefault("last_sync", int(time.time()))
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, indent=2, ensure_ascii=False)
    if client:
        client.reload_auth_if_updated()


# ---------- 后台自动刷新 ----------

_auto_refresh_task = None


async def start_auth_auto_refresh():
    """启动后台定时检查 token 是否快过期，自动密码登录续期"""
    global _auto_refresh_task
    if _auto_refresh_task is not None:
        return
    _auto_refresh_task = asyncio.create_task(_auth_auto_refresh_loop())
    logger.info("Auth 自动刷新后台任务已启动")


async def _auth_auto_refresh_loop():
    """每小时检查一次，token 剩余 < 3 天时自动密码登录续期"""
    REFRESH_THRESHOLD = 3 * 24 * 3600  # 3天
    CHECK_INTERVAL = 3600  # 1小时

    while True:
        try:
            await asyncio.sleep(CHECK_INTERVAL)
            expires_at, is_expired = _get_auth_expires_at()
            if expires_at is None:
                continue

            remaining = expires_at - int(time.time())
            if remaining > REFRESH_THRESHOLD:
                continue

            status = "已过期" if is_expired else f"剩余 {remaining // 3600} 小时"
            logger.info(f"Token {status}，自动密码登录续期...")

            result = await _login_by_password()
            if result:
                _save_auth_cache(result)
                logger.info(f"自动续期成功，新过期时间: {result.get('expires_at')}")
            else:
                logger.error("自动续期失败，密码登录未返回有效数据")
        except Exception as e:
            logger.error(f"自动刷新异常: {e}")


# ==================== 认证 API ====================

@router.get("/api/auth/status", summary="认证状态", tags=["交易账户"])
async def auth_status():
    """查看认证参数缓存状态"""
    try:
        cache_file = Path(__file__).parent.parent / "auth_cache.json"
        if not cache_file.exists():
            return {
                "status": "error",
                "message": "认证缓存不存在"
            }

        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        expires_at = data.get("expires_at")
        if expires_at:
            now = int(time.time())
            remaining_seconds = expires_at - now
            remaining_days = remaining_seconds // (24 * 3600)
            is_expired = now >= expires_at
            expires_time = datetime.fromtimestamp(expires_at).strftime("%Y-%m-%d %H:%M:%S")
        else:
            remaining_days = None
            is_expired = None
            expires_time = None

        return {
            "status": "success",
            "data": {
                "expires_at": expires_time,
                "remaining_days": remaining_days,
                "is_expired": is_expired,
                "status": "expired" if is_expired else "valid",
                "sync_source": data.get("sync_source", "unknown"),
                "last_sync": data.get("last_sync"),
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取认证状态失败: {e}")


@router.post("/api/auth/refresh", summary="推送认证Token", tags=["交易账户"])
async def auth_refresh(body: dict = Body(...)):
    """从 Hook 或客户端推送 token 数据并保存到 auth_cache.json"""
    try:
        auth = body.get("auth")
        if not isinstance(auth, dict) or not auth.get("key1") or not auth.get("key5"):
            raise HTTPException(status_code=400, detail="缺少必要字段: auth.key1, auth.key5")

        cache_data = {
            "auth": auth,
            "expires_at": body.get("expires_at"),
            "last_sync": int(time.time()),
            "sync_source": body.get("sync_source", "client_push")
        }

        _save_auth_cache(cache_data)

        return {
            "status": "success",
            "message": "认证参数已更新",
            "account": auth.get("key3", "")
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新认证失败: {e}")


@router.post("/api/auth/login", summary="密码登录获取Token", tags=["交易账户"])
async def auth_login():
    """服务端直接使用 config.json 中的账号密码登录获取 token（会踢掉手机端）"""
    result = await _login_by_password()
    if not result:
        raise HTTPException(status_code=500, detail="密码登录失败，请检查 config.json 中的 trade_account/trade_password")

    _save_auth_cache(result)

    expires_at = result.get("expires_at")
    expires_time = datetime.fromtimestamp(expires_at).strftime("%Y-%m-%d %H:%M:%S") if expires_at else "未知"

    return {
        "status": "success",
        "message": "密码登录成功，token 已更新",
        "account": result["auth"].get("key3", ""),
        "expires_at": expires_time,
    }


# ==================== 交易账户 ====================

@router.get("/api/trade/overview", summary="账户总览", tags=["交易账户"])
async def trade_overview():
    """账户总览（总资产、累计盈亏、当日盈亏、风险等级等）"""
    return await safe_call(client.get_account_overview())


@router.get("/api/trade/positions", summary="基金持仓", tags=["交易账户"])
async def trade_positions():
    """基金持仓列表（持仓基金明细、市值、收益等）"""
    return await safe_call(client.get_fund_positions())


@router.get("/api/trade/wallet", summary="活期宝", tags=["交易账户"])
async def trade_wallet():
    """活期宝/超级T+0（货币基金收益、可用余额等）"""
    return await safe_call(client.get_wallet_info())


@router.get("/api/trade/wallet/home", summary="钱包首页", tags=["交易账户"])
async def trade_wallet_home():
    """钱包首页（活期宝余额、冻结金额、累计收益等）"""
    return await safe_call(client.get_wallet_home())


@router.get("/api/trade/autoinvest/list", summary="定投计划列表", tags=["交易账户"])
async def trade_autoinvest_list(
    page_size: int = Query(20, description="每页数量"),
    status: str = Query("N", description="状态: N=全部, 1=执行中, 2=暂停"),
):
    """定投计划列表"""
    return await safe_call(client.get_auto_invest_list(page_size, status))


@router.get("/api/trade/autoinvest/summary", summary="定投汇总", tags=["交易账户"])
async def trade_autoinvest_summary():
    """定投汇总（总金额、下次执行日期、正常/暂停计划数等）"""
    return await safe_call(client.get_auto_invest_summary())


@router.get("/api/trade/binding", summary="账户绑定信息", tags=["交易账户"])
async def trade_binding():
    """账户绑定信息（客户ID、姓名、身份证号等）"""
    return await safe_call(client.get_account_binding())


@router.get("/api/trade/all", summary="全部交易数据", tags=["交易账户"])
async def trade_all():
    """一次性获取所有交易账户数据"""
    return await safe_call(client.get_trade_account_all())


@router.post("/api/trade/auth", summary="更新交易认证", tags=["交易账户"])
async def trade_auth_update(req: TradeAuthUpdate = Body(...)):
    """更新交易认证参数（token 过期后需要从 Hook 重新捕获）"""
    client.update_trade_auth(
        key1=req.key1, key2=req.key2, key3=req.key3, key5=req.key5,
        user_id=req.user_id, session_id=req.session_id, cookie=req.cookie,
    )
    return {"status": "ok", "message": "交易认证参数已更新"}


# ==================== 基金交易 ====================

@router.post("/api/trade/password", summary="设置交易密码", tags=["基金交易"])
async def trade_password_update(req: TradePasswordUpdate):
    """设置交易密码（明文）"""
    client.update_trade_password(req.password)
    return {"status": "ok", "message": "交易密码已设置"}


@router.post("/api/trade/buy", summary="买入基金", tags=["基金交易"])
async def trade_buy(req: BuyFundRequest):
    """买入基金（完整流程：初始化->检查->下单）"""
    # Step 0: 买入初始化，检查 maxBuy
    try:
        init_resp = await client._proxy_request(
            "/rz/trade/dubbo/subscribe/init",
            body=f"fundCode={req.fund_code}",
            content_type="application/x-www-form-urlencoded",
        )
        # _proxy_request 已经解析好了 result 层，直接取 data
        init_data = init_resp.get("data", init_resp)

        # 调试输出
        print(f"init_resp keys: {list(init_resp.keys())}")
        print(f"init_data keys: {list(init_data.keys())[:10]}")
        print(f"maxBuy raw: {init_data.get('maxBuy')}")
        print(f"minBuy raw: {init_data.get('minBuy')}")

        max_buy = float(init_data.get("maxBuy", 0))
        min_buy = float(init_data.get("minBuy", 0))
        fund_name = init_data.get("paramOpenFundAccBean", {}).get("fundName", req.fund_code)

        # 保存限额信息到数据库（无论是否能买入都保存）
        try:
            # max_buy 是当前可买金额（会随待确认订单变化）
            # 如果 max_buy 很大（如 99999999999999.99），说明是基金本身的每日限额
            daily_limit = max_buy if max_buy > 1000000 else 0
            fund_db.save_fund_limit(
                fund_code=req.fund_code,
                fund_name=fund_name,
                min_buy=min_buy,
                max_buy=max_buy,
                daily_limit=daily_limit,
                is_suspended=False
            )
        except Exception as e:
            print(f"保存限额信息失败: {e}")

        # 检查待确认额度
        if max_buy < req.amount:
            return {
                "status": "error",
                "message": f"买入失败：待确认额度不足",
                "details": {
                    "fund_code": req.fund_code,
                    "fund_name": fund_name,
                    "requested_amount": req.amount,
                    "max_buy": max_buy,
                    "min_buy": min_buy,
                    "reason": "待确认订单占用额度，需等待现有订单确认后才能继续购买"
                }
            }

        # 检查最小买入金额
        if req.amount < min_buy:
            return {
                "status": "error",
                "message": f"买入失败：低于最小买入金额",
                "details": {
                    "fund_code": req.fund_code,
                    "fund_name": fund_name,
                    "requested_amount": req.amount,
                    "min_buy": min_buy
                }
            }
    except Exception as e:
        # 初始化失败，可能是暂停申购，记录下来
        try:
            fund_db.mark_fund_suspended(req.fund_code, reason=str(e))
        except:
            pass
        raise HTTPException(status_code=500, detail=f"买入初始化失败: {e}")

    # 调用同花顺 API 买入
    result = await safe_call(client.buy_fund(req.fund_code, req.amount, req.use_wallet, req.password))

    # 解析结果
    order_no = result.get("app_sheet_serial_no") or result.get("appSheetSerialNo")
    fund_name = result.get("fund_name", "")

    # 检查订单号是否存在
    if not order_no:
        raw_resp = result.get("raw_response", {})
        error_code = -1
        code = ""
        message = raw_resp.get("message", "未知错误")

        return {
            "status": "error",
            "message": f"买入失败: {message}",
            "details": {
                "fund_code": req.fund_code,
                "error_code": error_code,
                "code": code,
                "raw_message": message,
                "raw_result": result
            }
        }

    # 保存交易记录到数据库
    try:
        fund_db.save_trade(
            fund_code=req.fund_code,
            fund_name=fund_name,
            action="buy",
            amount=req.amount,
            shares=None,
            order_no=order_no,
            reason=req.reason,
            api_response=result
        )
    except Exception as e:
        pass

    return {
        "status": "success",
        "message": f"买入成功: {fund_name}",
        "data": {
            "fund_code": req.fund_code,
            "fund_name": fund_name,
            "amount": req.amount,
            "order_no": order_no,
            "raw_result": result
        }
    }


@router.get("/api/trade/order/{order_no}", summary="查询订单", tags=["基金交易"])
async def trade_order_detail(order_no: str):
    """查询交易订单详情"""
    return await safe_call(client.get_order_detail(order_no))


@router.get("/api/trade/orders", summary="订单列表", tags=["基金交易"])
async def trade_order_list(
    days: int = Query(30, description="查询天数，默认30天"),
    op_type: str = Query("all", description="操作类型: all=全部"),
    limit: int = Query(20, description="每页条数"),
    offset: int = Query(1, description="页码（从1开始）"),
):
    """查询交易订单列表"""
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    raw_result = await safe_call(client.get_order_list(start_date, end_date, op_type, limit, offset))

    return {
        "status": "success",
        "data": raw_result
    }


@router.post("/api/trade/sell", summary="赎回基金", tags=["基金交易"])
async def trade_sell(req: SellFundRequest):
    """赎回基金（完整流程：获取持仓->初始化->提交赎回）"""
    share_vol_str = f"{req.share_vol:.2f}" if req.share_vol else None
    return await safe_call(client.sell_fund(req.fund_code, share_vol_str, req.sell_all, req.password))


@router.post("/api/trade/cancel", summary="撤销订单", tags=["基金交易"])
async def trade_cancel(req: CancelOrderRequest):
    """撤销交易订单"""
    return await safe_call(client.cancel_order(req.order_no, req.password))
