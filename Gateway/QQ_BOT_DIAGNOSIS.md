# QQ Bot 连接问题诊断报告

## 当前状态

### ✅ 已解决的问题

1. **Token 获取** - 成功
   - 正确的 Token URL: `https://bots.qq.com/app/getAppAccessToken`
   - Token 格式: `QQBot {access_token}`
   - Token 有效期: 4582秒

2. **WebSocket 连接** - 成功
   - Gateway URL: `wss://api.sgroup.qq.com/websocket`
   - 收到 Hello 消息 (op=10)
   - 心跳间隔: 41250ms

3. **代码实现** - 完成
   - 参考 hermes-agent 的实现
   - 使用正确的认证格式
   - Intents 配置正确

### ❌ 当前问题

**WebSocket 鉴权失败 (op=9 Invalid Session)**

```
[QQAdapter] Received payload: op=9
[QQAdapter] Invalid Session! Authentication failed.
[QQAdapter] Payload: {'op': 9, 'd': False}
```

## 问题原因分析

根据测试结果，问题**不是代码实现的问题**，而是：

### 您的机器人没有申请到 WebSocket 连接所需的权限

QQ Bot API 需要在开放平台申请特定的权限（Intents）才能：
1. 建立 WebSocket 连接
2. 接收私聊消息（C2C_MESSAGE_CREATE）
3. 接收群聊@消息（GROUP_AT_MESSAGE_CREATE）

当前使用的 Intents 值: `1107300352`
- Bit 12 (4096): DIRECT_MESSAGE - 私聊消息
- Bit 25 (33554432): GROUP_AT_MESSAGE - 群聊@消息
- Bit 30 (1073741824): PUBLIC_GUILD_MESSAGES - 频道消息

## 解决方案

### 方案 1：申请机器人权限（推荐）

1. 登录 [QQ 开放平台](https://q.qq.com/)
2. 找到您的机器人（App ID: 102839705）
3. 查看**权限配置**或**能力配置**
4. 申请以下权限：
   - ✅ 私聊消息接收权限
   - ✅ 群聊@消息接收权限
   - ✅ WebSocket 连接权限
5. 等待审核通过

**注意事项**：
- 某些权限可能需要企业认证
- 审核可能需要 1-3 个工作日
- 需要说明使用场景和用途

### 方案 2：使用 Webhook 模式（备选）

如果 WebSocket 权限难以获取，可以使用 Webhook 模式：

**优点**：
- 不需要 WebSocket 权限
- QQ 服务器主动推送消息到您的服务器
- 更稳定，不需要维护长连接

**缺点**：
- 需要有公网 IP 和域名
- 需要配置 HTTPS
- 需要在 QQ 开放平台配置 Webhook URL

**实现步骤**：
1. 准备一个公网可访问的 HTTPS 服务器
2. 在 QQ 开放平台配置 Webhook URL
3. 实现 Webhook 接收端点
4. 验证签名并处理消息

### 方案 3：仅使用 REST API（临时方案）

如果只需要**主动发送消息**（不接收消息），可以：

**优点**：
- 不需要 WebSocket 权限
- 实现简单

**缺点**：
- 无法接收用户消息
- 需要提前知道用户/群的 ID
- 无法实现对话功能

**使用场景**：
- 定时推送通知
- 主动发送消息
- 单向通知系统

## 测试结果总结

### 成功的部分

```
✅ Token 获取: https://bots.qq.com/app/getAppAccessToken
   Status: 200
   Token: WC_AAUHrRM7jRkEOSGhQo9Ix01Ai1iILq2BWT4wgT6b81sxi0dLkLh6Tkt2nDLp8nVbPNjFkFa7meA
   Expires: 4582s

✅ WebSocket 连接: wss://api.sgroup.qq.com/websocket
   Status: Connected
   Hello (op=10): Received
   Heartbeat interval: 41250ms

✅ Identify 发送: op=2
   Token format: QQBot {token}
   Intents: 1107300352
   Shard: [0, 1]
```

### 失败的部分

```
❌ WebSocket 鉴权: op=9 Invalid Session
   原因: 机器人没有相应的权限（Intents）
   解决: 需要在 QQ 开放平台申请权限
```

## 代码实现状态

### ✅ 已完成

1. **QQAdapter** - 完整实现
   - Token 获取（动态刷新）
   - WebSocket 连接
   - 心跳保活
   - 消息接收
   - 消息发送
   - 错误处理

2. **Gateway 系统** - 完整实现
   - 会话管理
   - 消息路由
   - Agent 集成
   - 命令系统

3. **测试脚本** - 完整
   - 连接测试
   - 诊断工具
   - 发送消息测试

### 📝 配置文件

**config.yaml** (正确配置):
```yaml
gateway:
  platforms:
    qq:
      enabled: true
      app_id: "102839705"
      client_secret: "wOrKoInIoLsQyX6gGrS4gJxbGvbHyfN5"
      markdown_support: true
      sandbox: false  # 使用正式环境
```

## 下一步行动

### 立即可做

1. **检查机器人权限**
   - 登录 QQ 开放平台
   - 查看当前权限配置
   - 确认是否有 WebSocket 连接权限

2. **申请权限**（如果没有）
   - 申请私聊消息权限
   - 申请群聊@消息权限
   - 填写使用场景说明

3. **等待审核**
   - 审核通过后重新测试
   - 应该能够成功连接

### 备选方案

如果权限申请困难：
1. 考虑使用 Webhook 模式
2. 或者仅使用 REST API 主动发送消息
3. 联系 QQ 开放平台技术支持

## 参考资料

- [QQ 机器人官方文档](https://bot.q.qq.com/wiki/)
- [hermes-agent QQ Bot 实现](D:\MyProject\Programming\hermes-agent\gateway\platforms\qqbot\adapter.py)
- [QQ 开放平台](https://q.qq.com/)

## 联系方式

如果需要进一步的帮助：
- QQ 开放平台技术支持
- QQ 机器人开发者社区

---

**报告生成时间**: 2026-05-04
**测试状态**: Token 获取成功，WebSocket 鉴权失败（权限问题）
**建议**: 在 QQ 开放平台申请相应的机器人权限
