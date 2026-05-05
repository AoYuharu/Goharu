# 从用户按下回车到首 Token 回复的完整流程

## 概览

```
用户按下回车
    ↓
main.py: read_user_message()
    ↓
main.py: run_agent()
    ↓
[循环开始] Actor.act()
    ↓
构建 Prompt
    ↓
调用 LLM API
    ↓
首 Token 返回
```

## 详细流程

### 1. 用户输入阶段 (`main.py:145-168`)

```python
user_input = read_user_message()
# - 读取用户输入
# - 处理命令（/help, /multi, /exit）
# - 返回用户消息文本
```

### 2. 进入 Agent 循环 (`main.py:215-370`)

```python
await run_agent(actor, reflector, question, memory_manager)
```

**初始化**：
- 记录日志：`logger.log_user_input(question)`
- 保存起始索引：`turn_start_index = memory_manager.get_context_size()`
- 添加用户消息到上下文：`memory_manager.append({"role": "user", "content": question})`

### 3. 第一次 Actor 执行 (`main.py:254` 或 `278`)

```python
action = await actor.act()
```

进入 `ActorAgent.act()` (`Agent/ActorAgent.py:72-145`)

### 4. 构建 Prompt (`ActorAgent.act() → build_messages()`)

#### 4.1 初始化防护器 (`ActorAgent.py:55-60`)
```python
tool_definitions = getattr(self.tool_runtime, "last_tool_definitions", None)
if self.guard is None and tool_definitions:
    self.guard = ToolCallGuard(tool_definitions)
```

#### 4.2 组装 Prompt Document (`ActorAgent.py:62-69`)
```python
document = self.prompt_assembler.build_actor_document(
    history=self.working.get_context(),
    soul_markdown=self.working.get_soul_markdown(),
    user_profile_markdown=self.working.get_user_profile_markdown(),
    memory_markdown=self.working.get_memory_markdown(),
    extra_system_prompt=extra_system_prompt,
    tool_definitions=tool_definitions,
)
```

**PromptAssembler.build_actor_document()** (`Prompting/PromptAssembler.py:207-256`)：

按顺序添加以下 section：

1. **Actor 系统 Prompt** (`prompts/actor/base.md`)
   - 核心原则（禁止编造、工具优先）
   - 工具调用格式
   - 工具使用指南

2. **SOUL.md**（如果存在）
   - 角色设定和行为边界

3. **USER.md**（如果存在）
   - 用户画像

4. **MEMORY.md**（如果存在）
   - 长期记忆索引

5. **工具目录** (Tool Definitions)
   ```json
   [
     {
       "name": "run_cmd",
       "description": "...",
       "inputSchema": {...}
     },
     ...
   ]
   ```

6. **对话历史** (History)
   - 之前的 user/assistant/tool 消息
   - 包括本次用户提问

7. **额外系统指令**（如果有）
   - 例如最终回答阶段的 `FINAL_ANSWER_PROMPT`

#### 4.3 渲染为 LLM 消息格式 (`ActorAgent.py:70`)
```python
return self.prompt_renderer.render_document(document)
```

将 PromptDocument 转换为标准消息格式：
```python
[
  {"role": "system", "content": "..."},
  {"role": "user", "content": "..."},
  {"role": "assistant", "content": "..."},
  ...
]
```

### 5. 调用 LLM (`ActorAgent.py:79`)

```python
reply = self.query(self.build_messages())
```

#### 5.1 LargeLanguageModel.query() (`Agent/LargeLanguageModel.py:11-12`)
```python
return self.core.generate(messages)
```

#### 5.2 LLMCore.generate() (`Agent/LLMCore.py:301-306`)

根据 provider 类型分发：

**如果是 `anthropic_compatible` (MiniMax)**：

```python
def _generate_anthropic_compatible(self, messages, **gen_kwargs):
    # 1. 准备消息
    system, remote_messages = self._prepare_anthropic_messages(messages)

    # 2. 构建请求参数
    request_kwargs = {
        "model": self.llm_config.get("model"),  # "MiniMax-M2.7"
        "max_tokens": 1024,
        "temperature": 0.7,
        "top_p": 0.9,
        "messages": remote_messages,
        "system": system,  # 所有 system 消息合并
    }

    # 3. 调用 Anthropic SDK
    response = self.client.messages.create(**request_kwargs)

    # 4. 提取响应文本
    return self._extract_response_text(response)
```

**消息准备过程** (`LLMCore.py:175-204`)：
- 将所有 `role="system"` 的消息合并为一个 system 字符串
- 将 `role="tool"` 转换为 `role="user"` 格式（因为 Anthropic API 不支持 tool role）
- 过滤空消息

**如果是 `local_hf` (本地 Qwen)**：

