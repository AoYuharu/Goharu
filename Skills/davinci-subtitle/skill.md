---
name: davinci-subtitle
description: >-
  在 DaVinci Resolve 里给时间线的口播/环境音轨生成并写入中文字幕（本地 whisper 转录 → SRT → 字幕轨），
  全程不改动视频/音频剪辑。当用户说"给这条时间线加字幕""提取这个项目的声音轨字幕"
  "把口播转成字幕入轨""环境音轨字幕""生成 SRT 并导入 Resolve""自动打轴"时使用。
---

# DaVinci 字幕生成与入轨

目标：把时间线某条音频轨的人声转成对齐的简体中文字幕，写入字幕轨，且**一条剪辑都不许动**。

## 前置条件
- Resolve 已打开目标项目与时间线；脚本走 `mcp__davinci_resolve__run_script`（纯 Resolve 操作）或 `run_script_unsafe`（需要写文件时）。
- 本地 whisper 模型目录（例：`E:\Davinci\_asr\models\whisper-large-v3`，约 3GB，含 `model.safetensors`）。缺模型见「失败处理」下载。
- Python 侧需要 `torch`、`transformers>=4.4x`、`imageio_ffmpeg`；繁转简需要 `zhconv`（缺失可跳过，见失败处理）。
- 脚本参考实现：`Skills/davinci-subtitle/scripts/`（`collect_clips.py`、`extract_audio.py`、`transcribe_hf.py`、`clean_srt.py`、`post_fix_srt.py`、`add_subtitles.py`、`shift_srt.py`）。

## 工具
- `mcp__davinci_resolve__run_script` — 只做内联的 Resolve 读写（沙箱里 **`exec` 不可用**）
- `mcp__davinci_resolve__run_script_unsafe` — 用 `exec(open(脚本).read())` 跑 scripts/ 下的脚本、或需要写 json 文件时用；脚本内不要出现中文（否则 `UnicodeEncodeError: surrogates`）
- `run_cmd` — 跑 python 脚本、ffmpeg、pip

## 流程

1. **先备份时间线**：`proj.GetCurrentTimeline().DuplicateTimeline(名字+"_backup_YYYYMMDD_HHMM")`。注意：备份后当前时间线会切到副本，**后续操作前必须 `proj.SetCurrentTimeline(proj.GetTimelineByIndex(1))` 切回原时间线**（`SetCurrentTimeline` 返回 bool，要用 `GetCurrentTimeline()` 重新取对象）。
2. **重新采集轨道数据**（禁止复用旧 clips.json）：
   `tl.GetItemListInTrack("audio", N)`，每项取 `GetStart() GetDuration() GetSourceStartFrame() GetSourceEndFrame()`，连 `fps=GetSetting("timelineFrameRate")`、`tl_start=GetStartFrame()` 一起写成 json。
   **核对段数与用户认知**：段数/时长与历史记录不符就说明时间线被改过，必须以当前为准。
3. **提取音频**：ffmpeg `-ss src_start/fps -t dur/fps -vn -ac 1 -ar 16000 -c:a pcm_s16le`，输出 `clipNN.wav`；逐段比对 wav 时长与 `dur/fps`（容差 0.15s），不符就报错。
4. **转录**：`transcribe_hf.py --model <模型目录> --clips clips.json --audio <wav目录> --out x.srt`。
   关键点：`model.generate(inputs, language="zh", task="transcribe", return_timestamps=True)`；
   解码用 `proc.decode(ids[0], output_offsets=True)`，**新版 transformers 返回 `offsets`，不是 `chunks`**，两种都兼容。
   时间轴换算：`offset = (clip["start"] - tl_start) / fps`，片段内时间戳 clamp 到 `dur/fps`。
5. **后处理**：`zhconv.convert(text, "zh-cn")` 繁转简；过滤幻觉（黑名单「明镜/点点栏目/请不吝点赞/订阅/转发/打赏」、单字符连续重复 ≥12、文本 >50 字、短语重复）。
6. **入轨**：
   1) `mp.ImportMedia([srt路径])` → 类型为「字幕」的 MediaPoolItem（会落在**当前文件夹**，取 clip 时遍历子文件夹找名字）。
   2) `tl.SetCurrentTimecode(tl.GetStartTimecode())` — **必须先把播放头移到时间线起点**，否则字幕会被追加到时间线末尾。
   3) `tl.AddTrack("subtitle")`（已有则跳过）。
   4) `mp.AppendToTimeline([{"mediaPoolItem": item}])` — 一条返回，实际展开成全部字幕条目。
   5) 若已入错位置：`tl.DeleteTrack("subtitle", 1)` 再 `AddTrack("subtitle")` 重来。

