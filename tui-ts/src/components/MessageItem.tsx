import React from "react";
import { Box, Text } from "ink";
import chalk from "chalk";
import wrapAnsi from "wrap-ansi";
import { separator } from "../format.js";
import { renderMarkdown } from "../markdown.js";

export type ChatMessage = {
  id: number;
  ts: string;
} & (
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string }
  | { kind: "thinking"; text: string; step: number }
  | { kind: "note"; text: string; step: number }
  | { kind: "tool"; tool: string; args: string; argsFull?: string; result: string; isError: boolean }
  | { kind: "system"; text: string }
  | { kind: "error"; text: string }
  | { kind: "splash"; text: string; color?: string }
);

const lineCache = new Map<number, string[]>();

/** Styled logical lines — single source of truth for both render and row measurement. */
export function messageToLines(msg: ChatMessage): string[] {
  const cached = lineCache.get(msg.id);
  if (cached) return cached;
  const lines = buildLines(msg);
  if (lineCache.size > 2000) lineCache.delete(lineCache.keys().next().value!);
  lineCache.set(msg.id, lines);
  return lines;
}

function buildLines(msg: ChatMessage): string[] {
  const ts = chalk.dim(`[${msg.ts}] `);
  switch (msg.kind) {
    case "splash": {
      const styles: Record<string, (s: string) => string> = {
        cyan: chalk.cyan, blue: chalk.blue, magenta: chalk.magenta,
      };
      const style = (msg.color && styles[msg.color]) || ((s: string) => s);
      return [style(msg.text)];
    }
    case "user":
      return (ts + chalk.bold.cyan("You") + ": " + msg.text).split("\n");
    case "assistant": {
      const sep = chalk.bold.cyan(separator());
      const header = ts + chalk.bold.green("Assistant") + ":";
      return [sep, header, ...renderMarkdown(msg.text).split("\n"), sep];
    }
    case "thinking":
      return [
        ts + chalk.bold.cyan("💭 Thinking") + chalk.dim(` (Step ${msg.step})`),
        ...msg.text.split("\n").map((l) => chalk.italic.yellow("  " + l)),
        " ",
      ];
    case "note":
      return [
        ts + chalk.bold.green("Assistant") + chalk.dim(` (Step ${msg.step})`),
        ...msg.text.split("\n"),
        " ",
      ];
    case "tool": {
      const line =
        chalk.magenta("⚙ ") +
        chalk.bold.magenta(msg.tool) +
        (msg.args ? chalk.dim(`(${msg.args})`) : "") +
        (msg.isError ? chalk.red(" ✗") : "");
      return [line];
    }
    case "system":
      return msg.text
        .split("\n")
        .map((l) => ts + chalk.bold.yellow("System") + ": " + chalk.italic(l));
    case "error":
      return msg.text
        .split("\n")
        .map((l) => ts + chalk.bold.red("Error") + ": " + chalk.red(l));
  }
}

/** Exact rendered row count (every line is pre-wrapped + truncate-rendered). */
export function measureMessage(msg: ChatMessage, width: number): number {
  let rows = 0;
  for (const line of messageToLines(msg)) {
    rows += wrapAnsi(line || " ", width, { hard: true, trim: false }).split("\n").length;
  }
  return rows;
}

const wrapCache = new Map<string, string[]>();

/** Pre-wrapped display lines, cached per (message, width). */
export function wrappedMessageLines(msg: ChatMessage, width: number): string[] {
  const key = `${msg.id}:${width}`;
  const cached = wrapCache.get(key);
  if (cached) return cached;
  const lines = messageToLines(msg).flatMap((l) =>
    wrapAnsi(l || " ", width, { hard: true, trim: false }).split("\n"),
  );
  if (wrapCache.size > 4000) wrapCache.delete(wrapCache.keys().next().value!);
  wrapCache.set(key, lines);
  return lines;
}

export function MessageItem({ msg }: { msg: ChatMessage }) {
  const width = Math.max(20, process.stdout.columns || 80);
  const lines = messageToLines(msg).flatMap((l) =>
    wrapAnsi(l || " ", width, { hard: true, trim: false }).split("\n"),
  );
  return (
    <Box flexDirection="column">
      {lines.map((line, i) => (
        <Text key={i} wrap="truncate">
          {line}
        </Text>
      ))}
    </Box>
  );
}
