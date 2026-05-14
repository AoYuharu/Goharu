"""JSON 解析工具 - 处理 LLM 返回的 JSON 格式"""
import json


class JSONParser:
    """JSON 解析工具类，用于处理 LLM 返回的可能包含 markdown 标记的 JSON"""

    @staticmethod
    def coerce_json(text: str) -> str:
        """
        清理 JSON 文本，移除 markdown 代码块标记

        Args:
            text: 原始文本，可能包含 ```json ... ``` 标记

        Returns:
            清理后的 JSON 文本
        """
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
        return cleaned

    @staticmethod
    def parse_with_retry(text: str, max_retries: int = 2) -> dict:
        """
        解析 JSON，支持自动清理和重试

        Args:
            text: 要解析的 JSON 文本
            max_retries: 最大重试次数

        Returns:
            解析后的字典对象

        Raises:
            ValueError: 解析失败且重试次数用尽
        """
        for attempt in range(max_retries):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                if attempt == 0:
                    # 第一次失败，尝试清理 markdown 标记
                    text = JSONParser.coerce_json(text)
                else:
                    raise ValueError(f"JSON 解析失败，已重试 {max_retries} 次")

        # 如果所有重试都失败
        raise ValueError(f"JSON 解析失败: {text[:100]}...")
