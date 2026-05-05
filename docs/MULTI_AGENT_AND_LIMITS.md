# 多 Agent 执行模式和迭代上限分析

## 📊 你的两个问题

### 问题 1: 多 agent 是否串行？

**答案：不是！多 agent 是并行执行的。**

#### 实现机制

**在工具调用层面（ActorAgent）**：
```python
# Agent/ActorAgent.py:392
# 并发执行所有工具调用
results = await asyncio.gather(*[execute_single_tool(tc) for tc in tool_calls])
```

- ✅ 使用 `asyncio.gather` 并发执行
- ✅ 多个工具调用同时执行
- ✅ 等待所有工具完成后返回

**在 AgentDelegate 层面**：
```python
# Tools/builtin/agent_delegate.py:258
# 提交任务到线程池（并行执行）
future = _manager.executor.submit(
    _execute_subagent_task,
    agent_type, task, agent_id, ...
)

# 267行：等待子agent完成（同步阻塞）
result = future.result(timeout=timeout)
```

#### 执行流程

```
主 Agent 调用 act()
    ↓
解析到多个 AgentDelegate 调用
    ↓
asyncio.gather 并发执行
    ↓
┌─────────────┬─────────────┬─────────────┐
│ AgentDelegate│ AgentDelegate│ AgentDelegate│
│   (线程1)    │   (线程2)    │   (线程3)    │
│  Explore    │  Explore    │   Plan      │
│  搜索.py    │  搜索.md    │  设计方案   │
└─────────────┴─────────────┴─────────────┘
    ↓           ↓           ↓
等待所有完成（asyncio.gather）
    ↓
返回聚合结果
```

#### 并发控制

**线程池配置**：
```python
# Tools/builtin/agent_delegate.py:47
self.executor = ThreadPoolExecutor(max_workers=10)
```

**并发限制**：
```python
# config.yaml
agent_delegate:
  explore:
    max_concurrent: 3  # 最多3个 Explore agent 同时运行
  plan:
    max_concurrent: 3  # 最多3个 Plan agent 同时运行
```

#### 实际表现

**场景 1：单个 AgentDelegate**
```
用户: 创建子 agent 搜索 .md 文件
  ↓
调用 1 个 AgentDelegate
  ↓
在线程中执行（不阻塞主线程）
  ↓
等待完成（await）
```

**场景 2：多个 AgentDelegate**
```
用户: 同时创建两个子 agent：搜索 .py 和 .md
  ↓
解析到 2 个 AgentDelegate 调用
  ↓
asyncio.gather 并发执行
  ↓
┌──────────────┬──────────────┐
│ 线程1: .py   │ 线程2: .md   │
│ 同时运行     │ 同时运行     │
└──────────────┴──────────────┘
  ↓
等待两者都完成
  ↓
返回聚合结果
```

**结论**：✅ **多 agent 是真正的并行执行**

---

### 问题 2: 迭代上限到达后会发生什么？

**答案：有多层保护机制。**

#### 迭代上限配置

```python
# main.py:456
max_depth = int(config.get("mcp.maxDepth", 8) or 8)  # 默认 8 步
max_reflection_steps = 5  # Reflection 最大 5 次
```

#### 到达上限后的行为

**情况 1：正常到达 max_depth**

```python
# main.py:514 / 641
for step in range(max_depth):  # 循环 8 次
    action = await actor.act()
    # ... 处理 action

# 循环结束后（第 748 行）
final_prompt = actor.build_messages(FINAL_ANSWER_PROMPT)
final_answer = actor.query(final_prompt)  # 强制生成最终答案
```

**流程**：
1. 执行完 8 步（或配置的 max_depth）
2. 跳出循环
3. **强制要求模型给出最终答案**
4. 使用特殊 prompt：`FINAL_ANSWER_PROMPT`
   ```python
   # main.py:26
   FINAL_ANSWER_PROMPT = "请基于以上所有信息，给出最终、完整、准确的回答。此阶段禁止调用工具，不要输出 JSON，只能直接输出给用户的自然语言答复。"
   ```

**情况 2：Reflection 达到上限**

```python
# main.py:466
if reflection_count >= max_reflection_steps:
    return False  # 不再进行 reflection

# main.py:638 / 745
else:
    # 达到 Reflection 最大次数，直接结束
    break
```

**流程**：
1. Reflection 执行 5 次后
2. 不再调用 Reflection agent
3. 直接跳出循环
4. 进入最终答案生成阶段

**情况 3：死循环检测**

```python
# main.py:574
if consecutive_rejections >= 10:
    console.print("[bold red]⚠ Reflection 连续 10 次拒绝，可能陷入死循环[/bold red]")
    return {
        "final_answer": f"执行失败：Reflection 连续 {consecutive_rejections} 次拒绝...",
        "reflections": reflections,
        "deadlock": True,
    }
```

**流程**：
1. Reflection 连续 10 次说"需要继续调用工具"
2. 检测到死循环
3. **立即终止**
4. 返回错误信息和诊断建议

