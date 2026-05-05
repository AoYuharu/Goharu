# Gateway 系统设计文档

## 架构概览

本 Gateway 系统参考 hermes-agent 的设计，实现了多平台消息路由与会话管理。

```
┌─────────────────────────────────────────────────────────────┐
│                      外部消息平台                              │
│  QQ │ Telegram │ Discord │ ...                               │
└──────┬──────────┬─────────┬──────────────────────────────────┘
       │          │         │
       ▼          ▼         ▼
┌─────────────────────────────────────────────────────────────┐
│                   Platform Adapters                          │
│  QQAdapter │ TelegramAdapter │ ...                          │
│  (继承 BasePlatformAdapter)                                  │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                  AdapterManager                              │
│  - 适配器生命周期管理                                          │
│  - 适配器创建和连接                                            │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                  MessageRouter                               │
│  - 消息路由和分发                                              │
│  - 用户鉴权和命令处理                                          │
│  - 会话状态管理                                                │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                   AgentBridge                                │
│  - Gateway 和 Agent 的桥接                                    │
│  - 消息格式转换                                                │
│  - 会话上下文构建                                              │
└──────────────────────┬──────────────────────────────────────┘
                       │
       ┌───────────────┼───────────────┐
       ▼               ▼               ▼
┌─────────────┐ ┌─────────────┐ ┌─────────────┐
│SessionStore │ │   Agent     │ │   Memory    │
│  会话存储    │ │   AI处理    │ │   记忆管理   │
└─────────────┘ └─────────────┘ └─────────────┘
```

## 核心组件

### 1. GatewayRunner (gateway_runner.py)
**职责**: 总协调器，负责初始化和协调各组件

**核心方法**:
- `start()`: 初始化所有组件并启动 Gateway
- `stop()`: 停止所有组件
- `run_forever()`: 持续运行

**组件协调**:
1. 初始化 SessionStore
2. 初始化 Agent 组件（Actor, Reflector, MemoryManager）
3. 初始化 AgentBridge
4. 初始化 MessageRouter
5. 初始化 AdapterManager 并启动所有适配器

### 2. AdapterManager (adapter_manager.py)
**职责**: 管理平台适配器的生命周期

**核心方法**:
- `start_all()`: 启动所有已启用的平台适配器
- `stop_all()`: 停止所有适配器
- `get_connected_platforms()`: 获取已连接的平台列表
- `_create_adapter()`: 创建平台适配器实例

**设计优势**:
- 集中管理适配器创建逻辑
- 统一的启动和停止流程
- 易于添加新平台支持

### 3. MessageRouter (message_router.py)
**职责**: 消息路由和分发逻辑

**核心方法**:
- `route_message()`: 消息路由入口
- `_is_user_authorized()`: 用户鉴权
- `_handle_command()`: 命令处理
- `_handle_busy_session()`: 处理忙碌的会话

**路由流程**:
1. 用户鉴权
2. 命令拦截 (/help, /new, /reset, /status)
3. 获取/创建 Session
4. 检查会话忙碌状态
5. 调用 MessageProcessor（AgentBridge）

**设计优势**:
- 单一职责：只负责路由逻辑
- 易于扩展鉴权和命令系统
- 与 Agent 处理逻辑解耦

### 4. AgentBridge (agent_bridge.py)
**职责**: 连接 Gateway 和现有的 Agent 系统

**核心方法**:
- `process_message()`: 处理消息
- `_build_session_context()`: 构建会话上下文

**设计优势**:
- 隔离 Gateway 和 Agent 的实现细节
- 易于适配不同的 Agent 实现
- 统一的消息格式转换

### 5. SessionStore (session.py)
**职责**: 会话存储与管理

**核心类**:
- `SessionEntry`: 会话条目
- `SessionContext`: 会话上下文
- `SessionStore`: 会话存储管理器

**核心函数**:
- `build_session_key()`: 生成唯一的 session_key
- `is_shared_session()`: 判断是否为多用户共享会话
- `build_session_context_prompt()`: 生成注入到 Agent 的系统提示

**Session Key 规则**:
- DM: `agent:main:{platform}:dm:{chat_id}`
- Group (按用户隔离): `agent:main:{platform}:group:{chat_id}:{user_id}`
- Group (共享): `agent:main:{platform}:group:{chat_id}`

