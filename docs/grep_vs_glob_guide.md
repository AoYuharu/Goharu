# Grep vs Glob 使用场景详解

## 🎯 核心区别

| 维度 | Glob | Grep |
|------|------|------|
| **搜索目标** | 文件名 | 文件内容 |
| **搜索方式** | 模式匹配（文件路径） | 文本搜索（文件内容） |
| **返回结果** | 文件路径列表 | 匹配的行（文件路径 + 行号 + 内容） |
| **速度** | 非常快（只扫描文件名） | 较慢（需要读取文件内容） |
| **适用场景** | 知道文件名模式 | 知道文件内容特征 |

## 📋 使用场景对比

### Glob 工具 - 按文件名查找

**使用场景：**
1. ✅ 查找特定类型的文件
2. ✅ 探索项目结构
3. ✅ 批量定位文件
4. ✅ 按文件扩展名筛选
5. ✅ 按目录结构查找

**典型示例：**

```python
# 场景 1: 查找所有 Python 文件
Glob(pattern="**/*.py")
# 返回：["main.py", "Agent/ActorAgent.py", "Tools/builtin/file_tools.py", ...]

# 场景 2: 查找所有测试文件
Glob(pattern="test_*.py")
# 返回：["test_tools.py", "test_agent.py", ...]

# 场景 3: 查找特定目录下的文件
Glob(pattern="Agent/**/*.py")
# 返回：["Agent/ActorAgent.py", "Agent/ReflectionAgent.py", ...]

# 场景 4: 查找配置文件
Glob(pattern="**/*.yaml")
# 返回：["config.yaml", "docker-compose.yaml", ...]

# 场景 5: 查找文档文件
Glob(pattern="docs/**/*.md")
# 返回：["docs/README.md", "docs/api.md", ...]
```

**何时使用 Glob：**
- ✅ 你知道文件名的模式（如 `test_*.py`）
- ✅ 你想快速浏览项目结构
- ✅ 你需要找到特定类型的所有文件
- ✅ 你不关心文件内容，只关心文件位置

### Grep 工具 - 按内容搜索

**使用场景：**
1. ✅ 搜索特定的代码片段
2. ✅ 查找函数或类定义
3. ✅ 搜索错误消息或日志
4. ✅ 查找 TODO 注释
5. ✅ 搜索配置项或变量

**典型示例：**

```python
# 场景 1: 查找函数定义
Grep(pattern="def process_data", path=".")
# 返回：[
#   {"file": "utils.py", "line": 42, "content": "def process_data(input):"},
#   {"file": "main.py", "line": 15, "content": "    def process_data(self, data):"}
# ]

# 场景 2: 查找类定义
Grep(pattern="class Agent", path="Agent/")
# 返回：[
#   {"file": "Agent/ActorAgent.py", "line": 11, "content": "class ActorAgent(LargeLanguageModel):"},
#   {"file": "Agent/ReflectionAgent.py", "line": 9, "content": "class ReflectionAgent(LargeLanguageModel):"}
# ]

# 场景 3: 查找 TODO 注释
Grep(pattern="TODO", path=".")
# 返回：[
#   {"file": "main.py", "line": 100, "content": "    # TODO: 优化性能"},
#   {"file": "agent.py", "line": 50, "content": "    # TODO: 添加错误处理"}
# ]

# 场景 4: 查找配置项
Grep(pattern="api_key", path=".", case_sensitive=False)
# 返回：[
#   {"file": "config.yaml", "line": 5, "content": "  api_key: xxx"},
#   {"file": "settings.py", "line": 20, "content": "API_KEY = os.getenv('API_KEY')"}
# ]

# 场景 5: 查找错误处理
Grep(pattern="except Exception", path=".")
# 返回：[
#   {"file": "main.py", "line": 150, "content": "    except Exception as e:"},
#   {"file": "utils.py", "line": 80, "content": "except Exception:"}
# ]
```

**何时使用 Grep：**
- ✅ 你知道文件内容的特征（如函数名、变量名）
- ✅ 你想找到特定代码的位置
- ✅ 你需要搜索跨文件的内容
- ✅ 你不知道文件名，但知道内容

## 🔄 组合使用场景

很多时候，Glob 和 Grep 需要配合使用：

### 场景 1: 先定位文件类型，再搜索内容

```python
# 步骤 1: 使用 Glob 找到所有 Python 文件
Glob(pattern="**/*.py")
# 结果：找到 50 个 Python 文件

# 步骤 2: 使用 Grep 在这些文件中搜索特定函数
Grep(pattern="async def", path=".")
# 结果：找到 15 个异步函数定义
```

### 场景 2: 先搜索内容，再读取文件

```python
# 步骤 1: 使用 Grep 找到包含特定内容的文件
Grep(pattern="class Config", path=".")
# 结果：{"file": "config.py", "line": 10, ...}

# 步骤 2: 使用 Read 读取完整文件
Read(path="config.py")
# 结果：读取整个文件内容
```

### 场景 3: 探索未知代码库

```python
# 步骤 1: 使用 Glob 了解项目结构
Glob(pattern="**/*.py")
# 结果：了解有哪些 Python 文件

# 步骤 2: 使用 Grep 找到入口点
Grep(pattern="if __name__ == '__main__'", path=".")
# 结果：找到主程序入口

# 步骤 3: 使用 Read 读取主程序
Read(path="main.py")
# 结果：理解程序逻辑
```

