import React from "react";
import { Box, Text } from "ink";
import chalk from "chalk";
import type { VoiceStatus } from "../gateway/protocol.js";

const PANEL_BG = "#1c1e2b";
const ACCENT = "cyan";
const ON = "#2e7d32";
const OFF = "#555";

/** Fixed panel height (incl. border) — App relies on this for viewport math. */
export const VOICE_PANEL_ROWS = 8;

function toggle(on: boolean, active: boolean): string {
  const label = on ? " 开 " : " 关 ";
  if (active) return chalk.bgHex(on ? ON : "#6b6b6b").white.bold(label);
  return chalk.hex(on ? ON : OFF)(label);
}

function Row({
  label,
  right,
  active,
  hint,
}: {
  label: string;
  right: string;
  active: boolean;
  hint?: string;
}) {
  return (
    <Text backgroundColor={PANEL_BG}>
      <Text color={active ? ACCENT : "white"} bold={active}>{active ? "› " : "  "}{label}</Text>
      <Text color={active ? ACCENT : "gray"}>{"─".repeat(Math.max(2, 24 - [...label].length))}</Text>
      <Text>{right}</Text>
      {hint ? <Text dimColor>{hint}</Text> : null}
    </Text>
  );
}

export function VoiceSettingsPanel({
  status,
  selIndex,
  downloadPercent,
  askDownload,
}: {
  status: VoiceStatus | null;
  selIndex: number;
  downloadPercent: number | null;
  askDownload: boolean;
}) {
  const st = status;
  const modelRight = !st
    ? chalk.gray("…")
    : st.download_running
      ? chalk.yellow(`下载中 ${downloadPercent ?? 0}%`)
      : st.models.sensevoice
        ? chalk.green("已就绪")
        : askDownload
          ? chalk.red("缺失 · Enter 确认下载 229MB")
          : chalk.red("缺失");

  const footer = askDownload
    ? "是否下载语音识别模型（约 229MB）？Enter=下载  Esc=取消"
    : "↑↓ 选择 · Enter 切换 · Esc 关闭";

  return (
    <Box
      flexDirection="column"
      height={VOICE_PANEL_ROWS}
      borderStyle="round"
      borderColor={ACCENT}
      backgroundColor={PANEL_BG}
      paddingX={1}
    >
      <Text backgroundColor={PANEL_BG}>
        <Text bold color={ACCENT}>🎤 语音设置</Text>
        <Text dimColor>{st && !st.mic_available ? "  ⚠ 未检测到麦克风" : ""}</Text>
      </Text>
      <Row
        label="语音识别 (ASR)"
        right={toggle(st?.asr_enabled ?? false, selIndex === 0)}
        active={selIndex === 0}
        hint=" 转写语音为文字"
      />
      <Row
        label="语音唤醒 (KWS)"
        right={toggle(st?.wakeup_enabled ?? false, selIndex === 1)}
        active={selIndex === 1}
        hint=" 需同时开启识别"
      />
      <Row
        label="SenseVoice 模型"
        right={modelRight}
        active={selIndex === 2}
      />
      <Text backgroundColor={PANEL_BG}> </Text>
      <Text dimColor backgroundColor={PANEL_BG}>{footer}</Text>
    </Box>
  );
}