6A. **增量追加（只给新片段加字幕，已有字幕一条都不能动）**：
   - 先快照现有字幕：`[(GetStart(), GetDuration(), GetName()) ...]` 存 json（含文本），用于事后逐条比对。
   - `AppendToTimeline` 对字幕是**追加语义**：`新条目起点 = 现有字幕轨最后一条的 end + SRT 时间码`（即使播放头在起点也如此）。
     实测：现有末尾 770.5s、SRT 首条 776.51s → 落在 1547.01s，并把时间线 end 拉长 762s。
   - 正确做法：`existing_end = max(start+dur)`（相对时间线起点），用 `shift_srt.py in.srt out.srt --shift <existing_end>` 把 SRT 时间码整体减去它，
     再 `ImportMedia` + 播放头到起点 + `AppendToTimeline` → 新条目正好落在 `existing_end + 剩余偏移` 的目标位置。
   - 误追加后的撤销：`tl.DeleteClips([多余条目], False)`（不要删整轨，会丢用户已调好的字幕）；再确认 `GetEndFrame()` 回到原值。
   - 验证：总数 = 旧数 + 新数；`cur[:len(old)] == old` 逐条相等；新条目区间落在新片段 `[start, start+dur)` 内；时间线 end 不变。
7. **验证**（必做）：
   - 字幕条目数 = SRT 条数；首条 `GetStart()` = `tl_start`；末条结束 ≈ 最后一条 SRT 时间。
   - 每条字幕区间落在某个 clip 的 `[start, start+dur)` 内（容差 0.05s）。
   - `GetItemListInTrack("video",1)` / `("audio",1..n)` 条目数与操作前一致。
   - `GetIsTrackEnabled("subtitle", 1)` 为真。

## 判定标准 / 红线
- 绝不改动视频轨、音频轨条目（不 AppendToTimeline 视频、不 DeleteTrack 非字幕轨）。
- 不复用旧时间轴数据：每次从当前时间线重新采集，段数不符先停下核对。
- 不猜时间码、不手拼偏移；时间轴只由 `GetStart()/GetStartFrame()` 推出。
- 字幕文本有错字（专名如"黄兴广场""衡山"）属识别局限，**如实告知用户需人工校对**，不要谎称准确。
- 幻觉条目宁可丢弃，也不写进时间线。
- 删除时间线/媒体池条目等破坏性操作，先问用户。

## 失败处理
| 现象 | 原因 | 处理 |
|---|---|---|
| `CreateSubtitlesFromAudio` 返回 Complete 但 0 条 | Resolve 自动转录对该素材失效 | 改用本地 whisper（本技能流程） |
| `proc.decode(..., output_offsets=True)` 的 `chunks` 为 None | transformers 新版改名 | 读 `decoded["offsets"]` |
| 转录结果 0 条但音频有电平 | 静音段/幻觉被过滤，或键名不匹配 | 打印 `decoded["text"]` 确认键名 |
| 字幕全部跑到时间线末尾 | 播放头不在起点，或增量追加未做 shift | 整轨重建用 `SetCurrentTimecode`；增量追加用 `shift_srt.py` 减去现有末尾秒数 |
| 追加后时间线被拉长（end 变大） | 字幕落到了末尾 | `DeleteClips([多余条目], False)` 撤销，再按 6A 重做 |
| 删条目后 `GetName()` 返回 None | item 已失效 | 删除前先记录文本，删除后重新取列表 |
| 字幕条目翻倍（如 370=185×2） | 重复 AppendToTimeline | `DeleteTrack("subtitle",1)` 清空后重做一次 |
| 轨道段数与旧 json 不一致 | 时间线被编辑过 | 重新采集 clips.json 并重提音频 |
| pip 清华源 Read timeout / ProxyError | 网络/代理 | `curl -sSL -k --ssl-no-revoke` 下载 tar.gz，再 `pip install . --no-index --no-build-isolation` |
| 模型下载中断 | modelscope SDK 超时 | `curl -C -` 断点续传单文件 |
| 脚本报 `UnicodeEncodeError: surrogates` | run_script 里含中文注释/字符串 | 脚本内只用英文 |
| `NameError: name 'exec' is not defined` | `run_script` 沙箱禁用 exec | 改用 `run_script_unsafe` |

## 自检清单
- [ ] 字幕条数 = SRT 条数，首条起点 = 时间线起点
- [ ] 每条字幕落在片段区间内，末条对齐音频结尾
- [ ] 视频/音频轨条目数未变
- [ ] 无超长/重复幻觉条目
- [ ] 已告知用户专名错字需校对
