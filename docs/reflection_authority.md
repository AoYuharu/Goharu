# Reflection Agent 决策权强化

## 问题背景

之前发现模型会编造执行结果，声称"脚本已创建并成功运行"，但实际上：
- 没有任何工具调用记录
- 文件根本不存在
- Reflection 阶段已经识别出"需要继续调用工具"，但最终还是给出了虚假回答

## 解决方案

### 1. 强化 Actor Prompt (`prompts/actor/base.md`)

**新增核心原则**：
- **禁止编造结果**：绝对不能编造、假设或猜测任何执行结果
- **工具优先**：执行操作必须先调用工具，等待真实结果
- **验证要求**：任何"成功"的声明都必须有工具调用记录作为证据

**新增验证要求**：
```markdown
- 创建文件后，必须调用 `run_cmd` 验证文件是否存在
- 运行脚本后，必须等待真实的执行输出
- 修改代码后，必须验证修改是否生效
```

### 2. 强化 Reflection Agent Prompt (`prompts/reflection/base.md`)

**赋予最终决策权**：
- Reflection Agent 是回合结束的**最终决策者**
- 只有它明确说"可以给出最终回答"，回合才能结束

**新增编造检测**：
```markdown
❌ 需要继续调用工具（出现任一情况）：
1. **编造检测**：Actor 声称"已完成"但没有工具调用记录
2. **操作未执行**：用户要求执行操作但没有看到工具调用
3. **结果未验证**：执行了操作但没有验证是否成功
4. **工具调用失败**：工具返回错误但 Actor 没有处理
5. **信息不足**：回答所需的关键信息还未获取
```

**明确输出格式**：
- 需要继续：必须包含"需要继续调用工具"
- 可以结束：必须包含"可以给出最终回答"

### 3. 修改主循环逻辑 (`main.py`)

**旧逻辑**（有漏洞）：
```python
if "可以给出最终回答" in reflection or action.get("type") == "answer":
    break
elif action.get("type") == "answer":
    break
```
问题：Actor 给出答案就可以结束，Reflection 无法阻止

**新逻辑**（Reflection 拥有决策权）：
```python
if "可以给出最终回答" in reflection:
    break  # 只有 Reflection 明确同意才能结束
elif "需要继续调用工具" in reflection:
    # 将反思内容作为指令反馈给 Actor
    memory_manager.append({
        "role": "user",
        "content": f"[Reflection] {reflection}\n\n请根据以上反思继续执行必要的操作。",
    })
    continue
elif action.get("type") == "answer":
    # Actor 给出答案但 Reflection 没有明确同意，拒绝结束
    memory_manager.append({
        "role": "user",
        "content": f"[Reflection] {reflection}\n\n你的回答需要更多验证，请继续执行必要的工具调用。",
    })
    continue
```

**关键改进**：
1. **强制 Reflection**：Actor 给出答案时，如果没有触发 reflection，强制执行一次
2. **反馈机制**：Reflection 的判断会作为新的 user 消息反馈给 Actor
3. **拒绝权**：Reflection 可以拒绝 Actor 的答案，要求继续执行

## 工作流程

```
用户提问
    ↓
Actor 执行 (step 1)
    ↓
Reflection 检查
    ├─ "可以给出最终回答" → 结束循环
    ├─ "需要继续调用工具" → 反馈给 Actor，继续循环
    └─ Actor 给出答案但 Reflection 未同意 → 拒绝，继续循环
    ↓
Actor 执行 (step 2)
    ↓
Reflection 检查
    ...
```

## 预期效果

### 场景 1：编造检测

**之前**：
```
用户：写一个输出日期的脚本
Actor：✅ 脚本已创建并成功运行！
Reflection：需要继续调用工具（但被忽略）
最终输出：脚本已创建 ❌ 编造
```

**现在**：
```
用户：写一个输出日期的脚本
Actor：✅ 脚本已创建并成功运行！
Reflection：Actor 在编造结果，没有调用任何工具。需要继续调用工具
系统反馈：[Reflection] 你的回答需要更多验证，请继续执行必要的工具调用
Actor：调用 run_cmd 创建文件
Actor：调用 run_cmd 执行脚本
Reflection：有完整的工具调用记录，可以给出最终回答 ✅
最终输出：脚本已创建并测试通过
```

### 场景 2：操作未验证

**之前**：
```
用户：创建一个 test.py 文件
Actor：调用 run_cmd: echo "..." > test.py
Actor：文件已创建 ❌ 未验证
```

**现在**：
```
用户：创建一个 test.py 文件
Actor：调用 run_cmd: echo "..." > test.py
Reflection：执行了操作但没有验证是否成功。需要继续调用工具
系统反馈：[Reflection] 请继续执行必要的操作
Actor：调用 run_cmd: ls test.py
Reflection：已验证文件存在，可以给出最终回答 ✅
```

## 配置选项

在 `config.yaml` 中可以查看 reflection 过程：

```yaml
ui:
  show_reflections: true  # 显示每次 reflection 的内容
```

## 测试建议

1. **编造测试**：要求执行操作，观察是否真的调用了工具
2. **验证测试**：要求创建文件，观察是否验证了文件存在
3. **错误处理**：故意给错误参数，观察是否重试
4. **复杂任务**：多步骤任务，观察 Reflection 是否正确判断完成度

## 已知限制

1. **最大步数限制**：仍受 `mcp.maxDepth` 限制（默认 8 步）
2. **Reflection 质量**：依赖 Reflection Agent 的判断能力
3. **上下文增长**：反馈机制会增加上下文长度

## 未来改进

- [ ] 支持 Reflection 给出具体的工具调用建议（结构化指令）
- [ ] 记录 Reflection 拒绝次数，超过阈值时警告用户
- [ ] 支持用户手动介入（当 Reflection 和 Actor 陷入循环时）
- [ ] 优化反馈消息格式，减少上下文消耗
