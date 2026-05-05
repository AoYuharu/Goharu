"""
子agent修复效果演示

展示：
1. 实时输出显示
2. 串行执行控制
3. Rich标记转义
"""

import asyncio
import sys
import codecs

# 修复Windows控制台编码问题
if sys.platform == "win32":
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.detach())
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.detach())

sys.path.insert(0, 'E:\\TableHelper')

from rich.console import Console
from rich.panel import Panel
from rich.markup import escape
from Tools.builtin.agent_delegate import _manager

# 创建Console时指定force_terminal=True避免编码问题
console = Console(force_terminal=True)


def demo_rich_escape():
    """演示Rich标记转义"""
    console.print("\n" + "=" * 60)
    console.print("[bold cyan]演示1: Rich标记转义[/bold cyan]")
    console.print("=" * 60 + "\n")

    # 模拟包含特殊标签的工具输出
    tool_outputs = [
        "[TOOL_CALL] Read(file='config.yaml') [/TOOL_CALL]",
        "找到 <class ActorAgent> 定义",
        "使用 [bold]粗体[/bold] 标记",
        "正常文本输出"
    ]

    console.print("[yellow]问题：[/yellow] 直接输出包含特殊标签的文本会导致 MarkupError")
    console.print("[green]解决：[/green] 使用 escape() 函数转义特殊字符\n")

    for i, text in enumerate(tool_outputs, 1):
        console.print(f"[dim]示例 {i}:[/dim]")
        # 注意：这里显示原文时也需要转义，否则会触发MarkupError
        console.print(f"  原文: {escape(text)}")
        console.print(f"  转义: {escape(text)}")

        # 显示转义后的效果
        console.print(Panel(
            escape(text),
            title="[bold green]转义后渲染效果[/bold green]",
            border_style="green",
            padding=(0, 2)
        ))
        console.print()


def demo_serial_execution():
    """演示串行执行"""
    console.print("\n" + "=" * 60)
    console.print("[bold cyan]演示2: 串行执行控制[/bold cyan]")
    console.print("=" * 60 + "\n")

    console.print("[yellow]问题：[/yellow] 主agent可能并发调用多个子agent，导致资源浪费")
    console.print("[green]解决：[/green] 使用全局锁强制串行执行\n")

    import threading
    import time

    execution_log = []

    def simulate_agent(agent_id: int):
        """模拟子agent执行"""
        with _manager.execution_lock:
            start_time = time.time()
            execution_log.append(f"[{start_time:.2f}] Agent {agent_id} 开始执行")
            console.print(f"[cyan]🚀 Agent {agent_id} 开始执行[/cyan]")

            # 模拟执行时间
            time.sleep(0.5)

            end_time = time.time()
            execution_log.append(f"[{end_time:.2f}] Agent {agent_id} 执行完成")
            console.print(f"[green]✅ Agent {agent_id} 执行完成[/green]")

    console.print("[bold]启动3个子agent（模拟并发调用）：[/bold]\n")

    # 创建3个线程模拟并发调用
    threads = []
    for i in range(3):
        t = threading.Thread(target=simulate_agent, args=(i + 1,))
        threads.append(t)
        t.start()

    # 等待所有线程完成
    for t in threads:
        t.join()

    console.print("\n[bold]执行日志：[/bold]")
    for log in execution_log:
        console.print(f"  {log}")

    console.print("\n[green]✓ 观察：虽然3个agent同时启动，但它们串行执行（一个完成后下一个才开始）[/green]")


