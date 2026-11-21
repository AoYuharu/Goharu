import React, { useEffect, useMemo, useState } from "react";
import { Box, Text, useInput } from "ink";
import type { ToolInfo } from "../gateway/protocol.js";

const PANEL_BG = "#1c1e2b";
const MAX_VISIBLE_LINES = 18;

/** All registered tools (builtin + MCP), grouped by source. ↑↓ navigate, PgUp/PgDn, Esc close. */
export function ToolsScreen({
  active,
  tools,
  loading,
  error,
  onClose,
}: {
  active: boolean;
  tools: ToolInfo[];
  loading: boolean;
  error: string | null;
  onClose: () => void;
}) {
  const [index, setIndex] = useState(0);

  useEffect(() => {
    if (active) setIndex(0);
  }, [active]);

  const groups = useMemo(() => {
    const mcp = new Map<string, ToolInfo[]>();
    const builtin: ToolInfo[] = [];
    for (const t of tools) {
      if (t.source === "mcp" && t.server) {
        if (!mcp.has(t.server)) mcp.set(t.server, []);
        mcp.get(t.server)!.push(t);
      } else {
        builtin.push(t);
      }
    }
    const out: { label: string; tools: ToolInfo[] }[] = [];
    for (const [server, list] of [...mcp.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
      out.push({ label: `MCP · ${server}`, tools: list.sort((a, b) => a.name.localeCompare(b.name)) });
    }
    if (builtin.length) {
      out.push({ label: "内置工具", tools: builtin.sort((a, b) => a.name.localeCompare(b.name)) });
    }
    return out;
  }, [tools]);

  const flat = useMemo(() => groups.flatMap((g) => g.tools), [groups]);
  const selected = flat[Math.min(index, Math.max(0, flat.length - 1))];

  // 每行：分组标题 或 工具；附带该行对应的 flat 索引（标题行为 -1）
  const rows = useMemo(() => {
    const out: { label: string; toolIndex: number; isHeader: boolean }[] = [];
    let cursor = 0;
    for (const g of groups) {
      out.push({ label: `${g.label}（${g.tools.length}）`, toolIndex: -1, isHeader: true });
      for (const t of g.tools) {
        out.push({ label: t.name, toolIndex: cursor, isHeader: false });
        cursor += 1;
      }
    }
    return out;
  }, [groups]);

  const selectedRow = rows.findIndex((r) => r.toolIndex === index);
  const windowStart = Math.max(
    0,
    Math.min(
      Math.max(0, rows.length - MAX_VISIBLE_LINES),
      (selectedRow >= 0 ? selectedRow : 0) - Math.floor(MAX_VISIBLE_LINES / 2),
    ),
  );
  const visible = rows.slice(windowStart, windowStart + MAX_VISIBLE_LINES);

  useInput(
    (input, key) => {
      if (key.escape) {
        onClose();
        return;
      }
      if (key.upArrow) {
        setIndex((i) => Math.max(0, i - 1));
        return;
      }
      if (key.downArrow) {
        setIndex((i) => Math.min(flat.length - 1, i + 1));
        return;
      }
      if (key.pageUp) {
        setIndex((i) => Math.max(0, i - 8));
        return;
      }
      if (key.pageDown) {
        setIndex((i) => Math.min(flat.length - 1, i + 8));
      }
    },
    { isActive: active },
  );

  if (!active) return null;

  const mcpCount = tools.filter((t) => t.source === "mcp").length;
  const params = selected
    ? Object.keys(((selected.schema?.properties as Record<string, unknown>) ?? {}))
    : [];

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor="cyan"
      backgroundColor={PANEL_BG}
      paddingX={1}
    >
      <Text backgroundColor={PANEL_BG}>
        <Text bold color="cyan">🧰 工具清单</Text>
        <Text dimColor>  共 {tools.length} 个 · MCP {mcpCount} · 内置 {tools.length - mcpCount}</Text>
      </Text>

      {loading && <Text color="yellow" backgroundColor={PANEL_BG}>  正在读取工具列表…</Text>}
      {error && <Text color="red" backgroundColor={PANEL_BG}>  读取失败：{error}</Text>}

      {!loading && !error && visible.map((row, i) =>
        row.isHeader ? (
          <Text key={`h${windowStart + i}`} bold color="magenta" backgroundColor={PANEL_BG}>
            {"  "}{row.label}
          </Text>
        ) : (
          <Text
            key={row.label + row.toolIndex}
            backgroundColor={row.toolIndex === index ? "cyan" : PANEL_BG}
            color={row.toolIndex === index ? "black" : undefined}
          >
            {"  "}{row.toolIndex === index ? "▸ " : "  "}{row.label}
          </Text>
        ),
      )}

      {!loading && !error && selected && (
        <Box flexDirection="column" marginTop={1}>
          <Text backgroundColor={PANEL_BG}>
            <Text bold color="cyan">{selected.name}</Text>
            <Text dimColor>
              {selected.source === "mcp" ? `  [MCP · ${selected.server}]` : "  [内置]"}
            </Text>
          </Text>
          <Text backgroundColor={PANEL_BG} color="white">
            {"  "}{selected.description.trim().slice(0, 500) || "(无描述)"}
          </Text>
          {params.length > 0 && (
            <Text dimColor backgroundColor={PANEL_BG}>{"  参数：" + params.join(", ")}</Text>
          )}
        </Box>
      )}

      <Text dimColor backgroundColor={PANEL_BG}>↑↓ 选择 · PgUp/PgDn 翻页 · Esc 关闭</Text>
    </Box>
  );
}
