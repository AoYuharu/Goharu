import React, { useEffect, useRef, useState } from "react";
import { Text, useInput } from "ink";
import chalk from "chalk";

/**
 * Minimal controlled text input.
 *
 * Replaces ink-text-input, which inserts letters for unlisted ctrl combos
 * (e.g. Ctrl+L inserted "l", breaking the app's clear shortcut).
 * All ctrl/meta combos are ignored here — App-level useInput owns shortcuts.
 * Multi-line paste: newlines collapse to spaces (single-line input).
 */
export function TextInput({
  value,
  onChange,
  onSubmit,
  placeholder = "",
  focus = true,
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit: (v: string) => void;
  placeholder?: string;
  focus?: boolean;
}) {
  const [cursorOffset, setCursorOffset] = useState(value.length);
  const lastInternal = useRef(value);

  // External value change (e.g. Tab autocomplete) → cursor to end
  useEffect(() => {
    if (value !== lastInternal.current) {
      lastInternal.current = value;
      setCursorOffset(value.length);
    }
  }, [value]);

  useInput((input, key) => {
    if (key.ctrl || key.meta) return;
    if (key.return) {
      onSubmit(value);
      return;
    }
    if (key.escape || key.upArrow || key.downArrow || key.tab) return;

    if (key.leftArrow) {
      setCursorOffset((o) => Math.max(0, o - 1));
      return;
    }
    if (key.rightArrow) {
      setCursorOffset((o) => Math.min(value.length, o + 1));
      return;
    }
    if (key.backspace || key.delete) {
      if (cursorOffset > 0) {
        const next = value.slice(0, cursorOffset - 1) + value.slice(cursorOffset);
        lastInternal.current = next;
        onChange(next);
        setCursorOffset(cursorOffset - 1);
      }
      return;
    }
    if (!input) return;
    // Ignore unparsed escape sequences (e.g. mouse report fragments)
    if (input.includes("\x1b")) return;

    const clean = input.replace(/[\r\n]+/g, " ");
    const next = value.slice(0, cursorOffset) + clean + value.slice(cursorOffset);
    lastInternal.current = next;
    onChange(next);
    setCursorOffset(cursorOffset + clean.length);
  }, { isActive: focus });

  if (value.length === 0) {
    if (!focus) {
      return <Text>{placeholder ? chalk.grey(placeholder) : " "}</Text>;
    }
    return (
      <Text>
        {placeholder
          ? chalk.inverse(placeholder[0]) + chalk.grey(placeholder.slice(1))
          : chalk.inverse(" ")}
      </Text>
    );
  }

  const chars = [...value];
  const rendered = chars
    .map((ch, i) => (focus && i === cursorOffset ? chalk.inverse(ch) : ch))
    .join("");
  const tail = focus && cursorOffset >= chars.length ? chalk.inverse(" ") : "";
  return <Text>{rendered + tail}</Text>;
}
