"""TelemetryDigest —— Gateway 事件流的哑聚合器。

纯计数器 + 滚动窗口：零 LLM、零 IO、线程安全（send_event 在 session turn
线程上同步调用，必须快速返回）。Architect 幕后助手的信息源——每次唤醒读
digest 快照，而不是现场翻日志。

事件经 Architect/channel.py（TelemetryChannel，channel 插件）搭 broadcast
便车进入，Gateway 本体零改动。
"""

import threading
import time
from collections import deque


class TelemetryDigest:
    """进程级遥测聚合器。"""

    def __init__(self, window: int = 200):
        self._lock = threading.Lock()
        self._window = max(10, int(window or 200))
        self._started_at = time.time()
        self._events = deque(maxlen=self._window)       # (ts, event_type)
        self._cache_ratios = deque(maxlen=50)
        self._counters = {
            "user_messages": 0, "answers": 0, "turn_errors": 0,
            "llm_retries": 0, "steps": 0, "tool_calls": 0, "tool_errors": 0,
            "micro_compacts": 0, "auto_compacts": 0, "interrupts": 0,
        }
        self._consecutive_turn_errors = 0
        self._tool_calls_by_name = {}
        self._tool_errors_by_name = {}
        self._last_by_type = {}       # event_type -> 最后一次发生的 ts
        self._last_token_stats = {}

    # ------------------------------------------------------------------
    # 事件入口（channel 插件调用；不得抛异常、不得阻塞）
    # ------------------------------------------------------------------

    def ingest(self, event_type: str, payload: dict | None = None):
        payload = payload or {}
        now = time.time()
        try:
            with self._lock:
                self._events.append((now, event_type))
                self._last_by_type[event_type] = now

                if event_type == "user.message":
                    self._counters["user_messages"] += 1
                elif event_type == "agent.step":
                    self._counters["steps"] += 1
                elif event_type == "agent.retry":
                    self._counters["llm_retries"] += 1
                elif event_type == "agent.answer":
                    self._counters["answers"] += 1
                    self._consecutive_turn_errors = 0
                elif event_type == "agent.error":
                    self._counters["turn_errors"] += 1
                    self._consecutive_turn_errors += 1
                elif event_type == "agent.interrupted":
                    self._counters["interrupts"] += 1
                elif event_type == "context.micro_compact":
                    self._counters["micro_compacts"] += 1
                elif event_type == "context.auto_compact":
                    self._counters["auto_compacts"] += 1
                elif event_type == "tool.call":
                    self._counters["tool_calls"] += 1
                    name = str(payload.get("tool", "?"))
                    self._tool_calls_by_name[name] = \
                        self._tool_calls_by_name.get(name, 0) + 1
                elif event_type == "tool.result":
                    if payload.get("is_error"):
                        self._counters["tool_errors"] += 1
                        name = str(payload.get("tool", "?"))
                        self._tool_errors_by_name[name] = \
                            self._tool_errors_by_name.get(name, 0) + 1
                elif event_type == "token.stats":
                    self._last_token_stats = dict(payload)
                    ratio = payload.get("prompt_cache_ratio")
                    if isinstance(ratio, (int, float)) and ratio > 0:
                        self._cache_ratios.append(float(ratio))
        except Exception:
            pass   # digest 是旁路观测，永远不影响主流程

    # ------------------------------------------------------------------
    # 快照与渲染
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            ratios = list(self._cache_ratios)
            return {
                "uptime_seconds": int(time.time() - self._started_at),
                "counters": dict(self._counters),
                "consecutive_turn_errors": self._consecutive_turn_errors,
                "tool_calls_by_name": dict(self._tool_calls_by_name),
                "tool_errors_by_name": dict(self._tool_errors_by_name),
                "cache_ratio_recent": round(sum(ratios) / len(ratios), 4) if ratios else None,
                "last_token_stats": dict(self._last_token_stats),
                "last_event_ts": self._events[-1][0] if self._events else None,
                "last_by_type": dict(self._last_by_type),
                "event_window": len(self._events),
            }

    def render_text(self, max_tools: int = 8) -> str:
        """渲染成紧凑文本，供 Architect 任务播种时注入。"""
        snap = self.snapshot()
        c = snap["counters"]
        lines = [
            f"## 运行遥测（uptime {snap['uptime_seconds']}s）",
            f"- 用户消息 {c['user_messages']} / 回答 {c['answers']} / "
            f"轮错误 {c['turn_errors']}（连续 {snap['consecutive_turn_errors']}）",
            f"- LLM 重试 {c['llm_retries']} / step {c['steps']} / "
            f"中断 {c['interrupts']}",
            f"- compact: micro {c['micro_compacts']} / auto {c['auto_compacts']}",
            f"- 工具调用 {c['tool_calls']}（错误 {c['tool_errors']}）",
        ]
        if snap["cache_ratio_recent"] is not None:
            lines.append(f"- 近期缓存命中率 {snap['cache_ratio_recent']:.1%}")
        ts = snap.get("last_token_stats") or {}
        if ts.get("current_tokens"):
            lines.append(f"- 最近上下文规模 ~{ts['current_tokens']} tokens")
        if snap["tool_calls_by_name"]:
            top = sorted(snap["tool_calls_by_name"].items(),
                         key=lambda kv: -kv[1])[:max_tools]
            lines.append("- 高频工具: " + ", ".join(f"{k}×{v}" for k, v in top))
        if snap["tool_errors_by_name"]:
            top = sorted(snap["tool_errors_by_name"].items(),
                         key=lambda kv: -kv[1])[:max_tools]
            lines.append("- 报错工具: " + ", ".join(f"{k}×{v}" for k, v in top))
        return "\n".join(lines)
