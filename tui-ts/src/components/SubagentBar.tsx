import React from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";

export interface SubagentActivity {
  level: string;
  message: string;
}

export interface SubagentInfo {
  agentId: string;
  agentType: string;
  task: string;
  status: "running" | "success" | "partial" | "error";
  startedAtMs: number;
  activities: SubagentActivity[];
}

function truncate(text: string, max: number): string {
  const compact = text.replace(/\s+/g, " ").trim();
  return compact.length <= max ? compact : compact.slice(0, max - 1) + "…";
}

export function statusIcon(status: SubagentInfo["status"]): React.ReactNode {
  switch (status) {
    case "running":
      return <Spinner type="dots" />;
    case "success":
      return <Text color="green">✓</Text>;
    case "partial":
      return <Text color="yellow">⚠</Text>;
    case "error":
      return <Text color="red">✗</Text>;
  }
}

function typeLabel(agentType: string): string {
  const t = agentType.toLowerCase();
  return t.charAt(0).toUpperCase() + t.slice(1);
}

export function SubagentBar({
  agents,
  selectMode,
  selIndex,
}: {
  agents: SubagentInfo[];
  selectMode: boolean;
  selIndex: number;
}) {
  if (agents.length === 0 && !selectMode) return null;

  if (!selectMode) {
    return (
      <Box>
        <Text dimColor>Agents: </Text>
        <Text color="cyan">● Main</Text>
        {agents.map((a) => (
          <Text key={a.agentId}>
            <Text dimColor> | </Text>
            {statusIcon(a.status)}
            <Text color="magenta"> {typeLabel(a.agentType)}</Text>
            <Text dimColor>: {truncate(a.task, 40)}</Text>
          </Text>
        ))}
        <Text dimColor>  (↓ select)</Text>
      </Box>
    );
  }

  // Selection mode: expanded list, Main on first row (index 0)
  const rows: Array<{ id: string; label: React.ReactNode }> = [
    {
      id: "main",
      label: (
        <Text>
          <Text color="cyan">● Main</Text>
          <Text dimColor> — primary agent (tool history)</Text>
        </Text>
      ),
    },
    ...agents.map((a) => ({
      id: a.agentId,
      label: (
        <Text>
          {statusIcon(a.status)}
          <Text color="magenta"> {typeLabel(a.agentType)}</Text>
          <Text dimColor> [{a.agentId}]</Text>
          <Text>: {truncate(a.task, 60)}</Text>
          {a.status === "running" && (
            <Text dimColor> ({Math.round((Date.now() - a.startedAtMs) / 1000)}s)</Text>
          )}
        </Text>
      ),
    })),
  ];

  return (
    <Box flexDirection="column">
      {rows.map((row, i) => (
        <Text key={row.id} inverse={i === selIndex}>
          {i === selIndex ? "❯ " : "  "}
          {row.label}
        </Text>
      ))}
      <Text dimColor>↑↓ Select | Enter View | Esc Back</Text>
    </Box>
  );
}
