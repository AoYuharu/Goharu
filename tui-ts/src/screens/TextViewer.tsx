import React, { useState } from "react";
import { Box, Text, useInput } from "ink";

/** Full-screen scrollable text viewer. Esc/q closes. */
export function TextViewerScreen({
  active,
  title,
  content,
  onClose,
}: {
  active: boolean;
  title: string;
  content: string;
  onClose: () => void;
}) {
  const [offset, setOffset] = useState(0);
  const height = Math.max(5, (process.stdout.rows || 24) - 6);
  const lines = content.split("\n");
  const maxOffset = Math.max(0, lines.length - height);

  useInput((input, key) => {
    if (key.escape || input === "q") {
      onClose();
      return;
    }
    if (key.upArrow) setOffset((o) => Math.max(0, o - 1));
    if (key.downArrow) setOffset((o) => Math.min(maxOffset, o + 1));
    if (key.pageUp) setOffset((o) => Math.max(0, o - height));
    if (key.pageDown) setOffset((o) => Math.min(maxOffset, o + height));
  }, { isActive: active });

  if (!active) return null;

  return (
    <Box flexDirection="column" borderStyle="round" borderColor="cyan">
      <Text bold color="cyan"> {title} </Text>
      <Box flexDirection="column" height={height}>
        {lines.slice(offset, offset + height).map((line, i) => (
          <Text key={offset + i} wrap="truncate">{line || " "}</Text>
        ))}
      </Box>
      <Text dimColor>
        ↑↓/PgUp/PgDn Scroll | {offset + 1}-{Math.min(offset + height, lines.length)}/{lines.length} | Esc Back
      </Text>
    </Box>
  );
}
