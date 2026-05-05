"""
AgentDelegate - 子agent委托工具

允许主agent创建和管理子agent（Explore、Plan等），实现任务的并行处理。
"""

import asyncio
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Any, Optional

from Agent.SubAgent import SubAgent
from configurationLoader import config
from Tools.registry import registry


class AgentDelegateManager:
    """子agent管理器 - 单例模式"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return

        # 当前运行的子agent计数器（按类型分类）
        self.running_agents = {
            "explore": 0,
            "plan": 0
        }

        # 锁，用于保护计数器
        self.counter_lock = threading.Lock()

        # 线程池
        self.executor = ThreadPoolExecutor(max_workers=10)

        # 输出回调函数
        self.output_callback = None

        # 任务去重：记录正在运行的任务 {(agent_type, task_hash): agent_id}
        self.running_tasks = {}
        self.tasks_lock = threading.Lock()

        self._initialized = True

    def get_max_concurrent(self, agent_type: str) -> int:
        """获取指定类型agent的最大并发数"""
        agent_config = config.get(f"agent_delegate.{agent_type.lower()}", {})
        return agent_config.get("max_concurrent", 3)

    def can_create_agent(self, agent_type: str) -> tuple[bool, Optional[str]]:
        """
        检查是否可以创建新的子agent

        Returns:
            (是否可以创建, 错误信息)
        """
        agent_type_lower = agent_type.lower()

        with self.counter_lock:
            current_count = self.running_agents.get(agent_type_lower, 0)
            max_count = self.get_max_concurrent(agent_type_lower)

            if current_count >= max_count:
                return False, f"已达到 {agent_type} agent 的最大并发数 ({max_count})，请等待现有任务完成"

            return True, None

    def increment_agent_count(self, agent_type: str):
        """增加运行中的agent计数"""
        with self.counter_lock:
            agent_type_lower = agent_type.lower()
            self.running_agents[agent_type_lower] = self.running_agents.get(agent_type_lower, 0) + 1

    def decrement_agent_count(self, agent_type: str):
        """减少运行中的agent计数"""
        with self.counter_lock:
            agent_type_lower = agent_type.lower()
            if agent_type_lower in self.running_agents:
                self.running_agents[agent_type_lower] = max(0, self.running_agents[agent_type_lower] - 1)

    def get_timeout(self) -> int:
        """获取子agent超时时间（秒）"""
        return config.get("agent_delegate.timeout", 300)

    def set_output_callback(self, callback):
        """设置输出回调函数"""
        self.output_callback = callback

    def notify_output(self, message: str, level: str = "info"):
        """通知输出消息"""
        if self.output_callback:
            self.output_callback(message, level)

    def _hash_task(self, agent_type: str, task: str) -> str:
        """生成任务哈希值（用于去重）"""
        import hashlib
        import re
        # 标准化任务描述：
        # 1. 转小写
        # 2. 去除首尾空格
        # 3. 将多个连续空格替换为单个空格
        normalized_task = re.sub(r'\s+', ' ', task.lower().strip())
        task_key = f"{agent_type.lower()}:{normalized_task}"
        return hashlib.md5(task_key.encode()).hexdigest()[:16]

    def check_duplicate_task(self, agent_type: str, task: str) -> tuple[bool, Optional[str]]:
        """
        检查是否有重复任务正在运行

        Returns:
            (是否重复, 已存在的agent_id)
        """
        task_hash = self._hash_task(agent_type, task)
        task_key = (agent_type.lower(), task_hash)

        with self.tasks_lock:
            if task_key in self.running_tasks:
                return True, self.running_tasks[task_key]
            return False, None

    def register_task(self, agent_type: str, task: str, agent_id: str):
        """注册正在运行的任务"""
        task_hash = self._hash_task(agent_type, task)
        task_key = (agent_type.lower(), task_hash)

        with self.tasks_lock:
            self.running_tasks[task_key] = agent_id

    def unregister_task(self, agent_type: str, task: str):
        """注销已完成的任务"""
        task_hash = self._hash_task(agent_type, task)
        task_key = (agent_type.lower(), task_hash)

        with self.tasks_lock:
            self.running_tasks.pop(task_key, None)


# 全局管理器实例
_manager = AgentDelegateManager()


def _execute_subagent_task(agent_type: str, task: str, agent_id: str, tools_registry, output_callback=None) -> Dict[str, Any]:
    """
    在线程中执行子agent任务

    Args:
        agent_type: agent类型
        task: 任务描述
        agent_id: agent ID
        tools_registry: 工具注册表
        output_callback: 输出回调函数

    Returns:
        执行结果
    """
    try:
        # 确保在线程中也加载了工具模块
        import Tools.builtin.core_tools
        import Tools.builtin.file_tools
        import Tools.builtin.glob_tool

        # 创建子agent
        subagent = SubAgent(
            agent_type=agent_type,
            task=task,
            agent_id=agent_id,
            tools_registry=tools_registry,
            output_callback=output_callback
        )

        # 执行任务
        result = subagent.execute()
        return result

    except Exception as e:
        import traceback
        return {
            "agent_id": agent_id,
            "agent_type": agent_type,
            "status": "error",
            "content": "",
            "error": f"子agent执行异常: {str(e)}\n{traceback.format_exc()}",
            "token_count": 0,
            "duration_ms": 0,
            "tool_calls_count": 0
        }


async def AgentDelegate(
    agent_type: str,
    task: str
) -> str:
    """
    创建并执行子agent

    Args:
        agent_type: agent类型（Explore、Plan等）
        task: 要执行的任务描述

    Returns:
        JSON格式的执行结果
    """
    start_time = time.time()

    # 验证agent类型
    valid_types = ["explore", "plan"]
    if agent_type.lower() not in valid_types:
        return json.dumps({
            "error": f"不支持的agent类型: {agent_type}",
            "valid_types": valid_types
        }, ensure_ascii=False)

    # 检查重复任务
    is_duplicate, existing_agent_id = _manager.check_duplicate_task(agent_type, task)
    if is_duplicate:
        return json.dumps({
            "error": f"相同的任务已在运行中",
            "duplicate_agent_id": existing_agent_id,
            "hint": "请等待该任务完成，或者调整任务描述使其更具体"
        }, ensure_ascii=False)

    # 检查并发限制
    can_create, error_msg = _manager.can_create_agent(agent_type)
    if not can_create:
        return json.dumps({
            "error": error_msg
        }, ensure_ascii=False)

    # 生成唯一ID
    agent_id = f"{agent_type.lower()}_{uuid.uuid4().hex[:8]}"

    # 获取工具注册表
    tools_registry_instance = registry

    # 增加计数
    _manager.increment_agent_count(agent_type)

    # 注册任务（防止重复）
    _manager.register_task(agent_type, task, agent_id)

    # 通知开始执行
    _manager.notify_output(f"🚀 启动 {agent_type} agent [{agent_id}]", "info")

    # 获取事件循环并异步执行
    loop = asyncio.get_event_loop()
    timeout = _manager.get_timeout()

    try:
        # 使用 run_in_executor 异步执行（不阻塞事件循环）
        result = await asyncio.wait_for(
            loop.run_in_executor(
                _manager.executor,
                _execute_subagent_task,
                agent_type,
                task,
                agent_id,
                tools_registry_instance,
                _manager.output_callback
            ),
            timeout=timeout
        )
    except asyncio.TimeoutError:
        result = {
            "agent_id": agent_id,
            "agent_type": agent_type,
            "status": "error",
            "content": "",
            "error": f"子agent执行超时（{timeout}秒）",
            "token_count": 0,
            "duration_ms": 0,
            "tool_calls_count": 0
        }
    except Exception as e:
        result = {
            "agent_id": agent_id,
            "agent_type": agent_type,
            "status": "error",
            "content": "",
            "error": f"子agent执行异常: {str(e)}",
            "token_count": 0,
            "duration_ms": 0,
            "tool_calls_count": 0
        }
    finally:
        # 减少计数
        _manager.decrement_agent_count(agent_type)

        # 注销任务
        _manager.unregister_task(agent_type, task)

        # 通知完成
        status_icon = "✅" if result.get("status") == "success" else "❌"
        _manager.notify_output(f"{status_icon} {agent_type} agent [{agent_id}] 完成", "info")

    # 添加总体统计
    total_duration = int((time.time() - start_time) * 1000)
    result["total_duration_ms"] = total_duration

    return json.dumps(result, ensure_ascii=False, indent=2)


# 注册工具
registry.register(
    name="AgentDelegate",
    description="""创建并执行子agent来处理特定类型的任务。

