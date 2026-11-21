import React from "react";
import { Box, Text } from "ink";

export interface TaskItem {
  id: number;
  subject: string;
  status: string; // pending | in_progress | completed
}

const STATUS_ICON: Record<string, { icon: string; color: string }> = {
  pending: { icon: "○", color: "gray" },
  in_progress: { icon: "◐", color: "yellow" },
  completed: { icon: "●", color: "green" },
};

function truncate(text: string, max: number): string {
  const compact = text.replace(/\s+/g, " ").trim();
  return compact.length <= max ? compact : compact.slice(0, max - 1) + "…";
}

/** 任务清单条：复杂任务的 Plan-Execute-Verify 进度实时显示。 */
export function TaskBar({ tasks }: { tasks: TaskItem[] }) {
  if (tasks.length === 0) return null;
  const done = tasks.filter((t) => t.status === "completed").length;
  return (
    <Box flexDirection="column">
      <Text dimColor>Tasks {done}/{tasks.length}</Text>
      {tasks.map((t) => {
        const s = STATUS_ICON[t.status] ?? STATUS_ICON.pending;
        return (
          <Text key={t.id} wrap="truncate">
            <Text color={s.color}>{s.icon} </Text>
            <Text color={s.color} dimColor={t.status === "pending"}>
              #{t.id} {truncate(t.subject, 70)}
            </Text>
          </Text>
        );
      })}
    </Box>
  );
}
