import React from "react";
import { Box, Text } from "ink";
import chalk from "chalk";
import wrapAnsi from "wrap-ansi";

export interface ConfirmRequest {
  confirm_id: string;
  cmd: string;
  description: string;
}

const PANEL_BG = "#1c1e2b";
const DARK_RED = "#8b2e2e";

/** Fixed panel height (incl. border) — App relies on this for viewport math. */
export const CONFIRM_PANEL_ROWS = 9;

export function ConfirmDialog({
  req,
  selIndex, // 0 = No, 1 = Yes
}: {
  req: ConfirmRequest;
  selIndex: number;
}) {
  const width = Math.max(30, (process.stdout.columns || 80) - 4);
  const wrap = (s: string, indent = "") =>
    s.split("\n").flatMap((l) => wrapAnsi(l || " ", width, { hard: true, trim: false }).split("\n"))
      .map((x) => indent + x);

  const descLines = wrap(req.description).slice(0, 2);
  const cmdLines = wrap(req.cmd).slice(0, 2);

  const opt = (label: string, active: boolean, color: string) =>
    active ? chalk.bgHex(color).white.bold(` ${label} `) : chalk.gray(` ${label} `);

  return (
    <Box
      flexDirection="column"
      height={CONFIRM_PANEL_ROWS}
      borderStyle="round"
      borderColor={DARK_RED}
      backgroundColor={PANEL_BG}
      paddingX={1}
    >
      <Text backgroundColor={PANEL_BG}>
        <Text bold color={DARK_RED}>⚠ 高危操作确认</Text>
        <Text dimColor>  此命令可能造成不可逆的更改</Text>
      </Text>
      {descLines.map((l, i) => (
        <Text key={i} backgroundColor={PANEL_BG} color="white">{l}</Text>
      ))}
      <Text backgroundColor={PANEL_BG}>
        <Text color="yellow">命令: </Text>
        <Text color="red" bold>{cmdLines[0] ?? ""}</Text>
      </Text>
      {cmdLines.length > 1 && <Text backgroundColor={PANEL_BG} color="red" bold>{cmdLines[1]}</Text>}
      <Text backgroundColor={PANEL_BG}> </Text>
      <Text backgroundColor={PANEL_BG}>
        {opt("✗ 拒绝", selIndex === 0, DARK_RED)}
        <Text>   </Text>
        {opt("✓ 允许执行", selIndex === 1, "#2e7d32")}
      </Text>
      <Text dimColor backgroundColor={PANEL_BG}>← → 选择 | Enter 确认 | Esc 拒绝</Text>
    </Box>
  );
}
