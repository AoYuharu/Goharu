import React, { useEffect, useState } from "react";
import { Box, Text, useInput } from "ink";
import { getCachedPrompts, touchPrompt, removePrompt, type CacheEntry } from "../promptCache.js";

/** Prompt cache browser: ↑↓ navigate, Enter select, Delete remove, Esc back. */
export function PromptCacheScreen({
  active,
  onSelect,
  onClose,
}: {
  active: boolean;
  onSelect: (text: string) => void;
  onClose: () => void;
}) {
  const [entries, setEntries] = useState<CacheEntry[]>([]);
  const [index, setIndex] = useState(0);

  // The component stays mounted while inactive — reload entries every time
  // the screen opens, otherwise the list is frozen at app-start state.
  useEffect(() => {
    if (active) {
      setEntries(getCachedPrompts());
      setIndex(0);
    }
  }, [active]);

  useInput((input, key) => {
    if (key.escape) {
      onClose();
      return;
    }
    if (key.upArrow) {
      setIndex((i) => Math.max(0, i - 1));
      return;
    }
    if (key.downArrow) {
      setIndex((i) => Math.min(entries.length - 1, i + 1));
      return;
    }
    if (key.return) {
      const entry = entries[index];
      if (entry) {
        touchPrompt(entry.text);
        onSelect(entry.text);
      }
      return;
    }
    if (key.delete || key.backspace) {
      const entry = entries[index];
      if (entry) {
        removePrompt(entry.text);
        const next = getCachedPrompts();
        setEntries(next);
        setIndex((i) => Math.min(i, Math.max(0, next.length - 1)));
      }
    }
  }, { isActive: active });

  if (!active) return null;

  return (
    <Box flexDirection="column" borderStyle="round" borderColor="blue">
      <Text bold color="blue"> 📋 Prompt Cache — Recently Used Long Prompts </Text>
      {entries.length === 0 ? (
        <Text dimColor>  (no cached prompts — type a message &gt;=15 characters)</Text>
      ) : (
        entries.slice(0, 15).map((entry, i) => {
          const preview = entry.text.replace(/\n/g, " ").slice(0, 100);
          return (
            <Text
              key={i}
              backgroundColor={i === index ? "blue" : undefined}
              color={i === index ? "white" : undefined}
            >
              {"  "}{i === index ? "▸" : " "} {preview}{entry.text.length > 100 ? "…" : ""}
            </Text>
          );
        })
      )}
      <Text dimColor>↑↓ Navigate | Enter Select | Delete Remove | Esc Back</Text>
    </Box>
  );
}
