---
name: netease-download
description: >-
  辅助用户使用网易云音乐：搜索歌曲、查看自己或他人的歌单与曲目、下载 MP3
  或无损 FLAC（MCP 工具 music_search / music_my_playlists / music_playlist_songs /
  music_download / music_cookie_status，以及内置工具 music_download_lossless）。
  当用户说"帮我下载一首歌"
  "把某某的歌下到桌面""看看我的歌单""这个歌单里有什么歌""网易云下歌"
  "要无损音质""不要降码率"
  时使用。含原唱/伴奏/翻唱甄别、VIP 权限与 Cookie 失效处理、歌单分页查看、
  保存路径与结果汇报。
---

# 网易云音乐下载（NetEase Download）

目标：准确找到用户想要的那一首歌，下载到指定位置并确认文件可用。

## 工具（MCP 前缀 `mcp__netease_music__`，注意是下划线）

可用 `/tools` 面板查看全部工具。实际工具名：

| 工具 | 用途 |
|---|---|
| `music_cookie_status` | 登录账号与 VIP 状态 |
| `music_search` | 搜索歌曲（歌名+歌手 → 歌曲 ID） |
| `music_my_playlists` | 列出账号的歌单（名称/ID/歌曲数）；传 uid 可看他人公开歌单 |
| `music_playlist_songs` | 分页查看歌单曲目（返回可下载的歌曲 ID） |
| `music_download` | 按 ID 下载并写标签（最高 320 kbps MP3） |
| `music_download_lossless` | **内置工具**：按 ID 下载无损 FLAC（含 Hi-Res），自动写标签 |

- `music_search(query, limit)` — 关键词搜索，返回歌曲 ID、歌名、歌手、专辑、收费类型
- `music_download(song_id, save_dir, br)` — 按 ID 下载 MP3，自动写 ID3（歌名/歌手/专辑）
- `music_download_lossless(song_id, save_dir="", allow_fallback=True, cookie_file="")`
  — 内置工具（非 MCP），走 br=999000 音源取 FLAC；无无损源时回退 320 kbps MP3
- `music_cookie_status()` — 查看当前登录账号与 VIP 状态

## 无损下载（用户要求"无损 / 原版音质 / 不要降码率"时）

- **优先用内置工具 `music_download_lossless`**，不要用 MCP 的 `music_download`
  （MCP 固定存 `.mp3` 并用 `MP3()` 解析，传 br=999000 拿到 flac 链接也会解析失败）。
- 返回 JSON 里看 `lossless` / `format` / `sample_rate` / `bits_per_sample`：
  真正无损应为 `format=flac`，常见 44100 Hz/16 bit，Hi-Res 为 96000 Hz/24 bit。
- 用户明确不要 MP3 时传 `allow_fallback=false`，没有无损源会直接报错而不是偷偷降级。
- Cookie 自动按 显式 `cookie_file` → `NETEASE_COOKIE` → MCP 脚本同目录
  `MUSIC/MyJar/*.txt` → `~/.netease_cookie.txt` 的顺序查找。
- 汇报时**如实说明格式与位深**（无损就说 FLAC 24bit/96kHz，回退了就说 MP3 320k），
  并核实文件真实落盘。

## 流程

1. **确认需求**：从用户话里提取歌名与歌手。缺歌手或歌名不完整时先问，
   不要直接按模糊词搜索后就下载。
2. **搜索**：`music_search(query="<歌名> <歌手>")`。若结果为空，去掉歌手
   只搜歌名，再不行换关键词（繁体/英文名/简称）。
3. **甄别版本（关键步骤）**：
   - 同名结果常混有：伴奏、翻唱、DJ版、慢速版、片段、remix、无损搬运
   - 只选**歌名与歌手都为原唱**的那条；用户点名要某版本时才选对应版本
   - 候选里无法确定时，把前 3 条（ID/歌名/歌手/版本）列给用户确认，
     **不要自作主张挑一条"最像的"**
4. **下载**：`music_download(song_id=<选中的 ID>)`。
   - 用户指定目录时传 `save_dir`；没指定则默认桌面
   - 用户要求普通音质时传 `br=128000`（默认 320000）
   - **用户要无损/原版音质 → 改用内置工具 `music_download_lossless(song_id=...)`**，
     见上一节「无损下载」
5. **汇报**：告知保存路径、大小、时长；如标签信息与预期不符，明确说明。

## 歌单相关请求

- 「我有哪些歌单 / 看看我的歌单」→ `music_my_playlists`（默认当前登录账号）
- 「xx 歌单里有什么歌」→ 先在 `music_my_playlists` 里按名称找到 ID，
  再 `music_playlist_songs(playlist_id=..., limit=50)`；歌单很大时分页
  （offset 参数），不要一次拉几百首
- 「把歌单里那首 X 下载下来」→ 在 `music_playlist_songs` 结果里按歌名+歌手
  定位歌曲 ID，再 `music_download`；**不要按位置序号猜测**
- 用户给的是歌单链接时，从链接里取 `id=` 参数作为 playlist_id

| fee | 含义 | 下载条件 |
|---|---|---|
| 0 | 免费 | 直接可下 |
| 1 | VIP 专享 | 必须用有 VIP 的账号 Cookie |
| 8 | 部分免费 | 普通音质可能需购买；VIP 可下高音质 |

## 失败处理

| 现象 | 处理 |
|---|---|
| "VIP 专享但账号无权限" | 先 `music_cookie_status` 确认；告知用户当前账号无 VIP，需换 VIP 账号重新抓 Cookie |
| "未登录/Cookie 失效" | 告诉用户：浏览器登录 music.163.com → F12 → Network → Copy as cURL → 存到 `newbroker/MUSIC/MyJar/goharu.txt`（或设置 `NETEASE_COOKIE` 环境变量），存好后让我再试 |
| "没有下载地址（fee=…）" | 版权限制/音质不可用；不要原样重试，换该歌曲的其他可用版本或告知用户 |
| 重试仍返回极小文件 | 音源异常；最多重试一次，然后报告失败 |

## 红线

- **歌曲 ID 必须来自搜索结果**，禁止猜测或手拼 ID
- 版本不匹配时宁可问用户，不"凑合下载"
- 不向用户复述 Cookie 内容，不把 Cookie 写进回答或代码
- 下载失败如实报告原因，不编造"已下载"
- 只帮用户下载其有权使用的音乐，不讨论绕过付费/盗版分发
