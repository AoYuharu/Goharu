# 子Agent系统修复文档

## 修复日期
2026-05-03

## 问题概述

在多agent系统中发现了以下问题：

1. **Rich MarkupError**: 输出中包含 `[/TOOL_CALL]` 等标签导致 Rich 库解析错误
2. **多个子agent同时启动**: 主agent会并发启动多个子agent，导致资源浪费
3. **执行顺序混乱**: explore 和 plan 应该分阶段执行，但会同时运行
4. **TUI输出不可见**: 子agent的内部 ReAct 过程完全不可见，只能看到最终结果

## 修复方案

### 1. Rich MarkupError 修复

**问题原因**: Rich 库会将 `[xxx]` 格式的文本解析为标记语言，但 `[/TOOL_CALL]` 等标签不是有效的 Rich 标记，导致解析错误。

**修复方法**: 在所有 Rich 输出前使用 `escape()` 函数转义特殊字符。

**修改文件**: `main.py`

```python
from rich.markup import escape

# 在所有 console.print() 中使用 escape()
console.print(f"[bold red]✗[/bold red] [red]{escape(error_msg)}[/red]")
console.print(Panel(escape(result_preview), title="...", ...))
```

**影响范围**:
- `render_step_result()` 函数
- `on_tool_call_start()` 回调函数
- `main()` 函数中的最终答案显示

### 2. 强制串行执行

**问题原因**: `ActorAgent.act()` 支持并发工具调用，导致多个 `AgentDelegate` 同时执行。

**修复方法**: 在 `AgentDelegateManager` 中添加全局执行锁，强制所有子agent串行执行。

**修改文件**: `Tools/builtin/agent_delegate.py`

```python
class AgentDelegateManager:
    def __init__(self):
        # 全局执行锁（强制串行执行）
        self.execution_lock = threading.Lock()

async def AgentDelegate(agent_type: str, task: str) -> str:
    # 使用全局锁强制串行执行
    with _manager.execution_lock:
        # 执行子agent...
```

**效果**:
- 即使主agent并发调用多个 `AgentDelegate`，它们也会串行执行
- 第一个子agent完成后，第二个才开始
- 避免资源竞争和重复劳动

### 3. 工具描述更新

**修改文件**: `Tools/builtin/agent_delegate.py`

**更新内容**:
- 明确说明子agent会串行执行，不支持并发
- 建议分多轮调用，而不是一次调用多个
- 强调执行顺序：先 Explore，后 Plan

**关键说明**:
```
## ⚠️ 重要：串行执行规则

**子agent会串行执行，不支持并发！**

- 每次只能运行一个子agent
- 如果需要多个子agent，必须**分多轮调用**
- 不要在一次响应中调用多个AgentDelegate

**✅ 正确做法：分阶段执行**
第1轮：调用 AgentDelegate(agent_type="Explore", task="分析Memory模块")
等待完成后...
第2轮：调用 AgentDelegate(agent_type="Explore", task="分析Agent模块")
```

### 4. 实时输出显示

**问题原因**: 子agent在独立线程中执行，其内部 ReAct 过程无法直接输出到主线程的 TUI。

**修复方法**: 实现回调机制，子agent通过回调函数将输出发送到主线程。

**修改文件**:
- `Tools/builtin/agent_delegate.py`: 添加输出回调管理
- `Agent/SubAgent.py`: 添加输出通知功能
- `main.py`: 设置回调函数

**实现细节**:

1. **AgentDelegateManager 添加回调支持**:
```python
class AgentDelegateManager:
    def __init__(self):
        self.output_callback = None

    def set_output_callback(self, callback):
        self.output_callback = callback

    def notify_output(self, message: str, level: str = "info"):
        if self.output_callback:
            self.output_callback(message, level)
```

2. **SubAgent 添加输出通知**:
```python
class SubAgent:
    def __init__(self, ..., output_callback=None):
        self.output_callback = output_callback

    def _notify(self, message: str, level: str = "info"):
        if self.output_callback:
            self.output_callback(message, level)

    def execute(self):
        # 在关键步骤通知输出
        self._notify(f"🔄 [{self.agent_id}] 迭代 {iteration + 1}", "info")
        self._notify(f"💭 [{self.agent_id}] 思考: {preview}", "debug")
        self._notify(f"🔧 [{self.agent_id}] 调用工具: {tool_name}", "info")
```

