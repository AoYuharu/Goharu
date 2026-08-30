# TUI 鼠标交互与应用内滚动视口：问题全记录

> 日期：2026-08-30 ｜ 涉及：`tui-ts/`（Ink 7 + React 19）、`Gateway/gateway_entry.py`、`Agent/SubAgent.py`、`Tools/builtin/agent_delegate.py`
> 环境：Windows 11 + **CMD（conhost/ConPTY）** + Node 24

本次为 TUI 增加四个交互能力：工具行极简风格、鼠标点击展开工具详情、子代理实时底栏与全屏视图、应用内滚动视口（输入框固定底部）。过程中踩了四类典型终端工程问题，记录如下。

---

## 问题 1：Ink `<Static>` 永远不重渲染已滚出的内容

**现象**：想实现"点击历史中的工具行展开详情"，但历史消息渲染在 `<Static>` 里。

**根因**：读 `node_modules/ink/build/components/Static.js` 确认其实现为 `items.slice(index)` 且 index 只增不减——已输出的条目物理上不会重渲染。选中高亮、行内展开在 Static 里都不可能。

**解法**：
- 放弃 Static，改为**应用内管理的滚动视口**：`MessageItem.tsx` 把每条消息转为"样式行数组"（`messageToLines` + wrap-ansi 预换行），App 按视口高度切片渲染。
- 渲染与测高共用同一函数，保证"测量行数 == 实际渲染行数"（`test/measure.test.tsx` 10 项断言逐类验证，含 markdown 与长行换行）。

**教训**：终端 UI 的交互自由度与渲染架构强相关；Static 换来的"免费 scrollback"意味着放弃对已输出内容的一切控制。

## 问题 2：鼠标转义序列泄漏成乱码 `[<0;76;35M`

**现象**：点击/移动鼠标后，输入框被注入 `[<0;5;38M` 之类的文本。

**根因（三层叠加）**：
1. 终端 SGR 鼠标序列（`\x1b[<0;76;35M`）作为 stdin 字节流到达；
2. **Windows ConPTY 会把一个序列拆成多个包**到达，Ink 的 `parseKeypress` 解析失败；
3. Ink 7 的 `use-input.js` 对解析失败的残缺序列会**剥掉 ESC 前缀后当成普通文本输入**（L95-99），残余字节遂插入输入框。

**三次拦截位置，前两次都是错的**：
| 尝试 | 做法 | 为什么失败 |
|---|---|---|
| v1 | `process.stdin.on("data")` 旁路监听 | 监听不能阻止 Ink 收到同样的字节；更糟的是挂 data 监听会把流切成 flowing 模式，**和 Ink 的拉取模式抢数据** |
| v2 | 劫持 `process.stdin.emit` | **Ink 7 根本不走 data 事件**——它用 `stdin.on("readable")` + `stdin.read()` 拉取（`components/App.js:175-206`），数据不经过 emit |
| v3 ✅ | 包装 `process.stdin.read()` | 唯一可靠的拦截点；自带缓冲区拼合 ConPTY 拆包 |

**教训**：给第三方库做输入拦截，先读它的源码确认消费模式（flowing vs readable），不要假设。

## 问题 3：鼠标一动就"卡死"，键盘输入全无响应

**现象**：开启 DECSET 1003（悬停跟踪）后，鼠标移动即卡死。

**根因**：Ink 的读取循环是 `while ((chunk = stdin.read()) !== null)`，**依赖 read() 返回 null 来判断"流已抽干"**。v3 初版的包装在抽到纯鼠标数据时返回 null，循环提前退出，混在鼠标事件后面的键盘字节**残留在流缓冲里**，且不再有新事件触发读取。

**解法**：read() 包装必须**自己也抽干整个流**，把过滤后的键盘文本累积后一次性返回，仅在流真正为空时返回 null。

**教训**：包装底层 IO 原语时，必须完整保持调用方依赖的契约（这里是"抽干语义"），否则故障模式极其隐蔽。

## 问题 4：悬停高亮与鼠标位置有固定偏差

**现象**：高亮的行总在鼠标上方几行。

**根因**：清屏（`\x1b[2J\x1b[H`）原本在 App 的 useEffect 里执行，**Ink 首帧已按启动时的光标位置（屏幕中部）渲染**，行号命中映射却假设视口从第 1 行开始。

**解法**：清屏移到 `main.tsx` 的 `render()` 之前，App 从第 1 行起拥有整个屏幕，命中映射变为 `行号 = 滚动偏移 + 点击y`，数学上精确。

## 终端协议的硬约束（架构决策依据）

- **鼠标捕获与原生滚轮/文本选择互斥**：开启鼠标上报后，终端把滚轮事件发给应用而非滚动 scrollback，所有终端皆如此。要"滚轮直接滚"只能由应用自己实现滚动视口——这正是放弃 Static 的第二个理由。
- 文本选择复制仍可用 **Shift+拖拽**（终端对鼠标捕获的标准绕过）。
- CMD/conhost 对 DECSET 1003（任意动作跟踪）的支持较弱，悬停高亮在部分终端可能不可用；点击（1000）+ SGR 扩展坐标（1006）在 CMD 与 Windows Terminal 均可用。

## 本次沉淀的调试设施

- `tui-ts/src/mouse.ts`：`drainMouseSequences` 纯函数解析器（可测）+ read() 包装。事件三类：click / wheel / move。
- `tui-ts/src/debugLog.ts`：文件日志（stdout 被 Ink 占用，日志只能写文件）。`GOHARU_TUI_DEBUG=1` 开启，输出到 `tui-ts/debug.log`，记录点击命中、悬停变化、批量解析计数。
- `tui-ts/test/mouse.test.ts`：12 项解析断言（拆包拼合、混合内容、假前缀冲刷等）。
- `tui-ts/test/measure.test.tsx`：渲染/测高一致性 10 项断言（ink-testing-library）。
