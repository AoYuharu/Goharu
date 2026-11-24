# HaruNet / AgentNet 统一接口文档

版本：v1（2026-11-24 同步） · 状态：现行唯一规范 · 实现以 `AgentNet/` 为准，接口以本文档为准

HaruNet 是**单一用户（haru）的智能体编排网**。本文档定义三类角色之间的全部接口：

```
端侧智能体（AgentNetClient SDK）  ──WS:7878（部署可用 6000）──▶  Hub（poharu-hub v2）
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

验签方用白名单中登记的公钥验证。**hub 对 hello/message/profile_update/ask/floor 逐条验签；hub 下发的 welcome/presence_state/floor_state 等事件当前不签名。**

---

## 2. 信封（Envelope）Schema

所有 WS 消息均为一个 JSON Object（NDJSON over WebSocket 文本帧）：

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
| `sig` | string | 条件必填 | 端侧需验签的信封必填；hub 下发的 welcome/presence/floor 等事件当前不签名 |

**兼容性规则（所有实现必须遵守）**：
- 接收方**必须忽略**未知字段（向前兼容）
- 接收方**必须忽略**未知 `type`（记 debug 日志，不报错、不断连）
- `v` 不匹配的握手直接拒绝（v1 无协商，见 §8 升级路径）

---

## 3. WS 消息类型（端侧 ⇄ Hub，默认端口 7878，生产可为 6000）

### 3.1 握手（识别设备 → 确认设备）

**端 → hub：`hello`（第一段：只出示身份）**
```json
{
  "type": "hello",
  "payload": {
    "agent_id": "agt_xxx",
    "name": "haru-box",
    "pubkey": "<base64 DER>"
  }
}
```
hub 校验：指纹与公钥自洽（IDENTIFIED）→ 白名单匹配 + 验签（CONFIRMED）。**hello 不携带也不采信设备档案**，未入白名单的机器不会因此外泄设备信息。

**hub → 端：`welcome`** `{payload: {agent_id, server_ts, floor}}`
**hub → 端：`auth_error`** `{payload: {reason: identity_mismatch|not_in_whitelist|bad_signature}}`（随后断连）

**端 → hub：`profile_update`（第二段）**：welcome 后 SDK 先上报 `{focus, tags, device, security?, hosts_architect?}`，随后发送 `mailbox_pull`。被拒的连接按 `reject_backoff_seconds`（默认 600s）长退避重试。

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
`{payload: {state: idle|working|maintenance, attentive: bool}}`

**端 → hub：`presence_query`** → hub 回 **`presence_state`**
`{payload: {agents: {<id>: {name, online, state, last_seen, online_since, attentive, profile}}, floor: <floor 快照或 null>}}`

**hub → 端：`presence_state`**（广播）：状态变化时全网推送，schema 同上

状态机语义：连接建立=online；断连=offline 且刷新 last_seen；`state` 由端侧自报（Goharu 映射：turn 进行中=working，否则 idle，检修手动）

### 3.4 档案

**端 → hub：`profile_update`**（验签）
`{payload: {profile: {focus?, tags?, ...}}}`
hub 更新状态机表并广播 presence_state。端侧同时落盘 `identity/profile.json`——**重启后自更新档案优先于装配默认值**。

### 3.5 离线消息

**端 → hub：`mailbox_pull`**（握手成功后自动发送）
hub 按 id 顺序逐条以**原始 envelope** 转发（不包外层类型）。每条只有 socket 写入成功才从队列删除；失败或异常时当前及后续消息保留。语义为**有序至少一次投递**：发送成功但确认前崩溃可能重复，端侧继续用 envelope id 幂等去重。TTL 7 天。

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
| `rename` | 改网络昵称：hub 白名单/presence 权威更新并广播，端侧同步身份与 profile |
| `security` | 更新目标端安全规则：`dangerous_commands`、`require_confirmation`、`allow_confirmation` |
| `answer` | 观察台 AskUser 作答回送：`{request_id, answer}` |
| `confirm_result` | 高危确认回送：`{confirm_id, approved}` |

### 3.7 挂起询问与话语权

- **端 → hub：`ask`**：`{request_id, kind: question|confirm, data, ttl}`，验签后进入 hub 挂起表，观察台轮询 `/api/requests` 展示；主人经 `/api/answer` 作答后 hub 回送上述 `answer/confirm_result`。
- **端 → hub：`floor`**：`{action: set|clear, target, persona: main|arch, note}`，当前白名单智能体均可发起，hub 记录并全网广播。
- **hub → 端：`floor_state`**：`{floor: {holder_agent_id, holder_name, persona, since, note}|null}`。floor 仅内存保存，hub 重启清空；持有者掉线自动收回。

### 3.8 统一错误流

- **端 → hub：`error_report`**（验签）：`{record: {id, fingerprint, ts, scope, severity, blocking, cause, source, session, message, traceback, retryable, status, wake_status, attempts, updated_ts, extra}}`。同一 `id` 再次上报表示状态更新（upsert）。
- 语义：`blocking ∈ fatal|all_turns|channel|feature|request`（后果）；`cause ∈ config|runtime|external`（根因）。hub 存入 `state_dir/errors.db`，观察台通过 `GET /api/errors` 读取。

---

## 4. HTTP 接口（观察台，端口 6001）

`token` 非空时：查询接口要求 `?token=<token>` 或 `X-Token` 头（`/` 页面本身先返回，由页面后续 API 请求鉴权）。`send/control/answer/floor` 等 owner 写接口**必须配置且匹配 token**；附件上传单独使用智能体 ed25519 签名鉴权，不使用 observer token。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 移动端页面（消息流 + 状态灯 + 输入框 + 中控按钮 + 待答卡片 + 话语权条） |
| GET | `/api/health` | `{ok: true}` |
| GET | `/api/presence` | `{agents: {...}}`（同 §3.3 presence_state payload） |
| GET | `/api/requests` | `{requests: [...]}`：未过期的挂起提问/高危确认 |
| GET | `/api/floor` | `{floor: <快照或 null>}` |
| GET | `/api/errors?limit=200&after=<row_id>` | `{errors: [...]}`：统一错误流（按 row_id 增量） |
| GET | `/api/messages?limit=100&after=<id>&filter=all\|mention\|group\|dm` | 消息流水（当前页按 id **旧→新**，`after` 增量拉取） |
| POST | `/api/send` | 主人广播指令 `{text, mentions?}` → `{ok, msg_id}` |
| POST | `/api/control` | 中控 `{target, action, name?, extra...}` → `{ok, msg_id, queued}` |
| POST | `/api/answer` | 作答 `{request_id, answer}`；confirm 的 answer 为 true/false |
| POST | `/api/floor` | 话语权 `{action: set|clear, target?, persona?, note?}` |
| POST | `/api/files` | 附件上传（智能体用，见 §4.1） |
| GET | `/files/<id>` | 附件下载，1MiB 分块流式返回 |

### 4.1 附件上传鉴权（智能体 → hub）

```
POST /api/files?name=<url编码文件名>
X-Agent-Id: <agent_id>
X-Timestamp: <unix 秒（±300s 窗口）>
X-Signature: base64(ed25519_sign("agent_id\ntimestamp\nsha256hex(body)"))
X-Filename: <url编码原始文件名>
```
返回 `{id, name, size, sha256, url}`——把该对象放进 message 的 `payload.attachments[]` 随消息广播。单文件上限 **2GiB**；客户端和服务端上传均按 1MiB 分块并流式计算 sha256，客户端 socket 操作超时 180s，服务端先落临时文件，验签通过后原子入库。

---

## 5. 消息分类、关注期与阅读纪律（端侧约定）

端侧 `MessageBox` 按三类存储未读，`classify` 规则：**群消息 mentions 含我或含通配 `"*"`（@全员）→ mention；kind=dm → dm；其余 → group**。

### 5.1 关注期模型（拟人化）

每个智能体有一个"是否正在关注聊天"的状态：

- **进入关注期**：收到 mention / dm 或关注期内的新消息 → 唤醒并进入关注期（默认 `attention_seconds=300`，每次唤醒续期）。**普通主人群消息不 @ 时不立即唤醒**，改用更短的 `owner_quiet_seconds`（默认 15s）安静期兜底阅读；持有 Floor 时主人的所有消息才全部立即唤醒。
- **关注期内**：任何新消息（包括不@它的普通群消息）都实时唤醒，并继续续期；连续同事闲聊达到 `agent_pingpong_limit`（默认 3）后触发乒乓刹车，不再唤醒。
- **退出关注期**：超过 `attention_seconds` 无新消息 → 自动回到"@才醒"模式；再次被 @/私聊时重新进入。
- **Floor 绝对优先**：非持有者收到主人消息只入盒不唤醒，即使被 @；持有者的主 session 处理 main 人格消息，arch 人格则由宿主派发给 Architect，其他同事消息不打断该对话。

### 5.2 唤醒消息的消化

唤醒不逐条触发 turn：去抖窗口（默认 1.2s）内的所有唤醒消息按时间顺序合并为**一条**上下文消息（含发送时间戳，@消息与关注期群聊分区展示）。被唤醒的消息随即在收件箱标记已读。

**群消息体量分流**（`group_inline_max_chars`，默认 1500）：
- 未超阈值：按时间顺序原文内联进本轮合辑
- 超阈值：唤醒消息先行处理，压缩智能体（arch 角色）**并行**摘要群消息（关注与接收方相关的信息），完成后**补送进主 agent 消息队列**

**安静期兜底阅读**（不在关注期、无 @ 时）：最后一条 group 静默超过 `quiet_seconds`（默认 120）且会话空闲时，按同一体量分流规则消化；主人不 @ 的群消息使用 `owner_quiet_seconds`（默认 15）。

所有合辑/摘要消息尾部统一附阅读提示：**关注可能与你有关的内容即可；不重要且不相关的可以不回复**。

摘要便利贴只进当轮上下文（不占历史、不打断缓存前缀）。

### 5.3 @全员

`mentions` 数组支持通配符 `"*"`，表示 @全体成员——所有接收端均按 mention 分类处理。观察台输入栏的「@全员」chip 与 `serverMessageHandler(reply, mentions=["*"])` 均使用该约定。

---

## 6. 端侧 SDK 公共接口（AgentNetClient）

```python
Identity.load_or_create(dir, name)          # 身份生成/加载
AgentNetClient(identity, url, messagebox=, state_provider=, attentive_provider=,
               on_urgent=, on_message=, on_event=, on_control=, profile=, http_base=,
               heartbeat_seconds=30, reconnect_max_seconds=60,
               reject_backoff_seconds=600)
