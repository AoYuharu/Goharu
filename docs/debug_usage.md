# Debug功能使用指南

## 功能概述

新增两个Debug功能：

1. **上下文调试编辑器** - 交互式查看和编辑三层上下文
2. **API响应日志** - 记录所有LLM API的请求和响应

---

## 1. 上下文调试编辑器

### 启用方式

1. 在 `config.yaml` 中启用Debug模式：
```yaml
debug:
  enabled: true
```

2. 运行程序后，输入 `/debug` 命令

### 功能界面

```
🔧 Context Debug Editor
三层上下文调试工具

┌─────────────────────────────────────────────────────────┐
│ 层级                    消息数    字符数    操作         │
├─────────────────────────────────────────────────────────┤
│ 1️⃣  System Prompt         3      12,450   [1] 编辑    │
│ 2️⃣  Memory Context        1       3,200   [2] 编辑    │
│ 3️⃣  Conversation History  8       5,600   [3] 编辑    │
└─────────────────────────────────────────────────────────┘

操作菜单：
  [1] 编辑 System Prompt
  [2] 编辑 Memory Context
  [3] 编辑 Conversation History
  [4] 发送到API测试
  [5] 导出上下文
  [q] 退出
```

### 三层上下文说明

| 层级 | 内容 | 说明 |
|------|------|------|
| **System Prompt** | 系统提示词 | 包含角色定义、工具定义、项目上下文等 |
| **Memory Context** | 记忆上下文 | MEMORY.md、SOUL.md、USER.md的内容 |
| **Conversation History** | 对话历史 | 用户和助手的历史对话记录 |

### 编辑操作

#### 编辑单条消息
1. 选择要编辑的层级（1/2/3）
2. 选择具体的消息索引
3. 输入新内容（多行输入）
4. 输入 `/done` 完成编辑

#### 添加消息
1. 选择层级3（对话历史）
2. 输入 `a` 添加新消息
3. 选择角色（user/assistant/system）
4. 输入内容，`/done` 完成

#### 删除消息
1. 选择层级3（对话历史）
2. 输入 `d` 删除
3. 输入要删除的消息索引

### 测试API
选择 `[4]` 可以将当前编辑后的上下文直接发送到API测试，查看模型响应。

### 导出上下文
选择 `[5]` 可以将当前上下文导出为JSON文件，保存在 `runtime_memory/context_exports/` 目录。

---

## 2. API响应日志

### 启用方式

在 `config.yaml` 中配置：
```yaml
debug:
  enabled: true
  log_dir: ./logs/debug                    # Markdown格式日志目录
  json_log_dir: ./runtime_memory/llm_logs  # JSON格式日志目录
  log_api_requests: true                   # 记录请求
  log_api_responses: true                  # 记录响应
```

### 日志格式

#### Markdown格式（人类可读）
位置：`logs/debug/api_YYYYMMDD_HHMMSS_ffffff.md`

包含：
- 请求参数（model, temperature, max_tokens）
- System Messages（系统提示词）
- Conversation Messages（对话消息）
- Available Tools（可用工具）
- Response Content（响应内容）
- Usage统计（token使用量）

#### JSON格式（机器可读）
位置：`runtime_memory/llm_logs/api_responses.jsonl`

JSONL格式（每行一个JSON对象），包含：
```json
{
  "timestamp": "2026-05-10T10:30:45.123456",
  "provider": "anthropic_compatible",
  "request": {
    "model": "claude-opus-4",
    "temperature": 0.7,
    "max_tokens": 4096,
    "messages_count": 5,
    "system_blocks_count": 3,
    "tools_count": 12
  },
  "status": "success",
  "response": {
    "id": "msg_01ABC123",
    "model": "claude-opus-4",
    "stop_reason": "end_turn",
    "usage": {
      "input_tokens": 2450,
      "output_tokens": 380,
      "cache_creation_input_tokens": 0,
      "cache_read_input_tokens": 2100
    },
    "content": [
      {
        "type": "text",
        "text": "响应内容..."
      }
    ]
  }
}
```

### 查看日志

#### 查看最新的API调用
```bash
tail -1 runtime_memory/llm_logs/api_responses.jsonl | jq .
```

#### 查看最近10次调用
```bash
tail -10 runtime_memory/llm_logs/api_responses.jsonl | jq .
```

#### 统计token使用
```bash
cat runtime_memory/llm_logs/api_responses.jsonl | jq '.response.usage'
```

#### 查看缓存命中率
```bash
cat runtime_memory/llm_logs/api_responses.jsonl | jq '.response.usage | select(.cache_read_input_tokens > 0)'
```

---

## 使用场景

### 场景1：调试提示词
1. 启用Debug模式
2. 运行程序，输入问题
3. 输入 `/debug` 查看完整上下文
4. 编辑System Prompt或Memory Context
5. 点击 `[4]` 测试修改后的效果
6. 满意后导出上下文，应用到实际配置

### 场景2：排查API问题
1. 启用Debug模式和日志记录
2. 运行程序，复现问题
3. 查看 `logs/debug/` 下的Markdown日志
4. 检查请求参数、响应内容、stop_reason
5. 查看 `api_responses.jsonl` 分析token使用和缓存情况

### 场景3：优化token成本
1. 启用JSON日志
2. 运行一段时间后，分析日志：
   ```bash
   cat runtime_memory/llm_logs/api_responses.jsonl | \
     jq '.response.usage | {input: .input_tokens, output: .output_tokens, cache_read: .cache_read_input_tokens}'
   ```
3. 查看缓存命中率，优化提示词结构

---

## 注意事项

1. **Debug模式会产生大量日志**，生产环境建议关闭
2. **日志文件可能包含敏感信息**，注意保护
3. **上下文编辑器的修改是临时的**，不会持久化到配置文件
4. **JSONL日志会持续追加**，定期清理旧日志：
   ```bash
   # 保留最近1000条
   tail -1000 runtime_memory/llm_logs/api_responses.jsonl > temp.jsonl
   mv temp.jsonl runtime_memory/llm_logs/api_responses.jsonl
   ```

---

## 快捷命令

```bash
# 启用Debug模式
sed -i 's/enabled: false/enabled: true/' config.yaml

# 查看最新API响应
tail -1 runtime_memory/llm_logs/api_responses.jsonl | jq .

# 查看最新Markdown日志
ls -t logs/debug/*.md | head -1 | xargs cat

# 统计今天的API调用次数
grep "$(date +%Y-%m-%d)" runtime_memory/llm_logs/api_responses.jsonl | wc -l

# 统计今天的token使用
grep "$(date +%Y-%m-%d)" runtime_memory/llm_logs/api_responses.jsonl | \
  jq -s 'map(.response.usage.output_tokens) | add'
```
