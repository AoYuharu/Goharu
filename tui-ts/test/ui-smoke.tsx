/**
 * UI smoke test — renders <App mock/> via ink-testing-library against the
 * mock gateway, types a message, and asserts the scripted reply renders.
 *
 * Run: npx tsx test/ui-smoke.tsx          (mock gateway)
 *      npx tsx test/ui-smoke.tsx --real   (real Python gateway, needs API key)
 */
import React from "react";
import { render } from "ink-testing-library";
import { readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

// Isolate prompt cache from the real user data BEFORE App/promptCache load.
const TEST_CACHE = join(tmpdir(), `goharu_prompt_cache_test_${process.pid}.json`);
process.env.GOHARU_PROMPT_CACHE = TEST_CACHE;

import { App } from "../src/components/App.js";
import { PROJECT_ROOT } from "../src/gateway/GatewayClient.js";

const REAL = process.argv.includes("--real");

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const { stdin, frames, lastFrame, unmount } = render(<App mock={!REAL} />);

  let failed = false;
  const check = (cond: boolean, label: string) => {
    console.log(`${cond ? "PASS" : "FAIL"}: ${label}`);
    if (!cond) failed = true;
  };

  // 1. Wait for gateway.ready → status "Gateway ready..."
  await sleep(REAL ? 10_000 : 1500);
  const all = () => frames.join("\n");
  check(all().includes("Gateway is ready"), "gateway.ready → system message rendered");
  check(all().includes("███"), "splash art rendered");
  if (!REAL) {
    check(all().includes("历史上的问题"), "history replay: user message rendered");
    check(all().includes("历史上的回答"), "history replay: assistant message rendered");
  }

  // 2. Type a message and submit
  stdin.write(REAL ? "用一句话回答：1+1等于几" : "hello mock");
  await sleep(200);
  stdin.write("\r");

  // 3. Wait for the answer
  const deadline = Date.now() + (REAL ? 90_000 : 3000);
  let answered = false;
  while (Date.now() < deadline) {
    await sleep(1000);
    if (REAL ? all().includes("Assistant") && /Ready/.test(frames[frames.length - 1] ?? "")
             : all().includes("这是 mock 回答")) {
      answered = true;
      break;
    }
  }
  if (!REAL) {
    check(all().includes("You: hello mock"), "user message rendered");
    check(all().includes("💭 Thinking"), "thinking_content rendered");
    check(all().includes("这是 mock 回答"), "answer rendered");
    check(all().includes("⚙ run_cmd"), "tool.call rendered as tool message");
    check(all().includes("→ hello"), "tool.result preview rendered");
    check(all().includes("Markdown 测试"), "markdown content rendered");
    check(all().includes("Current Tokens:"), "token stats rendered");
    check(/Ready/.test(frames[frames.length - 1] ?? ""), "final status is Ready");

    // 4. Esc → interrupt (mock replies interrupted:true + agent.interrupted event)
    stdin.write("");
    await sleep(500);
    check(all().includes("请求已被用户中断"), "Esc interrupt → agent.interrupted rendered");

    // 5. Ctrl+L → clear (ink-testing-library keeps Static output in frame history,
    //    so we verify the clear escape sequence + cleared status instead)
    const frameCountBefore = frames.length;
    stdin.write(""); // Ctrl+L
    await sleep(500);
    const afterClear = frames.slice(frameCountBefore).join("\n");
    check(afterClear.includes("Chat cleared"), "Ctrl+L sets cleared status");

    // 6. Autocomplete: typing "/" shows the suggestion dropdown
    stdin.write("/");
    await sleep(300);
    check(frames[frames.length - 1].includes("/clear"), "typing / shows command suggestions");
    stdin.write("hel");
    await sleep(300);
    check(frames[frames.length - 1].includes("/help"), "suggestions filter by prefix");
    stdin.write("\t"); // Tab → autocomplete to /help
    await sleep(200);
    stdin.write("\r"); // submit
    await sleep(500);
    check(all().includes("/compact - Summarize conversation"), "/help renders command list");

    // 7. Prompt cache: 15+ char message gets cached to disk
    stdin.write("这是一条足够长的测试消息用于缓存");
    await sleep(100);
    stdin.write("\r");
    await sleep(300);
    try {
      const raw = readFileSync(TEST_CACHE, "utf8");
      check(raw.includes("足够长的测试消息"), "long prompt cached to prompt_cache.json");
    } catch {
      check(false, "long prompt cached to prompt_cache.json");
    }

    // 8. /config → ConfigEditor screen opens, Esc closes
    stdin.write("/config");
    await sleep(100);
    stdin.write("\r");
    await sleep(400);
    check((lastFrame() ?? "").includes("Configuration Editor"), "/config opens ConfigEditor screen");
    stdin.write(""); // Esc
    await sleep(300);
    check((lastFrame() ?? "").includes("Type your message"), "Esc closes ConfigEditor");

    // 9. Ctrl+T → TextViewer for last assistant reply
    stdin.write(""); // Ctrl+T
    await sleep(300);
    check((lastFrame() ?? "").includes("Agent Response"), "Ctrl+T opens TextViewer");
    stdin.write(""); // Esc
    await sleep(300);

    // 10. /prompt-cache → screen opens with cached entry, Enter loads into input
    stdin.write("/prompt-cache");
    await sleep(100);
    stdin.write("\r");
    await sleep(400);
    check((lastFrame() ?? "").includes("Prompt Cache"), "/prompt-cache opens cache screen");
    check((lastFrame() ?? "").includes("足够长的测试消息"), "cached prompt listed");
    stdin.write("\r"); // Enter → select
    await sleep(300);
    check((lastFrame() ?? "").includes("> 这是一条足够长的测试消息用于缓存"), "Enter loads prompt into input");
  } else {
    check(answered, "real gateway produced an assistant answer");
    check(all().includes("Current Tokens:"), "token stats rendered");
  }

  unmount();
  try { rmSync(TEST_CACHE); } catch { /* ignore */ }
  console.log(failed ? "RESULT: FAIL" : "RESULT: PASS");
  process.exit(failed ? 1 : 0);
}

main().catch((err) => {
  console.error("FAIL: unexpected", err);
  process.exit(1);
});
