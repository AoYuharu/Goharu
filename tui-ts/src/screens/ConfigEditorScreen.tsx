import React, { useEffect, useState } from "react";
import { Box, Text, useInput } from "ink";
import { readFileSync, existsSync } from "node:fs";
import path from "node:path";
import YAML from "yaml";
import { PROJECT_ROOT } from "../gateway/GatewayClient.js";
import { CONFIG_CATALOG, type ConfigEntry } from "../configCatalog.js";
import { TextInput } from "../components/TextInput.js";

function readConfig(): Record<string, unknown> {
  const p = path.join(PROJECT_ROOT, "config.yaml");
  if (!existsSync(p)) return {};
  try {
    // uniqueKeys:false 与 Python yaml 的容忍行为对齐（重复键后者生效）
    const parsed = YAML.parse(readFileSync(p, "utf8"), { uniqueKeys: false });
    return (parsed as Record<string, unknown>) ?? {};
  } catch {
    return {};
  }
}

function getByKey(obj: Record<string, unknown>, key: string): unknown {
  let cur: unknown = obj;
  for (const k of key.split(".")) {
    if (cur === null || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[k];
  }
  return cur;
}

function coerce(raw: string, entry: ConfigEntry): unknown {
  switch (entry.type) {
    case "int": {
      const n = parseInt(raw, 10);
      return Number.isNaN(n) ? entry.default : n;
    }
    case "float": {
      const n = parseFloat(raw);
      return Number.isNaN(n) ? entry.default : n;
    }
    case "bool":
      return raw === "true" || raw === "1";
    default:
      return raw;
  }
}

/**
 * Config editor: ↑↓ navigate catalog, Enter edits (bool toggles instantly),
 * value saves immediately via agent.update_config, Esc cancels edit / closes.
 */
export function ConfigEditorScreen({
  active,
  onClose,
  onSave,
  onResult,
}: {
  active: boolean;
  onClose: () => void;
  onSave: (key: string, value: unknown) => Promise<string>;
  onResult: (msg: string) => void;
}) {
  const [configData, setConfigData] = useState<Record<string, unknown>>({});
  const [index, setIndex] = useState(0);
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState("");

  useEffect(() => {
    if (active) {
      setConfigData(readConfig());
      setEditing(false);
    }
  }, [active]);

  const entry = CONFIG_CATALOG[index];
  const currentValue = entry ? (getByKey(configData, entry.key) ?? entry.default) : undefined;

  useInput((input, key) => {
    if (editing) {
      // TextInput owns typing; Esc cancels the edit here.
      if (key.escape) setEditing(false);
      return;
    }
    if (key.escape) {
      onClose();
      return;
    }
    if (key.upArrow) {
      setIndex((i) => Math.max(0, i - 1));
      return;
    }
    if (key.downArrow) {
      setIndex((i) => Math.min(CONFIG_CATALOG.length - 1, i + 1));
      return;
    }
    if (key.return && entry) {
      if (entry.type === "bool") {
        const next = !Boolean(currentValue);
        void onSave(entry.key, next).then(onResult).catch((e) => onResult(`保存失败: ${e}`));
        setConfigData((prev) => {
          const copy = { ...prev };
          // shallow path write for immediate UI feedback
          const keys = entry.key.split(".");
          let cur: Record<string, unknown> = copy;
          for (const k of keys.slice(0, -1)) {
            cur[k] = { ...(cur[k] as Record<string, unknown> ?? {}) };
            cur = cur[k] as Record<string, unknown>;
          }
          cur[keys[keys.length - 1]] = next;
          return copy;
        });
        return;
      }
      setEditValue(String(currentValue ?? ""));
      setEditing(true);
    }
  }, { isActive: active });

  if (!active || !entry) return null;

  const submitEdit = (raw: string) => {
    setEditing(false);
    const value = coerce(raw.trim(), entry);
    void onSave(entry.key, value).then(onResult).catch((e) => onResult(`保存失败: ${e}`));
    setConfigData(readConfig());
  };

  // Group headers for display
  const rows: Array<{ type: "group"; label: string } | { type: "item"; entry: ConfigEntry; i: number }> = [];
  let lastGroup = "";
  CONFIG_CATALOG.forEach((e, i) => {
    if (e.group !== lastGroup) {
      rows.push({ type: "group", label: e.group });
      lastGroup = e.group;
    }
    rows.push({ type: "item", entry: e, i });
  });

  return (
    <Box flexDirection="column" borderStyle="round" borderColor="green">
      <Text bold color="green"> ⚙️  Configuration Editor </Text>
      <Box>
        <Box flexDirection="column" width="45%">
          {rows.map((row, ri) => {
            if (row.type === "group") {
              return <Text key={ri} bold underline color="yellow">{row.label}</Text>;
            }
            const e = row.entry;
            const val = getByKey(configData, e.key) ?? e.default;
            return (
              <Text
                key={ri}
                wrap="truncate"
                backgroundColor={row.i === index ? "green" : undefined}
                color={row.i === index ? "white" : undefined}
              >
                {"  "}{e.name}: {String(val)}
              </Text>
            );
          })}
        </Box>
        <Box flexDirection="column" width="55%" borderLeft borderColor="gray" paddingLeft={1}>
          <Text bold>{entry.name}</Text>
          <Text dimColor wrap="wrap">{entry.description}</Text>
          <Text> </Text>
          <Text>key: <Text color="cyan">{entry.key}</Text></Text>
          <Text>type: {entry.type}{entry.range ? `  range: ${entry.range[0]} - ${entry.range[1]}` : ""}</Text>
          <Text>current: <Text bold color="green">{String(currentValue)}</Text></Text>
          <Text dimColor wrap="wrap">tips: {entry.tips}</Text>
          {editing && (
            <Box>
              <Text color="yellow">新值: </Text>
              <TextInput value={editValue} onChange={setEditValue} onSubmit={submitEdit} placeholder="" />
            </Box>
          )}
        </Box>
      </Box>
      <Text dimColor>↑↓ Navigate | Enter Edit(bool=Toggle) | Enter 确认保存 | Esc {editing ? "Cancel" : "Back"}</Text>
    </Box>
  );
}
