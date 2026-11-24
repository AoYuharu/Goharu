import React, { useEffect, useState } from "react";
import { Box, Text, useInput } from "ink";
import { readFileSync, existsSync } from "node:fs";
import path from "node:path";
import YAML from "yaml";
import { PROJECT_ROOT } from "../gateway/GatewayClient.js";
import { TextInput } from "../components/TextInput.js";

interface ChannelEntry {
  type: string;
  channel_id: string;
  params?: Record<string, unknown>;
}

interface SessionEntry {
  id: string;
  channels?: ChannelEntry[];
  [key: string]: unknown;
}

interface HealthRow {
  session: string;
  type: string;
  channel_id: string;
  name: string;
  ok: boolean;
  state: string;
  detail: string;
}

function readConfig(): Record<string, unknown> {
  const p = path.join(PROJECT_ROOT, "config.yaml");
  if (!existsSync(p)) return {};
  try {
    // uniqueKeys:false —— Python yaml 容忍重复键（后者生效），这里保持一致，
    // 避免一个重复键把整个编辑器读空（2026-11-22 实锤事故）
    const parsed = YAML.parse(readFileSync(p, "utf8"), { uniqueKeys: false });
    return (parsed as Record<string, unknown>) ?? {};
  } catch {
    return {};
  }
}

const CHANNEL_TEMPLATE: ChannelEntry = {
  type: "qqbot",
  channel_id: "qq_new",
  params: { require_mention: true, priority: "high" },
};

/**
 * Channels editor: sessions[].channels 的列表编辑器。
 * ↑↓ 选择通道 · a 新增 · d 删除 · Enter/e 编辑（整行 JSON）· s 保存（整段
 * sessions 经 agent.update_config 写回 config.yaml）· Esc 返回。
 * 保存后需重启 Gateway 生效（channel 在 SessionManager 启动时一次性装配）。
 */
