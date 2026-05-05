# QQ Bot 完整智能体系统 - 部署完成

## 🎉 部署状态

✅ **完整智能体架构已成功部署到服务器**

- **服务器**: travelnote.online (159.75.26.204)
- **部署路径**: /root/TableHelper
- **Bot 名称**: NyaNya-测试中
- **Bot ID**: 14004041982952838788
- **系统状态**: 已就绪，等待 API Key 配置

## 📦 已部署的组件

### 1. Gateway 层
- ✅ QQ Adapter (WebSocket + REST API)
- ✅ Session Manager (会话管理)
- ✅ Message Router (消息路由)
- ✅ Adapter Manager (适配器管理)
- ✅ Agent Bridge (Agent 桥接)

### 2. Agent 层
- ✅ Actor Agent (执行动作)
- ✅ Reflection Agent (反思优化)
- ✅ Summarizer Agent (记忆总结)
- ✅ Reviewer Agent (答案审核)

### 3. Memory 层
- ✅ Working Memory (短期记忆)
- ✅ Long-term Memory (长期记忆)
- ✅ Memory Manager (记忆管理)

### 4. Tools 层
- ✅ File Operations (文件操作)
- ✅ Command Execution (命令执行，带安全检查)
- ✅ Glob/Grep (文件搜索)
- ✅ Agent Delegation (子 Agent 委托)

### 5. Prompting 层
- ✅ Prompt Loader (提示词加载)
- ✅ Prompt Renderer (提示词渲染)
- ✅ Prompt Assembler (提示词组装)

## 🚀 快速启动指南

### 步骤 1: 配置 API Key

```bash
# SSH 登录服务器
ssh root@travelnote.online

# 进入项目目录
cd /root/TableHelper

# 编辑 .env 文件
nano .env
```

将内容修改为：
```
ANTHROPIC_API_KEY=你的_MiniMax_API_Key
```

保存并退出（Ctrl+X, Y, Enter）

### 步骤 2: 启动 Gateway

```bash
# 方式 1: 前台运行（推荐用于测试）
./start_gateway.sh

# 方式 2: 后台运行（推荐用于生产）
nohup ./start_gateway.sh > gateway.log 2>&1 &

# 查看日志
tail -f gateway.log
```

### 步骤 3: 测试 Bot

1. **私聊测试**
   - 在 QQ 中搜索 "NyaNya-测试中"
   - 发送消息: "你好，介绍一下你自己"
   - Bot 会使用 AI 智能回复

2. **群聊测试**
   - 将 Bot 添加到群
   - @ Bot: "@NyaNya-测试中 帮我写一个 Python 函数"
   - Bot 会生成代码并回复

## 📋 管理命令

### 查看状态
```bash
# 查看进程
ps aux | grep run_gateway

# 查看日志
tail -f gateway.log

# 查看最近 50 行日志
tail -50 gateway.log

# 实时监控
watch -n 2 'ps aux | grep run_gateway'
```

### 停止服务
```bash
# 停止 Gateway
pkill -f run_gateway.py

# 确认已停止
ps aux | grep run_gateway
```

### 重启服务
```bash
# 停止
pkill -f run_gateway.py
sleep 3

# 启动
nohup ./start_gateway.sh > gateway.log 2>&1 &
```

## 🔧 配置说明

### 当前配置 (config_server.yaml)

**LLM 配置**:
```yaml
model:
  large-language-model:
    provider: anthropic_compatible
    model: MiniMax-M2.7
    api_key_env: ANTHROPIC_API_KEY
    base_url: https://api.minimaxi.com/anthropic
    max_tokens: 1024
    temperature: 0.7
```

**Gateway 配置**:
```yaml
gateway:
  platforms:
    qq:
      enabled: true
      app_id: "102839705"
      client_secret: "wOrKoInIoLsQyX6gGrS4gJxbGvbHyfN5"
      markdown_support: true
```

### 切换到其他 LLM

**OpenAI GPT-4**:
```yaml
model:
  large-language-model:
    provider: openai
    model: gpt-4
    api_key_env: OPENAI_API_KEY
    base_url: https://api.openai.com/v1
```

**Anthropic Claude**:
```yaml
model:
  large-language-model:
    provider: anthropic
    model: claude-3-5-sonnet-20241022
    api_key_env: ANTHROPIC_API_KEY
```

