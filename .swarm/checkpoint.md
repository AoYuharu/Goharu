# Checkpoint 域经验档案

## 领域知识

- **turn 级 checkpoint 双层捕获**：挂钩层（file_change_hook/change_events listener
  单例）管精确，git 基线对账（stash create + status/diff 求差）管完备。
  挂钩点放 handler 内部而非 registry dispatch——SubAgent 直连 registry 会被绕过，
  但 handler 是最终汇合点。
- **归属规则**：事件归发生时 open 的 checkpoint；turn 间隙事件进 pending 并入
  下一 turn。L1 的 source_turn_id 是 session+day 粒度，不能用于 turn 级归属。
- **回退 = 倒带**：rollback_to(K) 撤销 (K,H]；回退自身写 kind=rollback
  checkpoint（预捕捉当前态）→ redo 无特殊代码路径。

## 经验日志

### 2026-11-21 初版落地（45 契约 + 11 E2E 全绿）

- **坑：`git show` 文件不存在时返回空 stdout 而非报错**——subprocess 拿输出
  必须查 returncode，否则"空 bytes"被当成"存在但空的旧文件"，create 误判成
  modify，回退时会留下空文件而不是删除。
- **坑：`git status --porcelain --untracked-files=normal` 折叠新目录为 `dir/`**
  ——排除规则（如 `*.pyc`）对目录内文件失效。必须 `--untracked-files=all`。
- **坑：retention 在 begin_turn 跑时当前 manifest 尚未落盘**——max_count
  要减 1 预留槽位，否则最终数量恒多 1。
- **坑：fnmatch 的 `**/foo/**` 不匹配根级 `foo/x`**（`.*` 后必须还有 `/`）——
  排除规则测试要覆盖"根级目录"和"嵌套目录"两种形态；配合 `*.pyc` 这类
  后缀规则双保险。
- **坑：测试探针/文本 `_read_file_text` 走 universal newlines**——快照旧内容
  必须 `read_bytes()`（写前原始字节），不能 `original_text.encode("utf-8")`
  （CRLF 文件会失真）。
- **再现：harness ANTHROPIC_* 环境变量泄漏**导致 agent_loop 契约测试 401——
  E2E 子进程必须 `env` 剥离 ANTHROPIC_*（test_checkpoint_e2e.py 已内置）。
- **有效姿势**：E2E 驱动 gateway_entry 子进程时，等待第二轮 answer 必须记录
  事件下标只数新事件——否则秒回上一轮的旧 answer（`agent.answer` 是队列事件）。
