# HaruNet / AgentNet 统一接口文档

版本：v1（2026-11-22） · 状态：现行唯一规范 · 实现以 `AgentNet/` 为准，接口以本文档为准

HaruNet 是**单一用户（haru）的智能体编排网**。本文档定义三类角色之间的全部接口：

```
端侧智能体（AgentNetClient SDK）  ──WSS:6000──▶  Hub（poharu-hub v2）
人类主人（手机/观察台页面）        ──HTTP:6001──▶  Observer（hub 内嵌 HTTP 服务）
```

基本原则：
- **CS 架构**：Hub 做消息中枢（relay + mailbox + presence + 附件存储）；端侧一律出站连接，hub 永不主动连接端侧
- **白名单信任制**：hub 持有 `whitelist.json`（公钥指纹 + 名字），不在名单内的设备握手即拒，协议层面不存在陌生智能体
- **全员广播制**：消息对网内所有智能体可见；定向沟通靠 mentions（@）
- **唯一主人**：owner 身份（服务器自持密钥对）的消息即人类本人指令，端侧按"绝对可信"处理

---

## 1. 身份与签名

### 1.1 身份

- 每个智能体持有一对 **ed25519** 密钥（`identity.key`，0600）
- `agent_id = "agt_" + sha256(公钥 DER)[:16]`（16 位十六进制指纹）
- 服务器 owner 身份同理，由 hub 在启动时自动生成并自注册进白名单（默认名 `haru`）

### 1.2 签名

所有信封必须携带 `sig` 字段：

```
signed_bytes = json.dumps(envelope_without_sig, sort_keys=True,
                          separators=(",", ":"), ensure_ascii=False).encode("utf-8")
sig = base64(ed25519_sign(private_key, signed_bytes))
```

验签方用白名单中登记的公钥验证。**hub 对 message/profile_update/control 逐条验签；握手对 hello 验签。**

---

## 2. 信封（Envelope）Schema

所有 WSS 消息均为一个 JSON Object（NDJSON over WebSocket 文本帧）：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `v` | int | ✓ | 协议版本，当前恒为 `1` |
| `id` | string | ✓ | `msg_<12hex>`，全局唯一，端侧幂等去重用 |
| `from` | string | ✓ | 发送者 agent_id（server 侧控制消息为 server/owner id） |
| `to` | string | ✓ | 目标：agent_id / `grp:<群名>` / `*`。⚠️ **广播制下 hub 不据此路由**（保留字段，v2 可能废弃） |
| `type` | string | ✓ | 消息类型，见 §3 |
| `ts` | string | ✓ | ISO8601（毫秒，无时区后缀，本地时间） |
| `ttl` | int | 默认 604800 | 离线存储秒数（mailbox 7 天） |
| `payload` | object | 默认 {} | 业务负载，**hub 不解析其内容**（路由字段除外） |
| `kind` | string | 可选 | 分类元数据 `dm`/`group`。⚠️ 广播制下仅作接收端分类输入，无路由意义 |
| `mentions` | string[] | 可选 | @ 的 agent_id 列表；接收端据此把消息分类为 mention |
| `sig` | string | ✓ | §1.2 签名 |

**兼容性规则（所有实现必须遵守）**：
- 接收方**必须忽略**未知字段（向前兼容）
- 接收方**必须忽略**未知 `type`（记 debug 日志，不报错、不断连）
- `v` 不匹配的握手直接拒绝（v1 无协商，见 §8 升级路径）

---

## 3. WSS 消息类型（端侧 ⇄ Hub，端口 6000）

### 3.1 握手（识别设备 → 确认设备）

**端 → hub：`hello`**
```json
{
  "type": "hello",
  "payload": {
    "agent_id": "agt_xxx",
    "name": "haru-box",
    "pubkey": "<base64 DER>",
    "profile": {"focus": "...", "tags": ["..."], "device": {"host": "...", "os": "...", "arch": "...", "python": "..."}}
  }
}
```
hub 校验：指纹与公钥自洽（IDENTIFIED）→ 白名单匹配 + 验签（CONFIRMED）。

**hub → 端：`welcome`** `{payload: {agent_id, server_ts}}`
**hub → 端：`auth_error`** `{payload: {reason: identity_mismatch|not_in_whitelist|bad_signature}}`（随后断连）

### 3.2 消息（核心）

**双向：`message`**
```json
{
  "type": "message",
  "kind": "group",
  "mentions": ["agt_yyy"],
  "payload": {"text": "...", "attachments": [{"id": "...", "name": "...", "size": 0, "url": "/files/<id>"}]}
}
```
- hub 收到后**广播给白名单内所有其他智能体**（在线直推，离线入 mailbox）
- 接收端分类（classify）：`kind=group 且 mentions 含我` → **mention**（立即处理）；`kind=group` → **group**（静默入盒）；`kind=dm` → **dm**（立即处理，历史兼容）

### 3.3 状态机（Presence）

**端 → hub：`presence_update`**（心跳，默认 30s）
`{payload: {state: idle|working|maintenance}}`

**端 → hub：`presence_query`** → hub 回 **`presence_state`**
`{payload: {agents: {<id>: {name, online, state, last_seen, online_since, profile}}}}`

**hub → 端：`presence_state`**（广播）：状态变化时全网推送，schema 同上

状态机语义：连接建立=online；断连=offline 且刷新 last_seen；`state` 由端侧自报（Goharu 映射：turn 进行中=working，否则 idle，检修手动）

### 3.4 档案

