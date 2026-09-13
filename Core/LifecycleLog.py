"""LifecycleLog — 生命周期状态实时日志（单一 text 文件）。

从程序启动到每一轮 ReAct 循环结束的完整状态流程，实时 append 到项目根目录的
`lifecycle.txt`（每次 flush，供 `tail -f` / `Get-Content -Wait` 实时查看）。

每行自动带上当前线程名，区分主流程与后台线程（记忆管线 / LLM executor /
启动 token 统计等）的交错输出。用法：

    from Core.LifecycleLog import lifecycle
    lifecycle("启动: MemoryManager", "初始化完成")          # 顶层阶段
    lifecycle("══ 第 1 轮 ══", '用户: "你好"', level=1)     # 轮边界
    lifecycle("记忆检索", "完成", level=2)                   # 轮内子项

格式（肉眼可读）：
    [2026-09-12 21:50:01.234] ⟨主线程⟩ 启动: MemoryManager — 初始化完成

    [2026-09-12 21:50:10.000] ⟨主线程⟩ ══ 第 1 轮 ══ — 用户: "你好"
    [2026-09-12 21:50:10.100] ⟨主线程⟩   记忆检索 — 完成
    [2026-09-12 21:50:12.300] ⟨PipelineManager-L1⟩   LLM 请求发出 — ... tools=0
"""

import threading
from datetime import datetime
from pathlib import Path

_lock = threading.Lock()

# 单一输出文件：项目根目录 lifecycle.txt
_PATH = Path(__file__).resolve().parent.parent / "lifecycle.txt"

# 线程名中文化（其余线程用原名，如 PipelineManager-L1 / llm-query-0 / embedding-warmup）
_THREAD_ZH = {"MainThread": "主线程"}


def _thread_tag() -> str:
    name = threading.current_thread().name
    return _THREAD_ZH.get(name, name)


def lifecycle(stage: str, detail: str = "", *, level: int = 0) -> None:
    """实时追加一条生命周期状态。

    Args:
        stage: 阶段/事件名，如 "启动: MemoryManager"、"LLM 请求发出"。
        detail: 关键详情，如 组件名、token 数、工具名、耗时。
        level: 0=顶层阶段（无缩进）；1=轮级边界（前加空行）；2=轮内子项（缩进）。
    """
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        tag = _thread_tag()
        if level == 1:
            line = f"\n[{ts}] ⟨{tag}⟩ {stage}"
        elif level == 2:
            line = f"[{ts}] ⟨{tag}⟩   {stage}"
        else:
            line = f"[{ts}] ⟨{tag}⟩ {stage}"
        if detail:
            line += f" — {detail}"
        with _lock:
            with open(_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
    except Exception:
        # 生命周期日志绝不能影响主流程
        pass