def demo_realtime_output():
    """演示实时输出"""
    console.print("\n" + "=" * 60)
    console.print("[bold cyan]演示3: 实时输出显示[/bold cyan]")
    console.print("=" * 60 + "\n")

    console.print("[yellow]问题：[/yellow] 子agent的内部ReAct过程不可见，只能看到最终结果")
    console.print("[green]解决：[/green] 通过回调机制实时输出子agent的执行过程\n")

    # 设置输出回调
    def output_callback(message: str, level: str):
        if level == "info":
            console.print(f"[cyan]{escape(message)}[/cyan]")
        elif level == "debug":
            console.print(f"[dim]{escape(message)}[/dim]")
        elif level == "warning":
            console.print(f"[yellow]{escape(message)}[/yellow]")
        elif level == "error":
            console.print(f"[red]{escape(message)}[/red]")

    _manager.set_output_callback(output_callback)

    console.print("[bold]模拟子agent执行过程：[/bold]\n")

    # 模拟子agent的输出
    _manager.notify_output("🚀 启动 Explore agent [explore_demo123]", "info")
    _manager.notify_output("  🔄 [explore_demo123] 迭代 1/8", "info")
    _manager.notify_output("  💭 [explore_demo123] 思考: 我需要先查找相关文件...", "debug")
    _manager.notify_output("  🔧 [explore_demo123] 调用工具: Glob(pattern='**/*.py')", "info")
    _manager.notify_output("  ✓ [explore_demo123] 工具结果: 找到 15 个文件", "debug")
    _manager.notify_output("  🔄 [explore_demo123] 迭代 2/8", "info")
    _manager.notify_output("  💭 [explore_demo123] 思考: 现在读取关键文件...", "debug")
    _manager.notify_output("  🔧 [explore_demo123] 调用工具: Read(file='main.py')", "info")
    _manager.notify_output("  ✓ [explore_demo123] 工具结果: 读取成功，共 880 行", "debug")
    _manager.notify_output("  ✅ [explore_demo123] 得出最终结论", "info")
    _manager.notify_output("✅ Explore agent [explore_demo123] 完成", "info")

    console.print("\n[green]✓ 观察：用户可以实时看到子agent的思考和工具调用过程[/green]")


def demo_usage_guide():
    """演示使用指南"""
    console.print("\n" + "=" * 60)
    console.print("[bold cyan]演示4: 正确使用方式[/bold cyan]")
    console.print("=" * 60 + "\n")

    console.print("[bold green]✅ 正确做法：分阶段执行[/bold green]\n")

    correct_usage = """
第1轮：
  用户: "分析这个项目的架构"
  主agent: 调用 AgentDelegate(agent_type="Explore", task="分析项目结构")
  等待完成...

第2轮：
  主agent: 基于探索结果，调用 AgentDelegate(agent_type="Explore", task="分析Memory模块")
  等待完成...

第3轮：
  主agent: 调用 AgentDelegate(agent_type="Plan", task="设计新功能实现方案")
  等待完成...

第4轮：
  主agent: 基于Plan结果，开始实施
"""

    console.print(Panel(
        correct_usage,
        title="[bold green]推荐流程[/bold green]",
        border_style="green"
    ))

    console.print("\n[bold red]❌ 错误做法：一次调用多个[/bold red]\n")

    wrong_usage = """
一次性调用：
  AgentDelegate(agent_type="Explore", task="分析Memory模块")
  AgentDelegate(agent_type="Explore", task="分析Agent模块")
  AgentDelegate(agent_type="Plan", task="设计方案")

问题：
  - 会被强制串行化，浪费时间
  - 后面的agent无法利用前面的结果
  - 可能重复劳动
"""

    console.print(Panel(
        wrong_usage,
        title="[bold red]不推荐做法[/bold red]",
        border_style="red"
    ))


def main():
    """运行所有演示"""
    console.print("\n" + "=" * 70)
    console.print("[bold cyan]子Agent系统修复效果演示[/bold cyan]")
    console.print("=" * 70)

    try:
        # 演示1: Rich标记转义
        demo_rich_escape()

        # 演示2: 串行执行
        demo_serial_execution()

        # 演示3: 实时输出
        demo_realtime_output()

        # 演示4: 使用指南
        demo_usage_guide()

        console.print("\n" + "=" * 70)
        console.print("[bold green]演示完成！[/bold green]")
        console.print("=" * 70 + "\n")

        console.print("[dim]提示：运行 python main.py 启动完整系统，体验实际效果[/dim]\n")

    except Exception as e:
        console.print(f"\n[bold red]演示失败: {e}[/bold red]")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
