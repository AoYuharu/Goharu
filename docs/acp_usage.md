# ACP (Agent Communication Protocol) 使用指南

## 概述

ACP 是一个基于 HTTP API 的智能体通信协议，允许外部 Agent（如 Claude Code）与 TableHelper Agent 进行通信。

## 架构

```
Claude Code (外部 Agent)
    ↓ HTTP POST
ACP HTTP Server (127.0.0.1:8765)
    ↓
MessageRouter
    ↓
AgentBridge
    ↓
TableHelper Agent (ActorAgent + ReflectionAgent)
```

## 快速开始

### 1. 启动 Gateway（包含 ACP 服务器）

```bash
python Gateway/run_gateway.py
```

服务器将在 `http://127.0.0.1:8765` 启动。

### 2. 使用 Python 客户端

```python
from acp_client import ACPClient

client = ACPClient()

# 发送消息
result = client.send_message("你好，请介绍一下你自己")
if result.get("success"):
    print(result.get("response"))
```

### 3. 使用命令行客户端

```bash
python acp_client.py
```

进入交互模式，可以直接与 TableHelper Agent 对话。

### 4. 使用工具函数（推荐给 Claude Code）

```python
from Tools.tablehelper_comm import ask_tablehelper, check_tablehelper_status

# 检查状态
status = check_tablehelper_status()
print(status)

# 发送问题
response = ask_tablehelper("E:\TableHelper 项目有哪些工具？")
print(response)
```

## API 接口

### POST /api/message

发送消息到 Agent。

**请求体：**
```json
{
  "user_id": "claude_code",
  "user_name": "Claude Code",
  "text": "你好",
  "chat_type": "private"
}
```

**响应：**
```json
{
  "success": true,
  "response": "你好！我是 TableHelper Agent...",
  "user_id": "claude_code"
}
```

### GET /api/health

健康检查。

**响应：**
```json
{
  "status": "ok"
}
```

### GET /api/status

获取服务器状态。

**响应：**
```json
{
  "status": "running",
  "adapter": "acp",
  "connected": true,
  "host": "127.0.0.1",
  "port": 8765
}
```

## 配置

在 `config.yaml` 中配置 ACP 服务器：

```yaml
gateway:
  acp:
    enabled: true
    host: "127.0.0.1"
    port: 8765
```

## 使用场景

### 场景 1：Claude Code 询问 TableHelper

```python
# Claude Code 可以通过 run_cmd 调用
import subprocess
result = subprocess.run([
    "python", "-c",
    "from Tools.tablehelper_comm import ask_tablehelper; print(ask_tablehelper('项目有哪些工具？'))"
], capture_output=True, text=True)
print(result.stdout)
```

### 场景 2：多 Agent 协作

```python
# Agent A 向 TableHelper 请求信息
from acp_client import ACPClient
client = ACPClient()

# 获取项目信息
result = client.send_message("请列出项目中的所有工具")
tools_info = result.get("response")

# Agent A 使用这些信息继续工作
print(f"TableHelper 提供的工具：{tools_info}")
```

### 场景 3：远程调用

```python
# 可以从其他机器访问（需要修改 host 配置）
client = ACPClient(base_url="http://192.168.1.100:8765")
result = client.send_message("你好")
```

## 安全注意事项

1. **默认只监听本地**：`127.0.0.1` 只允许本机访问
2. **无认证机制**：当前版本没有身份验证，仅用于本地开发
3. **生产环境建议**：
   - 添加 API Key 认证
   - 使用 HTTPS
   - 添加速率限制
   - 限制访问 IP

## 故障排查

### 问题 1：连接失败

```bash
# 检查 Gateway 是否运行
curl http://127.0.0.1:8765/api/health

# 如果失败，启动 Gateway
python Gateway/run_gateway.py
```

### 问题 2：超时

```python
# 增加超时时间
result = client.send_message("复杂问题", timeout=600)
```

### 问题 3：依赖缺失

```bash
# 安装依赖
pip install aiohttp requests
```

## 扩展

### 添加新的 API 端点

在 `Gateway/http_server.py` 中添加：

```python
async def handle_custom(self, request: web.Request) -> web.Response:
    # 自定义处理逻辑
    return web.json_response({"result": "ok"})

# 注册路由
self.app.router.add_get("/api/custom", self.handle_custom)
```

### 支持流式响应

```python
async def handle_stream(self, request: web.Request) -> web.StreamResponse:
    response = web.StreamResponse()
    await response.prepare(request)

    # 流式发送数据
    for chunk in agent_response_stream:
        await response.write(chunk.encode())

    await response.write_eof()
    return response
```

## 测试

运行测试脚本：

```bash
python Tools/tablehelper_comm.py
```

预期输出：
```
Testing TableHelper communication...

1. Checking server status...
{
  "status": "running",
  "adapter": "acp",
  "connected": true,
  "host": "127.0.0.1",
  "port": 8765
}

2. Sending test message...
Response:
你好！我是 TableHelper Agent...
```
