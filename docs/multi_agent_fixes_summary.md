# 多Agent系统修复总结

## 修复日期
2026-05-03

## 问题概述

用户报告了多agent系统的4个问题：
1. Rich MarkupError: `closing tag '[/TOOL_CALL]' doesn't match any open tag`
2. 多个子agent同时启动且串行执行，效率低下
3. explore 和 plan 执行顺序混乱
4. TUI输出不美观，子agent的ReAct过程不可见

## 修复方案

### ✅ 问题1: Rich MarkupError

**原因**: 输出中包含 `[/TOOL_CALL]` 等标签，Rich 误认为是标记语言

**修复**: 使用 `escape()` 函数转义所有特殊字符

**修改文件**: `main.py`
- `render_step_result()`: 转义错误消息和工具结果
- `on_tool_call_start()`: 转义工具名称和参数
- `main()`: 转义最终答案

**代码示例**:
```python
from rich.markup import escape
console.print(Panel(escape(result_preview), title="...", ...))
```

### ✅ 问题2: 串行执行改为并行执行

**原因**: 用户指出应该支持并行执行，而不是强制串行

**修复**: 移除全局锁，实现智能去重机制

**修改文件**: `Tools/builtin/agent_delegate.py`

**关键改动**:
1. **移除全局锁**: 删除 `execution_lock`
2. **添加去重机制**:
   - `running_tasks`: 记录正在运行的任务
   - `_hash_task()`: 标准化任务描述并生成哈希
   - `check_duplicate_task()`: 检查重复任务
   - `register_task()` / `unregister_task()`: 注册/注销任务

**去重逻辑**:
```python
# 检查重复
is_duplicate, existing_agent_id = _manager.check_duplicate_task(agent_type, task)
if is_duplicate:
    return json.dumps({
        "error": "相同的任务已在运行中",
        "duplicate_agent_id": existing_agent_id
    })

# 注册任务
_manager.register_task(agent_type, task, agent_id)

# 执行...

# 注销任务
_manager.unregister_task(agent_type, task)
```

### ✅ 问题3: 执行顺序引导

**修复**: 更新工具描述，引导主agent合理使用

**关键说明**:
- 强调避免重复任务（系统会自动拒绝）
- 推荐并发分析不同模块
- 建议先Explore后Plan

### ✅ 问题4: 实时输出显示

**原因**: 子agent在独立线程中执行，输出无法直接显示

**修复**: 实现回调机制

**修改文件**:
- `Tools/builtin/agent_delegate.py`: 添加输出回调管理
- `Agent/SubAgent.py`: 添加 `_notify()` 方法
- `main.py`: 设置 `subagent_output_callback`

**实现**:
```python
# AgentDelegateManager
def set_output_callback(self, callback):
    self.output_callback = callback

def notify_output(self, message: str, level: str = "info"):
    if self.output_callback:
        self.output_callback(message, level)

# SubAgent
def _notify(self, message: str, level: str = "info"):
    if self.output_callback:
        self.output_callback(message, level)

# main.py
def subagent_output_callback(message: str, level: str):
    if level == "info":
        console.print(f"[cyan]{escape(message)}[/cyan]")
    # ...

agent_manager.set_output_callback(subagent_output_callback)
```

## 测试验证

### 测试文件
1. `test_subagent_fixes.py`: 验证Rich转义、串行执行（已废弃）
2. `test_parallel_agents.py`: 验证并行执行和去重机制

### 测试结果
```
✓ 任务哈希和标准化正常工作
✓ 重复任务检测正常工作
✓ 并行执行验证通过（Agent 2 在 Agent 1 完成前就开始）
✓ 并发限制正常工作
```

## 性能提升

### 旧策略（串行）
```
任务1: 60秒
任务2: 60秒 (等待)
任务3: 60秒 (等待)
总时间: 180秒
```

### 新策略（并行）
```
任务1: 60秒 ┐
任务2: 60秒 ├─ 并行
任务3: 60秒 ┘
总时间: 60秒 (提速3倍)
```

## 配置说明

`config.yaml`:
```yaml
agent_delegate:
  explore:
    max_concurrent: 3  # 最多3个并发
  plan:
    max_concurrent: 2  # 最多2个并发
  timeout: 300
  max_iterations: 8
  max_history_turns: 3

ui:
  verbose: false  # 是否显示debug级别输出
  show_actor_output: true
```

## 使用示例

### 正确用法（并发分析）
```python
# 主agent一次性调用多个不同任务
AgentDelegate(agent_type="Explore", task="分析Memory模块的实现")
AgentDelegate(agent_type="Explore", task="分析Agent模块的实现")
AgentDelegate(agent_type="Explore", task="分析Tools模块的实现")

# 这3个agent会并行执行，总时间 ≈ 单个agent时间
```

### 错误用法（重复任务）
```python
# 重复调用相同任务 - 会被拒绝
AgentDelegate(agent_type="Explore", task="分析项目结构")
AgentDelegate(agent_type="Explore", task="分析项目结构")  # 错误！
# 返回: {"error": "相同的任务已在运行中"}
```

## 输出效果

```
🚀 启动 Explore agent [explore_a1b2c3d4]
  🔄 [explore_a1b2c3d4] 迭代 1/8
  💭 [explore_a1b2c3d4] 思考: 我需要先查找相关文件...
  🔧 [explore_a1b2c3d4] 调用工具: Glob(pattern="**/*.py")
  ✓ [explore_a1b2c3d4] 工具结果: 找到 15 个文件
  🔄 [explore_a1b2c3d4] 迭代 2/8
  ...
✅ Explore agent [explore_a1b2c3d4] 完成
```

## 修改的文件

1. `main.py`
   - 添加 Rich 转义
   - 设置子agent输出回调

2. `Tools/builtin/agent_delegate.py`
   - 移除全局锁
   - 添加任务去重机制
   - 添加输出回调管理
   - 更新工具描述

3. `Agent/SubAgent.py`
   - 添加输出通知功能
   - 在关键步骤调用 `_notify()`

## 新增文件

1. `test_subagent_fixes.py` - 基础修复测试
2. `test_parallel_agents.py` - 并行执行和去重测试
3. `demo_subagent_fixes.py` - 修复效果演示
4. `docs/subagent_fixes.md` - 详细修复文档（已过时）
5. `docs/parallel_agent_strategy.md` - 并行执行策略文档

## 核心改进

1. ✅ **Rich MarkupError修复**: 转义特殊字符，不再报错
2. ✅ **并行执行**: 移除全局锁，支持真正的并行
3. ✅ **智能去重**: 自动检测并拒绝重复任务
4. ✅ **实时输出**: 回调机制让子agent过程可见
5. ✅ **性能提升**: 并行执行可提速3倍（3个agent场景）

## 总结

本次修复完成了从"强制串行"到"智能并行"的策略转变：

- **旧策略**: 全局锁 → 串行执行 → 效率低
- **新策略**: 去重机制 → 并行执行 → 高效率

通过智能去重避免了重复任务，通过并行执行充分利用了多核性能，同时保留了并发限制防止资源耗尽。这是一个平衡性能和资源的优秀方案。
