import React from "react";
import { Box, Text } from "ink";
import { TextInput } from "./TextInput.js";

export function InputBar({
  value,
  onChange,
  onSubmit,
  focus = true,
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit: (text: string) => void;
  focus?: boolean;
}) {
  return (
    <Box>
      <Text bold color="cyan">{"> "}</Text>
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
    </Box>
  );
}
