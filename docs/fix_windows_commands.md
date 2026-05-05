# 修复：Windows 命令语法问题

## 问题描述

用户报告所有工具调用都失败，日志显示：
- `此时不应有 <<。` - heredoc 语法在 Windows 上不支持
- `'printf' 不是内部或外部命令` - printf 在 Windows 上不可用
- `字符串缺少终止符: '@。` - PowerShell heredoc 语法错误

**根本原因**：模型在 Windows 环境下使用了 Unix/Linux 的 shell 语法。

## 根本原因分析

1. **缺少环境信息**
   - `run_cmd` 工具描述只有 "Run shell command"，没有说明是 Windows 环境
   - Actor 系统提示没有注入操作系统信息
   - 模型默认使用 Unix/Linux 语法

2. **工具描述不明确**
   - 没有说明可用的命令
   - 没有提供 Windows 命令示例
   - 没有警告禁止使用 Unix 命令

## 修复方案

### 1. 增强 `run_cmd` 工具描述

**文件**: `Tools/builtin/core_tools.py`

**修改前**：
```python
registry.register(
    name="run_cmd",
    description="Run shell command",
    arguments_schema={...},
    handler=run_cmd,
    group="core",
)
```

**修改后**：
```python
registry.register(
    name="run_cmd",
    description="""Run shell command on Windows (cmd.exe).

IMPORTANT - Windows Environment:
- This is a Windows system, use Windows command syntax
- Use Windows commands: dir, type, echo, copy, del, mkdir, etc.
- DO NOT use Unix commands: ls, cat, printf, touch, rm, etc.
- DO NOT use heredoc syntax (<<EOF)
- For multi-line files, use: echo line1 > file.txt && echo line2 >> file.txt
- Or use PowerShell: powershell -Command "content" | Out-File file.txt

Examples:
- List files: dir or dir /b
- Read file: type filename.txt
- Create file: echo content > file.txt
- Append: echo more >> file.txt
- Check existence: if exist file.txt echo exists""",
    arguments_schema={
        "type": "object",
        "properties": {
            "cmd": {
                "type": "string",
                "description": "Windows shell command to execute (cmd.exe syntax)",
            },
        },
        "required": ["cmd"],
    },
    handler=run_cmd,
    group="core",
)
```

### 2. 在 Actor 系统提示中添加环境信息

**文件**: `prompts/actor/base.md`

**在开头添加**：
```markdown
## 运行环境

**操作系统**: Windows
**Shell**: cmd.exe (Windows命令提示符)

**重要**：你必须使用Windows命令语法，不要使用Unix/Linux命令：
- ✅ 使用: `dir`, `type`, `echo`, `copy`, `del`, `mkdir`
- ❌ 禁止: `ls`, `cat`, `printf`, `touch`, `rm`, `<<EOF` heredoc
- 创建文件: `echo content > file.txt`
- 追加内容: `echo more >> file.txt`
- 多行文件: `echo line1 > file.txt && echo line2 >> file.txt`
```

**更新工具使用指南**：
```markdown
## 工具使用指南

- **创建文件**：调用 `run_cmd` 使用 Windows 命令
  - 单行: `echo content > file.txt`
  - 多行: `echo line1 > file.txt && echo line2 >> file.txt`
- **执行脚本**：调用 `run_cmd` 执行，等待真实输出
  - Python: `python script.py`
  - PowerShell: `powershell -File script.ps1`
- **验证操作**：调用 `run_cmd` 使用 Windows 命令验证
  - 列出文件: `dir` 或 `dir /b`
  - 查看文件: `type filename.txt`
  - 检查存在: `if exist file.txt echo exists`
```

## 常见 Windows 命令对照表

| 操作 | Unix/Linux | Windows (cmd.exe) |
|------|-----------|-------------------|
| 列出文件 | `ls` | `dir` 或 `dir /b` |
| 查看文件 | `cat file.txt` | `type file.txt` |
| 创建文件 | `echo "text" > file.txt` | `echo text > file.txt` |
| 追加内容 | `echo "text" >> file.txt` | `echo text >> file.txt` |
| 复制文件 | `cp src dst` | `copy src dst` |
| 删除文件 | `rm file.txt` | `del file.txt` |
| 创建目录 | `mkdir dir` | `mkdir dir` |
| 删除目录 | `rm -rf dir` | `rmdir /s /q dir` |
| 检查存在 | `[ -f file.txt ]` | `if exist file.txt echo exists` |
| 多行文件 | `cat <<EOF > file.txt` | `echo line1 > file.txt && echo line2 >> file.txt` |

## 创建 Python 文件的正确方式

### ❌ 错误（Unix heredoc）
```bash
cat <<EOF > script.py
from datetime import datetime
print(datetime.now())
EOF
```

### ✅ 正确（Windows 方式 1：逐行 echo）
```bash
echo from datetime import datetime > script.py && echo print(datetime.now()) >> script.py
```

### ✅ 正确（Windows 方式 2：PowerShell）
```bash
powershell -Command "@'
from datetime import datetime
print(datetime.now())
'@ | Out-File -Encoding utf8 script.py"
```

### ✅ 正确（Windows 方式 3：临时文件）
```bash
echo from datetime import datetime > temp.txt && echo print(datetime.now()) >> temp.txt && copy temp.txt script.py && del temp.txt
```

## 测试验证

### 测试 1：创建简单文件
```bash
python main.py
You > 创建一个文件 test.txt，内容为 "Hello World"
```

**预期**：模型应该使用 `echo Hello World > test.txt`

### 测试 2：创建 Python 脚本
```bash
You > 写一个 Python 文件，输出当前时间
```

**预期**：模型应该使用：
```bash
echo from datetime import datetime > time.py && echo print(datetime.now()) >> time.py
```

或者：
```bash
powershell -Command "@'
from datetime import datetime
print(datetime.now())
'@ | Out-File -Encoding utf8 time.py"
```

### 测试 3：验证文件
```bash
You > 验证文件是否创建成功
```

**预期**：模型应该使用 `dir time.py` 或 `type time.py`

## 文件修改清单

- ✅ `Tools/builtin/core_tools.py`：增强 `run_cmd` 工具描述
- ✅ `prompts/actor/base.md`：添加运行环境信息和 Windows 命令指南

## 验证

```bash
# 语法检查
python -m py_compile Tools/builtin/core_tools.py

# 运行程序
python main.py

# 测试命令
You > 创建一个文件 hello.txt，内容为 "Hello"
```

## 后续改进建议

### 1. 动态环境检测
在启动时检测操作系统，动态注入环境信息：
```python
import platform
os_info = platform.system()  # 'Windows', 'Linux', 'Darwin'
```

### 2. 跨平台命令抽象
创建跨平台工具，自动适配命令：
```python
def create_file(path, content):
    if platform.system() == "Windows":
        return f"echo {content} > {path}"
    else:
        return f"echo '{content}' > {path}"
```

### 3. 命令验证
在执行前验证命令是否适合当前平台：
```python
WINDOWS_ONLY = ["dir", "type", "copy", "del"]
UNIX_ONLY = ["ls", "cat", "cp", "rm"]

def validate_command(cmd, os_type):
    if os_type == "Windows" and any(c in cmd for c in UNIX_ONLY):
        raise ValueError(f"Unix command not supported on Windows: {cmd}")
```
