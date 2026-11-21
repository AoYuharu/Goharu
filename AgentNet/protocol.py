"""AgentNet 协议层：信封 schema、签名、消息分类。

设计要点：
- 信封与负载分离：server 只读路由字段（from/to/type），payload 不透明
- 签名覆盖除 sig 外的全部字段（canonical JSON：sort_keys + 紧凑分隔符）
- kind 只描述消息投递形态（dm/group），mention 是接收端分类判定的依据
  （classify() 在端侧算，server 不算——mention 列表在 envelope 里明文可见）

类型一览：
  hello / welcome / auth_error   握手（识别设备 → 确认设备）
  message                        用户消息（kind=dm|group）
  presence_update                端→server 心跳状态自报
  presence_state                 server→端 状态机快照（查询应答/广播）
  mailbox_pull / mailbox_items   离线消息拉取
  ack / error                    通用应答
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, Optional

PROTOCOL_VERSION = 1

# 消息类别（端侧分类结果，MessageBox 的存储维度）
CLASS_MENTION = "mention"
CLASS_DM = "dm"
CLASS_GROUP = "group"

# 投递形态
KIND_DM = "dm"
KIND_GROUP = "group"

# 状态机（Presence 状态机的合法状态）
STATE_IDLE = "idle"
STATE_WORKING = "working"
STATE_MAINTENANCE = "maintenance"
RUNTIME_STATES = {STATE_IDLE, STATE_WORKING, STATE_MAINTENANCE}


def now_ts() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def canonical_bytes(obj: dict) -> bytes:
    """签名用规范序列化：键排序 + 紧凑分隔符，剥离 sig 字段。"""
    body = {k: v for k, v in obj.items() if k != "sig"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def agent_id_from_pubkey_der(der: bytes) -> str:
    """公钥 DER → 身份 id（指纹截断 16 位十六进制）。"""
    return "agt_" + hashlib.sha256(der).hexdigest()[:16]


def make_envelope(
    *,
    sender: str,
    to: str,
    type_: str,
    payload: Optional[dict] = None,
    kind: Optional[str] = None,
    mentions: Optional[list[str]] = None,
    ttl: int = 604800,  # 默认 7 天（mailbox 存储期）
) -> dict:
    env: dict[str, Any] = {
        "v": PROTOCOL_VERSION,
        "id": f"msg_{uuid.uuid4().hex[:12]}",
        "from": sender,
        "to": to,
        "type": type_,
        "ts": now_ts(),
        "ttl": ttl,
        "payload": payload or {},
    }
    if kind is not None:
        env["kind"] = kind
    if mentions:
        env["mentions"] = list(mentions)
    return env


def classify(env: dict, my_id: str) -> str:
    """端侧消息分类：群@我 > 私聊 > 群闲聊。"""
    if env.get("kind") == KIND_GROUP:
        if my_id in (env.get("mentions") or []):
            return CLASS_MENTION
        return CLASS_GROUP
    return CLASS_DM


def parse(data: str | bytes) -> dict:
    """解析并做最低限度结构校验；不验签（验签需要 registry 的公钥）。"""
    env = json.loads(data)
    if not isinstance(env, dict):
        raise ValueError("envelope 必须是 JSON object")
    for field in ("v", "id", "from", "to", "type", "ts"):
        if field not in env:
            raise ValueError(f"envelope 缺字段: {field}")
    if env["v"] != PROTOCOL_VERSION:
        raise ValueError(f"协议版本不支持: {env['v']}")
    return env


def dumps(env: dict) -> str:
    return json.dumps(env, ensure_ascii=False, separators=(",", ":"))
