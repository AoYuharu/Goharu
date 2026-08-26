import React, { useEffect, useRef, useState } from "react";
import { Box, Static, Text, useApp, useInput } from "ink";
import Spinner from "ink-spinner";
import clipboardy from "clipboardy";
import { writeFileSync } from "node:fs";
import path from "node:path";
import { GatewayClient, PROJECT_ROOT } from "../gateway/GatewayClient.js";
import type { HistoryMessage } from "../gateway/protocol.js";
import { formatTokens, nowHHMMSS } from "../format.js";
import { MessageItem, type ChatMessage } from "./MessageItem.js";
import { StatusBar, type StatusState, type StatusType, type TokenStats } from "./StatusBar.js";
import { InputBar } from "./InputBar.js";
import { CommandSuggestions } from "./CommandSuggestions.js";
import { filterCommands, HELP_LINES } from "../commands.js";
import { detectAndCache } from "../promptCache.js";
import { TextViewerScreen } from "../screens/TextViewer.js";
import { PromptCacheScreen } from "../screens/PromptCacheScreen.js";
import { PromptEditorScreen } from "../screens/PromptEditorScreen.js";
import { ConfigEditorScreen } from "../screens/ConfigEditorScreen.js";

type ScreenName = "textviewer" | "promptcache" | "prompteditor" | "config";

let nextId = 1;

type DistributiveOmit<T, K extends keyof any> = T extends any ? Omit<T, K> : never;
type NewMessage = DistributiveOmit<ChatMessage, "id" | "ts">;

interface LiveTool {
  callId: string;
  tool: string;
  args: string;
  step: number;
}

function summarizeArgs(args: unknown, max = 120): string {
  let rendered: string;
  try {
    rendered = JSON.stringify(args) ?? String(args);
  } catch {
    rendered = String(args);
  }
  const compact = rendered.replace(/\s+/g, " ").trim();
  return compact.length <= max ? compact : compact.slice(0, max - 3) + "...";
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

const HISTORY_REPLAY_LIMIT = 5;
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
  const liveToolsRef = useRef<LiveTool[]>([]);
  const gatewayRef = useRef<GatewayClient | null>(null);
  const answerRenderedRef = useRef(false);
  const lastAssistantRef = useRef("");
  const historyRef = useRef<string[]>([]);

  const addMsg = (msg: NewMessage & { ts?: string }) =>
    setMessages((prev) => [...prev, { ...msg, id: nextId++, ts: msg.ts ?? nowHHMMSS() } as ChatMessage]);
  const addRef = useRef(addMsg);
  addRef.current = addMsg;
  const showWorkingRef = useRef((label: string) => setStatus({ kind: "spinner", label }));
  const showStatic = (text: string, type: StatusType = "info") =>
    setStatus({ kind: "static", text, type });

  const replayMessages = (all: HistoryMessage[]) => {
    const add = addRef.current;
    const chatOnly = all.filter(
      (m) => m.type === "text" && (m.role === "user" || m.role === "assistant") && m.content,
    );
    if (!chatOnly.length) {
      add({ kind: "system", text: "--- No history to restore ---" });
      return;
    }
    let slice = chatOnly;
    if (chatOnly.length > HISTORY_REPLAY_LIMIT) {
      add({ kind: "system", text: `(更久远的对话... ${chatOnly.length - HISTORY_REPLAY_LIMIT} messages)` });
      slice = chatOnly.slice(-HISTORY_REPLAY_LIMIT);
    }
    slice.forEach((m, i) => {
      setTimeout(() => {
        const ts = parseTs(m.timestamp);
        if (m.role === "user") {
          add({ kind: "user", text: m.content, ts });
          historyRef.current.push(`[${ts}] You: ${m.content}`);
        } else {
          lastAssistantRef.current = m.content;
          historyRef.current.push(`[${ts}] Assistant:\n${m.content}`);
          add({ kind: "assistant", text: m.content, ts });
        }
      }, 60 * (i + 1));
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
        args: summarizeArgs(p.arguments),
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
      if (t) {
        add({ kind: "tool", tool: t.tool, args: t.args, result: String(p.result ?? "") });
      }
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

  const quit = () => {
    gatewayRef.current?.stop();
    exit();
  };

  const clearAll = () => {
    clearTerminal();
    setMessages([]);
    historyRef.current = [];
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

  return (
    <Box flexDirection="column">
      <Static items={messages}>
        {(msg) => <MessageItem key={msg.id} msg={msg} />}
      </Static>
      {screen === null && liveTools.length > 0 && (
        <Box flexDirection="column">
          {liveTools.map((t) => (
            <Text key={t.callId} color="magenta">
              <Spinner type="dots" /> {t.tool}<Text dimColor>({t.args})</Text>
            </Text>
          ))}
        </Box>
      )}
      {screen === null && <CommandSuggestions suggestions={suggestions} selectedIndex={selIndex} />}
      {screen === null && <StatusBar status={status} stats={stats} />}
      {screen === null && (
        <InputBar value={inputValue} onChange={setInputValue} onSubmit={onSubmit} focus />
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
    </Box>
  );
}