```python
def _generate_local_hf(self, messages, **gen_kwargs):
    # 1. 准备消息
    prepared_messages = self._prepare_local_messages(messages)

    # 2. 应用 chat template
    text = self.tokenizer.apply_chat_template(
        prepared_messages,
        tokenize=False,
        add_generation_prompt=True,
    )

    # 3. Tokenize
    inputs = self.tokenizer(text, return_tensors="pt").to("cuda")

    # 4. 生成
    with self.torch.no_grad():
        outputs = self.model.generate(
            **inputs,
            max_new_tokens=1024,
            temperature=0.7,
            top_p=0.9,
            repetition_penalty=1.1,
        )

    # 5. Decode
    response = self.tokenizer.decode(
        outputs[0][inputs.input_ids.shape[-1]:],
        skip_special_tokens=True,
    )

    return response.strip()
```

### 6. 首 Token 返回

**对于 API 调用 (MiniMax)**：
- HTTP 请求发送到 `https://api.minimaxi.com/anthropic`
- 等待服务器响应
- 首 Token 到达（通常 1-3 秒）

**对于本地模型 (Qwen)**：
- GPU 推理开始
- 首 Token 生成（取决于 GPU 性能和模型大小）

### 7. 响应处理 (`ActorAgent.act():81-145`)

```python
# 1. 解析工具调用
tool_call = ToolCall.try_from_text(reply)

if tool_call is not None:
    # 2. 应用防护系统（防线 1-4）
    guard_result = self.guard.guard(tool_call.tool_name, tool_call.arguments)

    if not guard_result["success"]:
        # 3. 防线 5：重试
        if max_retries > 0:
            # 反馈错误，要求重新生成
            self.working.append({
                "role": "user",
                "content": f"工具调用失败: {error_msg}\n请重新生成正确的工具调用。",
            })
            return await self.act(max_retries=max_retries - 1)

    # 4. 执行工具
    result = await self.tool_runtime.call_tool(tool_name, args)

    # 5. 记录到上下文
    self.working.append(tool_call)
    self.working.append({"role": "tool", "name": tool_name, "content": result_text})

    return {"type": "tool", ...}
else:
    # 模型给出了答案（非工具调用）
    self.working.append({"role": "assistant", "content": reply})
    return {"type": "answer", ...}
```

### 8. Reflection 检查 (`main.py:260-271` 或 `284-295`)

```python
if should_reflect(step, action.get("type")):
    reflection = reflector.reflect(
        question=question,
        history=memory_manager.get_context(),
        memory_markdown=memory_manager.get_memory_markdown(),
        soul_markdown=memory_manager.get_soul_markdown(),
    )

    # 检查 Reflection 决策
    if "可以给出最终回答" in reflection:
        break  # 结束循环
    elif "需要继续调用工具" in reflection:
        # 反馈给 Actor，继续循环
        memory_manager.append({
            "role": "user",
            "content": f"[Reflection] {reflection}\n\n请根据以上反思继续执行必要的操作。",
        })
        continue
```

## 时间消耗分析

### 首次调用（冷启动）

1. **Prompt 构建**：< 10ms
   - 加载 prompt 文件
   - 组装 document
   - 渲染消息格式

2. **LLM 调用**：
   - **API (MiniMax)**：1-3 秒（网络延迟 + 服务器推理）
   - **本地 (Qwen)**：0.5-2 秒（取决于 GPU 和模型大小）

3. **响应解析**：< 5ms
   - 提取文本
   - 解析工具调用

4. **防护系统**：< 1ms
   - 工具名修复
   - JSON 修复
   - 类型转换
   - 参数清理

**总计（首 Token）**：
- API 模式：1-3 秒
- 本地模式：0.5-2 秒

### 后续调用（工具调用后）

每次工具调用后，会再次执行 Actor.act()：
- Prompt 构建：< 10ms（上下文更长，稍慢）
- LLM 调用：1-3 秒（API）或 0.5-2 秒（本地）

### Reflection 调用

每次 Reflection 也需要调用 LLM：
- Prompt 构建：< 5ms（更简单的 prompt）
- LLM 调用：1-3 秒（API）或 0.5-2 秒（本地）

## 优化建议

1. **Prompt 缓存**：缓存不变的 system prompt 部分
2. **并行 Reflection**：在工具执行期间并行运行 Reflection
3. **流式输出**：使用 streaming API 减少首 Token 延迟感知
4. **本地模型优化**：使用更小的模型或量化
5. **工具预测**：预测可能的工具调用，提前准备参数

## 当前瓶颈

**主要瓶颈**：LLM 推理时间（占总时间 > 95%）

**次要瓶颈**：
- 上下文长度增长（每次调用都包含完整历史）
- Reflection 串行执行（每次都要等待 LLM 响应）
- 多次重试（防线 5 最多 3 次，每次 1-3 秒）
