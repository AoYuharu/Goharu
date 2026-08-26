/**
 * Real-gateway debug: send one message, print all events until message.complete.
 * Run: npx tsx test/debug-real.ts
 */
import { GatewayClient } from "../src/gateway/GatewayClient.js";

const gateway = new GatewayClient();
gateway.on("*", (e: { type: string; payload: unknown }) => {
  const p = JSON.stringify(e.payload) ?? "";
  console.log(`[event] ${e.type} ${p.slice(0, 200)}`);
  if (e.type === "message.complete" || e.type === "agent.error" || e.type === "gateway.init_error") {
    gateway.stop();
    setTimeout(() => process.exit(0), 500);
  }
});
gateway.start();
gateway.once("gateway.ready", async () => {
  console.log("[debug] ready, sending message...");
  const r = await gateway.call("agent.send_message", {
    message: "用一句话回答：1+1等于几",
    session_id: "default",
  });
  console.log("[debug] send_message result:", JSON.stringify(r));
});
setTimeout(() => {
  console.log("[debug] TIMEOUT");
  gateway.stop();
  process.exit(1);
}, 120_000);
