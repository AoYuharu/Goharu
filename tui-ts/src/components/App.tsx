import React, { useEffect, useRef, useState } from "react";
import { Box, Text, useApp, useInput } from "ink";
import Spinner from "ink-spinner";
import stripAnsi from "strip-ansi";
import clipboardy from "clipboardy";
import { writeFileSync } from "node:fs";
import path from "node:path";
import { GatewayClient, PROJECT_ROOT } from "../gateway/GatewayClient.js";
import type { HistoryMessage, VoiceStatus, ModelStatus, BrowserLlmStatus, UserQuestionOption } from "../gateway/protocol.js";
import { formatTokens, formatTokensCompact, nowHHMMSS, highlightLine, hoverLine } from "../format.js";
import { wrappedMessageLines, buildMessageLines, type ChatMessage } from "./MessageItem.js";
import { StatusBar, type StatusState, type StatusType, type TokenStats } from "./StatusBar.js";
import { InputBar } from "./InputBar.js";
import { CommandSuggestions } from "./CommandSuggestions.js";
import {
  ToolDetailPanel,
  formatArgsBrief,
  formatArgsFull,
  makeSummary,
  toolDetailLines,
  toolPanelLines,
  TOOL_PANEL_ROWS,
  type ToolDetailData,
  type ToolHistoryEntry,
} from "./ToolDetailPanel.js";
import { SubagentBar, type SubagentInfo } from "./SubagentBar.js";
import { ConfirmDialog, CONFIRM_PANEL_ROWS, type ConfirmRequest } from "./ConfirmDialog.js";
import {
  QuestionDialog,
  QUESTION_PANEL_ROWS,
  customIndex,
  isBatchQuestion,
  type QuestionAnswers,
  type UserQuestionRequest,
} from "./QuestionDialog.js";
import { BgTaskBar, KILL_BUTTON_WIDTH, type BgTask } from "./BgTaskBar.js";
import { CheckpointBar, type CheckpointStatus } from "./CheckpointBar.js";
import { TaskBar, type TaskItem } from "./TaskBar.js";
import { VoiceSettingsPanel, VOICE_PANEL_ROWS } from "./VoiceSettingsPanel.js";
import { ModelSettingsPanel, modelPanelRows, type ModelDraft } from "./ModelSettingsPanel.js";
import { setMouseEnabled } from "../mouse.js";
import { debugLog } from "../debugLog.js";
import { subagentToMessages } from "../subagentMessages.js";
import wrapAnsi from "wrap-ansi";
import { filterCommands, HELP_LINES } from "../commands.js";
import { detectAndCache } from "../promptCache.js";
import { TextViewerScreen } from "../screens/TextViewer.js";
import { PromptCacheScreen } from "../screens/PromptCacheScreen.js";
import { PromptEditorScreen } from "../screens/PromptEditorScreen.js";
import { ConfigEditorScreen } from "../screens/ConfigEditorScreen.js";
import { ChannelsEditorScreen } from "../screens/ChannelsEditorScreen.js";
import { ToolsScreen } from "../screens/ToolsScreen.js";
import type { ToolInfo } from "../gateway/protocol.js";

type ScreenName = "textviewer" | "promptcache" | "prompteditor" | "config" | "tools" | "channels";
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
  cmd?: string; // run_cmd 的原始命令（用于关联实时输出）
}

