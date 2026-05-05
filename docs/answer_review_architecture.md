# 新架构：答案审核流程

## 概述

实现了全新的 Agent 执行流程，核心思想是：**Actor 自主决定何时回答，Reflection 审核答案质量**。

## 核心变更

### 1. Reflection 触发时机

**旧模式**（adaptive）：
- 定期触发（每 3 步或答案时）
- 判断"可以给出最终回答"或"需要继续调用工具"
- Actor 根据 Reflection 的判断调整行为

**新模式**（answer_review）：
- 仅在 Actor 输出 answer 时触发
- 审核答案质量，而不是判断是否继续
- Actor 完全自主决定何时回答

### 2. FileStateManager

**功能**：
- 记录所有 Read 工具读取的完整文件内容
- 记录所有工具调用的参数和结果
- 提供给 Reflection 共享的上下文

**隔离**：
- Reflection 可以看到工具调用结果
- Reflection 看不到 Actor 的思考过程（raw_reply）

### 3. 审核循环

**流程**：
1. Actor 输出答案
2. Reflection 审核答案
3. 如果审核通过 → 展示给用户
4. 如果审核不通过 → 反馈给 Actor 改进
5. 重复 1-4，最多 N 次（可配置）
6. 达到最大次数 → 强制展示当前答案

## 架构设计

### 组件关系

```
┌─────────────────────────────────────────────────┐
│                   User                          │
└────────────────┬────────────────────────────────┘
                 │ 提问
                 ↓
┌─────────────────────────────────────────────────┐
│              Main Loop                          │
│  - 选择执行模式（answer_review / adaptive）     │
└────────────────┬────────────────────────────────┘
                 │
                 ↓
┌─────────────────────────────────────────────────┐
│         run_agent_with_answer_review            │
│  - 管理执行流程                                  │
│  - 协调 Actor 和 Reflection                     │
└────────────────┬────────────────────────────────┘
                 │
        ┌────────┴────────┐
        ↓                 ↓
┌──────────────┐   ┌──────────────┐
│    Actor     │   │  Reflection  │
│  - 调用工具  │   │  - 审核答案  │
│  - 输出答案  │   │  - 提供反馈  │
└──────┬───────┘   └──────┬───────┘
       │                  │
       │                  │
       ↓                  ↓
┌─────────────────────────────────┐
│      FileStateManager           │
│  - 记录文件内容                  │
│  - 记录工具调用                  │
│  - 提供 Reflection 上下文        │
└─────────────────────────────────┘
```

### 数据流

```
1. Actor 调用工具
   ↓
2. FileStateManager 记录
   - Read 工具 → 记录文件内容
   - 其他工具 → 记录调用记录
   ↓
3. Actor 输出答案
   ↓
4. 触发 Reflection 审核
   ↓
5. Reflection 获取上下文
   - 用户问题
   - 工具调用记录（从 FileStateManager）
   - 文件内容（从 FileStateManager）
   - Actor 的答案
   - 不包含 Actor 的思考过程
   ↓
6. Reflection 输出审核结果
   - "答案可以接受" → 展示给用户
   - "答案需要改进" → 反馈给 Actor
   ↓
7. 如果需要改进
   - 反馈消息添加到 memory
   - Actor 看到反馈
   - Actor 可以继续调用工具或直接改进答案
   - 回到步骤 3
```

## 实现细节

### FileStateManager

**位置**：`Memory/FileStateManager.py`

**核心方法**：

```python
class FileStateManager:
    def record_file_read(self, path, content, start_line, end_line, total_lines):
        """记录文件读取"""

    def record_tool_call(self, tool_name, arguments, result, result_preview):
        """记录工具调用"""

    def get_reflection_context(self):
        """获取 Reflection 所需的完整上下文"""
        return {
            "files": self.files,
            "tool_calls": self.tool_calls,
            "files_summary": self.get_files_summary(),
            "tool_calls_summary": self.get_tool_calls_summary(),
        }
```

**特点**：
- 自动从 Read 工具的结果中提取文件内容
- 记录所有工具调用的参数和结果
- 提供格式化的摘要供 Reflection 使用

### ReflectionAgent.review_answer()

**位置**：`Agent/ReflectionAgent.py`

**签名**：
```python
def review_answer(
    self,
    question: str,
    answer: str,
    file_state_context: dict,
    memory_markdown="",
    soul_markdown=""
) -> str:
```

**输入**：
- `question`: 用户的原始问题
- `answer`: Actor 给出的答案
- `file_state_context`: FileStateManager 提供的上下文
- `memory_markdown`: 长期记忆
- `soul_markdown`: 角色定义

**输出**：
- 审核结果字符串，必须包含"答案可以接受"或"答案需要改进"

**审核标准**：
1. **完整性**：答案是否完整回答了用户的所有问题点
2. **准确性**：答案是否基于实际的工具调用结果
3. **清晰性**：答案是否清晰易懂，逻辑连贯
4. **相关性**：答案是否紧扣问题，没有偏题

### run_agent_with_answer_review()

**位置**：`Agent/answer_review_flow.py`

**核心逻辑**：