**情况 4：用户中断**

```python
# main.py:516 / 643
if check_interrupt():
    console.print("[yellow]⚠ Execution interrupted by user[/yellow]")
    return {
        "final_answer": "执行已被用户中断。",
        "reflections": reflections,
        "interrupted": True,
    }
```

**流程**：
1. 用户按 `/interrupt` 或 Ctrl+C
2. 立即停止执行
3. 返回中断信息

#### 最终答案生成

**即使到达上限，也会生成答案**：

```python
# main.py:748-761
final_prompt = actor.build_messages(FINAL_ANSWER_PROMPT)
used_fallback_answer = False
try:
    final_answer = actor.query(final_prompt)
    if not str(final_answer or "").strip() and last_answer:
        final_answer = last_answer  # 使用最后一次的答案
        used_fallback_answer = True
except Exception as e:
    if not last_answer:
        raise
    final_answer = last_answer  # 异常时使用最后一次的答案
    used_fallback_answer = True
```

**保护机制**：
1. 尝试生成最终答案
2. 如果失败，使用 `last_answer`（最后一次 Actor 的回答）
3. 如果连 `last_answer` 都没有，抛出异常

---

## 📈 完整的执行流程

```
用户输入问题
    ↓
for step in range(max_depth):  # 最多 8 步
    ↓
    Actor.act() → 返回 action
    ↓
    ┌─────────────────────────────────┐
    │ action 类型？                    │
    ├─────────────────────────────────┤
    │ tool / tool_batch:              │
    │   - 并发执行工具                 │
    │   - 记录结果到 memory           │
    │   - 继续下一步                   │
    ├─────────────────────────────────┤
    │ answer:                         │
    │   - 触发 Reflection             │
    │   - 如果同意：break             │
    │   - 如果拒绝：继续               │
    └─────────────────────────────────┘
    ↓
    Reflection (如果需要)
    ↓
    检查是否应该结束：
    - Reflection 同意？ → break
    - 达到 max_depth？ → break
    - 死循环检测？ → return error
    - 用户中断？ → return interrupted
    ↓
循环结束
    ↓
强制生成最终答案
    ↓
返回给用户
```

---

## 🎯 关键配置

### config.yaml

```yaml
mcp:
  maxDepth: 8  # 最大迭代步数
  reflection_mode: adaptive  # always / never / adaptive

agent_delegate:
  timeout: 300  # 子agent超时（秒）
  explore:
    max_concurrent: 3  # 最多3个并发
  plan:
    max_concurrent: 3

ui:
  show_reflections: true  # 显示 Reflection
  show_actor_output: true  # 显示 Actor 输出
```

### 硬编码限制

```python
max_reflection_steps = 5  # Reflection 最多 5 次
consecutive_rejections = 10  # 死循环检测阈值
```

---

## 💡 优化建议

### 当前设计的优点

✅ **多层保护**：
- max_depth 限制总步数
- max_reflection_steps 限制反思次数
- 死循环检测
- 用户中断支持

✅ **并发执行**：
- 工具调用并发
- 子 agent 并发
- 线程池管理

✅ **优雅降级**：
- 到达上限后强制生成答案
- 异常时使用 fallback
- 清晰的错误信息

### 可能的改进

#### 1. 动态调整 max_depth

```python
# 根据任务复杂度动态调整
if "深度分析" in question or "详细检索" in question:
    max_depth = 15
else:
    max_depth = 8
```

#### 2. 更智能的终止条件

```python
# 检测是否有实质性进展
if step > 3 and no_new_information_for_3_steps:
    break  # 提前终止
```

#### 3. 子 agent 超时优化

```python
# 根据任务类型设置不同超时
if agent_type == "explore":
    timeout = 60  # 快速探索
elif agent_type == "plan":
    timeout = 300  # 深度规划
```

#### 4. 并发数动态调整

```python
# 根据系统负载动态调整
current_load = get_system_load()
if current_load < 0.5:
    max_concurrent = 5  # 增加并发
else:
    max_concurrent = 2  # 减少并发
```

---

## 🎉 总结

### 问题 1：多 agent 是否串行？

**❌ 不是串行，是并行！**

- ✅ 使用 `asyncio.gather` 并发执行
- ✅ 线程池支持（max_workers=10）
- ✅ 并发限制保护（max_concurrent=3）
- ✅ 真正的并行执行

### 问题 2：到达迭代上限后会发生什么？

**✅ 有完善的保护机制！**

1. **正常终止**：强制生成最终答案
2. **Reflection 上限**：直接结束，生成答案
3. **死循环检测**：返回错误和诊断
4. **用户中断**：立即停止
5. **异常处理**：使用 fallback 答案

**不会出现**：
- ❌ 无限循环
- ❌ 程序崩溃
- ❌ 没有输出

**一定会有**：
- ✅ 最终答案（即使不完美）
- ✅ 清晰的状态信息
- ✅ 错误诊断（如果失败）

---

**系统设计合理，保护机制完善！** 🎊
