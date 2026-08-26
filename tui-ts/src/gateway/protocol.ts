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

export interface HistoryMessage {
  role: string;
  content: string;
  type: string; // "text" | "tool_call" | "tool_result"
  timestamp: string;
}
export interface GetHistoryResult { messages: HistoryMessage[] }

export interface RpcMethods {
  "agent.send_message": { params: SendMessageParams; result: QueuedResult };
  "agent.clear_session": { params: SessionParams; result: QueuedResult };
  "agent.compact": { params: SessionParams; result: QueuedResult };
  "agent.get_history": { params: Record<string, never>; result: GetHistoryResult };
  "agent.update_config": { params: UpdateConfigParams; result: UpdateConfigResult };
  "agent.interrupt": { params: Record<string, never>; result: InterruptResult };
}

export type MethodName = keyof RpcMethods;

// ── Events (gateway → client) ───────────────────────────────────────

export interface ToolCallSummary { tool_name?: string; arguments?: unknown }

export type GatewayEvent =
  | { type: "gateway.ready"; payload: Record<string, never> }
  | { type: "gateway.init_error"; payload: { message: string } }
  | { type: "agent.ready"; payload: { tools?: unknown[] } }
  | { type: "agent.thinking"; payload: Record<string, never> }
  | { type: "agent.thinking_content"; payload: { step: number; thinking: string } }
  | { type: "agent.text_content"; payload: { step: number; text: string } }
  | { type: "agent.step"; payload: { step: number } }
  | { type: "agent.llm_requesting"; payload: Record<string, never> }
  | { type: "agent.l1_extracting"; payload: Record<string, never> }
  | { type: "agent.answer"; payload: { answer: string } }
  | { type: "agent.streaming"; payload: { chunk: string } }
  | { type: "agent.interrupted"; payload: { message: string } }
  | { type: "agent.error"; payload: { message: string } }
  | { type: "tool.call"; payload: { tool: string; arguments: unknown; call_id: string; step: number } }
  | { type: "tool.result"; payload: { tool: string; call_id: string; result: string } }
  | { type: "token.stats"; payload: { current_tokens?: number; prompt_cache_ratio?: number } & Record<string, unknown> }
  | { type: "user.batch"; payload: { count: number; combined: string } }
  | { type: "user.merge"; payload: { count: number; merged: string } }
  | { type: "message.complete"; payload: { answer: string } }
  | { type: "context.micro_compact"; payload: { removed_results: number } }
  | { type: "task.background.started"; payload: { tool_calls: ToolCallSummary[] } }
  | { type: "task.background.completed"; payload: { count: number; task_ids: number[] } }
  | { type: "task.background.status"; payload: { pending_results: number } & Record<string, unknown> }
  | { type: "command.complete"; payload: { command: string; success: boolean; error?: string } };

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
