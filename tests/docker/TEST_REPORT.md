# Goharu 一键安装 / 调用工具 端到端验收报告

日期：2026-11-24
验收标准：**一键安装成功后，智能体既能成功回复，又能实际调用工具完成小事**。
测试 key：DeepSeek（写入 `ZHIPU_API_KEY`，模型 `deepseek-flash`）。

## 结论：全部 PASS

| 环境 | 一键安装 | gateway 启动 | 回复 | 调用工具 | 结论 |
|---|---|---|---|---|---|
| Docker ubuntu:24.04 (Node 20) | ✓ | ✓ | ✓ pyyaml | ✓ Read | **PASS** |
| Docker debian:12 (Node 20) | ✓ | ✓ | ✓ pyyaml | ✓ Read | **PASS** |
| Docker rockylinux:9 (Node 20) | ✓ | ✓ | ✓ pyyaml | ✓ Read | **PASS** |
| Windows 11（本机，从 GitHub 全新 clone 到 `%USERPROFILE%\Goharu`）| ✓ | ✓ | ✓ pyyaml | ✓ Read | **PASS** |

测试任务：让智能体「用 Read 工具读取 requirements.txt，告诉第一行」。四个环境均真实调用 `Read` 工具并返回 `pyyaml`。

## 测试方法

- Docker：`docker run --rm goharu-env:<distro>`（干净镜像，预装 git/python/node20），
  容器内跑真实一行安装 `bash -c "$(curl .../master/bootstrap.sh)"`，再用
  `tests/docker/e2e_gateway_test.py` 驱动 gateway 验收。
- Windows：从 `C:\` 跑一行 `powershell -Command "irm .../master/bootstrap.ps1 | iex"`
  （自动 clone 到 `%USERPROFILE%\Goharu`），再在同目录跑 e2e 脚本。
- e2e 脚本：真起 `Gateway/gateway_entry.py` 子进程，发 `agent.send_message`，
  收集事件直到 `agent.answer`，断言出现 `tool.call`（工具）且回复非空。
  为不与本机已运行实例冲突、并快速聚焦核心，e2e 用临时 config 做隔离：
  `rpc_port=0`（纯 stdio）、关 `bootstrap`/`upgrade`/`agentnet`、config 放在仓库目录内
  （保证相对路径 `./prompts` 正确解析）。

## 过程中发现并修复的 bug

1. **Node 版本必须 ≥20**（实测 bug）：TUI 依赖 `string-width@8` 用正则 `/v` 旗标（Unicode
   集合），Node 18 解析崩溃 `Invalid regular expression flags`。原要求写 18 是错的。
   修复：install.py / README / bootstrap.sh / bootstrap.ps1 统一 ≥20；tui-ts/package.json
   加 engines；Docker 环境镜像改装 Node 20。
2. **gateway 子进程用错 python**（实测 bug）：TUI 用 `PYTHON ?? python3` 拉 gateway，删掉
   `start.sh` 后无人设 PYTHON → gateway 落到系统 python3（无 yaml 等依赖）→ 崩在 import。
   修复：`run_tui_ts.py` 把 `sys.executable`（venv python）传给子进程；`goharu` 启动器
   显式 `export PYTHON`。
3. **requirements 缺 httpx**（实测 bug）：代码 4 处直接 `import httpx`，却只靠 anthropic/openai
   传递依赖，容器里解析成 `httpx2`（fork）→ 真 `httpx` 缺失。修复：显式加 `httpx`。
4. **anthropic 版本漂移**（实测 bug）：容器装 `anthropic 2.x` 内部改用 `httpx2`，与本代码
   的 `httpx.Timeout` 冲突（`TypeError`）。本机能跑是因为用 `0.95.0`（标准 httpx）。修复：
   钉 `anthropic==0.95.0`。
5. **bootstrap.ps1 Node 版本解析错**（实测 bug）：`-replace '[^0-9].*$',''` 把 `v24.12.0`
   从 `v` 起全删成空串 → 误判 <20 → 即使 Node 24 也报缺依赖。修复：去 `^v` 后按点切主版本。
6. **e2e 脚本隔离问题**（测试脚本自身）：需 `rpc_port=0` 避免与本机运行实例抢端口、
   关重子系统避免首启 bootstrap 拖慢、temp config 放仓库目录避免相对路径解析错。

## 复现命令

```bash
# Docker（推送到 master 后镜像已含 Node 20）
docker build -t goharu-env:ubuntu24 --build-arg BASE_IMAGE=ubuntu:24.04 -f tests/docker/Dockerfile.env .
docker run --rm goharu-env:ubuntu24
# 容器内：
bash -c "$(curl -fsSL https://raw.githubusercontent.com/AoYuharu/Goharu/master/bootstrap.sh)"
cd ~/Goharu && python3 tests/docker/e2e_gateway_test.py

# Windows（本机）
# 在一行 bootstrap 安装完成后，于安装目录：
venv\Scripts\python.exe tests\docker\e2e_gateway_test.py
```
