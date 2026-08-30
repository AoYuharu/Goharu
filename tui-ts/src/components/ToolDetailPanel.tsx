import React from "react";
import { Box, Text } from "ink";
import wrapAnsi from "wrap-ansi";

export interface ToolHistoryEntry {
  callId: string;
  tool: string;
  args: string;      // brief, values-only
  argsFull: string;  // pretty JSON
  result: string;
  isError: boolean;
  ts: string;
  summary: string;
}

export interface ToolDetailData {
  tool: string;
  argsBrief: string;
  argsFull: string;
  result: string;
  isError: boolean;
  ts: string;
}

/** Fixed panel height — App relies on this for mouse hit-map math. */
export const TOOL_PANEL_ROWS = 14;

export function makeSummary(result: string): { summary: string; isError: boolean } {
  const isError =
    /^(error|❌|✗)/i.test(result.trim()) ||
    result.includes("=== SUBAGENT FAILED ===") ||
    result.trimStart().startsWith('{"error"');
  const firstLine =
    result
      .split("\n")
      .map((l) => l.trim())
      .find((l) => l.length > 0) ?? "(empty)";
  const compact = firstLine.replace(/\s+/g, " ");
  const summary = compact.length <= 80 ? compact : compact.slice(0, 77) + "...";
  return { summary, isError };
}

/** Values-only args brief: `E:\Goharu\*.py, 100` (no key names). */
export function formatArgsBrief(args: unknown, max = 90): string {
  if (args == null) return "";
  let s: string;
  if (typeof args === "object" && !Array.isArray(args)) {
    s = Object.values(args as Record<string, unknown>)
      .map((v) => {
        const t = typeof v === "string" ? v : JSON.stringify(v) ?? String(v);
        return t.length > 24 ? t.slice(0, 21) + "…" : t;
      })
      .join(", ");
  } else {
    s = typeof args === "string" ? args : JSON.stringify(args) ?? String(args);
  }
  return s.length > max ? s.slice(0, max - 1) + "…" : s;
}

export function formatArgsFull(args: unknown, max = 2000): string {
  let s: string;
  try {
    s = JSON.stringify(args, null, 2) ?? String(args);
  } catch {
    s = String(args);
  }
  return s.length > max ? s.slice(0, max) + "\n…(truncated)" : s;
}

/** All content lines of the detail panel (header/hint excluded). */
export function toolDetailLines(data: ToolDetailData, width: number): string[] {
  const wrap = (s: string) =>
    s.split("\n").flatMap((l) => wrapAnsi(l || " ", width, { hard: true, trim: false }).split("\n"));
  return [
    "Args: " + (data.argsBrief || "(none)"),
    ...wrap(data.argsFull),
    "Result:",
    ...wrap(data.result || "(empty)"),
  ];
}

export function ToolDetailPanel({ data, scroll }: { data: ToolDetailData; scroll: number }) {
  const width = Math.max(20, (process.stdout.columns || 80) - 2);
  const contentRows = TOOL_PANEL_ROWS - 3; // header + hint + top rule
  const lines = toolDetailLines(data, width);
  const maxScroll = Math.max(0, lines.length - contentRows);
  const off = Math.min(scroll, maxScroll);

  return (
    <Box flexDirection="column" height={TOOL_PANEL_ROWS}>
      <Text>
        <Text color="magenta">⚙ </Text>
        <Text bold color="magenta">{data.tool}</Text>
        <Text dimColor>({data.argsBrief})</Text>
        <Text dimColor> [{data.ts}] </Text>
        <Text color={data.isError ? "red" : "green"}>{data.isError ? "✗ failed" : "✓ done"}</Text>
      </Text>
      <Text dimColor>{"─".repeat(Math.min(width, 60))}</Text>
      <Box flexDirection="column" height={contentRows}>
        {lines.slice(off, off + contentRows).map((line, i) => (
          <Text key={off + i} wrap="truncate">{line}</Text>
        ))}
      </Box>
      <Text dimColor>
        ↑↓ Scroll{maxScroll > 0 ? ` ${off + 1}-${Math.min(off + contentRows, lines.length)}/${lines.length}` : ""} | Click tool again or Esc to close
      </Text>
    </Box>
  );
}
