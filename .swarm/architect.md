# Architect 代理经验档案

> 管辖：`Architect/`。章程：`Architect/CLAUDE.md`。
> 契约测试：`tests/test_architect_hub_standalone.py`、`tests/test_bootstrap.py`。

## 领域知识（长期沉淀）

- Bootstrap 的 `unknown` 是“适用但规则未确认”，`unavailable/not_applicable` 必须有明确依据；缺少通用探测快路径不能直接判定设备无能力。
- Arch 补漏任务必须同时携带 `verified_facts` 与分层 gaps，只有缺项会让 Arch 重复已有探测。

## 经验日志（按时间追加，最新在最后）

- 2026-11-25｜**Windows 串口发现优先注册表只读快路径**。`Win32_SerialPort` CIM 查询在 PnP 枚举后可能明显超时；读取 `HKLM\HARDWARE\DEVICEMAP\SERIALCOMM` 更稳定，且不打开设备、不发送数据。
- 2026-11-25｜**L1–L5 分层探测落地**。单类别异常只记该类别 unknown，后续层继续；False、0、空列表是 verified，不是缺项。Windows/macOS 无通用 GPIO 快路径时保留 unknown 给 Arch，不能误报 not_applicable。安装者的 initial_exploration 与 gaps 合并成一次 KIND_EXPLORE，禁止安装、改源码/配置和真实设备动作，唯一写目标是 identity。
- 2026-11-25｜**Arch 上下文复用 subagent.update**。default_dispatch 在构造与执行外包 try/finally，started 带完整 task，finished 取真实 success/partial/error；通知异常必须独立隔离，不能替换业务返回值或异常。TUI `/arch` 定位最新动态 id，完成记录不参与普通子代理 30 秒消退。
- 2026-11-25｜**bootstrap 探索追加 verify 验收闭环**。KIND_EXPLORE 走 `hub._run_explore` → `explore_flow.run_explore_with_verify`，拿结构化 `request.context`（沿用 `_run_repair` 先例，不解析 task_text 字符串）。终止三选一：gaps 清空（硬规则）/ verify 判 PASS（软判断，"已尽力"必须逐项写凭据，缺凭据 FAIL 回喂）/ 达 max_rounds（默认 3）。新增 `verify_explore` 子代理——复用 verify 的 verdict 解析（`_build_result` 判定放宽为 `startswith("verify")`）但走普通任务模板，避开 `_build_verify_task_message` 的 git diff 预填干扰。教训：验收者是只读的，判断依据是 probe_report 里每个 unknown 的 evidence 有没有写"为什么无法只读确认"，而不是 gaps 是否绝对清零。
