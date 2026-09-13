import React from "react";
import { Box, Text } from "ink";
import chalk from "chalk";
import wrapAnsi from "wrap-ansi";
import { highlightLine } from "../format.js";

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

/** Fixed panel height (incl. border) — App relies on this for viewport math. */
export const TOOL_PANEL_ROWS = 14;

const PANEL_BG = "#1c1e2b";

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

function wrapLine(line: string, width: number): string[] {
  return wrapAnsi(line || " ", width, { hard: true, trim: false }).split("\n");
}

/** One line per argument: `key = value` with colored key. Falls back to raw text. */
function formatArgLines(argsFull: string): string[] {
  try {
    const obj: unknown = JSON.parse(argsFull);
    if (obj && typeof obj === "object" && !Array.isArray(obj)) {
      const entries = Object.entries(obj as Record<string, unknown>);
      if (entries.length === 0) return [chalk.dim("  (no arguments)")];
      return entries.map(([k, v]) => {
        const val =
          typeof v === "string" ? v : JSON.stringify(v, null, 0) ?? String(v);
        return "  " + chalk.cyan(k) + chalk.dim(" = ") + chalk.white(val);
      });
    }
    return ["  " + chalk.cyan("value") + chalk.dim(" = ") + chalk.white(JSON.stringify(obj))];
  } catch {
    return argsFull.split("\n").map((l) => chalk.gray(l));
  }
}

/** JSON results get pretty-printed with cyan keys; plain text passes through. */
function formatResultLines(result: string): { lines: string[]; isJson: boolean } {
  try {
    const parsed: unknown = JSON.parse(result);
    const pretty = JSON.stringify(parsed, null, 2).split("\n");
    return {
      isJson: true,
      lines: pretty.map((l) =>
        l.replace(/^(\s*)"([^"]+)":/, (_m, ind: string, k: string) =>
          ind + chalk.cyan(`"${k}"`) + ":",
        ),
      ),
    };
  } catch {
    return { isJson: false, lines: result.split("\n").map((l) => chalk.gray(l)) };
  }
}

/** All scrollable content lines (Args + Result sections), width-aware. */
export function toolDetailLines(data: ToolDetailData): string[] {
  const width = Math.max(20, (process.stdout.columns || 80) - 4);
  const args = formatArgLines(data.argsFull).flatMap((l) => wrapLine(l, width));
  const { lines: resultLines, isJson } = formatResultLines(data.result || "(empty)");
  const resultLabel =
    (data.isError ? chalk.bold.red("✗ Result") : chalk.bold.green("✓ Result")) +
    chalk.dim(` (${resultLines.length} lines${isJson ? ", JSON" : ""})`);
  return [
    chalk.bold.cyan("Args"),
    ...args,
    " ",
    resultLabel,
    ...resultLines.flatMap((l) => wrapLine(l, width)),
  ];
}

export function ToolDetailPanel({
  data,
  scroll,
  selFrom = -1,
  selTo = -1,
}: {
  data: ToolDetailData;
  scroll: number;
  selFrom?: number; // highlighted interior-line range (inclusive), -1 = none
  selTo?: number;
}) {
  const lines = toolPanelLines(data, scroll);
  const accent = data.isError ? "red" : "magenta";

  return (
    <Box
      flexDirection="column"
      height={TOOL_PANEL_ROWS}
      borderStyle="round"
      borderColor={accent}
      backgroundColor={PANEL_BG}
      paddingX={1}
    >
      {lines.map((line, i) => (
        <Text key={i} wrap="truncate" backgroundColor={PANEL_BG}>
          {i >= selFrom && i <= selTo ? highlightLine(line) : line}
        </Text>
      ))}
    </Box>
  );
}

/** The panel's exact interior lines (header + content window + hint). */
export function toolPanelLines(data: ToolDetailData, scroll: number): string[] {
  const contentRows = TOOL_PANEL_ROWS - 4; // border×2 + header + hint
  const lines = toolDetailLines(data);
  const maxScroll = Math.max(0, lines.length - contentRows);
  const off = Math.min(scroll, maxScroll);
  const colorName = data.isError ? "red" : "magenta";

  const header =
    chalk[colorName]("⚙ ") +
    chalk.bold[colorName](data.tool) +
    chalk.dim(`(${data.argsBrief})`) +
    chalk.dim(`  [${data.ts}] `) +
    (data.isError ? chalk.red("✗ failed") : chalk.green("✓ done"));
  const hint = chalk.dim(
    `↑↓ Scroll${maxScroll > 0 ? ` ${off + 1}-${Math.min(off + contentRows, lines.length)}/${lines.length}` : ""} | Click tool again or Esc to close`,
  );

  const content = lines.slice(off, off + contentRows);
  while (content.length < contentRows) content.push(" ");
  return [header, ...content, hint];
}
