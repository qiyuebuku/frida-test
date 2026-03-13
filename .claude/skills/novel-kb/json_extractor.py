"""
通用 JSON 提取器 - 处理 Claude 输出的各种格式问题

解决的问题：
1. Markdown 代码块包裹 (```json ... ```)
2. 额外的说明文字（前后文）
3. 未转义的引号
4. 不完整的 JSON（自动修复简单错误）
5. 多种 JSON 结构（对象/数组）
"""

import json
import re
from typing import Any, Optional


def extract_json_from_output(output: str, verbose: bool = False) -> Optional[Any]:
    """
    从 Claude 输出中提取并解析 JSON

    Args:
        output: Claude 的原始输出
        verbose: 是否打印调试信息

    Returns:
        解析后的 JSON 对象（dict 或 list），失败返回 None
    """
    if not output or not output.strip():
        if verbose:
            print("[JSON提取] 输入为空")
        return None

    text = output.strip()

    # 步骤 1: 移除 markdown 代码块
    # 尝试 ```json ... ```
    m = re.search(r'```json\s*\n(.*?)\n\s*```', text, re.DOTALL)
    if m:
        text = m.group(1).strip()
        if verbose:
            print(f"[JSON提取] 从 ```json 代码块中提取，长度 {len(text)}")
    else:
        # 尝试 ``` ... ```
        m = re.search(r'```\s*\n(.*?)\n\s*```', text, re.DOTALL)
        if m:
            text = m.group(1).strip()
            if verbose:
                print(f"[JSON提取] 从 ``` 代码块中提取，长度 {len(text)}")

    # 步骤 2: 定位 JSON 边界（{ ... } 或 [ ... ]）
    first_brace = text.find('{')
    first_bracket = text.find('[')

    # 选择更早出现的开始符号
    if first_brace == -1 and first_bracket == -1:
        if verbose:
            print(f"[JSON提取] 未找到 JSON 起始符号")
            print(f"[JSON提取] 内容前 200 字符: {text[:200]!r}")
        return None

    if first_brace == -1:
        start_char = '['
        start_pos = first_bracket
        end_char = ']'
    elif first_bracket == -1:
        start_char = '{'
        start_pos = first_brace
        end_char = '}'
    else:
        if first_brace < first_bracket:
            start_char = '{'
            start_pos = first_brace
            end_char = '}'
        else:
            start_char = '['
            start_pos = first_bracket
            end_char = ']'

    # 找到对应的结束位置（考虑嵌套）
    end_pos = _find_matching_bracket(text, start_pos, start_char, end_char)

    if end_pos == -1:
        # 如果找不到匹配的结束符，使用最后一个结束符
        end_pos = text.rfind(end_char)
        if end_pos == -1 or end_pos <= start_pos:
            if verbose:
                print(f"[JSON提取] 未找到匹配的 {end_char}")
            return None
        if verbose:
            print(f"[JSON提取] 使用最后出现的 {end_char} (可能不匹配)")

    json_str = text[start_pos:end_pos + 1]

    if verbose:
        print(f"[JSON提取] 提取 JSON 字符串，长度 {len(json_str)}")

    # 步骤 3: 尝试解析
    try:
        data = json.loads(json_str)
        if verbose:
            print(f"[JSON提取] ✅ 解析成功，类型: {type(data).__name__}")
        return data
    except json.JSONDecodeError as e:
        if verbose:
            print(f"[JSON提取] 首次解析失败: {e}")
            print(f"[JSON提取] 错误位置: 行 {e.lineno} 列 {e.colno} (字符 {e.pos})")

        # 步骤 4: 尝试修复常见错误
        json_str_fixed = _fix_common_json_errors(json_str, verbose)

        if json_str_fixed != json_str:
            try:
                data = json.loads(json_str_fixed)
                if verbose:
                    print(f"[JSON提取] ✅ 修复后解析成功")
                return data
            except json.JSONDecodeError as e2:
                if verbose:
                    print(f"[JSON提取] 修复后仍失败: {e2}")

        # 步骤 5: 最后尝试 - 保存到文件供人工检查
        if verbose:
            print(f"[JSON提取] ❌ 所有尝试均失败")
            # 显示错误附近的内容
            if e.pos:
                start = max(0, e.pos - 100)
                end = min(len(json_str), e.pos + 100)
                print(f"[JSON提取] 错误附近内容:\n{json_str[start:end]!r}")

        return None


