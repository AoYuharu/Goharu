import React from "react";
import { Box, Text } from "ink";
import { separator } from "../format.js";
import { renderMarkdown } from "../markdown.js";

export type ChatMessage = {
  id: number;
  ts: string;
} & (
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string }
  | { kind: "thinking"; text: string; step: number }
  | { kind: "note"; text: string; step: number }
  | { kind: "tool"; tool: string; args: string; result: string }
  | { kind: "system"; text: string }
  | { kind: "error"; text: string }
  | { kind: "splash"; text: string; color?: string }
);

export function MessageItem({ msg }: { msg: ChatMessage }) {
  switch (msg.kind) {
    case "splash":
      return msg.color ? <Text color={msg.color}>{msg.text}</Text> : <Text>{msg.text}</Text>;

    case "user":
      return (
        <Text>
          <Text dimColor>[{msg.ts}] </Text>
          <Text bold color="cyan">You</Text>
          <Text>: {msg.text}</Text>
        </Text>
      );

    case "assistant": {
      const sep = separator();
      return (
        <Box flexDirection="column">
          <Text bold color="cyan">{sep}</Text>
          <Text>
            <Text dimColor>[{msg.ts}] </Text>
            <Text bold color="green">Assistant</Text>
            <Text>:</Text>
          </Text>
          <Text>{renderMarkdown(msg.text)}</Text>
          <Text bold color="cyan">{sep}</Text>
        </Box>
      );
    }

    case "tool":
      return (
        <Box flexDirection="column">
          <Text>
            <Text dimColor>[{msg.ts}] </Text>
            <Text color="magenta">⚙ </Text>
            <Text bold color="magenta">{msg.tool}</Text>
            <Text dimColor>({msg.args})</Text>
          </Text>
          <Text>
            <Text dimColor>{"    → "}</Text>
            <Text color="gray">{msg.result}</Text>
          </Text>
        </Box>
      );

    case "thinking":
      return (
        <Box flexDirection="column">
          <Text>
            <Text dimColor>[{msg.ts}] </Text>
            <Text bold color="cyan">💭 Thinking</Text>
            <Text dimColor> (Step {msg.step})</Text>
          </Text>
          {msg.text.split("\n").map((line, i) => (
            <Text key={i} italic color="yellow">{"  "}{line}</Text>
          ))}
          <Text> </Text>
        </Box>
      );

    case "note":
      return (
        <Box flexDirection="column">
          <Text>
            <Text dimColor>[{msg.ts}] </Text>
            <Text bold color="green">Assistant</Text>
            <Text dimColor> (Step {msg.step})</Text>
          </Text>
          <Text>{msg.text}</Text>
          <Text> </Text>
        </Box>
      );

    case "system":
      return (
        <Text>
          <Text dimColor>[{msg.ts}] </Text>
          <Text bold color="yellow">System</Text>
          <Text>: </Text>
          <Text italic>{msg.text}</Text>
        </Text>
      );

    case "error":
      return (
        <Text>
          <Text dimColor>[{msg.ts}] </Text>
          <Text bold color="red">Error</Text>
          <Text>: </Text>
          <Text color="red">{msg.text}</Text>
        </Text>
      );
  }
}
