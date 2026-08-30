/**
 * File-based debug logging for the TUI (stdout is owned by Ink, so logs
 * must go to a file). Enabled by setting GOHARU_TUI_DEBUG=1.
 *
 *   GOHARU_TUI_DEBUG=1 python run_tui_ts.py   →  tui-ts/debug.log
 */
import { appendFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ENABLED = !!process.env.GOHARU_TUI_DEBUG;
const LOG_FILE = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "debug.log",
);

export function debugLog(tag: string, msg: string): void {
  if (!ENABLED) return;
  try {
    mkdirSync(path.dirname(LOG_FILE), { recursive: true });
    appendFileSync(LOG_FILE, `${new Date().toISOString()} [${tag}] ${msg}\n`, "utf8");
  } catch {
    // logging must never break the app
  }
}