def _find_matching_bracket(text: str, start_pos: int,
                           open_char: str, close_char: str) -> int:
    """找到匹配的右括号/右花括号位置（考虑嵌套）"""
    depth = 0
    in_string = False
    escape_next = False

    for i in range(start_pos, len(text)):
        char = text[i]

        if escape_next:
            escape_next = False
            continue

        if char == '\\':
            escape_next = True
            continue

        if char == '"' and not in_string:
            in_string = True
            continue

        if char == '"' and in_string:
            in_string = False
            continue

        if in_string:
            continue

        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return i

    return -1


def _fix_common_json_errors(json_str: str, verbose: bool = False) -> str:
    """修复常见的 JSON 错误"""
    original = json_str

    # 修复 1: 移除尾部多余的逗号（trailing comma）
    # 例如: {"a": 1,} -> {"a": 1}
    json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)

    # 修复 2: 字符串值中未转义的引号（逐行扫描）
    # 这种情况通常出现在 description/name 等字段
    lines = json_str.split('\n')
    fixed_lines = []

    for line in lines:
        # 检测模式："key": "value包含"错误引号"的内容"
        # 如果一行有 4+ 个引号且匹配 "xxx": "yyy" 模式，尝试修复
        quote_count = line.count('"')
        if quote_count >= 6 and '": "' in line:
            # 可能是未转义引号导致的
            # 找到第一个 ": " 后的所有内容
            colon_pos = line.find('": "')
            if colon_pos != -1:
                prefix = line[:colon_pos + 4]  # 'xxx": "'
                rest = line[colon_pos + 4:]

                # 在 rest 中，最后一个 " 应该是值的结束，其他都应该转义
                # 找到最后一个 " 前面的 , 或 }（如果有）
                # 简化：找最后一个 "，或 "}
                if rest.endswith('",') or rest.endswith('"') or rest.endswith('"}'):
                    # 从后往前找第一个未转义的引号
                    last_quote = len(rest) - 1 - rest[::-1].find('"')
                    value_part = rest[:last_quote]
                    suffix = rest[last_quote:]

                    # 转义 value_part 中的所有引号
                    value_fixed = value_part.replace('"', '\\"')
                    line = prefix + value_fixed + suffix
                    if verbose and value_part != value_fixed:
                        print(f"[JSON修复] 修复行中的未转义引号: {line[:80]}...")

        fixed_lines.append(line)

    json_str = '\n'.join(fixed_lines)

    if verbose and json_str != original:
        print(f"[JSON修复] 应用了修复")

    return json_str


# 向后兼容：保持原有函数名
def extract_json(output: str) -> Optional[Any]:
    """简化版本，不打印调试信息"""
    return extract_json_from_output(output, verbose=False)


if __name__ == "__main__":
    # 测试用例
    test_cases = [
        # Case 1: 纯 JSON
        '{"a": 1, "b": 2}',

        # Case 2: markdown 代码块
        '```json\n{"a": 1}\n```',

        # Case 3: 包含说明文字
        '这是分析结果：\n```json\n{"a": 1}\n```\n希望有帮助。',

        # Case 4: 未转义引号
        '{"description": "他说"你好""}',

        # Case 5: trailing comma
        '{"a": 1,}',

        # Case 6: 数组
        '[{"a": 1}, {"b": 2}]',
    ]

    for i, case in enumerate(test_cases, 1):
        print(f"\n=== 测试 {i} ===")
        print(f"输入: {case!r}")
        result = extract_json_from_output(case, verbose=True)
        print(f"结果: {result}")
