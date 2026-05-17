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
            with self._active_lock:
                self._active_metadata.pop(task_id, None)

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
            memory_manager.append(CoreMessage.context_only(
                MessageSource.BG_TASK,
                content,
            ).to_dict())
            logger.info(
                "[bg-task] Injected result for task #%d into working memory",
                r.task_id,
            )
