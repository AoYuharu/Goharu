# 可用工具列表与返回结果

## 工具概览

当前系统共有 **5 个工具**，分为两类：

### 核心工具（core_tools）
1. `run_cmd` - 执行 shell 命令
2. `getKnowledge` - 知识检索（未实现）

### 文件工具（file_tools）
3. `Grep` - 文本搜索
4. `Read` - 读取文件
5. `Edit` - 编辑文件

---

## 1. run_cmd

### 功能
执行 shell 命令并返回输出。

### 参数
```json
{
  "cmd": "string (必填)"  // 要执行的 shell 命令
}

```

### 返回结果
**类型**：字符串

**成功**：
```
命令的标准输出内容
```

**无输出**：
```
(no output)
```

### 示例

**调用**：
```json
{
  "tool": "run_cmd",
  "arguments": {
    "cmd": "echo Hello World"
  }
}
```

**返回**：
```
Hello World
```

**调用**：
```json
{
  "tool": "run_cmd",
  "arguments": {
    "cmd": "python date.py"
  }
}
```

**返回**：
```
2026-05-01
```

---

## 2. getKnowledge

### 功能
从本地文件检索知识（当前为占位实现）。

### 参数
```json
{
  "query": "string (必填)"  // 要检索的知识查询
}
```

### 返回结果
**类型**：字符串

**当前实现**：
```
Knowledge about '<query>' is not implemented yet.
```

### 示例

**调用**：
```json
{
  "tool": "getKnowledge",
  "arguments": {
    "query": "Python 日期处理"
  }
}
```

**返回**：
```
Knowledge about 'Python 日期处理' is not implemented yet.
```

---

## 3. Grep

### 功能
在文本文件中搜索指定模式，返回匹配的文件路径、行号和内容。

### 参数
```json
{
  "pattern": "string (必填)",           // 要查找的文本子串
  "path": "string (可选，默认 .)",      // 文件或目录路径
  "case_sensitive": "boolean (可选，默认 true)",  // 是否区分大小写
  "max_results": "integer (可选，默认 100)"       // 最多返回的匹配数量
}
```

### 返回结果
**类型**：JSON 字符串

**成功**：
```json
{
  "matches": [
    {
      "file": "E:\\TableHelper\\main.py",
      "line": 42,
      "content": "interrupt_requested = False  # 全局中断标志"
    },
    {
      "file": "E:\\TableHelper\\main.py",
      "line": 48,
      "content": "    interrupt_requested = True"
    }
  ],
  "count": 2,
  "truncated": false
}
```

**无匹配**：
```json
{
  "matches": [],
  "count": 0,
  "truncated": false
}
```

**错误**：
```json
{
  "error": "pattern 不能为空"
}
```

```json
{
  "error": "path 不存在",
  "path": "E:\\TableHelper\\nonexistent"
}
```

### 特性
- 自动跳过 `.git`、`.cache`、`__pycache__`、`.venv`、`node_modules` 等目录
- 只搜索文本文件（跳过二进制文件）
- 递归搜索目录
- **不授予 Edit 权限**（只查询，不能编辑）
- 并发安全：跳过正在写入的文件

### 示例

**调用**：
```json
{
  "tool": "Grep",
  "arguments": {
    "pattern": "interrupt_requested",
    "path": "E:\\TableHelper\\main.py"
  }
}
```

**返回**：
```json
{
  "matches": [
    {"file": "E:\\TableHelper\\main.py", "line": 42, "content": "interrupt_requested = False"},
    {"file": "E:\\TableHelper\\main.py", "line": 48, "content": "    interrupt_requested = True"}
  ],
  "count": 2,
  "truncated": false
}
```

---

## 4. Read

### 功能
读取文件的指定行范围，返回逐行内容。读取后会登记该范围，允许后续 Edit。

### 参数
```json
{
  "path": "string (必填)",                    // 文件路径
  "start_line": "integer (可选，默认 1)",     // 起始行号（1-based）
  "end_line": "integer (可选)",               // 结束行号（1-based，闭区间）
  "actor_id": "string (可选，默认 agent)"     // 调用者标识
}
```

### 返回结果
**类型**：JSON 字符串

