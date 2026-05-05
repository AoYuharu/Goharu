"""
安全命令检查模块
用于拦截危险的系统命令，防止意外的破坏性操作
"""
import re
from typing import Tuple, Optional
from configurationLoader import config


class CommandSecurityChecker:
    """命令安全检查器"""

    def __init__(self):
        self.security_config = config.get("tools.security", {})
        self.enabled = self.security_config.get("enabled", True)
        self.allow_confirmation = self.security_config.get("allow_confirmation", False)
        self.dangerous_commands = self.security_config.get("dangerous_commands", [])
        self.require_confirmation = self.security_config.get("require_confirmation", [])

    def check_command(self, cmd: str) -> Tuple[bool, Optional[str]]:
        """
        检查命令是否安全

        Args:
            cmd: 要执行的命令

        Returns:
            (is_safe, error_message)
            - is_safe: True 表示安全，False 表示危险
            - error_message: 如果不安全，返回错误消息
        """
        if not self.enabled:
            return True, None

        cmd_lower = cmd.lower().strip()

        # 检查危险命令黑名单
        for dangerous_pattern in self.dangerous_commands:
            if self._matches_pattern(cmd_lower, dangerous_pattern.lower()):
                return False, self._build_dangerous_error(cmd, dangerous_pattern)

        # 检查需要确认的命令
        if not self.allow_confirmation:
            for confirm_pattern in self.require_confirmation:
                if self._matches_pattern(cmd_lower, confirm_pattern.lower()):
                    return False, self._build_confirmation_error(cmd, confirm_pattern)

        return True, None

    @staticmethod
    def _matches_pattern(cmd: str, pattern: str) -> bool:
        """
        检查命令是否匹配模式

        支持：
        - 精确匹配：shutdown
        - 包含匹配：rm -rf
        - 正则表达式：以 regex: 开头
        """
        if pattern.startswith("regex:"):
            regex_pattern = pattern[6:].strip()
            return bool(re.search(regex_pattern, cmd))

        # 检查是否为独立命令（避免误匹配，如 "format" 不应匹配 "reformat"）
        # 使用单词边界检查
        pattern_parts = pattern.split()
        if len(pattern_parts) == 1:
            # 单个命令词，检查是否为命令开头或独立单词
            # 排除在 echo 等安全命令中出现的情况

            # 如果命令以 echo 开头，不应该匹配危险模式
            if cmd.strip().startswith('echo '):
                return False

            # 检查是否为命令的第一个词（独立命令）
            cmd_first_word = cmd.split()[0] if cmd.split() else ""
            if cmd_first_word == pattern:
                return True

            # 或者作为独立单词出现（用于参数中的匹配）
            return bool(re.search(r'\b' + re.escape(pattern) + r'\b', cmd))
        else:
            # 多个词的模式，检查是否包含
            # 同样排除 echo 命令
            if cmd.strip().startswith('echo '):
                return False
            return pattern in cmd

    @staticmethod
    def _build_dangerous_error(cmd: str, pattern: str) -> str:
        """构建危险命令错误消息"""
        return f"""🚫 SECURITY BLOCK: Dangerous command detected!

Command: {cmd}
Matched pattern: {pattern}

This command is blocked because it could cause:
- System shutdown or restart
- Irreversible data deletion
- System configuration damage
- Security vulnerabilities

REASON: This operation is too dangerous to execute automatically.

If you need to perform system maintenance:
1. Ask the user for explicit permission
2. Explain what the command will do
3. Suggest safer alternatives if available

DO NOT attempt to bypass this security check."""

    @staticmethod
    def _build_confirmation_error(cmd: str, pattern: str) -> str:
        """构建需要确认的命令错误消息"""
        return f"""⚠️ SECURITY WARNING: Command requires user confirmation

Command: {cmd}
Matched pattern: {pattern}

This command requires explicit user confirmation because it could:
- Delete files or directories
- Terminate processes
- Modify system registry
- Make other potentially destructive changes

REQUIRED ACTION:
1. Explain to the user what this command will do
2. Ask for explicit permission before proceeding
3. Suggest safer alternatives if available

Interactive confirmation is currently DISABLED in config.
To enable it, set tools.security.allow_confirmation: true in config.yaml

DO NOT execute this command without user permission."""


# 全局单例
_checker_instance = None


def get_security_checker() -> CommandSecurityChecker:
    """获取安全检查器单例"""
    global _checker_instance
    if _checker_instance is None:
        _checker_instance = CommandSecurityChecker()
    return _checker_instance


def check_command_safety(cmd: str) -> Tuple[bool, Optional[str]]:
    """
    便捷函数：检查命令安全性

    Args:
        cmd: 要检查的命令

    Returns:
        (is_safe, error_message)
    """
    checker = get_security_checker()
    return checker.check_command(cmd)