### 6. BasePlatformAdapter (platforms/base.py)
**职责**: 定义统一的平台接口

**抽象方法**（子类必须实现）:
- `connect()`: 连接到平台
- `disconnect()`: 断开连接
- `send()`: 发送消息

**可选方法**（子类可覆盖）:
- `send_typing()`: 发送"正在输入"状态
- `edit_message()`: 编辑消息
- `delete_message()`: 删除消息
- `send_image()`: 发送图片
- `send_voice()`: 发送语音

### 7. QQAdapter (platforms/qq_adapter.py)
**职责**: QQ 平台的具体实现

**功能**:
- WebSocket 连接接收消息
- REST API 发送消息
- 支持私聊和群聊
- 支持 Markdown 格式
- 自动心跳保活

## 设计原则

### 1. 单一职责原则
每个模块只负责一个明确的功能：
- `GatewayRunner`: 协调
- `AdapterManager`: 适配器管理
- `MessageRouter`: 路由
- `AgentBridge`: 桥接
- `SessionStore`: 会话存储

### 2. 低耦合
- 各组件通过接口交互，不直接依赖实现
- `MessageRouter` 不知道 `Agent` 的存在，只知道 `MessageProcessor` 接口
- `AdapterManager` 不知道消息如何处理，只负责适配器生命周期

### 3. 高内聚
- 相关功能集中在同一模块
- 命令处理逻辑集中在 `MessageRouter`
- 适配器创建逻辑集中在 `AdapterManager`

### 4. 易于扩展
- 添加新平台：只需在 `AdapterManager._create_adapter()` 中添加一行
- 添加新命令：只需在 `MessageRouter._handle_command()` 中添加分支
- 替换 Agent 实现：只需修改 `AgentBridge`

## 文件结构

```
Gateway/
├── __init__.py                 # 模块导出
├── gateway_runner.py           # Gateway 运行器（协调器）
├── adapter_manager.py          # 适配器管理器
├── message_router.py           # 消息路由器
├── agent_bridge.py             # Agent 桥接器
├── session.py                  # 会话管理
├── run_gateway.py              # 启动脚本
├── config_example.yaml         # 配置示例
├── README.md                   # 文档
└── platforms/
    ├── __init__.py             # 平台模块导出
    ├── base.py                 # 平台适配器基类
    └── qq_adapter.py           # QQ 适配器
```

## 配置示例

在 `config.yaml` 中添加：

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
      client_secret: "your-client-secret"
      markdown_support: true
```

## 使用方法

### 1. 启动 Gateway

```bash
# 方式 1: 使用启动脚本
python Gateway/run_gateway.py

# 方式 2: 在代码中使用
from Gateway import GatewayRunner
import asyncio

async def main():
    gateway = GatewayRunner()
    await gateway.start()
    await gateway.run_forever()

asyncio.run(main())
```

### 2. 添加新平台适配器

```python
# 1. 创建适配器类
from Gateway.platforms.base import BasePlatformAdapter, Platform

class MyPlatformAdapter(BasePlatformAdapter):
    def __init__(self, config):
        super().__init__(config, Platform.LOCAL)

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

# 2. 在 AdapterManager._create_adapter() 中注册
elif platform_name_lower == "myplatform":
    return MyPlatformAdapter(platform_config)
```

### 3. 添加新命令

```python
# 在 MessageRouter._handle_command() 中添加
elif command == "/mycommand":
    return "My command response"
```

### 4. 测试

```bash
python test_gateway.py
```

## 扩展建议

1. **新增平台**: 继承 `BasePlatformAdapter`，在 `AdapterManager` 中注册
2. **自定义路由**: 修改 `MessageRouter.route_message` 逻辑
3. **鉴权系统**: 扩展 `MessageRouter._is_user_authorized` 方法
4. **会话策略**: 调整 `SessionStore` 的重置配置
5. **Agent 替换**: 修改 `AgentBridge` 的实现

## 优势总结

1. **模块化**: 每个文件职责清晰，代码量适中（100-200行）
2. **低耦合**: 组件之间通过接口交互，易于替换和测试
3. **易扩展**: 添加新功能只需修改对应模块
4. **易维护**: 代码结构清晰，易于理解和修改
5. **可测试**: 每个组件可以独立测试