**成功**：
```json
{
  "file": "E:\\TableHelper\\test.py",
  "start_line": 1,
  "end_line": 10,
  "total_lines": 50,
  "content": [
    {"line": 1, "text": "import os"},
    {"line": 2, "text": "import sys"},
    {"line": 3, "text": ""},
    {"line": 4, "text": "def main():"},
    {"line": 5, "text": "    print('Hello')"},
    ...
  ]
}
```

**错误**：
```json
{
  "error": "path 不是文件",
  "path": "E:\\TableHelper\\nonexistent.py"
}
```

```json
{
  "error": "start_line 必须大于等于 1"
}
```

```json
{
  "error": "文件正在写入中",
  "blocked_by": "agent正在写入中"
}
```

### 特性
- 使用 1-based 行号（第一行是 1，不是 0）
- 闭区间：`[start_line, end_line]` 包含两端
- 不传 `end_line` 则读到文件末尾
- 读取后会登记范围，允许后续 Edit
- 并发安全：多个 agent 可同时读，但写入时会阻塞

### 示例

**调用**：读取整个文件
```json
{
  "tool": "Read",
  "arguments": {
    "path": "E:\\TableHelper\\test.py"
  }
}
```

**调用**：读取指定范围
```json
{
  "tool": "Read",
  "arguments": {
    "path": "E:\\TableHelper\\test.py",
    "start_line": 10,
    "end_line": 20
  }
}
```

---

## 5. Edit

### 功能
按行号编辑文件。**必须先用 Read 读取对应范围**。

### 参数
```json
{
  "path": "string (必填)",                    // 文件路径
  "operation": "string (必填)",               // insert、delete 或 replace
  "start_line": "integer (必填)",             // 起始行号（1-based）
  "end_line": "integer (可选)",               // 结束行号（delete/replace 使用）
  "content": "string (可选)",                 // insert/replace 的内容
  "actor_id": "string (可选，默认 agent)"     // 调用者标识
}
```

### 操作类型

#### insert
在 `start_line` **前**插入 `content`。

#### delete
删除 `[start_line, end_line]` 范围的行。

#### replace
用 `content` 替换 `[start_line, end_line]` 范围的行。

### 返回结果
**类型**：JSON 字符串

**成功**：
```json
{
  "ok": true,
  "file": "E:\\TableHelper\\test.py",
  "operation": "replace",
  "changed_lines": 5
}
```

**错误**：
```json
{
  "error": "修改范围未被 Read 读取过",
  "file": "E:\\TableHelper\\test.py"
}
```

```json
{
  "error": "operation 只允许 insert、delete、replace"
}
```

```json
{
  "error": "文件正在写入中",
  "blocked_by": "agent正在写入中"
}
```

```json
{
  "error": "文件正在读取中",
  "blocked_by": "agent正在读取中"
}
```

### 特性
- 使用 1-based 行号
- **必须先 Read 对应范围**（Grep 不授予编辑权限）
- 保留原文件换行风格（CRLF/LF/CR）
- 并发安全：写入时独占，阻塞其他读写操作

### 示例

**场景 1：替换文件内容**

1. 先读取：
```json
{
  "tool": "Read",
  "arguments": {
    "path": "E:\\TableHelper\\test.py",
    "start_line": 1,
    "end_line": 5
  }
}
```

2. 再替换：
```json
{
  "tool": "Edit",
  "arguments": {
    "path": "E:\\TableHelper\\test.py",
    "operation": "replace",
    "start_line": 1,
    "end_line": 5,
    "content": "import os\nimport sys\n\ndef main():\n    print('Hello World')"
  }
}
```

**返回**：
```json
{
  "ok": true,
  "file": "E:\\TableHelper\\test.py",
  "operation": "replace",
  "changed_lines": 5
}
```

**场景 2：插入新行**

1. 先读取：
```json
{
  "tool": "Read",
  "arguments": {
    "path": "E:\\TableHelper\\test.py",
    "start_line": 1,
    "end_line": 10
  }
}
```

2. 在第 5 行前插入：
```json
{
  "tool": "Edit",
  "arguments": {
    "path": "E:\\TableHelper\\test.py",
    "operation": "insert",
    "start_line": 5,
    "content": "# 这是新插入的注释"
  }
}
```

**场景 3：删除行**

