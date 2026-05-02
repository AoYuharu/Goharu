# 任务引导系统

## 功能概述

任务引导系统用于检测并纠正模型的重复或无效行为，防止模型陷入无意义的循环操作。

## 核心功能

### 1. 重复读取检测

**触发条件**：
- 模型连续读取同一文件的同一位置超过 3 次

**检测逻辑**：
```python
# 位置标识：(文件路径, 起始行, 结束行)
key = (file_path, start_line or 0, end_line or 0)

# 计数
read_history[key] += 1

# 超过 3 次触发警告
if read_history[key] > 3:
    return warning_message
```

**警告内容**：
```
⚠️ 任务引导警告：

你已经读取 <文件路径> (lines <起始行>-<结束行>) 超过 N 次了。

**停止重复阅读！这没有意义。**

可能的问题：
1. 你在寻找某些信息但没有找到 → 尝试用 Grep 搜索关键词
2. 你忘记了之前读过的内容 → 回顾对话历史
3. 你在犹豫是否要修改 → 如果需要修改，直接调用 Edit 工具
4. 你在等待某些变化 → 文件不会自动改变，除非你调用工具修改

**下一步行动：**
- 如果需要搜索：使用 Grep 工具
- 如果需要修改：使用 Edit 工具
- 如果需要其他文件：使用 Read 读取其他文件
- 如果已经有足够信息：停止读取，开始执行任务
```

### 2. 警告去重

**机制**：
- 每个位置只警告一次
- 使用 `warned` 集合记录已警告的位置
- 避免重复警告造成对话污染

**示例**：
```python
# 第 1-3 次读取：不警告
# 第 4 次读取：触发警告
# 第 5+ 次读取：不再重复警告（已经警告过）
```

## 集成方式

### 在 ActorAgent 中集成

```python
class ActorAgent(LargeLanguageModel):
    def __init__(self, tool_runtime, working):
        super().__init__()
        self.task_guide = TaskGuide()  # 初始化任务引导器

    async def act(self, max_retries=3):
        # ... 工具调用逻辑 ...

        # 成功执行后检测重复行为
        if self.task_guide:
            warning = self.task_guide.record_tool_call(tool_name, args)
            if warning:
                # 将警告作为系统消息注入对话
                self.working.append({
                    "role": "user",
                    "content": warning,
                })
```

### 在 Actor Prompt 中强化

在 `prompts/actor/base.md` 中添加：

```markdown
**执行，不要描述**：
- ❌ 错误：说"我将创建一个文件"、"我会运行这个命令"
- ✅ 正确：直接调用工具执行操作
- **记住：用户要的是结果，不是你的计划。不要说你要做什么，直接做！**
```

## 使用场景

### 场景 1：模型反复读取同一文件

**问题**：
```
[step 1] Read test.py (lines 1-50)
[step 2] Read test.py (lines 1-50)
[step 3] Read test.py (lines 1-50)
[step 4] Read test.py (lines 1-50)  ← 触发警告
```

**效果**：
- 系统自动注入警告消息
- 提示模型停止重复阅读
- 建议使用 Grep 搜索或 Edit 修改

### 场景 2：模型描述而不执行

**问题**：
```
模型输出："我将创建一个 test.py 文件，然后运行它..."
```

**效果**：
- Actor prompt 强调"执行，不要描述"
- 模型应该直接输出工具调用 JSON
- 不应该输出自然语言描述

## 统计功能

```python
guide = TaskGuide()

# 执行一些操作...

stats = guide.get_statistics()
# {
#     "total_reads": 10,
#     "unique_locations": 3,
#     "repeated_reads": {
#         "test.py (1-50)": 5,
#         "main.py (100-150)": 3
#     }
# }
```

## 配置选项

当前阈值硬编码为 3 次，可以在 `Tools/task_guide.py` 中修改：

```python
# 超过 3 次读取同一位置，发出警告
if count > 3 and key not in self.warned:
    # 修改这里的数字来调整阈值
```

## 未来扩展

可以添加更多检测模式：

1. **重复工具调用检测**：
   - 检测连续调用同一工具但参数相同
   - 例如：连续 5 次 `run_cmd ls`

2. **无效操作检测**：
   - 检测创建文件后立即删除
   - 检测修改文件后立即恢复

3. **循环模式检测**：
   - 检测 A → B → A → B 的循环模式
   - 例如：Read → Edit → Read → Edit（同一位置）

4. **时间阈值**：
   - 如果某个操作耗时过长，提示模型
   - 例如：单次工具调用超过 30 秒

## 测试

运行测试：
```bash
python test_task_guide_integration.py
```

测试覆盖：
- ✓ 重复读取检测
- ✓ 不同位置不触发警告
- ✓ 非 Read 工具不触发检测
- ✓ 统计功能
- ✓ 警告去重机制
