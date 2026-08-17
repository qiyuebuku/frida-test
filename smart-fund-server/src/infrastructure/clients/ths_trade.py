"""同花顺交易端点客户端 — 按需直调真机 hook 服务器（18900）。

设备端契约（MainHook，见逆向交接说明 §3.8/3.11/3.12）：
- GET  /stock/trade/query?name={name}
       → {"query":"proto_NNNN","pageId":N,"ok":true,"elapsed_ms":N,"data":{...}}
       → 失败 {"ok":false,"error":"..."}（预热期/登录门/超时等原因文本）
- POST /stock/trade/login   → {"result":"already_logged_in"|"success"|"fail",...}
       主动登录（统一登录链）：预热线程与查询登录门同链，冷启动后可主动调
       用替代等待 App 自发静默重登（实测 40s+ vs 旧机制盲等 2~3.5 分钟）
- POST /stock/trade/order   body={"action","code","price","qty","confirm":"true"}
- POST /stock/trade/cancel  body={"entrust_no","stock_code","stock_name",
       "market_code","shareholder_account","withdrawable_qty","confirm":"true"}
       → 成功附 business_ok/business_message（25102 stockArr[0].code=="0"）
- GET  /stock/trade/transfer/banks（只读）
- POST /stock/trade/transfer（已实现未验证，默认禁用）

设计决策（2026-08-17，用户指定）：交易型接口不挂调度任务、不建快照表、
不做任何缓存，每次有需求直接调用设备端点取最新数据。

串行铁律：设备端交易协议按 protocolId 注册观察者（固定协议页路由），
并发请求会互相覆盖/串帧（逆向 pitfalls #24）——全部请求经全局锁串行。
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class THSTradeError(Exception):
    """设备端交易端点业务/状态错误（含原因码，供上层降级为 unavailable）。"""

    def __init__(
        self,
        message: str,
        *,
        reason_code: str = "trade_endpoint_error",
        raw: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.raw = raw or {}


def _classify_device_error(message: str) -> str:
    """把设备端 error 文本映射为稳定原因码（映射子串见 MainHook ensureTradeRuntimeReady）。"""
    text = (message or "").lower()
    if "not logged in" in text or "relogin" in text:
        return "trade_account_not_logged_in"
    if "classloader not ready" in text or "runtime" in text:
        return "trade_runtime_not_ready"
    if "account manager" in text or "f(119)" in text or "no trade account" in text:
        return "trade_account_not_recovered"
    if "timeout" in text:
        return "trade_response_timeout"
    if "confirm" in text:
        return "trade_confirm_required"
    return "trade_endpoint_error"


# funds(1807) 字段 ID → 语义名（设备端已输出语义键，这里补全未知字段兜底映射）
FUNDS_FIELD_NAMES = {
    "36628": "total_assets",
    "36629": "float_profit",
    "36625": "available_amount",
    "36626": "total_market_value",
    "36623": "withdrawable_amount",
}


def parse_money(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


class THSTradeClient:
    """同花顺交易 SDK 端点的服务端客户端（同步实现，线程安全，无缓存）。

    MCP 层经 asyncio.to_thread 调用；所有请求在全局 threading.Lock 内串行
    执行（固定协议页不能并发）。写端点客户端强制 confirm=True 门控。
    """

    DEFAULT_BASE_URL = "http://192.168.31.162:18900"
    READ_QUERIES = (
        "positions",
        "funds",
        "today_order",
        "today_deal",
        "hist_order",
        "hist_deal",
    )

    _instance: "THSTradeClient | None" = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self._base_url = (
            base_url
            or os.getenv("THS_TRADE_BASE_URL", "").strip()
            or self.DEFAULT_BASE_URL
        ).rstrip("/")
        self._timeout = timeout if timeout is not None else float(
            os.getenv("THS_TRADE_TIMEOUT", "30")
        )
        self._lock = threading.Lock()
        # Connection: close — 设备端是自研 LightweightHTTP，keep-alive 长连接
        # 空闲后被服务端单方面关闭，httpx 连接池复用死连接会 RemoteProtocolError
        self._client = httpx.Client(
            timeout=self._timeout,
            headers={"Connection": "close"},
        )

    @classmethod
    def shared(cls) -> "THSTradeClient":
        """进程级单例（保证全局串行锁真正覆盖所有调用方）。"""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def close(self) -> None:
        with self._lock:
            self._client.close()

    # ------------------------------------------------------------------
    # 内部：串行请求
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """在全局串行锁内发请求；设备端 ok=false 抛 THSTradeError。

        写请求（POST）用更长超时（≥60s）：实测撤单响应可超 30s 而操作已在
        设备端执行——写超时后必须先查委托状态再决定是否重试，禁止盲目重发。
        """
        url = f"{self._base_url}{path}"
        timeout = max(self._timeout, 60.0) if method == "POST" else self._timeout
        with self._lock:
            try:
                resp = self._client.request(
                    method, url, json=json_body, timeout=timeout
                )
            except httpx.HTTPError as exc:
                raise THSTradeError(
                    f"trade endpoint unreachable: {exc}",
                    reason_code="trade_endpoint_unreachable",
                ) from exc
        if resp.status_code != 200:
            raise THSTradeError(
                f"trade endpoint HTTP {resp.status_code}",
                reason_code="trade_endpoint_http_error",
            )
        try:
            payload = resp.json()
        except ValueError as exc:
            raise THSTradeError(
                "trade endpoint returned non-JSON",
                reason_code="trade_endpoint_protocol_error",
            ) from exc
        if payload.get("ok") is False:
            error_text = str(payload.get("error", "unknown device error"))
            raise THSTradeError(
                error_text,
                reason_code=_classify_device_error(error_text),
                raw=payload,
            )
        return payload

    def _read_query(self, name: str) -> dict[str, Any]:
        """只读查询（无缓存，每次直调设备端点取最新数据）。"""
        if name not in self.READ_QUERIES:
            raise ValueError(
                f"unknown query name '{name}', supported: {self.READ_QUERIES}"
            )
        return self._request("GET", f"/stock/trade/query?name={name}")

    # ------------------------------------------------------------------
    # 只读端点（六查询）
    # ------------------------------------------------------------------

    def positions(self) -> dict[str, Any]:
        """持仓（协议 1891，含 14 列 schema；当前测试账户空仓返回空表）。"""
        return self._read_query("positions")

    def funds(self) -> dict[str, Any]:
        """资金（协议 1807 字段 ID 型，data.fields 为语义键对象）。"""
        return self._read_query("funds")

    def today_orders(self) -> dict[str, Any]:
        """当日委托（1811）。"""
        return self._read_query("today_order")

    def today_deals(self) -> dict[str, Any]:
        """当日成交（1810，响应帧旧协议号陷阱已由设备端处理）。"""
        return self._read_query("today_deal")

    def hist_orders(self) -> dict[str, Any]:
        """历史委托（1825，静态日期模板 2025-01-01 起全窗口）。"""
        return self._read_query("hist_order")

    def hist_deals(self) -> dict[str, Any]:
        """历史成交（1824）。"""
        return self._read_query("hist_deal")

    def normalized_funds(self) -> dict[str, Any]:
        """funds.fields 语义化：金额转 float、补齐未知字段 ID 的语义名。"""
        payload = self.funds()
        fields = (payload.get("data") or {}).get("fields") or {}
        normalized: dict[str, Any] = {}
        for key, value in fields.items():
            semantic = FUNDS_FIELD_NAMES.get(key.replace("field_", ""), key)
            normalized[semantic] = {"raw": value, "value": parse_money(value)}
        return {"funds": normalized, "elapsed_ms": payload.get("elapsed_ms")}

    # ------------------------------------------------------------------
    # 写端点（真实交易动作！客户端强制 confirm 门控）
    # ------------------------------------------------------------------

    def submit_order(
        self,
        *,
        action: str,
        code: str,
        price: str,
        qty: str,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """买入/卖出委托（协议 1820/1821，真实下单）。confirm 必须 True。"""
        action = action.strip().lower()
        if action not in ("buy", "sell"):
            raise ValueError("action must be 'buy' or 'sell'")
        if not confirm:
            raise THSTradeError(
                "order requires explicit confirm=True (real money operation)",
                reason_code="trade_confirm_required",
            )
        body = {
            "action": action,
            "code": code.strip(),
            "price": price.strip(),
            "qty": qty.strip(),
            "confirm": "true",
        }
        return self._request("POST", "/stock/trade/order", json_body=body)

    def cancel_order(
        self,
        *,
        entrust_no: str,
        stock_code: str,
        stock_name: str,
        market_code: str,
        shareholder_account: str,
        withdrawable_qty: str = "0",
        confirm: bool = False,
    ) -> dict[str, Any]:
        """撤单（协议 25102 六段式 entry，真实撤单）。confirm 必须 True。"""
        if not confirm:
            raise THSTradeError(
                "cancel requires explicit confirm=True (real money operation)",
                reason_code="trade_confirm_required",
            )
        body = {
            "entrust_no": entrust_no.strip(),
            "stock_code": stock_code.strip(),
            "stock_name": stock_name.strip(),
            "market_code": market_code.strip(),
            "shareholder_account": shareholder_account.strip(),
            "withdrawable_qty": withdrawable_qty.strip(),
            "confirm": "true",
        }
        return self._request("POST", "/stock/trade/cancel", json_body=body)

    def login(self) -> dict[str, Any]:
        """主动登录（POST /stock/trade/login，设备端唯一登录发起链）。

        已登录时秒回 already_logged_in；冷启动后直接调 f2s.q 登录执行器并
        同步等回调（实测 40s+，POST 长超时规则覆盖）。收到
        trade_account_not_logged_in 后可先 login 再重试原查询。
        """
        return self._request("POST", "/stock/trade/login", json_body={})

    def transfer_banks(self) -> dict[str, Any]:
        """存管银行列表（只读，协议 1830）。"""
        return self._request("GET", "/stock/trade/transfer/banks")

    def transfer(
        self,
        *,
        amount: str,
        bank_password: str,
        bank_index: str = "0",
        confirm: bool = False,
        allow_unverified: bool = False,
    ) -> dict[str, Any]:
        """银证转账（协议 1826，设备端未重放验证）。默认禁用。"""
        if not allow_unverified:
            raise THSTradeError(
                "transfer endpoint is implemented but replay-unverified; "
                "pass allow_unverified=True after manual verification",
                reason_code="trade_transfer_disabled",
            )
        if not confirm:
            raise THSTradeError(
                "transfer requires explicit confirm=True (real money operation)",
                reason_code="trade_confirm_required",
            )
        body = {
            "direction": "in",
            "amount": amount.strip(),
            "bank_password": bank_password,
            "bank_index": bank_index.strip(),
            "confirm": "true",
        }
        return self._request("POST", "/stock/trade/transfer", json_body=body)

    # ------------------------------------------------------------------
    # 诊断
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """设备端交易 SDK 状态（不进缓存、不抛业务错误）。"""
        try:
            return self._request("GET", "/stock/trade/status")
        except THSTradeError as exc:
            return {"ok": False, "error": str(exc), "reason_code": exc.reason_code}
