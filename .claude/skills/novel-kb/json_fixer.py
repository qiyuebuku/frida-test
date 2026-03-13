"""
三层 JSON 修复系统

Layer 1: 正则修复（处理常见格式错误）
Layer 2: 语法修复（修复结构性错误）
Layer 3: Claude AI 修复（处理复杂逻辑错误）
"""

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Optional, Tuple


class JSONFixer:
    """JSON 修复器 - 三层修复策略"""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.repair_log = []

    def fix(self, raw_output: str, save_debug_to: Optional[Path] = None) -> Tuple[Optional[Any], str]:
        """
        修复并解析 JSON

        Args:
            raw_output: Claude 的原始输出
            save_debug_to: 如果修复失败，保存调试信息到此路径

        Returns:
            (parsed_data, status_message)
            - parsed_data: 解析成功的数据，失败返回 None
            - status_message: 状态描述（如 "layer1_success", "layer3_failed"）
        """
        self.repair_log = []

        # 预处理：提取 JSON 内容
        json_str = self._extract_json_content(raw_output)
        if not json_str:
            return None, "no_json_found"

        # Layer 1: 正则修复
        result = self._layer1_regex_fixes(json_str)
        if result:
            return result, "layer1_success"

        # Layer 2: 语法修复
        result = self._layer2_syntax_fixes(json_str)
        if result:
            return result, "layer2_success"

        # Layer 3: Claude AI 修复
        result = self._layer3_claude_fixes(json_str)
        if result:
            return result, "layer3_success"

        # 所有修复都失败
        if save_debug_to:
            self._save_debug_info(raw_output, json_str, save_debug_to)

        return None, "all_layers_failed"

    def _extract_json_content(self, raw_output: str) -> Optional[str]:
        """提取 JSON 内容（移除代码块标记和前后文字）"""
        text = raw_output.strip()

        # 1. 尝试提取 markdown 代码块
        m = re.search(r'```json\s*\n(.*?)\n\s*```', text, re.DOTALL)
        if m:
            self._log("从 ```json 代码块中提取")
            return m.group(1).strip()

        m = re.search(r'```\s*\n(.*?)\n\s*```', text, re.DOTALL)
        if m:
            self._log("从 ``` 代码块中提取")
            return m.group(1).strip()

        # 2. 定位 JSON 边界
        first_brace = text.find('{')
        first_bracket = text.find('[')

        if first_brace == -1 and first_bracket == -1:
            return None

        # 选择更早出现的开始符号
        if first_brace == -1:
            start_pos, start_char, end_char = first_bracket, '[', ']'
        elif first_bracket == -1:
            start_pos, start_char, end_char = first_brace, '{', '}'
        else:
            if first_brace < first_bracket:
                start_pos, start_char, end_char = first_brace, '{', '}'
            else:
                start_pos, start_char, end_char = first_bracket, '[', ']'

        # 找到匹配的结束位置
        end_pos = self._find_matching_bracket(text, start_pos, start_char, end_char)
        if end_pos == -1:
            end_pos = text.rfind(end_char)
            if end_pos == -1 or end_pos <= start_pos:
                return None

        return text[start_pos:end_pos + 1]

    def _find_matching_bracket(self, text: str, start_pos: int,
                               open_char: str, close_char: str) -> int:
        """找到匹配的右括号（考虑嵌套和字符串）"""
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

    def _layer1_regex_fixes(self, json_str: str) -> Optional[Any]:
        """Layer 1: 正则修复常见错误"""
        self._log("=== Layer 1: 正则修复 ===")
        original = json_str

        # 修复 1: 移除尾部逗号
        json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
        if json_str != original:
            self._log("修复: 移除尾部逗号")

        # 修复 2: 字符串值中未转义的换行符
        def fix_newlines(match):
            key = match.group(1)
            value = match.group(2)
            # 将实际换行符替换为 \n
            value_fixed = value.replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
            return f'"{key}": "{value_fixed}"'

        json_str = re.sub(r'"(\w+)":\s*"([^"]*)"', fix_newlines, json_str)

        # 修复 3: 单引号改为双引号（JSON 标准要求双引号）
        # 只在非字符串值中替换（避免误伤字符串内容）
        # 简化：如果整个 JSON 都用单引号，全部替换
        if json_str.count("'") > json_str.count('"'):
            json_str = json_str.replace("'", '"')
            self._log("修复: 单引号改为双引号")

        # 尝试解析
        try:
            data = json.loads(json_str)
            self._log("✅ Layer 1 修复成功")
            return data
        except json.JSONDecodeError as e:
            self._log(f"Layer 1 失败: {e}")
            return None

    def _layer2_syntax_fixes(self, json_str: str) -> Optional[Any]:
        """Layer 2: 语法修复（处理结构性错误）"""
        self._log("=== Layer 2: 语法修复 ===")

        # 修复 1: 字符串值中未转义的引号（逐行扫描）
        lines = json_str.split('\n')
        fixed_lines = []

        for line_num, line in enumerate(lines, 1):
            # 检测模式："key": "value包含"错误引号"的内容"
            if '": "' in line:
                # 计算引号数量
                quote_count = line.count('"')

                # 如果引号数量 >= 6（正常是 4），说明值中有未转义引号
                if quote_count >= 6:
                    # 找到 ": " 的位置
                    colon_pos = line.find('": "')
                    if colon_pos != -1:
                        prefix = line[:colon_pos + 4]  # "key": "
                        rest = line[colon_pos + 4:]

                        # 找到这一行的结束引号
                        # 可能的结尾：", 或 " 或 "}
                        if rest.endswith('",'):
                            suffix = '","'
                            value_part = rest[:-2]
                        elif rest.endswith('"'):
                            suffix = '"'
                            value_part = rest[:-1]
                        elif rest.endswith('"}'):
                            suffix = '"}'
                            value_part = rest[:-2]
                        else:
                            # 无法确定结束，跳过
                            fixed_lines.append(line)
                            continue

                        # 转义 value_part 中的所有引号
                        value_fixed = value_part.replace('"', '\\"')
                        line = prefix + value_fixed + suffix
                        self._log(f"修复行 {line_num}: 转义字符串中的引号")

            fixed_lines.append(line)

        json_str = '\n'.join(fixed_lines)

        # 尝试解析
        try:
            data = json.loads(json_str)
            self._log("✅ Layer 2 修复成功")
            return data
        except json.JSONDecodeError as e:
            self._log(f"Layer 2 失败: {e}")
            return None

    def _layer3_claude_fixes(self, json_str: str) -> Optional[Any]:
        """Layer 3: 使用 Claude AI 修复复杂错误"""
        self._log("=== Layer 3: Claude AI 修复 ===")

        # 构建修复 prompt
        prompt = """你是 JSON 格式修复专家。以下 JSON 数据有语法错误，请修复它。

**原始 JSON**（有错误）：
```json
{json_content}
```

**任务**：
1. 识别并修复所有 JSON 语法错误
2. 保持原有数据内容不变（只修复格式，不修改数据含义）
3. 直接输出修复后的合法 JSON，不要添加任何解释

**常见错误类型**：
- 未转义的引号：`"text": "他说"你好""` → `"text": "他说\\"你好\\""`
- 尾部逗号：`{{"a": 1,}}` → `{{"a": 1}}`
- 缺失逗号：`{{"a": 1 "b": 2}}` → `{{"a": 1, "b": 2}}`
- 不匹配的括号：`{{"a": [1, 2}}` → `{{"a": [1, 2]}}`

直接输出修复后的 JSON：""".format(json_content=json_str[:5000])

        # 调用 Claude
        try:
            result = subprocess.run(
                ['claude', '-p', '-'],
                input=prompt.encode('utf-8'),
                capture_output=True,
                timeout=60
            )

            if result.returncode != 0:
                self._log(f"Claude 调用失败: {result.stderr.decode()[:200]}")
                return None

            fixed_json = result.stdout.decode('utf-8').strip()

            # 再次提取 JSON（Claude 可能包裹在代码块中）
            fixed_json = self._extract_json_content(fixed_json) or fixed_json

            # 尝试解析
            data = json.loads(fixed_json)
            self._log("✅ Layer 3 (Claude AI) 修复成功")
            return data

        except subprocess.TimeoutExpired:
            self._log("Layer 3 失败: Claude 调用超时")
            return None
        except json.JSONDecodeError as e:
            self._log(f"Layer 3 失败: Claude 修复后仍无法解析 - {e}")
            return None
        except Exception as e:
            self._log(f"Layer 3 失败: {e}")
            return None

    def _save_debug_info(self, raw_output: str, extracted_json: str, save_path: Path):
        """保存调试信息"""
        debug_content = f"""# JSON 修复失败调试信息

## 修复日志
{chr(10).join(self.repair_log)}

## 原始输出（前 2000 字符）
{raw_output[:2000]}

## 提取的 JSON（前 2000 字符）
{extracted_json[:2000]}

## 提取的 JSON（完整）
{extracted_json}
"""
        save_path.write_text(debug_content, encoding='utf-8')
        self._log(f"调试信息已保存到: {save_path}")

    def _log(self, message: str):
        """记录日志"""
        self.repair_log.append(message)
        if self.verbose:
            print(f"[JSONFixer] {message}")