3. **main.py 设置回调**:
```python
def subagent_output_callback(message: str, level: str):
    if RICH_AVAILABLE and console:
        from rich.markup import escape
        if level == "info":
            console.print(f"[cyan]{escape(message)}[/cyan]")
        elif level == "debug":
            if config.get("ui.verbose", False):
                console.print(f"[dim]{escape(message)}[/dim]")
        # ...

agent_manager.set_output_callback(subagent_output_callback)
```

**输出效果**:
```
🚀 启动 Explore agent [explore_a1b2c3d4]
  🔄 [explore_a1b2c3d4] 迭代 1/8
  💭 [explore_a1b2c3d4] 思考: 我需要先查找相关文件...
  🔧 [explore_a1b2c3d4] 调用工具: Glob(pattern="**/*.py")
  ✓ [explore_a1b2c3d4] 工具结果: 找到 15 个文件
  🔄 [explore_a1b2c3d4] 迭代 2/8
  ...
  ✅ [explore_a1b2c3d4] 得出最终结论
✅ Explore agent [explore_a1b2c3d4] 完成
```

## 测试验证

创建了 `test_subagent_fixes.py` 测试文件，验证所有修复：

### 测试1: 输出回调机制
- ✅ 回调函数正确注册和调用
- ✅ 消息和级别正确传递

### 测试2: 串行执行（全局锁）
- ✅ 多个线程同时执行时，任务串行化
- ✅ 每个任务的 start 和 end 连续

### 测试3: Rich标记转义
- ✅ `[TOOL_CALL]` 等特殊标签正确转义
- ✅ 转义后的文本可以正常渲染
- ✅ 不会抛出 MarkupError

### 测试4: AgentDelegate串行执行
- ✅ 两个任务串行执行
- ✅ 第一个完成后第二个才开始

## 配置说明

相关配置项（`config.yaml`）：

```yaml
agent_delegate:
  explore:
    max_concurrent: 3  # Explore类型子agent的最大并发数（实际会串行）
  plan:
    max_concurrent: 2  # Plan类型子agent的最大并发数（实际会串行）
  timeout: 300  # 单个子agent超时时间（秒）
  max_iterations: 8  # 子agent的最大ReAct循环次数
  max_history_turns: 3  # 保留的最大历史轮数

ui:
  verbose: false  # 是否显示debug级别的输出（子agent思考过程）
  show_actor_output: true  # 显示Actor的原始输出
```

## 使用建议

### 1. 分阶段使用子agent

**推荐流程**:
```
用户提问 → 主agent分析
         ↓
    需要探索代码？
         ↓ 是
    调用 Explore agent（第1轮）
         ↓
    分析探索结果
         ↓
    需要更多信息？
         ↓ 是
    调用 Explore agent（第2轮）
         ↓
    需要设计方案？
         ↓ 是
    调用 Plan agent（第3轮）
         ↓
    实施方案
```

### 2. 避免过度使用

**不需要子agent的场景**:
- 简单的单文件修改
- 明确的bug修复
- 已经了解代码结构

**需要子agent的场景**:
- 大型代码库探索
- 复杂架构理解
- 功能定位和依赖分析
- 方案设计和规划

### 3. 合理设置超时

- 简单探索任务: 60-120秒
- 复杂分析任务: 180-300秒
- 如果经常超时，考虑拆分任务

## 性能影响

### 优化效果
- ✅ 避免并发冲突，减少资源浪费
- ✅ 串行执行更可控，便于调试
- ✅ 实时输出提升用户体验

### 潜在影响
- ⚠️ 串行执行可能增加总时间（但避免了重复劳动）
- ⚠️ 回调输出增加少量开销（可忽略）

## 后续优化建议

1. **智能并发控制**: 根据任务类型决定是否允许并发
2. **任务队列**: 实现任务队列机制，支持优先级
3. **进度条**: 为长时间运行的子agent添加进度条
4. **结果缓存**: 缓存子agent结果，避免重复执行相同任务
5. **超时恢复**: 超时后支持断点续传

## 相关文件

- `main.py`: 主循环和TUI显示
- `Tools/builtin/agent_delegate.py`: 子agent委托工具
- `Agent/SubAgent.py`: 子agent执行器
- `test_subagent_fixes.py`: 修复效果测试
- `config.yaml`: 配置文件

## 总结

本次修复解决了多agent系统的4个关键问题：

1. ✅ **Rich MarkupError**: 通过转义特殊字符修复
2. ✅ **并发控制**: 通过全局锁强制串行执行
3. ✅ **执行顺序**: 通过工具描述引导正确使用
4. ✅ **输出可见性**: 通过回调机制实现实时输出

所有修复已通过测试验证，系统现在更加稳定和用户友好。
