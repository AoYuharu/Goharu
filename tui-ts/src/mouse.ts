/**
 * Minimal SGR mouse support (Ink has none built in).
 *
 * Enables click + any-motion reporting (DECSET 1000 + 1003) with SGR
 * extended coordinates (1006), then wraps `process.stdin.read()` so mouse
 * sequences are stripped BEFORE Ink's keypress parser ever sees them —
 * otherwise ConPTY split chunks leak into the text input as garbage like
 * `[<0;76;35M`. (Ink 7 consumes stdin via 'readable'/read(), not 'data'
 * events — read() is the only reliable interception point.)
 *
 * The app implements its own scrollable chat viewport, so captured wheel
 * events scroll chat content directly (no Shift needed). Text selection
 * still works with Shift+drag (terminal bypass).
 */

import { debugLog } from "./debugLog.js";

export interface MouseEvent {
  kind: "press" | "release" | "wheel" | "move";
  x: number;
  y: number;
  dir?: "up" | "down";
  dragging?: boolean; // move with left button held
}

const MOUSE_SEQ = /^\x1b\[<(\d+);(\d+);(\d+)([Mm])/;

/**
 * Pure parser: pull complete SGR mouse sequences out of `buffer`.
 * Returns the remaining text safe for Ink and the (possibly incomplete)
 * tail to keep buffered for the next chunk.
 */
export function drainMouseSequences(buffer: string): {
  rest: string;
  pending: string;
  events: MouseEvent[];
} {
  const events: MouseEvent[] = [];
  let rest = "";
  let i = 0;
  while (i < buffer.length) {
    const start = buffer.indexOf("\x1b[<", i);
    if (start === -1) {
      rest += buffer.slice(i);
      return { rest, pending: "", events };
    }
    rest += buffer.slice(i, start);
    const tail = buffer.slice(start);
    const m = MOUSE_SEQ.exec(tail);
    if (!m) {
      // Incomplete sequence (ConPTY split chunks) — hold the tail
      if (tail.length > 16) {
        // False prefix that never terminated: flush all but a fresh prefix
        rest += tail.slice(0, tail.length - 3);
        return { rest, pending: tail.slice(-3), events };
      }
      return { rest, pending: tail, events };
    }
    const b = Number(m[1]);
    const x = Number(m[2]);
    const y = Number(m[3]);
    const press = m[4] === "M";
    if (b & 64) {
      if (press) events.push({ kind: "wheel", x, y, dir: b & 1 ? "down" : "up" });
    } else if (b & 32) {
      if (press) events.push({ kind: "move", x, y, dragging: (b & 3) !== 3 });
    } else if ((b & 3) === 0) {
      events.push({ kind: press ? "press" : "release", x, y });
    }
    i = start + m[0].length;
  }
  return { rest, pending: "", events };
}

let pressHandler: ((x: number, y: number) => void) | null = null;
let releaseHandler: ((x: number, y: number) => void) | null = null;
let wheelHandler: ((dir: "up" | "down", x: number, y: number) => void) | null = null;
let moveHandler: ((x: number, y: number, dragging: boolean) => void) | null = null;
let pending = "";
let enabled = false;
let installed = false;

function installCapture(): void {
  if (installed) return;
  installed = true;
  // Ink 7 reads stdin in 'readable' mode and drains with
  // `while ((chunk = stdin.read()) !== null)`. The wrapper MUST preserve
  // that contract: fully drain the stream itself and return all filtered
  // text at once — returning null mid-drain would strand keyboard bytes
  // behind the mouse flood (the "frozen input" bug).
  const stdin = process.stdin as unknown as {
    read: (size?: number) => string | Buffer | null;
  };
  const origRead = stdin.read.bind(process.stdin);
  stdin.read = (): string | Buffer | null => {
    const first = origRead();
    if (!enabled || first === null || first === undefined) return first;
    let out = "";
    let chunk: string | Buffer | null = first;
    let eventCount = 0;
    while (chunk !== null && chunk !== undefined) {
      const input = pending + (typeof chunk === "string" ? chunk : chunk.toString("utf8"));
      const drained = drainMouseSequences(input);
      pending = drained.pending;
      out += drained.rest;
      eventCount += drained.events.length;
      for (const ev of drained.events) {
        if (ev.kind === "press") {
          debugLog("mouse", `press @${ev.x},${ev.y}`);
          pressHandler?.(ev.x, ev.y);
        } else if (ev.kind === "release") {
          debugLog("mouse", `release @${ev.x},${ev.y}`);
          releaseHandler?.(ev.x, ev.y);
        } else if (ev.kind === "wheel") {
          wheelHandler?.(ev.dir ?? "down", ev.x, ev.y);
        } else {
          moveHandler?.(ev.x, ev.y, ev.dragging ?? false);
        }
      }
      chunk = origRead();
    }
    if (eventCount > 0) debugLog("mouse", `drain: ${eventCount} events, text=${JSON.stringify(out)}`);
    return out.length > 0 ? out : null;
  };
}

export function setMouseEnabled(
  on: boolean,
  onPress?: (x: number, y: number) => void,
  onRelease?: (x: number, y: number) => void,
  onWheel?: (dir: "up" | "down", x: number, y: number) => void,
  onMove?: (x: number, y: number, dragging: boolean) => void,
): void {
  if (onPress) pressHandler = onPress;
  if (onRelease) releaseHandler = onRelease;
  if (onWheel) wheelHandler = onWheel;
  if (onMove) moveHandler = onMove;
  if (on === enabled) return;
  enabled = on;
  if (on) {
    installCapture();
    process.stdout.write("\x1b[?1000h\x1b[?1003h\x1b[?1006h");
  } else {
    pending = "";
    process.stdout.write("\x1b[?1000l\x1b[?1003l\x1b[?1006l");
  }
}
