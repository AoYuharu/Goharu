"""
Unit tests for MicroCompactor.

Verifies that:
- Messages without old tool results are returned unchanged
- Old tool results beyond keep limit are compacted into a placeholder
- Non-tool-result messages are always preserved
- All three tool result formats are detected correctly
"""

import time
import copy
from datetime import datetime, timedelta

from Agent.MicroCompactor import MicroCompactor


def _make_msg(role, content, hours_ago=0, **extra):
    """Build a message dict with timestamp at relative hours ago."""
    ts = (datetime.now() - timedelta(hours=hours_ago)).isoformat()
    msg = {"role": role, "content": content, "timestamp": ts, "id": f"msg_{id(content)}"}
    msg.update(extra)
    return msg


# ── Test helpers ──────────────────────────────────────

def _count_tool_results(msgs):
    return sum(1 for m in msgs if MicroCompactor._is_tool_result(m))


def _find_placeholder(msgs):
    for m in msgs:
        c = m.get("content", "")
        if isinstance(c, str) and "[micro-compact]" in c:
            return m
    return None


# ── Tests ─────────────────────────────────────────────

def test_no_old_messages_returns_unchanged():
    """All messages are recent — should return unchanged."""
    msgs = [
        _make_msg("user", "hello", hours_ago=0),
        _make_msg("tool", "result1", hours_ago=0, name="toolA"),
        _make_msg("assistant", "I did it", hours_ago=0),
    ]
    result = MicroCompactor.compact(msgs, age_threshold_hours=1, keep_tool_results=2)
    assert result is msgs, "Should return same list when nothing to compact"


def test_old_tool_results_under_limit():
    """Old tool results within keep limit — no compaction needed."""
    msgs = [
        _make_msg("tool", "old result", hours_ago=2, name="toolA"),
        _make_msg("tool", "another old", hours_ago=3, name="toolB"),
        _make_msg("user", "hello", hours_ago=0),
    ]
    result = MicroCompactor.compact(msgs, age_threshold_hours=1, keep_tool_results=5)
    assert result is msgs, "Should not compact when under keep limit"


def test_old_tool_results_exceed_limit():
    """Old tool results exceed keep limit — should compact."""
    msgs = [
        _make_msg("user", "question 1", hours_ago=3),
        _make_msg("tool", "result A", hours_ago=3, name="toolA"),
        _make_msg("tool", "result B", hours_ago=3, name="toolB"),
        _make_msg("tool", "result C", hours_ago=3, name="toolC"),
        _make_msg("user", "question 2", hours_ago=2),
        _make_msg("tool", "result D", hours_ago=2, name="toolD"),
        _make_msg("tool", "result E", hours_ago=2, name="toolE"),
        _make_msg("tool", "result F", hours_ago=2, name="toolF"),
        _make_msg("assistant", "summary", hours_ago=2),
        _make_msg("user", "recent", hours_ago=0),
    ]
    result = MicroCompactor.compact(msgs, age_threshold_hours=1, keep_tool_results=2)

    # Result should not be same object
    assert result is not msgs, "Should return new list after compaction"

    # Placeholder should exist
    placeholder = _find_placeholder(result)
    assert placeholder is not None, "Should have a placeholder message"

    # Should have kept 2 tool results in the expired portion
    expired_tool_count = _count_tool_results(
        [m for m in result if m.get("timestamp", "") < msgs[-1].get("timestamp")]
    )
    assert expired_tool_count <= 2, f"Should keep <= 2 tool results in expired, got {expired_tool_count}"

    # Recent message should still be there
    recent_msgs = [m for m in result if m.get("content") == "recent"]
    assert len(recent_msgs) == 1

    # Non-tool-result messages should be preserved
    user_msgs = [m for m in result if m.get("content") == "question 1"]
    assert len(user_msgs) == 1, "User questions should be preserved"


def test_non_tool_messages_preserved():
    """User questions and assistant replies are never removed."""
    msgs = [
        _make_msg("user", "old question", hours_ago=3),
        _make_msg("assistant", "old thinking", hours_ago=3),
        _make_msg("tool", "result1", hours_ago=3, name="toolA"),
        _make_msg("tool", "result2", hours_ago=3, name="toolB"),
        _make_msg("tool", "result3", hours_ago=3, name="toolC"),
        _make_msg("tool", "result4", hours_ago=3, name="toolD"),
        _make_msg("assistant", "old reply", hours_ago=3),
    ]
    result = MicroCompactor.compact(msgs, age_threshold_hours=1, keep_tool_results=2)

    # User message preserved
    assert any("old question" in str(m.get("content", "")) for m in result)
    # Assistant thinking preserved
    assert any("old thinking" in str(m.get("content", "")) for m in result)
    # Assistant reply preserved
    assert any("old reply" in str(m.get("content", "")) for m in result)