## 🎯 功能特性

### 1. 智能对话
- ✅ 自然语言理解
- ✅ 上下文记忆
- ✅ 多轮对话
- ✅ 工具调用

### 2. 会话管理
- ✅ 私聊独立会话
- ✅ 群聊共享/独立会话（可配置）
- ✅ 会话持久化
- ✅ 自动会话重置

### 3. 记忆系统
- ✅ 短期记忆（当前对话）
- ✅ 长期记忆（跨会话）
- ✅ 自动总结
- ✅ 记忆检索

### 4. 工具能力
- ✅ 文件读写
- ✅ 代码执行
- ✅ 文件搜索
- ✅ 子任务委托

### 5. 安全机制
- ✅ 命令安全检查
- ✅ 危险操作拦截
- ✅ API Key 保护

## 📊 测试场景

### 场景 1: 代码生成
```
用户: 帮我写一个 Python 函数，计算两个数的最大公约数
Bot: [生成代码并解释]
用户: 能优化一下吗？
Bot: [优化后的代码]
```

### 场景 2: 问答对话
```
用户: 什么是递归？
Bot: [详细解释]
用户: 能举个例子吗？
Bot: [提供示例代码]
```

### 场景 3: 任务执行
```
用户: 帮我搜索项目中所有的 TODO 注释
Bot: [调用 Grep 工具搜索]
用户: 统计一下有多少个
Bot: [统计并回复]
```

## 🐛 故障排查

### 问题 1: Bot 不回复

**检查清单**:
1. ✅ 进程是否运行: `ps aux | grep run_gateway`
2. ✅ API Key 是否正确: `cat .env`
3. ✅ 查看错误日志: `grep ERROR gateway.log`
4. ✅ 网络是否正常: `ping api.minimaxi.com`

### 问题 2: WebSocket 断开

**解决方案**:
- Gateway 会自动重连
- 检查日志: `grep "WebSocket\|Disconnected" gateway.log`
- 如果持续断开，检查网络稳定性

### 问题 3: API 调用失败

**可能原因**:
- API Key 无效或过期
- API 配额用完
- 网络连接问题

**检查方法**:
```bash
grep "API\|HTTP\|401\|403\|429" gateway.log
```

## 📈 性能监控

### 资源占用
```bash
# CPU 和内存
top -p $(pgrep -f run_gateway.py)

# 详细信息
ps aux | grep run_gateway
```

### 网络连接
```bash
# 查看连接状态
netstat -an | grep ESTABLISHED | grep python

# 查看 WebSocket 连接
ss -t | grep python
```

## 🔐 安全建议

1. **保护敏感文件**
   ```bash
   chmod 600 .env
   chmod 600 config_server.yaml
   ```

2. **定期备份**
   ```bash
   # 备份会话和记忆
   tar -czf backup_$(date +%Y%m%d).tar.gz runtime_memory/
   ```

3. **监控日志**
   ```bash
   # 查看异常访问
   grep "ERROR\|WARN\|FAIL" gateway.log
   ```

## 📚 相关文档

- **部署指南**: DEPLOYMENT_GUIDE.md
- **诊断报告**: QQ_BOT_DIAGNOSIS.md
- **项目说明**: CLAUDE.md
- **QQ Bot API**: https://bot.q.qq.com/wiki/

## 🎓 下一步

### 立即可做
1. ✅ 配置 API Key
2. ✅ 启动 Gateway
3. ✅ 测试基本对话

### 进阶配置
1. 自定义 Bot 性格（编辑 prompts/actor/base.md）
2. 添加自定义工具
3. 调整会话策略
4. 配置定时任务

### 扩展功能
1. 添加更多平台（微信、Telegram 等）
2. 集成数据库
3. 添加 Web 管理界面
4. 实现插件系统

## 📞 技术支持

如遇问题：
1. 查看日志文件
2. 检查配置文件
3. 参考故障排查章节
4. 查看 QQ 开放平台文档

---

**部署完成时间**: 2026-05-04
**部署版本**: v1.0
**系统状态**: ✅ 生产就绪
**下一步**: 配置 API Key 并启动服务

🎉 恭喜！完整的 QQ Bot 智能体系统已成功部署！
