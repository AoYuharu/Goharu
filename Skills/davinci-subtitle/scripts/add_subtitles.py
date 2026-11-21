"""把 SRT 写入当前时间线的字幕轨（在 mcp__davinci_resolve__run_script 里执行）。

用法：
    SRT_PATH = r"E:\\Davinci\\_asr\\srt\\out.srt"
    exec(open(r"E:\\Goharu\\Skills\\davinci-subtitle\\scripts\\add_subtitles.py", encoding="utf-8").read())

要点：必须先 SetCurrentTimecode 到时间线起点，否则 AppendToTimeline 会把字幕追加到末尾。
脚本内不要出现中文，run_script 会因 surrogates 报 UnicodeEncodeError。
"""
SRT_PATH = globals().get("SRT_PATH", r"E:\Davinci\_asr\srt\out.srt")
CLEAN_EXISTING = globals().get("CLEAN_EXISTING", True)

pm = resolve.GetProjectManager()
proj = pm.GetCurrentProject()
tl = proj.GetCurrentTimeline()
mp = proj.GetMediaPool()

if CLEAN_EXISTING and tl.GetTrackCount("subtitle") > 0:
    tl.DeleteTrack("subtitle", 1)
while tl.GetTrackCount("subtitle") < 1:
    tl.AddTrack("subtitle")

imported = mp.ImportMedia([SRT_PATH]) or []
target = imported[0] if imported else None
if target is None:
    root = mp.GetRootFolder()
    for f in (root.GetSubFolderList() or []):
        for c in (f.GetClipList() or []):
            if c.GetClipProperty("Type") == "subtitle":
                target = c

tl.SetCurrentTimecode(tl.GetStartTimecode())
res = mp.AppendToTimeline([{"mediaPoolItem": target}]) if target else []

items = tl.GetItemListInTrack("subtitle", 1) or []
rows = sorted([(it.GetStart(), it.GetDuration(), it.GetName()) for it in items])
fps = float(tl.GetSetting("timelineFrameRate"))
tl0 = tl.GetStartFrame()
result = {"timeline": tl.GetName(), "returned": len(res or []), "count": len(rows),
          "first_rel_s": round((rows[0][0] - tl0) / fps, 3) if rows else None,
          "last_rel_s": round((rows[-1][0] + rows[-1][1] - tl0) / fps, 2) if rows else None,
          "max_text_len": max((len(r[2]) for r in rows), default=0),
          "enabled": tl.GetIsTrackEnabled("subtitle", 1),
          "video_track1": len(tl.GetItemListInTrack("video", 1) or []),
          "audio_tracks": [len(tl.GetItemListInTrack("audio", i) or [])
                           for i in range(1, tl.GetTrackCount("audio") + 1)]}
