/**
 * Verify measureMessage() row counts match actual rendered rows.
 * This invariant is what makes mouse click hit-testing accurate.
 *
 * Run: npx tsx test/measure.test.tsx
 */
import React from "react";
import { render } from "ink-testing-library";
import { MessageItem, measureMessage, type ChatMessage } from "../src/components/MessageItem.js";

const WIDTH = 80;
const cases: Array<{ name: string; msg: ChatMessage }> = [
  { name: "splash", msg: { id: 1, ts: "10:00:00", kind: "splash", text: "  Goharu banner", color: "cyan" } },
  { name: "user", msg: { id: 2, ts: "10:00:01", kind: "user", text: "帮我看一下这个文件" } },
  {
    name: "user-long-wrap",
    msg: { id: 3, ts: "10:00:02", kind: "user", text: "word ".repeat(60) },
  },
  {
    name: "assistant-markdown",
    msg: { id: 4, ts: "10:00:03", kind: "assistant", text: "## Title\n\nSome **bold** text\n\n- item1\n- item2\n\n```py\nprint(1)\n```" },
  },
  { name: "thinking", msg: { id: 5, ts: "10:00:04", kind: "thinking", text: "line1\nline2", step: 2 } },
  { name: "note", msg: { id: 6, ts: "10:00:05", kind: "note", text: "step text", step: 1 } },
  {
    name: "tool-ok",
    msg: { id: 7, ts: "10:00:06", kind: "tool", tool: "Glob", args: "E:\\Goharu\\*.py", result: "a.py", isError: false },
  },
  {
    name: "tool-err",
    msg: { id: 8, ts: "10:00:07", kind: "tool", tool: "Read", args: "x.ts", result: "Error: nope", isError: true },
  },
  { name: "system", msg: { id: 9, ts: "10:00:08", kind: "system", text: "Gateway ready" } },
  { name: "error", msg: { id: 10, ts: "10:00:09", kind: "error", text: "boom" } },
];

let failed = 0;
for (const { name, msg } of cases) {
  const { lastFrame, unmount } = render(<MessageItem msg={msg} />, { columns: WIDTH });
  const actual = (lastFrame() ?? "").split("\n").length;
  const expected = measureMessage(msg, WIDTH);
  const ok = actual === expected;
  if (!ok) failed++;
  console.log(`${ok ? "PASS" : "FAIL"} ${name}: measured=${expected} rendered=${actual}`);
  unmount();
}
process.exit(failed ? 1 : 0);
