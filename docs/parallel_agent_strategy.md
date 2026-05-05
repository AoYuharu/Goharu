# 子Agent并行执行策略文档

## 更新日期
2026-05-03

## 策略变更

### 旧策略（已废弃）
- ❌ 使用全局锁强制串行执行
- ❌ 即使主agent并发调用多个子agent，也会排队等待
- ❌ 无法充分利用多核性能

### 新策略（当前）
- ✅ 移除全局锁，支持真正的并行执行
- ✅ 实现智能去重机制，避免重复任务
- ✅ 保留并发限制，防止资源耗尽
- ✅ 通过工具描述引导主agent合理使用

## 核心机制

### 1. 并行执行

**实现方式**: 移除了 `execution_lock`，允许多个子agent同时运行

**效果**:
```python
# 主agent可以并发调用多个不同的子agent
AgentDelegate(agent_type="Explore", task="分析Memory模块")
AgentDelegate(agent_type="Explore", task="分析Agent模块")
AgentDelegate(agent_type="Explore", task="分析Tools模块")

# 这3个agent会真正并行执行，充分利用多核CPU
```

### 2. 智能去重

**问题**: 主agent可能会重复调用相同的任务，浪费资源

**解决方案**: 任务去重机制

**实现** (`agent_delegate.py`):
```python
class AgentDelegateManager:
    def __init__(self):
        # 任务去重：记录正在运行的任务
        self.running_tasks = {}  # {(agent_type, task_hash): agent_id}
        self.tasks_lock = threading.Lock()

    def _hash_task(self, agent_type: str, task: str) -> str:
        """生成任务哈希值（用于去重）"""
        import hashlib
        import re
        # 标准化：转小写、去除首尾空格、合并多余空格
        normalized_task = re.sub(r'\s+', ' ', task.lower().strip())
        task_key = f"{agent_type.lower()}:{normalized_task}"
        return hashlib.md5(task_key.encode()).hexdigest()[:16]

    def check_duplicate_task(self, agent_type: str, task: str):
        """检查是否有重复任务正在运行"""
        task_hash = self._hash_task(agent_type, task)
        task_key = (agent_type.lower(), task_hash)

        with self.tasks_lock:
            if task_key in self.running_tasks:
                return True, self.running_tasks[task_key]
            return False, None
```

**去重规则**:
- 任务描述会被标准化：忽略大小写、首尾空格、多余空格
- `"分析Memory模块"` 和 `"  分析MEMORY模块  "` 会被认为是相同任务
- `"分析Memory模块"` 和 `"分析 Memory 模块"` 会被认为是不同任务（空格位置不同）

**效果**:
```python
# 第一次调用 - 成功
AgentDelegate(agent_type="Explore", task="分析Memory模块")

# 第二次调用相同任务 - 被拒绝
AgentDelegate(agent_type="Explore", task="分析Memory模块")
# 返回: {"error": "相同的任务已在运行中", "duplicate_agent_id": "explore_xxx"}
```

### 3. 并发限制

**目的**: 防止同时运行过多子agent导致资源耗尽

**限制**:
- Explore agent: 最多3个并发
- Plan agent: 最多2个并发

**实现**:
```python
def can_create_agent(self, agent_type: str) -> tuple[bool, Optional[str]]:
    """检查是否可以创建新的子agent"""
    with self.counter_lock:
        current_count = self.running_agents.get(agent_type_lower, 0)
        max_count = self.get_max_concurrent(agent_type_lower)

        if current_count >= max_count:
            return False, f"已达到 {agent_type} agent 的最大并发数 ({max_count})"

        return True, None
```

### 4. 工具描述引导

**策略**: 通过工具描述引导主agent合理使用子agent

**关键说明**:
```
## ⚠️ 重要：避免重复任务

**系统会自动检测并拒绝重复任务！**

- 如果相同的任务（agent类型 + 任务描述）已在运行，会返回错误
- 任务描述会被标准化后比较（忽略大小写、多余空格）

**✅ 正确做法：为不同目标创建不同任务**
AgentDelegate(agent_type="Explore", task="分析Memory模块的实现")
AgentDelegate(agent_type="Explore", task="分析Agent模块的实现")
AgentDelegate(agent_type="Explore", task="分析Tools模块的实现")

**❌ 错误做法：重复相同任务**
AgentDelegate(agent_type="Explore", task="分析项目结构")
AgentDelegate(agent_type="Explore", task="分析项目结构")  // 错误：重复任务！
```

