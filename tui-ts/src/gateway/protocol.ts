/**
 * Gateway JSON-RPC protocol contract (TypeScript side).
 *
 * Single source of truth for humans: docs/tui-protocol.md
 * Python side: TUI/gateway_entry.py (must stay in sync).
 *
 * Transport: NDJSON over stdin/stdout of the gateway subprocess.
 *   Request:  {"jsonrpc":"2.0","method":<MethodName>,"params":{...},"id":"N"}
 *   Response: {"jsonrpc":"2.0","result":{...},"id":"N"} | {"jsonrpc":"2.0","error":{...},"id":"N"}
 *   Event:    {"jsonrpc":"2.0","method":"event","params":{"type":<EventType>,"payload":{...}}}
 */

// ── RPC methods (client → gateway) ──────────────────────────────────

export interface SendMessageParams { message: string; session_id: string }
export interface SessionParams { session_id: string }
export interface UpdateConfigParams { key: string; value: unknown }

export interface QueuedResult { queued: boolean; command?: string }
export interface InterruptResult { interrupted: boolean }
export interface UpdateConfigResult { success: boolean; key: string; old_value: unknown; new_value: unknown }
export interface CancelBackgroundTaskParams { task_id: number }
export interface CancelBackgroundTaskResult { cancelled: boolean; task_id: number }
export interface ConfirmResponseResult { ok: boolean; approved: boolean }
export interface UserQuestionOption { label: string; description?: string }
export interface UserQuestionResponseResult { ok: boolean; request_id: string }

export interface HistoryMessage {
  role: string;
  kind: "text" | "thinking" | "tool";
  text?: string;
  tool?: string;
  args?: string;
  args_full?: string;
  result?: string | null;
  is_error?: boolean;
  timestamp: string;
}
export interface GetHistoryResult { messages: HistoryMessage[] }

export interface VoiceStatus {
  installed: boolean;
  sdk_available?: boolean;
  mic_available: boolean;
  mic_consent: boolean;
  asr_enabled: boolean;
  wakeup_enabled: boolean;
  models: { kws: boolean; vad: boolean; tokens: boolean; sensevoice: boolean };
  models_ready: boolean;
  need_download?: boolean;
  download_running: boolean;
  download_error: string | null;
  models_root: string;
}

export interface ModelHistoryEntry { model: string; base_url: string; api_key_env?: string }
export interface ModelStatus {
  model: string;
  base_url: string;
  provider: string;
  api_key_env: string;
  api_key_set: boolean;
  api_key_masked: string;
  history: ModelHistoryEntry[];
}
export interface ModelSetConfigParams { model?: string; base_url?: string; api_key?: string; api_key_env?: string }
export interface BrowserLlmStatus {
  enabled: boolean;
  model?: string | null;
  base_url?: string | null;
}
export interface BrowserLlmSetResult extends BrowserLlmStatus {
  success: boolean;
  message: string;
}

export interface ModelSetConfigResult {
  success: boolean;
  changed: string[];
  /** success=false 时：失败分类与一句可读原因；切换已回滚，原模型继续生效。
   *  kind 与 Gateway 侧 Agent.providers.errors 的归类一致 */
  error_kind?:
    | "missing_key" | "auth" | "quota" | "not_found" | "bad_request"
    | "rate_limit" | "timeout" | "network" | "server" | "unknown";
  message?: string;
  fallback_model?: string;
}

export interface ToolInfo {
  name: string;
  description: string;
  source: "builtin" | "mcp";
  server?: string | null;
  schema?: Record<string, unknown>;
}
export interface ListToolsResult { count: number; tools: ToolInfo[] }

export interface ChannelHealthRow {
  session: string;
  type: string;
  channel_id: string;
  name: string;
  ok: boolean;
  state: string;
  detail: string;
  params?: Record<string, unknown>;
  bot_name?: string;      // qqbot：/users/@me 的机器人名
  app_id?: string;        // qqbot：当前 AppID（非密）
  has_secret?: boolean;   // qqbot：是否已配置 AppSecret（密文不出网关）
}

export interface ChannelActionResult { ok: boolean; note?: string; error?: string }

