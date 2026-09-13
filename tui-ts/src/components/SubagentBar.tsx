import React from "react";
import { Box, Text } from "ink";
import chalk from "chalk";
import { highlightLine } from "../format.js";

export interface SubagentActivity {
  level: string;
  message: string;
  ts?: string;
  category?: "iteration" | "thinking" | "tool_call" | "tool_result" | "final" | "error" | "info";
  content?: string;
  tool?: string;
  is_error?: boolean;
}

export interface SubagentInfo {
  agentId: string;
  agentType: string;
  task: string;
  status: "running" | "success" | "partial" | "error";
  startedAtMs: number;
  activities: SubagentActivity[];
}

const TYPE_COLORS: Record<string, string> = {
  explore: "cyan",
  plan: "yellow",
  verify: "magenta",
};

function truncate(text: string, max: number): string {
  const compact = text.replace(/\s+/g, " ").trim();
  return compact.length <= max ? compact : compact.slice(0, max - 1) + "…";
}

function statusGlyph(status: SubagentInfo["status"]): string {
  switch (status) {
    case "running": return chalk.cyan("●");
    case "success": return chalk.green("✓");
    case "partial": return chalk.yellow("⚠");
    case "error": return chalk.red("✗");
  }
}

function typeLabel(agentType: string): string {
  const t = agentType.toLowerCase();
  return t.charAt(0).toUpperCase() + t.slice(1);
}

function typeColorChalk(agentType: string): (s: string) => string {
  const name = TYPE_COLORS[agentType.toLowerCase()] ?? "white";
  return name === "cyan" ? chalk.cyan : name === "yellow" ? chalk.yellow : name === "magenta" ? chalk.magenta : chalk.white;
}

/**
 * Always-visible agent list rendered BELOW the input bar — one row per agent.
 * ↓/↑ from an empty input moves the cursor naturally into this list.
 */
export function SubagentBar({
  agents,
  browsing,
  selIndex,
  viewingId = "main",
}: {
  agents: SubagentInfo[];
  browsing: boolean;
  selIndex: number;
  viewingId?: string;
}) {
  if (agents.length === 0 && !browsing) return null;

  const rows: Array<{ id: string; label: string }> = [
    { id: "main", label: chalk.cyan("● Main") + chalk.dim(" — primary agent") },
    ...agents.map((a) => {
      const elapsed =
        a.status === "running"
          ? chalk.dim(` (${Math.round((Date.now() - a.startedAtMs) / 1000)}s)`)
          : "";
      return {
        id: a.agentId,
        label:
          statusGlyph(a.status) + " " +
          typeColorChalk(a.agentType)(typeLabel(a.agentType)) +
          ": " + chalk.dim(truncate(a.task, 56)) + elapsed,
      };
    }),
  ];

  return (
    <Box flexDirection="column">
      {rows.map((row, i) => {
        const cursor = browsing && i === selIndex;
        const line =
          `${cursor ? "❯ " : "  "}${row.label}` +
          (viewingId === row.id ? chalk.cyan("  ◀ viewing") : "");
        return (
          <Text key={row.id} wrap="truncate">
            {cursor ? highlightLine(line) : line}
          </Text>
        );
      })}
      {browsing && <Text dimColor>↑↓ Select | Enter View | Esc Back</Text>}
    </Box>
  );
}
