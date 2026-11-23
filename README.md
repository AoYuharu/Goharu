<p align="center">
  <a href="#chinese">中文</a> | <a href="#english">English</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/LLM-Anthropic_Compatible-orange?style=for-the-badge&logo=anthropic&logoColor=white" alt="LLM">
  <img src="https://img.shields.io/badge/TUI-TypeScript_Ink-purple?style=for-the-badge&logo=terminal&logoColor=white" alt="TUI">
  <img src="https://img.shields.io/badge/license-MIT-green?style=for-the-badge" alt="License">
</p>

<p align="center">
  <img src="GoharuCover.png" alt="Goharu Cover" width="600">
</p>

<p align="center">
  双智能体个人 AI 系统：前台对话 Hero + 幕后助手 Arch。<br>
  A dual-agent personal AI system — Hero talks to you, Arch keeps the system alive.
</p>

---

<span id="chinese"></span>

## 中文文档

### Goharu 是什么？

**Goharu** 是一个运行在 Windows / Linux 上的个人 AI 智能体系统。它由两个智能体组成：

- **Hero（主智能体）**——你在 QQ / 终端 TUI 里对话的那个。ReAct 循环（思考 → 工具 → 观察 → 回答），操作文件、跑命令、联网、读 PDF，带着一套 L0→L3 记忆金字塔长期记得你。
- **Arch（幕后助手）**——你看不见的守护者。它在后台观察系统遥测，故障时自愈、代码更新时审计、裸机部署时自我探测环境。Hero 和 Arch 可以双向对话。

### 核心能力

| 能力 | 说明 |
|---|---|
| **多通道对话** | QQ 机器人（私聊/群聊@）+ TypeScript Ink TUI，消息跟随最近活跃通道，TUI 常驻旁观 |
| **记忆金字塔** | L0 原始消息 → L1 经验原子 → L2 场景 → L3 人设，混合检索（FTS5 + BM25 + 向量） |
| **子智能体** | Explore / Plan / Verify（只读）+ Arch（可写、有私有记忆塔） |
| **自愈与自升级** | Arch 监听健康信号（卡死/逻辑错误/更新），故障诊断修复；GitHub 更新经 staging 审计 + 四态确认流才合并 |
| **Bootstrap 裸机部署** | 首次启动自动探测 12 类系统信息（OS/CPU/GPU/网络/外设…），探不到的由 Arch 补漏 |
| **Checkpoint 审计** | turn 级文件+记忆双捕获，可回退 |
| **智能体组网** | HaruNet：多个 Goharu 实例互联广播 |
| **安全链** | 命令三态（允许/确认/硬拒）、先 Read 后 Edit、shell 写操作拦截 |

### 一键安装

一条命令，进入交互式引导页——选模型厂商、粘贴密钥，一气呵成装完即用。

**Windows**（Win+R 粘贴回车，或在 PowerShell 窗口执行）：

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/AoYuharu/Goharu/master/bootstrap.ps1 | iex"
```

**Linux / macOS**（粘贴到终端执行）：

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/AoYuharu/Goharu/master/bootstrap.sh)"
```

引导程序会自动：检查依赖 → clone 仓库 → 让你选模型厂商并粘贴 API Key（不可见输入）→ 选检索模式 → 建 venv 装依赖 → 构建 TUI → 写入 `.env` 与 `config.yaml` → 装完即可启动。

> 前置要求：git、Python ≥ 3.10、Node.js ≥ 18。引导程序会先检查，缺失时直接给出对应系统的安装命令。

**已克隆仓库的老习惯**（等价，同样进入引导页）：

```bash
./install.sh        # Linux/macOS
install.bat         # Windows
# 或直接：python install.py
```

**卸载：**

```bash
./uninstall.sh            # 清依赖产物，保留数据
./uninstall.sh --all      # 连记忆数据一起清
./uninstall.sh --purge    # 连 .env 密钥一起清
```

### 配置

全部走 `config.yaml`（首次安装无需改动即可跑）：

| 区块 | 控制什么 |
|---|---|
| `model` | LLM 端点/模型/重试（支持多模型历史切换） |
| `memory` | 记忆金字塔与检索参数 |
| `tools` | 工具链、安全黑名单/灰名单、MCP servers（`${ENV_VAR}` 引用密钥） |
| `agent` | ReAct 深度、看门狗、上下文压缩阶梯 |
| `architect_hub` | Arch 的遥测/唤醒/更新门控/Bootstrap |
| `sessions` | 多会话 + channel 挂载（TUI/QQBot/遥测） |

TUI 内置设置界面：右下角 `☰ Settings` → 模型 / 语音 / 浏览器抽取 / **通道管理**（可视化增删 channel，保存后重启生效）。

### 架构

```
QQ / TUI / …（channel 插件，能力声明轮发）
        │
   Gateway（多 session 装配、路由、压缩阶梯）
        │
   Hero ── ActorAgent（ReAct 引擎，主/子智能体共用）
        │
   Tools / Memory / Prompting / Agent（四大解耦子系统，facade 接入）
        │
   Arch ── 遥测 digest · 唤醒触发器 · staging 审计 · Bootstrap 探测
```

### 开发

模块化团队协作：每个子系统有 `CLAUDE.md` 维护者文档 + 独立契约测试（`tests/test_*_standalone.py`）。改公共接口后必须跑对应测试。详见根目录 `CLAUDE.md`。

---

<span id="english"></span>

## English

**Goharu** is a dual-agent personal AI system for Windows/Linux:

- **Hero** — the conversational agent you talk to (QQ bot / terminal TUI). ReAct loop, file & shell tools, L0→L3 memory pyramid, sub-agents (Explore/Plan/Verify).
- **Arch** — a backstage maintainer agent with its own private memory tower. Watches telemetry, self-heals failures, audits code updates through a staging pipeline, and probes bare-metal deployments.

### Quickstart

```bash
git clone https://github.com/AoYuharu/Goharu.git && cd Goharu
./install.sh        # Windows: install.bat
cp .env.example .env  # fill in your Anthropic-compatible API key
./start.sh          # Windows: start.bat
```

Uninstall: `./uninstall.sh [--all|--purge]` (Windows: `uninstall.bat`).

Any Anthropic-compatible endpoint works (DeepSeek, MiniMax, Kimi, GLM, official). Configuration is fully driven by `config.yaml`. Each subsystem (`Tools/`, `Memory/`, `Prompting/`, `Agent/`, `Architect/`) is independently pluggable with its own facade and contract tests.
