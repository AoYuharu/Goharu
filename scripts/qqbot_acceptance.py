"""QQBot channel 端到端验收脚本（无需 LLM）。

用回显 Actor 替代真实 LLM（本机 API key 失效期间），验证 channel 全双工：
用户在 QQ 发消息 → 经 WS 入站 → session 队列 → "agent" 回答 → REST 出站回 QQ。

流程：
1. 启动（真实 Memory bridge + SessionMemoryView + AgentSession + QQBotChannel）
2. 监听 QQ 消息；收到后回显 "[Goharu 通道自检] 收到：xxx"
3. 用户回复后再回显一次 → 全双工验证完成

运行：python scripts/qqbot_acceptance.py
停止：Ctrl+C
"""

import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)

from Gateway.channels.qqbot_channel import QQBotChannel
from Gateway.session import AgentSession
from Gateway.session_memory import SessionMemoryView
from Memory.bridge import create_memory
from Prompting.api import create_context_manager


class EchoActor:
    """回显 Actor：act() 直接返回答案（替代 LLM 路径的 ActorAgent）。"""

    def __init__(self, memory_view):
        self._memory = memory_view

    async def act(self, **kwargs):
        ctx = self._memory.get_context()
        last = ""
        for msg in reversed(ctx):
            if msg.get("role") == "user":
                last = str(msg.get("content", ""))[:80]
                break
        return {"type": "answer",
                "answer": f"[Goharu 通道自检] 收到你的消息：{last}"}

    def reset_retrieval_cache(self):
        pass

    def get_last_usage(self):
        return {}

    def build_messages_with_document(self, extra=None):
        return {"messages": []}

    def prepare_messages_for_provider(self, messages):
        return None

    def query(self, messages, **kw):
        return "自检摘要"


class FakeBgTasks:
    reactivation_count = 0

    def drain_pending(self):
        return []

    def has_pending(self):
        return False

    def inject_into_memory(self, memory, results):
        pass


def main():
    tmp = Path(tempfile.mkdtemp(prefix="qqbot_acceptance_"))
    bridge = create_memory(overrides={
        "memory.root_dir": str(tmp / "mem"),
        "memory.retrieval.embedding_model_path": str(tmp / "empty_model"),
        "memory.retrieval.use_reranker": False,
    })
    view = SessionMemoryView("qq_acceptance", bridge, max_messages=10)
    session = AgentSession(
        "qq_acceptance", view, EchoActor(view), pipeline_manager=None,
        bg_tasks=FakeBgTasks(), ctx_manager=create_context_manager(),
    )

    channel = QQBotChannel("qq_main", {"require_mention": True})
    session.attach_channel(channel)
    channel.bind(session.enqueue)
    channel.start()

    print("=" * 60, file=sys.stderr)
    print("QQBot 通道自检已启动。请在 QQ 里给机器人发一条消息。", file=sys.stderr)
    print("预期：机器人回显 [Goharu 通道自检]；你再回复一条，再次回显。", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n停止中…", file=sys.stderr)
    finally:
        channel.stop()
        bridge.close()


if __name__ == "__main__":
    main()
