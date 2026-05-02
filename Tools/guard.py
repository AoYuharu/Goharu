"""
工具调用多重防护系统
提供从工具名修复到参数验证的完整防护链
"""
import json
import re
from difflib import SequenceMatcher
from typing import Any


class ToolCallGuard:
    """工具调用防护器"""

    def __init__(self, available_tools: list[dict]):
        """
        Args:
            available_tools: 可用工具列表，格式 [{"name": "...", "inputSchema": {...}}]
        """
        self.available_tools = {tool["name"]: tool for tool in available_tools}
        self.tool_names = list(self.available_tools.keys())
        self.tool_names_lower = {name.lower(): name for name in self.tool_names}

    # ==================== 防线 1：工具名修复 ====================

    def fix_tool_name(self, raw_name: str) -> tuple[str | None, str]:
        """
        修复工具名：大小写、分隔符、模糊匹配

        Returns:
            (fixed_name, reason) - 修复后的名称和修复原因
        """
        if not isinstance(raw_name, str):
            return None, "工具名不是字符串"

        original = raw_name.strip()
        if not original:
            return None, "工具名为空"

        # 1. 精确匹配
        if original in self.available_tools:
            return original, "精确匹配"

        # 2. 大小写不敏感匹配
        lower_name = original.lower()
        if lower_name in self.tool_names_lower:
            fixed = self.tool_names_lower[lower_name]
            return fixed, f"大小写修复: {original} → {fixed}"

        # 3. 分隔符归一化（下划线 ↔ 驼峰 ↔ 短横线）
        normalized_candidates = [
            original.replace("-", "_"),
            original.replace("_", "-"),
            self._to_camel_case(original),
            self._to_snake_case(original),
        ]
        for candidate in normalized_candidates:
            if candidate in self.available_tools:
                return candidate, f"分隔符修复: {original} → {candidate}"
            candidate_lower = candidate.lower()
            if candidate_lower in self.tool_names_lower:
                fixed = self.tool_names_lower[candidate_lower]
                return fixed, f"分隔符+大小写修复: {original} → {fixed}"

        # 4. 模糊匹配（相似度 > 0.8）
        best_match = None
        best_ratio = 0.0
        for tool_name in self.tool_names:
            ratio = SequenceMatcher(None, original.lower(), tool_name.lower()).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = tool_name

        if best_ratio >= 0.8:
            return best_match, f"模糊匹配(相似度{best_ratio:.2f}): {original} → {best_match}"

        return None, f"未找到匹配工具: {original} (最接近: {best_match}, 相似度{best_ratio:.2f})"

    @staticmethod
    def _to_camel_case(text: str) -> str:
        """转为驼峰命名"""
        parts = re.split(r'[-_]', text)
        return parts[0] + ''.join(word.capitalize() for word in parts[1:])

    @staticmethod
    def _to_snake_case(text: str) -> str:
        """转为蛇形命名"""
        text = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', text)
        text = re.sub(r'([a-z\d])([A-Z])', r'\1_\2', text)
        return text.lower()

    # ==================== 防线 2：参数 JSON 修复 ====================

    @staticmethod
    def fix_json(raw_json: str) -> tuple[dict | None, str]:
        """
        修复常见 JSON 语法错误

        Returns:
            (parsed_dict, reason) - 解析后的字典和修复原因
        """
        if not isinstance(raw_json, str):
            if isinstance(raw_json, dict):
                return raw_json, "已是字典"
            return None, "参数不是字符串或字典"

        original = raw_json.strip()
        if not original:
            return {}, "空参数，返回空字典"

        # 1. 尝试直接解析
        try:
            parsed = json.loads(original)
            if isinstance(parsed, dict):
                return parsed, "JSON 格式正确"
            return None, f"JSON 解析结果不是对象: {type(parsed)}"
        except json.JSONDecodeError:
            pass

        # 2. 修复尾随逗号
        fixed = re.sub(r',\s*([}\]])', r'\1', original)
        try:
            parsed = json.loads(fixed)
            if isinstance(parsed, dict):
                return parsed, "修复尾随逗号"
        except json.JSONDecodeError:
            pass

        # 3. 修复未闭合的括号/引号
        fixed = original
        open_braces = fixed.count('{') - fixed.count('}')
        if open_braces > 0:
            fixed += '}' * open_braces
        open_brackets = fixed.count('[') - fixed.count(']')
        if open_brackets > 0:
            fixed += ']' * open_brackets

        # 检查未闭合的引号
        quote_count = fixed.count('"') - fixed.count('\\"')
        if quote_count % 2 != 0:
            fixed += '"'

        try:
            parsed = json.loads(fixed)
            if isinstance(parsed, dict):
                return parsed, f"修复未闭合括号/引号 (添加了 {open_braces} 个}}, {open_brackets} 个])"
        except json.JSONDecodeError:
            pass

        # 4. 尝试提取第一个完整的 JSON 对象
        match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', original)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict):
                    return parsed, "提取嵌入的 JSON 对象"
            except json.JSONDecodeError:
                pass

        return None, f"无法修复 JSON: {original[:100]}"

    # ==================== 防线 3：参数类型强制转换 ====================

    def coerce_arguments(self, tool_name: str, arguments: dict) -> tuple[dict, list[str]]:
        """
        根据 schema 强制转换参数类型

        Returns:
            (coerced_args, warnings) - 转换后的参数和警告列表
        """
        if tool_name not in self.available_tools:
            return arguments, [f"未知工具 {tool_name}，跳过类型转换"]

        schema = self.available_tools[tool_name].get("inputSchema", {})
        properties = schema.get("properties", {})

        coerced = {}
        warnings = []

        for key, value in arguments.items():
            if key not in properties:
                coerced[key] = value
                warnings.append(f"参数 {key} 不在 schema 中，保持原值")
                continue

            expected_type = properties[key].get("type")
            if not expected_type:
                coerced[key] = value
                continue

            converted, warning = self._coerce_value(key, value, expected_type)
            coerced[key] = converted
            if warning:
                warnings.append(warning)

        return coerced, warnings

    @staticmethod
    def _coerce_value(key: str, value: Any, expected_type: str) -> tuple[Any, str | None]:
        """
        转换单个值的类型

        Returns:
            (converted_value, warning)
        """
        # 如果类型已匹配，直接返回
        type_checks = {
            "string": lambda v: isinstance(v, str),
            "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
            "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
            "boolean": lambda v: isinstance(v, bool),
            "object": lambda v: isinstance(v, dict),
            "array": lambda v: isinstance(v, list),
        }

        checker = type_checks.get(expected_type)
        if checker and checker(value):
            return value, None

        # 尝试转换
        try:
            if expected_type == "string":
                return str(value), f"参数 {key}: {type(value).__name__} → string"

            elif expected_type == "integer":
                if isinstance(value, str):
                    # 尝试解析字符串
                    value = value.strip()
                    if value.lower() in ("true", "false"):
                        converted = 1 if value.lower() == "true" else 0
                        return converted, f"参数 {key}: boolean string → integer"
                    converted = int(float(value))  # 支持 "3.0" → 3
                    return converted, f"参数 {key}: string → integer"
                elif isinstance(value, bool):
                    converted = 1 if value else 0
                    return converted, f"参数 {key}: boolean → integer"
                elif isinstance(value, float):
                    converted = int(value)
                    return converted, f"参数 {key}: float → integer"

            elif expected_type == "number":
                if isinstance(value, str):
                    converted = float(value.strip())
                    return converted, f"参数 {key}: string → number"
                elif isinstance(value, bool):
                    converted = 1.0 if value else 0.0
                    return converted, f"参数 {key}: boolean → number"

            elif expected_type == "boolean":
                if isinstance(value, str):
                    value_lower = value.strip().lower()
                    if value_lower in ("true", "1", "yes", "y"):
                        return True, f"参数 {key}: string → boolean (true)"
                    elif value_lower in ("false", "0", "no", "n", ""):
                        return False, f"参数 {key}: string → boolean (false)"
                elif isinstance(value, (int, float)):
                    converted = bool(value)
                    return converted, f"参数 {key}: number → boolean"

            elif expected_type == "object":
                if isinstance(value, str):
                    # 尝试解析 JSON 字符串
                    parsed = json.loads(value)
                    if isinstance(parsed, dict):
                        return parsed, f"参数 {key}: JSON string → object"

            elif expected_type == "array":
                if isinstance(value, str):
                    # 尝试解析 JSON 数组
                    parsed = json.loads(value)
                    if isinstance(parsed, list):
                        return parsed, f"参数 {key}: JSON string → array"
                elif not isinstance(value, list):
                    # 单个值包装为数组
                    return [value], f"参数 {key}: {type(value).__name__} → array"

        except (ValueError, TypeError, json.JSONDecodeError):
            pass

        # 无法转换，保持原值
        return value, f"参数 {key}: 无法转换 {type(value).__name__} → {expected_type}，保持原值"

    # ==================== 防线 4：请求前参数清理 ====================

    @staticmethod
    def sanitize_arguments(arguments: dict) -> tuple[dict, list[str]]:
        """
        清理参数：移除 null、空字符串、重复键等

        Returns:
            (sanitized_args, warnings)
        """
        sanitized = {}
        warnings = []

        for key, value in arguments.items():
            # 移除 None 值
            if value is None:
                warnings.append(f"移除 null 参数: {key}")
                continue

            # 移除空字符串（可选，根据需求调整）
            if isinstance(value, str) and not value.strip():
                warnings.append(f"移除空字符串参数: {key}")
                continue

            # 递归清理嵌套对象
            if isinstance(value, dict):
                nested, nested_warnings = ToolCallGuard.sanitize_arguments(value)
                sanitized[key] = nested
                warnings.extend([f"{key}.{w}" for w in nested_warnings])
            elif isinstance(value, list):
                sanitized[key] = [
                    item for item in value
                    if item is not None and (not isinstance(item, str) or item.strip())
                ]
            else:
                sanitized[key] = value

        return sanitized, warnings

    # ==================== 完整防护流程 ====================

    def guard(self, raw_tool_name: str, raw_arguments: Any) -> dict:
        """
        执行完整的防护流程

        Returns:
            {
                "success": bool,
                "tool_name": str | None,
                "arguments": dict | None,
                "logs": list[str],  # 每一层的处理日志
                "error": str | None,
            }
        """
        logs = []

        # 防线 1：工具名修复
        fixed_name, name_reason = self.fix_tool_name(raw_tool_name)
        logs.append(f"[防线1-工具名] {name_reason}")

        if fixed_name is None:
            return {
                "success": False,
                "tool_name": None,
                "arguments": None,
                "logs": logs,
                "error": name_reason,
            }

        # 防线 2：参数 JSON 修复
        if isinstance(raw_arguments, dict):
            parsed_args = raw_arguments
            logs.append("[防线2-JSON] 参数已是字典，跳过解析")
        elif isinstance(raw_arguments, str):
            parsed_args, json_reason = self.fix_json(raw_arguments)
            logs.append(f"[防线2-JSON] {json_reason}")
            if parsed_args is None:
                return {
                    "success": False,
                    "tool_name": fixed_name,
                    "arguments": None,
                    "logs": logs,
                    "error": json_reason,
                }
        else:
            logs.append(f"[防线2-JSON] 参数类型错误: {type(raw_arguments)}")
            return {
                "success": False,
                "tool_name": fixed_name,
                "arguments": None,
                "logs": logs,
                "error": f"参数必须是字典或 JSON 字符串，实际: {type(raw_arguments)}",
            }

        # 防线 3：参数类型强制转换
        coerced_args, coerce_warnings = self.coerce_arguments(fixed_name, parsed_args)
        if coerce_warnings:
            logs.append(f"[防线3-类型转换] {len(coerce_warnings)} 个转换: " + "; ".join(coerce_warnings[:3]))
        else:
            logs.append("[防线3-类型转换] 所有参数类型匹配")

        # 防线 4：参数清理
        sanitized_args, sanitize_warnings = self.sanitize_arguments(coerced_args)
        if sanitize_warnings:
            logs.append(f"[防线4-清理] {len(sanitize_warnings)} 个清理: " + "; ".join(sanitize_warnings[:3]))
        else:
            logs.append("[防线4-清理] 无需清理")

        return {
            "success": True,
            "tool_name": fixed_name,
            "arguments": sanitized_args,
            "logs": logs,
            "error": None,
        }
