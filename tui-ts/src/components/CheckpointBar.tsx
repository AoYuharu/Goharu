import React from "react";
import { Text } from "ink";

export interface CheckpointStatus {
  seq: number;
  files: number;
  l1Ops: number;
  gitCaught?: number;
  degraded?: boolean;
  kind?: string;
  rollingBackTo?: number | null;
}

/** turn 级 checkpoint 状态栏一行：最近 checkpoint 摘要 + 回退进行中指示。 */
export function CheckpointBar({ status }: { status: CheckpointStatus | null }) {
  if (!status) return null;
  if (status.rollingBackTo != null) {
    return (
      <Text wrap="truncate">
        <Text color="yellow">↩ 回退中 → cp#{status.rollingBackTo}…</Text>
      </Text>
    );
  }
  const parts = [`cp#${status.seq}`, `Δ${status.files} files`];
  if (status.l1Ops > 0) parts.push(`${status.l1Ops} mem`);
  if ((status.gitCaught ?? 0) > 0) parts.push(`git+${status.gitCaught}`);
  if (status.kind === "rollback") parts.unshift("↩");
  return (
    <Text wrap="truncate">
      <Text dimColor>📸 {parts.join(" · ")}</Text>
      {status.degraded && <Text color="yellow"> ⚠git降级</Text>}
    </Text>
  );
}
