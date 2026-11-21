# Checkpoint/ 维护者文档

本目录是 **turn 级审计 + 回退子系统**（主代理直管）。外部接入只看 `api.py`。

## 架构

```
挂钩层（精确快照，source=hook:*）
  Tools/builtin/file_tools.py Write/Edit ──→ Tools/file_change_hook.py（listener 单例）
  Tools/builtin/pdf_annotate_tools.py _save_pdf ─┘
  Tools/builtin/music_tools.py 下载落盘 ───────┘
  Memory L1Repository.upsert/deactivate ──→ Memory/change_events.py
  SceneFileManager.write/delete_scene ───────┘（runtime_memory 下 git 看不到，必须挂钩）
  PersonaGenerator.write_persona ────────────┘
对账层（兜底，source=git）
  turn 开始 git stash create 冻结基线 ref（不动工作区）→ turn 结束
  status/diff 求差，捕获 run_cmd/MCP/后台任务等绕路写入
接线：Gateway/session_manager.py 装配 + session.py 的 process_message
  begin_turn（入口）/ end_turn（finally，asyncio.to_thread 防阻塞事件循环）
```

## 不变量（改动前必读）

1. **零 import Tools/Memory**：本目录不 import 任何兄弟子系统；记忆回退只经
   构造注入的 `memory.restore_l1_atom(old_row, atom_id)`（SQL 全在 Memory 内）。
   反向也成立：Tools/Memory 只知 listener 单例，不知 Checkpoint 存在。
2. **git 调用只走 `gitbase._run_git`**：tempfile 替代 PIPE（Windows 孙进程
   继承管道挂死）、stdin=DEVNULL、GIT_TERMINAL_PROMPT=0、timeout=10。
   - `git show` 失败返回 None 必须查 **returncode**（空输出≠文件不存在）。
   - status 必须 `--untracked-files=all`：normal 模式把新目录折叠成 `dir/`，
     排除规则（`*.pyc`）会对目录内文件失效。
3. **归属规则**：事件归发生时 open 的 checkpoint；无 open（turn 间隙的
   L1 daemon/L2 Timer 线程写入）→ pending 缓冲并入下一 checkpoint。
   方向是"多收"而非"漏收"，不得改成按 source_turn_id 精确归属（那是
   session+day 粒度，伪造精确性）。
4. **回退可 redo**：rollback 自身写 kind=rollback checkpoint（预捕捉当前态），
   不得绕过 manifest 直接改文件。blob/manifest 一律 tmp+os.replace 原子写。
5. **绝不中断 turn**：begin/end/emit/rollback 任何失败都降级记日志
   （degraded 事件广播），不得抛异常穿透到 turn 循环。
6. **retention 时机**：begin_turn 时以 max_count-1 裁剪（为即将到来的
   当前 manifest 预留槽位）；git 基线 ref（refs/goharu/cp/<seq>）随裁剪删除，
   不主动跑 git gc。
7. **checkpoint 目录必须在 gitignore 范围内**（默认 runtime_memory/checkpoints，
   已被 .gitignore 第 8 行覆盖）——否则对账会看到 checkpoint 自身写盘。

## 接口契约测试

`tests/test_checkpoint_standalone.py`（45 项：emit/回退/链式倒带/redo/git 兜底/
排除/冲突/降级/retention/并发/pending/L1 三表同步）——改动后必须跑。
`tests/test_checkpoint_e2e.py` 是真实 LLM 端到端验收（打 API，谨慎频繁运行）。