export function ChannelsEditorScreen({
  active,
  onClose,
  onSave,
  onResult,
  onHealth,
}: {
  active: boolean;
  onClose: () => void;
  onSave: (key: string, value: unknown) => Promise<string>;
  onResult: (msg: string) => void;
  onHealth?: () => Promise<HealthRow[]>;
}) {
  const [sessions, setSessions] = useState<SessionEntry[]>([]);
  const [index, setIndex] = useState(0);
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState("");
  const [dirty, setDirty] = useState(false);
  const [health, setHealth] = useState<HealthRow[]>([]);

  useEffect(() => {
    if (active) {
      const cfg = readConfig();
      const list = Array.isArray(cfg.sessions) ? (cfg.sessions as SessionEntry[]) : [];
      setSessions(list);
      setIndex(0);
      setEditing(false);
      setDirty(false);
    }
  }, [active]);

  // 连通状态轮询（每 5s 一次，只在面板打开时）——哪些配通了哪些失败一目了然
  useEffect(() => {
    if (!active || !onHealth) return;
    let cancelled = false;
    const tick = () => {
      onHealth().then((rows) => { if (!cancelled) setHealth(rows ?? []); })
        .catch(() => { /* 网关未就绪时忽略，下轮再试 */ });
    };
    tick();
    const timer = setInterval(tick, 5000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [active, onHealth]);

  // 展平：[(sessionIdx, channelIdx, channel)]
  const flat: Array<{ si: number; ci: number; ch: ChannelEntry; sid: string }> = [];
  sessions.forEach((s, si) => {
    (s.channels ?? []).forEach((ch, ci) => {
      flat.push({ si, ci, ch, sid: s.id ?? `session_${si}` });
    });
  });
  const current = flat[Math.min(index, flat.length - 1)];

  const persist = (next: SessionEntry[]) => {
    setSessions(next);
    setDirty(true);
  };

  const save = () => {
    void onSave("sessions", sessions)
      .then((msg) => {
        setDirty(false);
        onResult(`${msg}（channel 装配在启动时进行，重启 Gateway 后生效）`);
      })
      .catch((e) => onResult(`保存失败: ${e}`));
  };

  useInput((input, key) => {
    if (editing) {
      if (key.escape) setEditing(false);
      return;   // TextInput 拥有其余按键
    }
    if (key.escape) {
      if (dirty) save();
      onClose();
      return;
    }
    if (key.upArrow) { setIndex((i) => Math.max(0, i - 1)); return; }
    if (key.downArrow) { setIndex((i) => Math.min(flat.length - 1, i + 1)); return; }
    if (input === "a" && sessions.length > 0) {
      const next = sessions.map((s, si) => si === 0
        ? { ...s, channels: [...(s.channels ?? []), { ...CHANNEL_TEMPLATE }] }
        : s);
      persist(next);
      setIndex(flat.length);   // 选中新行
      return;
    }
    if (input === "d" && current) {
      const next = sessions.map((s, si) => si === current.si
        ? { ...s, channels: (s.channels ?? []).filter((_, ci) => ci !== current.ci) }
        : s);
      persist(next);
      setIndex((i) => Math.max(0, Math.min(i, flat.length - 2)));
      return;
    }
    if (input === "s") { save(); return; }
    if ((key.return || input === "e") && current) {
      setEditValue(JSON.stringify(current.ch));
      setEditing(true);
      return;
    }
  }, { isActive: active });

  if (!active) return null;

  const submitEdit = (raw: string) => {
    setEditing(false);
    let parsed: ChannelEntry;
    try {
      parsed = JSON.parse(raw) as ChannelEntry;
      if (!parsed.type || !parsed.channel_id) throw new Error("缺 type/channel_id");
    } catch (e) {
      onResult(`JSON 无效，未保存该行: ${e}`);
      return;
    }
    if (!current) return;
    const next = sessions.map((s, si) => si === current.si
      ? {
          ...s,
          channels: (s.channels ?? []).map((ch, ci) => (ci === current.ci ? parsed : ch)),
        }
      : s);
    persist(next);
  };

  return (
    <Box flexDirection="column" borderStyle="round" borderColor="cyan" backgroundColor="#1c1e2b" paddingX={1}>
      <Text bold color="cyan" backgroundColor="#1c1e2b">📡 Channels — 消息通道配置</Text>
      <Text dimColor backgroundColor="#1c1e2b">
        {dirty ? "● 有未保存修改（s 保存 / Esc 保存并返回）" : "已保存"}
      </Text>
      {health.length > 0 && (
        <Box flexDirection="column" marginBottom={0}>
          <Text bold color="gray" backgroundColor="#1c1e2b">连通状态：</Text>
          {health.map((h, i) => (
            <Text key={i} backgroundColor="#1c1e2b" wrap="truncate"
                  color={h.ok ? "green" : "yellow"}>
              {h.ok ? "✓" : "✗"} {h.type}#{h.channel_id}（{h.name}）  {h.state}
              {h.detail ? `  ${h.detail}` : ""}
            </Text>
          ))}
        </Box>
      )}
      {flat.length === 0 && (
        <Text color="yellow" backgroundColor="#1c1e2b">（无通道，按 a 新增）</Text>
      )}
      {flat.map((row, i) => {
        const selected = i === index;
        const params = row.ch.params ? JSON.stringify(row.ch.params) : "";
        return (
          <Text key={i} backgroundColor={selected ? "cyan" : "#1c1e2b"}
                color={selected ? "black" : undefined} wrap="truncate">
            {selected ? "› " : "  "}[{row.sid}] {row.ch.type}#{row.ch.channel_id}
            {params ? `  ${params}` : ""}
          </Text>
        );
      })}
      {editing && (
        <Box>
          <Text color="yellow" backgroundColor="#1c1e2b">编辑 JSON: </Text>
          <TextInput value={editValue} onChange={setEditValue} onSubmit={submitEdit} placeholder="" />
        </Box>
      )}
      <Text dimColor backgroundColor="#1c1e2b">
        ↑↓ 选择 · a 新增 · d 删除 · Enter/e 编辑(JSON) · s 保存 · Esc 保存并返回
      </Text>
      <Text dimColor backgroundColor="#1c1e2b">
        type 可用值: tui / qqbot / architect_telemetry（外部插件经 channels.plugins 注册）· 保存后重启 Gateway 生效
      </Text>
    </Box>
  );
}
