import React from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";

export interface BgTask {
  taskId: number;
  tool: string;
  args: string;
  startedAtMs: number;
}

function truncate(text: string, max: number): string {
  const compact = text.replace(/\s+/g, " ").trim();
  return compact.length <= max ? compact : compact.slice(0, max - 1) + "…";
}

const KILL_BUTTON_WIDTH = 4; // " [x]"

/** One line per running background task (AgentDelegate excluded — it has its own bar). */
export function BgTaskBar({ tasks, onCancel }: { tasks: BgTask[]; onCancel?: (taskId: number) => void }) {
  if (tasks.length === 0) return null;
  return (
    <Box flexDirection="column">
      {tasks.map((t) => (
        <Text key={t.taskId}>
          <Text color="yellow">
            <Spinner type="dots" /> BG #{t.taskId} {t.tool}
          </Text>
          <Text dimColor>({truncate(t.args, 60)})</Text>
          <Text dimColor> {Math.round((Date.now() - t.startedAtMs) / 1000)}s</Text>
          {onCancel && <Text color="red"> [x]</Text>}
        </Text>
      ))}
    </Box>
  );
}

export { KILL_BUTTON_WIDTH };
