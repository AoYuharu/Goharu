# 多级缓存实现文档

## 概述

本项目实现了基于 Anthropic Prompt Caching 的多级缓存策略，通过对不同稳定性的内容应用不同的缓存策略，显著降低 API 成本并提升响应速度。

## 缓存层级

### 1. 最稳定层（始终缓存）
- **SOUL.md**：角色定义和行为边界
- **Tool Directory**：工具定义和 schema

### 2. 中频更新层（默认缓存）
- **User Profile**：用户画像和偏好设置
- **Memory (MEMORY.md)**：长期记忆索引

### 3. 历史对话层（按轮次缓存）
- **历史轮次**：除最新一轮外的所有对话
- **最新轮次**：当前正在进行的对话（不缓存）

## 实现细节

### 代码修改

#### 1. `PromptAssembler.py`

**User Profile 缓存**：
```python
def _build_user_profile_section(self, user_profile_markdown, enable_cache=True):
    # 添加 enable_cache 参数，默认启用缓存
    if enable_cache:
        section_kwargs["cache_control"] = {"type": "ephemeral"}
```

**Memory 缓存**：
```python
def _build_memory_section(self, memory_markdown, background_only=False, enable_cache=True):
    # 添加 enable_cache 参数，默认启用缓存
    if enable_cache:
        section_kwargs["cache_control"] = {"type": "ephemeral"}
```

**历史对话缓存**：
```python
def build_actor_document(self, history, ...):
    # 找到最后一个用户消息的位置
    last_user_idx = -1
    for i in range(len(history_list) - 1, -1, -1):
        if role == "user":
            last_user_idx = i
            break

    # 最新一轮之前的所有消息都缓存
    for i, record in enumerate(history_list):
        enable_cache = (last_user_idx >= 0 and i < last_user_idx)
        section = self.record_to_section(record, enable_cache=enable_cache)
```

**record_to_section 支持缓存**：
```python
@staticmethod
def record_to_section(record, enable_cache=False):
    # 为所有类型的消息（user, assistant, tool_result）添加缓存支持
    if enable_cache:
        section_kwargs["cache_control"] = {"type": "ephemeral"}
```

### 缓存策略逻辑

1. **系统提示词**：
   - SOUL.md：始终缓存
   - User Profile：始终缓存
   - Memory：始终缓存
   - Tool Directory：始终缓存

2. **对话历史**：
   - 从后向前查找最后一个 `role="user"` 的消息
   - 该位置之前的所有消息（包括 user、assistant、tool_result）都标记为缓存
   - 该位置及之后的消息（最新一轮）不缓存

## 性能收益

### 预期效果

根据 Anthropic 官方文档：
- **缓存命中**：缓存 token 成本降低约 90%
- **首 token 延迟**：显著降低（缓存内容无需重新处理）
- **缓存 TTL**：5 分钟（Anthropic 默认值）

### 成本分析

假设一次对话包含：
- SOUL.md: 500 tokens
- User Profile: 200 tokens
- Memory: 300 tokens
- Tool Directory: 1000 tokens
- 历史对话（5轮）: 2000 tokens
- 最新一轮: 100 tokens

**无缓存**：
- 总 tokens: 4100
- 成本: 4100 × 标准价格

**多级缓存**：
- 缓存 tokens: 4000 (SOUL + Profile + Memory + Tools + 历史)
- 非缓存 tokens: 100 (最新一轮)
- 成本: 4000 × 缓存价格 + 100 × 标准价格
- **节省约 85-90%**

## 测试验证

运行测试：
```bash
python test_multilevel_cache.py
```

测试覆盖：
- ✓ SOUL.md 缓存
- ✓ User Profile 缓存
- ✓ Memory 缓存
- ✓ Tool Directory 缓存
- ✓ 历史对话缓存（除最新一轮）
- ✓ 最新一轮不缓存

## 配置选项

### 禁用特定层级的缓存

如果需要禁用某个层级的缓存，可以在调用时传递参数：

```python
# 禁用 User Profile 缓存
user_profile_section = self._build_user_profile_section(
    user_profile_markdown,
    enable_cache=False
)

# 禁用 Memory 缓存
memory_section = self._build_memory_section(
    memory_markdown,
    enable_cache=False
)
```

### 调整历史对话缓存策略

当前实现按"最新一轮"划分。如果需要调整策略（如最新 N 轮不缓存），可以修改 `build_actor_document` 中的逻辑：

```python
# 示例：最新 2 轮不缓存
last_n_turns = 2
user_indices = [i for i, r in enumerate(history_list) if r.get("role") == "user"]
cutoff_idx = user_indices[-last_n_turns] if len(user_indices) >= last_n_turns else 0

for i, record in enumerate(history_list):
    enable_cache = (i < cutoff_idx)
```

## 注意事项

1. **缓存失效**：
   - 缓存 TTL 为 5 分钟
   - 如果 User Profile 或 Memory 更新，下次请求会自动刷新缓存

2. **Provider 兼容性**：
   - 仅 `anthropic_compatible` provider 支持 Prompt Caching
   - `local_hf` provider 会忽略 `cache_control` 字段

3. **成本优化**：
   - 频繁更新的内容不应缓存
   - 缓存内容应该相对稳定（至少在 5 分钟内不变）

4. **调试**：
   - 使用 `/sysprompt` 命令查看完整的系统提示词结构
   - 检查 `cache_control` 字段是否正确添加

## 未来优化方向

1. **动态缓存策略**：
   - 根据 User Profile 和 Memory 的实际更新频率动态调整缓存策略
   - 添加配置项控制缓存行为

2. **缓存统计**：
   - 记录缓存命中率
   - 统计成本节省情况

3. **智能分段**：
   - 对超长历史对话进行智能分段
   - 只缓存最相关的历史片段

## 参考资料

- [Anthropic Prompt Caching 文档](https://docs.anthropic.com/claude/docs/prompt-caching)
- 项目配置：`config.yaml`
- 测试文件：`test_multilevel_cache.py`
