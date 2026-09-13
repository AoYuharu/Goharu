/**
 * Convert a subagent's activity stream into standard ChatMessage objects so
 * the subagent context view reuses the main agent's entire rendering pipeline
 * (thinking blocks, collapsed tool lines, click-to-expand detail panel,
 * markdown answers) — no duplicated rendering code.
 */
import type { ChatMessage } from "./components/MessageItem.js";
import type { SubagentInfo } from "./components/SubagentBar.js";
import { formatArgsBrief } from "./components/ToolDetailPanel.js";

const TYPE_COLOR_NAME: Record<string, string> = {
  explore: "cyan",
  plan: "yellow",
  verify: "magenta",
};

function briefArgs(content: string | undefined): string {
  if (!content) return "";
  try {
    return formatArgsBrief(JSON.parse(content));
  } catch {
    return content.replace(/\s+/g, " ").slice(0, 60);
  }
}

export function subagentToMessages(agent: SubagentInfo): ChatMessage[] {
  const msgs: ChatMessage[] = [];
  let nid = 0;
  const nextNeg = () => --nid; // negative ids: never collide with real messages

  const typeName =
    agent.agentType.charAt(0).toUpperCase() + agent.agentType.slice(1).toLowerCase();
  const elapsed = Math.round((Date.now() - agent.startedAtMs) / 1000);
  const statusColor =
    agent.status === "running" ? "cyan" : agent.status === "success" ? "green"
    : agent.status === "partial" ? "yellow" : "red";

  msgs.push({
    id: nextNeg(), ts: "", kind: "splash",
    text: `● ${typeName} [${agent.agentId}]  ${agent.status} · ${elapsed}s`,
    color: TYPE_COLOR_NAME[agent.agentType.toLowerCase()] ?? "cyan",
  });
  msgs.push({
    id: nextNeg(), ts: "", kind: "splash",
    text: `  Task: ${agent.task.replace(/\s+/g, " ").trim()}`,
    color: "gray",
  });
  msgs.push({ id: nextNeg(), ts: "", kind: "splash", text: `  Status: ${agent.status}`, color: statusColor });
  msgs.push({ id: nextNeg(), ts: "", kind: "splash", text: " ", color: "gray" });

  // Pair tool_call with its tool_result → one standard "tool" message
  let pendingTool: { callIdx: number; tool: string; args: string; argsFull: string; ts: string } | null = null;
  const flushPending = (result: string, isError: boolean) => {
    if (!pendingTool) return;
    msgs.push({
      id: nextNeg(), ts: pendingTool.ts, kind: "tool",
      tool: pendingTool.tool, args: pendingTool.args, argsFull: pendingTool.argsFull,
      result, isError,
    });
    pendingTool = null;
  };

  for (const act of agent.activities) {
    const ts = act.ts ?? "";
    switch (act.category) {
      case "iteration":
        flushPending("(no result — interrupted)", true);
        msgs.push({
          id: nextNeg(), ts, kind: "splash",
          text: `── Iteration ${act.content ?? ""} ${"─".repeat(30)}`, color: "gray",
        });
        break;
      case "thinking":
        msgs.push({
          id: nextNeg(), ts, kind: "thinking",
          text: act.content ?? act.message.replace(/\[[a-z]+_[a-f0-9]+\]/i, "").trim(), step: 0,
        });
        break;
      case "tool_call":
        flushPending("(no result — interrupted)", true);
        pendingTool = {
          callIdx: nid, tool: act.tool ?? "?",
          args: briefArgs(act.content), argsFull: act.content ?? "", ts,
        };
        break;
      case "tool_result":
        flushPending(act.content ?? "", act.is_error ?? false);
        break;
      case "final":
        flushPending("(no result — interrupted)", true);
        if ((act.content ?? "").trim()) {
          msgs.push({ id: nextNeg(), ts, kind: "assistant", text: act.content! });
        }
        break;
      case "error":
        msgs.push({ id: nextNeg(), ts, kind: "error", text: act.message });
        break;
      default:
        msgs.push({
          id: nextNeg(), ts, kind: "splash",
          text: "  " + act.message.replace(/\[[a-z]+_[a-f0-9]+\]/i, "").trim(),
          color: "gray",
        });
    }
  }
  flushPending("(executing…)", false);
  return msgs;
}