## 支持的agent类型

### Explore
- 专门用于代码库探索和搜索
- 只读权限：可以使用 Read, Grep, Glob 和受限的 run_cmd
- 适用场景：查找文件、搜索代码、理解项目结构

### Plan
- 专门用于架构设计和实现规划
- 只读权限：可以使用 Read, Grep, Glob 和受限的 run_cmd
- 适用场景：设计实现方案、规划任务步骤、分析架构

## When To Use（何时使用）

**强烈推荐使用子agent的场景：**
- 🎯 **代码库分析**：当用户希望分析、理解、探索代码库时，积极使用多个Explore agent并发分析不同模块
- 🎯 **架构理解**：需要理解系统架构、模块关系、设计模式时
- 🎯 **功能定位**：查找特定功能的实现位置、依赖关系时
- 🎯 **方案设计**：需要设计新功能、重构方案时，使用Plan agent

**其他适用场景：**
- 需要探索大型代码库
- 需要设计复杂功能的实现方案
- 任务可以并行处理（如同时探索多个模块）

**何时不使用子agent：**
- 简单的单文件修改
- 明确的bug修复
- 已经了解代码结构的情况

## ⚠️ 重要：避免重复任务

**系统会自动检测并拒绝重复任务！**

- 如果相同的任务（agent类型 + 任务描述）已在运行，会返回错误
- 任务描述会被标准化后比较（忽略大小写、多余空格）
- 这是为了避免资源浪费和重复劳动

