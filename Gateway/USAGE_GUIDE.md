# Gateway 使用指南

## 连接成功！

QQ Bot Gateway 已经成功连接并测试通过。

## 快速开始

### 1. 配置

在 `config.yaml` 中配置 QQ Bot 凭证：

```yaml
gateway:
  session:
    storage_path: ./runtime_memory/gateway/sessions.json
    group_sessions_per_user: true
    thread_sessions_per_user: false
    reset_mode: idle
    reset_at_hour: 4
    reset_idle_minutes: 1440

  platforms:
    qq:
      enabled: true
      app_id: "your-app-id"
      bot_token: "your-bot-token"  # 使用 QQ 开放平台的 client_secret 作为 bot_token
      markdown_support: true
```

**重要**:
- `bot_token` 字段使用 QQ 开放平台提供的 `client_secret`
- 不需要通过 API 动态获取 access token

### 2. 启动 Gateway

#### 方式 1: 使用启动脚本

```bash
python Gateway/run_gateway.py
```

#### 方式 2: 在代码中使用

```python
import asyncio
from Gateway import GatewayRunner

async def main():
    gateway = GatewayRunner()
    await gateway.start()

    # Gateway 会持续运行，监听消息
    await gateway.run_forever()

asyncio.run(main())
```

#### 方式 3: 集成到现有应用

```python
from Gateway import GatewayRunner

# 在应用启动时
gateway = GatewayRunner()
await gateway.start()

# 在应用关闭时
await gateway.stop()
```

### 3. 测试连接

运行测试脚本验证连接：

```bash
# 测试基本连接
python test_qq_connection.py

# 测试完整 Gateway 功能
python test_gateway_full.py
```

## 功能特性

### 1. 消息接收

Gateway 会自动接收来自 QQ 的消息：
- **私聊消息**: 用户直接发送给机器人的消息
- **群聊@消息**: 群聊中 @ 机器人的消息

### 2. 会话管理

每个用户/群聊都有独立的会话：

- **私聊会话**: `agent:main:qqbot:dm:{user_id}`
- **群聊会话（隔离）**: `agent:main:qqbot:group:{group_id}:{user_id}`
- **群聊会话（共享）**: `agent:main:qqbot:group:{group_id}`

会话配置：
- `group_sessions_per_user: true` - 群聊中每个用户独立会话
- `group_sessions_per_user: false` - 群聊中所有用户共享会话

### 3. 会话重置策略

支持多种会话重置策略：

- `daily`: 每日指定时间重置（`reset_at_hour`）
- `idle`: 空闲超时重置（`reset_idle_minutes`）
- `both`: 任一条件满足即重置
- `none`: 不自动重置

### 4. 命令系统

用户可以使用以下命令：

- `/help` - 显示帮助信息
- `/new` 或 `/reset` - 重置当前会话
- `/status` - 查看会话状态

### 5. Agent 集成

Gateway 通过 `AgentBridge` 与现有的 Agent 系统集成：

1. 接收用户消息
2. 路由到对应的会话
3. 调用 Agent 处理消息
4. 返回 Agent 的响应
5. 发送响应到 QQ

## 架构说明

### 模块职责

```
Gateway/
├── gateway_runner.py      # 总协调器，初始化和协调所有组件
├── adapter_manager.py     # 适配器生命周期管理
├── message_router.py      # 消息路由、命令处理、鉴权
├── agent_bridge.py        # Gateway 和 Agent 的桥接
├── session.py             # 会话存储和管理
└── platforms/
    ├── base.py            # 平台适配器基类
    └── qq_adapter.py      # QQ 平台实现
```

### 消息流程

```
QQ 平台
  ↓ (WebSocket)
QQAdapter (接收消息)
  ↓
MessageRouter (路由、鉴权、命令处理)
  ↓
AgentBridge (格式转换、会话上下文)
  ↓
Agent (ActorAgent + ReflectionAgent)
  ↓
AgentBridge (响应处理)
  ↓
MessageRouter
  ↓
QQAdapter (发送消息)
  ↓ (REST API)
QQ 平台
```

## 认证说明

### QQ Bot 认证方式

QQ Bot API 使用以下认证方式：

1. **Bot Token**: 直接使用 `client_secret` 作为 token
2. **Authorization Header**: `Bot {app_id}.{bot_token}`
3. **WebSocket 连接**: 使用 Identify 消息进行鉴权

**不需要**通过 `/app/getAppAccessToken` 端点获取 access token。

### 获取凭证

1. 访问 [QQ 开放平台](https://q.qq.com/)
2. 创建机器人应用
3. 获取 `app_id` 和 `client_secret`
4. 将 `client_secret` 作为 `bot_token` 配置

## 扩展指南

### 添加新平台

1. 创建新的适配器类：

```python
from Gateway.platforms.base import BasePlatformAdapter, Platform

class TelegramAdapter(BasePlatformAdapter):
    def __init__(self, config):
        super().__init__(config, Platform.TELEGRAM)

    async def connect(self) -> bool:
        # 实现连接逻辑
        self._mark_connected()
        return True

    async def disconnect(self) -> None:
        # 实现断开逻辑
        self._mark_disconnected()

    async def send(self, chat_id: str, content: str, **kwargs):
        # 实现发送逻辑
        return SendResult(success=True, message_id="msg_123")
```

2. 在 `AdapterManager._create_adapter()` 中注册：

```python
elif platform_name_lower == "telegram":
    return TelegramAdapter(platform_config)
```

3. 在 `config.yaml` 中配置：

```yaml
gateway:
  platforms:
    telegram:
      enabled: true
      bot_token: "your-telegram-token"
```

### 添加新命令

在 `MessageRouter._handle_command()` 中添加：

```python
elif command == "/stats":
    # 实现统计功能
    return "Session statistics: ..."
```

## 故障排查

### 连接失败

如果连接失败，检查：

1. `app_id` 和 `bot_token` 是否正确
2. 机器人是否已在 QQ 开放平台启用
3. 网络连接是否正常
4. 防火墙是否允许 WebSocket 连接

### 消息接收不到

检查：

1. 机器人是否已添加到群聊/好友
2. Intent 配置是否正确（私聊、群聊权限）
3. WebSocket 连接是否保持活跃
4. 心跳是否正常

### 会话问题

检查：

1. `storage_path` 目录是否可写
2. 会话重置策略是否符合预期
3. 会话 key 映射是否正确

## 测试结果

✅ **连接测试**: 通过
- QQ Bot WebSocket 连接成功
- 心跳保活正常
- 认证通过

✅ **模块测试**: 通过
- Session 映射测试
- SessionStore 测试
- 消息处理测试
- Gateway 初始化测试

✅ **集成测试**: 通过
- Gateway 启动成功
- 适配器连接成功
- Agent 桥接正常

## 下一步

1. **实际使用**: 将机器人添加到 QQ 群聊或好友，发送消息测试
2. **监控日志**: 观察消息接收和处理流程
3. **性能优化**: 根据实际使用情况调整配置
4. **扩展平台**: 添加其他平台适配器（Telegram、Discord 等）

## 参考资料

- [QQ 机器人官方文档](https://bot.q.qq.com/wiki/)
- [hermes-agent Gateway 架构](D:\MyProject\Programming\hermes-agent\analyseDocs\Gateway_Session_Routing_Architecture.md)
- [Gateway 实现总结](./IMPLEMENTATION_SUMMARY.md)
