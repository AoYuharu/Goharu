# ACP 快速启动指南

## 🚀 快速开始（3 步）

### 1. 安装依赖

```bash
pip install aiohttp requests
```

或者安装完整依赖：

```bash
pip install -r requirements.txt
```

### 2. 启动 Gateway（包含 ACP 服务器）

```bash
python Gateway/run_gateway.py
```

你应该看到：

```
[Gateway] Starting...
[Gateway] SessionStore initialized
[Gateway] Agent components initialized
[Gateway] AgentBridge initialized
[Gateway] MessageRouter initialized
[Gateway] ACP HTTP Server started on http://127.0.0.1:8765
[Gateway] Started with 1 platform(s)
[Gateway] Running... Press Ctrl+C to stop
```

### 3. 测试通信

**方式 1：运行测试脚本**

```bash
python test_acp_system.py
```

**方式 2：使用交互式客户端**

```bash
python acp_client.py
```

**方式 3：使用 Python 代码**

```python
from acp_client import ACPClient

client = ACPClient()
result = client.send_message("你好，请介绍一下你自己")
print(result.get("response"))
```

**方式 4：使用 curl（命令行）**

```bash
curl -X POST http://127.0.0.1:8765/api/message \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test","user_name":"Test","text":"你好"}'
```

## 📋 完整示例

### Claude Code 与 TableHelper 对话

```python
# 在 Claude Code 中执行
from Tools.tablehelper_comm import ask_tablehelper

# 询问项目信息
response = ask_tablehelper("项目中有哪些可用的工具？")
print(response)

# 询问代码问题
response = ask_tablehelper("main.py 的主要功能是什么？")
print(response)

# 请求帮助
response = ask_tablehelper("如何使用 Read 工具读取文件？")
print(response)
```

### 多轮对话

```python
from acp_client import ACPClient

client = ACPClient()

# 第一轮
result1 = client.send_message("请列出项目中的所有工具")
print(f"Agent: {result1.get('response')}")

# 第二轮（基于上下文）
result2 = client.send_message("Read 工具的具体用法是什么？")
print(f"Agent: {result2.get('response')}")
```

## 🔧 配置

在 `config.yaml` 中修改 ACP 配置：

```yaml
gateway:
  acp:
    enabled: true      # 是否启用 ACP 服务器
    host: "127.0.0.1"  # 监听地址（127.0.0.1 仅本地，0.0.0.0 允许外部访问）
    port: 8765         # 监听端口
```

## 🐛 故障排查

### 问题 1：无法连接到服务器

**症状：**
```
✗ Cannot connect to server: Connection refused
```

**解决方案：**
1. 确认 Gateway 已启动：`python Gateway/run_gateway.py`
2. 检查端口是否被占用：`netstat -ano | findstr 8765`（Windows）
3. 检查防火墙设置

### 问题 2：请求超时

**症状：**
```
✗ Request timed out after 60 seconds
```

**解决方案：**
1. 增加超时时间：
   ```python
   result = client.send_message("复杂问题", timeout=300)
   ```
2. 检查 Agent 是否正常工作
3. 查看 Gateway 日志

### 问题 3：依赖缺失

**症状：**
```
ModuleNotFoundError: No module named 'aiohttp'
```

**解决方案：**
```bash
pip install aiohttp requests
```

### 问题 4：端口已被占用

**症状：**
```
OSError: [Errno 48] Address already in use
```

**解决方案：**
1. 修改 `config.yaml` 中的端口号
2. 或者杀死占用端口的进程：
   ```bash
   # Windows
   netstat -ano | findstr 8765
   taskkill /PID <PID> /F
   ```

## 📊 API 端点

| 端点 | 方法 | 描述 |
|------|------|------|
| `/api/message` | POST | 发送消息到 Agent |
| `/api/health` | GET | 健康检查 |
| `/api/status` | GET | 获取服务器状态 |

## 🎯 使用场景

### 场景 1：Claude Code 询问项目信息

```python
# Claude Code 想了解项目结构
response = ask_tablehelper("请介绍一下这个项目的架构")
```

### 场景 2：多 Agent 协作

```python
# Agent A 需要 TableHelper 的帮助
client = ACPClient()
result = client.send_message("请分析 main.py 的代码结构")
analysis = result.get("response")

# Agent A 使用分析结果继续工作
print(f"根据 TableHelper 的分析：{analysis}")
```

### 场景 3：自动化测试

```python
# 自动化脚本测试 Agent 功能
test_cases = [
    "列出所有工具",
    "Read 工具的用法",
    "如何使用 Grep 搜索文件",
]

for question in test_cases:
    result = client.send_message(question)
    assert result.get("success"), f"Failed: {question}"
```

## 📚 更多文档

- 详细使用指南：`docs/acp_usage.md`
- API 文档：查看 `Gateway/http_server.py`
- 客户端代码：`acp_client.py`
- 通信工具：`Tools/tablehelper_comm.py`

## ✅ 验证安装

运行完整测试：

```bash
python test_acp_system.py
```

预期输出：

```
Test Summary
============================================================
✓ PASS   Server Health
✓ PASS   Server Status
✓ PASS   Simple Message
✓ PASS   Complex Question
✓ PASS   Python Client

Total: 5/5 tests passed

🎉 All tests passed!
```

## 🎉 成功！

现在你可以：
1. ✅ 从 Claude Code 向 TableHelper Agent 发送消息
2. ✅ 获取 Agent 的回答
3. ✅ 实现多 Agent 协作
4. ✅ 构建自动化工作流

开始使用吧！🚀