## 执行流程

### 并发执行示例

```
用户: "分析这个项目的架构"

主agent分析 → 决定并发启动多个Explore agent

并发调用:
├─ AgentDelegate(Explore, "分析Memory模块")  ─┐
├─ AgentDelegate(Explore, "分析Agent模块")   ├─ 并行执行
└─ AgentDelegate(Explore, "分析Tools模块")   ─┘

等待所有完成 → 汇总结果 → 决定下一步

可能继续:
└─ AgentDelegate(Plan, "设计新功能方案")
```

### 去重示例

```
主agent错误地重复调用:

第1次: AgentDelegate(Explore, "分析Memory模块")
  → 成功启动 [explore_a1b2c3d4]

第2次: AgentDelegate(Explore, "分析Memory模块")
  → 检测到重复
  → 返回错误: "相同的任务已在运行中"
  → 主agent收到错误，不会再次调用
```

## 测试验证

### 测试1: 任务哈希和标准化
```
✓ "分析Memory模块" == "  分析Memory模块  " (忽略首尾空格)
✓ "分析Memory模块" == "分析MEMORY模块" (忽略大小写)
✓ "分析Memory模块" != "分析Agent模块" (不同任务)
```

### 测试2: 重复任务检测
```
✓ 注册任务后，再次调用会被检测为重复
✓ 不同任务不会被误判为重复
✓ 注销任务后可以重新执行
```

### 测试3: 并行执行验证
```
✓ 3个agent同时启动
✓ Agent 2 在 Agent 1 完成前就开始（并行执行）
✓ 总执行时间 ≈ 单个agent时间（而非3倍）
```

### 测试4: 并发限制
```
✓ 前3个Explore agent可以创建
✓ 第4个Explore agent被拒绝
✓ 错误信息: "已达到 explore agent 的最大并发数 (3)"
```

## 配置说明

`config.yaml`:
```yaml
agent_delegate:
  explore:
    max_concurrent: 3  # Explore类型子agent的最大并发数
  plan:
    max_concurrent: 2  # Plan类型子agent的最大并发数
  timeout: 300  # 单个子agent超时时间（秒）
  max_iterations: 8  # 子agent的最大ReAct循环次数
  max_history_turns: 3  # 保留的最大历史轮数
```

## 性能对比

### 旧策略（串行执行）
```
任务1: 60秒
任务2: 60秒 (等待任务1完成)
任务3: 60秒 (等待任务2完成)
总时间: 180秒
```

### 新策略（并行执行）
```
任务1: 60秒 ┐
任务2: 60秒 ├─ 并行
任务3: 60秒 ┘
总时间: 60秒 (提速3倍)
```

## 优势

1. **性能提升**: 充分利用多核CPU，显著减少总执行时间
2. **智能去重**: 自动避免重复任务，节省资源
3. **灵活控制**: 保留并发限制，防止资源耗尽
4. **用户友好**: 通过工具描述引导正确使用

## 注意事项

1. **任务要具体**: 确保每个任务的目标明确且不同
2. **合理拆分**: 将大任务拆分成可并行的小任务
3. **监控资源**: 注意系统资源使用情况，必要时调整并发限制
4. **错误处理**: 主agent应该正确处理去重错误，不要重复调用

## 相关文件

- `Tools/builtin/agent_delegate.py`: 子agent委托工具（核心实现）
- `Agent/SubAgent.py`: 子agent执行器
- `test_parallel_agents.py`: 并行执行和去重测试
- `config.yaml`: 配置文件

## 总结

新策略通过以下方式实现了高效的并行执行：

1. ✅ **移除全局锁**: 允许真正的并行执行
2. ✅ **智能去重**: 自动检测并拒绝重复任务
3. ✅ **并发限制**: 防止资源耗尽
4. ✅ **工具描述**: 引导主agent正确使用

这种设计既保证了性能，又避免了资源浪费，是一个平衡的解决方案。
