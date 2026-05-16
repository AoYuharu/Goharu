"""
ToolResultBudget — 工具结果 Token 预算管理

当工具调用返回结果过大（超过 token 阈值），将完整结果保存到缓存目录，
用占位符替代，提示模型使用 Read 工具进行流式读取。

同时处理批量工具调用：如果单次调用多个工具的总结果超过上限，
则全部保存并替换为占位符。
"""

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple


class ToolResultBudget:
    """工具结果 Token 预算管理器。

    设计原则：
      - 单结果超限：只替换超过单条上限的结果
      - 批量超限：如果总 token 超过批量上限，全部替换为占位符
      - 缓存文件命名：{tool_name}_{timestamp}_{uuid8}.txt
      - 占位符格式：明确告知模型文件路径和读取方式
    """

    # ── 内置默认值 ────────────────────────────────
    DEFAULT_MAX_SINGLE = 8000   # 单结果 token 上限
    DEFAULT_MAX_BATCH = 24000   # 批量总 token 上限
    DEFAULT_CACHE_DIR = "./runtime_memory/tool_cache"

    # ── 公共接口 ───────────────────────────────────

    @classmethod
    def apply(
        cls,
        results: List[Dict[str, Any]],
        cache_dir: Optional[str] = None,
        max_single_tokens: Optional[int] = None,
        max_batch_tokens: Optional[int] = None,
    ) -> bool:
        """对工具结果列表施加 token 预算（原地修改 result_text 字段）。

        Args:
            results: 结果字典列表，每条至少含 "tool_name" 和 "result_text"
            cache_dir: 缓存目录路径，默认从 config 读取
            max_single_tokens: 单条结果上限，默认 8000
            max_batch_tokens: 批量总上限，默认 24000

        Returns:
            bool: 是否有任何结果被替换（True = 至少有一条被缓存）
        """
        if not results:
            return False

        cache = cache_dir or cls.DEFAULT_CACHE_DIR
        single_limit = (
            max_single_tokens
            if max_single_tokens is not None
            else cls.DEFAULT_MAX_SINGLE
        )
        batch_limit = (
            max_batch_tokens
            if max_batch_tokens is not None
            else cls.DEFAULT_MAX_BATCH
        )

        if single_limit <= 0 and batch_limit <= 0:
            return False

        cache_path = Path(cache)
        cache_path.mkdir(parents=True, exist_ok=True)

        from Agent.TokenEstimator import TokenEstimator
        estimator = TokenEstimator()

        # 过滤出有效结果
        valid = [r for r in results if "error" not in r and r.get("result_text")]

        # ── 第一步：批量总 token 检查 ─────────────
        if batch_limit > 0:
            batch_tokens = sum(
                estimator.estimate(r.get("result_text", "")) for r in valid
            )
            if batch_tokens > batch_limit:
                # 全部保存，全部替换
                for r in valid:
                    r["result_text"] = cls._save_and_replace(
                        r.get("tool_name", "unknown"),
                        r["result_text"],
                        cache_path,
                        estimator,
                        batch_limit,
                    )
                return True

        # ── 第二步：逐条检查 ───────────────────────
        if single_limit <= 0:
            return False

        any_saved = False
        for r in valid:
            text = r.get("result_text", "")
            tokens = estimator.estimate(text)
            if tokens > single_limit:
                r["result_text"] = cls._save_and_replace(
                    r.get("tool_name", "unknown"),
                    text,
                    cache_path,
                    estimator,
                    single_limit,
                )
                any_saved = True

        return any_saved

    # ── 内部方法 ───────────────────────────────────

    @classmethod
    def _extract_readable_text(cls, result_text: str) -> str:
        """Try to extract human-readable text from a structured JSON result.

        For run_cmd results (JSON with stdout/stderr), returns the plain text
        output instead of the compact JSON wrapper, so the persisted file has
        natural line breaks and is readable line-by-line with the Read tool.
        """
        try:
            parsed = json.loads(result_text)
            if isinstance(parsed, dict) and "exit_code" in parsed:
                stdout = (parsed.get("stdout") or "").strip()
                stderr = (parsed.get("stderr") or "").strip()
                parts = []
                if stderr:
                    parts.append(f"stderr:\n{stderr}")
                if stdout:
                    parts.append(f"stdout:\n{stdout}")
                if not parts:
                    parts.append("(no output)")
                parts.append(
                    f"\n---\nexit_code: {parsed.get('exit_code')}"
                    f"  timed_out: {parsed.get('timed_out', False)}"
                    f"  interrupted: {parsed.get('interrupted', False)}"
                )
                return "\n".join(parts)
        except (json.JSONDecodeError, TypeError):
            pass
        return str(result_text)

    @classmethod
    def _save_and_replace(
        cls,
        tool_name: str,
        result_text: str,
        cache_dir: Path,
        estimator,
        limit: int,
    ) -> str:
        """将结果保存到文件，并返回占位符字符串。"""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        short_id = uuid.uuid4().hex[:8]
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in tool_name)
        filename = f"tool_{safe_name}_{ts}_{short_id}.txt"
        filepath = cache_dir / filename

        # 提取可读文本（拆开 JSON 包装，保留原始换行）
        readable = cls._extract_readable_text(result_text)

        try:
            filepath.write_text(readable, encoding="utf-8")
        except Exception as exc:
            return json.dumps(
                {"error": f"Failed to save tool result: {exc}"},
                ensure_ascii=False,
            )

        tokens = estimator.estimate(result_text)
        token_str = estimator.format(int(tokens))
        limit_str = estimator.format(int(limit))

        return (
            f"[TRUNCATED] Output too large ({token_str} tokens, limit: {limit_str}).\n"
            f"Full output saved to: {filepath}\n"
            f"Use Read(file_path=\"{filepath}\") to read it. "
            f"The first lines contain a summary."
        )
