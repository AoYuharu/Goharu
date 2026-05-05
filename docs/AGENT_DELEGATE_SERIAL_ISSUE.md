# AgentDelegate 串行执行问题分析

## 🐛 问题描述

用户观察到：虽然主智能体一次性输出了多个 AgentDelegate 调用，但它们似乎是**一个一个串行执行**的，而不是并行执行。

## 🔍 问题分析

### 实际情况

从日志 `logs/conversation_20260503_113735.log` 可以看到：

```
[11:43:56] ASSISTANT:
{"tool": "AgentDelegate", "arguments": {"agent_type": "explore", "task": "任务1"}}

{"tool": "AgentDelegate", "arguments": {"agent_type": "explore", "task": "任务2"}}

{"tool": "AgentDelegate", "arguments": {"agent_type": "explore", "task": "任务3"}}
...
{"tool": "AgentDelegate", "arguments": {"agent_type": "plan", "task": "任务8"}}

{"tool": "Read", "arguments": {"path": "..."}}
```

**共 9 个工具调用**（8个 AgentDelegate + 1个 Read）

### 执行流程

#### 第 1 步：解析（✅ 正确）

```python
# Agent/ActorAgent.py:346
tool_calls = ToolCall.try_all_from_text(reply)
# 结果：成功解析 9 个工具调用
```

#### 第 2 步：并发提交（✅ 正确）

```python
# Agent/ActorAgent.py:392
results = await asyncio.gather(*[execute_single_tool(tc) for tc in tool_calls])
```

**这里会创建 9 个并发任务**：
- 8 个 AgentDelegate 调用
- 1 个 Read 调用

#### 第 3 步：AgentDelegate 执行（❌ 问题所在）

```python
# Tools/builtin/agent_delegate.py:258
future = _manager.executor.submit(_execute_subagent_task, ...)

# 267 行：等待子agent完成（同步阻塞）
result = future.result(timeout=timeout)
```

**关键问题**：`future.result()` 是**同步阻塞**的！

### 问题根源

虽然 `asyncio.gather` 并发启动了 8 个 AgentDelegate 调用，但每个 AgentDelegate 内部都会：

1. 提交任务到线程池（非阻塞）✅
2. **立即调用 `future.result()` 等待完成（阻塞）** ❌

**实际执行顺序**：

```
asyncio.gather 启动 8 个协程
    ↓
┌─────────────┬─────────────┬─────────────┐
│ 协程1       │ 协程2       │ 协程3       │
│ AgentDelegate│ AgentDelegate│ AgentDelegate│
└─────────────┴─────────────┴─────────────┘
    ↓           ↓           ↓
提交到线程池（非阻塞）
    ↓           ↓           ↓
future.result() 等待（阻塞！）
    ↓
┌─────────────┐
│ 线程1执行   │ ← 只有这个在运行
│ 任务1       │
└─────────────┘
    ↓ 完成
协程1 返回
    ↓
┌─────────────┐
│ 线程2执行   │ ← 现在这个开始
│ 任务2       │
└─────────────┘
```

**结果**：虽然有线程池，但因为 `future.result()` 阻塞了协程，所以**实际上是串行执行**。

## 🔧 解决方案

### 方案 1：使用 asyncio.to_thread（推荐）

修改 `Tools/builtin/agent_delegate.py`：

```python
async def AgentDelegate(agent_type: str, task: str) -> str:
    # ... 前面的检查代码 ...

    # 使用 asyncio.to_thread 而不是 executor.submit
    try:
        result = await asyncio.to_thread(
            _execute_subagent_task,
            agent_type, task, agent_id,
            tools_registry_instance,
            _manager.output_callback
        )
    except asyncio.TimeoutError:
        result = {
            "agent_id": agent_id,
            "status": "error",
            "error": "子agent超时"
        }
    finally:
        _manager.decrement_agent_count(agent_type)
        _manager.unregister_task(agent_type, task)
```

**优点**：
- ✅ 真正的异步执行
- ✅ 不阻塞事件循环
- ✅ 多个 AgentDelegate 真正并发

### 方案 2：使用 run_in_executor

