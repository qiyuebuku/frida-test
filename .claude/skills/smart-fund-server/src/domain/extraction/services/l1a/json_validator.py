"""JSON 解析 + 修复 + schema 校验"""
import json
import logging
import re

logger = logging.getLogger(__name__)

# 事件必填字段
REQUIRED_FIELDS = {"is_event", "event_type", "title"}
# 数值字段范围
FLOAT_FIELDS = {"strength", "sentiment", "novelty", "certainty"}


def repair_and_parse(raw: str) -> list[dict]:
    """从 LLM 输出中解析 JSON 事件列表

    支持: JSON 数组、JSON 对象、markdown 代码块包裹的 JSON
    """
    if not raw:
        return []

    text = raw.strip()

    # 去 markdown 代码块
    if text.startswith("```"):
        match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        if match:
            text = match.group(1).strip()

    # 尝试直接解析
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            return [data]
    except json.JSONDecodeError:
        pass

    # 提取所有 {...} 块
    results = []
    depth = 0
    start = -1
    for i, c in enumerate(text):
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    obj = json.loads(text[start:i + 1])
                    results.append(obj)
                except json.JSONDecodeError:
                    pass
                start = -1
    return results


def validate_event(event: dict) -> tuple[bool, list[str]]:
    """校验单个事件 JSON 的基本完整性

    Returns: (is_valid, quality_flags)
    """
    flags = []

    if not event.get("is_event"):
        return False, ["not_an_event"]

    # 必填字段
    for field in REQUIRED_FIELDS:
        if not event.get(field):
            flags.append(f"missing_{field}")

    # 数值字段范围检查
    for field in FLOAT_FIELDS:
        val = event.get(field)
        if val is not None:
            try:
                v = float(val)
                if field == "sentiment" and not (-1 <= v <= 1):
                    flags.append(f"{field}_out_of_range")
                elif field != "sentiment" and not (0 <= v <= 1):
                    flags.append(f"{field}_out_of_range")
            except (ValueError, TypeError):
                flags.append(f"{field}_invalid_type")

    is_valid = len([f for f in flags if f.startswith("missing_")]) == 0
    return is_valid, flags


def coerce_event(event: dict) -> dict:
    """强制类型转换，确保字段类型正确"""
    result = dict(event)

    # 数值字段
    for field in FLOAT_FIELDS:
        val = result.get(field)
        if val is not None:
            try:
                result[field] = float(val)
            except (ValueError, TypeError):
                result[field] = 0.5 if field != "sentiment" else 0.0

    # 列表字段
    for field in ("affected_stocks", "affected_industries", "affected_concepts", "affected_regions"):
        val = result.get(field)
        if not isinstance(val, list):
            result[field] = []

    # 字符串字段默认值
    for field in ("event_type", "event_subtype", "title", "summary", "direction", "scope", "duration"):
        if not result.get(field):
            result[field] = "" if field not in ("direction",) else "neutral"
            if field == "scope":
                result[field] = "market"
            if field == "duration":
                result[field] = "short"

    return result