```python
for step in range(max_depth):
    action = await actor.act()

    if action_type == "tool":
        # 记录工具调用到 FileStateManager
        file_state.record_tool_call(...)

    elif action_type == "answer":
        # 触发审核
        review_result = reflector.review_answer(
            question=question,
            answer=answer,
            file_state_context=file_state.get_reflection_context(),
            ...
        )

        if "答案可以接受" in review_result:
            # 审核通过
            final_answer = answer
            break

        elif "答案需要改进" in review_result:
            review_cycle += 1

            if review_cycle >= max_review_cycles:
                # 达到最大次数，强制使用
                final_answer = answer
                break
            else:
                # 反馈给 Actor
                memory_manager.append({
                    "role": "user",
                    "content": feedback,
                })
                continue
```

## 配置

### config.yaml

```yaml
mcp:
  executor: D:\MyAnaconda\envs\llm\python.exe
  args:
    - E:\TableHelper\MCP\MCP.py
  maxDepth: 8
  reflection_mode: answer_review  # 使用新模式
  max_review_cycles: 3  # 最多审核循环次数
```

### reflection_mode 选项

| 模式 | 说明 | 触发时机 |
|------|------|----------|
| `adaptive` | 旧模式 | 定期触发（每 3 步或答案时） |
| `always` | 旧模式 | 每步都触发 |
| `never` | 旧模式 | 不触发 |
| `answer_review` | 新模式 | 仅在 Actor 输出答案时触发 |

## 使用示例

### 测试用例 1：信息收集

**问题**："扫描当前项目结构，告诉我你自己是怎么被搭建起来的"

**执行流程**：
1. Actor 调用 `dir`, `Read CLAUDE.md`, `Read config.yaml`, `Read main.py`
2. FileStateManager 记录所有文件内容
3. Actor 输出答案（描述项目架构）
4. Reflection 审核：检查答案是否基于实际读取的文件
5. 如果答案准确 → 审核通过 → 展示给用户
6. 如果答案编造 → 要求改进 → Actor 重新回答

### 测试用例 2：多轮审核

**问题**："详细说明项目的内存管理机制"

**执行流程**：
1. Actor 第一次回答（可能不够详细）
2. Reflection 审核：答案需要改进（缺少细节）
3. Actor 看到反馈，继续调用工具读取相关文件
4. Actor 第二次回答（补充了细节）
5. Reflection 审核：答案可以接受
6. 展示给用户

**审核统计**：
```
审核循环: 1 次 | 读取文件: 5 个 | 工具调用: 8 次
```

## 优势

### 1. 职责更清晰

| 角色 | 旧模式 | 新模式 |
|------|--------|--------|
| Actor | 调用工具 + 回答问题 | 调用工具 + 回答问题 + **自主决定何时回答** |
| Reflection | 判断是否继续 | **审核答案质量** |

### 2. 更好的答案质量

- Reflection 可以看到所有工具调用结果
- Reflection 可以检查答案是否基于实际数据
- 多轮审核机制确保答案质量

### 3. 更灵活的执行

- Actor 不受 Reflection 的"可以给出最终回答"判断限制
- Actor 可以根据自己的理解决定何时回答
- 如果答案不够好，可以继续改进

### 4. 更好的用户体验

- 用户只看到审核通过的答案
- 审核统计信息透明（循环次数、文件数、工具调用数）
- 避免展示中间的不完整答案

## 对比

### 旧流程问题

1. **信息不对称**：
   - Reflection 判断"可以给出最终回答"
   - 但 Actor 不知道，继续调用工具
   - 导致用户看到 tool call JSON

2. **职责混乱**：
   - Reflection 既判断是否继续，又判断答案质量
   - Actor 既要调用工具，又要理解 Reflection 的判断

3. **同步问题**：
   - Reflection 和 Actor 的判断可能不一致
   - 需要复杂的反馈机制来同步

### 新流程优势

1. **职责清晰**：
   - Actor：自主决定何时回答
   - Reflection：审核答案质量

2. **信息共享**：
   - FileStateManager 记录所有工具调用结果
   - Reflection 可以看到完整的上下文

3. **质量保证**：
   - 多轮审核机制
   - 只展示审核通过的答案

## 测试

### 运行测试

```bash
# 1. 修改配置
vim config.yaml
# 设置 reflection_mode: answer_review

# 2. 运行主程序
python main.py

# 3. 输入测试问题
扫描当前项目结构，告诉我你自己是怎么被搭建起来的
```

### 验证要点

- ✓ Reflection 仅在 Actor 输出答案时触发
- ✓ FileStateManager 记录所有文件内容和工具调用
- ✓ Reflection 可以看到工具调用结果
- ✓ 审核循环正常工作
- ✓ 最终展示审核通过的答案
- ✓ 显示审核统计信息

## 相关文件

- `Memory/FileStateManager.py` - 文件状态管理器
- `Agent/ReflectionAgent.py` - 添加 review_answer() 方法
- `Agent/answer_review_flow.py` - 新的执行流程
- `main.py` - 集成新流程
- `config.yaml` - 添加配置项
- `test_answer_review_flow.py` - 测试说明
