# 多级缓存实现总结

## 实现内容

已成功实现多级 Prompt Caching 策略，将缓存从 2 层扩展到 5 层：

### 缓存层级

| 层级 | 内容 | 缓存策略 | 更新频率 |
|------|------|----------|----------|
| Level 1 | SOUL.md | ✓ 始终缓存 | 极少变化 |
| Level 1 | Tool Directory | ✓ 始终缓存 | 极少变化 |
| Level 2 | User Profile | ✓ 默认缓存 | 中频更新 |
| Level 2 | Memory (MEMORY.md) | ✓ 默认缓存 | 中频更新 |
| Level 3 | 历史对话（除最新轮） | ✓ 按轮次缓存 | 每轮新增 |
| - | 最新一轮对话 | ✗ 不缓存 | 实时变化 |

## 修改的文件

1. **Prompting/PromptAssembler.py**
   - `_build_user_profile_section()`: 添加 `enable_cache` 参数
   - `_build_memory_section()`: 添加 `enable_cache` 参数
   - `record_to_section()`: 添加 `enable_cache` 参数，支持对话历史缓存
   - `build_actor_document()`: 实现按轮次缓存逻辑

2. **test_multilevel_cache.py** (新增)
   - 完整的缓存策略测试
   - 验证所有层级的缓存行为

3. **docs/multilevel_cache.md** (新增)
   - 详细的实现文档
   - 配置选项和优化建议

4. **CLAUDE.md** (更新)
   - 更新性能优化部分
   - 添加测试和文档引用

## 测试结果

运行 `python test_multilevel_cache.py`：

```
总消息数: 10
已缓存: 8
未缓存: 2

系统消息: 5
  - SOUL.md: [CACHED] ✓
  - User Profile: [CACHED] ✓
  - Memory: [CACHED] ✓
  - Tool Directory: [CACHED] ✓

用户消息: 3
  - 历史轮 1: [CACHED] ✓
  - 历史轮 2: [CACHED] ✓
  - 最新轮 3: [NOT CACHED] ✓

助手消息: 2
  - 历史回复 1: [CACHED] ✓
  - 历史回复 2: [CACHED] ✓
```

**测试通过率: 100%**

## 性能收益

### 成本节省

假设典型对话场景：
- 系统提示词: 2000 tokens (SOUL + Profile + Memory + Tools)
- 历史对话 (5轮): 2000 tokens
- 最新一轮: 100 tokens

**无缓存成本**: 4100 tokens × 标准价格

**多级缓存成本**:
- 4000 tokens × 缓存价格 (90% 折扣)
- 100 tokens × 标准价格
- **总节省: ~85-90%**

### 延迟优化

- 缓存命中时，首 token 延迟显著降低
- 4000 tokens 的缓存内容无需重新处理

## 使用方法

### 运行测试
```bash
python test_multilevel_cache.py
```

### 查看系统提示词结构
在 `main.py` 中使用命令：
```
/sysprompt
```

### 禁用特定层级缓存（可选）
```python
# 在 PromptAssembler 中
user_profile_section = self._build_user_profile_section(
    user_profile_markdown,
    enable_cache=False  # 禁用缓存
)
```

## 技术细节

### 缓存标记
```python
# 在 PromptSection 中添加
cache_control={"type": "ephemeral"}
```

### 历史对话缓存逻辑
```python
# 找到最后一个用户消息
last_user_idx = -1
for i in range(len(history_list) - 1, -1, -1):
    if role == "user":
        last_user_idx = i
        break

# 该位置之前的所有消息都缓存
for i, record in enumerate(history_list):
    enable_cache = (last_user_idx >= 0 and i < last_user_idx)
```

## 注意事项

1. **Provider 兼容性**: 仅 `anthropic_compatible` provider 支持缓存
2. **缓存 TTL**: 5 分钟，超时后自动失效
3. **内容更新**: User Profile 或 Memory 更新后，下次请求会刷新缓存
4. **调试**: 使用 `/sysprompt` 命令检查 `cache_control` 字段

## 下一步优化

1. 添加缓存命中率统计
2. 根据实际更新频率动态调整缓存策略
3. 对超长历史对话进行智能分段缓存

## 参考文档

- 详细实现: `docs/multilevel_cache.md`
- 测试代码: `test_multilevel_cache.py`
- 项目配置: `CLAUDE.md`
