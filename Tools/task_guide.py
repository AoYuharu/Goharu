"""
任务引导系统：检测重复行为并给出警告
"""
from collections import defaultdict


class TaskGuide:
    """任务引导器，检测并纠正模型的重复或无效行为"""

    def __init__(self):
        # 记录文件读取历史：{(file_path, start_line, end_line): count}
        self.read_history = defaultdict(int)
        # 记录警告历史：避免重复警告
        self.warned = set()

    def record_read(self, file_path: str, start_line: int = None, end_line: int = None) -> str | None:
        """
        记录文件读取操作，检测重复读取

        Args:
            file_path: 文件路径
            start_line: 起始行号（可选）
            end_line: 结束行号（可选）

        Returns:
            警告消息（如果需要警告），否则返回 None
        """
        # 标准化行号（None 表示读取整个文件）
        key = (file_path, start_line or 0, end_line or 0)

        self.read_history[key] += 1
        count = self.read_history[key]

        # 超过 3 次读取同一位置，发出警告
        if count > 3 and key not in self.warned:
            self.warned.add(key)

            location = f"{file_path}"
            if start_line or end_line:
                location += f" (lines {start_line or '?'}-{end_line or '?'})"

            warning = f"""⚠️ 任务引导警告：

你已经读取 {location} 超过 {count} 次了。

**停止重复阅读！这没有意义。**

可能的问题：
1. 你在寻找某些信息但没有找到 → 尝试用 Grep 搜索关键词
2. 你忘记了之前读过的内容 → 回顾对话历史
3. 你在犹豫是否要修改 → 如果需要修改，直接调用 Edit 工具
4. 你在等待某些变化 → 文件不会自动改变，除非你调用工具修改

**下一步行动：**
- 如果需要搜索：使用 Grep 工具
- 如果需要修改：使用 Edit 工具
- 如果需要其他文件：使用 Read 读取其他文件
- 如果已经有足够信息：停止读取，开始执行任务
"""
            return warning

        return None

    def record_tool_call(self, tool_name: str, arguments: dict) -> str | None:
        """
        记录工具调用，检测特定模式

        Args:
            tool_name: 工具名称
            arguments: 工具参数

        Returns:
            警告消息（如果需要），否则返回 None
        """
        # 检测 Read 工具调用
        if tool_name == "Read":
            file_path = arguments.get("path") or arguments.get("file_path")
            start_line = arguments.get("start_line")
            end_line = arguments.get("end_line")

            if file_path:
                return self.record_read(file_path, start_line, end_line)

        return None

    def reset(self):
        """重置所有记录（新的对话回合）"""
        self.read_history.clear()
        self.warned.clear()

    def get_statistics(self) -> dict:
        """获取统计信息"""
        return {
            "total_reads": sum(self.read_history.values()),
            "unique_locations": len(self.read_history),
            "repeated_reads": {
                f"{path} ({start}-{end})": count
                for (path, start, end), count in self.read_history.items()
                if count > 1
            },
        }
