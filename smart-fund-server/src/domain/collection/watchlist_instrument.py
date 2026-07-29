"""Watchlist instrument identity and deterministic code validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


InstrumentType = Literal["stock", "fund", "index"]

_MARKET_PREFIXES = ("sh", "sz", "bj")
_FUND_NAME_MARKERS = ("基金", "ETF", "LOF", "联接", "QDII")


@dataclass(frozen=True)
class InstrumentIdentity:
    code: str
    name: str
    instrument_type: InstrumentType
    exchange_traded: bool


def normalize_instrument(
    *,
    code: str,
    instrument_type: str,
    name: str = "",
) -> InstrumentIdentity:
    """Normalize an A-share stock, Chinese fund/ETF, or mainland index."""

    raw_code = str(code or "").strip().lower().replace(".", "")
    normalized_name = str(name or "").strip()
    requested_type = str(instrument_type or "auto").strip().lower()
    if requested_type == "etf":
        requested_type = "fund"
    if requested_type not in {"auto", "stock", "fund", "index"}:
        raise ValueError("type 必须是 auto、stock、fund、etf 或 index")

    prefix, digits = _split_market_code(raw_code)
    if len(digits) != 6 or not digits.isdigit():
        raise ValueError(f"不支持的标的代码格式: {code!r}")

    resolved_type = _resolve_type(
        requested_type=requested_type,
        prefix=prefix,
        digits=digits,
        name=normalized_name,
    )

    if resolved_type == "fund":
        return InstrumentIdentity(
            code=digits,
            name=normalized_name,
            instrument_type="fund",
            exchange_traded=is_exchange_traded_fund(digits),
        )

    market = prefix or _infer_market(digits, resolved_type)
    _validate_market_code(market=market, digits=digits, instrument_type=resolved_type)
    return InstrumentIdentity(
        code=f"{market}{digits}",
        name=normalized_name,
        instrument_type=resolved_type,
        exchange_traded=False,
    )


def is_exchange_traded_fund(code: str) -> bool:
    digits = str(code or "").strip().lower()
    if digits.startswith(_MARKET_PREFIXES):
        digits = digits[2:]
    if len(digits) != 6 or not digits.isdigit():
        return False
    if digits.startswith(("15", "16")):
        return True
    if digits.startswith(("50", "51", "52", "56", "58")):
        return not digits.startswith("519")
    return False


def _split_market_code(code: str) -> tuple[str, str]:
    if code.startswith(_MARKET_PREFIXES):
        return code[:2], code[2:]
    if len(code) == 8 and code[-2:] in _MARKET_PREFIXES:
        return code[-2:], code[:-2]
    return "", code


def _resolve_type(
    *,
    requested_type: str,
    prefix: str,
    digits: str,
    name: str,
) -> InstrumentType:
    if requested_type != "auto":
        return requested_type  # type: ignore[return-value]
    if is_exchange_traded_fund(digits):
        return "fund"
    if any(marker.lower() in name.lower() for marker in _FUND_NAME_MARKERS):
        return "fund"
    if (prefix == "sh" and digits.startswith("000")) or (
        prefix == "sz" and digits.startswith("399")
    ):
        return "index"
    if digits.startswith(("6", "3", "4", "8")):
        return "stock"
    raise ValueError(
        f"代码 {digits} 无法自动区分股票与场外基金，请显式指定 type"
    )


def _infer_market(digits: str, instrument_type: InstrumentType) -> str:
    if instrument_type == "index":
        if digits.startswith("399"):
            return "sz"
        if digits.startswith("000"):
            return "sh"
        raise ValueError(f"指数代码 {digits} 缺少明确市场前缀")
    if digits.startswith(("6", "9")):
        return "sh"
    if digits.startswith(("0", "3")):
        return "sz"
    if digits.startswith(("4", "8")):
        return "bj"
    raise ValueError(f"无法判断股票代码 {digits} 的交易所")


def _validate_market_code(
    *,
    market: str,
    digits: str,
    instrument_type: InstrumentType,
) -> None:
    if market not in _MARKET_PREFIXES:
        raise ValueError(f"不支持的市场前缀: {market!r}")
    if instrument_type == "stock":
        allowed = {
            "sh": ("6", "9"),
            "sz": ("0", "3"),
            "bj": ("4", "8"),
        }
        if not digits.startswith(allowed[market]):
            raise ValueError(f"{market}{digits} 与股票市场规则不匹配")
    elif instrument_type == "index":
        if market == "sh" and not digits.startswith("000"):
            raise ValueError(f"暂不支持该上证指数代码: {market}{digits}")
        if market == "sz" and not digits.startswith("399"):
            raise ValueError(f"暂不支持该深证指数代码: {market}{digits}")
        if market == "bj":
            raise ValueError("暂不支持北交所指数跟踪")