def test_native_tool_result_format():
    """Anthropic native tool_result blocks should be detected."""
    msgs = [
        _make_msg("user", "hello", hours_ago=3),
        _make_msg("user", [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "data1"},
        ], hours_ago=3),
        _make_msg("user", [
            {"type": "tool_result", "tool_use_id": "toolu_2", "content": "data2"},
        ], hours_ago=3),
        _make_msg("user", [
            {"type": "tool_result", "tool_use_id": "toolu_3", "content": "data3"},
        ], hours_ago=3),
        _make_msg("user", [
            {"type": "tool_result", "tool_use_id": "toolu_4", "content": "data4"},
        ], hours_ago=3),
        _make_msg("user", [
            {"type": "tool_result", "tool_use_id": "toolu_5", "content": "data5"},
        ], hours_ago=3),
        _make_msg("user", [
            {"type": "tool_result", "tool_use_id": "toolu_6", "content": "data6"},
        ], hours_ago=3),
        _make_msg("user", "recent", hours_ago=0),
    ]
    result = MicroCompactor.compact(msgs, age_threshold_hours=1, keep_tool_results=3)

    assert result is not msgs
    placeholder = _find_placeholder(result)
    assert placeholder is not None, "Should compact excess native tool_result blocks"

    # Should have kept exactly 3 tool_result messages in the expired portion
    recent = [m for m in result if m.get("content") == "recent"]
    expired = [m for m in result if m is not recent[0] and m is not placeholder]
    tool_count = _count_tool_results(expired)
    assert tool_count == 3, f"Should keep 3 tool results, got {tool_count}"


def test_background_task_format():
    """Background task completion messages should be detected."""
    msgs = [
        _make_msg("user", "hello", hours_ago=3),
        _make_msg("user", "[task-background #1 completed]\nTool: search\nResult: found it", hours_ago=3),
        _make_msg("user", "[task-background #2 completed]\nTool: analyze\nResult: analyzed", hours_ago=3),
        _make_msg("user", "[task-background #3 completed]\nTool: summarize\nResult: summary", hours_ago=3),
        _make_msg("user", "recent", hours_ago=0),
    ]
    result = MicroCompactor.compact(msgs, age_threshold_hours=1, keep_tool_results=1)

    assert result is not msgs
    placeholder = _find_placeholder(result)
    assert placeholder is not None, "Should compact excess background task results"


def test_empty_messages():
    """Empty message list should return empty."""
    result = MicroCompactor.compact([], age_threshold_hours=1, keep_tool_results=5)
    assert result == []


def test_keep_zero_disables():
    """keep_tool_results=0 should return unchanged (compact disabled)."""
    msgs = [
        _make_msg("tool", "result", hours_ago=3, name="toolA"),
        _make_msg("user", "hello", hours_ago=0),
    ]
    result = MicroCompactor.compact(msgs, age_threshold_hours=1, keep_tool_results=0)
    assert result is msgs


def test_placeholder_structure():
    """Placeholder should contain useful info about removed tools."""
    msgs = [
        _make_msg("tool", "result1", hours_ago=3, name="search"),
        _make_msg("tool", "result2", hours_ago=3, name="search"),
        _make_msg("tool", "result3", hours_ago=3, name="analyze"),
        _make_msg("user", "recent", hours_ago=0),
    ]
    result = MicroCompactor.compact(msgs, age_threshold_hours=1, keep_tool_results=1)

    placeholder = _find_placeholder(result)
    assert placeholder is not None
    content = placeholder["content"]
    assert "[micro-compact]" in content
    assert "search" in content
    assert placeholder["role"] == "user"


if __name__ == "__main__":
    print("=== MicroCompactor unit tests ===\n")

    tests = [
        ("no old messages returns unchanged", test_no_old_messages_returns_unchanged),
        ("old results under limit unchanged", test_old_tool_results_under_limit),
        ("old results exceed limit compacted", test_old_tool_results_exceed_limit),
        ("non-tool messages preserved", test_non_tool_messages_preserved),
        ("native tool_result format detected", test_native_tool_result_format),
        ("background task format detected", test_background_task_format),
        ("empty messages", test_empty_messages),
        ("keep_zero disables compact", test_keep_zero_disables),
        ("placeholder structure", test_placeholder_structure),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
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
