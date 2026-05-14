# TableHelper TUI 使用指南

## 概述

TableHelper TUI 是一个基于 Textual 的终端用户界面，提供了与 TableHelper Agent 交互的图形化终端体验。

## 架构设计

参考 hermes-agent 的 TUI 架构，采用 **JSON-RPC over stdio** 的通信模式：

```
┌─────────────────┐         JSON-RPC          ┌──────────────────┐
│   TUI Frontend  │ ◄──────────────────────► │  Gateway Server  │
│   (Textual UI)  │    stdin/stdout pipe      │  (Agent Logic)   │
└─────────────────┘                           └──────────────────┘
```

### 核心组件

1. **TUI Frontend** (`TUI/app.py`)
   - 基于 Textual 的终端 UI
   - 显示聊天历史、工具调用、状态信息
   - 处理用户输入

2. **Gateway Client** (`TUI/gateway_client.py`)
   - 管理 Gateway 子进程
   - 处理 JSON-RPC 通信
   - 事件分发

3. **Gateway Server** (`TUI/gateway_entry.py`)
   - JSON-RPC 服务器
   - Agent 逻辑处理
   - 工具执行

4. **UI Widgets** (`TUI/widgets/`)
   - `ChatPanel`: 聊天面板
   - `ToolPanel`: 工具调用面板
   - `StatusBar`: 状态栏

## 启动方式

### 方式 1: 使用启动脚本

```bash
python run_tui.py
```

### 方式 2: 直接运行

```bash
python TUI/entry.py
```

### 方式 3: 作为模块运行

```bash
python -m TUI.app
```

## 功能特性

### 1. 实时聊天

- 在底部输入框输入消息
- 按 Enter 发送
- 实时显示 Agent 响应

### 2. 工具调用可视化

右侧面板显示：
- 工具名称
- 调用参数（JSON 格式）
- 执行结果

### 3. 状态监控

底部状态栏显示：
- 当前状态（Ready/Thinking/Working/Error）
- 统计信息

### 4. 快捷键

- `Ctrl+C`: 退出
- `Ctrl+L`: 清空聊天历史
- `Ctrl+H`: 显示帮助（待实现）

## 事件系统

Gateway 通过 JSON-RPC 事件通知 TUI：

### 事件类型

1. **gateway.ready**
   - Gateway 启动完成
   - Payload: `{}`

2. **agent.thinking**
   - Agent 开始思考
   - Payload: `{}`

3. **tool.call**
   - 工具调用开始
   - Payload: `{"tool": "tool_name", "arguments": {...}, "step": 1}`

4. **tool.result**
   - 工具执行完成
   - Payload: `{"tool": "tool_name", "result": "..."}`

5. **message.complete**
   - 消息处理完成
   - Payload: `{"answer": "..."}`

6. **agent.error**
   - 发生错误
   - Payload: `{"message": "error message"}`

## JSON-RPC 方法

### agent.send_message

发送用户消息给 Agent。

**请求:**
```json
{
  "jsonrpc": "2.0",
  "method": "agent.send_message",
  "params": {
    "message": "用户消息",
    "session_id": "default"
  },
  "id": "1"
}
```

**响应:**
```json
{
  "jsonrpc": "2.0",
  "result": {
    "answer": "Agent 回复"
  },
  "id": "1"
}
```

### agent.clear_session

清空会话上下文。

**请求:**
```json
{
  "jsonrpc": "2.0",
  "method": "agent.clear_session",
  "params": {
    "session_id": "default"
  },
  "id": "2"
}
```

**响应:**
```json
{
  "jsonrpc": "2.0",
  "result": {
    "success": true
  },
  "id": "2"
}
```

## 测试

### 测试 Gateway 启动

```bash
python test_gateway_startup.py
```

### 测试完整功能

```bash
python test_tui_basic.py
```

## 故障排查

### Gateway 无法启动

1. 检查日志：`logs/tui_gateway_crash.log`
2. 检查依赖：`pip install textual`
3. 检查配置：`config.yaml`

### TUI 显示异常

1. 确保终端支持 UTF-8
2. 确保终端尺寸足够（至少 80x24）
3. 尝试调整终端颜色方案

### 通信超时

1. 检查 Gateway 是否正常运行
2. 检查 stderr 输出
3. 增加超时时间（修改 `gateway_client.py` 中的 `timeout` 参数）

## 开发指南

### 添加新的 Widget

1. 在 `TUI/widgets/` 创建新文件
2. 继承 Textual 的 Widget 类
3. 在 `TUI/widgets/__init__.py` 中导出
4. 在 `TUI/app.py` 中使用

### 添加新的事件类型

1. 在 `gateway_entry.py` 中发送事件：
   ```python
   write_json({
       "jsonrpc": "2.0",
       "method": "event",
       "params": {
           "type": "your.event.type",
           "payload": {...}
       }
   })
   ```

2. 在 `app.py` 中注册处理器：
   ```python
   self.gateway.on_event("your.event.type", self._on_your_event)
   ```

### 添加新的 RPC 方法

1. 在 `gateway_entry.py` 的 `dispatch` 函数中添加处理逻辑
2. 在 `gateway_client.py` 中调用：
   ```python
   result = client.call("your.method", {"param": "value"})
   ```

## 性能优化

1. **异步处理**: Gateway 使用 asyncio 处理 Agent 逻辑
2. **事件驱动**: TUI 通过事件更新 UI，避免轮询
3. **缓冲输出**: 使用 `bufsize=1` 确保实时通信

## 已知限制

1. 暂不支持多会话切换（计划中）
2. 暂不支持历史记录搜索（计划中）
3. 暂不支持自定义主题（计划中）

## 未来计划

- [ ] 多会话管理
- [ ] 历史记录搜索
- [ ] 自定义主题
- [ ] 快捷命令（/help, /clear, /exit）
- [ ] 文件上传支持
- [ ] 图片预览支持
- [ ] 语音输入支持（集成 ACP）

## 参考资料

- [Textual 文档](https://textual.textualize.io/)
- [JSON-RPC 2.0 规范](https://www.jsonrpc.org/specification)
- [hermes-agent TUI 实现](https://github.com/anthropics/hermes-agent)
