"""论文分析请求处理器

负责检测和处理论文分析请求。
"""
import re
from pathlib import Path
from typing import Optional, Tuple

from Agent.PaperAnalysisOrchestrator import PaperAnalysisOrchestrator
from Tools.registry import registry
from Tools.loader import load_builtin_tools
from configurationLoader import config


def detect_paper_analysis_request(question: str) -> Tuple[bool, Optional[str]]:
    """
    检测用户输入是否是论文分析请求

    Args:
        question: 用户输入的问题

    Returns:
        (is_paper_analysis, pdf_path): 是否是论文分析请求，以及PDF路径
    """
    # 检测关键词
    keywords = [
        r'分析论文[：:]\s*(.+\.pdf)',
        r'分析这篇论文[：:]\s*(.+\.pdf)',
        r'分析论文\s+(.+\.pdf)',  # 新增：分析论文 xxx.pdf
        r'分析这篇论文\s+(.+\.pdf)',  # 新增：分析这篇论文 xxx.pdf
        r'分析\s+(.+\.pdf)',
        r'analyze\s+paper[：:]\s*(.+\.pdf)',
        r'analyze\s+(.+\.pdf)',
    ]

    for pattern in keywords:
        match = re.search(pattern, question, re.IGNORECASE)
        if match:
            pdf_path = match.group(1).strip()

            # 验证文件是否存在
            # 尝试多种路径组合
            possible_paths = [
                pdf_path,  # 原始路径
                Path(pdf_path),  # Path 对象
                Path("essay") / pdf_path,  # essay 目录
                Path("essay") / Path(pdf_path).name,  # essay 目录 + 文件名
            ]

            for path in possible_paths:
                if Path(path).exists():
                    return (True, str(path))

            # 文件不存在，但检测到了论文分析意图
            return (True, pdf_path)

    return (False, None)


async def handle_paper_analysis(pdf_path: str, logger, console) -> dict:
    """
    处理论文分析请求

    Args:
        pdf_path: PDF文件路径
        logger: 日志记录器
        console: Rich控制台对象

    Returns:
        结果字典，包含 final_answer 等字段
    """
    # 验证文件存在
    if not Path(pdf_path).exists():
        error_msg = f"错误：找不到文件 {pdf_path}"
        if logger:
            logger.log_error(error_msg)
        return {
            "final_answer": error_msg,
            "error": True
        }

    # 检查是否有 Rich 可用
    try:
        from rich.console import Console
        RICH_AVAILABLE = True
    except ImportError:
        RICH_AVAILABLE = False

    # 创建输出回调函数
    def output_callback(message: str, level: str):
        """子agent输出回调"""
        if RICH_AVAILABLE and console:
            from rich.markup import escape
            if level == "info":
                console.print(f"[cyan]{escape(message)}[/cyan]")
            elif level == "debug":
                if config.get("ui.verbose", False):
                    console.print(f"[dim]{escape(message)}[/dim]")
            elif level == "warning":
                console.print(f"[yellow]{escape(message)}[/yellow]")
            elif level == "error":
                console.print(f"[red]{escape(message)}[/red]")
        else:
            print(message)

    try:
        # 确保工具已加载
        load_builtin_tools()

        # 创建 PaperAnalysisOrchestrator 实例
        orchestrator = PaperAnalysisOrchestrator(
            tools_registry=registry,
            output_callback=output_callback
        )

        # 执行论文分析
        if RICH_AVAILABLE and console:
            console.print(f"\n[bold cyan]开始分析论文：{pdf_path}[/bold cyan]\n")
        else:
            print(f"\n开始分析论文：{pdf_path}\n")

        result = orchestrator.analyze_paper(pdf_path)

        # 记录到日志
        if logger:
            logger.log_assistant_response(result.get("report", ""))

        # 格式化结果
        final_answer = result.get("report", "论文分析完成")

        return {
            "final_answer": final_answer,
            "paper_analysis": True,
            "paper_id": result.get("paper_id"),
            "stages_completed": result.get("stages_completed", []),
            "total_duration_ms": result.get("total_duration_ms", 0),
            "total_tokens": result.get("total_tokens", 0)
        }

    except Exception as e:
        error_msg = f"论文分析失败：{str(e)}"
        if logger:
            logger.log_error(error_msg)

        if RICH_AVAILABLE and console:
            console.print(f"[bold red]{error_msg}[/bold red]")
        else:
            print(error_msg)

        return {
            "final_answer": error_msg,
            "error": True
        }
