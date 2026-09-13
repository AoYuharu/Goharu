# CLAUDE.md
This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

- Install dependencies from the checked-in requirements file:
  - `pip install -r requirements.txt`
- Run the TUI application (primary entrypoint):
  - `python run_tui_ts.py` (TypeScript/Ink TUI; requires `cd tui-ts && npm install && npm run build` once)
  - The legacy Python/Textual TUI lives on the `python` branch.
  - Protocol contract between TUI and Gateway: `docs/tui-protocol.md`
- TUI keyboard shortcuts:
  - `Ctrl+C` — Quit
  - `Ctrl+L` — Clear chat
  - `Ctrl+H` — Toggle help

There is no repo-defined `pytest`/`unittest` test suite, lint configuration, or formatter configuration. The testing approach is:

**Interactive code** (has `input()`, GUI, infinite loops): Create standalone test files (e.g., `test_xxx.py`) that test core functions without running the interactive loop. Run specific tests directly with `python test_xxx.py`.
**Standalone scripts** (pure functions/classes): Run directly with `python script.py`.

The agent uses a simple ReAct loop: the actor calls tools step by step, then generates a final answer. No reflection or review is needed.

## Architecture

- `run_tui_ts.py` is the primary runtime entrypoint.
  - Launches the TypeScript TUI (`tui-ts/`, Ink) which starts the agent backend as a subprocess (`Gateway/gateway_entry.py`) and communicates via JSON-RPC over stdio.
  - `Gateway/gateway_entry.py` initializes `MemoryManager`, `ActorAgent`, and the in-process tool runtime, then runs the agent loop in `GatewaySession.process_message()`.

- `Agent/LargeLanguageModel.py` is a thin wrapper over the singleton `Agent/LLMCore.py`.
  - `LargeLanguageModel.query()` delegates to `LLMCore.generate()`.
  - `LLMCore` exclusively supports the `anthropic_compatible` provider (Anthropic-compatible API).

- Prompt locations are split by role.
  - Tool-use prompt: `Agent/ActorAgent.py`
  - Summarization prompt: `Agent/SummarizerAgent.py`

- Memory is coordinated by `Memory/MemoryManager.py` and `Agent/PipelineManager.py`.
  - Short-term context is persisted by `Memory/WorkingMemory.py` as daily JSON files under `memory.daily.dir`, with dual-write to SQLite (`memory.db`).
  - Long-term memory uses the L0→L1→L2→L3 pyramid pipeline managed by `PipelineManager`:
    - L0: Raw messages in SQLite (`L0Repository`)
    - L1: Memory atoms extracted per-turn (`SummarizeAgent`, stored in SQLite + embedding)
    - L2: Scene context aggregated from L1 atoms → `scene_blocks/*.md` (`SceneExtractor`)
    - L3: Persona profile generated from L2 scenes → `persona.md` (`PersonaGenerator`)
  - PipelineManager triggers: L1 on turn interval (configurable via `pipeline_manager.l1.turn_interval`), L2 with delay after L1, L3 when new atom threshold reached.
  - Main answer generation builds prompts through `PromptAssembler`, injecting: actor system prompt → SOUL.md → L3 persona → L1 retrieved memories → L2 retrieved scenes → conversation history.

- All tools run in-process through `Tools.runtime.InProcessToolRuntime`.
  - `Tools/builtin/` contains all built-in tools loaded via `config.yaml` → `tools.builtin_modules`.
  - **Security layer**: `Tools/security.py` implements command safety checks for `run_cmd`
  - **Dangerous commands blocked**: shutdown, restart, rm -rf, del /s, format, diskpart, reg delete, etc.
  - **Configuration**: Security settings in `config.yaml` under `tools.security`

- File operation tools (`Tools/builtin/file_tools.py`):
  - **Write**: Create new files (fails if file exists)
  - **Read**: Read file content by line range, grants Edit permission
  - **Edit**: Patch-style editing (old_string → new_string), requires prior Read
  - **Grep**: Search file content (read-only, does not grant Edit permission)
  - All tools use a permission system: must Read before Edit
  - Concurrent access control: read locks (multiple readers) and write locks (exclusive)

- Tool design philosophy:
  - **Patch-style Edit**: Like git diff, requires exact old_string and new_string
  - **Safety first**: Write refuses to overwrite, Edit requires Read permission
  - **run_cmd write-side restrictions**: shell file writes are rejected (echo > / >>, sed -i / --in-place) — file changes must go through Write/Edit tools
  - **Read-side commands allowed**: cat/head/tail/grep/sed-filter/awk/pipes work in run_cmd; the goal is controlling result size, and oversized output is auto-truncated by ToolResultBudget rather than rejected
  - **Enforcement at runtime**: run_cmd validates commands and returns error messages guiding to use dedicated tools
  - **No workarounds**: Model cannot bypass restrictions; attempting shell file writes will fail with instructive error
  - **Security checks**: Dangerous commands (shutdown, rm -rf, format, etc.) are blocked to prevent system damage
  - **Command safety**: Multi-layer security system intercepts destructive operations before execution

