import React, { useEffect, useRef, useState } from "react";
import { Box, Text, useApp, useInput } from "ink";
import Spinner from "ink-spinner";
import chalk from "chalk";
import clipboardy from "clipboardy";
import { writeFileSync } from "node:fs";
import path from "node:path";
import { GatewayClient, PROJECT_ROOT } from "../gateway/GatewayClient.js";
import type { HistoryMessage } from "../gateway/protocol.js";
import { formatTokens, nowHHMMSS } from "../format.js";
import { wrappedMessageLines, type ChatMessage } from "./MessageItem.js";
import { StatusBar, type StatusState, type StatusType, type TokenStats } from "./StatusBar.js";
import { InputBar } from "./InputBar.js";
import { CommandSuggestions } from "./CommandSuggestions.js";
import {
  ToolDetailPanel,
  formatArgsBrief,
  formatArgsFull,
  makeSummary,
  toolDetailLines,
  TOOL_PANEL_ROWS,
  type ToolHistoryEntry,
} from "./ToolDetailPanel.js";
import { SubagentBar, type SubagentInfo } from "./SubagentBar.js";
import { setMouseEnabled } from "../mouse.js";
import { debugLog } from "../debugLog.js";
import { filterCommands, HELP_LINES } from "../commands.js";
import { detectAndCache } from "../promptCache.js";
import { TextViewerScreen } from "../screens/TextViewer.js";
import { PromptCacheScreen } from "../screens/PromptCacheScreen.js";
import { PromptEditorScreen } from "../screens/PromptEditorScreen.js";
import { ConfigEditorScreen } from "../screens/ConfigEditorScreen.js";
import { SubagentScreen } from "../screens/SubagentScreen.js";

type ScreenName = "textviewer" | "promptcache" | "prompteditor" | "config" | "subagent";
type BrowseMode = "none" | "agents";

let nextId = 1;

type DistributiveOmit<T, K extends keyof any> = T extends any ? Omit<T, K> : never;
type NewMessage = DistributiveOmit<ChatMessage, "id" | "ts">;

interface LiveTool {
  callId: string;
  tool: string;
  args: string;
  argsFull: string;
  step: number;
}

const SPLASH: Array<{ text: string; color?: string }> = [
  { text: "" },
  { text: "  ██████╗  ██████╗ ██╗  ██╗ █████╗ ██████╗ ██╗   ██╗", color: "cyan" },
  { text: "  ██╔════╝ ██╔═══██╗██║  ██║██╔══██╗██╔══██╗██║   ██║", color: "cyan" },
  { text: "  ██║  ███╗██║   ██║███████║███████║██████╔╝██║   ██║", color: "blue" },
  { text: "  ██║   ██║██║   ██║██╔══██║██╔══██║██╔══██╗██║   ██║", color: "blue" },
  { text: "  ╚██████╔╝╚██████╔╝██║  ██║██║  ██║██║  ██║╚██████╔╝", color: "magenta" },
  { text: "   ╚═════╝  ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝", color: "magenta" },
  { text: "" },
  { text: "  Your Personal Coding Agent — Program, Debug, Automate.", color: "cyan" },
  { text: "" },
];

function initialMessages(): ChatMessage[] {
  return [
    ...SPLASH.map((s) => ({
      id: nextId++,
      ts: nowHHMMSS(),
      kind: "splash" as const,
      text: s.text,
      color: s.color,
    })),
    {
      id: nextId++,
      ts: nowHHMMSS(),
      kind: "system" as const,
      text: "Type your message and press Enter to start. Waiting for Gateway...",
    },
  ];
}

/** Clear terminal screen + scrollback (Windows Terminal / ANSI). */
function clearTerminal(): void {
  process.stdout.write("\x1b[2J\x1b[3J\x1b[H");
}

