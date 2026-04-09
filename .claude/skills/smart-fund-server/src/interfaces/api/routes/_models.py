"""Pydantic Models 和常量"""

from pydantic import BaseModel, Field


class FundRankingRequest(BaseModel):
    sort_type: str = Field("year", description="排序字段: year/hyear/tmonth/month/week/nowyear/tyear/fyear/now/sharpeYear/maxDrawDownYear")
    sort: str = Field("DESC", description="排序方向: DESC/ASC")
    limit: int = Field(30, description="每页数量", ge=1, le=200)
    offset: int = Field(0, description="偏移量", ge=0)
    fund_type: str = Field(None, description="基金类型代码（如 282001001=股票型）")
    fund_company: str = Field(None, description="基金公司 orgid")
    min_scale: float = Field(None, description="最小规模（元），默认1000万")
    board: str = Field(None, description="排行榜名称（涨幅榜/反弹榜/人气榜/加仓榜/超额榜），会自动获取配置")
    strategy: str = Field(None, description="策略筛选key（fund0001=年年正收益/fund0002=三年翻倍/fund0010=能涨抗跌 等）")


class BuyFundRequest(BaseModel):
    fund_code: str = Field(..., description="基金代码")
    amount: float = Field(..., description="买入金额（元）", gt=0)
    use_wallet: bool = Field(True, description="是否使用活期宝支付")
    password: str = Field(None, description="交易密码（明文，自动MD5）或MD5哈希；优先级高于 set_password")
    reason: str = Field(None, description="买入理由")


class SellFundRequest(BaseModel):
    fund_code: str = Field(..., description="基金代码")
    share_vol: float = Field(None, description="赎回份额数量")
    sell_all: bool = Field(False, description="是否全部赎回")
    password: str = Field(None, description="交易密码（明文或MD5）")


class CancelOrderRequest(BaseModel):
    order_no: str = Field(..., description="订单号 appSheetSerialNo")
    password: str = Field(None, description="交易密码（明文或MD5）")


class TradeAuthUpdate(BaseModel):
    key1: str = Field(None, description="设备UUID")
    key2: str = Field(None, description="签名hash")
    key3: str = Field(None, description="客户ID (custId)")
    key5: str = Field(None, description="JWT token")
    user_id: str = Field(None, description="用户ID")
    session_id: str = Field(None, description="会话ID")
    cookie: str = Field(None, description="用户cookie")


class TradePasswordUpdate(BaseModel):
    password: str = Field(..., description="交易密码（明文）")


# ==================== Constants ====================

PERIODIC_RATE_TYPES = {"day", "week", "month", "quarter", "year"}
ANNOUNCEMENT_CATS = {"all", "report", "dividend", "change", "operation", "other"}
HOTLIST_MARKETS = {"a", "hk", "us"}
HOTLIST_PLATE_TYPES = {"concept", "industry"}
