/**
 * Verify the mouse-sequence drain parser, especially ConPTY split chunks.
 * Run: npx tsx test/mouse.test.ts
 */
import { drainMouseSequences } from "../src/mouse.js";

let failed = 0;
function check(name: string, cond: boolean, extra = "") {
  if (!cond) failed++;
  console.log(`${cond ? "PASS" : "FAIL"} ${name}${extra ? " — " + extra : ""}`);
}

// 1. Complete click sequence
let r = drainMouseSequences("\x1b[<0;35;12M");
check("click parsed", r.events.length === 1 && r.events[0].kind === "click" && r.events[0].x === 35 && r.events[0].y === 12);
check("click leaves no rest", r.rest === "" && r.pending === "");

// 2. Split across two chunks (ConPTY behavior)
r = drainMouseSequences("\x1b[<0;7");
check("split chunk 1: held, nothing forwarded", r.rest === "" && r.pending === "\x1b[<0;7" && r.events.length === 0);
const r2 = drainMouseSequences(r.pending + "6;35M");
check("split chunk 2: click completed", r2.events.length === 1 && r2.events[0].x === 76);
check("split chunk 2: no leak", r2.rest === "" && r2.pending === "");

// 3. Release event swallowed silently
r = drainMouseSequences("\x1b[<0;10;5m");
check("release swallowed", r.events.length === 0 && r.rest === "");

// 4. Motion (hover) event
r = drainMouseSequences("\x1b[<35;20;8M");
check("move parsed", r.events[0]?.kind === "move" && r.events[0]?.x === 20);

// 5. Wheel up/down
r = drainMouseSequences("\x1b[<64;10;3M\x1b[<65;10;3M");
check("wheel up+down", r.events.length === 2 && r.events[0].dir === "up" && r.events[1].dir === "down");

// 6. Plain keyboard text passes through untouched
r = drainMouseSequences("hello");
check("plain text passes", r.rest === "hello" && r.events.length === 0);

// 7. Mixed: text + mouse + text in one chunk
r = drainMouseSequences("a\x1b[<0;1;1Mb");
check("mixed content", r.rest === "ab" && r.events.length === 1);

// 8. Arrow key sequence untouched
r = drainMouseSequences("\x1b[A");
check("arrow key passes", r.rest === "\x1b[A");

// 9. False prefix eventually flushes
r = drainMouseSequences("\x1b[<" + "x".repeat(20));
check("false prefix flushes", r.rest.length > 0 && r.pending.length <= 3);

process.exit(failed ? 1 : 0);