export interface RpcMethods {
  "agent.send_message": { params: SendMessageParams; result: QueuedResult };
  "agent.clear_session": { params: SessionParams; result: QueuedResult };
  "agent.compact": { params: SessionParams; result: QueuedResult };
  "agent.upgrade": { params: SessionParams; result: QueuedResult };
  "agent.get_history": { params: Record<string, never>; result: GetHistoryResult };
  "tools.list": { params: Record<string, never>; result: ListToolsResult };
  "agent.update_config": { params: UpdateConfigParams; result: UpdateConfigResult };
  "agent.interrupt": { params: Record<string, never>; result: InterruptResult };
  "agent.confirm_response": { params: { confirm_id: string; approved: boolean }; result: ConfirmResponseResult };
  "agent.user_question_response": { params: { request_id: string; answer: string }; result: UserQuestionResponseResult };
  "agent.cancel_background_task": { params: CancelBackgroundTaskParams; result: CancelBackgroundTaskResult };
  "voice.get_status": { params: Record<string, never>; result: VoiceStatus };
  "voice.set_config": { params: { mic_consent?: boolean; asr_enabled?: boolean; wakeup_enabled?: boolean }; result: VoiceStatus };
  "voice.download_model": { params: Record<string, never>; result: { started: boolean; message: string } };
  "browser_llm.get_status": { params: Record<string, never>; result: BrowserLlmStatus };
  "browser_llm.set_config": { params: { enabled: boolean }; result: BrowserLlmSetResult };
  "model.get_status": { params: Record<string, never>; result: ModelStatus };
  "model.set_config": { params: ModelSetConfigParams; result: ModelSetConfigResult };
  "channels.health": { params: Record<string, never>; result: ChannelHealthRow[] };
  "channels.reconnect": { params: { channel_id: string }; result: ChannelActionResult };
  "channels.set_password": { params: { channel_id: string; password: string }; result: ChannelActionResult };
  "channels.set_qq_credentials": { params: { channel_id: string; app_id: string; app_secret: string }; result: ChannelActionResult };
}

export type MethodName = keyof RpcMethods;

// ── Events (gateway → client) ───────────────────────────────────────

export interface ToolCallSummary { tool_name?: string; arguments?: unknown }

export interface SubagentTokenStats {
  current_tokens?: number;
  cache_read_tokens?: number;
  cache_write_tokens?: number;
  uncached_input_tokens?: number;
  prompt_cache_ratio?: number;
}

export interface SubagentUpdatePayload {
  agent_id: string;
  agent_type: string;
  task: string;
  kind: "started" | "activity" | "finished" | "stats";
  level: "info" | "warning" | "error" | "debug";
  message: string;
  status?: "success" | "partial" | "error";
  category?: "iteration" | "thinking" | "tool_call" | "tool_result" | "final" | "error" | "info";
  content?: string;
  tool?: string;
  is_error?: boolean;
  /** kind="stats" 时携带：该子代理最近一次 LLM 请求的 Token/Cache 统计 */
  token_stats?: SubagentTokenStats;
}

export interface BgTaskUpdatePayload {
  task_id: number;
  tool: string;
  args?: string;
  status: "running" | "success" | "error";
  started_at?: number;
  timed_out?: boolean;
  error?: string;
}

export interface CheckpointSummaryPayload {
  seq: number;
  checkpoint_id?: string;
  kind?: "turn" | "rollback";
  outcome?: string;
  ended_at?: string;
  files: number;
  l1_ops: number;
  git_caught?: number;
  degraded?: boolean;
  warnings?: string[];
}

export interface CheckpointRollbackResult {
  ok: boolean;
  rollback_seq?: number;
  undone?: number;
  applied?: number;
  failed?: Array<Record<string, unknown>>;
  conflicts?: Array<{ path: string; reason: string }>;
}

