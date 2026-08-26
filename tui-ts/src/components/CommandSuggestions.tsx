import React from "react";
import { Box, Text } from "ink";
import type { CommandDef } from "../commands.js";

export function CommandSuggestions({
  suggestions,
  selectedIndex,
}: {
  suggestions: CommandDef[];
  selectedIndex: number;
}) {
  if (suggestions.length === 0) return null;
  return (
    <Box flexDirection="column" borderStyle="single" borderColor="blue" width={50}>
      {suggestions.map((cmd, i) => (
        <Text
          key={cmd.name}
          backgroundColor={i === selectedIndex ? "blue" : undefined}
          color={i === selectedIndex ? "white" : undefined}
        >
          {" "}{cmd.name.padEnd(16)} <Text dimColor>{cmd.description}</Text>
        </Text>
      ))}
    </Box>
  );
}