## ❌ Grep 的限制

### 当前 Grep 工具的限制：

1. **只支持文本文件**
   ```python
   # ❌ 不支持 PDF 文件
   Grep(pattern="关键词", path="document.pdf")
   # 结果：跳过（PDF 包含二进制数据，被 _is_probably_text_file 过滤）

   # ❌ 不支持 Word 文档
   Grep(pattern="关键词", path="document.docx")
   # 结果：跳过（二进制格式）

   # ❌ 不支持图片
   Grep(pattern="text", path="image.png")
   # 结果：跳过（二进制格式）
   ```

2. **二进制文件检测机制**
   ```python
   # 代码实现（file_tools.py:155-161）
   def _is_probably_text_file(path_obj: Path) -> bool:
       with path_obj.open("rb") as file_obj:
           chunk = file_obj.read(4096)
       return b"\x00" not in chunk  # 包含空字节 = 二进制文件
   ```

   - 读取文件前 4096 字节
   - 如果包含空字节（`\x00`），判定为二进制文件
   - 二进制文件会被自动跳过

3. **只支持简单文本搜索**
   ```python
   # ✅ 支持：简单文本匹配
   Grep(pattern="def main", path=".")

   # ❌ 不支持：正则表达式
   Grep(pattern="def \w+\(.*\)", path=".")  # 不会按正则处理

   # ❌ 不支持：多行匹配
   Grep(pattern="class.*\n.*def", path=".")  # 只在单行内搜索
   ```

## 🔧 如何搜索 PDF 等文档？

### 方案 1: 使用 Read 工具（推荐）

Read 工具支持多种文件格式：

```python
# ✅ 读取 PDF 文件
Read(path="document.pdf", pages="1-5")
# 返回：PDF 内容（如果模型支持）

# ✅ 读取图片文件
Read(path="screenshot.png")
# 返回：图片的 base64 编码

# ✅ 读取 Jupyter Notebook
Read(path="analysis.ipynb")
# 返回：所有 cell 的内容
```

### 方案 2: 先转换再搜索

```python
# 步骤 1: 使用外部工具转换 PDF 为文本
run_cmd("pdftotext document.pdf document.txt")

# 步骤 2: 使用 Grep 搜索文本文件
Grep(pattern="关键词", path="document.txt")
```

### 方案 3: 使用专门的搜索工具

```python
# 如果需要搜索 PDF，可以使用专门的工具
# 例如：pdfgrep（需要安装）
run_cmd("pdfgrep '关键词' document.pdf")
```

## 📊 性能对比

| 操作 | Glob | Grep | Read |
|------|------|------|------|
| 查找 1000 个文件 | ~10ms | N/A | N/A |
| 搜索 1000 个文件内容 | N/A | ~500ms | N/A |
| 读取单个文件 | N/A | N/A | ~5ms |
| 支持 PDF | ❌ | ❌ | ✅ |
| 支持图片 | ❌ | ❌ | ✅ |
| 支持二进制文件 | ❌ | ❌ | 部分支持 |

## 🎯 决策树

```
需要查找文件？
├─ 知道文件名模式？
│  └─ 使用 Glob (pattern="**/*.py")
│
└─ 知道文件内容？
   ├─ 是文本文件？
   │  └─ 使用 Grep (pattern="关键词")
   │
   └─ 是 PDF/图片/特殊格式？
      └─ 使用 Read (path="file.pdf")
```

## 💡 最佳实践

### 1. 优先使用 Glob 缩小范围

```python
# ❌ 不好：在整个项目中搜索
Grep(pattern="def main", path=".")  # 可能搜索数千个文件

# ✅ 更好：先用 Glob 缩小范围
Glob(pattern="**/main*.py")  # 只找主程序文件
Grep(pattern="def main", path="main.py")  # 只搜索相关文件
```

### 2. 使用 Grep 定位，使用 Read 查看

```python
# 步骤 1: 用 Grep 找到目标
result = Grep(pattern="class Config", path=".")
# 结果：{"file": "config.py", "line": 10}

# 步骤 2: 用 Read 读取完整上下文
Read(path="config.py", start_line=1, end_line=50)
```

### 3. 对于 PDF 等文档，直接使用 Read

```python
# ❌ 不要用 Grep 搜索 PDF
Grep(pattern="关键词", path="document.pdf")  # 会被跳过

# ✅ 使用 Read 读取 PDF
Read(path="document.pdf", pages="1-10")
# 然后在返回的内容中搜索关键词
```

## 📝 总结

### Glob 适合：
- ✅ 按文件名查找
- ✅ 探索项目结构
- ✅ 快速定位文件类型
- ✅ 不关心文件内容

### Grep 适合：
- ✅ 按内容搜索
- ✅ 查找代码片段
- ✅ 只支持文本文件
- ❌ 不支持 PDF/图片/二进制文件

### Read 适合：
- ✅ 读取完整文件
- ✅ 支持 PDF、图片、Notebook
- ✅ 授予 Edit 权限
- ✅ 查看文件详细内容

**关键点：Grep 目前不支持 PDF 等二进制文档，需要使用 Read 工具来处理这些文件。**
