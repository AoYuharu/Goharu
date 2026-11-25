import React, { useEffect, useMemo, useState } from "react";
import { Box, Text, useInput } from "ink";
import { existsSync, readFileSync } from "node:fs";
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

export interface ChannelHealthRow {
  session: string;
  type: string;
  channel_id: string;
  name: string;
  ok: boolean;
  state: string;
  detail: string;
  params?: Record<string, unknown>;
  bot_name?: string;
  app_id?: string;
  has_secret?: boolean;
}

type ActionResult = { ok: boolean; note?: string; error?: string };

/** 列表行：config 里的通道 ∪ 运行时才有的通道（如 harunet，由顶层 harunet.* 装配） */
interface Row {
  key: string;
  session: string;
  type: string;
  channel_id: string;
  params: Record<string, unknown>;
  health?: ChannelHealthRow;
  cfg?: { si: number; ci: number };   // 来自 config sessions 时可编辑并保存
}

const CHANNEL_TEMPLATE: ChannelEntry = {
  type: "qqbot",
  channel_id: "qq_new",
  params: { require_mention: true, priority: "high" },
};

// 各类型通道在详情页展示的字段（其余内部调参不展示，避免"乱七八糟"）
const FIELDS: Record<string, string[]> = {
  harunet: ["url", "name", "password", "http_base", "focus"],
  qqbot: ["app_id", "app_secret", "require_mention", "priority", "sandbox"],
  tui: [],
  architect_telemetry: [],
};
const LABELS: Record<string, string> = {
  url: "hub 地址", name: "网络昵称", password: "接入密码", http_base: "上传地址",
  focus: "侧重方向", app_id: "AppID", app_secret: "AppSecret",
  require_mention: "仅@时响应", priority: "消息优先级", sandbox: "QQ 沙箱",
};
const SECRET_RE = /secret|password|token|api_key|app_secret/i;

/** 通道类型 → 用户能看懂的名字与一句话说明（列表/详情都用） */
const TYPE_INFO: Record<string, { label: string; desc: string }> = {
  tui: { label: "终端界面", desc: "当前这个 TUI 本身，无需配置" },
  harunet: { label: "HaruNet 组网", desc: "接入智能体组网：hub 地址 / 昵称 / 接入密码" },
  qqbot: { label: "QQ 频道机器人", desc: "QQ 官方机器人；AppID/AppSecret 在此直接配置，即时生效" },
  architect_telemetry: { label: "架构师遥测", desc: "内部通道：把运行事件喂给 arch，自动工作，无需配置" },
};
function typeLabel(type: string) { return TYPE_INFO[type]?.label ?? type; }

function readConfig(): Record<string, unknown> {
  const p = path.join(PROJECT_ROOT, "config.yaml");
  if (!existsSync(p)) return {};
  try {
    // uniqueKeys:false —— Python yaml 容忍重复键（后者生效），这里保持一致
    const parsed = YAML.parse(readFileSync(p, "utf8"), { uniqueKeys: false });
    return (parsed as Record<string, unknown>) ?? {};
  } catch {
    return {};
  }
}

function isSecret(key: string) {
  return SECRET_RE.test(key);
}

function mask(v: unknown): string {
  const s = v == null ? "" : String(v);
  if (!s) return "（未设置）";
  return s.length > 4 ? "****" + s.slice(-4) : "****";
}

