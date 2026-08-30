import React, { useState } from "react";
import { Box, Text, useInput } from "ink";
import type { SubagentInfo } from "../components/SubagentBar.js";
import { statusIcon } from "../components/SubagentBar.js";
import type { ToolHistoryEntry } from "../components/ToolDetailPanel.js";

/** Full-screen live view of one subagent's context (task + activity stream). */
export function SubagentScreen({
  active,
  agent, // null → Main agent view (shows tool history)
  mainTools,
  onClose,
}: {
  active: boolean;
  agent: SubagentInfo | null;
  mainTools: ToolHistoryEntry[];
  onClose: () => void;
}) {
  const [offset, setOffset] = useState(0);
  const [follow, setFollow] = useState(true);
  const height = Math.max(5, (process.stdout.rows || 24) - 9);

  const isMain = agent === null;
  const lines: Array<{ text: string; level: string }> = isMain
    ? mainTools.map((t) => ({
        text: `[${t.ts}] ⚙ ${t.tool}(${t.args}) → ${t.isError ? "✗" : "✓"} ${t.summary}`,
        level: t.isError ? "error" : "info",
      }))
    : agent!.activities.map((a) => ({ text: a.message, level: a.level }));

  const maxOffset = Math.max(0, lines.length - height);
  const effectiveOffset = follow ? maxOffset : Math.min(offset, maxOffset);

  useInput((input, key) => {
    if (key.escape || input === "q") {
      onClose();
      return;
    }
    if (key.upArrow) {
      setFollow(false);
      setOffset(Math.min(effectiveOffset, maxOffset) - 1 < 0 ? 0 : Math.min(effectiveOffset, maxOffset) - 1);
    }
    if (key.downArrow) {
      const next = Math.min(effectiveOffset + 1, maxOffset);
      setOffset(next);
      if (next >= maxOffset) setFollow(true);
    }
    if (key.pageUp) {
      setFollow(false);
      setOffset((o) => Math.max(0, Math.min(o, maxOffset) - height));
    }
    if (key.pageDown) {
      const next = Math.min(Math.min(offset, maxOffset) + height, maxOffset);
      setOffset(next);
      if (next >= maxOffset) setFollow(true);
    }
  }, { isActive: active });

  if (!active) return null;

  const title = isMain
    ? "Main Agent — Tool History"
    : `${agent!.agentType.toUpperCase()} [${agent!.agentId}]`;

  return (
    <Box flexDirection="column" borderStyle="round" borderColor="cyan">
      <Box>
        <Text bold color="cyan"> {title} </Text>
        {!isMain && (
          <Text>
            {statusIcon(agent!.status)}
            <Text dimColor>
              {" "}{agent!.status} · {Math.round((Date.now() - agent!.startedAtMs) / 1000)}s
            </Text>
          </Text>
        )}
      </Box>
      {!isMain && (
        <Box flexDirection="column" marginBottom={1}>
          <Text bold>Task:</Text>
          {agent!.task.split("\n").slice(0, 3).map((line, i) => (
            <Text key={i} wrap="truncate">  {line}</Text>
          ))}
        </Box>
      )}
      <Box flexDirection="column" height={height}>
        {lines.length === 0 && <Text dimColor>(no activity yet)</Text>}
        {lines.slice(effectiveOffset, effectiveOffset + height).map((line, i) => (
          <Text
            key={effectiveOffset + i}
            wrap="truncate"
            color={line.level === "error" ? "red" : line.level === "warning" ? "yellow" : undefined}
            dimColor={line.level === "debug"}
          >
            {line.text || " "}
          </Text>
        ))}
      </Box>
      <Text dimColor>
        ↑↓/PgUp/PgDn Scroll{follow ? " (following)" : ""} | {lines.length} events | Esc Back
      </Text>
    </Box>
  );
}
