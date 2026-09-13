import React from "react";
import { Box, Text } from "ink";
import { TextInput } from "./TextInput.js";

export function InputBar({
  value,
  onChange,
  onSubmit,
  focus = true,
  disabled = false,
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit: (text: string) => void;
  focus?: boolean;
  disabled?: boolean;
}) {
  return (
    <Box
      borderStyle="round"
      borderColor={disabled ? "gray" : "cyan"}
      paddingX={1}
    >
      <Text bold color={disabled ? "gray" : "cyan"}>
        {"❯ "}
      </Text>
      {disabled ? (
        <Text dimColor>正在连接 Gateway，加载完成后即可输入…</Text>
      ) : (
        <TextInput
          value={value}
          onChange={onChange}
          focus={focus}
          placeholder="Type your message or / for commands..."
          onSubmit={(text) => {
            onChange("");
            onSubmit(text);
          }}
        />
      )}
    </Box>
  );
}