function parseTs(raw: string | undefined): string {
  if (raw) {
    const m = /T(\d{2}:\d{2}:\d{2})/.exec(raw);
    if (m) return m[1];
  }
  return nowHHMMSS();
}

const HISTORY_REPLAY_FALLBACK = 20;
const SESSION_ID = "default";

export function App({ mock = false }: { mock?: boolean }) {
  const { exit } = useApp();
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages);
  const [status, setStatus] = useState<StatusState>({ kind: "static", text: "Starting...", type: "info" });
  const [stats, setStats] = useState<TokenStats | null>(null);
  const [liveTools, setLiveTools] = useState<LiveTool[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [selIndex, setSelIndex] = useState(0);
  const [screen, setScreen] = useState<ScreenName | null>(null);
  const [browseMode, setBrowseMode] = useState<BrowseMode>("none");
  const [toolHistory, setToolHistory] = useState<ToolHistoryEntry[]>([]);
  const [detailToolId, setDetailToolId] = useState<number | null>(null);
  const [detailScroll, setDetailScroll] = useState(0);
  const [subagents, setSubagents] = useState<SubagentInfo[]>([]);
  const [agentSel, setAgentSel] = useState(0);
  const [selectedAgentId, setSelectedAgentId] = useState<string>("main");
  const liveToolsRef = useRef<LiveTool[]>([]);
  const gatewayRef = useRef<GatewayClient | null>(null);
  const answerRenderedRef = useRef(false);
  const lastAssistantRef = useRef("");
  const historyRef = useRef<string[]>([]);
  // Chat viewport: app-managed scrolling (replaces terminal scrollback)
  const [scroll, setScroll] = useState<{ pinned: boolean; top: number }>({ pinned: false, top: 0 });
  const [hoverId, setHoverId] = useState<number | null>(null);
  const scrollRef = useRef(scroll);
  scrollRef.current = scroll;
  const hoverRef = useRef<number | null>(null);
  hoverRef.current = hoverId;
  // Mouse hit-map: viewport row → line owner (rebuilt every render)
  const layoutRef = useRef<{
    top: number;
    viewH: number;
    maxTop: number;
    lines: Array<{ id: number; kind: string }>;
  }>({ top: 0, viewH: 1, maxTop: 0, lines: [] });
  const clickHandlerRef = useRef<(x: number, y: number) => void>(() => {});
  const detailIdRef = useRef<number | null>(null);
  detailIdRef.current = detailToolId;

  const addMsg = (msg: NewMessage & { ts?: string }) =>
    setMessages((prev) => [...prev, { ...msg, id: nextId++, ts: msg.ts ?? nowHHMMSS() } as ChatMessage]);
  const addRef = useRef(addMsg);
  addRef.current = addMsg;
  const showWorkingRef = useRef((label: string) => setStatus({ kind: "spinner", label }));
  const showStatic = (text: string, type: StatusType = "info") =>
    setStatus({ kind: "static", text, type });

  const replayMessages = (all: HistoryMessage[]) => {
    const add = addRef.current;
    // 优先回放今天的全部条目，让界面"好像从未关闭"；跨天无内容时回退最近 N 条
    const now = new Date();
    const pad = (n: number) => String(n).padStart(2, "0");
    const todayPrefix = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
    let entries = all.filter((m) => (m.timestamp ?? "").startsWith(todayPrefix));
    let truncated = 0;
    if (entries.length === 0 && all.length > 0) {
      entries = all.slice(-HISTORY_REPLAY_FALLBACK);
      truncated = all.length - entries.length;
    }
    if (entries.length === 0) {
      add({ kind: "system", text: "--- No history to restore ---" });
      return;
    }
    if (truncated > 0) {
      add({ kind: "system", text: `(更久远的对话... ${truncated} entries)` });
    }
    entries.forEach((m, i) => {
      setTimeout(() => {
        const ts = parseTs(m.timestamp);
        if (m.kind === "thinking") {
          add({ kind: "thinking", text: m.text ?? "", step: 0, ts });
        } else if (m.kind === "tool") {
          const result = m.result ?? "(no result recorded)";
          const { summary, isError } = makeSummary(result);
          add({
            kind: "tool",
            tool: m.tool ?? "?",
            args: m.args ?? "",
            argsFull: m.args_full ?? m.args ?? "",
            result,
            isError: Boolean(m.is_error) || isError,
            ts,
          });
          setToolHistory((prev) => [
            ...prev.slice(-49),
            {
              callId: `replay_${m.timestamp}_${m.tool ?? ""}`,
              tool: m.tool ?? "?",
              args: m.args ?? "",
              argsFull: m.args_full ?? m.args ?? "",
              result,
              isError: Boolean(m.is_error) || isError,
              ts,
              summary,
            },
          ]);
        } else if (m.role === "user") {
          add({ kind: "user", text: m.text ?? "", ts });
          historyRef.current.push(`[${ts}] You: ${m.text ?? ""}`);
        } else {
          lastAssistantRef.current = m.text ?? "";
          historyRef.current.push(`[${ts}] Assistant:\n${m.text ?? ""}`);
          add({ kind: "assistant", text: m.text ?? "", ts });
        }
      }, 30 * (i + 1));
    });
  };

  useEffect(() => {
    const gw = new GatewayClient({ mock });
    gatewayRef.current = gw;
    const add = (msg: NewMessage) => addRef.current(msg);
    const showWorking = (label: string) => showWorkingRef.current(label);

    gw.on("gateway.ready", () => {
      showStatic("Gateway ready - You can start chatting!", "success");
      add({ kind: "system", text: "Gateway is ready! You can now send messages." });
      gw.call("agent.get_history", {})
        .then((res) => replayMessages(res.messages ?? []))
        .catch((e) => add({ kind: "system", text: `(history load failed: ${e})` }));
    });
    gw.on("gateway.init_error", (p) => {
      showStatic(`FATAL: ${p.message}`, "error");
      add({ kind: "system", text: "=".repeat(50) });
      add({ kind: "error", text: `Gateway failed to start: ${p.message}` });
      add({ kind: "system", text: "Please check config.yaml and restart." });
      add({ kind: "system", text: "=".repeat(50) });
    });
    gw.on("agent.thinking", () => showWorking("Thinking..."));
    gw.on("agent.thinking_content", (p) => {
      if (p.thinking) add({ kind: "thinking", text: p.thinking, step: p.step ?? 0 });
    });
    gw.on("agent.text_content", (p) => {
      if (p.text) add({ kind: "note", text: p.text, step: p.step ?? 0 });
    });
    gw.on("agent.step", (p) => showWorking(`Processing step ${p.step}...`));
    gw.on("agent.llm_requesting", () => showWorking("Requesting..."));
    gw.on("agent.l1_extracting", () => showWorking("Querying..."));
    gw.on("agent.answer", (p) => {
      if (p.answer) {
        lastAssistantRef.current = p.answer;
        historyRef.current.push(`[${nowHHMMSS()}] Assistant:\n${p.answer}`);
        add({ kind: "assistant", text: p.answer });
        answerRenderedRef.current = true;
      }
    });
    gw.on("tool.call", (p) => {
      showWorking(`Processing: ${p.tool}`);
      const callId = String(p.call_id ?? "");
      const entry: LiveTool = {
        callId,
        tool: String(p.tool ?? "unknown"),
        args: formatArgsBrief(p.arguments),
        argsFull: formatArgsFull(p.arguments),
        step: Number(p.step ?? 0),
      };
      liveToolsRef.current = [...liveToolsRef.current.filter((t) => t.callId !== callId), entry];
      setLiveTools(liveToolsRef.current);
    });
    gw.on("tool.result", (p) => {
      showWorking("Thinking...");
      const callId = String(p.call_id ?? "");
      const t = liveToolsRef.current.find((x) => x.callId === callId);
      liveToolsRef.current = liveToolsRef.current.filter((x) => x.callId !== callId);
      setLiveTools(liveToolsRef.current);
      const result = String(p.result ?? "");
      const toolName = t?.tool ?? String(p.tool ?? "unknown");
      const argsBrief = t?.args ?? "";
      const argsFull = t?.argsFull ?? "";
      const { summary, isError } = makeSummary(result);
      setToolHistory((prev) => [
        ...prev.slice(-49),
        { callId, tool: toolName, args: argsBrief, argsFull, result, isError, ts: nowHHMMSS(), summary },
      ]);
      add({ kind: "tool", tool: toolName, args: argsBrief, argsFull, result, isError });
    });
    gw.on("subagent.update", (p) => {
      setSubagents((prev) => {
        const idx = prev.findIndex((a) => a.agentId === p.agent_id);
        let next: SubagentInfo[];
        if (p.kind === "started" || idx < 0) {
          const info: SubagentInfo = {
            agentId: p.agent_id,
            agentType: p.agent_type,
            task: p.task,
            status: p.kind === "finished" ? p.status ?? "success" : "running",
            startedAtMs: Date.now(),
            activities: [{ level: p.level, message: p.message }],
          };
          next = idx >= 0 ? prev.map((a, i) => (i === idx ? info : a)) : [...prev, info];
        } else {
          next = prev.map((a, i) => {
            if (i !== idx) return a;
            const activities = [...a.activities.slice(-99), { level: p.level, message: p.message }];
            return p.kind === "finished"
              ? { ...a, activities, status: p.status ?? "success" }
              : { ...a, activities };
          });
        }
        // Prune oldest finished agents beyond 10 (running agents never pruned)
        const finishedIdxs = next
          .map((a, i) => (a.status !== "running" ? i : -1))
          .filter((i) => i >= 0);
        if (finishedIdxs.length > 10) next.splice(finishedIdxs[0], 1);
        return next;
      });
    });
    gw.on("token.stats", (p) => {
      setStats({
        tokens: formatTokens(Number(p.current_tokens ?? 0)),
        cache: `${((Number(p.prompt_cache_ratio) || 0) * 100).toFixed(1)}%`,
      });
    });
    gw.on("user.batch", (p) =>
      add({ kind: "system", text: `Batched ${p.count} messages into one context` }));
    gw.on("user.merge", (p) =>
      add({ kind: "system", text: `Merged ${p.count} new message(s) before next reasoning step` }));
    gw.on("message.complete", (p) => {
      if (!answerRenderedRef.current && p.answer) {
        lastAssistantRef.current = p.answer;
        add({ kind: "assistant", text: p.answer });
      }
      answerRenderedRef.current = false;
      showStatic("Ready", "success");
    });
    gw.on("task.background.started", (p) => {
      const names = (p.tool_calls ?? []).map((t: { tool_name?: string }) => t.tool_name ?? "?").join(", ");
      showWorking(`BG task: ${names}`);
    });
    gw.on("task.background.completed", (p) =>
      showStatic(`BG tasks completed: ${p.count}`, "success"));
    gw.on("task.background.status", (p) => {
      if (Number(p.pending_results) > 0) showWorking(`BG: ${p.pending_results} pending`);
    });
    gw.on("agent.interrupted", (p) => {
      liveToolsRef.current = [];
      setLiveTools([]);
      add({ kind: "system", text: `! ${p.message}` });
      showWorking("What should I do?");
    });
    gw.on("agent.error", (p) => {
      add({ kind: "error", text: p.message });
      showStatic(`Error: ${String(p.message).slice(0, 50)}`, "error");
      try { gw.restart(); } catch { /* restart failure surfaces via next error */ }
    });
    gw.on("command.complete", (p) => {
      if (p.success) {
        const text = p.command === "compact"
          ? "Conversation compacted — history summarized via LLM"
          : p.command === "clear_session"
            ? "Session cleared on gateway"
            : `Command '${p.command}' completed`;
        add({ kind: "system", text });
      } else {
        add({ kind: "error", text: `Command '${p.command}' failed: ${p.error ?? ""}` });
      }
    });

    gw.start();
    return () => gw.stop();
  }, [mock]);

  // Re-render on terminal resize (screen is cleared in main.tsx before render)
  useEffect(() => {
    const onResize = () => setScroll((s) => ({ ...s }));
    process.stdout.on("resize", onResize);
    return () => {
      process.stdout.off("resize", onResize);
    };
  }, []);

  const quit = () => {
    setMouseEnabled(false);
    gatewayRef.current?.stop();
    exit();
  };

  const clearAll = () => {
    clearTerminal();
    setMessages([]);
    historyRef.current = [];
    setToolHistory([]);
    setSubagents([]);
    setBrowseMode("none");
    setDetailToolId(null);
    setDetailScroll(0);
    setScroll({ pinned: false, top: 0 });
    setHoverId(null);
    setStatus({ kind: "static", text: "Chat cleared", type: "success" });
    gatewayRef.current
      ?.call("agent.clear_session", { session_id: SESSION_ID })
      .catch(() => { /* best effort */ });
  };

  const interrupt = () => {
    setStatus({ kind: "static", text: "Interrupting...", type: "warning" });
    gatewayRef.current
      ?.call("agent.interrupt", {})
      .then((r) => {
        if (!r?.interrupted) setStatus({ kind: "static", text: "Ready", type: "success" });
      })
      .catch(() => setStatus({ kind: "static", text: "Ready", type: "success" }));
  };

  const suggestions = filterCommands(inputValue);
  if (selIndex >= suggestions.length && suggestions.length > 0) setSelIndex(0);

  useInput((input, key) => {
    if (key.ctrl && input === "c") {
      quit();
      return;
    }
    // While a screen is open, the screen owns all other keys.
    if (screen) return;
    if (key.ctrl && input === "l") {
      clearAll();
      return;
    }
    // F2 sequences (xterm SS3 / CSI) or Ctrl+T → text viewer for last reply
    if (input.includes("OQ") || input.includes("[12~") || (key.ctrl && input === "t")) {
      if (lastAssistantRef.current) setScreen("textviewer");
      else addRef.current({ kind: "system", text: "No assistant message to view" });
      return;
    }
    // Swallow leaked mouse-report fragments (parsed by mouse.ts separately)
    if (input.includes("\x1b[<")) return;
    // ── Browse modes own all keys (Esc exits mode, never interrupts) ──
    if (detailToolId !== null) {
      // Tool detail panel open: arrows scroll, Esc closes
      const detailMsg = messages.find((m) => m.id === detailToolId);
      const lineCount =
        detailMsg && detailMsg.kind === "tool"
          ? toolDetailLines({
              tool: detailMsg.tool,
              argsBrief: detailMsg.args,
              argsFull: detailMsg.argsFull ?? detailMsg.args,
              result: detailMsg.result,
              isError: detailMsg.isError,
              ts: detailMsg.ts,
            }).length
          : 0;
      const maxScroll = Math.max(0, lineCount - (TOOL_PANEL_ROWS - 4));
      if (key.escape) {
        setDetailToolId(null);
        return;
      }
      if (key.upArrow) {
        setDetailScroll((s) => Math.max(0, s - 1));
        return;
      }
      if (key.downArrow) {
        setDetailScroll((s) => Math.min(maxScroll, s + 1));
        return;
      }
      if (key.pageUp) {
        setDetailScroll((s) => Math.max(0, s - 10));
        return;
      }
      if (key.pageDown) {
        setDetailScroll((s) => Math.min(maxScroll, s + 10));
        return;
      }
      return;
    }
    if (browseMode === "agents") {
      const rowCount = 1 + subagents.length;
      if (key.escape) {
        setBrowseMode("none");
        return;
      }
      if (key.upArrow) {
        setAgentSel((i) => Math.max(0, i - 1));
        return;
      }
      if (key.downArrow) {
        setAgentSel((i) => Math.min(Math.max(0, rowCount - 1), i + 1));
        return;
      }
      if (key.return) {
        setSelectedAgentId(agentSel === 0 ? "main" : subagents[agentSel - 1]?.agentId ?? "main");
        setBrowseMode("none");
        setScreen("subagent");
        return;
      }
      return;
    }

    if (inputValue.startsWith("/") && suggestions.length > 0) {
      if (key.upArrow) {
        setSelIndex((i) => (i - 1 + suggestions.length) % suggestions.length);
        return;
      }
      if (key.downArrow) {
        setSelIndex((i) => (i + 1) % suggestions.length);
        return;
      }
      if (key.tab) {
        setInputValue(suggestions[selIndex].name + " ");
        setSelIndex(0);
        return;
      }
    }
    if (inputValue === "") {
      if (key.downArrow && subagents.length > 0) {
        setAgentSel(0);
        setBrowseMode("agents");
        return;
      }
    }
    if (key.pageUp || key.pageDown) {
      const L = layoutRef.current;
      const cur = scrollRef.current;
      const base = cur.pinned ? cur.top : L.maxTop;
      const next = Math.max(0, Math.min(L.maxTop, base + (key.pageUp ? -L.viewH : L.viewH)));
      setScroll({ pinned: next < L.maxTop, top: next });
      return;
    }
    if (key.escape) {
      interrupt();
    }
  });

  const runCommand = (raw: string): boolean => {
    const cmd = raw.toLowerCase().split(/\s+/)[0];
    const gw = gatewayRef.current;
    const add = addRef.current;

    switch (cmd) {
      case "/clear":
        clearAll();
        add({ kind: "system", text: "Chat history cleared" });
        return true;
      case "/compact":
        gw?.call("agent.compact", { session_id: SESSION_ID })
          .then((r) => {
            if (r?.queued) add({ kind: "system", text: "Compact queued — will execute after current tasks complete" });
          })
          .catch((e) => add({ kind: "error", text: `Compact failed: ${e}` }));
        return true;
      case "/copy": {
        const last = lastAssistantRef.current;
        if (!last) {
          add({ kind: "system", text: "No assistant message to copy" });
          return true;
        }
        clipboardy.write(last)
          .then(() => add({ kind: "system", text: "Last assistant message copied to clipboard!" }))
          .catch(() => {
            try {
              writeFileSync(path.join(PROJECT_ROOT, "last_message.txt"), last, "utf8");
              add({ kind: "system", text: "Last message saved to last_message.txt (clipboard unavailable)" });
            } catch (e) {
              add({ kind: "error", text: `Failed to copy: ${e}` });
            }
          });
        return true;
      }
      case "/export": {
        try {
          const now = new Date();
          const pad = (n: number) => String(n).padStart(2, "0");
          const filename = `chat_export_${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}.txt`;
          writeFileSync(path.join(PROJECT_ROOT, filename), historyRef.current.join("\n\n") + "\n", "utf8");
          add({ kind: "system", text: `Chat history exported to ${filename}` });
        } catch (e) {
          add({ kind: "error", text: `Failed to export: ${e}` });
        }
        return true;
      }
      case "/help":
        for (const line of HELP_LINES) add({ kind: "system", text: line });
        return true;
      case "/prompt":
        setScreen("prompteditor");
        return true;
      case "/prompt-cache":
        setScreen("promptcache");
        return true;
      case "/config":
        setScreen("config");
        return true;
      case "/exit":
      case "/quit":
        quit();
        return true;
      default:
        add({ kind: "error", text: `Unknown command: ${cmd}` });
        add({ kind: "system", text: "Type /help for available commands" });
        return true;
    }
  };

  const onSubmit = (raw: string) => {
    const message = raw.trim();
    if (!message) return;
    setSelIndex(0);

    if (message.startsWith("/")) {
      runCommand(message);
      return;
    }

    detectAndCache(message);
    setMessages((prev) => [...prev, { id: nextId++, ts: nowHHMMSS(), kind: "user", text: message }]);
    historyRef.current.push(`[${nowHHMMSS()}] You: ${message}`);

    const gw = gatewayRef.current;
    if (!gw) return;
    gw.call("agent.send_message", { message, session_id: SESSION_ID }).catch(async () => {
      try {
        gw.restart();
        await gw.call("agent.send_message", { message, session_id: SESSION_ID });
      } catch (err) {
        addRef.current({ kind: "error", text: `Failed to send message: ${err}` });
      }
    });
  };

  const saveConfig = async (key: string, value: unknown): Promise<string> => {
    const gw = gatewayRef.current;
    if (!gw) throw new Error("gateway not connected");
    const r = await gw.call("agent.update_config", { key, value });
    return `✅ ${key} = ${JSON.stringify(value)}（旧值: ${JSON.stringify(r.old_value)}）`;
  };

  // ── Chat viewport geometry (deterministic: every line is pre-wrapped) ──
  const width = Math.max(20, process.stdout.columns || 80);
  const termRows = process.stdout.rows || 24;
  let bottomRows = 0;
  if (screen === null) {
    if (liveTools.length > 0) bottomRows += liveTools.length;
    if (subagents.length > 0 || browseMode === "agents")
      bottomRows += browseMode === "agents" ? subagents.length + 2 : 1;
    if (suggestions.length > 0) bottomRows += suggestions.length + 2; // border box
    if (detailToolId !== null) bottomRows += TOOL_PANEL_ROWS;
    if (scroll.pinned) bottomRows += 1; // "scrolled up" hint
    bottomRows += 2; // StatusBar + InputBar
  }
  const viewH = Math.max(3, termRows - bottomRows);

  const allLines: Array<{ id: number; kind: string; text: string }> = [];
  for (const m of messages) {
    for (const text of wrappedMessageLines(m, width)) {
      allLines.push({ id: m.id, kind: m.kind, text });
    }
  }
  const maxTop = Math.max(0, allLines.length - viewH);
  const effTop = scroll.pinned ? Math.min(scroll.top, maxTop) : maxTop;
  const visibleLines = allLines.slice(effTop, effTop + viewH);
  layoutRef.current = {
    top: effTop,
    viewH,
    maxTop,
    lines: allLines.map((l) => ({ id: l.id, kind: l.kind })),
  };

  const scrollChatBy = (delta: number) => {
    const cur = scrollRef.current;
    const base = cur.pinned ? cur.top : layoutRef.current.maxTop;
    const next = Math.max(0, Math.min(layoutRef.current.maxTop, base + delta));
    setScroll({ pinned: next < layoutRef.current.maxTop, top: next });
  };

  clickHandlerRef.current = (_x, y) => {
    const layout = layoutRef.current;
    if (y < 1 || y > layout.viewH) {
      debugLog("click", `@y=${y} outside viewport (viewH=${layout.viewH}) — ignored`);
      return;
    }
    const line = layout.lines[layout.top + (y - 1)];
    debugLog("click", `@y=${y} → line[${layout.top + (y - 1)}] kind=${line?.kind ?? "none"} id=${line?.id ?? "-"}`);
    if (line && line.kind === "tool") {
      setDetailScroll(0);
      setDetailToolId((cur) => (cur === line.id ? null : line.id));
    }
  };

  // Mouse reporting: active only on the main chat view (screens need native selection)
  useEffect(() => {
    setMouseEnabled(
      screen === null,
      (x, y) => clickHandlerRef.current(x, y),
      (dir, _x, y) => {
        const layout = layoutRef.current;
        if (y > layout.viewH) {
          // Over bottom UI: wheel scrolls the open detail panel
          if (detailIdRef.current !== null) {
            setDetailScroll((s) => Math.max(0, s + (dir === "up" ? -3 : 3)));
          }
          return;
        }
        scrollChatBy(dir === "up" ? -3 : 3);
      },
      (_x, y) => {
        const layout = layoutRef.current;
        let id: number | null = null;
        if (y >= 1 && y <= layout.viewH) {
          const line = layout.lines[layout.top + (y - 1)];
          if (line && line.kind === "tool") id = line.id;
        }
        if (hoverRef.current !== id) {
          debugLog("hover", `@y=${y} → toolId=${id ?? "-"}`);
          hoverRef.current = id;
          setHoverId(id);
        }
      },
    );
  }, [screen]);
  useEffect(() => () => setMouseEnabled(false), []);

  return (
    <Box flexDirection="column">
      {screen === null && (
        <Box flexDirection="column" height={viewH} overflow="hidden">
          {visibleLines.map((l, i) => (
            <Text key={effTop + i} wrap="truncate">
              {l.id === hoverId && l.kind === "tool" ? chalk.inverse(l.text) : l.text}
            </Text>
          ))}
        </Box>
      )}
      {screen === null && scroll.pinned && (
        <Text dimColor>↑ scrolled — wheel/PgDn to bottom to follow</Text>
      )}
      {screen === null && liveTools.length > 0 && (
        <Box flexDirection="column">
          {liveTools.map((t) => (
            <Text key={t.callId} color="magenta">
              <Spinner type="dots" /> {t.tool}<Text dimColor>({t.args})</Text>
            </Text>
          ))}
        </Box>
      )}
      {screen === null && (subagents.length > 0 || browseMode === "agents") && (
        <SubagentBar agents={subagents} selectMode={browseMode === "agents"} selIndex={agentSel} />
      )}
      {screen === null && <CommandSuggestions suggestions={suggestions} selectedIndex={selIndex} />}
      {screen === null && <StatusBar status={status} stats={stats} />}
      {screen === null && detailToolId !== null && (() => {
        const m = messages.find((x) => x.id === detailToolId);
        if (!m || m.kind !== "tool") return null;
        return (
          <ToolDetailPanel
            data={{
              tool: m.tool,
              argsBrief: m.args,
              argsFull: m.argsFull ?? m.args,
              result: m.result,
              isError: m.isError,
              ts: m.ts,
            }}
            scroll={detailScroll}
          />
        );
      })()}
      {screen === null && (
        <InputBar value={inputValue} onChange={setInputValue} onSubmit={onSubmit} focus={browseMode === "none"} />
      )}
      <TextViewerScreen
        active={screen === "textviewer"}
        title="Agent Response (select & copy text freely)"
        content={lastAssistantRef.current}
        onClose={() => setScreen(null)}
      />
      <PromptCacheScreen
        active={screen === "promptcache"}
        onSelect={(text) => {
          setInputValue(text);
          setScreen(null);
          addRef.current({ kind: "system", text: "📋 Prompt loaded into input — press Enter to send or edit first" });
        }}
        onClose={() => setScreen(null)}
      />
      <PromptEditorScreen
        active={screen === "prompteditor"}
        onClose={() => setScreen(null)}
      />
      <ConfigEditorScreen
        active={screen === "config"}
        onClose={() => setScreen(null)}
        onSave={saveConfig}
        onResult={(msg) => addRef.current({ kind: "system", text: msg })}
      />
      <SubagentScreen
        active={screen === "subagent"}
        agent={
          selectedAgentId === "main"
            ? null
            : subagents.find((a) => a.agentId === selectedAgentId) ?? null
        }
        mainTools={toolHistory}
        onClose={() => setScreen(null)}
      />
    </Box>
  );
}
