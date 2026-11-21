"""
BackgroundTaskManager - Thread-safe singleton for background task lifecycle.

When a tool call exceeds the background_timeout threshold, it is moved to
a background thread via ThreadPoolExecutor. The agent continues working,
and completed results are drained at step boundaries and injected into
WorkingMemory as user-role system messages.
"""

import asyncio
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from Core.LogManager import get_logger
from Core.Message import CoreMessage, MessageSource

logger = get_logger(__name__)


@dataclass
class BackgroundResult:
    """Result of a completed background task."""
    task_id: int
    tool_name: str
    result: Any
    description: str = ""
    error: Optional[str] = None
    completed_at: float = 0.0
    timed_out: bool = False


class BackgroundTaskManager:
    """Thread-safe singleton for managing background task lifecycle.

    Tasks are submitted as coroutines and executed in dedicated threads
    with their own event loops. Results are collected via drain_pending()
    and injected into WorkingMemory via inject_into_memory().
    """

    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True

        self._counter = 0
        self._counter_lock = threading.Lock()
        self._pending_results: Dict[int, BackgroundResult] = {}
        self._results_lock = threading.Lock()
        self._active_metadata: Dict[int, dict] = {}  # task_id -> {tool_name, args, submitted_at}
        self._active_lock = threading.Lock()
        self._tracked_tasks: Dict[int, "asyncio.Task"] = {}  # task_id -> running Task (for cancellation)
        # 后台硬超时：到期仍未完成的自动转后台任务被 cancel（结果照常注入，非静默）
        self._expiry_handles: Dict[int, object] = {}
        self._expired: set = set()
        self._executor = ThreadPoolExecutor(
            max_workers=5, thread_name_prefix="bg-task-"
        )
        self._completion_callbacks: List[Callable] = []
        self._callbacks_lock = threading.Lock()
        self._start_callbacks: List[Callable] = []
        self._watcher_active = False
        self._reactivation_count = 0

    def _fire_start_callbacks(self, task_id: int):
        meta = self.get_active_metadata(task_id)
        with self._callbacks_lock:
            callbacks = list(self._start_callbacks)
        for cb in callbacks:
            try:
                cb(task_id, meta or {})
            except Exception:
                logger.debug(
                    "[bg-task] Start callback error for task #%d", task_id, exc_info=True
                )

    def get_active_metadata(self, task_id: int) -> Optional[dict]:
        with self._active_lock:
            return dict(self._active_metadata.get(task_id) or {}) or None

    def _next_id(self) -> int:
        with self._counter_lock:
            self._counter += 1
            return self._counter

    def submit(self, tool_name: str, coro, description: str = "") -> int:
        """Submit a coroutine to run in background.

        Returns the task ID immediately. The coroutine is executed in a
        dedicated thread with its own event loop.

        Args:
            tool_name: Name of the tool being executed.
            coro: Coroutine to execute.
            description: Human-readable description for logging / injection.

        Returns:
            int: Unique task ID.
        """
        task_id = self._next_id()

        # Record active metadata
        args_str = description
        if description.startswith(tool_name) and len(description) > len(tool_name):
            raw = description[len(tool_name):].strip()
            if raw.startswith('(') and raw.endswith(')'):
                args_str = raw[1:-1]
        with self._active_lock:
            self._active_metadata[task_id] = {
                "tool_name": tool_name,
                "args": args_str,
                "submitted_at": time.time(),
            }

        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(coro)
            except Exception as exc:
                result = json.dumps(
                    {"error": f"Task #{task_id} failed: {type(exc).__name__}: {exc}"},
                    ensure_ascii=False,
                )
            finally:
                loop.close()

            bg_result = BackgroundResult(
                task_id=task_id,
                tool_name=tool_name,
                result=result,
                description=description,
                error=(
                    None
                    if not isinstance(result, str) or not result.startswith('{"error"')
                    else result
                ),
                completed_at=time.time(),
            )

            with self._results_lock:
                self._pending_results[task_id] = bg_result
            with self._active_lock:
                self._active_metadata.pop(task_id, None)

            logger.info(
                "[bg-task] Task #%d (%s) completed%s",
                task_id,
                tool_name,
                " (error)" if bg_result.error else "",
            )

            # Fire completion callbacks
            with self._callbacks_lock:
                callbacks = list(self._completion_callbacks)
            for cb in callbacks:
                try:
                    cb(task_id, bg_result)
                except Exception:
                    logger.debug(
                        "[bg-task] Callback error for task #%d", task_id, exc_info=True
                    )

        self._executor.submit(_run)
        self._fire_start_callbacks(task_id)
        logger.info(
            "[bg-task] Task #%d submitted: %s - %s",
            task_id, tool_name, description[:120],
        )
        return task_id

    def track_task(self, tool_name: str, task: "asyncio.Task", description: str = "", expire_after_seconds: float = 0) -> int:
        """Track a live asyncio.Task that continues running in the background.

        The task can later be cancelled via :meth:`cancel_task`. For tools that
        spawn subprocesses (e.g. run_cmd), cancelling the asyncio Task raises
        :class:`asyncio.CancelledError` inside the handler, which is expected to
        clean up its own subprocess.

        Args:
            tool_name: Name of the tool being executed.
            task: The running asyncio.Task to track.
            description: Human-readable description for logging / injection.
            expire_after_seconds: >0 时到点仍未完成则 cancel（结果照常注入，
                非静默终止）。只用于自动转后台的任务；显式 run_background
                的长任务（服务器/守护进程）传 0 不设限。

        Returns:
            int: Unique task ID.
        """
        task_id = self._next_id()
        self._tracked_tasks[task_id] = task
        if expire_after_seconds and expire_after_seconds > 0:
            self._schedule_expiry(task_id, task, float(expire_after_seconds))

        # Record active metadata
        args_str = description
        if description.startswith(tool_name) and len(description) > len(tool_name):
            raw = description[len(tool_name):].strip()
            if raw.startswith('(') and raw.endswith(')'):
                args_str = raw[1:-1]
        with self._active_lock:
            self._active_metadata[task_id] = {
                "tool_name": tool_name,
                "args": args_str,
                "submitted_at": time.time(),
            }

        def _on_done(t: "asyncio.Task"):
            self._tracked_tasks.pop(task_id, None)
            handle = self._expiry_handles.pop(task_id, None)
            if handle is not None:
                handle.cancel()
            timed_out = task_id in self._expired
            self._expired.discard(task_id)
            try:
                result = t.result()
                error = None
            except asyncio.CancelledError:
                if timed_out:
                    result = json.dumps(
                        {
                            "error": (
                                f"Task #{task_id}（{tool_name}）在后台执行超时，"
                                f"超过最大允许时长已被终止"
                            ),
                            "hint": "该任务未能在后台完成。可拆小步骤重试，或改前台执行；不要原样重发同一个长任务。",
                        },
                        ensure_ascii=False,
                    )
                else:
                    result = json.dumps(
                        {"error": f"Task #{task_id} was cancelled"},
                        ensure_ascii=False,
                    )
                error = result
            except Exception as exc:
                result = json.dumps(
                    {"error": f"Task #{task_id} failed: {type(exc).__name__}: {exc}"},
                    ensure_ascii=False,
                )
                error = result

            bg_result = BackgroundResult(
                task_id=task_id,
                tool_name=tool_name,
                result=result,
                description=description,
                error=error,
                completed_at=time.time(),
                timed_out=timed_out,
            )

            with self._results_lock:
                self._pending_results[task_id] = bg_result
            with self._active_lock:
                self._active_metadata.pop(task_id, None)

            logger.info(
                "[bg-task] Tracked task #%d (%s) completed%s%s",
                task_id,
                tool_name,
                " (error)" if bg_result.error else "",
                " (timed out)" if timed_out else "",
            )

            with self._callbacks_lock:
                callbacks = list(self._completion_callbacks)
            for cb in callbacks:
                try:
                    cb(task_id, bg_result)
                except Exception:
                    logger.debug(
                        "[bg-task] Callback error for tracked task #%d",
                        task_id,
                        exc_info=True,
                    )

        task.add_done_callback(_on_done)
        self._fire_start_callbacks(task_id)
        logger.info(
            "[bg-task] Task #%d tracked (shielded): %s - %s",
            task_id, tool_name, description[:120],
        )
        return task_id

    def _schedule_expiry(self, task_id: int, task: "asyncio.Task", seconds: float):
        """到点仍未完成则 cancel——结果经正常 done 回调注入（含超时说明），非静默。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        def _expire():
            if self._tracked_tasks.get(task_id) is task:
                self._expired.add(task_id)
                logger.warning(
                    "[bg-task] Task #%d exceeded background max duration (%.0fs), cancelling",
                    task_id, seconds,
                )
                task.cancel()

        self._expiry_handles[task_id] = loop.call_later(seconds, _expire)

    def is_tracked(self, task) -> bool:
        """判断 asyncio.Task 是否已被纳入后台管理（显式 run_background 或
        超时自动转后台）。工具 handler 可用它调整行为，例如 run_cmd 对已
        后台化的子进程豁免硬超时——后台任务不应被定时器杀死。"""
        return any(t is task for t in list(self._tracked_tasks.values()))

    def cancel_task(self, task_id: int) -> bool:
        """Request cancellation of a tracked background task.

        Returns True if the task was found and cancellation was requested.
        The actual stop is cooperative: the asyncio Task receives a cancel
        request, and tools that spawn subprocesses are expected to clean up
        in their asyncio.CancelledError handler.
        """
        task = self._tracked_tasks.pop(task_id, None)
        if task is None:
            logger.info("[bg-task] Cancel requested for unknown task #%d", task_id)
            return False
        task.cancel()
        with self._active_lock:
            self._active_metadata.pop(task_id, None)
        logger.info("[bg-task] Cancellation requested for task #%d", task_id)
        return True

    def is_completed(self, task_id: int) -> bool:
        with self._results_lock:
            return task_id in self._pending_results

    def get_result(self, task_id: int) -> Optional[BackgroundResult]:
        with self._results_lock:
            return self._pending_results.get(task_id)

    def drain_pending(self) -> List[BackgroundResult]:
        """Get all completed results and clear pending queue."""
        with self._results_lock:
            results = list(self._pending_results.values())
            self._pending_results.clear()
        return results

    def has_pending(self) -> bool:
        with self._results_lock:
            return len(self._pending_results) > 0

    def register_callback(self, cb: Callable[[int, BackgroundResult], None]):
        """Register a callback invoked when any background task completes.

        Callback signature: callback(task_id: int, result: BackgroundResult)
        """
        with self._callbacks_lock:
            self._completion_callbacks.append(cb)

    def register_start_callback(self, cb: Callable[[int, dict], None]):
        """Register a callback invoked when any background task starts.

        Callback signature: callback(task_id: int, metadata: {tool_name, args, submitted_at})
        """
        with self._callbacks_lock:
            self._start_callbacks.append(cb)

    def unregister_callback(self, cb: Callable):
        with self._callbacks_lock:
            try:
                self._completion_callbacks.remove(cb)
            except ValueError:
                pass

    def cancel_all(self):
        """Shutdown the executor (best-effort cancellation)."""
        self._executor.shutdown(wait=False)
        with self._results_lock:
            self._pending_results.clear()

    def get_status_summary(self) -> dict:
        with self._results_lock:
            pending_count = len(self._pending_results)
            task_ids = list(self._pending_results.keys())
        return {
            "pending_results": pending_count,
            "task_ids": task_ids,
            "reactivation_count": self._reactivation_count,
        }

    def get_detailed_status(self) -> str:
        """Return a human-readable status report of all background tasks.

        Includes running tasks (with tool name, args, task ID, start time)
        and pending completed tasks awaiting drain. Formatted for easy reading
        by both humans and agents.
        """
        now = time.time()
        lines = ["=== Background Task Status ===", ""]

        # Active (running) tasks
        with self._active_lock:
            active = list(self._active_metadata.items())
        active.sort(key=lambda x: x[1]["submitted_at"])

        if active:
            lines.append(f"> RUNNING ({len(active)})")
            lines.append("-" * 40)
            for task_id, meta in active:
                elapsed = int(now - meta["submitted_at"])
                elapsed_str = f"{elapsed}s" if elapsed < 60 else f"{elapsed // 60}m{elapsed % 60}s"
                start_time = datetime.fromtimestamp(meta["submitted_at"]).strftime("%Y-%m-%d %H:%M:%S")
                lines.append(f"  #{task_id}  {meta['tool_name']}")
                lines.append(f"       Args: {meta['args']}")
                lines.append(f"       Started: {start_time} | elapsed: {elapsed_str}")
                lines.append("")
        else:
            lines.append("> RUNNING: none")
            lines.append("")

        # Pending (completed, not yet drained) results
        with self._results_lock:
            pending = list(self._pending_results.values())
        pending.sort(key=lambda r: r.completed_at)

        if pending:
            lines.append(f"> PENDING ({len(pending)}) -- completed, awaiting injection")
            lines.append("-" * 40)
            for r in pending:
                comp_time = datetime.fromtimestamp(r.completed_at).strftime("%Y-%m-%d %H:%M:%S")
                lines.append(f"  #{r.task_id}  {r.tool_name}  (completed {comp_time})")
                lines.append(f"       Description: {r.description[:200]}")
                if r.error:
                    lines.append(f"       ERROR: {r.error[:120]}")
                lines.append("")
        else:
            lines.append("> PENDING: none")
            lines.append("")

        # Reactivation count
        lines.append(f"Reactivations: {self._reactivation_count}")
        return "\n".join(lines)

    def increment_reactivation(self):
        self._reactivation_count += 1

    @property
    def reactivation_count(self) -> int:
        """空闲唤醒计数（只读）——宿主据此判断是否达到 max_reactivations 上限。"""
        return self._reactivation_count

    @staticmethod
    def inject_into_memory(memory_manager, results: List[BackgroundResult]):
        """Format completed background results as user-role messages and
        inject into WorkingMemory.

        Uses the [task-background #N] prefix so the LLM naturally interprets
        these as system-initiated context updates.

        Applies ToolResultBudget to non-error results — same token-budget
        path as synchronous tool results in ActorAgent, so oversized outputs
        are saved to cache files and replaced with Read-able placeholders.
        """
        try:
            from Core.ToolResultBudget import ToolResultBudget
        except ImportError:
            ToolResultBudget = None  # 宿主未带 Core 时跳过预算截断（降级不报错）
        from Core.ToolCall import ToolCall
        from Tools.config_proxy import get_config
        config = get_config()

        # Collect non-error results for unified token budget processing
        budget_inputs = []
        budget_idx_by_original = {}
        for i, r in enumerate(results):
            if not r.error:
                budget_idx_by_original[i] = len(budget_inputs)
                budget_inputs.append({
                    "tool_name": r.tool_name,
                    "result_text": str(r.result),
                })

        if budget_inputs and ToolResultBudget is not None:
            max_single = config.get("tools.result_budget.max_single_tokens", 8000)
            max_batch = config.get("tools.result_budget.max_batch_tokens", 24000)
            cache_dir = config.get(
                "tools.result_budget.cache_dir", "./runtime_memory/tool_cache"
            )
            ToolResultBudget.apply(budget_inputs, cache_dir, max_single, max_batch)

        for i, r in enumerate(results):
            # r.error 只覆盖 handler 抛异常的情况；run_cmd 等工具正常返回但
            # 内容为失败（exit_code!=0 / timed_out / interrupted）时，必须用
            # 统一的分类器识别，否则"服务器被超时杀掉"会以普通成功结果注入，
            # 模型极易忽略并继续声称服务存活。
            classified_fail = (not r.error) and ToolCall.classify_is_error(
                r.tool_name, str(r.result)
            )
            if r.error:
                content = (
                    f"[task-background #{r.task_id} completed with error]\n"
                    f"Tool: {r.tool_name}\n"
                    f"Error: {r.error}\n\n"
                    f"Please note this tool failed in the background. "
                    f"Do NOT retry it unless the user explicitly asks."
                )
            elif classified_fail:
                result_str = budget_inputs[budget_idx_by_original[i]]["result_text"]
                content = (
                    f"[task-background #{r.task_id} FAILED]\n"
                    f"Tool: {r.tool_name}\n"
                    f"Description: {r.description}\n"
                    f"Result:\n{result_str}\n\n"
                    f"⚠️ 该后台任务失败或已被终止。如果它是服务器/守护进程，"
                    f"现在已经停止运行——不要假设它仍然存活；向用户汇报前必须先重启并实测。"
                )
            else:
                result_str = budget_inputs[budget_idx_by_original[i]]["result_text"]
                content = (
                    f"[task-background #{r.task_id} completed]\n"
                    f"Tool: {r.tool_name}\n"
                    f"Description: {r.description}\n"
                    f"Result:\n{result_str}\n\n"
                    f"This result was from a background task. "
                    f"Please incorporate it into your response if relevant."
                )
            memory_manager.append(CoreMessage.context_only(
                MessageSource.BG_TASK,
                content,
            ).to_dict())
            logger.info(
                "[bg-task] Injected result for task #%d into working memory",
                r.task_id,
            )