function fmtVal(key: string, v: unknown): string {
  if (v == null || v === "") return "（未设置）";
  if (isSecret(key)) return mask(v);
  if (Array.isArray(v)) return v.length ? v.map(String).join(", ") : "（空）";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function stateColor(h?: ChannelHealthRow): string {
  if (!h) return "gray";
  if (h.ok) return "green";
  if (h.state === "connecting") return "yellow";
  return "red";
}

function stateText(h?: ChannelHealthRow): string {
  if (!h) return "未运行";
  const zh: Record<string, string> = {
    ready: "就绪", online: "在线", offline: "离线", connecting: "连接中",
    not_configured: "未配置", error: "错误",
  };
  return zh[h.state] ?? h.state;
}

function buildRows(sessions: SessionEntry[], health: ChannelHealthRow[]): Row[] {
  const rows: Row[] = [];
  const used = new Set<string>();
  sessions.forEach((s, si) => {
    (s.channels ?? []).forEach((ch, ci) => {
      const sid = s.id ?? `session_${si}`;
      const h = health.find((x) => x.session === sid && x.type === ch.type
                             && x.channel_id === ch.channel_id);
      if (h) used.add(`${h.session}|${h.type}|${h.channel_id}`);
      rows.push({
        key: `${sid}|${ch.type}|${ch.channel_id}`,
        session: sid, type: ch.type, channel_id: ch.channel_id,
        params: { ...(ch.params ?? {}), ...(h?.params ?? {}) },
        health: h, cfg: { si, ci },
      });
    });
  });
  for (const h of health) {
    const k = `${h.session}|${h.type}|${h.channel_id}`;
    if (used.has(k)) continue;
    rows.push({
      key: k, session: h.session, type: h.type, channel_id: h.channel_id,
      params: { ...(h.params ?? {}) }, health: h,
    });
  }
  return rows;
}

type Item =
  | { kind: "field"; key: string; label: string; value: unknown; display?: string }
  | { kind: "action"; id: "reconnect"; label: string; hint: string };

/**
 * 通道设置：列表（运行状态）→ Enter 进入该通道的配置详情 → 字段可编辑。
 * 编辑即保存：字段回车确认后立即持久化（config 字段写 config.yaml、
 * qqbot 凭据写 .env 并热重连、harunet 密码走 RPC），没有二次"保存"步骤。
 */
export function ChannelsEditorScreen({
  active, onClose, onSave, onResult, onHealth, onReconnect, onSetPassword,
  onSetCredentials,
}: {
  active: boolean;
  onClose: () => void;
  onSave: (key: string, value: unknown) => Promise<string>;
  onResult: (msg: string) => void;
  onHealth: () => Promise<ChannelHealthRow[]>;
  onReconnect: (channelId: string) => Promise<ActionResult>;
  onSetPassword: (channelId: string, password: string) => Promise<ActionResult>;
  onSetCredentials: (channelId: string, appId: string, appSecret: string) => Promise<ActionResult>;
}) {
  const [sessions, setSessions] = useState<SessionEntry[]>([]);
  const [health, setHealth] = useState<ChannelHealthRow[]>([]);
  const [view, setView] = useState<"list" | "detail">("list");
  const [index, setIndex] = useState(0);        // 列表选中
  const [sel, setSel] = useState(0);            // 详情选中（字段/操作）
  const [editing, setEditing] = useState<{ key: string; label: string; secret: boolean } | null>(null);
  const [editValue, setEditValue] = useState("");
  const [busy, setBusy] = useState(false);

  const rows = useMemo(() => buildRows(sessions, health), [sessions, health]);
  const current = rows[Math.min(index, rows.length - 1)];

  useEffect(() => {
    if (!active) return;
    const cfg = readConfig();
    setSessions(Array.isArray(cfg.sessions) ? (cfg.sessions as SessionEntry[]) : []);
    setIndex(0); setView("list"); setSel(0); setEditing(null);
  }, [active]);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    const tick = () => onHealth().then((r) => { if (!cancelled) setHealth(r ?? []); }).catch(() => {});
    tick();
    const t = setInterval(tick, 5000);
    return () => { cancelled = true; clearInterval(t); };
  }, [active, onHealth]);

  const items = useMemo<Item[]>(() => {
    if (!current) return [];
    const list: Item[] = [];
    const keys = FIELDS[current.type] ?? Object.keys(current.params);
    for (const key of keys) {
      if (current.type === "qqbot" && (key === "app_id" || key === "app_secret")) {
        // 凭据始终展示（通常存在 .env 而非 params）：值取自运行时 health
        if (key === "app_id") {
          const v = current.health?.app_id || current.params.app_id || "";
          list.push({ kind: "field", key, label: LABELS[key], value: v });
        } else {
          const has = Boolean(current.health?.has_secret || current.params.app_secret);
          list.push({ kind: "field", key, label: LABELS[key], value: has ? "*" : "",
                      display: has ? "已设置（Enter 输入新值覆盖）" : "（未设置）" });
        }
        continue;
      }
      if (!(key in current.params)) continue;
      list.push({ kind: "field", key, label: LABELS[key] ?? key, value: current.params[key] });
    }
    if (current.type === "harunet") {
      list.push({ kind: "action", id: "reconnect", label: "立即重连", hint: "Enter 触发：跳过退避，立刻重新握手" });
    }
    return list;
  }, [current]);

  const startEdit = (key: string, label: string) => {
    const secret = isSecret(key);
    setEditing({ key, label, secret });
    // qqbot AppID 一般不在 params（在 .env），预填运行时值
    const prefill = current?.type === "qqbot" && key === "app_id"
      ? (current.health?.app_id ?? current.params[key] ?? "")
      : (current?.params[key] ?? "");
    setEditValue(secret ? "" : String(prefill));
  };

  /** 配置改动立即写回 config.yaml（无二次确认步骤）。 */
  const persist = (next: SessionEntry[]) => {
    setSessions(next);
    setBusy(true);
    onSave("sessions", next)
      .then((msg) => onResult(`${msg}（重启 Gateway 后生效）`))
      .catch((e) => onResult(`保存失败: ${e}`))
      .finally(() => setBusy(false));
  };

  const applyEdit = (raw: string) => {
    const ed = editing;
    setEditing(null);
    if (!ed || !current) return;
    const v = raw.trim();
    if (current.type === "harunet" && ed.key === "password") {
      // 接入密码：写回 .env + 立即重连（env 优先级高于 config，写 config 会被遮蔽）
      if (!v) { onResult("密码为空，未修改"); return; }
      setBusy(true);
      onSetPassword(current.channel_id, v)
        .then((r) => onResult(r.ok ? `✔ ${r.note ?? "接入密码已更新"}` : `✘ ${r.error ?? "失败"}`))
        .catch((e) => onResult(`✘ ${e}`))
        .finally(() => setBusy(false));
      return;
    }
    if (current.type === "qqbot" && (ed.key === "app_id" || ed.key === "app_secret")) {
      // 凭据：写 .env + 热重连，立即生效（不走 config.yaml）
      if (!v) {
        onResult(ed.key === "app_id" ? "AppID 不能为空" : "留空 = 保持不变");
        return;
      }
      const appId = ed.key === "app_id"
        ? v : String(current.health?.app_id ?? current.params.app_id ?? "");
      const secret = ed.key === "app_secret" ? v : "";
      setBusy(true);
      onSetCredentials(current.channel_id, appId, secret)
        .then((r) => onResult(r.ok ? `✔ ${r.note ?? "凭据已更新"}` : `✘ ${r.error ?? "失败"}`))
        .catch((e) => onResult(`✘ ${e}`))
        .finally(() => setBusy(false));
      return;
    }
    if (!current.cfg) {
      // 运行时通道（harunet）：字段直接写顶层 harunet.<key>
      setBusy(true);
      onSave(`harunet.${ed.key}`, v)
        .then((msg) => onResult(`${msg}（${ed.label}，重启生效）`))
        .catch((e) => onResult(`保存失败: ${e}`))
        .finally(() => setBusy(false));
      return;
    }
    const { si, ci } = current.cfg;
    persist(sessions.map((s, i) => i === si
      ? {
          ...s,
          channels: (s.channels ?? []).map((ch, c) => (c === ci
            ? { ...ch, params: { ...(ch.params ?? {}), [ed.key]: v } } : ch)),
        }
      : s));
  };

  const doReconnect = () => {
    if (!current) return;
    setBusy(true);
    onReconnect(current.channel_id)
      .then((r) => onResult(r.ok ? `✔ ${r.note ?? "已触发重连"}` : `✘ ${r.error ?? "失败"}`))
      .catch((e) => onResult(`✘ ${e}`))
      .finally(() => setBusy(false));
  };

  useInput((input, key) => {
    if (editing) {
      if (key.escape) setEditing(null);
      return;   // TextInput 拥有其余按键
    }
    if (busy) return;   // 保存/动作进行中，忽略输入防止并发写
    if (view === "detail") {
      if (key.escape) { setView("list"); return; }
      if (key.upArrow) { setSel((i) => Math.max(0, i - 1)); return; }
      if (key.downArrow) { setSel((i) => Math.min(items.length - 1, i + 1)); return; }
      if (key.return && items[sel]) {
        const it = items[sel];
        if (it.kind === "field") startEdit(it.key, it.label);
        else doReconnect();
      }
      return;
    }
    // ── 列表 ──
    if (key.escape) { onClose(); return; }
    if (key.upArrow) { setIndex((i) => Math.max(0, i - 1)); return; }
    if (key.downArrow) { setIndex((i) => Math.min(rows.length - 1, i + 1)); return; }
    if (key.return && current) { setView("detail"); setSel(0); return; }
    if (input === "a" && sessions.length > 0) {
      persist(sessions.map((s, si) => si === 0
        ? { ...s, channels: [...(s.channels ?? []), { ...CHANNEL_TEMPLATE }] }
        : s));
      setIndex(rows.length);
      return;
    }
    if (input === "d" && current?.cfg) {
      const { si, ci } = current.cfg;
      persist(sessions.map((s, i) => i === si
        ? { ...s, channels: (s.channels ?? []).filter((_, c) => c !== ci) } : s));
      setIndex((i) => Math.max(0, Math.min(i, rows.length - 2)));
      return;
    }
  }, { isActive: active });

  if (!active) return null;

  const botName = current?.type === "qqbot" ? (current.health?.bot_name ?? "") : "";

  return (
    <Box flexDirection="column" borderStyle="round" borderColor="cyan" backgroundColor="#1c1e2b" paddingX={1}>
      <Text bold color="cyan" backgroundColor="#1c1e2b">
        📡 通道{view === "detail" && current
          ? ` › ${typeLabel(current.type)}（${current.type}#${current.channel_id}）${botName ? ` 🤖 ${botName}` : ""}` : ""}
      </Text>
      {view === "list" && (
        <>
          <Text dimColor backgroundColor="#1c1e2b">
            各通道运行状态；Enter 进入配置（修改即保存）
          </Text>
          {rows.length === 0 && (
            <Text color="yellow" backgroundColor="#1c1e2b">（无通道，按 a 新增）</Text>
          )}
          {rows.map((row, i) => {
            const selected = i === index;
            const h = row.health;
            const info = TYPE_INFO[row.type];
            const bot = row.type === "qqbot" && h?.bot_name ? ` · 🤖 ${h.bot_name}` : "";
            const st = h ? `${stateText(h)}${h.detail ? ` · ${h.detail}` : ""}` : "未运行";
            const line = `${selected ? "› " : "  "}● ${typeLabel(row.type).padEnd(8)} ${st}`;
            return (
              <Box key={row.key} flexDirection="column" backgroundColor={selected ? "cyan" : "#1c1e2b"}>
                <Text backgroundColor={selected ? "cyan" : "#1c1e2b"}
                      color={selected ? "black" : stateColor(h)} wrap="truncate">
                  {line}
                </Text>
                {(selected || !info) && (
                  <Text backgroundColor={selected ? "cyan" : "#1c1e2b"}
                        color={selected ? "black" : "gray"} dimColor={!selected} wrap="truncate">
                    {`    ${row.type}#${row.channel_id}${bot}${info ? ` — ${info.desc}` : ""}`}
                  </Text>
                )}
              </Box>
            );
          })}
          <Text dimColor backgroundColor="#1c1e2b">
            ↑↓ 选择 · Enter 配置 · a 新增(qqbot) · d 删除 · Esc 返回
          </Text>
        </>
      )}
      {view === "detail" && current && (
        <>
          <Text backgroundColor="#1c1e2b" color={stateColor(current.health)}>
            {`状态：${stateText(current.health)}${current.health?.detail ? ` — ${current.health.detail}` : ""}${botName ? ` · 🤖 ${botName}` : ""}`}
          </Text>
          <Text dimColor backgroundColor="#1c1e2b">─ 配置（回车确认即保存）─</Text>
          {items.length === 0 && (
            <Text dimColor backgroundColor="#1c1e2b">
              {`  ${TYPE_INFO[current.type]?.desc ?? "内部通道"}（无需配置）`}
            </Text>
          )}
          {items.map((it, i) => {
            const selected = i === sel;
            const body = it.kind === "field"
              ? `${it.label}：${it.display ?? fmtVal(it.key, it.value)}`
              : `⚡ ${it.label}`;
            return (
              <Text key={i} backgroundColor={selected ? "cyan" : "#1c1e2b"}
                    color={selected ? "black" : (it.kind === "action" ? "yellow" : undefined)}
                    wrap="truncate">
                {`${selected ? "› " : "  "}${body}`}
              </Text>
            );
          })}
          {editing && (
            <Box>
              <Text color="yellow" backgroundColor="#1c1e2b">
                {editing.label}{editing.secret ? "（输入不可见）" : ""}:{" "}
              </Text>
              <TextInput value={editValue} onChange={setEditValue} onSubmit={applyEdit}
                         placeholder={editing.secret ? "输入新值，回车确认；留空保持" : ""} />
            </Box>
          )}
          <Text dimColor backgroundColor="#1c1e2b">
            {busy ? "保存中…"
              : items[sel]?.kind === "action" ? (items[sel] as { hint?: string }).hint
              : "Enter 编辑该字段（回车确认即保存）"}
          </Text>
          <Text dimColor backgroundColor="#1c1e2b">
            ↑↓ 选择 · Enter 编辑/执行 · Esc 返回列表
          </Text>
        </>
      )}
    </Box>
  );
}