/** token.stats / 子代理 token_stats → 状态栏 TokenStats（命中率 + 读/总量明细） */
function buildTokenStats(p: {
  current_tokens?: number;
  prompt_cache_ratio?: number;
  cache_read_tokens?: number;
  cache_write_tokens?: number;
  uncached_input_tokens?: number;
}): TokenStats {
  const read = Number(p.cache_read_tokens ?? 0);
  const write = Number(p.cache_write_tokens ?? 0);
  const uncached = Number(p.uncached_input_tokens ?? 0);
  const totalInput = read + write + uncached;
  const pct = (Number(p.prompt_cache_ratio) || 0) * 100;
  const cache = totalInput > 0
    ? `${pct.toFixed(1)}% (${formatTokensCompact(read)}/${formatTokensCompact(totalInput)})`
    : "—";
  return { tokens: formatTokens(Number(p.current_tokens ?? 0)), cache };
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
const LIVE_PANEL_ROWS = 12;

export function App({ mock = false }: { mock?: boolean }) {
  const { exit } = useApp();
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages);
  const [status, setStatus] = useState<StatusState>({ kind: "static", text: "Starting...", type: "info" });
  const [stats, setStats] = useState<TokenStats | null>(null);
  const [liveTools, setLiveTools] = useState<LiveTool[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [selIndex, setSelIndex] = useState(0);
  const [screen, setScreen] = useState<ScreenName | null>(null);
  // /tools 面板数据
  const [toolList, setToolList] = useState<ToolInfo[]>([]);
  const [toolsLoading, setToolsLoading] = useState(false);
  const [toolsError, setToolsError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [browseMode, setBrowseMode] = useState<BrowseMode>("none");
  const [toolHistory, setToolHistory] = useState<ToolHistoryEntry[]>([]);
  const [detailToolId, setDetailToolId] = useState<number | null>(null);
  const [detailScroll, setDetailScroll] = useState(0);
  // 运行中工具的实时输出（仅在展开时显示；未展开只显示"正在运行"）
  const [liveOutput, setLiveOutput] = useState<Array<{ line: string; stream: string }>>([]);
  const [liveOutputToolId, setLiveOutputToolId] = useState<string | null>(null);
  // 危险命令确认队列
  const [confirmQueue, setConfirmQueue] = useState<ConfirmRequest[]>([]);
  const [confirmSel, setConfirmSel] = useState(0); // 0=拒绝, 1=允许
  // AskUser 交互提问队列（选项面板，含"自己填写"项）
  const [questionQueue, setQuestionQueue] = useState<UserQuestionRequest[]>([]);
  const [questionSel, setQuestionSel] = useState(0);
  const [questionInputMode, setQuestionInputMode] = useState(false);
  const [questionDraft, setQuestionDraft] = useState("");
  const [questionAnswers, setQuestionAnswers] = useState<QuestionAnswers>({});
  const [questionPage, setQuestionPage] = useState(0);
  const [settingsOpen, setSettingsOpen] = useState(false); // 设置菜单：0=模型, 1=语音, 2=浏览器LLM
  const [settingsSel, setSettingsSel] = useState(0);
  const [browserLlm, setBrowserLlm] = useState<BrowserLlmStatus | null>(null); // 浏览器 LLM 抽取开关状态
  const [browserLlmBusy, setBrowserLlmBusy] = useState(false);
  const [modelOpen, setModelOpen] = useState(false);
  const [modelStatus, setModelStatus] = useState<ModelStatus | null>(null);
  const [currentModel, setCurrentModel] = useState("");
  const [modelSel, setModelSel] = useState(0);
  const [modelDraft, setModelDraft] = useState<ModelDraft>({ model: "", base_url: "", api_key: "" });
  const [voiceOpen, setVoiceOpen] = useState(false);
  const [voiceStatus, setVoiceStatus] = useState<VoiceStatus | null>(null);
  const [voiceSel, setVoiceSel] = useState(0);
  const [voiceDownloadPct, setVoiceDownloadPct] = useState<number | null>(null);
  const [voiceAskDownload, setVoiceAskDownload] = useState(false);
  const [voiceIndicator, setVoiceIndicator] = useState<string | null>(null);
  const voicePendingEnableRef = useRef<{ asr_enabled?: boolean; wakeup_enabled?: boolean } | null>(null);
  const [subagents, setSubagents] = useState<SubagentInfo[]>([]);
  // 各子代理的最新 Token/Cache 统计（agent_id → 状态栏数据），子代理视图右下角显示
  const [subagentStats, setSubagentStats] = useState<Record<string, TokenStats>>({});
  const [bgTasks, setBgTasks] = useState<BgTask[]>([]);
  const [checkpoint, setCheckpoint] = useState<CheckpointStatus | null>(null);
  const [taskList, setTaskList] = useState<TaskItem[]>([]);
  const [agentSel, setAgentSel] = useState(0);
  // Which agent's context owns the chat viewport ("main" or an agent_id)
  const [viewAgentId, setViewAgentId] = useState<string>("main");
  const viewAgentIdRef = useRef(viewAgentId);
  viewAgentIdRef.current = viewAgentId;
  // Per-view scroll memory + current view's message list (for detail lookup)
  const viewScrollsRef = useRef(new Map<string, { pinned: boolean; top: number }>());
  const viewMessagesRef = useRef<ChatMessage[]>([]);
  const liveToolsRef = useRef<LiveTool[]>([]);
  const gatewayRef = useRef<GatewayClient | null>(null);
  const answerRenderedRef = useRef(false);
  const lastAssistantRef = useRef("");
  const historyRef = useRef<string[]>([]);
  // Chat viewport: app-managed scrolling (replaces terminal scrollback)
  const [scroll, setScroll] = useState<{ pinned: boolean; top: number }>({ pinned: false, top: 0 });
  const [hoverId, setHoverId] = useState<number | null>(null);
  const [settingsHover, setSettingsHover] = useState(false);
  const scrollRef = useRef(scroll);
  scrollRef.current = scroll;
  const hoverRef = useRef<number | null>(null);
  hoverRef.current = hoverId;
  // Settings 按钮命中区域（右下角，随尺寸/渲染刷新）
  const settingsHitRef = useRef<{ row: number; x0: number }>({ row: -1, x0: -1 });
  // Mouse hit-map: viewport row → line owner (rebuilt every render)
  const layoutRef = useRef<{
    top: number;
    viewH: number;
    topOffset: number;         // 1 = voice indicator bar occupies the top row
    maxTop: number;
    lines: Array<{ id: number; kind: string; text: string }>;
    bottomOffset: number;      // index namespace: [0, bottomOffset) = chat, then panel lines
    panelStartRow: number;     // 1-based first interior row of the detail panel, -1 = closed
    panelLines: string[];
    liveToolsStartRow: number; // 1-based first row of the running-tools block, -1 = none
    liveToolsCount: number;
    bgTaskStartRow: number;    // 1-based first row of BgTaskBar, -1 = none
    bgTaskCount: number;
  }>({ top: 0, viewH: 1, topOffset: 0, maxTop: 0, lines: [], bottomOffset: 0, panelStartRow: -1, panelLines: [], liveToolsStartRow: -1, liveToolsCount: 0, bgTaskStartRow: -1, bgTaskCount: 0 });
  const clickHandlerRef = useRef<(x: number, y: number) => void>(() => {});
  const rowToIndexRef = useRef<(y: number) => number | null>(() => null);
  const detailIdRef = useRef<number | null>(null);
  detailIdRef.current = detailToolId;
  const liveOutputToolIdRef = useRef<string | null>(null);
  liveOutputToolIdRef.current = liveOutputToolId;
  // Drag-to-select: absolute line-index range within the chat viewport
  const [selRange, setSelRange] = useState<{ from: number; to: number } | null>(null);
  const [mouseOff, setMouseOff] = useState(false);
  const selAnchorRef = useRef<number | null>(null);
  const selRangeRef = useRef(selRange);
  selRangeRef.current = selRange;

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
      setReady(true);
      // 前后状态同步：gateway 可能刚重启（子进程崩溃后心跳自动拉起），
      // 旧 session 里所有瞬时状态都已失效。若不清空，TUI 会残留
      // "running" 的子代理 / 工具 / 后台任务僵尸条目，误导用户。
      liveToolsRef.current = [];
      setLiveTools([]);
      setSubagents([]);
      setSubagentStats({});
      setBgTasks([]);
      setConfirmQueue([]);
      setDetailToolId(null);
      setLiveOutputToolId(null);
      setVoiceIndicator(null);
      setBrowseMode("none");
      answerRenderedRef.current = false;
      showStatic("Gateway ready - You can start chatting!", "success");
      add({ kind: "system", text: "Gateway is ready! You can now send messages." });
      gw.call("agent.get_history", {})
        .then((res) => replayMessages(res.messages ?? []))
        .catch((e) => add({ kind: "system", text: `(history load failed: ${e})` }));
      // 左下角当前模型指示
      gw.call("model.get_status", {})
        .then((s) => setCurrentModel(s.model))
        .catch(() => {});
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
    gw.on("agent.retry", (p) => {
      const msg = p.phase === "requesting"
        ? `正在重试 LLM ${p.attempt}/${p.max_retries}（重新请求中）...`
        : `正在重试 LLM ${p.attempt}/${p.max_retries}（退避 ${p.backoff}s）...`;
      showWorking(msg);
    });
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
      const toolName = String(p.tool ?? "unknown");
      const argsObj = p.arguments && typeof p.arguments === "object" ? p.arguments as Record<string, unknown> : {};
      const entry: LiveTool = {
        callId,
        tool: toolName,
        args: formatArgsBrief(p.arguments),
        argsFull: formatArgsFull(p.arguments),
        step: Number(p.step ?? 0),
        cmd: toolName === "run_cmd" ? String(argsObj.cmd ?? "") : undefined,
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
      // 该工具结束 → 关闭它的实时输出面板
      if (liveOutputToolIdRef.current === callId) setLiveOutputToolId(null);
    });
    // 危险命令确认请求：进入队列，弹确认框
    gw.on("tool.confirm_request", (p) => {
      setConfirmQueue((prev) => [...prev, {
        confirm_id: String(p.confirm_id ?? ""),
        cmd: String(p.cmd ?? ""),
        description: String(p.description ?? ""),
      }]);
      setConfirmSel(0); // 默认拒绝，需用户主动右移选择允许
    });
    // AskUser 交互提问：进入队列，弹选项面板（questions 数组，支持批量多问/多选）
    gw.on("tool.user_question_request", (p) => {
      const raw = (Array.isArray(p.questions) ? p.questions : []) as Array<{
        question?: unknown;
        options?: unknown;
        multi_select?: unknown;
      }>;
      const questions = raw
        .filter((q) => q && Array.isArray(q.options) && q.options.length > 0)
        .map((q) => ({
          question: String(q.question ?? ""),
          options: q.options as UserQuestionOption[],
          multi_select: Boolean(q.multi_select),
        }));
      if (questions.length === 0) return;
      setQuestionQueue((prev) => [...prev, {
        request_id: String(p.request_id ?? ""),
        questions,
      }]);
      setQuestionSel(0);
      setQuestionInputMode(false);
      setQuestionDraft("");
      setQuestionAnswers({});
      setQuestionPage(0);
    });
    // 实时输出流：缓存到滚动缓冲（仅展开时显示，未展开不刷屏）
    gw.on("tool.output", (p) => {
      const line = String(p.line ?? "");
      const stream = String(p.stream ?? "stdout");
      setLiveOutput((prev) => [...prev.slice(-299), { line, stream }]);
    });
    gw.on("subagent.update", (p) => {
      if (p.kind === "stats") {
        // 统计事件：只更新右下角数据，不进子代理活动流
        if (p.token_stats) {
          setSubagentStats((prev) => ({ ...prev, [p.agent_id]: buildTokenStats(p.token_stats!) }));
        }
        return;
      }
      const activity = {
        level: p.level,
        message: p.message,
        ts: nowHHMMSS(),
        category: p.category,
        content: p.content,
        tool: p.tool,
        is_error: p.is_error,
      };
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
            activities: [activity],
          };
          next = idx >= 0 ? prev.map((a, i) => (i === idx ? info : a)) : [...prev, info];
        } else {
          next = prev.map((a, i) => {
            if (i !== idx) return a;
            const activities = [...a.activities.slice(-99), activity];
            return p.kind === "finished"
              ? { ...a, activities, status: p.status ?? "success" }
              : { ...a, activities };
          });
        }
        return next;
      });
      // Finished agents fade out after 30s (unless currently being viewed)
      if (p.kind === "finished") {
        const agentId = p.agent_id;
        setTimeout(() => {
          if (viewAgentIdRef.current === agentId) return;
          setSubagents((prev) => prev.filter((a) => a.agentId !== agentId || a.status === "running"));
        }, 30_000);
      }
    });
    gw.on("token.stats", (p) => {
      setStats(buildTokenStats(p));
    });
    gw.on("user.message", (p) =>
      add({ kind: "user", text: p.text }));
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
    gw.on("task.background.update", (p) => {
      // AgentDelegate subagents have their own bar — excluded here
      if (p.tool === "AgentDelegate") return;
      if (p.status === "running") {
        setBgTasks((prev) => {
          const entry: BgTask = {
            taskId: p.task_id,
            tool: p.tool,
            args: p.args ?? "",
            startedAtMs: (p.started_at ?? 0) * 1000 || Date.now(),
          };
          return prev.some((t) => t.taskId === p.task_id)
            ? prev.map((t) => (t.taskId === p.task_id ? entry : t))
            : [...prev, entry];
        });
      } else {
        setBgTasks((prev) => prev.filter((t) => t.taskId !== p.task_id));
        // 显式反馈结束原因：超时被杀不能静默消失（透明化原则）
        if (p.timed_out) {
          showStatic(`BG #${p.task_id} ${p.tool} 超时被终止`, "error");
        } else if (p.status === "error") {
          showStatic(`BG #${p.task_id} ${p.tool} 失败${p.error ? `：${p.error}` : ""}`, "error");
        }
      }
    });
    gw.on("task.background.completed", (p) =>
      showStatic(`BG tasks completed: ${p.count}`, "success"));
    gw.on("task.background.status", (p) => {
      // 静态而非 spinner：这可能是最后一个事件，spinner 会永远转圈误导用户
      if (Number(p.pending_results) > 0) showStatic(`BG: ${p.pending_results} pending`, "info");
    });
    gw.on("tasks.updated", (p) => {
      setTaskList(Array.isArray(p.tasks) ? p.tasks : []);
    });
    gw.on("checkpoint.created", (p) => {
      setCheckpoint({
        seq: p.seq, files: p.files, l1Ops: p.l1_ops,
        gitCaught: p.git_caught ?? 0, degraded: p.degraded ?? false,
        kind: p.kind, rollingBackTo: null,
      });
    });
    gw.on("checkpoint.rollback.started", (p) => {
      setCheckpoint((prev) => ({
        seq: prev?.seq ?? p.target, files: prev?.files ?? 0, l1Ops: prev?.l1Ops ?? 0,
        rollingBackTo: p.target,
      }));
    });
    gw.on("checkpoint.rollback.completed", (p) => {
      setCheckpoint((prev) => (prev ? { ...prev, rollingBackTo: null } : prev));
      if (p.ok) {
        showStatic(`回退完成 → cp#${p.rollback_seq}（撤销 ${p.undone} 个 checkpoint）`, "success");
      } else {
        showStatic(`回退部分失败：${(p.failed ?? []).length} 项未应用`, "error");
      }
    });
    gw.on("checkpoint.degraded", (p) => {
      showStatic(`checkpoint git 降级：${p.reason ?? "未知原因"}（本轮回退仅覆盖工具写）`, "error");
    });
    gw.on("voice.download_progress", (p) => setVoiceDownloadPct(p.percent ?? 0));
    gw.on("voice.transcript", (p) => {
      // 语音识别结果填入输入框，由用户回车发送
      setVoiceOpen(false);
      setInputValue((prev) => (prev ? prev + " " + p.text : p.text));
      setVoiceIndicator("🎤 已填入输入框，回车发送");
    });
    gw.on("voice.state", (p) => {
      if (p.state === "listening") setVoiceIndicator("🎤 聆听中…");
      else if (p.state === "recognizing") setVoiceIndicator("🎤 识别中…");
    });
    gw.on("voice.download_complete", (p) => {
      setVoiceDownloadPct(null);
      if (p.ok) {
        if (p.status) setVoiceStatus(p.status);
        const pending = voicePendingEnableRef.current;
        if (pending) {
          voicePendingEnableRef.current = null;
          gw.call("voice.set_config", pending).then(setVoiceStatus);
        }
      }
      showStatic(p.ok ? "语音模型下载完成" : `语音模型下载失败: ${p.error ?? "未知错误"}`, p.ok ? "success" : "error");
    });
    gw.on("agent.interrupted", (p) => {
      liveToolsRef.current = [];
      setLiveTools([]);
      add({ kind: "system", text: `! ${p.message}` });
      // 中断是终态，不是"进行中"——必须用静态文本，spinner 会永远转圈
      showStatic("Interrupted - What should I do?", "warning");
    });
    gw.on("agent.error", (p) => {
      const meta = [p.blocking, p.cause, p.scope].filter(Boolean).join(" / ");
      add({ kind: "error", text: meta ? `[${meta}] ${p.message}` : p.message });
      showStatic(`Error: ${String(p.message).slice(0, 50)}`, "error");
      // 只有进程级致命错误才重启；配置/单轮/通道错误只展示，避免崩溃循环
      if (p.blocking === "fatal") {
        try { gw.restart(); } catch { /* restart failure surfaces via next error */ }
      }
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

  // 打开设置菜单时刷新浏览器 LLM 开关状态
  useEffect(() => {
    if (!settingsOpen) return;
    gatewayRef.current
      ?.call("browser_llm.get_status", {})
      .then(setBrowserLlm)
      .catch(() => setBrowserLlm(null));
  }, [settingsOpen]);

  const quit = () => {
    setMouseEnabled(false);
    gatewayRef.current?.stop();
    exit();
  };

  // 危险命令确认应答
  const answerConfirm = (req: ConfirmRequest, approved: boolean) => {
    gatewayRef.current
      ?.call("agent.confirm_response", { confirm_id: req.confirm_id, approved })
      .catch(() => { /* best effort */ });
    setConfirmQueue((prev) => prev.slice(1));
    setConfirmSel(0);
  };

  // AskUser 提问应答：单问=选中的 label 或自填；向导=JSON({answers:[...]})
  const answerQuestion = (req: UserQuestionRequest, answer: string) => {
    gatewayRef.current
      ?.call("agent.user_question_response", { request_id: req.request_id, answer })
      .catch(() => { /* best effort */ });
    setQuestionQueue((prev) => prev.slice(1));
    setQuestionSel(0);
    setQuestionInputMode(false);
    setQuestionDraft("");
    setQuestionAnswers({});
    setQuestionPage(0);
  };

  // 向导问卷：按当前勾选状态组装答案并提交（未作答单选=null，多选=[]）
  const submitBatchAnswers = (req: UserQuestionRequest) => {
    const answers = req.questions.map((q, qi) => {
      const ans = questionAnswers[qi];
      if (q.multi_select) {
        return { question: q.question, answer: Array.isArray(ans) ? ans : [] };
      }
      return { question: q.question, answer: typeof ans === "string" ? ans : null };
    });
    answerQuestion(req, JSON.stringify({ answers }));
  };

  const clearAll = () => {
    clearTerminal();
    setMessages([]);
    historyRef.current = [];
    setToolHistory([]);
    setSubagents([]);
    setSubagentStats({});
    setBgTasks([]);
    setBrowseMode("none");
    setDetailToolId(null);
    setDetailScroll(0);
    setScroll({ pinned: false, top: 0 });
    setHoverId(null);
    setSelRange(null);
    setLiveOutput([]);
    setLiveOutputToolId(null);
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

  const cancelBgTask = (taskId: number) => {
    setStatus({ kind: "static", text: `Cancelling BG #${taskId}...`, type: "warning" });
    gatewayRef.current
      ?.call("agent.cancel_background_task", { task_id: taskId })
      .then((r) => {
        if (r?.cancelled) {
          setStatus({ kind: "static", text: `BG #${taskId} cancelled`, type: "success" });
        } else {
          setStatus({ kind: "static", text: `BG #${taskId} not found`, type: "warning" });
        }
      })
      .catch((e) => setStatus({ kind: "static", text: `Cancel BG #${taskId} failed: ${String(e)}`, type: "error" }));
  };

  const openModelPanel = () => {
    setSettingsOpen(false);
    setModelOpen(true);
    setModelSel(0);
    setModelDraft({ model: "", base_url: "", api_key: "" });
    gatewayRef.current
      ?.call("model.get_status", {})
      .then((s) => {
        setModelStatus(s);
        setModelDraft((d) => ({
          model: d.model === "" ? s.model : d.model,
          base_url: d.base_url === "" ? s.base_url : d.base_url,
          api_key: d.api_key,
        }));
      })
      .catch(() => setModelStatus(null));
  };

  // 浏览器 LLM 抽取开关：切换后 Gateway 会重连 browser-use MCP server
  const toggleBrowserLlm = () => {
    if (browserLlmBusy) return;
    const next = !(browserLlm?.enabled ?? false);
    setBrowserLlmBusy(true);
    showStatic(next ? "正在开启浏览器 LLM 抽取…" : "正在关闭…", "info");
    gatewayRef.current
      ?.call("browser_llm.set_config", { enabled: next })
      .then((r) => {
        setBrowserLlm({ enabled: r.enabled, model: r.model, base_url: r.base_url });
        showStatic(r.message || (r.success ? "已切换" : "切换失败"), r.success ? "success" : "error");
      })
      .catch((e) => showStatic(`浏览器 LLM 切换失败: ${String(e)}`, "error"))
      .finally(() => setBrowserLlmBusy(false));
  };

  const saveModelConfig = () => {
    const gw = gatewayRef.current;
    if (!gw) return;
    gw.call("model.set_config", {
      model: modelDraft.model.trim() || undefined,
      base_url: modelDraft.base_url.trim() || undefined,
      api_key: modelDraft.api_key.trim() || undefined,
    })
      .then((r) => {
        if (!r.success) {
          // 优雅失败：一句人话 + 保持面板打开聚焦到 API Key 栏，等待修正重试
          showStatic(`❌ ${r.message ?? "模型切换失败"}（已回退，当前仍使用 ${r.fallback_model || "原模型"}）`, "error");
          if (r.error_kind === "auth") {
            setModelSel(2);
            setModelDraft((d) => ({ ...d, api_key: "" }));
          }
          return;
        }
        showStatic(`模型配置已保存并热生效（${r.changed.join("、") || "无变更"}）`, "success");
        setCurrentModel(modelDraft.model.trim());
        setModelOpen(false);
        setSettingsOpen(false);
      })
      .catch((e) => showStatic(`模型配置保存失败: ${e}`, "error"));
  };

  const switchToRecentModel = (entry: { model: string; base_url: string; api_key_env?: string }) => {
    const gw = gatewayRef.current;
    if (!gw) return;
    gw.call("model.set_config", {
      model: entry.model,
      base_url: entry.base_url,
      api_key_env: entry.api_key_env,
    })
      .then((r) => {
        if (!r.success) {
          showStatic(`❌ ${r.message ?? "模型切换失败"}（已回退，当前仍使用 ${r.fallback_model || "原模型"}）`, "error");
          if (r.error_kind === "auth") {
            // 认证失败：把面板填充为该条目的手动切换表单，聚焦 key 栏等待重填
            setModelSel(2);
            setModelDraft({ model: entry.model, base_url: entry.base_url, api_key: "" });
          }
          return;
        }
        showStatic(`已切换到 ${entry.model}`, "success");
        setCurrentModel(entry.model);
        setModelOpen(false);
        setSettingsOpen(false);
      })
      .catch((e) => showStatic(`模型切换失败: ${e}`, "error"));
  };

  const openVoicePanel = () => {
    setSettingsOpen(false);
    setVoiceOpen(true);
    setVoiceSel(0);
    setVoiceDownloadPct(null);
    setVoiceAskDownload(false);
    voicePendingEnableRef.current = null;
    gatewayRef.current
      ?.call("voice.get_status", {})
      .then((s) => setVoiceStatus(s))
      .catch(() => setVoiceStatus(null));
  };

  const applyVoiceSel = () => {
    const st = voiceStatus;
    const gw = gatewayRef.current;
    if (!st || !gw) return;
    const needModel = !st.models.sensevoice;
    if (voiceSel === 0) {
      const enabling = !st.asr_enabled;
      if (enabling && needModel && !st.download_running) {
        voicePendingEnableRef.current = { asr_enabled: true };
        setVoiceAskDownload(true);
        setVoiceSel(2);
        return;
      }
      gw.call("voice.set_config", { asr_enabled: enabling }).then(setVoiceStatus);
    } else if (voiceSel === 1) {
      const enabling = !st.wakeup_enabled;
      if (enabling && needModel && !st.download_running) {
        voicePendingEnableRef.current = { wakeup_enabled: true };
        setVoiceAskDownload(true);
        setVoiceSel(2);
        return;
      }
      gw.call("voice.set_config", { wakeup_enabled: enabling }).then(setVoiceStatus);
    } else if (voiceSel === 2) {
      if (needModel && !st.download_running) {
        setVoiceAskDownload(false);
        setVoiceDownloadPct(0);
        gw.call("voice.download_model", {}).catch(() => {});
      }
    }
  };

  const suggestions = filterCommands(inputValue);
  if (selIndex >= suggestions.length && suggestions.length > 0) setSelIndex(0);

  useInput((input, key) => {
    if (key.ctrl && input === "c") {
      quit();
      return;
    }
    // Ctrl+M: suspend mouse capture → terminal-native drag select/copy
    if (key.ctrl && input === "m") {
      setMouseOff((v) => {
        const next = !v;
        setStatus({
          kind: "static",
          text: next ? "Mouse OFF (Ctrl+M to re-enable) — native select/copy active" : "Mouse ON",
          type: "info",
        });
        return next;
      });
      return;
    }
    // While a screen is open, the screen owns all other keys.
    if (screen) return;
    // ── 危险命令确认框拥有最高优先级 ──
    if (confirmQueue.length > 0) {
      const cur = confirmQueue[0];
      if (key.leftArrow) { setConfirmSel(0); return; }
      if (key.rightArrow) { setConfirmSel(1); return; }
      if (key.return) { answerConfirm(cur, confirmSel === 1); return; }
      if (key.escape) { answerConfirm(cur, false); return; }
      return; // 其余按键吞掉
    }
    // ── AskUser 提问面板（同级模态）──
    if (questionQueue.length > 0) {
      const cur = questionQueue[0];
      if (isBatchQuestion(cur)) {
        // 向导问卷：一页一题。←→ 切题；↑↓ 选项；单选 Enter/空格=选中并下一题；
        // 多选 空格=勾选（停留）、Enter=下一题；末题 Enter=提交全部
        const pageQ = cur.questions[questionPage];
        const optCount = pageQ ? pageQ.options.length : 0;
        const isLast = questionPage === cur.questions.length - 1;

        const advance = () => {
          if (isLast) { submitBatchAnswers(cur); return; }
          setQuestionPage((p) => Math.min(cur.questions.length - 1, p + 1));
          setQuestionSel(0);
        };

        if (key.leftArrow) {
          if (questionPage > 0) { setQuestionPage((p) => p - 1); setQuestionSel(0); }
          return;
        }
        if (key.rightArrow) { advance(); return; }
        if (key.upArrow) { setQuestionSel((v) => Math.max(0, v - 1)); return; }
        if (key.downArrow) { setQuestionSel((v) => Math.min(optCount - 1, v + 1)); return; }
        if (key.return || (input === " " && !key.ctrl && !key.meta)) {
          const label = pageQ?.options[questionSel]?.label ?? "";
          if (!label) return;
          if (pageQ.multi_select) {
            if (key.return) { advance(); return; } // Enter=下一题（空格继续走勾选）
            setQuestionAnswers((prev) => {
              const arr = Array.isArray(prev[questionPage]) ? [...(prev[questionPage] as string[])] : [];
              const i = arr.indexOf(label);
              if (i >= 0) arr.splice(i, 1); else arr.push(label);
              return { ...prev, [questionPage]: arr };
            });
            return;
          }
          // 单选：选中并前进
          setQuestionAnswers((prev) => ({ ...prev, [questionPage]: label }));
          advance();
          return;
        }
        if (key.escape) {
          // 取消全部：所有题作答为 null 提交，模型按默认方案继续
          const answers = cur.questions.map((q) => ({ question: q.question, answer: null }));
          answerQuestion(cur, JSON.stringify({ answers }));
          return;
        }
        return; // 其余按键吞掉
      }
      if (questionInputMode) {
        // 输入模式：字符/Enter 由 TextInput 处理，Esc 返回选项
        if (key.escape) { setQuestionInputMode(false); return; }
        return;
      }
      if (key.upArrow) { setQuestionSel((v) => Math.max(0, v - 1)); return; }
      if (key.downArrow) { setQuestionSel((v) => Math.min(customIndex(cur), v + 1)); return; }
      if (key.return) {
        if (questionSel === customIndex(cur)) { setQuestionInputMode(true); return; }
        answerQuestion(cur, cur.questions[0]?.options[questionSel]?.label ?? "");
        return;
      }
      if (key.escape) { answerQuestion(cur, "（用户取消了本次选择，请按最合理的默认方案继续）"); return; }
      return; // 其余按键吞掉
    }
    // ── 模型设置面板 ──
    if (modelOpen) {
      const histLen = modelStatus?.history?.length ?? 0;
      if (key.upArrow) { setModelSel((v) => Math.max(0, v - 1)); return; }
      if (key.downArrow) { setModelSel((v) => Math.min(2 + histLen, v + 1)); return; }
      if (key.return && modelSel >= 3) {
        const entry = modelStatus?.history?.[modelSel - 3];
        if (entry) switchToRecentModel(entry);
        return;
      }
      if (key.escape) {
        setModelOpen(false);
        setSettingsOpen(true);
        return;
      }
      return; // 字符/Enter 由选中字段的 TextInput 处理
    }
    // ── 设置菜单 ──

    if (settingsOpen) {
      if (key.upArrow) { setSettingsSel((v) => Math.max(0, v - 1)); return; }
      if (key.downArrow) { setSettingsSel((v) => Math.min(3, v + 1)); return; }
      if (key.return) {
        if (settingsSel === 0) openModelPanel();
        else if (settingsSel === 1) openVoicePanel();
        else if (settingsSel === 2) toggleBrowserLlm();
        else { setSettingsOpen(false); setScreen("channels"); }
        return;
      }
      if (key.escape) { setSettingsOpen(false); return; }
      return;
    }
    // ── 语音设置面板拥有次高优先级 ──
    if (voiceOpen) {
      if (key.upArrow) { setVoiceSel((v) => Math.max(0, v - 1)); return; }
      if (key.downArrow) { setVoiceSel((v) => Math.min(2, v + 1)); return; }
      if (key.return) { applyVoiceSel(); return; }
      if (key.escape) {
        voicePendingEnableRef.current = null;
        setVoiceAskDownload(false);
        setVoiceOpen(false);
        return;
      }
      return; // 其余按键吞掉
    }
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
    if (liveOutputToolId !== null) {
      // Live output panel open: Esc closes it
      if (key.escape) setLiveOutputToolId(null);
      return;
    }
    if (detailToolId !== null) {
      // Tool detail panel open: arrows scroll, Esc closes
      const detailMsg = viewMessagesRef.current.find((m) => m.id === detailToolId);
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
        // Switch the whole chat viewport to the selected agent's context
        const target = agentSel === 0 ? "main" : subagents[agentSel - 1]?.agentId ?? "main";
        viewScrollsRef.current.set(viewAgentId, scroll);
        setViewAgentId(target);
        setScroll(viewScrollsRef.current.get(target) ?? { pinned: false, top: 0 });
        setSelRange(null);
        setHoverId(null);
        setDetailToolId(null);
        setBrowseMode("none");
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
        setAgentSel(1); // first agent row (Main is row 0)
        setBrowseMode("agents");
        return;
      }
      if (key.upArrow && subagents.length > 0) {
        setAgentSel(subagents.length); // last row
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
      // Viewing a subagent's context: Esc returns to the main view first
      if (viewAgentId !== "main") {
        viewScrollsRef.current.set(viewAgentId, scroll);
        setViewAgentId("main");
        setScroll(viewScrollsRef.current.get("main") ?? { pinned: false, top: 0 });
        setSelRange(null);
        setHoverId(null);
        setDetailToolId(null);
        return;
      }
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
      case "/upgrade":
        gw?.call("agent.upgrade", { session_id: SESSION_ID })
          .then((r) => {
            if (r?.queued) add({ kind: "system", text: "Upgrade flow started — gathering info, will ask for confirmation" });
          })
          .catch((e) => add({ kind: "error", text: `Upgrade failed: ${e}` }));
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
      case "/tools": {
        setScreen("tools");
        setToolsError(null);
        setToolsLoading(true);
        gw?.call("tools.list", {})
          .then((r) => setToolList(Array.isArray(r?.tools) ? r.tools : []))
          .catch((e) => setToolsError(String(e)))
          .finally(() => setToolsLoading(false));
        return true;
      }
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
    setVoiceIndicator(null);

    if (message.startsWith("/")) {
      runCommand(message);
      return;
    }

    detectAndCache(message);
    setMessages((prev) => [...prev, { id: nextId++, ts: nowHHMMSS(), kind: "user", text: message }]);
    setScroll({ pinned: false, top: 0 }); // 发送新消息后自动回到底部
    historyRef.current.push(`[${nowHHMMSS()}] You: ${message}`);
    // New turn starts: clear finished agents from the bar (except the one being viewed)
    setSubagents((prev) =>
      prev.filter((a) => a.status === "running" || a.agentId === viewAgentIdRef.current),
    );

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
  // Settings 按钮：右下角最后一行，右对齐 "☰ Settings" 的命中区
  settingsHitRef.current = { row: termRows, x0: width - 12 };

  // Viewport content: main chat, or the selected subagent's own ReAct flow —
  // both rendered through the SAME ChatMessage pipeline (no duplicate rendering).
  const viewedAgent =
    viewAgentId === "main" ? undefined : subagents.find((a) => a.agentId === viewAgentId);
  const viewMessages: ChatMessage[] = viewedAgent ? subagentToMessages(viewedAgent) : messages;
  viewMessagesRef.current = viewMessages;

  const detailMsg = detailToolId !== null ? viewMessages.find((x) => x.id === detailToolId) : undefined;
  const detailData: ToolDetailData | null =
    detailMsg && detailMsg.kind === "tool"
      ? {
          tool: detailMsg.tool,
          argsBrief: detailMsg.args,
          argsFull: detailMsg.argsFull ?? detailMsg.args,
          result: detailMsg.result,
          isError: detailMsg.isError,
          ts: detailMsg.ts,
        }
      : null;

  const liveRows = screen === null ? liveTools.length : 0;
  const subBarRows =
    screen === null && (subagents.length > 0 || browseMode === "agents")
      ? 1 + subagents.length + (browseMode === "agents" ? 1 : 0) // Main row + agent rows (+ hint)
      : 0;
  const suggRows = screen === null && suggestions.length > 0 ? suggestions.length + 2 : 0;
  const pinnedRows = screen === null && scroll.pinned ? 1 : 0;
  const panelRows = screen === null && detailData ? TOOL_PANEL_ROWS : 0;
  const bgRows = screen === null ? bgTasks.length : 0;
  const taskRows = screen === null && taskList.length > 0 ? taskList.length + 1 : 0; // +1 标题行
  const livePanelRows = screen === null && liveOutputToolId ? LIVE_PANEL_ROWS : 0;
  const confirmRows = screen === null && confirmQueue.length > 0 ? CONFIRM_PANEL_ROWS : 0;
  const questionRows = screen === null && questionQueue.length > 0 ? QUESTION_PANEL_ROWS : 0;
  const voiceRows = screen === null && voiceOpen ? VOICE_PANEL_ROWS : 0;
  const modelRows = screen === null && modelOpen ? modelPanelRows(modelStatus?.history?.length ?? 0) : 0;
  const settingsRows = screen === null && settingsOpen ? 7 : 0; // 设置菜单：标题 + 4 选项 + 空行 + footer
  // 输入框纵向展开：内容宽 = 总宽 - 边框2 - paddingX2 - 提示符"❯ "2；行数随折行增长
  const inputContentW = Math.max(1, width - 6);
  const inputLines = wrapAnsi(inputValue || " ", inputContentW, { hard: true, trim: false }).split("\n").length;
  const inputBarRows = inputLines + 2; // +2 上下边框
  const bottomRows = liveRows + subBarRows + suggRows + panelRows + pinnedRows + bgRows + taskRows + livePanelRows + confirmRows + questionRows + voiceRows + modelRows + settingsRows + 1 + inputBarRows + 1; // StatusBar(1) + InputBar(动态) + Settings 行(1)
  const topOffset = screen === null && voiceIndicator ? 1 : 0;
  const viewH = Math.max(3, termRows - bottomRows - topOffset);

  const allLines: Array<{ id: number; kind: string; text: string }> = [];
  for (const m of viewMessages) {
    // Subagent messages are rebuilt live — bypass the id-based line cache
    const texts = viewedAgent
      ? buildMessageLines(m).flatMap((l) =>
          wrapAnsi(l || " ", width, { hard: true, trim: false }).split("\n"),
        )
      : wrappedMessageLines(m, width);
    for (const text of texts) {
      allLines.push({ id: m.id, kind: m.kind, text });
    }
  }
  const maxTop = Math.max(0, allLines.length - viewH);
  const effTop = scroll.pinned ? Math.min(scroll.top, maxTop) : maxTop;
  const visibleLines = allLines.slice(effTop, effTop + viewH);

  // Detail panel interior starts after: viewport + pinned hint + liveTools + suggestions + StatusBar + border-top
  const panelLines = detailData ? toolPanelLines(detailData, detailScroll) : [];
  const panelStartRow = detailData
    ? topOffset + viewH + pinnedRows + liveRows + suggRows + 1 + 2
    : -1;
  const bgTaskStartRow = screen === null && bgTasks.length > 0
    ? topOffset + viewH + pinnedRows + liveRows + livePanelRows + suggRows + 1 + panelRows + 1
    : -1;
  layoutRef.current = {
    top: effTop,
    viewH,
    topOffset,
    maxTop,
    lines: allLines,
    bottomOffset: allLines.length,
    panelStartRow,
    panelLines,
    liveToolsStartRow: liveRows > 0 ? topOffset + viewH + pinnedRows + 1 : -1,
    liveToolsCount: liveRows,
    bgTaskStartRow,
    bgTaskCount: bgRows,
  };

  rowToIndexRef.current = (y: number): number | null => {
    const L = layoutRef.current;
    if (y >= 1 + L.topOffset && y <= L.topOffset + L.viewH) {
      return L.bottomOffset > 0 ? Math.min(L.top + y - 1 - L.topOffset, L.bottomOffset - 1) : null;
    }
    if (L.panelStartRow > 0 && y >= L.panelStartRow && y < L.panelStartRow + L.panelLines.length) {
      return L.bottomOffset + (y - L.panelStartRow);
    }
    return null;
  };

  const scrollChatBy = (delta: number) => {
    const cur = scrollRef.current;
    const base = cur.pinned ? cur.top : layoutRef.current.maxTop;
    const next = Math.max(0, Math.min(layoutRef.current.maxTop, base + delta));
    setScroll({ pinned: next < layoutRef.current.maxTop, top: next });
  };

  clickHandlerRef.current = (x, y) => {
    const layout = layoutRef.current;
    // 运行中工具区：点击切换该工具的实时输出面板
    if (
      layout.liveToolsStartRow > 0 &&
      y >= layout.liveToolsStartRow &&
      y < layout.liveToolsStartRow + layout.liveToolsCount
    ) {
      const t = liveToolsRef.current[y - layout.liveToolsStartRow];
      if (t) {
        setDetailToolId(null); // 与详情面板互斥
        setLiveOutputToolId((cur) => (cur === t.callId ? null : t.callId));
      }
      return;
    }

    // 后台任务区：点击行末 [x] 取消任务
    if (
      layout.bgTaskStartRow > 0 &&
      y >= layout.bgTaskStartRow &&
      y < layout.bgTaskStartRow + layout.bgTaskCount
    ) {
      if (x >= width - KILL_BUTTON_WIDTH) {
        const idx = y - layout.bgTaskStartRow;
        const task = bgTasks[idx];
        if (task) {
          debugLog("click", `BgTaskBar kill @y=${y} x=${x} → taskId=${task.taskId}`);
          cancelBgTask(task.taskId);
        }
      }
      return;
    }

    // 右下角 Settings 按钮 → 切换设置菜单
    if (y === settingsHitRef.current.row && x >= settingsHitRef.current.x0) {
      if (voiceOpen || modelOpen || settingsOpen) {
        setVoiceOpen(false);
        setModelOpen(false);
        setSettingsOpen(false);
      } else {
        setSettingsOpen(true);
        setSettingsSel(0);
      }
      return;
    }

    if (y < 1 + layout.topOffset || y > layout.topOffset + layout.viewH) {
      debugLog("click", `@y=${y} outside viewport (viewH=${layout.viewH}) — ignored`);
      return;
    }
    const line = layout.lines[layout.top + (y - 1 - layout.topOffset)];
    debugLog("click", `@y=${y} → line[${layout.top + (y - 1 - layout.topOffset)}] kind=${line?.kind ?? "none"} id=${line?.id ?? "-"}`);
    if (line && line.kind === "tool") {
      setLiveOutputToolId(null); // 与实时输出面板互斥
      setDetailScroll(0);
      setDetailToolId((cur) => (cur === line.id ? null : line.id));
    }
  };

  // Mouse reporting: active only on the main chat view (screens need native selection)
  useEffect(() => {
    setMouseEnabled(
      screen === null && !mouseOff,
      // press: begin a potential drag-selection (anchor at this line)
      (_x, y) => {
        const idx = rowToIndexRef.current(y);
        selAnchorRef.current = idx;
        setSelRange(idx !== null ? { from: idx, to: idx } : null);
      },
      // release: collapsed range = click (tool toggle); extended = copy selection
      (_x, y) => {
        const L = layoutRef.current;
        const range = selRangeRef.current;
        const anchor = selAnchorRef.current;
        selAnchorRef.current = null;
        if (range && anchor !== null && range.from !== range.to) {
          const [from, to] = [Math.min(range.from, range.to), Math.max(range.from, range.to)];
          const lines: string[] = [];
          for (let i = from; i <= to; i++) {
            const text =
              i < L.bottomOffset ? L.lines[i]?.text ?? "" : L.panelLines[i - L.bottomOffset] ?? "";
            lines.push(stripAnsi(text));
          }
          const copied = lines.join("\n").replace(/\n{3,}/g, "\n\n").trim();
          clipboardy.write(copied).then(
            () => setStatus({ kind: "static", text: `Copied ${to - from + 1} lines ✓`, type: "success" }),
            () => setStatus({ kind: "static", text: "Copy failed (clipboard unavailable)", type: "warning" }),
          );
          debugLog("select", `copied lines ${from}-${to}`);
          return;
        }
        setSelRange(null);
        clickHandlerRef.current(_x, y);
      },
      (dir, _x, y) => {
        const layout = layoutRef.current;
        if (y > layout.topOffset + layout.viewH) {
          // Over bottom UI: wheel scrolls the open detail panel
          if (detailIdRef.current !== null) {
            setDetailScroll((s) => Math.max(0, s + (dir === "up" ? -3 : 3)));
          }
          return;
        }
        scrollChatBy(dir === "up" ? -3 : 3);
      },
      (_x, y, dragging) => {
        const layout = layoutRef.current;
        const sh = y === settingsHitRef.current.row && _x >= settingsHitRef.current.x0;
        setSettingsHover(sh);
        if (dragging && selAnchorRef.current !== null) {
          // Extend selection; clamp past the edges so fast drags still work
          const total = layout.bottomOffset + layout.panelLines.length;
          let idx = rowToIndexRef.current(y);
          if (idx === null) idx = y < 1 + layout.topOffset ? 0 : total - 1;
          idx = Math.max(0, Math.min(total - 1, idx));
          const cur = selRangeRef.current;
          if (!cur || cur.to !== idx) {
            setSelRange({ from: selAnchorRef.current, to: idx });
          }
          return;
        }
        let id: number | null = null;
        if (y >= 1 + layout.topOffset && y <= layout.topOffset + layout.viewH) {
          const line = layout.lines[layout.top + (y - 1 - layout.topOffset)];
          if (line && line.kind === "tool") id = line.id;
        }
        if (hoverRef.current !== id) {
          debugLog("hover", `@y=${y} → toolId=${id ?? "-"}`);
          hoverRef.current = id;
          setHoverId(id);
        }
      },
    );
  }, [screen, mouseOff]);
  useEffect(() => () => setMouseEnabled(false), []);

  return (
    <Box flexDirection="column">
      {screen === null && voiceIndicator && (
        <Box justifyContent="flex-end" paddingX={1} height={1} overflow="hidden">
          <Text color="magenta" bold wrap="truncate">{voiceIndicator}</Text>
        </Box>
      )}
      {screen === null && (
        <Box flexDirection="column" height={viewH} overflow="hidden">
          {visibleLines.map((l, i) => {
            const idx = effTop + i;
            const selected =
              selRange !== null &&
              selRange.from !== selRange.to &&
              idx >= Math.min(selRange.from, selRange.to) &&
              idx <= Math.max(selRange.from, selRange.to);
            const hovered = l.id === hoverId && l.kind === "tool";
            return (
              <Text key={idx} wrap="truncate">
                {selected ? highlightLine(l.text) : hovered ? hoverLine(l.text) : l.text}
              </Text>
            );
          })}
        </Box>
      )}
      {screen === null && scroll.pinned && (
        <Text dimColor>↑ scrolled — wheel/PgDn to bottom to follow</Text>
      )}
      {screen === null && liveTools.length > 0 && (
        <Box flexDirection="column">
          {liveTools.map((t) => (
            <Text key={t.callId} color="magenta" wrap="truncate">
              <Spinner type="dots" /> {t.tool}<Text dimColor>({t.args})</Text>
              {liveOutputToolId === t.callId
                ? <Text color="cyan">  ▼ 实时输出</Text>
                : t.tool === "run_cmd" && <Text dimColor>  (点击看实时输出)</Text>}
            </Text>
          ))}
        </Box>
      )}
      {screen === null && liveOutputToolId !== null && (() => {
        const tool = liveTools.find((x) => x.callId === liveOutputToolId);
        const contentRows = LIVE_PANEL_ROWS - 4;
        const lines = liveOutput.slice(-contentRows);
        return (
          <Box
            flexDirection="column"
            height={LIVE_PANEL_ROWS}
            borderStyle="round"
            borderColor="cyan"
            backgroundColor="#1c1e2b"
            paddingX={1}
          >
            <Text backgroundColor="#1c1e2b">
              <Text color="cyan">⚡ 实时输出</Text>
              <Text dimColor> — {tool ? `${tool.tool}(${tool.args})` : "运行中"}</Text>
            </Text>
            <Box flexDirection="column" height={contentRows}>
              {lines.length === 0
                ? <Text dimColor backgroundColor="#1c1e2b">（等待输出…）</Text>
                : lines.map((o, i) => (
                    <Text key={i} wrap="truncate" backgroundColor="#1c1e2b"
                      color={o.stream === "stderr" ? "red" : "gray"}>
                      {o.line || " "}
                    </Text>
                  ))}
            </Box>
            <Text dimColor backgroundColor="#1c1e2b">自动跟随最新输出 | Esc 收起</Text>
          </Box>
        );
      })()}
      {screen === null && <CommandSuggestions suggestions={suggestions} selectedIndex={selIndex} />}
      {screen === null && <StatusBar status={status} stats={viewAgentId === "main" ? stats : subagentStats[viewAgentId] ?? null} model={currentModel} />}
      {screen === null && detailData !== null && (() => {
        const panelFrom = selRange ? Math.min(selRange.from, selRange.to) - allLines.length : -1;
        const panelTo = selRange ? Math.max(selRange.from, selRange.to) - allLines.length : -1;
        const overlap = selRange !== null && panelTo >= 0 && panelFrom <= panelLines.length - 1;
        return (
          <ToolDetailPanel
            data={detailData}
            scroll={detailScroll}
            selFrom={overlap ? Math.max(0, panelFrom) : -1}
            selTo={overlap ? Math.min(panelLines.length - 1, panelTo) : -1}
          />
        );
      })()}
      {screen === null && <BgTaskBar tasks={bgTasks} onCancel={cancelBgTask} />}
      {screen === null && <CheckpointBar status={checkpoint} />}
      {screen === null && <TaskBar tasks={taskList} />}
      {screen === null && confirmQueue.length > 0 && (
        <ConfirmDialog req={confirmQueue[0]} selIndex={confirmSel} />
      )}
      {screen === null && questionQueue.length > 0 && (
        <QuestionDialog
          req={questionQueue[0]}
          selIndex={questionSel}
          inputMode={questionInputMode}
          draft={questionDraft}
          onDraftChange={setQuestionDraft}
          answers={questionAnswers}
          page={questionPage}
          onDraftSubmit={(v) => {
            const cur = questionQueue[0];
            if (cur && v.trim()) answerQuestion(cur, v.trim());
          }}
        />
      )}
      {screen === null && settingsOpen && (
        <Box
          flexDirection="column"
          height={7}
          borderStyle="round"
          borderColor="cyan"
          backgroundColor="#1c1e2b"
          paddingX={1}
        >
          <Text backgroundColor="#1c1e2b">
            <Text bold color="cyan">⚙ 设置</Text>
          </Text>
          <Text backgroundColor="#1c1e2b">
            <Text color={settingsSel === 0 ? "cyan" : "white"} bold={settingsSel === 0}>{settingsSel === 0 ? "› " : "  "}🧠 模型设置</Text>
          </Text>
          <Text backgroundColor="#1c1e2b">
            <Text color={settingsSel === 1 ? "cyan" : "white"} bold={settingsSel === 1}>{settingsSel === 1 ? "› " : "  "}🎤 语音识别</Text>
          </Text>
          <Text backgroundColor="#1c1e2b">
            <Text color={settingsSel === 2 ? "cyan" : "white"} bold={settingsSel === 2}>{settingsSel === 2 ? "› " : "  "}🌐 浏览器 LLM 抽取</Text>
            <Text dimColor>{browserLlmBusy ? "  切换中…" : browserLlm?.enabled ? "  [已开启]" : "  [已关闭]"}</Text>
          </Text>
          <Text backgroundColor="#1c1e2b">
            <Text color={settingsSel === 3 ? "cyan" : "white"} bold={settingsSel === 3}>{settingsSel === 3 ? "› " : "  "}📡 通道管理</Text>
            <Text dimColor>  sessions[].channels</Text>
          </Text>
          <Text backgroundColor="#1c1e2b"> </Text>
          <Text dimColor backgroundColor="#1c1e2b">↑↓ 选择 · Enter 进入/切换 · Esc 关闭</Text>
        </Box>
      )}
      {screen === null && modelOpen && (
        <ModelSettingsPanel
          status={modelStatus}
          selIndex={modelSel}
          draft={modelDraft}
          onDraftChange={(field, value) => setModelDraft((d) => ({ ...d, [field]: value }))}
          onSave={saveModelConfig}
        />
      )}
      {screen === null && voiceOpen && (
        <VoiceSettingsPanel
          status={voiceStatus}
          selIndex={voiceSel}
          downloadPercent={voiceDownloadPct}
          askDownload={voiceAskDownload}
        />
      )}
      {screen === null && (
        <InputBar
          value={inputValue}
          onChange={setInputValue}
          onSubmit={onSubmit}
          focus={browseMode === "none" && !voiceOpen && !modelOpen && !settingsOpen}
          disabled={!ready}
        />
      )}
      {screen === null && (subagents.length > 0 || browseMode === "agents") && (
        <SubagentBar
          agents={subagents}
          browsing={browseMode === "agents"}
          selIndex={agentSel}
          viewingId={viewAgentId}
        />
      )}
      {screen === null && (
        <Box justifyContent="flex-end" paddingX={1}>
          <Text
            color={settingsHover ? "cyan" : "gray"}
            bold={settingsHover}
            backgroundColor={settingsHover ? "#1c1e2b" : undefined}
          >
            {settingsHover ? "› Settings" : "☰ Settings"}
          </Text>
        </Box>
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
      <ChannelsEditorScreen
        active={screen === "channels"}
        onClose={() => setScreen(null)}
        onSave={saveConfig}
        onResult={(msg) => addRef.current({ kind: "system", text: msg })}
        onHealth={() => (gatewayRef.current
          ? gatewayRef.current.call("channels.health", {})
          : Promise.resolve([])) as Promise<Array<{
          session: string; type: string; channel_id: string; name: string;
          ok: boolean; state: string; detail: string;
        }>>}
      />
      <ToolsScreen
        active={screen === "tools"}
        tools={toolList}
        loading={toolsLoading}
        error={toolsError}
        onClose={() => setScreen(null)}
      />
    </Box>
  );
}
