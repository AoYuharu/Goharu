"""
Unit tests for BackgroundTaskManager.

Tests that the manager:
- Returns task ID immediately on submit
- Correctly reports has_pending() / drain_pending()
- Properly injects results into a mock memory manager
- Thread safety via concurrent submissions
"""

import asyncio
import threading
import time
import json

from Agent.BackgroundTaskManager import BackgroundTaskManager


class MockMemoryManager:
    """Minimal mock that records appended messages."""

    def __init__(self):
        self.messages = []

    def append(self, message):
        self.messages.append(message)

    def get_context(self):
        return list(self.messages)


def test_submit_returns_task_id_immediately():
    """Submit a slow task — task_id should be returned immediately."""
    mgr = BackgroundTaskManager()
    mgr.drain_pending()  # Clean state

    async def _slow():
        await asyncio.sleep(2)
        return "done"

    start = time.time()
    task_id = mgr.submit("test_tool", _slow(), "slow test task")
    elapsed = time.time() - start

    assert isinstance(task_id, int), f"Expected int task_id, got {type(task_id)}"
    assert task_id > 0, f"Expected positive task_id, got {task_id}"
    assert elapsed < 0.5, f"submit() blocked for {elapsed:.2f}s, expected <0.5s"


def test_has_pending_while_running():
    """has_pending() should be False while task runs, True after completion."""
    mgr = BackgroundTaskManager()
    mgr.drain_pending()  # Clean state

    async def _slow():
        await asyncio.sleep(1)
        return "result"

    task_id = mgr.submit("test_tool", _slow(), "test")

    # Should not be pending yet (still running)
    assert not mgr.has_pending(), "has_pending() should be False while task is running"

    # Wait for completion
    time.sleep(1.5)

    # Should be pending now
    assert mgr.has_pending(), "has_pending() should be True after task completes"


def test_drain_pending_returns_results():
    """drain_pending() should return completed results and clear the queue."""
    mgr = BackgroundTaskManager()
    mgr.drain_pending()  # Clean state

    async def _work():
        await asyncio.sleep(0.5)
        return {"output": "hello world"}

    task_id = mgr.submit("test_tool", _work(), "simple task")
    time.sleep(1.0)

    assert mgr.has_pending()

    results = mgr.drain_pending()
    assert len(results) >= 1, f"Expected at least 1 result from this test"
    # Find our specific result
    our_result = [r for r in results if r.task_id == task_id]
    assert len(our_result) == 1, f"Expected to find task {task_id} in results"
    assert our_result[0].tool_name == "test_tool"
    assert our_result[0].error is None

    # Queue should be empty after drain
    assert not mgr.has_pending(), "Queue should be empty after drain_pending()"


def test_inject_into_memory():
    """inject_into_memory should format results as user-role messages."""
    from Agent.BackgroundTaskManager import BackgroundResult

    memory = MockMemoryManager()

    results = [
        BackgroundResult(
            task_id=1,
            tool_name="test_tool",
            result="some output data",
            description="test tool(desc)",
        ),
        BackgroundResult(
            task_id=2,
            tool_name="failed_tool",
            result='{"error": "something broke"}',
            description="failing tool",
            error="something broke",
        ),
    ]

    BackgroundTaskManager.inject_into_memory(memory, results)

    assert len(memory.messages) == 2, f"Expected 2 messages, got {len(memory.messages)}"
    assert memory.messages[0]["role"] == "user"
    assert "[task-background #1 completed]" in memory.messages[0]["content"]
    assert "some output data" in memory.messages[0]["content"]

    assert memory.messages[1]["role"] == "user"
    assert "[task-background #2 completed with error]" in memory.messages[1]["content"]
    assert "something broke" in memory.messages[1]["content"]


