#!/usr/bin/env node
/**
 * Mock gateway for offline TUI development.
 *
 * Speaks the same NDJSON JSON-RPC protocol as Gateway/gateway_entry.py.
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
  // ── 后台任务事件（task.background.update 协议演示）──
  event("task.background.update", { task_id: 7, tool: "run_cmd", args: "python train_model.py --epochs 50", status: "running", started_at: Date.now() / 1000 });
  event("task.background.update", { task_id: 8, tool: "parse_pdf", args: "big_report.pdf, 1-20", status: "running", started_at: Date.now() / 1000 });
  await sleep(200);
  // ── subagent 事件序列（subagent.update 结构化协议演示）──
  const archTask = "检查 bootstrap verified_facts 与 gaps_pending，禁止重复已验证探测";
  const exploreTask = "Search for config files in E:/Goharu/src which refer to Animation and Rendering";
  const planTask = "Design the rendering pipeline";
  const base0 = { agent_id: "architect_hub_test01", agent_type: "architect", task: archTask };
  const base1 = { agent_id: "explore_a1b2c3d4", agent_type: "explore", task: exploreTask };
  const base2 = { agent_id: "plan_e5f6a7b8", agent_type: "plan", task: planTask };
  event("subagent.update", { ...base0, kind: "started", level: "info", message: "启动 Architect [architect_hub_test01]" });
  event("subagent.update", { ...base1, kind: "started", level: "info", message: "🚀 启动 explore agent [explore_a1b2c3d4]" });
  event("subagent.update", { ...base2, kind: "started", level: "info", message: "🚀 启动 plan agent [plan_e5f6a7b8]" });
  await sleep(400);
  event("subagent.update", { ...base0, kind: "activity", level: "info", category: "iteration", content: "1/40", message: "Arch 迭代 1/40" });
  event("subagent.update", { ...base1, kind: "activity", level: "info", category: "iteration", content: "1/8", message: "🔄 迭代 1/8" });
  event("subagent.update", { ...base1, kind: "activity", level: "debug", category: "thinking", content: "我需要先找到所有 yaml 配置文件，然后逐一检查是否涉及 Animation。\n先从 src 目录开始。", message: "💭 思考" });
  event("subagent.update", { ...base1, kind: "activity", level: "info", category: "tool_call", tool: "Glob", content: '{"pattern": "**/*.yaml"}', message: "🔧 调用工具: Glob" });
  await sleep(400);
  event("subagent.update", { ...base1, kind: "activity", level: "debug", category: "tool_result", tool: "Glob", content: '{"filenames": ["src/config.yaml", "src/anim.yaml"]}', message: "✓ 工具结果" });
  event("subagent.update", { ...base2, kind: "activity", level: "info", category: "iteration", content: "1/5", message: "🔄 迭代 1/5" });
  event("subagent.update", { ...base2, kind: "activity", level: "debug", category: "thinking", content: "渲染管线需要分层设计：数据层、布局层、绘制层。", message: "💭 思考" });
  await sleep(400);
  event("subagent.update", { ...base0, kind: "activity", level: "info", category: "final", content: "Arch 上下文验收完成", message: "Arch 得出最终结论" });
  event("subagent.update", { ...base0, kind: "finished", level: "info", message: "Architect 完成", status: "success" });
  event("subagent.update", { ...base1, kind: "activity", level: "info", category: "final", content: "找到 3 个相关配置文件：**src/config.yaml**、src/anim.yaml、src/render.yaml。", message: "✅ 得出最终结论" });
  event("subagent.update", { ...base1, kind: "finished", level: "info", message: "✅ explore agent 完成", status: "success" });
  event("subagent.update", { ...base2, kind: "finished", level: "error", message: "❌ plan agent 完成", status: "error" });
  event("task.background.update", { task_id: 7, tool: "run_cmd", status: "success" });
  event("task.background.completed", { count: 1, task_ids: [7] });
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
          { role: "user", kind: "text", text: "历史上的问题", timestamp: "2026-08-25T10:00:00" },
          { role: "assistant", kind: "thinking", text: "用户在问历史问题，让我想想……", timestamp: "2026-08-25T10:00:01" },
          { role: "assistant", kind: "tool", tool: "Grep", args: "foo, src", args_full: '{\n  "pattern": "foo",\n  "path": "src"\n}', result: "src/a.ts:10: foo()\nsrc/b.ts:22: foo(x)", is_error: false, timestamp: "2026-08-25T10:00:03" },
          { role: "assistant", kind: "text", text: "历史上的回答", timestamp: "2026-08-25T10:00:05" },
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