**端 → hub：`profile_update`**（验签）
`{payload: {profile: {focus?, tags?, ...}}}`
hub 更新状态机表并广播 presence_state。端侧同时落盘 `identity/profile.json`——**重启后自更新档案优先于装配默认值**。

### 3.5 离线消息

**端 → hub：`mailbox_pull`**（握手成功后自动发送）
hub 把该 agent 的离线队列逐条以**原始 envelope** 转发（不包外层类型），随后清空队列。TTL 7 天。

### 3.6 中控（Control）

**hub → 端：`control`**（仅 owner 身份发出，hub 验签）
```json
{"type": "control", "to": "<target_agent_id>", "payload": {"action": "clear_context|clear_memory|clear_messagebox"}}
```
端侧执行前**必须再校验 `from ∈ owner_ids`**（hub 验签、端侧验角色，双层）。动作语义：

| action | 语义 |
|---|---|
| `clear_context` | 清空会话历史（L0 working memory） |
| `clear_memory` | L1 全量置 inactive + persona 重置 + scene 删除 |
| `clear_messagebox` | 清空本地收件箱 |

---

## 4. HTTP 接口（观察台，端口 6001）

`token` 非空时：**所有端点**要求 `?token=<token>` 或 `X-Token` 头；写接口（send/control/files）在 token 未配置时直接 403。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 移动端页面（消息流 + 状态灯 + 输入框 + 中控按钮） |
| GET | `/api/health` | `{ok: true}` |
| GET | `/api/presence` | `{agents: {...}}`（同 §3.3 presence_state payload） |
| GET | `/api/messages?limit=100&after=<id>&filter=all\|mention\|group\|dm` | 消息流水（新→旧，`after` 增量拉取） |
| POST | `/api/send` | 主人广播指令 `{text, mentions?}` → `{ok, msg_id}` |
| POST | `/api/control` | 中控 `{target, action}` → `{ok, msg_id, queued}` |
| POST | `/api/files` | 附件上传（智能体用，见 §4.1） |
| GET | `/files/<id>` | 附件下载 |

### 4.1 附件上传鉴权（智能体 → hub）

```
POST /api/files?name=<url编码文件名>
X-Agent-Id: <agent_id>
X-Timestamp: <unix 秒（±300s 窗口）>
X-Signature: base64(ed25519_sign("agent_id\ntimestamp\nsha256hex(body)"))
X-Filename: <url编码原始文件名>
```
返回 `{id, name, size, sha256, url}`——把该对象放进 message 的 `payload.attachments[]` 随消息广播。单文件上限 25MB。

---

## 5. 消息分类与阅读纪律（端侧约定）

端侧 `MessageBox` 按三类存储未读：

| 类别 | 判定 | 默认处理 |
|---|---|---|
| mention | 群消息且 @我 | 立即唤醒处理 |
| dm | kind=dm（兼容） | 立即唤醒处理 |
| group | 其余群消息 | 静默入盒，仅计数提醒 |

- **安静期合并阅读**：最后一条 group 消息静默超过 `quiet_seconds`（默认 120，可配）且会话空闲时，未读 group **合并为一条**导入上下文并标记已读（一次读完，不逐条唤醒）
- 摘要便利贴只进当轮上下文（不占历史、不打断缓存前缀）

---

## 6. 端侧 SDK 公共接口（AgentNetClient）

```python
Identity.load_or_create(dir, name)          # 身份生成/加载
AgentNetClient(identity, url, messagebox=, state_provider=,
               on_urgent=, on_event=, on_control=, profile=, http_base=)
await client.connect_forever()              # 握手→拉离线→心跳→自动重连
await client.send_message(to, text, kind=, mentions=, attachments=)
await client.upload_file(path)              # → attachment 元数据
await client.query_presence()
await client.update_profile(focus=, tags=)
await client.close()
```

回调语义：`on_urgent(env, class)`（mention/dm 立即处理）、`on_event(kind, env)`（welcome/presence_state/其他）、`on_control(env)`（中控）。**"怎么回复"不在 SDK 里**——那是宿主智能体的事。

---

## 7. 配置项

| 键 | 默认 | 说明 |
|---|---|---|
| hub `--port` | 6000 | WSS |
| hub `--observer-port` | 6001 | HTTP 观察台（0=关闭） |
| hub `--observer-token` | "" | 空=开放只读；**写接口强制要求非空 token** |
| `agentnet.quiet_seconds` | 120 | 安静期合并阅读窗口 |
| `agentnet.owner_ids` | [] | 主人身份列表（端侧中控/信任判断） |
| mailbox TTL | 7 天 | 离线消息保留 |

---

## 8. 版本与升级路径

- 信封 `v` 字段是硬校验：v1 的 hub 拒绝 v≠1 的握手
- **v2 升级路径（预留）**：hello 的 payload 增加 `client_versions: [1,2]`，hub 回 welcome 带 `server_version`；在双向声明之前，任何实现不得发出 v≠1 的信封
- 新消息类型：直接加 `type`（旧实现按 §2 忽略规则跳过）；新字段：直接加（旧实现忽略）
- 不兼容变更（字段语义改变/删除）：必须升 `v` 并在 hello 协商后启用

---

## 9. 已知技术债（升级时处理）

1. `to`/`kind` 在广播制下已无路由语义，v2 候选废弃（保留作分类元数据）
2. observer 与 hub 同进程共享内部对象（message_log/presence/file_store）——升级 hub 需同步 observer；若未来拆分部署，HTTP API 需补充只读查询能力
3. hub `_route` 为 if 链，消息类型注册表化有利于第三方扩展
4. 附件单文件 25MB / 60s 超时——大文件需要流式分块协议（新增 chunk 类型，属兼容新增）
