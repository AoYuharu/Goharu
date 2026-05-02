# 命令安全检查系统

## 概述

为了防止 Agent 执行危险的系统命令，项目实现了多层安全检查机制，可以拦截可能造成系统损坏、数据丢失或安全风险的命令。

## 安全层级

### 1. 危险命令黑名单（直接拒绝）

以下类型的命令会被直接拒绝执行：

#### 系统关机/重启
- `shutdown`, `restart`, `reboot`, `poweroff`, `halt`

#### 危险删除操作
- `rm -rf` (Unix)
- `del /s`, `del /q` (Windows)
- `rmdir /s` (Windows)
- `format`, `diskpart`

#### 磁盘操作
- `fdisk`, `mkfs`, `dd`

#### 权限提升
- `sudo`, `runas`

#### 注册表危险操作
- `reg delete`, `reg add`

#### 批量进程终止
- `taskkill /f`
- `killall`, `pkill`

#### 危险脚本执行
- `powershell -encodedcommand`
- `powershell -enc`

### 2. 需要确认的命令（可配置）

以下命令需要用户明确确认（当前配置为直接拒绝）：
- `del` - 删除文件
- `rmdir` - 删除目录
- `taskkill` - 终止进程
- `reg` - 注册表操作

### 3. 文件操作限制（已有机制）

以下命令被重定向到专用工具：
- `echo >`, `cat`, `head`, `tail`, `sed`, `awk`
- 必须使用 `Write`, `Read`, `Edit`, `Grep` 工具

## 配置

在 `config.yaml` 中配置安全选项：

```yaml
tools:
  security:
    # 是否启用安全检查
    enabled: true

    # 是否允许交互式确认（false 则直接拒绝）
    allow_confirmation: false

    # 危险命令黑名单
    dangerous_commands:
      - shutdown
      - restart
      - "rm -rf"
      - "del /s"
      - format
      # ... 更多命令

    # 需要用户确认的命令
    require_confirmation:
      - del
      - rmdir
      - taskkill
      - "reg "
```

## 实现细节

### 架构

```
run_cmd (MCP tool)
    ↓
check_command_safety (Tools/security.py)
    ↓
CommandSecurityChecker
    ↓
Pattern Matching + Config Check
    ↓
[ALLOW] or [BLOCK with error message]
```

### 模式匹配逻辑

1. **精确命令匹配**：
   - `shutdown` 匹配 `shutdown /s`
   - 不匹配 `echo shutdown`（安全上下文）

2. **多词模式匹配**：
   - `rm -rf` 匹配 `rm -rf /tmp`
   - 不匹配 `echo rm -rf`（安全上下文）

3. **单词边界检查**：
   - `format` 匹配 `format C:`
   - 不匹配 `reformat code`（不同命令）

4. **正则表达式支持**：
   - 可以使用 `regex:` 前缀定义复杂模式

### 错误消息

当命令被拦截时，返回详细的错误消息：

```
🚫 SECURITY BLOCK: Dangerous command detected!

Command: shutdown /s /t 0
Matched pattern: shutdown

This command is blocked because it could cause:
- System shutdown or restart
- Irreversible data deletion
- System configuration damage
- Security vulnerabilities

REASON: This operation is too dangerous to execute automatically.

If you need to perform system maintenance:
1. Ask the user for explicit permission
2. Explain what the command will do
3. Suggest safer alternatives if available

DO NOT attempt to bypass this security check.
```

## 测试

运行完整的安全测试套件：

```bash
python test_command_security.py
```

测试覆盖：
- ✓ 危险命令拦截（19 个测试）
- ✓ 安全命令放行（18 个测试）
- ✓ 确认命令处理（5 个测试）
- ✓ 模式匹配逻辑（8 个测试）

**测试结果：100% 通过**

## 使用示例

### 被拦截的命令

```python
# Agent 尝试执行
run_cmd("shutdown /s /t 0")

# 返回
"🚫 SECURITY BLOCK: Dangerous command detected!
Command: shutdown /s /t 0
Matched pattern: shutdown
..."
```

### 允许的命令

```python
# Agent 执行
run_cmd("dir")
run_cmd("python script.py")
run_cmd("ping 8.8.8.8")

# 正常执行并返回结果
```

### 需要确认的命令

```python
# Agent 尝试执行
run_cmd("del file.txt")

# 返回（当 allow_confirmation=false）
"⚠️ SECURITY WARNING: Command requires user confirmation
Command: del file.txt
..."
```

## 扩展黑名单

要添加新的危险命令，编辑 `config.yaml`：

```yaml
tools:
  security:
    dangerous_commands:
      - your_dangerous_command
      - "regex:your_regex_pattern"
```

## 禁用安全检查（不推荐）

如果需要临时禁用安全检查（仅用于调试）：

```yaml
tools:
  security:
    enabled: false
```

**警告**：禁用安全检查会使系统面临风险，仅在完全信任的环境中使用。

## 安全最佳实践

1. **保持黑名单更新**：定期审查和更新危险命令列表
2. **最小权限原则**：Agent 应该只执行必要的命令
3. **用户确认**：对于敏感操作，始终要求用户确认
4. **审计日志**：记录所有被拦截的命令尝试
5. **定期测试**：运行测试套件确保安全机制正常工作

## 已知限制

1. **绕过风险**：复杂的命令链可能绕过检查
2. **误报**：某些合法命令可能被误拦截
3. **平台差异**：Windows 和 Unix 命令需要分别配置

## 未来改进

1. 添加命令执行审计日志
2. 实现用户交互式确认机制
3. 支持更复杂的模式匹配规则
4. 添加命令白名单模式
5. 集成沙箱执行环境

## 相关文件

- `Tools/security.py` - 安全检查核心实现
- `Tools/builtin/core_tools.py` - run_cmd 工具集成
- `config.yaml` - 安全配置
- `test_command_security.py` - 测试套件
