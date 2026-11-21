"""AskUser 工具离线单测（直接 python tests/test_ask_user.py 运行）。

覆盖三条路径：
  - answered：另一线程模拟 TUI，收到 notifier 回调后 resolve，断言返回答案
  - timeout：timeout 设 1 秒无人应答，断言返回 timeout
  - unavailable：notifier 置 None，断言返回 unavailable

注意 question_manager 是进程级单例，各用例间恢复 notifier 状态。
"""

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Tools.builtin.ask_user_tools import ask_user  # noqa: E402
from Tools.question_manager import question_manager  # noqa: E402

OPTIONS = [{"label": "方案 A", "description": "快但糙"}, {"label": "方案 B"}]


def _reset():
    question_manager.set_notifier(None)


def test_answered():
    _reset()
    captured = {}

    def fake_tui_notifier(request_id, question, options):
        captured["request_id"] = request_id
        captured["question"] = question
        captured["options"] = options
        # 模拟用户思考 0.1s 后选择
        threading.Timer(0.1, lambda: question_manager.resolve(request_id, "方案 A")).start()

    question_manager.set_notifier(fake_tui_notifier)
    try:
        result = json.loads(ask_user("选哪个方案？", OPTIONS))
        assert result["status"] == "answered", result
        assert result["answer"] == "方案 A", result
        assert captured["question"] == "选哪个方案？"
        assert captured["options"] == OPTIONS
        assert len(captured["request_id"]) == 12
    finally:
        _reset()
    print("PASS test_answered")


def test_timeout():
    _reset()
    # notifier 存在但永不 resolve；用 manager.request 直接控制 1s 超时
    question_manager.set_notifier(lambda rid, q, o: None)
    try:
        start = time.monotonic()
        answer = question_manager.request("超时测试", OPTIONS, timeout=1)
        elapsed = time.monotonic() - start
        assert answer is None
        assert 0.9 < elapsed < 3.0, elapsed
        # resolve 迟到的请求 id 应返回 False（已弹出）
    finally:
        _reset()
    print("PASS test_timeout")


def test_unavailable():
    _reset()
    result = json.loads(ask_user("没人接的提问", OPTIONS))
    assert result["status"] == "unavailable", result
    assert "正文" in result["message"]
    print("PASS test_unavailable")


def test_empty_options():
    _reset()
    result = json.loads(ask_user("空选项", []))
    assert result["status"] == "error", result
    print("PASS test_empty_options")


def test_handler_timeout_via_config():
    """handler 走配置超时路径：注入配置把 timeout 设为 1 秒。"""
    from Tools.config_proxy import reset_config, set_config

    _reset()
    question_manager.set_notifier(lambda rid, q, o: None)  # 永不应答
    set_config({"tools": {"ask_user": {"timeout_seconds": 1}}})
    try:
        start = time.monotonic()
        result = json.loads(ask_user("配置超时测试", OPTIONS))
        elapsed = time.monotonic() - start
        assert result["status"] == "timeout", result
        assert "1 秒" in result["message"], result
        assert elapsed < 3.0, elapsed
    finally:
        reset_config()
        _reset()
    print("PASS test_handler_timeout_via_config")


if __name__ == "__main__":
    test_answered()
    test_timeout()
    test_unavailable()
    test_empty_options()
    test_handler_timeout_via_config()
    print("\nALL TESTS PASSED")