- Performance optimization:
  - **Prompt Cache 断点机制（核心机制要素，改动前必读）**：MiniMax（Anthropic 兼容端点）按**前缀**缓存——请求从头逐字节匹配已缓存前缀，命中部分按 cache_read 低价计费，未命中部分全价并触发 cache_write。**前缀中任何一处字节变化，都会使其后的全部缓存失效**。历史上曾因每轮检索记忆插入 system 中部，导致 cache_write 达 cache_read 的 2.7 倍（几乎全部失效）。
  - **铁律一：断点 ≤4**（Anthropic 硬限制）。当前 Actor 只用 2 个：①最后一个 system 块（覆盖静态前缀：角色定义 + git_status）②最后一个可缓存历史轮（覆盖全部累积历史）。收敛逻辑在 `Prompting/PromptAssembler.py` `build_actor_document`。
  - **铁律二：静态前缀逐字节稳定**。system 区绝不放每轮变化的内容；per-turn 检索结果（L3 persona / L1 memories / L2 scenes）与技能列表由 `_build_memory_context` 合成"本轮上下文块"，贴在**当轮用户消息**上，并按消息 id 记账（`ActorAgent._memory_contexts`）——历史渲染时贴回各自原消息，旧消息内容永不变化（对齐 Claude Code 的 system-reminder 常驻历史模式）。
  - **铁律三：多步循环的消息列表必须纯追加**。SubAgent（Explore/Plan/Verify）`execute` 循环用 `_advance_cache_breakpoint` 每步把历史断点推进到最新一条消息（`Agent/SubAgent.py`）；system 断点恒在 messages[0]。
  - **铁律四：一次性 LLM 调用**（SummarizerAgent、ContextCompactor、SceneExtractor、PersonaGenerator）由 `PromptLoader` 给末位 system section 打 1 个断点即可，无历史增长，无失效风险。
  - **新增每轮注入内容时的正确姿势**：合成进"本轮上下文块"（`_build_memory_context` / turn_context），不要新开 system section，不要修改已渲染历史，不要在消息列表中部插入。
  - **改动后必须验证**：`python test_cache_breakpoints.py`（断点收敛 + 前缀跨 turn 逐字节稳定断言）；E2E 实测（2026-09-12）：热身后命中率 91-95%、cache_write=0 即为达标；TUI 状态栏 Cache Hit 显示的是真实 API usage 命中率（`cache_read/(input+read+write)`，来自 `LLMCore._last_usage`），不要再用本地 cache_control 标记估算命中率。

- Retrieval is handled by `Memory/retrieval/HybridRetriever.py` (FTS5 + embedding + BM25 → RRF fusion) and is integrated into the main flow via `MemoryManager.retrieve_prompt_memory()`.
  - The knowledge tools (`Tools/builtin/knowledge_tools.py`) provide the interface to the knowledge management system.

## Important repository specifics

- `config.yaml` controls model paths, memory paths and retention, tool settings, and retrieval/embedding settings. The checked-in paths are Windows-specific absolute local paths.
- This repository is centered on local agent/runtime code. Fine-tuning scripts and runtime LoRA loading have been removed.
- `run_tui_ts.py` is the active execution mode: TUI-based multi-step agent workflow with actor/memory management

## 开发理念
- **模块化**：将代码分解为独立的模块和类，以便于理解和维护与后期延展
- **可配置性**：通过配置文件（如 `config.yaml`）来控制模型路径、内存限制、MCP设置等，使得代码更加灵活
- **单元测试与验收测试**：添加小模块后对小模块进行全面的单元测试，与整体耦合结束后主动启动项目对模型进行询问与成果验收(必须要能够得到模型回复，才算是对话成功)
- **仔细思考**：当收到用户的需求，对每个需求进行详细思考，如果有不明确的地方应该**主动向用户提问，排除模糊点再修改**，而不是直接进行假设并编写代码
- **最简开发**：在完成需求的前提下，尽量减少代码量，避免任何过度设计
- **全面日志**：在添加任何新模块的时候，一定要写详尽的日志，以便后期查日志了解问题

## 透明化原则
程序内部在执行什么、进行到什么状态，用户必须心知肚明、完全透明。

- **长耗时操作必须反馈进度**：任何会让用户"干等"的操作（LLM 重试、模型加载、后台任务、子代理运行）都必须在左下角状态栏实时显示当前状态（如「正在重试 LLM 第 3/10 次」）。
- **失败必须优雅降级或优雅报错**：可恢复的失败（LLM 超时重试耗尽、embedding 不可用、依赖缺失）要静默降级或返回明确错误消息，绝不能触发 Gateway 崩溃重启（用户会看到一次突兀的"重启"，体验很差）。
- **新增后台/异步流程必须配套状态事件**：每新增一个后台或异步流程，都要在 Gateway→TUI 事件协议（`tui-ts/src/gateway/protocol.ts` + `Gateway/gateway_entry.py` 的 `write_json` 事件）中增加对应事件，并在状态栏/面板里可视化，不能只有日志。

## 用户交互
- **默认最简回答：**当用户询问问题的时候，默认进行简要的关键点回答，回答尽量简练明确重点，不要长篇大论分点
- **用户辅助排查错误时：**给用户最真实的LOG日志以及问题点（可能是代码，也可能是提示词等），告诉问题所在，简要给出解决方案

## 测试策略（针对交互式代码）
模型在测试代码时会根据代码类型选择合适的策略：

**可自动测试的代码**：
- 纯函数/类：直接运行 `python script.py`
- 有测试文件：运行 `pytest` 或 `python -m unittest`

**交互式代码**（包含 `input()`, GUI, 无限循环等）：
- 模型会识别交互式特征，避免直接运行
- 创建独立的测试文件（如 `test_xxx.py`）测试核心函数
- 或说明代码已完成，建议用户手动测试
- 示例：对于包含 `input()` 的计算器，创建测试文件测试 `add()`, `subtract()` 等核心函数，而不测试交互式的 `calculator()` 函数
