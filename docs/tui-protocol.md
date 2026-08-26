# TUI ↔ Gateway 协议契约

前端（`TUI/` Python Textual 或 `tui-ts/` TypeScript Ink）与后端 Gateway
（`TUI/gateway_entry.py` 子进程）之间的通信协议。**两侧修改协议时必须同步更新本文档。**

TS 侧类型定义：`tui-ts/src/gateway/protocol.ts`（discriminated union，编译期穷尽检查）。

## 传输

- 子进程 stdio，NDJSON：每行一个 JSON 对象，UTF-8 编码
- 请求 → `{"jsonrpc":"2.0","method":<method>,"params":{...},"id":"N"}`
- 响应 → `{"jsonrpc":"2.0","result":{...},"id":"N"}` 或 `{"jsonrpc":"2.0","error":{"code":N,"message":"..."},"id":"N"}`
- 事件 → `{"jsonrpc":"2.0","method":"event","params":{"type":<event_type>,"payload":{...}}}`
- Gateway 的 stderr 仅用于日志/诊断，不参与协议；客户端应保留最后若干行用于崩溃报告

## RPC 方法（客户端 → Gateway）

| 方法 | params | result | 说明 |
|---|---|---|---|
| `agent.send_message` | `{message, session_id}` | `{queued: true}` | **非阻塞**：立即返回，回答经事件流到达 |
| `agent.clear_session` | `{session_id}` | `{queued: true, command}` | 排队执行，结果经 `command.complete` 事件 |
| `agent.compact` | `{session_id}` | `{queued: true, command}` | 排队执行 LLM 摘要压缩 |
| `agent.get_history` | `{}` | `{messages: [{role, content, type, timestamp}]}` | 序列化历史（跳过 context_only）|
| `agent.update_config` | `{key, value}` | `{success, key, old_value, new_value}` | 点号路径写 config.yaml 并保存 |
| `agent.interrupt` | `{}` | `{interrupted: bool}` | 仅处理中有效；中断经 `agent.interrupted` 事件确认 |

## 事件（Gateway → 客户端）

| type | payload | 语义 |
|---|---|---|
| `gateway.ready` | `{}` | 初始化完成；此后可调 `agent.get_history` 回放历史 |
| `gateway.init_error` | `{message}` | 就绪前崩溃；message 附 stderr 尾部 |
| `agent.thinking` | `{}` | 开始处理（显示 spinner）|
| `agent.thinking_content` | `{step, thinking}` | 思考块内容 |
| `agent.text_content` | `{step, text}` | 工具调用之间的说明文字 |
| `agent.step` | `{step}` | 第 N 步推理 |
| `agent.llm_requesting` | `{}` | 即将发起 LLM 请求 |
| `agent.l1_extracting` | `{}` | L1 记忆提取中 |
| `agent.answer` | `{answer}` | 最终回答（完整文本，markdown）|
| `agent.streaming` | `{chunk}` | 流式片段（当前少用，保留）|
| `agent.interrupted` | `{message}` | 用户中断确认 |
| `agent.error` | `{message}` | 错误；Python TUI 收到后会自动重启 gateway |
| `tool.call` | `{tool, arguments, call_id, step}` | 工具调用开始（实时）|
| `tool.result` | `{tool, call_id, result}` | 单个工具完成；result 为预览字符串（已截断）|
| `token.stats` | `{current_tokens, prompt_cache_ratio}` | 下一次请求的 token 估算 |
| `user.batch` | `{count, combined}` | 多条消息合并为一个上下文 |
| `user.merge` | `{count, merged}` | 处理中合并了新消息 |
| `message.complete` | `{answer}` | 一轮处理结束；若 `agent.answer` 未渲染过则用它兜底 |
| `context.micro_compact` | `{removed_results}` | 微压缩移除的旧工具结果数 |
| `task.background.started` | `{tool_calls: [{tool_name}]}` | 工具转入后台 |
| `task.background.completed` | `{count, task_ids}` | 后台任务结果已注入 |
| `task.background.status` | `{pending_results, ...}` | 后台任务状态 |
| `command.complete` | `{command, success, error?}` | `clear_session`/`compact` 执行结果 |

## 进程管理约定

- 启动：`python -u TUI/gateway_entry.py`，cwd=项目根，env `PYTHONIOENCODING=utf-8`
- 崩溃语义：stdout EOF 发生在 `gateway.ready` 之前 → 客户端合成 `gateway.init_error`；之后 → 合成 `agent.error`；主动 kill 两者都不发
- 客户端 5s 心跳检查子进程存活，死亡自动重启
- Windows 强杀：`taskkill /pid <pid> /T /F`（杀整个进程树）