# 向后兼容的简化接口
def fix_and_parse_json(raw_output: str, verbose: bool = False,
                       save_debug_to: Optional[Path] = None) -> Optional[Any]:
    """
    修复并解析 JSON（简化接口）

    Args:
        raw_output: Claude 的原始输出
        verbose: 是否打印调试信息
        save_debug_to: 失败时保存调试信息的路径

    Returns:
        解析后的 Python 对象（dict/list），失败返回 None
    """
    fixer = JSONFixer(verbose=verbose)
    result, status = fixer.fix(raw_output, save_debug_to)
    return result


if __name__ == "__main__":
    # 测试用例
    test_cases = [
        # Case 1: 尾部逗号
        ('{"a": 1,}', "尾部逗号"),

        # Case 2: 未转义引号
        ('{"text": "他说"你好""}', "未转义引号"),

        # Case 3: markdown 代码块
        ('```json\n{"a": 1}\n```', "代码块"),

        # Case 4: 单引号
        ("{'a': 1, 'b': 2}", "单引号"),

        # Case 5: 复杂嵌套错误
        ('''{"data": [
            {"name": "测试", "desc": "包含"引号"的描述"},
            {"name": "测试2", "desc": "正常"},
        ]}''', "复杂错误"),
    ]

    fixer = JSONFixer(verbose=True)

    for i, (test_input, description) in enumerate(test_cases, 1):
        print(f"\n{'='*60}")
        print(f"测试 {i}: {description}")
        print(f"{'='*60}")
        result, status = fixer.fix(test_input)
        if result:
            print(f"✅ 成功 ({status})")
            print(f"结果: {result}")
        else:
            print(f"❌ 失败 ({status})")
