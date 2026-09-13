import React from "react";
import { Box, Text } from "ink";
import chalk from "chalk";
import type { ModelHistoryEntry, ModelStatus } from "../gateway/protocol.js";
import { TextInput } from "./TextInput.js";

const PANEL_BG = "#1c1e2b";
const ACCENT = "cyan";

/** Fixed panel height (incl. border) — App relies on this for viewport math. */
export const MODEL_PANEL_ROWS = 8;

/** 历史列表总行数：表头 1 行 + 每条 1 行 */
export function modelPanelRows(historyCount: number): number {
  return MODEL_PANEL_ROWS + (historyCount > 0 ? 1 + historyCount : 0);
}

export interface ModelDraft {
  model: string;
  base_url: string;
  api_key: string;
}

function FieldRow({
  label,
  value,
  active,
  placeholder,
  onChange,
  onSubmit,
}: {
  label: string;
  value: string;
  active: boolean;
  placeholder: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
}) {
  const width = Math.max(2, 16 - [...label].length);
  return (
    <Text backgroundColor={PANEL_BG}>
      <Text color={active ? ACCENT : "white"} bold={active}>{active ? "› " : "  "}{label}</Text>
      <Text color={active ? ACCENT : "gray"}>{"─".repeat(width)}</Text>
      <TextInput
        value={value}
        onChange={onChange}
        onSubmit={onSubmit}
        focus={active}
        placeholder={placeholder}
      />
    </Text>
  );
}

export function ModelSettingsPanel({
  status,
  selIndex,
  draft,
  onDraftChange,
  onSave,
}: {
  status: ModelStatus | null;
  selIndex: number;
  draft: ModelDraft;
  onDraftChange: (field: keyof ModelDraft, value: string) => void;
  onSave: () => void;
}) {
  const apiKeyPlaceholder = status?.api_key_set
    ? `留空保持不变（当前 ${status.api_key_masked}）`
    : "输入 API Key";
  const history: ModelHistoryEntry[] = status?.history ?? [];

  return (
    <Box
      flexDirection="column"
      height={modelPanelRows(history.length)}
      borderStyle="round"
      borderColor={ACCENT}
      backgroundColor={PANEL_BG}
      paddingX={1}
    >
      <Text backgroundColor={PANEL_BG}>
        <Text bold color={ACCENT}>🧠 模型设置</Text>
        {status?.model ? <Text dimColor>  当前 {status.model}</Text> : null}
      </Text>
      <FieldRow
        label="模型名"
        value={draft.model}
        active={selIndex === 0}
        placeholder={status?.model || "模型名"}
        onChange={(v) => onDraftChange("model", v)}
        onSubmit={onSave}
      />
      <FieldRow
        label="API URL"
        value={draft.base_url}
        active={selIndex === 1}
        placeholder={status?.base_url || "https://..."}
        onChange={(v) => onDraftChange("base_url", v)}
        onSubmit={onSave}
      />
      <FieldRow
        label="API Key"
        value={draft.api_key}
        active={selIndex === 2}
        placeholder={apiKeyPlaceholder}
        onChange={(v) => onDraftChange("api_key", v)}
        onSubmit={onSave}
      />
      {history.length > 0 && (
        <>
          <Text backgroundColor={PANEL_BG}>
            <Text bold color={ACCENT}>↻ 最近使用</Text>
            <Text dimColor>  ↓ 进入后 Enter 一键切换</Text>
          </Text>
          {history.map((h, i) => {
            const active = selIndex === 3 + i;
            const isCurrent = h.model === status?.model;
            return (
              <Text key={`${h.model}@${h.base_url}`} backgroundColor={PANEL_BG}>
                <Text color={active ? ACCENT : "white"} bold={active}>
                  {active ? "› " : "  "}{i + 1}. {h.model}
                </Text>
                <Text color={active ? ACCENT : "gray"} dimColor={!active}>
                  {"  "}{h.base_url}{isCurrent ? "（当前）" : ""}
                </Text>
              </Text>
            );
          })}
        </>
      )}
      <Text backgroundColor={PANEL_BG}> </Text>
      <Text dimColor backgroundColor={PANEL_BG}>↑↓ 切换字段 · 直接输入编辑 · Enter 保存并热生效 · Esc 返回</Text>
    </Box>
  );
}
