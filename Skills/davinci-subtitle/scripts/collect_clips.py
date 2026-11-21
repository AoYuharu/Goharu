"""从当前 Resolve 时间线采集一条音频轨的片段信息，写成 clips.json。

用法（在 mcp__davinci_resolve__run_script_unsafe 里执行）：
    TRACK_TYPE = "audio"; TRACK_INDEX = 1
    OUT = r"E:\\Davinci\\_asr\\clips.json"
    exec(open(r"E:\\Goharu\\Skills\\davinci-subtitle\\scripts\\collect_clips.py", encoding="utf-8").read())

采集后必须核对：段数与时长是否与用户认知一致；不一致说明时间线被改过。
"""
import json
from pathlib import Path

TRACK_TYPE = globals().get("TRACK_TYPE", "audio")
TRACK_INDEX = globals().get("TRACK_INDEX", 1)
OUT = globals().get("OUT", r"E:\Davinci\_asr\clips.json")

tl = project.GetCurrentTimeline()
fps = float(tl.GetSetting("timelineFrameRate"))
tl_start = tl.GetStartFrame()

clips = []
for it in (tl.GetItemListInTrack(TRACK_TYPE, TRACK_INDEX) or []):
    mpi = it.GetMediaPoolItem()
    clips.append({
        "name": it.GetName(),
        "path": mpi.GetClipProperty("File Path") if mpi else "",
        "start": it.GetStart(),
        "dur": it.GetDuration(),
        "src_start": it.GetSourceStartFrame(),
        "src_end": it.GetSourceEndFrame(),
    })

out = Path(OUT)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({"fps": fps, "tl_start": tl_start, "clips": clips},
                          ensure_ascii=False, indent=1), encoding="utf-8")
result = {"timeline": tl.GetName(), "fps": fps, "tl_start": tl_start,
          "count": len(clips), "file": str(out),
          "total_sec": round(sum(c["dur"] for c in clips) / fps, 1)}