1. 先读取：
```json
{
  "tool": "Read",
  "arguments": {
    "path": "E:\\TableHelper\\test.py",
    "start_line": 10,
    "end_line": 20
  }
}
```

2. 删除第 15-17 行：
```json
{
  "tool": "Edit",
  "arguments": {
    "path": "E:\\TableHelper\\test.py",
    "operation": "delete",
    "start_line": 15,
    "end_line": 17
  }
}
```

---

## 工具使用流程

### 典型工作流

#### 1. 搜索代码
```
Grep → 找到目标文件和行号
```

#### 2. 查看代码
```
Read → 读取文件内容
```

#### 3. 修改代码
```
Edit → 修改文件（必须先 Read）
```

#### 4. 验证修改
```
run_cmd → 运行测试或检查
```

### 示例：修改 Python 脚本

**任务**：找到并修改 `main.py` 中的 `max_depth` 变量

**步骤 1：搜索**
```json
{
  "tool": "Grep",
  "arguments": {
    "pattern": "max_depth",
    "path": "E:\\TableHelper"
  }
}
```

**返回**：
```json
{
  "matches": [
    {"file": "E:\\TableHelper\\main.py", "line": 315, "content": "    max_depth = int(config.get(\"mcp.maxDepth\", 8) or 8)"}
  ],
  "count": 1,
  "truncated": false
}
```

**步骤 2：读取上下文**
```json
{
  "tool": "Read",
  "arguments": {
    "path": "E:\\TableHelper\\main.py",
    "start_line": 310,
    "end_line": 320
  }
}
```

**步骤 3：修改**
```json
{
  "tool": "Edit",
  "arguments": {
    "path": "E:\\TableHelper\\main.py",
    "operation": "replace",
    "start_line": 315,
    "end_line": 315,
    "content": "    max_depth = int(config.get(\"mcp.maxDepth\", 10) or 10)"
  }
}
```

**步骤 4：验证**
```json
{
  "tool": "run_cmd",
  "arguments": {
    "cmd": "python -m py_compile main.py"
  }
}
```

---

## 并发控制

### 多读单写锁

文件工具实现了多读单写锁机制：

- **多个 agent 可同时读取同一文件**
- **写入时独占，阻塞所有读写**
- **读取时阻塞写入**

### 冲突处理

当发生并发冲突时，工具返回 JSON error：

```json
{
  "error": "文件正在写入中",
  "blocked_by": "agent正在写入中"
}
```

**建议**：Agent 应稍后重试或先读取。

---

## 注意事项

### 1. Read 是 Edit 的前提
- ❌ 错误：直接 Edit 未读取的文件
- ✅ 正确：先 Read，再 Edit

### 2. Grep 不授予编辑权限
- Grep 只查询，不能作为 Edit 的前提
- 必须用 Read 读取后才能 Edit

### 3. 行号从 1 开始
- 所有行号都是 1-based
- 第一行是 1，不是 0

### 4. 闭区间
- `[start_line, end_line]` 包含两端
- 例如：`[1, 5]` 包含第 1、2、3、4、5 行

### 5. 并发安全
- 工具会自动处理并发冲突
- 返回 error 时应重试或调整策略

---

## 工具配置

### config.yaml

```yaml
tools:
  runtime: in_process        # 工具运行时模式
  builtin_modules:           # 内置工具模块
    - Tools.builtin.core_tools
    - Tools.builtin.file_tools
```

### 添加新工具

1. 在 `Tools/builtin/` 下创建新模块
2. 使用 `registry.register()` 注册工具
3. 在 `config.yaml` 中添加模块路径

---

## 总结

| 工具 | 功能 | 返回类型 | 并发 |
|------|------|----------|------|
| `run_cmd` | 执行命令 | 字符串 | - |
| `getKnowledge` | 知识检索 | 字符串 | - |
| `Grep` | 文本搜索 | JSON | 多读 |
| `Read` | 读取文件 | JSON | 多读 |
| `Edit` | 编辑文件 | JSON | 单写 |

**核心原则**：
- 先搜索（Grep）→ 再读取（Read）→ 后修改（Edit）→ 最后验证（run_cmd）
- 所有文件操作使用 1-based 行号
- Edit 必须先 Read
- 并发冲突时重试