export type GatewayEvent =
  | { type: "gateway.ready"; payload: Record<string, never> }
  | { type: "gateway.init_error"; payload: { message: string; detail?: string } }
  | { type: "agent.ready"; payload: { tools?: unknown[] } }
  | { type: "agent.thinking"; payload: Record<string, never> }
  | { type: "agent.thinking_content"; payload: { step: number; thinking: string } }
  | { type: "agent.text_content"; payload: { step: number; text: string } }
  | { type: "agent.step"; payload: { step: number } }
  | { type: "agent.llm_requesting"; payload: Record<string, never> }
  | { type: "agent.retry"; payload: { attempt: number; max_retries: number; backoff: number; phase: "backoff" | "requesting"; step: number } }
  | { type: "agent.l1_extracting"; payload: Record<string, never> }
  | { type: "agent.answer"; payload: { answer: string } }
  | { type: "agent.streaming"; payload: { chunk: string } }
  | { type: "agent.interrupted"; payload: { message: string } }
  | { type: "agent.error"; payload: {
      message: string;
      id?: string;
      fingerprint?: string;
      scope?: string;
      severity?: string;
      blocking?: "fatal" | "all_turns" | "channel" | "feature" | "request";
      cause?: "config" | "runtime" | "external";
      wake_status?: string;
    } }
  | { type: "agent.environment_status"; payload: { component: string; ok: boolean; message: string } }
  | { type: "tool.call"; payload: { tool: string; arguments: unknown; call_id: string; step: number } }
  | { type: "tool.result"; payload: { tool: string; call_id: string; result: string } }
  | { type: "token.stats"; payload: { current_tokens?: number; prompt_cache_ratio?: number; cache_read_tokens?: number; cache_write_tokens?: number; uncached_input_tokens?: number } & Record<string, unknown> }
  | { type: "user.batch"; payload: { count: number; combined: string } }
  | { type: "user.message"; payload: { text: string; channel: string } }
  | { type: "user.merge"; payload: { count: number; merged: string } }
  | { type: "message.complete"; payload: { answer: string } }
  | { type: "context.micro_compact"; payload: { removed_results: number } }
  | { type: "tasks.updated"; payload: { tasks: Array<{ id: number; subject: string; status: string }> } }
  | { type: "task.background.started"; payload: { tool_calls: ToolCallSummary[] } }
  | { type: "task.background.completed"; payload: { count: number; task_ids: number[] } }
  | { type: "task.background.status"; payload: { pending_results: number } & Record<string, unknown> }
  | { type: "command.complete"; payload: { command: string; success: boolean; error?: string } }
  | { type: "subagent.update"; payload: SubagentUpdatePayload }
  | { type: "tool.output"; payload: { line: string; stream: string; cmd: string } }
  | { type: "tool.confirm_request"; payload: { confirm_id: string; cmd: string; description: string } }
  | { type: "tool.user_question_request"; payload: { request_id: string; questions: Array<{ question: string; options: UserQuestionOption[]; multi_select?: boolean }> } }
  | { type: "task.background.update"; payload: BgTaskUpdatePayload }
  | { type: "checkpoint.created"; payload: CheckpointSummaryPayload }
  | { type: "checkpoint.rollback.started"; payload: { target: number; undone: number } }
  | { type: "checkpoint.rollback.completed"; payload: CheckpointRollbackResult }
  | { type: "checkpoint.degraded"; payload: { seq: number; reason: string | null } }
  | { type: "voice.download_progress"; payload: { downloaded_bytes: number; total_bytes: number; percent: number } }
  | { type: "voice.download_complete"; payload: { ok: boolean; error?: string; status?: VoiceStatus } }
  | { type: "voice.transcript"; payload: { text: string } }
  | { type: "voice.state"; payload: { state: string } };

export type GatewayEventType = GatewayEvent["type"];
export type GatewayEventPayload<T extends GatewayEventType> =
  Extract<GatewayEvent, { type: T }>["payload"];

// ── Wire shapes ─────────────────────────────────────────────────────

export interface RpcRequest<M extends MethodName = MethodName> {
  jsonrpc: "2.0";
  method: M;
  params: RpcMethods[M]["params"];
  id: string;
}

export interface RpcResponse {
  jsonrpc: "2.0";
  id: string;
  result?: unknown;
  error?: { code: number; message: string };
}

export interface EventMessage {
  jsonrpc: "2.0";
  method: "event";
  params: { type: string; payload: unknown };
}
