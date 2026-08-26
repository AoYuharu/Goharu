import React from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";

export type StatusType = "success" | "error" | "warning" | "thinking" | "working" | "info";

export type StatusState =
  | { kind: "spinner"; label: string }
  | { kind: "static"; text: string; type: StatusType };

const LABELS: Record<StatusType, string> = {
  success: "[OK]",
  error: "[ERR]",
  warning: "[WARN]",
  thinking: "...",
  working: "[BUSY]",
  info: "[*]",
};

const COLORS: Record<StatusType, string> = {
  success: "green",
  error: "red",
  warning: "yellow",
  thinking: "magenta",
  working: "cyan",
  info: "blue",
};

export interface TokenStats {
  tokens: string;
  cache: string;
}

export function StatusBar({ status, stats }: { status: StatusState; stats: TokenStats | null }) {
  return (
    <Box>
      {status.kind === "spinner" ? (
        <Text color="cyan">
          <Spinner type="dots" /> {status.label}
        </Text>
      ) : (
        <Text bold color={COLORS[status.type]}>
          {LABELS[status.type]} {status.text}
        </Text>
      )}
      <Box flexGrow={1} />
      {stats && (
        <Text>
          <Text dimColor>Current Tokens: </Text>
          <Text bold color="cyan">{stats.tokens}</Text>
          <Text dimColor> | Prompt Cache Ratio: </Text>
          <Text bold color="green">{stats.cache}</Text>
        </Text>
      )}
    </Box>
  );
}
