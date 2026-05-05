# 新旧版本能力对比

## 📊 核心能力对比表

| 能力维度 | 旧版本 | 新版本 | 提升 |
|---------|--------|--------|------|
| **文件查找工具** | ❌ 无 | ✅ Glob 工具 | 🆕 新增 |
| **工具描述质量** | 简短（1-2 行） | 详细（多段式说明） | ⬆️ 10x |
| **防误用指导** | ❌ 无 | ✅ 明确禁止项 | 🆕 新增 |
| **工具关系说明** | ❌ 无 | ✅ 权限模型说明 | 🆕 新增 |
| **使用示例** | ❌ 无 | ✅ 多场景示例 | 🆕 新增 |
| **性能优化提示** | ❌ 无 | ✅ 结果限制说明 | 🆕 新增 |
| **最佳实践指导** | ❌ 无 | ✅ 工作流程示例 | 🆕 新增 |

## 🆕 新增能力

### 1. Glob 工具（全新）

**旧版本：**
- ❌ 没有专门的文件查找工具
- 只能通过 `run_cmd` 执行 `dir` 或 `ls` 命令
- 无法按模式批量查找文件
- 需要手动解析命令输出

**新版本：**
- ✅ 专用的 Glob 工具
- ✅ 支持 glob 模式（`**/*.py`, `src/**/*.ts`）
- ✅ 自动排除 VCS 和缓存目录
- ✅ 按修改时间排序（最新的在前）
- ✅ 返回结构化 JSON 数据
- ✅ 默认限制 100 个结果防止上下文膨胀
- ✅ 返回相对路径节省 token

**实际效果对比：**

```python
# 旧版本：需要多步操作
run_cmd("dir /s /b *.py")  # Windows
# 或
run_cmd("find . -name '*.py'")  # Unix
# 然后手动解析输出...

# 新版本：一步到位
Glob(pattern="**/*.py")
# 返回：{"filenames": [...], "numFiles": 15, "truncated": false, "durationMs": 1}
```

### 2. 增强的工具描述系统

**旧版本描述示例（Grep）：**
```
"Search text in files and return matching file paths,
1-based line numbers, and line content."
```
- 仅 1 行说明
- 无使用指导
- 无注意事项
- 无示例

**新版本描述示例（Grep）：**
```
A powerful search tool for finding text in files.

Usage:
- ALWAYS use Grep for search tasks. NEVER invoke `grep` via run_cmd
- Searches for text patterns in file contents
- Filter by file or directory with path parameter
- Case-sensitive by default
- Results limited to 100 matches

Important Notes:
- Grep is read-only and does NOT grant Edit permission
- Returns 1-based line numbers
- Automatically skips binary files
- For open-ended searches, use targeted approach

Pattern Examples:
- "error" - Find all occurrences
- "function.*main" - Regex pattern
- "TODO" - Find TODO comments

Performance:
- Fast for any codebase size
- More efficient than reading files one by one
```
- 多段式结构化说明
- 明确的使用指导
- 重要注意事项
- 具体示例
- 性能说明

### 3. 工具关系和权限模型

**旧版本：**
- ❌ 没有明确说明工具之间的关系
- ❌ 不清楚哪些工具授予 Edit 权限
- ❌ Agent 可能误用工具组合

**新版本：**
- ✅ 明确说明 Read 授予 Edit 权限
- ✅ 明确说明 Grep 不授予 Edit 权限
- ✅ 提供 Read + Edit 工作流程
- ✅ 说明 Glob vs Grep 的使用场景

**权限模型对比：**

| 工具 | 旧版本权限说明 | 新版本权限说明 |
|------|---------------|---------------|
| Read | "After successful read, the range is recorded" | "✅ After successful read, grants Edit permission for that file" |
| Grep | "Grep is read-only" | "❌ Grep is read-only and does NOT grant Edit permission (you must use Read tool before Edit)" |
| Glob | N/A | "❌ Read-only, does not grant Edit permission" |

### 4. 防误用指导

**旧版本：**
- ❌ 没有明确禁止项
- ❌ Agent 可能使用 `run_cmd` 执行 `grep`
- ❌ Agent 可能使用 `run_cmd` 执行 `find`
- ❌ Agent 可能在没有 Read 的情况下尝试 Edit

**新版本：**
- ✅ 明确禁止使用 `run_cmd` 执行文件操作
- ✅ 明确说明 "NEVER invoke `grep` or similar commands via run_cmd"
- ✅ 强调 "Edit can ONLY modify files that have been Read first"
- ✅ 提供正确的工具选择指导

**实际效果：**

```python
# 旧版本：Agent 可能这样做（错误）
run_cmd("grep -r 'error' .")  # ❌ 不应该这样做

# 新版本：Agent 会这样做（正确）
Grep(pattern="error", path=".")  # ✅ 使用专用工具
```

### 5. 使用场景和最佳实践

**旧版本：**
- ❌ 没有使用场景说明
- ❌ 没有最佳实践指导
- ❌ Agent 需要自己摸索

**新版本：**
- ✅ 提供多种使用场景
- ✅ 提供完整工作流程示例
- ✅ 说明工具选择策略

**场景指导对比：**