def test_concurrent_submissions():
    """Multiple concurrent submissions should all work correctly."""
    mgr = BackgroundTaskManager()
    mgr.drain_pending()  # Clean state

    async def _task(sleep_time):
        await asyncio.sleep(sleep_time)
        return f"done in {sleep_time}s"

    task_ids = []
    for i in range(3):
        tid = mgr.submit(f"tool_{i}", _task(0.5 + i * 0.2), f"task {i}")
        task_ids.append(tid)

    # All IDs should be unique
    assert len(set(task_ids)) == 3, "Task IDs should be unique"

    # Wait for all to complete
    time.sleep(3.0)

    results = mgr.drain_pending()
    result_ids = {r.task_id for r in results}
    assert set(task_ids).issubset(result_ids), \
        f"Expected tasks {task_ids} in results, got {result_ids}"


def test_get_status_summary():
    """get_status_summary should return correct counts."""
    mgr = BackgroundTaskManager()
    mgr.drain_pending()  # Clean state

    async def _quick():
        return "ok"

    tid_a = mgr.submit("tool_a", _quick(), "task a")
    tid_b = mgr.submit("tool_b", _quick(), "task b")

    time.sleep(0.5)

    summary = mgr.get_status_summary()
    assert summary["pending_results"] >= 2
    assert len(summary["task_ids"]) >= 2


def test_error_handling():
    """Tasks that raise exceptions should capture the error."""
    mgr = BackgroundTaskManager()
    mgr.drain_pending()  # Clean state

    async def _failing():
        await asyncio.sleep(0.3)
        raise ValueError("test failure")

    task_id = mgr.submit("failing_tool", _failing(), "will fail")
    time.sleep(1.5)

    assert mgr.has_pending()
    results = mgr.drain_pending()
    # Find our specific error result
    our_result = [r for r in results if r.task_id == task_id]
    assert len(our_result) >= 1, f"Expected to find error task {task_id}"
    r = our_result[0]
    assert r.error is not None, "Error result should have error field"
    assert "ValueError" in str(r.result)


def test_drain_pending_clears():
    """drain_pending() should clear the pending queue."""
    mgr = BackgroundTaskManager()
    mgr.drain_pending()  # Clean state

    async def _quick():
        return "ok"

    mgr.submit("tool", _quick(), "test")
    time.sleep(0.5)

    assert mgr.has_pending()
    mgr.drain_pending()
    assert not mgr.has_pending(), "Queue should be empty after drain"


def test_callback_registration():
    """Callbacks should fire when tasks complete."""
    mgr = BackgroundTaskManager()
    mgr.drain_pending()  # Clean state
    callback_results = []
    callback_lock = threading.Lock()

    def on_complete(task_id, result):
        with callback_lock:
            callback_results.append((task_id, result.tool_name))

    mgr.register_callback(on_complete)

    async def _quick():
        return "ok"

    tid = mgr.submit("callback_tool", _quick(), "test")
    time.sleep(0.5)

    assert len(callback_results) >= 1, f"Callback should have fired, got {callback_results}"

    # Unregister and submit another task
    mgr.unregister_callback(on_complete)
    tid2 = mgr.submit("another_tool", _quick(), "after unregister")
    time.sleep(0.5)

    # Callback should not fire for the second task
    our_callbacks = [(t, n) for t, n in callback_results if t == tid]
    assert len(our_callbacks) == 1, f"Callback for task {tid} should fire exactly once"
    second_callbacks = [(t, n) for t, n in callback_results if t == tid2]
    assert len(second_callbacks) == 0, f"Unregistered callback should not fire for task {tid2}"


if __name__ == "__main__":
    print("=== Running BackgroundTaskManager unit tests ===\n")

    tests = [
        ("submit returns task_id immediately", test_submit_returns_task_id_immediately),
        ("has_pending state management", test_has_pending_while_running),
        ("drain_pending returns results", test_drain_pending_returns_results),
        ("inject_into_memory formatting", test_inject_into_memory),
        ("concurrent submissions", test_concurrent_submissions),
        ("get_status_summary", test_get_status_summary),
        ("error handling", test_error_handling),
        ("drain_pending clears queue", test_drain_pending_clears),
        ("callback registration", test_callback_registration),
    ]

    passed = 0
    failed = 0

    for name, test_fn in tests:
        try:
            test_fn()
            print(f"  PASS: {name}")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {name}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\nResults: {passed} passed, {failed} failed out of {len(tests)} tests")
    if failed > 0:
        exit(1)
