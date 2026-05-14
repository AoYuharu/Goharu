"""
LLM响应日志记录器

按调用分文件记录所有LLM API请求和响应，用于调试和复查。
"""
import json
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Optional


class LLMResponseLogger:
    """LLM响应日志记录器（按调用分文件写入）"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        session_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_dir = Path("runtime_memory/logs/api") / session_ts
        self.session_dir.mkdir(parents=True, exist_ok=True)

        self.call_count = 0
        self._initialized = True

    def log_request(self, messages: list, system_blocks: list, tools: Optional[list] = None, **kwargs) -> int:
        """
        记录API请求

        Args:
            messages: Anthropic格式的messages列表
            system_blocks: 系统消息块列表
            tools: 工具定义列表
            **kwargs: 其他请求参数 (model, max_tokens, temperature等)

        Returns:
            call_id: 本次调用的ID
        """
        self.call_count += 1
        call_id = self.call_count

        request_data = {
            "call_id": call_id,
            "timestamp": datetime.now().isoformat(),
            "model": kwargs.pop("model", None),
            "max_tokens": kwargs.pop("max_tokens", None),
            "temperature": kwargs.pop("temperature", None),
            "top_p": kwargs.pop("top_p", None),
            "system": system_blocks,
            "messages": messages,
        }
        if tools:
            request_data["tools"] = tools
        if kwargs:
            request_data["extra_params"] = kwargs

        file_path = self.session_dir / f"call_{call_id:04d}_request.json"
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(request_data, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            print(f"[LLMResponseLogger] 写入请求日志失败: {e}")

        return call_id

    def log_response(self, call_id: int, response: Any, error: Optional[str] = None):
        """
        记录API响应

        Args:
            call_id: 对应的请求ID
            response: API返回的完整响应 (Anthropic Pydantic对象或dict)
            error: 错误信息（如果有）
        """
        # 序列化响应对象
        if hasattr(response, "model_dump"):
            response_data = response.model_dump()
        elif hasattr(response, "dict"):
            response_data = response.dict()
        elif isinstance(response, dict):
            response_data = response
        else:
            response_data = str(response)

        log_entry = {
            "call_id": call_id,
            "timestamp": datetime.now().isoformat(),
            "response": response_data,
            "error": error,
        }

        file_path = self.session_dir / f"call_{call_id:04d}_response.json"
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(log_entry, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            print(f"[LLMResponseLogger] 写入响应日志失败: {e}")

    def get_session_dir(self) -> Path:
        """获取当前会话日志目录"""
        return self.session_dir


# 全局单例
llm_response_logger = LLMResponseLogger()