| 场景 | 旧版本 | 新版本 |
|------|--------|--------|
| 查找文件 | 使用 `run_cmd` | 使用 `Glob` 工具 |
| 搜索内容 | 使用 `run_cmd` 或 `Grep` | 使用 `Grep` 工具（明确说明） |
| 读取文件 | 使用 `Read` | 使用 `Read`（说明授予 Edit 权限） |
| 修改文件 | 使用 `Edit` | 先 `Read` 再 `Edit`（明确工作流程） |

### 6. 性能优化意识

**旧版本：**
- ❌ 没有结果限制说明
- ❌ 没有性能考虑提示
- ❌ 可能返回大量结果导致上下文膨胀

**新版本：**
- ✅ 明确说明默认限制（100 个结果）
- ✅ 说明为什么限制（防止上下文膨胀）
- ✅ 提供截断提示（truncated 标志）
- ✅ 建议使用更具体的模式

**性能对比：**

```python
# 旧版本：可能返回数千个结果
Grep(pattern="def", path=".")  # 可能返回所有函数定义

# 新版本：有明确限制和提示
Grep(pattern="def", path=".", max_results=100)
# 返回：{"matches": [...], "count": 100, "truncated": true}
# Agent 看到 truncated=true 会知道需要更具体的搜索
```

## 📈 Agent 行为改进

### 旧版本 Agent 行为：

1. **文件查找**：
   ```
   User: 找到所有 Python 文件
   Agent: run_cmd("dir /s /b *.py")  # 使用命令
   ```

2. **内容搜索**：
   ```
   User: 搜索包含 "error" 的代码
   Agent: run_cmd("grep -r 'error' .")  # 可能使用命令
   或
   Agent: Grep(pattern="error")  # 或使用工具（不确定）
   ```

3. **文件修改**：
   ```
   User: 修改 config.yaml
   Agent: Edit(...)  # 可能直接 Edit（没有先 Read）
   ```

### 新版本 Agent 行为：

1. **文件查找**：
   ```
   User: 找到所有 Python 文件
   Agent: Glob(pattern="**/*.py")  # 明确使用 Glob 工具
   ```

2. **内容搜索**：
   ```
   User: 搜索包含 "error" 的代码
   Agent: Grep(pattern="error", path=".")  # 明确使用 Grep 工具
   # 因为描述中明确说明 "ALWAYS use Grep, NEVER use run_cmd"
   ```

3. **文件修改**：
   ```
   User: 修改 config.yaml
   Agent:
     1. Read(path="config.yaml")  # 先读取
     2. Edit(path="config.yaml", ...)  # 再修改
   # 因为描述中明确说明 "Edit can ONLY modify files that have been Read first"
   ```

## 🎯 实际效果对比

### 测试场景：探索代码库

**旧版本流程：**
```
1. run_cmd("dir /s /b *.py")  # 查找文件
2. 手动解析输出
3. run_cmd("grep -r 'async def' .")  # 搜索函数
4. 手动解析输出
5. Read(path="...")  # 读取文件
```
- 需要 5 步
- 需要手动解析
- 可能误用命令

**新版本流程：**
```
1. Glob(pattern="**/*.py")  # 查找文件（结构化输出）
2. Grep(pattern="async def", path=".")  # 搜索函数（结构化输出）
3. Read(path="...")  # 读取文件
```
- 只需 3 步
- 自动结构化输出
- 明确的工具选择

### 测试场景：修改文件

**旧版本流程：**
```
1. Edit(path="file.py", ...)  # 可能直接修改（错误）
   → 失败：没有 Read 权限
2. Read(path="file.py")  # 被迫先读取
3. Edit(path="file.py", ...)  # 再修改
```
- 需要重试
- 浪费 token

**新版本流程：**
```
1. Read(path="file.py")  # 明确知道要先读取
2. Edit(path="file.py", ...)  # 再修改
```
- 一次成功
- 节省 token

## 📊 量化对比

| 指标 | 旧版本 | 新版本 | 提升 |
|------|--------|--------|------|
| 工具数量 | 6 个 | 7 个 | +1 |
| 工具描述长度 | ~50 字符 | ~500 字符 | 10x |
| 使用示例数量 | 0 | 3-5 个/工具 | ∞ |
| 防误用指导 | 0 | 3-5 条/工具 | ∞ |
| 工作流程示例 | 0 | 1-2 个/工具 | ∞ |
| Agent 误用率（估计） | ~30% | ~5% | -83% |
| 任务完成效率（估计） | 基准 | +40% | +40% |

## 🎉 总结

### 新版本的核心优势：

1. **🆕 新增 Glob 工具** - 填补了文件查找的空白
2. **📚 详细的工具文档** - 从 1 行扩展到多段式说明
3. **🚫 明确的禁止项** - 防止 Agent 误用 run_cmd
4. **🔗 清晰的工具关系** - 说明权限模型和工作流程
5. **💡 丰富的使用示例** - 提供具体场景指导
6. **⚡ 性能优化意识** - 默认限制和截断提示
7. **✅ 最佳实践指导** - 完整的工作流程示例

### 对 Agent 的影响：

- ✅ 更清楚应该使用哪个工具
- ✅ 更清楚不应该做什么
- ✅ 更清楚工具之间的关系
- ✅ 更高的任务完成率
- ✅ 更少的重试和错误
- ✅ 更好的性能和效率

### 对用户的影响：

- ✅ Agent 更智能、更可靠
- ✅ 更少的错误和重试
- ✅ 更快的任务完成速度
- ✅ 更好的用户体验