**✅ 正确做法：为不同目标创建不同任务**
```
// 同时分析不同模块 - 允许并发
AgentDelegate(agent_type="Explore", task="分析Memory模块的实现")
AgentDelegate(agent_type="Explore", task="分析Agent模块的实现")
AgentDelegate(agent_type="Explore", task="分析Tools模块的实现")
```

**❌ 错误做法：重复相同任务**
```
// 不要这样做 - 会被拒绝
AgentDelegate(agent_type="Explore", task="分析项目结构")
AgentDelegate(agent_type="Explore", task="分析项目结构")  // 错误：重复任务！
```

## 并发执行最佳实践

**✅ 推荐：并发分析不同模块**
- 将大任务拆分成独立的子任务
- 每个子任务分析不同的模块或方面
- 利用并发优势加速分析

**示例：分析项目架构**
```
// 一次性启动多个Explore agent，并发分析
AgentDelegate(agent_type="Explore", task="分析Memory模块：理解内存管理机制")
AgentDelegate(agent_type="Explore", task="分析Agent模块：理解Actor和Reflection的实现")
AgentDelegate(agent_type="Explore", task="分析Tools模块：理解工具注册和调用机制")
AgentDelegate(agent_type="Explore", task="分析Prompting模块：理解提示词组装流程")

// 等所有Explore完成后，再启动Plan
AgentDelegate(agent_type="Plan", task="基于以上分析，设计新功能的实现方案")
```

## 并发限制

- Explore agent: 最多3个并发
- Plan agent: 最多2个并发
- 超过限制时会返回错误，需要等待现有任务完成

## 执行顺序建议

1. **先Explore，后Plan**：先用多个Explore并发了解代码，再用Plan设计方案
2. **任务要具体**：明确指定要分析的模块或方面，避免任务描述过于宽泛
3. **避免重复**：确保每个任务的目标不同

## 重要提示

- 子agent不能创建子agent（禁止嵌套）
- 子agent只有只读权限，不能修改文件
- 子agent支持并发执行，充分利用多核性能
- 系统会自动去重，避免重复执行相同任务
- 根据任务复杂度合理使用，避免过度使用""",
    arguments_schema={
        "type": "object",
        "properties": {
            "agent_type": {
                "type": "string",
                "enum": ["Explore", "Plan", "explore", "plan"],
                "description": "子agent类型：Explore（探索代码库）或 Plan（规划实现）"
            },
            "task": {
                "type": "string",
                "description": "要执行的任务描述，应该清晰具体。建议将大任务拆分成不同的子任务，可以并发调用多个AgentDelegate分析不同模块"
            }
        },
        "required": ["agent_type", "task"]
    },
    handler=AgentDelegate,
    group="agent"
)
