#!/usr/bin/env node
/**
 * Mock gateway for offline TUI development.
 *
 * Speaks the same NDJSON JSON-RPC protocol as TUI/gateway_entry.py.
 * Modes:
 *   (default)             ready → on send_message replay a scripted event sequence
 *   --die-before-ready    exit(1) immediately with stderr noise
 *   --crash-after-ready   emit gateway.ready, then exit(1) after 1.5s
 */

const mode = process.argv.includes("--die-before-ready")
  ? "die-before-ready"
  : process.argv.includes("--crash-after-ready")
    ? "crash-after-ready"
    : "normal";

function send(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function event(type, payload = {}) {
  send({ jsonrpc: "2.0", method: "event", params: { type, payload } });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

if (mode === "die-before-ready") {
  process.stderr.write("Traceback (most recent call last):\n");
  process.stderr.write("  File \"gateway_entry.py\", line 1, in <module>\n");
  process.stderr.write("RuntimeError: simulated init failure\n");
  process.exit(1);
}

let buffer = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buffer += chunk;
  for (;;) {
    const nl = buffer.indexOf("\n");
    if (nl < 0) break;
    const line = buffer.slice(0, nl).trim();
    buffer = buffer.slice(nl + 1);
    if (line) handleRequest(JSON.parse(line));
  }
});

event("gateway.ready");
event("token.stats", { current_tokens: 0, prompt_cache_ratio: 0 });
if (mode === "crash-after-ready") {
  setTimeout(() => {
    process.stderr.write("simulated crash after ready\n");
    process.exit(1);
  }, 1500);
}

async function replayScript(message) {
  event("agent.thinking");
  await sleep(200);
  event("agent.step", { step: 1 });
  event("agent.thinking_content", { step: 1, thinking: "用户说的是：" + message.slice(0, 40) + "，我需要调用工具。" });
  await sleep(200);
  event("agent.llm_requesting");
  await sleep(300);
  event("tool.call", { tool: "run_cmd", arguments: { command: "echo hello" }, call_id: "call_1", step: 1 });
  await sleep(300);
  event("tool.result", { tool: "run_cmd", call_id: "call_1", result: "hello\n" });
  event("agent.text_content", { step: 1, text: "工具执行完成，我来整理结果。" });
  await sleep(200);
  event("agent.step", { step: 2 });
  event("agent.llm_requesting");
  await sleep(300);
  event("token.stats", { current_tokens: 1234, prompt_cache_ratio: 0.85 });
  event("agent.answer", { answer: "这是 mock 回答。\n\n## Markdown 测试\n\n- 列表项一\n- 列表项二\n\n```python\nprint('code block')\n```" });
  event("message.complete", { answer: "这是 mock 回答。" });
}

function handleRequest(req) {
  const { method, params = {}, id } = req;
  const respond = (result) => send({ jsonrpc: "2.0", result, id });

  switch (method) {
    case "agent.send_message":
      respond({ queued: true });
      replayScript(params.message ?? "").catch(() => {});
      break;
    case "agent.get_history":
      respond({
        messages: [
          { role: "user", content: "历史上的问题", type: "text", timestamp: "2026-08-25T10:00:00" },
          { role: "assistant", content: "历史上的回答", type: "text", timestamp: "2026-08-25T10:00:05" },
        ],
      });
      break;
    case "agent.clear_session":
    case "agent.compact":
      respond({ queued: true, command: method.split(".")[1] });
      setTimeout(() => event("command.complete", { command: method.split(".")[1], success: true }), 300);
      break;
    case "agent.interrupt":
      respond({ interrupted: true });
      event("agent.interrupted", { message: "请求已被用户中断" });
      break;
    case "agent.update_config":
      respond({ success: true, key: params.key, old_value: null, new_value: params.value });
      break;
    default:
      send({ jsonrpc: "2.0", error: { code: -32601, message: `Method not found: ${method}` }, id });
  }
}
