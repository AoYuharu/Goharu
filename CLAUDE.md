# CLAUDE.md
This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

- Install dependencies from the checked-in requirements file:
  - `pip install -r requirements.txt`
- Run the main MCP-enabled agent CLI:
  - `python main.py`
- Interactive commands available in `main.py`:
  - `/help` - Show help message
  - `/multi` - Enter multiline input mode
  - `/context` - Show the current context structure (message count, roles, and previews)
  - `/sysprompt` - Show the complete system prompt sections sent to the LLM
  - `/clear` - Clear the current session context
  - `/interrupt` - Interrupt the current agent execution
  - `/exit` or `/quit` - Exit the application

There is no repo-defined `pytest`/`unittest` test suite, lint configuration, or formatter configuration. The testing approach is:

**Interactive code** (has `input()`, GUI, infinite loops): Create standalone test files (e.g., `test_xxx.py`) that test core functions without running the interactive loop. Run specific tests directly with `python test_xxx.py`.
**Standalone scripts** (pure functions/classes): Run directly with `python script.py`.

The `reflection_mode: answer_review` in `config.yaml` is the current default. In this mode, `Agent/answer_review_flow.py` handles the actor→reflection→answer cycle, with `FileStateManager` tracking which files have been read/modified to avoid redundant operations.

## Architecture

- `main.py` is the primary runtime entrypoint.
  - `run_agent()` appends the user message to memory, loops up to `config.get("mcp.maxDepth")`, asks `ActorAgent.act()` to either answer or call a tool, runs `ReflectionAgent.reflect()` after each step, and then forces one final answer pass from the accumulated working-memory context.
  - `main()` starts an MCP stdio client using `mcp.executor` and `mcp.args` from `config.yaml`, creates `MemoryManager`, `ActorAgent`, and `ReflectionAgent`, and then runs the interactive loop.
  - Two execution modes selected by `mcp.reflection_mode`: the older `run_agent()` loop and the newer `answer_review_flow.py` (current default).

- `Agent/LargeLanguageModel.py` is a thin wrapper over the singleton `Agent/LLMCore.py`.
  - `LLMCore` loads the base Qwen model and tokenizer from `config.yaml` and applies 4-bit quantization.
  - `LargeLanguageModel.query()` delegates to `LLMCore.generate()`.

- Prompt locations are split by role.
  - Tool-use prompt: `Agent/ActorAgent.py`
  - Reflection prompt: `Agent/ReflectionAgent.py`
  - Summarization prompt: `Agent/SummarizerAgent.py`

- Memory is coordinated by `Memory/MemoryManager.py`.
  - Short-term context is persisted by `Memory/WorkingMemory.py` as daily JSON files under `memory.daily.dir`.
  - Long-term memory is persisted by `Memory/LongTermMemory.py` as `MEMORY.md` plus per-topic markdown files under `memory.topic.dir`.
  - `MemoryManager.detectOverflow()` checks for expired daily files based on `memory.daily.retention_days`, summarizes one expired day through `SummarizeAgent`, updates long-term memory, and deletes that daily file.
  - Main answer generation builds prompts through `MemoryManager.get_prompt_context()`, which injects the actor system prompt, then `MEMORY.md`, then recent working-memory messages, and finally any extra system prompt.

- MCP/tooling is separate from the model runtime.
  - `MCP/MCP.py` exposes file operation tools (`Read`, `Write`, `Edit`, `Grep`) and `run_cmd` via FastMCP.
  - `main.py` connects to that MCP server over stdio using the executor and args configured in `config.yaml`.
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
  - Implementation: `cache_control: {"type": "ephemeral"}` on PromptSection → PromptRenderer → LLMCore
  - Test: Run `python test_multilevel_cache.py` to verify caching strategy
  - Documentation: See `docs/multilevel_cache.md` for detailed implementation guide

- Retrieval/RAG code exists, but it is not integrated into the main agent flow.
  - `MCP/RAG.py` contains standalone embedding, Milvus, and reranking code.
  - The MCP `getKnowledge` implementation in `MCP/MCP.py` is still a stub, and `main.py` does not wire `MCP/RAG.py` into the main loop.

## Important repository specifics

- `config.yaml` controls model paths, memory paths and retention, MCP executor/args, and the retained embedding/rerank settings for the unused RAG shell. The checked-in paths are Windows-specific absolute local paths.
- This repository is centered on local agent/runtime code. Fine-tuning scripts and runtime LoRA loading have been removed.
- `main.py` is the active execution mode: MCP-enabled multi-step agent workflow with actor/reflection/memory management

## 开发理念
- **模块化**：将代码分解为独立的模块和类，以便于理解和维护与后期延展
- **可配置性**：通过配置文件（如 `config.yaml`）来控制模型路径、内存限制、MCP设置等，使得代码更加灵活
- **单元测试与验收测试**：添加小模块后对小模块进行全面的单元测试，与整体耦合结束后主动启动项目对模型进行询问与成果验收(必须要能够得到模型回复，才算是对话成功)
- **仔细思考**：当收到用户的需求，对每个需求进行详细思考，如果有不明确的地方应该**主动向用户提问，排除模糊点再修改**，而不是直接进行假设并编写代码
- **最简开发**：在完成需求的前提下，尽量减少代码量，避免任何过度设计

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

## Gateway 管理

Gateway 是连接 QQ 机器人的网关服务，支持 QQ 私聊和群聊消息处理。

**重启 Gateway**：
```bash
./restart_gateway.sh
```

该脚本执行以下操作：
1. 杀死旧 Gateway 进程
2. 记录重启日志
3. 启动新的 Gateway 进程
4. 检查启动状态

**手动重启 Gateway**：
```bash
cd /root/TableHelper
pkill -f "Gateway/run_gateway" 2>/dev/null
sleep 2
source venv/bin/activate
export CONFIG_FILE=config_gateway.yaml
export ANTHROPIC_API_KEY=你的API_KEY
nohup python3 -u Gateway/run_gateway.py >> logs/gateway.log 2>&1 &
sleep 8 && tail -20 logs/gateway.log
```

**查看 Gateway 日志**：
```bash
tail -100 logs/gateway.log
```