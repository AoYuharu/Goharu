#!/usr/bin/env python3
"""Analyze gateway_client_ts.log for requesting hangs.

A 'requesting hang' is defined as: agent.llm_requesting event is emitted,
but no progress event (API_RESPONSE, tool.call, agent.answer,
agent.thinking_content, agent.text_content) is received for a long time,
or the gateway restarts before any progress.
"""
import re
from datetime import datetime
from pathlib import Path

log_path = Path("E:/Goharu/runtime_memory/logs/tui/gateway_client_ts.log")
lines = log_path.read_text(encoding="utf-8").splitlines()

# Parse lines: TUI timestamp + event type
pattern = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[DEBUG\] (Event: .+|stderr: .+)$")

PROGRESS_EVENTS = {
    "API_RESPONSE", "tool.call", "agent.answer",
    "agent.thinking_content", "agent.text_content"
}

events = []
for i, line in enumerate(lines):
    m = pattern.match(line)
    if not m:
        continue
    ts_str, content = m.groups()
    ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
    if content.startswith("Event: "):
        ev_type = content[7:]
        events.append((ts, i, ev_type, line))
    elif "Agent.LLMCore: API request:" in content:
        events.append((ts, i, "API_REQUEST", line))
    elif "Agent.LLMCore: API response:" in content:
        events.append((ts, i, "API_RESPONSE", line))
    elif "httpx: HTTP Request: POST" in content:
        events.append((ts, i, "HTTP_POST", line))
    elif "Initializing gateway session" in content:
        events.append((ts, i, "GATEWAY_INIT", line))

# Find llm_requesting events and measure time to next progress event or restart
hangs = []
for idx, (ts, line_no, ev_type, line) in enumerate(events):
    if ev_type != "agent.llm_requesting":
        continue
    next_ts = None
    next_type = None
    next_line = None
    for j in range(idx + 1, len(events)):
        _, _, et, l = events[j]
        if et in PROGRESS_EVENTS or et == "GATEWAY_INIT":
            next_ts, next_type, next_line = events[j][0], et, l
            break
    if next_ts is None:
        continue
    delta = (next_ts - ts).total_seconds()
    if next_type == "GATEWAY_INIT" or delta > 10:
        hangs.append({
            "ts": ts,
            "line": line_no + 1,
            "delta": delta,
            "next": next_type,
            "next_line": next_line,
            "context": line,
        })

print(f"Total agent.llm_requesting events: {sum(1 for e in events if e[2] == 'agent.llm_requesting')}")
print(f"Potential hangs (>10s or gateway restart): {len(hangs)}")
print()
for h in hangs[-30:]:
    status = "RESTART" if h["next"] == "GATEWAY_INIT" else f"{h['delta']:.1f}s"
    print(f"{h['ts']}  line {h['line']:>6}  -> {status:>10}  next={h['next']}")
