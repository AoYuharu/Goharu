"""
AgentDelegate - 子agent委托工具

允许主agent创建和管理子agent（Explore、Plan等），实现任务的并行处理。
"""

import asyncio
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Any, Optional

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
            "plan": 0,
            "verify": 0,
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

        from Agent.SubAgent import SubAgent  # 懒加载，避免循环依赖

        # 创建子agent
        subagent = SubAgent(
            agent_type=agent_type,
            task=task,
            agent_id=agent_id,
            tools_registry=tools_registry,
            output_callback=output_callback,
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
    task: str,
) -> str:
    """
    创建并执行子agent

    runtime 层统一处理 run_background 和超时后台化，本函数无需感知。

    Args:
        agent_type: agent类型（Explore、Plan、Verify等）
        task: 要执行的任务描述

    Returns:
        JSON格式的执行结果
    """
    start_time = time.time()

    # 验证agent类型
    valid_types = ["explore", "plan", "verify"]
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

    # 在线程池中执行子agent（阻塞等待，runtime 层管理超时/后台化）
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        _manager.executor,
        _execute_subagent_task,
        agent_type,
        task,
        agent_id,
        tools_registry_instance,
        _manager.output_callback,
    )

    # 清理
    _manager.decrement_agent_count(agent_type)
    _manager.unregister_task(agent_type, task)

    # 通知完成
    if result.get("status") == "success":
        status_icon = "✅"
    elif result.get("status") == "partial":
        status_icon = "⚠️"
    else:
        status_icon = "❌"
    _manager.notify_output(f"{status_icon} {agent_type} agent [{agent_id}] 完成", "info")

    # 添加总体统计
    total_duration = int((time.time() - start_time) * 1000)
    result["total_duration_ms"] = total_duration

    # 错误结果使用突出的格式，确保主 LLM 无法忽略
    if result.get("status") == "error":
        return (
            f"=== SUBAGENT FAILED ===\n"
            f"Agent: {result.get('agent_id')} ({result.get('agent_type')})\n"
            f"Error: {result.get('error')}\n"
            f"Duration: {total_duration}ms\n"
            f"=== DO NOT RETRY THIS SUBAGENT - IT HAS ALREADY FAILED ===\n\n"
            f"Raw result:\n{json.dumps(result, ensure_ascii=False, indent=2)}"
        )

    # partial 和 success 统一返回 JSON
    if result.get("status") == "partial" and result.get("warning"):
        result["content"] = (
            f"[{result['warning']}]\n\n"
            f"{result.get('content', '')}"
        )

    return json.dumps(result, ensure_ascii=False, indent=2)


# 注册工具
registry.register(
    name="AgentDelegate",
    description="""CRITICAL: For broad search/exploration tasks, you MUST split the work: make MULTIPLE concurrent AgentDelegate calls in parallel, each covering a DIFFERENT target area.

WHY SPLIT: Sub-agents run concurrently (up to 3 Explore at once). Parallel search is much faster than searching one area at a time. After all complete, merge their results.

WHEN TO SPLIT:
- Scanning multiple directories/disks: one agent per directory tree
- Analyzing multiple modules: one agent per module
- Searching by multiple patterns: one agent per pattern
- Any task that can be partitioned: DO IT

GOOD (split — concurrent):
  call 1: Explore task="Search for 'keyword' in C:/Projects/src"
  call 2: Explore task="Search for 'keyword' in C:/Projects/tests"
  call 3: Explore task="Search for 'keyword' in D:/archive"

BAD (monolithic — slow):
  Explore task="Search entire C:/ and D:/ drives for 'keyword'"  ← DON'T do this

VERIFY WHEN TO USE:
- After non-trivial implementation work
- When the user explicitly asks for verification, testing, adversarial review, or trying to break the change
- For risky changes in Agent / Tools / Gateway / TUI / config / background / timeout / permission logic
- Verify agents are read-only and should produce evidence-backed PASS / FAIL / PARTIAL reports

Concurrent execution: max 3 Explore, max 2 Plan, max 1 Verify by config. Sub-agents are read-only. Auto-deduplicates identical tasks.""",
    arguments_schema={
        "type": "object",
        "properties": {
            "agent_type": {
                "type": "string",
                "enum": ["Explore", "Plan", "Verify", "explore", "plan", "verify"],
                "description": "Agent type: Explore (search/understand codebase), Plan (design implementation plan), or Verify (adversarial read-only verification)."
            },
            "task": {
                "type": "string",
                "description": "Task for THIS specific area only. Each AgentDelegate call = ONE target. DO NOT bundle multiple areas in one call. Example: 'Search for config.yaml in C:/Users' rather than 'Search everywhere for config.yaml'. Be specific about path and target. For verify, structure the task with original task, key files, implementation summary, expected behavior, risk points, and constraints when possible."
            },
            "run_background": {
                "type": "boolean",
                "description": "If true, launch the subagent directly in the background and return task metadata immediately. If false, run in foreground first and only background on timeout.",
                "default": False
            }
        },
        "required": ["agent_type", "task"]
    },
    handler=AgentDelegate,
    group="agent"
)
