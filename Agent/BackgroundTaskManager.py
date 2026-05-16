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
from typing import Any, Callable, Dict, List, Optional

from Core.LogManager import get_logger

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
        self._executor = ThreadPoolExecutor(
            max_workers=5, thread_name_prefix="bg-task-"
        )
        self._completion_callbacks: List[Callable] = []
        self._callbacks_lock = threading.Lock()
        self._watcher_active = False
        self._reactivation_count = 0

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
        logger.info(
            "[bg-task] Task #%d submitted: %s - %s",
            task_id, tool_name, description[:120],
        )
        return task_id

    def track_task(self, tool_name: str, task: "asyncio.Task", description: str = "") -> int:
        """Track a live asyncio.Task that continues running in the background.

        Unlike submit() which starts a new coroutine in a ThreadPoolExecutor,
        this wraps an already-running Task (protected by asyncio.shield) with
        a done callback. The subprocess behind the task is NOT killed — it
        keeps running and its result is collected when it finishes naturally.

        Args:
            tool_name: Name of the tool being executed.
            task: The running asyncio.Task to track.
            description: Human-readable description for logging / injection.

        Returns:
            int: Unique task ID.
        """
        task_id = self._next_id()

        def _on_done(t: "asyncio.Task"):
            try:
                result = t.result()
                error = None
            except asyncio.CancelledError:
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
            )

            with self._results_lock:
                self._pending_results[task_id] = bg_result

            logger.info(
                "[bg-task] Tracked task #%d (%s) completed%s",
                task_id,
                tool_name,
                " (error)" if bg_result.error else "",
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
        logger.info(
            "[bg-task] Task #%d tracked (shielded): %s - %s",
            task_id, tool_name, description[:120],
        )
        return task_id

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

    def increment_reactivation(self):
        self._reactivation_count += 1

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
        from Agent.ToolResultBudget import ToolResultBudget
        from configurationLoader import config

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

        if budget_inputs:
            max_single = config.get("tools.result_budget.max_single_tokens", 8000)
            max_batch = config.get("tools.result_budget.max_batch_tokens", 24000)
            cache_dir = config.get(
                "tools.result_budget.cache_dir", "./runtime_memory/tool_cache"
            )
            ToolResultBudget.apply(budget_inputs, cache_dir, max_single, max_batch)

        for i, r in enumerate(results):
            if r.error:
                content = (
                    f"[task-background #{r.task_id} completed with error]\n"
                    f"Tool: {r.tool_name}\n"
                    f"Error: {r.error}\n\n"
                    f"Please note this tool failed in the background. "
                    f"Do NOT retry it unless the user explicitly asks."
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
            memory_manager.append({"role": "user", "content": content})
            logger.info(
                "[bg-task] Injected result for task #%d into working memory",
                r.task_id,
            )
