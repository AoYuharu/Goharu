---
name: davinci-fusion-3d-text
description: >-
  在 DaVinci Resolve 里用 Fusion 做真正有厚度、有光照的 3D 立体文字叠加（片头字、MV 悬浮字、logo 字）。
  当用户说"加个 3D 立体字""Fusion 里做个 3D 标题""文字要有厚度/高光""3D 字是纯黑/纯白一片、看不出立体"
  "Text3D 加了但是平面的""给 MV 加个悬浮文字"，或需要在时间线全程叠加 3D 文字时使用。
---

# Fusion 3D 立体文字

目标：在时间线的片段上叠一层**看得出厚度和光照**的 3D 文字，并保证结果真的保存进了项目。

## 工具
- `mcp__davinci_resolve__run_script` — 所有 Fusion 操作（沙箱内，`resolve` / `project` 已注入）
- `mcp__davinci_resolve__search_scripting_api` — 查 API 名称，不确定就查，别猜
- `mcp__davinci_resolve__get_resolve_status` — 先确认 Resolve 在跑
- 验证用：`Project.ExportCurrentFrameAsStill(path)` + 本地 `PIL`/`numpy` 分析

## 流程

### 1. 摸清现场（不要跳过）
1. 取当前时间线的目标 clip → `clip.GetFusionCompByIndex(1)`，**先 dump 现有节点**：
   `[t.Name for t in comp.GetToolList(False).values()]`
2. 只有 `MediaIn`/`MediaOut` 说明之前的节点**没保存**——别假设"上次做过了"，一律重做。
3. 记录 clip 的起止帧，文字要覆盖整段就挂在**这个 clip 自己的 Fusion comp** 上（自动全程覆盖，
   不用新建 Fusion Composition clip 再去调时长）。

### 2. 搭节点（3D 链 + 2D 合成）
```
MediaIn1 ─────────────────────────────► Merge1.Background
Text3D1 → LightAmbient1 → LightDirectional1 → Renderer3D1 ─► Merge1.Foreground
                                              Merge1 ──────► MediaOut1.Input
```
- 灯光**必须串进 3D 链**（`light.SceneInput.ConnectTo(prevTool)`），不连不生效。
- 工具 ID 实测可用：`Text3D` `Renderer3D` `Merge` `LightAmbient` `LightDirectional` `LightPoint` `Camera3D` `Glow`。

### 3. 三个必设项（缺一个就"看不出立体"）
| 项 | 值 | 不设的后果 |
|---|---|---|
| `Renderer3D.RendererSoftware.LightingEnabled` | `1` | **默认是 0**，灯光完全不参与计算，文字只有材质固有色 → 一片纯色（材质暗时就是"纯黑字"） |
| `Text3D.ExtrusionDepth` | `0.30 ~ 0.45` | 0.1 级别太薄，侧壁投影只有几像素，视觉上仍是平面字 |
| `Text3D.Transform3DOp.Rotate.Y` | 弧度 `radians(-25) ~ radians(-30)` | 相机正对时侧壁被正面完全遮住，看不到厚度 |

### 4. 关键参数（全部是脚本实测值）
```
Text3D:  StyledText="Hoshino"  Font="Arial"  Style="Bold"  Size=0.45
         Extrusion=1  ExtrusionDepth=0.35  BevelDepth=0.015  BevelWidth=0.012
         UseOneMaterial=1  Red1=0.90 Green1=0.90 Blue1=0.96
         SpecularIntensity=0.7  SpecularExponent=18
         Transform3DOp.Rotate.X=radians(4)  Rotate.Y=radians(-30)
LightAmbient:      Intensity=0.18
LightDirectional:  Intensity=0.85  Rotate.X=radians(30) Y=radians(-55) Z=radians(-20)
```
光比是核心：ambient 0.15~0.20 + directional 0.75~0.90。**方向光超过 1.2 会过曝**，
正反面一起饱和到白，直方图挤在最高档，立体感全丢。

### 5. 验证（看不到画面时唯一可靠的办法）
1. `ExportCurrentFrameAsStill` 导两帧做 A/B：断开 `Merge1.Foreground` 导 baseline，
   接回来导 with_text，`np.abs(a-b).max(axis=2) > 0.02` 得到文字 mask。
2. 只看文字：把 `MediaOut1.Input` 临时直连 `Renderer3D1` 导出。
   **不要靠断开 Merge 的 Background 来隔离**——Fusion 的 Merge 没有背景输入会输出空画面，导出全黑，会误判成"文字没了"。
3. 用数值判定，不靠"看起来对"：
   - `bbox` 宽高比 ≈ 5:1（7 个字母）；接近 1:1 说明文字被转成了侧视
   - 文字区亮度 `std > 0.25`；`std < 0.1` 就是平面纯色
   - 亮度直方图必须分散，全部堆在某一档 = 过曝或不受光
   - 按 x 分 6~8 段看平均亮度，应有梯度（正面亮、侧壁暗）
4. 字形对不对：把 mask 缩成 76×20 打 ASCII（`#`/`O`/`+`/`.`），能认出字母才算形状正确。

### 6. 收尾（否则等于白做）
- `comp.Save()` **再** `resolve.GetProjectManager().SaveProject()`
- 注意：`proj.SaveProject()` 不存在，保存项目要用 ProjectManager
- 复检连线：`out.Input`→Merge1、`merge.Background`→MediaIn1、`merge.Foreground`→Renderer3D1
- 最后**在 Resolve 里播放给用户看**，不要用渲染 MOV 代替

## 红线
- 3D 旋转输入是**弧度**。`SetInput("Transform3DOp.Rotate.Y", 22)` 是 22 弧度 ≈ 1260°，
  文字会变成侧视斜条——所有角度一律 `math.radians(度)`。
- `GetInputList()` 拿到的 input 对象在 Resolve 包装里 `GetValue()` 会抛异常，
  读值统一用 `tool.GetInput("INPS_ID")`。
- 灯光旋转、文字旋转都要用弧度；`Translate`/`Size`/`ExtrusionDepth` 是场景单位，不是像素。
- 一次 `run_script` 不要塞太多操作（AddTool 很慢），默认 10s 会超时；给 `timeout=60`。
- 不确认 `LightingEnabled` 就下结论说"3D 做好了"，是自欺欺人。

## 失败处理
| 现象 | 原因 | 处理 |
|---|---|---|
| 文字纯黑/纯色一片 | `LightingEnabled=0` | 置 1，并确认灯光已串进 3D 链 |
| 有光但整体死白 | 方向光过强过曝 | 降到 0.75~0.9，ambient 0.15~0.2 |
| 看不出厚度 | 挤出太薄 / 相机正对 | `ExtrusionDepth≥0.3` + Y 轴转 25~30° |
| 文字变斜条、字形消失 | 旋转传了度数 | 改 `math.radians()` |
| 导出全黑 | 断开 Merge 背景导致空画面 | 改直连 `MediaOut1.Input` 到 Renderer3D |
| 重开项目后节点没了 | 只 `comp.Save()` 没存项目 | 补 `SaveProject()` |
| 文字在 MV 上不够醒目 | 与背景亮度差 <0.2 | 加 `Glow`（Threshold/Gain/XGlowSize）或加深投影 |
