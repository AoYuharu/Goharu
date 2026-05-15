"""
Unit tests for background task reactivation logic.

Covers:
  1. _drain_background_results correctly drains, injects, and emits
  2. Callback queues synthetic msg + triggers _start_processing when idle
  3. Callback does NOT queue when agent is processing
  4. Dedup: two rapid callbacks only queue one reactivation
  5. Callback always emits task.background.status
  6. Flag cleared when bg_reactivation is consumed
"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

# Setup path
project_root = Path(__file__).parent.resolve()
sys.path.insert(0, str(project_root))

import pytest

from Agent.BackgroundTaskManager import BackgroundTaskManager


# ── helpers ──────────────────────────────────────────────────────────

def _reset_bg_task_manager():
    """Reset the BackgroundTaskManager singleton for test isolation."""
    BackgroundTaskManager._instance = None


class FakeMemoryManager:
    """Minimal mock for MemoryManager that records appended messages."""

    def __init__(self):
        self.messages = []

    def append(self, msg):
        self.messages.append(msg)

    def get_context(self):
        return list(self.messages)

    def clear_context(self):
        self.messages.clear()


def _make_on_bg_complete(session, write_json_fn, start_processing_fn):
    """Build a callback closure with explicit function references.

    The closure mimics the real _on_bg_complete registered in
    GatewaySession.initialize(), but accepts write_json_fn and
    start_processing_fn as parameters so tests can inject mocks.
    """
    def _on_bg_complete(task_id, bg_result):
        from Agent.BackgroundTaskManager import BackgroundTaskManager as BTM
        status = BTM().get_status_summary()
        write_json_fn({
            "jsonrpc": "2.0", "method": "event",
            "params": {
                "type": "task.background.status",
                "payload": status,
            }
        })
        if session._processing:
            return
        if session._bg_reactivation_queued:
            return
        session._bg_reactivation_queued = True
        synthetic_msg = (
            "[System: background results arrived after previous answer] "
            "Background task(s) completed. Please review and respond if needed."
        )
        with session._batch_lock:
            session.pending_messages.append(synthetic_msg)
            session.pending_requests.append("bg_reactivation")
        start_processing_fn("default")

    return _on_bg_complete


# ── test _drain_background_results ───────────────────────────────────

def test_drain_background_results_drains_and_injects():
    """_drain_background_results should drain, inject, and emit."""
    _reset_bg_task_manager()
    from TUI.gateway_entry import GatewaySession

    session = GatewaySession()
    session.memory_manager = FakeMemoryManager()
    task_mgr = BackgroundTaskManager()

    from Agent.BackgroundTaskManager import BackgroundResult
    result = BackgroundResult(
        task_id=42,
        tool_name="test_tool",
        result="test output",
        description="test desc",
        completed_at=time.time(),
    )
    with task_mgr._results_lock:
        task_mgr._pending_results[42] = result

    assert task_mgr.has_pending()

    with patch('TUI.gateway_entry.write_json') as mock_write:
        drained = session._drain_background_results()

    assert len(drained) == 1
    assert drained[0].task_id == 42
    assert not task_mgr.has_pending()

    # Verify injected into memory
    assert len(session.memory_manager.messages) == 1
    msg = session.memory_manager.messages[0]
    assert msg["role"] == "user"
    assert "task-background #42 completed" in msg["content"]
    assert "test output" in msg["content"]

    # Verify event emitted
    mock_write.assert_called_once()
    args_dict = mock_write.call_args[0][0]
    assert args_dict["method"] == "event"
    assert args_dict["params"]["type"] == "task.background.completed"
    assert args_dict["params"]["payload"]["count"] == 1
    assert args_dict["params"]["payload"]["task_ids"] == [42]


def test_drain_background_results_empty():
    """_drain_background_results with no pending returns empty list."""
    _reset_bg_task_manager()
    from TUI.gateway_entry import GatewaySession

    session = GatewaySession()
    session.memory_manager = FakeMemoryManager()

    with patch('TUI.gateway_entry.write_json') as mock_write:
        drained = session._drain_background_results()

    assert drained == []
    assert len(session.memory_manager.messages) == 0
    mock_write.assert_not_called()


def test_drain_background_results_with_error():
    """_drain_background_results handles error results correctly."""
    _reset_bg_task_manager()
    from TUI.gateway_entry import GatewaySession
    from Agent.BackgroundTaskManager import BackgroundResult

    session = GatewaySession()
    session.memory_manager = FakeMemoryManager()
    task_mgr = BackgroundTaskManager()

    result = BackgroundResult(
        task_id=99,
        tool_name="failing_tool",
        result='{"error": "something broke"}',
        description="error test",
        error='{"error": "something broke"}',
        completed_at=time.time(),
    )
    with task_mgr._results_lock:
        task_mgr._pending_results[99] = result

    with patch('TUI.gateway_entry.write_json'):
        drained = session._drain_background_results()

    assert len(drained) == 1
    msg = session.memory_manager.messages[0]
    assert "error" in msg["content"].lower()
    assert "#99" in msg["content"]


# ── test callback behavior ──────────────────────────────────────────

def test_callback_when_idle_queues_reactivation():
    """Callback should queue synthetic msg when agent is idle (_processing=False)."""
    _reset_bg_task_manager()
    from TUI.gateway_entry import GatewaySession

    session = GatewaySession()
    session._processing = False
    session._bg_reactivation_queued = False

    captured_start = []
    write_calls = []

    def fake_start_processing(session_id):
        captured_start.append(session_id)

    def fake_write_json(obj):
        write_calls.append(obj)
        return True

    on_complete = _make_on_bg_complete(session, fake_write_json, fake_start_processing)
    on_complete(1, MagicMock())

    # Should have queued a synthetic message
    assert session.has_pending()
    msgs, reqs = session.get_pending_messages()
    assert len(msgs) == 1
    assert "background results" in msgs[0]
    assert "bg_reactivation" in reqs

    # Should have triggered processing
    assert len(captured_start) == 1
    assert captured_start[0] == "default"

    # Flag should be set
    assert session._bg_reactivation_queued is True

    # Status event should have been emitted
    status_events = [c for c in write_calls
                     if c.get("params", {}).get("type") == "task.background.status"]
    assert len(status_events) == 1


def test_callback_when_processing_does_nothing():
    """Callback should NOT queue when agent is processing (_processing=True)."""
    _reset_bg_task_manager()
    from TUI.gateway_entry import GatewaySession

    session = GatewaySession()
    session._processing = True
    session._bg_reactivation_queued = False

    captured_start = []
    write_calls = []

    def fake_start_processing(session_id):
        captured_start.append(session_id)

    def fake_write_json(obj):
        write_calls.append(obj)
        return True

    on_complete = _make_on_bg_complete(session, fake_write_json, fake_start_processing)
    on_complete(1, MagicMock())

    # Should NOT have queued anything
    assert not session.has_pending()
    assert len(captured_start) == 0
    assert session._bg_reactivation_queued is False

    # But status event should still have been emitted
    status_events = [c for c in write_calls
                     if c.get("params", {}).get("type") == "task.background.status"]
    assert len(status_events) == 1


def test_callback_dedup_two_rapid_completions():
    """Two callbacks in quick succession should only queue one reactivation."""
    _reset_bg_task_manager()
    from TUI.gateway_entry import GatewaySession

    session = GatewaySession()
    session._processing = False
    session._bg_reactivation_queued = False

    captured_start = []

    def fake_start_processing(session_id):
        captured_start.append(session_id)

    def fake_write_json(obj):
        return True

    on_complete = _make_on_bg_complete(session, fake_write_json, fake_start_processing)

    # First callback
    on_complete(1, MagicMock())
    # Second callback (should be deduped)
    on_complete(2, MagicMock())

    # Should only have ONE synthetic message queued
    msgs, reqs = session.get_pending_messages()
    assert len(msgs) == 1, f"Expected 1 message but got {len(msgs)}"
    assert len(reqs) == 1

    # Should only have called _start_processing once
    assert len(captured_start) == 1

    # Flag should be set
    assert session._bg_reactivation_queued is True


def test_callback_emits_status_even_when_processing():
    """Callback should always emit task.background.status regardless of processing state."""
    _reset_bg_task_manager()
    from TUI.gateway_entry import GatewaySession

    session = GatewaySession()
    session._processing = True  # Even when processing, status should emit
    session._bg_reactivation_queued = False

    write_calls = []

    def fake_write_json(obj):
        write_calls.append(obj)
        return True

    def fake_start_processing(session_id):
        pass

    on_complete = _make_on_bg_complete(session, fake_write_json, fake_start_processing)
    on_complete(1, MagicMock())

    # Should have emitted status event
    status_events = [c for c in write_calls
                     if c.get("params", {}).get("type") == "task.background.status"]
    assert len(status_events) == 1
    assert "pending_results" in status_events[0]["params"]["payload"]


def test_flag_cleared_on_bg_reactivation_consumption():
    """When bg_reactivation is consumed, flag should clear."""
    _reset_bg_task_manager()
    from TUI.gateway_entry import GatewaySession

    session = GatewaySession()
    session._bg_reactivation_queued = True

    with session._batch_lock:
        session.pending_messages.append("synthetic bg msg")
        session.pending_requests.append("bg_reactivation")

    pending_msgs, pending_reqs = session.get_pending_messages()
    if "bg_reactivation" in pending_reqs:
        session._bg_reactivation_queued = False

    assert session._bg_reactivation_queued is False


def test_callback_does_not_queue_for_non_bg_result():
    """Callback handles calls correctly (just emits status, skipping queue when processing)."""
    _reset_bg_task_manager()
    from TUI.gateway_entry import GatewaySession

    session = GatewaySession()
    session._processing = True
    session._bg_reactivation_queued = False

    write_calls = []

    def fake_write_json(obj):
        write_calls.append(obj)
        return True

    def fake_start_processing(session_id):
        pass

    on_complete = _make_on_bg_complete(session, fake_write_json, fake_start_processing)

    # Call with a mock BackgroundResult
    mock_result = MagicMock()
    mock_result.task_id = 10
    mock_result.tool_name = "pdf_parser"
    on_complete(10, mock_result)

    # Should emit status
    assert len(write_calls) == 1
    assert write_calls[0]["params"]["type"] == "task.background.status"
    # Should NOT queue (processing=True)
    assert not session.has_pending()


# ── run ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
