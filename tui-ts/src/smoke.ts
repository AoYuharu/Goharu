/**
 * M0 protocol smoke test.
 *
 * Usage:
 *   tsx src/smoke.ts          # against the real Python gateway
 *   tsx src/smoke.ts --mock   # against test/mock-gateway.mjs
 *
 * Steps: start gateway → wait gateway.ready (60s) → call agent.get_history →
 * print summary → stop. Exit code 0 = PASS, 1 = FAIL.
 */

import { GatewayClient, PROJECT_ROOT } from "./gateway/GatewayClient.js";
import type { GetHistoryResult } from "./gateway/protocol.js";

const useMock = process.argv.includes("--mock");
const mockArgs = process.argv.includes("--crash-after-ready")
  ? ["--crash-after-ready"]
  : process.argv.includes("--die-before-ready")
    ? ["--die-before-ready"]
    : [];

console.log(`[smoke] project root: ${PROJECT_ROOT}`);
console.log(`[smoke] mode: ${useMock ? "mock" : "real gateway"} ${mockArgs.join(" ")}`);

const gateway = new GatewayClient({ mock: useMock, mockArgs });

let failed = false;
const fail = (msg: string) => {
  failed = true;
  console.error(`[smoke] FAIL: ${msg}`);
};

gateway.on("gateway.init_error", (p: { message: string }) => {
  console.log(`[smoke] got gateway.init_error:\n${p.message}`);
  finish();
});
gateway.on("agent.error", (p: { message: string }) => {
  console.log(`[smoke] got agent.error:\n${p.message}`);
  if (mockArgs.includes("--crash-after-ready")) finish();
});
gateway.on("*", (e: { type: string }) => {
  if (!["gateway.ready", "gateway.init_error", "agent.error"].includes(e.type)) {
    console.log(`[smoke] event: ${e.type}`);
  }
});

const readyWait = new Promise<void>((resolve) => {
  gateway.once("gateway.ready", () => {
    console.log("[smoke] gateway.ready received");
    resolve();
  });
  if (mockArgs.includes("--die-before-ready")) resolve(); // never ready; init_error path
});

async function finish() {
  gateway.stop();
  // give stop() a moment to close the pipe
  setTimeout(() => {
    if (mockArgs.includes("--die-before-ready") || mockArgs.includes("--crash-after-ready")) {
      console.log(failed ? "[smoke] RESULT: FAIL" : "[smoke] RESULT: PASS (error path)");
      process.exit(failed ? 1 : 0);
    }
    console.log(failed ? "[smoke] RESULT: FAIL" : "[smoke] RESULT: PASS");
    process.exit(failed ? 1 : 0);
  }, 500);
}

async function main() {
  gateway.start();
  const timeout = setTimeout(() => {
    fail("gateway.ready timeout (60s)");
    finish();
  }, 60_000);

  await readyWait;
  if (mockArgs.length > 0) return; // error-path modes end via events

  try {
    const history = await gateway.call("agent.get_history", {});
    const h = history as GetHistoryResult;
    console.log(`[smoke] get_history ok: ${h.messages.length} message(s)`);
  } catch (err) {
    fail(`get_history failed: ${err}`);
  }

  clearTimeout(timeout);
  await finish();
}

main().catch((err) => {
  fail(`unexpected: ${err}`);
  finish();
});
