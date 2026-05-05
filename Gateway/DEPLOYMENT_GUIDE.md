# QQ Bot 完整智能体系统部署指南

## 部署状态

✅ **完整项目已部署到服务器**

- 服务器: travelnote.online (159.75.26.204)
- 部署路径: /root/TableHelper
- Bot 名称: NyaNya-测试中
- Bot ID: 14004041982952838788

## 系统架构

```
QQ Bot Gateway System
├── Gateway Layer (消息接收/发送)
│   ├── QQ Adapter (WebSocket + REST API)
│   ├── Session Manager (会话管理)
│   └── Message Router (消息路由)
├── Agent Layer (智能对话)
│   ├── Actor Agent (执行动作)
│   ├── Reflection Agent (反思优化)
│   └── Summarizer Agent (记忆总结)
├── Memory Layer (记忆系统)
│   ├── Working Memory (短期记忆)
│   └── Long-term Memory (长期记忆)
└── Tools Layer (工具调用)
    ├── File Operations
    ├── Command Execution
    └── Agent Delegation
```

## 快速启动

### 1. 配置 API Key

```bash
ssh root@travelnote.online
cd /root/TableHelper

# 编辑 .env 文件，设置你的 MiniMax API key
nano .env
```

将 `your_minimax_api_key_here` 替换为你的实际 API key。

### 2. 启动 Gateway

```bash
# 使用启动脚本
chmod +x start_gateway.sh
./start_gateway.sh

# 或者直接运行
export ANTHROPIC_API_KEY=your_key
python3 run_gateway.py
```

### 3. 后台运行

```bash
# 使用 nohup 后台运行
nohup ./start_gateway.sh > gateway.log 2>&1 &

# 查看日志
tail -f gateway.log

# 查看进程
ps aux | grep run_gateway

# 停止服务
pkill -f run_gateway.py
```

## 配置说明

### config_server.yaml

关键配置项：

```yaml
model:
  large-language-model:
    provider: anthropic_compatible
    model: MiniMax-M2.7
    api_key_env: ANTHROPIC_API_KEY
    base_url: https://api.minimaxi.com/anthropic
    max_tokens: 1024
    temperature: 0.7

gateway:
  platforms:
    qq:
      enabled: true
      app_id: "102839705"
      client_secret: "wOrKoInIoLsQyX6gGrS4gJxbGvbHyfN5"
      markdown_support: true
```

### 切换到其他 LLM 提供商

**使用 OpenAI**:
```yaml
model:
  large-language-model:
    provider: openai
    model: gpt-4
    api_key_env: OPENAI_API_KEY
    base_url: https://api.openai.com/v1
```

**使用 Anthropic Claude**:
```yaml
model:
  large-language-model:
    provider: anthropic
    model: claude-3-5-sonnet-20241022
    api_key_env: ANTHROPIC_API_KEY
```

## 功能特性

### 1. 智能对话

Bot 现在可以：
- 理解自然语言问题
- 调用工具执行任务
- 记住对话历史
- 多轮对话推理

### 2. 会话管理

- **私聊**: 每个用户独立会话
- **群聊**: 可配置为共享会话或每人独立
- **会话重置**: 支持定时重置或空闲重置

### 3. 命令系统

内置命令（在消息路由器中实现）：
- `/help` - 显示帮助
- `/clear` - 清除当前会话
- `/status` - 查看 Bot 状态

### 4. 工具调用

Agent 可以调用的工具：
- 文件操作 (Read, Write, Edit)
- 命令执行 (run_cmd)
- 文件搜索 (Glob, Grep)
- 子 Agent 委托

## 测试方法

### 基础测试

1. **私聊测试**
   - 在 QQ 中搜索 "NyaNya-测试中"
   - 发送: "你好"
   - 应该收到智能回复

2. **群聊测试**
   - 将 Bot 添加到群
   - @ Bot: "@NyaNya-测试中 介绍一下你自己"
   - 应该收到智能回复

### 功能测试

1. **多轮对话**
   ```
   用户: 帮我写一个 Python 函数计算斐波那契数列
   Bot: [生成代码]
   用户: 能优化一下性能吗？
   Bot: [优化后的代码]
   ```

2. **记忆测试**
   ```
   用户: 我叫张三
   Bot: 好的，记住了
   [过一会儿]
   用户: 我叫什么名字？
   Bot: 你叫张三
   ```

## 监控和维护

### 查看日志

```bash
# Gateway 日志
tail -f gateway.log

# 系统日志
journalctl -f | grep python

# 错误日志
grep ERROR gateway.log
```

### 检查状态

```bash
# 查看进程
ps aux | grep run_gateway

# 查看网络连接
netstat -an | grep ESTABLISHED | grep python

# 查看资源占用
top -p $(pgrep -f run_gateway.py)
```

### 重启服务

```bash
# 停止
pkill -f run_gateway.py

# 等待进程完全退出
sleep 3

# 启动
nohup ./start_gateway.sh > gateway.log 2>&1 &
```

## 故障排查

### 问题 1: Bot 不回复消息

**检查**:
```bash
# 1. 检查进程是否运行
ps aux | grep run_gateway

# 2. 查看日志
tail -50 gateway.log

# 3. 检查 API key
grep ANTHROPIC_API_KEY .env
```

**解决**:
- 确保 API key 正确
- 检查网络连接
- 查看日志中的错误信息

### 问题 2: WebSocket 断开

**检查**:
```bash
# 查看日志中的 WebSocket 相关信息
grep "WebSocket\|ws\|Disconnected" gateway.log
```

**解决**:
- Gateway 会自动重连
- 如果持续断开，检查网络稳定性
- 检查 QQ 开放平台是否有限制

### 问题 3: API 调用失败

**检查**:
```bash
# 查看 API 相关错误
grep "API\|HTTP\|401\|403\|429" gateway.log
```

**解决**:
- 检查 API key 是否有效
- 检查 API 配额是否用完
- 检查 base_url 是否正确

## 性能优化

### 1. 调整并发数

编辑 `config_server.yaml`:
```yaml
agent_delegate:
  explore:
    max_concurrent: 3  # 增加并发数
  plan:
    max_concurrent: 2
```

### 2. 调整超时时间

```yaml
agent_delegate:
  timeout: 300  # 增加超时时间（秒）
```

### 3. 调整记忆保留

```yaml
memory:
  daily:
    retention_days: 7  # 减少保留天数
```

## 安全建议

1. **保护 API Key**
   ```bash
   chmod 600 .env
   ```

2. **限制命令执行**
   - 已启用命令安全检查
   - 危险命令会被拦截

3. **定期备份**
   ```bash
   # 备份会话数据
   tar -czf backup_$(date +%Y%m%d).tar.gz runtime_memory/
   ```

## 扩展功能

### 添加新平台

1. 创建新的适配器 (参考 `qq_adapter.py`)
2. 在 `config_server.yaml` 中配置
3. 在 `adapter_manager.py` 中注册

### 添加自定义工具

1. 在 `Tools/builtin/` 下创建新工具
2. 在 `config_server.yaml` 中注册
3. 重启 Gateway

### 自定义提示词

编辑 `prompts/actor/base.md` 来自定义 Bot 的行为和性格。

## 系统要求

- Python 3.10+
- 2GB+ RAM
- 稳定的网络连接
- 有效的 LLM API key

## 相关文档

- QQ Bot API: https://bot.q.qq.com/wiki/
- Gateway 架构: Gateway/QQ_BOT_DIAGNOSIS.md
- 项目说明: CLAUDE.md

---

**部署时间**: 2026-05-04
**版本**: v1.0
**状态**: ✅ 生产就绪
