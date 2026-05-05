"""
FileStateManager - 管理工具调用过程中读取的文件状态

功能：
1. 记录所有通过 Read 工具读取的文件内容
2. 提供给 Reflection Agent 共享的上下文
3. 隔离 Actor 的思考过程，只共享工具调用结果
"""
from typing import Dict, List, Optional
from datetime import datetime


class FileStateManager:
    """文件状态管理器"""

    def __init__(self):
        self.files: Dict[str, dict] = {}  # path -> file_info
        self.tool_calls: List[dict] = []  # 所有工具调用记录

    def record_file_read(self, path: str, content: str, start_line: int = 1, end_line: Optional[int] = None, total_lines: int = 0):
        """
        记录文件读取

        Args:
            path: 文件路径
            content: 文件内容
            start_line: 起始行
            end_line: 结束行
            total_lines: 总行数
        """
        if path not in self.files:
            self.files[path] = {
                "path": path,
                "content": content,
                "read_ranges": [],
                "first_read_at": datetime.now().isoformat(),
                "total_lines": total_lines,
            }
        else:
            # 更新内容（如果读取了更多行）
            self.files[path]["content"] = content

        # 记录读取范围
        self.files[path]["read_ranges"].append({
            "start_line": start_line,
            "end_line": end_line or total_lines,
            "read_at": datetime.now().isoformat(),
        })

    def record_tool_call(self, tool_name: str, arguments: dict, result: str, result_preview: str = None):
        """
        记录工具调用（不包含 Actor 的思考）

        Args:
            tool_name: 工具名称
            arguments: 工具参数
            result: 工具结果
            result_preview: 结果预览
        """
        self.tool_calls.append({
            "tool_name": tool_name,
            "arguments": arguments,
            "result": result,
            "result_preview": result_preview or result[:200],
            "called_at": datetime.now().isoformat(),
        })

        # 如果是 Read 工具，提取文件内容
        if tool_name == "Read" and arguments.get("path"):
            try:
                # 尝试从结果中解析文件内容
                import json
                result_data = json.loads(result)
                if isinstance(result_data, dict) and "content" in result_data:
                    content_lines = result_data.get("content", [])
                    # 重建完整内容
                    full_content = "\n".join(
                        line.get("text", "") for line in content_lines
                    )
                    self.record_file_read(
                        path=arguments["path"],
                        content=full_content,
                        start_line=result_data.get("start_line", 1),
                        end_line=result_data.get("end_line"),
                        total_lines=result_data.get("total_lines", 0),
                    )
            except (json.JSONDecodeError, KeyError, TypeError):
                # 如果解析失败，只记录工具调用
                pass

    def get_files_summary(self) -> str:
        """
        获取文件摘要（用于 Reflection）

        Returns:
            文件摘要字符串
        """
        if not self.files:
            return "（未读取任何文件）"

        summary_lines = []
        for path, info in self.files.items():
            summary_lines.append(f"## 文件: {path}")
            summary_lines.append(f"总行数: {info['total_lines']}")
            summary_lines.append(f"读取次数: {len(info['read_ranges'])}")
            summary_lines.append("")
            summary_lines.append("```")
            summary_lines.append(info["content"])
            summary_lines.append("```")
            summary_lines.append("")

        return "\n".join(summary_lines)

    def get_tool_calls_summary(self) -> str:
        """
        获取工具调用摘要（用于 Reflection）

        Returns:
            工具调用摘要字符串
        """
        if not self.tool_calls:
            return "（未调用任何工具）"

        summary_lines = []
        for i, call in enumerate(self.tool_calls, 1):
            summary_lines.append(f"### 工具调用 {i}: {call['tool_name']}")
            summary_lines.append(f"参数: {call['arguments']}")
            summary_lines.append(f"结果预览: {call['result_preview']}")
            summary_lines.append("")

        return "\n".join(summary_lines)

    def get_reflection_context(self) -> dict:
        """
        获取 Reflection 所需的完整上下文

        Returns:
            包含文件内容和工具调用记录的字典
        """
        return {
            "files": self.files,
            "tool_calls": self.tool_calls,
            "files_summary": self.get_files_summary(),
            "tool_calls_summary": self.get_tool_calls_summary(),
        }

    def clear(self):
        """清空所有记录"""
        self.files.clear()
        self.tool_calls.clear()

    def get_file_content(self, path: str) -> Optional[str]:
        """
        获取指定文件的内容

        Args:
            path: 文件路径

        Returns:
            文件内容，如果未读取则返回 None
        """
        if path in self.files:
            return self.files[path]["content"]
        return None

    def has_file(self, path: str) -> bool:
        """
        检查是否已读取指定文件

        Args:
            path: 文件路径

        Returns:
            是否已读取
        """
        return path in self.files

    def get_stats(self) -> dict:
        """
        获取统计信息

        Returns:
            统计信息字典
        """
        return {
            "files_count": len(self.files),
            "tool_calls_count": len(self.tool_calls),
            "total_content_size": sum(
                len(info["content"]) for info in self.files.values()
            ),
        }
