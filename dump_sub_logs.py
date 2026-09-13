import json
import glob
import os

files = sorted(glob.glob("runtime_memory/logs/agents/sub/*.jsonl"),
               key=os.path.getmtime)[-3:]
for path in files:
    print("=" * 70)
    print(os.path.basename(path))
    for line in open(path, encoding="utf8"):
        line = line.strip()
        if not line:
            continue
        j = json.loads(line)
        ev = j.get("event", "?")
        if ev == "start":
            print(f"  TASK: {str(j.get('task'))[:150]}")
        elif ev == "iteration":
            tools = j.get("tool_names", [])
            txt = (j.get("text_preview") or j.get("thinking_preview") or "").replace("\n", " ")
            print(f"  iter {j.get('iteration')}: tools={j.get('tool_names')} | {txt[:70]}")
        elif ev == "end":
            print(f"  END: {j.get('status')} iters={j.get('iterations')} tools={j.get('tool_calls_count')}")
        elif ev == "end":
            pass
        else:
            print(f"  {ev}: {str(j)[:200]}")