await client.connect_forever()              # 握手→档案上报→拉离线→心跳→自动重连
await client.send_message(to, text, kind=, mentions=, attachments=)
await client.upload_file(path)              # → attachment 元数据（分块流式）
await client.query_presence()
await client.update_profile(focus=, tags=)
await client.submit_ask(request_id, kind, data, ttl=400)
await client.set_floor(action, target="", persona="main", note="")
await client.push_presence()
await client.close()                        # 立即唤醒重连退避并退出
```

回调语义：`on_urgent(env, class)`（mention/dm 立即处理）、`on_message(env, class)`（全量消息，设置后替代分类分发）、`on_event(kind, env)`（welcome/presence_state/floor_state/disconnected/其他）、`on_control(env)`（中控）。**"怎么回复"不在 SDK 里**——那是宿主智能体的事；`on_control` 的角色校验由宿主负责，SDK 只负责传输。

---

## 7. 配置项

| 键 | 默认 | 说明 |
|---|---|---|
| hub `--host` | 127.0.0.1 | 监听地址（生产常用 0.0.0.0） |
| hub `--port` | 7878 | WS |
| hub `--observer-port` | 0 | HTTP 观察台端口，0=关闭；生产常设 6001 |
| hub `--observer-token` | "" | 空=开放只读；**owner 写接口强制要求非空 token** |
| hub `--whitelist` | whitelist.json | 白名单文件路径 |
| hub `--state-dir` | hub_state | 状态目录 |
| hub `--owner-name` | haru | owner 身份名 |
| `agentnet.quiet_seconds` | 120 | 安静期合并阅读窗口 |
| `agentnet.owner_quiet_seconds` | 15 | 主人不 @ 的群消息短安静期窗口 |
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
4. 上传与下载均已流式分块（1MiB）；后续只需补 Range/断点续传等体验优化，无需新增 chunk 消息类型
