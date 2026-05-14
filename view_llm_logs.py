#!/usr/bin/env python3
"""
查看 LLM API 日志的可视化工具

用法:
    python view_llm_logs.py                    # 查看最近10条
    python view_llm_logs.py --limit 20         # 查看最近20条
    python view_llm_logs.py --all              # 查看全部
    python view_llm_logs.py --filter thinking  # 只看包含thinking的
"""

import json
import sys
from pathlib import Path
from datetime import datetime

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.syntax import Syntax
    from rich.markdown import Markdown
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


def load_logs(log_file, limit=None):
    """加载 JSONL 日志文件"""
    if not log_file.exists():
        return []

    logs = []
    with open(log_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    logs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    # 返回最近的 N 条
    if limit:
        return logs[-limit:]
    return logs


def format_timestamp(ts_str):
    """格式化时间戳"""
    try:
        dt = datetime.fromisoformat(ts_str)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except:
        return ts_str


def display_with_rich(logs):
    """使用 Rich 库显示日志"""
    console = Console()

    console.print(f"\n[bold cyan]📊 LLM API 日志查看器[/bold cyan]")
    console.print(f"[dim]共 {len(logs)} 条记录[/dim]\n")

    for i, log in enumerate(logs, 1):
        timestamp = format_timestamp(log.get("timestamp", ""))
        provider = log.get("provider", "unknown")
        status = log.get("status", "unknown")

        # 标题
        status_emoji = "✅" if status == "success" else "❌"
        console.print(f"\n[bold yellow]{'='*80}[/bold yellow]")
        console.print(f"[bold cyan]#{i}[/bold cyan] {status_emoji} [dim]{timestamp}[/dim] | Provider: [green]{provider}[/green]")

        # 请求信息
        request = log.get("request", {})
        if request:
            table = Table(show_header=False, box=None, padding=(0, 2))
            table.add_column(style="cyan", width=20)
            table.add_column(style="white")

            table.add_row("Model", request.get("model", "N/A"))
            table.add_row("Temperature", str(request.get("temperature", "N/A")))
            table.add_row("Max Tokens", str(request.get("max_tokens", "N/A")))
            table.add_row("Messages Count", str(request.get("messages_count", "N/A")))
            table.add_row("System Blocks", str(request.get("system_blocks_count", "N/A")))
            table.add_row("Tools Count", str(request.get("tools_count", "N/A")))

            console.print(Panel(table, title="[bold]📤 Request Info[/bold]", border_style="blue"))

        # 响应信息
        if status == "error":
            error = log.get("error", "Unknown error")
            console.print(Panel(f"[red]{error}[/red]", title="[bold red]❌ Error[/bold red]", border_style="red"))
        else:
            response = log.get("response", {})
            if response:
                # 使用信息
                usage = response.get("usage", {})
                if usage:
                    input_tokens = usage.get("input_tokens", 0)
                    output_tokens = usage.get("output_tokens", 0)
                    cache_creation = usage.get("cache_creation_input_tokens", 0)
                    cache_read = usage.get("cache_read_input_tokens", 0)

                    usage_table = Table(show_header=False, box=None, padding=(0, 2))
                    usage_table.add_column(style="cyan", width=25)
                    usage_table.add_column(style="white")

                    from Agent.TokenEstimator import TokenEstimator

                    usage_table.add_row("Input Tokens", TokenEstimator.format(input_tokens))
                    usage_table.add_row("Output Tokens", TokenEstimator.format(output_tokens))
                    usage_table.add_row("Cache Creation", TokenEstimator.format(cache_creation))
                    usage_table.add_row("Cache Read", TokenEstimator.format(cache_read))

                    cache_rate = (cache_read / input_tokens * 100) if input_tokens > 0 else 0
                    usage_table.add_row("Cache Hit Rate", f"{cache_rate:.1f}%")
                    usage_table.add_row("Total Tokens", TokenEstimator.format(input_tokens + output_tokens))

                    console.print(Panel(usage_table, title="[bold]💰 Token Usage[/bold]", border_style="green"))

                # 响应内容
                content = response.get("content", [])
                if content:
                    console.print(f"\n[bold magenta]📝 Response Content ({len(content)} blocks):[/bold magenta]")

                    for j, block in enumerate(content, 1):
                        block_type = block.get("type", "unknown")

                        if block_type == "thinking":
                            thinking_text = block.get("thinking", "")
                            console.print(Panel(
                                thinking_text[:500] + ("..." if len(thinking_text) > 500 else ""),
                                title=f"[bold cyan]💭 Block {j}: THINKING[/bold cyan] ({len(thinking_text)} chars)",
                                border_style="cyan"
                            ))

                        elif block_type == "text":
                            text_content = block.get("text", "")
                            console.print(Panel(
                                text_content[:500] + ("..." if len(text_content) > 500 else ""),
                                title=f"[bold green]📄 Block {j}: TEXT[/bold green] ({len(text_content)} chars)",
                                border_style="green"
                            ))

                        else:
                            console.print(f"[dim]Block {j}: {block_type}[/dim]")

                # Stop Reason
                stop_reason = response.get("stop_reason")
                if stop_reason:
                    console.print(f"\n[dim]Stop Reason: {stop_reason}[/dim]")


def display_plain(logs):
    """纯文本显示日志"""
    print(f"\n{'='*80}")
    print(f"LLM API 日志查看器 - 共 {len(logs)} 条记录")
    print(f"{'='*80}\n")

    for i, log in enumerate(logs, 1):
        timestamp = format_timestamp(log.get("timestamp", ""))
        provider = log.get("provider", "unknown")
        status = log.get("status", "unknown")

        print(f"\n{'='*80}")
        print(f"#{i} [{status.upper()}] {timestamp} | Provider: {provider}")
        print(f"{'='*80}")

        # 请求信息
        request = log.get("request", {})
        if request:
            print("\n[REQUEST INFO]")
            print(f"  Model: {request.get('model', 'N/A')}")
            print(f"  Temperature: {request.get('temperature', 'N/A')}")
            print(f"  Max Tokens: {request.get('max_tokens', 'N/A')}")
            print(f"  Messages Count: {request.get('messages_count', 'N/A')}")
            print(f"  System Blocks: {request.get('system_blocks_count', 'N/A')}")
            print(f"  Tools Count: {request.get('tools_count', 'N/A')}")

        # 响应信息
        if status == "error":
            error = log.get("error", "Unknown error")
            print(f"\n[ERROR]\n  {error}")
        else:
            response = log.get("response", {})
            if response:
                # Token 使用
                usage = response.get("usage", {})
                if usage:
                    input_tokens = usage.get("input_tokens", 0)
                    output_tokens = usage.get("output_tokens", 0)
                    cache_creation = usage.get("cache_creation_input_tokens", 0)
                    cache_read = usage.get("cache_read_input_tokens", 0)
                    cache_rate = (cache_read / input_tokens * 100) if input_tokens > 0 else 0

                    from Agent.TokenEstimator import TokenEstimator

                    print("\n[TOKEN USAGE]")
                    print(f"  Input Tokens: {TokenEstimator.format(input_tokens)}")
                    print(f"  Output Tokens: {TokenEstimator.format(output_tokens)}")
                    print(f"  Cache Creation: {TokenEstimator.format(cache_creation)}")
                    print(f"  Cache Read: {TokenEstimator.format(cache_read)}")
                    print(f"  Cache Hit Rate: {cache_rate:.1f}%")
                    print(f"  Total Tokens: {TokenEstimator.format(input_tokens + output_tokens)}")

                # 响应内容
                content = response.get("content", [])
                if content:
                    print(f"\n[RESPONSE CONTENT] ({len(content)} blocks)")

                    for j, block in enumerate(content, 1):
                        block_type = block.get("type", "unknown")

                        if block_type == "thinking":
                            thinking_text = block.get("thinking", "")
                            print(f"\n  Block {j}: THINKING ({len(thinking_text)} chars)")
                            print(f"  {'-'*76}")
                            preview = thinking_text[:300] + ("..." if len(thinking_text) > 300 else "")
                            print(f"  {preview}")

                        elif block_type == "text":
                            text_content = block.get("text", "")
                            print(f"\n  Block {j}: TEXT ({len(text_content)} chars)")
                            print(f"  {'-'*76}")
                            preview = text_content[:300] + ("..." if len(text_content) > 300 else "")
                            print(f"  {preview}")

                        else:
                            print(f"\n  Block {j}: {block_type}")

                # Stop Reason
                stop_reason = response.get("stop_reason")
                if stop_reason:
                    print(f"\n  Stop Reason: {stop_reason}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="查看 LLM API 日志")
    parser.add_argument("--limit", type=int, default=10, help="显示最近 N 条记录 (默认: 10)")
    parser.add_argument("--all", action="store_true", help="显示全部记录")
    parser.add_argument("--filter", type=str, help="过滤关键词 (例如: thinking, text)")
    parser.add_argument("--file", type=str, default="./runtime_memory/llm_logs/api_responses.jsonl", help="日志文件路径")

    args = parser.parse_args()

    log_file = Path(args.file)
    if not log_file.exists():
        print(f"❌ 日志文件不存在: {log_file}")
        return

    # 加载日志
    limit = None if args.all else args.limit
    logs = load_logs(log_file, limit)

    if not logs:
        print("📭 没有找到日志记录")
        return

    # 过滤
    if args.filter:
        filtered_logs = []
        for log in logs:
            log_str = json.dumps(log, ensure_ascii=False).lower()
            if args.filter.lower() in log_str:
                filtered_logs.append(log)
        logs = filtered_logs
        print(f"🔍 过滤关键词 '{args.filter}': 找到 {len(logs)} 条记录")

    # 显示
    if RICH_AVAILABLE:
        display_with_rich(logs)
    else:
        display_plain(logs)
        print("\n💡 提示: 安装 rich 库可获得更好的显示效果: pip install rich")


if __name__ == "__main__":
    main()
