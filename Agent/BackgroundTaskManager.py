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

logger = logging.getLogger(__name__)


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

    def submit_subagent(self, agent_type: str, task_desc: str,
                        agent_id: str, tools_registry, shared_state) -> int:
        """Submit a sub-agent as a background task.

        Same as submit() but wraps the sub-agent execution in a coroutine.
        """
        from Tools.builtin.agent_delegate import _execute_subagent_task

        async def _bg_subagent():
            return await asyncio.get_event_loop().run_in_executor(
                None,
                _execute_subagent_task,
                agent_type,
                task_desc,
                agent_id,
                tools_registry,
                None,  # output_callback — not available in background
                shared_state,
            )

        return self.submit(
            f"AgentDelegate[{agent_type}]",
            _bg_subagent(),
            f"{agent_type} agent [{agent_id}]: {task_desc[:200]}",
        )

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
        """
        for r in results:
            if r.error:
                content = (
                    f"[task-background #{r.task_id} completed with error]\n"
                    f"Tool: {r.tool_name}\n"
                    f"Error: {r.error}\n\n"
                    f"Please note this tool failed in the background. "
                    f"Do NOT retry it unless the user explicitly asks."
                )
            else:
                result_str = str(r.result)
                if len(result_str) > 5000:
                    result_str = result_str[:5000] + "\n... [truncated]"
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