```python
async def AgentDelegate(agent_type: str, task: str) -> str:
    # ... 前面的检查代码 ...

    loop = asyncio.get_event_loop()

    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(
                _manager.executor,
                _execute_subagent_task,
                agent_type, task, agent_id,
                tools_registry_instance,
                _manager.output_callback
            ),
            timeout=timeout
        )
    except asyncio.TimeoutError:
        result = {"status": "error", "error": "超时"}
    finally:
        _manager.decrement_agent_count(agent_type)
        _manager.unregister_task(agent_type, task)
```

**优点**：
- ✅ 异步等待
- ✅ 支持超时
- ✅ 兼容现有线程池

### 方案 3：完全异步化 SubAgent（最佳，但工作量大）

将 `Agent/SubAgent.py` 改为异步实现：

```python
class SubAgent:
    async def execute(self):
        # 异步执行任务
        pass

async def _execute_subagent_task(...):
    subagent = SubAgent(...)
    result = await subagent.execute()  # 异步
    return result

async def AgentDelegate(...):
    # 直接 await，不需要线程池
    result = await _execute_subagent_task(...)
```

**优点**：
- ✅ 完全异步
- ✅ 性能最优
- ✅ 代码更清晰

**缺点**：
- ❌ 需要大量重构
- ❌ SubAgent 内部也要异步化

## 📊 性能对比

### 当前实现（串行）

```
任务1: 60秒
任务2: 60秒
任务3: 60秒
总计: 180秒
```

### 修复后（并行）

```
任务1: 60秒 ┐
任务2: 60秒 ├─ 同时执行
任务3: 60秒 ┘
总计: 60秒
```

**性能提升**：3倍（对于 3 个任务）

## 🎯 推荐实施步骤

### 短期修复（方案 2）

1. 修改 `AgentDelegate` 函数
2. 使用 `loop.run_in_executor` + `asyncio.wait_for`
3. 测试验证

**预计工作量**：30 分钟
**风险**：低

### 长期优化（方案 3）

1. 将 `SubAgent.execute()` 改为异步
2. 将所有工具调用改为异步
3. 移除线程池依赖
4. 全面测试

**预计工作量**：2-3 小时
**风险**：中等

## 🧪 验证方法

### 测试代码

```python
import asyncio
import time

async def test_parallel():
    from Agent.ActorAgent import ActorAgent
    from Memory.MemoryManager import MemoryManager
    from Tools.runtime import create_tool_runtime
    from configurationLoader import config

    memory_manager = MemoryManager()
    tool_runtime = create_tool_runtime(config.get("tools.runtime", "in_process"))
    await tool_runtime.initialize()

    actor = ActorAgent(tool_runtime, memory_manager)

    # 创建 3 个 AgentDelegate 调用
    memory_manager.working.append({
        "role": "user",
        "content": "同时创建3个子agent：1) 搜索.py 2) 搜索.md 3) 搜索.txt"
    })

    start = time.time()
    result = await actor.act()
    duration = time.time() - start

    print(f"执行时间: {duration:.1f}秒")

    if result['type'] == 'tool_batch':
        agent_calls = [tc for tc in result['tool_calls']
                      if tc['tool_name'] == 'AgentDelegate']
        print(f"AgentDelegate 调用数: {len(agent_calls)}")

        # 如果是并行，3个任务应该在 ~60秒 内完成
        # 如果是串行，需要 ~180秒
        if duration < 90:
            print("✓ 并行执行")
        else:
            print("✗ 串行执行")

asyncio.run(test_parallel())
```

### 预期结果

**修复前**：
```
执行时间: 180.0秒
AgentDelegate 调用数: 3
✗ 串行执行
```

**修复后**：
```
执行时间: 60.0秒
AgentDelegate 调用数: 3
✓ 并行执行
```

## 📝 总结

### 问题确认

✅ **确实是串行执行**
- 虽然使用了 `asyncio.gather`
- 但 `future.result()` 阻塞了协程
- 导致实际串行执行

### 解决方案

✅ **推荐方案 2**（短期）
- 使用 `loop.run_in_executor`
- 工作量小，风险低
- 立即见效

✅ **推荐方案 3**（长期）
- 完全异步化
- 性能最优
- 代码更清晰

### 预期效果

- 🚀 **性能提升 3-8 倍**（取决于并发数）
- ✅ 真正的并行执行
- ✅ 更好的资源利用

---

**需要立即修复！当前实现虽然有并发代码，但实际是串行的。** ⚠️
