# 第三方工具接入指南

## 📋 当前工具调用系统

### 系统架构

```
配置文件 (config.yaml)
    ↓
加载器 (Tools/loader.py)
    ↓
注册表 (Tools/registry.py) ← 全局单例
    ↓
运行时 (Tools/runtime.py)
    ↓
Agent 调用工具
```

### 核心组件

1. **ToolRegistry** (`Tools/registry.py`)
   - 全局工具注册表（单例）
   - 管理所有工具的定义和执行
   - 提供 `register()` 方法注册工具

2. **Loader** (`Tools/loader.py`)
   - 动态加载工具模块
   - 从 `config.yaml` 读取模块列表
   - 自动导入并注册工具

3. **Runtime** (`Tools/runtime.py`)
   - 工具执行抽象层
   - 支持 `InProcessToolRuntime`（当前使用）
   - 支持 `MCPToolRuntime`（MCP协议）

---

## 🔧 当前配置

### config.yaml

```yaml
tools:
  runtime: in_process  # 使用进程内执行
  builtin_modules:
    - Tools.builtin.core_tools        # 核心工具（run_cmd）
    - Tools.builtin.file_tools        # 文件工具（Read/Write/Edit/Grep）
    - Tools.builtin.glob_tool         # 文件匹配（Glob）
    - Tools.builtin.agent_delegate    # 子agent委托
```

### 已注册的工具

| 工具名 | 模块 | 功能 |
|--------|------|------|
| `run_cmd` | core_tools | 执行shell命令 |
| `Read` | file_tools | 读取文件 |
| `Write` | file_tools | 创建文件 |
| `Edit` | file_tools | 修改文件 |
| `Grep` | file_tools | 搜索文件内容 |
| `Glob` | glob_tool | 文件模式匹配 |
| `AgentDelegate` | agent_delegate | 创建子agent |

---

## 🚀 接入第三方工具（3种方法）

### 方法1：创建新的内置模块（推荐）

**适用场景**：你的工具是Python函数，想集成到项目中

#### 步骤1：创建工具模块

创建文件 `Tools/builtin/my_tools.py`：

```python
from Tools.registry import registry

def my_tool(param1: str, param2: int) -> str:
    """
    你的工具实现

    Args:
        param1: 参数1说明
        param2: 参数2说明

    Returns:
        工具执行结果（字符串）
    """
    # 你的逻辑
    result = f"处理 {param1} 和 {param2}"
    return result

# 注册工具
registry.register(
    name="MyTool",
    description="这是我的自定义工具，用于...",
    arguments_schema={
        "type": "object",
        "properties": {
            "param1": {
                "type": "string",
                "description": "参数1的说明"
            },
            "param2": {
                "type": "integer",
                "description": "参数2的说明"
            }
        },
        "required": ["param1", "param2"]
    },
    handler=my_tool,
    is_async=False  # 如果是异步函数，设为True
)
```

#### 步骤2：配置加载

编辑 `config.yaml`，添加你的模块：

```yaml
tools:
  runtime: in_process
  builtin_modules:
    - Tools.builtin.core_tools
    - Tools.builtin.file_tools
    - Tools.builtin.glob_tool
    - Tools.builtin.agent_delegate
    - Tools.builtin.my_tools  # 添加你的模块
```

#### 步骤3：测试

```bash
python main.py
```

然后测试：
```
> 调用 MyTool，参数 param1="test", param2=123
```

---

### 方法2：异步工具

**适用场景**：你的工具需要异步执行（网络请求、IO操作等）

#### 示例：异步HTTP请求工具

```python
from Tools.registry import registry
import aiohttp

async def fetch_url(url: str) -> str:
    """
    异步获取URL内容

    Args:
        url: 要获取的URL

    Returns:
        网页内容
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return await response.text()

# 注册异步工具
registry.register(
    name="FetchURL",
    description="异步获取URL内容",
    arguments_schema={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "要获取的URL"
            }
        },
        "required": ["url"]
    },
    handler=fetch_url,
    is_async=True  # 标记为异步
)
```

---

### 方法3：外部工具包装

**适用场景**：你想调用外部程序或API

#### 示例：调用外部API

```python
from Tools.registry import registry
import requests
import json

def call_external_api(query: str) -> str:
    """
    调用外部API

    Args:
        query: 查询参数

    Returns:
        API返回结果（JSON字符串）
    """
    try:
        response = requests.post(
            "https://api.example.com/query",
            json={"query": query},
            timeout=30
        )
        response.raise_for_status()
        return json.dumps(response.json(), ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

registry.register(
    name="ExternalAPI",
    description="调用外部API进行查询",
    arguments_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "查询内容"
            }
        },
        "required": ["query"]
    },
    handler=call_external_api
)
```

---

## 📝 工具定义规范

### 完整的注册参数

```python
registry.register(
    name="ToolName",           # 工具名（必需）
    description="工具描述",     # 工具说明（必需）
    arguments_schema={...},    # 参数schema（必需）
    handler=function,          # 处理函数（必需）
    group="category",          # 工具分组（可选）
    is_async=False            # 是否异步（可选）
)
```

### Schema 格式（JSON Schema）

```python
arguments_schema = {
    "type": "object",
    "properties": {
        "param_name": {
            "type": "string",        # string/integer/number/boolean/object/array
            "description": "参数说明",
            "enum": ["a", "b"],      # 可选：枚举值
            "default": "value"       # 可选：默认值
        }
    },
    "required": ["param_name"]  # 必需参数列表
}
```

### 支持的参数类型

