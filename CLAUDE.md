# CLAUDE.md
This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

- Install dependencies from the checked-in requirements file:
  - `pip install -r requirements.txt`
- Run the TUI application (primary entrypoint):
  - `python run_tui.py`
- TUI keyboard shortcuts:
  - `Ctrl+C` — Quit
  - `Ctrl+L` — Clear chat
  - `Ctrl+H` — Toggle help

There is no repo-defined `pytest`/`unittest` test suite, lint configuration, or formatter configuration. The testing approach is:

**Interactive code** (has `input()`, GUI, infinite loops): Create standalone test files (e.g., `test_xxx.py`) that test core functions without running the interactive loop. Run specific tests directly with `python test_xxx.py`.
**Standalone scripts** (pure functions/classes): Run directly with `python script.py`.

The agent uses a simple ReAct loop: the actor calls tools step by step, then generates a final answer. No reflection or review is needed.

## Architecture

- `run_tui.py` is the primary runtime entrypoint.
  - Launches a Textual TUI (`TUI/app.py`) which starts the agent backend as a subprocess (`TUI/gateway_entry.py`) and communicates via JSON-RPC over stdio.
  - `TUI/gateway_entry.py` initializes `MemoryManager`, `ActorAgent`, and the in-process tool runtime, then runs the agent loop in `GatewaySession.process_message()`.
  - The shared agent execution loop (`Agent/agent_loop.py`) is used by the Gateway path (`Gateway/agent_bridge.py`).

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
  - **Testing**: Run `python test_command_security.py` to verify security checks (100% pass rate)

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
  - **Hard restrictions on run_cmd**: Actively rejects file operation commands (echo, cat, sed, awk, etc.)
  - **Enforcement at runtime**: run_cmd validates commands and returns error messages guiding to use dedicated tools
  - **No workarounds**: Model cannot bypass restrictions; attempting to use echo/cat/sed will fail with instructive error
  - **Security checks**: Dangerous commands (shutdown, rm -rf, format, etc.) are blocked to prevent system damage
  - **Command safety**: Multi-layer security system intercepts destructive operations before execution

- Performance optimization:
  - **Multi-level Prompt Caching**: Implemented Anthropic's Prompt Caching with tiered strategy
  - **Level 1 (Always cached)**: SOUL.md (role definition), Tool Directory (tool definitions)
  - **Level 2 (Cached by default)**: User Profile, Memory (MEMORY.md)
  - **Level 3 (Turn-based caching)**: Historical conversation turns (all except the latest turn)
  - **Not cached**: Latest conversation turn (current user input and ongoing responses)
  - Cache TTL: 5 minutes (Anthropic default)
  - Expected benefits: ~85-90% cost reduction on cached tokens, faster first-token latency
  - Implementation: `cache_control: {"type": "ephemeral"}` on PromptSection `→` PromptRenderer `→` LLMCore (Anthropic path only)
  - Test: Run `python test_multilevel_cache.py` to verify caching strategy

- Retrieval/RAG code exists, but it is not integrated into the main agent flow.
  - `MCP/RAG.py` contains standalone embedding, Milvus, and reranking code.
  - The knowledge tools (`Tools/builtin/knowledge_tools.py`) provide the interface to the knowledge management system.

## Important repository specifics

- `config.yaml` controls model paths, memory paths and retention, tool settings, and the retained embedding/rerank settings for the unused RAG shell. The checked-in paths are Windows-specific absolute local paths.
- This repository is centered on local agent/runtime code. Fine-tuning scripts and runtime LoRA loading have been removed.
- `run_tui.py` is the active execution mode: TUI-based multi-step agent workflow with actor/memory management

## 开发理念
- **模块化**：将代码分解为独立的模块和类，以便于理解和维护与后期延展
- **可配置性**：通过配置文件（如 `config.yaml`）来控制模型路径、内存限制、MCP设置等，使得代码更加灵活
- **单元测试与验收测试**：添加小模块后对小模块进行全面的单元测试，与整体耦合结束后主动启动项目对模型进行询问与成果验收(必须要能够得到模型回复，才算是对话成功)
- **仔细思考**：当收到用户的需求，对每个需求进行详细思考，如果有不明确的地方应该**主动向用户提问，排除模糊点再修改**，而不是直接进行假设并编写代码
- **最简开发**：在完成需求的前提下，尽量减少代码量，避免任何过度设计
- **全面日志**：在添加任何新模块的时候，一定要写详尽的日志，以便后期查日志了解问题

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