| JSON Schema 类型 | Python 类型 | 示例 |
|-----------------|-------------|------|
| `string` | `str` | `"hello"` |
| `integer` | `int` | `123` |
| `number` | `int`, `float` | `3.14` |
| `boolean` | `bool` | `True` |
| `object` | `dict` | `{"key": "value"}` |
| `array` | `list` | `[1, 2, 3]` |
| `null` | `None` | `None` |

---

## 🎯 完整示例：天气查询工具

### 创建文件 `Tools/builtin/weather_tools.py`

```python
from Tools.registry import registry
import json

def get_weather(city: str, unit: str = "celsius") -> str:
    """
    获取城市天气（示例）

    Args:
        city: 城市名称
        unit: 温度单位（celsius/fahrenheit）

    Returns:
        天气信息（JSON字符串）
    """
    # 这里是模拟数据，实际应该调用天气API
    weather_data = {
        "city": city,
        "temperature": 25 if unit == "celsius" else 77,
        "unit": unit,
        "condition": "晴天",
        "humidity": "60%"
    }

    return json.dumps(weather_data, ensure_ascii=False, indent=2)

# 注册工具
registry.register(
    name="GetWeather",
    description="获取指定城市的天气信息",
    arguments_schema={
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "城市名称（如：北京、上海）"
            },
            "unit": {
                "type": "string",
                "description": "温度单位",
                "enum": ["celsius", "fahrenheit"],
                "default": "celsius"
            }
        },
        "required": ["city"]
    },
    handler=get_weather,
    group="weather"  # 工具分组
)
```

### 配置 `config.yaml`

```yaml
tools:
  runtime: in_process
  builtin_modules:
    - Tools.builtin.core_tools
    - Tools.builtin.file_tools
    - Tools.builtin.glob_tool
    - Tools.builtin.agent_delegate
    - Tools.builtin.weather_tools  # 添加天气工具
```

### 使用

```bash
python main.py
```

对话测试：
```
> 查询北京的天气
```

模型会调用：
```json
{"tool": "GetWeather", "arguments": {"city": "北京", "unit": "celsius"}}
```

---

## 🔒 安全注意事项

### 1. 输入验证

```python
def my_tool(param: str) -> str:
    # ✅ 验证输入
    if not param or len(param) > 1000:
        return json.dumps({"error": "参数无效"})

    # 你的逻辑
    return result
```

### 2. 错误处理

```python
def my_tool(param: str) -> str:
    try:
        # 你的逻辑
        result = do_something(param)
        return json.dumps({"result": result})
    except Exception as e:
        # ✅ 返回错误信息
        return json.dumps({"error": str(e)})
```

### 3. 超时控制

```python
import asyncio

async def my_async_tool(param: str) -> str:
    try:
        # ✅ 设置超时
        result = await asyncio.wait_for(
            long_running_task(param),
            timeout=30.0
        )
        return json.dumps({"result": result})
    except asyncio.TimeoutError:
        return json.dumps({"error": "操作超时"})
```

### 4. 资源清理

```python
def my_tool(file_path: str) -> str:
    file = None
    try:
        file = open(file_path, 'r')
        content = file.read()
        return content
    finally:
        # ✅ 确保资源释放
        if file:
            file.close()
```

---

## 🎨 高级特性

### 1. 工具分组

```python
# 按功能分组
registry.register(
    name="Tool1",
    group="database",  # 数据库工具组
    ...
)

registry.register(
    name="Tool2",
    group="network",   # 网络工具组
    ...
)

# 获取特定组的工具
db_tools = registry.list_entries(group="database")
```

### 2. 动态工具注册

```python
# 在运行时动态注册工具
def register_custom_tool(name, handler):
    registry.register(
        name=name,
        description=f"动态注册的工具: {name}",
        arguments_schema={
            "type": "object",
            "properties": {}
        },
        handler=handler
    )
```

### 3. 工具权限控制

```python
def restricted_tool(param: str) -> str:
    # 检查权限
    if not has_permission():
        return json.dumps({"error": "权限不足"})

    # 执行操作
    return do_restricted_operation(param)
```

---

## 📊 工具调用流程

```
用户输入
    ↓
Actor Agent 解析
    ↓
生成工具调用 JSON
    ↓
ToolCallGuard 防护（5层）
    ↓
Registry.dispatch()
    ↓
Schema 验证
    ↓
执行 handler
    ↓
返回结果
    ↓
显示给用户
```

---

## 🐛 调试技巧

### 1. 查看已注册的工具

```python
from Tools.registry import registry

# 列出所有工具
tools = registry.list_entries()
for tool in tools:
    print(f"{tool.name}: {tool.description}")
```

### 2. 测试工具调用

```python
# 直接测试工具
result = registry.dispatch_sync("MyTool", {"param1": "test", "param2": 123})
print(result)
```

### 3. 启用详细日志

```yaml
# config.yaml
ui:
  verbose: true  # 显示详细的工具调用日志
```

---

## ✅ 检查清单

接入新工具前，确保：

- [ ] 工具函数实现完整
- [ ] 参数 schema 定义正确
- [ ] 错误处理完善
- [ ] 添加到 config.yaml
- [ ] 测试工具调用
- [ ] 文档说明清晰

---

## 📚 参考示例

查看现有工具实现：
- `Tools/builtin/core_tools.py` - 基础工具
- `Tools/builtin/file_tools.py` - 文件操作
- `Tools/builtin/glob_tool.py` - 文件匹配
- `Tools/builtin/agent_delegate.py` - 子agent委托

---

**接入第三方工具就是这么简单！创建模块 → 注册工具 → 配置加载 → 开始使用。** 🎉
